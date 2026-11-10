#!/usr/bin/env python3
"""Study-consistency audit ORCHESTRATOR/CLI (Phase 3b) — the I/O half of the audit.

`scripts/preflight_helpers/study_audit.py` is the PURE engine (zero I/O): it receives already-loaded
facts and returns findings + the readiness funnel. THIS file is the orchestrator that opens the five
real pilot populations, packs each into the engine's `*Facts` dataclasses keyed by the pinned
normalized Study-Adı key, calls `study_audit.audit`, and writes the report. It mirrors the shape of
`check_studies.run_once` and `sync_force_measures.main` — a thin loader + dry-run-by-default CLI.

The five populations (joined over D ∪ Z — NEVER the 282-study legacy cohort in runs.jsonl):

  D  datasheet  — `check_studies.read_sheet(cfg)` (hasta_kayit `kayit` tab); the operator's CLAIMS
                  (Study Adı, Operator, Artifacts, Alt-kohort) + the RAW Study-Adı column for the
                  collision scan (the engine scans BEFORE any dedup).
  Z  zips       — `data/goruntuler_mirror/<operator>/<study>.zip` -> `count_zip_artifacts` (real
                  M/R/D) + `check_study` (modalities / verdict / brightness, which internally
                  `deconflict_zip`s) + `study_name_grammar.parse` (name parses?).
  M  materialize— `env.STUDIES_ROOT.glob('**/materialize_manifest.json')` + `data/intake_hold/
                  *.hold.json`. PATHLIB glob is deliberate: it does NOT descend the legacy-cohort
                  symlinks under studies_root (anonaaq -> <repo>/private/goruntuler/...), so the
                  materialized set is the 3 real new-capture trees, not a stray manifest a `glob.glob`
                  recursive walk would pull out of the symlinked legacy corpus.
  R  runs       — `bundle_writer.iter_runs()` re-indexed by `Path(study_path).name` (NOT study_code —
                  the live join-key bug the plan calls out), reduced to the LATEST non-draft run.
  F  force      — `gws_io.sheets_values_get(force_id, '<tab>!A2:F')` (config `sheets.force_measures`)
                  -> `sync_force_measures.parse_force_rows` / `plan_writes` (orphans) + the raw col-A
                  names for collisions.

Each loader is isolated: a failure (e.g. `gws` unauthenticated, a corrupt zip) degrades that ONE
population to empty and records a skip note printed in the summary — the audit still runs on whatever
loaded, and the report says which seam it could not see.

  python scripts/audit_studies.py            # dry-run: console summary only
  python scripts/audit_studies.py --report   # also write data/audit/audit_report.{json,md}

Safety: the audit is READ-ONLY end-to-end — it never mutates the datasheet, the force sheet, the
source zips, or studies_root. It only reads the five populations and writes its findings to the
console and (with --report) to data/audit/audit_report.{json,md}; no sheet is ever written.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for `preflight_helpers` + sibling scripts

from preflight_helpers import env
from preflight_helpers import study_audit as sa

REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_DIR = REPO_ROOT / "data" / "audit"
MIRROR_DIR = REPO_ROOT / "data" / "goruntuler_mirror"
HOLD_DIR = REPO_ROOT / "data" / "intake_hold"


# ============================ small coercion helpers ============================
def _num(v) -> Optional[float]:
    """A float from a sheet/aggregate value, or None. Tolerates the force sheet's Turkish decimal
    comma ('26,8') and blank strings; a non-numeric token (e.g. a stray label) returns None."""
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _distinct_occurrences(*name_lists) -> List[str]:
    """One raw spelling per DISTINCT normalized study name across the given lists. A study listed in
    BOTH the datasheet and the force sheet is then counted once, while two DIFFERENT patients sharing a
    study NUMBER both survive (their full-name keys differ) — so the collision scan still fires but its
    count reflects PATIENTS, not cross-sheet duplication."""
    seen: Dict[str, str] = {}
    for names in name_lists:
        for nm in names:
            seen.setdefault(sa.normalize_key(nm), nm)
    return list(seen.values())


def _parse_brightness(param_notes) -> Optional[float]:
    """check_study stamps mean luminance as a param-note 'parlaklık <x>'. Pull the number back out so
    the engine's WASHOUT check sees it — reusing check_study's own computation (no second frame read,
    no reach into a private)."""
    for n in param_notes or []:
        s = str(n).strip()
        if s.lower().startswith("parlaklık"):
            return _num(s.split()[-1])
    return None


# ============================ D — datasheet (hasta_kayit `kayit` tab) ============================
def load_datasheet(cfg: dict):
    """{norm_key: DatasheetFacts}, raw Study-Adı occurrences, {study: row}, {study: {header: val}}.

    `read_sheet` already dedups by header; the RAW Study-Adı list (all rows, pre-dedup) is kept
    separately so the collision scan can see a reused study number. `study_to_row`/`study_to_vals`
    feed `sync_force_measures.plan_writes` for the force-orphan set."""
    import check_studies as ck

    _title, _header, rows = ck.read_sheet(cfg)
    facts: Dict[str, sa.DatasheetFacts] = {}
    raw_names: List[str] = []
    study_to_row: Dict[str, int] = {}
    study_to_vals: Dict[str, dict] = {}
    for r in rows:
        study = (r.get(ck.KEY_COL, "") or "").strip()
        if not study:
            continue
        raw_names.append(study)
        facts[sa.normalize_key(study)] = sa.DatasheetFacts(
            study=study,
            operator=(r.get("Operator", "") or "").strip() or None,
            artifacts_mrd=(r.get("Artifacts (mp4/RawImages/dcm)", "") or "").strip() or None,
            declared_profile=ck.declared_profile(r),
            row=r.get("_row"),
        )
        study_to_row[study] = r.get("_row")
        study_to_vals[study] = r
    return facts, raw_names, study_to_row, study_to_vals


# ============================ Z — source zips (the goruntuler mirror) ============================
def load_zips(skips: List[Tuple[str, str]]) -> Dict[str, sa.ZipFacts]:
    """{norm_key: ZipFacts} for every `data/goruntuler_mirror/<operator>/<study>.zip`. Per-zip
    isolation: a single unreadable zip is recorded as a skip note and the rest still load."""
    from preflight_helpers import check_study as cs
    from preflight_helpers import study_name_grammar as sng
    from preflight_helpers.study_artifacts import count_zip_artifacts

    out: Dict[str, sa.ZipFacts] = {}
    for zp in sorted(MIRROR_DIR.glob("*/*.zip")):
        study, operator = zp.stem, zp.parent.name
        try:
            artifacts = count_zip_artifacts(str(zp)).as_str()
            proto = cs.study_protocol(str(zp))
            rpt = cs.check_study(str(zp), protocol=proto, study=study)
            counts = {k: int(v) for k, v in (rpt.modality_counts or {}).items()}
            out[sa.normalize_key(study)] = sa.ZipFacts(
                study=study,
                artifacts_mrd=artifacts,
                modalities=counts,
                has_femur=counts.get("femur", 0) > 0,
                has_sweep=counts.get("sweep", 0) > 0,
                name_parsed=sng.parse(study).matched,
                folder_operator=operator,
                verdict=rpt.verdict,
                brightness=_parse_brightness(rpt.param_notes),
            )
        except Exception as exc:  # noqa: BLE001 - one bad zip must not lose the population
            skips.append((f"Z zip {study}", f"{type(exc).__name__}: {exc}"))
    return out


# ============================ M — materialized view + holds ============================
def load_materializes(declared_by_nkey: Dict[str, Optional[str]]) -> Dict[str, sa.MaterializeFacts]:
    """{norm_key: MaterializeFacts}. A manifest == materialized (captures = primary + annex); a
    `*.hold.json` == HELD (with a reason). `expected_captures` comes from the operator's declared
    profile (main=3 / subcohort=8) when known — None otherwise, so a not-declared row never reads as a
    manifest mismatch. The orphan-duplicate auto-exclusions ride straight from the manifest; only the
    ASK-tier lone-orphan caveats ('KEPT for review … not auto-excluded') become PAIRING_AMBIGUOUS —
    the length-ambiguous sweep note is advisory and intentionally not surfaced as a pairing finding."""
    from preflight_helpers.check_study import EXPECTED_PROFILES

    out: Dict[str, sa.MaterializeFacts] = {}
    for man in sorted(env.STUDIES_ROOT.glob("**/materialize_manifest.json")):
        try:
            d = json.loads(man.read_text(encoding="utf-8"))
        except Exception:
            continue
        study = d.get("study") or man.parent.name
        nkey = sa.normalize_key(study)
        annex = d.get("annex") or []
        prof = declared_by_nkey.get(nkey)
        expected = EXPECTED_PROFILES[prof]["captures"] if prof in EXPECTED_PROFILES else None
        # Prefer the STRUCTURED ambiguous_captures contract (materialize Phase 2); fall back to
        # scraping the human caveats only for older manifests written before that field existed.
        amb_struct = d.get("ambiguous_captures")
        if amb_struct is not None:
            ambiguous = [a.get("reason", "") if isinstance(a, dict) else str(a) for a in amb_struct]
        else:
            caveats = ((d.get("primary") or {}).get("caveats")) or []
            ambiguous = [c for c in caveats if ("not auto-excluded" in c) or ("KEPT for review" in c)]
        out[nkey] = sa.MaterializeFacts(
            materialized=True,
            manifest_captures=1 + len(annex),
            expected_captures=expected,
            excluded_captures=list(d.get("excluded_captures") or []),
            ambiguous_captures=ambiguous,
            # Phase 2 ALWAYS writes excluded_captures (even []); its absence means the manifest predates
            # Phase 2, so the ORPHAN-RAWIMAGES auto-correct could not have run — surfaced as a coverage note.
            manifest_pre_phase2=("excluded_captures" not in d),
        )

    for hf in sorted(HOLD_DIR.glob("*.hold.json")):
        try:
            d = json.loads(hf.read_text(encoding="utf-8"))
        except Exception:
            continue
        study = d.get("study") or hf.name[: -len(".hold.json")]
        nkey = sa.normalize_key(study)
        if nkey in out:               # a materialized tree supersedes a stale hold record
            continue
        out[nkey] = sa.MaterializeFacts(materialized=False, hold_reason=d.get("reason"))
    return out


# ============================ R — scored runs (re-indexed by study_path basename) ============================
def _repr_pctile(placements: dict) -> Optional[float]:
    """A representative cohort placement percentile: the overall pctile of the first available among a
    stable priority of features. None == no placement resolved (the PLACEMENT_BLANK data-lineage gap)."""
    if not isinstance(placements, dict):
        return None
    order = ["star_ratio", "qmt_mm", "rf_thickness_mm", "full_thickness_q50_mm", "int_cv_within"]
    for k in order + [k for k in placements if k not in order]:
        p = placements.get(k)
        if isinstance(p, dict):
            pct = (p.get("overall") or {}).get("pctile")
            if pct is not None:
                return float(pct)
    return None


def _frame_unions(frame_features: list) -> Tuple[List[str], List[str]]:
    """Union the per-frame `drop_reasons` + `qmt_validity_flags` lists behind the aggregate (the
    FRAME_DROPS surfacing). Empty when a run stored no per-frame provenance (the pilot runs)."""
    drops, flags = set(), set()
    for f in frame_features or []:
        if not isinstance(f, dict):
            continue
        for v in (f.get("drop_reasons") or []):
            drops.add(v)
        for v in (f.get("qmt_validity_flags") or []):
            flags.add(v)
    return sorted(drops), sorted(flags)


def _runfacts(rec: dict, *, finalized: bool) -> sa.RunFacts:
    agg = rec.get("aggregate") or {}
    routed = rec.get("routed_modalities") or agg.get("_routed_modalities") or []
    drops, flags = _frame_unions(rec.get("frame_features") or [])
    return sa.RunFacts(
        has_run=True,
        finalized=finalized,
        fat_to_depth=_num(agg.get("fat_to_depth_q50")),
        int_cv=_num(agg.get("int_cv_within")),
        qmt_mm=_num(agg.get("qmt_mm")),
        placement_pctile=_repr_pctile(rec.get("placements") or {}),
        femur_present=("femur" in routed),
        drop_reasons=drops,
        qmt_validity_flags=flags,
    )


def load_runs() -> Dict[str, sa.RunFacts]:
    """{norm_key(study_path basename): RunFacts}. Group every ledger record by its study_path
    basename (the pinned key — NOT study_code), then reduce to the LATEST FINALIZED run; a study with
    only non-finalized runs (draft / none) is has_run-but-not-finalized (RUN_NOT_FINALIZED)."""
    from preflight_helpers.bundle_writer import iter_runs

    grouped: Dict[str, Dict[str, list]] = {}
    for r in iter_runs():
        sp = r.get("study_path", "")
        if not sp:
            continue
        base = Path(sp).name
        bucket = grouped.setdefault(base, {"final": [], "draft": []})
        # A run is FINALIZED only when lifecycle == 'finalized' (plus the legacy missing-field case,
        # which readers treat as finalized — bundle_writer.py). bundle_writer documents a THIRD value
        # 'none'; it (and any unknown value) is NON-final, so it must NOT bucket as final or it would
        # inflate the processed/validatable funnel. (Latent today — all 38 ledger records are finalized.)
        lifecycle = r.get("lifecycle") or "finalized"
        if lifecycle == "finalized":
            bucket["final"].append(r)
        else:                                    # 'draft', 'none', or any non-finalized value
            bucket["draft"].append(r)

    out: Dict[str, sa.RunFacts] = {}
    for base, b in grouped.items():
        nkey = sa.normalize_key(base)
        if b["final"]:
            latest = sorted(b["final"], key=lambda r: r.get("created_at_utc", ""))[-1]
            out[nkey] = _runfacts(latest, finalized=True)
        else:
            out[nkey] = sa.RunFacts(has_run=True, finalized=False)
    return out


# ============================ F — force / strength criterion ============================
def load_forces(cfg: dict, d_norm_keys, study_to_row: dict, study_to_vals: dict,
                d_loaded: bool):
    """({norm_key: ForceFacts}, force_orphans, raw col-A names). vals = [isometric torque, isometric
    CoV, isokinetic torque, isokinetic power, isokinetic CoV] (force-sheet cols B-F). Orphans (no kayit row) come from
    `plan_writes` — but ONLY when D actually loaded, else every force row would falsely read as an
    orphan and matched_kayit is left True (unknown, never accuse)."""
    from preflight_helpers import gws_io as io
    import sync_force_measures as sfm

    force_id = cfg["sheets"]["force_measures"]
    ftitle = io.first_sheet(io.sheets_meta(force_id))["title"]
    fvals = io.sheets_values_get(force_id, f"{ftitle}!A1:F1000")
    sfm.assert_force_header(fvals[0] if fvals else [])
    data_rows = fvals[1:]
    raw_names = [(row[0] or "").strip() for row in data_rows if row and (row[0] or "").strip()]
    force_by_norm = sfm.parse_force_rows(data_rows)

    orphans: List[str] = []
    if d_loaded:
        _w, _s, _b, orphans = sfm.plan_writes(force_by_norm, study_to_row, study_to_vals)

    facts: Dict[str, sa.ForceFacts] = {}
    for nkey, (_fstudy, vals) in force_by_norm.items():
        facts[nkey] = sa.ForceFacts(
            in_force_sheet=True,
            matched_kayit=(nkey in d_norm_keys) if d_loaded else True,
            isometric_present=bool(vals[0]),                      # B izometrik peak tork
            isokinetic_present=bool(vals[2]) or bool(vals[3]),    # D konsantrik tork / E power
            isokinetic_cov=_num(vals[4]) if vals[4] else None,   # F CoV (isokinetic); vals[1]=izometrik CoV, not gated
            all_blank=not any(vals),
        )
    return facts, list(orphans), raw_names


# ============================ report assembly ============================
def _finding_dict(f: sa.AuditFinding) -> dict:
    return {"code": f.code, "severity": f.severity, "audience": f.audience, "action": f.action,
            "boundary": sa.CHECKS[f.code].boundary if f.code in sa.CHECKS else sa.ENGINE,
            "message": f.message, "data": f.data}


def _row_dict(row: sa.StudyAuditRow) -> dict:
    return {
        "study": row.study, "norm_key": row.norm_key, "operator": row.operator,
        "present": {"d": row.d is not None, "z": row.z is not None,
                    "m": bool(row.m and row.m.materialized),
                    "r": bool(row.r and row.r.finalized), "f": bool(row.f and row.f.in_force_sheet)},
        "readiness": {"recorded": row.recorded, "consistent": row.consistent,
                      "processed": row.processed, "validatable": row.validatable,
                      "reliable": row.reliable},
        "findings": [_finding_dict(f) for f in row.findings],
    }


def _set_diffs(rows: List[sa.StudyAuditRow]) -> dict:
    """Population membership + the four seam set-diffs, by study, for the report."""
    def present(pred):
        return sorted(r.study for r in rows if pred(r))
    in_d = lambda r: r.d is not None
    in_z = lambda r: r.z is not None
    in_m = lambda r: bool(r.m and r.m.materialized)
    in_run = lambda r: bool(r.r and r.r.has_run)
    in_fin = lambda r: bool(r.r and r.r.finalized)
    return {
        "counts": {"D": sum(map(in_d, rows)), "Z": sum(map(in_z, rows)),
                   "M": sum(map(in_m, rows)), "R_finalized": sum(map(in_fin, rows)),
                   "F": sum(1 for r in rows if r.f and r.f.in_force_sheet)},
        "z_without_d": present(lambda r: in_z(r) and not in_d(r)),
        "d_without_z": present(lambda r: in_d(r) and not in_z(r)),
        "materialized_no_run": present(lambda r: in_m(r) and not in_run(r)),
        "run_without_materialize": present(lambda r: in_fin(r) and not in_m(r)),
    }


def _cov_table(rows: List[sa.StudyAuditRow]) -> List[dict]:
    """One row per force-bearing study: isometric / isokinetic presence + CoV + the reliability call.
    The validation anchor — isometric is the primary criterion, isokinetic is CoV-gated."""
    out = []
    for r in rows:
        f = r.f
        if not (f and f.in_force_sheet):
            continue
        out.append({"study": r.study, "isometric": f.isometric_present,
                    "isokinetic": f.isokinetic_present, "cov_pct": f.isokinetic_cov,
                    # isokinetic present but CoV blank = reliability UNKNOWN (render '—'), not 'NO';
                    # mirrors the COV-GATE finding, which only fires when the CoV value is present.
                    "reliable": (sa.isokinetic_reliable(f.isokinetic_cov)
                                 if (f.isokinetic_present and f.isokinetic_cov is not None) else None),
                    "all_blank": f.all_blank})
    return out


def _coverage_notes(result: sa.AuditResult) -> List[str]:
    """One-line caveats so the report never advertises a seam it could NOT fully check. Two sources:
    (1) DEFERRED checks — registered in `CHECKS` but intentionally inert this phase (the loader never
        feeds them), so the legend would otherwise advertise a dead check;
    (2) pre-Phase-2 manifests — materializations that predate the Phase-2 orphan auto-correct, so the
        ORPHAN-RAWIMAGES (AUTO) finding is unexercised live until they are re-materialized under --force."""
    notes: List[str] = []
    deferred = sorted(code for code, spec in sa.CHECKS.items() if spec.deferred)
    if deferred:
        notes.append(
            f"Deferred checks (registered but INERT this phase — surfaced so the legend cannot advertise "
            f"a dead check): {', '.join(deferred)}. DEPTH-OFFSPEC is deferred under v8's relative-rank "
            f"depth design — each capture's depth is matched by RELATIVE rank (deepest=femur), not a fixed "
            f"protocol depth, so a probe that images deep (e.g. 831 at ~9cm) is in-spec and no fixed-depth "
            f"comparison applies; re-activate only with a relative-aware depth rule.")
    n_m = sum(1 for r in result.rows if r.m and r.m.materialized)
    n_pre2 = sum(1 for r in result.rows if r.m and r.m.materialized and r.m.manifest_pre_phase2)
    if n_pre2:
        notes.append(
            f"{n_pre2}/{n_m} materialized manifest(s) predate Phase 2 (no excluded_captures field) — the "
            f"ORPHAN-RAWIMAGES auto-correct (AUTO tier) is UNEXERCISED live until those studies are "
            f"re-materialized under --force (Phase 0 mirror pull + Phase 2 re-materialize). The audit "
            f"reconciles against the materializations on disk as-is.")
    return notes


def build_report(result: sa.AuditResult, loaded: dict, skips: List[Tuple[str, str]]) -> dict:
    fn = result.funnel
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "populations_loaded": loaded,
        "skipped": [{"population": p, "reason": why} for p, why in skips],
        "funnel": {"recorded": fn.recorded, "consistent": fn.consistent, "processed": fn.processed,
                   "validatable": fn.validatable, "reliable": fn.reliable,
                   "validatable_keys": fn.validatable_keys, "reliable_keys": fn.reliable_keys,
                   "headline": fn.headline()},
        "set_diffs": _set_diffs(result.rows),
        "rows": [_row_dict(r) for r in result.rows],
        "global_findings": [_finding_dict(f) for f in result.global_findings],
        "cov_table": _cov_table(result.rows),
        "coverage_notes": _coverage_notes(result),
        "legend": sa.legend(),
    }


_SEV_RANK = {sa.FAIL: 0, sa.WARN: 1, sa.INFO: 2}
_BOUND_LABEL = {sa.B0: "B0 collisions + population set-diffs", sa.B1: "B1 source ↔ datasheet",
                sa.B2: "B2 datasheet ↔ processing", sa.B3: "B3 processing ↔ numbers",
                sa.B4: "B4 force / strength criterion", sa.ENGINE: "engine"}


def render_markdown(report: dict, result: sa.AuditResult) -> str:
    fn, sd = report["funnel"], report["set_diffs"]
    L: List[str] = []
    L.append(f"# Study-consistency audit — {report['generated_at']}")
    L.append("")
    L.append("Reconciles the five pilot populations — source zips (Z), the hasta_kayit datasheet (D), "
             "the materialized view (M), the scored runs (R), the force/strength criterion (F) — over "
             "the D ∪ Z universe (never the 282-study legacy cohort).")
    L.append("")

    # ---- funnel (the headline) ----
    L.append("## Readiness funnel — VALIDATABLE N is the pilot number")
    L.append("")
    L.append(f"`{fn['headline']}`")
    L.append("")
    L.append("| tier | meaning | n |")
    L.append("|---|---|---|")
    L.append(f"| recorded | present in Z or D | {fn['recorded']} |")
    L.append(f"| consistent (T0) | recorded, no FAIL finding | {fn['consistent']} |")
    L.append(f"| processed (T1) | + materialized + a finalized run | {fn['processed']} |")
    L.append(f"| **VALIDATABLE (T2)** | **+ isometric peak torque present** | **{fn['validatable']}** |")
    L.append(f"| reliable (T3) | + reliable isokinetic (CoV ≤ {sa.COV_GATE_PCT:g}%) | {fn['reliable']} |")
    L.append("")
    if fn["validatable_keys"]:
        L.append("Validatable studies: " + ", ".join(f"`{k}`" for k in fn["validatable_keys"]))
        L.append("")

    # ---- populations ----
    L.append("## Populations loaded")
    L.append("")
    L.append("`n (universe)` = how many joined a D ∪ Z row; `n (loaded)` = the raw size the loader saw.")
    L.append("")
    L.append("| population | loaded | n (universe) | n (loaded) |")
    L.append("|---|---|---|---|")
    note_lines: List[str] = []
    for key, label in (("D", "D datasheet"), ("Z", "Z zips"), ("M", "M materialized"),
                       ("R", "R scored runs"), ("F", "F force")):
        info = report["populations_loaded"].get(key, {})
        n, nl = info.get("n", 0), info.get("n_loaded", info.get("n", 0))
        L.append(f"| {label} | {'yes' if info.get('loaded') else 'NO'} | {n} | {nl} |")
        if key == "R" and nl != n:
            note_lines.append(f"- R: {nl} distinct studies in the run ledger; {nl - n} legacy-cohort "
                              f"studies are outside the D ∪ Z universe and excluded by the study_path-"
                              f"basename re-index (the pinned-key discipline — never `study_code`).")
    L.append("")
    for ln in note_lines:
        L.append(ln)
    if note_lines:
        L.append("")
    if report["skipped"]:
        L.append("Skipped (degraded to empty):")
        for s in report["skipped"]:
            L.append(f"- **{s['population']}** — {s['reason']}")
        L.append("")

    # ---- coverage notes (deferred checks + pre-Phase-2 manifests) ----
    cov_notes = report.get("coverage_notes") or []
    if cov_notes:
        L.append("## Coverage notes (what the audit did NOT fully check)")
        L.append("")
        for note in cov_notes:
            L.append(f"- {note}")
        L.append("")

    # ---- set diffs ----
    L.append("## Population set-differences (the seams)")
    L.append("")
    c = sd["counts"]
    L.append(f"Counts — D {c['D']} · Z {c['Z']} · M {c['M']} · R(finalized) {c['R_finalized']} · F {c['F']}")
    L.append("")
    for label, keyname in (("zip present, no datasheet row (`SET-Z-NO-D`)", "z_without_d"),
                           ("datasheet row, no uploaded zip (`SET-D-NO-Z`)", "d_without_z"),
                           ("materialized but never scored (`SET-M-NO-R`)", "materialized_no_run"),
                           ("finalized run, no materialized tree — ledger drift (`SET-R-NO-M`)",
                            "run_without_materialize")):
        items = sd[keyname]
        shown = ", ".join(f"`{s}`" for s in items) if items else "—"
        L.append(f"- {label}: {len(items)}  {shown}")
    L.append("")

    # ---- findings, FAIL then WARN then INFO, grouped by boundary then action ----
    L.append("## Findings — FAIL → WARN → INFO, by boundary + action")
    L.append("")
    per_row = [(r.study, f) for r in result.rows for f in r.findings]
    if not per_row and not result.global_findings:
        L.append("No findings.")
        L.append("")
    else:
        for bnd in (sa.B0, sa.B1, sa.B2, sa.B3, sa.B4, sa.ENGINE):
            bucket = [(st, f) for st, f in per_row
                      if (sa.CHECKS[f.code].boundary if f.code in sa.CHECKS else sa.ENGINE) == bnd]
            glob = [f for f in result.global_findings
                    if (sa.CHECKS[f.code].boundary if f.code in sa.CHECKS else sa.ENGINE) == bnd]
            if not bucket and not glob:
                continue
            L.append(f"### {_BOUND_LABEL[bnd]}")
            L.append("")
            rows_sorted = sorted(
                [(None, f) for f in glob] + [(st, f) for st, f in bucket],
                key=lambda sf: (_SEV_RANK.get(sf[1].severity, 9), sf[1].action, sf[1].code))
            L.append("| severity | action | code | study | detail |")
            L.append("|---|---|---|---|---|")
            for st, f in rows_sorted:
                study = f"`{st}`" if st else "_(global)_"
                msg = f.message.replace("|", "\\|")
                L.append(f"| {f.severity} | {f.action} | {f.code} | {study} | {msg} |")
            L.append("")

    # ---- collisions ----
    collisions = [f for f in result.global_findings if f.code == sa.NAME_COLLISION]
    L.append("## Reused study numbers (REMIND — cosmetic, breaks nothing)")
    L.append("")
    if collisions:
        for f in collisions:
            occ = ", ".join(f"`{o}`" for o in f.data.get("occurrences", []))
            L.append(f"- number `{f.data.get('number')}` reused by {len(f.data.get('occurrences', []))}: {occ}")
    else:
        L.append("None.")
    L.append("")

    # ---- CoV table ----
    L.append(f"## Force reliability (isokinetic CoV gate = {sa.COV_GATE_PCT:g}%)")
    L.append("")
    L.append("Isometric peak torque is the PRIMARY criterion (never gated); isokinetic torque/power "
             "are gated on the isokinetic CoV.")
    L.append("")
    L.append("| study | isometric | isokinetic | CoV % | reliable? |")
    L.append("|---|---|---|---|---|")
    for c in report["cov_table"]:
        cov = "—" if c["cov_pct"] is None else f"{c['cov_pct']:g}"
        rel = "blank" if c["all_blank"] else ("—" if c["reliable"] is None
                                              else ("yes" if c["reliable"] else "NO"))
        L.append(f"| `{c['study']}` | {'✓' if c['isometric'] else '—'} | "
                 f"{'✓' if c['isokinetic'] else '—'} | {cov} | {rel} |")
    L.append("")
    return "\n".join(L)


# ============================ console ============================
def print_summary(report: dict, result: sa.AuditResult) -> None:
    fn, sd = report["funnel"], report["set_diffs"]
    pl = report["populations_loaded"]
    def _popcell(k, lbl):
        info = pl.get(k, {})
        n, nl = info.get("n", 0), info.get("n_loaded", info.get("n", 0))
        extra = "" if info.get("loaded") else " (SKIPPED)"
        if nl != n:
            extra += f" ({nl} ledger)"
        return f"{lbl} {n}{extra}"

    print(f"\n=== STUDY AUDIT === {report['generated_at']}")
    print("populations: " + " | ".join(_popcell(k, lbl) for k, lbl in (
        ("D", "D datasheet"), ("Z", "Z zips"), ("M", "M mat"),
        ("R", "R scored"), ("F", "F force"))))
    if report["skipped"]:
        for s in report["skipped"]:
            print(f"  [skipped] {s['population']}: {s['reason']}")

    print(f"\nfunnel: {fn['headline']}")
    if fn["validatable_keys"]:
        print("  validatable: " + ", ".join(fn["validatable_keys"]))
    if fn["reliable_keys"]:
        print("  reliable:    " + ", ".join(fn["reliable_keys"]))

    print(f"\nset-diffs: Z\\D {len(sd['z_without_d'])} | D\\Z {len(sd['d_without_z'])} | "
          f"M-no-run {len(sd['materialized_no_run'])} | "
          f"run-no-M {len(sd['run_without_materialize'])}"
          + (f" ({', '.join(sd['run_without_materialize'])})" if sd['run_without_materialize'] else ""))

    # severity / action tallies across row + global findings
    allf = [f for r in result.rows for f in r.findings] + result.global_findings
    by_sev = {s: sum(1 for f in allf if f.severity == s) for s in (sa.FAIL, sa.WARN, sa.INFO)}
    by_act = {a: sum(1 for f in allf if f.action == a) for a in (sa.ASK, sa.REMIND, sa.AUTO)}
    print(f"findings: FAIL {by_sev[sa.FAIL]} | WARN {by_sev[sa.WARN]} | INFO {by_sev[sa.INFO]}   "
          f"(ASK {by_act[sa.ASK]}, REMIND {by_act[sa.REMIND]}, AUTO {by_act[sa.AUTO]})")
    for r in result.rows:
        for f in r.findings:
            if f.severity == sa.FAIL:
                print(f"  FAIL {f.code:16} {r.study}: {f.message}")

    cov = report["cov_table"]
    valued = sum(1 for x in cov if not x["all_blank"])
    blank = sum(1 for x in cov if x["all_blank"])
    over = sum(1 for x in cov if x["reliable"] is False)
    print(f"force: {valued} valued, {blank} blank, {len(sd_orphans(result))} orphan; CoV>{sa.COV_GATE_PCT:g}%: {over}")
    coll = [f for f in result.global_findings if f.code == sa.NAME_COLLISION]
    if coll:
        print("collisions: " + "; ".join(
            f"{f.data.get('number')} x{len(f.data.get('occurrences', []))}" for f in coll))
    for note in report.get("coverage_notes") or []:
        print(f"[coverage] {note}")


def sd_orphans(result: sa.AuditResult) -> List[str]:
    return [f.data.get("study", "") for f in result.global_findings if f.code == sa.FORCE_ORPHAN]


# ============================ driver ============================
def run_audit(cfg: dict) -> Tuple[sa.AuditResult, dict, List[Tuple[str, str]]]:
    """Load the five populations (each isolated), reconcile via the pure engine, build the report."""
    skips: List[Tuple[str, str]] = []

    # D — datasheet (live read). On failure, degrade to empty + a skip note.
    datasheet, raw_d_names, study_to_row, study_to_vals = {}, [], {}, {}
    d_loaded = False
    try:
        datasheet, raw_d_names, study_to_row, study_to_vals = load_datasheet(cfg)
        d_loaded = True
    except Exception as exc:  # noqa: BLE001
        skips.append(("D datasheet", f"{type(exc).__name__}: {exc}"))

    # Z — mirror zips. Per-zip errors are isolated inside the loader; wrap its SETUP too (the glob /
    # the count_zip_artifacts+check_study imports) so a failure there degrades the Z seam instead of
    # aborting the whole audit — matching the try/except the other four loaders already have.
    try:
        zips = load_zips(skips)
    except Exception as exc:  # noqa: BLE001
        zips = {}
        skips.append(("Z zips", f"{type(exc).__name__}: {exc}"))

    # M — materialized + holds (declared profile from D feeds expected-capture counts).
    declared_by_nkey = {k: v.declared_profile for k, v in datasheet.items()}
    try:
        materializes = load_materializes(declared_by_nkey)
    except Exception as exc:  # noqa: BLE001
        materializes = {}
        skips.append(("M materialized", f"{type(exc).__name__}: {exc}"))

    # R — scored runs (local ledger).
    try:
        runs = load_runs()
    except Exception as exc:  # noqa: BLE001
        runs = {}
        skips.append(("R runs", f"{type(exc).__name__}: {exc}"))

    # F — force sheet (live read).
    forces, force_orphans, raw_f_names = {}, [], []
    try:
        forces, force_orphans, raw_f_names = load_forces(
            cfg, set(datasheet), study_to_row, study_to_vals, d_loaded)
    except Exception as exc:  # noqa: BLE001
        skips.append(("F force", f"{type(exc).__name__}: {exc}"))

    pops = sa.Populations(
        datasheet=datasheet, zips=zips, materializes=materializes, runs=runs, forces=forces,
        raw_name_occurrences=_distinct_occurrences(raw_d_names, raw_f_names),
        force_orphans=force_orphans,
    )
    result = sa.audit(pops)

    # Population sizes: the headline `n` is the IN-UNIVERSE count (how many of each population joined a
    # D ∪ Z row); `n_loaded` is the raw size the loader saw. They differ only for R — iter_runs holds
    # the 282-study legacy cohort too, and re-indexing by study_path basename keeps those run records
    # OUT of the D ∪ Z universe (the join-key discipline the plan calls out). Showing both proves the
    # legacy cohort was excluded, not silently swept in.
    skip_pops = [p for p, _ in skips]
    loaded = {
        "D": {"loaded": d_loaded, "n": sum(1 for r in result.rows if r.d is not None),
              "n_loaded": len(datasheet)},
        "Z": {"loaded": True, "n": sum(1 for r in result.rows if r.z is not None),
              "n_loaded": len(zips)},
        "M": {"loaded": "M materialized" not in skip_pops,
              "n": sum(1 for r in result.rows if r.m and r.m.materialized), "n_loaded": len(materializes)},
        "R": {"loaded": "R runs" not in skip_pops,
              "n": sum(1 for r in result.rows if r.r is not None), "n_loaded": len(runs)},
        "F": {"loaded": "F force" not in skip_pops,
              "n": sum(1 for r in result.rows if r.f and r.f.in_force_sheet), "n_loaded": len(forces)},
    }
    report = build_report(result, loaded, skips)
    return result, report, skips


def write_report(report: dict, result: sa.AuditResult) -> Tuple[Path, Path]:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    json_path = AUDIT_DIR / "audit_report.json"
    md_path = AUDIT_DIR / "audit_report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report, result), encoding="utf-8")
    return json_path, md_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Reconcile the five pilot populations into findings + the validatable-N funnel.")
    ap.add_argument("--config", default="configs/round3_intake.json")
    ap.add_argument("--report", action="store_true",
                    help="write data/audit/audit_report.{json,md} (default: console only)")
    args = ap.parse_args(argv)

    cfg = json.loads((REPO_ROOT / args.config).read_text(encoding="utf-8")
                     if not Path(args.config).is_absolute() else Path(args.config).read_text("utf-8"))
    result, report, _skips = run_audit(cfg)
    print_summary(report, result)

    if args.report:
        jp, mp = write_report(report, result)
        print(f"\n[report] wrote {jp.relative_to(REPO_ROOT)} + {mp.relative_to(REPO_ROOT)}")
    else:
        print("\n[dry-run] console only. --report writes data/audit/audit_report.{json,md}.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

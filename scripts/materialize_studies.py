#!/usr/bin/env python3
"""Materialize intake-cache study zips into the studies_root/ layout the assessment pipeline
discovers — the missing bridge between intake (sync_records / check_studies, which only PARSE
and VERIFY a zip) and the scorer (discover_studies -> run_pipeline -> condition score, which
walks an UNPACKED on-disk tree). Before this, a green-verdict pilot study sat as a zip in
data/intake_cache/ and was invisible to discover_studies(); nothing unpacked it.

What it produces, per zip — one discoverable study + a non-discoverable annex:

    studies_root/<operator>/<session>/<study>/
        <study>_<ts>_<idx>.dcm          PRIMARY = the dominant-leg RF SWEEP capture's
        <study>_<ts>.mp4                  depth-matched .dcm (4.9cm) + mp4 + frames. This single
        RawImagesForResearch_<ts>/        (.dcm, .mp4, RawImagesForResearch_) triple is what
            <base>.jpg ...                _scan_study_dir validates and run_pipeline scores.
        captures/                         ANNEX: every OTHER capture's FRAMES, preserved.
            c1_femur_deep/  <dcm><mp4><raw>   Each capture's .dcm is depth-matched by RELATIVE RANK
            c2_rf_shallow/  <mp4><raw>        (deepest = femur, shallower = sweep/RF — NO fixed
            _dcm_pool/<name>__6.9cm.dcm        protocol depth, so a deeper-probe study materializes
            _dcm_pool/<name>__4.9cm.dcm        at its OWN geometry); a capture with no plausible .dcm
        materialize_manifest.json              is left dcm-less and every UNUSED .dcm is pooled here
                                               LABELED by its measured depth, so a later compounder
                                               resolves each capture's geometry by depth.

Why DEPTH-RANK, not timestamp: operators SAVE-IMAGE before/after MAKE-VIDEO, so deconfliction's
by-time .dcm attachment is scrambled (a sweep's own .dcm orphans; a still steals the next .dcm).
The reliable signal is absolute depth = Rows x PixelSpacing, ranked WITHIN the study (no fixed cm
target — probe-831 runs the shallow plane at 6.9, not 4.9). studies_root studies aren't in
transport_metadata, so spacing comes from the study's own .dcm — a wrong-depth .dcm would corrupt
the scale, so rank-matching keeps the femur from stealing the sweep's plane AND a guard HOLDS a
sweep whose own (shallow) .dcm was skipped rather than scale it with a leftover femur-plane .dcm.

Why the SWEEP is the discoverable primary: the 282-study landmark cohort + the score's Fat /
Texture / RF-size axes are all sweep-based. The femur only adds the STAR/QMT axis. The bridge is
LOSSLESS — every capture's frames + a depth-labeled .dcm pool are on disk — but the SINGLE-RUN
score for a sweep-primary intake lands STAR/QMT at the no_femur tier until a captures/-aware
compounder composes the STAR axis from the femur capture. Discovery allows ONE study_code per
discoverable triple, so a both-legs sub-cohort (two sweeps) needs a dominant-leg decision we can't
infer from metadata — those are HELD until leg assignment exists. v1 materializes single-sweep MAIN.

    python scripts/materialize_studies.py                                   # DRY-RUN every intake_cache zip
    python scripts/materialize_studies.py --zip data/intake_cache/X.zip --operator akadir
    python scripts/materialize_studies.py --zip ... --operator akadir --apply

Default is DRY-RUN (prints the planned tree, writes nothing). Reads the source zip READ-ONLY;
the only writes are under --studies-root (default env.STUDIES_ROOT) and --hold-dir.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for `preflight_helpers`

from preflight_helpers import check_study as cs
from preflight_helpers import env
from preflight_helpers.study_deconfliction import Capture, deconflict_zip
from preflight_helpers.study_discovery import discover_studies, list_frame_bases

REPO_ROOT = Path(__file__).resolve().parents[1]
_FRAME_BASE_RE = re.compile(r"^\d{8}_\d{9}$")   # mirrors study_discovery._FRAME_BASE_RE
_RAW_LITERAL = "RawImagesForResearch_"          # the EXACT prefix discovery requires
_FEMUR_GAP_CM = 1.0   # a .dcm at least this much deeper than the shallow plane is the femur's


def _is_junk(name: str) -> bool:
    """Device-trash / hidden / OS-cruft paths (matches study_deconfliction / study_artifacts)."""
    return any(p.startswith(".trashed-") or p.startswith(".") or p == "__MACOSX"
               for p in name.split("/") if p)


def _session_for(study: str) -> str:
    """The date-bucketed session dir: the leading date token + the per-date count if present.
    Derived structurally (not via grammar.parse) so legacy spelled-out-sex names like
    '10jun26_001_anonaam_anonacx_anonacu_25_erkek_174_70' — which the <age><sex> grammar rejects — still
    bucket as '10jun26_001' instead of degrading to the full (doubled, PHI-bearing) study name."""
    toks = study.split("_")
    if not toks:
        return study
    if len(toks) > 1 and toks[1].isdigit():
        return f"{toks[0]}_{toks[1]}"
    return toks[0]


# ---------- result of planning one zip ----------
@dataclass
class StudyPlan:
    zip_path: str
    study: str
    operator: str
    session: str
    protocol: str
    verdict: str                       # source-zip check_study headline
    materialize: bool                  # False == HELD
    reason: str = ""                   # why held / a note
    primary: Optional[Capture] = None
    primary_dcm: Optional[str] = None  # depth-matched .dcm zip-member for the sweep geometry
    primary_dcm_note: str = ""
    primary_nframes: int = 0
    primary_caveats: List[str] = field(default_factory=list)
    dcm_depths: Dict[str, Optional[float]] = field(default_factory=dict)  # member -> measured cm
    dcm_for: Dict[str, str] = field(default_factory=dict)   # capture.raw_folder -> depth-matched .dcm member
    pool: List[str] = field(default_factory=list)           # leftover .dcm members (depth-labeled on write)
    annex: List[Capture] = field(default_factory=list)
    orphan_dcms: List[str] = field(default_factory=list)
    excluded_captures: List[dict] = field(default_factory=list)  # auto-corrected orphan duplicates (Phase 2)
    ambiguous_captures: List[dict] = field(default_factory=list)  # lone orphans KEPT for review (Ask tier)


# ---------- zip introspection ----------
def _all_dcm_names(zf: zipfile.ZipFile) -> List[str]:
    return [n for n in zf.namelist() if n.lower().endswith(".dcm") and not _is_junk(n)]


def _frame_bases_in_zip(zf: zipfile.ZipFile, raw_folder: str) -> List[str]:
    """Bare {base}.jpg stems matching ^\\d{8}_\\d{9}$ under a RawImages folder — replicates
    list_frame_bases on the zip, so we can enforce the non-empty-frames guard discovery omits."""
    prefix = raw_folder.rstrip("/") + "/"
    bases = set()
    for nm in zf.namelist():
        if not nm.startswith(prefix) or nm.endswith("/") or _is_junk(nm):
            continue
        rel = nm[len(prefix):]
        if "/" in rel:
            continue
        p = Path(rel)
        if p.suffix == ".jpg" and _FRAME_BASE_RE.match(p.stem):
            bases.add(p.stem)
    return sorted(bases)


def _own_plane_cm(c: Capture) -> Optional[float]:
    """The capture's own depth from its frame width alone — no .dcm involved, so it is independent
    of every rank inference. Returns the snapped device step, or None when the width is unknown."""
    if not c.width:
        return None
    try:
        from preflight_helpers.leltek_deconfliction import depth_from_jpg_width, snap_to_device_step
        return snap_to_device_step(depth_from_jpg_width(c.width))
    except Exception:                                    # noqa: BLE001 - advisory signal only
        return None


def _assign_dcms(captures: List[Capture], depths: Dict[str, Optional[float]]
                 ) -> Tuple[Dict[str, str], List[str], List[str]]:
    """Depth-match each capture to a .dcm by RELATIVE RANK — no fixed protocol depths (Akadir
    2026-06-25). The femur (the deepest capture, per the deconfliction's relative width) takes the
    DEEPEST .dcm, but only when that .dcm sits a clear plane (>= _FEMUR_GAP_CM) below the rest —
    else the femur's own deep .dcm is missing and it materializes geometry-less (flagged). The
    shallow captures (the sweep FIRST, so the discoverable primary is guaranteed geometry, then the
    RF) take the SHALLOWEST remaining .dcm. This unpins the depth: a deeper-probe sweep at 6.9 cm
    gets its own 6.9 cm geometry instead of being rejected for not being 4.9. A GUARD (below) then
    drops any shallow capture whose nearest .dcm is a femur-plane one (its own shallow .dcm was
    skipped) so a sweep never materializes at a wrong, deep scale. Returns
    (dcm_for {raw_folder: member}, pool [leftover members], flags)."""
    valid = sorted((d, n) for n, d in depths.items() if d is not None)   # shallow .. deep
    dcm_for: Dict[str, str] = {}
    flags: List[str] = []
    unreadable = [n for n, d in depths.items() if d is None]
    if not valid:
        return dcm_for, unreadable, ["no readable .dcm depths"]
    used: set = set()
    femur = next((c for c in captures if c.identity == "femur"), None)
    if femur is not None:
        deepest_d, deepest_n = valid[-1]
        shallower = [d for d, _ in valid[:-1]]
        if not shallower or deepest_d - max(shallower) >= _FEMUR_GAP_CM:
            dcm_for[femur.raw_folder] = deepest_n
            used.add(deepest_n)
        else:
            flags.append(f"femur cine present but no deeper .dcm (deepest {deepest_d}cm ≈ the "
                         f"shallow plane) — femur geometry deferred to a frame-width fallback")
    remaining = [n for _, n in valid if n not in used]   # shallow .. deep
    shallow_caps = sorted((c for c in captures if c.identity in ("sweep", "rf")),
                          key=lambda c: 0 if c.is_sweep else 1)   # sweep first
    for c in shallow_caps:
        if remaining:
            dcm_for[c.raw_folder] = remaining.pop(0)     # shallowest first
            used.add(dcm_for[c.raw_folder])
    # GUARD against wrong-scale geometry: a shallow capture must never be scaled by a FEMUR-plane
    # .dcm. The relative rank alone misses a femur + retake (two deep .dcm) whose sweep .dcm was
    # skipped — the sweep then grabs the leftover deep one. So if the femur took a deep .dcm and a
    # sweep/RF ended up within _FEMUR_GAP_CM of it, that capture's own (shallower) .dcm is missing:
    # drop the assignment (the sweep HOLDs upstream rather than materialize at a deep, wrong scale).
    # Residual: a shallow capture with NO femur capture present + only deep .dcm — the width->depth
    # table (future work) would be needed to catch that doubly-degenerate zip.
    if femur is not None and femur.raw_folder in dcm_for:
        df = depths[dcm_for[femur.raw_folder]]
        for c in shallow_caps:
            n = dcm_for.get(c.raw_folder)
            if n is not None and df is not None and df - depths[n] <= _FEMUR_GAP_CM:
                # Before discarding, ask the UNIVERSAL signal — the capture's own frame width, which
                # needs no .dcm at all (`width x depth = FOV_CONST_128L`; it recovers the device step
                # 7/7, worst raw error 0.06 cm against ~1 cm spacing). If the assigned .dcm sits at
                # the capture's OWN plane, it is not a femur-plane ruler: the rank comparison is what
                # is wrong, not the assignment. This is the "width->depth table" the note below
                # anticipated, and `Capture.width` had been passed in here unused all along.
                # Live case: femur 6.9 / sweep 5.9 — a gap of EXACTLY _FEMUR_GAP_CM, so the femur
                # claim (`>=`) and this guard (`<=`) both fired at 1.0 and a structurally perfect
                # study was held out of the cohort with a "bozuk zip" label (2026-08-03, sheet 113).
                own = _own_plane_cm(c)
                if own is not None and abs(own - depths[n]) < 0.05:
                    flags.append(f"{c.identity} .dcm {depths[n]}cm confirmed by its own frame width "
                                 f"({c.width}px -> {own}cm); kept despite the {df}cm femur plane")
                    continue
                del dcm_for[c.raw_folder]
                used.discard(n)
                flags.append(f"{c.identity} own .dcm missing — the nearest is a femur-plane "
                             f"{depths[n]}cm (too deep to scale a shallow capture); left unscaled")
    pool = [n for _, n in valid if n not in used] + unreadable
    return dcm_for, pool, flags


# ---------- auto-correct (orphan exclusion) ----------
_DEDUPE_IDENTITIES = ("femur", "rf", "sweep")   # the known identities an exclusion can dedupe against


def _classify_orphans(captures: List[Capture]) -> Tuple[List[Capture], List[dict], List[str]]:
    """Auto-correct surplus orphan captures NON-DESTRUCTIVELY (the safety rule: never mutate the
    source, never auto-drop on ambiguity). An ORPHAN capture (its RawImages had no mutually-nearest
    mp4 per the deconfliction — a deleted/re-recorded take, or a borrowed still) is auto-EXCLUDED
    *only* when another capture of the SAME known identity (femur/rf/sweep) is PAIRED — i.e. it is a
    redundant duplicate leftover beside a clean take (anonaal's 2nd femur 143212 beside the paired
    143250). A LONE orphan — the only take of its identity (a borrowed femur with no in-zip video),
    or an unclassified one — is KEPT and escalated to an Ask-tier caveat, never silently dropped.
    Returns (excluded_captures, excluded_meta, ambiguous_meta) — excluded_meta and ambiguous_meta each
    a list of {folder,nframes,identity,reason}; the reason doubles as the human caveat string."""
    paired_identities = {c.identity for c in captures
                         if c.pairing == "paired" and c.identity in _DEDUPE_IDENTITIES}
    excluded: List[Capture] = []
    excluded_meta: List[dict] = []
    ambiguous_meta: List[dict] = []
    for c in captures:
        if c.pairing != "orphan":
            continue
        if c.identity in paired_identities:
            reason = (f"orphan {c.identity} (no mp4 — leftover/re-recorded take) redundant beside a "
                      f"paired {c.identity} capture; excluded as a duplicate")
            excluded.append(c)
            excluded_meta.append({"folder": Path(c.raw_folder).name, "nframes": c.nframes,
                                  "identity": c.identity, "reason": reason})
        else:
            what = c.identity if c.identity in _DEDUPE_IDENTITIES else "unclassified"
            reason = (f"orphan {what} capture {Path(c.raw_folder).name} (no mp4) is the only {what} "
                      f"take — KEPT for review (borrowed/missing video?), not auto-excluded")
            ambiguous_meta.append({"folder": Path(c.raw_folder).name, "nframes": c.nframes,
                                   "identity": c.identity, "reason": reason})
    return excluded, excluded_meta, ambiguous_meta


# ---------- planning ----------
def _unified_verdict(zip_path: str, operator: str, plan: StudyPlan) -> str:
    """The operator-facing headline from the UNIFIED relative-rank model — the same one
    check_studies writes to the sheet, so the manifest can no longer contradict it.

    Before this, planning stamped `cs.check_study(...).headline`, which tests the femur at an
    ABSOLUTE 6.9 cm and so reported "femur (6.9cm) eksik" for a femur imaged at 7.9 cm. Every
    manifest written on 2026-07-30 carries that false negative (⚠️80 / ❌13, zero ✅) while the
    sheet correctly read ✅ — the same false-femur-eksik class that `study_qa` was written to kill.

    Imported inside the function on purpose: study_qa imports plan_zip from this module, so a
    module-level import would be circular. Passing `plan` also stops study_qa re-planning the zip,
    which is what would otherwise recurse.
    """
    from study_qa import study_qa, render_cell    # local: breaks the study_qa <-> plan_zip cycle
    return render_cell(study_qa(zip_path, operator, plan=plan))


def plan_zip(zip_path: str, operator: str) -> StudyPlan:
    """Plan one zip, then stamp the verdict. Split so the verdict is computed ONCE, after the
    captures are resolved, and lands on every return path of `_plan_captures` (there are five)."""
    p = _plan_captures(zip_path, operator)
    p.verdict = _unified_verdict(zip_path, operator, p)
    return p


def _plan_captures(zip_path: str, operator: str) -> StudyPlan:
    study = Path(zip_path).stem
    protocol = cs.study_protocol(zip_path)
    res = deconflict_zip(zip_path)
    verdict = ""            # stamped by plan_zip once the captures below are resolved

    # AUTO-CORRECT (Phase 2): drop redundant orphan DUPLICATES before selection, so a leftover take
    # can neither inflate the sweep count (a false both-legs HOLD) nor be picked as a capture. A lone
    # orphan — the only take of its identity — is kept + flagged ASK (see _classify_orphans). The
    # source zip is never touched; exclusion lives in this materialized view + the manifest.
    excluded, excluded_meta, ambiguous_meta = _classify_orphans(res.captures)
    excluded_folders = {c.raw_folder for c in excluded}
    kept = [c for c in res.captures if c.raw_folder not in excluded_folders]

    p = StudyPlan(zip_path=zip_path, study=study, operator=operator, session=_session_for(study),
                  protocol=protocol, verdict=verdict, materialize=False,
                  orphan_dcms=list(res.orphan_dcms), excluded_captures=excluded_meta,
                  ambiguous_captures=ambiguous_meta)

    sweeps = [c for c in kept if c.is_sweep]
    if not sweeps:
        p.reason = "no sweep capture (sweep eksik) — nothing cohort-comparable to materialize"
        return p
    if len(sweeps) > 1:
        p.reason = (f"{len(sweeps)} sweeps (both-legs sub-cohort) — needs a dominant-leg "
                    f"decision this bridge can't infer; held for leg assignment")
        return p

    primary = sweeps[0]
    with zipfile.ZipFile(zip_path) as zf:
        bases = _frame_bases_in_zip(zf, primary.raw_folder)
        if not bases:
            p.reason = "primary sweep has no conforming frames — would score 'picked_frames empty'"
            return p
        depths = {nm: cs._dcm_depth_cm(zf, nm) for nm in _all_dcm_names(zf)}
        dcm_for, pool, assign_flags = _assign_dcms(kept, depths)
        primary_dcm = dcm_for.get(primary.raw_folder)
        if primary_dcm is None:
            measured = sorted({d for d in depths.values() if d is not None})
            why = ("no .dcm depths readable (pydicom missing?)" if not measured
                   else f"no .dcm at the sweep's own (shallow) plane (measured {measured})")
            p.reason = f"sweep geometry unresolvable: {why} — would raise in run_pipeline"
            return p

    p.materialize = True
    p.primary = primary
    p.primary_dcm = primary_dcm
    p.primary_dcm_note = f"depth-matched .dcm at {depths[primary_dcm]}cm (relative rank — sweep = shallow plane)"
    p.primary_nframes = len(bases)
    # A length-ambiguous sweep (frame count near the sweep/still boundary) is materialized with a
    # CAVEAT, not held: is_sweep already cleared the >=200-frame bar, and the band's ceiling (320
    # frames) overlaps normal sweeps — the real 10jun26 sweep is 228. The flag rides into the
    # manifest so a human can spot-check it is a sweep, not a long still, without blocking the score.
    p.primary_caveats = ([fl for fl in primary.flags if fl.startswith("length-ambiguous")]
                         + assign_flags + [a["reason"] for a in ambiguous_meta])
    p.dcm_depths = depths
    p.dcm_for = dcm_for
    p.pool = pool
    p.annex = [c for c in kept if c is not primary]
    return p


# ---------- writing ----------
def _copy_member(zf: zipfile.ZipFile, member: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(zf.read(member))


def _normalize_raw_dirname(basename: str) -> str:
    """Force the destination RawImages folder name to discovery's exact literal. Intake counts
    'rawimages*' case-insensitively, but discovery requires 'RawImagesForResearch_' verbatim, so
    a future odd-cased capture would be counted yet undiscoverable. No-op for the real pilots."""
    if basename.startswith(_RAW_LITERAL):
        return basename
    low = basename.lower()
    if low.startswith(_RAW_LITERAL.lower()):
        return _RAW_LITERAL + basename[len(_RAW_LITERAL):]
    if low.startswith("rawimages"):
        return _RAW_LITERAL + basename[len("rawimages"):].lstrip("_")
    return _RAW_LITERAL + basename


def _copy_capture(zf: zipfile.ZipFile, cap: Capture, dest_dir: Path, dcm: Optional[str]) -> int:
    """Copy a capture's mp4 + the given (depth-matched) .dcm + its RawImages frame tree into
    dest_dir verbatim. `dcm` may be None (no target-depth .dcm). Returns the bare-frame count."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    if cap.mp4:
        _copy_member(zf, cap.mp4, dest_dir / Path(cap.mp4).name)
    if dcm:
        _copy_member(zf, dcm, dest_dir / Path(dcm).name)
    raw_dest = dest_dir / _normalize_raw_dirname(Path(cap.raw_folder).name)
    raw_dest.mkdir(parents=True, exist_ok=True)
    prefix = cap.raw_folder.rstrip("/") + "/"
    nframes = 0
    for nm in zf.namelist():
        if not nm.startswith(prefix) or nm.endswith("/") or _is_junk(nm):
            continue
        rel = nm[len(prefix):]
        if "/" in rel:
            continue
        _copy_member(zf, nm, raw_dest / rel)
        if Path(rel).suffix == ".jpg" and _FRAME_BASE_RE.match(Path(rel).stem):
            nframes += 1
    return nframes


def write_plan(p: StudyPlan, studies_root: Path, *, force: bool) -> Tuple[bool, str]:
    """Materialize a plan under studies_root ATOMICALLY (stage into a .partial sibling, then
    os.replace) so a mid-copy crash never leaves a sticky half-tree. Returns (ok, message)."""
    study_dir = studies_root / p.operator / p.session / p.study
    if study_dir.exists() and not force:
        return False, f"exists, skipped (use --force): {study_dir}"
    tmp_dir = study_dir.with_name(study_dir.name + ".partial")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    try:
        annex_meta, pool_meta = [], []
        with zipfile.ZipFile(p.zip_path) as zf:
            _copy_capture(zf, p.primary, tmp_dir, p.primary_dcm)
            cap_root = tmp_dir / "captures"
            for i, c in enumerate(p.annex, start=1):
                dcm = p.dcm_for.get(c.raw_folder)        # depth-matched at plan time (relative rank)
                nf = _copy_capture(zf, c, cap_root / f"c{i}_{c.identity}_{c.depth_setting}", dcm)
                annex_meta.append({"dir": f"c{i}_{c.identity}_{c.depth_setting}",
                                   "identity": c.identity, "depth_setting": c.depth_setting,
                                   "nframes": nf, "dcm": Path(dcm).name if dcm else None,
                                   "dcm_depth_cm": p.dcm_depths.get(dcm) if dcm else None})
            for nm in p.pool:                            # leftover .dcm -> depth-labeled pool
                d = p.dcm_depths.get(nm)
                tag = f"{d}cm" if d is not None else "unknownDepth"
                _copy_member(zf, nm, cap_root / "_dcm_pool" / f"{Path(nm).stem}__{tag}.dcm")
                pool_meta.append({"dcm": Path(nm).name, "depth_cm": d})
            manifest = {
                "source_zip": str(p.zip_path),
                "materialized_at": datetime.now().isoformat(timespec="seconds"),
                "study": p.study, "operator": p.operator, "session": p.session,
                "protocol": p.protocol, "source_zip_verdict": p.verdict,
                "primary": {"identity": p.primary.identity, "depth_setting": p.primary.depth_setting,
                            "nframes": p.primary_nframes, "dcm": Path(p.primary_dcm).name,
                            "dcm_depth_cm": p.dcm_depths.get(p.primary_dcm),
                            "dcm_resolution": p.primary_dcm_note, "caveats": p.primary_caveats},
                "annex": annex_meta,
                # orphan duplicate takes auto-excluded at ingest (Phase 2) — frames NOT copied; the
                # source zip is untouched. A lone orphan is NOT here (it is kept; see ambiguous_captures).
                "excluded_captures": p.excluded_captures,
                # lone orphans KEPT for review (Ask tier) — the STRUCTURED contract the audit reads for
                # PAIRING-AMBIGUOUS instead of substring-matching the human caveats.
                "ambiguous_captures": p.ambiguous_captures,
                "dcm_pool": pool_meta,
                # timestamp-orphans the depth-rank did NOT rescue (the assigned ones are removed —
                # a DICOM used as a capture's geometry is not an orphan, whatever its timestamp said).
                "orphan_dcms": [Path(o).name for o in p.orphan_dcms if o not in p.dcm_for.values()],
                "note": "verdict computed on the SOURCE zip; re-running check_study on this "
                        "sweep-primary tree would falsely report 'femur eksik' (the femur is under "
                        "captures/). Each capture's .dcm is depth-matched by RELATIVE RANK (deepest "
                        "= femur, shallow = sweep/RF — no fixed protocol depth); leftover .dcm are "
                        "pooled in captures/_dcm_pool/ labeled by measured depth.",
            }
            (tmp_dir / "materialize_manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if study_dir.exists():
            shutil.rmtree(study_dir)
        os.replace(tmp_dir, study_dir)
    except Exception as e:   # never leave a half-written tree behind
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return False, f"materialize FAILED ({type(e).__name__}: {e}); partial tree removed"
    return True, f"materialized -> {study_dir}"


def verify_in_place(study_dir: Path, studies_root: Path) -> Tuple[bool, str]:
    """Re-run discovery on the new tree: the study must be valid WITH frames. The captures/ annex
    sits one level below the study dir, so discovery's fixed-depth operator/session/study walk
    structurally cannot surface it as a second study (no run-time leak check needed)."""
    hits = [e for e in discover_studies(studies_root) if e.study_path == study_dir]
    if not hits:
        return False, "discover_studies did not return the new study"
    e = hits[0]
    if not e.valid:
        return False, f"discovered but INVALID: drop_reason={e.drop_reason}"
    n = len(list_frame_bases(e.raw_image_dir))
    if n == 0:
        return False, "valid but zero frames (would raise 'picked_frames is empty')"
    return True, f"valid, {n} frames (annex stays below the discovered study level)"


# ---------- driver ----------
def _load_records_csv(path: Optional[str]) -> dict:
    if not path:
        return {}
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            study = (row.get("Study Adı") or row.get("study") or "").strip()
            op = (row.get("Operator") or row.get("operator") or "").strip()
            if study and op:
                out[study] = op
    return out


def _print_plan(p: StudyPlan) -> None:
    head = "MATERIALIZE" if p.materialize else "HOLD"
    print(f"\n[{head}] {p.study}  ({p.protocol}; verdict {p.verdict})")
    print(f"    operator/session: {p.operator}/{p.session}")
    if not p.materialize:
        print(f"    reason: {p.reason}")
        return
    print(f"    primary sweep: {p.primary_nframes} frames; dcm = {p.primary_dcm_note}")
    if p.primary_caveats:
        print(f"    caveat: {'; '.join(p.primary_caveats)}")
    print(f"    annex captures ({len(p.annex)}): "
          + ", ".join(f"{c.identity}/{c.depth_setting}/{c.nframes}f" for c in p.annex)
          + (f"; orphan .dcm x{len(p.orphan_dcms)}" if p.orphan_dcms else ""))
    if p.excluded_captures:
        print(f"    auto-excluded duplicate(s) ({len(p.excluded_captures)}): "
              + ", ".join(f"{e['folder']} [{e['identity']},{e['nframes']}f]" for e in p.excluded_captures))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Materialize intake zips into the studies_root layout.")
    ap.add_argument("--zip", action="append", help="a specific zip (repeatable); default = all in --intake-cache")
    ap.add_argument("--operator", default=None, help="operator (studies_root level); required unless --records-csv covers the study")
    ap.add_argument("--records-csv", default=None, help="CSV mapping Study Adı -> Operator (e.g. a records_list export)")
    ap.add_argument("--intake-cache", default="data/intake_cache", help="where cached zips live")
    ap.add_argument("--studies-root", default=str(env.STUDIES_ROOT), help="output root (default env.STUDIES_ROOT)")
    ap.add_argument("--hold-dir", default="data/intake_hold", help="where HELD studies record why")
    ap.add_argument("--apply", action="store_true", help="perform writes (default: dry-run)")
    ap.add_argument("--force", action="store_true", help="overwrite an existing materialized study dir")
    args = ap.parse_args(argv)

    studies_root = Path(args.studies_root)
    hold_dir = Path(args.hold_dir)
    records = _load_records_csv(args.records_csv)
    if args.zip:
        zips = [str(REPO_ROOT / z) if not Path(z).is_absolute() else z for z in args.zip]
    else:
        zips = sorted(str(q) for q in (REPO_ROOT / args.intake_cache).glob("*.zip"))
    if not zips:
        print("no zips to process")
        return 0

    materialized = held = failed = 0
    for zp in zips:
        study = Path(zp).stem
        operator = args.operator or records.get(study)
        if not operator:
            print(f"\n[ERROR] {study}: no operator (pass --operator or a --records-csv row); skipped")
            failed += 1
            continue
        p = plan_zip(zp, operator)
        _print_plan(p)
        if not p.materialize:
            held += 1
            if args.apply:
                hold_dir.mkdir(parents=True, exist_ok=True)
                (hold_dir / f"{study}.hold.json").write_text(json.dumps(
                    {"study": study, "reason": p.reason, "verdict": p.verdict,
                     "held_at": datetime.now().isoformat(timespec="seconds")},
                    ensure_ascii=False, indent=2), encoding="utf-8")
            continue
        if not args.apply:
            continue
        ok, msg = write_plan(p, studies_root, force=args.force)
        print(f"    {msg}")
        if not ok:
            failed += 1
            continue
        vok, vmsg = verify_in_place(studies_root / p.operator / p.session / p.study, studies_root)
        print(f"    verify: {'OK' if vok else 'FAILED'} — {vmsg}")
        if vok:
            materialized += 1
            stale = hold_dir / f"{study}.hold.json"   # this study no longer holds
            if stale.exists():
                stale.unlink()
        else:
            failed += 1

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n[{mode}] {materialized} materialized, {held} held, {failed} failed "
          f"(of {len(zips)} zip(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

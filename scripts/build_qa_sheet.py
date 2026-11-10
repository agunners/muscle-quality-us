#!/usr/bin/env python3
"""Build the generated 'qa' projection for the hasta_kayit sheet — DRY (preview + payload only).

The 'qa' tab is a GENERATED PROJECTION, never a system of record: one row per materialized study,
keyed on the PHI-safe study_uid (= 'NR'+sha8(dirname), `study_metadata.study_uid_for_dirname`, the
same key the new-round cohort mints). It re-uses existing producers — it re-segments nothing:

  BATCH SPINE (every materialized study under data/studies_root/**):
    identity          study_uid · study_name(PHI) · operator · session · date
    acquisition       kontrol_verdict + rf_depth_flag   (live 'kayit' sheet: Kontrol Sonucu W /
                      Kontrol Detayı X; the 'RF hafif derin/sığ (Ncm)' caveat parsed out of X)
    integrity         capture_verdict (CLEAN/FLAGGED + top reason)  (capture_audit.audit_tree)
    measurement_trust qmt_provenance + int_cv_trust + rf_source   (per_study_cohort_new.csv +
                      new_round_cohort_provenance.json)
    results           condition_score + band + size/fat/texture contributions (RE-DERIVED via
                      condition_model.score_patient on the csv features) + star/qmt/int_cv/fat +
                      İzometrik torque + İzo-CV (live sheet Y/Z)
    freshness         measurement_code_hash computed now, stale=False (the batch row is fresh)
    provenance        'auto-frames' (the automated top-5 selection, not operator-picked frames)

  RECORD OVERLAY (studies with a FINALIZED runs.jsonl run whose run_study_uid matches the spine):
    opsel_score · opsel_band · opsel_int_cv · texture_state · n_routed_frames · opsel_freshness
    provenance -> 'operator-frames (run <date>)'.  Latest finalized run per study_uid wins.

femur_qmt_consistency is EXCLUDED (deferred: selection-dependent + provisional threshold).

THREE PROJECTIONS, ONE BUILDER (they cannot diverge — all keyed study_uid):
  (A) the SHEET TAB — `HEADER`/`to_payload`, the lean glanceable QA columns above. NEVER widened.
  (B) `data/qa_master.csv` — `MASTER_COLUMNS`: every sheet column PLUS the analysis payload
      (confounders age/sex/height/weight/bmi, all 5 force columns, the full cohort feature set,
      the doctors' `quality_label` + `cohort_round`, `rel_path`). The canonical STUDY-LEVEL table
      for the force/state correlation work and for hybrid (CNN + tabular) modelling.
  (C) `data/qa_frames.csv` — `FRAME_COLUMNS`: the long FRAME INDEX, one row per CONTENT frame of
      every capture (not only the selected frames — MIL treats a study as a BAG, and indexing only
      the picks would bake in the frame-selection brittleness we set out to measure).
(B) and (C) bind uid -> raw dirname / frame paths = PHI, so both are gitignored AND the writer
asserts the target path is git-ignored before writing (`study_registry._assert_git_ignored`).

DRY DISCIPLINE — this module NEVER writes to the Google Sheet. It writes a local preview to
scratchpad/ (gitignored, may carry study_name PHI), prints a compact table, and ASSEMBLES the exact
addSheet+values payload a future, human-reviewed --apply step would send. --apply is an unimplemented
placeholder that raises, guarded by _WRITE_GUARD, so it cannot fire this pass.

    .venv/bin/python scripts/build_qa_sheet.py            # DRY: preview + stdout table + payload
    .venv/bin/python scripts/build_qa_sheet.py --emit-dict  # also (re)write the committed dictionary
    .venv/bin/python scripts/build_qa_sheet.py --no-sheet  # skip the live sheet read (kontrol/force blank)
    .venv/bin/python scripts/build_qa_sheet.py --analysis  # also write data/qa_master.csv + qa_frames.csv
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import math
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))          # for `preflight_helpers`

import pandas as pd

from preflight_helpers import env
from preflight_helpers import condition_model as cm
from preflight_helpers import cohort_placement as cp
from preflight_helpers import capture_audit
from preflight_helpers import study_name_grammar as grammar
from preflight_helpers.study_metadata import study_uid_for_dirname, run_study_uid

REPO_ROOT = Path(__file__).resolve().parents[1]
COHORT_CSV = REPO_ROOT / "data" / "cohort_stats" / "per_study_cohort_new.csv"
# The OLD (282-study) round, v1.2 re-base — the ONLY table where `quality_label` (the doctors'
# grade) is populated. Its `study_code` is a LEGACY cohort code (ABQ555-style), so the join to a
# study_uid runs through the registry's `legacy_study_code`, never by name.
OLD_COHORT_CSV = REPO_ROOT / "data" / "cohort_stats" / "per_study_cohort_v1.2.csv"
PROVENANCE_JSON = REPO_ROOT / "data" / "cohort_stats" / "new_round_cohort_provenance.json"
CONFIG_JSON = REPO_ROOT / "configs" / "round3_intake.json"
PREVIEW_TSV = REPO_ROOT / "scratchpad" / "qa_sheet_preview.tsv"
DICTIONARY_MD = REPO_ROOT / "docs" / "hasta_kayit_dictionary.md"
# (B) + (C): PHI-bearing analysis projections — gitignored, guard-asserted before every write.
MASTER_CSV = REPO_ROOT / "data" / "qa_master.csv"
FRAMES_CSV = REPO_ROOT / "data" / "qa_frames.csv"
# The batch's top-5-by-RF-area landmark selection (derive_new_round_cohort --from-json).
LANDMARK_JSON = REPO_ROOT / "scratchpad" / "new_round_landmark_full.json"

# The QMT cleaned-femur measurement cutoff: a finalized run created before this, with no stamped
# measurement_code_hash, predates the fix and is inferred STALE (memory: project_qmt_depth_read_raw_femur).
QMT_CLEANED_FEMUR_CUTOFF = "2026-07-16"

# ---- HARD WRITE GUARD --------------------------------------------------------
# This module is DRY by contract. No gws write is reachable: apply() is an unimplemented placeholder,
# and _WRITE_GUARD is asserted at the top of anything that could touch the sheet. Flipping it is a
# separate, human-reviewed step (like check_studies --apply) — NOT this pass.
_WRITE_GUARD = False


def apply(payload: dict) -> None:  # noqa: ARG001 - placeholder signature
    """Placeholder for the future --apply step: send addSheet('qa') + values via gws_io. Intentionally
    UNIMPLEMENTED this pass so a write can never fire from here. The real step would (human-reviewed):
        io.sheets_batch_update(hk, [{"addSheet": {"properties": {"title": "qa"}}}])
        io.sheets_values_update(hk, "qa!A1", [payload["header"]] + payload["rows"])
    """
    raise NotImplementedError(
        "build_qa_sheet.apply() is a DRY placeholder — writing the 'qa' tab is a separate, "
        "human-reviewed --apply step (not implemented this pass). No sheet write happens here.")


# ==================== column layout (grouped) ====================
# (group, key). Order IS the sheet column order. Overlay keys are blank on an auto-frames-only row.
SPINE_COLUMNS: List[tuple] = [
    ("identity", "study_uid"), ("identity", "study_name"), ("identity", "operator"),
    ("identity", "session"), ("identity", "date"),
    ("acquisition", "kontrol_verdict"), ("acquisition", "rf_depth_flag"),
    ("integrity", "capture_verdict"),
    ("measurement_trust", "qmt_provenance"), ("measurement_trust", "int_cv_trust"),
    ("measurement_trust", "rf_source"),
    ("results", "condition_score"), ("results", "band"),
    ("results", "size_contribution"), ("results", "fat_contribution"), ("results", "texture_contribution"),
    ("results", "star_ratio"), ("results", "qmt_mm"), ("results", "int_cv_within"),
    ("results", "fat_to_depth_q50"), ("results", "izo_torque_nm"), ("results", "izo_cv_pct"),
    ("freshness", "measurement_code_hash"), ("freshness", "stale"),
    ("freshness", "segmenter"), ("freshness", "bundle_stale"),
]
# NOT "vetted": the score is produced by the SAME scorer — only the FRAME SELECTION differs
# (operator-picked vs the auto top-5-by-RF-area). These columns are a frame-selection SENSITIVITY
# readout, not a human judgement on the score. (Akadir 2026-07-20)
OVERLAY_COLUMNS: List[tuple] = [
    ("opsel", "opsel_score"), ("opsel", "opsel_band"), ("opsel", "opsel_int_cv"),
    ("opsel", "texture_state"), ("opsel", "n_routed_frames"), ("opsel", "opsel_freshness"),
    ("opsel", "measurement_basis"),
]
PROVENANCE_COLUMN = ("provenance", "provenance")
ALL_COLUMNS = SPINE_COLUMNS + OVERLAY_COLUMNS + [PROVENANCE_COLUMN]
HEADER = [key for _grp, key in ALL_COLUMNS]

# ---- (B) qa_master.csv: the sheet columns PLUS the analysis payload -----------
# Everything here is a JOIN, never a re-measurement. Kept OUT of HEADER on purpose: the sheet tab
# stays glanceable, the master table carries what a model needs.
MASTER_EXTRA_COLUMNS: List[tuple] = [
    # confounders — the nested force model's floor is torque ~ sex + weight + height + age
    ("demographics", "age"), ("demographics", "sex"),
    ("demographics", "height_cm"), ("demographics", "weight_kg"), ("demographics", "bmi"),
    ("demographics", "hw_source"),        # which source won for height/weight: sheet|study_name|''
    # force / strength criterion — all 5 dynamometer columns (the sheet tab carries only 2)
    ("force", "isok_torque_ext_nm"), ("force", "isok_mean_power_ext_w"), ("force", "isok_cv_pct"),
    # the measurement gate's receipt: which criterion cells are HELD (blanked here, hard-flagged or
    # killed in the flag→kill ledger) and which carry a SOFT flag (kept, visible). Parsed by
    # ingest_all's "held measurements are actually held" invariant — formats change together.
    ("force", "measurement_flags"),
    # the full measured feature set from the cohort table (the sheet tab carries only 4)
    ("features", "rf_thickness_mm"), ("features", "muscle_fat_ratio"),
    ("features", "full_thickness_q50_mm"), ("features", "fat_thickness_q50_mm"),
    ("features", "fat_thickness_max_mm"), ("features", "visible_rf_area_mm2"),
    # the OTHER target: the doctors' grade (old round only) + which round this row belongs to
    ("target", "quality_label"), ("target", "cohort_round"),
    # where the study lives, so an analysis can reach its frames (PHI — gitignored output only)
    ("locator", "rel_path"),
]
MASTER_COLUMNS = HEADER + [key for _grp, key in MASTER_EXTRA_COLUMNS]

# ---- (C) qa_frames.csv: the long frame index ---------------------------------
FRAME_COLUMNS = [
    "study_uid", "role", "capture_dir", "frame_base", "rel_jpg_path",
    "depth_cm", "pixel_spacing_row_mm", "n_frames_in_capture",
    "in_auto_landmark", "in_opsel", "source_note",
]


# ==================== input loaders (defensive) ====================
def _load_config() -> dict:
    try:
        return json.loads(CONFIG_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


def discover_spine() -> List[Path]:
    """Every materialized study: a dir with a materialize_manifest.json AND a captures/ subdir."""
    out = []
    for mf in glob.glob(str(env.STUDIES_ROOT) + "/**/materialize_manifest.json", recursive=True):
        d = Path(mf).parent
        if (d / "captures").is_dir():
            out.append(d)
    return sorted(out, key=lambda p: p.name)


def load_cohort_features() -> Dict[str, dict]:
    """study_uid -> feature row (the csv key `study_code` IS the study_uid)."""
    if not COHORT_CSV.exists():
        return {}
    df = pd.read_csv(COHORT_CSV)
    return {str(r["study_code"]): r.to_dict() for _i, r in df.iterrows()}


def load_old_cohort_grades() -> Dict[str, str]:
    """LEGACY study_code -> quality_label, from the old round's v1.2 table (282/282 populated).

    The old table's key is a cohort code (ABQ555), NOT a uid — callers must resolve it through the
    registry's `legacy_study_code`. Empty dict if the file is absent (graceful degrade)."""
    if not OLD_COHORT_CSV.exists():
        return {}
    try:
        df = pd.read_csv(OLD_COHORT_CSV)
    except Exception as exc:
        print(f"[warn] old cohort read failed ({exc!r}); quality_label will be blank", file=sys.stderr)
        return {}
    out: Dict[str, str] = {}
    for _i, r in df.iterrows():
        code = str(r.get("study_code") or "").strip()
        lab = r.get("quality_label")
        if code and _is_num(lab) and str(lab).strip():
            out[code] = str(lab).strip()
    return out


def load_registry_rows() -> Dict[str, dict]:
    """study_uid -> its registry row (legacy_study_code / cohort / rel_path). {} if unbuilt.

    Goes through `study_registry` — the ONE door from any identifier to the canonical uid."""
    try:
        import study_registry as sr
        return {r["study_uid"]: r for r in sr.load_registry() if r.get("study_uid")}
    except Exception as exc:
        print(f"[warn] study_registry unavailable ({exc!r}); quality_label/cohort_round blank",
              file=sys.stderr)
        return {}


def load_provenance() -> Dict[str, dict]:
    """study_uid -> {rf_source, reliability, int_cv_trusted}."""
    try:
        prov = json.loads(PROVENANCE_JSON.read_text())
    except Exception:
        return {}
    return {p["study_code"]: p for p in prov.get("per_study", [])}


def load_capture_verdicts() -> Dict[str, "capture_audit.StudyAudit"]:
    """dirname -> StudyAudit (torch-free audit_tree over the materialized root)."""
    out = {}
    try:
        for a in capture_audit.audit_tree(env.STUDIES_ROOT):
            out[a.study] = a
    except Exception as exc:                          # never let one bad tree blind the build
        print(f"[warn] capture_audit.audit_tree failed: {exc!r}", file=sys.stderr)
    return out


def load_finalized_runs() -> Dict[str, dict]:
    """study_uid -> the LATEST finalized run record (by created_at_utc), keyed via run_study_uid."""
    latest: Dict[str, dict] = {}
    if not env.RUNS_JSONL.exists():
        return latest
    for line in env.RUNS_JSONL.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        if rec.get("lifecycle") != "finalized":
            continue
        uid = run_study_uid(rec)
        if not uid:
            continue
        cur = latest.get(uid)
        if cur is None or (rec.get("created_at_utc") or "") > (cur.get("created_at_utc") or ""):
            latest[uid] = rec
    return latest


# Turkish dotted/dotless I folds to plain 'i' BEFORE casefold — Python's casefold maps 'I'->'i' but
# leaves 'İ' as 'i̇' (i + combining dot) and 'ı' as 'ı', so 'İzokinetik' and 'Izokinetik' would still
# differ. Fold both sides of the comparison with this.
_TR_I = str.maketrans({"İ": "i", "I": "i", "ı": "i", "̇": ""})


def _norm_header(name: str) -> str:
    """Comparison key for a sheet header: Turkish-I folded, casefolded, whitespace collapsed."""
    return " ".join(str(name or "").translate(_TR_I).casefold().translate(_TR_I).split())


def load_sheet_rows(cfg: dict) -> Dict[str, dict]:
    """study_uid -> {study_name, operator, kontrol_verdict, kontrol_detail, izo_torque, izo_cv}
    from the live 'kayit' sheet (READ-ONLY, via gws_io). {} if the read fails (graceful degrade)."""
    try:
        from preflight_helpers import gws_io as io
        from preflight_helpers import datasheet_schema as ds
        hk = cfg["sheets"]["hasta_kayit"]
        title = io.first_sheet(io.sheets_meta(hk))["title"]
        last = ds.col_letter(len(ds.HEADERS) - 1)
        values = io.sheets_values_get(hk, f"{title}!A1:{last}1000")
    except Exception as exc:
        print(f"[warn] live 'kayit' read failed ({exc!r}); kontrol/force columns will be blank",
              file=sys.stderr)
        return {}
    if not values:
        return {}
    hdr = values[0]
    # Exact string matching on these headers fails SILENTLY and blanks the PRIMARY criterion column
    # for every row: a trailing space, a lowercased word, or a plain ASCII 'I' typed for 'İ' all
    # miss. Match on a normalized key instead, and WARN on anything that still does not resolve.
    by_norm = {}
    for i, name in enumerate(hdr):
        by_norm.setdefault(_norm_header(name), i)

    wanted = ("Study Adı", "Operator", "Kontrol Sonucu", "Kontrol Detayı",
              "Boy (cm)", "Kilo (kg)",
              "İzometrik Tepe Tork (Nm)", "İzometrik CV (%)",
              "İzokinetik Tepe Tork Ekst (Nm)", "İzokinetik Ort. Güç Ekst (W)",
              "İzokinetik CV (%)")
    ci = {n: by_norm.get(_norm_header(n)) for n in wanted}
    missing = [n for n, i in ci.items() if i is None]
    if missing:
        print(f"[warn] 'kayit' header not found for {missing} — those columns will be BLANK for "
              f"every row (sheet headers seen: {hdr})", file=sys.stderr)
    out: Dict[str, dict] = {}
    for r_off, row in enumerate(values[1:], start=2):
        def cell(name: str) -> str:
            i = ci.get(name)
            return (row[i] if (i is not None and i < len(row)) else "").strip()
        study = cell("Study Adı")
        if not study:
            continue
        out[study_uid_for_dirname(study)] = {
            "study_name": study,
            "sheet_row": r_off,     # 1-based sheet row — the measurement gate's operator pointer
            "operator": cell("Operator"),
            "kontrol_verdict": cell("Kontrol Sonucu"),
            "kontrol_detail": cell("Kontrol Detayı"),
            # confounders — height/weight live ONLY here (the cohort csv has age/sex/bmi but not
            # the raw two), so a failed sheet read is what makes the study-name fallback matter.
            "height_cm": cell("Boy (cm)"),
            "weight_kg": cell("Kilo (kg)"),
            # all 5 dynamometer columns (the sheet TAB shows 2; the master table carries all)
            "izo_torque": cell("İzometrik Tepe Tork (Nm)"),
            "izo_cv": cell("İzometrik CV (%)"),
            "isok_torque_ext_nm": cell("İzokinetik Tepe Tork Ekst (Nm)"),
            "isok_mean_power_ext_w": cell("İzokinetik Ort. Güç Ekst (W)"),
            "isok_cv_pct": cell("İzokinetik CV (%)"),
        }
    return out


# ==================== derivations ====================
_RF_DEPTH_RE = re.compile(r"RF hafif (derin|sığ) \(([\d.]+)\s*cm\)")


def parse_rf_depth_flag(detail: str) -> str:
    """Pull the 'RF hafif derin/sığ (Ncm)' caveat out of the Kontrol Detayı text (blank if none).
    RF-deep is a FLAG, not a withhold (standing assumption) — surfaced so the reviewer can verify."""
    m = _RF_DEPTH_RE.search(detail or "")
    return f"RF {m.group(1)} {m.group(2)}cm" if m else ""


def _is_num(v) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def _round(v, n):
    return round(float(v), n) if _is_num(v) else None


def _num_or_blank(v):
    """A float when the value parses as one, '' otherwise. Sheet cells arrive as strings and may be
    empty, '-', or a stray note — a blank beats a wrong number."""
    if v is None:
        return ""
    if isinstance(v, str):
        v = v.strip().replace(",", ".")
        if not v:
            return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ""
    return "" if math.isnan(f) else f


def demographics(feats: dict, sheet: dict, sn) -> dict:
    """age / sex / height_cm / weight_kg / bmi + the source that won for height+weight.

    age/sex/bmi come from the cohort table when the study is in it; height/weight exist ONLY on the
    live 'kayit' sheet, so the study-name grammar (`<age><sex>_<boy>_<kilo>_<asis><leg>`) is the
    fallback when the sheet read failed or the row is absent. Every field degrades to '' on its own
    — a missing height never blanks a present age.
    """
    from preflight_helpers.study_metadata import _SEX_MAP        # the ONE e/k -> M/F policy

    age = _num_or_blank(feats.get("age"))
    if age == "" and sn is not None and sn.matched:
        age = _num_or_blank(sn.age)
    # The old cohort spells an unresolved sex 'unknown'; the grammar spells it '' (a 'y' years-only
    # token carries no sex). ONE spelling downstream — blank — or a model reads 'unknown' as a level.
    sex = str(feats.get("sex") or "").strip()
    if sex.lower() in ("", "nan", "unknown", "none"):
        sex = (_SEX_MAP.get(sn.sex or "") or "") if (sn is not None and sn.matched) else ""
    bmi = _num_or_blank(feats.get("bmi"))

    # Height and weight resolve INDEPENDENTLY. A partial sheet fill (height typed, weight still
    # blank) must not blank a weight the study name carries — that row would silently drop out of
    # the complete-case `torque ~ sex + weight + height + age` this table exists to enable — and
    # must not be labelled 'sheet' when half of it came from the grammar.
    h, h_src = _num_or_blank(sheet.get("height_cm")), "sheet"
    w, w_src = _num_or_blank(sheet.get("weight_kg")), "sheet"
    if sn is not None and sn.matched:
        if h == "":
            h, h_src = _num_or_blank(sn.height), "study_name"
        if w == "":
            w, w_src = _num_or_blank(sn.weight), "study_name"
    h_src = h_src if h != "" else ""
    w_src = w_src if w != "" else ""
    # One label when the two agree (or only one resolved), an explicit per-field label when they
    # differ — never a single source name over a mixed row.
    src = (h_src or w_src) if (h_src == w_src or not h_src or not w_src) \
        else f"height:{h_src}+weight:{w_src}"
    # BMI last resort: derive it from the two raw numbers we did resolve (same formula the sheet uses)
    if bmi == "" and h not in ("", 0) and w != "":
        bmi = round(float(w) / (float(h) / 100.0) ** 2, 1)
    return {"age": age, "sex": sex, "height_cm": h, "weight_kg": w, "bmi": bmi, "hw_source": src}


def capture_top_reason(a: "capture_audit.StudyAudit") -> str:
    """CLEAN, or 'FLAGGED: <top reason>' — the most-actionable reason first (score-integrity reasons
    before the manifest-surfaced ambiguities)."""
    if a is None:
        return ""
    if a.verdict == "CLEAN":
        return "CLEAN"
    reasons = list(a.reasons) + list(a.manifest_ambiguous)
    return f"FLAGGED: {reasons[0]}" if reasons else "FLAGGED"


def score_contributions(feats: dict) -> dict:
    """RE-DERIVE the batch condition score + band + the size/fat/texture additive contributions from
    the frozen scorer (DRY — the same numpy path the app serves). Blank when the score is unavailable
    (e.g. texture withheld -> both headline C and the no-femur fallback A lose int_cv)."""
    art = _ARTIFACT
    res = cm.score_patient(art, feats)
    hl = res.get("headline") or {}
    out = {"condition_score": None, "band": "", "size_contribution": None,
           "fat_contribution": None, "texture_contribution": None}
    if hl.get("available"):
        out["condition_score"] = _round(hl.get("condition"), 0)
        out["band"] = hl.get("band", "")
    # axis contributions (beta*z) — size=star_ratio, fat=fat_to_depth_q50, texture=int_cv_within
    axis_key = {"star_ratio": "size_contribution", "fat_to_depth_q50": "fat_contribution",
                "int_cv_within": "texture_contribution"}
    for ax in res.get("axes", []):
        k = axis_key.get(ax.get("col"))
        if k:
            out[k] = _round(ax.get("contribution"), 3)
    return out


def opsel_freshness(rec: dict) -> str:
    """fresh / STALE for a finalized run: compare its stamped measurement_code_hash to the live hash;
    if the stamp is absent, infer STALE when the run predates the QMT-cleaned-femur cutoff (else the
    hash is simply unknown — a post-cutoff run measured with newer code but not stamped)."""
    frozen = rec.get("measurement_code_hash")
    if frozen:
        return "fresh" if frozen == _CURRENT_HASH else "STALE"
    created = rec.get("created_at_utc") or ""
    if created and created < QMT_CLEANED_FEMUR_CUTOFF:
        return "STALE (pre-cutoff, unstamped)"
    return "unknown (unstamped)"


def n_routed_frames(rec: dict) -> int:
    """Sum of per-role n_analysis_frames across routed_picks; 0 for a pre-fix record with none."""
    rp = rec.get("routed_picks") or {}
    total = 0
    for role in rp.values():
        try:
            total += int((role or {}).get("n_analysis_frames") or 0)
        except (TypeError, ValueError):
            continue
    return total


def measurement_basis(rec: dict) -> str:
    """How many operator-picked frames actually fed the measurement.

    NOTE — this is GATE ACTIVITY, not override-detection. The record stores the operator's keep/drop
    decision (the gates) but NOT the per-frame QA evidence it was taken against (`frame_features`
    carries no `image_quality`), so we can honestly say "4/6 analysed", never "kept 2 despite a
    flag". Surfacing a true override needs the per-frame QA persisted alongside the gates.
    """
    rp = rec.get("routed_picks") or {}
    if not rp:
        return "operator-frames (picks not recorded — pre-fix run)"
    picked = used = 0
    for role in rp.values():
        r = role or {}
        picked += len(r.get("frame_bases") or [])
        try:
            used += int(r.get("n_analysis_frames") or 0)
        except (TypeError, ValueError):
            pass
    out = f"operator-frames: {used}/{picked} analysed"
    return out + (f" ({picked - used} excluded)" if picked > used else "")


def bundle_sha(rec: dict) -> str:
    """The segmenter bundle that produced this record's measurement (short sha)."""
    return str((rec.get("model_bundle") or {}).get("sha256") or "")[:12]


def build_overlay(rec: dict) -> dict:
    """The record-overlay cells for a study with a finalized run. NOT a human-given score — the SAME
    scorer, run over the OPERATOR-SELECTED frames instead of the auto top-5."""
    agg = rec.get("aggregate") or {}
    sc = score_contributions(agg)                     # re-derive from the record's OWN aggregate feats
    created = (rec.get("created_at_utc") or "")[:10]
    sha = bundle_sha(rec)
    return {
        "opsel_score": sc["condition_score"],
        "opsel_band": sc["band"],
        "opsel_int_cv": _round(agg.get("int_cv_within"), 3),
        "texture_state": rec.get("texture_state") or rec.get("_rf_reliability") or "",
        "n_routed_frames": n_routed_frames(rec),
        "opsel_freshness": opsel_freshness(rec),
        "measurement_basis": measurement_basis(rec),
        "segmenter": sha,
        # blank rather than guess when either side is unresolvable
        "bundle_stale": (sha != _CURRENT_BUNDLE_SHA) if (sha and _CURRENT_BUNDLE_SHA) else "",
        "provenance": f"operator-frames (run {created})" if created else "operator-frames",
    }


# ==================== the build ====================
_ARTIFACT: dict = {}
_CURRENT_HASH: str = ""
_BATCH_BUNDLE: str = ""          # the bundle NAME the batch cohort was measured with (provenance)
_CURRENT_BUNDLE_SHA: str = ""    # live default segmenter sha, "" when unresolvable (never guess)


def build_rows(*, read_sheet: bool = True, record_flags: bool = False) -> List[dict]:
    """One dict per materialized study (spine + overlay), keyed by study_uid.

    `record_flags`: append fresh measurement-gate findings to the flag→kill ledger (pass True only
    when writing real outputs, i.e. --analysis; a preview must not mutate the ledger)."""
    global _ARTIFACT, _CURRENT_HASH, _BATCH_BUNDLE, _CURRENT_BUNDLE_SHA
    # Score with the v1.2 PRODUCTION model the app serves — NOT cm.load()'s v1.0 default
    # (condition_models.json). v1.0 vs v1.2 differ by 1-4 pts on ~40% of studies; the QA tab
    # must match what the operator sees. Score is fixed to v1.2 (test_score_is_fixed_to_v12).
    _ARTIFACT = cm.load(cp.model_path_for_source(cp.V12))
    _CURRENT_HASH = cm.measurement_code_hash()
    # Segmenter (model) version axis — orthogonal to the measurement-CODE hash above. Lets a row say
    # which segmenter measured it, so when the in-progress refinement lands the tab shows exactly
    # which studies are on the old model. Both sides are best-effort: blank beats a wrong answer.
    try:
        _BATCH_BUNDLE = str(json.loads(PROVENANCE_JSON.read_text()).get("bundle") or "")
    except Exception:
        _BATCH_BUNDLE = ""
    try:
        from preflight_helpers.model_registry import default_bundle, list_bundles
        _CURRENT_BUNDLE_SHA = str(getattr(default_bundle(list_bundles(compute_hash=True)),
                                          "sha256", "") or "")[:12]
    except Exception:
        _CURRENT_BUNDLE_SHA = ""

    cfg = _load_config()
    feats_by_uid = load_cohort_features()
    prov_by_uid = load_provenance()
    caps_by_dir = load_capture_verdicts()
    runs_by_uid = load_finalized_runs()
    sheet_by_uid = load_sheet_rows(cfg) if read_sheet else {}
    reg_by_uid = load_registry_rows()
    grade_by_legacy = load_old_cohort_grades()

    # ---- the measurement gate, substrate side: re-evaluate whatever is ON the sheet (including
    # values typed directly into kayit, never synced) and enforce the holds — a killed or
    # hard-flagged criterion cell is BLANKED here with a receipt in `measurement_flags`, so
    # `qa_master.csv` never serves a value the ledger says is suspect (held-by-default, pending
    # human verdict via scripts/measurement_verdict.py).
    from preflight_helpers import flag_kill_ledger as fkl
    from preflight_helpers import measurement_gate as mg
    # A ledger-killed study (duplicate ingest, dead recording) leaves the substrate IMMEDIATELY —
    # the frozen cohort CSV still lists it until the next re-freeze (percentile placement moves
    # only at freezes, by design), but qa_master is what analyses read, and a duplicate must not
    # count one person twice there (the AKA158 lesson, 2026-08-13). Enforced at the spine loop.
    dead_uids = fkl.killed_study_uids()
    meas_holds: Dict[str, dict] = {}
    if sheet_by_uid:
        states = {u: (d.get("sheet_row"), mg.values_from_sheet_state(d))
                  for u, d in sheet_by_uid.items()}
        planned = mg.plan_entries(mg.evaluate(states))
        if planned and record_flags:
            mg.record(planned)
            print(f"[measurement-gate] {len(planned)} fresh flag entries appended to "
                  f"data/kill_list.jsonl", file=sys.stderr)
        meas_holds = fkl.open_measurement_holds()
        # a fresh finding holds IMMEDIATELY, even when preview mode did not record it — the
        # preview must show what the analysis build will serve, which under the no-force rule
        # is ZERO criterion cells for ANY open flag, either tier (mirrors
        # open_measurement_holds; the tier-filtered version under-held — red-team 2026-08-13).
        from preflight_helpers.torque_plausibility import FORCE_FIELDS
        for e in (planned if not record_flags else []):
            for f in FORCE_FIELDS:
                meas_holds.setdefault(e["study_uid"], {})[f] = e

    rows: List[dict] = []
    n_killed_skipped = 0
    for study_dir in discover_spine():
        dirname = study_dir.name
        uid = study_uid_for_dirname(dirname)
        if uid in dead_uids:      # ledger-killed: folder stays on disk, row does not exist
            n_killed_skipped += 1
            continue
        rel = os.path.relpath(study_dir, env.STUDIES_ROOT).split(os.sep)
        operator = rel[0] if len(rel) > 1 else ""
        session = rel[1] if len(rel) > 2 else (rel[0] if len(rel) > 1 else "")
        sn = grammar.parse(dirname)
        feats = feats_by_uid.get(uid, {})
        prov = prov_by_uid.get(uid, {})
        sheet = sheet_by_uid.get(uid, {})
        held = meas_holds.get(uid) or {}
        if held:
            # blank the held cells BEFORE the row reads them — both the spine (izo_*) and the
            # master extras (isok_*) below inherit the blanks; the receipt column says why
            sheet = dict(sheet)
            for fld, key in mg.FIELD_TO_SHEET_KEY.items():
                if fld in held:
                    sheet[key] = ""
        sc = score_contributions(feats) if feats else {
            "condition_score": None, "band": "", "size_contribution": None,
            "fat_contribution": None, "texture_contribution": None}

        int_cv = feats.get("int_cv_within")
        row = {
            # identity
            "study_uid": uid,
            "study_name": sheet.get("study_name") or dirname,     # PHI — preview + payload only
            "operator": operator or sheet.get("operator", ""),
            "session": session,
            "date": sn.date if (sn.matched and sn.date) else "",
            # acquisition (live sheet)
            "kontrol_verdict": sheet.get("kontrol_verdict", ""),
            "rf_depth_flag": parse_rf_depth_flag(sheet.get("kontrol_detail", "")),
            # integrity
            "capture_verdict": capture_top_reason(caps_by_dir.get(dirname)),
            # measurement trust
            "qmt_provenance": feats.get("qmt_provenance", "") if feats else "(not in cohort)",
            "int_cv_trust": ("withheld" if not _is_num(int_cv) else "present") if feats else "",
            "rf_source": prov.get("rf_source", ""),
            # results (batch / auto-frames)
            "condition_score": sc["condition_score"], "band": sc["band"],
            "size_contribution": sc["size_contribution"], "fat_contribution": sc["fat_contribution"],
            "texture_contribution": sc["texture_contribution"],
            "star_ratio": _round(feats.get("star_ratio"), 4),
            "qmt_mm": _round(feats.get("qmt_mm"), 2),
            "int_cv_within": _round(int_cv, 3),
            "fat_to_depth_q50": _round(feats.get("fat_to_depth_q50"), 3),
            "izo_torque_nm": sheet.get("izo_torque", ""),
            "izo_cv_pct": sheet.get("izo_cv", ""),
            # freshness (batch row is measured with the live code by construction)
            "measurement_code_hash": _CURRENT_HASH, "stale": False,
            # the batch provenance records a bundle NAME (not a sha) — no honest comparison to the
            # record rows' sha, so leave bundle_stale blank rather than guess
            "segmenter": _BATCH_BUNDLE, "bundle_stale": "",
            # overlay (blank unless a finalized run exists)
            "opsel_score": None, "opsel_band": "", "opsel_int_cv": None,
            "texture_state": "", "n_routed_frames": "", "opsel_freshness": "",
            "measurement_basis": "auto-frames (top-5 by RF area)",
            # provenance
            "provenance": "auto-frames",
        }
        # ---- (B)-only additions: joins, never re-measurements. Extra keys are inert for the sheet
        # projection (it selects HEADER), so the tab cannot widen by accident.
        reg = reg_by_uid.get(uid, {})
        legacy_code = (reg.get("legacy_study_code") or "").strip()
        cohort = (reg.get("cohort") or "").strip()
        row.update(demographics(feats, sheet, sn if sn.matched else None))
        row.update({
            "isok_torque_ext_nm": sheet.get("isok_torque_ext_nm", ""),
            "isok_mean_power_ext_w": sheet.get("isok_mean_power_ext_w", ""),
            "isok_cv_pct": sheet.get("isok_cv_pct", ""),
            "measurement_flags": mg.receipt(uid, meas_holds) if sheet_by_uid else "",
            "rf_thickness_mm": _round(feats.get("rf_thickness_mm"), 3),
            "muscle_fat_ratio": _round(feats.get("muscle_fat_ratio"), 3),
            "full_thickness_q50_mm": _round(feats.get("full_thickness_q50_mm"), 3),
            "fat_thickness_q50_mm": _round(feats.get("fat_thickness_q50_mm"), 3),
            "fat_thickness_max_mm": _round(feats.get("fat_thickness_max_mm"), 3),
            "visible_rf_area_mm2": _round(feats.get("visible_rf_area_mm2"), 3),
            # the doctors' grade: OLD round only, joined via the registry's legacy code (never by
            # name). Blank for the 66 new-round studies — they have no grade yet, and blank is the
            # honest value.
            "quality_label": grade_by_legacy.get(legacy_code, "") if legacy_code else "",
            "cohort_round": {"new": "new", "old": "old", "both": "both"}.get(cohort, ""),
            "rel_path": reg.get("rel_path") or os.path.relpath(study_dir, env.STUDIES_ROOT),
        })
        rec = runs_by_uid.get(uid)
        if rec is not None:
            row.update(build_overlay(rec))
        rows.append(row)
    if n_killed_skipped:
        print(f"[kill] {n_killed_skipped} ledger-killed stud(y/ies) on disk excluded from the "
              f"substrate (data/kill_list.jsonl)", file=sys.stderr)
    return rows


# ==================== (B) the master projection ====================
def build_master_rows(rows: List[dict]) -> List[dict]:
    """The study-level analysis table = the sheet spine (66 materialized, new-round studies) PLUS
    the OLD round carried in from `per_study_cohort_v1.2.csv`.

    Why the old round is here at all: `quality_label` — the doctors' grade — exists ONLY for the old
    282, and it is the second target of the grade-vs-force reconciliation. A master table built on
    the spine alone would ship that column 0/66 populated, i.e. the reconciliation could not be run.
    The two rounds are told apart by `cohort_round`, never blended:

      cohort_round=new : full QA/acquisition/freshness columns, force from the sheet, NO grade
      cohort_round=old : features + grade + demographics only; every QA/acquisition/force cell BLANK
                         (those studies were never materialized under this pipeline — blank is the
                         honest value, and the analysis must not read absence as a verdict)

    Old-round rows are keyed on the SAME study_uid, resolved from the registry's legacy_study_code —
    so a joiner never has to know which round a row came from. Identity for an old row degrades to
    whatever the registry knows; a legacy code that resolves to no uid is skipped, never invented.
    """
    out = list(rows)
    seen = {r["study_uid"] for r in rows}
    grades = load_old_cohort_grades()
    if not grades:
        return out
    try:
        old = pd.read_csv(OLD_COHORT_CSV)
    except Exception:
        return out
    feats_by_code = {str(r["study_code"]): r.to_dict() for _i, r in old.iterrows()}

    # legacy code -> uid, via the registry (the ONE resolver). Ambiguous codes are dropped by
    # `collision_report`'s contract: legacy_study_code -> >1 uid should be zero; if it ever is not,
    # take no row rather than the wrong one.
    uid_by_code: Dict[str, Optional[str]] = {}
    reg_by_uid = load_registry_rows()
    for uid, r in reg_by_uid.items():
        code = (r.get("legacy_study_code") or "").strip()
        if not code:
            continue
        uid_by_code[code] = None if code in uid_by_code else uid

    for code, feats in sorted(feats_by_code.items()):
        uid = uid_by_code.get(code)
        if not uid or uid in seen:            # unresolvable, ambiguous, or already on the spine
            continue
        seen.add(uid)
        reg = reg_by_uid.get(uid, {})
        sn = grammar.parse(reg.get("dirname") or "")
        row = {c: "" for c in MASTER_COLUMNS}
        sc = score_contributions(feats)
        row.update({
            "study_uid": uid,
            "study_name": reg.get("dirname", ""),          # PHI — gitignored output only
            "operator": reg.get("operator", ""), "session": reg.get("session", ""),
            "date": sn.date if (sn.matched and sn.date) else "",
            "condition_score": sc["condition_score"], "band": sc["band"],
            "size_contribution": sc["size_contribution"], "fat_contribution": sc["fat_contribution"],
            "texture_contribution": sc["texture_contribution"],
            "star_ratio": _round(feats.get("star_ratio"), 4),
            "qmt_mm": _round(feats.get("qmt_mm"), 2),
            "int_cv_within": _round(feats.get("int_cv_within"), 3),
            "fat_to_depth_q50": _round(feats.get("fat_to_depth_q50"), 3),
            "qmt_provenance": str(feats.get("qmt_provenance") or ""),
            "rf_thickness_mm": _round(feats.get("rf_thickness_mm"), 3),
            "muscle_fat_ratio": _round(feats.get("muscle_fat_ratio"), 3),
            "full_thickness_q50_mm": _round(feats.get("full_thickness_q50_mm"), 3),
            "fat_thickness_q50_mm": _round(feats.get("fat_thickness_q50_mm"), 3),
            "fat_thickness_max_mm": _round(feats.get("fat_thickness_max_mm"), 3),
            "visible_rf_area_mm2": _round(feats.get("visible_rf_area_mm2"), 3),
            "quality_label": grades.get(code, ""),
            "cohort_round": "old",
            "rel_path": reg.get("rel_path", ""),
            "provenance": "old-round cohort (per_study_cohort_v1.2.csv)",
            "measurement_basis": "old-round triple (cohort table)",
        })
        row.update(demographics(feats, {}, sn if sn.matched else None))
        out.append(row)
    return out


# ==================== (C) the frame index ====================
# Physical comparability is the whole point of this table. RF depth varies across studies
# (4.9 / 5.9 / 6.9 / 8.9 cm), so raw pixels are NOT comparable: a model trained on frames without
# their per-capture depth learns the depth setting, not the tissue. Depth + row spacing therefore
# come from THAT capture's OWN .dcm (each capture dir carries one) — never from a study-level
# default, and never guessed: an unresolvable .dcm leaves the two cells blank and KEEPS the frame.

def load_landmark_picks() -> Dict[str, Dict[str, set]]:
    """dirname -> role -> {frame_base} for the batch's top-5-by-RF-area landmark selection.

    Reads `scratchpad/new_round_landmark_full.json` when present. NOTE: the file as written by
    `derive_new_round_cohort` records per-role AGGREGATES (and `n_frames`), not the frame bases it
    picked — so this returns {} against today's artifact and `in_auto_landmark` stays BLANK
    (unknown), which is the honest value. The reader is keyed on the frame-base lists the json
    would carry if re-derived with picks, so it lights up the moment they exist.
    """
    if not LANDMARK_JSON.exists():
        return {}
    try:
        entries = json.loads(LANDMARK_JSON.read_text())
    except Exception as exc:
        print(f"[warn] landmark json unreadable ({exc!r}); in_auto_landmark blank", file=sys.stderr)
        return {}
    out: Dict[str, Dict[str, set]] = {}
    for e in entries if isinstance(entries, list) else []:
        study = str((e or {}).get("study") or "")
        if not study:
            continue
        for role, vals in ((e.get("vals") or {}).items()):
            bases = (vals or {}).get("frame_bases") or (vals or {}).get("picked_frame_bases")
            if bases:
                out.setdefault(study, {})[role] = {str(b) for b in bases}
    return out


def opsel_picks(rec: dict) -> Dict[str, set]:
    """role -> {frame_base} the operator's finalized run actually analysed.

    Post-fix records carry `routed_picks[role].analysis_frame_bases`; a PRE-FIX record has no
    routed_picks at all, so every frame gets in_opsel=False (absence of a pick, not a claim)."""
    out: Dict[str, set] = {}
    for role, r in ((rec or {}).get("routed_picks") or {}).items():
        bases = (r or {}).get("analysis_frame_bases")
        if bases:
            out[str(role)] = {str(b) for b in bases}
    return out


_SPACING_CACHE: Dict[str, object] = {}


def _spacing_from_dcm_file(dcm: Path):
    """PixelSpacing[0] (row spacing, mm/px) of ONE named .dcm, or '' — the same tag, same units
    `run_pipeline._spacing_row_from_dicom` returns as `pixel_spacing_y`."""
    try:
        import pydicom
        ds = pydicom.dcmread(str(dcm), stop_before_pixels=True)
        ps = ds.get("PixelSpacing") or ds.get("ImagerPixelSpacing")
        return _num_or_blank(ps[0]) if (ps is not None and len(ps) == 2) else ""
    except Exception:
        return ""


def capture_spacing_row_mm(capture_dir: Path, dcm_path: Optional[Path] = None):
    """The capture's DICOM row spacing (mm/px), or '' when unresolvable.

    `dcm_path` — the .dcm the MANIFEST bound to this capture — wins when given: it is the
    authoritative ruler for these frames. Globbing is only the fallback, and it is alphabetical, so
    the day a second .dcm (or a pooled one) lands in a capture dir it could silently pick the wrong
    ruler — and depth/spacing are exactly what make frames physically comparable.

    The fallback reuses `run_pipeline._spacing_row_from_dicom` (globs the dir, returns
    `pixel_spacing_y`), imported LAZILY: run_pipeline pulls in the segmenter (torch) and this
    indexer must stay runnable torch-free — hence the direct-pydicom last resort on the same tag.
    """
    key = str(dcm_path or capture_dir)
    if key in _SPACING_CACHE:
        return _SPACING_CACHE[key]
    val = ""
    if dcm_path is not None and Path(dcm_path).exists():
        val = _spacing_from_dcm_file(Path(dcm_path))
    if val == "":
        try:
            from preflight_helpers.run_pipeline import _spacing_row_from_dicom
            val = _num_or_blank(_spacing_row_from_dicom(capture_dir, None).get("pixel_spacing_y"))
        except Exception:                     # torch-free fallback: the same tag, read directly
            dcm = next(iter(sorted(Path(capture_dir).glob("*.dcm"))), None)
            val = _spacing_from_dcm_file(dcm) if dcm is not None else ""
    _SPACING_CACHE[key] = val
    return val


def _manifest_dcm(cap_dir: Path, study_dir: Path, member: Optional[str]) -> Optional[Path]:
    """The .dcm the manifest bound to this capture, as a path — None when it names none or the file
    is gone (the caller then falls back to a glob, and says so in source_note)."""
    if not member:
        return None
    for cand in (Path(cap_dir) / member, Path(study_dir) / member):
        if cand.exists():
            return cand
    return None


def _disk_capture_records(study_dir: Path) -> List[tuple]:
    """`capture_audit._capture_records`'s tuple shape, rebuilt from a bare disk walk — the fallback
    when the manifest-driven enumeration raises. Same content-frame predicate; role and dcm unknown
    (blank), never guessed."""
    out: List[tuple] = []
    try:
        for d in sorted(Path(study_dir).glob("RawImagesForResearch*")):
            if capture_audit._content_jpgs(d):
                out.append(("sweep(root)", d, None, None, None))
                break
        for cap in sorted(Path(study_dir).glob("captures/c*")):
            for raw in sorted(cap.glob("RawImagesForResearch*")):
                if capture_audit._content_jpgs(raw):
                    out.append((cap.name, raw, None, None, None))
                    break
    except Exception as exc:
        print(f"[warn] disk walk failed for {Path(study_dir).name}: {exc!r}", file=sys.stderr)
    return out


def build_frame_rows(*, runs_by_uid: Optional[Dict[str, dict]] = None) -> List[dict]:
    """One row per CONTENT frame of every capture of every materialized study.

    ALL content frames, not only the selected ones: MIL treats a study as a bag, and indexing only
    the picks would bake in the very frame-selection brittleness this table exists to measure.
    A content frame is `capture_audit`'s own predicate (`CONTENT_JPG` = \\d{8}_\\d{9}), so the 256px
    aux pngs stay out by the same rule the audit uses.
    """
    runs_by_uid = load_finalized_runs() if runs_by_uid is None else runs_by_uid
    landmark = load_landmark_picks()
    rows: List[dict] = []

    for study_dir in discover_spine():
        dirname = study_dir.name
        uid = study_uid_for_dirname(dirname)
        opsel = opsel_picks(runs_by_uid.get(uid) or {})
        lm = landmark.get(dirname, {})
        try:
            mf = json.loads((study_dir / "materialize_manifest.json").read_text())
        except Exception:
            mf = {}
        degraded = False
        try:
            records = capture_audit._capture_records(study_dir, mf)
        except Exception as exc:
            # A study-level enumeration failure used to drop EVERY frame of that study from the
            # index — a whole-bag loss, invisible in the csv (stderr only). Fall back to a bare disk
            # walk so the bag survives with blank role/depth, flagged in source_note.
            print(f"[warn] capture enumeration failed for {uid}: {exc!r}; falling back to a disk walk",
                  file=sys.stderr)
            records = _disk_capture_records(study_dir)
            degraded = True

        for label, raw_dir, identity, dcm_member, dcm_depth in records:
            cap_dir = Path(raw_dir).parent
            role = str(identity or "")
            notes: List[str] = []
            if degraded:
                notes.append("capture-enumeration:DEGRADED (disk walk)")
            # The manifest's own dcm member is the ruler BOUND to this capture; a glob is
            # alphabetical and would silently pick the wrong one if a second .dcm ever lands here.
            dcm = _manifest_dcm(cap_dir, study_dir, dcm_member)
            depth = _num_or_blank(dcm_depth)
            if depth != "":
                notes.append("depth:manifest")
            else:                              # manifest silent -> read the capture's bound .dcm
                p = dcm or next(iter(sorted(cap_dir.glob("*.dcm"))), None)
                depth = _num_or_blank(capture_audit.dcm_depth_cm(p)) if p is not None else ""
                notes.append(("depth:dcm" if dcm else "depth:dcm-glob") if depth != ""
                             else "depth:UNRESOLVED")
            spacing = capture_spacing_row_mm(cap_dir, dcm)
            if spacing == "":
                notes.append("spacing:UNRESOLVED")
            if not role:
                notes.append("role:unassigned")

            bases = sorted(Path(p).stem for p in capture_audit._content_jpgs(Path(raw_dir)))
            n = len(bases)
            lm_role = lm.get(role)
            for base in bases:
                rows.append({
                    "study_uid": uid,
                    "role": role,
                    "capture_dir": label,
                    "frame_base": base,
                    # PHI (carries the raw dirname) — gitignored output only
                    "rel_jpg_path": os.path.relpath(Path(raw_dir) / f"{base}.jpg", env.STUDIES_ROOT),
                    "depth_cm": depth,
                    "pixel_spacing_row_mm": spacing,
                    "n_frames_in_capture": n,
                    # blank (not False) when the picks are simply unknown — never a guess
                    # blank (not False) when the picks are simply unknown — never a guess. A
                    # pre-fix record carries no routed_picks at all, so "not selected" would be a
                    # claim we cannot make; both flags spell absence the same way.
                    "in_auto_landmark": (base in lm_role) if lm_role is not None else "",
                    "in_opsel": (base in opsel[role]) if role in opsel else
                                ("" if not opsel else False),
                    "source_note": "; ".join(notes),
                })
    return rows


# ==================== outputs ====================
def _fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    return str(v)


def to_payload(rows: List[dict]) -> dict:
    """The exact addSheet+values payload the future --apply step will send (assembled, NOT sent)."""
    return {"title": "qa", "header": list(HEADER),
            "rows": [[_fmt(r.get(k)) for k in HEADER] for r in rows]}


def write_preview(rows: List[dict]) -> Path:
    """Local TSV preview (scratchpad, gitignored — may carry study_name PHI)."""
    PREVIEW_TSV.parent.mkdir(parents=True, exist_ok=True)
    with open(PREVIEW_TSV, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(HEADER)
        for r in rows:
            w.writerow([_fmt(r.get(k)) for k in HEADER])
    return PREVIEW_TSV


def _write_phi_csv(path: Path, columns: List[str], rows: List[dict]) -> Path:
    """Write a PHI-bearing analysis projection — but only after the ONE PHI guard in this repo
    (`study_registry._assert_git_ignored`) confirms git ignores the target. These tables bind
    study_uid to raw dirnames / frame paths; a tracked path would leak patient identifiers."""
    from study_registry import _assert_git_ignored
    _assert_git_ignored(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({c: _fmt(r.get(c)) for c in columns})
    return path


def write_master(rows: List[dict]) -> Path:
    """(B) data/qa_master.csv — the canonical study-level analysis table."""
    return _write_phi_csv(MASTER_CSV, MASTER_COLUMNS, rows)


def write_frames(rows: List[dict]) -> Path:
    """(C) data/qa_frames.csv — the long frame index."""
    return _write_phi_csv(FRAMES_CSV, FRAME_COLUMNS, rows)


def print_table(rows: List[dict]) -> None:
    """A compact, PHI-light stdout table (study_uid, not study_name)."""
    cols = ["study_uid", "operator", "kontrol_verdict", "capture_verdict", "qmt_provenance",
            "int_cv_trust", "condition_score", "band", "provenance"]
    widths = {c: max(len(c), *(len(_fmt(r.get(c))) for r in rows)) for c in cols} if rows else {}
    line = "  ".join(c.ljust(widths[c]) for c in cols)
    print(line)
    print("  ".join("-" * widths[c] for c in cols))
    for r in rows:
        v = dict(r)
        v["kontrol_verdict"] = (v.get("kontrol_verdict") or "")[:22]
        v["capture_verdict"] = (v.get("capture_verdict") or "")[:22]
        widths2 = {c: max(widths[c], len(_fmt(v.get(c)))) for c in cols}
        print("  ".join(_fmt(v.get(c)).ljust(widths2[c]) for c in cols))


# ==================== dictionary generator (registry-backed + curated) ====================
def render_dictionary() -> str:
    """Render docs/hasta_kayit_dictionary.md: GENERATED sections straight from the live registries
    (so they cannot drift) + CURATED sections. PHI-FREE by construction — study_uid + structure only,
    never a patient name / Study Adı / raw dirname."""
    from preflight_helpers import datasheet_schema as ds
    from preflight_helpers import feature_catalog as fc
    from preflight_helpers import study_audit as sa
    from labeling_app import qa_thresholds as qt
    from labeling_app import image_quality as iq

    L: List[str] = []
    L.append("# hasta_kayit + qa data dictionary\n")
    L.append("PHI-FREE reference for the Round-3 `hasta_kayit` datasheet and its generated `qa` "
             "projection. Study identity here is the **study_uid** only (`NR`+sha8 of the folder "
             "name) — never a patient name, `Study Adı`, or raw dirname.\n")
    L.append(f"_GENERATED sections are rendered from the live registries by "
             f"`scripts/build_qa_sheet.py:render_dictionary()` — do not hand-edit them; re-run "
             f"`build_qa_sheet.py --emit-dict`. Measurement-code hash at generation: "
             f"`{cm.measurement_code_hash()}`._\n")

    # ---- GENERATED 1: hasta_kayit columns ----
    L.append("## hasta_kayit columns (GENERATED — `datasheet_schema.COLUMNS`)\n")
    L.append("| # | Column | Source | Meaning |")
    L.append("|---|--------|--------|---------|")
    for i, c in enumerate(ds.COLUMNS):
        note = (c.note or "").replace("|", "\\|").replace("\n", " ")
        L.append(f"| {ds.col_letter(i)} | {c.header} | {c.source} | {note} |")
    L.append("")

    # ---- GENERATED 2: displayed features ----
    L.append("## Displayed measurement features (GENERATED — `feature_catalog`)\n")
    L.append("| Feature | Label | Tier |")
    L.append("|---------|-------|------|")
    def _tier(k):
        if k in fc.HEADLINE_FEATURES:
            return "headline"
        if k in fc.SECONDARY_FEATURES:
            return "secondary"
        return "card-only"
    for k, fd in fc.FEATURES.items():
        L.append(f"| `{k}` | {fd.label} | {_tier(k)} |")
    L.append("")

    # ---- GENERATED 3: QA detectors + thresholds ----
    L.append("## Image-quality QA detectors (GENERATED — `image_quality._DETECTOR_REGISTRY` + "
             "`qa_thresholds.THRESHOLDS`)\n")
    L.append(f"Active detectors ({len(iq._DETECTOR_REGISTRY)}): "
             + ", ".join(f"`{n}`" for n, _ in iq._DETECTOR_REGISTRY) + "\n")
    L.append("| Threshold key | Value | Units | Tier | Calibrated |")
    L.append("|---------------|-------|-------|------|------------|")
    for name, e in qt.THRESHOLDS.items():
        val = "nan (pending)" if (isinstance(e.value, float) and math.isnan(e.value)) else e.value
        cal = e.calibrated_on or ("n/a (Tier-A)" if e.tier == "A" else "pending")
        L.append(f"| `{name}` | {val} | {e.units} | {e.tier} | {cal} |")
    L.append("")

    # ---- GENERATED 4: acquisition-QA verdict vocabulary ----
    L.append("## Acquisition-QA verdict vocabulary\n")
    L.append("### study_qa (operator `Kontrol Sonucu`) — verdict levels + reason phrases\n")
    L.append("`study_qa` renders one of three levels; `render_cell` prefixes the cell:\n")
    L.append("| Level | Prefix | Meaning |")
    L.append("|-------|--------|---------|")
    L.append("| fail | ❌ | a re-record-worthy problem: a missing modality (`femur/RF/sweep eksik`) "
             "or `gerçek RF yok` (RF on the femur plane) |")
    L.append("| warn | ⚠️ | a softer deviation: `RF hafif derin/sığ (Ncm)`, a ruler mismatch, a "
             "scored-role orphan (`… cine yok`), high brightness, or a deferred sub-cohort |")
    L.append("| ok | ✅ | `tam` — all three cines present, RF anchored |")
    L.append("")
    L.append("### study_audit.CHECKS (analyst reconciliation registry — GENERATED via `legend()`)\n")
    L.append("| Code | Severity | Audience | Action | Boundary | Blurb |")
    L.append("|------|----------|----------|--------|----------|-------|")
    for r in sa.legend():
        blurb = r["blurb"].replace("|", "\\|")
        defer = " _(deferred)_" if r.get("deferred") else ""
        L.append(f"| {r['code']} | {r['severity']} | {r['audience']} | {r['action']} | "
                 f"{r['boundary']} | {blurb}{defer} |")
    L.append("")

    # ---- CURATED sections ----
    L.append("## Acquisition protocol — the 3-cine scheme (CURATED)\n")
    L.append("Each single-leg study is three cines, classified by **length + depth** (relative rank, "
             "no fixed protocol depth — `study_qa`):\n")
    L.append("| Cine | Length | Depth | Role in scoring |")
    L.append("|------|--------|-------|-----------------|")
    L.append("| **RF** | short | ~4.9 cm, ALWAYS shallow (the one fixed anchor) | texture (int_cv / "
             "echo) from the center-RF frame |")
    L.append("| **femur** | short | 6.9 cm+, FREE (deepest cine) | all geometry + fat + QMT/STAR |")
    L.append("| **sweep** | long | ~4.9 cm, MAY go deep | EI cross-check only (not scored) |")
    L.append("")
    L.append("Roles are assigned by RELATIVE rank: femur = deepest cine, RF = shallowest short cine, "
             "sweep = longest clip. Robust to a tweaked-deeper femur and to probe 831 (~9cm) vs 829 "
             "(~4.9cm). The ONE absolute check: if even the shallowest capture sits on the femur plane "
             "(≥6.4 cm), no real RF was recorded → fail.\n")

    L.append("## Study encoding (CURATED)\n")
    L.append("- **study_uid** = `NR` + first 8 hex of sha256(folder-name), uppercased "
             "(`study_metadata.study_uid_for_dirname`). Collision-free (distinct studies have "
             "distinct folder names), PHI-safe, and identical to the new-round cohort's join key. "
             "**This is the `qa` tab's row key.**\n")
    L.append("- **AKA<seq>** = a DISPLAY pseudonym only (`lookup_study`). Its sequence numbers repeat "
             "across dates/annotators, so it is **NOT collision-safe** — never key identity on it.\n")
    L.append("- **run_study_uid** (`study_metadata.run_study_uid`) derives the same `NR`+sha8 from a "
             "ledger run's stored `study_path` basename — so the record overlay joins to the spine by "
             "study_uid, NOT by the colliding study_code.\n")

    L.append("## Standing assumptions & known exceptions (CURATED)\n")
    L.append("- **RF-deep = FLAG, not withhold.** A slightly-deep RF (`RF hafif derin (Ncm)`) is "
             "surfaced for the reviewer to verify; it does not withhold the measurement. Only RF on "
             "the femur plane (`gerçek RF yok`) is a re-record fail.\n")
    L.append("- **Texture withheld ⇒ no condition score.** When `int_cv_within` is null (11/66 "
             "new-round studies, low-reliability RF texture), headline C AND the no-femur fallback A "
             "both lose their texture axis → `condition_score` is blank. This is honest, not a bug.\n")
    L.append("- **QMT provenance tiers:** `standard_qmt` (femur-anchored), `femur_supported_proxy` "
             "(femur present but the standard bounds degrade), `no_femur_proxy` (no measurable "
             "femur). Only `standard_qmt` is fully trusted for QMT/STAR.\n")
    L.append("- **`configs/study_codes.csv` is a committed PHI exception** — it maps folder dirnames "
             "(PHI) to display pseudonyms and is deliberately in-repo (both repos private; no public "
             "flip without a PHI scrub). Every OTHER committed artifact (this dictionary included) is "
             "study_uid-only.\n")
    L.append("- **Batch spine rows are `auto-frames`** — measured on the automated top-5-by-RF-area "
             "selection. A row flips to `operator-frames (run <date>)` only when a finalized "
             "runs.jsonl run matches its study_uid (the record overlay). Same scorer either way: "
             "only the FRAME SELECTION differs, so this is a sensitivity readout, not a human "
             "verdict on the score.\n")
    L.append("- **femur_qmt_consistency is EXCLUDED** from the `qa` tab (deferred: selection-"
             "dependent + a provisional 0.05 CV threshold, memory `project_qmt_cv_flag_calibration`).\n")

    return "\n".join(L)


def emit_dictionary() -> Path:
    DICTIONARY_MD.parent.mkdir(parents=True, exist_ok=True)
    DICTIONARY_MD.write_text(render_dictionary(), encoding="utf-8")
    return DICTIONARY_MD


# ==================== CLI ====================
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the DRY 'qa' projection (preview + payload only).")
    ap.add_argument("--no-sheet", action="store_true", help="skip the live 'kayit' read (blanks kontrol/force)")
    ap.add_argument("--emit-dict", action="store_true", help="(re)write docs/hasta_kayit_dictionary.md")
    ap.add_argument("--analysis", action="store_true",
                    help="also write data/qa_master.csv + data/qa_frames.csv (gitignored, PHI)")
    ap.add_argument("--apply", action="store_true", help="[GUARDED] write the 'qa' tab — NOT implemented")
    args = ap.parse_args(argv)

    if args.apply:
        assert _WRITE_GUARD, ("--apply is guarded off: writing the 'qa' tab is a separate, "
                              "human-reviewed step not implemented this pass.")
        apply({})                                     # unreachable (guard is False) — belt and braces
        return 0

    rows = build_rows(read_sheet=not args.no_sheet, record_flags=args.analysis)
    payload = to_payload(rows)
    preview = write_preview(rows)

    print_table(rows)
    # NOTE the prefix must track build_overlay's provenance string ("operator-frames (run <date>)").
    # It used to read "operator-vetted", which build_overlay stopped emitting — the counter printed
    # 0 while 10 rows carried the overlay.
    n_overlay = sum(1 for r in rows if str(r.get("provenance") or "").startswith("operator-frames"))
    print()
    print(f"spine rows (materialized studies): {len(rows)}")
    print(f"record-overlay rows (finalized run matched): {n_overlay}")
    print(f"payload: title={payload['title']!r}  header cols={len(payload['header'])}  "
          f"rows={len(payload['rows'])}")
    print(f"preview TSV (gitignored, PHI): {preview}")

    if args.analysis:
        master = build_master_rows(rows)
        m = write_master(master)
        n_new = sum(1 for r in master if r.get("cohort_round") == "new")
        print(f"\nqa_master  (gitignored, PHI): {m}  rows={len(master)}  cols={len(MASTER_COLUMNS)}"
              f"  (new={n_new}, old={len(master) - n_new})")
        with_hw = sum(1 for r in master if r.get("height_cm") != "" and r.get("weight_kg") != "")
        with_isok = sum(1 for r in master if str(r.get("isok_torque_ext_nm") or "").strip())
        with_grade = sum(1 for r in master if str(r.get("quality_label") or "").strip())
        print(f"  confounders complete (height+weight): {with_hw}/{len(master)}   "
              f"isokinetic torque: {with_isok}   doctors' grade: {with_grade}")
        n_held = sum(1 for r in master if str(r.get("measurement_flags") or "").startswith("HELD"))
        n_soft = sum(1 for r in master if "SOFT" in str(r.get("measurement_flags") or ""))
        if n_held or n_soft:
            print(f"  measurement gate: {n_held} row(s) with HELD cells (blanked, pending verdict) "
                  f"· {n_soft} soft-flagged (kept, visible)")
        frames = build_frame_rows()
        f = write_frames(frames)
        n_depth = sum(1 for r in frames if r["depth_cm"] != "")
        n_sp = sum(1 for r in frames if r["pixel_spacing_row_mm"] != "")
        print(f"qa_frames  (gitignored, PHI): {f}  rows={len(frames)}  cols={len(FRAME_COLUMNS)}")
        print(f"  frames with depth_cm: {n_depth}/{len(frames)}   "
              f"with row spacing: {n_sp}/{len(frames)}   "
              f"in_opsel=True: {sum(1 for r in frames if r['in_opsel'] is True)}")

    if args.emit_dict:
        p = emit_dictionary()
        print(f"dictionary (committed, PHI-free): {p}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

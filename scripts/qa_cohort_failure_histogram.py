"""QA cohort failure-mode histogram — Build-Step 2 Phase 2 item 7 (§ 14 Step 2a).

Runs the RF image-quality detectors (`labeling_app.image_quality`) over the
full transport cohort and emits the per-reason-code frequency distribution —
the first honest readout of how often each failure mode occurs across the
cohort, and the input to the later Build-Step 2 calibration readout (§ 11.2).
Producing it completes Build-Step 2a (the front-loaded detectors + the
cohort-wide failure-mode histogram).

WHAT THIS HISTOGRAM IS, AND IS NOT — read before trusting a count:

  * The cohort masks are the annotator / CVAT masks shipped in
    `transport/transport_metadata.csv` — clean human segmentations, NOT
    segmenter output. So the detectors run on the same mask the measurements
    were computed on.
  * 13 detectors run through `compute_image_quality_flags`; `subq_fat_compressed`
    runs alongside it (it needs pixel spacing the `(img, mask)` contract omits
    — RF QA plan § 5.2). A frame whose transport row has no spacing still
    grades on the 13 spacing-free detectors.
  * `segmentation_low_confidence` is NOT run here: it needs the segmenter's
    softmax entropy, which an annotator mask cannot carry. It is honestly
    excluded and is wired in at Build-Step 2 Phase 3 (pipeline integration).
  * Calibration status is per detector, carried in each frame's detector
    metadata as `pending_calibration` (§ 11.1 / § 8.6) and surfaced in every
    `per_reason_code_frequency` row. Only 4 detectors are calibrated day one
    (`femur_*`, `edge_clip_*` — Threshold-tier A or reuse). Every other
    detector is un-calibrated Tier-B until Build-Step 2b:
      - NaN-belted (`motion_blur`, `tgc_banding`, `air_gap_reverberation`,
        `near_field_clutter`, `vessel_shadow_in_roi`) — a 0 count is the belt
        (a NaN comparison is always False), not an absent failure mode;
      - finite starting point (`wrong_site`, `dark_clip_high`, the saturation
        fraction cut-offs, `subq_fat_compressed`) — these DO fire, but on an
        un-calibrated number, so the count is a readout, not a verdict.

Usage:  python scripts/qa_cohort_failure_histogram.py
Output: data/cohort_stats/qa_failure_mode_histogram.json  +  a printed table.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))

from labeling_app.image_quality import (        # noqa: E402
    _DETECTOR_REGISTRY, compute_image_quality_flags, detect_subq_fat_compressed,
)
from labeling_app.measurements import CONTRACT_VERSION   # noqa: E402
from preflight_helpers.study_metadata import study_code_for_dirname  # noqa: E402

_TRANSPORT = _REPO / "transport"
_METADATA = _TRANSPORT / "transport_metadata.csv"
_OUT = _REPO / "data" / "cohort_stats" / "qa_failure_mode_histogram.json"
# The ANNOTATOR-mask per-(frame, detector) score table — a readout / comparison
# artifact (annotator vs predicted scores feed the mechanism audit). The
# CANONICAL calibration substrate the selector + fitter read is the
# PREDICTED-mask table (`qa_per_frame_detector_scores.parquet`), written by
# `scripts/calibration/build_predicted_score_table.py` — deployment-matched
# masks (Akadir 2026-05-28 mask-source correction). This annotator table is
# NOT what calibration fits against.
_OUT_PARQUET = _REPO / "data" / "cohort_stats" / "qa_per_frame_detector_scores_annotator.parquet"
_MODALITY = "_final.png"   # the QA modality this cohort is graded on (decision D3)

# The canonical discriminating scalar each detector emits in its metadata —
# the value the threshold fits against (verified against live metadata keys
# 2026-05-28). A detector whose score is conditionally absent (e.g.
# air_gap_reverberation when the near-surface band is too short) records NaN.
# `segmentation_low_confidence` only has a value on PREDICTED-mask runs (the
# segmenter softmax `boundary_entropy`); this annotator-mask histogram leaves
# it empty, but the predicted cohort pass
# (`scripts/calibration/build_predicted_score_table.py`) fills it.
_SCORE_KEY = {
    "motion_blur": "fascia_band_tenengrad",
    "tgc_banding": "detrended_residual_sigma",
    "air_gap_reverberation": "comb_peak_magnitude",
    "near_field_clutter": "clutter_energy",
    "vessel_shadow_in_roi": "darkest_column_mean",
    "dark_clip_high": "roi_dark_fraction",
    "saturation_high_global": "roi_saturated_fraction",
    "saturation_high_regional": "worst_block_saturated_fraction",
    "subq_fat_compressed": "central_fat_thickness_mm",
    "wrong_site": "rf_column_coverage",
    "segmentation_low_confidence": "boundary_entropy",
}


def _code_sig() -> str:
    """sha256 over the detector source files — the anti-stale-cohort signature
    (subsystem B / F). The fitter refuses to run when the live code's sig
    differs from the score table's, so a detector-code change can't silently
    pair new scores against old labels."""
    h = hashlib.sha256()
    for rel in ("labeling_app/image_quality.py", "labeling_app/qa_thresholds.py"):
        h.update((_REPO / rel).read_bytes())
    return h.hexdigest()[:16]

# Detectors run by this script: the 13 registry detectors + subq_fat_compressed.
_DETECTORS_RUN = [name for name, _ in _DETECTOR_REGISTRY] + ["subq_fat_compressed"]

# Threshold-tier B detectors whose firing threshold ships as NaN — they cannot
# fire until Build-Step 2b calibration (the § 8.6 safety belt). A 0 count for
# any of these is the belt, not evidence the failure mode is absent. This is a
# subset of the un-calibrated detectors — per-row `pending_calibration` is the
# full set (the audit's calibration boundary is `is_starting_point`, not NaN).
_BELTED_UNCALIBRATED = (
    "motion_blur", "tgc_banding", "air_gap_reverberation",
    "near_field_clutter", "vessel_shadow_in_roi",
)


def _safe_float(value):
    """Parse a CSV spacing field; None when blank or unparseable. `px_to_mm`
    handles None, so a frame missing spacing still grades on the 13 detectors
    that need none — only `subq_fat_compressed` is skipped for that frame."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _load_mask(path: Path) -> np.ndarray:
    """A multi-class label-ID mask (uint8). The transport masks are mode-'L'
    PNGs storing label IDs 0-9 directly, so a grayscale read yields the IDs."""
    with Image.open(path) as im:
        return np.asarray(im.convert("L"), dtype=np.uint8)


def _load_final(path: Path, h: int, w: int) -> np.ndarray:
    """The `_final.png` grayscale resized to the mask resolution — the QA
    detectors run on `img_gray` at mask resolution (RF QA plan § 7.2)."""
    with Image.open(path) as im:
        gray = im.convert("L")
        if gray.size != (w, h):
            gray = gray.resize((w, h), Image.BILINEAR)
        return np.asarray(gray, dtype=np.uint8)


def grade_frame(img_gray: np.ndarray, mask: np.ndarray, spacing_row: dict) -> tuple:
    """Every image-quality flag for one frame, plus the raw detector metadata.

    Returns ``(flags, detector_metadata)`` — the 13 registry detectors via
    `compute_image_quality_flags`, plus `subq_fat_compressed` (run separately as
    it needs the pixel spacing)."""
    out = compute_image_quality_flags(img_gray, mask)
    flags = list(out["flags"])
    metadata = dict(out["detector_metadata"])
    fat = detect_subq_fat_compressed(mask, spacing_row)
    metadata["subq_fat_compressed"] = fat["metadata"]
    if fat["flag"]:
        flags.append(fat["flag"])
    return tuple(flags), metadata


def _sanity_block(per_code: dict) -> dict:
    """Compare the distribution against item 7's done-when expectations
    (`edge_clip_lateral` common, `wrong_site` rare) and record any finding —
    the Step-2a histogram's § 11.2-step-2 role is to surface a weak detector
    before deployment, so an un-met expectation is reported, never silenced."""
    ecl = per_code.get("edge_clip_lateral", {}).get("fraction", 0.0)
    ws = per_code.get("wrong_site", {}).get("fraction", 0.0)
    sat = per_code.get("saturation_high_global", {}).get("fraction", 0.0)
    findings = []
    if ecl > 0.5:
        findings.append(
            f"edge_clip_lateral fires on {ecl:.1%} of frames — consistent with "
            f"the C11 coverage rationale (§ 5.2: few scans in this acquisition "
            f"set capture the full in-frame RF cross-section).")
    if ws > 0.2:
        findings.append(
            f"FINDING — wrong_site fires on {ws:.1%} of cohort frames, above the "
            f"'rare' expectation for a correct-RF cohort. wrong_site is the v3 "
            f"rf_column_coverage detector (Tier-A, fires when <50% of image "
            f"columns carry RF; commit 246e2ec); a high rate here points to a "
            f"segmentation regression or a genuinely off-site cohort — "
            f"investigate before trusting the column-coverage cut.")
    if sat == 0.0:
        findings.append(
            "FINDING (HANDOFF Finding C) — saturation_high_global / _regional "
            "fired on 0 frames, but this is NOT a clean cohort: the _final.png "
            "modality (decision D3) caps at pixel value 244 cohort-wide "
            "(956/956 frames; the device _original.jpg caps there too), below "
            "the 250 saturation rail — so the saturation detectors are "
            "structurally inert on the canonical QA modality. The _raw.png "
            "modality does reach 254. A § 13 limitation + a § 2.2 D3-revisit "
            "item; no Phase-2 code change.")
    return {
        "edge_clip_lateral_fraction": round(ecl, 4),
        "edge_clip_lateral_expectation_met": ecl > 0.5,
        "wrong_site_fraction": round(ws, 4),
        "wrong_site_rare_expectation_met": ws < 0.2,
        "saturation_modality_blind": sat == 0.0,
        "findings": findings,
    }


def build_histogram(metadata_path: Path, transport_root: Path,
                    *, score_rows: Optional[list] = None) -> dict:
    """Run every cohort frame through the detectors and tally the per-reason-code
    frequency distribution.

    `score_rows` (subsystem B) — when a list is passed, the per-(frame, detector)
    score table is materialised into it as one PHI-clean dict per detector per
    graded frame: `study_code` + `frame_base` (never the patient-name path) +
    the detector's canonical scalar (`_SCORE_KEY`) + full metadata JSON +
    `fires` + threshold + modality + contract/code signatures. The caller
    writes it to parquet. Default None keeps the aggregate-only behaviour the
    existing test relies on."""
    with open(metadata_path, newline="") as f:
        rows = list(csv.DictReader(f))

    code_sig = _code_sig()
    computed_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    code_frames = Counter({d: 0 for d in _DETECTORS_RUN})   # every detector, 0-initialised
    skipped: Counter = Counter()
    detector_pending: dict = {}                             # detector -> pending_calibration
    n_graded = 0
    n_zero_flag = 0
    n_without_spacing = 0

    for row in rows:
        mask_rel, final_rel = row.get("mask_path"), row.get("final_path")
        if not mask_rel or not final_rel:
            skipped["no_mask_or_final_path"] += 1
            continue
        mask_path, final_path = transport_root / mask_rel, transport_root / final_rel
        if not mask_path.exists() or not final_path.exists():
            skipped["mask_or_final_file_missing"] += 1
            continue
        try:
            mask = _load_mask(mask_path)
            img = _load_final(final_path, mask.shape[0], mask.shape[1])
            spacing_row = {
                "pixel_spacing_y": _safe_float(row.get("pixel_spacing_y")),
                "dcm_rows": _safe_float(row.get("dcm_rows")),
                "mask_height": int(mask.shape[0]),
            }
            flags, metadata = grade_frame(img, mask, spacing_row)
        except Exception as exc:                            # noqa: BLE001 — report, don't crash the run
            skipped[f"error:{type(exc).__name__}"] += 1
            continue
        n_graded += 1
        if spacing_row["pixel_spacing_y"] is None or spacing_row["dcm_rows"] is None:
            n_without_spacing += 1
        if not flags:
            n_zero_flag += 1
        fired_set = set(flags)
        for flag in fired_set:
            code_frames[flag] += 1
        for det_name, det_meta in metadata.items():
            if isinstance(det_meta, dict) and "pending_calibration" in det_meta:
                detector_pending[det_name] = det_meta["pending_calibration"]

        # Subsystem B — materialise the per-(frame, detector) score rows. One
        # row per detector that defines a canonical scalar (`_SCORE_KEY`). The
        # study_code is the PHI-safe map of the patient-name dirname (memory
        # feedback_phi_guardrail — the dirname / paths never enter this table).
        if score_rows is not None:
            study_code = study_code_for_dirname(row.get("study_dirname", "")) \
                or "UNKNOWN"
            frame_base = row.get("frame_base", "")
            for det_name, score_key in _SCORE_KEY.items():
                det_meta = metadata.get(det_name)
                if not isinstance(det_meta, dict):
                    continue                    # detector not run on this frame
                raw = det_meta.get(score_key)
                score_rows.append({
                    "study_code": study_code,
                    "frame_base": frame_base,
                    "detector": det_name,
                    "raw_score": float(raw) if isinstance(raw, (int, float)) else float("nan"),
                    "fires": det_name in fired_set,
                    "threshold_at_compute_time": det_meta.get("threshold"),
                    "pending_calibration": det_meta.get("pending_calibration"),
                    "metadata_json": json.dumps(
                        {k: v for k, v in det_meta.items() if k != "thresholds_used"},
                        default=str),
                    "modality": _MODALITY,
                    "contract_version": CONTRACT_VERSION,
                    "code_sig": code_sig,
                    "computed_at_utc": computed_at,
                })

    per_code = {
        code: {"frames": n,
               "fraction": round(n / n_graded, 4) if n_graded else 0.0,
               "pending_calibration": detector_pending.get(code)}
        for code, n in sorted(code_frames.items(), key=lambda kv: (-kv[1], kv[0]))
    }
    n_calibrated = sum(1 for v in per_code.values() if v["pending_calibration"] is False)
    return {
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "build_step": "Build-Step 2 Phase 2 item 7 — § 14 Step 2a (completes Build-Step 2a)",
        "cohort_source": str(metadata_path),
        "n_frames_total": len(rows),
        "n_frames_graded": n_graded,
        "n_frames_skipped": sum(skipped.values()),
        "skipped_reasons": dict(skipped),
        "n_frames_without_spacing": n_without_spacing,
        "detectors_run": _DETECTORS_RUN,
        "n_detectors_calibrated": n_calibrated,
        "detector_excluded": {
            "segmentation_low_confidence":
                "needs the segmenter softmax entropy — not derivable from an "
                "annotator mask; wired in at Build-Step 2 Phase 3.",
        },
        "detectors_belted_uncalibrated": list(_BELTED_UNCALIBRATED),
        "calibration_note":
            "per_reason_code_frequency carries `pending_calibration` per "
            "detector: only the 4 calibrated ones (pending_calibration=False — "
            "femur / edge_clip) give a trustworthy verdict today. Every other "
            "count is a readout, not a verdict, until Build-Step 2b. The "
            "belted subset (NaN threshold) additionally cannot fire at all.",
        "subq_fat_compressed_note":
            f"evaluable on {n_graded - n_without_spacing} of {n_graded} graded "
            f"frames — the other {n_without_spacing} have no pixel spacing in "
            f"the transport metadata, so its mm threshold cannot be applied.",
        "frames_with_zero_flags": n_zero_flag,
        "per_reason_code_frequency": per_code,
        "sanity": _sanity_block(per_code),
    }


def _print_table(hist: dict) -> None:
    g = hist["n_frames_graded"]
    print(f"\nQA cohort failure-mode histogram — {g} frames graded "
          f"({hist['n_frames_skipped']} skipped of {hist['n_frames_total']})")
    print(f"{'reason code':30s} {'frames':>7s} {'fraction':>9s}  calibration")
    print("-" * 78)
    for code, v in hist["per_reason_code_frequency"].items():
        if code in _BELTED_UNCALIBRATED:
            note = "belted — NaN threshold, cannot fire until 2b"
        elif v.get("pending_calibration"):
            note = "pending calibration — un-calibrated Tier-B"
        else:
            note = "calibrated (Tier-A / reuse)"
        print(f"{code:30s} {v['frames']:7d} {v['fraction']:9.4f}  {note}")
    print("-" * 78)
    print(f"{'(frames with no flag)':30s} {hist['frames_with_zero_flags']:7d}")
    for finding in hist["sanity"]["findings"]:
        print(f"\n* {finding}")
    print(f"\nExcluded: segmentation_low_confidence — needs the segmenter "
          f"softmax (Phase-3 wiring).")


def write_score_table(score_rows: list, out_path: Path = _OUT_PARQUET) -> None:
    """Write the per-(frame, detector) score rows to parquet (subsystem B)."""
    import pandas as pd
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(score_rows)
    df.to_parquet(out_path, index=False)
    n_studies = df["study_code"].nunique() if not df.empty else 0
    n_detectors = df["detector"].nunique() if not df.empty else 0
    print(f"\nScore table: {len(df)} rows ({n_studies} studies × "
          f"{n_detectors} detectors) → {out_path}")
    # PHI guard — a loud assertion that no patient-name column leaked in.
    assert "study_dirname" not in df.columns and "study_id" not in df.columns \
        and "mask_path" not in df.columns, "PHI column leaked into score table"


def main() -> None:
    score_rows: list = []
    hist = build_histogram(_METADATA, _TRANSPORT, score_rows=score_rows)
    # E4 — study-level demotion of a pervasive per-frame flag. dark_clip_high
    # (under-gain) is bimodal (~30/319 studies fire on >=80% of frames, the rest
    # near-zero); surface those studies ONCE as a study-level under-gain cue
    # instead of painting their every frame (the calibrated per-frame detector is
    # unchanged — this is an aggregation/surfacing layer on top). The fraction is
    # over the ~3 transport frames/study today (granularity ~1/3), but the pervasive
    # set is a clean spike at 1.0 so the 0.8 cut is robust; it refines when the loop
    # grades full sweeps.
    import pandas as pd
    from preflight_helpers.study_level_qa import (
        per_study_firing_fraction, pervasive_studies)
    _sdf = pd.DataFrame(score_rows)
    hist["study_level_under_gain"] = {}
    if not _sdf.empty and "dark_clip_high" in set(_sdf["detector"]):
        _dc = _sdf[_sdf["detector"] == "dark_clip_high"]
        hist["study_level_under_gain"]["dark_clip_high"] = pervasive_studies(
            per_study_firing_fraction(_dc["study_code"], _dc["fires"]), 0.8)
    _OUT.parent.mkdir(parents=True, exist_ok=True)
    _OUT.write_text(json.dumps(hist, indent=2))
    _print_table(hist)
    print(f"\nWritten: {_OUT}")
    write_score_table(score_rows)


if __name__ == "__main__":
    main()

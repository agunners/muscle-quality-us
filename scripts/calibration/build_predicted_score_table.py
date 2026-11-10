"""Build the PREDICTED-mask cohort score table — the calibration substrate.

Runs the canonical segmenter over the transport cohort and grades all 15 QA
detectors on the PREDICTED masks (incl `segmentation_low_confidence`, whose
score — the softmax `boundary_entropy` — only exists on a predicted run). Writes
`data/cohort_stats/qa_per_frame_detector_scores.parquet` — the deployment-matched
table the candidate selector ranks on and the fitter joins against.

Why predicted, not annotator (Akadir 2026-05-28): production QA runs the
detectors on the segmenter's predicted masks, so a threshold fit on annotator
scores would be calibrated on a different distribution than it operates on
(train/serve skew). Grading on predicted masks dissolves that gap AND is the
only mask source on which `segmentation_low_confidence` has a value.

PHI-clean: rows carry study_code + frame_base only (never the patient-name path).
Needs the model bundle + a GPU. Run:  python scripts/calibration/build_predicted_score_table.py [--limit N]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from pathlib import Path

import numpy as np
import torch
from PIL import Image

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))

from labeling_app.measurements import CONTRACT_VERSION                 # noqa: E402
from preflight_helpers.segmenter import (                              # noqa: E402
    BundleMeta, build_voi_hist_clahe_stack, load_learner_safe, predict_mask,
)
from preflight_helpers.feature_compute import compute_image_quality_features  # noqa: E402
from preflight_helpers.study_metadata import study_code_for_dirname    # noqa: E402
from preflight_helpers import env                                      # noqa: E402
# Reuse the canonical score-key map, code-sig, and parquet writer.
from qa_cohort_failure_histogram import _SCORE_KEY, _code_sig, write_score_table  # noqa: E402

_TRANSPORT = _REPO / "transport"
_METADATA = _TRANSPORT / "transport_metadata.csv"
_OUT = _REPO / "data" / "cohort_stats" / "qa_per_frame_detector_scores.parquet"
_MODALITY = "_final.png"


def _find_canonical_bundle() -> Path:
    # The pinned canonical bundle (configs/canonical_bundle.json), not newest-mtime —
    # the calibration table must be built on the same segmenter the cohort was.
    from preflight_helpers.model_registry import resolve_canonical_pkl
    p = resolve_canonical_pkl()
    if p is None:
        raise SystemExit(f"No canonical bundle resolvable (pointer + {env.MODEL_DIR_LOCAL})")
    return p


def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _load_final(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("L"), dtype=np.uint8)


def build_predicted_score_rows(*, limit: int | None = None) -> list[dict]:
    bundle_pkl = _find_canonical_bundle()
    meta = BundleMeta.from_pkl(bundle_pkl)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    learn = load_learner_safe(bundle_pkl)
    print(f"[bundle] {bundle_pkl.name}  modalities={list(meta.modalities)} "
          f"resize_to={meta.resize_to}  device={device}")

    code_sig = _code_sig()
    computed_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    with open(_METADATA, newline="") as f:
        rows = list(csv.DictReader(f))
    if limit:
        rows = rows[:limit]

    out_rows: list[dict] = []
    n_done = n_skip = 0
    for i, row in enumerate(rows):
        final_rel = row.get("final_path")
        if not final_rel:
            n_skip += 1
            continue
        final_path = _TRANSPORT / final_rel
        if not final_path.exists():
            n_skip += 1
            continue
        try:
            final_u8 = _load_final(final_path)
            stacked = build_voi_hist_clahe_stack(final_u8, modalities=meta.modalities)
            conf: dict = {}
            pred = predict_mask(learn, stacked, meta, device=device, confidence_out=conf)
            boundary_entropy = conf.get("boundary_entropy")
            spacing = {
                "pixel_spacing_y": _safe_float(row.get("pixel_spacing_y")),
                "dcm_rows": _safe_float(row.get("dcm_rows")),
                "mask_height": int(pred.shape[0]),
            }
            iqf = compute_image_quality_features(
                frame_base=row.get("frame_base", ""),
                multiclass_mask=pred,
                final_image_u8=final_u8,
                spacing_row=spacing,
                boundary_entropy=boundary_entropy,
                qmt_provenance=None,        # QMT floor affects qmt tiers, not raw scores
            )
        except Exception as exc:            # noqa: BLE001 — report, don't crash the batch
            print(f"  ! skip {row.get('frame_base')}: {type(exc).__name__}: {exc}")
            n_skip += 1
            continue

        metadata = iqf.image_quality_detector_metadata
        fired = set(iqf.image_quality_validity_flags)
        study_code = study_code_for_dirname(row.get("study_dirname", "")) or "UNKNOWN"
        frame_base = row.get("frame_base", "")
        for det, score_key in _SCORE_KEY.items():
            det_meta = metadata.get(det)
            if not isinstance(det_meta, dict):
                continue
            raw = det_meta.get(score_key)
            out_rows.append({
                "study_code": study_code,
                "frame_base": frame_base,
                "detector": det,
                "raw_score": float(raw) if isinstance(raw, (int, float)) else float("nan"),
                "fires": det in fired,
                "threshold_at_compute_time": det_meta.get("threshold"),
                "pending_calibration": det_meta.get("pending_calibration"),
                "metadata_json": _json_meta(det_meta),
                "modality": _MODALITY,
                "mask_source": "predicted",
                "contract_version": CONTRACT_VERSION,
                "code_sig": code_sig,
                "computed_at_utc": computed_at,
            })
        n_done += 1
        if n_done % 100 == 0:
            print(f"  … {n_done}/{len(rows)} frames graded")

    print(f"[done] {n_done} frames graded, {n_skip} skipped")
    return out_rows


def _json_meta(det_meta: dict) -> str:
    import json
    return json.dumps({k: v for k, v in det_meta.items() if k != "thresholds_used"},
                      default=str)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None,
                    help="grade only the first N cohort frames (smoke test)")
    args = ap.parse_args()
    rows = build_predicted_score_rows(limit=args.limit)
    write_score_table(rows, _OUT)
    # Quick per-detector readout so a run is self-verifying.
    import pandas as pd
    df = pd.DataFrame(rows)
    print("\nPer-detector predicted-score spread:")
    for det in _SCORE_KEY:
        s = df[df.detector == det]["raw_score"] if not df.empty else pd.Series(dtype=float)
        nv = int(s.notna().sum())
        if nv:
            print(f"  {det:28s} n_valid={nv:4d}  min={s.min():.3g} "
                  f"med={s.median():.3g} max={s.max():.3g}  fires={int(df[(df.detector==det)].fires.sum())}")
        else:
            print(f"  {det:28s} no valid scores")


if __name__ == "__main__":
    main()

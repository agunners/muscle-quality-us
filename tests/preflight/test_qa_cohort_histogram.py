"""Build-Step 2 Phase 2 item 7 — the cohort failure-mode histogram script.

scripts/qa_cohort_failure_histogram.py runs the QA detectors over the transport
cohort. This exercises its loop / load / tally / skip logic on a controlled
synthetic mini-cohort; the detectors themselves are covered exhaustively by
test_image_quality.py, and the real 956-frame cohort run is the § 14 Step-2a
deliverable (data/cohort_stats/qa_failure_mode_histogram.json).
"""
from __future__ import annotations

import csv

import numpy as np
from PIL import Image

from labeling_app.measurements import LABEL_FEMUR, LABEL_RF
from qa_cohort_failure_histogram import build_histogram


def _write_png(path, arr) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr.astype(np.uint8)).save(path)        # 2-D uint8 -> mode 'L'


def _write_cohort(root, frames) -> "Path":
    """frames: list of (mask_array | None, final_array). A None mask means the
    file is not written — exercising the skip path."""
    csv_rows = []
    for i, (mask, final) in enumerate(frames):
        mrel, frel = f"f{i}_mask.png", f"f{i}_final.png"
        if mask is not None:
            _write_png(root / mrel, mask)
        _write_png(root / frel, final)
        csv_rows.append({"mask_path": mrel, "final_path": frel,
                         "pixel_spacing_y": "0.13", "dcm_rows": "585"})
    csv_path = root / "transport_metadata.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["mask_path", "final_path", "pixel_spacing_y", "dcm_rows"])
        writer.writeheader()
        writer.writerows(csv_rows)
    return csv_path


def test_build_histogram_tallies_a_synthetic_cohort(tmp_path) -> None:
    """Two frames load and grade; a no-femur frame contributes exactly one
    `femur_out_of_frame`, and every detector appears in the table even at 0."""
    h, w = 60, 60
    rf_and_femur = np.zeros((h, w), np.uint8)
    rf_and_femur[15:40, 15:45] = LABEL_RF
    rf_and_femur[45:55, 22:38] = LABEL_FEMUR        # femur present
    rf_only = np.zeros((h, w), np.uint8)
    rf_only[15:40, 15:45] = LABEL_RF                # no femur -> femur_out_of_frame
    flat = np.full((h, w), 110, np.uint8)

    csv_path = _write_cohort(tmp_path, [(rf_and_femur, flat), (rf_only, flat)])
    hist = build_histogram(csv_path, tmp_path)

    assert hist["n_frames_total"] == 2
    assert hist["n_frames_graded"] == 2
    assert hist["n_frames_skipped"] == 0
    assert hist["per_reason_code_frequency"]["femur_out_of_frame"]["frames"] == 1
    # 0-initialised Counter — a never-firing detector still has a table row
    assert "motion_blur" in hist["per_reason_code_frequency"]


def test_build_histogram_skips_a_frame_with_a_missing_file(tmp_path) -> None:
    """A cohort row whose mask file is absent is skipped with a recorded
    reason — the run never crashes on incomplete transport data."""
    rf = np.zeros((50, 50), np.uint8)
    rf[10:30, 10:30] = LABEL_RF
    flat = np.full((50, 50), 110, np.uint8)
    csv_path = _write_cohort(tmp_path, [(rf, flat), (None, flat)])

    hist = build_histogram(csv_path, tmp_path)
    assert hist["n_frames_graded"] == 1
    assert hist["n_frames_skipped"] == 1
    assert hist["skipped_reasons"].get("mask_or_final_file_missing") == 1

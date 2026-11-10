"""Subsystem B — the per-(frame, detector) cohort score table.

`scripts/qa_cohort_failure_histogram.py` gained a `score_rows` out-param +
`write_score_table`. These tests pin: the row schema, the PHI-clean discipline
(study_code only — never the patient-name dirname/paths), the canonical
per-detector score key, NaN handling for conditionally-absent scores, and the
parquet round-trip.
"""
from __future__ import annotations

import csv

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from labeling_app.measurements import LABEL_FEMUR, LABEL_RF
from qa_cohort_failure_histogram import (
    _SCORE_KEY, _code_sig, build_histogram, write_score_table,
)


def _write_png(path, arr) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr.astype(np.uint8)).save(path)


def _write_cohort(root, frames) -> "object":
    """frames: list of (mask, final). CSV carries study_dirname + frame_base so
    the PHI-safe study_code mapping path is exercised."""
    csv_rows = []
    for i, (mask, final) in enumerate(frames):
        mrel, frel = f"f{i}_mask.png", f"f{i}_final.png"
        _write_png(root / mrel, mask)
        _write_png(root / frel, final)
        csv_rows.append({
            "mask_path": mrel, "final_path": frel,
            "pixel_spacing_y": "0.13", "dcm_rows": "585",
            "study_dirname": f"synthetic_study_{i}",   # won't resolve → UNKNOWN
            "frame_base": f"2026010{i}_120000000",
        })
    csv_path = root / "transport_metadata.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(csv_rows[0].keys()))
        writer.writeheader()
        writer.writerows(csv_rows)
    return csv_path


def _two_frame_cohort(tmp_path):
    h, w = 60, 60
    rf = np.zeros((h, w), np.uint8)
    rf[15:40, 15:45] = LABEL_RF
    rf2 = np.zeros((h, w), np.uint8)
    rf2[15:40, 15:45] = LABEL_RF
    rf2[45:55, 22:38] = LABEL_FEMUR
    flat = np.full((h, w), 110, np.uint8)
    return _write_cohort(tmp_path, [(rf, flat), (rf2, flat)])


def test_score_rows_emitted_when_list_passed(tmp_path) -> None:
    """A row per (frame, detector-with-a-score-key) lands in score_rows; the
    aggregate dict is unchanged (the existing test path)."""
    csv_path = _two_frame_cohort(tmp_path)
    rows: list = []
    hist = build_histogram(csv_path, tmp_path, score_rows=rows)
    assert hist["n_frames_graded"] == 2
    # The annotator histogram path produces a row per detector that emits a
    # score on annotator masks — i.e. every _SCORE_KEY detector EXCEPT
    # segmentation_low_confidence, whose boundary_entropy only exists on a
    # predicted-mask run (build_predicted_score_table.py). So 2 frames ×
    # (all but seg).
    annotator_detectors = set(_SCORE_KEY) - {"segmentation_low_confidence"}
    assert len(rows) == 2 * len(annotator_detectors)
    detectors = {r["detector"] for r in rows}
    assert detectors == annotator_detectors


def test_score_rows_omitted_by_default(tmp_path) -> None:
    """Backward-compat — no score_rows param means aggregate-only (the
    existing test relies on this)."""
    csv_path = _two_frame_cohort(tmp_path)
    hist = build_histogram(csv_path, tmp_path)      # no score_rows
    assert hist["n_frames_graded"] == 2             # unchanged behaviour


def test_score_rows_are_phi_clean(tmp_path) -> None:
    """The row carries study_code (here UNKNOWN — synthetic dirname doesn't
    resolve) + frame_base, NEVER the patient-name dirname or any path."""
    csv_path = _two_frame_cohort(tmp_path)
    rows: list = []
    build_histogram(csv_path, tmp_path, score_rows=rows)
    for r in rows:
        assert set(r.keys()) >= {
            "study_code", "frame_base", "detector", "raw_score", "fires",
            "metadata_json", "code_sig", "contract_version"}
        assert "study_dirname" not in r
        assert "mask_path" not in r and "final_path" not in r
        assert r["study_code"] == "UNKNOWN"          # synthetic, unresolved
        assert r["frame_base"].startswith("2026")


def test_score_key_maps_to_a_real_metadata_value(tmp_path) -> None:
    """The canonical score lands in raw_score — dark_clip_high's roi_dark_fraction
    is a finite float on a frame with an RF region."""
    csv_path = _two_frame_cohort(tmp_path)
    rows: list = []
    build_histogram(csv_path, tmp_path, score_rows=rows)
    dch = [r for r in rows if r["detector"] == "dark_clip_high"]
    assert dch and all(isinstance(r["raw_score"], float) for r in dch)
    # roi_dark_fraction is in [0, 1].
    assert all(0.0 <= r["raw_score"] <= 1.0 for r in dch)


def test_conditionally_absent_score_is_nan_not_crash(tmp_path) -> None:
    """air_gap_reverberation emits no comb_peak_magnitude when the near-surface
    band is too short (tiny synthetic frame) — the row records NaN, not a
    crash, not a missing row."""
    csv_path = _two_frame_cohort(tmp_path)
    rows: list = []
    build_histogram(csv_path, tmp_path, score_rows=rows)
    agr = [r for r in rows if r["detector"] == "air_gap_reverberation"]
    assert agr, "air_gap_reverberation must still produce a row"
    # On a 60×60 frame the band is too short → NaN score, but the row exists.
    assert all(isinstance(r["raw_score"], float) for r in agr)


def test_write_score_table_round_trips(tmp_path) -> None:
    csv_path = _two_frame_cohort(tmp_path)
    rows: list = []
    build_histogram(csv_path, tmp_path, score_rows=rows)
    out = tmp_path / "scores.parquet"
    write_score_table(rows, out)
    df = pd.read_parquet(out)
    assert len(df) == len(rows)
    assert "study_code" in df.columns
    assert "study_dirname" not in df.columns         # PHI guard held


def test_code_sig_is_stable_and_short(tmp_path) -> None:
    """The anti-stale-cohort signature is deterministic across calls (same
    source) and a short hex digest."""
    a, b = _code_sig(), _code_sig()
    assert a == b and len(a) == 16 and all(c in "0123456789abcdef" for c in a)

"""The study-landmark helper — the median over the top-K largest-clean RF frames.

The measurement wrapper (`measure_frame_geometry`) is a thin call into the
heavily-tested `compute_frame_features` and is already exercised end-to-end by the
cohort precompute; these tests pin the NEW logic — ranking the sweep, taking the
top-K clean, and medianing — plus that the wrapper returns the landmark keys.
"""
from __future__ import annotations

import numpy as np

from labeling_app.measurements import LABEL_RF
from preflight_helpers import landmark as L

_CLEAN = {"image_quality_validity_flags": [],
          "image_quality_pending_calibration_flags": [],
          "image_quality_aggregate_tier": "valid"}


def _mask(width: int) -> np.ndarray:
    m = np.zeros((20, 40), dtype=int)
    m[8:12, 20 - width // 2:20 + width // 2] = LABEL_RF   # 4 rows × width cols
    return m


def test_compute_study_landmark_medians_the_top_k(monkeypatch):
    masks = {"big": _mask(30), "mid": _mask(16), "small": _mask(6), "tiny": _mask(2)}
    grades = {fb: _CLEAN for fb in masks}
    # Stub the measurement: return a value keyed off the mask's RF-pixel area, so
    # the median over the top-3 (big, mid, small) is the MIDDLE one's value.
    by_area = {120: 300.0, 64: 160.0, 24: 60.0, 8: 20.0}   # 4*width -> value

    def _fake_measure(mask, final, spacing):
        v = by_area[int((mask == LABEL_RF).sum())]
        return {"visible_rf_area_mm2": v, "rf_thickness_mm": v / 10,
                "full_thickness_q50_mm": v / 12}

    monkeypatch.setattr(L, "measure_frame_geometry", _fake_measure)
    out = L.compute_study_landmark(masks, grades, finals={}, spacing_row={}, k=3)
    assert out["frames"][0] == "big"                       # largest-clean first
    assert len(out["frames"]) == 3                          # big, mid, small
    assert out["values"]["visible_rf_area_mm2"] == 160.0    # median(300,160,60)
    assert out["values"]["rf_thickness_mm"] == 16.0


def test_compute_study_landmark_withholds_when_no_clean_frame():
    masks = {"a": _mask(30), "b": _mask(20)}
    grades = {  # both GEOMETRY-gated (wrong_site / vertical clip) -> no clean cand.
        "a": {**_CLEAN, "image_quality_validity_flags": ["wrong_site"]},
        "b": {**_CLEAN, "image_quality_validity_flags": ["edge_clip_vertical"]},
    }
    out = L.compute_study_landmark(masks, grades, finals={}, spacing_row={}, k=5)
    assert out["values"] == {} and out["frames"] == []


def test_compute_study_landmark_withholds_nonpositive_measurements(monkeypatch):
    masks = {"a": _mask(30), "b": _mask(20)}
    grades = {fb: _CLEAN for fb in masks}
    # a degenerate spacing/mask yields 0 area / NaN / negative thickness — these
    # must be withheld, NOT medianed into a non-physical 0 landmark.
    monkeypatch.setattr(L, "measure_frame_geometry", lambda m, f, s: {
        "visible_rf_area_mm2": 0.0, "rf_thickness_mm": float("nan"),
        "full_thickness_q50_mm": -1.0})
    out = L.compute_study_landmark(masks, grades, finals={}, spacing_row={}, k=5)
    assert out["values"] == {}                     # all non-positive -> withheld


def test_measure_frame_geometry_returns_the_landmark_keys():
    mask = _mask(24)
    spacing = {"pixel_spacing_x": 0.1, "pixel_spacing_y": 0.1, "dcm_rows": 20,
               "dcm_cols": 40, "jpg_height": 20, "jpg_width": 40,
               "mask_height": 20, "mask_width": 40}
    ff = L.measure_frame_geometry(mask, None, spacing)
    for key in L.LANDMARK_FEATURES:
        assert key in ff
    assert ff["visible_rf_area_mm2"] is not None            # RF present -> a real area

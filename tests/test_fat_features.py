"""Superficial-fat (SAT) thickness from the fat segmentation (`labeling_app/fat_features.py`).

The SAT region is the SINGLE largest fat component, hole-filled, restricted to
above the muscle (Akadir's instruction). The measure + the drawn caliper read that
region's own top-to-bottom extent — so a stray skin pixel inside the fat or a
fascia pixel below it can never become the boundary. Live-frame verification is
the akadir-reviewer (docs/akadir_reviewer_profile.md)."""
import numpy as np

from labeling_app.fat_features import build_superficial_fat_mask, compute_fat_geometry, fat_q50_column
from labeling_app.measurements import (
    LABEL_FASCIA, LABEL_FAT, LABEL_FEMUR, LABEL_RF, LABEL_SKIN,
)


def _synthetic() -> np.ndarray:
    m = np.zeros((120, 80), dtype=np.int64)
    m[0:6, 10:70] = LABEL_SKIN        # skin, rows 0-5
    m[6:21, 10:70] = LABEL_FAT        # subcutaneous fat, rows 6-20 (15px slab)
    m[21:24, 10:70] = LABEL_FASCIA    # fascia, rows 21-23
    m[24:61, 10:70] = LABEL_RF        # RF muscle, rows 24-60
    m[40:46, 30:50] = LABEL_FAT       # DEEP intramuscular fat — must be excluded
    m[90:111, 15:65] = LABEL_FEMUR    # femur, rows 90-110
    return m


def test_band_excludes_deep_fat_and_keeps_subcutaneous():
    band = build_superficial_fat_mask(_synthetic())
    assert not band[40:46, 30:50].any(), "deep intramuscular fat leaked into the slab"
    assert band[6:21, 10:70].any(), "subcutaneous fat missing from the slab"
    ys = np.flatnonzero(band[:, 40])
    assert ys.min() >= 6 and ys.max() <= 20, f"slab spans {ys.min()}-{ys.max()}, expected 6-20"


def test_fat_thickness_and_ratio():
    spacing = {"pixel_spacing_y": 0.1, "dcm_rows": 120, "mask_height": 120}  # mm_per_y = 0.1
    g = compute_fat_geometry(_synthetic(), spacing)
    # fat slab rows 6-20 -> extent 15px * 0.1 = 1.5mm
    assert g["fat_thickness_q50_mm"] is not None
    assert abs(g["fat_thickness_q50_mm"] - 1.5) < 0.2, g["fat_thickness_q50_mm"]
    assert g["fat_thickness_max_mm"] is not None and g["fat_thickness_max_mm"] >= g["fat_thickness_q50_mm"]
    # fat (~15px) / skin-to-femur (~85px) ~ 0.18
    assert g["fat_to_depth_q50"] is not None
    assert 0.12 < g["fat_to_depth_q50"] < 0.22, g["fat_to_depth_q50"]


def test_fat_ignores_skin_stray_inside_the_fat():
    """A skin pixel mislabeled INSIDE the fat must NOT pull the measured/drawn top
    down to it — the single-component + hole-fill closes it (Akadir 2026-06-04, the
    screenshot defect)."""
    m = _synthetic()
    m[12, 38:44] = LABEL_SKIN   # a skin speckle in the middle of the fat slab
    g = compute_fat_geometry(m, {"pixel_spacing_y": 0.1, "dcm_rows": 120, "mask_height": 120})
    assert abs(g["fat_thickness_q50_mm"] - 1.5) < 0.2, g["fat_thickness_q50_mm"]
    c, _th, fat_top, fat_bottom = fat_q50_column(m)
    assert fat_top == 6, f"fat top pulled to the stray: {fat_top}"   # still row 6, not 12
    assert fat_bottom == 20


def test_fat_q50_column_for_overlay():
    col = fat_q50_column(_synthetic())
    assert col is not None
    c, th, fat_top_y, fat_bottom_y = col
    assert fat_top_y == 6 and fat_bottom_y == 20   # the fat slab's own top/bottom
    assert abs(th - 15.0) < 0.5                     # 20 - 6 + 1 = 15px


def test_missing_inputs_return_none():
    m = np.zeros((100, 80), dtype=np.int64)   # no RF columns
    g = compute_fat_geometry(m, {"pixel_spacing_y": 0.1, "dcm_rows": 100, "mask_height": 100})
    assert g["fat_thickness_q50_mm"] is None
    assert g["fat_to_depth_q50"] is None

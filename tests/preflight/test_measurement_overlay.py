"""F11 — measurement-location overlay: q50-thickness column finder + annotate.

`q50_thickness_column` must locate the column whose per-column thickness sits
at the q50 — that is where the white-paper `full_thickness_q50_mm` is actually
measured, and F11 draws the marker there. `annotate_overlay` must compose the
inherited drawing stack without ever blanking the overlay on a bad mask.
"""
from __future__ import annotations

import numpy as np

from labeling_app.assessment_pages.measurement_overlay import (
    annotate_overlay, q50_thickness_column,
)


def test_q50_thickness_column_picks_the_median_thickness_column() -> None:
    # 5 columns with RF-pixel runs of thickness 2/4/6/8/10 (q50 = 6).
    mask = np.zeros((20, 5), dtype=bool)
    for col, thickness in enumerate([2, 4, 6, 8, 10]):
        mask[2:2 + thickness, col] = True
    col, thickness_px, top_y, bot_y = q50_thickness_column(mask)
    assert col == 2                       # the thickness-6 column
    assert thickness_px == 6.0
    assert top_y == 2 and bot_y == 7      # ys.max - ys.min + 1 == 6


def test_q50_thickness_column_handles_empty() -> None:
    assert q50_thickness_column(np.zeros((10, 10), dtype=bool)) is None
    assert q50_thickness_column(np.zeros((0, 0), dtype=bool)) is None
    assert q50_thickness_column(None) is None


def test_q50_thickness_column_matches_vendor_quantile() -> None:
    # The finder's q50 must agree with rf_quality's thickness_quantiles.
    rng = np.random.default_rng(0)
    mask = np.zeros((40, 30), dtype=bool)
    for c in range(30):
        t = int(rng.integers(3, 20))
        start = int(rng.integers(0, 40 - t))
        mask[start:start + t, c] = True
    col, thickness_px, _t, _b = q50_thickness_column(mask)
    per_col = []
    for c in range(30):
        ys = np.flatnonzero(mask[:, c])
        if ys.size:
            per_col.append(float(ys.max() - ys.min() + 1))
    q50 = float(np.quantile(per_col, 0.5))
    # The chosen column's thickness is the closest available value to the q50.
    assert abs(thickness_px - q50) == min(abs(t - q50) for t in per_col)


def test_annotate_overlay_preserves_shape_and_survives_bad_masks() -> None:
    overlay = np.full((64, 96, 3), 100, dtype=np.uint8)
    # A plausible multi-class mask: an RF blob (1) over a VI blob (2).
    mc = np.zeros((64, 96), dtype=np.uint8)
    mc[10:30, 20:70] = 1
    mc[30:48, 20:70] = 2
    eroded = np.zeros((64, 96), dtype=bool)
    eroded[13:27, 23:67] = True
    out = annotate_overlay(overlay, mc, eroded)
    assert out.shape == overlay.shape and out.dtype == np.uint8

    # A degenerate (all-zero) mask must not blow up or blank the overlay.
    out2 = annotate_overlay(overlay, np.zeros((64, 96), dtype=np.uint8), None)
    assert out2.shape == overlay.shape


def test_annotate_overlay_show_flags_gate_each_measurement() -> None:
    """Per-view gating (Akadir 2026-06-26): the femur cine draws QMT only, the center-RF cine draws
    RF + q50 + fat only. Verify the show_* flags actually gate the marks."""
    overlay = np.full((64, 96, 3), 100, dtype=np.uint8)
    mc = np.zeros((64, 96), dtype=np.uint8)
    mc[10:30, 20:70] = 1   # RF blob → RF ticks / q50 / fat have something to draw
    mc[30:48, 20:70] = 2   # VI blob (no femur class 7 → the QMT caliper can't draw here)
    eroded = np.zeros((64, 96), dtype=bool)
    eroded[13:27, 23:67] = True

    # Every mark gated OFF → nothing drawn, overlay returned untouched.
    assert np.array_equal(
        annotate_overlay(overlay, mc, eroded, show_qmt=False, show_rf=False,
                         show_q50=False, show_fat=False),
        overlay), "all show_* False must draw nothing"

    # The center-RF view (RF + q50 + fat) DOES draw on this RF-bearing mask.
    rf_view = annotate_overlay(overlay, mc, eroded, show_qmt=False, show_rf=True,
                               show_q50=True, show_fat=True)
    assert not np.array_equal(rf_view, overlay), "center-RF view must draw RF/q50/fat marks"

    # The femur view (QMT only) on this femur-less mask draws nothing — proving the RF/q50/fat
    # marks were exactly what got suppressed (the QMT caliper needs a femur, absent here).
    femur_view = annotate_overlay(overlay, mc, eroded, show_qmt=True, show_rf=False,
                                  show_q50=False, show_fat=False)
    assert np.array_equal(femur_view, overlay), "femur view must suppress the RF/q50/fat marks"

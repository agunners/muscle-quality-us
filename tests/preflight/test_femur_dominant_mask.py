"""Bug 1 — the phantom-femur guard (`_dominant_femur_mask`).

Operator review (Akadir, 2026-05-22 Google Doc, point 1) reported that on
AGZ855 the QMT caliper anchored to a stray cluster of mis-labelled femur
pixels mid-frame, ignoring the real femur at the bottom. Subagent A
root-caused it: the QMT pipeline used every "femur" pixel with no
largest-component / minimum-area guard, and the plateau detector anchored
on the highest femur pixel — so a stray sitting above the real femur won.

Fix: `_dominant_femur_mask` keeps only the largest 8-connected component,
and treats the frame as femur-absent if that component is below
`QMT_FEMUR_MIN_AREA_PX`. `_femur_top_rows` consumes the cleaned mask, so
every downstream QMT consumer inherits the guard.
"""
from __future__ import annotations

import numpy as np

from labeling_app.measurements import (
    LABEL_FEMUR, QMT_FEMUR_MIN_AREA_PX, _dominant_femur_mask, _femur_top_rows,
)


def test_dominant_femur_mask_filters_stray_cluster() -> None:
    """A stray cluster of femur pixels mid-frame is filtered out — the real
    femur band survives, the stray does not. This is the AGZ855 case."""
    mask = np.zeros((300, 300), dtype=np.uint8)
    # Real femur band — large, near the bottom (~3600 px).
    mask[235:260, 5:150] = LABEL_FEMUR
    # Stray cluster — small (~25 px), mid-frame, sitting ABOVE the real femur
    # (the geometry that hijacks the plateau anchor).
    mask[150:155, 226:231] = LABEL_FEMUR
    cleaned = _dominant_femur_mask(mask)
    assert cleaned[235:260, 5:150].any(), "real femur band must survive"
    assert not cleaned[150:155, 226:231].any(), (
        "stray mid-frame cluster must be filtered out")


def test_dominant_femur_mask_returns_empty_when_below_min_area() -> None:
    """A femur class with only a tiny cluster (< QMT_FEMUR_MIN_AREA_PX) is
    treated as femur-absent — the frame is honestly 'no femur' downstream.
    The threshold is intentionally low (5 px) to keep production-realistic
    behaviour; the load-bearing fix is largest-component selection, which
    handles the AGZ855 stray-plus-real-femur case on its own."""
    mask = np.zeros((100, 100), dtype=np.uint8)
    # 3 px — well below the 5-px threshold; pure noise.
    mask[40, 40:43] = LABEL_FEMUR
    cleaned = _dominant_femur_mask(mask)
    assert not cleaned.any(), "below-min-area noise -> femur-absent"


def test_dominant_femur_mask_keeps_largest_when_two_real_blobs() -> None:
    """When two large femur components exist, the larger one wins; the
    smaller is filtered (or kept only if it exceeds the min-area threshold —
    which it does not, here)."""
    mask = np.zeros((200, 200), dtype=np.uint8)
    mask[100:130, 20:80] = LABEL_FEMUR     # 30 × 60 = 1800 px (large)
    mask[150:160, 100:110] = LABEL_FEMUR   # 10 × 10 = 100 px (smaller but > 50)
    cleaned = _dominant_femur_mask(mask)
    assert cleaned[100:130, 20:80].any(), "large blob kept"
    assert not cleaned[150:160, 100:110].any(), (
        "smaller blob filtered — only the dominant one survives")


def test_dominant_femur_mask_no_femur_pixels() -> None:
    """No femur at all -> empty mask, no error."""
    mask = np.zeros((50, 50), dtype=np.uint8)
    cleaned = _dominant_femur_mask(mask)
    assert not cleaned.any()
    assert cleaned.shape == (50, 50)


def test_femur_top_rows_uses_dominant_blob_only() -> None:
    """End-to-end: `_femur_top_rows` must report rows from the real femur band
    only — never from a stray cluster sitting above it. (This is the Bug 1
    surface — the plateau detector's `min_row` consumed `_femur_top_rows`,
    and the stray's top row was lower-valued than the real femur's, so the
    stray won. With the guard, the stray never reaches the boundary map.)"""
    mask = np.zeros((300, 300), dtype=np.uint8)
    mask[235:260, 5:150] = LABEL_FEMUR     # real femur, top at row 235
    mask[150:155, 226:231] = LABEL_FEMUR   # stray, top at row 150
    rows = _femur_top_rows(mask)
    assert rows, "real femur band must be picked up"
    assert min(rows.values()) >= 235, (
        f"_femur_top_rows leaked the stray (min row = {min(rows.values())}); "
        f"should be >= 235 (the real femur's top)")


def test_min_area_threshold_is_the_named_constant() -> None:
    """Regression guard — the min-area threshold must be the named registry
    constant, not a literal somewhere. A Build-Step 2b calibration will set
    it; a hardcoded literal would silently drift."""
    assert QMT_FEMUR_MIN_AREA_PX > 0
    assert QMT_FEMUR_MIN_AREA_PX < 1000, (
        "min-area must remain below a real femur's area (~thousands)")

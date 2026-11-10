"""Femur cortex-top outlier trim — removes a CONNECTED over-segmentation "hat"
that survives the largest-component filter and hijacks the QMT plateau anchor.

Real motivating case (verified live + cohort sweep, 2026-06-04): a tier-1
standard-QMT frame, ACL762 20260101_114816734 — an otherwise-smooth femur cortex
with a sharp CONNECTED spike (12 cols, ~143 px) poking above it; removing the
spike shifts the femur-anchored QMT by ~+6 px (~2%). The spike is one 8-connected
blob with the cortex, so the largest-component step cannot drop it — only the
cortex-line trim can. NB: CBN652 .898/.757 are SMOOTH DOMES (the femur's natural
rounded top), NOT spikes — the trim correctly leaves them untouched (the MAD-scaled
threshold discriminates). The synthetic cases below exercise the mechanism.
"""
import numpy as np

from labeling_app.measurements import (
    LABEL_FEMUR,
    QMT_FEMUR_SLOPE_MAX_ABS,
    _dominant_femur_mask,
    _trim_femur_top_outliers,
)


def _tops(mask_bool: np.ndarray) -> dict:
    cols = np.where(mask_bool.any(axis=0))[0]
    return {int(c): int(np.argmax(mask_bool[:, c])) for c in cols}


def test_connected_hat_trimmed_to_cortex():
    """A connected hat is removed so the per-column top falls back to the cortex."""
    mask = np.zeros((100, 200), dtype=np.int64)
    mask[40:51, 20:180] = LABEL_FEMUR            # flat cortex band, rows 40-50
    mask[25:40, 90:111] = LABEL_FEMUR            # the hat (rows 25-39), connected at row 40
    dom = _dominant_femur_mask(mask)
    tops = _tops(dom)
    # hat columns now anchor on the cortex (~40), not the hat (~25)
    for c in (90, 100, 110):
        assert tops[c] >= 38, f"col {c} top {tops[c]} not trimmed back to the cortex"
    # non-hat columns are untouched
    for c in (30, 60, 150, 170):
        assert tops[c] == 40, f"col {c} top {tops[c]} should be the cortex row 40"
    assert dom.any(), "trim must never empty a real femur"


def test_tilted_cortex_not_trimmed():
    """A legitimately tilted cortex (slope < QMT_FEMUR_SLOPE_MAX_ABS) is preserved."""
    slope = 0.1
    assert slope < QMT_FEMUR_SLOPE_MAX_ABS
    mask = np.zeros((120, 220), dtype=np.int64)
    expected = {}
    for c in range(20, 200):
        top = 40 + round((c - 20) * slope)
        mask[top:top + 11, c] = LABEL_FEMUR
        expected[c] = top
    dom = _dominant_femur_mask(mask)
    tops = _tops(dom)
    for c in (20, 80, 120, 199):
        assert tops[c] == expected[c], f"tilt col {c}: {tops[c]} != {expected[c]} (wrongly trimmed)"


def test_trim_is_noop_without_protrusion():
    """A clean flat cortex is returned unchanged."""
    before = np.zeros((100, 200), dtype=bool)
    before[40:51, 20:180] = True
    after = _trim_femur_top_outliers(before.copy())
    assert np.array_equal(before, after)


def test_trim_skips_when_too_few_columns():
    """Below the column floor the trim is skipped (no robust line)."""
    before = np.zeros((100, 200), dtype=bool)
    before[40:51, 50:54] = True                  # only 4 columns
    after = _trim_femur_top_outliers(before.copy())
    assert np.array_equal(before, after)

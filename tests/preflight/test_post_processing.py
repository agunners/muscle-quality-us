"""Post-processing: largest_component → erode → produce full + eroded pair."""
from __future__ import annotations

import numpy as np

from preflight_helpers.post_processing import post_process_rf
from preflight_helpers.segmenter import LABEL_RF


def _synthetic_mask_with_islands() -> np.ndarray:
    """Return a 256×128 mask with one big RF blob and two small islands."""
    m = np.zeros((256, 128), dtype=np.uint8)
    # Big blob.
    m[80:160, 30:90] = LABEL_RF
    # Two small islands (should be dropped by largest_component).
    m[10:14, 10:14] = LABEL_RF
    m[200:204, 100:104] = LABEL_RF
    return m


def test_largest_component_drops_islands() -> None:
    m = _synthetic_mask_with_islands()
    pair = post_process_rf(m, rf_class_id=LABEL_RF, erosion_px=3)
    # rf_full preserves the main blob only.
    assert pair.rf_full_mask.sum() == 80 * 60  # 80x60 = 4800
    # Erosion shrinks the blob by ~3px on each side.
    assert pair.rf_eroded_mask.sum() < pair.rf_full_mask.sum()
    # Retained fraction roughly (74×54)/(80×60) ≈ 0.83.
    assert 0.7 <= pair.area_retained_fraction <= 0.95
    assert not pair.is_empty


def test_empty_mask_returns_is_empty() -> None:
    m = np.zeros((64, 32), dtype=np.uint8)
    pair = post_process_rf(m, rf_class_id=LABEL_RF)
    assert pair.is_empty
    assert pair.rf_full_mask.sum() == 0
    assert "rf_empty_after_largest_component" in pair.drop_reasons


def test_edge_touch_flag() -> None:
    m = np.zeros((64, 64), dtype=np.uint8)
    m[0:20, 10:30] = LABEL_RF  # touches the top edge
    pair = post_process_rf(m, rf_class_id=LABEL_RF, erosion_px=3)
    assert pair.full_touches_edge is True

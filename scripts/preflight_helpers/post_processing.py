"""Post-processing of multi-class masks into (rf_full_mask, rf_eroded_mask) pairs.

Pipeline (revised per Akadir 2026-05-12 + canonical white-paper match):
  1. Extract RF binary as (mask == LABEL_RF).
  2. Largest connected-component filter (drops islands).
  3. *Optionally* fill internal holes — NOT applied by the canonical
     `rf_quality` pipeline; we keep it OFF by default so stage1 (annotator
     masks) and stage2 (predicted masks) feed cohort-matched values into
     downstream features. Caller can pass `fill_holes=True` to enable.
  4. Produce BOTH `rf_full_mask` (post step 3) AND `rf_eroded_mask`
     (cv2.MORPH_ELLIPSE 3-px erosion of rf_full_mask).
  5. Each feature module picks the variant it needs — full for areas,
     eroded for thickness + intensity. This module passes BOTH downstream.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
import scipy.ndimage as ndi

from .segmenter import LABEL_RF


@dataclass(frozen=True)
class RFMaskPair:
    rf_full_mask: np.ndarray       # bool HxW — largest component, optionally hole-filled
    rf_eroded_mask: np.ndarray     # bool HxW — 3-px ellipse-eroded full mask
    area_retained_fraction: Optional[float]
    full_touches_edge: bool
    is_empty: bool
    drop_reasons: tuple[str, ...]


def _largest_component(mask_u8: np.ndarray) -> np.ndarray:
    if mask_u8.sum() == 0:
        return mask_u8.astype(bool)
    num, labels = cv2.connectedComponents(mask_u8)
    if num <= 1:
        return mask_u8.astype(bool)
    sizes = np.zeros(num, dtype=np.int64)
    for cid in range(1, num):
        sizes[cid] = int((labels == cid).sum())
    best = int(np.argmax(sizes[1:]) + 1)
    return labels == best


def _erode_ellipse(mask_bool: np.ndarray, px: int) -> np.ndarray:
    if px <= 0 or mask_bool.sum() == 0:
        return mask_bool.astype(bool)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * px + 1, 2 * px + 1))
    out = cv2.erode(mask_bool.astype(np.uint8), kernel, iterations=1)
    return out.astype(bool)


def post_process_rf(
    multiclass_mask: np.ndarray,
    *,
    rf_class_id: int = LABEL_RF,
    erosion_px: int = 3,
    fill_holes: bool = False,
) -> RFMaskPair:
    """Produce (rf_full_mask, rf_eroded_mask) from a multi-class argmax mask.

    Both outputs are bool HxW.
    """
    drop: list[str] = []
    if multiclass_mask.size == 0:
        return RFMaskPair(
            rf_full_mask=np.zeros((0, 0), dtype=bool),
            rf_eroded_mask=np.zeros((0, 0), dtype=bool),
            area_retained_fraction=None,
            full_touches_edge=False,
            is_empty=True,
            drop_reasons=("empty_input",),
        )

    rf_bin = (multiclass_mask == rf_class_id).astype(np.uint8)
    rf_full = _largest_component(rf_bin)

    if fill_holes:
        rf_full = ndi.binary_fill_holes(rf_full).astype(bool)

    full_area = int(rf_full.sum())
    if full_area == 0:
        drop.append("rf_empty_after_largest_component")
        eroded = rf_full.copy()
        return RFMaskPair(
            rf_full_mask=rf_full,
            rf_eroded_mask=eroded,
            area_retained_fraction=None,
            full_touches_edge=False,
            is_empty=True,
            drop_reasons=tuple(drop),
        )

    rf_eroded = _erode_ellipse(rf_full, erosion_px)
    eroded_area = int(rf_eroded.sum())
    retained = float(eroded_area / full_area) if full_area else None

    # Edge-touch is informational — area features still compute on rf_full,
    # thickness + intensity features use rf_eroded which removes the edge band.
    h, w = rf_full.shape
    touches_edge = bool(
        rf_full[0, :].any() or rf_full[-1, :].any()
        or rf_full[:, 0].any() or rf_full[:, -1].any()
    )

    return RFMaskPair(
        rf_full_mask=rf_full,
        rf_eroded_mask=rf_eroded,
        area_retained_fraction=retained,
        full_touches_edge=touches_edge,
        is_empty=False,
        drop_reasons=(),
    )

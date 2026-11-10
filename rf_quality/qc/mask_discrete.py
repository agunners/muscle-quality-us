"""Tier-2A discrete-mask QC (works on GT and predicted masks identically).

All checks operate on the discrete class map; nothing here reads model
softmax. The Tier-2B logit-gated checks live in ``mask_logit.py`` and are
sourced from a separate, null-safe cache.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .._vendor.roi import (
    component_sizes,
    connected_component_count,
    erode_mask,
    largest_component,
)
from ..config import MaskCfg, QCCfg


@dataclass(frozen=True)
class MaskQC:
    rf_full_area_px: int
    rf_full_components: int
    top1_frac: float
    rf_fragmented_prediction: bool
    rf_analysis_area_px: int       # after 3-px erosion
    area_retained_fraction: float  # eroded/full
    mask_elongation: float         # bbox aspect (min/max)
    fascia_support_area_px: int
    rf_touches_top_row: bool
    rf_touches_bottom_row: bool
    fascia_continuity_frac: float  # 1 − (columns with no fascia / columns with RF)
    boundary_gradient_coherence: float | None

    # flags
    flag_small: bool
    flag_small_after_erosion: bool
    flag_fragmented: bool
    flag_low_retained: bool
    flag_elongation: bool
    flag_low_fascia_support: bool
    flag_touches_top: bool
    flag_touches_bottom: bool
    flag_fascia_discontinuity: bool

    @property
    def hard_fail(self) -> bool:
        return self.flag_small or self.flag_small_after_erosion

    @property
    def flag_any(self) -> bool:
        return any(
            (
                self.flag_small, self.flag_small_after_erosion, self.flag_fragmented,
                self.flag_low_retained, self.flag_elongation, self.flag_low_fascia_support,
                self.flag_touches_top, self.flag_touches_bottom, self.flag_fascia_discontinuity,
            )
        )


def _mask_bbox_elongation(mask: np.ndarray) -> float:
    ys, xs = np.where(mask)
    if ys.size == 0:
        return 0.0
    h = float(ys.max() - ys.min() + 1)
    w = float(xs.max() - xs.min() + 1)
    if h <= 0 or w <= 0:
        return 0.0
    return min(h, w) / max(h, w)


def _fascia_continuity(rf_mask: np.ndarray, fascia_mask: np.ndarray) -> float:
    """Fraction of RF columns whose column has a fascia pixel above or within ±2 rows of RF top.

    Geometry intuition: upper fascia should sit just above the RF top at
    most columns. Gaps reflect fascia mis-segmentation and therefore a
    shakier RF ROI boundary.
    """
    h, w = rf_mask.shape
    cols_with_rf = np.flatnonzero(rf_mask.any(axis=0))
    if cols_with_rf.size == 0:
        return 0.0
    hit = 0
    for c in cols_with_rf:
        rf_top = int(np.flatnonzero(rf_mask[:, c])[0])
        # look a few rows above for fascia support
        lo = max(0, rf_top - 6)
        hi = min(h, rf_top + 2)
        if fascia_mask[lo:hi, c].any():
            hit += 1
    return float(hit / cols_with_rf.size)


def _boundary_gradient_coherence(img_u8: np.ndarray, rf_mask: np.ndarray) -> float | None:
    """Structure-tensor coherence along a thin strip above the RF top row."""
    if rf_mask.sum() == 0:
        return None
    cols_with_rf = np.flatnonzero(rf_mask.any(axis=0))
    if cols_with_rf.size == 0:
        return None
    h, w = rf_mask.shape
    strip = np.zeros_like(rf_mask, dtype=bool)
    for c in cols_with_rf:
        rf_top = int(np.flatnonzero(rf_mask[:, c])[0])
        lo = max(0, rf_top - 4)
        hi = max(lo + 1, rf_top)
        strip[lo:hi, c] = True
    if strip.sum() < 64:
        return None

    Ix = cv2.Sobel(img_u8, cv2.CV_32F, 1, 0, ksize=3)
    Iy = cv2.Sobel(img_u8, cv2.CV_32F, 0, 1, ksize=3)
    Jxx = cv2.GaussianBlur(Ix * Ix, (5, 5), 1.0)
    Jyy = cv2.GaussianBlur(Iy * Iy, (5, 5), 1.0)
    Jxy = cv2.GaussianBlur(Ix * Iy, (5, 5), 1.0)

    Jxx_m = float(Jxx[strip].mean())
    Jyy_m = float(Jyy[strip].mean())
    Jxy_m = float(Jxy[strip].mean())
    tr = Jxx_m + Jyy_m
    disc = max((Jxx_m - Jyy_m) ** 2 + 4 * Jxy_m ** 2, 0.0) ** 0.5
    l1 = 0.5 * (tr + disc)
    l2 = 0.5 * (tr - disc)
    if l1 + l2 <= 1e-12:
        return 0.0
    return float((l1 - l2) / (l1 + l2))


def score_mask(pred_mask: np.ndarray, img_u8: np.ndarray, mask_cfg: MaskCfg, qc_cfg: QCCfg) -> MaskQC:
    rf_raw = pred_mask == mask_cfg.muscle_class_id
    fascia_mask = pred_mask == mask_cfg.fascia_class_id
    rf_full_components = connected_component_count(rf_raw)
    comps = component_sizes(rf_raw)
    total = sum(comps) if comps else 0
    top1 = (max(comps) / total) if comps and total > 0 else 0.0
    rf_fragmented = bool(rf_full_components > qc_cfg.max_components or (top1 > 0 and top1 < qc_cfg.top1_component_frac))

    # keep largest component only for downstream geometry
    rf = largest_component(rf_raw)
    rf_full_area = int(rf.sum())
    rf_eroded = erode_mask(rf, mask_cfg.erosion_px)
    rf_eroded_area = int(rf_eroded.sum())
    retained = float(rf_eroded_area / rf_full_area) if rf_full_area > 0 else 0.0

    elongation = _mask_bbox_elongation(rf)
    fascia_support = int(fascia_mask.sum())

    h, w = pred_mask.shape
    touches_top = bool(rf[0, :].any())
    touches_bot = bool(rf[-1, :].any())

    continuity = _fascia_continuity(rf, fascia_mask)
    boundary_coh = _boundary_gradient_coherence(img_u8, rf)

    return MaskQC(
        rf_full_area_px=rf_full_area,
        rf_full_components=rf_full_components,
        top1_frac=float(top1),
        rf_fragmented_prediction=rf_fragmented,
        rf_analysis_area_px=rf_eroded_area,
        area_retained_fraction=retained,
        mask_elongation=elongation,
        fascia_support_area_px=fascia_support,
        rf_touches_top_row=touches_top,
        rf_touches_bottom_row=touches_bot,
        fascia_continuity_frac=continuity,
        boundary_gradient_coherence=boundary_coh,
        flag_small=bool(rf_full_area < qc_cfg.min_rf_full_area_px),
        flag_small_after_erosion=bool(rf_eroded_area < qc_cfg.min_rf_analysis_area_px),
        flag_fragmented=rf_fragmented,
        flag_low_retained=bool(retained < qc_cfg.min_area_retained_fraction),
        flag_elongation=bool(elongation < qc_cfg.mask_elongation_lo or elongation > qc_cfg.mask_elongation_hi),
        flag_low_fascia_support=bool(fascia_support < qc_cfg.min_fascia_support_area_px),
        flag_touches_top=touches_top,
        flag_touches_bottom=touches_bot,
        flag_fascia_discontinuity=bool(continuity < (1.0 - qc_cfg.max_fascia_continuity_gap_frac)),
    )

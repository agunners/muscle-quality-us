"""Subcutaneous-fat (SAT) thickness measured from the FAT segmentation itself.

Akadir's instruction (repeated): take the SINGLE largest connected component of
the fat and FILL it (close its holes), then measure THAT region's own
top-to-bottom extent per column — and draw the caliper on the same region.

Measuring from the skin and fascia boundaries instead (an earlier mistake) makes
the line cling to a stray skin pixel *inside* the fat at the top and land *inside*
the fascia at the bottom. The single-component + hole-fill is exactly what removes
those strays: a mislabeled skin/fascia speckle inside the fat is an interior hole
that the fill closes, so the region's top and bottom become the fat's own clean
edges.

  * SAT region — `build_superficial_fat_mask`: LABEL_FAT restricted to ABOVE the
    muscle (deep intramuscular fat dropped), dominant connected component,
    hole-filled.
  * thickness — q50 (median) and max of the per-column extent (top..bottom) over
    the RF-bearing columns; mm via the mask-space rescale (`measurements.px_to_mm`).
  * ratio — fat q50 / (skin-to-femur soft-tissue depth), px-cancelling, per frame.
  * `fat_q50_column` — where to DRAW the caliper: the fat region's own top and
    bottom at the q50 column, so the overlay matches the measure exactly.

Not pure-numpy (scipy.ndimage for the connected-component + fill), so it lives
outside `measurements.py`.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np
from scipy import ndimage as ndi

from labeling_app.measurements import (
    LABEL_FAT, LABEL_RF, LABEL_SKIN, _dominant_femur_mask, px_to_mm,
)


def _dominant_filled(cls: np.ndarray) -> np.ndarray:
    """Largest 8-connected component of a boolean mask, hole-filled.

    The fill is load-bearing here: a stray non-fat pixel INSIDE the fat (a
    mislabeled skin or fascia speckle) is an interior hole that gets closed, so the
    region's per-column top/bottom are the fat's own clean boundaries, never the
    stray's."""
    lab, n = ndi.label(cls, structure=np.ones((3, 3), dtype=int))
    if n == 0:
        return cls
    sizes = [int((lab == i).sum()) for i in range(1, n + 1)]
    big = 1 + int(np.argmax(sizes))
    return ndi.binary_fill_holes(lab == big)


def build_superficial_fat_mask(mask: np.ndarray) -> np.ndarray:
    """The subcutaneous-fat slab as a SINGLE, hole-filled region (Akadir's
    instruction). LABEL_FAT restricted to ABOVE the muscle top per column (so deep
    intramuscular fat is excluded), then the dominant connected component,
    hole-filled. The measure and the drawn caliper both read THIS region's own top
    and bottom — so a stray skin pixel inside the fat or a fascia pixel below it can
    never become the boundary."""
    fat = mask == LABEL_FAT
    if not fat.any():
        return fat
    rf_dom = _dominant_filled(mask == LABEL_RF)
    h, w = mask.shape
    if rf_dom.any():
        # muscle top per column (h where the column has no dominant muscle); keep
        # only fat strictly above it.
        muscle_top = np.where(rf_dom.any(axis=0), rf_dom.argmax(axis=0), h)
        rows = np.arange(h)[:, None]
        fat = fat & (rows < muscle_top[None, :])
    if not fat.any():
        return fat
    return _dominant_filled(fat)


def _fat_extent_q50_max_px(fat_mask: np.ndarray, columns: np.ndarray) -> Tuple[Optional[float], Optional[float]]:
    """(q50, max) of the per-column top-to-bottom extent (`ys.max - ys.min + 1`) of
    the filled fat region, over `columns`. None when the region is empty there."""
    ext = []
    for c in columns:
        ys = np.flatnonzero(fat_mask[:, c])
        if ys.size:
            ext.append(int(ys.max() - ys.min() + 1))
    if not ext:
        return None, None
    return float(np.median(ext)), float(np.max(ext))


def _skin_to_femur_q50_px(mask: np.ndarray, columns: np.ndarray) -> Optional[float]:
    """q50 of (femur-top - skin-bottom) over `columns` where both exist — the
    soft-tissue depth between the two crisp boundaries (skin + dominant femur), the
    ratio's denominator."""
    skin = mask == LABEL_SKIN
    femur = _dominant_femur_mask(mask)
    spans = []
    for c in columns:
        ys_s = np.flatnonzero(skin[:, c])
        ys_f = np.flatnonzero(femur[:, c])
        if ys_s.size and ys_f.size:
            sb = int(ys_s.max())
            ft = int(ys_f.min())
            if ft > sb:
                spans.append(ft - sb)
    if not spans:
        return None
    return float(np.median(spans))


def fat_q50_column(mask: np.ndarray) -> Optional[Tuple[int, float, int, int]]:
    """The column whose fat extent is closest to the q50, with the fat region's own
    top and bottom y — so the caliper is drawn exactly where the measure was taken.
    Returns (col, thickness_px, fat_top_y, fat_bottom_y) or None."""
    fat_mask = build_superficial_fat_mask(mask)
    per_col: list = []
    for c in np.where((mask == LABEL_RF).any(axis=0))[0]:
        ys = np.flatnonzero(fat_mask[:, c])
        if ys.size:
            per_col.append((int(c), float(ys.max() - ys.min() + 1),
                            int(ys.min()), int(ys.max())))
    if not per_col:
        return None
    q50 = float(np.quantile([t[1] for t in per_col], 0.5))
    return min(per_col, key=lambda t: (abs(t[1] - q50), t[0]))


def compute_fat_geometry(mask: np.ndarray, spacing_row: Optional[Dict] = None) -> Dict[str, Optional[float]]:
    """Per-frame SAT geometry.

    Returns:
      fat_thickness_q50_mm  — median SAT slab thickness, top-to-bottom (lower better)
      fat_thickness_max_mm  — max slab thickness
      fat_to_depth_q50      — fat q50 / skin-to-femur soft-tissue depth (px cancels,
                              so it needs no spacing); medianed across frames upstream.
    All None when the inputs are missing (no RF columns / no fat / no spacing)."""
    out: Dict[str, Optional[float]] = {
        "fat_thickness_q50_mm": None,
        "fat_thickness_max_mm": None,
        "fat_to_depth_q50": None,
    }
    rf_cols = np.where((mask == LABEL_RF).any(axis=0))[0]
    if rf_cols.size == 0:
        return out
    fat_mask = build_superficial_fat_mask(mask)
    fat_q50_px, fat_max_px = _fat_extent_q50_max_px(fat_mask, rf_cols)

    spacing_row = spacing_row or {}
    sy = spacing_row.get("pixel_spacing_y")
    dr = spacing_row.get("dcm_rows")
    mh = spacing_row.get("mask_height")
    out["fat_thickness_q50_mm"] = px_to_mm(fat_q50_px, sy, dr, mh) if fat_q50_px is not None else None
    out["fat_thickness_max_mm"] = px_to_mm(fat_max_px, sy, dr, mh) if fat_max_px is not None else None

    depth = _skin_to_femur_q50_px(mask, rf_cols)
    if fat_q50_px is not None and depth is not None and depth > 0:
        out["fat_to_depth_q50"] = float(fat_q50_px / depth)
    return out

"""Vendored from ``muscle_quality/muscle_quality/roi.py`` (upstream r0).

Only these helpers are used by ``rf_quality/``:
  - ``erode_mask``             (cv2 MORPH_ELLIPSE, radius=erosion_px)
  - ``connected_component_count``
  - ``largest_component``
  - ``thickness_quantiles`` / ``max_thickness``
  - ``build_depth_bands``      (no-overlap 3-split)
  - ``build_superficial_fat_mask``

The SimpleITK BinaryErode sitkBall alternative is kept for the equivalence
test (see ``tests/test_erosion_kernel_equivalence.py``).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class ROIConfigLite:
    muscle_class_id: int
    fascia_class_id: int
    skin_class_id: int
    fat_class_id: int
    erosion_px: int


def _as_u8(mask: np.ndarray) -> np.ndarray:
    return mask.astype(np.uint8)


def connected_component_count(mask: np.ndarray) -> int:
    if mask.sum() == 0:
        return 0
    num, _ = cv2.connectedComponents(_as_u8(mask))
    return int(max(num - 1, 0))


def largest_component(mask: np.ndarray) -> np.ndarray:
    if mask.sum() == 0:
        return mask.astype(bool)
    num, labels = cv2.connectedComponents(_as_u8(mask))
    if num <= 1:
        return mask.astype(bool)
    best_id, best_size = 1, 0
    for comp_id in range(1, num):
        size = int((labels == comp_id).sum())
        if size > best_size:
            best_size = size
            best_id = comp_id
    return labels == best_id


def component_sizes(mask: np.ndarray) -> list[int]:
    if mask.sum() == 0:
        return []
    num, labels = cv2.connectedComponents(_as_u8(mask))
    return [int((labels == i).sum()) for i in range(1, num)]


def erode_mask(mask: np.ndarray, px: int) -> np.ndarray:
    if px <= 0 or mask.sum() == 0:
        return mask.astype(bool)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * px + 1, 2 * px + 1))
    out = cv2.erode(_as_u8(mask), kernel, iterations=1)
    return out.astype(bool)


def thickness_quantiles(mask: np.ndarray, quantiles: tuple[float, ...] = (0.1, 0.3, 0.5, 0.7, 0.9)) -> dict[str, float | None]:
    cols = np.flatnonzero(mask.any(axis=0))
    if cols.size == 0:
        return {f"thickness_q{int(q * 100)}_px": None for q in quantiles}
    thicknesses: list[float] = []
    for c in cols:
        ys = np.flatnonzero(mask[:, c])
        if ys.size == 0:
            continue
        thicknesses.append(float(ys.max() - ys.min() + 1))
    if not thicknesses:
        return {f"thickness_q{int(q * 100)}_px": None for q in quantiles}
    arr = np.asarray(thicknesses, dtype=np.float32)
    out: dict[str, float] = {}
    for q in quantiles:
        out[f"thickness_q{int(q * 100)}_px"] = float(np.quantile(arr, q))
    return out


def max_thickness(mask: np.ndarray) -> float | None:
    cols = np.flatnonzero(mask.any(axis=0))
    if cols.size == 0:
        return None
    best = 0.0
    for c in cols:
        ys = np.flatnonzero(mask[:, c])
        if ys.size == 0:
            continue
        best = max(best, float(ys.max() - ys.min() + 1))
    return best


def build_depth_bands(mask: np.ndarray) -> dict[str, np.ndarray]:
    """Non-overlapping superficial / mid / deep band masks (column-wise 3-split)."""
    sup = np.zeros_like(mask, dtype=bool)
    mid = np.zeros_like(mask, dtype=bool)
    deep = np.zeros_like(mask, dtype=bool)
    h, w = mask.shape
    for c in range(w):
        ys = np.flatnonzero(mask[:, c])
        if ys.size == 0:
            continue
        y0, y1 = int(ys.min()), int(ys.max())
        t = max(y1 - y0 + 1, 1)
        a = int(round(y0 + (1 / 3) * t))
        b = int(round(y0 + (2 / 3) * t))
        a = max(y0, min(y1, a))
        b = max(a, min(y1, b))
        sup[y0 : a, c] = mask[y0 : a, c]
        mid[a : b, c] = mask[a : b, c]
        deep[b : y1 + 1, c] = mask[b : y1 + 1, c]
    return {"superficial": sup, "mid": mid, "deep": deep}


def build_superficial_fat_mask(pred_mask: np.ndarray, cfg: ROIConfigLite, muscle_mask: np.ndarray) -> np.ndarray:
    """Fat pixels between skin-bottom and (fascia-top ∪ muscle-top) per column.

    Keep only connected components that touch the skin-bottom boundary.
    """
    fat = pred_mask == cfg.fat_class_id
    skin = pred_mask == cfg.skin_class_id
    fascia = pred_mask == cfg.fascia_class_id

    h, w = pred_mask.shape
    skin_bottom = np.full(w, -1, dtype=int)
    fascia_top = np.full(w, -1, dtype=int)
    muscle_top = np.full(w, -1, dtype=int)

    for c in range(w):
        ys_skin = np.flatnonzero(skin[:, c])
        ys_fascia = np.flatnonzero(fascia[:, c])
        ys_muscle = np.flatnonzero(muscle_mask[:, c])
        if ys_skin.size:
            skin_bottom[c] = int(ys_skin.max())
        if ys_fascia.size:
            fascia_top[c] = int(ys_fascia.min())
        if ys_muscle.size:
            muscle_top[c] = int(ys_muscle.min())

    candidate = np.zeros_like(fat, dtype=bool)
    for c in range(w):
        sb = skin_bottom[c]
        if sb < 0:
            continue
        lower_candidates = [y for y in (fascia_top[c], muscle_top[c]) if y >= 0]
        if not lower_candidates:
            continue
        y_low = min(lower_candidates)
        if y_low <= sb + 1:
            continue
        candidate[sb + 1 : y_low, c] = fat[sb + 1 : y_low, c]

    if candidate.sum() == 0:
        return candidate

    num, labels = cv2.connectedComponents(_as_u8(candidate))
    keep = np.zeros_like(candidate, dtype=bool)
    for comp in range(1, num):
        ys, xs = np.where(labels == comp)
        if ys.size == 0:
            continue
        touches_skin = False
        for y, x in zip(ys, xs, strict=False):
            sb = skin_bottom[x]
            if sb >= 0 and y <= sb + 3:
                touches_skin = True
                break
        if touches_skin:
            keep[labels == comp] = True
    return keep

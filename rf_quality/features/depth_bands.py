"""Depth-stratified features: superficial / middle / deep band means,
superficial-to-deep ratio, OLS depth gradient.
"""

from __future__ import annotations

import numpy as np

from .._vendor.roi import build_depth_bands


def _masked_mean(img: np.ndarray, mask: np.ndarray) -> float | None:
    vals = img[mask]
    return float(vals.mean()) if vals.size else None


def _depth_ols_slope(img_u8: np.ndarray, mask: np.ndarray) -> float | None:
    """OLS slope of intensity vs normalised depth (0 superficial, 1 deep)
    pooled across all muscle columns.
    """
    h, w = mask.shape
    xs, ys, vs = [], [], []
    for c in range(w):
        col = np.flatnonzero(mask[:, c])
        if col.size < 2:
            continue
        y0, y1 = int(col.min()), int(col.max())
        if y1 == y0:
            continue
        norm = (col - y0) / (y1 - y0)
        xs.append(norm)
        vs.append(img_u8[col, c].astype(np.float64))
    if not xs:
        return None
    x = np.concatenate(xs)
    y = np.concatenate(vs)
    if x.size < 8:
        return None
    xm = x.mean(); ym = y.mean()
    num = float(((x - xm) * (y - ym)).sum())
    den = float(((x - xm) ** 2).sum())
    if abs(den) < 1e-12:
        return None
    return float(num / den)


def compute_depth_bands(img_u8: np.ndarray, eroded_mask: np.ndarray) -> dict[str, float | None]:
    bands = build_depth_bands(eroded_mask)
    sup_mean = _masked_mean(img_u8, bands["superficial"])
    mid_mean = _masked_mean(img_u8, bands["mid"])
    deep_mean = _masked_mean(img_u8, bands["deep"])
    out: dict[str, float | None] = {
        "superficial_int_mean": sup_mean,
        "mid_int_mean": mid_mean,
        "deep_int_mean": deep_mean,
        "int_superficial_to_deep_ratio": None,
        "int_depth_gradient": _depth_ols_slope(img_u8, eroded_mask),
    }
    if sup_mean is not None and deep_mean is not None and abs(deep_mean) > 1e-6:
        out["int_superficial_to_deep_ratio"] = float(sup_mean / deep_mean)
    return out

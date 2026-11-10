"""Intensity features over RF-eroded ROI: mean/std/median/IQR/MAD/p10/p90/entropy
plus shape (skew, kurt_excess, bimodality coefficient, hist_uniformity).
"""

from __future__ import annotations

import numpy as np


def _shannon_entropy(vals_u8: np.ndarray) -> float | None:
    if vals_u8.size == 0:
        return None
    hist = np.bincount(vals_u8.reshape(-1), minlength=256).astype(np.float64)
    probs = hist / max(hist.sum(), 1.0)
    nz = probs > 0
    return float(-(probs[nz] * np.log2(probs[nz])).sum())


def _hist_uniformity(vals_u8: np.ndarray) -> float | None:
    if vals_u8.size == 0:
        return None
    hist = np.bincount(vals_u8.reshape(-1), minlength=256).astype(np.float64)
    probs = hist / max(hist.sum(), 1.0)
    return float(np.sum(probs ** 2))


def _skew_kurt(vals_f: np.ndarray) -> tuple[float | None, float | None]:
    if vals_f.size < 3:
        return None, None
    mean = float(vals_f.mean())
    std = float(vals_f.std())
    if std < 1e-6:
        return None, None
    z = (vals_f - mean) / std
    skew = float(np.mean(z ** 3))
    kurt_excess = float(np.mean(z ** 4) - 3.0)
    return skew, kurt_excess


def compute_intensity(img_u8: np.ndarray, mask: np.ndarray) -> dict[str, float | None]:
    vals = img_u8[mask]
    out: dict[str, float | None] = {
        "int_mean": None, "int_std": None, "int_median": None,
        "int_p10": None, "int_p90": None, "int_iqr": None,
        "int_mad": None, "int_entropy": None,
        "int_skew": None, "int_kurtosis_excess": None,
        "int_bimodality_coefficient": None,
        "hist_uniformity": None,
        "int_cv_within": None,
    }
    if vals.size == 0:
        return out
    vals_u8 = vals.astype(np.uint8)
    vals_f = vals.astype(np.float64)
    mean = float(vals_f.mean())
    std = float(vals_f.std())
    median = float(np.median(vals_f))
    q25 = float(np.quantile(vals_f, 0.25))
    q75 = float(np.quantile(vals_f, 0.75))
    q10 = float(np.quantile(vals_f, 0.10))
    q90 = float(np.quantile(vals_f, 0.90))
    mad = float(np.median(np.abs(vals_f - median)))

    skew, kurt = _skew_kurt(vals_f)
    # bimodality coefficient with ε-guard on the denominator
    bc = None
    if skew is not None and kurt is not None:
        denom = kurt + 3.0
        if abs(denom) > 1e-6:
            bc = float((skew ** 2 + 1.0) / denom)
            bc = max(0.0, min(1.0, bc))

    out["int_mean"] = mean
    out["int_std"] = std
    out["int_median"] = median
    out["int_p10"] = q10
    out["int_p90"] = q90
    out["int_iqr"] = q75 - q25
    out["int_mad"] = mad
    out["int_entropy"] = _shannon_entropy(vals_u8)
    out["int_skew"] = skew
    out["int_kurtosis_excess"] = kurt
    out["int_bimodality_coefficient"] = bc
    out["hist_uniformity"] = _hist_uniformity(vals_u8)
    if abs(mean) > 1e-6:
        out["int_cv_within"] = float(std / abs(mean))
    return out

"""Vendored QC primitives from ``muscle_quality/muscle_quality/metrics.py``.

Trimmed to the helpers Tier-1 image QC needs: Shannon entropy, Laplacian
focus, and saturation ratio. Intentionally small so the upstream package
does not have to be installed.
"""

from __future__ import annotations

import cv2
import numpy as np


def shannon_entropy_u8(img: np.ndarray) -> float:
    hist = np.bincount(img.reshape(-1), minlength=256).astype(np.float64)
    probs = hist / max(hist.sum(), 1.0)
    nz = probs > 0
    return float(-(probs[nz] * np.log2(probs[nz])).sum())


def laplacian_focus(img: np.ndarray) -> float:
    lap = cv2.Laplacian(img, cv2.CV_64F)
    return float(lap.var())


def saturation_ratio(img: np.ndarray, low: int = 2, high: int = 253) -> float:
    sat = ((img <= low) | (img >= high)).mean()
    return float(sat)

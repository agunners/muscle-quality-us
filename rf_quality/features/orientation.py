"""Orientation features — structure-tensor (Folder A) + Sobel histogram (Folder B).

``orient_coherence``, ``orient_grad_mean``, ``orient_grad_std`` use the
Gaussian-smoothed Sobel structure tensor — more principled than histogram
binning. The 8-bin orientation histogram from Folder B is retained as a
complementary direction descriptor (``grad_orient_*``).
"""

from __future__ import annotations

import cv2
import numpy as np


def _structure_tensor_stats(img_u8: np.ndarray, mask: np.ndarray) -> dict[str, float | None]:
    if mask.sum() < 32:
        return {"orient_coherence": None, "orient_grad_mean": None, "orient_grad_std": None}
    Ix = cv2.Sobel(img_u8, cv2.CV_32F, 1, 0, ksize=3)
    Iy = cv2.Sobel(img_u8, cv2.CV_32F, 0, 1, ksize=3)
    Jxx = cv2.GaussianBlur(Ix * Ix, (5, 5), 1.0)
    Jyy = cv2.GaussianBlur(Iy * Iy, (5, 5), 1.0)
    Jxy = cv2.GaussianBlur(Ix * Iy, (5, 5), 1.0)
    Jxx_m = float(Jxx[mask].mean())
    Jyy_m = float(Jyy[mask].mean())
    Jxy_m = float(Jxy[mask].mean())
    tr = Jxx_m + Jyy_m
    disc = max((Jxx_m - Jyy_m) ** 2 + 4 * Jxy_m ** 2, 0.0) ** 0.5
    l1 = 0.5 * (tr + disc)
    l2 = 0.5 * (tr - disc)
    coh = float((l1 - l2) / (l1 + l2)) if (l1 + l2) > 1e-12 else 0.0

    grad_mag = np.sqrt(Ix.astype(np.float64) ** 2 + Iy.astype(np.float64) ** 2)
    grads = grad_mag[mask]
    return {
        "orient_coherence": max(0.0, min(1.0, coh)),
        "orient_grad_mean": float(grads.mean()) if grads.size else None,
        "orient_grad_std": float(grads.std()) if grads.size else None,
    }


def _sobel_orientation_hist(img_u8: np.ndarray, mask: np.ndarray, nbins: int = 8) -> dict[str, float | None]:
    if mask.sum() < 32:
        return {
            "grad_orient_dominant_angle_deg": None,
            "grad_orient_entropy": None,
            "grad_orient_concentration": None,
        }
    Ix = cv2.Sobel(img_u8, cv2.CV_32F, 1, 0, ksize=3)
    Iy = cv2.Sobel(img_u8, cv2.CV_32F, 0, 1, ksize=3)
    ang = (np.arctan2(Iy, Ix) * 180.0 / np.pi) % 180.0  # undirected 0..180
    mag = np.sqrt(Ix ** 2 + Iy ** 2)
    ang_m = ang[mask]
    mag_m = mag[mask]
    if ang_m.size == 0:
        return {
            "grad_orient_dominant_angle_deg": None,
            "grad_orient_entropy": None,
            "grad_orient_concentration": None,
        }
    hist, edges = np.histogram(ang_m, bins=nbins, range=(0.0, 180.0), weights=mag_m)
    probs = hist / max(hist.sum(), 1e-12)
    dom_bin = int(np.argmax(probs))
    dom_angle = 0.5 * (edges[dom_bin] + edges[dom_bin + 1])
    nz = probs > 0
    entropy = float(-(probs[nz] * np.log2(probs[nz])).sum()) if nz.any() else 0.0
    concentration = float(probs.max())
    return {
        "grad_orient_dominant_angle_deg": float(dom_angle),
        "grad_orient_entropy": entropy,
        "grad_orient_concentration": concentration,
    }


def compute_orientation(img_u8: np.ndarray, eroded_mask: np.ndarray) -> dict[str, float | None]:
    out = _structure_tensor_stats(img_u8, eroded_mask)
    out.update(_sobel_orientation_hist(img_u8, eroded_mask))
    return out

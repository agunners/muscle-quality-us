"""Fat-proxy features.

Fat mask: superficial fat band (between skin-bottom and fascia/muscle top,
touching skin; see ``_vendor/roi.build_superficial_fat_mask``).

Emits:
  - fat_to_area_ratio  : geometry, ratio of fat area to RF area.
  - fat_int_mean       : raw fat-mask mean intensity (gain-confounded; support only).
  - ei_to_fat_ratio    : within-frame muscle/fat brightness contrast (gain-normalised).
  - fat_thickness_mm   : per-column fat extent × pixel_spacing_y, averaged across columns
                         (Young 2015 Eq. 5/6 input; the prerequisite for an additive
                         attenuation correction).

Groundings:
  - ei_to_fat_ratio: NOT Young 2015 r=0.91 — that was for additive fat-corrected EI vs
    MRI %fat, not muscle/fat ratio. This is a gain-normalised contrast feature.
  - fat_thickness_mm: enables Young 2015 (Muscle Nerve 52(6)) additive correction
    EI_corrected = EI_raw + fat_thickness_cm × cf, with cf = 40.5278 EI per 1.0 cm
    on their LOGIQ e device (literature) and a cohort-refit α as the device-specific
    operational alternative. See rf_quality.features.intensity.compute_intensity for
    the corrected-sibling emission.
"""

from __future__ import annotations

import numpy as np


def compute_fat_thickness_mm(
    fat_mask: np.ndarray,
    pixel_spacing_y_mm_per_px: float | None,
) -> float | None:
    """Mean per-column fat extent in mm.

    For each column with any fat-mask pixel, take y_max − y_min + 1 (i.e. the vertical
    extent in pixels), multiply by ``pixel_spacing_y_mm_per_px``, and average across
    columns that contain any fat. Mirrors the per-column extent pattern used by
    ``rf_quality._vendor.roi.thickness_quantiles`` on the muscle mask.

    Returns None when no fat pixels are present or spacing is missing.
    """
    if pixel_spacing_y_mm_per_px is None or pixel_spacing_y_mm_per_px <= 0:
        return None
    if fat_mask.sum() == 0:
        return None
    h, w = fat_mask.shape
    extents_px: list[float] = []
    for c in range(w):
        ys = np.flatnonzero(fat_mask[:, c])
        if ys.size > 0:
            extents_px.append(float(ys.max() - ys.min() + 1))
    if not extents_px:
        return None
    return float(np.mean(extents_px) * pixel_spacing_y_mm_per_px)


def compute_fat(
    img_u8: np.ndarray,
    rf_full_mask: np.ndarray,
    fat_mask: np.ndarray,
    rf_int_mean: float | None,
    pixel_spacing_y_mm_per_px: float | None = None,
) -> dict[str, float | None]:
    rf_area = int(rf_full_mask.sum())
    fat_area = int(fat_mask.sum())
    out: dict[str, float | None] = {
        "fat_to_area_ratio": None,
        "fat_int_mean": None,
        "ei_to_fat_ratio": None,
        "fat_thickness_mm": None,
    }
    if rf_area > 0:
        out["fat_to_area_ratio"] = float(fat_area / rf_area)
    if fat_area > 0:
        out["fat_int_mean"] = float(img_u8[fat_mask].mean())
    if rf_int_mean is not None and out["fat_int_mean"] is not None and out["fat_int_mean"] > 1e-6:
        out["ei_to_fat_ratio"] = float(rf_int_mean / out["fat_int_mean"])
    out["fat_thickness_mm"] = compute_fat_thickness_mm(fat_mask, pixel_spacing_y_mm_per_px)
    return out

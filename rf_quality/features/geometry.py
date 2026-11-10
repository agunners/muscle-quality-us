"""Geometry features — area, thickness quantiles, max thickness, retained fraction.

Aggregation policy: ``anchor_primary`` (§4.2 of plan). Flanks (lateral vs
medial) are different anatomy, so aggregating thickness across a sweep is
nonsense; the representative-frame (center) value is the primary number, and
we only emit ``agg_median`` / ``agg_range`` as support columns.
"""

from __future__ import annotations

import numpy as np

from .._vendor.roi import erode_mask, max_thickness, thickness_quantiles


def compute_geometry(
    rf_full_mask: np.ndarray,
    rf_eroded_mask: np.ndarray,
    spacing_y_mm_per_px: float | None,
    spacing_x_mm_per_px: float | None,
) -> dict[str, float | None]:
    full_area_px = int(rf_full_mask.sum())
    eroded_area_px = int(rf_eroded_mask.sum())
    retained = float(eroded_area_px / full_area_px) if full_area_px > 0 else None

    quants = thickness_quantiles(rf_eroded_mask, quantiles=(0.1, 0.3, 0.5, 0.7, 0.9))
    max_t_px = max_thickness(rf_eroded_mask)

    out: dict[str, float | None] = {
        "full_area_px": float(full_area_px),
        "full_area_mm2": None,
        "full_max_thickness_mm": None,
        "full_thickness_q50_mm": None,
        "area_retained_fraction_after_erosion": retained,
    }
    if spacing_y_mm_per_px is not None and spacing_x_mm_per_px is not None:
        out["full_area_mm2"] = float(full_area_px * spacing_x_mm_per_px * spacing_y_mm_per_px)
    if max_t_px is not None and spacing_y_mm_per_px is not None:
        out["full_max_thickness_mm"] = float(max_t_px * spacing_y_mm_per_px)
    q50 = quants.get("thickness_q50_px")
    if q50 is not None and spacing_y_mm_per_px is not None:
        out["full_thickness_q50_mm"] = float(q50 * spacing_y_mm_per_px)
    return out

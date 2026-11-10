"""Fascia decomposition — split the existing fascia mask into superficial
(above the muscle centroid) and deep (below) layers, per the user's
2026-04-29 hypothesis (round-2 plan A2.4).

The current registry's ``fascia_thickness_mm`` lumps the superficial
epimysium (between SAT and the rectus femoris muscle) and the deep fascia
(at the muscle/vastus-intermedius interface) into a single per-column
pixel-count thickness. The two layers carry different mechanical roles and
may thicken differentially under different conditions, so a decomposition
into superficial and deep is a candidate worth testing.

Implementation: split the binary fascia mask by Y-coordinate above vs
below the muscle ROI's centroid in the same frame. This is the cheap
"path (a)" from the FEATURE_VALUE_TABLE — assumes the fascia pixels above
the muscle centroid are predominantly superficial epimysium and those
below are predominantly deep fascia. In the bbox of the muscle, this
holds well; near the bbox edges it can mis-attribute. We accept this as
an integration-quality first cut; the discovery_sandbox verdict in the
value table calls out the structural assumption explicitly.

Sign priors stay at 0 per the round-2 γ-pass: the interpretive sign
discussion lives in feature cards, not in Stage F vetoes.
"""
from __future__ import annotations

import numpy as np


def _mean_per_column_thickness_mm(
    layer_mask: np.ndarray,
    pixel_spacing_y_mm_per_px: float | None,
) -> float | None:
    """Same per-column-pixel-count rule as `baseline_geometry` uses.

    Returns None when spacing is missing or layer mask is empty.
    """
    if pixel_spacing_y_mm_per_px is None or pixel_spacing_y_mm_per_px <= 0:
        return None
    if layer_mask.sum() == 0:
        return None
    col_counts = layer_mask.sum(axis=0)
    nz = col_counts[col_counts > 0]
    if nz.size == 0:
        return None
    return float(nz.mean() * pixel_spacing_y_mm_per_px)


def compute_fascia_split(
    pred_mask: np.ndarray,
    fascia_class_id: int,
    muscle_class_id: int,
    pixel_spacing_y_mm_per_px: float | None,
) -> dict[str, float | None]:
    """Split the fascia mask by muscle-centroid Y-coordinate.

    Returns:
        ``fascia_superficial_thickness_mm`` — mean per-column pixel-count
            thickness of fascia pixels whose Y < muscle-centroid-Y.
        ``fascia_deep_thickness_mm`` — same for fascia pixels whose Y ≥
            muscle-centroid-Y.

    Both values are ``None`` if the muscle mask is empty (no centroid) or
    spacing is missing.
    """
    out = {
        "fascia_superficial_thickness_mm": None,
        "fascia_deep_thickness_mm": None,
    }
    if pixel_spacing_y_mm_per_px is None or pixel_spacing_y_mm_per_px <= 0:
        return out

    fascia_mask = pred_mask == fascia_class_id
    muscle_mask = pred_mask == muscle_class_id
    if not fascia_mask.any() or not muscle_mask.any():
        return out

    # Muscle centroid Y (true row-weighted center of mass of the binary
    # muscle mask). Earlier implementation used `rows.mean()` over UNIQUE
    # row indices that contain any muscle pixel — that's the mean of the
    # *occupied row range*, not the center of mass, and biases the split
    # point on asymmetric muscle distributions (rows with 1 pixel weighted
    # the same as rows with 100 pixels). On the project's typical RF mid-
    # belly cross-section with tendon attachments at the deep margin, the
    # bias was ~5 rows deep — meaningful. Fix 2026-05-02 (red-team review):
    # use `np.where(muscle_mask)[0].mean()` for the row-weighted-by-pixel-
    # count COM. Verified by hand on a 110-px asymmetric synthetic mask
    # (true COM=20.636, buggy=25.857, fixed=20.636 exactly).
    pixel_rows = np.where(muscle_mask)[0]
    if pixel_rows.size == 0:
        return out
    muscle_centroid_y = float(pixel_rows.mean())

    h = fascia_mask.shape[0]
    superficial_mask = np.zeros_like(fascia_mask)
    deep_mask = np.zeros_like(fascia_mask)
    cy = int(round(muscle_centroid_y))
    cy = max(0, min(h - 1, cy))
    if cy > 0:
        superficial_mask[:cy] = fascia_mask[:cy]
    if cy < h:
        deep_mask[cy:] = fascia_mask[cy:]

    out["fascia_superficial_thickness_mm"] = _mean_per_column_thickness_mm(
        superficial_mask, pixel_spacing_y_mm_per_px
    )
    out["fascia_deep_thickness_mm"] = _mean_per_column_thickness_mm(
        deep_mask, pixel_spacing_y_mm_per_px
    )
    return out

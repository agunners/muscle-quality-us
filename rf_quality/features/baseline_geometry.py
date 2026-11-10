"""Baseline soft-tissue layer geometry — skin and fascia thickness.

The segmenter produces a multi-label mask with at least: muscle (1), femur (4),
fascia (5), skin (6), bone (7), fat (8). The pipeline already extracts muscle
geometry (full_area_mm2, full_thickness_q50_mm, etc.) and fat thickness via
``rf_quality.features.fat.compute_fat_thickness_mm``. This file extends the
same per-column-extent pattern to the **skin** and **fascia** masks.

These are baseline morphometric features that any clinical reader will expect
in a stakeholder brief. Adding them is a small, low-risk Act-2 lifecycle op
under RUNNER.md — the masks are already produced; the extraction is a clone
of the fat-thickness routine.

Sign priors are deliberately ``0`` (no_prior) until the focused literature pass
(see ``prompts/04_literature_grounding_prompt.md``) verifies direction against
in-corpus papers. The candidate references for sign-prior support are
LIT-EI-2021 (subcutaneous fat confound) and LIT-EI-2022B (subcutaneous adipose
+ skeletal muscle thickness on RF echo intensity), both in
``MSK_research/papers_2026-04-15/`` — not yet opened paper-by-paper.
"""

from __future__ import annotations

import numpy as np


def _mean_per_column_thickness_mm(
    layer_mask: np.ndarray,
    pixel_spacing_y_mm_per_px: float | None,
) -> float | None:
    """Mean per-column **pixel count** of a binary layer mask, in mm.

    For each column with any layer pixel, count the number of layer pixels in
    that column, multiply by ``pixel_spacing_y_mm_per_px``, and average across
    columns. This counts total layer material per column, regardless of whether
    the pixels are contiguous.

    This deliberately differs from the y-extent rule used by
    ``rf_quality.features.fat.compute_fat_thickness_mm``: the fat mask is built
    by ``build_superficial_fat_mask`` to be a single contiguous superficial band
    per column, so y-extent and pixel-count are equivalent there. For skin and
    fascia, the multi-label mask can place pixels of the same class at
    disjoint y-positions in one column (e.g., fascia appears at both the
    superficial and deep muscle borders, or with sparse misseg in between).
    Using y-extent in that case spans across the muscle and produces values
    of order 15–25 mm — physically wrong, since fascia is sub-mm to a few mm.
    Using per-column pixel count gives total layer thickness honestly.

    Returns ``None`` when the layer mask is empty or spacing is missing.
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


# Backwards-compat alias for the (now superseded) y-extent function.
# Kept private because no caller should reach for it; superseded by the
# pixel-count rule above.
_mean_per_column_extent_mm = _mean_per_column_thickness_mm


def compute_skin_thickness_mm(
    pred_mask: np.ndarray,
    skin_class_id: int,
    pixel_spacing_y_mm_per_px: float | None,
) -> float | None:
    """Mean per-column skin-layer extent in mm.

    Derives the binary skin mask from the multi-label segmentation
    (``pred_mask == skin_class_id``) and applies the per-column extent rule.
    """
    skin_mask = pred_mask == skin_class_id
    return _mean_per_column_thickness_mm(skin_mask, pixel_spacing_y_mm_per_px)


def compute_fascia_thickness_mm(
    pred_mask: np.ndarray,
    fascia_class_id: int,
    pixel_spacing_y_mm_per_px: float | None,
) -> float | None:
    """Mean per-column fascia-layer extent in mm.

    Derives the binary fascia mask from the multi-label segmentation
    (``pred_mask == fascia_class_id``) and applies the per-column extent rule.
    """
    fascia_mask = pred_mask == fascia_class_id
    return _mean_per_column_thickness_mm(fascia_mask, pixel_spacing_y_mm_per_px)


def compute_baseline_geometry(
    pred_mask: np.ndarray,
    skin_class_id: int,
    fascia_class_id: int,
    pixel_spacing_y_mm_per_px: float | None,
) -> dict[str, float | None]:
    """Bundle skin + fascia thickness extraction for a single frame.

    Returns a dict with keys ``skin_thickness_mm`` and ``fascia_thickness_mm``,
    each ``None`` if the corresponding layer is absent or spacing is missing.
    """
    return {
        "skin_thickness_mm": compute_skin_thickness_mm(
            pred_mask, skin_class_id, pixel_spacing_y_mm_per_px
        ),
        "fascia_thickness_mm": compute_fascia_thickness_mm(
            pred_mask, fascia_class_id, pixel_spacing_y_mm_per_px
        ),
    }

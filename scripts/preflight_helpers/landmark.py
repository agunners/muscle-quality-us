"""A study's RF LANDMARK — the median over its top-K largest-CLEAN RF sweep
frames — for the size features (visible RF area + RF thicknesses).

The landmark is the *selection-independent* value the cohort comparison uses: it
answers "where does this STUDY's fullest cross-section sit among peers' fullest",
regardless of which frames the operator happened to pick. It is shared by the
offline cohort precompute (every cohort study) and the live assessment (this
study, in the app) through ONE measurement primitive, so a cohort number and a
live number measured on the same mask + spacing are identical — there is no path
for the two to drift apart.
"""
from __future__ import annotations

from statistics import median
from typing import Optional

import numpy as np

from labeling_app import repr_scorer
from labeling_app.measurements import LABEL_RF
from preflight_helpers.feature_compute import compute_frame_features
from preflight_helpers.post_processing import post_process_rf

# The size features that ride the landmark (geometry, area-primary). Fat / echo /
# QMT do NOT — fat is flat along the sweep, QMT rides its provenance gate.
LANDMARK_FEATURES = ("visible_rf_area_mm2", "rf_thickness_mm", "full_thickness_q50_mm")


def measure_frame_geometry(mask: np.ndarray, final_u8: Optional[np.ndarray],
                           spacing_row: dict) -> dict:
    """The per-frame geometry features for ONE mask, through the app's own path
    (post_process_rf -> compute_frame_features). The cohort precompute and the
    live landmark both call this, so a cohort number and a live number on the
    same mask + spacing are bit-identical. `final_u8` only feeds the (unused-here)
    intensity features, so None is fine for a geometry-only landmark."""
    rf_pair = post_process_rf(mask, rf_class_id=LABEL_RF, erosion_px=3, fill_holes=False)
    return compute_frame_features(
        frame_base="_landmark", is_anchor=False, multiclass_mask=mask,
        all_frame_masks=[mask], final_image_u8=final_u8, spacing_row=spacing_row,
        rf_mask_pair=rf_pair,
    ).to_dict()


def compute_study_landmark(masks: dict, sweep_grades: Optional[dict],
                           finals: Optional[dict], spacing_row: dict,
                           k: int = 5, top_frames: Optional[list] = None) -> dict:
    """The study's landmark for the size features.

    Returns `{"values": {feature: landmark_value}, "frames": [top-k frame_bases]}`.
    Measures the top-k frames through `measure_frame_geometry` and medians each feature
    over them. `top_frames` lets the caller INJECT the ranked top-k — the femur-cine
    landmark passes `femur_cine_scorer`'s ranking; when None, ranks `masks` by the largest
    CLEAN RF cross-section (`repr_scorer`), the legacy-sweep behavior, unchanged. One
    measurement primitive, two rankers (sweep = repr_scorer; femur = femur_cine_scorer).
    Returns empty values when there is no clean frame — the caller then WITHHOLDS the
    landmark rather than faking it."""
    finals = finals or {}
    top = top_frames if top_frames is not None else repr_scorer.top_clean_frames(masks, sweep_grades, k=k)
    if not top:
        return {"values": {}, "frames": []}
    per_feature: dict = {f: [] for f in LANDMARK_FEATURES}
    for fb in top:
        ff = measure_frame_geometry(masks[fb], finals.get(fb), spacing_row)
        for f in LANDMARK_FEATURES:
            v = ff.get(f)
            # > 0 guard: a degenerate spacing/mask yields a non-physical 0 area or
            # NaN thickness — withhold it rather than placing the study at p0.
            if v is not None and not (isinstance(v, float) and np.isnan(v)) and v > 0:
                per_feature[f].append(v)
    values = {f: float(median(vs)) for f, vs in per_feature.items() if vs}
    return {"values": values, "frames": top}

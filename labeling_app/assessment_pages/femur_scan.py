"""Sweep-wide pre-scan for the frame picker (phase 3).

Segments every sweep frame and tags each with two cues for the operator:
  * its QMT tier (`qmt_review.frame_qmt_status`) — femur-bearing first;
  * its image-quality grade (`feature_compute.compute_image_quality_features`)
    — a per-thumbnail quality hint so the operator sees what the QA layer
    will say *before* committing to frames (operator review Q1 / chunk 4).
GPU work — gated behind an explicit button in the picker sidebar, run with
a progress callback. `mask_cache` / `final_cache` / `image_quality_cache`
(the session-state caches) are read-and-filled, so frames already
segmented or graded in this session are not redone, and phase 4 reuses
the masks.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

import torch

from preflight_helpers.feature_compute import compute_image_quality_features
from preflight_helpers.segmenter import (
    build_voi_hist_clahe_stack, predict_mask, synthesize_final_u8,
)

from labeling_app.assessment_pages.qmt_review import frame_qmt_status


def scan_sweep_for_femur(
    frame_bases: list[str],
    learn,
    bundle_meta,
    raw_dir: Path,
    *,
    mask_cache: dict,
    final_cache: dict,
    image_quality_cache: Optional[dict] = None,
    progress: Optional[Callable[[float, str], None]] = None,
) -> list[dict]:
    """Segment every sweep frame; tag with QMT tier + image-quality grade.

    Returns `[{frame_base, tier, label, provenance, image_quality}]` sorted
    Tier 1 → 2 → 3, stable within a tier (sweep order preserved).
    `mask_cache` / `final_cache` / `image_quality_cache` are read-and-filled,
    so frames already segmented or graded in this session are not redone.

    `image_quality_cache` is optional: pass a dict (typically a session-state
    dict) to fold image-quality grading into the scan; pass None to skip it
    (legacy callers / tests that only need the femur tier). The QA grade is
    `compute_image_quality_features(...)` with `spacing_row={}` and
    `boundary_entropy=None` — the calibrated four detectors (femur_*,
    edge_clip_*) all run without those signals; the picker's per-thumbnail
    cue uses only the calibrated subset anyway, so the inert spacing /
    softmax inputs do not bias the cue.
    """
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    results: list[dict] = []
    n = len(frame_bases)
    for i, fb in enumerate(frame_bases):
        if fb not in mask_cache:
            if fb not in final_cache:
                final_cache[fb] = synthesize_final_u8(raw_dir, fb)
            stacked = build_voi_hist_clahe_stack(
                final_cache[fb], modalities=bundle_meta.modalities)
            mask_cache[fb] = predict_mask(learn, stacked, bundle_meta, device=device)
        status = frame_qmt_status(mask_cache[fb])
        iq = None
        if image_quality_cache is not None:
            if fb in image_quality_cache:
                iq = image_quality_cache[fb]
            else:
                iq = compute_image_quality_features(
                    frame_base=fb,
                    multiclass_mask=mask_cache[fb],
                    final_image_u8=final_cache[fb],
                    spacing_row={},
                    qmt_provenance=status.get("provenance"),
                    boundary_entropy=None,
                ).to_dict()
                image_quality_cache[fb] = iq
        results.append({
            "frame_base": fb,
            "tier": status["tier"],
            "label": status["label"],
            "provenance": status["provenance"],
            # A dominant femur was segmented iff `femur_absent` did NOT fire.
            # The picker badge uses this to avoid mislabelling a tier-3 frame
            # that HAS a femur (but an unmeasurable muscle path) as "no femur".
            "femur_present": "femur_absent" not in (status.get("flags") or []),
            "image_quality": iq,
        })
        if progress is not None:
            progress((i + 1) / n, fb)
    results.sort(key=lambda r: r["tier"])      # stable → Tier 1 first
    return results

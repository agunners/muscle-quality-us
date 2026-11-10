"""Femur-cine representative-frame scorer (Akadir 2026-06-26).

Ranks femur-cine frames by the operator's stated criteria for a *good* femur frame — NOT by RF area
(the long sweep's metric, see `repr_scorer`). The femur cine's job is the QMT measurement, so a frame
ranks by whether its QMT caliper actually reaches the femur:

  * Tier 1 — a standard, femur-anchored fascia-to-femur QMT ("the caliper reaches the bone", cortex in
    view). Best.
  * Tier 2 — a proxy span (femur present but the geometry is too poor to anchor it). Below Tier 1.
  * Tier 3 — no measurable femur. NOT a candidate (scores 0; never "recommended").

Within a tier, the **fullest / least-squeezed** cross-section wins — measured by muscle occupancy
(`qmt_muscle_occupancy_pct`, a depth-independent percentage, robust across the cine's varying depth).
This mirrors the legacy long-sweep pre-scan's role (surface the best frames before picking) but keys
on QMT geometry, which is what the femur cine measures.

Pure + torch-free (operates on already-predicted masks; segmentation happens in the pre-scan), so the
ranking is unit-testable without a model. The tier ladder + occupancy come from the SAME canonical
measurement the assessment uses (`measurements._qmt_measurement_details` + `aggregation.qmt_tier`), so
the pre-scan's "recommended" and the scored QMT cannot disagree on what a femur-anchored frame is.
"""
from __future__ import annotations

from typing import Dict, List

import numpy as np

from labeling_app.measurements import _qmt_measurement_details
from preflight_helpers.aggregation import qmt_tier

# Tier dominates the rank; occupancy (0-100) only breaks ties within a tier. Not a tuned weight — a
# separation constant > the occupancy range so a fuller Tier-2 can never outrank a leaner Tier-1.
_TIER_SEPARATION = 1000.0


def femur_frame_score(mask: np.ndarray) -> float:
    """Higher = a better femur-cine frame; 0 for a Tier-3 (no measurable femur) frame.

    Tier 1 (caliper reaches the bone) ranks above Tier 2 (proxy) above Tier 3 (excluded); within a
    tier the fullest/least-squeezed cross-section (muscle occupancy) wins. Tier 1 -> 2000 + occupancy,
    Tier 2 -> 1000 + occupancy, Tier 3 -> 0.
    """
    details = _qmt_measurement_details(mask)
    tier = qmt_tier(details)
    if tier >= 3:
        return 0.0
    occ = details.get("qmt_muscle_occupancy_pct")
    occ = float(occ) if occ is not None else 0.0
    return (3 - tier) * _TIER_SEPARATION + occ


def score_femur_cine(masks: Dict[str, np.ndarray]) -> Dict[str, float]:
    """{frame_base: score} for every femur-cine frame (see `femur_frame_score`)."""
    return {fb: femur_frame_score(m) for fb, m in masks.items()}


def top_femur_frames(masks: Dict[str, np.ndarray], k: int = 3) -> List[str]:
    """The top-k femur frames (femur-anchored first, fullest within a tier) — the pre-scan's
    'recommended' picks. Only frames with a measurable femur (score > 0) are returned, so the list is
    shorter than k (or empty) when the cine has fewer femur-anchored frames. Ties broken by frame_base
    so the order is deterministic."""
    scored = [(fb, s) for fb, s in score_femur_cine(masks).items() if s > 0]
    scored.sort(key=lambda x: (-x[1], x[0]))
    return [fb for fb, _ in scored[:k]]


# ---- QMT trajectory / consistency QA (Akadir 2026-07-17) ---------------------
# Is the femur QMT a stable, reliable read across the frames, or does the segmentation wobble?
# A per-study RELIABILITY signal (independent of whether the value agrees with a rater), surfaced
# as a badge. Validated in the v1.2 re-base: clean studies land at CV ~1-3% / roughness 0.008-0.026,
# genuinely erratic ones at roughness 0.28-0.43. Both measures are SCALE-FREE (uses qmt_px, and each
# normalises by the median, so px vs mm cancels) — no spacing needed.
# Standard-QMT frame-to-frame dispersion (CV) at/above this reads "variable"; below, "reliable".
# 0.05 = calibrated 2026-07-18 on the OLD single-sweep cohort (190 rater-QMT studies; median CV ~1%,
# a clean gap opens ~0.05). PROVISIONAL by design: the badge's real target is NEW-protocol femur cines,
# so this is a first draft measured on a stand-in population. RECALIBRATE once ~two-dozen+ new-protocol
# femur-cine studies have been scored — one command rebuilds the CV distribution + threshold sweep:
#     .venv/bin/python scripts/calibration/qmt_cv_flag_calibration.py
# read the distribution's gap + the flag rate (see its --help), then reset this value. The WHEN + HOW,
# and why CV flags a wobble not a wrong value, live in memory: project_qmt_cv_flag_calibration.
QMT_CV_FLAG = 0.05


def _qmt_consistency_stats(qmt_values: List[float]) -> dict:
    """Reliability stats over a study's standard (femur-anchored) QMT across its frames.
      cv        — dispersion (the femur-sweep measure: every frame images the femur, so a wide spread
                  = the segmentation wobbles). std/median.
      roughness — median|q[i-1]-2q[i]+q[i+1]|/median over frames in order (the lateral->medial sweep
                  measure: robust to a smooth flow, large only for erratic jumps). None when n<3.
    n<2 -> insufficient (no verdict). Both rounded, scale-free."""
    qs = [float(q) for q in qmt_values if q is not None]
    n = len(qs)
    if n < 2:
        return {"n": n, "cv": None, "roughness": None, "verdict": "insufficient"}
    med = float(np.median(qs)) or 1.0
    cv = float(np.std(qs) / med)
    rough = None
    if n >= 3:
        a = np.asarray(qs, dtype=float)
        rough = float(np.median(np.abs(a[:-2] - 2 * a[1:-1] + a[2:])) / med)
    return {"n": n, "cv": round(cv, 4),
            "roughness": None if rough is None else round(rough, 4),
            "verdict": "variable" if cv >= QMT_CV_FLAG else "reliable"}


def femur_qmt_consistency(masks: Dict[str, np.ndarray]) -> dict:
    """QMT consistency across femur frames (frame_base order). Keeps the standard (Tier-1,
    femur-anchored) qmt_px per frame — the measurement the score actually rides — then
    `_qmt_consistency_stats`. Pure + torch-free (predicted masks in, no model, no spacing)."""
    qs: List[float] = []
    for fb in sorted(masks):
        details = _qmt_measurement_details(masks[fb])
        if qmt_tier(details) == 1 and details.get("qmt_px"):
            qs.append(float(details["qmt_px"]))
    return _qmt_consistency_stats(qs)

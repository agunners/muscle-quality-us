"""Slice F (redesigned 2026-06-09) — the RF "landmark" frame score.

A per-frame score that ranks which sweep frame best captures the rectus-femoris
(RF) cross-section, so RF area / thickness are read — and compared across studies
— at each study's FULLEST CLEAN cross-section (its anatomical landmark), not at an
arbitrary picked frame. The same score does three jobs:
  * recommends the best frames to pick — the picker surfaces the top few BEFORE
    selection;
  * defines the study's landmark = the median of the top-K largest-clean frames'
    measured value (`landmark_value`) — the selection-independent value the cohort
    comparison uses (Phase 2);
  * (legacy) ranks the operator's PICKED frames to suggest a representative
    (`_ai_suggested_repr` in p03).

Reuses the p03 pre-scan's cached masks + QA grades — no new GPU pass.

RANKING: among CLEAN frames, the LARGEST RF cross-section ranks highest; centering
breaks ties. A frame is CLEAN unless its RF mask is untrustworthy or its area is
corrupted (`_clean`): no RF body, `wrong_site`, `edge_clip_vertical`, or aggregate
tier `invalid`. Lateral clipping is EXPECTED with the 128L probe (shorter than RF,
so the RF is rarely fully in frame) and is NOT disqualifying — only VERTICAL
(top/bottom) clipping, which truncates the cross-section, is. Darkness is NOT a
geometry disqualifier either — RF is read off the (bright) fascia, which survive a
dark interior; confirmed by an adjacent-frame test, so `dark_clip_high` was pulled
from this gate (Akadir 2026-06-10) and stays only on the echo features.

═══════════════════════════════════════════════════════════════════════════
FLAGGED — clinical assumption A1: the sweep is roughly longitudinal, so the
FULLEST cross-section ≈ the anatomical mid-belly (the standard RF measurement
location). The whole landmark rests on this. CONFIRM with the acquisition
protocol; a Phase-0 measurement (largest-RF-frame vs largest-QMT-frame
coincidence) tests a related question. The cross-study-comparability CLAIM is
earned by validation, not asserted here.
═══════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

from statistics import median
from typing import Optional

import numpy as np

from labeling_app.measurements import LABEL_RF
# Reuse the exact edge-clip primitives the detectors use, so the score keys off
# the same RF body the measurement + the edge_clip detectors do.
from labeling_app.image_quality import _largest_component, _ring_touch

# Centering only orders frames of near-equal area (the tiebreaker); it never
# overrides a genuinely larger cross-section.
_CENTERING_TIEBREAK = 1e-3
_RING_PX = 1   # lateral-clip ring width — matches the edge_clip detectors' default

# Clean-landmark gate flags: a frame firing any of these is not a landmark
# candidate, because they corrupt the GEOMETRY — wrong_site (the region is not RF)
# and edge_clip_vertical (the cross-section is truncated top/bottom).
#
# dark_clip_high is deliberately NOT here (Akadir 2026-06-10). An adjacent-frame
# test — comparing a dark frame to its clean neighbour ~0.2 s away, i.e. the same
# anatomy at a different gain — found RF area essentially unchanged by darkness
# (median 1.005×, within natural frame-to-frame variation). RF is a fascia-based
# measurement and the bright fascia survive a dark interior, so gating geometry on
# darkness only discarded good belly frames (the very frames the landmark wants).
# Dark still gates the ECHO features (int_cv_within / rf_echo) in the § 6.2 table,
# where it is the right signal. edge_clip_LATERAL is also absent — lateral clip is
# expected with the 128L probe + tolerated.
_GATE_FLAGS = frozenset({"wrong_site", "edge_clip_vertical"})


def _rf_body(mask: np.ndarray) -> np.ndarray:
    """The largest 8-connected RF component — the measured RF body, strays out."""
    return _largest_component(mask == LABEL_RF)


def completeness(mask: np.ndarray) -> float:
    """C ∈ {0, 0.5, 1} — RF not cut off laterally. Kept for the p04 'why' panel;
    lateral clip is tolerated by the gate, so this INFORMS but does not reject."""
    rf = _rf_body(mask)
    if not rf.any():
        return 0.0
    _t, _b, left, right = _ring_touch(rf, _RING_PX)
    return 1.0 - 0.5 * (int(left) + int(right))


def centering(mask: np.ndarray) -> float:
    """K ∈ [0,1] — RF horizontal centroid near image centre, so a central
    measurement column lands on the muscle belly, not its taper."""
    rf = _rf_body(mask)
    if not rf.any():
        return 0.0
    w = rf.shape[1]
    cx = float(np.argwhere(rf)[:, 1].mean())
    half = w / 2.0
    return max(0.0, 1.0 - abs(cx - half) / half) if half > 0 else 0.0


def rf_area_px(mask: np.ndarray) -> int:
    """The largest-RF-component area in pixels (the landmark raw signal)."""
    return int(_rf_body(mask).sum())


def fullness(area: float, sweep_areas: list) -> float:
    """M ∈ [0,1] — `area`'s percentile rank within its sweep (fraction of frames
    with RF area ≤ this). The fullest cross-section → 1.0; an empty sweep → 0."""
    areas = [a for a in sweep_areas if a is not None]
    if not areas:
        return 0.0
    return sum(1 for a in areas if a <= area) / len(areas)


def _clean(mask: np.ndarray, grade: Optional[dict]) -> bool:
    """Is this frame a clean landmark candidate? Needs an RF body, no gate flag
    fired (across validity + pending), and aggregate tier not `invalid`. Lateral
    clip is NOT a reject (only vertical, via `_GATE_FLAGS`)."""
    if not _rf_body(mask).any():
        return False
    g = grade or {}
    fired = (set(g.get("image_quality_validity_flags") or ())
             | set(g.get("image_quality_pending_calibration_flags") or ()))
    if fired & _GATE_FLAGS:
        return False
    return g.get("image_quality_aggregate_tier") != "invalid"


def repr_score(mask: np.ndarray, *, sweep_rf_areas: list,
               grade: Optional[dict] = None) -> float:
    """R ∈ [0, ~1] — area-primary, centering tiebreaker; 0 if the frame is not a
    clean landmark candidate. The fullest CLEAN cross-section scores highest."""
    if not _clean(mask, grade):
        return 0.0
    m = fullness(rf_area_px(mask), sweep_rf_areas)
    return m + _CENTERING_TIEBREAK * centering(mask)


def score_sweep(masks: dict, grades: Optional[dict] = None) -> dict:
    """R for every frame — `{frame_base: R}`. `grades` = `{fb: image_quality}`
    supplies the gate (tier + flags); absent → the gate only checks RF presence.
    Fullness is computed relative to THIS sweep, so the score is sweep-relative."""
    sweep_areas = [rf_area_px(m) for m in masks.values()]
    out: dict = {}
    for fb, m in masks.items():
        out[fb] = repr_score(m, sweep_rf_areas=sweep_areas,
                             grade=(grades or {}).get(fb))
    return out


def top_clean_frames(masks: dict, grades: Optional[dict] = None,
                     k: int = 5) -> list:
    """The k frames with the largest CLEAN RF cross-section (centering breaks
    ties) — the landmark candidates AND the picker's pre-selection recommendation.
    Returns frame_bases best-first; EMPTY when the study has no clean frame (e.g.
    a pervasively-dark study) — callers then withhold the landmark, not fake it."""
    scored = [(fb, s) for fb, s in score_sweep(masks, grades).items() if s > 0]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [fb for fb, _ in scored[:k]]


def landmark_value(values: dict, landmark_frames: list) -> Optional[float]:
    """The study's landmark VALUE for one feature: the median of `values` over the
    `landmark_frames` (= `top_clean_frames(...)`). `values` =
    `{frame_base: measured_value}` (RF area mm², thickness mm, …). None when no
    landmark frame has a value — the cohort placement is then withheld, not faked."""
    vals = [values[fb] for fb in landmark_frames
            if values.get(fb) is not None]
    return float(median(vals)) if vals else None

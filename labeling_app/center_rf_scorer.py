"""Center-RF cine representative-frame scorer (Akadir 2026-07-16).

Ranks CENTER-RF frames for the ONE thing that cine feeds — the echo-texture read
(`int_cv_within` / fascicular contrast). A frame is good for texture when its RF
ROI is free of acoustic **shadow** (dropout), the one artifact that corrupts
`int_cv`. This is the texture analogue of the other two rankers, each scored on its
modality's job:

  * `repr_scorer`       — sweep / RF **geometry**: fullest CLEAN RF cross-section.
    Deliberately IGNORES darkness (RF is fascia-based, survives a dark interior —
    Akadir 2026-06-10), which is correct for geometry and wrong for texture.
  * `femur_cine_scorer` — femur cine / **QMT**: caliper-reaches-bone, then fullest.
  * `center_rf_scorer`  — center-RF / **int_cv texture**: shadow-free first. (here)

**Shadow is not darkness — that is the whole point.** A true acoustic shadow blocks
the beam, so every pixel DEEPER in the same column is dark too and never recovers;
real hypoechoic muscle is dark but has measurable echo beneath it. So we test DEPTH
CONTINUITY per pixel (is there echo deeper in this column?), not a brightness
threshold. Excluding dark-but-real tissue would strip the exact signal `int_cv`
measures. Verified on AKA019-1E7A / AKA024 (100% of dark recovers = real tissue) vs
AKA017 (dark to depth = true shadow); the artifact carries the evidence.

The score is a RANKING, not a cutoff — no arbitrary "shadow_frac >= X" gate to
defend. Among frames with a clean RF body it prefers the least-shadow frame, RF
fullness breaking ties. When a study's whole cine is shadowed the top frame is
simply the least-bad; the WITHHOLD decision (does the residual shadow move int_cv
past the reproducibility floor?) is a separate downstream gate, not this ranker's
job. Torch-free (operates on already-predicted masks + the final_u8 the segmenter
saw), so it is unit-testable without a model.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np

from labeling_app.measurements import LABEL_RF
from labeling_app.repr_scorer import _clean, fullness, rf_area_px
from preflight_helpers.post_processing import post_process_rf

# The depth-continuity test's two physics anchors (NOT a shadow-vs-tissue cutoff —
# that call is per-pixel + threshold-free). SHADOW_DARK_RAIL: a pixel this dark is a
# dropout candidate (near the ~0 clip floor). SHADOW_ECHO_FLOOR: a pixel this bright
# is unambiguously real echo, so a column carrying one below the dark pixel proves
# the beam was NOT blocked. Both are calibratable against the cohort; deliberately
# conservative so a real cortex/echo below a dark patch reads as recovery.
SHADOW_DARK_RAIL = 8
SHADOW_ECHO_FLOOR = 40
# Fullness only orders frames of near-equal shadow — it never lets a fuller but more
# shadowed frame outrank a cleaner one (shadow is what corrupts int_cv).
_FULLNESS_TIEBREAK = 1e-3


def _bright_below(image: np.ndarray) -> np.ndarray:
    """Per pixel: is there a pixel >= SHADOW_ECHO_FLOOR strictly DEEPER in its
    column? (echo beneath => the beam reached depth => not a shadow)."""
    bright = image >= SHADOW_ECHO_FLOOR
    out = np.zeros_like(bright)
    acc = np.zeros(image.shape[1], dtype=bool)
    for r in range(image.shape[0] - 1, -1, -1):
        out[r] = acc
        acc = acc | bright[r]
    return out


def shadow_fraction(mask: np.ndarray, image: np.ndarray) -> float:
    """Fraction of the RF-eroded ROI (where int_cv is read) that is TRUE SHADOW:
    dark (<= SHADOW_DARK_RAIL) AND with no measurable echo deeper in the same column.
    Real dark tissue (echo beneath) is NOT counted. 0.0 when the ROI is empty.

    `image` is the final_u8 grayscale the segmenter saw, at mask resolution."""
    roi = post_process_rf(mask, rf_class_id=LABEL_RF, erosion_px=3).rf_eroded_mask
    n = int(roi.sum())
    if n == 0:
        return 0.0
    if image.shape != mask.shape:
        # the cached final_u8 is the raw synthesis (not mask-res); match it to the mask
        # the same way feature_compute does before reading intensity.
        from PIL import Image as _PILImage
        image = np.asarray(
            _PILImage.fromarray(image).resize((mask.shape[1], mask.shape[0]), _PILImage.BILINEAR))
    dark = roi & (image <= SHADOW_DARK_RAIL)
    shadow = dark & ~_bright_below(image)
    return float(shadow.sum()) / n


def center_rf_frame_score(mask: np.ndarray, image: np.ndarray, *,
                          sweep_rf_areas: list, grade: Optional[dict] = None) -> float:
    """T in [0, ~1] — texture-suitability. 0 if the frame is not a clean RF frame
    (`repr_scorer._clean`: RF body present, no wrong_site / vertical clip, tier not
    invalid). Otherwise (1 - shadow_fraction), RF fullness a tiny tiebreaker, so the
    least-shadow clean frame ranks highest."""
    if not _clean(mask, grade):
        return 0.0
    sf = shadow_fraction(mask, image)
    m = fullness(rf_area_px(mask), sweep_rf_areas)
    return (1.0 - sf) + _FULLNESS_TIEBREAK * m


def score_center_rf_cine(masks: Dict[str, np.ndarray], finals: Dict[str, np.ndarray],
                         grades: Optional[Dict[str, dict]] = None) -> Dict[str, float]:
    """{frame_base: T} for every center-RF frame. `finals` = {fb: final_u8} (shadow
    needs the image). `grades` = {fb: image_quality} for the clean gate; absent ->
    the gate only checks RF presence. Fullness is cine-relative."""
    sweep_areas = [rf_area_px(m) for m in masks.values()]
    out: Dict[str, float] = {}
    for fb, m in masks.items():
        img = finals.get(fb)
        out[fb] = 0.0 if img is None else center_rf_frame_score(
            m, img, sweep_rf_areas=sweep_areas, grade=(grades or {}).get(fb))
    return out


def top_texture_frames(masks: Dict[str, np.ndarray], finals: Dict[str, np.ndarray],
                       grades: Optional[Dict[str, dict]] = None, k: int = 5) -> List[str]:
    """The k least-shadow clean center-RF frames (best-first) — the picker's texture
    recommendation. Ties broken by frame_base for determinism. EMPTY when no clean
    frame exists (callers then fall back / withhold, not fake)."""
    scored = [(fb, s) for fb, s in score_center_rf_cine(masks, finals, grades).items() if s > 0]
    scored.sort(key=lambda x: (-x[1], x[0]))
    return [fb for fb, _ in scored[:k]]

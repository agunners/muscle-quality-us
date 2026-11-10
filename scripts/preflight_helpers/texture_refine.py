"""RF-texture region refinement (Akadir 2026-07-14).

int_cv (fascicular contrast) is read from the RF's CLEAN INTERIOR — not the raw mask, whose boundary
picks up fascia. The default is the 3px-eroded RF mask (`post_process_rf`, unchanged — Akadir kept 3px).
The operator can REFINE it per study when contamination is visible: erode MORE (uniform boundary cleanup)
and/or keep a depth BAND (trim the contaminated deep edge — the shallow FOV clips the RF's deep fascia,
so the *bottom* of the RF is the usual offender). Deterministic + reproducible: the refine is captured as
`{erosion_px, band}` numbers, so a session's exact region can be re-derived — this is the no-canvas
refine, a clean RF *section*, not a hand-drawn shape.

Torch-free (post_process_rf = cv2, compute_intensity = numpy) so it is importable headlessly + unit-testable.
"""
from __future__ import annotations

from statistics import median
from typing import Optional

import numpy as np

from preflight_helpers.post_processing import post_process_rf
from preflight_helpers.segmenter import LABEL_RF

DEFAULT_EROSION_PX = 3
FULL_BAND = (0.0, 1.0)


def vetted_rf_region(multiclass_mask: np.ndarray, erosion_px: int = DEFAULT_EROSION_PX,
                     band: tuple = FULL_BAND) -> np.ndarray:
    """The RF pixels int_cv is read from after the operator's refine. Erode `erosion_px` (>=3 = the
    default clean margin), then keep only the vertical depth band [band[0], band[1]] of the RF's extent
    (0.0 = top/superficial, 1.0 = bottom/deep). The default (3, (0,1)) reproduces current behavior."""
    rfp = post_process_rf(multiclass_mask, rf_class_id=LABEL_RF, erosion_px=int(erosion_px),
                          fill_holes=False)
    region = rfp.rf_eroded_mask
    lo, hi = float(band[0]), float(band[1])
    if (lo, hi) != FULL_BAND and region.any():
        rows = np.where(region.any(axis=1))[0]
        top, bot = int(rows.min()), int(rows.max())
        h = max(bot - top, 1)
        r0 = top + int(round(max(0.0, lo) * h))
        r1 = top + int(round(min(1.0, hi) * h))
        keep = np.zeros_like(region)
        keep[r0:r1 + 1, :] = True
        region = region & keep
    return region


def refined_int_cv(masks: dict, finals: dict, erosion_px: int = DEFAULT_EROSION_PX,
                   band: tuple = FULL_BAND) -> Optional[float]:
    """Median int_cv over the given frames, each read from its vetted RF region. `masks`/`finals` are
    `{frame_base: multiclass_mask / final_u8}`. Returns None when no frame yields a usable region (all
    empty) — the caller then leaves int_cv unchanged / withholds, never invents one."""
    from rf_quality.features.intensity import compute_intensity
    vals = []
    for fb, mask in masks.items():
        final = finals.get(fb)
        if mask is None or final is None:
            continue
        region = vetted_rf_region(mask, erosion_px, band)
        if int(region.sum()) == 0:
            continue
        icv = compute_intensity(final, region).get("int_cv_within")
        if icv is not None:
            vals.append(float(icv))
    return float(median(vals)) if vals else None


def is_refined(erosion_px: int, band: tuple) -> bool:
    """Did the operator actually refine, vs the 3px full-band default?"""
    return int(erosion_px) != DEFAULT_EROSION_PX or (float(band[0]), float(band[1])) != FULL_BAND

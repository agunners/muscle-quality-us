"""Three hyperechoic-fraction thresholds: Otsu, fixed t=200, mean+1·SD.

Only ``mean+1SD`` is ``preferred=True`` in the registry. Otsu is retained
for comparison (known-weak on unimodal healthy RF) and t200 for
continuity with legacy reports (but tagged ``scanner_risk=high`` because
a fixed 200 cutoff on an 8-bit gain-sensitive scale is brittle).

Cascade-rule note (`hyperechoic_frac_t200_fat_corrected_young_*`, fixed
2026-04-30): Young's correction shifts every pixel value by ``α × fat_thickness``,
so the corrected fraction-above-threshold is ``frac(raw_pixel >= 200 - α × fat)``,
NOT ``raw_fraction + α × fat`` (which would add an EI-units quantity to a
fraction in [0, 1] — dimensionally incoherent). The corrected siblings are
computed per-frame via :func:`compute_hyperechoic_fat_corrected_t200` because
they require pixel access; they cannot be derived from the per-frame summary
fraction alone. The other two hyperechoic stems are shift-invariant under
Young's correction (mean+1SD: the shift cancels because both threshold and
pixels move together; Otsu: the threshold is itself dataset-relative), so
they need no corrected siblings.
"""

from __future__ import annotations

import cv2
import numpy as np


def _otsu_threshold(vals_u8: np.ndarray) -> int:
    arr = vals_u8.reshape(1, -1)
    ret, _ = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return int(ret)


def compute_hyperechoic(img_u8: np.ndarray, mask: np.ndarray) -> dict[str, float | None]:
    vals = img_u8[mask]
    out: dict[str, float | None] = {
        "hyperechoic_frac_otsu": None,
        "hyperechoic_frac_t200": None,
        "hyperechoic_frac_mean_plus_1sd": None,
    }
    if vals.size == 0:
        return out
    vals_u8 = vals.astype(np.uint8)
    thr = _otsu_threshold(vals_u8)
    out["hyperechoic_frac_otsu"] = float((vals_u8 >= thr).mean())
    out["hyperechoic_frac_t200"] = float((vals_u8 >= 200).mean())
    mean = float(vals.astype(np.float64).mean())
    std = float(vals.astype(np.float64).std())
    thr_rel = mean + std
    out["hyperechoic_frac_mean_plus_1sd"] = float((vals_u8 >= thr_rel).mean())
    return out


def compute_hyperechoic_fat_corrected_t200(
    img_u8: np.ndarray,
    mask: np.ndarray,
    fat_thickness_mm: float | None,
    literature_cf_per_cm: float,
    cohort_ei_per_fat_mm_slope: float | None,
) -> dict[str, float | None]:
    """Young-corrected ``hyperechoic_frac_t200`` siblings via per-frame threshold shift.

    Under the additive correction, every pixel value increases by
    ``slope × fat_thickness_mm``, so the corrected fraction-above-200 equals
    ``frac(raw_pixel >= 200 - slope × fat)``. This is mathematically equivalent
    to applying the correction to the image and re-thresholding at 200, with
    the convenience that we don't need to materialise the corrected image — we
    just shift the threshold the other way.

    The two slopes are NOT interchangeable:
      * ``literature_cf_per_cm / 10`` is Young's within-subject α (probe-pressure
        perturbation; n=5×4).
      * ``cohort_ei_per_fat_mm_slope`` is the cohort between-subject regression
        slope (n≈216, R²≈0.115). Confounded with cohort variation; documented
        in ``YoungCorrectionCfg`` and ``feature_changelog.md``.

    The ``_fat_corrected_young_refit`` column suffix is preserved across the
    round-2 unit-honest rename for backward compatibility.

    Returns ``None`` for both siblings when ``fat_thickness_mm`` is missing or
    the mask is empty. Returns ``None`` for the refit variant when
    ``cohort_ei_per_fat_mm_slope`` is None. Values are clamped to [0, 1] by
    construction (they're fractions of pixel counts).
    """
    out: dict[str, float | None] = {
        "hyperechoic_frac_t200_fat_corrected_young_lit": None,
        "hyperechoic_frac_t200_fat_corrected_young_refit": None,
    }
    if fat_thickness_mm is None:
        return out
    vals = img_u8[mask]
    if vals.size == 0:
        return out
    vals_f = vals.astype(np.float64)

    # Literature variant: shift = fat_thickness_mm × (cf_per_cm / 10)
    shift_lit = float(fat_thickness_mm) * (float(literature_cf_per_cm) / 10.0)
    t_lit = 200.0 - shift_lit
    out["hyperechoic_frac_t200_fat_corrected_young_lit"] = float((vals_f >= t_lit).mean())

    # Cohort-slope variant: shift = fat_thickness_mm × cohort_ei_per_fat_mm_slope
    # (column suffix kept as `_young_refit` for backward compat).
    if cohort_ei_per_fat_mm_slope is not None:
        shift_refit = float(fat_thickness_mm) * float(cohort_ei_per_fat_mm_slope)
        t_refit = 200.0 - shift_refit
        out["hyperechoic_frac_t200_fat_corrected_young_refit"] = float((vals_f >= t_refit).mean())

    return out

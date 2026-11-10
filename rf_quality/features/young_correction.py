"""Young 2015 additive fat-attenuation correction — sibling helpers.

Young's correction is a per-study scalar additive shift in EI units:

    EI_corrected = EI_raw + fat_thickness_cm × cf

where cf is positive (their LOGIQ e value: 40.5278 EI per 1.0 cm). We expose
two variants:

  * ``_young_lit``   — fat_thickness_mm × (literature_cf_per_cm / 10)
  * ``_young_refit`` — fat_thickness_mm × cohort_ei_per_fat_mm_slope  (None until fit)

The ``_young_refit`` column suffix is a historical name — the slope it carries is
the cohort between-subject regression slope, not a refit of Young's α (see
:class:`rf_quality.config.YoungCorrectionCfg` for the unit-honest framing). The
column suffix is preserved across the round-2 rename to avoid registry churn.

Helpers here are used by `features/intensity.py`, `features/depth_bands.py`,
`features/hyperechoic.py`, and `features/fat.py` to emit corrected siblings
for first-order moments and for ratios involving muscle EI in the numerator.

Math note (why we don't bother with corrected siblings for shape/dispersion/
texture/gradient features): an additive scalar shift `image + c` is shape-
invariant for std, IQR, MAD, skew, kurtosis, bimodality coefficient, gradient
magnitude, GLCM relative pair frequencies, and Shannon entropy. So those
features have the same value before and after correction; emitting siblings
would just duplicate columns. This is why the cascade in `pipeline.py` only
calls these helpers for the affected stems.

Cascade-rule edge case (`int_cv_within`, fixed 2026-04-30):
``int_cv_within = std / |mean|`` is a *ratio of moments*, not a first-order
moment. Treating it as a first-order moment in the cascade (i.e. ``raw + shift``)
adds an EI-units quantity to a dimensionless ratio — dimensionally invalid as a
Young correction. The proper correction propagates through the components: a
constant shift ``c`` on the underlying image leaves std unchanged (shift-
invariant) and shifts mean to ``mean + c``, so the dimensionally-correct
corrected CV is ``std / |mean + c|``. The cv_within siblings keep their
canonical names — ``int_cv_within_fat_corrected_young_{lit,refit}`` — and now
emit dimensionally-correct values via :func:`corrected_cv_within` below. The
in-place edit is appropriate here because these are experimental sibling stems
that document the literature method rather than carrying load-bearing claims;
the column-value change is recorded in ``feature_changelog.md`` and a future
reader recomputing from the registry will get the corrected values.
"""

from __future__ import annotations


def young_shift_lit(fat_thickness_mm: float | None, literature_cf_per_cm: float) -> float | None:
    """Returns Young's literature shift (per study) in EI u8 units.

    Young 2015 cf is per *cm*; we convert to per-mm by dividing by 10.
    Returns ``None`` when fat_thickness_mm is unavailable.
    """
    if fat_thickness_mm is None:
        return None
    return float(fat_thickness_mm) * (float(literature_cf_per_cm) / 10.0)


def young_shift_refit(fat_thickness_mm: float | None, cohort_ei_per_fat_mm_slope: float | None) -> float | None:
    """Returns the cohort-slope shift (per study) in EI u8 units.

    The slope is the cohort between-subject regression slope (EI per mm of fat
    thickness), not Young's within-subject α. The column suffix
    ``_fat_corrected_young_refit`` is kept for backward compatibility.

    Returns ``None`` when ``fat_thickness_mm`` or ``cohort_ei_per_fat_mm_slope``
    is unavailable.
    """
    if fat_thickness_mm is None or cohort_ei_per_fat_mm_slope is None:
        return None
    return float(fat_thickness_mm) * float(cohort_ei_per_fat_mm_slope)


def corrected_value(raw: float | None, shift: float | None) -> float | None:
    """Apply an additive shift; return ``None`` when either input is missing."""
    if raw is None or shift is None:
        return None
    return float(raw) + float(shift)


def corrected_ratio(numerator_raw: float | None, denominator_raw: float | None,
                    shift_num: float | None, shift_den: float | None = None) -> float | None:
    """Apply additive shifts to numerator and (optionally) denominator, then ratio.

    Used for ``ei_to_fat_ratio_fat_corrected_young_*`` (only numerator shifted —
    Young's correction is for muscle EI; fat EI is not corrected) and for
    ``int_superficial_to_deep_ratio_fat_corrected_young_*`` (both shifted by the
    same per-study scalar; the ratio still changes because the shift does not
    cancel: ``(s+c)/(d+c) ≠ s/d``).
    """
    if numerator_raw is None or denominator_raw is None or shift_num is None:
        return None
    num = float(numerator_raw) + float(shift_num)
    den = float(denominator_raw) + (float(shift_den) if shift_den is not None else 0.0)
    if abs(den) < 1e-6:
        return None
    return float(num / den)


def corrected_cv_within(
    mean: float | None, std: float | None, shift: float | None
) -> float | None:
    """Young-correct the within-frame CV by propagating the shift through the mean.

    ``int_cv_within = std / |mean|`` is a *ratio of moments*, not a first-order
    moment. A constant shift ``c`` on the underlying image leaves std unchanged
    (shift-invariant) and shifts mean to ``mean + c``. So the dimensionally-
    correct Young-corrected CV is ``std / |mean + c|``, NOT ``(std/|mean|) + c``
    (which would add an EI-units quantity to a dimensionless ratio — incoherent
    as a Young correction).

    Returns ``None`` if any input is missing or the corrected denominator is
    ε-small (post-shift mean too close to zero to compute a stable ratio).
    """
    if mean is None or std is None or shift is None:
        return None
    new_mean = float(mean) + float(shift)
    if abs(new_mean) < 1e-6:
        return None
    return float(std) / abs(new_mean)


# Stems that get a corrected sibling (per the plan's mathematical scoping).
# Used by the registry self-test to assert presence + by `pipeline.py` to
# dispatch the right correction per stem class.
#
# Four dispatch classes:
#   FIRST_ORDER          — additive shift on value (canonical Young form)
#   RATIOS               — additive shift on numerator and/or denominator
#   RATIO_OF_MOMENTS     — component-wise propagation through `corrected_cv_within`
#   FRACTION_ABOVE_THRESHOLD — per-frame threshold shift, computed at extraction
#                              time via `compute_hyperechoic_fat_corrected_t200`
#                              (requires raw pixel access; not derivable from
#                              summary stats)
#
# NOTE: ``int_cv_within`` and ``hyperechoic_frac_t200`` are intentionally NOT
# in the FIRST_ORDER tuple — they are dimensionally distinct (a ratio of
# moments and a fraction-above-threshold respectively), and the additive
# cascade is invalid for them. Their siblings live under the canonical
# ``<parent>_fat_corrected_young_{lit,refit}`` names with dimensionally-
# correct values from their dedicated dispatch paths.
YOUNG_CORRECTED_STEMS_FIRST_ORDER = (
    "int_mean",
    "int_median",
    "int_p10",
    "int_p90",
    "superficial_int_mean",
    "mid_int_mean",
    "deep_int_mean",
)

YOUNG_CORRECTED_STEMS_RATIOS = (
    "ei_to_fat_ratio",
    "int_superficial_to_deep_ratio",
)

# Ratio-of-moments stems: corrected via component-wise propagation (not via
# additive shift on the value itself). Siblings ship under the canonical
# ``<parent>_fat_corrected_young_{lit,refit}`` names. In-place edit is
# appropriate during the interim phase because these are experimental sibling
# stems rather than load-bearing main features; column-value changes are
# recorded in ``feature_changelog.md``.
YOUNG_CORRECTED_STEMS_RATIO_OF_MOMENTS = (
    "int_cv_within",
)

# Fraction-above-absolute-threshold stems: corrected via per-frame threshold
# shift at extraction time (requires pixel access; not derivable from summary
# stats). Siblings emitted by ``compute_hyperechoic_fat_corrected_t200`` in
# ``rf_quality/features/hyperechoic.py``; the cascade in ``pipeline.py`` does
# not handle these.
YOUNG_CORRECTED_STEMS_FRACTION_ABOVE_THRESHOLD = (
    "hyperechoic_frac_t200",
)

YOUNG_CORRECTED_STEMS_ALL = (
    YOUNG_CORRECTED_STEMS_FIRST_ORDER
    + YOUNG_CORRECTED_STEMS_RATIOS
    + YOUNG_CORRECTED_STEMS_RATIO_OF_MOMENTS
    + YOUNG_CORRECTED_STEMS_FRACTION_ABOVE_THRESHOLD
)

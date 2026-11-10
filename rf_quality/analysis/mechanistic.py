"""Per-stem pre-registered expected direction vs the y_track_a ordinal
target (preserved=0, intermediate=1, poor=2).

Sign convention:
    +1 → feature is expected to INCREASE with worse muscle quality
    -1 → feature is expected to DECREASE with worse muscle quality
     0 → no strong directional prior

**Domain priors, explicitly derived:**

Normal rectus femoris on B-mode: **dark myofiber parenchyma** with a
couple of bright reflectors at the fascial interfaces. Distribution
is *unimodal dark with a narrow bright tail* (right-skewed).

Fibro-fatty infiltration (poor quality): the fatty/fibrotic replacement
is bright throughout — what was "dark parenchyma + bright fascia" becomes
"uniformly bright tissue + fascia buried in it". The histogram *flattens
toward one bright mode* and the shape moments **shrink**, not grow.

This flips several priors from the naive "heterogeneity ↑ with pathology"
reading to the empirically grounded "histogram unimodalises under
infiltration" reading. Specifically:

  * int_skew, int_cv_within, int_bimodality_coefficient, int_iqr,
    int_kurtosis_excess DECREASE with worse quality (the distribution
    becomes more uniform as bright fibro-fat takes over).
  * hist_uniformity = Σ p² INCREASES (one dominant mode).
  * GLCM contrast / entropy DECREASE (smoother, more homogeneous texture).
  * GLCM ASM (angular second moment = measure of uniformity) INCREASES.

Citation-grounded direction for the features with explicit literature:
  * ei_to_fat_ratio: Young 2015, higher EI relative to fat UP with fibro-fat.
  * Pillen & van Alfen review: mean EI UP with infiltration.
  * Noorkoiv 2010 / Kumar 2017: muscle thickness DOWN with atrophy.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# IMPORTANT: round-2 γ-pass invariant (see end of this file)
#
# The literal +1 / -1 / 0 values in the dict below are **historical priors**,
# preserved as code-history for the formula+intuition discussion that lives
# in each per-feature card's "Sign analysis" subsection (per
# `prompts/acts_567_authoring_playbook.md` §"Family-level signal" item 5).
#
# **The runtime values are ALL ZEROED** at the bottom of this file by the
# γ-pass loop (`for _key in list(EXPECTED_SIGN): EXPECTED_SIGN[_key] = 0`).
# That means `expected_sign("int_skew")` returns 0 at runtime regardless of
# what the inline comment says, and `mechanism_verdict()` returns `no_prior`
# for every stem.
#
# The per-key comments below document the original reasoning so a feature-
# card author writing the "Sign analysis" subsection can read the formula+
# intuition expected sign from the comment, then compare against the run's
# observed cohort sign. **Do NOT re-promote these comments to runtime values
# without explicit literature support and a changelog `sign_prior` op.**
# ---------------------------------------------------------------------------

EXPECTED_SIGN: dict[str, int] = {
    # --- intensity central tendency: fibro-fatty infiltration → brighter
    "int_mean": +1,
    "int_median": +1,
    "int_p10": +1,
    "int_p90": +1,
    "int_entropy": +1,   # more tissue types in ROI → shannon entropy UP

    # --- intensity spread (careful — unimodalisation reduces spread):
    #     CV = σ/μ; μ rises faster than σ under infiltration → CV DOWN
    "int_cv_within": -1,
    #     IQR tightens as dark myofiber peak disappears into bright tissue
    "int_iqr": -1,
    #     Raw std can go either way (more bright content adds variance);
    #     keep neutral.
    "int_std": 0,
    "int_mad": 0,

    # --- intensity shape moments (histogram unimodalises under fibro-fat):
    #     skew: healthy = right-skewed (dark + bright tail); poor = more
    #     symmetric single-bright-mode → skew DOWN.
    "int_skew": -1,
    #     kurt_excess: healthy has heavy tails; poor closer to Gaussian →
    #     kurt_excess DOWN.
    "int_kurtosis_excess": -1,
    #     BC = (skew² + 1) / (kurt_excess + 3): healthy bimodal (dark
    #     myofiber + bright reflectors) → BC high; poor unimodal → BC DOWN.
    "int_bimodality_coefficient": -1,
    #     hist_uniformity = Σ p²: direction is genuinely ambiguous in this cohort
    #     (the original "single dominant bin → high uniformity" model assumes a
    #     unimodal-bright diseased histogram, but speckle-rich infiltration could
    #     just as easily broaden the histogram and lower uniformity). Demoted to
    #     no_prior pending a literature-grounded re-evaluation; the feature still
    #     enters Stage B → F on data merit, only the mechanism vote abstains.
    "hist_uniformity": 0,

    # --- hyperechoic fractions (bright-pixel share): UP with infiltration
    "hyperechoic_frac_otsu": +1,
    "hyperechoic_frac_t200": +1,
    "hyperechoic_frac_mean_plus_1sd": +1,

    # --- GLCM (smoother, more homogeneous, less varied texture in poor):
    "glcm_d1_contrast": -1,
    "glcm_d2_contrast": -1,
    "glcm_d1_homogeneity": -1,    # the "striated" preserved fibre pattern is locally homogeneous; infiltration breaks it (ambiguous but plan says -1)
    "glcm_d2_homogeneity": -1,
    # glcm_d1_entropy / glcm_d2_entropy / glcm_d1_asm: original priors disagreed
    # with observed sign in Stage F triangulation. Flipping would require a
    # cited rationale (Pillen 2008, Stock & Thompson 2017, etc. on GLCM in
    # healthy vs diseased muscle) we have not verified. Demoted to no_prior;
    # features stay in the registry, only the mechanism vote abstains.
    "glcm_d1_entropy": 0,
    "glcm_d2_entropy": 0,
    "glcm_d1_correlation": 0,
    "glcm_d2_correlation": 0,
    "glcm_d1_asm": 0,
    "glcm_anisotropy_index": -1,   # directional contrast lost

    # --- orientation (fibre alignment lost with infiltration):
    "orient_coherence": -1,
    "orient_grad_mean": 0,
    "orient_grad_std": 0,
    "grad_orient_dominant_angle_deg": 0,
    "grad_orient_entropy": +1,     # disorder UP
    "grad_orient_concentration": -1,

    # --- depth bands: all three intensity means UP
    "superficial_int_mean": +1,
    "mid_int_mean": +1,
    "deep_int_mean": +1,
    "int_superficial_to_deep_ratio": 0,  # depends on attenuation
    "int_depth_gradient": 0,

    # --- fat:
    "fat_to_area_ratio": +1,
    "ei_to_fat_ratio": +1,
    "fat_int_mean": 0,
    "fat_thickness_mm": +1,  # higher fat-layer thickness correlates with worse muscle quality
    # baseline soft-tissue layer thicknesses — sign priors deliberately 0 pending
    # the focused literature pass (prompts/04_literature_grounding_prompt.md). The
    # symmetry rule from RUNNER.md Act 2 says: no in-corpus citation verified, so
    # no_prior. Stage F will report whatever direction the cohort surfaces; we will
    # NOT post-hoc rationalise it without literature support.
    "skin_thickness_mm": 0,
    "fascia_thickness_mm": 0,
    # int_fat_corrected REMOVED 2026-04-28 — formula was rf_int_mean / (1 + fat_to_area_ratio),
    # which decreased EI as fat grew (wrong direction for Young 2015 attenuation correction)
    # AND was incoherent under the muscle-quality convention (lower EI = healthier muscle, so
    # "EI penalised by fat" implies "more fat = better muscle"). Replaced by additive Young
    # corrected siblings via fat_thickness_mm. Their expected_sign inherits from raw parents
    # because the additive shift preserves direction-with-quality.

    # --- geometry: atrophy
    "full_area_mm2": -1,
    "full_max_thickness_mm": -1,
    "full_thickness_q50_mm": -1,
}


# --- Young-corrected siblings inherit raw parent's sign ---
# Rationale: Young 2015's correction is a positive additive shift
# (EI_corrected = EI_raw + α × fat_thickness_mm, α > 0) applied per-study.
# An additive scalar shift does not flip the direction-with-quality relationship
# for first-order moments — corrected EI still rises with fat infiltration; the
# correction just removes the attenuation artifact and tightens the correlation
# (Young's r went from 0.79 raw to 0.91 corrected in RF). For ratios with muscle
# EI in the numerator, the same logic holds since only the muscle side shifts.
# Auto-populating below keeps EXPECTED_SIGN in sync with the registry's
# corrected-sibling list without hand-maintenance.
_YOUNG_CORRECTED_SIBLING_PARENTS = (
    "int_mean", "int_median", "int_p10", "int_p90",
    "superficial_int_mean", "mid_int_mean", "deep_int_mean",
    "ei_to_fat_ratio", "int_superficial_to_deep_ratio",
)
for _parent in _YOUNG_CORRECTED_SIBLING_PARENTS:
    if _parent not in EXPECTED_SIGN:
        raise RuntimeError(f"Young-correction parent missing from EXPECTED_SIGN: {_parent}")
    EXPECTED_SIGN[f"{_parent}_fat_corrected_young_lit"] = EXPECTED_SIGN[_parent]
    EXPECTED_SIGN[f"{_parent}_fat_corrected_young_refit"] = EXPECTED_SIGN[_parent]

# Stems with dimensionally-distinct corrected dispatch (in-place fix 2026-04-30):
#
#   int_cv_within (ratio of moments): corrected via component-wise propagation
#     (corrected CV = std / |mean + α × fat|). Direction-with-quality is
#     preserved — the shift only changes the magnitude of the denominator,
#     not the sign of the cv-vs-label correlation.
#
#   hyperechoic_frac_t200 (fraction above absolute threshold): corrected via
#     per-frame threshold shift (corrected fraction = frac(pixel >= 200 - α × fat)).
#     Direction-with-quality is preserved — shifting the threshold down for
#     patients with more fat compensates for the attenuation that made their
#     pixels artificially dim, so a sicker patient still gets a higher fraction
#     of bright pixels relative to the corrected threshold.
#
# Both inherit parent's expected_sign. See registry.py for the post-fix formulas.
for _parent in ("int_cv_within", "hyperechoic_frac_t200"):
    if _parent not in EXPECTED_SIGN:
        raise RuntimeError(f"{_parent} missing from EXPECTED_SIGN (required for corrected siblings)")
    EXPECTED_SIGN[f"{_parent}_fat_corrected_young_lit"] = EXPECTED_SIGN[_parent]
    EXPECTED_SIGN[f"{_parent}_fat_corrected_young_refit"] = EXPECTED_SIGN[_parent]


# --- Round-2 new stems (added 2026-05-02) — sign priors at 0 per γ-pass ---
#
# Per the round-2 integration plan
# (~/.claude/plans/let-s-start-with-your-hazy-scroll.md, Block A5), all 25
# round-2 additions enter EXPECTED_SIGN at 0. The interpretive sign
# discussion (formula+intuition expected sign vs observed cohort sign)
# lives in feature cards (per acts_567_authoring_playbook.md
# §"Family-level signal" item 5), not in Stage F vetoes.
_ROUND2_NEW_STEMS = (
    # GLCM extensions (d=1 only)
    "glcm_d1_cluster_shade", "glcm_d1_dissimilarity", "glcm_d1_idm",
    # GLRLM
    "glrlm_short_run_emphasis", "glrlm_long_run_emphasis",
    "glrlm_low_gray_run_emphasis", "glrlm_run_length_nonuniformity",
    # GLSZM
    "glszm_small_zone_emphasis", "glszm_zone_size_nonuniformity",
    # NGTDM
    "ngtdm_busyness", "ngtdm_coarseness",
    # GLDM
    "gldm_gray_level_nonuniformity",
    # LBP
    "lbp_entropy_p8r1",
    # Multi-Otsu trinary
    "multi_otsu_fat_fraction", "multi_otsu_collagen_fraction",
    # Speckle / blob
    "bright_speckle_density_per_mm2", "blob_area_fraction",
    # Local intensity
    "local_entropy_mean",
    # Multi-scale / sheet / orientation
    "wavelet_detail_energy_ratio", "fractal_dimension_box_count",
    "frangi_septae_density_mean_response", "gabor_energy_mean",
    # Dubois TAI (multi-angle anisotropy)
    "texture_anisotropy_index_dubois",
    # Fascia split (geometry)
    "fascia_superficial_thickness_mm", "fascia_deep_thickness_mm",
)
for _stem in _ROUND2_NEW_STEMS:
    EXPECTED_SIGN[_stem] = 0


# --- Round-2 γ-pass (2026-05-02) — symmetric prior reset to 0 ---
#
# Every entry in EXPECTED_SIGN set to 0. Round-2 starts the analysis
# afresh: no carry-over of round-1 cluster-rep choices or prior-sign
# decisions. The interpretive sign analysis moves to feature card prose
# at Acts 5–7 (per plan B1 / acts_567_authoring_playbook.md), where each
# card includes a "Sign analysis" subsection covering formula+intuition
# expected sign, observed cohort sign, and a divergence-flag analysis.
# Stage F triangulation runs on methods_passed_count alone (no programmatic
# mechanism-mismatch veto). The diagnostic loop that caught the
# `int_cv_within_fat_corrected_young_lit` dispatch error pre-fix is
# preserved by the per-feature-card sign discussion, not by the Stage F
# veto.
#
# The original prior values are preserved in this file's prose (the
# docstring + per-key comments above) as code-history; only the runtime
# values are zeroed.
for _key in list(EXPECTED_SIGN):
    EXPECTED_SIGN[_key] = 0


def expected_sign(stem: str) -> int:
    return EXPECTED_SIGN.get(stem, 0)


def mechanism_verdict(stem: str, observed_sign: int) -> str:
    exp = expected_sign(stem)
    if observed_sign == 0:
        return "no_observation"
    if exp == 0:
        return "no_prior"
    return "match" if (exp == observed_sign) else "mismatch"

"""Feature registry — single source of truth for every computed column.

The registry drives four things downstream:
  1. ``study_features.csv`` column construction (repr_* / agg_*).
  2. Plausibility gating (``min_plausible`` / ``max_plausible``).
  3. Aggregation behaviour per family (``aggregation_policy``).
  4. Auto-generated ``feature_dictionary.csv``.

Anti-drift test (``registry_selftest``) asserts every emitted column either
maps to a ``FeatureSpec`` stem or is a declared non-feature column
(IDs, provenance, QC flags). That test is the anti-drift backbone.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


Role = Literal["primary", "support", "anchor", "exploratory"]
AggregationPolicy = Literal[
    "anchor_primary",                     # repr_* is primary; agg_* support only
    "agg_primary_with_anchor_passthrough",  # agg_* primary; repr_* sensitivity
    "per_frame_only",                     # no study_features column
]
ScannerRisk = Literal["low", "medium", "high", "n/a"]


class Family(str, Enum):
    GEOMETRY = "geometry"
    INTENSITY = "intensity"
    SHAPE = "shape"
    HYPERECHOIC = "hyperechoic"
    GLCM = "glcm"
    GLCM_ANISOTROPY = "glcm_anisotropy"
    ORIENTATION = "orientation"
    GRAD_ORIENT_HIST = "grad_orient_hist"
    DEPTH_BAND = "depth_band"
    DEPTH_RATIO = "depth_ratio"
    FAT = "fat"
    CV_WITHIN = "cv_within"
    ANCHOR = "anchor"            # labeling-app continuity (aka folder-A "legacy_*")
    QC_SUPPORT = "qc_support"    # Tier-1 image QC exposed as a support feature
    MASK_QC = "mask_qc"          # Tier-2A discrete-mask flags
    BAND_GLCM = "band_glcm"
    # --- Round-2 additions (2026-05-02): radiomics matrix families and
    # texture-beyond-Haralick families. Hand-rolled IBSI-aligned
    # implementations live in rf_quality.features.radiomics_matrices and
    # rf_quality.features.texture_advanced. Each family is its own enum
    # member so the family-level signal landscape doctrine (per
    # acts_567_authoring_playbook.md §"Family-level signal") aggregates
    # cleanly per family in the response pack.
    GLRLM = "glrlm"               # Gray-Level Run-Length Matrix (IBSI 3.5)
    GLSZM = "glszm"               # Gray-Level Size-Zone Matrix (IBSI 3.6)
    NGTDM = "ngtdm"               # Neighborhood Gray-Tone Difference Matrix (IBSI 3.7)
    GLDM = "gldm"                 # Gray-Level Dependence Matrix (IBSI 3.9)
    LBP = "lbp"                   # Local Binary Pattern entropy
    MULTI_OTSU = "multi_otsu"     # trinary Otsu fat / collagen / muscle fractions
    SPECKLE_BLOB = "speckle_blob" # bright_speckle_density + blob_area_fraction
    LOCAL_INTENSITY = "local_intensity"  # sliding-window entropy
    WAVELET = "wavelet"           # DWT detail-energy ratio
    FRACTAL = "fractal"           # box-counting fractal dimension
    SHEETNESS = "sheetness"       # Sato/Frangi multi-scale sheet-detector
    GABOR = "gabor"               # multi-orientation × multi-frequency Gabor bank


@dataclass(frozen=True)
class FeatureSpec:
    stem: str                                   # canonical short name (no modality/agg prefix)
    family: Family
    unit: str
    measurement_domain: str                     # "intensity", "geometry", "texture", "meta", ...
    formula_summary: str
    role: Role
    aggregation_policy: AggregationPolicy
    scanner_risk: ScannerRisk
    preferred: bool = True
    grounding_family: str = ""                  # e.g. "Haralick GLCM", "Young 2015 EI/Fat"
    min_plausible: float | None = None
    max_plausible: float | None = None
    null_rules: str = "null when ROI pixel count < min; never coerced to 0."
    notes: str = ""

    def emits_study_columns(self, modality: str) -> list[str]:
        """Returns the wide-table column names this stem contributes for a modality."""
        p = f"{modality}_rf_{self.stem}"
        if self.aggregation_policy == "anchor_primary":
            return [f"{p}_repr", f"{p}_agg_median", f"{p}_agg_range"]
        if self.aggregation_policy == "agg_primary_with_anchor_passthrough":
            return [f"{p}_agg_median", f"{p}_agg_iqr", f"{p}_agg_n_valid", f"{p}_repr"]
        return []  # per_frame_only


# -------------------- registry --------------------

def _mk(**kw) -> FeatureSpec:
    return FeatureSpec(**kw)


REGISTRY: list[FeatureSpec] = [
    # --- geometry (anchor_primary: flanks are different anatomy) ---
    _mk(stem="full_area_mm2", family=Family.GEOMETRY, unit="mm^2",
        measurement_domain="geometry",
        formula_summary="Σ (muscle-class pixels) × spacing_x × spacing_y",
        role="primary", aggregation_policy="anchor_primary", scanner_risk="low",
        grounding_family="Noorkoiv 2010 CSA ultrasound",
        min_plausible=50.0, max_plausible=2000.0),
    _mk(stem="full_area_px", family=Family.GEOMETRY, unit="px",
        measurement_domain="geometry",
        formula_summary="count of muscle-class pixels after largest-component filter",
        role="support", aggregation_policy="anchor_primary", scanner_risk="low",
        min_plausible=500.0, max_plausible=60000.0,
        notes="support; used by Tier-2A gate."),
    _mk(stem="full_max_thickness_mm", family=Family.GEOMETRY, unit="mm",
        measurement_domain="geometry",
        formula_summary="max over columns of (y_max − y_min + 1) × spacing_y",
        role="primary", aggregation_policy="anchor_primary", scanner_risk="low",
        min_plausible=2.0, max_plausible=40.0),
    _mk(stem="full_thickness_q50_mm", family=Family.GEOMETRY, unit="mm",
        measurement_domain="geometry",
        formula_summary="median over columns of (y_max − y_min + 1) × spacing_y",
        role="primary", aggregation_policy="anchor_primary", scanner_risk="low",
        grounding_family="Kumar 2017 thickness reliability",
        min_plausible=2.0, max_plausible=35.0),
    _mk(stem="area_retained_fraction_after_erosion", family=Family.GEOMETRY, unit="frac",
        measurement_domain="geometry",
        formula_summary="area(eroded by 3px SE) / area(full)",
        role="support", aggregation_policy="anchor_primary", scanner_risk="low",
        min_plausible=0.0, max_plausible=1.0,
        notes="low values → thin/fragmented RF mask; used by QC Tier-2A."),
    _mk(stem="skin_thickness_mm", family=Family.GEOMETRY, unit="mm",
        measurement_domain="geometry",
        formula_summary="mean per-column pixel count on skin-class mask × spacing_y",
        role="primary", aggregation_policy="anchor_primary", scanner_risk="low",
        grounding_family="baseline soft-tissue morphometry — sign prior pending literature audit (LIT-EI-2021, LIT-EI-2022B candidates)",
        min_plausible=0.3, max_plausible=8.0,
        notes="baseline morphometry; expected_sign=0 until literature pass verifies direction"),
    _mk(stem="fascia_thickness_mm", family=Family.GEOMETRY, unit="mm",
        measurement_domain="geometry",
        formula_summary="mean per-column pixel count on fascia-class mask × spacing_y",
        role="primary", aggregation_policy="anchor_primary", scanner_risk="low",
        grounding_family="baseline soft-tissue morphometry — sign prior pending literature audit (fibrosis literature candidates: TBD)",
        min_plausible=0.1, max_plausible=5.0,
        notes="baseline morphometry; expected_sign=0 until literature pass verifies direction"),

    # --- intensity (agg primary + anchor passthrough) ---
    _mk(stem="int_mean", family=Family.INTENSITY, unit="u8",
        measurement_domain="intensity",
        formula_summary="mean pixel value in RF-eroded ROI",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="high",
        grounding_family="Pillen & van Alfen muscle US review",
        min_plausible=5.0, max_plausible=245.0),
    _mk(stem="int_std", family=Family.INTENSITY, unit="u8",
        measurement_domain="intensity",
        formula_summary="std pixel value in RF-eroded ROI",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        min_plausible=0.5, max_plausible=80.0),
    _mk(stem="int_median", family=Family.INTENSITY, unit="u8",
        measurement_domain="intensity",
        formula_summary="median pixel value in RF-eroded ROI",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="high",
        min_plausible=5.0, max_plausible=245.0),
    _mk(stem="int_p10", family=Family.INTENSITY, unit="u8",
        measurement_domain="intensity", formula_summary="p10 pixel value in RF-eroded ROI",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="high", min_plausible=0.0, max_plausible=245.0),
    _mk(stem="int_p90", family=Family.INTENSITY, unit="u8",
        measurement_domain="intensity", formula_summary="p90 pixel value in RF-eroded ROI",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="high", min_plausible=0.0, max_plausible=255.0),
    _mk(stem="int_iqr", family=Family.INTENSITY, unit="u8",
        measurement_domain="intensity", formula_summary="p75 − p25 in RF-eroded ROI",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium", min_plausible=0.0, max_plausible=150.0),
    _mk(stem="int_entropy", family=Family.INTENSITY, unit="bits",
        measurement_domain="intensity",
        formula_summary="Shannon entropy of masked 8-bit histogram",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="Shannon 1948",
        min_plausible=0.0, max_plausible=8.0),
    _mk(stem="int_mad", family=Family.INTENSITY, unit="u8",
        measurement_domain="intensity",
        formula_summary="median(|x − median(x)|) in RF-eroded ROI",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium", min_plausible=0.0, max_plausible=100.0),

    # --- shape (skew/kurt/bimodality/hist_uniformity) ---
    _mk(stem="int_skew", family=Family.SHAPE, unit="unitless",
        measurement_domain="intensity",
        formula_summary="mean of z^3 (z=(x−mean)/std)",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium", min_plausible=-5.0, max_plausible=5.0),
    _mk(stem="int_kurtosis_excess", family=Family.SHAPE, unit="unitless",
        measurement_domain="intensity",
        formula_summary="mean of z^4 − 3",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium", min_plausible=-3.0, max_plausible=20.0),
    _mk(stem="int_bimodality_coefficient", family=Family.SHAPE, unit="unitless",
        measurement_domain="intensity",
        formula_summary="(skew^2 + 1) / (kurt_excess + 3) with ε-guard",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="Freeman & Dale 2013 bimodality coefficient",
        min_plausible=0.0, max_plausible=1.0,
        notes=">0.555 suggests bimodality; sensitive to small samples."),
    _mk(stem="hist_uniformity", family=Family.SHAPE, unit="unitless",
        measurement_domain="intensity",
        formula_summary="Σ p_i^2 where p_i = hist_i / N",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium", min_plausible=0.0, max_plausible=1.0),

    # --- hyperechoic fractions (three; mean+1SD preferred) ---
    # Round-2 (2026-05-02): preferred flag flipped False → True for both
    # round-1-deprecated hyperechoic siblings. The structural concerns
    # (Otsu unimodal weakness; t200 gain-sensitivity) are preserved verbatim
    # in `notes` and surface as eval-time caveats in the response pack per
    # the family-level signal doctrine — they are testable hypotheses the
    # funnel can confirm or reject, not modeling-selector vetoes.
    _mk(stem="hyperechoic_frac_otsu", family=Family.HYPERECHOIC, unit="frac",
        measurement_domain="intensity",
        formula_summary="fraction of ROI ≥ Otsu threshold (on ROI pixels)",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium", preferred=True,
        grounding_family="Otsu 1979",
        min_plausible=0.0, max_plausible=1.0,
        notes="weak on unimodal/healthy RF — no valley to find."),
    _mk(stem="hyperechoic_frac_t200", family=Family.HYPERECHOIC, unit="frac",
        measurement_domain="intensity",
        formula_summary="fraction of ROI ≥ 200 (fixed)",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="high", preferred=True,
        min_plausible=0.0, max_plausible=1.0,
        notes="hard threshold on gain-sensitive scale."),
    _mk(stem="hyperechoic_frac_mean_plus_1sd", family=Family.HYPERECHOIC, unit="frac",
        measurement_domain="intensity",
        formula_summary="fraction of ROI ≥ (mean + 1·SD) of ROI itself",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium", preferred=True,
        min_plausible=0.0, max_plausible=1.0,
        notes="tissue-relative threshold; complements Otsu."),

    # --- GLCM (split distance d=1 / d=2; grey levels L=32) ---
    _mk(stem="glcm_d1_contrast", family=Family.GLCM, unit="unitless",
        measurement_domain="texture",
        formula_summary="Σ p_ij (i−j)^2 averaged over 4 angles at d=1",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="Haralick 1973",
        min_plausible=0.0, max_plausible=500.0),
    _mk(stem="glcm_d1_homogeneity", family=Family.GLCM, unit="unitless",
        measurement_domain="texture",
        formula_summary="Σ p_ij / (1 + (i−j)^2) at d=1",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium", min_plausible=0.0, max_plausible=1.0),
    _mk(stem="glcm_d1_entropy", family=Family.GLCM, unit="bits",
        measurement_domain="texture",
        formula_summary="−Σ p_ij log2(p_ij) at d=1",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium", min_plausible=0.0, max_plausible=12.0),
    _mk(stem="glcm_d1_correlation", family=Family.GLCM, unit="unitless",
        measurement_domain="texture",
        formula_summary="Haralick correlation at d=1",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium", min_plausible=-1.0, max_plausible=1.0),
    _mk(stem="glcm_d1_asm", family=Family.GLCM, unit="unitless",
        measurement_domain="texture",
        formula_summary="Σ p_ij^2 at d=1",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium", min_plausible=0.0, max_plausible=1.0),
    _mk(stem="glcm_d2_contrast", family=Family.GLCM, unit="unitless",
        measurement_domain="texture",
        formula_summary="as d=1, d=2",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium", min_plausible=0.0, max_plausible=1000.0),
    _mk(stem="glcm_d2_homogeneity", family=Family.GLCM, unit="unitless",
        measurement_domain="texture",
        formula_summary="as d=1, d=2",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium", min_plausible=0.0, max_plausible=1.0),
    _mk(stem="glcm_d2_entropy", family=Family.GLCM, unit="bits",
        measurement_domain="texture",
        formula_summary="as d=1, d=2",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium", min_plausible=0.0, max_plausible=12.0),
    _mk(stem="glcm_d2_correlation", family=Family.GLCM, unit="unitless",
        measurement_domain="texture",
        formula_summary="as d=1, d=2",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium", min_plausible=-1.0, max_plausible=1.0),
    _mk(stem="glcm_anisotropy_index", family=Family.GLCM_ANISOTROPY, unit="unitless",
        measurement_domain="texture",
        formula_summary="|contrast_a0_d1 − contrast_a90_d1|",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium", min_plausible=0.0, max_plausible=500.0),

    # --- structure-tensor orientation (Folder A) ---
    _mk(stem="orient_coherence", family=Family.ORIENTATION, unit="unitless",
        measurement_domain="texture",
        formula_summary="(λ1 − λ2) / (λ1 + λ2) on Gaussian-smoothed Sobel structure tensor",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium", min_plausible=0.0, max_plausible=1.0),
    _mk(stem="orient_grad_mean", family=Family.ORIENTATION, unit="u8/px",
        measurement_domain="texture",
        formula_summary="mean gradient magnitude in ROI",
        role="support", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium", min_plausible=0.0, max_plausible=255.0),
    _mk(stem="orient_grad_std", family=Family.ORIENTATION, unit="u8/px",
        measurement_domain="texture",
        formula_summary="std gradient magnitude in ROI",
        role="support", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium", min_plausible=0.0, max_plausible=255.0),
    _mk(stem="grad_orient_dominant_angle_deg", family=Family.GRAD_ORIENT_HIST, unit="deg",
        measurement_domain="texture",
        formula_summary="argmax of 8-bin Sobel orientation histogram in [0°,180°)",
        role="exploratory", aggregation_policy="per_frame_only", scanner_risk="medium",
        min_plausible=0.0, max_plausible=180.0),
    _mk(stem="grad_orient_entropy", family=Family.GRAD_ORIENT_HIST, unit="bits",
        measurement_domain="texture",
        formula_summary="Shannon entropy of 8-bin Sobel orientation histogram",
        role="support", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium", min_plausible=0.0, max_plausible=3.0),
    _mk(stem="grad_orient_concentration", family=Family.GRAD_ORIENT_HIST, unit="frac",
        measurement_domain="texture",
        formula_summary="max bin fraction of 8-bin Sobel orientation histogram",
        role="support", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium", min_plausible=0.125, max_plausible=1.0),

    # --- depth bands (no-overlap 3-split from Folder A) ---
    _mk(stem="superficial_int_mean", family=Family.DEPTH_BAND, unit="u8",
        measurement_domain="intensity", formula_summary="mean in superficial 1/3 of RF",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="high", min_plausible=0.0, max_plausible=255.0),
    _mk(stem="mid_int_mean", family=Family.DEPTH_BAND, unit="u8",
        measurement_domain="intensity", formula_summary="mean in middle 1/3 of RF",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="high", min_plausible=0.0, max_plausible=255.0),
    _mk(stem="deep_int_mean", family=Family.DEPTH_BAND, unit="u8",
        measurement_domain="intensity", formula_summary="mean in deep 1/3 of RF",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="high", min_plausible=0.0, max_plausible=255.0),
    _mk(stem="int_superficial_to_deep_ratio", family=Family.DEPTH_RATIO, unit="unitless",
        measurement_domain="intensity",
        formula_summary="superficial_int_mean / deep_int_mean (ε-guarded)",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="low",
        min_plausible=0.1, max_plausible=10.0,
        notes="tissue-relative → lower scanner risk than absolute intensity."),
    _mk(stem="int_depth_gradient", family=Family.DEPTH_RATIO, unit="u8/depth-frac",
        measurement_domain="intensity",
        formula_summary="OLS slope of intensity vs normalised depth across muscle columns",
        role="support", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        min_plausible=-255.0, max_plausible=255.0,
        notes="confounded by TGC; exploratory."),

    # --- fat proxy (Folder A) ---
    _mk(stem="fat_to_area_ratio", family=Family.FAT, unit="frac",
        measurement_domain="geometry",
        formula_summary="superficial_fat_area / rf_full_area",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="low", min_plausible=0.0, max_plausible=5.0),
    _mk(stem="ei_to_fat_ratio", family=Family.FAT, unit="unitless",
        measurement_domain="intensity",
        formula_summary="rf_int_mean / fat_int_mean (ε-guarded)",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="low",
        grounding_family="Within-frame muscle/fat brightness contrast; gain-normalised by construction. NOT Young 2015's fat-corrected EI (Young's r=0.91 was for additive-corrected EI vs MRI %fat in RF, not muscle/fat ratio).",
        min_plausible=0.1, max_plausible=10.0,
        notes=(
            "Useful as a gain-normalised quality contrast (the muscle/fat ratio "
            "cancels global gain in the limit where fat_int_mean depends only on gain). "
            "Does NOT correct for fat attenuation — that requires fat_thickness_mm "
            "(Young 2015 Eq. 5/6, see int_mean_fat_corrected_young_*). "
            "Round-2 caveat (Paris/Bell/Mourtzakis 2022, LIT-EI-2022B): subcutaneous "
            "adipose tissue echo intensity is itself negatively correlated with SAT "
            "thickness (r = −0.54 to −0.63 in 3 of 4 sex×age subgroups), meaning "
            "fat_int_mean varies systematically with SAT thickness in addition to gain. "
            "The gain-cancellation property is partially undermined when SAT thickness "
            "varies across studies; in our narrow-healthy 20–40 cohort the residual "
            "confound is bounded but worth flagging in interpretation."
        )),
    _mk(stem="fat_int_mean", family=Family.FAT, unit="u8",
        measurement_domain="intensity",
        formula_summary="mean of superficial fat mask pixels",
        role="support", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="high", min_plausible=0.0, max_plausible=255.0),
    _mk(stem="fat_thickness_mm", family=Family.FAT, unit="mm",
        measurement_domain="geometry",
        formula_summary="mean per-column fat extent (y_max−y_min+1) × pixel_spacing_y over fat-bearing columns",
        role="support", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="low",
        grounding_family="Young 2015 — input to additive attenuation correction (Eq. 5/6)",
        min_plausible=0.0, max_plausible=40.0,
        notes="Prerequisite for int_*_fat_corrected_young_lit/refit; the per-study scalar shift α × fat_thickness_mm is added to muscle EI and EI-derived first-order moments."),

    # --- within-frame CV ---
    _mk(stem="int_cv_within", family=Family.CV_WITHIN, unit="unitless",
        measurement_domain="intensity",
        formula_summary="std / |mean| within RF-eroded ROI",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="low", min_plausible=0.0, max_plausible=2.0),

    # --- labeling-app continuity anchors (was folder-A legacy_*) ---
    _mk(stem="anchor_rf_echo", family=Family.ANCHOR, unit="u8",
        measurement_domain="intensity",
        formula_summary="mean EI computed by labeling app on same mask+frame",
        role="anchor", aggregation_policy="anchor_primary", scanner_risk="n/a",
        grounding_family="labeling-app continuity",
        min_plausible=0.0, max_plausible=255.0,
        notes="family=anchor; correctness anchor only; not modelled."),
    _mk(stem="anchor_rf_thickness_mm", family=Family.ANCHOR, unit="mm",
        measurement_domain="geometry",
        formula_summary="RF thickness at representative column per labeling app",
        role="anchor", aggregation_policy="anchor_primary", scanner_risk="n/a",
        min_plausible=0.0, max_plausible=40.0,
        notes="family=anchor; correctness anchor only."),
    _mk(stem="anchor_rf_area_mm2", family=Family.ANCHOR, unit="mm^2",
        measurement_domain="geometry",
        formula_summary="RF area in mm^2 per labeling app",
        role="anchor", aggregation_policy="anchor_primary", scanner_risk="n/a",
        min_plausible=0.0, max_plausible=3000.0,
        notes="family=anchor; correctness anchor only."),

    # ============================================================
    # Round-2 additions (2026-05-02) — 25 new stems
    # ============================================================
    # Per the round-2 integration plan
    # (~/.claude/plans/let-s-start-with-your-hazy-scroll.md, Block A5).
    # Plausibility ranges are wide-first per the plan: tighten in a
    # follow-up `plausibility_range` op once Stage A produces the cohort
    # distribution. Wide-first prevents an implementation bug producing
    # 5× expected magnitude from silently nulling.

    # --- GLCM extensions (d=1 only): cluster_shade, dissimilarity, IDM ---
    # Computed by rf_quality.features.glcm.compute_glcm via the EXT_PROPS
    # tuple. Cluster_shade is unbounded (third-moment); dissimilarity at
    # L=32 ranges roughly [0, 32]; IDM in [0, 1] by construction.
    _mk(stem="glcm_d1_cluster_shade", family=Family.GLCM, unit="unitless",
        measurement_domain="texture",
        formula_summary="Σ ((i+j-μ_i-μ_j)^3) p_ij averaged over 4 angles at d=1",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="Haralick 1973; Texture Analysis MSK Review 2025 (Jan)",
        min_plausible=-1e6, max_plausible=1e6,
        notes="Third-order moment of GLCM; positive when bright-bright clumping dominates."),
    _mk(stem="glcm_d1_dissimilarity", family=Family.GLCM, unit="unitless",
        measurement_domain="texture",
        formula_summary="Σ |i−j| p_ij at d=1 (linear analog of contrast)",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="Haralick 1973; Texture Analysis MSK Review 2025",
        min_plausible=0.0, max_plausible=32.0),
    _mk(stem="glcm_d1_idm", family=Family.GLCM, unit="unitless",
        measurement_domain="texture",
        formula_summary="Σ p_ij / (1 + |i−j|) at d=1 (IBSI inverse difference moment; distinct from glcm_d1_homogeneity which uses 1/(1+(i-j)^2))",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="Haralick 1973; Watanabe 2017 (n=145 healthy QF age decline)",
        min_plausible=0.0, max_plausible=1.0),

    # --- GLRLM (Gray-Level Run-Length Matrix; IBSI 3.5) ---
    # Computed by rf_quality.features.radiomics_matrices.compute_glrlm,
    # 4-direction averaged at d=1 with binCount=32. Run-length j is
    # 1-indexed in IBSI (we use j+1 internally for j_idx 0-indexed).
    _mk(stem="glrlm_short_run_emphasis", family=Family.GLRLM, unit="unitless",
        measurement_domain="texture",
        formula_summary="Σ_ij p(i,j)/j² / N_r averaged over 4 directions at d=1 (IBSI SRE)",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="IBSI Zwanenburg 2020 §3.5; Tang 2022 (RF sarcopenia); Mirón-Mombiela 2023 (vastus lateralis frailty)",
        min_plausible=0.0, max_plausible=10.0,
        notes="Short runs dominate when texture is finely grained (preserved muscle); broaden under fibrofat infiltration."),
    _mk(stem="glrlm_long_run_emphasis", family=Family.GLRLM, unit="unitless",
        measurement_domain="texture",
        formula_summary="Σ_ij p(i,j)·j² / N_r averaged over 4 directions at d=1 (IBSI LRE)",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="IBSI; Texture Analysis MSK Review 2025",
        min_plausible=0.0, max_plausible=1e6),
    _mk(stem="glrlm_low_gray_run_emphasis", family=Family.GLRLM, unit="unitless",
        measurement_domain="texture",
        formula_summary="Σ_ij p(i,j)/i² / N_r averaged over 4 directions at d=1 (IBSI LGRE; i is 1-indexed gray level)",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="IBSI; Kumbhare 2018 (myofascial pain trapezius)",
        min_plausible=0.0, max_plausible=1.0,
        notes="Long runs of low gray-level dominate in healthy hypoechoic muscle; reduce under fibrofat replacement."),
    _mk(stem="glrlm_run_length_nonuniformity", family=Family.GLRLM, unit="unitless",
        measurement_domain="texture",
        formula_summary="Σ_j (Σ_i p(i,j))² / N_r averaged over 4 directions at d=1 (IBSI RLNU)",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="IBSI; Texture Analysis MSK Review 2025 (4/8 Galloway studies)",
        min_plausible=0.0, max_plausible=1e6),

    # --- GLSZM (Gray-Level Size-Zone Matrix; IBSI 3.6) ---
    # One matrix per ROI (no direction averaging). 8-connectivity per IBSI 2-D
    # recommendation. Computed by radiomics_matrices.compute_glszm.
    _mk(stem="glszm_small_zone_emphasis", family=Family.GLSZM, unit="unitless",
        measurement_domain="texture",
        formula_summary="Σ_ij p(i,j)/j² / N_z (IBSI SZE; j is 1-indexed zone size)",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="IBSI Zwanenburg 2020 §3.6; Khan 2024 (n=1191 deltoid radiomics, AUROC 0.86)",
        min_plausible=0.0, max_plausible=10.0,
        notes="Small zones dominate finely-organized preserved muscle."),
    _mk(stem="glszm_zone_size_nonuniformity", family=Family.GLSZM, unit="unitless",
        measurement_domain="texture",
        formula_summary="Σ_j (Σ_i p(i,j))² / N_z (IBSI ZSN)",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="IBSI; Mirón-Mombiela 2023 (vastus lateralis frailty)",
        min_plausible=0.0, max_plausible=1e6),

    # --- NGTDM (Neighborhood Gray-Tone Difference Matrix; IBSI 3.7) ---
    # 3x3 neighborhood (radius=1). Computed by radiomics_matrices.compute_ngtdm.
    _mk(stem="ngtdm_busyness", family=Family.NGTDM, unit="unitless",
        measurement_domain="texture",
        formula_summary="Σ p_i s_i / Σ_{i≠j, p_i,p_j>0} |i p_i − j p_j| (IBSI busyness)",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="IBSI Zwanenburg 2020 §3.7; Mirón-Mombiela 2023",
        min_plausible=0.0, max_plausible=1000.0),
    _mk(stem="ngtdm_coarseness", family=Family.NGTDM, unit="unitless",
        measurement_domain="texture",
        formula_summary="1 / (Σ p_i s_i + ε) (IBSI coarseness; ε=1e-6)",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="IBSI; Mirón-Mombiela 2023",
        min_plausible=0.0, max_plausible=2.0e6,
        notes="Plausibility ceiling 2e6 because IBSI's 1/(Σ p_i s_i + ε) formula returns 1/ε ≈ 1e6 on uniformly-bright ROIs (Σ p_i s_i → 0). Healthy hypoechoic muscle in this project's narrow 20-40 cohort approaches that limit by selection. Wide-first per round-2 plan: better to admit large values than null silently. Tighten in a follow-up plausibility_range op once cohort distribution is observed."),

    # --- GLDM (Gray-Level Dependence Matrix; IBSI 3.9) ---
    _mk(stem="gldm_gray_level_nonuniformity", family=Family.GLDM, unit="unitless",
        measurement_domain="texture",
        formula_summary="Σ_i (Σ_j P(i,j))² / N_total (IBSI GLN; distance=1, eps_alpha=0)",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="IBSI Zwanenburg 2020 §3.9; Khan 2024 (n=1191 deltoid US, AUROC 0.86 for T2DM detection)",
        min_plausible=0.0, max_plausible=1e6,
        notes="Closes the 5th radiomics matrix family (Khan 2024 names this stem explicitly with AUROC anchor). Plausibility ceiling 1e6 mirrors sibling nonuniformity stems (GLRLM RLNU, GLSZM ZSN); GLN scales as Σ_i count_i² / N which for typical 30k-100k pixel ROIs lands in 10k-100k range (smoke 2026-05-03 verified 12k-65k on synthetic and the original 1000 ceiling nullified all 32 frames)."),

    # --- LBP entropy ---
    _mk(stem="lbp_entropy_p8r1", family=Family.LBP, unit="bits",
        measurement_domain="texture",
        formula_summary="Shannon entropy (bits) of uniform LBP code histogram over muscle ROI; P=8, R=1, 10 bins",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="Ojala 2002; Bell 2022 (rectus femoris LBP entropy correlates with insulin resistance — closest direct cohort+anatomy anchor of round-2)",
        min_plausible=0.0, max_plausible=10.0,
        notes="LBP operates on local rank order, not absolute brightness — relatively gain-robust."),

    # --- Multi-Otsu trinary fat / collagen / muscle fractions ---
    _mk(stem="multi_otsu_fat_fraction", family=Family.MULTI_OTSU, unit="frac",
        measurement_domain="intensity",
        formula_summary="frac(arr ≥ T_2) where T_1, T_2 = threshold_multiotsu(arr, classes=3)",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="Otsu 1979; López-Gómez 2025 (n=337 oncology RF AI-segmented, FATi correlates with CT %IMAT)",
        min_plausible=0.0, max_plausible=1.0,
        notes="Caveat: tri-modal-ish histogram required; on uniformly hypoechoic healthy RF the upper threshold can be unstable. Wide range accepted in round-2 sandbox."),
    _mk(stem="multi_otsu_collagen_fraction", family=Family.MULTI_OTSU, unit="frac",
        measurement_domain="intensity",
        formula_summary="frac((arr ≥ T_1) & (arr < T_2)) (López-Gómez 2025 NMNFATi)",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="López-Gómez 2025 (NMNFATi: non-muscle non-fat index; collagen/fibrosis proxy)",
        min_plausible=0.0, max_plausible=1.0,
        notes="Closes the conceptual gap where hyperechoic_frac_* collapses fat with collagen. The middle-class fraction is mechanically distinct from fat and may carry independent quality signal."),

    # --- Bright-speckle density + blob area fraction ---
    _mk(stem="bright_speckle_density_per_mm2", family=Family.SPECKLE_BLOB, unit="1/mm^2",
        measurement_domain="texture",
        formula_summary="N_peaks(top-hat-filtered ROI ≥ μ + 1.5σ, σ_disk=1.5) ÷ full_area_mm²",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="Molinger 2024 'starry night' formalization; Texture Analysis Review 2025 (5/38 studies use blob analysis)",
        min_plausible=0.0, max_plausible=100.0,
        notes="Counts bright local maxima per mm². Sigma + min-distance currently un-tuned (sandbox); plausibility wide-first."),
    _mk(stem="blob_area_fraction", family=Family.SPECKLE_BLOB, unit="frac",
        measurement_domain="texture",
        formula_summary="Σ blob_area / ROI_area; thresh = μ + 1·SD intra-ROI; min_blob_size_px=4, 8-connectivity",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="Texture Analysis MSK Review 2025; complementary geometric statistic to bright_speckle_density",
        min_plausible=0.0, max_plausible=1.0),

    # --- Local entropy (sliding-window Shannon entropy) ---
    _mk(stem="local_entropy_mean", family=Family.LOCAL_INTENSITY, unit="bits",
        measurement_domain="texture",
        formula_summary="mean of skimage.filters.rank.entropy(img_u8, disk(r=5)) over muscle ROI",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="Texture Analysis MSK Review 2025",
        min_plausible=0.0, max_plausible=10.0,
        notes="Local-vs-global of int_entropy. Sandbox-tier per round-2 value table; risk of redundancy with int_entropy is the cohort-signal test the funnel will run."),

    # --- Wavelet detail energy ratio ---
    _mk(stem="wavelet_detail_energy_ratio", family=Family.WAVELET, unit="frac",
        measurement_domain="texture",
        formula_summary="(HL+LH+HH energy) / total wavelet energy after single-level Daubechies db4 dwt2 on bbox-cropped ROI with non-mask zeroed",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="Texture Analysis MSK Review 2025 (wavelet features across muscle studies)",
        min_plausible=0.0, max_plausible=1.0),

    # --- Fractal dimension box-counting ---
    _mk(stem="fractal_dimension_box_count", family=Family.FRACTAL, unit="unitless",
        measurement_domain="texture",
        formula_summary="slope of log N(ε) vs log(1/ε) for ε ∈ {2,4,8,16,32,64} px on ROI binarized at intra-ROI median",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="Mandelbrot fractal dimension; Mirón-Mombiela 2021 (transverse RF n=102 mixed-frailty; FD did NOT outperform EI alone — sober precedent)",
        min_plausible=0.0, max_plausible=3.0,
        notes="Round-2 sandbox: betting against Mirón-Mombiela 2021's negative cohort signal on RF. The funnel's verdict in our cohort is the test."),

    # --- Frangi/Sato sheetness mean (perimysium / septae detector) ---
    _mk(stem="frangi_septae_density_mean_response", family=Family.SHEETNESS, unit="unitless",
        measurement_domain="texture",
        formula_summary="mean of skimage.filters.sato(img, sigmas=(1,2,4), black_ridges=False) over muscle ROI",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="Sato 1998 (sheetness analog of Frangi 1998 vesselness); no muscle-US published validation (exploratory)",
        min_plausible=0.0, max_plausible=500.0,
        notes="Hessian-based sheet-detector targets perimysium / intermuscular septae geometry. Round-2 sandbox: no prior cohort validation in muscle US. Plausibility ceiling 500 is empirically calibrated: skimage.filters.sato output is unbounded eigenvalue-magnitude (depends on local gradient strength × σ scale); a 9-study × 3-frame balanced smoke produced ROI-mean values in 9–13 range with per-pixel max ~80. Initial round-2 ceiling of 1.0 was an aspiration-on-normalization that does not hold for raw Sato; the gate nulled 100% of frames pre-fix. 500.0 = ~5× empirical max; tighten in a future plausibility-tuning pass once per-cohort distribution is observed."),

    # --- Gabor energy mean (multi-orientation, multi-frequency bank) ---
    _mk(stem="gabor_energy_mean", family=Family.GABOR, unit="unitless",
        measurement_domain="texture",
        formula_summary="mean energy across 8-orientation × 3-frequency Gabor bank (frequencies 0.1/0.2/0.3) over muscle ROI",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="Stoyanov 2010 (longitudinal fiber-orientation enhancement; transverse use is exploratory)",
        min_plausible=0.0, max_plausible=10000.0,
        notes="Round-2 sandbox: published Gabor-on-muscle work is on longitudinal frames; transverse use is unanchored."),

    # --- Dubois TAI (texture anisotropy index, multi-angle rotation bank) ---
    # Goes into the existing GLCM_ANISOTROPY family alongside glcm_anisotropy_index;
    # both are anisotropy measures, just different definitions.
    _mk(stem="texture_anisotropy_index_dubois", family=Family.GLCM_ANISOTROPY, unit="unitless",
        measurement_domain="texture",
        formula_summary="std(I_θ) / mean(I_θ) across 18-orientation rotation bank (Dubois 2018)",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="medium",
        grounding_family="Dubois 2018 (lower TAI in dystrophy; documented gain-robustness vs gray-scale index)",
        min_plausible=0.0, max_plausible=2.0,
        notes="Distinct from glcm_anisotropy_index (the simpler 2-angle GLCM-contrast difference). Multi-angle integral with documented gain-robustness."),

    # --- Fascia decomposition (superficial epimysium vs deep fascia) ---
    # Computed by rf_quality.features.fascia_split.compute_fascia_split.
    # Path-(a) split-by-Y-coordinate above/below muscle centroid; sandbox
    # verdict per round-2 plan acknowledges the contiguity assumption.
    _mk(stem="fascia_superficial_thickness_mm", family=Family.GEOMETRY, unit="mm",
        measurement_domain="geometry",
        formula_summary="mean per-column pixel-count thickness of fascia mask pixels with Y < muscle centroid Y, in mm",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="low",
        grounding_family="Mechanism-only (no direct corpus citation in round-2 lit pass); user 2026-04-29 hypothesis. Closest analog: Paris 2022 (LIT-EI-2022B) on muscle-EI superficial-vs-deep gradient, which is structurally distinct from fascia decomposition.",
        min_plausible=0.0, max_plausible=10.0,
        notes="Path-(a) cheap split; assumes fascia mask is bimodally distributed in Y around the muscle centroid. Verify on 50 sample frames if signal looks suspicious."),
    _mk(stem="fascia_deep_thickness_mm", family=Family.GEOMETRY, unit="mm",
        measurement_domain="geometry",
        formula_summary="mean per-column pixel-count thickness of fascia mask pixels with Y ≥ muscle centroid Y, in mm",
        role="primary", aggregation_policy="agg_primary_with_anchor_passthrough",
        scanner_risk="low",
        grounding_family="Mechanism-only. Sibling of fascia_superficial_thickness_mm; user expects weaker cohort signal in healthy 20-40 because fibrotic remodeling at the deep fascia is pathology-specific.",
        min_plausible=0.0, max_plausible=10.0),
]


# --------- Young 2015 corrected siblings (per-stem-class dispatch) ---------
#
# Four dispatch classes, all emitting siblings under the canonical
# ``<stem>_fat_corrected_young_{lit,refit}`` names:
#
#   1. First-order moments of muscle EI — additive shift on the value:
#        sibling = raw + fat_thickness_mm × (cf_per_cm / 10 | cohort_ei_per_fat_mm_slope)
#      Auto-generated below from `_YOUNG_CORRECTED_PARENTS`.
#
#   2. Ratios with muscle EI — additive shift on numerator and/or denominator:
#        ei_to_fat_ratio: numerator only (fat_EI is the attenuator itself)
#        int_superficial_to_deep_ratio: both terms (trivial Young form)
#      Auto-generated below from `_YOUNG_CORRECTED_PARENTS`.
#
#   3. Ratio of moments (`int_cv_within = std / |mean|`) — component-wise
#      propagation: sibling = std / |mean + α × fat_thickness_mm|. Cannot use
#      additive shift (dimensionally invalid: EI-units shift on a unit-less
#      ratio). Explicit registry entries below; cascade dispatched via
#      :func:`features.young_correction.corrected_cv_within`.
#
#   4. Fraction above absolute threshold (`hyperechoic_frac_t200`) — per-frame
#      threshold shift: sibling = frac(raw_pixel >= 200 - α × fat_thickness_mm).
#      Cannot use additive shift (dimensionally invalid: EI-units shift on a
#      [0, 1] fraction). Explicit registry entries below; emitted at extraction
#      time via :func:`features.hyperechoic.compute_hyperechoic_fat_corrected_t200`.
#
# Raw versions retain their existing role and aggregation_policy (no downgrades);
# siblings get added, not substituted. Stage B → F decides which form wins per
# family on the data.
def _mk_corrected_sibling(parent: FeatureSpec, variant: str) -> FeatureSpec:
    """Build a corrected-sibling FeatureSpec inheriting most metadata from the parent.

    ``variant`` is "lit" or "refit". Plausibility max is widened to allow for the
    additive shift (typical α ≈ 4 EI/mm, fat ≤ 40 mm → max shift ≈ 160 EI; we
    widen ratio max by 2× and intensity max to 500). Min stays 0 because shift
    is non-negative.
    """
    label = "literature cf" if variant == "lit" else "cohort refit α"
    raw_max = parent.max_plausible if parent.max_plausible is not None else 1.0
    new_max = float(raw_max) * 2.0 if "ratio" in parent.stem else max(500.0, float(raw_max) * 2.0)
    return _mk(
        stem=f"{parent.stem}_fat_corrected_young_{variant}",
        family=parent.family,
        unit=parent.unit,
        measurement_domain=parent.measurement_domain,
        formula_summary=f"{parent.formula_summary}  +  Young 2015 additive correction (variant: {label})",
        role="support",  # corrected siblings start as support; can earn primary via stability (plan §4.1)
        aggregation_policy=parent.aggregation_policy,
        scanner_risk="medium",
        preferred=parent.preferred,
        grounding_family=f"Young 2015 — additive fat-attenuation correction ({label}); parent stem: {parent.stem}",
        min_plausible=0.0,
        max_plausible=new_max,
        notes=f"Corrected sibling of `{parent.stem}` via Young 2015 Eq. 6 (EI_corrected = EI_raw + α × fat_thickness_mm). expected_sign inherits from parent (additive shift preserves direction-with-quality).",
    )


# Stems whose corrected siblings are auto-generated by the additive cascade
# (kept in sync with the FIRST_ORDER and RATIOS tuples in
# `features/young_correction.py`).
#
# NOTE: ``int_cv_within`` (ratio of moments) and ``hyperechoic_frac_t200``
# (fraction above absolute threshold) are intentionally NOT in this tuple.
# The additive cascade is dimensionally invalid for them; their corrected
# siblings ship as explicit registry entries further below, computed via
# dedicated dispatch paths (component-wise propagation for cv_within;
# per-frame threshold shift for hyperechoic_frac_t200).
_YOUNG_CORRECTED_PARENTS = (
    # first-order moments
    "int_mean", "int_median", "int_p10", "int_p90",
    "superficial_int_mean", "mid_int_mean", "deep_int_mean",
    # ratios with muscle EI
    "ei_to_fat_ratio", "int_superficial_to_deep_ratio",
)

_parent_index = {s.stem: s for s in REGISTRY}
for _parent_stem in _YOUNG_CORRECTED_PARENTS:
    _parent = _parent_index.get(_parent_stem)
    if _parent is None:
        raise RuntimeError(f"Young-correction parent not in REGISTRY: {_parent_stem}")
    REGISTRY.append(_mk_corrected_sibling(_parent, "lit"))
    REGISTRY.append(_mk_corrected_sibling(_parent, "refit"))


# --------- int_cv_within corrected siblings — component-wise correction ---------
#
# Cascade-rule fix shipped 2026-04-30. The pre-fix implementation applied the
# additive shift directly to the CV value (``raw + α × fat_thickness``), which
# is dimensionally invalid: it adds an EI-units quantity to a dimensionless
# ratio. The siblings keep their canonical names and now emit dimensionally-
# correct values via :func:`features.young_correction.corrected_cv_within` as
# ``std / |mean + α × fat_thickness|``.
#
# In-place edit is appropriate because these are experimental sibling stems
# rather than load-bearing main features. The column-value change is recorded
# in ``feature_changelog.md`` and a future reader recomputing from the registry
# will get the corrected (post-fix) values; pre-fix values exist only on disk
# in prior runs' ``study_features.csv`` artifacts and are documented as such.
_cv_within_parent = _parent_index.get("int_cv_within")
if _cv_within_parent is None:
    raise RuntimeError("int_cv_within parent missing from REGISTRY (required for corrected siblings)")
for _variant in ("lit", "refit"):
    _label = "literature cf" if _variant == "lit" else "cohort refit α"
    REGISTRY.append(_mk(
        stem=f"int_cv_within_fat_corrected_young_{_variant}",
        family=_cv_within_parent.family,
        unit=_cv_within_parent.unit,
        measurement_domain=_cv_within_parent.measurement_domain,
        formula_summary=(
            f"std / |mean + α × fat_thickness_mm|  (component-wise Young correction; "
            f"variant: {_label}; in-place fix 2026-04-30 — replaces pre-fix additive "
            f"shift on the CV value, which was dimensionally invalid)"
        ),
        role="support",
        aggregation_policy=_cv_within_parent.aggregation_policy,
        scanner_risk="medium",  # corrected-CV interacts with gain via the constant shift
        preferred=_cv_within_parent.preferred,
        grounding_family=(
            f"Young 2015 — fat-attenuation correction propagated through CV components "
            f"({_label}); parent stem: int_cv_within"
        ),
        min_plausible=0.0,
        max_plausible=2.0,
        notes=(
            "Component-wise Young correction (in-place fix 2026-04-30). The pre-fix "
            "additive-shift formula was dimensionally invalid for this ratio-of-moments "
            "stem; this entry holds the corrected formula under the canonical sibling "
            "name. expected_sign inherits from parent (additive shift on the mean "
            "preserves direction-with-quality)."
        ),
    ))


# --------- hyperechoic_frac_t200 corrected siblings — per-frame threshold shift ---------
#
# Cascade-rule fix shipped 2026-04-30 (in-place edit). The pre-fix implementation
# applied the additive shift directly to the fraction value (``raw_fraction +
# α × fat_thickness``), which is dimensionally invalid: it adds an EI-units
# quantity to a fraction in [0, 1]. The siblings keep their canonical names and
# now emit dimensionally-correct values via the per-frame threshold shift:
# ``frac(raw_pixel >= 200 - α × fat_thickness_mm)`` — equivalent to applying
# Young's correction to every pixel and re-thresholding at 200, but computed
# more efficiently by shifting the threshold the other way.
#
# Computed in :func:`features.hyperechoic.compute_hyperechoic_fat_corrected_t200`
# at extraction time (requires raw pixel access; not derivable from summary
# stats). Cascade in `pipeline.py::_compute_young_corrected_siblings` does NOT
# handle these — the per-frame extractor emits them directly.
_hyperechoic_t200_parent = _parent_index.get("hyperechoic_frac_t200")
if _hyperechoic_t200_parent is None:
    raise RuntimeError("hyperechoic_frac_t200 parent missing from REGISTRY (required for corrected siblings)")
for _variant in ("lit", "refit"):
    _label = "literature cf" if _variant == "lit" else "cohort refit α"
    REGISTRY.append(_mk(
        stem=f"hyperechoic_frac_t200_fat_corrected_young_{_variant}",
        family=_hyperechoic_t200_parent.family,
        unit=_hyperechoic_t200_parent.unit,
        measurement_domain=_hyperechoic_t200_parent.measurement_domain,
        formula_summary=(
            f"frac(raw_pixel >= 200 - α × fat_thickness_mm)  (per-frame threshold shift; "
            f"variant: {_label}; in-place fix 2026-04-30 — replaces pre-fix additive "
            f"shift on the fraction value, which was dimensionally invalid)"
        ),
        role="support",
        aggregation_policy=_hyperechoic_t200_parent.aggregation_policy,
        scanner_risk="medium",  # threshold shift makes the corrected fraction depend on fat-thickness signal
        preferred=_hyperechoic_t200_parent.preferred,
        grounding_family=(
            f"Young 2015 — fat-attenuation correction applied via threshold shift on "
            f"absolute-intensity fraction-above-200 ({_label}); parent stem: "
            f"hyperechoic_frac_t200"
        ),
        min_plausible=0.0,
        max_plausible=1.0,
        notes=(
            "Per-frame threshold-shift Young correction (in-place fix 2026-04-30). "
            "The pre-fix additive-shift formula was dimensionally invalid for this "
            "fraction-above-threshold stem; this entry holds the corrected formula. "
            "expected_sign inherits from parent."
        ),
    ))


def registry_dict() -> dict[str, FeatureSpec]:
    return {s.stem: s for s in REGISTRY}


def preferred_primary_stems() -> list[str]:
    return [s.stem for s in REGISTRY if s.role == "primary" and s.preferred]


def stems_by_family(family: Family) -> list[str]:
    return [s.stem for s in REGISTRY if s.family == family]


def all_wide_columns(modality: str) -> list[str]:
    cols: list[str] = []
    for spec in REGISTRY:
        cols.extend(spec.emits_study_columns(modality))
    return cols

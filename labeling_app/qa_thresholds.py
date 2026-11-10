"""QA threshold registry — the single source of truth for every numeric
threshold the image-quality detectors use.

RF Image-Quality QA/QC Canonical Plan v0.2 (docs/rf_qa_canonical_plan_v0_1.md),
§ 12.4: "Thresholds are not magic numbers sprinkled across detector code. They
live in one place, with provenance." A detector looks a value up by name; no
detector hard-codes a number. `grep` for any QA threshold literal should match
exactly one line — its registry entry here (the § 12.4 single-source rule).

Two provenance tiers — the **Threshold-tier** axis of § 11.1, deliberately
distinct from the Tier-0/1/2 *detector-build* axis:

  * Threshold-tier A — universal, not cohort-derived. The value comes from
    signal physics (the 8-bit saturation rail), a fixed geometric convention
    (the N=1 edge-clip ring), a reused already-calibrated pipeline constant
    (the femur central-zone margin), or a scale-free statistical standard (the
    modified Z-score outlier cut) — never fitted to this cohort. Ships
    trustworthy on day one; `calibrated_on` stays None by design.

  * Threshold-tier B — cohort-calibrated. The value depends on scale, cohort,
    or implementation specifics and must be fitted on data (§ 11.2's five-step
    process). It ships here as a *starting point only*: `calibrated_on` is None
    until the § 11.2 calibration fills it. A Tier-B detector renders
    "pending calibration" (§ 8.6) and does NOT ship a verdict against an
    un-calibrated number — see the convention note below.

Convention for an un-calibrated Tier-B `value`:
  * Where the plan names a sensible starting point (§ 5.2 — `wrong_site`
    rf_fraction 0.5, `subq_fat_compressed` 3 mm, the saturation / dark-clip
    fractions), that number is used: it seeds the § 11.2 calibration and
    documents intent.
  * Where the plan names none (the scale-/cohort-dependent unit-free metrics
    — fascia-band Tenengrad, detrended-residual σ, FFT comb magnitude, …),
    `value` is `float("nan")`: honestly "no number exists yet", and because a
    NaN comparison is always False a detector that (wrongly) fired against it
    would simply never fire — a safety belt behind the § 8.6 "pending
    calibration" gate.

In every Tier-B case the live signal that the value is not yet trustworthy is
`calibrated_on is None` (equivalently `tier == "B" and calibrated_on is None`).
Build-Step 2b's calibration is a *data-update* that fills `calibrated_on`,
`cohort_*`, and `sens/spec_at_value` — never a code change to a detector
(§ 12.4). The calibration script (§ 11.4) writes proposed updates to a diff
file and never mutates this registry; promotion is a separate human commit.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal, Optional, Union

from labeling_app.measurements import CONTRACT_VERSION


@dataclass(frozen=True)
class ThresholdEntry:
    """One detector threshold + its calibration provenance (§ 12.4).

    The field set is the canonical § 12.4 schema, verbatim. `value` is the
    number a detector resolves; the remaining fields record where it came
    from so a deployed accuracy claim can be audited (§ 11.0 publish bar).
    """
    value: Union[float, int]
    units: str
    tier: Literal["A", "B"]              # § 11.1 Threshold-tier nomenclature
    calibrated_on: Optional[str]         # ISO date; None for Tier-A / un-calibrated Tier-B
    cohort_id: Optional[str]             # e.g. "v1_cohort_319studies"; None for Tier-A
    cohort_n_frames: Optional[int]
    sens_at_value: Optional[float]       # held-out validation, § 11.2 step 4
    spec_at_value: Optional[float]
    calibration_report_path: Optional[str]
    contract_version: str                # which contract version introduced this value

    @property
    def is_calibrated(self) -> bool:
        """True once § 11.2 calibration has set a trustworthy value.

        Tier-A entries are physics-grounded and need no calibration, so they
        count as calibrated. A Tier-B entry is calibrated only once
        `calibrated_on` is filled — until then it is a starting point and a
        detector keyed on it renders "pending calibration" (§ 8.6).
        """
        return self.tier == "A" or self.calibrated_on is not None

    @property
    def is_starting_point(self) -> bool:
        """True for a Tier-B entry still on its un-calibrated starting value."""
        return self.tier == "B" and self.calibrated_on is None


def _tier_a(value: Union[float, int], units: str) -> ThresholdEntry:
    """A physics-grounded Threshold-tier A entry — no cohort, no calibration."""
    return ThresholdEntry(
        value=value, units=units, tier="A",
        calibrated_on=None, cohort_id=None, cohort_n_frames=None,
        sens_at_value=None, spec_at_value=None, calibration_report_path=None,
        contract_version=CONTRACT_VERSION,
    )


def _tier_b(value: Union[float, int], units: str) -> ThresholdEntry:
    """An un-calibrated Threshold-tier B starting point.

    `calibrated_on=None` is the "pending calibration" signal (§ 8.6); § 11.2's
    five-step calibration fills `calibrated_on`, `cohort_*`, and the held-out
    sensitivity / specificity. `value` is a starting point only (see the
    module docstring's NaN convention).
    """
    return ThresholdEntry(
        value=value, units=units, tier="B",
        calibrated_on=None, cohort_id=None, cohort_n_frames=None,
        sens_at_value=None, spec_at_value=None, calibration_report_path=None,
        contract_version=CONTRACT_VERSION,
    )


# `nan` marks a Tier-B threshold with no physics-grounded starting point — the
# § 11.2 calibration sets the real number; until then the detector renders
# "pending calibration" (§ 8.6) and the NaN never-fires property is the belt.
_NAN = float("nan")


THRESHOLDS: dict[str, ThresholdEntry] = {
    # -- Threshold-tier A — physics-grounded universals (§ 11.1) ------------
    # The 8-bit rails are genuine signal physics — cohort-independent. Shared
    # by saturation_high_global and saturation_high_regional (§ 5.2).
    "saturation_high__pixel_value_min": _tier_a(250, "grayscale (8-bit DN)"),
    "dark_clip_high__pixel_value_max": _tier_a(5, "grayscale (8-bit DN)"),
    # Edge-clip ring width — the existing `touches_edge` behaviour (N=1), used
    # by both edge_clip_lateral and edge_clip_vertical (§ 5.2, § 11.1).
    "edge_clip__boundary_ring_px": _tier_a(1, "pixels (boundary-ring width)"),
    # femur_off_central_zone reuses the QMT pipeline's central-zone margin —
    # QMT_CENTRAL_ZONE_MARGIN_PX = 15 (labeling_app/measurements.py), already
    # calibrated for the QMT path (§ 5.2 / § 11.1).
    "femur_central_zone__margin_px": _tier_a(15, "pixels (central-zone inset)"),
    # wrong_site — the v3 redesign (commit 246e2ec, Akadir 2026-05-24 § 4)
    # replaced the rf_fraction class-composition ratio with a geometric
    # rf_column_coverage rule (fraction of image columns carrying ≥1 cleaned-RF
    # pixel; the detector fires below the cut). The 0.5 "half of columns" cut is
    # a fixed geometric convention — like the edge-clip ring — ratified by
    # Akadir, not cohort-fitted: Threshold-tier A. (Supersedes the old
    # wrong_site__rf_fraction_min / __min_quad_px Tier-B entries, removed below.)
    "wrong_site__rf_column_coverage_min": _tier_a(
        0.5, "fraction of image columns with ≥1 cleaned-RF pixel"),
    # anisotropy_dropout_superficial (E4, 2026-06-04) — the superficial fascia (the
    # bright line on top of the muscle) drops out where the beam is off-perpendicular.
    # continuity_fraction = the fraction of muscle-top columns carrying a fascia
    # ridge (segmented or Frangi-recovered); the detector fires below the cut. 0.70
    # is a defensible geometric line (a >30% dropout span is a real plane problem),
    # pending Akadir ratification + Se/Sp validation on Round-3 labelled dropout;
    # Tier-A so it is ALIVE without the calibration loop.
    "anisotropy_dropout_superficial__continuity_fraction_min": _tier_a(
        0.70, "fraction of muscle-top columns with a fascia ridge"),

    # -- Threshold-tier B — cohort-calibrated starting points (§ 11.1) ------
    # Saturation / dark-clip ROI-fraction cut-offs. The pixel rails above are
    # physics; these fractions are NOT (the 2% is borrowed from the QMT
    # muscle-occupancy tolerance — § 11.1 honesty caveat, limitation L3), so
    # they are Tier-B. Audit note: the technical audit recommends tightening
    # the global cut-off toward 1% — that is a calibration call (§ 5.2).
    "saturation_high_global__roi_fraction_max": _tier_b(0.02, "fraction of ROI pixels"),
    # Raised 0.02 -> 0.10 (Akadir 2026-06-10): the 2% cut fired on MILD darkness that
    # does not corrupt the (fascia-based) RF geometry — an adjacent-frame test showed
    # RF area unchanged across a dark/clean boundary. At 10% it flags only SUBSTANTIAL
    # under-gain, where the ECHO features (int_cv_within / rf_echo) genuinely degrade;
    # this "parks" the detector — present + still gating echo, but no longer dominant.
    "dark_clip_high__roi_fraction_max": _tier_b(0.10, "fraction of ROI pixels"),
    # Regional saturation — >10% within any block; the block edge is 32 px
    # (the technical audit's 32×32 window, § 5.2).
    "saturation_high_regional__block_fraction_max": _tier_b(0.10, "fraction of block pixels"),
    "saturation_high_regional__block_size_px": _tier_b(32, "pixels (square block edge)"),
    # tgc_banding — σ of the detrended row-mean residual, muscle rows only
    # (§ 5.2 rewrite). Scale-dependent → no physics starting point.
    "tgc_banding__detrended_residual_sigma_max": _tier_b(_NAN, "DN (detrended row-mean residual σ)"),
    # motion_blur — fascia-band Tenengrad; unit-free, depends on probe / gain /
    # target resolution (§ 5.2 rewrite). A LOW value fires the flag.
    "motion_blur__fascia_band_tenengrad_min": _tier_b(_NAN, "fascia-band Tenengrad (unit-free)"),
    # air_gap_reverberation — FFT comb peak in the segmented skin–fascia gap.
    # The 3–8 px period band is the named reverberation periodicity (§ 5.2);
    # it is Tier-B because it depends on probe footprint.
    "air_gap_reverberation__comb_peak_magnitude_min": _tier_b(_NAN, "FFT comb-peak magnitude (unit-free)"),
    "air_gap_reverberation__comb_period_px_min": _tier_b(3, "pixels (reverberation period)"),
    "air_gap_reverberation__comb_period_px_max": _tier_b(8, "pixels (reverberation period)"),
    # near_field_clutter — high-frequency non-periodic clutter energy in the
    # segmented near-field layers (skin → fat) (§ 5.2).
    "near_field_clutter__clutter_energy_max": _tier_b(_NAN, "near-field clutter energy (unit-free)"),
    # vessel_shadow_in_roi — a near-zero-intensity vertical column (§ 5.2).
    "vessel_shadow_in_roi__column_darkness_max": _tier_b(_NAN, "DN (mean column intensity)"),
    # subq_fat_compressed — median central-zone fat thickness. 3 mm is "plausible
    # but not physics-grounded" (§ 5.2); single-frame discrimination is
    # intrinsically weak (limitation L8) — do not deploy without § 11 calibration.
    "subq_fat_compressed__central_fat_thickness_mm_min": _tier_b(3.0, "mm (median central-zone fat thickness)"),
    # wrong_site — its cut is now the Tier-A wrong_site__rf_column_coverage_min
    # in the Tier-A block above (the v3 geometric rule); the superseded
    # rf_fraction / min_quad_px Tier-B entries were removed in the 2026-05-24
    # redesign.
    # segmentation_low_confidence — mask-boundary entropy from the segmenter
    # softmax (§ 5.4). The signal is [Active] (predict_mask exposes it); the
    # threshold is Tier-B — no entropy number ships un-calibrated (§ 5.4).
    "segmentation_low_confidence__boundary_entropy_max": _tier_b(_NAN, "nats (mean entropy over the mask-boundary band)"),

    # -- The aggregate-tier rule (§ 6.3) ------------------------------------
    # The fraction of the eight § 6.2 gating-table measurements that must be
    # `valid` (with every headline-4 valid) for an aggregate "valid" badge.
    # 0.60 of 8 → ceil(4.8) → 5-of-8. Explicitly Threshold-tier B in § 6.3 —
    # the starting figure is calibrated against the cohort distribution in
    # Build-Step 2. image_quality_aggregate_tier() reads it from here.
    "aggregate_tier__headline_secondary_valid_fraction_min": _tier_b(
        0.60, "fraction of the 8 gating-table measurements valid"),

    # -- frame_rf_consistency — the sweep-level rf-thickness outlier gate ---
    # RF QA plan § 3.4 / § 11.1 / § 14 Step 4 (Build-Step 2 Phase 3 item 9a):
    # promotes the passive `frame_rf_consistency` σ metric to an active gate —
    # a frame whose `rf_thickness_mm` is a robust outlier among its sweep
    # siblings is dropped from the per-run `rf_thickness_mm` pool (aggregation.py
    # `_rf_consistency_report`). The test is the Iglewicz-Hoaglin modified
    # Z-score `0.6745·(x−median)/MAD`; the cut is its published standard, 3.5.
    #
    # Threshold-tier A. § 11.1 defines Tier-A as "universal, not cohort-derived"
    # and its own honesty caveat is explicit that "universal" — not literally
    # "physics" — is the test. The modified-Z cut meets it cleanly: scale-
    # invariant by construction, a published statistical standard, not a cohort
    # fit (a cleaner Tier-A entry than the saturation ROI-fractions already
    # there). Like every Tier-A value it is covered by the § 12.6 recurring
    # Threshold-tier-A audit.
    #
    # The gate makes a *pooling* decision, not a frame-quality verdict —
    # dropping a far-from-median frame is sound robust estimation whether or
    # not that frame is image-quality-good; a flagged frame may be a genuine
    # anatomical extreme, so it is a "potential sweep outlier," never a defect.
    #
    # Two named Build-Step 2b calibration items (limitations, not blockers):
    # tiny-MAD — a very tight sweep shrinks the k·MAD window so a modestly-off
    # frame over-fires (bounded today: the per-run anchor_primary policy caps
    # the headline effect at sub-mm; `mad_mm`/`range_mm` are recorded so 2b
    # fits a MAD floor from data); and small-N — the modified Z-score is coarse
    # at the 4-frame floor, catching only gross outliers there. `min_frames`=4
    # is the floor below which a robust outlier statement is not meaningful —
    # you cannot pick the odd one of 3; the cohort's 3-frame triples never
    # trigger the gate, by design.
    "frame_rf_consistency__modified_z_max": _tier_a(
        3.5, "Iglewicz-Hoaglin modified Z-score (MAD-based, unit-free)"),
    "frame_rf_consistency__min_frames": _tier_a(
        4, "frames (minimum sweep size for a robust outlier test)"),
}

# Detectors that reuse an already-calibrated pipeline threshold and so add no
# new registry entry:
#   * femur_out_of_frame — reuses the QMT pipeline's `femur_absent` cortex +
#     acoustic-shadow search (§ 5.2; Threshold-tier A by reuse). It has no
#     numeric threshold of its own; femur_off_central_zone's central-zone
#     margin is `femur_central_zone__margin_px` above.
#
# Tier-1 [Conditional] detectors — `oblique_plane` (Radon parallelism),
# `anisotropy_dropout_superficial` / `_deep`, and `deep_attenuation_high` — are
# deferred per the § 2.3 ratified Tier-1 deferral (Build-sequence Step 3, not
# Build-Step 2). Their Threshold-tier B entries are added when the one-page
# Tier-1 technical spec lands and the Step-2 failure-mode readout triggers
# Step 3 (§ 14). They are deliberately absent here so the registry honestly
# scopes to the Build-Step 2 working set.


def get(name: str) -> ThresholdEntry:
    """Resolve a threshold by registry name. Raises KeyError with the closest
    candidate names if the name is unknown — a typo in a detector should fail
    loud, not silently read a wrong number."""
    try:
        return THRESHOLDS[name]
    except KeyError:
        near = sorted(k for k in THRESHOLDS if name.split("__")[0] in k)
        raise KeyError(
            f"unknown QA threshold {name!r}; "
            f"{'did you mean one of ' + repr(near) + '?' if near else 'no near match'}"
        ) from None


def uncalibrated_tier_b() -> tuple[str, ...]:
    """The Tier-B thresholds still on their un-calibrated starting value.

    The Build-Step 2b calibration worklist (§ 11.2) and the § 8.6 "pending
    calibration" UI state both key off this set.
    """
    return tuple(name for name, e in THRESHOLDS.items() if e.is_starting_point)


def _self_check() -> None:
    """Import-time invariants — a malformed registry should fail loudly."""
    for name, e in THRESHOLDS.items():
        assert e.tier in ("A", "B"), f"{name}: bad tier {e.tier!r}"
        assert e.contract_version == CONTRACT_VERSION, f"{name}: stale contract_version"
        if e.tier == "A":
            # Physics-grounded — must carry a real (non-NaN) value and need no
            # calibration provenance.
            assert not (isinstance(e.value, float) and math.isnan(e.value)), \
                f"{name}: Tier-A must have a concrete value"
            assert e.calibrated_on is None, f"{name}: Tier-A carries no calibration date"


_self_check()

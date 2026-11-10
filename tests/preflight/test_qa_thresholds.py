"""Build-Step 2 Phase 1 (item 4) — the QA threshold registry.

docs/rf_qa_canonical_plan_v0_1.md § 12.4 / build_step_2_plan.md item 4: every
detector threshold resolves to one registry entry, and no QA threshold literal
lives outside the registry (the single-source rule). These tests guard the
registry's schema and the Threshold-tier A / B provenance invariants.
"""
from __future__ import annotations

import math

import pytest

from labeling_app.measurements import CONTRACT_VERSION
from labeling_app.qa_thresholds import (
    THRESHOLDS, ThresholdEntry, get, uncalibrated_tier_b,
)


def test_every_entry_is_a_threshold_entry_with_a_valid_tier() -> None:
    assert THRESHOLDS, "registry is empty"
    for name, e in THRESHOLDS.items():
        assert isinstance(e, ThresholdEntry), name
        assert e.tier in ("A", "B"), f"{name}: tier {e.tier!r}"
        assert e.contract_version == CONTRACT_VERSION, f"{name}: stale contract_version"


def test_tier_a_entries_are_physics_grounded_and_calibration_free() -> None:
    """Threshold-tier A — physics-grounded universal (§ 11.1): a concrete
    (non-NaN) value, no cohort, no calibration date."""
    tier_a = {n: e for n, e in THRESHOLDS.items() if e.tier == "A"}
    assert tier_a, "expected the pixel rails + edge ring + central margin"
    for name, e in tier_a.items():
        assert not (isinstance(e.value, float) and math.isnan(e.value)), name
        assert e.calibrated_on is None, f"{name}: Tier-A carries no calibration date"
        assert e.cohort_id is None and e.cohort_n_frames is None, name
        assert e.is_calibrated and not e.is_starting_point, name


def test_physics_rail_values_match_the_plan() -> None:
    """The Threshold-tier A values § 11.1 names explicitly."""
    assert THRESHOLDS["saturation_high__pixel_value_min"].value == 250
    assert THRESHOLDS["dark_clip_high__pixel_value_max"].value == 5
    assert THRESHOLDS["edge_clip__boundary_ring_px"].value == 1
    assert THRESHOLDS["femur_central_zone__margin_px"].value == 15


def test_tier_b_entries_ship_uncalibrated_at_phase_1() -> None:
    """Build-Step 2 Phase 1 ships every Threshold-tier B value as an
    un-calibrated starting point — calibrated_on stays None until Build-Step 2b
    (§ 11.2). `calibrated_on is None` is the § 8.6 "pending calibration" gate."""
    tier_b = {n: e for n, e in THRESHOLDS.items() if e.tier == "B"}
    assert tier_b
    for name, e in tier_b.items():
        assert e.calibrated_on is None, f"{name}: not calibrated at Phase 1"
        assert e.is_starting_point and not e.is_calibrated, name
    assert set(uncalibrated_tier_b()) == set(tier_b)


def test_named_starting_points_match_the_plan() -> None:
    """The Threshold-tier B starting points § 5.2 names explicitly."""
    assert THRESHOLDS["subq_fat_compressed__central_fat_thickness_mm_min"].value == 3.0
    assert THRESHOLDS["saturation_high_global__roi_fraction_max"].value == 0.02
    assert THRESHOLDS["dark_clip_high__roi_fraction_max"].value == 0.10   # raised from 0.02 (parked)
    assert THRESHOLDS["saturation_high_regional__block_fraction_max"].value == 0.10
    assert THRESHOLDS["saturation_high_regional__block_size_px"].value == 32


def test_wrong_site_column_coverage_cut_is_tier_a() -> None:
    """v3 redesign (commit 246e2ec, Akadir 2026-05-24): wrong_site's
    rf_column_coverage cut is a geometric design constant — Threshold-tier A,
    resolved from the registry (not hardcoded). The old rf_fraction /
    min_quad_px Tier-B entries were removed."""
    e = THRESHOLDS["wrong_site__rf_column_coverage_min"]
    assert e.value == 0.5 and e.tier == "A" and e.calibrated_on is None
    assert "wrong_site__rf_fraction_min" not in THRESHOLDS
    assert "wrong_site__min_quad_px" not in THRESHOLDS


def test_aggregate_tier_floor_is_a_registered_tier_b_threshold() -> None:
    """The § 6.3 aggregate-Valid fraction floor lives in the registry (§ 6.3
    tags it Threshold-tier B) so image_quality_aggregate_tier sources it."""
    e = THRESHOLDS["aggregate_tier__headline_secondary_valid_fraction_min"]
    assert e.value == 0.60 and e.tier == "B"


def test_build_step_2_detectors_each_resolve_a_threshold() -> None:
    """build_step_2_plan.md item 4 done-when: every Build-Step 2 detector
    (Phase 2, plan items 5–6) that carries a numeric threshold of its own has
    a registry entry. `femur_out_of_frame` is the documented exception — it
    reuses the QMT pipeline's calibrated `femur_absent` detector."""
    for name in (
        "saturation_high__pixel_value_min",
        "saturation_high_global__roi_fraction_max",
        "saturation_high_regional__block_fraction_max",
        "dark_clip_high__pixel_value_max",
        "dark_clip_high__roi_fraction_max",
        "edge_clip__boundary_ring_px",
        "femur_central_zone__margin_px",
        "tgc_banding__detrended_residual_sigma_max",
        "motion_blur__fascia_band_tenengrad_min",
        "air_gap_reverberation__comb_peak_magnitude_min",
        "near_field_clutter__clutter_energy_max",
        "vessel_shadow_in_roi__column_darkness_max",
        "subq_fat_compressed__central_fat_thickness_mm_min",
        "wrong_site__rf_column_coverage_min",
        "segmentation_low_confidence__boundary_entropy_max",
    ):
        assert name in THRESHOLDS, f"missing registry entry: {name}"


def test_get_resolves_and_raises_loud_on_a_typo() -> None:
    assert get("motion_blur__fascia_band_tenengrad_min") is \
        THRESHOLDS["motion_blur__fascia_band_tenengrad_min"]
    with pytest.raises(KeyError):
        get("motion_blur__not_a_real_threshold")

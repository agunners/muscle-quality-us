"""Lock the per-detector calibration spec invariants (`labeling_app/qa_calibration_spec.py`).

The module's `_self_check()` runs at import; these tests pin the contract so a
future malformed edit fails in CI, and assert the Round-1 scope + the
Akadir-ratified semantic anchors (operator question per detector; score-reveal
policy; no detector pre-judged into mechanism review).
"""
from __future__ import annotations

import dataclasses

import pytest

from labeling_app import qa_calibration_spec as spec


ROUND1 = (
    "motion_blur", "tgc_banding", "air_gap_reverberation", "near_field_clutter",
    "vessel_shadow_in_roi", "dark_clip_high", "segmentation_low_confidence",
)


def test_round1_scope_is_exactly_the_seven() -> None:
    assert spec.round1_detectors() == ROUND1
    assert set(spec.CALIBRATION_SPEC) == set(ROUND1)


def test_excluded_detectors_raise_with_a_reason() -> None:
    """subq_fat_compressed + the two saturation detectors are out of scope and
    a lookup gives the WHY, not a bare KeyError (§ 3)."""
    for excluded in ("subq_fat_compressed", "saturation_high_global",
                     "saturation_high_regional"):
        with pytest.raises(KeyError) as ei:
            spec.get(excluded)
        assert "out of Round-1 scope" in str(ei.value)


def test_unknown_detector_raises_with_near_match() -> None:
    with pytest.raises(KeyError) as ei:
        spec.get("motion_blurr")
    assert "motion_blur" in str(ei.value)


def test_every_operator_question_is_plain_and_has_no_raw_code() -> None:
    """Anchor A — the operator must never see the detector code; the question
    must read as a question. A code leak or a non-question fails here."""
    for name, s in spec.CALIBRATION_SPEC.items():
        assert name not in s.operator_question, f"{name}: code leaked into question"
        assert "_" not in s.operator_question.replace("high-frequency", ""), (
            f"{name}: snake_case identifier likely leaked: {s.operator_question!r}")
        q = s.operator_question.rstrip()
        assert q.endswith("?") or q.endswith(")"), f"{name}: not a question"


def test_score_reveal_visualised_only_when_score_is_shown() -> None:
    """score_reveal='visualised' is reserved for detectors whose visual aid IS
    the score (confidence heatmap or an auxiliary plot). tgc_banding (residual
    plot) and segmentation_low_confidence (confidence wash) are the two."""
    visualised = {n for n, s in spec.CALIBRATION_SPEC.items()
                  if s.score_reveal == "visualised"}
    assert visualised == {"tgc_banding", "segmentation_low_confidence"}
    for n in visualised:
        s = spec.CALIBRATION_SPEC[n]
        assert "confidence" in s.default_layers or s.auxiliary_widget is not None


def test_no_detector_pre_judged_into_mechanism_review() -> None:
    """Anchor C (Akadir 2026-05-28) — every Round-1 detector starts at
    pending_calibration; none is pre-flagged into mechanism review."""
    for name, s in spec.CALIBRATION_SPEC.items():
        assert s.detector_status == "pending_calibration", (
            f"{name}: should start pending_calibration, not {s.detector_status}")


def test_motion_blur_default_layers_hide_the_fill() -> None:
    """Anchor A visual half — motion_blur's signal is raw edge sharpness; the
    colored fill must NOT be a default layer (it would hide the signal). The
    contour is default; the full overlay is operator-toggleable only."""
    s = spec.get("motion_blur")
    assert "segmentation_contour" in s.default_layers
    assert "segmentation_full" not in s.default_layers
    assert "segmentation_full" in s.toggleable_layers


def test_segmentation_low_confidence_shows_the_heatmap_by_default() -> None:
    """The confidence wash IS this detector's score visualisation (§ 8.7)."""
    s = spec.get("segmentation_low_confidence")
    assert "confidence" in s.default_layers
    assert s.score_reveal == "visualised"


def test_fit_objective_and_sens_floor_are_coupled() -> None:
    for name, s in spec.CALIBRATION_SPEC.items():
        if s.fit_objective == "sens_target_at_spec":
            assert s.sens_target_at_spec_floor is not None
            sens, spc = s.sens_target_at_spec_floor
            assert 0.0 < sens <= 1.0 and 0.0 < spc <= 1.0
        else:
            assert s.sens_target_at_spec_floor is None


def test_auxiliary_widgets_are_registered() -> None:
    for name, s in spec.CALIBRATION_SPEC.items():
        if s.auxiliary_widget is not None:
            assert s.auxiliary_widget in spec.VALID_AUX_WIDGETS


def test_specs_are_frozen() -> None:
    """The spec dataclass is immutable — a runtime mutation must raise (the
    spec is a contract, not mutable state)."""
    s = spec.get("motion_blur")
    with pytest.raises(dataclasses.FrozenInstanceError):
        s.operator_question = "tampered"  # type: ignore[misc]


def test_self_check_catches_a_malformed_spec() -> None:
    """A code leak into the operator question must trip the self-check — proving
    the import-time guard actually guards."""
    bad = dataclasses.replace(
        spec.get("motion_blur"),
        operator_question="does motion_blur fire here?",  # raw code leak
    )
    patched = dict(spec.CALIBRATION_SPEC)
    patched["motion_blur"] = bad
    orig = spec.CALIBRATION_SPEC
    try:
        spec.CALIBRATION_SPEC = patched  # type: ignore[attr-defined]
        with pytest.raises(AssertionError):
            spec._self_check()
    finally:
        spec.CALIBRATION_SPEC = orig  # type: ignore[attr-defined]

"""N3 percentile phrasing — peer-relative phrase bands + per-feature sentiment.

`percentile_phrase(pctile, feature) -> (phrase, sentiment)` feeds F7 (B2 metric
card direction-of-better) and F8 (B3 cohort-viz half-band). The two things this
must guarantee: the phrase is *stable* (deterministic — same input, same
output, every call) and the sentiment is *correct per feature family*
(directional features swing concern↔strength; geometry stays neutral).
"""
from __future__ import annotations

import pytest

from preflight_helpers.cohort_placement import percentile_phrase


# ---- Stability -------------------------------------------------------------

@pytest.mark.parametrize("pctile", [None, 0.0, 9.99, 10.0, 25.0, 50.0, 74.9,
                                     75.0, 90.0, 100.0, 11.0])
@pytest.mark.parametrize("feature", ["star_ratio", "rf_thickness_mm",
                                      "int_cv_within", "full_thickness_q50_mm"])
def test_phrase_is_deterministic(pctile, feature) -> None:
    a = percentile_phrase(pctile, feature)
    b = percentile_phrase(pctile, feature)
    assert a == b
    phrase, sentiment = a
    assert isinstance(phrase, str) and phrase
    assert sentiment in {"concern", "neutral", "strength"}


# ---- Phrase bands ----------------------------------------------------------

@pytest.mark.parametrize("pctile,expected", [
    (0.0,   "bottom 10% of peers"),
    (5.0,   "bottom 10% of peers"),
    (9.99,  "bottom 10% of peers"),
    (10.0,  "lower quartile of peers"),
    (24.99, "lower quartile of peers"),
    (25.0,  "typical-to-lower"),
    (49.99, "typical-to-lower"),
    (50.0,  "typical-to-upper"),
    (74.99, "typical-to-upper"),
    (75.0,  "upper quartile of peers"),
    (89.99, "upper quartile of peers"),
    (90.0,  "top 10% of peers"),
    (100.0, "top 10% of peers"),
])
def test_phrase_band_boundaries(pctile, expected) -> None:
    # Use a neutral feature so only the phrase band is under test.
    phrase, _ = percentile_phrase(pctile, "rf_thickness_mm")
    assert phrase == expected


def test_pctile_is_clamped() -> None:
    # Out-of-range percentiles clamp to the nearest band rather than crashing.
    assert percentile_phrase(105.0, "rf_thickness_mm")[0] == "top 10% of peers"
    assert percentile_phrase(-3.0, "rf_thickness_mm")[0] == "bottom 10% of peers"


def test_none_pctile_is_unavailable() -> None:
    phrase, sentiment = percentile_phrase(None, "star_ratio")
    assert phrase == "placement unavailable"
    assert sentiment == "neutral"


# ---- Per-feature sentiment -------------------------------------------------

@pytest.mark.parametrize("feature", ["star_ratio", "int_cv_within"])
def test_directional_features_swing_with_percentile(feature) -> None:
    """Higher-is-better features: bottom quartile = concern, top = strength.
    (muscle_fat_ratio was here; demoted 2026-06-10 → neutral, no longer placed.)"""
    assert percentile_phrase(5.0, feature)[1] == "concern"
    assert percentile_phrase(24.99, feature)[1] == "concern"
    assert percentile_phrase(25.0, feature)[1] == "neutral"
    assert percentile_phrase(50.0, feature)[1] == "neutral"
    assert percentile_phrase(74.99, feature)[1] == "neutral"
    assert percentile_phrase(75.0, feature)[1] == "strength"
    assert percentile_phrase(95.0, feature)[1] == "strength"


@pytest.mark.parametrize("feature", ["rf_thickness_mm",
                                      "qmt_mm", "full_thickness_q50_mm",
                                      "full_max_thickness_mm",
                                      "visible_rf_area_mm2"])
@pytest.mark.parametrize("pctile", [2.0, 25.0, 50.0, 80.0, 99.0])
def test_geometry_features_are_always_neutral(feature, pctile) -> None:
    """Big is not better for geometry — sentiment stays neutral at any percentile."""
    assert percentile_phrase(pctile, feature)[1] == "neutral"

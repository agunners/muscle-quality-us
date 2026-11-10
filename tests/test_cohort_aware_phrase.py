"""The cohort-aware green direction line must not overclaim: a 'strength' only
when EVERY available cohort agrees; 'concern' if ANY cohort is a concern; the
phrase flags a mixed read. Fixes the defect where a green "upper quartile" sat
above a 3-row box with a p68 row (Akadir note 6, 2026-06-04)."""
from preflight_helpers.cohort_placement import cohort_aware_phrase


def test_mixed_cohorts_do_not_overclaim_strength():
    # STAR (higher-better): overall p84 (strength), age p68 (neutral), sex p89 (strength)
    phrase, sent = cohort_aware_phrase(84, 68, 89, "star_ratio")
    assert sent == "neutral", "must not claim strength when one cohort is mid-pack"
    assert "mixed" in phrase


def test_all_cohorts_strength():
    phrase, sent = cohort_aware_phrase(84, 80, 89, "star_ratio")
    assert sent == "strength"
    assert "mixed" not in phrase


def test_any_cohort_concern_dominates():
    _, sent = cohort_aware_phrase(84, 20, 89, "star_ratio")   # age p20 = concern
    assert sent == "concern"


def test_lower_is_better_inverts():
    # fat thickness (lower-better): high percentile = concern, low = strength
    _, sent_hi = cohort_aware_phrase(80, 80, 80, "fat_thickness_q50_mm")
    assert sent_hi == "concern"
    _, sent_lo = cohort_aware_phrase(10, 10, 10, "fat_thickness_q50_mm")
    assert sent_lo == "strength"


def test_unavailable():
    phrase, sent = cohort_aware_phrase(None, None, None, "star_ratio")
    assert sent == "neutral"
    assert "unavailable" in phrase

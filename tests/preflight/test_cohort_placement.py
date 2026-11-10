"""Cohort placement reproduces triple_placement.json numbers for cohort cases."""
from __future__ import annotations

import json

import pytest

from preflight_helpers import env
from preflight_helpers.cohort_placement import cohort_box, percentile


@pytest.fixture(scope="module")
def triple_placement():
    with open(env.TRIPLE_PLACEMENT) as f:
        return json.load(f)


@pytest.mark.integration
def test_agz855_star_is_not_measurable(triple_placement) -> None:
    # AGZ855 is a no_femur study — every rater's QMT was a no_femur_proxy, so
    # under the Tier-1 cohort gate (cohort_stats_precompute v2.3) it has no
    # standard STAR. The placement must be the graceful None-block, NOT a fake
    # bottom-of-cohort number.
    expected = triple_placement["AGZ855"]["star_ratio"]
    assert expected["value"] is None
    p = percentile("star_ratio", expected["value"], age=45, sex="M")
    assert p.overall is None and p.age_matched is None and p.sex_matched is None


@pytest.mark.integration
def test_acl762_star_overall_age(triple_placement) -> None:
    # ACL762 IS a standard_qmt study — its STAR places normally. Sex is
    # unknown for ACL762, so only overall + age-matched are present.
    expected = triple_placement["ACL762"]["star_ratio"]
    assert expected["value"] is not None
    p = percentile("star_ratio", expected["value"], age=29, sex=None)
    # Within 0.5 percentile — the cohort_summary is a different sampling of
    # the same dataframe; rounding can shift a hair.
    assert abs(p.overall["pctile"] - expected["pctile_overall"]) < 0.5
    assert p.overall["n"] == expected["n_overall"]
    assert abs(p.age_matched["pctile"] - expected["pctile_age_matched"]) < 0.5


@pytest.mark.integration
def test_agz855_full_thickness_q50(triple_placement) -> None:
    expected = triple_placement["AGZ855"]["full_thickness_q50_mm"]
    p = percentile("full_thickness_q50_mm", expected["value"], age=45, sex="M")
    assert abs(p.overall["pctile"] - expected["pctile_overall"]) < 0.5
    assert abs(p.age_matched["pctile"] - expected["pctile_age_matched"]) < 0.5


@pytest.mark.integration
def test_missing_sex_returns_none(triple_placement) -> None:
    p = percentile("star_ratio", 1.0, age=45, sex=None)
    assert p.overall is not None
    assert p.age_matched is not None
    assert p.sex_matched is None


# ---- F8 cohort_box — box-plot stats for the cohort-placement viz ----------

@pytest.mark.integration
def test_cohort_box_quantiles_are_ordered() -> None:
    box = cohort_box("rf_thickness_mm", age=45, sex="M")
    for key in ("overall", "age_matched", "sex_matched"):
        b = box[key]
        assert b is not None, key
        assert b["n"] > 0
        assert b["p10"] <= b["p25"] <= b["p50"] <= b["p75"] <= b["p90"]
        assert b["min"] <= b["p10"] and b["p90"] <= b["max"]


@pytest.mark.integration
def test_cohort_box_n_matches_percentile() -> None:
    # The box and the percentile marker must come from the SAME subsets, or
    # the marker can land outside its own box.
    box = cohort_box("star_ratio", age=45, sex="M")
    p = percentile("star_ratio", 1.0, age=45, sex="M")
    assert box["overall"]["n"] == p.overall["n"]
    assert box["age_matched"]["n"] == p.age_matched["n"]
    assert box["sex_matched"]["n"] == p.sex_matched["n"]


@pytest.mark.integration
def test_cohort_box_graceful_on_unknown_feature_and_sex() -> None:
    assert cohort_box("not_a_feature", age=45, sex="M") == {
        "overall": None, "age_matched": None, "sex_matched": None}
    box = cohort_box("rf_thickness_mm", age=45, sex=None)
    assert box["overall"] is not None
    assert box["sex_matched"] is None


# --------------------------------------------------------------------------- condition index
# Placement is a RANKING; the ordinal model's 0-100 score cannot carry one, because the logistic it
# passes through is flat at its ends. On the served 116-study reference the score takes 33 distinct
# values and pins 37 studies at exactly 100 — two of which sit at index percentiles p63 and p99.
# (Akadir 2026-08-04: "cohort placement is a ranking and we should do the raw ranking".)

def test_condition_index_orders_studies_the_score_reports_as_identical() -> None:
    """The whole point: two feature sets that both saturate the score must still be ordered."""
    from preflight_helpers import cohort_placement as cp
    cols, mu, sd, beta = cp._model_artifact()
    good = dict(zip(cols, [m - 3 * s for m, s in zip(mu, sd)]))
    better = dict(zip(cols, [m - 6 * s for m, s in zip(mu, sd)]))
    a, b = cp.condition_index(good), cp.condition_index(better)
    assert a is not None and b is not None
    assert a != b, "the index must separate what the score flattens"


def test_condition_index_is_none_when_a_feature_is_missing() -> None:
    """A study that cannot be scored cannot be ranked — and must not land at the bottom."""
    from preflight_helpers import cohort_placement as cp
    cols = cp._model_artifact()[0]
    feats = {c: 1.0 for c in cols}
    feats[cols[0]] = None
    assert cp.condition_index(feats) is None
    assert cp.condition_index({**feats, cols[0]: float("nan")}) is None


def test_condition_index_is_higher_is_better() -> None:
    """It is sign-flipped from the model's eta so it reads like every other percentile on the page."""
    from preflight_helpers import cohort_placement as cp
    cols, mu, sd, beta = cp._model_artifact()
    base = dict(zip(cols, mu))
    # move each feature one sd in the direction its coefficient calls WORSE; index must fall
    worse = {c: m + s * (1 if b > 0 else -1) for c, m, s, b in zip(cols, mu, sd, beta)}
    assert cp.condition_index(worse) < cp.condition_index(base)


def test_the_cohort_frame_carries_the_index_so_placement_can_rank_it() -> None:
    from preflight_helpers import cohort_placement as cp
    df = cp._cohort_df(cp.NEW)
    if df.empty:
        return
    assert cp.CONDITION_INDEX in df.columns
    assert df[cp.CONDITION_INDEX].notna().sum() > 0


def test_a_broken_model_artifact_raises_rather_than_flattening_every_rank() -> None:
    """Silently returning None would place every study at the same rank while looking healthy —
    the failure mode this module already has scars from."""
    import pytest
    from preflight_helpers import cohort_placement as cp
    cp._model_artifact.cache_clear()
    try:
        with pytest.raises(Exception):
            cp._model_artifact("NO_SUCH_OPTION")
    finally:
        cp._model_artifact.cache_clear()

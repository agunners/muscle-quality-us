"""The p01 cohort-source selector's data layer (scripts/preflight_helpers/cohort_placement.py).

Population axis (Akadir 2026-07-14): 'old' = the re-based v1.1 old-round cohort (282), 'new' = the
new-round population (per_study_cohort_new.csv, 66), 'mixed' = both (348). The condition SCORE is FIXED
to the v1.1 model for every population source (new/mixed have no grades to refit) — the toggle moves the
percentiles, not the score. An ABSENT cohort file still degrades gracefully (empty -> None placement,
never a crash or a false p0). The pre-fix v1.0 stats stay on disk for LEGACY rollback only.
"""
from __future__ import annotations

from preflight_helpers import cohort_placement as cp


def test_old_new_mixed_cohorts_load():
    assert cp.cohort_n(cp.OLD) > 0                                   # v1.1 old-round (282 in practice)
    assert cp.cohort_n(cp.NEW) >= cp.NEW_COHORT_MIN_N                # new-round population (66)
    pl = cp.percentile("full_thickness_q50_mm", 15.0, age=45, sex="M", source=cp.NEW)
    assert pl.overall is not None and pl.overall["n"] > 0


def test_mixed_folds_old_and_new():
    assert cp.cohort_n(cp.MIXED) == cp.cohort_n(cp.OLD) + cp.cohort_n(cp.NEW)


def test_placement_differs_by_population():
    # the whole point of the toggle: old vs new place a value at genuinely different percentiles.
    old = cp.percentile("full_thickness_q50_mm", 15.0, age=45, sex="M", source=cp.OLD)
    new = cp.percentile("full_thickness_q50_mm", 15.0, age=45, sex="M", source=cp.NEW)
    assert old.overall is not None and new.overall is not None
    assert old.overall["pctile"] != new.overall["pctile"]


def test_score_is_fixed_to_the_active_version_for_every_population():
    # The condition SCORE model must be the SAME for old/new/mixed — the population toggle moves
    # placement percentiles only, never the score. The VERSION is read from ACTIVE_OLD_VERSION rather
    # than spelled out: this test named v1.2 in its own function name and went on passing after the
    # v1.3 re-freeze repointed the cohort, because it asserted the very thing that had gone stale.
    v = cp.ACTIVE_OLD_VERSION
    paths = {cp.model_path_for_source(s) for s in (cp.OLD, cp.NEW, cp.MIXED)}
    assert len(paths) == 1 and paths.pop().endswith(f"condition_models_{v}.json")
    opts = {cp.options_path_for_source(s) for s in (cp.OLD, cp.NEW, cp.MIXED)}
    assert len(opts) == 1 and opts.pop().endswith(f"condition_options_{v}.json")


def test_legacy_rollback_still_resolves():
    # v1.0 is not offered in the UI but must still load (rollback): its own cohort + its own v1.0 model.
    assert cp.cohort_n(cp.LEGACY) > 0
    assert cp.model_path_for_source(cp.LEGACY).endswith("condition_models.json")


def test_absent_new_cohort_degrades_gracefully(monkeypatch):
    # point 'new' at a nonexistent file -> empty frame -> None placement (never a crash or a false p0).
    monkeypatch.setitem(cp._COHORT_FILE, cp.NEW, "per_study_cohort_new__absent_for_test.csv")
    cp._cohort_df.cache_clear()
    try:
        assert cp.cohort_n(cp.NEW) == 0
        pl = cp.percentile("rf_thickness_mm", 12.0, age=50, sex="M", source=cp.NEW)
        assert pl.overall is None and pl.age_matched is None
        assert cp.cohort_box("rf_thickness_mm", 50, "M", source=cp.NEW)["overall"] is None
        assert cp.cohort_points("rf_thickness_mm", 50, "M", source=cp.NEW)["overall"] is None
    finally:
        cp._cohort_df.cache_clear()      # drop the monkeypatched-empty entries so later tests re-read the real files


def test_intensity_vs_geometry_split():
    # only echo-intensity features carry the software-footprint caveat; geometry does not
    assert cp.is_intensity_feature("int_cv_within")
    assert not cp.is_intensity_feature("star_ratio")        # STAR = QMT/BMI -> geometry, not intensity
    assert not cp.is_intensity_feature("rf_thickness_mm")
    assert not cp.is_intensity_feature("fat_thickness_q50_mm")
    assert not cp.is_intensity_feature("qmt_mm")

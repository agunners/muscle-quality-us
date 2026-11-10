"""Invariant: the displayed-feature lists + their labels/digits never drift.

This is the test the subcutaneous-fat regression needed and didn't have. When the
fat features were added to the display lists but NOT to the previous-runs sidebar
label dict / BOOTSTRAP_FEATURES, the sidebar crashed
(`KeyError('fat_thickness_q50_mm')`) and the cross-run "study bootstrap" table
blanked fat ("— / single run") — and the suite stayed green because nothing
asserted that every displayed feature is labelled AND pooled. These fail loudly
on that drift.
"""
from __future__ import annotations


def test_every_displayed_feature_has_label_and_digits():
    from preflight_helpers import feature_catalog as fc
    for f in fc.ALL_DISPLAY_FEATURES:
        assert f in fc.FEATURE_LABELS, f"no card label for {f}"
        assert f in fc.DIGITS, f"no digits for {f}"


def test_every_headline_feature_has_a_short_sidebar_label():
    # The previous-runs sidebar hard-indexed SHORT_LABELS[f] over HEADLINE_FEATURES
    # and crashed when fat lacked an entry. Guard that every headline feature is
    # labelled in the catalog the sidebar now reads from.
    from preflight_helpers import feature_catalog as fc
    for f in fc.HEADLINE_FEATURES:
        assert f in fc.SHORT_LABELS, f"no sidebar short label for {f}"


def test_bootstrap_pools_every_displayed_feature():
    # The run-history "study bootstrap" table iterates the displayed features and
    # reads boot["per_feature"][f]; a displayed feature missing from
    # BOOTSTRAP_FEATURES renders as "— / single run" (the fat bug). Pool == display.
    from preflight_helpers import feature_catalog as fc
    missing = set(fc.ALL_DISPLAY_FEATURES) - set(fc.BOOTSTRAP_FEATURES)
    assert not missing, f"displayed but not pooled by the bootstrap: {sorted(missing)}"


def test_run_pipeline_reexports_the_catalog_lists():
    # run_pipeline re-exports the catalog so `from run_pipeline import
    # HEADLINE_FEATURES` callers (preflight, the sidebar) stay wired to one source.
    from preflight_helpers import feature_catalog as fc
    from preflight_helpers import run_pipeline as rp
    assert tuple(rp.HEADLINE_FEATURES) == tuple(fc.HEADLINE_FEATURES)
    assert tuple(rp.SECONDARY_FEATURES) == tuple(fc.SECONDARY_FEATURES)


def test_study_bootstrap_pools_run_pipeline_displayed_features():
    # The wired-module end-to-end guard: whatever run_pipeline says is displayed,
    # the actual study_bootstrap module must pool.
    from preflight_helpers import run_pipeline as rp
    from preflight_helpers import study_bootstrap as sb
    displayed = set(rp.HEADLINE_FEATURES) | set(rp.SECONDARY_FEATURES)
    missing = displayed - set(sb.BOOTSTRAP_FEATURES)
    assert not missing, f"study_bootstrap does not pool: {sorted(missing)}"


def test_fat_features_specifically_are_pooled_and_labelled():
    # The exact regression: the fat features must be displayed, labelled, AND
    # pooled across runs (the bug left them displayed+labelled-on-card but not
    # pooled, so the run-history table showed "— / single run").
    from preflight_helpers import feature_catalog as fc
    for f in ("fat_thickness_q50_mm", "fat_to_depth_q50"):
        assert f in fc.ALL_DISPLAY_FEATURES, f
        assert f in fc.FEATURE_LABELS, f
        assert f in fc.BOOTSTRAP_FEATURES, f


def test_every_displayed_feature_has_a_cohort_column():
    # The cohort-layer half of the single-source invariant: every feature the card
    # displays must have a matching column in the cohort reference table, else
    # cohort_placement returns None and the box plot is SILENTLY BLANK — exactly the
    # visible_rf_area_mm2-vs-rf_area_mm2 name-drift that bit us. (muscle_fat_ratio is
    # demoted, so it is not in ALL_DISPLAY and not required here.) Reads the cohort
    # table through the same loader cohort_placement uses.
    from preflight_helpers import feature_catalog as fc
    from preflight_helpers.cohort_placement import _cohort_df
    cols = set(_cohort_df().columns)
    missing = [f for f in fc.ALL_DISPLAY_FEATURES if f not in cols]
    assert not missing, f"displayed features with no cohort column: {sorted(missing)}"

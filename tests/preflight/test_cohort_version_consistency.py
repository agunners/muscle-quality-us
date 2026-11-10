"""The percentiles and the score must describe the SAME population.

This exists because they didn't. On 2026-07-30 the v1.3 probe re-freeze — which removed the 11
studies shot on the Leltek 128LH probe, a confound on int_cv_within, the axis carrying 43% of the
score — repointed the COHORT file to v1.3 and left the MODEL default at v1.2. For three days the app
placed patients against the probe-cleaned 271 while scoring them with a model fit on the contaminated
282, with condition_models_v1.3.json committed and never loaded. Nothing failed; nothing said so.

The fix is not "repoint it": that is one edit away from happening again. Both now derive from
ACTIVE_OLD_VERSION, and these tests pin the derivation so a future re-freeze cannot half-land.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from preflight_helpers import cohort_placement as cp


def _stem_version(path: str) -> str:
    """'…/condition_models_v1.3.json' -> 'v1.3'; the un-suffixed artifact is the v1.0 legacy."""
    name = Path(path).stem
    for v in (cp.V13, cp.V12, cp.V11):
        if name.endswith("_" + v.replace(".", ".")):
            return v
    return cp.LEGACY


@pytest.mark.parametrize("source", [cp.OLD, cp.NEW, cp.MIXED])
def test_cohort_and_model_are_the_same_version_for_every_served_source(source) -> None:
    """The population toggle must not silently change which model version scores the patient."""
    assert _stem_version(cp.model_path_for_source(source)) == cp.ACTIVE_OLD_VERSION
    assert _stem_version(cp.options_path_for_source(source)) == cp.ACTIVE_OLD_VERSION


def test_the_old_cohort_file_is_derived_from_the_active_version_not_typed() -> None:
    """The exact drift that happened: OLD hardcoded to one version, the model default to another."""
    assert cp._COHORT_FILE[cp.OLD] == cp._COHORT_FILE[cp.ACTIVE_OLD_VERSION]


def test_the_active_version_has_all_three_artifacts_on_disk() -> None:
    """A version is only servable if cohort + model + options all exist — otherwise repointing
    ACTIVE_OLD_VERSION swaps in a file that is not there and placement degrades to empty."""
    from preflight_helpers import env
    v = cp.ACTIVE_OLD_VERSION
    for table in (cp._COHORT_FILE, cp._MODEL_FILE, cp._OPTIONS_FILE):
        p = Path(env.COHORT_STATS_DIR) / table[v]
        assert p.is_file(), f"{p.name} missing for the active version {v}"


def test_the_served_model_was_fit_on_the_served_cohort() -> None:
    """The semantic check the path comparison cannot make: the model artifact records n_cohort, and
    it must equal the number of rows in the cohort file actually served. v1.2 recorded n_cohort=282
    while the served cohort held 271 — that mismatch IS the bug, in one number."""
    import csv
    from preflight_helpers import env
    art = json.load(open(cp.model_path_for_source(cp.OLD), encoding="utf-8"))
    n_cohort = {m.get("n_cohort") for m in art["models"].values() if "n_cohort" in m}
    assert len(n_cohort) == 1, f"models disagree on n_cohort: {n_cohort}"
    served = Path(env.COHORT_STATS_DIR) / cp._COHORT_FILE[cp.OLD]
    rows = sum(1 for _ in csv.DictReader(open(served, encoding="utf-8")))
    assert n_cohort.pop() == rows, "the served model was not fit on the served cohort"


def test_rollback_sources_still_reach_their_own_versions() -> None:
    """Pinning the default must not break rollback — the whole point of keeping v1.0/v1.1/v1.2."""
    assert _stem_version(cp.model_path_for_source(cp.V11)) == cp.V11
    assert _stem_version(cp.model_path_for_source(cp.V12)) == cp.V12
    assert _stem_version(cp.model_path_for_source(cp.LEGACY)) == cp.LEGACY

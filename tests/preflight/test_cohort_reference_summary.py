"""The 'what am I comparing against' strip beside the reference selector.

It exists because the selector's effect is enormous and was invisible. The same old-round study reads
p90.2 for star_ratio against OLD and p49.5 against NEW — a 40-point swing produced by nothing about the
patient. The reason is legible only once you can see that OLD has median age 49 and star_ratio 1.24
while NEW has age 30 and star_ratio 1.72: same millimetres, different peers. Without the strip a user
has to guess what they just switched to, and a 40-point move looks like instrument noise.

Pure/headless so it is testable without streamlit, per the module convention.
"""
from __future__ import annotations

import pytest

from preflight_helpers import cohort_placement as cp

SOURCES = [cp.OLD, cp.NEW, cp.MIXED]


@pytest.mark.parametrize("source", SOURCES)
def test_every_served_population_reports_a_size_and_demographics(source) -> None:
    s = cp.cohort_reference_summary(source)
    assert s["n"] > 0, f"{source} has no studies — the selector would offer an empty reference"
    assert s["features"]["age"] and s["features"]["age"]["median"] > 0


@pytest.mark.parametrize("source", SOURCES)
def test_quartiles_bracket_the_median(source) -> None:
    """A malformed strip (p25 above the median) would read as a plausible number and mislead."""
    for name, d in cp.cohort_reference_summary(source)["features"].items():
        if d is None:
            continue
        assert d["p25"] <= d["median"] <= d["p75"], f"{source}/{name} quartiles are inconsistent"


def test_a_missing_feature_is_None_not_zero() -> None:
    """The populations do not carry identical columns. A missing reference rendering as 0.00 would be
    a number the user could act on."""
    s = cp.cohort_reference_summary(cp.NEW)
    assert all(d is None or d["n"] > 0 for d in s["features"].values())


def test_an_empty_population_degrades_rather_than_dividing_by_zero() -> None:
    """Guards the branch the page uses to print 'no studies yet' instead of a percentile."""
    s = cp.cohort_reference_summary("a-source-that-does-not-exist-and-yields-an-empty-frame")
    assert isinstance(s["n"], int)


def test_mixed_is_at_least_as_large_as_either_half() -> None:
    n = {s: cp.cohort_reference_summary(s)["n"] for s in SOURCES}
    assert n[cp.MIXED] >= max(n[cp.OLD], n[cp.NEW])


def test_the_populations_actually_differ_on_the_axis_that_drives_the_swing() -> None:
    """Not a tautology — it pins the FACT the strip exists to communicate. If old and new ever
    converge this test failing is the signal that the caveat copy needs revisiting."""
    old = cp.cohort_reference_summary(cp.OLD)["features"]
    new = cp.cohort_reference_summary(cp.NEW)["features"]
    assert old["age"]["median"] > new["age"]["median"], "the new round is the younger population"
    assert new["star_ratio"]["median"] > old["star_ratio"]["median"]


def test_the_summary_covers_every_feature_the_strip_renders() -> None:
    """The page formats exactly these keys; a rename in one place must fail here, not render '—'."""
    s = cp.cohort_reference_summary(cp.OLD)["features"]
    for k in cp.REFERENCE_SUMMARY_DEMOGRAPHICS + cp.REFERENCE_SUMMARY_FEATURES:
        assert k in s

"""E4 — the study-level demotion of a pervasive per-frame flag (dark_clip_high
under-gain): per-study firing fraction + the pervasive-study pull-out."""
from preflight_helpers.study_level_qa import (
    per_study_firing_fraction, pervasive_studies, study_level_under_gain,
)


def test_per_study_firing_fraction():
    sc = ["A", "A", "A", "A", "B", "B"]
    fired = [True, True, True, True, False, True]   # A 4/4=1.0, B 1/2=0.5
    fr = per_study_firing_fraction(sc, fired)
    assert fr["A"] == 1.0 and fr["B"] == 0.5


def test_pervasive_studies_threshold():
    fr = {"A": 1.0, "B": 0.5, "C": 0.85, "D": 0.8}
    p = pervasive_studies(fr, threshold=0.8)
    assert set(p) == {"A", "C", "D"}    # B (0.5) excluded; D (==0.8) included
    assert "B" not in p


def test_study_level_under_gain_end_to_end():
    # one study under-gained (every frame clips), one clean
    sc = ["UG"] * 10 + ["OK"] * 10
    fired = [True] * 10 + [False] * 9 + [True]   # UG 100%, OK 10%
    ug = study_level_under_gain(sc, fired, threshold=0.8)
    assert set(ug) == {"UG"} and ug["UG"] == 1.0


def test_empty():
    assert per_study_firing_fraction([], []) == {}
    assert pervasive_studies({}) == {}

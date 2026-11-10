"""(d) the minimal 2-cut fitter — fit_detector_cut on synthetic labels."""
from __future__ import annotations

from calibration.fit_cut import fit_detector_cut


def test_fit_high_direction_recovers_a_clean_cut() -> None:
    """dark_clip-like: problem when score is HIGH. Labels: yes for score > 0.10."""
    scores = {f"k{i}": i / 100 for i in range(21)}            # 0.00 .. 0.20
    labels = {f"k{i}": ("yes" if i / 100 > 0.10 else "no") for i in range(21)}
    r = fit_detector_cut(scores, labels, direction="high", current_cut=0.02)
    assert r["n"] == 21 and r["n_pos"] == 10 and r["n_neg"] == 11
    assert r["youden"]["se"] == 1.0 and r["youden"]["sp"] == 1.0   # clean sep
    assert 0.10 <= r["youden"]["cut"] <= 0.11
    # the deployed 0.02 cut over-fires: catches every positive (Se=1) but at low Sp
    assert r["se_at_current"] == 1.0 and r["sp_at_current"] < 1.0
    assert r["sens_target"] is not None                           # floor reachable


def test_fit_low_direction_for_wrong_site() -> None:
    """wrong_site-like: problem when score is LOW (coverage < cut)."""
    scores = {f"k{i}": i / 20 for i in range(21)}                 # 0.00 .. 1.00
    labels = {f"k{i}": ("yes" if i / 20 < 0.50 else "no") for i in range(21)}
    r = fit_detector_cut(scores, labels, direction="low", current_cut=0.5)
    assert r["n_pos"] == 10
    assert r["youden"]["se"] == 1.0 and r["youden"]["sp"] == 1.0
    assert 0.45 <= r["youden"]["cut"] <= 0.50


def test_fit_drops_unsure_and_missing_scores() -> None:
    scores = {"a": 0.3, "b": 0.05}                               # 'c' has no score
    labels = {"a": "yes", "b": "no", "c": "yes", "d": "unsure"}
    r = fit_detector_cut(scores, labels, direction="high", current_cut=0.02)
    assert r["n"] == 2                                            # only a, b count


def test_fit_empty_is_safe() -> None:
    r = fit_detector_cut({}, {}, direction="high", current_cut=0.02)
    assert r["n"] == 0 and r["youden"] is None and r["agree_at_current"] is None


def test_fit_unreachable_sensitivity_floor_returns_none() -> None:
    """Heavy overlap → no cut meets Se>=0.80 at Sp>=0.85 → mechanism review."""
    scores = {"a": 0.10, "b": 0.10, "c": 0.10, "d": 0.10}
    labels = {"a": "yes", "b": "no", "c": "yes", "d": "no"}       # score can't separate
    r = fit_detector_cut(scores, labels, direction="high", current_cut=0.05)
    assert r["sens_target"] is None

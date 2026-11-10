"""Coverage for the probe readout's load-bearing go/no-go logic
(`scripts/calibration/analyze_sweep_pool_readout.py`).

The internal adversarial review flagged that both probe scripts had ZERO direct
tests, while `_tail`'s separability rule carried a dormant tiny-MAD bug. This
pins the two pure functions the GREEN/AMBER/RED call rests on:

  * `_tail` — separable_population vs heavy_tail_not_separable vs no_tail, and
    the `spike` guard (a value-spike shrinks MAD and would manufacture a false
    separable tail — the dormant bug, now flagged).
  * `_call` — study-clustering aware: a tail in too few studies, or on collapsed
    (mask-confounded) masks, or spike-driven, is not a GREEN.
"""
import importlib.util
from pathlib import Path

import numpy as np

_P = (Path(__file__).resolve().parents[2]
      / "scripts" / "calibration" / "analyze_sweep_pool_readout.py")
_spec = importlib.util.spec_from_file_location("_aspr_under_test", _P)
aspr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aspr)


def test_tail_tight_unimodal_is_not_separable():
    x = np.random.default_rng(0).normal(100.0, 5.0, 4000)
    t = aspr._tail(x, "high")
    assert t["verdict"] in ("no_tail", "heavy_tail_not_separable")
    assert t["z8"] < 20


def test_tail_far_cluster_is_separable():
    rng = np.random.default_rng(1)
    x = np.concatenate([rng.normal(10.0, 1.0, 3900), rng.normal(80.0, 1.0, 100)])
    t = aspr._tail(x, "high")
    assert t["verdict"] == "separable_population"
    assert t["z8"] >= 20


def test_tail_spike_is_flagged():
    rng = np.random.default_rng(2)
    x = np.concatenate([np.zeros(1500), rng.normal(0.05, 0.02, 2500)])
    assert aspr._tail(x, "high")["spike"] is True


def test_call_no_tail_is_red():
    assert aspr._call(5000, finite=False, n_studies=0, tail={"verdict": "no_tail"}) == "RED"


def test_call_mask_confounded_separable_is_red():
    assert aspr._call(5000, finite=False, n_studies=10,
                      tail={"verdict": "separable_population", "confounded": True}) == "RED"


def test_call_separable_enough_studies_is_green():
    assert aspr._call(5000, finite=False, n_studies=60,
                      tail={"verdict": "separable_population",
                            "confounded": False, "spike": False}) == "GREEN"


def test_call_separable_study_starved_is_amber():
    assert aspr._call(5000, finite=False, n_studies=18,
                      tail={"verdict": "separable_population",
                            "confounded": False, "spike": False}) == "AMBER"


def test_call_heavy_tail_study_starved_is_red():
    assert aspr._call(5000, finite=False, n_studies=1,
                      tail={"verdict": "heavy_tail_not_separable"}) == "RED"


def test_call_finite_many_studies_is_green():
    assert aspr._call(5000, finite=True, fires=3000, n_studies=100) == "GREEN"


def test_call_finite_study_starved_is_amber():
    assert aspr._call(5000, finite=True, fires=300, n_studies=18) == "AMBER"

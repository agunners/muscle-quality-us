"""E5 reproducibility harness — κ / ICC(2,1) / MDC95 pinned to hand-computed and
textbook (Shrout & Fleiss 1979) values. This is the MOP that ships now; the MOE
bands on Round-3 re-scan sub-cohorts are data-gated."""
import numpy as np
import pytest

from preflight_helpers.reproducibility import (
    cohens_kappa, icc_2_1, mdc95, mean_pairwise_kappa, reproducibility_summary,
    sem_from_icc,
)


def test_kappa_perfect():
    assert cohens_kappa([1, 0, 1, 0], [1, 0, 1, 0]) == 1.0


def test_kappa_known_value():
    # a=[1,1,1,0,0] b=[1,1,0,0,0]: po=0.8, pe=0.6*0.4+0.4*0.6=0.48 -> 0.32/0.52
    assert abs(cohens_kappa([1, 1, 1, 0, 0], [1, 1, 0, 0, 0]) - 0.61538) < 1e-4


def test_kappa_below_chance():
    # perfectly anti-correlated: po=0, pe=0.5 -> (0-0.5)/0.5 = -1.0
    assert abs(cohens_kappa([1, 0, 1, 0], [0, 1, 0, 1]) - (-1.0)) < 1e-9


def test_mean_pairwise_kappa():
    # two identical raters + one anti -> mean of (1.0, -1.0, -1.0) = -1/3
    r = np.array([[1, 1, 0], [0, 0, 1], [1, 1, 0], [0, 0, 1]])
    assert abs(mean_pairwise_kappa(r) - (-1.0 / 3.0)) < 1e-9


def test_icc_shrout_fleiss_1979():
    # The canonical Shrout & Fleiss example: ICC(2,1) = 0.290.
    x = np.array([[9, 2, 5, 8], [6, 1, 3, 2], [8, 4, 6, 8],
                  [7, 1, 2, 6], [10, 5, 6, 9], [6, 2, 4, 7]], dtype=float)
    assert abs(icc_2_1(x) - 0.290) < 0.005


def test_icc_perfect_agreement():
    x = np.array([[1.0, 1.0], [2.0, 2.0], [3.0, 3.0], [4.0, 4.0]])
    assert icc_2_1(x) > 0.999


def test_mdc95_known():
    # SD=10, ICC=0.75 -> SEM=10*sqrt(0.25)=5 -> MDC95=1.96*sqrt(2)*5=13.859
    assert abs(sem_from_icc(10.0, 0.75) - 5.0) < 1e-9
    assert abs(mdc95(10.0, 0.75) - 13.8593) < 1e-3


def test_summary_shapes():
    x = np.array([[9, 2, 5, 8], [6, 1, 3, 2], [8, 4, 6, 8],
                  [7, 1, 2, 6], [10, 5, 6, 9], [6, 2, 4, 7]], dtype=float)
    s = reproducibility_summary(x)
    assert s["n_subjects"] == 6 and s["k_raters"] == 4
    assert abs(s["icc_2_1"] - 0.290) < 0.005
    assert s["mdc95"] > 0


def test_guards():
    with pytest.raises(ValueError):
        cohens_kappa([1, 2], [1])
    with pytest.raises(ValueError):
        icc_2_1(np.zeros((1, 3)))

"""Reproducibility metrics for the muscle-quality measurements (E5).

Cohen's κ (categorical agreement), ICC(2,1) (continuous agreement across raters —
two-way random effects, absolute agreement, single measurement), and MDC95
(minimal detectable change at 95% confidence). These ship NOW as the MOP: pure
functions with unit tests pinned to hand-computed / textbook values. They run on
the 3-operator decision-log (inter-rater agreement) today; the MOE bands
(κ ≥ 0.6, ICC ≥ 0.75, MDC reported) on the paired / 24h re-scan sub-cohorts are
DATA-GATED on the Round-3 controlled acquisition (no re-scans exist yet).

ICC(2,1) uses the Shrout & Fleiss (1979) ANOVA mean-square form. MDC95 = 1.96·√2·
SEM with SEM = SD·√(1−ICC).
"""
from __future__ import annotations

from typing import Sequence

import numpy as np


def cohens_kappa(a: Sequence, b: Sequence) -> float:
    """Cohen's κ for two raters' categorical labels over the same items.

    κ = (po − pe)/(1 − pe): observed agreement corrected for chance. 1.0 perfect,
    0 at chance, negative below chance. Categories are the union of both raters'."""
    a = np.asarray(a)
    b = np.asarray(b)
    if a.shape != b.shape or a.size == 0:
        raise ValueError("a and b must be equal-length and non-empty")
    po = float(np.mean(a == b))
    pe = 0.0
    for c in np.union1d(a, b):
        pe += float(np.mean(a == c)) * float(np.mean(b == c))
    if pe >= 1.0:
        return 1.0
    return float((po - pe) / (1.0 - pe))


def mean_pairwise_kappa(ratings: np.ndarray) -> float:
    """Mean Cohen's κ over every rater pair — a simple multi-rater categorical
    agreement summary for the 3-operator log. `ratings` is (n_items, k_raters)."""
    x = np.asarray(ratings)
    n, k = x.shape
    if k < 2:
        raise ValueError("need >=2 raters")
    ks = [cohens_kappa(x[:, i], x[:, j])
          for i in range(k) for j in range(i + 1, k)]
    return float(np.mean(ks))


def icc_2_1(ratings: np.ndarray) -> float:
    """ICC(2,1): two-way random-effects, absolute agreement, single measurement.

    `ratings` is (n_subjects, k_raters), every rater scores every subject. The
    standard ANOVA decomposition (Shrout & Fleiss 1979)."""
    x = np.asarray(ratings, dtype=float)
    if x.ndim != 2:
        raise ValueError("ratings must be 2-D (subjects x raters)")
    n, k = x.shape
    if n < 2 or k < 2:
        raise ValueError("need >=2 subjects and >=2 raters")
    grand = x.mean()
    ss_total = float(((x - grand) ** 2).sum())
    ss_rows = float(k * ((x.mean(axis=1) - grand) ** 2).sum())
    ss_cols = float(n * ((x.mean(axis=0) - grand) ** 2).sum())
    ss_err = ss_total - ss_rows - ss_cols
    msr = ss_rows / (n - 1)
    msc = ss_cols / (k - 1)
    mse = ss_err / ((n - 1) * (k - 1))
    denom = msr + (k - 1) * mse + (k / n) * (msc - mse)
    if denom == 0:
        return 0.0
    return float((msr - mse) / denom)


def sem_from_icc(sd: float, icc: float) -> float:
    """Standard error of measurement = SD·√(1−ICC) (ICC clamped to [0,1])."""
    return float(sd * np.sqrt(max(0.0, 1.0 - min(1.0, icc))))


def mdc95(sd: float, icc: float) -> float:
    """Minimal detectable change at 95% confidence = 1.96·√2·SEM — the smallest
    change in a measurement that exceeds measurement error (not Round-3 re-scan
    noise) with 95% confidence."""
    return float(1.96 * np.sqrt(2.0) * sem_from_icc(sd, icc))


def reproducibility_summary(ratings: np.ndarray) -> dict:
    """ICC(2,1) + SD + MDC95 for one continuous measurement across raters/sessions.
    `ratings` is (n_subjects, k_raters). SD is the pooled subject-mean SD."""
    x = np.asarray(ratings, dtype=float)
    icc = icc_2_1(x)
    sd = float(x.mean(axis=1).std(ddof=1))
    return {"icc_2_1": icc, "sd": sd, "sem": sem_from_icc(sd, icc),
            "mdc95": mdc95(sd, icc), "n_subjects": int(x.shape[0]),
            "k_raters": int(x.shape[1])}

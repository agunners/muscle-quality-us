"""Statistical helpers for Stage B–F.

- ``spearman_with_n``    — Spearman ρ + p + effective n (≥5 guard).
- ``stouffer_combine``   — combine p-values via inverse-normal weighted by √n.
- ``bh_fdr``             — Benjamini-Hochberg q-values.
- ``shuffle_target``     — deterministic target shuffler for null baseline.
"""

from __future__ import annotations

from typing import Iterable, Sequence

import numpy as np
import pandas as pd
from scipy import stats


def spearman_with_n(x: np.ndarray, y: np.ndarray) -> tuple[float | None, float | None, int]:
    """Spearman on pairs where both are finite. Returns (r, p, n)."""
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    mask = np.isfinite(x) & np.isfinite(y)
    n = int(mask.sum())
    if n < 5:
        return None, None, n
    try:
        r, p = stats.spearmanr(x[mask], y[mask])
        if r is None or np.isnan(r):
            return None, None, n
        return float(r), float(p), n
    except Exception:
        return None, None, n


def stouffer_combine(
    p_values: Sequence[float | None],
    weights: Sequence[float] | None = None,
) -> tuple[float | None, float | None]:
    """Stouffer's weighted z combine. Returns (combined_z, combined_p)."""
    clean_p, clean_w = [], []
    for i, p in enumerate(p_values):
        if p is None or not np.isfinite(p) or p <= 0 or p >= 1:
            continue
        clean_p.append(float(p))
        clean_w.append(float(weights[i]) if weights is not None else 1.0)
    if not clean_p:
        return None, None
    z = stats.norm.isf(np.asarray(clean_p))  # one-sided inverse (right tail)
    w = np.asarray(clean_w, dtype=np.float64)
    if w.sum() <= 0:
        return None, None
    z_combined = float(np.sum(w * z) / np.sqrt(np.sum(w ** 2)))
    p_combined = float(stats.norm.sf(z_combined))
    return z_combined, p_combined


def bh_fdr(p_values: Sequence[float | None], *, q: float = 0.05) -> tuple[np.ndarray, np.ndarray]:
    """Return (bh_q_values, survives) arrays aligned with input order.

    ``None`` / NaN p-values are excluded from the ranking but receive
    ``NaN`` in the q-value vector.
    """
    p = np.asarray([np.nan if x is None else float(x) for x in p_values], dtype=np.float64)
    mask = np.isfinite(p)
    m = int(mask.sum())
    q_arr = np.full_like(p, np.nan)
    if m == 0:
        return q_arr, np.zeros_like(p, dtype=bool)
    # BH step-up
    valid_idx = np.flatnonzero(mask)
    p_valid = p[valid_idx]
    order = np.argsort(p_valid)
    ranked = p_valid[order]
    k = np.arange(1, m + 1, dtype=np.float64)
    bh = ranked * m / k
    # enforce monotonic non-decreasing from the right
    bh_mono = np.minimum.accumulate(bh[::-1])[::-1]
    bh_mono = np.clip(bh_mono, 0.0, 1.0)
    q_back = np.empty_like(bh_mono)
    q_back[order] = bh_mono
    q_arr[valid_idx] = q_back
    survives = (q_arr <= q) & mask
    return q_arr, survives


def shuffle_target(y: np.ndarray, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = np.array(y, copy=True)
    rng.shuffle(out)
    return out


def weighted_mean(values: Iterable[float | None], weights: Iterable[float]) -> float | None:
    vs, ws = [], []
    for v, w in zip(values, weights, strict=False):
        if v is None or not np.isfinite(v):
            continue
        vs.append(float(v))
        ws.append(float(w))
    if not vs or sum(ws) <= 0:
        return None
    return float(np.sum(np.asarray(vs) * np.asarray(ws)) / np.sum(np.asarray(ws)))


def top_k_jaccard(a: Sequence[str], b: Sequence[str], k: int) -> float:
    sa = set(list(a)[:k])
    sb = set(list(b)[:k])
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / max(len(sa | sb), 1)

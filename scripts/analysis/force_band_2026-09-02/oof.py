"""Nested-CV OOF engine, harness-identical: outer KFold(5, shuffle, seed) + the harness's
make_model (StandardScaler+Ridge, alpha over logspace(-3,3,25) tuned on an inner KFold(5, seed),
or the shallow HGB grid). Returns per-seed OOF predictions so residual bands can be pooled.
Verified bit-identical to results/force_ladder_results.csv for seed 0 (ridge and hgb)."""
from __future__ import annotations
import numpy as np
from sklearn.model_selection import KFold
from common import load_harness
FL = load_harness()

def oof_pred(X, y, seed: int, kind: str = "ridge") -> np.ndarray:
    outer = KFold(5, shuffle=True, random_state=seed)
    pred = np.empty_like(y, dtype=float)
    for tr, te in outer.split(X):
        gs = FL.make_model(kind, seed)
        gs.fit(X[tr], y[tr])
        pred[te] = gs.predict(X[te])
    return pred

def oof_many(X, y, seeds, kind="ridge") -> np.ndarray:
    return np.vstack([oof_pred(X, y, s, kind) for s in seeds])   # seeds x n

def metrics(y, pred) -> dict:
    res = y - pred
    return {"r2": 1.0 - float(np.sum(res**2)) / float(np.sum((y - y.mean())**2)),
            "rmse": float(np.sqrt(np.mean(res**2))), "mae": float(np.mean(np.abs(res)))}

def summarize(vals) -> dict:
    a = np.asarray(vals, dtype=float)
    return {"mean": float(a.mean()), "sd": float(a.std()), "p2_5": float(np.percentile(a, 2.5)),
            "p97_5": float(np.percentile(a, 97.5)), "n_seeds": int(len(a))}

def band(res, lo=10, hi=90) -> dict:
    r = np.asarray(res, dtype=float)
    q10, q90 = np.percentile(r, lo), np.percentile(r, hi)
    return {"q10": float(q10), "q90": float(q90), "width": float(q90 - q10), "sd": float(r.std()), "n": int(len(r))}

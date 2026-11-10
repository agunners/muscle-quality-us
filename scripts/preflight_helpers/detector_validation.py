"""Held-out sensitivity/specificity validation for a detector cut.

This is the harness the DATA-GATED MOEs run VERBATIM once clinician-labelled
positives exist — the E1 held-out detector Se/Sp (Se>=0.80 @ Sp>=0.85) and the
E4 fascia 0.70-cut Se/Sp. It is built + unit-tested NOW (against hand-computed
confusion matrices) so the day controlled labels arrive, validating the cut is a
single call, not a build. The cut-FITTING side lives in
`scripts/calibration/fit_cut.py`; this is the held-out VALIDATION side that keeps
the Se/Sp estimate honest (cut fitted on train, reported on a disjoint test fold).

`labels`: 1 = the failure mode is present (per a qualified rater), 0 = absent.
`higher_fires`: True when a HIGHER score means the detector fires (e.g.
dark_clip's roi_dark_fraction); False when a LOWER score fires (e.g. the fascia
`continuity_fraction`, which fires below 0.70).
"""
from __future__ import annotations

from typing import Sequence

import numpy as np


def confusion_at_cut(labels: Sequence, scores: Sequence, cut: float,
                     higher_fires: bool = True) -> tuple:
    """(tp, fp, tn, fn) at a firing cut."""
    y = np.asarray(labels).astype(int)
    s = np.asarray(scores, dtype=float)
    fired = s >= cut if higher_fires else s <= cut
    tp = int(np.sum(fired & (y == 1)))
    fp = int(np.sum(fired & (y == 0)))
    tn = int(np.sum(~fired & (y == 0)))
    fn = int(np.sum(~fired & (y == 1)))
    return tp, fp, tn, fn


def sensitivity_specificity(labels: Sequence, scores: Sequence, cut: float,
                            higher_fires: bool = True) -> dict:
    """Se = TP/(TP+FN), Sp = TN/(TN+FP) at the cut (+ the raw confusion counts)."""
    tp, fp, tn, fn = confusion_at_cut(labels, scores, cut, higher_fires)
    se = tp / (tp + fn) if (tp + fn) else float("nan")
    sp = tn / (tn + fp) if (tn + fp) else float("nan")
    return {"sensitivity": se, "specificity": sp,
            "tp": tp, "fp": fp, "tn": tn, "fn": fn}


def youden_cut(labels: Sequence, scores: Sequence,
               higher_fires: bool = True) -> dict:
    """The cut maximising Youden's J = Se + Sp - 1 over the observed scores."""
    y = np.asarray(labels).astype(int)
    s = np.asarray(scores, dtype=float)
    best_cut, best_j = float(np.min(s)) if s.size else 0.0, -np.inf
    for c in np.unique(s):
        ss = sensitivity_specificity(y, s, float(c), higher_fires)
        j = ss["sensitivity"] + ss["specificity"] - 1.0
        if j > best_j:
            best_j, best_cut = j, float(c)
    return {"cut": best_cut, "youden_j": float(best_j)}


def held_out_se_sp(labels: Sequence, scores: Sequence,
                   train_idx: Sequence, test_idx: Sequence,
                   higher_fires: bool = True) -> dict:
    """Fit the Youden cut on the train fold, report Se/Sp on the DISJOINT test fold
    (no optimistic bias) — the E1 MOE procedure."""
    y = np.asarray(labels).astype(int)
    s = np.asarray(scores, dtype=float)
    tr, te = np.asarray(train_idx, int), np.asarray(test_idx, int)
    cut = youden_cut(y[tr], s[tr], higher_fires)["cut"]
    res = sensitivity_specificity(y[te], s[te], cut, higher_fires)
    res["cut"] = cut
    return res


def meets_band(se_sp: dict, se_min: float = 0.80, sp_min: float = 0.85) -> bool:
    """Does the held-out Se/Sp clear the MOE band (default Se>=0.80 @ Sp>=0.85)?"""
    return bool(se_sp.get("sensitivity", 0) >= se_min
                and se_sp.get("specificity", 0) >= sp_min)

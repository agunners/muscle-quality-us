"""The boss's view: your THREE model options, compared — and the data the
assessment-page box plots need. Supersedes scripts/model_variant_comparison.py.

The three options (each an ordinal logit predicting the doctors' grade):
  A  RFthk / Fat / Texture   <- the proven default (size = RF thickness)
  B  all 8 features          <- the kitchen sink
  C  STAR  / Fat / Texture   <- size = STAR (QMT/BMI), the boss's contender

Outputs, all written to data/cohort_stats/condition_options.json so the app can
draw from it without recomputing:
  1. BOSS TABLE   per option: coverage, cross-validated QWK / exact% / off-by-2%,
                  worst VIF (robustness).  Accuracy is 10-fold out-of-fold (honest).
  2. AGREEMENT    on the shared patients (all 8 present): correlation of the three
                  condition scores + how often all three agree on the band. This is
                  the punchline — does the answer even depend on which option?
  3. BOX-PLOT DATA per option: the cohort condition-score distribution (the five
                  numbers a box plot draws) the triplet renders.

Run:  .venv/bin/python scripts/condition_options_compare.py
"""
from __future__ import annotations

import json
import os
import warnings

import numpy as np
import pandas as pd
from sklearn.model_selection import StratifiedKFold
from statsmodels.miscmodels.ordinal_model import OrderedModel

warnings.filterwarnings("ignore")

CSV = os.environ.get("CONDITION_COHORT_CSV") or "data/cohort_stats/per_study_cohort.csv"
OUT = os.environ.get("CONDITION_OPTIONS_OUT") or "data/cohort_stats/condition_options.json"
ORD = {"PRESERVED": 0, "INTERMEDIATE": 1, "POOR": 2}

EIGHT = ["star_ratio", "full_thickness_q50_mm", "int_cv_within", "rf_thickness_mm",
         "fat_thickness_q50_mm", "qmt_mm", "visible_rf_area_mm2", "fat_to_depth_q50"]
OPTIONS = {
    "A: RFthk / Fat / Texture": ["full_thickness_q50_mm", "fat_to_depth_q50", "int_cv_within"],
    "B: all 8 features":         EIGHT,
    "C: STAR / Fat / Texture":   ["star_ratio", "fat_to_depth_q50", "int_cv_within"],
}


def _qwk(a, b, K=3):
    O = np.zeros((K, K))
    for i, j in zip(a, b):
        O[int(i), int(j)] += 1
    w = np.array([[((i - j) ** 2) / ((K - 1) ** 2) for j in range(K)] for i in range(K)])
    E = np.outer(O.sum(1), O.sum(0)) / O.sum()
    d = (w * E).sum()
    return float(1 - (w * O).sum() / d) if d else float("nan")


def _folds(y):
    f = np.full(len(y), -1)
    for k, (_, te) in enumerate(StratifiedKFold(10, shuffle=True, random_state=0).split(y, y)):
        f[te] = k
    return f


def _cond(probs):  # expected grade 0..2 -> 0..100, higher = better
    return (1 - (probs @ np.array([0.0, 1.0, 2.0])) / 2.0) * 100.0


def _fit_predict(Xtr, ytr, Xte):
    mu, sd = Xtr.mean(0), Xtr.std(0)
    sd[sd == 0] = 1.0
    r = OrderedModel(ytr, (Xtr - mu) / sd, distr="logit").fit(method="bfgs", disp=False, maxiter=300)
    p = r.model.predict(r.params, exog=(Xte - mu) / sd)
    return p


def _oof(raw, y, folds):
    pred = np.full(len(y), -1)
    cond = np.full(len(y), np.nan)
    for k in np.unique(folds):
        tr, te = folds != k, folds == k
        try:
            p = _fit_predict(raw[tr], y[tr], raw[te])
            pred[te] = p.argmax(1)
            cond[te] = _cond(p)
        except Exception:
            pred[te] = 1
            cond[te] = 50.0
    return pred, cond


def _vif_worst(raw):
    Z = (raw - raw.mean(0)) / (raw.std(0) + 1e-9)
    worst = 0.0
    for i in range(Z.shape[1]):
        others = [j for j in range(Z.shape[1]) if j != i]
        A = np.column_stack([np.ones(len(Z)), Z[:, others]])
        b, *_ = np.linalg.lstsq(A, Z[:, i], rcond=None)
        r2 = 1 - np.var(Z[:, i] - A @ b) / np.var(Z[:, i])
        worst = max(worst, 1 / max(1e-9, 1 - r2))
    return float(worst)


def _box(v):
    v = np.asarray(v, float)
    q = np.percentile(v, [0, 25, 50, 75, 100])
    return {"min": round(q[0], 1), "q1": round(q[1], 1), "median": round(q[2], 1),
            "q3": round(q[3], 1), "max": round(q[4], 1), "mean": round(float(v.mean()), 1)}


def main():
    df = pd.read_csv(CSV)
    df["q"] = df["quality_label"].map(ORD)
    N = len(df)
    print(f"cohort n={N}\n")

    # ---- 1. BOSS TABLE: each option on its own coverage, CV metrics -----------
    print("BOSS TABLE  (accuracy = 10-fold out-of-fold; human rater-vs-rater QWK 0.63-0.73)")
    print(f"  {'option':26s} {'n':>4s} {'cover':>6s} {'QWK':>6s} {'exact':>6s} {'off2':>5s} {'wVIF':>5s}")
    table = {}
    for name, cols in OPTIONS.items():
        sub = df[cols + ["q"]].dropna().reset_index(drop=True)
        y = sub["q"].astype(int).values
        raw = sub[cols].values.astype(float)
        pred, _ = _oof(raw, y, _folds(y))
        rec = {"n": len(y), "coverage_pct": round(len(y) / N * 100, 1),
               "qwk": round(_qwk(y, pred), 3),
               "exact_pct": round((pred == y).mean() * 100, 1),
               "off2_pct": round((np.abs(pred - y) == 2).mean() * 100, 1),
               "worst_vif": round(_vif_worst(raw), 1)}
        table[name] = rec
        print(f"  {name:26s} {rec['n']:>4d} {rec['coverage_pct']:>5.1f}% {rec['qwk']:>6.3f} "
              f"{rec['exact_pct']:>5.1f}% {rec['off2_pct']:>4.1f}% {rec['worst_vif']:>5.1f}")

    # ---- 2. AGREEMENT on shared patients (all 8 present) ----------------------
    common = df[EIGHT + ["q"]].dropna().reset_index(drop=True)
    yc = common["q"].astype(int).values
    scores, bands = {}, {}
    for name, cols in OPTIONS.items():
        raw = common[cols].values.astype(float)
        # full-fit scores = what the deployed model outputs for these patients
        p = _fit_predict(raw, yc, raw)
        scores[name] = _cond(p)
        bands[name] = p.argmax(1)
    names = list(OPTIONS)
    print(f"\nAGREEMENT  (shared n={len(common)}; do the options even differ?)")
    print("  condition-score correlation between options:")
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            r = float(np.corrcoef(scores[names[i]], scores[names[j]])[0, 1])
            print(f"    {names[i][0]} vs {names[j][0]}:  r = {r:.3f}")
    allsame = np.mean([(bands[names[0]][k] == bands[names[1]][k] == bands[names[2]][k])
                       for k in range(len(common))]) * 100
    ac_same = (bands[names[0]] == bands[names[2]]).mean() * 100
    print(f"  all THREE options put the patient in the same band: {allsame:.0f}%")
    print(f"  A and C (RFthk vs STAR) agree on the band:           {ac_same:.0f}%")

    # ---- 3. BOX-PLOT DATA per option ------------------------------------------
    print("\nBOX-PLOT DATA  (cohort condition-score spread the triplet draws)")
    box = {}
    for name in names:
        box[name] = _box(scores[name])
        b = box[name]
        print(f"  {name:26s} min {b['min']:>5.1f} | q1 {b['q1']:>5.1f} | med {b['median']:>5.1f} "
              f"| q3 {b['q3']:>5.1f} | max {b['max']:>5.1f}")

    out = {"boss_table": table,
           "agreement": {"all_three_same_band_pct": round(allsame, 0),
                         "A_vs_C_same_band_pct": round(ac_same, 0),
                         "shared_n": len(common)},
           "box_plot": box,
           "note": "accuracy=10-fold OOF; agreement+boxplots=full-fit on shared n"}
    with open(OUT, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()

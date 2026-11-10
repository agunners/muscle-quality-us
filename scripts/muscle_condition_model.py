"""Muscle-condition model: the accurate / robust / practical design.

Closes the loop from docs/muscle_score_whitepaper.md. The analysis script
(score_design_analysis.py) proved WHY naive combinations fail; this picks a
methodology, builds it, and reports -- honestly, cross-validated -- how well it
does and at what coverage.

DESIGN IN ONE LINE. Don't fit the doctors' grade on eight collinear features
(accurate, but unstable coefficients + uninterpretable + low coverage). Fit it
on the THREE clinical axes the features actually span -- muscle size, fat,
tissue texture -- one measurement each. The model's inputs are then exactly the
size/fat/texture breakdown the assessment page shows, and its output is the
single condition score that rides on top. One coherent object.

THE BAKE-OFF (all 10-fold cross-validated -- no in-sample optimism). Part A runs
every model on the SAME complete-8 rows so "fewer features" is the only thing
that varies; Part B reports the shippable models on their own (larger) coverage.

  - baseline            always the middle grade            (sanity floor, kappa=0)
  - texture-only        int_cv_within                      (1 input; parsimony/coverage floor)
  - 3-construct         size + fat + texture               (THE candidate)
  - 8-feature ordinal   all eight                          (accuracy-ceiling reference; not shippable)
  - 8-feature ridge     all eight, L2-penalized            ("just regularize the kitchen sink?")

Run:  .venv/bin/python scripts/muscle_condition_model.py
Deps: pandas, numpy, scipy, statsmodels, scikit-learn (all in the project venv).
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from statsmodels.miscmodels.ordinal_model import OrderedModel

warnings.filterwarnings("ignore")

CSV = "data/cohort_stats/per_study_cohort.csv"
ORD = {"PRESERVED": 0, "INTERMEDIATE": 1, "POOR": 2}  # 0 = best, 2 = worst

# One representative per clinical axis. Chosen for: clean factor marker, best
# within-axis coverage, and (size) the companion study's confirmed-strong stem.
SIZE = "full_thickness_q50_mm"   # RF thickness median  (loads 0.93 on the size factor)
FAT = "fat_to_depth_q50"         # fat fraction         (depth-normalized; better coverage than fat thickness)
TEXTURE = "int_cv_within"        # fascicular contrast  (the only tissue-texture feature)
CONSTRUCT = [SIZE, FAT, TEXTURE]

DISPLAY = [
    "star_ratio", "full_thickness_q50_mm", "int_cv_within", "rf_thickness_mm",
    "fat_thickness_q50_mm", "qmt_mm", "visible_rf_area_mm2", "fat_to_depth_q50",
]
NAME = {"star_ratio": "STAR", "full_thickness_q50_mm": "RF-thickness",
        "int_cv_within": "Texture", "rf_thickness_mm": "RF-thk-peak",
        "fat_thickness_q50_mm": "Fat-thickness", "qmt_mm": "QMT",
        "visible_rf_area_mm2": "RF-area", "fat_to_depth_q50": "Fat-fraction"}


# ----------------------------------------------------------------- metrics ----
def _qwk(a, b, K=3):
    O = np.zeros((K, K))
    for i, j in zip(a, b):
        O[int(i), int(j)] += 1
    w = np.array([[((i - j) ** 2) / ((K - 1) ** 2) for j in range(K)] for i in range(K)])
    E = np.outer(O.sum(1), O.sum(0)) / O.sum()
    denom = (w * E).sum()
    return 1 - (w * O).sum() / denom if denom else float("nan")


def _metrics(y, pred):
    y, pred = np.asarray(y), np.asarray(pred)
    return {
        "exact": (pred == y).mean() * 100,
        "within1": (np.abs(pred - y) <= 1).mean() * 100,
        "off2": (np.abs(pred - y) == 2).mean() * 100,
        "qwk": _qwk(y, pred),
    }


# ------------------------------------------------------------ model fitters ---
def _zfit(tr):
    mu, sd = tr.mean(0), tr.std(0)
    sd[sd == 0] = 1.0
    return mu, sd


def _ordinal_oof(X, y, folds):
    """Out-of-fold predicted class from a proportional-odds ordinal logit."""
    pred = np.full(len(y), -1)
    for k in np.unique(folds):
        tr, te = folds != k, folds == k
        mu, sd = _zfit(X[tr])
        Ztr, Zte = (X[tr] - mu) / sd, (X[te] - mu) / sd
        try:
            res = OrderedModel(y[tr], Ztr, distr="logit").fit(method="bfgs", disp=False, maxiter=300)
            probs = res.model.predict(res.params, exog=Zte)
            pred[te] = probs.argmax(1)
        except Exception:
            pred[te] = 1  # degenerate fold -> middle grade
    return pred


def _ridge_oof(X, y, folds):
    pred = np.full(len(y), -1)
    for k in np.unique(folds):
        tr, te = folds != k, folds == k
        mu, sd = _zfit(X[tr])
        clf = LogisticRegression(penalty="l2", C=1.0, max_iter=2000)
        clf.fit((X[tr] - mu) / sd, y[tr])
        pred[te] = clf.predict((X[te] - mu) / sd)
    return pred


# ---------------------------------------------------------------- bake-off ----
def bakeoff(df):
    common = df[DISPLAY + ["q"]].dropna().reset_index(drop=True)
    y = common["q"].astype(int).values
    folds = np.full(len(y), -1)
    for k, (_, te) in enumerate(StratifiedKFold(10, shuffle=True, random_state=0).split(y, y)):
        folds[te] = k

    print("=" * 78)
    print(f"PART A -- fair head-to-head, all models on the SAME complete-8 rows (n={len(y)})")
    print("         so the ONLY thing that varies is which features the model may use.")
    print("=" * 78)
    print(f"  {'model':22s} {'#feat':>5s} {'exact%':>7s} {'within1%':>9s} {'off-by-2%':>10s} {'QWK':>6s}")

    def row(label, nfeat, pred):
        m = _metrics(y, pred)
        print(f"  {label:22s} {nfeat:>5d} {m['exact']:>7.1f} {m['within1']:>9.1f} "
              f"{m['off2']:>10.1f} {m['qwk']:>6.3f}")
        return m

    row("baseline (mid grade)", 0, np.full(len(y), 1))
    row("texture-only", 1, _ordinal_oof(common[[TEXTURE]].values, y, folds))
    cand = row("3-construct  *", 3, _ordinal_oof(common[CONSTRUCT].values, y, folds))
    row("8-feature ordinal", 8, _ordinal_oof(common[DISPLAY].values, y, folds))
    row("8-feature ridge", 8, _ridge_oof(common[DISPLAY].values, y, folds))
    print("  * = the candidate. Human rater-vs-rater QWK is 0.63-0.73 (companion study).")

    print("\n" + "=" * 78)
    print("PART B -- shippable models on their OWN coverage (the real deployment numbers)")
    print("=" * 78)
    print(f"  {'model':22s} {'n':>4s} {'coverage':>9s} {'exact%':>7s} {'QWK':>6s}")
    N = len(df)
    for label, cols in [("texture-only", [TEXTURE]), ("3-construct  *", CONSTRUCT),
                        ("8-feature", DISPLAY)]:
        sub = df[cols + ["q"]].dropna().reset_index(drop=True)
        ys = sub["q"].astype(int).values
        fk = np.full(len(ys), -1)
        for k, (_, te) in enumerate(StratifiedKFold(10, shuffle=True, random_state=0).split(ys, ys)):
            fk[te] = k
        pred = _ordinal_oof(sub[cols].values, ys, fk)
        m = _metrics(ys, pred)
        print(f"  {label:22s} {len(ys):>4d} {len(ys)/N*100:>8.1f}% {m['exact']:>7.1f} {m['qwk']:>6.3f}")
    return cand


# ----------------------------------------------- chosen model's properties ---
def candidate_properties(df):
    print("\n" + "=" * 78)
    print("THE CHOSEN MODEL -- 3-construct ordinal logit: properties that make it ROBUST")
    print("=" * 78)
    sub = df[CONSTRUCT + ["q"]].dropna().reset_index(drop=True)
    X = sub[CONSTRUCT].values
    mu, sd = _zfit(X)
    Z = (X - mu) / sd
    y = sub["q"].astype(int).values
    res = OrderedModel(y, Z, distr="logit").fit(method="bfgs", disp=False, maxiter=300)

    print(f"\n  fitted on n={len(y)} (its full coverage). Coefficients (standardized):")
    print(f"  {'axis':14s} {'beta':>7s} {'p':>8s}  reading")
    reading = {SIZE: "bigger muscle -> better grade",
               FAT: "more fat -> worse grade",
               TEXTURE: "more contrast -> better grade"}
    for i, f in enumerate(CONSTRUCT):
        b, p = res.params[i], res.pvalues[i]
        print(f"  {NAME[f]:14s} {b:>+7.2f} {p:>8.4f}  {reading[f]}")

    # bootstrap sign-stability (should be rock-solid: the 3 axes aren't collinear)
    rng = np.random.default_rng(0)
    signs = {f: [] for f in CONSTRUCT}
    for _ in range(300):
        idx = rng.integers(0, len(Z), len(Z))
        try:
            r = OrderedModel(y[idx], Z[idx], distr="logit").fit(method="bfgs", disp=False, maxiter=300)
            for i, f in enumerate(CONSTRUCT):
                signs[f].append(np.sign(r.params[i]))
        except Exception:
            pass
    print("\n  bootstrap sign-stability (300 resamples; 100% = the weight never flips):")
    for f in CONSTRUCT:
        a = np.array(signs[f])
        print(f"    {NAME[f]:14s} {max((a > 0).mean(), (a < 0).mean()) * 100:>5.0f}%")

    # VIF among the 3 inputs -- should be tiny (the whole point)
    print("\n  collinearity among the 3 inputs (VIF; the 8-feature model hit 40):")
    for i, f in enumerate(CONSTRUCT):
        others = [j for j in range(3) if j != i]
        b, *_ = np.linalg.lstsq(np.column_stack([np.ones(len(Z)), Z[:, others]]), Z[:, i], rcond=None)
        r2 = 1 - np.var(Z[:, i] - np.column_stack([np.ones(len(Z)), Z[:, others]]) @ b) / np.var(Z[:, i])
        print(f"    {NAME[f]:14s} VIF={1 / max(1e-9, 1 - r2):>4.1f}")
    return res, mu, sd


# ---------------------------------------------------- coverage + bias check ---
def coverage_and_bias(df):
    print("\n" + "=" * 78)
    print("COVERAGE + COMPLETE-CASE BIAS -- is the 3-input subset less skewed than the 8?")
    print("=" * 78)
    N = len(df)
    for label, cols in [("3-construct", CONSTRUCT), ("8-feature", DISPLAY)]:
        keep = df[cols].notna().all(axis=1)
        kept, drop = df[keep], df[~keep]
        pres = (kept["quality_label"] == "PRESERVED").mean() * 100
        poor = (kept["quality_label"] == "POOR").mean() * 100
        print(f"  {label:12s} coverage {keep.mean()*100:>5.1f}%  kept: PRES {pres:.0f}% / POOR {poor:.0f}%"
              f"  median age {kept['age'].median():.0f}")
    full_pres = (df["quality_label"] == "PRESERVED").mean() * 100
    full_poor = (df["quality_label"] == "POOR").mean() * 100
    print(f"  {'full cohort':12s} coverage 100.0%  ref:  PRES {full_pres:.0f}% / POOR {full_poor:.0f}%"
          f"  median age {df['age'].median():.0f}")


# --------------------------------------- condition score + worked examples ---
def condition_examples(df, res, mu, sd):
    print("\n" + "=" * 78)
    print("THE OUTPUT -- one condition score (0-100, higher=better) + the breakdown")
    print("=" * 78)
    sub = df[CONSTRUCT + ["quality_label", "q", "age", "sex", "bmi"]].dropna().reset_index(drop=True)
    Z = (sub[CONSTRUCT].values - mu) / sd
    probs = res.model.predict(res.params, exog=Z)           # n x 3
    egrade = probs @ np.array([0, 1, 2])                    # expected grade 0..2
    sub["condition"] = (1 - egrade / 2) * 100               # 0..100, 100=best
    # condition-oriented axis percentile (100 = best end of that axis)
    for f, better_high in [(SIZE, True), (FAT, False), (TEXTURE, True)]:
        pct = sub[f].rank(pct=True) * 100
        sub[f + "_p"] = pct if better_high else 100 - pct

    def show(label, r):
        print(f"\n  {label}")
        print(f"    condition score = {r['condition']:.0f}/100   (doctors' grade: {r['quality_label']})")
        print(f"    breakdown ->  size {r[SIZE+'_p']:>3.0f}   fat {r[FAT+'_p']:>3.0f}   "
              f"tissue {r[TEXTURE+'_p']:>3.0f}   (each 0-100, higher=better)")

    show("a clearly-preserved muscle", sub.loc[sub["condition"].idxmax()])
    show("a clearly-poor muscle", sub.loc[sub["condition"].idxmin()])
    # the case the single number alone would hide: size and tissue disagree most
    split = (sub[SIZE + "_p"] - sub[TEXTURE + "_p"]).abs()
    show("a SPLIT case (big muscle, poor tissue) -- why the breakdown matters", sub.loc[split.idxmax()])


def main():
    df = pd.read_csv(CSV)
    df["q"] = df["quality_label"].map(ORD)
    print(f"cohort: {CSV}  n={len(df)}  "
          f"(PRESERVED {(df.quality_label=='PRESERVED').sum()} / "
          f"INTERMEDIATE {(df.quality_label=='INTERMEDIATE').sum()} / "
          f"POOR {(df.quality_label=='POOR').sum()})\n")
    bakeoff(df)
    res, mu, sd = candidate_properties(df)
    coverage_and_bias(df)
    condition_examples(df, res, mu, sd)


if __name__ == "__main__":
    main()

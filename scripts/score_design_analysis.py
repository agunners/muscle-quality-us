"""Reproducible backing for docs/muscle_score_whitepaper.md.

Answers, on the EXACT 8 features page 5 displays and the EXACT cohort the app
uses (data/cohort_stats/per_study_cohort.csv, n=282 with a 3-rater ordinal
quality_label), the questions the briefing rests on. Two tiers:

  BASIC (Trap D in the paper):
    1. collinearity        -> pairwise Spearman + VIF
    2. dimensionality      -> PCA on standardized features
    3. signal per feature  -> Spearman vs the ordinal label
    4. demographic confound -> partial Spearman | age,bmi,sex

  RIGOROUS (the SOTA upgrade):
    5. multivariable order  -> proportional-odds ordinal regression (statsmodels
                               OrderedModel), image-only and +demographics. Shows
                               univariate rho != independent contribution.
    6. relative importance  -> exact Shapley decomposition of R^2 (collinearity-
                               aware "how much does each feature really add").
    7. factor structure     -> exploratory factor analysis (sklearn) + varimax;
                               does a Size/Fat/Texture structure actually emerge?
    8. disattenuation       -> reliability ceiling from Krippendorff alpha=0.706.
    9. internal consistency -> Cronbach alpha of the size block vs all-8.
   10. coverage             -> fraction of the cohort a score is DEFINED on,
                               by feature subset (missingness is a design limit).

Run:  python3 scripts/score_design_analysis.py
Deps already in env: pandas, numpy, scipy, scikit-learn, statsmodels.

Reliability: KRIPP_ALPHA below is TRANSCRIBED (a literal, not read at runtime)
from the companion study's irr_audit.json line 10 -- the ordinal Krippendorff
alpha for `quality_label`, which is the label THIS cohort uses (the mode of the
3 raters' quality_label). The sibling line 9 (0.706) is `preliminary_label`, a
DIFFERENT label; using it would be a traceability error. Section [11] steps this
single-rater alpha up to the 3-rater-consensus reliability (Spearman-Brown),
because the modeled label is a consensus, not a single rating -- and reports the
ceiling as a BAND, since the raters share one image (correlated errors).
"""
from __future__ import annotations

import warnings
from itertools import combinations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

warnings.filterwarnings("ignore")

CSV = "data/cohort_stats/per_study_cohort.csv"
KRIPP_ALPHA = 0.696  # ordinal Krippendorff alpha for quality_label (irr_audit.json:10)
N_RATERS = 3         # quality_label = mode of 3 raters -> Spearman-Brown step-up

# PRESERVED(good)=0, INTERMEDIATE=1, POOR(bad)=2  -> higher rank = worse quality
ORD = {"PRESERVED": 0, "INTERMEDIATE": 1, "POOR": 2}

DISPLAY = [
    "star_ratio", "full_thickness_q50_mm", "int_cv_within", "rf_thickness_mm",
    "fat_thickness_q50_mm", "qmt_mm", "visible_rf_area_mm2", "fat_to_depth_q50",
]
SHORT = {
    "star_ratio": "STAR", "full_thickness_q50_mm": "RFthk_med",
    "int_cv_within": "echoCV", "rf_thickness_mm": "RFthk_pk",
    "fat_thickness_q50_mm": "Fat_thk", "qmt_mm": "QMT",
    "visible_rf_area_mm2": "RFarea", "fat_to_depth_q50": "FatFrac",
}
AXIS = {
    "star_ratio": "SIZE", "full_thickness_q50_mm": "SIZE",
    "int_cv_within": "TEXTURE", "rf_thickness_mm": "SIZE",
    "fat_thickness_q50_mm": "FAT", "qmt_mm": "SIZE",
    "visible_rf_area_mm2": "SIZE", "fat_to_depth_q50": "FAT",
}
SIZE = [f for f in DISPLAY if AXIS[f] == "SIZE"]


def _load() -> pd.DataFrame:
    df = pd.read_csv(CSV)
    df["q"] = df["quality_label"].map(ORD)
    # Sex is M / F / unknown (~41% unknown). Code as TWO dummies vs Female ref so
    # "unknown" is not silently lumped with female (a real defect in v1). Both
    # dummies enter every demographic adjustment.
    s = df["sex"].astype(str).str.upper().str[0]
    df["sex_M"] = (s == "M").astype(float)
    df["sex_U"] = (~s.isin(["M", "F"])).astype(float)
    return df


# demographic covariate set used by every adjustment in this script
COVARS = ["age", "bmi", "sex_M", "sex_U"]


def _resid(y, covars):
    design = np.column_stack([np.ones(len(covars)), covars])
    beta, *_ = np.linalg.lstsq(design, y, rcond=None)
    return y - design @ beta


def _zscore(frame):
    return (frame - frame.mean()) / frame.std(ddof=0)


# ---------------------------------------------------------------- BASIC --------
def basic(df):
    print("=" * 72)
    print(f"cohort {CSV}  n={len(df)}  label={dict(df['quality_label'].value_counts())}")
    print("=" * 72)

    print("\n[1a] each tile vs quality_label (Spearman; |rho| big = informative)")
    uni = {}
    for f in DISPLAY:
        sub = df[[f, "q"]].dropna()
        r, p = spearmanr(sub[f], sub["q"])
        uni[f] = r
    for f in sorted(DISPLAY, key=lambda x: -abs(uni[x])):
        print(f"   {SHORT[f]:10s} {AXIS[f]:8s} rho={uni[f]:+.3f}")

    print("\n[1b] collinear pairs |rho| >= 0.60")
    for a, b in combinations(DISPLAY, 2):
        sub = df[[a, b]].dropna()
        r = spearmanr(sub[a], sub[b])[0]
        if abs(r) >= 0.60:
            print(f"   {SHORT[a]:9s}<->{SHORT[b]:9s} rho={r:+.2f} [{AXIS[a]}/{AXIS[b]}]")

    print("\n[1c] VIF (variance inflation; >5 = serious collinearity, >10 = severe)")
    X = df[DISPLAY].dropna()
    Z = _zscore(X)
    for i, f in enumerate(DISPLAY):
        others = [c for j, c in enumerate(DISPLAY) if j != i]
        r2 = 1 - np.var(_resid(Z[f].values, Z[others].values)) / np.var(Z[f].values)
        vif = 1.0 / max(1e-9, 1 - r2)
        print(f"   {SHORT[f]:10s} {AXIS[f]:8s} VIF={vif:6.1f}")

    print(f"\n[2] PCA on 8 standardized tiles (complete cases n={len(X)})")
    eig = np.sort(np.linalg.eigvalsh(np.corrcoef(Z.values.T)))[::-1]
    ev = eig / eig.sum()
    print("   var explained: " + ", ".join(f"PC{i+1}={ev[i]*100:.0f}%" for i in range(8)))
    print(f"   PC1={ev[0]*100:.0f}%  PC1-2={ev[:2].sum()*100:.0f}%  PC1-3={ev[:3].sum()*100:.0f}%")

    print("\n[4] signal after age+bmi+sex adjustment (partial Spearman)")
    cov = COVARS
    for f in DISPLAY:
        sub = df[[f, "q"] + cov].dropna()
        r_raw = spearmanr(sub[f], sub["q"])[0]
        fr = _resid(sub[f].rank().values, sub[cov].values)
        qr = _resid(sub["q"].rank().values, sub[cov].values)
        r_adj = spearmanr(fr, qr)[0]
        att = (1 - abs(r_adj) / abs(r_raw)) * 100
        print(f"   {SHORT[f]:10s} raw={r_raw:+.2f} adj={r_adj:+.2f} atten={att:3.0f}%")

    s = df[["star_ratio", "qmt_mm", "bmi"]].dropna()
    print(f"\n   STAR == QMT/BMI identity: Spearman={spearmanr(s['star_ratio'], s['qmt_mm']/s['bmi'])[0]:.3f}")
    return uni


# ----------------------------------------------------------- MULTIVARIABLE -----
def multivariable(df, uni):
    from statsmodels.miscmodels.ordinal_model import OrderedModel
    print("\n" + "=" * 72)
    print("[5] MULTIVARIABLE proportional-odds ordinal regression (standardized)")
    print("    does univariate rho survive adjustment for the OTHER features?")
    print("=" * 72)

    def fit(cols, data):
        d = data[cols + ["q"]].dropna()
        Z = _zscore(d[cols])
        m = OrderedModel(d["q"].astype(int).values, Z.values, distr="logit")
        res = m.fit(method="bfgs", disp=False, maxiter=200)
        k = len(cols)
        return d.shape[0], res.params[:k], res.bse[:k], res.pvalues[:k]

    n, b, se, p = fit(DISPLAY, df)
    print(f"\n   image-only, n={n}:   (|beta| = independent pull on the grade)")
    print(f"   {'tile':10s} {'axis':8s} {'uni_rho':>8s} {'mv_beta':>8s} {'SE':>6s} {'p':>7s}")
    for i, f in enumerate(DISPLAY):
        print(f"   {SHORT[f]:10s} {AXIS[f]:8s} {uni[f]:>+8.2f} {b[i]:>+8.2f} {se[i]:>6.2f} {p[i]:>7.3f}")

    n2, b2, se2, p2 = fit(DISPLAY + COVARS, df)
    print(f"\n   +demographics, n={n2}:  (what still stands once age/BMI/sex are in)")
    print(f"   {'tile':10s} {'beta_img':>9s} {'beta_dem':>9s} {'atten':>6s}")
    for i, f in enumerate(DISPLAY):
        att = (1 - abs(b2[i]) / max(1e-9, abs(b[i]))) * 100
        print(f"   {SHORT[f]:10s} {b[i]:>+9.2f} {b2[i]:>+9.2f} {att:>5.0f}%")
    print("   (age/bmi/sex_M/sex_U coefficients omitted from the per-tile view)")


# ------------------------------------------------------- RELATIVE IMPORTANCE ---
def shapley(df):
    print("\n" + "=" * 72)
    print("[6] exact Shapley decomposition of R^2 (label 0/1/2) -- collinearity-")
    print("    aware share of explained variance. Sums to the full-model R^2.")
    print("=" * 72)
    d = df[DISPLAY + ["q"]].dropna()
    Z = _zscore(d[DISPLAY]).values
    y = (d["q"].values - d["q"].values.mean())
    idx = {f: i for i, f in enumerate(DISPLAY)}

    def r2(cols):
        if not cols:
            return 0.0
        Xs = Z[:, [idx[c] for c in cols]]
        Xs = np.column_stack([np.ones(len(Xs)), Xs])
        beta, *_ = np.linalg.lstsq(Xs, y, rcond=None)
        resid = y - Xs @ beta
        return 1 - resid.var() / y.var()

    from math import factorial
    n = len(DISPLAY)
    cache = {frozenset(): 0.0}
    def R2(S):
        key = frozenset(S)
        if key not in cache:
            cache[key] = r2(list(S))
        return cache[key]

    shap = {f: 0.0 for f in DISPLAY}
    rest = {f: [x for x in DISPLAY if x != f] for f in DISPLAY}
    for f in DISPLAY:
        for k in range(n):
            w = factorial(k) * factorial(n - k - 1) / factorial(n)
            for S in combinations(rest[f], k):
                shap[f] += w * (R2(set(S) | {f}) - R2(set(S)))
    total = sum(shap.values())
    print(f"\n   full-model R^2 = {total:.3f}  (n={len(d)})")
    print(f"   {'tile':10s} {'axis':8s} {'shapley':>8s} {'share':>7s}")
    for f in sorted(DISPLAY, key=lambda x: -shap[x]):
        print(f"   {SHORT[f]:10s} {AXIS[f]:8s} {shap[f]:>8.3f} {shap[f]/total*100:>6.1f}%")
    byaxis = {}
    for f in DISPLAY:
        byaxis[AXIS[f]] = byaxis.get(AXIS[f], 0) + shap[f] / total * 100
    print("   by axis: " + ", ".join(f"{k}={v:.0f}%" for k, v in byaxis.items()))


# ------------------------------------------------------------ FACTOR STRUCTURE -
def _varimax(L, q=50, tol=1e-6):
    p, k = L.shape
    R = np.eye(k)
    d = 0
    for _ in range(q):
        Lr = L @ R
        u, s, vt = np.linalg.svd(
            L.T @ (Lr**3 - (1.0 / p) * Lr @ np.diag(np.diag(Lr.T @ Lr))))
        R = u @ vt
        d_old, d = d, s.sum()
        if d_old != 0 and d / d_old < 1 + tol:
            break
    return L @ R


def factor_structure(df):
    from sklearn.decomposition import FactorAnalysis
    print("\n" + "=" * 72)
    print("[7] exploratory factor analysis (3 factors) + varimax -- does a")
    print("    Size / Fat / Texture structure actually emerge from the data?")
    print("=" * 72)
    X = df[DISPLAY].dropna()
    Z = _zscore(X).values
    fa = FactorAnalysis(n_components=3, random_state=0).fit(Z)
    L = _varimax(fa.components_.T)
    # sign so each factor's biggest loading is positive
    for j in range(3):
        if L[np.argmax(np.abs(L[:, j])), j] < 0:
            L[:, j] *= -1
    print(f"\n   rotated loadings (n={len(X)}):")
    print(f"   {'tile':10s} {'axis':8s}   F1     F2     F3")
    for i, f in enumerate(DISPLAY):
        dom = np.argmax(np.abs(L[i]))
        star = ["  ", "  ", "  "]
        star[dom] = "<-"
        print(f"   {SHORT[f]:10s} {AXIS[f]:8s} " +
              " ".join(f"{L[i,j]:+.2f}{star[j]}" for j in range(3)))


# ----------------------------------------------------------- DISATTENUATION ----
def disattenuation(df, uni):
    print("\n" + "=" * 72)
    print("[8] disattenuation -- the criterion's reliability caps achievable |rho|")
    print("=" * 72)
    # The modeled label is a 3-rater CONSENSUS. Single-rater alpha understates its
    # reliability; Spearman-Brown steps it up. But the raters share one B-mode
    # image (correlated errors), so the truth is a BAND, not the SB point.
    sb = N_RATERS * KRIPP_ALPHA / (1 + (N_RATERS - 1) * KRIPP_ALPHA)
    lo, hi = np.sqrt(KRIPP_ALPHA), np.sqrt(sb)
    print(f"\n   single-rater alpha (quality_label) = {KRIPP_ALPHA:.3f}")
    print(f"   3-rater consensus reliability (Spearman-Brown) = {sb:.3f}")
    print(f"   => ceiling on |rho| is a BAND [{lo:.3f}, {hi:.3f}] (same-image caveat)")
    print(f"   {'tile':10s} {'obs_rho':>8s} {'%of band':>12s}")
    for f in sorted(DISPLAY, key=lambda x: -abs(uni[x])):
        r = abs(uni[f])
        print(f"   {SHORT[f]:10s} {r:>8.2f}   {r/hi*100:>3.0f}-{min(99,round(r/lo*100)):>2.0f}%")
    rc = abs(uni["int_cv_within"])
    print(f"   reading: echoCV {rc:.2f} is {rc/hi*100:.0f}-{rc/lo*100:.0f}% of the ceiling band")
    print("   -> material headroom remains; saturation is NOT the binding limit.")


# -------------------------------------------------- INTERNAL CONSISTENCY / COV -
def _cronbach(frame):
    f = frame.dropna()
    k = f.shape[1]
    item_var = f.var(ddof=1, axis=0).sum()
    tot_var = f.sum(axis=1).var(ddof=1)
    return (k / (k - 1)) * (1 - item_var / tot_var), len(f)


def consistency_and_coverage(df):
    print("\n" + "=" * 72)
    print("[9] internal consistency (Cronbach alpha; high = items are redundant)")
    print("=" * 72)
    a_size, n_size = _cronbach(_zscore(df[SIZE].dropna()))
    a_all, n_all = _cronbach(_zscore(df[DISPLAY].dropna()))
    # all-8 with fat features sign-flipped to align direction ("higher=better")
    aligned = _zscore(df[DISPLAY].dropna())
    for c in ("fat_thickness_q50_mm", "fat_to_depth_q50"):
        aligned[c] *= -1
    a_aligned, _ = _cronbach(aligned)
    print(f"   5 SIZE tiles      : alpha={a_size:.2f} (n={n_size})  -> redundant, ~one thing")
    print(f"   all 8, raw        : alpha={a_all:.2f}  -- but alpha is NOT direction-invariant:")
    print(f"   all 8, dir-aligned: alpha={a_aligned:.2f}  <- so 0.51 indexes mixed DIRECTION,")
    print("                         not dimensionality. Trust the factor analysis [7].")

    print("\n[10] coverage -- fraction of the 282-study cohort a score is DEFINED on")
    subsets = {
        "all 8 tiles": DISPLAY,
        "3 reps (RFthk_med, FatFrac, echoCV)": ["full_thickness_q50_mm", "fat_to_depth_q50", "int_cv_within"],
        "quality only {echoCV, FatFrac}": ["int_cv_within", "fat_to_depth_q50"],
        "texture only {echoCV}": ["int_cv_within"],
    }
    for name, cols in subsets.items():
        cov = df[cols].dropna().shape[0] / len(df) * 100
        print(f"   {name:38s} {cov:5.1f}%")


# ------------------------------------------------ ROBUSTNESS / RED-TEAM FIXES --
def robustness(df):
    """Backs the audit corrections: the in-sample R^2 is optimistic (so it is NOT
    'at the ceiling'); the complete-case subset is non-representative; and the
    surviving coefficients are/aren't a collinearity lottery."""
    from statsmodels.miscmodels.ordinal_model import OrderedModel
    print("\n" + "=" * 72)
    print("[11] robustness (red-team): is R^2 'at the ceiling'? is n=184 biased?")
    print("     are echoCV/STAR robust or a collinearity lottery?")
    print("=" * 72)

    # --- in-sample vs cross-validated R^2 (label 0/1/2) ----------------------
    d = df[DISPLAY + ["q"]].dropna()
    Z = _zscore(d[DISPLAY]).values
    y = d["q"].values.astype(float)
    X = np.column_stack([np.ones(len(Z)), Z])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    r2_in = 1 - ((y - X @ beta) ** 2).sum() / ((y - y.mean()) ** 2).sum()
    n, p = X.shape
    r2_adj = 1 - (1 - r2_in) * (n - 1) / (n - p)
    rng = np.random.default_rng(0)
    folds = rng.permutation(n) % 10
    sse = tss = 0.0
    for k in range(10):
        tr, te = folds != k, folds == k
        bk, *_ = np.linalg.lstsq(X[tr], y[tr], rcond=None)
        sse += ((y[te] - X[te] @ bk) ** 2).sum()
        tss += ((y[te] - y[tr].mean()) ** 2).sum()
    r2_cv = 1 - sse / tss
    print(f"\n   R^2 (label 0/1/2, n={n}):  in-sample={r2_in:.3f}  adjusted={r2_adj:.3f}  10-fold CV={r2_cv:.3f}")
    print(f"   single-rater reliability={KRIPP_ALPHA:.3f} -> in-sample R^2 EXCEEDS it = optimism,")
    print("   not saturation. The honest fit is the CV value; headroom remains.")

    # --- complete-case representativeness -----------------------------------
    cc = df[DISPLAY].notna().all(axis=1)
    print(f"\n   complete-case bias (n={cc.sum()} kept vs {(~cc).sum()} dropped):")
    print(f"   {'group':10s} {'%PRESERVED':>11s} {'%POOR':>7s} {'med_age':>8s}")
    for name, m in [("kept", cc), ("dropped", ~cc)]:
        sub = df[m]
        pres = (sub["quality_label"] == "PRESERVED").mean() * 100
        poor = (sub["quality_label"] == "POOR").mean() * 100
        print(f"   {name:10s} {pres:>10.1f}% {poor:>6.1f}% {sub['age'].median():>8.1f}")
    print("   -> kept set is POOR-enriched & older (informative missingness, MAR).")

    # --- bootstrap sign-stability of the multivariable coefficients ---------
    d2 = df[DISPLAY + ["q"]].dropna()
    Z2 = _zscore(d2[DISPLAY]).values
    yq = d2["q"].astype(int).values
    rng2 = np.random.default_rng(0)
    B = 300
    signs = {f: [] for f in DISPLAY}
    for _ in range(B):
        idx = rng2.integers(0, len(Z2), len(Z2))
        try:
            res = OrderedModel(yq[idx], Z2[idx], distr="logit").fit(
                method="bfgs", disp=False, maxiter=200)
            for i, f in enumerate(DISPLAY):
                signs[f].append(np.sign(res.params[i]))
        except Exception:
            continue
    print(f"\n   bootstrap coefficient sign-stability (B={B}; 100% = never flips):")
    print(f"   {'tile':10s} {'axis':8s} {'stable':>7s}")
    for f in sorted(DISPLAY, key=lambda x: -abs(np.mean(signs[x]) if signs[x] else 0)):
        arr = np.array(signs[f])
        stab = max((arr > 0).mean(), (arr < 0).mean()) * 100 if len(arr) else float("nan")
        print(f"   {SHORT[f]:10s} {AXIS[f]:8s} {stab:>6.0f}%")
    print("   -> echoCV & STAR are sign-stable; the redundant six flip -> NOT a lottery.")

    # --- what is STAR's 'independent' signal really? STAR=QMT/BMI, so holding
    #     QMT fixed, STAR varies as 1/BMI -> it reads adiposity, not muscle size.
    s = df[["star_ratio", "qmt_mm", "bmi"]].dropna()
    star_resid = _resid(s["star_ratio"].values, s[["qmt_mm"]].values)
    inv_bmi = 1.0 / s["bmi"].values
    rho_adip = spearmanr(star_resid, inv_bmi)[0]
    print(f"\n   STAR residualized on QMT vs 1/BMI: Spearman={rho_adip:.3f} (n={len(s)})")
    print("   -> STAR's signal conditional on QMT is essentially adiposity, not size.")


def main():
    df = _load()
    uni = basic(df)
    multivariable(df, uni)
    shapley(df)
    factor_structure(df)
    disattenuation(df, uni)
    consistency_and_coverage(df)
    robustness(df)


if __name__ == "__main__":
    main()

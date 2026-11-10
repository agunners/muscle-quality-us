"""Re-derive the sign of every axis on BOTH anchors, to settle whether the
texture (fascicular-contrast) direction was a reversal or a confirmation.

Anchor 1 (independent, physical): isometric knee-extension torque, n=56, above demographics.
Anchor 2 (doctors' eye):          ordinal quality grade on the old 282.

Prints standardized betas with 95% CIs so the DIRECTION is readable separately
from the significance.
"""
import numpy as np
import pandas as pd
from scipy import stats

df = pd.read_csv("data/qa_master.csv")

CONF = ["sex_m", "weight_kg", "height_cm", "age"]
df["sex_m"] = (df["sex"].astype(str).str.upper().str[0].isin(["M", "E"])).astype(float)


def ols(X, y):
    """Return beta, resid-var, R2, n, k using plain least squares with intercept."""
    X = np.column_stack([np.ones(len(X)), X])
    beta, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    sse = float(resid @ resid)
    sst = float(((y - y.mean()) ** 2).sum())
    return beta, sse, 1 - sse / sst, len(y), X.shape[1], X


def nested(sub, base_cols, add_col, outcome):
    """Partial-F of add_col over base_cols. Returns dict with beta/CI/dR2/p."""
    cols = base_cols + [add_col]
    d = sub.dropna(subset=cols + [outcome]).copy()
    if len(d) < 12:
        return None
    y = d[outcome].to_numpy(float)
    # z-score predictors so beta = "per 1 SD"
    Z = d[cols].astype(float).copy()
    for c in cols:
        s = Z[c].std(ddof=0)
        Z[c] = (Z[c] - Z[c].mean()) / (s if s else 1.0)

    _, sse0, r20, n, _, _ = ols(Z[base_cols].to_numpy(float), y)
    beta, sse1, r21, _, k1, X1 = ols(Z[cols].to_numpy(float), y)

    dfe = n - k1
    f = ((sse0 - sse1) / 1) / (sse1 / dfe)
    p = 1 - stats.f.cdf(f, 1, dfe)
    # SE of the last coefficient
    XtXi = np.linalg.pinv(X1.T @ X1)
    se = float(np.sqrt(sse1 / dfe * XtXi[-1, -1]))
    b = float(beta[-1])
    tcrit = stats.t.ppf(0.975, dfe)
    return dict(n=n, beta=b, lo=b - tcrit * se, hi=b + tcrit * se,
                dR2=r21 - r20, p=float(p), R2_base=r20)


print("=" * 78)
print("ANCHOR 1 — ISOMETRIC TORQUE (independent physical criterion), above demographics")
print("=" * 78)
force = df.dropna(subset=["izo_torque_nm"] + CONF)
print(f"force rows with complete demographics: n={len(force)}")
m0 = force.dropna(subset=CONF + ["izo_torque_nm"])
_, _, r2b, nb, _, _ = ols(m0[CONF].astype(float).to_numpy(), m0["izo_torque_nm"].to_numpy(float))
print(f"M0  torque ~ sex+weight+height+age : R2={r2b:.3f}  n={nb}\n")

AXES = [("size    star_ratio", "star_ratio"),
        ("size    qmt_mm", "qmt_mm"),
        ("size    full_thickness_q50_mm", "full_thickness_q50_mm"),
        ("fat     fat_to_depth_q50", "fat_to_depth_q50"),
        ("TEXTURE int_cv_within", "int_cv_within"),
        ("compos. condition_score", "condition_score")]

print(f"{'axis':32} {'n':>4} {'beta/SD':>9} {'95% CI':>20} {'dR2':>7} {'p':>7}  sign")
print("-" * 90)
for label, col in AXES:
    r = nested(force, CONF, col, "izo_torque_nm")
    if r is None:
        print(f"{label:32}  -- insufficient rows --")
        continue
    sign = "POSITIVE" if r["beta"] > 0 else "negative"
    print(f"{label:32} {r['n']:>4} {r['beta']:>+9.2f} "
          f"[{r['lo']:>+7.2f},{r['hi']:>+7.2f}] {r['dR2']*100:>6.2f}% {r['p']:>7.3f}  {sign}")

print()
print("=" * 78)
print("ANCHOR 2 — DOCTORS' ORDINAL GRADE (old cohort)")
print("=" * 78)
ORDER = {"POOR": 0, "INTERMEDIATE": 1, "PRESERVED": 2}
g = df.dropna(subset=["quality_label"]).copy()
g["grade_ord"] = g["quality_label"].astype(str).str.upper().str.strip().map(ORDER)
g = g.dropna(subset=["grade_ord"])
print(f"graded rows: n={len(g)}   {g['quality_label'].value_counts().to_dict()}\n")
print(f"{'feature':32} {'n':>4} {'Spearman rho':>13} {'p':>9}  reads as")
print("-" * 82)
for label, col in [("TEXTURE int_cv_within", "int_cv_within"),
                   ("fat     fat_to_depth_q50", "fat_to_depth_q50"),
                   ("size    star_ratio", "star_ratio"),
                   ("size    qmt_mm", "qmt_mm"),
                   ("compos. condition_score", "condition_score")]:
    d = g.dropna(subset=[col])
    if len(d) < 10:
        print(f"{label:32}  -- insufficient --")
        continue
    rho, p = stats.spearmanr(d[col].astype(float), d["grade_ord"].astype(float))
    reads = "higher -> MORE PRESERVED" if rho > 0 else "higher -> more POOR"
    print(f"{label:32} {len(d):>4} {rho:>+13.3f} {p:>9.2g}  {reads}")

print()
print("=" * 78)
print("THE JOINT TEST — do the two anchors agree on DIRECTION?")
print("=" * 78)
for label, col in [("TEXTURE int_cv_within", "int_cv_within"),
                   ("fat     fat_to_depth_q50", "fat_to_depth_q50"),
                   ("size    star_ratio", "star_ratio")]:
    rf = nested(force, CONF, col, "izo_torque_nm")
    dg = g.dropna(subset=[col])
    rho, _ = stats.spearmanr(dg[col].astype(float), dg["grade_ord"].astype(float))
    agree = "AGREE" if (rf["beta"] > 0) == (rho > 0) else "DISAGREE"
    print(f"{label:32} force beta {rf['beta']:>+8.2f} | grade rho {rho:>+6.3f}  -> {agree}")

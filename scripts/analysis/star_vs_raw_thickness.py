"""VERIFY (2026-07-29): an audit agent claims the score's size axis, `star_ratio`
(= QMT / BMI), DESTROYS criterion validity against measured isometric torque —
raw QMT reaching rho +0.590 while STAR falls to +0.239, and plain RF thickness
reaching +0.719.

If true, the shipped score divides away real signal on 33% of its weight.
Checked two ways: raw Spearman (the agent's method) and demographic-adjusted
partial-F (the method the force pre-registration actually used), because those
can disagree and only the second is comparable to the filed result.
"""
import numpy as np
import pandas as pd
from scipy import stats

d = pd.read_csv("data/qa_master.csv")
d["sex_m"] = (d["sex"].astype(str).str.upper().str[0].isin(["M", "E"])).astype(float)
CONF = ["sex_m", "weight_kg", "height_cm", "age"]
CANDS = ["qmt_mm", "star_ratio", "full_thickness_q50_mm", "rf_thickness_mm", "bmi"]

print("=" * 78)
print("RAW SPEARMAN vs isometric torque  (the agent's method)")
print("=" * 78)
print(f"{'feature':26} {'n':>4} {'rho':>8} {'p':>9}")
print("-" * 50)
raw = {}
for c in CANDS:
    s = d.dropna(subset=[c, "izo_torque_nm"])
    if len(s) < 10:
        continue
    r, p = stats.spearmanr(s[c].astype(float), s.izo_torque_nm.astype(float))
    raw[c] = (r, len(s))
    print(f"{c:26} {len(s):>4} {r:>+8.3f} {p:>9.2g}")

# bootstrap the qmt-vs-star gap on the paired subset
pair = d.dropna(subset=["qmt_mm", "star_ratio", "izo_torque_nm"])
rng = np.random.default_rng(0)
gaps = []
for _ in range(2000):
    i = rng.integers(0, len(pair), len(pair))
    s = pair.iloc[i]
    gaps.append(stats.spearmanr(s.qmt_mm, s.izo_torque_nm)[0]
                - stats.spearmanr(s.star_ratio, s.izo_torque_nm)[0])
lo, hi = np.percentile(gaps, [2.5, 97.5])
print(f"\n  qmt_mm minus star_ratio gap = {raw['qmt_mm'][0]-raw['star_ratio'][0]:+.3f}"
      f"  95% bootstrap CI [{lo:+.3f}, {hi:+.3f}]  n={len(pair)}"
      f"  -> {'EXCLUDES zero' if lo > 0 else 'includes zero'}")


def ols(X, y):
    X = np.column_stack([np.ones(len(X)), X])
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    r = y - X @ b
    return float(r @ r), 1 - float(r @ r) / float(((y - y.mean()) ** 2).sum()), X.shape[1]


print()
print("=" * 78)
print("DEMOGRAPHIC-ADJUSTED  (the pre-registration's method — the comparable one)")
print("=" * 78)
print(f"{'feature':26} {'n':>4} {'dR2':>8} {'p':>9}")
print("-" * 50)
for c in CANDS:
    s = d.dropna(subset=CONF + [c, "izo_torque_nm"])
    if len(s) < 12:
        continue
    y = s.izo_torque_nm.to_numpy(float)
    Z = s[CONF + [c]].astype(float)
    Z = (Z - Z.mean()) / Z.std(ddof=0).replace(0, 1)
    sse0, r20, _ = ols(Z[CONF].to_numpy(), y)
    sse1, r21, k1 = ols(Z.to_numpy(), y)
    dfe = len(s) - k1
    f = (sse0 - sse1) / (sse1 / dfe)
    print(f"{c:26} {len(s):>4} {(r21-r20)*100:>7.2f}% {1-stats.f.cdf(f,1,dfe):>9.3f}")

print()
print("=" * 78)
print("IS OUR IMAGING MORE REPEATABLE THAN THE DYNAMOMETER IT IS VALIDATED AGAINST?")
print("=" * 78)
for col, lab in [("izo_cv_pct", "isometric torque"), ("isok_cv_pct", "isokinetic torque")]:
    s = d[col].dropna().astype(float)
    if len(s):
        print(f"  {lab:20} within-subject repeat CV: median {s.median():.2f}%  "
              f"p75 {s.quantile(.75):.2f}%  n={len(s)}")
print("  imaging RF-thickness CV (agent's same-session pair estimate): 3.77% / 3.93%")
print("  -> compare directly; if imaging is lower, the criterion is the noisier instrument.")

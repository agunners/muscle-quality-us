"""Is the new 66 distributionally different from the old 282 on the SHARED features?

The score's coefficients + bands were fitted on the old 282. If the new cohort's
features sit elsewhere, the score is being applied off-calibration. This CANNOT
separate device/protocol shift from population shift (new round is healthier) —
which is exactly the point: only a common anchor (a doctor's grade on BOTH) can.
"""
import numpy as np
import pandas as pd
from scipy import stats

d = pd.read_csv("data/qa_master.csv")
FEATS = ["star_ratio", "qmt_mm", "fat_to_depth_q50", "int_cv_within", "condition_score"]

old = d[d.cohort_round == "old"]
new = d[d.cohort_round == "new"]

print("=" * 92)
print("FEATURE DISTRIBUTIONS — old 282 (fitted on) vs new 66 (scored with old coefficients)")
print("=" * 92)
print(f"{'feature':22} {'old n':>6} {'old med':>9} {'old SD':>8} "
      f"{'new n':>6} {'new med':>9} {'new SD':>8} {'shift/oldSD':>12} {'KS p':>9}")
print("-" * 92)
for f in FEATS:
    o = old[f].dropna().astype(float)
    n = new[f].dropna().astype(float)
    if len(o) < 5 or len(n) < 5:
        print(f"{f:22}  -- insufficient --")
        continue
    shift = (n.median() - o.median()) / o.std(ddof=0)
    ks = stats.ks_2samp(o, n)
    print(f"{f:22} {len(o):>6} {o.median():>9.3f} {o.std(ddof=0):>8.3f} "
          f"{len(n):>6} {n.median():>9.3f} {n.std(ddof=0):>8.3f} "
          f"{shift:>+12.2f} {ks.pvalue:>9.2g}")

print()
print("=" * 92)
print("WHERE DO THE NEW 66 LAND IN THE OLD COHORT'S SCORE BANDS?")
print("=" * 92)
print("old-cohort grade distribution (the fit population):")
print("   ", old.quality_label.value_counts().to_dict())
print()
osc = old.condition_score.dropna()
nsc = new.condition_score.dropna()
print(f"condition_score   old: median {osc.median():.1f}  IQR {osc.quantile(.25):.1f}-{osc.quantile(.75):.1f}  n={len(osc)}")
print(f"condition_score   new: median {nsc.median():.1f}  IQR {nsc.quantile(.25):.1f}-{nsc.quantile(.75):.1f}  n={len(nsc)}")
# what fraction of new studies exceed the old cohort's upper band edge
for q in (0.5, 0.75, 0.90):
    thr = osc.quantile(q)
    frac = (nsc > thr).mean()
    print(f"   new studies above the old cohort's p{int(q*100)} ({thr:.1f}): {frac*100:.0f}%")

print()
print("=" * 92)
print("PER-GRADE COMPARISON — the check we CANNOT run today")
print("=" * 92)
n_new_graded = new.quality_label.notna().sum()
print(f"new-cohort studies carrying a doctor's grade: {n_new_graded}")
print("→ With 0 graded new studies we cannot ask: 'for studies a doctor calls PRESERVED,")
print("  do old and new features sit in the same place?' — the ONLY way to separate")
print("  device/protocol shift from population shift. Grading the new cohort unlocks it.")

print()
print("=" * 92)
print("POWER — what an incremental head-to-head could actually resolve at n=56")
print("=" * 92)


def min_detectable_dr2(n, k_full, r2_base, alpha=0.05, power=0.80):
    """Smallest ΔR² (1 added term) reaching `power` via the partial-F noncentrality.

    λ = f²·n with f² = ΔR² / (1 − R²_full) and R²_full = R²_base + ΔR². The
    denominator is the FULL model's RESIDUAL variance. Using (1 − ΔR²) instead —
    as a first pass here did — ignores that demographics already absorb ~75% of
    torque, and understates power by ~4×.
    """
    from scipy.optimize import brentq
    dfe = n - k_full - 1
    fcrit = stats.f.ppf(1 - alpha, 1, dfe)

    def gap(dr2):
        resid = 1.0 - r2_base - dr2           # full model's residual variance
        lam = (dr2 / max(1e-9, resid)) * n
        return stats.ncf.sf(fcrit, 1, dfe, lam) - power
    return brentq(gap, 1e-6, (1 - r2_base) * 0.95)


# torque ~ sex+weight+height+age alone is R2 = 0.755 (measured, above)
for k, lab in [(5, "demographics + 1 imaging term"),
               (6, "demographics + grade + score (the head-to-head)")]:
    print(f"  n=56, {lab:46} → min detectable ΔR² ≈ {min_detectable_dr2(56, k, 0.755)*100:.1f}%")
print()
print("  For reference, what the imaging terms actually delivered:")
print("    size +2.4%   fat +2.9%   texture +1.1%   composite +0.5%")
print("  → a head-to-head 'does the score beat the doctor' at n=56 is underpowered")
print("    for anything smaller than a LARGE effect. It is not the reason to do it.")

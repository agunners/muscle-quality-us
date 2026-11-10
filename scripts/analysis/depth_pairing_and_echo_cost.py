"""Akadir 2026-07-29 doc-2, point 3: "not all of the 66 have sweep and center-RF at
the same depth of 4.9 ... for the studies where the depths differ, we can see how much
the depth difference cost in echo intensity and its relevant derivatives."

An earlier agent claimed "same depth, 66/66". Those cannot both be true. Settle it,
then quantify what a depth mismatch costs the texture axis.

Also answers point 7's practical question: does dividing thickness by BMI help or hurt
a WITHIN-PERSON (longitudinal) claim, as opposed to a cross-sectional one?
"""
import numpy as np
import pandas as pd
from scipy import stats

f = pd.read_csv("data/qa_frames.csv")
m = pd.read_csv("data/qa_master.csv")

# ---- 1. per-study depth of the centre-RF still vs the sweep -------------------
dep = (f[f.role.isin(["rf", "sweep"])]
       .groupby(["study_uid", "role"])["depth_cm"].median().unstack())
dep = dep.dropna(subset=["rf", "sweep"])
dep["delta"] = (dep["sweep"] - dep["rf"]).round(3)
same = (dep["delta"].abs() <= 0.05)

print("=" * 78)
print("POINT 3 PREMISE — are the centre-RF still and the sweep at the SAME depth?")
print("=" * 78)
print(f"  studies with both captures: {len(dep)}")
print(f"  SAME depth (|delta| <= 0.05 cm): {same.sum()}   DIFFERENT: {(~same).sum()}")
print(f"\n  depth values used, centre-RF still : {sorted(dep['rf'].unique())}")
print(f"  depth values used, sweep            : {sorted(dep['sweep'].unique())}")
print(f"\n  distribution of (sweep - rf) in cm:")
print("   ", dep["delta"].value_counts().sort_index().to_dict())
print(f"\n  studies where BOTH captures sit at the 4.9 cm protocol depth: "
      f"{((dep['rf'].sub(4.9).abs()<=0.05) & (dep['sweep'].sub(4.9).abs()<=0.05)).sum()}")

# ---- 2. what does a depth mismatch cost the texture axis? ---------------------
print()
print("=" * 78)
print("WHAT A DEPTH MISMATCH COSTS — echo-derived features vs geometry")
print("=" * 78)
j = dep.reset_index().merge(m, on="study_uid", how="inner")
j["abs_delta"] = j["delta"].abs()
print(f"  merged studies: {len(j)}   with a depth mismatch: {(j.abs_delta>0.05).sum()}")
FE = [("int_cv_within", "texture (echo-derived)"),
      ("fat_to_depth_q50", "fat fraction (echo+geometry)"),
      ("qmt_mm", "QMT (pure geometry)"),
      ("rf_thickness_mm", "RF thickness (pure geometry)"),
      ("full_thickness_q50_mm", "RF full thickness (pure geometry)"),
      ("condition_score", "the shipped score")]
print(f"\n{'feature':30} {'n':>4} {'rho vs |depth mismatch|':>24} {'p':>8}")
print("-" * 70)
for c, lab in FE:
    s = j.dropna(subset=[c, "abs_delta"])
    if len(s) < 10 or s.abs_delta.nunique() < 2:
        print(f"{lab:30} {len(s):>4}   -- no depth variation --")
        continue
    r, p = stats.spearmanr(s[c].astype(float), s.abs_delta.astype(float))
    print(f"{lab:30} {len(s):>4} {r:>+24.3f} {p:>8.3f}")

# absolute depth (not mismatch) vs the same features — the confound to separate
print(f"\n{'feature':30} {'n':>4} {'rho vs ABSOLUTE rf depth':>24} {'p':>8}")
print("-" * 70)
for c, lab in FE:
    s = j.dropna(subset=[c, "rf"])
    if len(s) < 10 or s["rf"].nunique() < 2:
        continue
    r, p = stats.spearmanr(s[c].astype(float), s["rf"].astype(float))
    print(f"{lab:30} {len(s):>4} {r:>+24.3f} {p:>8.3f}")

# ---- 3. point 7 — does the BMI denominator help or hurt LONGITUDINALLY? -------
print()
print("=" * 78)
print("POINT 7 — the BMI denominator, cross-sectionally vs within-person")
print("=" * 78)
d = m.dropna(subset=["izo_torque_nm", "qmt_mm", "star_ratio", "bmi", "full_thickness_q50_mm"])
for c in ["full_thickness_q50_mm", "qmt_mm", "star_ratio", "bmi"]:
    r, p = stats.spearmanr(d[c].astype(float), d.izo_torque_nm.astype(float))
    print(f"  cross-sectional rho vs torque   {c:24} {r:>+7.3f}  (p={p:.2g})")
cv_bmi = (m.bmi.std() / m.bmi.mean()) * 100
print(f"\n  cohort BMI: mean {m.bmi.mean():.1f}, SD {m.bmi.std():.1f}  (between-subject CV {cv_bmi:.1f}%)")
print("""
  THE LONGITUDINAL POINT, which the cross-sectional numbers cannot show:
  a ratio carries the noise AND the drift of BOTH terms. Within one person,
  thickness is compared against themselves, so body size is already held fixed —
  the denominator adds nothing to remove. But BMI is not fixed over time. Any
  intervention that changes weight (weight-loss drugs above all, but also ICU
  fluid shifts, oedema, refeeding) moves BMI directly, so STAR moves even when
  the muscle does not. For a monitoring product the denominator manufactures
  false change. Report raw millimetres for within-person claims; keep a
  normalised form only for cross-sectional 'is this proportionate' questions.""")

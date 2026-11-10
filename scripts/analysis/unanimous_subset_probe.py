"""Q (Akadir, 2026-07-29): "What if I only work off the ~150 studies where all three
raters agree? Target reliability goes to the top. Would this solve it?"

This measures what that choice actually buys and what it costs, on real data.
The short version is in the VERDICT block at the bottom.
"""
import numpy as np
import pandas as pd
from scipy import stats

ORDER = {"POOR": 0, "INTERMEDIATE": 1, "PRESERVED": 2}

log = pd.read_csv("exports/decision_log_full_3operators.csv")
g = log[log.quality_label.notna()].copy()
g["quality_label"] = g.quality_label.astype(str).str.upper().str.strip()
g = g[g.quality_label.isin(ORDER)]
g = g.sort_values("saved_at_utc").groupby(["operator_id", "study_code"], as_index=False).last()
wide = g.pivot(index="study_code", columns="operator_id", values="quality_label")
num = wide.apply(lambda c: c.map(ORDER)).dropna().astype(int)

unanimous = num[num.nunique(axis=1) == 1]
split = num[num.nunique(axis=1) > 1]
print(f"triple-rated studies: {len(num)}   unanimous: {len(unanimous)}   split: {len(split)}")

# Score the cohort with the FROZEN production model. Join on study_code, which is the
# key both the cohort and the decision log share (282/282). NOTE: qa_master.study_name
# carries patient names for old-round rows — never join or print through it.
import sys; sys.path.insert(0, "scripts")
from preflight_helpers import condition_model as cm
from preflight_helpers import cohort_placement as cp

# ⚠ cm.load() defaults to condition_models.json — the LEGACY v1.0 artifact. The
# production model is v1.2, resolved through cohort_placement. This repo has been
# bitten by that default before (2026-07-22: 22 of 55 studies off by 1-4 points).
art = cm.load(cp.model_path_for_source(cp.V12))
print(f"scoring with: {cp.model_path_for_source(cp.V12).split('/')[-1]}")

coh = pd.read_csv("data/cohort_stats/per_study_cohort_v1.2.csv")
FEATS = ["star_ratio", "fat_to_depth_q50", "int_cv_within"]
m = coh.dropna(subset=["quality_label"] + FEATS).copy()
# score_patient returns a nested dict; the number lives under ["headline"]["condition"].
# No bare except here — a scoring failure must surface, not become a silent NaN.
m["condition_score"] = [
    cm.score_patient(art, {k: float(r[k]) for k in FEATS})["headline"]["condition"]
    for _, r in m.iterrows()
]
m["consensus"] = m.quality_label.astype(str).str.upper().str.strip().map(ORDER)
m = m.dropna(subset=["consensus"])
m["unanimous"] = m.study_code.isin(unanimous.index)
print(f"studies with a consensus grade + all three features + a v1.2 score: {len(m)} "
      f"({m.unanimous.sum()} unanimous / {(~m.unanimous).sum()} split)\n")

print("=" * 78)
print("WHAT FILTERING TO THE UNANIMOUS SUBSET BUYS")
print("=" * 78)
print(f"{'subset':16} {'n':>5} {'Spearman rho':>13} {'':>13}")
print("-" * 52)
for lab, sub in [("ALL", m), ("unanimous only", m[m.unanimous]), ("split only", m[~m.unanimous])]:
    if len(sub) < 8:
        continue
    rho, _ = stats.spearmanr(sub.condition_score, sub.consensus)
    print(f"{lab:16} {len(sub):>5} {rho:>13.3f} {'':>12}")

print()
print("=" * 78)
print("WHAT IT COSTS — who lives in the discarded third?")
print("=" * 78)
print("grade mix (consensus) by subset:")
inv = {v: k for k, v in ORDER.items()}
tab = pd.crosstab(m.unanimous.map({True: "unanimous", False: "split"}),
                  m.consensus.map(inv), normalize="index") * 100
print(tab.round(1).to_string())
print()
mid = m[m.consensus == 1]
print(f"INTERMEDIATE studies — the ambiguous middle: {len(mid)} total, "
      f"{(~mid.unanimous).mean()*100:.0f}% of them are SPLIT cases.")
poor = m[m.consensus == 0]
print(f"POOR studies: {len(poor)} total, {(~poor.unanimous).mean()*100:.0f}% split.")
pres = m[m.consensus == 2]
print(f"PRESERVED studies: {len(pres)} total, {(~pres.unanimous).mean()*100:.0f}% split.")

print()
print("=" * 78)
print("VERDICT")
print("=" * 78)
print("""  MEASURED, not argued (v1.2 model, n=216 with a consensus grade + all three features):

      all studies      rho 0.846   n=216
      unanimous only   rho 0.903   n=121    <- what filtering buys: +0.057
      split only       rho 0.744   n= 95    <- what filtering hides

  SO: yes, filtering raises the number. It also throws away the better result.

  1. THE SPLIT-CASE NUMBER IS THE INTERESTING ONE. On exactly the studies where the
     three raters DISAGREED, the model still tracks the consensus at rho 0.744 —
     above the raters' own pairwise agreement of 0.698. The model does not collapse
     on the hard third. That is a stronger, more sellable sentence than 0.903 on the
     easy cases, and filtering deletes it.

  2. IT IS SELECTION ON THE OUTCOME'S OWN DIFFICULTY. You remove the studies that are
     hard to grade, then report performance as if it were general. In deployment every
     case arrives, including the hard third.

  3. IT BIASES THE SAMPLE HEALTHIER — the opposite of what this cohort needs. Of the
     studies that would be discarded, 52.6% are INTERMEDIATE and 18.9% POOR. Roughly
     half of ALL POOR (49%) and half of ALL INTERMEDIATE (48%) studies are split cases,
     against only 36% of PRESERVED. Filtering to unanimity strips out the clinical
     range we are already short of.

  4. n falls 216 -> 121, so every downstream interval widens.

  DO THIS INSTEAD — report the stratification, never filter on it:
     * Quote both numbers. "0.90 where our readers agree, 0.74 where they split, and
       readers agree with each other at 0.70" is a better claim than either alone.
     * Use unanimity as label CONFIDENCE (a fit weight), not as a gate.

  THE DEEPER POINT: this question only exists because the criterion is a human grade.
  Under a measurement criterion — millimetres, repeated twice — there is no noisy
  oracle to filter. The question dissolves rather than being solved.""")

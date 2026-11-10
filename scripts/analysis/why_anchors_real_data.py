"""The anchor argument, demonstrated on REAL data only. No simulation.

The three raters graded the SAME 270 old studies. They drew the line between
PRESERVED and not-PRESERVED in different places. So we can ask, using nothing but
data already on disk:

    "If I compute the average texture value of the PRESERVED studies,
     how much does that average move just by switching whose labels I use?"

The images are identical. The machine is identical. The patients are identical.
Anything that moves is pure labelling. That is the size of the error you make when
you compare two groups labelled by different people, or by the same people at
different times.
"""
import numpy as np
import pandas as pd

ORDER = {"POOR": 0, "INTERMEDIATE": 1, "PRESERVED": 2}

# --- the three raters' individual grades -----------------------------------
log = pd.read_csv("exports/decision_log_full_3operators.csv")
log = log[log.quality_label.notna()].copy()
log["quality_label"] = log.quality_label.astype(str).str.upper().str.strip()
log = log[log.quality_label.isin(ORDER)]
log = log.sort_values("saved_at_utc").groupby(["operator_id", "study_code"], as_index=False).last()
grades = log.pivot(index="study_code", columns="operator_id", values="quality_label")

# --- the measured numbers for those same studies ---------------------------
coh = pd.read_csv("data/cohort_stats/per_study_cohort_v1.2.csv").set_index("study_code")

FEATURES = [("texture  (int_cv_within)", "int_cv_within"),
            ("size     (star_ratio)", "star_ratio"),
            ("fat      (fat_to_depth_q50)", "fat_to_depth_q50")]

both = grades.join(coh, how="inner")
raters = list(grades.columns)
full = both.dropna(subset=raters)
print(f"Studies graded by all three raters, with measurements on file: {len(full)}")
print("These are the SAME images, the SAME machine, the SAME patients throughout.\n")

print("=" * 79)
print("STEP 1 — how many studies does each rater call PRESERVED?")
print("=" * 79)
for r in raters:
    n = (full[r] == "PRESERVED").sum()
    print(f"  {r:12} calls {n:3} of {len(full)} studies PRESERVED  ({n/len(full)*100:.0f}%)")
counts = [(full[r] == "PRESERVED").sum() for r in raters]
print(f"\n  Highest minus lowest: {max(counts) - min(counts)} studies.")
print("  Nobody is wrong. They simply draw the line in different places.\n")

print("=" * 79)
print("STEP 2 — the average measurement AMONG the studies each rater called PRESERVED")
print("=" * 79)
for label, col in FEATURES:
    print(f"\n  {label}")
    vals = {}
    for r in raters:
        sel = full[(full[r] == "PRESERVED")][col].dropna().astype(float)
        vals[r] = sel.mean()
        print(f"     {r:12} n={len(sel):3}   average = {sel.mean():.3f}")
    spread = max(vals.values()) - min(vals.values())
    allsd = full[col].dropna().astype(float).std(ddof=0)
    print(f"     ---> switching rater moves this average by {spread:.3f}"
          f"  (= {spread/allsd:.2f} of the cohort's own spread)")

print()
print("=" * 79)
print("STEP 3 — put that next to the old-vs-new gap we are trying to explain")
print("=" * 79)
qa = pd.read_csv("data/qa_master.csv")
for label, col in FEATURES:
    o = qa[qa.cohort_round == "old"][col].dropna().astype(float)
    n = qa[qa.cohort_round == "new"][col].dropna().astype(float)
    gap = n.median() - o.median()
    vals = [full[(full[r] == "PRESERVED")][col].dropna().astype(float).mean() for r in raters]
    spread = max(vals) - min(vals)
    pct = abs(spread / gap) * 100 if gap else float("nan")
    print(f"\n  {label}")
    print(f"     old cohort median          {o.median():.3f}")
    print(f"     new cohort median          {n.median():.3f}")
    print(f"     the gap to explain         {gap:+.3f}")
    print(f"     movement from rater alone  {spread:.3f}   = {pct:.0f}% of that gap")

print()
print("=" * 79)
print("WHAT THIS MEANS")
print("=" * 79)
print("  Just SWAPPING WHICH RATER labelled the studies moves the 'average PRESERVED")
print("  study' by a large fraction of the very gap we want to measure — on data where")
print("  nothing physical changed at all.")
print()
print("  So if we grade the new cohort in a fresh session and compare its PRESERVED")
print("  studies against the old cohort's PRESERVED studies, we cannot tell how much of")
print("  the difference is the machine and how much is simply a differently-drawn line.")
print()
print("  Fix: put old studies INTO the new grading session. Then both sides carry labels")
print("  drawn by the same people on the same day, and the line is the same on both")
print("  sides. Whatever difference survives is the machine.")

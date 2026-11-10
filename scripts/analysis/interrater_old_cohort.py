"""Is there usable INTER-RATER data on the old 282? If yes, the human agreement
ceiling — the yardstick the model's CV-QWK 0.813 must be read against — is
computable TODAY, with no new reading.
"""
import itertools
import numpy as np
import pandas as pd

df = pd.read_csv("exports/decision_log_full_3operators.csv")
print(f"rows={len(df)}  columns={len(df.columns)}")
print(f"operators: {df.operator_id.value_counts().to_dict()}")
print(f"decision_status: {df.decision_status.value_counts().to_dict()}")
print(f"quality_label: {df.quality_label.value_counts(dropna=False).to_dict()}")
print()

# keep rows that actually carry a grade
g = df[df.quality_label.notna()].copy()
g["quality_label"] = g.quality_label.astype(str).str.upper().str.strip()
ORDER = {"POOR": 0, "INTERMEDIATE": 1, "PRESERVED": 2}
g = g[g.quality_label.isin(ORDER)]
print(f"rows carrying a valid grade: {len(g)}   studies: {g.study_code.nunique()}")
print(f"grades per operator:")
print(pd.crosstab(g.operator_id, g.quality_label))
print()

# one grade per (operator, study) — keep the LAST saved if an operator regraded
g = g.sort_values("saved_at_utc").groupby(["operator_id", "study_code"], as_index=False).last()
wide = g.pivot(index="study_code", columns="operator_id", values="quality_label")
n_raters_per_study = wide.notna().sum(axis=1)
print("studies by number of operators who graded them:")
print(n_raters_per_study.value_counts().sort_index().to_string())
print()

ops = list(wide.columns)
print("=" * 76)
print("PAIRWISE OVERLAP + AGREEMENT (quadratic-weighted kappa, the model's own metric)")
print("=" * 76)


def qwk(a, b, k=3):
    a = np.asarray(a, int); b = np.asarray(b, int)
    O = np.zeros((k, k))
    for x, y in zip(a, b):
        O[x, y] += 1
    W = np.array([[(i - j) ** 2 / (k - 1) ** 2 for j in range(k)] for i in range(k)])
    ha = np.bincount(a, minlength=k); hb = np.bincount(b, minlength=k)
    E = np.outer(ha, hb) / len(a) ** 2 * O.sum()
    den = (W * E).sum()
    return 1 - (W * O).sum() / den if den else np.nan


print(f"{'pair':22} {'n both':>7} {'exact %':>9} {'QWK':>8}")
print("-" * 50)
pair_rows = []
for x, y in itertools.combinations(ops, 2):
    sub = wide[[x, y]].dropna()
    if len(sub) < 5:
        print(f"{str(x)[:10]+' vs '+str(y)[:10]:22} {len(sub):>7}   -- too few --")
        continue
    a = sub[x].map(ORDER).to_numpy(); b = sub[y].map(ORDER).to_numpy()
    ex = (a == b).mean() * 100
    k = qwk(a, b)
    pair_rows.append((f"{x} vs {y}", len(sub), ex, k))
    print(f"{str(x)[:10]+' vs '+str(y)[:10]:22} {len(sub):>7} {ex:>8.1f}% {k:>8.3f}")

if pair_rows:
    print()
    print(f"  mean pairwise QWK = {np.mean([r[3] for r in pair_rows]):.3f}"
          f"   <-- the HUMAN CEILING to read model CV-QWK 0.813 against")

# consensus where >=2 graded
multi = wide[n_raters_per_study >= 2]
print()
print("=" * 76)
print(f"CONSENSUS FEASIBILITY — {len(multi)} studies have >=2 grades")
print("=" * 76)
if len(multi):
    ordm = multi.apply(lambda r: sorted(ORDER[v] for v in r.dropna()), axis=1)
    unan = ordm.apply(lambda v: len(set(v)) == 1).sum()
    alldiff = ordm.apply(lambda v: len(v) >= 3 and len(set(v)) == len(v)).sum()
    print(f"  unanimous:            {unan} ({unan/len(multi)*100:.0f}%)")
    print(f"  all-three-differ:     {alldiff} ({alldiff/len(multi)*100:.0f}%)")
    print(f"  split (some disagree):{len(multi)-unan-alldiff}")
    print()
    print("  median-of-raters consensus label distribution:")
    med = ordm.apply(lambda v: int(np.median(v)))
    inv = {v: k for k, v in ORDER.items()}
    print("   ", med.map(inv).value_counts().to_dict())

# does the cohort the MODEL was fit on use these?
try:
    coh = pd.read_csv("data/cohort_stats/per_study_cohort_v1.2.csv")
    key = "study_code" if "study_code" in coh.columns else coh.columns[0]
    ov = set(coh[key].astype(str)) & set(wide.index.astype(str))
    print()
    print(f"overlap with the fitted cohort v1.2 ({key}): {len(ov)} / {len(coh)} studies")
except Exception as e:
    print(f"\n(cohort join skipped: {e})")

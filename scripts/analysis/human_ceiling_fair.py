"""The FAIR benchmark. Model CV-QWK 0.813 is model-vs-CONSENSUS. Comparing that to
rater-vs-rater (0.697) is rigged in the model's favour: a consensus is an average,
so it is denoised, and ANY judge scores higher against it than against one noisy rater.

The like-for-like question is: how well does a HUMAN rater agree with the consensus
of the OTHERS? That is the bar a second read must clear.
"""
import itertools
import numpy as np
import pandas as pd

ORDER = {"POOR": 0, "INTERMEDIATE": 1, "PRESERVED": 2}
INV = {v: k for k, v in ORDER.items()}

df = pd.read_csv("exports/decision_log_full_3operators.csv")
g = df[df.quality_label.notna()].copy()
g["quality_label"] = g.quality_label.astype(str).str.upper().str.strip()
g = g[g.quality_label.isin(ORDER)]
g = g.sort_values("saved_at_utc").groupby(["operator_id", "study_code"], as_index=False).last()
wide = g.pivot(index="study_code", columns="operator_id", values="quality_label")
ops = list(wide.columns)


def qwk(a, b, k=3):
    a = np.asarray(a, int); b = np.asarray(b, int)
    O = np.zeros((k, k))
    for x, y in zip(a, b):
        O[x, y] += 1
    W = np.array([[(i - j) ** 2 / (k - 1) ** 2 for j in range(k)] for i in range(k)])
    E = np.outer(np.bincount(a, minlength=k), np.bincount(b, minlength=k)) / len(a) ** 2 * O.sum()
    den = (W * E).sum()
    return 1 - (W * O).sum() / den if den else np.nan


num = wide.applymap(lambda v: ORDER.get(v, np.nan) if isinstance(v, str) else np.nan)
full = num.dropna()          # studies all three graded
print(f"studies with all three grades: {len(full)}")
print()

print("=" * 78)
print("RATER THRESHOLD DIFFERENCES — the θ shift, measured on identical studies")
print("=" * 78)
print(f"{'rater':14} {'POOR':>6} {'INTERM':>8} {'PRESERVED':>11}   (same {len(full)} studies)")
print("-" * 46)
for o in ops:
    c = full[o].map(INV).value_counts()
    print(f"{o:14} {c.get('POOR',0):>6} {c.get('INTERMEDIATE',0):>8} {c.get('PRESERVED',0):>11}")
spread = max(full[o].map(INV).value_counts().get("PRESERVED", 0) for o in ops) - \
         min(full[o].map(INV).value_counts().get("PRESERVED", 0) for o in ops)
print(f"\n  spread in PRESERVED count across raters, same studies: {spread} studies "
      f"({spread/len(full)*100:.0f}% of the cohort)")
print("  -> the raters' PRESERVED cutpoints genuinely differ. This is mechanism 1")
print("     of the anchor argument, measured rather than hypothesised.")

print()
print("=" * 78)
print("LEAVE-ONE-OUT: each rater vs the CONSENSUS OF THE OTHER TWO")
print("=" * 78)
print(f"{'rater':14} {'n':>5} {'exact %':>9} {'QWK vs others':>15}")
print("-" * 46)
loo = []
for o in ops:
    others = [x for x in ops if x != o]
    cons = full[others].median(axis=1)
    # median of two can be x.5 — that is a genuine tie; drop those rows for fairness
    keep = cons == cons.round()
    a = full.loc[keep, o].astype(int).to_numpy()
    b = cons[keep].astype(int).to_numpy()
    k = qwk(a, b)
    loo.append(k)
    print(f"{o:14} {len(a):>5} {(a==b).mean()*100:>8.1f}% {k:>15.3f}")
print(f"\n  mean rater-vs-others QWK = {np.mean(loo):.3f}   <-- THE FAIR HUMAN BAR")

print()
print("=" * 78)
print("KRIPPENDORFF'S ALPHA (ordinal) — chance-corrected agreement across all 3")
print("=" * 78)
vals = full.to_numpy(int)
n_items, n_r = vals.shape
# ordinal alpha via the general disagreement form
allv = vals.ravel()
counts = np.bincount(allv, minlength=3)


def ord_delta(c, k_, l_):
    lo, hi = min(k_, l_), max(k_, l_)
    s = counts[lo:hi + 1].sum() - (counts[lo] + counts[hi]) / 2
    return s ** 2


Do = np.mean([np.mean([ord_delta(counts, vals[i, a], vals[i, b])
                       for a, b in itertools.permutations(range(n_r), 2)])
              for i in range(n_items)])
pairs = [(x, y) for x in allv for y in allv]
De = np.mean([ord_delta(counts, x, y) for x, y in
              itertools.product(range(3), repeat=2)
              for _ in range(1)] * 1)
# proper De: expectation over the marginal distribution
p = counts / counts.sum()
De = sum(p[i] * p[j] * ord_delta(counts, i, j) for i in range(3) for j in range(3))
print(f"  alpha = {1 - Do/De:.3f}   (>=0.80 conventionally 'reliable'; 0.67 tentative floor)")

print()
print("=" * 78)
print("THE COMPARISON THAT MATTERS")
print("=" * 78)
print(f"  model vs consensus  (CV-QWK, frozen v1.2) : 0.813")
print(f"  human vs consensus  (mean leave-one-out)  : {np.mean(loo):.3f}")
print(f"  human vs human      (mean pairwise)       : 0.697")

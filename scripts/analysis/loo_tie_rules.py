"""How much does the 'human bar' move with the tie rule? Three runs gave 0.707,
0.732 and 0.794. If a number swings that far on an arbitrary convention, it is not
a benchmark yet. Compute it under every defensible rule and report the range.

Leave-one-rater-out: each rater is scored against the consensus of the OTHER TWO.
With two raters a 'consensus' is only defined when they agree. When they disagree
we must choose, and that choice is the whole disagreement.
"""
import itertools
import numpy as np
import pandas as pd

ORDER = {"POOR": 0, "INTERMEDIATE": 1, "PRESERVED": 2}

log = pd.read_csv("exports/decision_log_full_3operators.csv")
g = log[log.quality_label.notna()].copy()
g["quality_label"] = g.quality_label.astype(str).str.upper().str.strip()
g = g[g.quality_label.isin(ORDER)]
g = g.sort_values("saved_at_utc").groupby(["operator_id", "study_code"], as_index=False).last()
wide = g.pivot(index="study_code", columns="operator_id", values="quality_label")
num = wide.apply(lambda c: c.map(ORDER))
full = num.dropna().astype(int)
raters = list(full.columns)
print(f"studies graded by all three: {len(full)}\n")


def qwk(a, b, k=3, w=None):
    a = np.asarray(a, int); b = np.asarray(b, int)
    w = np.ones(len(a)) if w is None else np.asarray(w, float)
    O = np.zeros((k, k))
    for x, y, ww in zip(a, b, w):
        O[x, y] += ww
    W = np.array([[(i - j) ** 2 / (k - 1) ** 2 for j in range(k)] for i in range(k)])
    ha = np.array([w[a == i].sum() for i in range(k)])
    hb = np.array([w[b == i].sum() for i in range(k)])
    E = np.outer(ha, hb) / w.sum() ** 2 * O.sum()
    den = (W * E).sum()
    return 1 - (W * O).sum() / den if den else np.nan


RULES = {
    "drop ties (evaluates only where the other two AGREE)": "drop",
    "tie -> INTERMEDIATE (toward the middle)": "mid",
    "tie -> the HIGHER grade (toward PRESERVED)": "up",
    "tie -> the LOWER grade (toward POOR)": "down",
    "tie -> keep BOTH targets, half weight each": "both",
}

print("=" * 84)
print("LEAVE-ONE-RATER-OUT: rater vs consensus of the other two, by tie rule")
print("=" * 84)
print(f"{'tie rule':52} {'n used':>7} {'mean QWK':>10}")
print("-" * 72)
results = {}
for label, rule in RULES.items():
    per, n_used = [], 0
    for r in raters:
        others = [x for x in raters if x != r]
        o1, o2 = full[others[0]].to_numpy(), full[others[1]].to_numpy()
        me = full[r].to_numpy()
        agree = o1 == o2
        if rule == "drop":
            a, b, w = me[agree], o1[agree], None
        elif rule == "both":
            a = np.concatenate([me, me[~agree]])
            b = np.concatenate([np.where(agree, o1, o1), o2[~agree]])
            w = np.concatenate([np.where(agree, 1.0, 0.5), np.full((~agree).sum(), 0.5)])
        else:
            pick = {"mid": lambda: np.full((~agree).sum(), 1),
                    "up": lambda: np.maximum(o1, o2)[~agree],
                    "down": lambda: np.minimum(o1, o2)[~agree]}[rule]()
            b = o1.copy(); b[~agree] = pick
            a, w = me, None
        per.append(qwk(a, b, w=w)); n_used = len(a)
    results[label] = float(np.mean(per))
    print(f"{label:52} {n_used:>7} {np.mean(per):>10.3f}")

print()
pair = [qwk(full[x].to_numpy(), full[y].to_numpy()) for x, y in itertools.combinations(raters, 2)]
print(f"{'PAIRWISE rater-vs-rater (no consensus, no tie rule)':52} {len(full):>7} {np.mean(pair):>10.3f}")
print(f"    individual pairs: {' / '.join(f'{p:.3f}' for p in pair)}")

lo, hi = min(results.values()), max(results.values())
print()
print("=" * 84)
print("VERDICT")
print("=" * 84)
print(f"  The 'human bar' ranges {lo:.3f} to {hi:.3f} across defensible tie rules.")
print(f"  That is a spread of {hi-lo:.3f} QWK from a convention nobody has fixed.")
print(f"  The model's CV-QWK is 0.813.")
print()
print(f"  Against the LOOSEST rule ({max(results, key=results.get)[:34]}...) the model is at parity.")
print(f"  Against the STRICTEST it is clearly above.")
print("  Either way the comparison is NOT apples-to-apples until the model is re-scored")
print("  on the SAME subset against the SAME target, with a paired bootstrap CI.")
print("  -> that is exactly what task A12 requires. Do not quote a human bar before it.")

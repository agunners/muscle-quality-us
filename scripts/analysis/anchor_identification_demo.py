"""What the 'subtraction' actually is — a complete worked example with known truth.

We SIMULATE a world where we know the right answer, run both estimators, and see
which one recovers it. Nothing here touches real data; the point is to show the
identification argument works and what it costs.

GROUND TRUTH we plant:
  * protocol offset  Δ_protocol = +0.50 feature units  (new protocol reads higher
    at IDENTICAL muscle) -- this is what we want to recover
  * raters drift STRICTER between the old session and the new one
  * the new cohort is genuinely HEALTHIER than the old one
All three push the same way, which is exactly why they are easy to confuse.
"""
import numpy as np
import pandas as pd

rng = np.random.default_rng(20260727)

# ---------------------------------------------------------------- ground truth
D_PROTOCOL = 0.50           # <-- the estimand
BETA = 1.00                 # feature units per unit of latent quality
NOISE = 0.30

CUT_OLD = (-0.5, 0.5)       # (POOR|INTERMEDIATE, INTERMEDIATE|PRESERVED) at T_old
CUT_NEW = (-0.1, 1.0)       # raters drifted STRICTER by T_new
POP_OLD_MEAN, POP_NEW_MEAN = 0.0, 0.80    # new cohort is healthier

N_OLD, N_NEW, N_ANCHOR = 282, 66, 60


def grade(q, cuts):
    lo, hi = cuts
    return np.where(q < lo, 0, np.where(q < hi, 1, 2))


# latent quality
q_old = rng.normal(POP_OLD_MEAN, 1.0, N_OLD)
q_new = rng.normal(POP_NEW_MEAN, 1.0, N_NEW)

# features: protocol adds a constant offset at fixed q
f_old = 0.0        + BETA * q_old + rng.normal(0, NOISE, N_OLD)
f_new = D_PROTOCOL + BETA * q_new + rng.normal(0, NOISE, N_NEW)

# grades actually on file
g_old_atT_old = grade(q_old, CUT_OLD)     # the 282's historical grades
g_new_atT_new = grade(q_new, CUT_NEW)     # new cohort, graded now

# the anchor set: a stratified sample of OLD studies, RE-graded in the new session
anchor_idx = rng.choice(N_OLD, N_ANCHOR, replace=False)
g_anchor_atT_new = grade(q_old[anchor_idx], CUT_NEW)   # same images, today's raters

NAMES = {0: "POOR", 1: "INTERMEDIATE", 2: "PRESERVED"}
print("=" * 78)
print(f"GROUND TRUTH: Δ_protocol = {D_PROTOCOL:+.2f}   (what a correct estimator must return)")
print("=" * 78)

# ------------------------------------------------- estimator 1: the naive one
print("\nESTIMATOR 1 — naive: new cohort (new grades) vs old cohort (ITS OWN OLD grades)")
print("-" * 78)
print(f"{'grade':14} {'new mean F':>11} {'old mean F':>11} {'difference':>12}")
naive_terms = []
for lvl in (2, 1, 0):
    a = f_new[g_new_atT_new == lvl]
    b = f_old[g_old_atT_old == lvl]
    if len(a) < 3 or len(b) < 3:
        continue
    d = a.mean() - b.mean()
    naive_terms.append(d)
    print(f"{NAMES[lvl]:14} {a.mean():>11.3f} {b.mean():>11.3f} {d:>+12.3f}")
naive = float(np.mean(naive_terms))
print(f"\n  naive estimate of Δ_protocol = {naive:+.3f}"
      f"   (truth {D_PROTOCOL:+.2f}) -> ERROR {naive - D_PROTOCOL:+.3f}")

# ------------------------------------------- estimator 2: anchored comparison
print("\nESTIMATOR 2 — anchored: new cohort vs ANCHORS, both graded in the SAME session")
print("-" * 78)
print(f"{'grade':14} {'new mean F':>11} {'anchor mn F':>12} {'difference':>12}")
f_anchor = f_old[anchor_idx]
anch_terms, weights = [], []
for lvl in (2, 1, 0):
    a = f_new[g_new_atT_new == lvl]
    b = f_anchor[g_anchor_atT_new == lvl]
    if len(a) < 3 or len(b) < 3:
        continue
    d = a.mean() - b.mean()
    anch_terms.append(d); weights.append(len(a) + len(b))
    print(f"{NAMES[lvl]:14} {a.mean():>11.3f} {b.mean():>12.3f} {d:>+12.3f}")
anchored = float(np.average(anch_terms, weights=weights))
print(f"\n  anchored estimate of Δ_protocol = {anchored:+.3f}"
      f"   (truth {D_PROTOCOL:+.2f}) -> ERROR {anchored - D_PROTOCOL:+.3f}")

# --------------------------------------- estimator 3: the regression version
print("\nESTIMATOR 3 — the same thing as one regression (what you would actually run)")
print("-" * 78)
pool = pd.DataFrame({
    "F": np.concatenate([f_new, f_anchor]),
    "is_new_protocol": np.concatenate([np.ones(N_NEW), np.zeros(N_ANCHOR)]),
    "grade": np.concatenate([g_new_atT_new, g_anchor_atT_new]),
})
X = np.column_stack([np.ones(len(pool)), pool.is_new_protocol,
                     (pool.grade == 1).astype(float), (pool.grade == 2).astype(float)])
beta, *_ = np.linalg.lstsq(X, pool.F.to_numpy(), rcond=None)
resid = pool.F.to_numpy() - X @ beta
se = np.sqrt((resid @ resid) / (len(pool) - X.shape[1]) * np.linalg.pinv(X.T @ X)[1, 1])
print("  model:  F ~ is_new_protocol + grade    (grades from ONE session, so θ cancels)")
print(f"  coefficient on is_new_protocol = {beta[1]:+.3f}  ±{1.96*se:.3f} (95% CI)"
      f"   truth {D_PROTOCOL:+.2f}")

# ------------------------------------------------- what the anchors also show
print("\n" + "=" * 78)
print("BONUS — the drift the anchors expose directly (same images, then vs now)")
print("=" * 78)
cm = pd.crosstab(pd.Series([NAMES[v] for v in g_old_atT_old[anchor_idx]], name="graded THEN"),
                 pd.Series([NAMES[v] for v in g_anchor_atT_new], name="graded NOW"))
print(cm.to_string())
moved = (g_old_atT_old[anchor_idx] != g_anchor_atT_new).mean()
print(f"\n  {moved*100:.0f}% of anchor studies changed grade with NO change to the image.")
print("  That is pure rater drift, and it is the entire reason estimator 1 fails.")

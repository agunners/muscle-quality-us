# scripts/analysis — the receipts behind the canonical claims

Every number quoted in `docs/NORTHSTAR.md`, `docs/PRODUCT_LADDER.md` and
`docs/force_validation_preregistration.md` is reproduced by a script here.

**These lived in `scratchpad/` until 2026-07-27, which is gitignored** — the reproduction code for the
headline results was one cleanup away from being lost. Moving them here discharges part of NORTHSTAR
task A7 and retires the "text tier" of kill-shot K5 (single-copy evidence).

Run from the repository root, with the environment bridge sourced:

```bash
source scripts/env.sh && .venv/bin/python scripts/analysis/<script>.py
```

All of them read `data/qa_master.csv` and/or `exports/decision_log_full_3operators.csv`, which are
**gitignored because they carry PHI**. The scripts are committed; their inputs are not.

| script | what it establishes | cited in |
|---|---|---|
| `verify_texture_sign.py` | The sign test — all three axes land on their pre-stated side on two criteria measured on disjoint people (size +11.66/SD & ρ +0.706 · fat −16.96 & ρ −0.758 · texture +7.99 & ρ +0.711). | prereg "The sign test"; NORTHSTAR §2 |
| `old_vs_new_shift.py` | Gate **G8** — the score saturates on protocol v8 (40% score exactly 100 vs 8%; score SD ratio 0.58 while inputs hold 0.96–1.04). Also the min-detectable-ΔR² power figure. | NORTHSTAR G8, §8 |
| `interrater_old_cohort.py` | Three raters graded 270 of the 282 old studies; pairwise weighted κ 0.635 / 0.726 / 0.733. | NORTHSTAR §1 leg 3, A12 |
| `human_ceiling_fair.py` | Leave-one-rater-out against the consensus of the other two, plus the rater threshold spread (114 / 97 / 76 PRESERVED on identical studies). | NORTHSTAR A12, A24 |
| `loo_tie_rules.py` | **The human bar is not yet a number** — it spans 0.659–0.794 across five defensible tie rules, so 0.813 must not be quoted against it. | NORTHSTAR A12 |
| `why_anchors_real_data.py` | The anchor argument on real data only — swapping which rater labelled the studies moves the mean texture of "the PRESERVED studies" by 16% of the old-vs-new gap. | NORTHSTAR A24 |
| `anchor_identification_demo.py` | The identification argument, on planted ground truth: the naive estimator errs +109%, the anchored one +32%. The only script here using simulated data, and it is labelled as such. | NORTHSTAR A24 |

**Discipline.** If a number enters a canonical document, its receipt belongs in this directory. A claim
whose reproduction lives only in `scratchpad/` is a claim with no receipt.

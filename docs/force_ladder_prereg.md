# Force ladder — pre-registration

*Locked 2026-08-11, **before any result was computed**. The lock is this file's first commit; any
later deviation gets a dated amendment paragraph here, never a silent edit.*

## Purpose, and where it leads

Establish the **baseline**: how well plain tabular measurement explains isometric knee-extension
torque, block by block, on one frozen substrate — so that (1) stakeholders see where the methodology
stands before anything fancier is added, (2) the frozen-encoder **embeddings** experiment lands
against a known number, (3) the **force card**'s size-expected strength band inherits fitted, honest
intervals, and (4) the PRODUCT_CANDIDATES force section cites measured deltas instead of intentions.

## Substrate

`data/qa_master.csv`, new-round rows only (`cohort_round == "new"`), as produced by the r4 freeze of
2026-08-11 (cohort commit `8e6b37e`). Criterion: `izo_torque_nm` (isometric peak torque, Nm).
No CV gate on the criterion (decided with data 2026-08-11: criterion reliability 0.974; a CV≤10 cut
buys +0.004 for −7 subjects).

**Row sets.**
- **PRIMARY** — common-complete across *all* blocks and the criterion, so every rung fits **identical
  rows and identical folds**: n=90 at lock time (53 M / 37 F).
- **SENSITIVITY-A** — rungs 1–3 on the texture-free complete set (n=106 at lock time), to show the
  texture rung's row restriction is not driving conclusions.
- **SENSITIVITY-B** — the primary analysis repeated on the `izo_cv_pct ≤ 10` subset.

## Blocks — exact columns, nothing else

| block | columns |
|---|---|
| B1 demographics | `age`, `sex` (M=1, F=0), `height_cm`, `weight_kg` |
| B2 geometry | `rf_thickness_mm`, `full_thickness_q50_mm`, `visible_rf_area_mm2`, `qmt_mm` |
| B3 fat | `fat_thickness_q50_mm`, `fat_thickness_max_mm`, `fat_to_depth_q50` |
| B4 texture | `int_cv_within` |

Deliberately excluded: `bmi` (derived from B1), `muscle_fat_ratio` and `fat_contribution` (cross-block
composites), `star_ratio` (retired normaliser), `opsel_*` (inert), `isok_*` (a second criterion —
its own registration if ever). Rungs: R1=B1 · R2=B1+B2 · R3=B1+B2+B3 · R4=all.

## Models — two classes, tuned inner, nothing else

- **Ridge**: standardize on the training fold, α tuned over `logspace(−3, 3, 25)`.
- **Shallow GBM**: `HistGradientBoostingRegressor`, grid `max_depth {2,3} × learning_rate {0.03,0.1}
  × max_iter {100,300}` (8 configs), `min_samples_leaf=5`, early stopping off.

## Cross-validation

Outer 5-fold (shuffled), **seeds 0–24**; inner 5-fold on each outer-train for tuning. Metrics pooled
out-of-fold per seed: **R², RMSE, MAE**. Per-rung deltas computed per seed; reported as mean ± SD over
the 25 seeds with the [2.5, 97.5] percentile band.

**Block attribution**: exact block-Shapley on ridge — all 2⁴ block subsets fit per seed on the same
folds; each block's value = its average marginal R² over orderings. (GBM Shapley not registered —
16× the tuning cost for no additional decision.)

## Tripwires and validation gates

1. **Permuted-target smoke BEFORE the real run**: the harness runs with the criterion shuffled;
   pooled out-of-fold R² must land ≈ 0 (|median| ≤ 0.05) on every rung. A positive R² on noise =
   leakage; the real run does not start until this passes.

   > **Amendment 2026-08-11 (pre-run, after the smoke itself and before any real result).** The
   > criterion above was written two-sided (|median| ≤ 0.05) but the stated intent is one-sided —
   > only a *positive* R² on noise indicates leakage. The first smoke run returned ridge ≈ −0.03
   > (pass either way) and GBM ≈ **−0.22**: a flexible model overfitting pure noise predicts *worse*
   > than the mean out-of-fold, which is evidence of **no** leakage, and the two-sided bound wrongly
   > called it a failure. The gate is amended to **median R² ≤ +0.05 per rung and model**; strongly
   > negative values are expected for the GBM and explicitly not a failure. Nothing else changes.

   > **Amendment 2 — 2026-08-11 (pre-run; still no real result computed).** The pre-run red-team
   > review traced two criterion rows inside PRIMARY to near-certain unit/entry errors at the source
   > sheet: `NR0D53363E` (isometric 9.1 Nm, CV 0.0, isokinetic ext **81.3** Nm) and `NR245F7FC5`
   > (isometric 8.7 Nm, CV 0.0, isokinetic ext **84.5** Nm). A subject cannot produce ~1/9 of her own
   > isokinetic torque isometrically; ×9.81 (a kgf·m value read as Nm) lands both at ≈ their
   > isokinetic values; CV exactly 0.0 marks single-value entries. **Both rows are quarantined from
   > every row set pending Akadir's source audit** — the operators' sheet is not ours to correct, and
   > rescaling by assumption would be inventing data. Registered sensitivity **SENS-C** = PRIMARY
   > plus the two rows exactly as recorded. Row counts at amendment time: PRIMARY 88 · no-texture
   > 104 · cv≤10 82 · SENS-C 90. Also registered per review: the smoke's scope is seeds 0–4 on
   > PRIMARY, all four rungs, both models; the smoke writes `results/force_ladder_smoke.json` and
   > the real run refuses to start unless that receipt exists and reads PASS.

   > **Amendment 3 — 2026-08-12 (pre-re-run; no re-run result computed).** Akadir's no-force rule,
   > set today, changes the row universe: *a row carrying any open measurement flag is no-force
   > equivalent* — the substrate blanks all five criterion cells pending an explicit human verdict
   > (the flag→kill ledger; `DATA_LIFECYCLE.md` §7 item 8). Under it, sheet row 24 (soft flag,
   > iso/isok ratio 0.64) joins rows 87 and 92 in serving no criterion, and Akadir's decision is to
   > **keep the three out now and re-pin the baseline** rather than wait for the source audit
   > (which stays owed and can still return values via a CLEAR verdict). Consequences, registered
   > before running: (1) the baseline is re-derived on the reduced PRIMARY (expected 87 = 88 minus
   > row 24) with the SAME harness, seeds 0–24, rungs, and models — nothing else changes; (2) the
   > leakage smoke re-runs first (the row set changed; same gate, seeds 0–4); (3) **SENS-C is
   > RETIRED** — the substrate can no longer serve held values, so "suspects exactly as recorded"
   > is not derivable by design; its 2026-08-11 receipt (n=90) stands as the historical record;
   > (4) the n=88 receipts are snapshotted as `results/force_ladder_*_2026-08-11_n88.*` and the
   > canonical filenames carry the re-pinned baseline the embeddings rung will be compared
   > against; (5) if the audit later clears row 24, re-inclusion is one re-derivation with this
   > same harness — the 08-11 n=88 numbers already bound what it would look like.
   >
   > **Amendment 3 resolution — same day, pre-run.** The "expected 87" above was wrong: row 24
   > (NRB991100C) carries a texture withhold (`int_cv_within` blank, `int_cv_trust=withheld`), so
   > it was **never in PRIMARY** — PRIMARY under the no-force rule is **set-identical to the
   > 2026-08-11 baseline at n=88** (verified: none of the three flagged uids present; isometric
   > coverage 106 pre-hold on both dates and sheet cells are fill-only, so no compensating
   > membership change is possible). What the rule actually moves: SENS_A_no_texture 104→103
   > (row 24 leaves the only set it was in) and SENS_C ≡ PRIMARY (retired as registered). The
   > re-run therefore serves as a REPRODUCTION check of the baseline on the rebuilt substrate —
   > PRIMARY rung numbers must match the n=88 receipts exactly (deterministic seeds/models);
   > a mismatch would mean the substrate rebuild changed more than the three held rows.

   > **Amendment 4 — 2026-08-13 (pre-run; no result under the new rule computed).** Akadir's
   > reliability rule, set today in his answers doc: *"I don't think these should be part of
   > analysis, part of cohort placement or force calculations. Let's do this → above 15 CV."*
   > An isometric CV above 15% now fires a HARD measurement flag (`cv_unreliable`,
   > `torque_plausibility.CV_UNRELIABLE_PCT` = 15.0; the 15.0 boundary itself stays in;
   > isokinetic CV deliberately exempt — it is never the served criterion), so under the
   > standing no-force rule the substrate serves no criterion cells for such rows. Exactly two
   > rows qualify today, both feature-complete: `NRCC2F325C` (CV 15.1) and `NRD512FB46`
   > (CV 19.2) — both sat in PRIMARY and SENS_A, so the row universe changes: expected
   > **PRIMARY 88 → 86** and **SENS_A_no_texture 103 → 101** (SENS_B cv≤10 unchanged — neither
   > row was in it; SENS_C stays retired). Registered consequences, before running: (1) the
   > leakage smoke re-runs first (the row set changed; same gate, seeds 0–4); (2) the baseline
   > re-derives on the reduced sets with the SAME harness, seeds 0–24, rungs, and models —
   > nothing else changes; the canonical receipts move to n=86 and **the embeddings rung
   > compares against the new PRIMARY number**; the 08-11 n=88 receipts stay snapshotted
   > (`results/force_ladder_*_2026-08-11_n88.*`) and git history carries the 08-12 re-pin;
   > (3) the served Strength-card model refreezes on the reduced SENS_A through
   > `force_model.py`'s own abort chain (substrate sha + n + receipt re-pinned first); the
   > 2026-08-12 artifact (n=103, OOF R² .6999) stands as the historical record; (4) landing in
   > the same substrate rebuild, registered for completeness: Akadir's source-audit verdicts on
   > sheet rows 87 + 92 (**KILLED** — the isometric pair stays blank forever; the isokinetic
   > cells release per the kill semantics, changing no set since the criterion is isometric),
   > row 24 unchanged (flagged-held, audit still open), and the AKA158 duplicate twin's study
   > kill (`NRA565AD79` — no set change; the twin never carried force).

   > **Amendment 5 — 2026-08-23 (pre-run; no result on the r5 substrate computed).** Freeze r5
   > (2026-08-23) grew the cohort 125 → 175 (+51 arrivals, −1 the killed twin) and the substrate
   > was rebuilt the same day: isometric coverage 143/175. One NEW hard flag fired at entry under
   > the standing Amendment-4 rule: sheet row 144 (`NRC51F688E`, isometric CV 23.6%) — its
   > criterion cells are held by the gate, so it never enters any row set. QUARANTINE unchanged
   > (`NR0D53363E`, `NR245F7FC5`). Harness unchanged in every respect (blocks, rungs, seeds 0–24,
   > models, alpha grid). Registered consequences, before running: (1) the leakage smoke re-runs
   > first (the row universe changed; same gate, seeds 0–4); (2) the ladder re-derives on the
   > grown sets with the SAME harness; the canonical receipts move to the new n and **the
   > embeddings rung compares against the new PRIMARY R2 ridge number** — the n=86 receipts
   > (.6795 ± .0155) stay snapshotted as `results/force_ladder_*_2026-08-13_n86.*`; (3) the
   > served Strength-card model refreezes on the new SENS_A through `force_model.py`'s own abort
   > chain; the 08-13 card (SENS_A n=101, OOF R² .6906 / RMSE 40.09) stands as the historical
   > record; (4) row-set sizes are deliberately NOT pinned here — the harness derives them — but
   > coverage bounds PRIMARY ≤ 143, and one compositional fact is registered pre-run because it
   > motivates Amendment 6: the force-served sex balance moved from M 57 / F 44 to **M 69 /
   > F 74** — the female arm is no longer the smaller one.

   > **Amendment 6 — 2026-08-23 (pre-run; named secondary analyses, registered before any is
   > computed).** Two additions motivated by the female-band thread (08-12 → 08-23), neither
   > gating, neither primary: **(a) per-sex out-of-fold readout** — for PRIMARY and
   > SENS_A_no_texture, rungs R1/R2, ridge: pool the existing fits' OOF residuals over seeds
   > 0–24 and report per-sex OOF R² and per-sex q10/q90 band widths. A readout of fits the
   > prereg already registers — no new model, no selection. **(b) log-target sensitivity arm** —
   > the identical harness run once with criterion `log(izo_torque_nm)`, ridge only, same seeds
   > and folds; report log-space OOF R² per rung and per-sex residual q10/q90 (log units ≈
   > relative error). Decision rule, registered now: if the female R1→R2 band narrows under the
   > log model where the Nm model showed no narrowing, the female flatness is at least partly a
   > loss-weighting artifact of fitting absolute Nm on a pooled cohort; if it stays flat, the
   > mechanism question stays open (coupling or n). Reported as sensitivity, never as the
   > headline; the primary claims remain the Nm-space rungs.
2. **Criterion floor**: pooled out-of-fold RMSE **< 12.0 Nm** on any rung ⇒ stop and audit for
   leakage — the dynamometer's own within-subject SD is ≈ 12.5 Nm (n=96), and an honest model does
   not beat its target's noise.
3. **Anchors** (expected magnitudes from prior receipts, sanity not gates): univariate
   ρ(full thickness, torque) ≈ +0.73 (NORTHSTAR §2, n=96); texture's in-sample adj-R² gain over full
   covariates ≈ +3.0 pp (`texture_force_test.py`, n=96) — the out-of-fold delta is expected smaller.

## Outputs

`scripts/analysis/results/force_ladder_results.csv` (per seed × rung × model × row-set),
`force_ladder_summary.json` (the reported numbers + the Shapley table), both committed as receipts.
Reporting: a technical section plus a stakeholder page in plain language, intervals not points.
Harness: `scripts/analysis/force_ladder.py` (CPU-only, deterministic, `--permute` runs gate 1).

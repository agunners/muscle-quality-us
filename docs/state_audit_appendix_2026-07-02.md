# State audit — code-facing appendix (2026-07-02)

Companion to the full **State of muscle_mania** assessment (the boss→expert→appendix strategic
read; durable copy at `scratchpad/state_audit_2026-07-02/State_of_muscle_mania_2026-07-02.md`,
and headed for the critical-docs Drive folder). This file carries only the **code-facing** subset
— the pipeline joints, correctness traps, stubs, and reproducibility blockers a developer needs —
so the repo keeps a durable technical record. Every item was adversarially re-verified (7-lane
workflow `wf_8a5d5f8f-2fd`, 53 agents, 0 findings refuted outright).

## The one urgent trap — a live PHI-leak in `.gitignore`

[`.gitignore:20`](.gitignore#L20) puts the comment **on the same line** as the rule; git does not
strip trailing comments, so `data/studies_root_flat/` (18 patient-named symlinks) and `scratchpad/`
(force-sheet snapshots + dynamometer PDFs) are **NOT ignored** — a `git add -A` stages 85 paths,
most PHI-bearing. Latent, not realized (`git ls-files` is clean). The exact two-line fix + the
safe push recipe (which forbids `git add -A`, because the tracked `data/calibration_labels.jsonl`
would also be swept) is in `scratchpad/state_audit_2026-07-02/staged_fixes/PLAN.md` — **staged,
not applied.**

## Pipeline joints that drift silently (work now, unguarded against tomorrow)

- **Frozen model ↔ live cohort CSV are decoupled.** Nothing at serve time checks the frozen μ/σ
  in `data/cohort_stats/condition_models.json` against `per_study_cohort.csv`. Drift is currently
  `0.00000` but **no test enforces it** — accreting pilot studies into the CSV without a re-freeze
  silently desyncs the headline band from the percentile bars. Two percentile engines also coexist:
  `condition_model._percentile_in` clamps [5,95] vs `cohort_placement._pctile` empirical [0,100]
  → same feature can differ ±5 pts at the tails on one page.
- **Heaviest axis routed-new / scored-legacy.** `int_cv_within` (β = -2.069,
  [`condition_models.json:149`](data/cohort_stats/condition_models.json)) is read from the new
  center-RF cine on new studies but z-scored against legacy sweep μ/σ (0.5067/0.1746). The
  cross-round intensity caveat that [`cohort_placement.py:55`](scripts/preflight_helpers/cohort_placement.py#L55)
  encodes is bypassed by the score. **Magnitude of the legacy-vs-new echo-scale gap is UNMEASURED**
  — the highest-value open quantity (needs a segmentation pass on paired legacy-sweep vs new-RF frames).
- **STAR rests on a folder-name BMI with no plausibility gate.** `star_ratio = qmt_mm/bmi`, BMI
  parsed from the study folder name ([`measurements.py:1460-1465`](labeling_app/measurements.py#L1460))
  with only a positivity check → a wrong-but-in-range weight flips the band; a missing one makes the
  headline silently vanish ([`p05_assessment_view.py:908-911`](labeling_app/assessment_pages/p05_assessment_view.py#L908)).
- **Not rebuildable off this machine.** [`cohort_stats_precompute.py:57-61`](scripts/preflight_helpers/cohort_stats_precompute.py#L57)
  hardcodes absolute sibling-repo paths (incl. a dated `20260503` run); [`feature_compute.py:33-35`](scripts/preflight_helpers/feature_compute.py#L33)
  imports an un-vendored `rf_quality`. A second site cannot reproduce the cohort or the measurements
  — a direct provenance blocker for the validation/permission unlock.

## Capture / measurement correctness

- **AKA017 scores on the wrong capture — fix designed, not applied** (`docs/implementation.md:110`).
  Root: [`study_deconfliction.py:131`](scripts/preflight_helpers/study_deconfliction.py#L131)
  anchors roles on `max(widths)` over all captures, so a stills-only orphan out-widths the real
  cine and steals the RF role; the `ambiguous_captures` flag written at
  [`materialize_studies.py:382`](scripts/materialize_studies.py#L382) is never read by
  `study_modalities` → dies in the manifest. A wrong 7.9 cm DICOM on 6.9 cm frames inflates every
  mm +14.5%. The cohort audit flags only AKA017 (17/18 clean) but is a disk×**manifest** hybrid —
  it would miss a clean future mis-set.
- **Femur trustworthiness is UNMEASURED cohort-wide** (only 2/18 segmented); two of three headline
  axes (STAR, fat) route from it. A targeted cohort femur-segmentation audit is owed.
- **Zero-echo `int_cv` debias** is a cheap, un-gated correctness win: the feature averages over
  near-black pixels ([`rf_quality/features/intensity.py:41`](../muscle_quality_exploration)); the
  Young fat-correction exists but is unwired. (Enrichment beyond this is legitimately Round-3-gated.)
- **EI-divergence warning doesn't fire for AKA017 as scored** — no sweep pick → the crosscheck at
  [`feature_routing.py:148`](scripts/preflight_helpers/feature_routing.py#L148) is skipped, so the
  one guard that would catch this class is silent on the exact case it should catch.

## Stubs and single-study pins

- [`preflight.py:173,288`](scripts/preflight.py#L173) — the cross-study numeric-reproduction gate
  raises "non-AGZ855 not implemented"; covers 1 of 3 reference studies, none of the new pilots.
- `check_study.py:30,354` — device parameter (freq/gain/preset/probe) extraction is an EXPERIMENTAL
  stub, "reports nothing trusted."
- `p03_frame_picker.py:6` / `assessment_app.py:8` — frame auto-suggest is a disabled stub (manual
  picking only).
- `leltek_deconfliction.py:14-22,30` — stale NOTE + stale `ROWS=585` (wrong for probe-829 Rows=570).

## Delivery-state notes

- 2 commits (`f69dbb6`, `2883252`) unpushed behind an expired `gh` token; two uncommitted arcs
  (İzometrik-CV column migration + the AKA017 capture audit).
- Live datasheet has 29 cols; git-tracked schema has 28 — a tree-revert + `--sync-structure --apply`
  ([`sync_records.py:221`](scripts/sync_records.py#L221)) would write 4-force headers positionally
  over 5-force live data. Commit the İzo-CV schema before any such run.
- `data/calibration_labels.jsonl` is **tracked** (committed `659f596`) despite `CLAUDE.md:121`
  saying "left uncommitted" — the convention text is now literally wrong.

## Reproduce the load-bearing ones

```
git -C . check-ignore -v data/studies_root_flat/ scratchpad/     # both NOT ignored → the PHI trap
jq '.C.n_fit' data/cohort_stats/condition_models.json            # 197 (the biased shipped subset)
sed -n '131p' scripts/preflight_helpers/study_deconfliction.py   # shallow_w = max(widths) → the capture bug
python scratchpad/score_vs_criterion/harness.py                  # the score↔force points (2 clean, both at ceiling)
```

# RF image-quality QA — audit orientation (anchor-free)

*This document orients an independent auditor to the RF (rectus-femoris) image-quality QA workstream in `muscle_mania`. It gives you four things and nothing else: the **claim** being made, a **map** of the artifacts and the order to read them, a **claims → evidence index** (each load-bearing number with the one source you re-derive it from), and the **known weak spots**.*

*It deliberately carries **no rationale and no defense** of the design. Your job is to attack the claim; pre-loading you with the builders' reasoning would bias that, and "it sounds settled" is the exact failure mode this audit exists to break. Read this with `rf_qa_audit_guidelines.md` (your stance and method). **Trust nothing here as reported — re-derive every number from its source yourself.***

---

## State in one breath

The QA **detectors** (per-frame quality flags), the **per-frame and aggregate grading** that consumes them, and the **capture UI** (the `p4x` page where an operator labels frames) are built and tested. The **calibration consumer** — the fitter that turns labels into thresholds, the promoter that flips a detector from pending to calibrated, and the compound-false-positive backstop — is **not built**. The calibration substrate currently in the repo is the **wrong frame pool** (a fix is planned, not done — see Gaps). So the central claim, that the loop reaches defensible per-detector accuracy from operator labels, **has not been demonstrated end to end.**

---

## The thesis under audit

The workstream asserts, in substance:

> *A per-detector-calibrated image-quality QA layer makes RF muscle-ultrasound assessment trustworthy enough for its stated use — and the calibration loop (operators label frames per detector → a fitter sets each threshold from those labels → a promoter flips it `pending → calibrated`) reaches defensible per-detector accuracy from operator labels.*

Pin the **stated use** yourself from canonical plan § 1 before you start. The builders' framing is: RF-thickness / QMT / STAR measurements, pooled per study and placed against a reference cohort, for research-grade muscle assessment. If the intended use is under-specified, that is itself a finding — "trustworthy enough" is meaningless without a use to be trustworthy *for*.

Decompose the thesis into sub-claims you may take separately:

1. **Goal claim** — frame-level image-quality QA is the right lever for making the downstream RF measurement trustworthy for the stated use.
2. **Architecture claim** — 15 hand-engineered per-detector primitives, each separately calibrated and gated, is a sound way to do that QA.
3. **Method claim** — operator labels collected through the capture loop can fit thresholds with defensible sensitivity/specificity that generalise out of sample.
4. **Trust claim** — the deployed verdict (calibrated-flags-only, compound-FPR-gated) is honest: it neither over-flags clean frames nor passes bad ones.

You are licensed to reject the **framing**, not only the execution. Alternatives to weigh on their merits, not because they are recommended here: a single **learned frame-quality grader** vs 15 hand-engineered detectors; **one overall study-quality gate** vs per-detector gating; **fixing the measurement itself** rather than gating frames; or **no QA layer at all** — is QA the bottleneck, or is it the segmenter, the measurement definition, or the cohort?

---

## Artifact map and read order

Read in tier order. Tiers 1–4 are the work; **Tier 5 is the builders' narrative — read it last and treat it as claims to check, not ground truth.** Paths are relative to the repo root.

### Tier 1 — the claim and the contracts

| Artifact | What it is | Why you open it |
|---|---|---|
| `docs/rf_qa_audit_guidelines.md` | Your mandate | Stance, coverage axes, independence rule, the limits of this audit |
| `docs/rf_qa_canonical_plan_v0_1.md` | The canonical contract (v0.2, ~215K) | § 1 use · § 4 failure-mode catalogue · § 5 detectors · § 6 gating tables · § 7 data contract · § 9 transparency rules · § 11 calibration method · § 13 limitations · § 10 open questions |
| `docs/rf_qa_detector_calibration_spec.md` | The calibration-loop contract (v0.2, ~40K) | § 1 scope · § 3 in/out-of-scope · § 4 lifecycle ladder · § 5 label semantics + pre-fill exclusion · § 6 compound-FPR · § 7 drift · § 8 per-detector cards · § 9–10 round protocol |

### Tier 2 — the code (read the code, not the summaries of it)

| Artifact | What it is | Why you open it |
|---|---|---|
| `labeling_app/image_quality.py` | The 15 detectors | The primitives; `calibrated_flags`; the `@_detector` provenance wrapper that stamps `pending_calibration` |
| `labeling_app/qa_thresholds.py` | The threshold registry | `THRESHOLDS`, `ThresholdEntry`, `is_starting_point` — the calibration boundary |
| `scripts/preflight_helpers/feature_compute.py` | Per-frame grading | `compute_image_quality_features`, `ImageQualityFeatures`, the QMT provenance cap |
| `scripts/preflight_helpers/aggregation.py` | The pooling gate | `_aggregate_with_image_quality`, `_rf_consistency_report` |
| `scripts/calibration/{candidate_selector,label_store,frame_loader,build_predicted_score_table}.py` | Substrate + capture machinery | Candidate selection, the label store, frame loading, the cohort score-table builder |
| `labeling_app/qa_calibration_spec.py` | Machine-readable per-detector spec | Mirrors the spec doc; self-checks at import (claims to drift-fail) |
| `labeling_app/assessment_pages/p4x_calibration_capture.py` (+ `qa_calibration_aux.py`) | The capture UI | Where the operator labels frames; the blind-then-reveal flow |

### Tier 3 — the evidence (re-derive every number here)

| Artifact | What it is | Why you open it |
|---|---|---|
| `data/cohort_stats/qa_failure_mode_histogram.json` | Cohort firing rates | Per-detector fire fractions + `pending_calibration` per detector |
| `data/cohort_stats/qa_per_frame_detector_scores.parquet` | The per-(frame, detector) score table | The fit substrate (predicted masks); row count, columns, `mask_source`, `code_sig` |
| `exports/decision_log_full_3operators.csv` | The 3-operator quality-gate labels (gitignored) | Overall study-quality labels; the alive-cohort filter lives here |

### Tier 4 — the tests (run them)

| Artifact | Why you open it |
|---|---|
| `tests/calibration/`, `tests/preflight/test_qa_aggregation.py`, `tests/preflight/test_qa_surfacing.py`, `tests/semantic_invariants/`, `tests/ui/test_p4x_calibration.py` | The claimed behaviour, as executable assertions — run them; don't trust the reported counts |
| `scripts/trace_frame.py` | Frame-trace CLI + semantic-invariant checks across post-processing → detectors → picker → aggregation |

### Tier 5 — builder narrative (read last; treat as claims, not truth)

| Artifact | What it is |
|---|---|
| `HANDOFF.md` | The running state log — the builders' reasoning and "resolved" framing. The single most anchoring document; read after you have your own view |
| `~/.claude/plans/ok-now-we-plan-adaptive-yeti.md` | The workplan: subsystems A–K, sequencing, metrics M1–M9, risks R1–R15 (external to the repo) |
| `docs/{build_step_2_plan,qa_ui_surfacing_plan,qa_data_collection_plan,assessors_guide_v0}.md` | Supporting plans and the cohort-pass rubric |

### Document currency

The artifacts were written at different times and do not all reflect the same state. **Where they disagree, the code is the current system — verify behavior against the code, not the prose.**

- The **canonical plan** is v0.2 (last changelog 2026-05-21). It predates the 2026-05-24 → 28 operator-review redesign series (see `HANDOFF.md`'s session log) and the **calibration spec** (2026-05-28). Read it as design intent, not current-state truth: several § 5 detector descriptions describe superseded designs. Confirmed example — `wrong_site`: the canonical plan (§ 4.2 F1, § 5.2, § 11.1, § 14, § 17) documents an `rf_fraction` quadriceps-composition design, but the live `_detect_wrong_site` (`labeling_app/image_quality.py`) is a different primitive (`rf_column_coverage`). Audit the live detector, not the plan's description. The supporting plans (`qa_data_collection_plan.md`, `qa_ui_surfacing_plan.md`, `build_step_2_plan.md`) predate the redesigns too and carry the same stale `wrong_site` 79.8% / 4-calibrated framing.
- The **calibration spec** (v0.2, 2026-05-28) and the **histogram JSON** (regenerated after the redesigns) are the most current design and evidence artifacts.
- `HANDOFF.md` carries a dated **CURRENT-STATE CORRECTIONS** block at the top that reconciles the calibrated count and the `wrong_site` design across the corpus; treat anything below it that predates it as historical. (It is Tier-5 builder narrative regardless — check it, don't trust it.)

---

## Claims → evidence index

Each row: a load-bearing claim, and the **one source** you re-derive it from. The "how" column is the re-derivation, not a vouching — recompute it; do not trust the figure as written here.

| Claim | Source (one hop) | How to re-derive |
|---|---|---|
| 15 detectors ship; **5 calibrated, 10 pending**; 7 of the 10 are in Round-1 scope | calibration spec § 1 + § 3; `qa_failure_mode_histogram.json` (`n_detectors_calibrated`) | Read both; cross-check the named detectors against `pending_calibration` in the JSON and `is_starting_point` in `qa_thresholds.py` |
| Only **calibrated** flags drive the deployed verdict; pending flags are recorded but never gate or pool | `feature_compute.compute_image_quality_features`; `image_quality.calibrated_flags`; `tests/preflight/test_qa_aggregation.py` | Read the function; run the test module |
| `wrong_site` fires on **4.6%** of cohort (44/956), post-v3 redesign (was 79.8% pre-v3) | `qa_failure_mode_histogram.json` → `per_reason_code_frequency.wrong_site` | Read the JSON field |
| `edge_clip_lateral` **98.4%**, `dark_clip_high` **18.0%**, `femur_out_of_frame` **15.0%** | same JSON | Read the fields |
| `saturation_high_global/_regional` fire on **0%** — modality-inert (`_final.png` caps at pixel 244, below the 250 rail) | same JSON (`sanity.findings`); canonical § 13 / FW-25 | Read the JSON sanity block; confirm the rail in `qa_thresholds.py` |
| QA detectors grade the synthesized **`_final`** modality (D3 — the despeckled device "final", re-encoded to PNG and **sized to the raw/before_DSC dimensions**), not `raw` or `before_DSC`; each capture frame exists in all three modalities. The p03 picker already enumerates + segments sweep frames in this modality. A rebuild over the raw sweep pool must synthesize the same `_final` (size included) or its scores are not comparable to the deployed cohort scores | `synthesize_final_u8`; D3 (`_final`, canonical § 2 / Finding C); the p03 picker in `assessment_app.py` | Trace which modality **and pixel size** each detector actually sees end-to-end; a `raw` / `before_DSC` / wrong-size mixup yields scores incomparable to the cohort table |
| Cohort score table = **10,516 rows**, predicted masks, PHI-clean | `qa_per_frame_detector_scores.parquet`; `build_predicted_score_table.py` | `parquet` row count + column list; confirm `mask_source == predicted` |
| **Alive cohort = 272 distinct studies** (operator `akadir` ∧ `decision_status == completed`, 274 rows − 2 re-saves); 47 `quality_failed` (the 128LH probe); 319 total | `exports/decision_log_full_3operators.csv` + the filter | Group by `(operator_id, decision_status)`; count distinct `study_code` |
| 3-operator quality-gate substrate: `akadir`/`anonabx`/`anonacl`, **960 rows**, overall study quality (not per-detector) | same CSV | Distinct `operator_id`; row count; column list |
| Each detector self-reports `pending_calibration`; no hand-maintained flag→threshold map | `image_quality.py` `@_detector` wrapper; `qa_thresholds.ThresholdEntry.is_starting_point` | Read the wrapper and the dataclass |
| Every QA threshold resolves from the registry (no literals outside it) | `qa_thresholds.THRESHOLDS` | Grep the codebase for QA threshold literals outside the registry |
| `frame_rf_consistency` gate = modified Z-score `0.6745·(x−median)/MAD`, cut 3.5, ≥4 frames | `aggregation._rf_consistency_report`; `qa_thresholds.py` | Read both |
| QMT verdict is provenance-capped (a proxy QMT is never `valid`) | `feature_compute.py` (`qmt_provenance_capped_tiers`) | Read the cap logic |
| Aggregate-verdict rule *as specified*: ≥2 calibrated detectors must concur on `invalid` before the aggregate downgrades | calibration spec § 6 | Read § 6 — then check the Gaps: is it implemented? |
| Full suite: **599 passed / 10 skipped / 1 xfailed** | `pytest tests/` | Run it |

---

## Known gaps (honest, as the builders understand them)

These prime you that weaknesses exist; they are not exhaustive, and finding gaps **not** listed here is part of the job.

1. **The loop has never closed.** The consumer half — fitter, promoter, compound-FPR backstop — is not built. **Zero detectors have been calibrated through the operator-label loop.** The 5 "calibrated" detectors were made trustworthy by reuse (femur/edge logic borrowed from the QMT pipeline) and one redesign (`wrong_site` → column-coverage), **not** by the loop. So the Method claim (3) and Trust claim (4) have no end-to-end evidence yet. (HANDOFF "Resume here"; spec § 4 ladder; plan subsystems F–K unbuilt.)

2. **The calibration substrate in the repo is the wrong pool.** The cohort score table is built on the 956 annotator-selected frames (~3/study) — a clean, measurement-biased subset that under-represents the failure modes (motion blur, air gap, vessel shadow) the pending detectors must catch. The ratified fix — rebuild over the **full sweep pool of the 272 alive studies at 5 fps** — is the next action and **is not yet done**. Until then, failure-mode detectors cannot calibrate on representative data. (HANDOFF substrate re-scope; Step 2.)

3. **Clinical correctness of the gating tables is unconfirmed.** The per-measurement valid/borderline/invalid gating (canonical § 6) and the failure-mode catalogue (§ 4) have had no clinical-authority sign-off — tracked as open question Q1 / § 17 future-work. The QA layer's clinical validity rests on tables no clinician has reviewed.

4. **Two saturation detectors are structurally inert on the shipped modality.** They fire on 0% because `_final.png` caps at pixel 244, below the 250 rail. Unresolved: re-pin the rail or move saturation QA to `_raw.png`. (Histogram JSON sanity; canonical § 13 / FW-25.)

5. **`subq_fat_compressed` is structurally weak single-frame.** Single-frame compress-vs-anatomy discrimination is weak; the real disambiguator is sweep-level. Out of Round-1 scope. (Canonical § 13 L8; spec § 3.)

6. **Circularity / rubber-stamping risk.** The operator label is the ground truth that sets each threshold — but if the UI reveals the score before asking, or pre-fills the answer, the label echoes the detector and it validates against itself. Guarded by blind-before-reveal plus a pre-fill exclusion rule and an anchoring-rate metric (spec § 5; canonical § 7.0, § 11.2) — but the guard's effectiveness is unproven, because no loop has run. (Spec § 5; canonical § 7.0, § 11.2.)

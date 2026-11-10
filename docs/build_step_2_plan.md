# Build-Step 2 — execution plan (the do-now phasing)

*The sequenced, do-now execution plan for Build-Step 2 of the RF image-quality QA/QC workstream — every piece of engineering that can proceed **now**, with no operator, no calibration, and no cohort assessment pass. It re-slices the canonical plan's § 14 build sequence (Steps 2a / 4 / 6 and the § 8 UI surface) into a dependency- and value-ordered sequence an implementer can execute top to bottom.*

*This is **execution sequencing, not new contract.** Every item traces by § reference to the canonical plan — `docs/rf_qa_canonical_plan_v0_1.md` (internal version v0.2). That document is the spec and it governs; this file only orders the work, names the artifacts, and states each item's validation bar. Build-Step 2's deliverables and status tags are the plan's § 14; if this file and the plan disagree, the plan wins — fix this file.*

*Created 2026-05-21. Predecessor: **Build-Step 1**, committed `a5f4e3e` on `main`. Implementation begins in a fresh Claude Code session: read `HANDOFF.md` first, then this file, then the canonical plan's § references as each item calls for them.*

---

## § A — Why this phasing (the sequencing principle)

**Build inside-out.** Lock the *contracts* first — the segmentation-confidence signal, the detector-module entry point, the gating function, the threshold registry — so every detector and every consumer is written against a stable interface and is never retrofitted. Then the *detectors*. Then the *wiring* that makes a QA verdict actually change an assessment number. Then the *surface* (the UI).

Two rules inside that arc:

- **Front-load the calibration-free parts.** Threshold-tier A detectors, and detectors that reuse an existing pipeline signal, ship *trustworthy* verdicts on day one — they never need calibration. They precede the algorithm-heavy Tier-0 detectors, which ship and *run* but render "pending calibration" (§ 8.6) until Build-Step 2b.
- **Nothing on this path waits on a label.** The single hard data-dependency in the whole § 14 sequence is Step 1's labels → Step 2b's calibration (§ 14 closing note). Every phase below is upstream of it. Calibration, when it comes, only sharpens threshold *numbers* in the registry (§ 12.4) — it is a data-update, never a code rebuild, and it never gates this work.

**The today/tomorrow slice is Phase 1, items 1–3** — the signal, the module/record contract, the gating spine. Everything in Phases 2–3 hangs on them.

## § B — The phased plan

Each item is **name · action · done-when** — the done-when is the validation bar, the measurable test for "done" (the § 14 discipline). The line under each phase header is its placement logic.

### Phase 1 — Foundations (the contracts everything else is coded against)

*Fix these four interfaces once; every detector and consumer is then coded against stable contracts.*

| # | Item · plan § | Action | Done when |
|---|---|---|---|
| 1 | Segmentation-confidence signal · § 5.4 | In `predict_mask` (`scripts/preflight_helpers/segmenter.py`), softmax the already-materialised logits tensor, reduce the mask-boundary band to a per-pixel confidence map + a boundary-entropy scalar, return them via an optional out-parameter (the `final_u8_out` idiom — no caller breaks) | with the parameter omitted, `predict_mask` is byte-identical to today (regression); with it on, it returns the map + scalar; a unit test on a synthetic logits tensor passes |
| 2 | QA module + record contract · § 5.5, § 7.1, § 7.2 | Create `labeling_app/image_quality.py` exposing `compute_image_quality_flags(img_gray, mask) -> {'flags', 'detector_metadata'}`; define the `ImageQualityFeatures` frozen dataclass (§ 7.1, joined to the frame record on `frame_base`); add `LABEL_VL = 3` and `LABEL_VM = 4` to `measurements.py` | the module imports clean; `compute_image_quality_flags` returns the documented shape on a synthetic mask; `ImageQualityFeatures` constructs and round-trips |
| 3 | The gating spine · § 6.1–6.3 | Implement `image_quality_tier(measurement, flags) -> 'valid'\|'borderline'\|'invalid'` encoding the § 6.2 gating table, and the § 6.3 aggregate-tier rule (headline-4 hard, ≥ 5-of-8 secondary; ships with the 60 % default) | a table-driven unit test reproduces every § 6.2 cell; the § 15 worked examples A and B reproduce end to end |
| 4 | The threshold registry · § 12.4 | Create `labeling_app/qa_thresholds.py` — the `ThresholdEntry` dataclass + the `THRESHOLDS` dict; Threshold-tier A values filled (pixel rails, edge-clip N = 1, femur reuse, `QMT_CENTRAL_ZONE_MARGIN_PX`), Tier-B entries present as starting points with `calibrated_on=None` | every detector threshold resolves to a registry entry; a grep confirms no QA threshold literal lives outside the registry (§ 12.4 single-source rule) |

### Phase 2 — The detectors

*The front-loaded detectors go live first: `femur_*` / `edge_clip_*` are genuinely Threshold-tier A — a reused calibrated signal or a physics rail — and trustworthy day one; `saturation_high_*` / `dark_clip_high` pair a Tier-A pixel rail with a Threshold-tier B fraction cut-off (§ 11.1), so their verdict is "pending calibration." The algorithm-heavy Tier-0 detectors ship and run, also "pending calibration." The histogram is the first honest readout of the cohort's quality.*

| # | Item · plan § | Action | Done when |
|---|---|---|---|
| 5 | Front-loaded detectors — **live** · § 5.2, § 11.1 | Implement the detectors that reuse a calibrated signal or key on a physics rail: `femur_out_of_frame` / `femur_off_central_zone` (lift the column-wise `femur_absent` / `outside_central_zone` to a frame-level flag — Threshold-tier A by reuse), `edge_clip_lateral` / `edge_clip_vertical` (directional extension of `touches_edge` — Tier-A), `saturation_high_global` / `_regional` and `dark_clip_high` (the ≥ 250 / ≤ 5 pixel rails — **a Tier-A rail paired with a Threshold-tier B fraction cut-off**, § 11.1, so the verdict is pending calibration) | each fires on a synthetic positive and stays silent on a synthetic negative; the femur / edge detectors agree with the existing `QMT_VALIDITY_FLAG_PRIORITY` / `touches_edge` signals on cohort frames |
| 6 | Tier-0 algorithm detectors — pending-calibration · § 5.2, § 5.4 | Implement `motion_blur` (fascia-band Tenengrad), `tgc_banding` (σ of the detrended row-mean residual), `wrong_site` (quadriceps class-composition `rf_fraction` + the `MIN_QUAD_PX` off-thigh floor), `air_gap_reverberation` (FFT comb in the segmented skin–fascia gap), `near_field_clutter`, `vessel_shadow_in_roi`, `subq_fat_compressed` (central-zone fat thickness); plus the `segmentation_low_confidence` boundary-entropy reduction consuming item 1's signal. Each reads its starting-point threshold from the registry | each fires / stays silent on synthetic positive / negative cases; every Tier-B threshold renders the § 8.6 "pending calibration" state — no guessed verdict ships as fact |
| 7 | Cohort failure-mode histogram · § 14 Step 2a | Run `compute_image_quality_flags` over the full cohort; emit the per-reason-code frequency distribution (the § 14 Step-2a deliverable, and the input to the later Step-2 readout) | the histogram covers every cohort frame; the distribution is sane (`edge_clip_lateral` common per the C11 rationale, `wrong_site` rare) — **completes Build-Step 2a** |

### Phase 3 — Make it bite, then surface it

*Aggregation wiring is what turns a QA verdict from a display into something that changes the assessment number. Step 4 generalises the proven QMT-validity pattern. The UI is where it all becomes operator value — and the confidence heatmap can light up the moment item 1 lands.*

| # | Item · plan § | Action | Done when |
|---|---|---|---|
| 8 | Wire the QA tier into aggregation — Step 6 · § 7.3 | Implement `_aggregate_with_image_quality` in `aggregation.py` — partition the feeding frames by `image_quality_tier_per_measurement`, apply the three-tier prefer-valid / fall-back-to-borderline / never-pool-invalid rule (generalising `_aggregate_qmt`) | an integration test confirms a frame flagged `invalid` for a measurement is excluded from that measurement's pool and the aggregate re-pools; an all-valid run is unchanged |
| 9 | Step 4 — consistency gate + generalise · § 14 Step 4 | Promote `frame_rf_consistency` from a passive metric to an active gate; apply the generalised `image_quality_tier()` validity pattern to ≥ 1 non-QMT measurement | `frame_rf_consistency` actually gates a frame in aggregation; the generalised-pattern integration test passes |
| 10a | The § 8 UI — the **read** surfaces · § 8.3, § 8.6 | The picker + result-card QA badges, the "what changed" drilldown panel, the `ungraded` honest state, the raw-record view, the segmentation-confidence heatmap — the Tier-0/1/3 surfaces. **Re-scoped: `docs/qa_ui_surfacing_plan.md` is item 10's controlling input** — the surfacing inventory + tier model; this row's § 8.3 sketch predates Build-Step 2 items 8–9, which each added hidden decisions to surface. Read it first. | a Layer-3 operator click-through shows, per graded frame, the verdict + dominant reason + a drilldown naming every pool exclusion / tier change with its evidence; `ungraded` never renders as `valid`; the heatmap renders |
| 10b | The § 8 UI — the **capture** surfaces · § 8.3.4–8.3.6, § 8.6 | The Phase-4 checkbox pre-fill via the `prefill_flags` seam, the `override_action` UI + Gate-1 pre-fill, the § 8.6 "pending calibration" reactable surface + the pending-detector calibration queue, Channel-D explicit-confirm (`docs/qa_ui_surfacing_plan.md` § E). Designed from what 10a's operator prototype teaches. | an operator can react to a verdict and to a pending detector; the reaction yields a § 11.2-usable label; the § 7.0 pre-fill circularity rule holds (a silent pre-fill accept is excluded; an explicit confirm / override is a label) |

### Parallel — off the critical path, do anytime

| # | Item · plan § | Action | Done when |
|---|---|---|---|
| 11 | Tier-1 one-page technical spec · § 2.3 | Write the § 2.3 one-page Tier-1 spec — confidence-map algorithm choice, input/output API, where confidence maps are stored, thresholds to calibrate, validation cohort, owner | the one-pager exists and covers every § 2.3 checklist point — so Step 3 can begin the moment the Step-2 readout calls for it |

## § C — Artifacts ledger (the tools this build creates and touches)

**New files**

- `labeling_app/image_quality.py` — the detector module: `compute_image_quality_flags()` + every Tier-0 detector (items 2, 5, 6).
- `labeling_app/qa_thresholds.py` — the threshold registry: `ThresholdEntry` + `THRESHOLDS` (item 4, § 12.4).
- `scripts/calibrate_image_quality_detectors.py` — the calibration script (§ 11.4). Do-now-*able* (the script can be written before there is data) but it only *runs* at Build-Step 2b; write the skeleton alongside item 4 or just before 2b. Not on this build's critical path.
- New test modules — e.g. `tests/preflight/test_image_quality.py` (detectors + gating), `tests/preflight/test_qa_aggregation.py` (the § 7.3 hook); UI coverage extends `tests/ui/test_appflow.py`. Exact split is the implementer's call.

**New symbols**

- `compute_image_quality_flags(img_gray, mask)` — in `image_quality.py` (§ 5.5, § 7.2).
- `ImageQualityFeatures` (frozen dataclass) — § 7.1, "lives beside `FrameFeatures`, joined on `frame_base`"; exact module (`feature_compute.py` vs `image_quality.py`) is an implementer call.
- `image_quality_tier(measurement, flags)` — § 6.1, modelled on `qmt_tier()` (`aggregation.py:80`); placement (`aggregation.py` vs `image_quality.py`) is an implementer call.
- `_aggregate_with_image_quality()` — in `aggregation.py`, sibling of `_aggregate_qmt` (§ 7.3).
- `ThresholdEntry`, `THRESHOLDS` — in `qa_thresholds.py` (§ 12.4).
- `LABEL_VL = 3`, `LABEL_VM = 4` — added to `labeling_app/measurements.py`.
- the per-detector functions — in `image_quality.py`.

**Touched files**

- `scripts/preflight_helpers/segmenter.py` — `predict_mask` gains the optional confidence out-parameter (item 1).
- `scripts/preflight_helpers/feature_compute.py` — `ImageQualityFeatures` + its join to `FrameFeatures`.
- `scripts/preflight_helpers/aggregation.py` — `image_quality_tier()` and/or `_aggregate_with_image_quality()`.
- `scripts/preflight_helpers/run_pipeline.py` — wire the QA compute into the pipeline; persist `ImageQualityFeatures` into the run record.
- `labeling_app/measurements.py` — `LABEL_VL` / `LABEL_VM`.
- `labeling_app/assessment_pages/p03_frame_picker.py` — the per-thumbnail QA badge (§ 7.5).
- `labeling_app/assessment_pages/p04_frame_review.py` — pre-fill via the `prefill_flags` seam; the `override_action` UI + Gate-1 pre-fill.
- `labeling_app/assessment_pages/p05_assessment_view.py` — per-card QA badge + cohort caveat.
- `scripts/cohort_stats_precompute.py` (or a new sibling script) — for the failure-mode histogram (item 7).

**The Build-Step-1 base it builds on** (already in the repo, committed `a5f4e3e`)

- `IMAGE_QUALITY_FLAG_PRIORITY` — the § 5.1 22-code taxonomy; the detector flag namespace.
- `CONTRACT_VERSION = "v0.2"` — Build-Step 2 begins stamping it onto records.
- `canonical_image_quality_flag_order()` — the priority-order helper.
- `GateAnswers` extended with `drop_reason_codes` / `drop_reason_other` / `qa_overrides` (the QA-capture schema).
- `_structured_drop_reasons(fb, prefill_flags=())` in `p04_frame_review.py` — the `prefill_flags` seam item 10 wires; and `_gate_completeness_issues` (the lock-gates completeness gate).

## § D — Not in Build-Step 2 — what genuinely waits

For completeness, the work that genuinely needs an operator, a label volume, or the cohort pass — and so is *not* in the do-now path above:

- **The cohort assessment pass** — the one upstream event (~50–100 operator-hours); everything below hangs off it.
- **Step 2b** — Threshold-tier B calibration (the § 11.2 five-step process); consumes the cohort-pass labels.
- **Step 2.5** — the IRR study; two operators × 150 frames; gates the *publish claim*, not the build.
- **Step 5** — the Tier-2 learned grader; ≥ 200 labelled positives *per code* — months of label accumulation.
- **Step 6.5** — operator-acceptance / clinical sign-off.
- **Step 3 / 4.5** — the Tier-1 confidence-map detectors and their re-calibration; gated on the Step-2 readout (item 7's histogram + a false-negative review), per the § 2.3 deferral. Item 11 (the one-page spec) is the do-now prerequisite.
- **Anonaao's clinical review** of `docs/assessors_guide_v0.md` and the § 4 / § 6 sanity check (future-work FW-2).

## § E — Disciplines for the fresh session

- **Standing disciplines** — commit/push only on Akadir's explicit word; the PHI guardrail; the verification ladder; launch the app with `./run_app.sh` — all in `HANDOFF.md`.
- **Each item's done-when is its verification gate.** Meet it before moving on; Layer-2 (`pytest tests/`) green before any Akadir click-through. Self-administer Layers 1–2; Layer 3 is Akadir's.
- **Tier-B detectors ship visibly "pending calibration"** (§ 8.6) — never wire an operator-facing claim to an un-calibrated number. Build-Step 2b sharpens those numbers later; the detector code does not change.
- **The plan governs.** If this file and `rf_qa_canonical_plan_v0_1.md` disagree, the plan wins — fix this file.

---

*Build-Step 2 is the do-now engineering of the RF QA layer. It produces a running pipeline whose Threshold-tier A detectors are trustworthy on day one and whose Tier-B detectors are honest about being un-calibrated — ready for Build-Step 2b to slot calibration in as a data-update the moment the cohort assessment pass delivers labels.*

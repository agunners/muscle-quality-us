# RF QA layer — the layered plan (option A: study-level robustness-filter QA)

*Authored 2026-05-30. One self-contained plan a fresh session can execute. It supersedes the sequencing in `docs/rf_qa_next_phase_plan.md` for the build, and inherits its ratified direction verbatim. Every load-bearing number below was verified against the on-disk code/data this session (not doc prose); where a number is load-bearing it carries its source as a clickable link.*

> **Honesty-pass note (2026-05-30, main session).** This plan was authored by a multi-agent workflow and then adversarially self-checked (verdict: *revise*). I read it end-to-end and re-assessed each finding against the text. Disposition: (1) the check's headline "Part 1 over-builds" — judged **already mitigated** (§1.6 + §6.1 make the ship path explicitly Slice A + Slice B; Slices D/E are labeled non-blocking / off-the-ship-path), so no restructure was done — a future reader need not re-raise it. (2) The one substantive catch — the clean-study provenance line — was a real internal contradiction (§1.2 said always-show; §3.3 said render-only-when-set-aside); reconciled to **quiet-on-clean by default** (the cardinal rule), with the always-on line preserved as the key open design call (§6.4). (3) Fixed the six-vs-eight gate-count wording. Everything else the check raised was already handled in the text.

---

## Executive summary + the cardinal rule

**What this is.** An RF (rectus-femoris) thigh-muscle ultrasound **ASSESSMENT** app (operator flow: p01 study-pick -> p02 model-pick -> **p03 frame-picker** -> **p04 frame-review** -> **p05 study-level trusted summary**). The QA/QC layer is a sub-process woven into that flow, never a standalone "labeling app". This plan specifies **option A** — a study-level, robustness-filter QA layer (ratified 2026-05-30, [docs/rf_qa_next_phase_plan.md:123](docs/rf_qa_next_phase_plan.md#L123)) — that produces a **trustworthy study-level number with an honest disclosure**, ships **without a labeling campaign** on reuse + defaults, and surfaces a calibrated per-thumbnail cue at p03. The honest starting fact: **option A is ~80% already in the codebase** — the per-frame grade, the pooling gate, the consistency gate, the picker cue, and the entire `qa_surfacing` presentation spine are built, tested, and wired. Part 1 is mostly **wiring + one render + one re-scope**, not greenfield. **Wave-1 scope note (Akadir 2026-05-31):** two deliberate, scoped additions join A+B in wave 1 — **Slice F** (the RF-representative-frame scorer; §1.6 — improves *which frame we measure*, reusing the pre-scan + repr-suggestion machinery) and **Slice G** (surface the full 272-study cohort; §1.7 — the app currently opens only ~5). Both are bounded; Slice F ships as an *advisory score* with its cross-study-comparability claim deferred to validation (Part 4).

**The cardinal rule — DEEP DESIGN, THIN BUILD.** Parts 2-4 (data architecture, surfacing/UX, algorithmic rigor) are deep and carry researched best-practice with cited sources, because they are the things that, if gotten wrong now, are expensive to fix later. Part 1 (the near-term build) is minimal: ship the smallest robust thing, then iterate from operator reactions. This is the project's ratified **"contract carries rigor, UI ships thin"** principle ([memory `feedback_contract_carries_rigor`]). Part 5 (the user-facing app / subsystem-B per-frame coach) is documented **INTENT + HOOKS** so it is recoverable, **not built now** — it is blocked on DATA, not design.

**The friction-budget discipline (the cross-cutting safety constraint).** Default **QUIET**. Every surfaced flag must EARN its place against a default of silence, and the test it must pass is not "is this true?" but "**does the operator need to act on this, on this screen, for this task?**" Badge-fatigue is the named enemy: a QA flag the operator learns to ignore is strictly *worse* than no flag — it costs the same glance and trains the eye to skip the *next* flag, including the one that mattered. This is not a style preference; it is the most evidence-backed UX decision in the layer (Part 3.0). The study-level frame makes default-quiet affordable: **a false fire only drops one frame from a robust pool** (the study number barely moves), so the cost of suppression is low and the cost of over-surfacing dominates.

**Two PHI/commit guardrails, non-negotiable.** (1) Every record carries `study_code + frame_base` ONLY — never a patient-name folder path or demographics beyond the parsed-safe set (Part 2.2). (2) Commit/push only on the human's explicit word; nothing has been pushed (`origin/main` at `57e599f`, all calibration-loop commits local — [HANDOFF.md:137](HANDOFF.md#L137)).

---

## Part 0 — Grounding (the verified factual snapshot)

*Read this before touching code. Every number here was read off the on-disk data/code this session.*

### 0.1 The product and the two operator tasks

The operator has exactly **two real tasks**, and every surface in this plan ties to one of them:
- **Task 1 (p03):** pick a good representative frame out of the sweep.
- **Task 2 (p05):** decide whether to trust the study number the app reports.

The p05 headline measurements: STAR (QMT/BMI), full-RF median thickness, fascicular contrast (`int_cv_within`), RF peak thickness — plus secondary QMT, visible RF area, muscle/fat ratio ([qa_surfacing.py:46](labeling_app/assessment_pages/qa_surfacing.py#L46)).

### 0.2 The small validated layer — verified live state

*Six detectors + two pooling gates = the eight rows below (collectively "the gate layer").*

| Gate | Mechanism (code) | Tier / state | Fires (canonical 956) | Drives p05 pool? | Drives p03 cue? |
|---|---|---|---|---|---|
| `dark_clip_high` | ROI fraction <=5 grey `> 0.02` ([image_quality.py:693](labeling_app/image_quality.py#L693)) | rail Tier-A; **fraction Tier-B 0.02, pending** ([qa_thresholds.py:153](labeling_app/qa_thresholds.py#L153)) | 18.0% | **No** (pending) | **No** (pending) |
| `wrong_site` | `rf_column_coverage < 0.5` on cleaned RF ([image_quality.py:754](labeling_app/image_quality.py#L754)) | **Tier-A by fiat**, pending=False ([qa_thresholds.py:143](labeling_app/qa_thresholds.py#L143)) | 4.6% | **Yes** (INVALID, all measurements) | **Yes** |
| `femur_out_of_frame` | reuses QMT `_dominant_femur_mask` ([image_quality.py:566](labeling_app/image_quality.py#L566)) | Tier-A reuse; calibrated | 15.0% | **Yes** (qmt/star INVALID) | Yes (suppressed when "no femur" dot says it) |
| `femur_off_central_zone` | reuses QMT `outside_central_zone` ([image_quality.py:604](labeling_app/image_quality.py#L604)) | Tier-A reuse; calibrated | 0.1% | **Yes** (qmt/star BORDERLINE) | Yes |
| `edge_clip_lateral` | RF body in outer 1-px lateral ring ([image_quality.py:621](labeling_app/image_quality.py#L621)) | Tier-A; **RESERVE — empty `_GATING_TABLE` row, taints NOTHING; wave-1 its lateral-clip primitive is reused *positively* by the representative-frame scorer (Slice F, §1.6)** ([image_quality.py:188](labeling_app/image_quality.py#L188)) | 98.4% | **No** (reserve) | **No** (reserve-filtered) |
| `edge_clip_vertical` | RF/fascia/femur in outer 1-px top/bottom ring ([image_quality.py:642](labeling_app/image_quality.py#L642)) | Tier-A; calibrated | 0.0% | **Yes** (rf_thickness INVALID) | Yes |
| QMT-provenance cap | `qmt_provenance_capped_tiers` ([image_quality.py:409](labeling_app/image_quality.py#L409)) | proxy->borderline cap, no-femur->invalid | — | **Yes** (caps qmt/star) | n/a (proxy note) |
| `frame_rf_consistency` | Iglewicz-Hoaglin modified-Z>3.5 sweep-outlier on `rf_thickness_mm`, min 4 frames ([aggregation.py:37](scripts/preflight_helpers/aggregation.py#L37)) | **Tier-A** (published statistical standard) | per-run gate | **Yes** (drops outlier from rf_thickness pool) | n/a (run-level) |

**Calibrated count = 5 of 15** (femur x2, edge x2, `wrong_site`) — `wrong_site` joined in the v3 redesign (`246e2ec`). The other 10 are pending: 5 carry a finite starting value (dark_clip 0.02, subq_fat 3mm, 2 saturation fractions, the aggregate-tier 0.60 floor), the rest are NaN-belted (cannot fire). The histogram JSON's `calibration_note` prose still says "4 calibrated" — stale artifact, harmless.

*Two clarifications from the 2026-05-31 review:* **(a) the `Fires` column is the clean annotator-selected 956 cohort** — selection-biased LOW for the centering/femur detectors (operators picked centered, femur-anchored frames), so e.g. `femur_off_central_zone` 0.1% is a floor; the full-sweep-pool rates run higher. **(b) the QMT-provenance cap is NOT redundant with the two femur detectors** — different layers: the detectors flag the *frame-geometry cause* (no femur / femur off the central plateau zone) and give the operator a plain reason; the cap enforces the *measurement-quality ceiling* by provenance, uniquely handling the `femur_supported_proxy` degraded-fallback the detectors don't model. The no-femur case is a deliberate double-cover; `femur_off_central_zone` is retained mainly as a reason code now the cap does the ceiling work.

### 0.3 The five hard constraints (design WITHIN these)

1. **Segmenter is the de-facto arbiter.** Every detector reads the segmenter's *predicted* mask ([feature_compute.py:474](scripts/preflight_helpers/feature_compute.py#L474)). Bad-frame failure modes are exactly where the segmenter is least reliable (chicken-and-egg): the 272-study probe found **1,297 frames with femur_px==0 and 74 with rf_px==0** that grade non-degenerate but make RF-ROI detectors return NaN.
2. **Cohort positive-starvation is REAL** (not a clean-cohort artifact). Probe over 18,332 sweep frames (272 alive studies): only `dark_clip_high` is a clean GREEN (3,629 fires / 108 studies); `motion_blur`/`vessel_shadow` have NO z>3.5 tail; `tgc`/`near_field` heavy-tail-not-separable; `air_gap` separable but 18-study-concentrated + 16% unscoreable. The 47-study rejected (128LH) re-run confirms starvation ([data/cohort_stats/qa_sweep_pool_readout.md](data/cohort_stats/qa_sweep_pool_readout.md)).
3. **Study-level frame.** A false fire drops one frame from a robust pool (low harm) — the compound-FPR concurrence gate is far less load-bearing here; revisit only if a composite per-frame verdict is surfaced.
4. **Calibrated-only at the picker.** `picker_qa_cue` ignores pending + reserve-state flags, is silent on clean ([qa_surfacing.py:622-650](labeling_app/assessment_pages/qa_surfacing.py#L622)). An un-calibrated detector (`wrong_site` was 80% pre-v3) would paint every thumbnail.
5. **Aggregate verdict over the calibrated subset only** (HANDOFF Finding B): a frame reads `valid` while `validity_flags` visibly carries an un-calibrated flag ([feature_compute.py:492](scripts/preflight_helpers/feature_compute.py#L492)); the pending set is materialized separately for transparency.

### 0.4 Data stores — verified on disk this session

- **Canonical score table** `data/cohort_stats/qa_per_frame_detector_scores.parquet`: **10,516 rows, 956 frames, 319 studies, 11 detectors, `code_sig 1580ea06c18764d6`, modality `_final.png`, contract v0.2**. This is the **annotator-selected "wrong pool"** (~3/study, under-represents failure modes); `candidate_selector._PARQUET` still points here ([candidate_selector.py:28](scripts/calibration/candidate_selector.py#L28)).
- **Probe substrate (the real evidence, NON-canonical)** `..._sweep_pool_5fps.parquet`: **201,652 rows, 18,332 frames, 272 studies, `code_sig 850b6e7c`, modality `_final(synth)`**. Plus the `_qf` rejected-cohort twin (4,096 frames / 47 studies) and the frame manifest. The two parquets correctly carry **different code_sig and modality** — the incomparability guard working as designed.
- **Both calibration ledgers ABSENT on disk** — `data/calibration_labels.jsonl` and `data/calibration_rounds.jsonl` do not exist; **zero labels collected, the loop has never closed.**

### 0.5 What is DECIDED (do not relitigate) vs OPEN

**Decided:** option A; no-campaign ship; the small validated set (above); `edge_clip_lateral` informational/reserve; two surfaces (p05 pooled disclosure + p03 calibrated cue); compound-FPR less load-bearing at study-level; subsystem B parked (blocked on DATA); the ungradeable/end-of-sweep gate (Q1) deferred (`wrong_site` already excludes those frames); p4x kept + re-scoped; calibrated count = 5 of 15; probe evidence settled.

**Open** (carried into Parts 1/6): the exact shape/wording of the p05 disclosure; the `dark_clip_high` cut + `wrong_site` 0.5 validation (optional trust-bar labeling, never a functional blocker); whether `dark_clip_high` surfaces at the picker as a steer (Part 3 lens vs Part 1 lens conflict — resolved below); the "X of Y" denominator convention; Anonaao on the §6 gating semantics; the §6 compound-FPR arithmetic (wrong by ~500x, dormant for A); a different model family to re-check the probe's separability heuristic; canonical-plan v0.3 reconciliation; **(Slice F) the two clinical assumptions A1 (longitudinal sweep → fullest cross-section ≈ mid-belly) and A2 (centroid-centering ≈ probe centered transversely), to confirm with Anonaao / the acquisition protocol, plus the cross-study-comparability validation of the representative-frame score; (Slice F) the C/K/M sub-score weights (start equal, tunable); (Slice G) any cohort the app should surface beyond the 272 alive (e.g. the 47 quality_failed for review).**

---

## Part 1 — Near-term build (option A): the smallest robust thing that ships

*MINIMAL + executable now. Almost everything load-bearing already exists; this part is four small, independent slices. Only Slice B is net-new UI. The ship path is Slice A (verify) + Slice B (one render).*

### 1.0 What is already true (so we don't rebuild it)

Verified this session: the per-frame grade is computed over the calibrated subset only ([feature_compute.py:492](scripts/preflight_helpers/feature_compute.py#L492)); `aggregate_features` already pools every measurement over its QA-eligible frames (prefer `valid`, fall back to `borderline`, never `invalid`) and records `_qa_tier` ([aggregation.py:188](scripts/preflight_helpers/aggregation.py#L188)); `frame_rf_consistency` already drops sweep outliers and records `_rf_consistency` ([aggregation.py:37](scripts/preflight_helpers/aggregation.py#L37)); `run_what_changed` + `frames_with_changes` already compute exactly the "pooled from X of Y; Z set aside (reasons)" data, **returning an empty list when nothing changed** ([qa_surfacing.py:381](labeling_app/assessment_pages/qa_surfacing.py#L381)); `picker_qa_cue` is the calibrated-only cue already wired into p03 ([qa_surfacing.py:594](labeling_app/assessment_pages/qa_surfacing.py#L594)); and the sweep-frame addressing the re-wire needs already exists as working code in `build_sweep_pool_score_table.py` ([build_sweep_pool_score_table.py:20](scripts/calibration/build_sweep_pool_score_table.py#L20)).

### 1.1 Slice A — verify the six-gate layer feeds p05 (near-zero code)

All eight rows (the six detectors + the two pooling gates) and their live state are in Part 0.2. The only real decision in Slice A: **does `dark_clip_high` ship driving the p05 verdict, or stay pending?**

**Decision: ship it PENDING (status quo) for MVP/demo.** Rationale: it is the only cleanly-GREEN acquisition detector (3,629 fires / 108 studies), but its 0.02 ROI-fraction cut is a borrowed starting point, not validated. On a clean cohort that medians `roi_dark_fraction ~= 0.0012` (p90 0.049, p95 0.096 canonical-pool), an unvalidated 0.02 cut will fire on the **upper tail of normal frames**, not just truly-dark ones — and a false fire that *softens the headline number's verdict badge* on a clean study is exactly the badge-fatigue the friction budget forbids. Promoting it to Tier-A is a **one-line registry flip** (`_tier_b(0.02) -> _tier_a(validated cut)`, [qa_thresholds.py:153](labeling_app/qa_thresholds.py#L153)) that waits for the optional validation pass (Slice E). No code path needs it to ship a trustworthy number. This keeps the calibrated count honest at 5 of 15 and the demo calm. It stays in p04's gray "extra checks the app is still tuning" sub-section.

**Slice-A acceptance:** the trace-frame ritual on canonical AGZ855 ([memory `feedback_trace_frame_pre_claim_ritual`]) shows the expected per-measurement tiers and pool decisions unchanged; `pytest tests/preflight/test_qa_aggregation.py tests/semantic_invariants/test_data_invariants.py` green (the latter guards the `edge_clip_lateral` empty-row reserve invariant). **No production-code change** if `dark_clip` stays pending.

### 1.2 Slice B — the p05 "pooled from X of Y; Z set aside" disclosure (the ONE net-new render)

The only genuinely new UI in Part 1, small because the **data model already exists**: `frames_with_changes(aggregate, frame_features)` returns `{n_total, n_set_aside, frames[], run_level[]}` ([qa_surfacing.py:475](labeling_app/assessment_pages/qa_surfacing.py#L475)) and `run_what_changed` returns the per-decision rows ([qa_surfacing.py:369](labeling_app/assessment_pages/qa_surfacing.py#L369)). The per-frame Image-quality table was deliberately stripped from p05 to keep the summary calm (Akadir 2026-05-24 Layer-2c, [p05_assessment_view.py:444](labeling_app/assessment_pages/p05_assessment_view.py#L444)) — so the disclosure must be a **compact run-level summary, NOT the per-frame table coming back.**

**Shape — one quiet line + one conditional expander, placed directly under the headline-4 card stack** ([p05_assessment_view.py:762](labeling_app/assessment_pages/p05_assessment_view.py#L762)), so it sits with the numbers it qualifies:

1. **The headline disclosure line** — a single caption, rendered ONLY when frames were actually set aside:
   > *"Study number pooled from **N** of **M** sweep frames. **K** set aside — see why."*
   **On a fully-clean study (`n_set_aside == 0` and every measurement pooled at `valid`) the disclosure is QUIET — no line, no expander.** That is the cardinal default-quiet rule: a provenance caption that fires on 100% of clean studies is exactly the badge-fatigue risk §3.0 forbids. (Open design call, §6.4: an always-present calm provenance line is the argued trust-contract alternative — see below.)
2. **The expander ("see why"), `expanded=False`, rendered only when `run_what_changed` returns rows** — so a clean run has the line but no expander to open. Inside, render each `run_what_changed` row as the ratified `title` / `detail` / `evidence` triple, severity-coloured (`drop` -> error weight, `caveat` -> warning weight). `run_what_changed` already folds QMT+STAR into one row and already emits the `frame_rf_consistency` outlier row with sweep median + per-frame Z + clean median — so the render is a `for row in rows:` loop, no new logic.

**The argued alternative — an always-present provenance line.** There is a real, sourced case (Part 3.4) that a study-level number's provenance is a *trust contract*, not an alarm: unlike a per-card badge whose silence reads "fine," an always-present "pooled from M frames" caption is a calm receipt, and progressive-disclosure evidence says the mere *presence* of an inspectable trail builds calibrated trust even when the operator never opens it. That case is defensible and may be right for a clinical-grade trust artifact. **But it is the one place this layer would carve an exception to default-quiet, so it is the key open design call (§6.4), not a settled default** — the conservative MVP ships quiet-on-clean and earns the always-on line from a real reaction (you can't un-train an eye that has started skipping).

**Why NOT reuse `_qa_pool_badge` or `frames_with_changes` here.** The per-card badge answers "is *this card* degraded?" and stays ([p05_assessment_view.py:352](labeling_app/assessment_pages/p05_assessment_view.py#L352)). The disclosure answers the orthogonal study-level question "how many frames stand behind *the study*?" — complementary, both cheap. **Do not** reach for `frames_with_changes` for the *render* — that is the per-frame model whose p05 render was deliberately stripped; pulling it back in re-creates the calm-page regression. Use it only as a count source.

**Build:** add `_pooled_disclosure(run_data)` to `p05_assessment_view.py`, called once after the headline-4 `_measurement_card(..., layout="stack")` block. Pure-render. Add one accessor in `qa_surfacing` that returns the compact `(N, M, K)` tuple (keeps p05 thin, keeps the count logic unit-testable): **M** = analysis frames (`frames_with_changes.n_total`); **N** = the size of the *largest clean pool* across display measurements (the conservative, never-overstates choice); **K** = `n_set_aside`.

**Slice-B acceptance:** (1) a clean all-`valid` run shows the clean line and **no expander**; (2) a run with one `frame_rf_consistency` outlier shows "M-1 of M ... 1 set aside" and the expander carries the outlier row with its Z-score evidence; (3) a no-femur run shows the QMT/STAR provenance row in the expander and the headline line still reports the geometry pool honestly; (4) wording carries **no raw detector code and no "§"** (every string flows through `flag_prose`, already true of the `run_what_changed` rows). Tests: extend `tests/preflight/test_qa_surfacing.py` with the `(N, M, K)` accessor cases; one AppTest in `tests/ui/` asserting the line renders and the expander is conditional. Self-administer verification ladder Layers 1+2 before any Akadir click-through ([memory `feedback_verification_ladder`]).

### 1.3 Slice C — the p03 picker cue (already shipped; verify only)

The picker cue is **done** and correctly disciplined ([qa_surfacing.py:622-650](labeling_app/assessment_pages/qa_surfacing.py#L622)): calibrated-only, reserve-filtered, silent-on-clean, colour + glyph + prose, sits under the femur tier dot. With `dark_clip` staying pending (Slice A), the cue today fires only on the calibrated geometry set (`wrong_site`, `femur_*`, `edge_clip_vertical`) — the correct calibrated subset. **Action: none beyond a verification pass.** Confirm the sidebar "Image quality — N frame(s) with a trusted-detector concern" tally counts the same calibrated-only set the per-thumbnail cue uses (it reads `image_quality_aggregate_tier`, the calibrated-subset verdict — consistent). If Slice A later promotes `dark_clip` to Tier-A, the cue picks it up automatically with **zero code change** (the calibrated/pending split is data-driven via `pending_calibration`) — that design payoff is the reason to keep `dark_clip` pending rather than hard-coding it out.

> **Cross-lens conflict, resolved.** The Part-3 surfacing lens recommended a narrow allow-set so `dark_clip_high` surfaces at the picker at its 0.02 default as a pick-time *steer* (the harm model is inverted at the picker — no pooled number to corrupt). The Part-1 lens recommended keeping it silent everywhere until validated. **Resolution: keep it silent at the picker too for the MVP** (the conservative ship), and record the allow-set as a deliberate, recoverable design call in 1.8 / Part 6. Rationale: the brief's friction-budget discipline plus "ship the smallest robust thing, iterate from reactions" both favor *not* adding a new always-firing surface on an unvalidated cut before seeing a reaction — and the allow-set is a ~2-line change that can land the moment Akadir wants the steer. This is the one place the two lenses genuinely diverged; the conservative read wins for the demo, the steer stays one reaction away.

**Slice-C acceptance:** on the canonical cohort, the pre-scan paints a cue on the `wrong_site`/no-femur frames and stays silent on clean frames (no all-thumbnails-orange regression — the pre-v3 80% failure mode); `tests/ui/test_femur_scan.py` green.

### 1.4 Slice D — the p4x re-scope to the focus subset (non-blocking; re-point + add `wrong_site`)

Today p4x serves the 7 parked Round-1 detectors and resolves frames through the 956-frame transport `frame_loader` ([frame_loader.py:36](scripts/calibration/frame_loader.py#L36)). The ratified re-scope ([docs/rf_qa_next_phase_plan.md:129](docs/rf_qa_next_phase_plan.md#L129)) points it at the two live detectors with a real candidate tail. Three sub-steps:

- **D1 — slim sweep-frame `frame_loader` re-wire (the shared dependency D/E need).** The sweep-pool probe already proves the addressing: `(study_code) -> goruntuler_backup/<relative_frame_dir>/RawImagesForResearch_*/<frame_base>.jpg`, with `relative_frame_dir` from `configs/study_codes.csv`, and `_final` synthesized via `synthesize_final_u8` ([build_sweep_pool_score_table.py:20](scripts/calibration/build_sweep_pool_score_table.py#L20), note: probe found the live frames under `goruntuler_backup`, [build_sweep_pool_score_table.py:24](scripts/calibration/build_sweep_pool_score_table.py#L24)). **Build:** add a second resolver path to `frame_loader` — when `(study_code, frame_base)` misses the transport index, fall back to the study_codes.csv -> goruntuler_backup map and synthesize `_final` natively. Keep the transport path for back-compat. The PHI guardrail is preserved: the new map is still `(study_code, frame_base) -> path`, server-side, never displayed. This is a copy of code that already exists in the probe script.
- **D2 — re-point `candidate_selector._PARQUET` to the sweep-pool table.** Point it at `..._sweep_pool_5fps.parquet` (201,652 rows, 272 studies — the real candidate population). `_SCORE_KEY` already carries both `dark_clip_high` (`roi_dark_fraction`) and `wrong_site` (`rf_column_coverage`). **Do NOT promote the sweep-pool parquet to canonical** (a deliberate post-gate action, Part 2.6) — just re-point. Guard with a clear FileNotFoundError if the path is absent.
- **D3 — add `wrong_site` to the labelable set as a VALIDATION spec.** `wrong_site` is Tier-A-by-fiat so it is **not** in `CALIBRATION_SPEC` ([qa_calibration_spec.py:112](labeling_app/qa_calibration_spec.py#L112)) and **not** in p4x's `_THRESHOLD_KEY` ([p4x_calibration_capture.py:56](labeling_app/assessment_pages/p4x_calibration_capture.py#L56)). Add a `CalibrationSpec` entry: `operator_question` = *"Is there too little rectus femoris visible in this frame to assess it?"* (plain, no code), `selector_strategy="flag_fires"` (sample around 0.5), `score_reveal="hide_before"`, `contour_classes=("rf",)`, default layers `("raw", "segmentation_contour")`; add `"wrong_site": "wrong_site__rf_column_coverage_min"` to `_THRESHOLD_KEY`. Frame it as **validation** (the goal is the agree/disagree rate at the 0.5 boundary, especially separating true off-site from benign end-of-sweep) — the probe found the seg-collapse tail is 97% end-of-sweep (median sweep position 0.95), so the 0.5 cut firing there is ~correct; the labels confirm that read and would settle the deferred Q1 gate. `dark_clip_high` is already in `CALIBRATION_SPEC`.

**Slice-D is explicitly NON-BLOCKING for the ship.** p4x is built + tested ("~free to keep"); it generates optional validation labels but the MVP/demo number does not depend on a single label. Build it if D1 is being done anyway for Slice E; otherwise it can trail.

**Slice-D acceptance:** p4x detector picker lists `dark_clip_high` + `wrong_site`; selecting either serves sweep frames that resolve through the new loader and grade live on predicted masks; a label round-trips into `data/calibration_labels.jsonl` with the full metadata snapshot ([label_store.py:48](scripts/calibration/label_store.py#L48)); `tests/calibration/test_candidate_selector.py` + `tests/ui/test_p4x_calibration.py` green against the re-pointed table (the AppTest monkeypatches the heavy `_predicted_grade` seam, so no GPU in CI).

### 1.5 Slice E — the optional dark_clip / wrong_site validation pass (off the ship path, recoverable)

A small, targeted, optional labeling pass for the **TRUST/PUBLISH bar only — never a functional blocker** ([docs/rf_qa_next_phase_plan.md:127](docs/rf_qa_next_phase_plan.md#L127)). The natural use of Slice D once the loader is wired. Two questions: (1) is 0.02 the right `dark_clip_high` ROI-dark-fraction cut before it earns Tier-A (fit objective `Se >= 0.80 at Sp >= 0.85`)? On agreement, the registry flip promotes it with one line and it joins the calibrated set automatically. (2) does `wrong_site` firing below 0.5 separate true off-site from benign end-of-sweep? The expected outcome (from the probe's sweep-position covariate) is that the labels mostly *confirm* the cut, which also settles the deferred Q1 gate. **Scope discipline:** tens of frames per detector at the boundary band (the `flag_fires` borderline band is the right sampler), drawn study-clustered (Part 4.3), **not** the 50-positive Round-1 campaign target (parked with B). **Acceptance is a data deliverable, not a UI gate:** >=1 labeling session per detector lands clean labels; the agree/disagree rate at the boundary is computed (a tiny read script over the JSONL). No promotion without Akadir's word.

### 1.6 Slice F — RF-representative-frame scorer (wave-1; Akadir 2026-05-31)

*Pulled into wave-1 by Akadir's call — a deliberate, scoped deviation from pure thin-build, justified because this is the one addition that improves the MEASUREMENT (which frame we measure), not just the gating. It ships as an advisory SCORE; the strong "unbiased cross-study comparison" CLAIM is earned by validation (Part 4), not at ship.*

**What it is + why.** A per-frame "representative-ness" score that guides which frame the operator picks/designates as the study's repr, so RF thickness/area is measured at a consistent, central, fully-captured cross-section — making `RF thickness — median` / `peak` comparable across studies. It **supersedes `edge_clip_lateral`'s dead reserve slot**: it reuses that detector's lateral-clip primitive, but as a *positive selector*, not a reject gate (the gate fired ~98% because the 128L probe is shorter than RF — useless to reject on, useful to *rank* on).

**Where it plugs in (reuse, not greenfield).** The p03 pre-scan `scan_sweep_for_femur` already segments every sweep frame and caches the mask + final + QA grade ([femur_scan.py:29](labeling_app/assessment_pages/femur_scan.py#L29)); `_ai_suggested_repr` already ranks picked Tier-1 frames and surfaces `✦ AI suggests as repr` + the `selection_repr_status` sidebar ([p03_frame_picker.py:153](labeling_app/assessment_pages/p03_frame_picker.py#L153)). Slice F computes the score from the cached masks and swaps it into that ranking. **No new GPU pass, no new operator surface.**

**The algorithm** (per frame with a trustworthy RF mask; every term from the cached mask + the sweep distribution):
- **Hard gate `G`** — trustworthy mask: RF present, not `wrong_site`, calibrated aggregate tier not `invalid`. Else the frame is not a repr candidate (`R = 0`).
- **Completeness `C` ∈ [0,1]** — the RF cross-section is not cut off laterally: `_largest_component(RF)` touches neither lateral edge → 1; one side → 0.5; both → 0. (The `edge_clip_lateral` primitive, used positively; best-available within a study, since full lateral width is often unattainable with this probe.)
- **Centering `K` ∈ [0,1]** — RF horizontal centroid near image center: `K = 1 − |cx − W/2| / (W/2)`. A central measurement column then lands on the muscle belly, not its taper.
- **Fullness `M` ∈ [0,1]** — the frame's RF area's percentile rank *within its own sweep*: the fullest cross-sections (the muscle's most-complete capture) rank highest. Uses the sweep context the pre-scan already iterates.
- **Score** `R = G · (w_C·C + w_K·K + w_M·M)`, weights equal to start — a tunable Tier-B-style starting point (open calibration item, §1.10). Surface the top-`R` frame as the `✦` repr suggestion; via `selection_repr_status`, nudge toward a high-`R` unpicked frame. **Advisory — it suggests, never gates** (friction budget).

**Two clinical assumptions it rests on — CONFIRM (Anonaao / the acquisition protocol):**
- **A1** — the sweep is roughly *longitudinal*, so the *fullest* cross-section ≈ the anatomical *mid-belly* (the standard RF measurement location). If the geometry differs, `R` still picks the best-captured central frame, but the "standard anatomical location" reading needs confirmation. *(This is the one question worth answering early; it gates the comparability claim, not the score.)*
- **A2** — in-image centroid-centering ≈ the operator centered the probe transversely on the belly.

**Wave-1 vs earned-later.** SHIPS: the score + the advisory repr suggestion (a heuristic pick-aid). EARNED-LATER (validation, needs data — Part 4): the strong claim that `R`-selected frames make `RF median/peak` an *unbiased cross-study comparison tool* — i.e. that selecting by `R` measurably **reduces cross-study variance** of those numbers vs the operator's free pick. The score ships; the claim is earned.

**Slice-F acceptance:** on the canonical cohort the scan surfaces a `✦` suggestion that favors centered, full, complete-cross-section frames; advisory only (no auto-pick, no gate, no new always-on surface); unit tests on the `C`/`K`/`M` sub-scores + the `R` ranking from synthetic masks (new `tests/preflight/test_repr_scorer.py`); the existing picker AppTests stay green. Independent of Slice A+B — build in parallel.

### 1.7 Slice G — surface the full alive cohort in the app (p01 study-discovery re-wire; wave-1, foundational)

*Elevated to wave-1 (Akadir 2026-05-31): without it the app opens only ~5 studies, not the 272 alive cohort — so it is the prerequisite for reviewing or validating anything on the real data.*

**The problem.** `discover_studies()` walks `data/studies_root`, which symlinks only ~4 legacy centers (+ `akadir_self`) → the p01 picker surfaces **5 studies** (verified). The other 268 alive studies' raw sweeps live in **`goruntuler_backup`**, addressed via `configs/study_codes.csv` `relative_frame_dir` (the map the probe used; [build_sweep_pool_score_table.py:20](scripts/calibration/build_sweep_pool_score_table.py#L20)) — which study-discovery does not traverse.

**Build.** Add a second discovery source: resolve the alive cohort (akadir ∧ completed, 272) from `study_codes.csv` → `goruntuler_backup/<relative_frame_dir>/RawImagesForResearch_*`, and merge it into the p01 picker's study list (prefer a live `studies_root` entry when present; fall back to the backup map). This is the **study-discovery sibling** of Slice D1's `frame_loader` re-wire (both point at `goruntuler_backup`) — do them together. **PHI guardrail holds:** discovery yields `(study_code, demographics-safe meta)`; the patient-name folder path stays server-side, never surfaced (Part 2.2).

**Slice-G acceptance:** the p01 picker lists the 272 alive studies (study_code-addressed); opening one resolves its sweep + frames through the re-wired loader; the PHI invariant holds (no folder path in any UI/record); the study-discovery tests pass on the merged source.

### 1.8 Sequencing within Part 1 (minimal critical path)

The **trustworthy-number ship path is Slice A (verify) + Slice B (disclosure render)** — those two alone deliver option A's headline contract on the existing calibrated 5-gate set. Slice C is already shipped (verify only). **Slice G (surface the full 272-study cohort) is foundational — do it early so the app is usable on real data. Slice F (the representative-frame scorer) is wave-1, independent of A+B — build in parallel.** Slices D + E are the optional validation channel, gated behind the shared slim-loader re-wire (D1), explicitly non-blocking. **Suggested order: A -> B -> (ship/demo) -> D1 -> D2/D3 -> E.** Each slice is independently shippable and independently testable.

### 1.9 Friction-budget check (do-no-harm, applied to Part 1)

Every surface stays default-quiet: the p05 cue is silent on clean (unchanged); the p05 per-card badge is silent on clean (unchanged); the **new p05 pooled disclosure** is QUIET on a fully-clean study and renders only when frames were set aside; p4x is opt-in, off the assessment flow, zero ambient friction. No new always-on badge, no new colour on a clean study. (Whether to add an always-present provenance caption is the one flagged open design call — §1.2 / §3.4 / §6.4 — not shipped by default.)

### 1.10 Deferred from Part 1 (recoverable, named)

- `dark_clip_high` Tier-A promotion (the one-line registry flip) — after Slice E; ships PENDING for MVP/demo; the calibrated/pending machinery auto-picks it up.
- The `dark_clip` picker-cue allow-set (the Part-3 steer) — recorded as a ~2-line recoverable design call; lands the moment Akadir wants the pick-time dark-clip steer.
- The Slice-E labeling pass; promoting the sweep-pool parquet to canonical; the §6 compound-FPR arithmetic (dormant for A); subsystem B; the 5 parked acquisition detectors in p4x's `CALIBRATION_SPEC` (leave visible or hide — non-blocking); Anonaao's §6 gating sign-off; canonical-plan v0.3 reconciliation; the exact prose wording of the disclosure (Part 1 fixes the shape + contract; final wording iterates from Akadir's reaction).

---

## Part 2 — What we record + how we will use it (data architecture)

*DEEP. Data architecture is the thing that, if gotten wrong now, is expensive to fix later: a score paired against stale code, a label that carries a patient name, a schema that cannot accommodate subsystem B without a migration. The build implication is THIN — option A ships on the data layer that already exists. Almost everything below is already built and verified on disk; this part documents it, maps every field to a named future use, names the few forward-compatibility additions, and draws the bright line between what A needs (nothing new) and what B will need (a few additive fields, no migration).*

### 2.0 The governing principle: record nothing without a named use

**Every recorded field maps to a named future use** — validation (now), inter-rater agreement, subsystem-B revival, or audit/compliance. A field with no named consumer is removed, not kept "just in case." This is the data-architecture form of the friction-budget discipline: just as a surfaced flag must earn its place on the screen, a recorded field must earn its place in the schema. The §2.3 tables make the mapping explicit field by field, so a future reader can delete a field the moment its use is retired, and can tell when adding a field whether it has a use or is speculative bloat.

A second principle from the current ML-data-engineering literature governs the *shape*: **treat data as immutable, keep full history, preserve lineage** ([lakeFS, *Data Version Control Best Practices*](https://lakefs.io/data-version-control/dvc-best-practices/)). All three stores below are append-only or regenerated-never-mutated; none is edited in place. The reason is auditability — a clinical-adjacent measurement product must answer "what did the system record about this study on this date, under which code?" months later, and an in-place update destroys that answer.

### 2.1 The three stores, and why each is the format it is

| Store | On disk | Format | Write cadence | Job | Status |
|---|---|---|---|---|---|
| **Per-run QA grade** | inside each run record via `ImageQualityFeatures` | JSON (nested in the run JSON) | once per assessment run | the deployed verdict + full evidence for one study's frames | **live, shipping in A** |
| **Cohort score table** | `data/cohort_stats/qa_per_frame_detector_scores.parquet` | **Parquet** | regenerated as a batch (never appended) | the columnar substrate the fitter/selector/analysis read | **built** (10,516 rows) |
| **Calibration label ledger** | `data/calibration_labels.jsonl` | **JSONL, append-only, fcntl-locked** | one line per operator answer | the human ground-truth, immutable event log | **schema built; file ABSENT — zero labels** |

**Why Parquet for the score table.** It is read by column, in bulk, repeatedly: the selector filters to one detector and reads its `raw_score` column over the whole cohort ([candidate_selector.py:43](scripts/calibration/candidate_selector.py#L43)). Columnar storage with predicate push-down is exactly this access pattern, and Parquet gives ~10x compression over text ([conversiontools.io](https://conversiontools.io/convert/jsonl-to-parquet)) — material here: the 201,652-row probe substrate is 6.5 MB as Parquet (verified on disk). It is regenerated, never mutated — a code change triggers a full rebuild (§2.4), so it needs no append semantics and no row-level locking.

**Why append-only JSONL for the label ledger.** A human label is an *event* — "operator X answered Yes for (study, frame, detector) at time T under spec V." Events are immutable; you never edit one, you only append the next. JSONL is the natural event-log format and the canonical choice for log/streaming/ML-training records ([atlan, *Data Labeling for LLMs*](https://atlan.com/know/data-labeling-best-practices-llms/); [oneuptime, *Event Store Design*](https://oneuptime.com/blog/post/2026-01-30-event-store-design/view)). The ledger already takes an exclusive `fcntl.flock` per write ([label_store.py:48](scripts/calibration/label_store.py#L48)) so two browser tabs cannot interleave a write. The trade-off (JSONL is slow to query in bulk) does not bite, because the fitter joins labels against the *Parquet* score table, not by scanning the ledger.

**Why JSON-nested for the per-run grade.** `ImageQualityFeatures` ([feature_compute.py:179](scripts/preflight_helpers/feature_compute.py#L179)) joins to the measurement record on `frame_base` and lives *inside* the run JSON, versioning independently (`image_quality_contract_version`) so the QA schema can churn without touching the measurement schema. It is read one study at a time at p04/p05 — a document access pattern. It round-trips losslessly (`to_dict`/`from_dict`), the audit guarantee that a persisted grade reconstructs exactly.

This three-format split is the *lightweight-provenance* pattern from the current literature: keep the compact, frequently-read record small and put the bulky data in a supplementary columnar file ([yProv4ML / W3C-PROV, arXiv 2507.01075](https://arxiv.org/html/2507.01075v1)). We landed on it by access-pattern reasoning before finding the literature names it; the citation is corroboration, not the source of the decision.

### 2.2 The PHI-safe addressing spine (the non-negotiable constraint)

Every record in all three stores is addressed by **`(study_code, frame_base)` and nothing else**. `study_code` is the parsed-safe study identifier; `frame_base` is the millisecond-timestamp frame name (e.g. `20260127_151046404`). **The patient-name folder path exists in exactly one place — the server-side index inside `frame_loader` — and that index is used only to load pixels for display, never to emit an identifier into any store** ([frame_loader.py:48](scripts/calibration/frame_loader.py#L48)). The `Candidate` dataclass carries `study_code + frame_base` only; the `Label` dataclass persists `study_code + frame_base` only ([label_store.py:48](scripts/calibration/label_store.py#L48)).

This is what makes the data layer **forward-compatible with the user-facing data channel** (the B-revival source). When non-professional users start producing captures, their frames enter the *same* `(study_code, frame_base)` address space; the only new work is teaching `frame_loader`'s index about a second path root. No store schema changes, because no store ever knew the path in the first place. **The guardrail and the forward-compatibility are the same design choice.** The one operator identifier we keep, `operator_id_hash`, is a **salted hash, not a name** — enough to compute inter-rater agreement without storing who they are.

### 2.3 The schema, field by field, each mapped to a named use

#### 2.3.1 The cohort score table (Parquet, 13 columns — VERIFIED on disk)

10,516 rows · 956 frames · 319 studies · 11 detectors · `code_sig 1580ea06c18764d6` · `contract_version v0.2`.

| Column | What it is | Named use |
|---|---|---|
| `study_code` | PHI-safe study id | join key; inter-rater & per-study grouping; PHI invariant |
| `frame_base` | PHI-safe frame timestamp | join key to the label ledger and the run grade |
| `detector` | which of the 11 scored detectors | per-detector filter (selector, fitter, analysis) |
| `raw_score` | the detector's headline scalar this frame | the candidate-selection signal; the fit input (joined live) |
| `fires` | did it cross its current threshold | candidate banding; cohort firing-rate readout |
| `threshold_at_compute_time` | the cut in force when scored | audit snapshot of what "fires" meant at compute time |
| `pending_calibration` | was the detector un-calibrated then | the calibrated-vs-pending partition gating the picker cue (A) + the verdict (Finding B) |
| `metadata_json` | the **full** detector metadata dict, JSON-encoded | the "right scales/metrics" anchor — lets later analysis discover a *different* quantity discriminates better, without a GPU re-run |
| `modality` | `_final.png` (canonical) / `_final(synth)` (probe) | the **incomparability guard** — scores from different modalities must never be pooled |
| `mask_source` | `predicted` | same guard — annotator-mask vs predicted-mask scores are different populations |
| `contract_version` | the QA contract version | filter to the current contract; label-durability boundary (§2.4) |
| `code_sig` | sha256 of the detector source files | the anti-stale-pairing lock (§2.4) — the single most load-bearing provenance field |
| `computed_at_utc` | when scored | audit; tie-breaking between regenerations |

The `metadata_json` field snapshots **every intermediate quantity a detector computed** (e.g. for `dark_clip_high`: `roi_dark_fraction`, `pixel_rail`, `roi_fraction_max`, `n_roi_px`). The named use is concrete: if `dark_clip_high`'s ROI-fraction discriminates worse than, say, a connected-component count of dark regions, that alternative quantity is *already recorded* and can be re-analyzed without a GPU re-run ([atlan](https://atlan.com/know/data-labeling-best-practices-llms/) — "record the right metrics, not just the label").

#### 2.3.2 The calibration label ledger (JSONL — schema built, file ABSENT)

Schema = the `Label` dataclass ([label_store.py:48](scripts/calibration/label_store.py#L48)). This file does not exist; the loop has never closed. **Option A does not write to this file** — A ships on reuse + defaults — so this store is documented as the channel that already exists for the optional trust-bar validation and for B revival, not as an A deliverable.

| Field | What it is | Named use |
|---|---|---|
| `study_code`, `frame_base`, `detector` | PHI-safe address + which detector | join to the score table; PHI invariant |
| `label` | yes / no / unsure | the ground-truth target the fitter learns against |
| `score_at_label_time` | the detector's scalar when shown | **audit snapshot only** — the fitter joins against *current* scores (drift P1) |
| `detector_metadata_at_label_time` | the full metadata dict at label time | audit/compliance trail + the "different quantity discriminates" discovery path |
| `threshold_at_label_time` | the cut shown to the operator | audit; reconstructing what "fires" meant in the cell |
| `label_source` | `p4x` (blind-then-reveal) vs `cohort_pass` (natural-frequency) | the fitter weights these as **different evidence** and reports sens/spec per source |
| `score_revealed_at` | before / after / never | the anti-self-validation control — proves the label was blind |
| `post_reveal_confirmation` | kept / changed_to_* | the **anchoring-rate signal** — if operators flip to match the detector >10% after seeing the score, the reveal is anchoring them and that detector switches to `reveal=never` |
| `response_time_ms` | answer latency | fatigue / data-quality signal |
| `visual_aids_shown` | which layers were on | reproducibility of the labeling condition; per-detector aid efficacy |
| `contract_version`, `spec_version_at_label_time` | contract + spec version | the **label-durability boundary** — a code change keeps labels; a primitive change deep enough to reword the *question* filters old labels out (§2.4) |
| `operator_id_hash` | salted operator hash | **inter-rater agreement** (Cohen's κ) without storing identity |
| `notes` | optional free text | compliance capture; qualitative redesign signal |
| `relabel_target`, `relabel_of`, `relabel_due_utc` | test-retest tagging (+14d) | **within-operator test-retest κ** — the IRR workaround until a second assessor joins |
| `label_id`, `timestamp_utc` | uuid + write time | event identity; immutable ordering of the append-only log |

The `label_source` split is the field most easily under-valued and most load-bearing for honesty: a detector that scores 90% sensitivity on threshold-band-biased `p4x` labels but 60% on cohort-natural `cohort_pass` labels is **failing on the real distribution while passing the test you over-sampled** — and only recording the source per-label surfaces that gap.

#### 2.3.3 The per-run QA grade (`ImageQualityFeatures`, JSON-nested — live in A)

This is the store **A actually writes**, on every assessment run.

| Field | What it is | Named use |
|---|---|---|
| `frame_base` | join key | links the grade to the measurement record |
| `image_quality_validity_flags` | **every** flag raised, priority order | Transparency Rule 1 (never hide a flag); the p04 attribution panel |
| `image_quality_pending_calibration_flags` | the un-calibrated subset | the Finding-B dual semantic; the "+N pending" badge suffix; the surface that lets a pending detector *ever* get calibrated ([memory: unsurfaced-can't-calibrate]) |
| `image_quality_detector_metadata` | raw per-detector scores | the p04/picker rationale; the per-run calibration substrate |
| `image_quality_tier_per_measurement` | measurement -> valid/borderline/invalid | the **pooling decision** at p05 |
| `image_quality_aggregate_tier` | the deployed verdict (calibrated subset only) | the p05 per-card badge; the aggregate gate |
| `image_quality_contract_version` | the QA contract version | independent QA-schema versioning; re-grade eligibility |

`validity_flags` (everything) and `aggregate_tier` (calibrated-only verdict) are **both** recorded. That is not redundancy — it is the Finding-B honesty mechanism materialized into the schema, so a consumer reads the dual semantic directly ([feature_compute.py:492](scripts/preflight_helpers/feature_compute.py#L492)).

### 2.4 Provenance & versioning: how a score never pairs against stale code

The recommendation across the ML-provenance literature is uniform: **a score, a label, or a model claim must trace to the exact data + code + environment that produced it, or it is not reproducible** ([agility-at-scale, *Model Lineage*](https://agility-at-scale.com/ai/governance/model-lineage-and-reproducibility/); [ScienceDirect, *End-to-end provenance*](https://www.sciencedirect.com/science/article/pii/S0306437924001534)). Mapped onto the W3C-PROV Entity/Activity/Agent model ([arXiv 2507.01075](https://arxiv.org/html/2507.01075v1)): **Entity** (scored artifact) carries `code_sig`, `contract_version`, `modality`, `mask_source` — already on every row; **Activity** (scoring run) carries `model_bundle`, `device`, `fps` — recorded in the probe's `run_summary.json` but **not yet on the canonical score row** (the gap below); **Agent** (the labeler) carries `operator_id_hash`, `spec_version_at_label_time` — already on the `Label`.

**The system already has the load-bearing lock.** `code_sig` is a sha256 over `image_quality.py` + `qa_thresholds.py` — a content-address of the detector code, in the content-addressed-storage spirit lakeFS recommends. The drift policy: the fitter **refuses to run when the live code's `code_sig` differs from the score table's**, with a regen instruction. Three drift rules follow, all already specified:
1. **Labels join against *current* scores, never the snapshot.** `score_at_label_time` is an audit artifact; the fitter re-reads the live Parquet. A label is a durable fact about a *frame*, not a transient score.
2. **A code change keeps labels, regenerates scores, re-fits.** Mechanical detector changes are non-destructive to the human ground-truth.
3. **Only a primitive change deep enough to reword the operator question destroys labels** — filtered via `spec_version_at_label_time`.

This gives the threshold a **time-travel lineage** in the lakeFS `valid_from/valid_to` sense: `ThresholdEntry` records `calibrated_on`, `cohort_id`, `cohort_n_frames`, `sens_at_value`, `spec_at_value`, `calibration_report_path`, `contract_version` — so a deployed accuracy claim is auditable to the cohort and date that produced it. The `detector_status` ladder (`pending -> calibrated_provisional -> calibrated_published -> under_mechanism_review -> retired`) is the temporal dimension.

**The one real provenance gap, named (not silently carried).** The canonical score-row records `code_sig`/`contract_version`/`modality`/`mask_source` but **does not carry `model_bundle`** at the row level — the segmenter bundle name lives only in `run_summary.json` (probe: `0319_voi_hist_clahe...fold4`). Because the **segmenter is the de-facto arbiter** (every detector reads its predicted mask), a segmenter swap changes every score as surely as a detector-code change does — but `code_sig` would not move, so the stale-pairing lock would not catch it. **Recommendation (forward-compatibility, NOT an A blocker): add `model_sig` (a hash or pinned bundle name) to the score-row schema and fold it into the fitter's refuse-to-run check.** One-column additive change; deferred (§2.6) because A ships on reuse + defaults and does not fit thresholds, so no stale pairing can occur in A. It must land **before** any fit consumes labels (B, or the optional trust-bar pass).

A second, smaller drift recorded honestly: the canonical parquet (`code_sig 1580ea06`, `_final.png`) and the probe substrate (`code_sig 850b6e7c`, `_final(synth)`) carry **different sigs and different modality strings** — verified on disk. This is correct and expected (the canonical predates the wrong_site cleanup; the probe synthesizes `_final`), and the `code_sig`/`modality` fields are doing exactly their job by making the difference *visible and machine-checkable* rather than letting the two pools be silently concatenated.

### 2.5 The ingestion path: how one capture flows record -> validation (now) -> B (later)

**Path 1 — the deployed grade (live in A).** A run reaches the pipeline -> `compute_image_quality_features` grades each frame on the **predicted** mask -> the `ImageQualityFeatures` record persists into the run JSON -> `aggregate_features` pools per measurement using `image_quality_tier_per_measurement` ([aggregation.py:181](scripts/preflight_helpers/aggregation.py#L181)) and runs the `frame_rf_consistency` gate -> p05 reads the pool tiers + `run_what_changed` for the disclosure (Part 3's surface). **This path requires no labels and no new data. It is what A ships.**

**Path 2 — the optional trust-bar validation (channel exists, not an A blocker).** The operator enters p4x from p04 -> the selector reads the **score table** and bands frames for the target detector -> `frame_loader` resolves pixels from the PHI-safe address -> the operator answers blind, then sees the reveal -> a `Label` appends to the ledger. Needs the slim `frame_loader`/`candidate_selector` sweep-frame re-wire (Slice D1). **The only data-layer work A's open follow-ups imply, and explicitly optional/later.**

**Path 3 — the fit consumer (parked with B).** When B is revived: the fitter joins the ledger against the current score table (re-reading scores, drift P1), reports sens/spec per `label_source` and per operating point, writes a `ThresholdEntry` with full lineage, advances `detector_status`. **Blocked on the acquisition-of-positives dataset, not on the data layer** — the schema is ready; the cohort lacks the positives to fit against. B revival is a *volume of positives* problem, not a *schema* problem — the payoff of recording the full schema now even though A uses a thin slice.

### 2.6 Near-term build implication (THIN) and what is deferred

**For A, the data-layer build is: nothing new.** A ships on three already-built stores via Path 1, which writes the per-run record that already exists. No label collected, no score fitted, no ledger written. The data layer's role in A is to *be correct and forward-compatible*, which it is.

**Deferred, named with owner + trigger so it is recoverable:**
- **`model_sig` on the score row** (§2.4) — the one real provenance gap. Additive one-column change; *owner: implementer; trigger: first fit run (B or trust-bar pass), NOT before A*.
- **The slim `frame_loader`/`candidate_selector` sweep-frame re-wire** (Slice D1) — *owner: implementer; trigger: Akadir greenlights the trust-bar pass*.
- **The round-state ledger (`calibration_rounds.jsonl`) + the fitter/promoter** — built only when B is revived; the compound-FPR backstop must land first and be re-derived correctly (§6 arithmetic wrong by ~500x). *Owner: implementer + statistician; trigger: B revival*.
- **Promote the probe substrate to canonical + re-point `candidate_selector._PARQUET`** — a post-gate action. *Owner: implementer; trigger: B revival or the trust-bar pass*.
- **Reconcile the stale `code_sig`/`model_bundle` on the canonical parquet** — regenerates with any substrate rebuild; harmless for A. *Owner: implementer; trigger: next substrate rebuild*.

None of these blocks A.

---

## Part 3 — Surfacing + UX (how we show it)

*DEEP. This layer designs the operator-facing surface: what the operator sees, when, in what words, and — just as load-bearing — what they deliberately do NOT see. It builds on the existing `qa_surfacing.py` spine (pure, unit-tested, no Streamlit import). Every recommendation ties to one of the two operator tasks (Part 0.1). A surface that serves neither is friction, and the default for friction is: do not build it.*

### 3.0 The friction budget is a safety constraint, not a style preference

The clinical-monitoring literature is blunt. Across studies, **68-99% of clinical alarms are false or non-actionable** ([NCBI, *Making Healthcare Safer III*](https://www.ncbi.nlm.nih.gov/books/NBK555522/)), and the consequence is mechanical: high false-positive rates (80-99%) *train practitioners to ignore alarms*. Monitor-alarm positive predictive value was measured at **27%** (sensitivity 97%, specificity 58%) — nearly three of four alarms that fire are noise, and clinicians learn that. In CDS, **alert acceptance rates run below 10%**, and that low acceptance is itself what *desensitizes clinicians to alerts* ([JAMIA](https://academic.oup.com/jamia/article/31/4/968/7610153)). The single highest-impact intervention in that 2.99-million-firing study was *suppression*: removing one over-firing notification eliminated **138,592 unnecessary firings (34.8% of all firings)** and raised acceptance of what remained by 52%.

The translation, restated as the cardinal rule of this layer: **a QA flag the operator learns to ignore is strictly worse than no QA flag at all** — same screen space, same glance, same interrupt, and it additionally trains the operator to skip the *next* flag. This is why the existing code is right to return `None` on clean from `qa_badge`, `picker_qa_cue`, and `cohort_caveat`, and why the 2026-05-22 review correctly killed the green-on-clean badges (a green check on every clean card is a 100%-firing non-actionable alarm). Silence *is* the "everything is fine" signal. The study-level frame makes this affordable: a false fire drops one frame from a robust pool, so we optimize against badge-fatigue, not against the occasional missed frame.

### The four surfacing rules (the operator-facing contract)

1. **Default quiet.** Clean -> render nothing. No green badge, no "0 issues." The absence of a flag is the message.
2. **Quiet-or-loud, never lukewarm.** When a surface fires, it must be impossible to skip — a boxed `st.warning`/`st.error` the eye snags on, not grey inline text. The 2026-05-22 finding ("the green-text badges were not noticeable") is the failure mode in both directions: a signal that is neither silent nor loud adds noise without salience. There is no middle setting.
3. **Never hide a frame.** Quiet means quiet about *clean* frames and *by default about detail*; it never means silently dropping a frame without a trail. Every set-aside frame is recoverable on demand (3.3). Suppression of the *alert* is not suppression of the *fact*.
4. **Trust the number, show what was set aside.** p05 leads with the pooled measurement as a confident value, and offers — one level down — the honest "pooled from X of Y; Z set aside (reasons)."

### 3.1 Plain language is a hard requirement; the translation table is the enforcement point

No detector code, no threshold string, no section reference ever reaches an operator surface. This is already enforced structurally: every operator-facing string routes through `flag_prose(code)` ([qa_surfacing.py:100](labeling_app/assessment_pages/qa_surfacing.py#L100)), the 22-code table that maps `edge_clip_lateral` -> "the muscle runs off the side of the image." The 2026-05-22 review flagged raw codes leaking through as *"a category failure, not isolated typos."* The XAI-in-CDS scoping review reaches the same conclusion: explanations must match *"the application domain's vocabulary and the users' domain knowledge"* ([PMC11758527](https://pmc.ncbi.nlm.nih.gov/articles/PMC11758527/)). Two sub-disciplines, already partly coded:
- **No double-surfacing.** When two surfaces on one thumbnail state the same fact, silence one. The picker already does this via `suppress_flags=("femur_out_of_frame",)` when the tier dot says "no femur" ([qa_surfacing.py:642](labeling_app/assessment_pages/qa_surfacing.py#L642)). Generalize: **one fact, one surface, per screen region.**
- **Per-measurement attribution, never the union.** `per_measurement_attribution` ([qa_surfacing.py:225](labeling_app/assessment_pages/qa_surfacing.py#L225)) emits one row per measurement naming only the flag(s) that drove *its* tier. Misattribution is a trust leak: an operator who catches the app blaming the wrong thing once discounts every subsequent explanation.

### 3.2 The p03 picker cue: calibrated-only, and honest about how thin that currently is

**Operator task:** pick a representative frame. The cue keeps the operator from picking a frame that will not measure well — a *steering* cue, not a verdict. The calibrated-only constraint is correct and coded: `picker_qa_cue` filters out pending + reserve-state flags and stays silent on clean ([qa_surfacing.py:622-650](labeling_app/assessment_pages/qa_surfacing.py#L622)).

**The tension, named honestly.** `dark_clip_high__roi_fraction_max` is Tier-B with a 0.02 starting point ([qa_thresholds.py:153](labeling_app/qa_thresholds.py#L153)), which stamps `pending_calibration=True`. So **`dark_clip_high` is in the pending set today, and the picker cue is silent on it** — the cue effectively carries only `wrong_site` (too little RF) plus the femur/edge geometry. The cue is correct but **thin**, and the thinness is a direct consequence of the 0.02 cut still wearing its "pending" stamp.

**Resolution (see the Part-1.3 cross-lens note).** Two options were on the table: (a) a narrow allow-set so `dark_clip` surfaces at the picker at its 0.02 default *as a steer* (the harm model is inverted at the picker — surfacing a borderline dark-clip steer just nudges toward a different also-fine frame; there is no pooled number to corrupt, and dark_clip fires on ~18% not 80%, so it would not paint the grid); or (b) keep it silent until validated. **For the MVP the plan ships (b) — silent — and records (a) as a deliberate, recoverable ~2-line design call** (one allow-set constant + a one-line change to the `calibrated`-filter). The conservative read wins for the demo per "ship the smallest robust thing, iterate from reactions"; the steer is one reaction away. **Do NOT generalize this into a per-detector eligibility framework — that is over-engineering until more detectors calibrate.**

The picker cue's *form* is already right and should not grow: a single coloured one-liner under the thumbnail (`:red[✗ prose]` / `:orange[⚠ prose]`), colour AND glyph AND words, silent on clean. **Do not** add per-detector breakdowns, scores, or expanders to a 130-px thumbnail; the sidebar tally already carries the count. The notification-design literature is explicit that glanceable peripheral cues *"signify something newly available to view but are completely passive and do not block user workflow"* ([Boundev](https://www.boundev.ai/blog/notification-design-ux-guide)) — the picker cue must stay one.

### 3.3 The p05 disclosure: "trust the number; here's what was set aside"

**Operator task:** decide whether to trust the study number. This is the surface the settled direction calls *open / not-yet-designed*, and the centerpiece of this layer. The data already exists; only the render is undesigned.

It must let the operator answer three questions: (1) **What is the number?** — the headline cards (lead with it, full confidence). (2) **Is it on solid ground?** — the per-card `_qa_pool_badge`, silent on clean, boxed warning/error otherwise ([p05_assessment_view.py:352](labeling_app/assessment_pages/p05_assessment_view.py#L352)). (3) **What exactly was set aside, and why?** — the new disclosure, **on demand, one level down**. It must **not** become the per-frame Image-quality panel deliberately stripped from p05 (Akadir 2026-05-24 Layer-2c — "the summary page stays calm"; [p05_assessment_view.py:444](labeling_app/assessment_pages/p05_assessment_view.py#L444)).

**Recommended shape — one collapsed line that expands to provenance sentences** (the build spec is Part 1.2). This is the progressive-disclosure pattern: people *"rarely read all details, [but] glance over available data to confirm nothing is hidden, giving them faith they could check details if needed"* ([UXmatters](https://www.uxmatters.com/mt/archives/2020/05/designing-for-progressive-disclosure.php)). The mere *presence* of an inspectable trail is most of the trust work; the operator usually will not open it, and that is the success case.

Collapsed (rendered only when something was set aside): *"Pooled from 7 of 9 frames · 2 set aside. (expand)"*. Expanded — the model is **already computed** by `run_what_changed` ([qa_surfacing.py:369](labeling_app/assessment_pages/qa_surfacing.py#L369)), each row the ratified explainability triple **X done, because Y, evidence Z**:
> - **RF thickness — 1 outlier frame set aside.** One frame's RF-thickness value sat far from the rest of the sweep (usually a mis-segmentation or off-axis slice). *Sweep median 12.4 mm; set aside 151033.404 (Z = 4.1); clean median 12.1 mm.*
> - **QMT and STAR — measured without the thigh-bone anchor.** No frame had a usable thigh bone, so QMT (and STAR, which uses it) is an estimate, ~half a proper measurement, and not comparable to the reference cohort. *3 frames anchored, 0 fallback, 2 no thigh bone.*

Three properties: **it reuses the existing data model verbatim** (`run_what_changed` produces NO row when nothing changed, folds QMT+STAR into one row, carries `title`/`detail`/`evidence`/`severity` per row — the build is a `for row in rows:` loop; do NOT reach for `frames_with_changes` for the render); **severity drives form** (`drop` -> error weight, `caveat` -> warning weight; colour never alone); **it degrades to silence** (no rows -> the whole disclosure line does not render).

**The "X of Y" counter is the trust anchor — get its denominator honest.** Y is the analysis-included frame count (`use_for_analysis AND usable`, the set `_analysis_frames` computes, [qa_surfacing.py:283](labeling_app/assessment_pages/qa_surfacing.py#L283)); X is how many actually fed the pooled number. Because measurements pool differently, the single run-level "X of Y" should report the **most-conservative (largest-clean-pool / min-pool) value** across the displayed measurements, with per-measurement truth in the expander. This is the model-card "nutrition label" instinct at run scope — disclose *"the breakdown of total records"* and what was excluded ([Michelle Yi, *Model Cards*](https://www.michelleyi.ai/blog/model-cards-a-nutrition-label-for-predictive-models)). Exact convention (min-pool vs headline-measurement-anchored) is a one-line open call (Part 6); I lean min-pool because it never overstates.

**Keep the three existing p05 QA surfaces; the disclosure joins them.** The per-card `_qa_pool_badge`, the `_qmt_provenance_note` (proxy/no-femur QMT shown without a percentile it cannot stand behind, [p05_assessment_view.py:232](labeling_app/assessment_pages/p05_assessment_view.py#L232)), and the `cohort_caveat` line ([p05_assessment_view.py:783](labeling_app/assessment_pages/p05_assessment_view.py#L783)) all stay. The new disclosure is the **fourth and final** p05 QA surface. **Four is the ceiling. Resist a fifth.**

### 3.4 Trust calibration: the number is trustworthy *because* its provenance is inspectable

This resolves an apparent paradox — *doesn't qualifying the number undermine trust in it?* The evidence says the opposite, when done right. *"Surfacing uncertainty is in tension with the design-for-trust instinct... However, the absence of uncertainty signals can leave users making reliance decisions without having full access to all the information"* ([Designative, *Trust Calibration in Agentic AI*](https://www.designative.info/2026/05/21/trust-calibration-in-agentic-ai-designing-for-appropriate-reliance-not-blind-trust/)). The goal is not maximal trust; it is **calibrated** trust — *"helping people understand when to rely, when to question, and when to intervene."* And critically: *"explanations must be task-aligned and accompanied by uncertainty and intended-use boundaries to enable informed uptake and avoid overtrust"* (corroborated by [PMC11758527](https://pmc.ncbi.nlm.nih.gov/articles/PMC11758527/): *"when the system is uncertain or out-of-distribution, explanations should convey that uncertainty rather than overconfident pronouncements"*).

Mapped onto our surfaces: **the pooled number is the confident output** — lead with it, no hedging chrome on a clean pool (over-qualifying a clean number *manufactures* uncertainty and teaches under-reliance on good numbers); **the disclosure is the intended-use boundary** — "this number was built from 7 of 9 frames; QMT here is a proxy, not cohort-comparable" is exactly the boundary the literature says must accompany the output to prevent overtrust. The `_qmt_provenance_note` suppressing the cohort percentile on a proxy QMT is a textbook intended-use-boundary surface.

**A judgment I own in first person** (distinct from the cited facts): *I think the segmenter-as-arbiter constraint should eventually surface as an honest intended-use boundary somewhere in this app*, because every QA verdict and every measurement is downstream of a predicted mask, and the operator currently has no surface that says "all of this rests on the AI's segmentation." I am **not** recommending a global "this rests on segmentation" banner now — it risks becoming exactly the always-on non-actionable chrome 3.0 warns against. I flag it as the most important *latent* trust-calibration gap, to be revisited when the app moves toward non-expert users (Part 5), where the operator can no longer be assumed to know an ultrasound assessment app is segmentation-bound.

### 3.5 Where MORE surfacing would HARM (the explicit do-no-harm map)

| Candidate surface | Verdict | Why it harms |
|---|---|---|
| Green "✓ valid" badge on clean cards/frames | **Do not surface** (already removed) | 100%-firing non-actionable signal; trains the eye to skip the badge — the 27%-PPV alarm-fatigue mechanism, self-inflicted |
| Pending (un-calibrated) flags on p03/p05 | **Do not surface** (already excluded) | `wrong_site` pre-v3 fired on ~80%; pending detectors paint the grid and cards. They belong only in p04's grey "still tuning" sub-section + the p4x flow |
| `edge_clip_lateral` (reserve) anywhere it implies a measurement is degraded | **Do not surface as a verdict driver** (already filtered) | It taints nothing (empty `_GATING_TABLE` row); surfacing it is a fact with no consequence — pure noise, actively misleading |
| Per-frame Image-quality table on p05 | **Do not restore** (deliberately stripped) | The summary page is the boss/trust surface; per-frame churn re-creates the calm-page regression. Detail lives on p04, one click away |
| Detector scores / thresholds / Z-values as primary text | **Do not surface as primary** | "rf_column_coverage = 0.43 < 0.5" is engineer-facing. Numbers live in the *evidence* line of an expanded row (e.g. "Z = 4.1"), supporting prose, never leading |
| Interruptive modal / blocking dialog on a QA flag | **Never** | The CDS literature is unambiguous: interruptive alerts *"should be used only when other less intrusive options have been thoroughly considered"* ([JAMIA](https://academic.oup.com/jamia/article/31/4/968/7610153)); passive CDS routinely outperforms them ([PMC10830237](https://pmc.ncbi.nlm.nih.gov/articles/PMC10830237/)). The picker already embodies this — `selection_repr_status` *warns, does not block* |
| Red as the only signal for "concern" | **Never** (already paired) | WCAG 1.4.1 Level A: colour cannot be the sole carrier ([W3C 1.4.1](https://www.w3.org/WAI/WCAG21/Understanding/use-of-color.html)). Red carries *"troubling physiologic associations... the color of blood"* ([saif71](https://blog.saif71.com/red-notification-badge-ux/)) — fine for genuine invalidity, wrong for a mild caveat (use orange + ⚠) |

The unifying test: **a surface earns its place only if the operator would do something different because of it, on the screen where it appears.** A green check, a pending flag, a reserve-state fact, a raw score — change nothing about either operator decision, so they cost without paying.

### 3.6 Accessibility (already strong; hold the line and extend the new surface)

The foundation is genuinely good and ahead of most clinical tools: **colour is never alone** — `_TIER_STYLE` is a `(colour, glyph, word)` triple for every tier ([qa_surfacing.py:35](labeling_app/assessment_pages/qa_surfacing.py#L35)), and `qa_badge` renders all three (`✓ valid` / `⚠ borderline` / `✗ invalid` / `○ not graded`), satisfying WCAG 2.2 SC 1.4.1 ([TheWCAG](https://www.thewcag.com/criteria/1.4.1)); **screen-reader parity** via `aria_label` ([qa_surfacing.py:159](labeling_app/assessment_pages/qa_surfacing.py#L159)). The new disclosure must do the same: each row's severity shows as glyph + word + prose; the collapsed line's accessible text reads "pooled from 7 of 9 frames, 2 set aside, expand for detail." **Build note:** prefer the `qa_surfacing` text-glyph + word form (✓ ⚠ ✗ ○ inside the prose string) over an emoji icon argument, for screen-reader and cross-platform consistency (verify at build time that the `_qa_pool_badge` emoji glyphs render consistently). The disclosure inherits all of this for free by routing through `flag_prose` and the existing tier styles — only the discipline not to introduce a colour-only or glyph-only state is needed.

### 3.7 Thin build implications (Part-1 near-term)

(1) **p05 disclosure render** — Part 1.2, the one genuinely new surface, render over data that exists. (2) **Picker cue** — verify-only for the MVP (the dark_clip allow-set is recorded as recoverable, not shipped). (3) **Hold all four existing p05 QA surfaces and the picker cue form as-is** — no green-on-clean, no per-frame table on p05, no scores-as-primary. **Most of the "build" is *not building* things.**

### 3.8 Deferred (recoverable, not dropped)

The "X of Y" denominator convention; the exact collapsed-line copy + `st.expander`-vs-custom choice (wording lands from Akadir's reaction); the global "this rests on segmentation" intended-use boundary (the most important latent trust gap, revisit on the move to non-expert users); subsystem-B per-frame coach surfacing (p04, reusing `frame_qa_summary` + `flag_prose` + `_GATING_TABLE`); the non-expert user-facing-app surfacing regime (a harder friction/trust budget); a general per-detector picker-cue eligibility framework (explicitly NOT now); the dark_clip picker allow-set.

---

## Part 4 — Algorithmic rigor (calibration + validation)

*DEEP. The statistical methodology that makes the small validated layer DEFENSIBLE, not just shipped. Split: a one-paragraph near-term posture (defaults ship; nothing here blocks MVP/demo) then the deep methodology for the trust/publish bar. Every number was reproduced against the on-disk parquets this session.*

### 4.0 Near-term posture (THIN — read this and move on if building the MVP)

Option A ships on **reuse + defaults**. None of Part 4 is a ship blocker. `dark_clip_high` fires at `roi_fraction_max = 0.02` (18.0% canonical, 19.8% probe); `wrong_site` fires at the Tier-A-by-fiat `rf_column_coverage < 0.5`; femur/edge geometry and `frame_rf_consistency` need no data (reuse-validated / published statistical standard). **The validation work in 4.2-4.6 is the bar you cross before you publish a sensitivity/specificity number — not before you demo.** The one thing the build must get right *now* is structural, not statistical: **do not surface a composite per-frame verdict that depends on the broken §6 concurrence arithmetic** (4.4). At study-level pooling, you don't need it.

### 4.1 What "calibration" means here, and what it does not

Two outcomes are conflated under "calibrate"; keeping them separate is the whole discipline ([docs/rf_qa_detector_calibration_spec.md](docs/rf_qa_detector_calibration_spec.md)): **(1) threshold fit** — choosing the operating point on the score (a statistics problem with a clean answer once you have labels); **(2) mechanism validation** — confirming the detector's *primitive* measures the thing the operator sees (if the score says "problem" and the operator says "no problem" 40% of the time, no threshold fixes that — the primitive is wrong; this is upstream of (1)). For option A only **two** detectors have an open operating point: `dark_clip_high` (the ROI-fraction cut; the pixel rail <=5 is a fixed Tier-A physics constant) and `wrong_site` (the 0.5 cut). The wrong_site question is really a *mechanism* question (is 0.5 separating true off-site from benign end-of-sweep?), not a fit-a-number question.

### 4.2 Operating-point estimation: the method for the two open cuts

When labels exist (the optional trust-bar pass), fit each cut as follows. **Report two operating points, always.** For each candidate threshold compute Se = TP/(TP+FN) and Sp = TN/(TN+FP) against the operator labels, then report:
- the **Youden-J optimum** — the threshold maximizing `J = Se + Sp - 1`. J is **prevalence-independent**, so a cut chosen on the labeled (deliberately positive-enriched) p4x sample transports to the cohort's natural base rate ([ScienceDirect, *Youden index*](https://www.sciencedirect.com/topics/medicine-and-dentistry/youden-index); [Schisterman et al., *J Clin Epidemiol*](https://www.jclinepi.com/article/S0895-4356(07)00380-0/fulltext)).
- the **sensitivity-targeted point** — the threshold meeting the per-detector Se floor (Se >= 80% at Sp >= 85% for the intensity-statistic class). A QA filter that **drops** suspect frames is asymmetric: a missed bad frame (false negative) silently poisons the pool, whereas a false alarm merely drops one good frame from a robust pool. So the **sensitivity-targeted point, not the J-optimum, is usually the one to deploy** — J is the honesty check, not necessarily the shipped cut.

**Flag divergence.** If the J-optimum and the sensitivity-targeted point disagree by more than ~5% in Se or Sp, surface it — large divergence means heavy overlap between the positive and negative score distributions, a mechanism warning. If the sensitivity floor is **unreachable at any threshold**, the fitter must refuse to emit a cut and route the detector to mechanism review rather than ship a number that cannot exist.

**The two open cuts, specifically.** `dark_clip_high` — a 1-D fraction score with a real dynamic range (canonical-pool quantiles p50 0.0012, p90 0.049, p95 0.096; probe-pool p95 0.117, p99 0.283); the 0.02 starting point sits ~p80-p90 — a clean Youden-J + sensitivity-target fit once labeled. `wrong_site` — the score is `rf_column_coverage`, **heavily mass-piled near 1.0** (probe-pool p50 0.875, mean 0.808, σ 0.214), so the ROC is dominated by the lower tail; the probe already answers most of it (4.7) — the seg-collapse tail is 97% end-of-sweep ramp-off (median sweep position 0.95), so wrong_site firing there is approximately **correct**. The optional pass confirms that boundary, it does not discover a number.

### 4.3 Study-clustering-aware sampling and inference (the load-bearing correction)

**The independent unit is the study, not the frame.** At 5 fps a sweep yields 20-176 frames (probe-pool: median 65, mean 67.4 frames/study across 272 studies). Frames within a sweep are massively correlated. Treating 18,332 frames as 18,332 independent observations is the single most dangerous statistical error available here, and the data shows why: `dark_clip_high` fires on 3,629 frames but only **108 of 272 studies**, with within-study fire fraction median 0.487, p90 = 1.000 (when a study dark-clips, roughly half or all of its frames do); `wrong_site` fires on 1,963 frames across **141 of 272 studies**, fire fraction median 0.185. The fires are *clumped by study*, not sprinkled across frames.

**Consequence — the effective sample size is far below the frame count.** The design effect for clustered binary data is `Deff = 1 + ρ·(m̄ - 1)`, effective N = `N_frames / Deff` ([CRAN PracTools, *Design Effects*](https://cran.r-project.org/web/packages/PracTools/vignettes/Design-effects.html)). With m̄ ≈ 67 and the high within-study clumping, even a modest ρ drives Deff into the tens, so **18,332 frames carry information closer to the ~272-study scale**. The operating rule: **count studies, not frames, when you state how much evidence you have.** The spec's "≥50 independent positives ≈ studies at 5 fps" heuristic is exactly this instinct, correctly applied — keep it.

**Sampling rule for the optional labeling pass.** Draw the labeling sample **by study, then by frame within study** — never a flat random draw over the frame pool (which over-weights long sweeps and the clumped failure studies). Target a fixed small number of frames per sampled study (2-4) across **enough distinct studies** that the positive count reaches the target at the *study* level. Stratify across the score range (enrich near the threshold) so the ROC's decision region is populated — but record `label_source` so the threshold-enriched p4x labels are reported separately from natural-frequency cohort-pass labels.

**Inference rule — the clustered percentile bootstrap.** To put a CI on Se, Sp, the cut, or AUC, **resample whole studies with replacement** (take all of a resampled study's labeled frames together), recompute the statistic, take the 2.5/97.5 percentiles. This is the standard for patient-clustered diagnostic data: Rutter (2000) shows the cluster bootstrap matches or beats analytic intervals for Se/Sp/AUC coverage when patients contribute multiple correlated results, the advantage growing with observations-per-patient ([Rutter 2000, PubMed 10845400](https://pubmed.ncbi.nlm.nih.gov/10845400/); [Frontiers Vet Sci 2023](https://www.frontiersin.org/journals/veterinary-science/articles/10.3389/fvets.2023.1274786/full)). A frame-level bootstrap would report intervals several-fold too narrow and manufacture false confidence.

### 4.4 The compound-false-positive question, done correctly

The spec's §6 arithmetic is **wrong**, and the error is instructive. It claimed *"at 80% per-detector specificity, 14 independent Tier-0 detectors yield 0.80^14 ≈ 4% probability that a clean frame trips zero false flags ... the two-detector concurrence collapses this to (1 - 0.96)² = 0.0016."* The first sentence is right (`0.80^14 = 0.044`, so **95.6% of clean frames trip ≥1 spurious flag**). The second breaks: `(1 - 0.96)² = 0.0016` is **not** `P(≥2 of 14 fire falsely)` — it silently collapsed n from 14 to 2 *and* swapped the per-detector FPR from 0.20 to 0.04 (the `0.04` is the *all-14* zero-trip probability, mis-used as a *single-detector* rate). **Reproduced this session** (`from math import comb`):

| Quantity | Spec claimed | Correct (verified) |
|---|---|---|
| P(≥1 of 14 fires falsely), q=0.20 | 0.956 (right) | **0.9560** |
| P(≥2 of 14 fire falsely), q=0.20 | "0.0016" | **0.8021** |
| P(exactly 1 of 14), q=0.20 | — | 0.1539 |
| (the world where 0.0016 is right: n=2, q=0.04) | — | 0.0016 |

So requiring **two** detectors to concur, *if you had 14 independent always-on detectors at 80% specificity*, would still falsely downgrade **~80%** of clean frames. The two-detector rule only delivers the advertised `0.0016` when there are genuinely two detectors (n=2, q=0.04) — a different world. **This ~80% figure must replace the spec's `0.0016` wherever a composite verdict is contemplated.**

**How load-bearing is concurrence for option A? Barely, quantified three ways:**
1. **The denominator is not 14.** Only detectors with a finite, firing cut that *taints a measurement* can produce a false downgrade: `wrong_site`, `dark_clip_high`, `femur_out_of_frame`, `edge_clip_vertical` — and `edge_clip_lateral` is reserve (taints nothing), `femur_off_central_zone` fires on 0.1%, `edge_clip_vertical` on 0.0%. The effective n of *meaningfully firing* detectors is **~2-3, not 14**. With n=2 at q=0.20, `P(≥2) = 0.04`; n=5, `P(≥2) = 0.26` (both verified). The badge-fatigue threat the concurrence rule was designed to suppress is **already much smaller** than the §6 motivation assumes (it over-counted the firing set by ~5x).
2. **The harm per false fire is one frame, not the study.** Pooling prefers `valid`, falls back to `borderline`, excludes only `invalid` ([aggregation.py:188](scripts/preflight_helpers/aggregation.py#L188)). A false `invalid` on one frame of a ~65-frame sweep drops that frame; the pooled median over the remaining ~64 is essentially unchanged. The cost is **bounded and tiny** at study-level.
3. **A surfaces no composite per-frame gate.** It surfaces (a) the p05 pooled measurement (per-measurement pooling, low-harm) and (b) the p03 calibrated-only informational cue. **Concurrence becomes load-bearing only if/when a composite per-frame verdict is surfaced — i.e. subsystem B.** Re-derive it then, correctly, with a statistician, using the *actual* count of always-on firing detectors and their *measured* (not assumed-0.20) FPRs against operator labels — the independence assumption baked into `q^n` is itself suspect because failure modes co-occur (a dark frame is often also low-confidence).

**Build implication (thin):** leave the existing two-detector backstop in place as written — it is a correct *no-op* while ≤1 detector is calibrated and the floor-based aggregate governs — but **do not cite its `0.0016` justification anywhere operator- or auditor-facing**, and do not let any new p05/p03 surface depend on a composite per-frame downgrade.

### 4.5 The publish / clinical bar (what "trusted" requires beyond shipping)

Shipping on defaults (4.0) is the MVP bar. The publish/clinical bar adds, per detector: (1) **a pre-registered operating point with a clustered-bootstrap CI** (4.2-4.3), reported as Se/Sp with study-level n, separately for p4x-enriched and cohort-pass-natural label sources; (2) **an out-of-sample regression check** — the Youden-J cut is optimistically biased when chosen and evaluated on the same data, so fit on sample 1 and validate Se/Sp on a sample 2 drawn weeks later by the same study-clustered strategy, requiring sample-2 Se/Sp inside the sample-1 Wilson 95% CI ([MetricGate](https://metricgate.com/docs/youden-index/): "always validate the chosen threshold in an independent sample before clinical use"); (3) **a precision target that sizes the labeling pass** via the **Wilson score interval** (asymmetric, never overshoots [0,1], accurate near 0/1 and small n — the Se-near-1 regime here), stated in **studies, not frames** ([Wilson interval, Wikipedia](https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval); [MDPI Mathematics 8(8):1258](https://www.mdpi.com/2227-7390/8/8/1258)); (4) **inter-rater reliability** — with one operator today, the within-operator **test-retest κ** (relabel a held-out subset blind after a 2-week gap) is the available signal until Anonaao joins as a second rater; treat a low κ as a mechanism-validity warning; (5) **a multiplicity statement IF a composite verdict is published** — Holm (FWER) for independent confirmatory claims across the detector family, Benjamini-Hochberg (FDR) for the exploratory "which detectors separate" screen ([mbrenndoerfer](https://mbrenndoerfer.com/writing/multiple-comparisons-fwer-fdr-bonferroni-holm-benjamini-hochberg); [Columbia Mailman, *FDR*](https://www.publichealth.columbia.edu/research/population-health-methods/false-discovery-rate)). For option A's two-cut, study-level filter this is **not** invoked — there is no family of confirmatory hypotheses being published.

### 4.6 In-house vs. statistician: the explicit boundary

**In-house (Claude + Akadir), no statistician needed:** run the Youden-J + sensitivity-target fit on the two open cuts once labels exist; implement and run the **study-clustered percentile bootstrap** (a resample-studies loop, not exotic); report Wilson intervals and size the labeling pass in studies; compute test-retest κ; keep the corrected compound-FPR numbers and ensure no A-surface depends on the broken arithmetic.

**Requires a human statistician (gates *trust*, not *shipping*):** re-deriving the concurrence rule if subsystem B ever surfaces a composite per-frame verdict — specifically the independence assumption (failure modes co-occur, so `q^n` is wrong, and the operative q must be the *measured* per-detector FPR, not 0.20) and the multiplicity correction; signing off the publish-bar operating points and their CIs for any clinical/publication claim (the fit is in-house, the *endorsement* is not); the fit-method choice itself (Youden-J vs cost-weighted) if the asymmetric-cost argument is formalized into a loss function.

**Requires a clinician (Anonaao):** the §6 gating-table semantics — which measurement each flag should invalidate vs soften — is a clinical-meaning question, not a statistical one, and is upstream of every fit.

### 4.7 What the probe already settled (so it is not re-litigated as a Part-4 gap)

The probe is *evidence Part 4 builds on*, not an open item. Reproduced this session against `..._sweep_pool_5fps.parquet` (201,652 rows, 272 studies, 18,332 frames, 0 skipped, 0 degenerate) and its `_qf` twin (4,096 frames / 47 studies): **only `dark_clip_high` is a clean GREEN** (3,629 fires / 108 studies); `wrong_site` is GREEN on availability (1,963 fires / 141 studies); the acquisition-failure detectors are **positive-starved** — `motion_blur`/`vessel_shadow` empty z>3.5 tail, `tgc`/`near_field` heavy-tail-not-separable, `air_gap` separable but 18-study-concentrated and 16% unscoreable; the rejected-cohort re-run confirms starvation is **real, not a clean-cohort selection artifact** (`air_gap` worse at 37% scoreable). The wrong_site "dangerous mislabel" worry was **over-read** — its `rf_column_coverage` piles at 1.0 and the firing tail is 97% end-of-sweep ramp-off. These are why Part 4's open surface is **two cuts** and why the per-detector calibration *campaign* is parked with subsystem B.

### 4.8 Summary table: what is rigorous, what is deferred, who acts

| Item | State for option A | Owner | Blocks MVP? |
|---|---|---|---|
| `dark_clip_high` / `wrong_site` fire on defaults | Done (0.02 / 0.5) | shipped | no |
| Youden-J + sens-target fit for the 2 cuts | Method specified (4.2); runs when labels exist | Claude + Akadir | no (trust bar) |
| Study-clustered bootstrap CIs | Method specified (4.3); resample-studies loop | Claude in-house | no (trust bar) |
| Wilson-sized, study-clustered labeling pass | Method + sizing rule specified | Claude + Akadir + operator | no (trust bar) |
| Compound-FPR corrected number (~80%, not 0.16%) | Corrected + reproduced (4.4) | Claude (done) | no — must not surface composite verdict |
| Concurrence rule re-derivation | Deferred with subsystem B | statistician | no |
| Publish/clinical operating-point sign-off | Deferred (publish bar) | statistician + Anonaao | no |
| §6 gating-table clinical semantics | Open | Anonaao | no |

---

## Part 5 — Forward architecture (user-facing app / subsystem-B per-frame coach): INTENT + HOOKS, not a near-term build

*This part is documented INTENT so the path is recoverable, NOT a build. Subsystem B is PARKED — not abandoned, blocked on DATA not design. Nothing here is built now. The point is that the data layer (Part 2), the surfacing spine (Part 3), and the calibration machinery already exist as hooks, so B is a volume-of-positives problem, not a re-architecture.*

### 5.1 The binding prerequisite: an acquisition-of-positives dataset

Subsystem B is an **operator-facing per-frame failure-mode coach** — per-frame "bad because X" callouts on p04 for the acquisition-failure modes (motion blur, air gap, vessel shadow, TGC banding, near-field clutter). It cannot be built on this cohort because **the cohort lacks the positives** — the probe confirmed starvation is real in both the alive and rejected cohorts (Part 0.3, Part 4.7). The binding prerequisite #1 is a **deliberately-collected set of examples of the starved modes**. This is a DATA problem; no engineering shrinks it.

**The unblock playbook (the concrete "what to do," so "blocked on DATA" is a checklist, not a slogan — Akadir 2026-05-31).** To unblock B you need ≥ ~50 *confirmed-positive* examples **per acquisition detector**, counted at the **study** level (Part 4.3), of the modes the research cohort lacks. Three acquisition paths, in increasing cost/yield:
1. **Deliberate failure-rich capture** — a few sessions where an operator *intentionally* produces the failure modes (motion blur by moving during capture; air gap by under-coupling; vessel shadow by imaging over a vessel; TGC banding by mis-setting gain). Fastest, most controllable; yields clean positives for motion_blur / air_gap / tgc immediately.
2. **The user-facing-app passive channel** (§5.2) — non-professional users produce these modes as a byproduct of using the app; ingest via the same `(study_code, frame_base)` spine. Highest yield, ~zero extra acquisition effort, but gated on the app existing.
3. **Mining a messier external clinical stream** — pull failure-rich frames from a real-world (non-research) ultrasound source. Medium cost; depends on access.

Once positives exist: label them via **p4x** (already built; the labeling surface), fit with the **study-clustered method** (Part 4.2–4.3), and stand up the **consumer** (fitter / promoter / the **corrected** compound-FPR backstop — §4.4) *before* any promotion. The binding constraint is **path 1/2/3 yield**, not engineering — the schema, the labeling surface, and the fit method are already ready (§5.3).

### 5.2 The natural acquisition channel: a future user-facing app with non-professional users

The app may become a **user-facing app with real, possibly non-professional users** (NOTED, not actioned, Akadir 2026-05-30, [docs/rf_qa_next_phase_plan.md:131](docs/rf_qa_next_phase_plan.md#L131)). Such users produce the messy, failure-mode-rich captures the trained-operator research cohort lacks — i.e. **a user-facing app is the natural acquisition-of-positives channel that unblocks subsystem B.** This is the B-revival path, recorded so it is not lost. Worry later.

### 5.3 The hooks that already exist (why B is recoverable, not a rebuild)

- **Data ingest hook.** Non-professional captures enter the *same* `(study_code, frame_base)` address space (Part 2.2); the only new work is teaching `frame_loader`'s index a second path root. No store schema changes. The full `Label` ledger schema (Part 2.3.2) is the ready contract a B write satisfies; `label_source` already distinguishes evidence channels.
- **Capture-surface hook.** p4x (the blind-then-reveal capture page) is built + tested and is the **ready-made labeling surface** for the B-revival data, after the same slim `frame_loader`/`candidate_selector` re-wire A's optional pass needs (Slice D1).
- **Surfacing hook.** B's surface is **p04** (where per-frame detail already lives, deliberately kept off the calm p05), reusing `frame_qa_summary` + `flag_prose` + the `_GATING_TABLE` attribution that already exist (Part 3.8).
- **Provenance hook.** The `model_sig` addition (Part 2.4) and the `detector_status` lifecycle ladder are the provenance scaffolding a B fit will write into.

### 5.4 What B will need built (recorded, not built now)

1. **The acquisition-of-positives dataset** (5.1) — the binding prerequisite.
2. **The consumer machinery** (fitter / promoter / **compound-FPR backstop**) — built only when B is revived, before any promotion, and the backstop built **correctly** (the §6 arithmetic is wrong by ~500x; re-derive the concurrence rule + specificity targets with a statistician, Part 4.4/4.6, using measured FPRs and co-occurrence-aware, non-independent math). The round-state ledger (`calibration_rounds.jsonl`) is part of this.
3. **The per-frame coach surfacing** on p04 — and, for non-expert users, **the global "this rests on AI segmentation" intended-use boundary** (Part 3.4) and far more aggressive uncertainty surfacing, since a non-expert cannot supply the priors a trained operator has. The non-expert surfacing regime is a different, harder friction/trust budget than the trained-operator app.

### 5.5 The trigger for revival

Subsystem B revives when the acquisition-of-positives dataset exists (the user-facing channel, or a deliberate failure-rich collection) AND Akadir greenlights the consumer build. The ungradeable/end-of-sweep gate (Q1) also revives here — it is deferred in A because `wrong_site` already excludes those frames and the study number is unchanged; its real value is behind-the-scenes (clean `wrong_site` validation + latent safety on messier cohorts), so it returns when per-frame reasons are surfaced prominently or on a messier data source.

---

## Part 6 — Sequencing + acceptance gates + what needs humans

### 6.1 The build sequence (option A)

| Step | Slice | What | Blocks MVP/demo? | Acceptance gate |
|---|---|---|---|---|
| 1 | **A** | Verify the six-gate layer feeds p05 pooling + the consistency gate; decide `dark_clip` ships pending (it does) | **Yes** (verify) | trace_frame AGZ855 unchanged; `pytest tests/preflight/test_qa_aggregation.py tests/semantic_invariants/test_data_invariants.py` green |
| 2 | **B** | The one net-new render: the compact p05 "pooled from N of M; K set aside" disclosure (honest-on-clean line + conditional expander over `run_what_changed` rows) | **Yes** | clean run -> line + no expander; outlier run -> "M-1 of M" + Z-evidence row; no-femur run -> QMT/STAR provenance row; no raw codes / no "§"; `tests/preflight/test_qa_surfacing.py` (the (N,M,K) accessor) + one `tests/ui/` AppTest green; **Layers 1+2 self-administered before Akadir click-through** |
| — | — | **SHIP / DEMO** (A + B deliver option A's trust contract) | — | — |
| 3 | **C** | Verify the p03 picker cue (already shipped; no all-orange regression) | No (verify) | picker paints `wrong_site`/no-femur, silent on clean; `tests/ui/test_femur_scan.py` green |
| 4 | **D1** | Slim sweep-frame `frame_loader` re-wire (goruntuler_backup fallback resolver) | No | sweep `(study_code, frame_base)` resolves + synthesizes `_final` natively; transport path back-compat intact; PHI invariant held |
| 5 | **D2/D3** | Re-point `candidate_selector._PARQUET` to the sweep-pool table; add `wrong_site` as a labelable validation spec | No | p4x lists `dark_clip_high` + `wrong_site`; a label round-trips into the (newly-created) ledger with full metadata; `tests/calibration/` + `tests/ui/test_p4x_calibration.py` green |
| 6 | **E** | Optional dark_clip / wrong_site validation pass (study-clustered, score-stratified, tens of frames/detector) | No (trust bar) | ≥1 clean labeling session/detector; agree/disagree rate at the boundary computed; **no promotion without Akadir's word** |

Slices D + E share the D1 re-wire; build them only when the optional validation is greenlit. **Wave-1 also includes Slice G** (surface the 272-study cohort — foundational, do early; §1.7) **and Slice F** (the RF-representative-frame scorer — wave-1, parallel to A+B; §1.6, ships advisory with the comparability claim deferred). Both are independently testable; Slice G is the prerequisite for using A/B/F on the real cohort.

### 6.2 The cross-cutting acceptance gates (load-bearing, not aspirational)

- **Trace-frame pre-claim ritual** ([memory `feedback_trace_frame_pre_claim_ritual`]) — before claiming any UI/QA change "done," run `scripts/trace_frame.py` on canonical AGZ855 frames + verify semantic invariants. Run it for Slice A.
- **Verification ladder Layers 1+2** ([memory `feedback_verification_ladder`]) — self-administer before any Akadir click-through (Layer 3). HARD GATE posture on Layer 1. Layer 3 is not a fallback for skipped self-tests.
- **The friction-budget gate** (Part 3.0) — every new surface must pass "does the operator need to act on this, on this screen, for this task?" The only deliberate exception to default-quiet is the single p05 provenance caption.
- **The PHI gate** — every QA/calibration record carries `study_code + frame_base` only; the patient-name path stays in `frame_loader`'s server-side index, never emitted.
- **The composite-verdict gate** (Part 4.4) — no new p05/p03 surface may depend on a composite per-frame downgrade; the §6 `0.0016` justification is never cited operator- or auditor-facing.

### 6.3 What needs humans

**Akadir (the human, decision gates):** the build-or-pivot ordering; the call on whether `dark_clip` surfaces at the picker as a steer (the recorded allow-set); greenlighting the optional Slice-E validation pass; **commit/push** (nothing pushes without explicit word); any threshold promotion (the `dark_clip` Tier-A flip).

**Anonaao (clinician, gates TRUST not shipping):** confirm the §6 gating-table clinical semantics — which measurement each flag should invalidate vs soften. This is a clinical-meaning question upstream of every fit; it does not block the MVP/demo.

**A human statistician (gates TRUST not shipping):** re-derive the concurrence rule **if and only if** subsystem B surfaces a composite per-frame verdict (using the corrected ~80% baseline, measured per-detector FPRs, and co-occurrence-aware non-independent math); sign off the publish-bar operating points and their clustered-bootstrap CIs for any clinical/publication claim. Dormant for option A.

**A different model family (gates the EVIDENCE, not shipping):** re-check the probe's separability heuristic (the z>8 persistence + study-spread CALL logic) as a cross-family audit of the starvation verdict — the same-model-bias caveat, applied to the evidence Part 4 rests on. The probe and the internal 17-agent review both initially missed the position-in-sweep covariate ([docs/rf_qa_next_phase_plan.md:136](docs/rf_qa_next_phase_plan.md#L136)); a different family is the structural guard against family-shared blind spots. Recorded as a deferred validation, not a ship blocker.

### 6.4 The open questions a fresh session must surface to the human (not silently resolve)

1. The exact **"X of Y" denominator convention** for the disclosure line (min-pool vs headline-measurement-anchored) — plan leans min-pool; one-line change either way.
2. **Whether the p05 provenance line shows on a fully-clean study (always-on trust contract) or stays quiet** — the MVP ships **quiet-on-clean** (§1.2 / §3.4); the always-on line is the argued alternative, one reaction away. Plus the exact collapsed-line copy and `st.expander`-vs-custom-caret choice (wording lands from Akadir's reaction; UI ships thin).
3. Whether `dark_clip_high` surfaces at the **p03 picker as a steer** (the recorded allow-set) — the one genuine cross-lens divergence; plan ships silent, allow-set one reaction away.
4. Whether/when to run the **optional Slice-E validation pass** (and the `dark_clip` Tier-A promotion it would unlock).
5. Whether/when to **promote the sweep-pool parquet to canonical** + re-point `candidate_selector._PARQUET` (a post-gate action).

None of (1)-(5) blocks shipping A; all are recorded with owner + trigger so the scope does not silently disappear.
# RF Image-Quality QA/QC — Canonical Plan v0.2

*The single canonical specification and implementation plan for the rectus-femoris (RF) ultrasound image-quality QA/QC initiative in the muscle_mania project. This document supersedes and absorbs the prior triad — the v0 contract (`rf_frame_qa_contract_v0.md`), Akadir's redline pass (`docs/_audits/contract_v0_audit/05_akadir_redlines.md`), and the integration prompt (`docs/_audits/contract_v0_audit/INTEGRATION_PROMPT.md`) — into one self-sufficient artifact. A fresh reader — human or model — should be able to understand and execute from this document alone, without opening any other file.*

*Status: **v0.2 — canonical**. Lineage: v0 contract (commit `ed22f9f`, 2026-05-18) → four-agent audit → Akadir redline read-pass (2026-05-19) → four-critic review → v0.1 canonical plan (2026-05-20) → Akadir's 28-comment review + an independent critical-reader audit (2026-05-20) → this v0.2. Author: Claude Opus 4.7, ratifying decisions made by Akadir in-session. Anonaao's (clinical authority) sanity-check of § 4 and § 6 is tracked in the § 17 future-work register; per Akadir's 2026-05-20 review it is no longer a release blocker.*

*Document state (2026-05-20): complete — § 0–§ 17 + Appendix A. v0.2 folds in Akadir's 28-comment review of v0.1 and an independent critical-reader audit (7 HIGH / 10 MED / 5 LOW findings, all dispositioned — see `docs/_audits/contract_v0_audit/07_v0_2_comment_dispositions.md`). All five § 10 open questions are resolved; the § 14 build sequence is reframed; § 17 (future-work register) is added; the § 4.2 catalogue, the § 5–§ 6 detector and gating specs, and the § 11 calibration substrate are corrected; the version axis is disambiguated so the plan ships as v0.2.*

---

## § 0.1 — How to read this document

This plan serves five distinct readers. Each row says what to read first and what is safe to skip on a first pass. Every reader should start with § 0 (this section, the status taxonomy, the glossary, and the nomenclature key) so the vocabulary and the tagging convention are in hand.

| You are | Read first | Skip-OK on first pass |
|---|---|---|
| **Akadir** — project lead, design review | § 0, § 1, § 2 (ratified decisions), § 6 (gating table), § 8 (UI/UX), § 10 (open questions), § 14 (build sequence) | § 5 detector internals, § 11 calibration math |
| **Anonaao** — clinical authority | § 0, § 1, § 3 (measurement landscape), § 4 (failure-mode catalogue), § 6 (gating table), § 8.1–8.2 (operator journey), § 10 | § 5 code-level detector specs, § 7 backend integration, § 11–§ 12 |
| **Fresh-Claude implementer** — a future model executing this plan | All of it, top to bottom. Per the project discipline "read named sources end-to-end," do not sample. | Nothing |
| **Paper reviewer** — future academic scrutiny | § 0, § 1, § 3, § 4, § 5.1 (the reason-code tuple), § 6, § 11 (calibration methodology), § 13 (limitations register) | § 5.2–§ 5.5 code-level detector specs, § 7–§ 8 engineering, § 14 sequencing |
| **Curious outsider with a STEM background** — bright, not a medical-imaging specialist | § 0 (lean on the glossary, § 0.3), § 1 (project context), § 6.3 (the aggregate-tier rule — needed to follow § 15), § 8.1 (operator journey before/after), § 14 (build sequence), § 15 (worked examples) | § 5, § 6.1–§ 6.2, § 11.2–§ 11.5 statistical method, § 8.3–§ 8.7 component internals |

If you hit a term you do not know, it is in the glossary (§ 0.3). If you hit a status tag in square brackets — `[Active]`, `[Conditional]`, `[Named-deferred]`, `[Future-direction]`, `[Held]` — it is defined in § 0.2. If you hit a process word — *tier*, *stage*, *step*, *phase*, *channel* — the nomenclature key (§ 0.4) disambiguates it.

---

## § 0.2 — Status taxonomy

Every detector, failure mode, calibration method, build step, citation, and limitation in this plan carries exactly one status tag. The tag tells a reader — and a future revision — how load-bearing the item is *right now*. Tags are written inline in square brackets, e.g. `[Active]`.

| Tag | Meaning |
|---|---|
| **[Active]** | Implemented, or implementable in v0.2, with a named detector / method / value. This is what v0.2 actually ships. |
| **[Conditional]** | Activated only when a specific downstream condition is met (e.g. a Step-2 cohort result, a Tier-1 ship trigger). Dormant by design until then. |
| **[Named-deferred]** | Listed in the catalogue or registry for completeness, but its detector / mechanism is deferred to a later version. Named so the gap is visible, not silent. |
| **[Future-direction]** | A pointer to literature or a scope for a future workstream. No v0.2 commitment. |
| **[Held]** | Considered and explicitly decided against *for now*, with a recorded revisit trigger. The decision is parked, not abandoned. |

**Global rule — owner and trigger.** Wherever a tag appears: every `[Conditional]` item must name the *specific condition* that activates it; every `[Named-deferred]`, `[Future-direction]`, and `[Held]` item must name an *owner* (who picks it up) and a *trigger* (the event that makes it live). "Deferred for now" with no owner and no trigger is not allowed — that is how scope silently disappears. Where an owner is genuinely not yet decided, write `owner: TBD pending <event>` rather than omitting it.

---

## § 0.3 — Glossary

Terms are defined here once and used throughout. A reader new to ultrasound or to this project should be able to follow the whole plan with this table in hand.

### Anatomy and measurements

| Term | Definition |
|---|---|
| **RF** | Rectus femoris — one of the four quadriceps muscles of the thigh, and the muscle this project images and measures. |
| **VL / VM / VI** | Vastus lateralis / vastus medialis / vastus intermedius — the other three quadriceps muscles, anatomical neighbours of RF. The segmentation model labels each as a distinct class, which matters for the `wrong_site` detector (§ 5). |
| **QMT** | Quadriceps muscle thickness — the vertical depth measured from the RF superficial fascia down to the femur (thigh-bone) cortex. A core clinical measurement. |
| **BMI** | Body-mass index — body weight divided by height squared; a standard body-size measure. |
| **STAR** | The QMT-to-BMI ratio (QMT ÷ BMI) — a body-size-normalised muscle thickness, so two people of different builds can be compared. |
| **fascia / aponeurosis** | The bright sheets of connective tissue that bound a muscle on ultrasound. The "superficial fascia" is the upper boundary of RF, the "deep fascia" (or deep aponeurosis) the lower one. This document uses **fascia** throughout for consistency. |
| **fascicular contrast** | The variation of pixel brightness *within* the muscle region — quantified as `int_cv_within`, the coefficient of variation (standard deviation ÷ mean) of intensity inside the muscle. High contrast = visible striated fascicle texture; low contrast = a uniformly bright or dark muscle, which reads as poor image quality or as a clinical finding. |
| **ROI / eroded ROI** | Region of interest — the muscle mask. The "eroded" ROI is that mask shrunk inward by a few pixels, so measurements computed inside it ignore the noisy mask boundary. |

### Imaging and signal terms

| Term | Definition |
|---|---|
| **DICOM** | Digital Imaging and Communications in Medicine — the standard file-and-metadata format the ultrasound device emits. |
| **TGC** | Time-gain compensation — the ultrasound machine's depth-dependent brightness correction. Mis-set TGC produces horizontal brightness banding unrelated to anatomy. |
| **anisotropy** | The angle-dependence of an ultrasound reflector's brightness. A fascia segment can darken or vanish ("anisotropy dropout") when the beam strikes it off-perpendicular, even though the tissue is intact. |
| **specular reflection** | A mirror-like reflection off a smooth surface — strong only when the ultrasound beam strikes near-perpendicular to it. The fascia sheets bounding the muscle are specular reflectors; this is why beam angle matters, and why an off-angle beam makes a fascia segment darken (the *anisotropy* above) even though the tissue itself is intact. |
| **acoustic shadow** | The dark band that appears *beneath* a structure which strongly reflects or absorbs the beam. Bone blocks the beam almost entirely — so a clean dark shadow beneath a bright line is what confirms that line is a true bone cortex. The femur detectors (§ 5) rely on this bright-line-plus-shadow signature. |
| **reverberation** | When the beam bounces repeatedly between two closely-spaced reflectors — most often across a trapped pocket of air — it returns a regularly-spaced stack of false bright lines, a "comb" pattern. The `air_gap_reverberation` detector (§ 5) looks for exactly that comb. |
| **despeckling** | Noise-reduction filtering applied to ultrasound images to suppress the grainy "speckle" texture; the `_final.png` modality (§ 2, D3) is the despeckled grayscale the pipeline measures on. |
| **Tenengrad** | A gradient-energy sharpness measure — the summed squared image gradients. High Tenengrad = crisp edges; low = blurred. Used by the `motion_blur` detector (§ 5). |
| **Karamalis confidence map** | A per-pixel estimate of "how trustworthy is the ultrasound signal here," computed by a random-walk method (Karamalis et al., 2012). Low confidence flags regions the beam likely imaged poorly; used by the Tier-1 detectors (§ 5). |
| **argmax / softmax** | A segmentation model produces, per pixel, a score for each possible class. **softmax** converts those scores into probabilities; **argmax** picks the single highest-scoring class. The current segmenter keeps only the argmax label and discards the probabilities — relevant to `segmentation_low_confidence` (§ 5). |

### Method and statistics terms

| Term | Definition |
|---|---|
| **IRR** | Inter-rater reliability — how often two independent human assessors assign the same label to the same frame. |
| **Cohen's κ (kappa)** | An agreement statistic that corrects for agreement expected by chance. κ ≥ 0.6 is conventionally "substantial agreement." |
| **Wilson 95% CI** | The Wilson score 95% confidence interval — a confidence interval for a proportion (e.g. a detector's sensitivity) that stays reliable at small sample sizes, unlike the naive interval. |
| **ROC** | Receiver-operating-characteristic — the curve traced by a detector's sensitivity versus specificity as its threshold is swept. Used to pick an operating point (§ 11). |
| **FDR / BH-FDR** | False-discovery rate — the expected fraction of flags that are false alarms. BH-FDR is the Benjamini-Hochberg procedure that bounds the FDR when many detectors run at once. |
| **ARIA** | Accessible Rich Internet Applications — the set of HTML attributes (screen-reader labels, roles) that make a UI usable with assistive technology. |
| **INV / BDR** | The two non-passing verdicts of the per-measurement gating table (§ 6). **INV** = *invalid* — the measurement cannot be trusted from this frame and is excluded from the pooled aggregate (the frame and the value are still shown — § 6.4). **BDR** = *borderline* — usable but degraded; kept, with a flag attached. A blank gating cell means the flag is transparent to that measurement. |

### The two "tier" axes — read this carefully

The word **tier** is used on two *different* axes in this plan. They are deliberately kept distinct; conflating them is a known documentation hazard.

| Axis | Values | Meaning |
|---|---|---|
| **Detector tier** | Tier-0 / Tier-1 / Tier-2 | How hard the *detector* is to build. **Tier-0** = deterministic, ships from physics or simple geometry. **Tier-1** = ultrasound-specific, needs a confidence map. **Tier-2** = a learned model, needs training data. |
| **Threshold-tier** | Threshold-tier A / Threshold-tier B | Where a detector's numeric *threshold* comes from. **Threshold-tier A** = physics-grounded universal (e.g. the 255 saturation rail), ships with a sensible default. **Threshold-tier B** = cohort-calibrated, must be fitted on data. |

A single detector has *both* a detector tier and a threshold-tier. Example: `saturation_high` is a **Tier-0** detector (deterministic) with a **Threshold-tier A** threshold (the 8-bit saturation rail is universal). `motion_blur` is a **Tier-0** detector with a **Threshold-tier B** threshold (the sharpness cut-off depends on the cohort's probe and gain).

---

## § 0.4 — Nomenclature key

This plan uses several numbered or lettered series — *tier*, *stage*, *step*, *phase*, *class*, *channel*, *version*. They are deliberately distinct and are not interchangeable; this table is the single key. (Added in v0.2: the v0.1 review found the process vocabulary hard to keep straight.)

| Term | Values | What it tells you | Defined in |
|---|---|---|---|
| **Status tag** | `[Active]` / `[Conditional]` / `[Named-deferred]` / `[Future-direction]` / `[Held]` | How load-bearing an item is *right now* | § 0.2 |
| **Detector tier** | Tier-0 / Tier-1 / Tier-2 | How hard the *detector* is to build — deterministic → confidence-map → learned | § 4.1 |
| **Threshold-tier** | A / B | Where a detector's numeric *threshold* comes from — physics-universal (A) vs cohort-calibrated (B) | § 11.1 |
| **Detector-class** | A–G (v0.2 draws on B/C/D/G) | What *kind of algorithm* a detector is | § 4.1 |
| **Validity tier** | valid / borderline / invalid | The QA *verdict* — per measurement, and the per-frame aggregate | § 6 |
| **Workflow phase** | Phase 1–5 | A stage of the operator's *assessment workflow* (study → model → picker → review → assessment) | § 1.1, § 8.1 |
| **Label-pipeline stage** | Stage 1–5 | A step in how a *label or grade flows* from capture to wire-back | § 7.0 |
| **Build step** | Step 0–7 | A step of the *implementation roadmap* | § 14 |
| **Feedback channel** | Channel A / B / C / D | A structured *production-feedback* path | § 12.2 |
| **Version** | v0 → v0.1 → v0.2 (current) | The revision of this plan, which *is* the contract; the build ships under the current version and `CONTRACT_VERSION` stamps it onto every grade | § 12.3, § 16 |

The word **tier** is the one genuine overload — it names *both* the detector-build axis (Tier-0/1/2) *and* the threshold-provenance axis (Threshold-tier A/B). Every detector carries exactly one of each; the worked pairing is in § 0.3 ("the two 'tier' axes"). The other series above never collide.

**Version is a single axis.** This plan *is* the contract (§ 16); its revisions run v0 → v0.1 → **v0.2** (current). The build ships under v0.2, and the `CONTRACT_VERSION` constant (§ 12.3) stamps that number onto every grade. Where the text names an *earlier* revision — "the v0.1 spec", "Akadir's review of v0.1", the § 16 v0.1 CHANGELOG row — it keeps that revision's number as a historical reference; everything the current build does is v0.2. There is no separate "product v0.1" line: the plan revision and the shipped contract carry the same number.

The plan also carries five plain ID registers, each enumerated and defined in its own section: Decisions **D1–D8** (§ 2), Failure modes **F1–F22** (§ 4.2), Transparency rules **1–4** (§ 9), Open questions **Q1–Q5** (§ 10), Limitations **L1–L8** (§ 13).

---

## § 1 — Scope and project context

### 1.1 — What the muscle_mania assessment pipeline does (context for the outsider)

The muscle_mania project assesses thigh-muscle health from ultrasound. A clinical operator places an ultrasound probe on a patient's mid-thigh, aligned along the muscle's long axis, and performs a **sweep** — moving the probe to capture a short run of image frames across the rectus femoris (RF). Those frames are stored as a study.

Later, at the **assessment phase**, an operator works through a five-phase software workflow (the assessment app): pick a study, pick a segmentation model, **pick the good frames** from the sweep, review and gate each picked frame, and then see an assessment result — a card of muscle measurements placed against a reference cohort. The headline measurements are quadriceps muscle thickness (QMT), the body-size-normalised STAR ratio, full-RF thickness, and fascicular contrast (§ 3 defines all of them).

The measurements are only as trustworthy as the frames they are computed on. A frame can be blurred, clipped at the image edge, mis-exposed, scanned off-axis, or even of the wrong muscle. **This plan specifies a QA/QC layer that grades, for every frame, whether each measurement can be extracted from it reliably** — and surfaces that grading to the operator before they commit to a frame selection.

### 1.2 — What this plan covers

A **per-frame image-quality grading system** for RF ultrasound frames, applied at the assessment phase — *after* a sweep has been captured and stored, *before or during* the operator's frame selection. The system answers one question, per frame, per measurement:

> *"Is this frame good enough that the assessment pipeline can extract* measurement-X *from it reliably?"*

The answer is structured, not a single score: a per-frame tuple of reason-coded flags (e.g. `motion_blur`, `femur_out_of_frame`), and a per-measurement validity tier (`valid` / `borderline` / `invalid`) derived from those flags. The plan covers the failure-mode catalogue (§ 4), the detectors that find them (§ 5), the gating rule that turns flags into tiers (§ 6), the backend integration (§ 7), the **user-facing UI/UX integration** (§ 8 — a load-bearing peer of the backend, not an afterthought), the calibration methodology (§ 11), and the build sequence (§ 14).

### 1.3 — What this plan does NOT cover

Each exclusion below carries an **owner** and a **trigger** per the § 0.2 global rule, so the deferred scope is visible and routable, not silently dropped.

- **Muscle-health quality.** Frames are graded on *measurability* — can the pipeline extract the number — not on what the muscle looks like clinically. A frame can be image-quality-perfect and the muscle clinically poor; this plan grades only the former. *(Out of scope by definition — clinical interpretation is the assessment product's job, not the QA layer's.)*
- **Acquisition-time QA.** The same flag taxonomy *could* run at scan time, pushing checks back to the probe so a bad frame is never saved. The detectors in § 5 are deliberately designed to be reusable at scan time without re-architecture. `[Future-direction]` — *owner: TBD pending a dedicated acquisition-side workstream; trigger: v0.2 assessment-side QA validated on the cohort and an acquisition-side product decision taken.*
- **Multi-frame / sweep-level consistency.** `frame_rf_consistency_px` is already computed post-hoc as a sensitivity metric ([`labeling_app/measurements.py:1168`](../labeling_app/measurements.py#L1168)). Promoting it from a passive metric to an active gate is a sweep-level concern, whereas this plan is strictly per-frame. **`[Done]`** — Build-Step 2 Phase 3 item 9a promoted it to an active *per-run* gate: `aggregation._rf_consistency_report`, an Iglewicz-Hoaglin modified-Z outlier test on `rf_thickness_mm` that excludes a sweep-outlier frame from the aggregate pool (§ 14 Step 4).
- **Auto-recommendation of N best frames.** A diversity-aware recommender — automatically pick 3–5 frames that are individually good *and* collectively spread across the muscle — depends on this plan's per-frame grading but is a separate capability. `[Future-direction]` — *owner: TBD; trigger: Build-sequence Step 7 (§ 14), after Steps 1–6 ship.*

### 1.4 — The "is it measurable?" framing — the organising idea

The whole plan is organised around one question asked per frame, per measurement: **is image quality good enough to *measure* this?** This is deliberately *not* the question "is this a good frame overall." A single overall verdict throws away the most useful information, because **different measurements fail on different, often disjoint, image problems** (§ 3, Observation 1). A frame with a missing femur is useless for QMT but perfectly fine for fascicular contrast. The QA layer therefore grades each measurement independently and surfaces the per-measurement picture — that is the "is it measurable?" framing, and it is the reason the contract is structured as per-measurement flags rather than one score (§ 2, D1).

---

## § 2 — Ratified decisions and held alternatives

These decisions were ratified by Akadir in-session (2026-05-18, with redline additions 2026-05-19). Each is stated with its **reasoning** — the *why* before the *what* — so a reader can judge edge cases rather than just follow the rule. All are `[Active]` unless tagged otherwise.

### 2.1 — The eight ratified decisions

| # | Decision | Why |
|---|---|---|
| **D1** | **Per-measurement flags, not one scalar score.** `[Active]` | Measurements fail independently on disjoint image problems (§ 3, Observation 1). Pooling into one number discards which measurements are still usable. The existing QMT validity-flag system already commits to per-measurement validity; this plan generalises that proven pattern. |
| **D2** | **Assessment-gating scope only for v0.2; acquisition-time QA is a future extension.** `[Active]` | Acquisition-time QA has a different latency budget and a different consumer (the operator at the bedside, not the post-hoc assessor). The detectors are built to be reusable there later, but the v0.2 *surface* is the assessment workflow only. See § 1.3. |
| **D3** | **QA runs on the `_final.png` modality, not the raw `.jpg`.** `[Held]` — see § 2.2 for the deliberation. | Intensity measurements (`rf_echo`, `int_cv_within`) are computed on the synthesised despeckled grayscale `_final.png`. QA must run on the *same array the measurement uses*, or it audits a different image than the one being measured. |
| **D4** | **Auto-grade every frame; surface the grade visibly before the operator selects.** `[Active]` | The operator should see "what is viable and what is not" *before* committing to a selection, not after. This mirrors the assessment app's existing discipline of showing status, never auto-picking. **D4 governs inference-time auto-grading** — the detectors run on every frame at the picker stage; no human label is required for D4 to function. (Contrast D7.) |
| **D5** | **The contract document carries full rigor; the UI is a calibrated summarisation of it.** `[Active]` | The design document is the authoritative source. UI surfaces never carry information that is not derivable from the contract, and the contract's depth is independent of how much the UI can show. Ship the UI thin, iterate its depth from operator reactions — but never let UI bandwidth cap the contract's rigor. |
| **D6** | **Four transparency rules bind every UI surface:** never hide a frame, always show the "why," always allow override, pre-fill don't pre-decide. `[Active]` | These make every QA decision auditable. They are stated in full and operationalised per-component in § 8 / § 9. |
| **D7** | **Step-1 label capture = the operator-picked frames + 5 sweep-stratified unpicked frames.** `[Active]` | One unpicked frame is sampled from each of five equal temporal zones of the sweep. This guarantees spatial spread of the labelled set and prevents a random sampler from clustering all labels in one part of the muscle. Cost: ~30 seconds of extra labelling per study. **D7 governs label-capture for calibration** — it produces the ground-truth dataset used to tune detector thresholds (§ 11); it is a *different track* from D4's inference-time grading. (Contrast D4.) |
| **D8** | **`subq_fat_compressed` is a Tier-0 deterministic detector.** `[Active]` | Subcutaneous fat is already a segmented class (`LABEL_FAT = 8`) and is already used by `muscle_fat_ratio()`. Because the fat layer is segmented, its thickness can be measured deterministically from the mask — so the compression detector is Tier-0, not a deferred Tier-1 concept. |

**Why D4 and D5/D7 are easy to confuse — and the distinction that resolves it.** D4 is about *inference*: detectors run automatically on every frame and produce a grade, with no human in the loop. D7 is about *label capture*: humans grade a sampled set of frames to produce the ground-truth data that the § 11 calibration uses to *set the detectors' thresholds*. Inference-grading (D4) is what the operator sees; label-capture (D7) is how the detectors get tuned. They are two separate tracks that meet only at calibration time.

### 2.2 — D3 held-alternative: the `_final.png` vs raw-`.jpg` deliberation

D3 was challenged during Akadir's 2026-05-19 read-pass and deliberated over several exchanges. The deliberation is recorded here so it is not silently re-litigated.

**The challenge.** The `_final.png` is derived from the device's `.jpg`; PNG conversion cannot recover detail already lost in the original JPEG compression. Why not run QA (and measurement) on the raw `.jpg` directly, with the segmentation mask rescaled to the `.jpg` resolution — the segmenter itself would not need to change?

**Why it was held.** The proposed flip is technically tractable but carries real, named costs against a benefit that is currently only theoretical:

- *Pixel-spacing rework.* Moving to `.jpg` space re-activates horizontal pixel-spacing (`sx`) across detectors. There is a dormant x-axis spacing bug in a sibling module (`resolve_spacing` scales x by the full overlay-frame width, not the signal width); expanding `sx` usage without care would wake it. The vertical-spacing formula would also need per-device verification of `.jpg` vs DICOM dimensions.
- *Baseline re-validation.* The measurement cohort and the QMT provenance gate are baselined on the current `_final.png` pipeline; a flip voids that baseline and forces a full re-validation.
- *No evidence of harm yet.* No audit found that the `_final.png` downsampling actually degrades any detector. The benefit (more native detail for high-frequency detectors) is plausible but unmeasured.

**Held disposition.** `[Held]` — D3 stands; `_final.png` is canonical for both measurement and QA in v0.2. **Revisit trigger:** if the Step-2 cohort calibration (§ 11) shows poor positive/negative separation specifically for the high-frequency detectors (`motion_blur`, `tgc_banding`, `int_cv_within`), revisit. **Intermediate option if revisited:** raise the segmenter's input resolution rather than performing the full canonical-array swap — a smaller-blast-radius change. *Owner: Akadir, with the implementer; trigger: the Step-2 separation result.*

### 2.3 — Tier-1 deferral conditions and the Step-3 handoff requirement

Akadir added the following two paragraphs during the 2026-05-19 read-pass. They are ratified policy and are reproduced verbatim.

> **Tier-1 confidence-map work is deferred until after Step 2 unless Step 2 shows unacceptable residual failures in anisotropy dropout or deep attenuation.** Step 2 must produce a failure-mode histogram and false-negative review. If Tier-0 misses clinically meaningful fascia dropout or deep attenuation cases, Step 3 becomes an owned implementation task: build a confidence-map module, sample confidence around superficial/deep fascia bands, calibrate thresholds using structured Phase-4 labels, and wire resulting flags into `image_quality_tier()`. Until that trigger is met, Tier-1 remains scoped but unimplemented.

> **Step-3 handoff requirement:** before implementation, create a one-page Tier-1 technical spec covering: confidence-map algorithm choice, input/output API, where confidence maps are stored, thresholds to calibrate, validation cohort, and owner. Do not start Tier-1 engineering from the current contract alone; it is a detector concept, not a build spec.

This makes the Tier-1 detectors (`anisotropy_dropout_*`, `deep_attenuation_high`) `[Conditional]`: *condition — the Step-2 failure-mode histogram and false-negative review show Tier-0 misses clinically meaningful anisotropy-dropout or deep-attenuation cases; owner — TBD, named in the one-page Tier-1 spec; trigger — the Step-2 readout.*

### 2.4 — Resolved: OF-2 (Tier-1 timing)

A v0.1 open fork (OF-2) asked whether the Tier-1 confidence-map work should run *sequentially after* the Step-2 cohort run or *in parallel with* it. **Resolved: sequential.** The § 2.3 deferral condition already settles it — Tier-1 is deferred until the Step-2 failure-mode readout shows Tier-0 misses clinically meaningful cases, so Tier-1 timing is necessarily gated on that readout; there is no remaining fork. *(v0.2 — the v0.1 "Open fork" framing is resolved per the critical-reader audit finding M10; OF-2 needed no separate decision and is not carried into the § 10 open-questions register.)*

---

## § 3 — The measurement landscape

This section explains *what* the assessment pipeline measures, and *what image structures each measurement physically depends on*. The QA layer (§ 4–§ 6) grades frames against exactly these dependencies, so this section is the foundation the rest of the plan builds on.

### 3.1 — The `FrameFeatures` record

The pipeline computes, per frame, a `FrameFeatures` record — a frozen dataclass at [`scripts/preflight_helpers/feature_compute.py:77-112`](../scripts/preflight_helpers/feature_compute.py#L77) with **24 fields** (verified 2026-05-20). Those 24 fields fall into three groups:

- **Measurements surfaced on the result card (7):** the four headline measurements and the three secondary measurements — see § 3.2.
- **Supporting measurements not carded (8):** `rf_echo`, `full_area_mm2`, `full_max_thickness_mm`, `area_retained_fraction_after_erosion`, `int_mean`, `int_std`, `int_median`, `int_entropy` — computed and available, used in calibration and aggregation but not shown on the headline card.
- **Provenance and mask-status metadata (9):** `frame_base`, `is_anchor`, `qmt_provenance`, `qmt_guidance_status`, `qmt_validity_flags`, `touches_edge`, `is_empty`, `drop_reasons`, `provenance`.

(An earlier vastus-intermedius (VI) feature set was removed in the May-2026 measurement trim that took the displayed feature count from 9 down to 7; see [`scripts/cohort_stats_precompute.py:47`](../scripts/cohort_stats_precompute.py#L47). The QA layer adds five `image_quality_*` fields in a separate `ImageQualityFeatures` dataclass joined on `frame_base` — specified in § 7.1.)

### 3.2 — The seven surfaced measurements

The result card shows seven measurements, defined in [`scripts/preflight_helpers/run_pipeline.py:77-87`](../scripts/preflight_helpers/run_pipeline.py#L77).

**Headline four** (`HEADLINE_FEATURES`, `run_pipeline.py:77-82` — the demo card carries these):

| Measurement | What it is |
|---|---|
| `star_ratio` | STAR — quadriceps muscle thickness divided by body-mass index (QMT ÷ BMI). A body-size-normalised thickness. |
| `full_thickness_q50_mm` | The median ("q50" = 50th percentile) full-RF thickness, measured across the eroded ROI. |
| `int_cv_within` | "Fascicular contrast" — the coefficient of variation of pixel intensity inside the eroded ROI (standard deviation ÷ mean). Captures how visible the muscle's striated texture is. |
| `rf_thickness_mm` | RF thickness at the centre column of the muscle. |

**Secondary three** (`SECONDARY_FEATURES`, `run_pipeline.py:83-87` — shown in a collapsible card):

| Measurement | What it is |
|---|---|
| `muscle_fat_ratio` | The ratio of muscle to subcutaneous-fat thickness. |
| `qmt_mm` | The raw quadriceps muscle thickness in millimetres (the un-normalised input to STAR). |
| `rf_area_mm2` | The cross-sectional area of the RF muscle in the frame. |

*(Note: an in-code comment at `p05_assessment_view.py:708` says "the secondary 5" — that comment is stale, predating the 9→7 trim. `SECONDARY_FEATURES` has exactly three members; this plan is correct, the comment should be fixed when that file is next touched.)*

### 3.3 — Structure dependencies — what each measurement physically requires

This table is the core of the plan. The **"structures required"** column lists the *image structures* whose presence and quality a measurement depends on. The **"failure modes that kill it"** column points forward to the catalogue in § 4. The governing rule: **a frame is good for measurement X if and only if every structure in X's row is intact.**

| Measurement | Structures required | Failure modes that kill it (§ 4) |
|---|---|---|
| `rf_thickness_mm` | Superficial + deep RF fascia | `oblique_plane`, `anisotropy_dropout_superficial` / `_deep`, `motion_blur`, `edge_clip_vertical` |
| `rf_area_mm2`, `full_area_mm2` | Complete in-frame RF cross-section + fascia | `edge_clip_lateral`, `edge_clip_vertical`, `oblique_plane`, `anisotropy_dropout_*` |
| `full_thickness_q50_mm`, `full_max_thickness_mm` | Both fascia continuous across the eroded ROI width | `anisotropy_dropout_*`, `oblique_plane`, `motion_blur`, `edge_clip_vertical` |
| `area_retained_fraction_after_erosion` | Mask plausibility (downstream of segmentation quality) | Degenerate masks — already gated by `touches_edge` / `is_empty` |
| `qmt_mm` | RF superficial fascia + femur cortex + acoustic shadow in the central zone | `femur_out_of_frame`, `femur_off_central_zone`, `oblique_plane`, `anisotropy_dropout_*`, `motion_blur` |
| `star_ratio` | = `qmt_mm` ÷ BMI — inherits every `qmt_mm` failure, plus needs a valid BMI | inherits `qmt_mm` flags + demographic-missing (not an image-quality flag) |
| `muscle_fat_ratio` | Subcutaneous fat layer present + uncompressed + RF mask | `subq_fat_compressed`, `edge_clip_vertical`, `oblique_plane` |
| `rf_echo`, `int_mean`, `int_median` | ROI grayscale fidelity — no saturation, no dark-clip | `saturation_high`, `dark_clip_high`, `tgc_banding` |
| `int_cv_within`, `int_std`, `int_entropy` | ROI grayscale fidelity + no texture-homogenising artifact | `saturation_high`, `dark_clip_high`, `tgc_banding`, `motion_blur` (smears texture) |

### 3.4 — Three load-bearing observations

These three observations explain *why* the plan is shaped the way it is. They should survive into any future revision.

**Observation 1 — the geometry block and the intensity block fail on disjoint flags.** A frame can have flawless intensity (no saturation, no TGC banding) but a missing femur cortex: `rf_echo` survives, `qmt_mm` dies. The reverse also happens. Because the failure sets barely overlap, pooling all measurements into one scalar score would discard the single most useful fact — *which* measurements are still usable. This is the direct justification for D1 (per-measurement flags) and for the "is it measurable?" framing (§ 1.4).

**Observation 2 — `qmt_mm` is the most fragile measurement.** It depends on three structures at once — the RF superficial fascia, the femur cortex, and the acoustic shadow beneath the femur — all present and clean. It therefore has the longest failure-mode list in § 3.3. The existing QMT validity-flag system already encodes much of this fragility; the QA contract generalises that proven pattern to every measurement.

**Observation 3 — `wrong_site` is the orthogonal failure mode.** If the operator scanned the wrong muscle — vastus lateralis instead of rectus femoris, say — then *every* measurement is silently wrong while every individual structure still looks plausible. No per-measurement structural check catches this, because the anatomy is fine; it is just the wrong anatomy. `wrong_site` therefore needs its own top-priority flag and its own detection strategy (§ 5).

---

## § 4 — Failure-mode catalogue

This section names every way an RF frame can fail to support a measurement. Each entry has a physical mechanism, a visual signature an operator can recognise, the measurements it taints (forward into § 6's gating table), a detector class, and a status tag (§ 0.2). The detector *specifications* — how each is actually computed — are in § 5.

### 4.1 — Two classification axes (read § 0.3 first)

Each failure mode is tagged on the two "tier" axes defined in the § 0.3 glossary, plus a **detector-class letter**. To keep all three legible at a glance:

**Detector tier — how hard the detector is to build:**
- **Tier-0** — deterministic; ships day one from physics or simple geometry.
- **Tier-1** — ultrasound-specific; needs a confidence-map infrastructure.
- **Tier-2** — learned; needs a trained model and training data.

**Detector-class letter — what kind of algorithm it is** (from the brainstorm's § 3 "Algorithms available," an A–G ranking weakest-to-strongest for *this* problem; v0.2 draws on B/C/D/G because those four each have an already-existing hook in the live pipeline):
- **Class B** — deterministic pixel-statistics (saturation %, focus measures, edge-clip). Hook: `touches_edge` already exists.
- **Class C** — ultrasound confidence map (per-pixel signal trust; Karamalis-style random-walk). Hook: evaluable inside the ROI.
- **Class D** — anatomical / structural model (is the femur present? are the two fascia lines parallel?). Hook: the QMT validity flags are already structural QA, scoped to one measurement.
- **Class G** — measurement-coupled QA (segmenter uncertainty, mask plausibility, multi-frame consistency). Hook: `frame_rf_consistency` is already computed.
- *(Classes A — generic natural-image quality, E — learned graders, F — anomaly/out-of-distribution detection — are named in § 11 / § 14 as future directions; v0.2 does not anchor on A or F, and E is the Step-5 learned grader.)*

### 4.2 — The catalogue (22 failure modes)

Each row's **Detector** column carries, compactly: the detector tier, the detector-class letter (§ 4.1), a one-line mechanism, and the threshold-tier (§ 11.1 — *A* = physics-universal, ships with a default; *B* = cohort-calibrated, set from data). The full per-detector specification — algorithm, exact thresholds, override semantics — is § 5; this column is the at-a-glance version (enriched in v0.2 so a reader can see *how* a mode is caught and *whether* its threshold is calibrated without jumping to § 5).

| # | Failure mode | Physical mechanism | Visual signature | Measurements tainted | Detector — tier · class · mechanism · threshold-tier | Status |
|---|---|---|---|---|---|---|
| F1 | **wrong_site** | Operator scanned the wrong muscle — vastus lateralis/medialis/intermedius, or off-thigh entirely — instead of RF | RF is not the dominant muscle in the frame; a neighbouring muscle's belly fills it | ALL measurements, silently | Tier-0 · D+G · ROI quadriceps class-composition (`rf_fraction`) · Thr-B | **[Active]** — segmenter class-composition detector (§ 5); a learned anatomical classifier is **[Future-direction]** — *owner: TBD; trigger: the Step-5 learned grader* |
| F2 | **segmenter_mislabel_wrong_class** | The segmenter confidently labels a non-RF frame *as* RF (so class-composition cannot catch it) | None visible — the segmenter output looks self-consistent | ALL measurements, silently | Tier-2 · D · no v0 detector — needs cross-class confidence or the Step-5 learned grader | **[Named-deferred]** — *owner: TBD; trigger: Step-5 learned grader, or a cross-class confidence detector* |
| F3 | **femur_out_of_frame** | Probe too shallow, or wrong depth setting | No bright continuous cortical line + clean acoustic shadow anywhere below the deep fascia | `qmt_mm`, `star_ratio` | Tier-0 · D · reuses `femur_absent` — cortex + acoustic-shadow search · Thr-A | **[Active]** — reuses existing `femur_absent` |
| F4 | **femur_off_central_zone** | Femur present but off to one side of the frame | Femur cortex + shadow exist, but outside the central-zone pixel margin | `qmt_mm` (degrades to proxy), `star_ratio` | Tier-0 · D · reuses `outside_central_zone` — 15-px central margin · Thr-A | **[Active]** — reuses existing `outside_central_zone` |
| F5 | **edge_clip_lateral** | RF belly extends past the left/right frame edge | RF mask touches the lateral frame border | `rf_area_mm2`, `full_area_mm2` only (see § 6 + § 2 on the coverage-limitation rule) | Tier-0 · B · mask in the outermost lateral-column ring (N=1) · Thr-A | **[Active]** |
| F6 | **edge_clip_vertical** | Probe pressed shallow or pulled deep — a measurement-bearing structure is itself cut off at the top/bottom edge | Superficial fascia clipped at the top edge, or deep fascia / femur clipped at the bottom edge — the structure runs off-frame, not merely near it | `rf_thickness_mm`, `full_thickness_*`, `qmt_mm` if femur is then off-bottom | Tier-0 · B · measurement-structure mask in the outermost top/bottom-row ring (N=1) · Thr-A | **[Active]** |
| F7 | **saturation_high_global** | Gain too high — pixels clipped at the 255 rail across the ROI | Flat-white regions; intensity histogram piled at the maximum | `rf_echo`, `int_mean/std/median/entropy`, `int_cv_within` | Tier-0 · B · ROI-wide fraction of pixels ≥ 250 grey · Thr-A rail / Thr-B fraction | **[Active]** |
| F8 | **saturation_high_regional** | Strong local specular reflector — concentrated clipping at the 255 rail in one patch | A bright flat-white patch, often in the fascia band | `rf_thickness_mm` (if the patch hits the fascia band); `rf_echo` mostly survives | Tier-0 · B · fraction ≥ 250 grey in any 32×32 block · Thr-A rail / Thr-B fraction | **[Active]** — split from a single `saturation_high` per technical audit |
| F9 | **dark_clip_high** | Gain too low, or deep attenuation — pixels clipped at the 0 rail | Flat-black regions inside or beneath the muscle | `rf_echo`, `int_*`; propagates to `anisotropy_dropout_deep` if it touches the deep fascia band | Tier-0 · B · ROI fraction of pixels ≤ 5 grey · Thr-A rail / Thr-B fraction | **[Active]** |
| F10 | **tgc_banding** | Time-gain compensation mis-set: depth-dependent gain produces horizontal bands (typically several mm tall) | Horizontal bright/dark stripes that do not follow anatomy | `rf_echo`, `int_*` (depth-dependent bias) | Tier-0 · B · σ of the detrended row-mean profile, muscle rows only · Thr-B | **[Active]** |
| F11 | **motion_blur** | Probe or patient moved during frame capture | Fascia lines smeared — thick and soft, not crisp and thin; fascicles smeared | All geometry (border detection), `int_cv_within` (smears texture) | Tier-0 · B · fascia-band Tenengrad sharpness · Thr-B | **[Active]** |
| F12 | **air_gap_reverberation** | Poor probe-skin coupling — an air bubble | Periodic reverberation comb-bands in the skin-contact zone; a dark wedge | Every measurement in the affected column set | Tier-0 · B · FFT comb in the segmented skin–fascia gap, 3–8 px period · Thr-B | **[Active]** |
| F13 | **near_field_clutter** | Probe-element artifacts in the very near field (top 2–5 mm) | Non-anatomical structure in the top 2–5 mm, which the segmenter may absorb into the skin, superficial-fascia or fat mask | Superficial geometry; mask plausibility | Tier-0 · B · high-frequency non-periodic clutter in the segmented near-field layers (skin→fat) · Thr-B | **[Active]** — distinct from F12: clutter is high-frequency, not periodic; the near-field zone is segmentation-anchored (skin/fat layers), not a fixed pixel window — § 5.2 |
| F14 | **vessel_shadow_in_roi** | A small artery or calcified plaque (deep to the fat layer) casts a vertical shadow column | A near-zero-intensity vertical column with no horizontal (TGC) pattern | Every measurement in the shadowed column; mimics `anisotropy_dropout_deep` to the deep-fascia detector | Tier-0 · B · near-zero-intensity vertical column, no horizontal TGC pattern · Thr-B | **[Active]** |
| F15 | **posterior_acoustic_enhancement** | Sound passes through a fluid/echo-poor region (e.g. a vessel) and over-brightens the tissue *beneath* it | A locally brighter vertical strip beneath an echo-poor structure — a regional, anatomy-located gain artifact (not depth-uniform like TGC) | `int_mean`, `rf_echo` in narrow vertical strips | Tier-0 · B · no v0 detector — named for taxonomy completeness | **[Named-deferred]** — *owner: TBD; trigger: a v0.x detector pass; rare at RF mid-thigh, named for taxonomy completeness* |
| F16 | **subq_fat_compressed** | Operator pressed too hard on the probe | Subcutaneous fat layer thin and flat, or absent | `muscle_fat_ratio` primarily; `qmt_mm` biased BDR (compression shortens the measured fascia-to-femur distance — *provisional*, see § 6.2); all thicknesses underestimated to a lesser degree | Tier-0 · B · median central-zone fat thickness from the mask · Thr-B | **[Active]** — fat is segmented (`LABEL_FAT`), so the *measurement* is deterministic (decision D8); but single-frame "compressed" vs naturally-thin discrimination is intrinsically weak — see § 5.2 |
| F17 | **oblique_plane** | Probe rotated off the muscle's long axis | Fascia lines converge / fade across the width; fascicular striations out of plane | `rf_thickness_*`, `qmt_mm`, `rf_area_*`, `full_*`, `int_cv_within`, `rf_echo` (BDR) | Tier-1 · D · Radon orientation — superficial-vs-deep fascia parallelism · Thr-B | **[Conditional]** — *condition: a Radon-parallelism component is implemented (§ 5); owner: TBD per the Step-3 one-page Tier-1 spec; trigger: the Step-2 readout. The v0 QMT-fascia-slope reuse is dropped — see § 5.* |
| F18 | **anisotropy_dropout_superficial** | Beam tilt: the superficial fascia loses its specular reflection over one segment of width | One contiguous segment of the superficial fascia darkens / disappears; the rest stays bright | `rf_thickness_mm` (if a column hits the dropout), `rf_area_mm2`, `full_*` | Tier-1 · C · confidence map sampled in the superficial-fascia band · Thr-B | **[Conditional]** — *condition: confidence-map module built; owner/trigger per § 2.3* |
| F19 | **anisotropy_dropout_deep** | Same as F18, on the deep fascia / RF–VI interface | Same as F18, on the deep edge | `rf_thickness_mm`, `qmt_mm`, `rf_area_mm2`, `full_*` | Tier-1 · C · confidence map sampled in the deep-fascia band · Thr-B | **[Conditional]** — *condition/owner/trigger per § 2.3* |
| F20 | **deep_attenuation_high** | Deep / high-BMI patient: the beam attenuates before reaching deep structures | Deep aponeurosis and femur faint, low-contrast | `qmt_mm`, deep `full_*` | Tier-1 · C · deep-half mean confidence (precedence vs F19 — § 5.3) · Thr-B | **[Conditional]** — *condition/owner/trigger per § 2.3* |
| F21 | **segmentation_low_confidence** | The segmentation model itself is uncertain about the mask | No human-visible signature — a model-internal signal | All geometry (mask-derived) | Tier-2 · G · mask-boundary entropy from the segmenter softmax · Thr-B | **[Active]** — v0.2 promotion (Comment 2): exposing per-pixel softmax is a ~15-LOC addition to `predict_mask` (§ 5.4); the boundary-entropy *threshold* is Threshold-tier B (calibrated) |
| F22 | **probe_orientation_flipped** | Medial-lateral or proximal-distal flip of the right muscle | None on a single frame — the muscle looks correct, just mirrored | STAR ratios survive; any future anatomical/directional measurement (e.g. fascicle angle) inverts | — · D · no v0 detector | **[Named-deferred]** — *owner: TBD; trigger: introduction of a directional measurement; `wrong_site` covers wrong-muscle but not wrong-orientation* |

### 4.3 — Three observations carried from § 3

The catalogue confirms § 3's three observations. **(1)** The catalogue's modes group into families that taint *largely disjoint* measurement sets — geometry (F5, F6, F11, F17–F20), intensity (F7–F10, F15), femur (F3, F4), coupling/artifact (F12, F13, F14), anatomical (F16), model-derived (F21), orientation (F22), and the orthogonal modes (F1, F2). Geometry and intensity in particular barely overlap — the direct reason for per-measurement gating. (The families are an explanatory grouping, not a strict partition; a few modes span two blocks.) **(2)** `qmt_mm` appears in the most rows (F3, F4, F6, F11, F16, F17, F19, F20) — it is the most fragile measurement. **(3)** `wrong_site` (F1) and `segmenter_mislabel_wrong_class` (F2) are orthogonal — they taint *everything* while every individual structure still looks intact.

---

## § 5 — Detector specifications

This section specifies *how* each failure mode is detected. The reason codes are held in a frozen priority tuple; the per-code specs follow.

### 5.1 — The reason-code priority tuple

`IMAGE_QUALITY_FLAG_PRIORITY` lives in `labeling_app/measurements.py` adjacent to the existing `QMT_VALIDITY_FLAG_PRIORITY` ([`labeling_app/measurements.py:51`](../labeling_app/measurements.py#L51)). It is a frozen tuple; **priority order controls UI surfacing prominence** — when a frame has several flags, the highest-priority one is shown as the "dominant reason" badge — **and flag-precedence in debugging**. It does *not* control visibility (every flag is always inspectable; per Transparency Rule 1, § 9).

```python
# To live in labeling_app/measurements.py adjacent to QMT_VALIDITY_FLAG_PRIORITY.
IMAGE_QUALITY_FLAG_PRIORITY = (
    # orthogonal — taints every measurement
    "wrong_site",
    "segmenter_mislabel_wrong_class",
    # structural — femur
    "femur_out_of_frame",
    "femur_off_central_zone",
    # geometric / framing
    "edge_clip_lateral",
    "edge_clip_vertical",
    # gross intensity
    "saturation_high_global",
    "saturation_high_regional",
    "dark_clip_high",
    "tgc_banding",
    # gross sharpness
    "motion_blur",
    # coupling / near-field / artifact
    "air_gap_reverberation",
    "near_field_clutter",
    "vessel_shadow_in_roi",
    "posterior_acoustic_enhancement",
    # anatomical (deterministic — fat is segmented)
    "subq_fat_compressed",
    # ultrasound-specific (Tier-1, Conditional)
    "oblique_plane",
    "anisotropy_dropout_superficial",
    "anisotropy_dropout_deep",
    "deep_attenuation_high",
    # model-derived (Tier-2; [Active] in v0.2 — § 5.4)
    "segmentation_low_confidence",
    # orientation (named-deferred)
    "probe_orientation_flipped",
)
```

Each detector below carries: **meaning** (the physical fact); **detector** (the algorithm — technical-audit rewrites are carried verbatim where they exist); **threshold** with its **threshold-tier** (A = physics-grounded universal, B = cohort-calibrated — see § 0.3 and § 11.1); **measurements tainted**; **override semantics**; **status**.

### 5.2 — Tier-0 detectors (deterministic — the v0.2 working set)

*A naming note: identifiers in backticks such as `femur_absent`, `outside_central_zone`, and `touches_edge` are **pre-existing** detectors and flags already in the codebase (in `QMT_VALIDITY_FLAG_PRIORITY` and the post-processing step). Where a spec below says it "reuses" one, v0.2 wires that already-computed signal into the QA layer rather than building it fresh.*

#### `wrong_site` — [Active]

**Meaning.** The frame is not of RF — it could be vastus lateralis (VL), vastus medialis (VM), vastus intermedius (VI), or off-thigh.

**Detector.** A **Tier-0 deterministic class-composition check** on the segmentation mask — newly available because the segmenter outputs RF, VI, VL, VM as *distinct* classes (verified 2026-05-20: `RF=1, VI=2, VL=3, VM=4` in `scripts/preflight_helpers/segmenter.py` — labels 5–7 are other tissue classes, fascia / skin / bone, not quadriceps muscle, so the class-composition check ignores them; the v0 contract under-claimed this capability, calling `wrong_site` human-only). The detector counts pixels per quadriceps class within the ROI and computes `rf_fraction = RF_pixels ÷ all_quadriceps_pixels`; it flags `wrong_site` when `rf_fraction` is below a threshold and surfaces the dominant alternative class. **The off-thigh case is handled first and separately (audit fix H7):** when the ROI contains essentially no quadriceps-class pixels at all, `rf_fraction` is undefined (0 ÷ 0), so *before* the ratio is computed the detector tests the total quadriceps-pixel count against a floor `MIN_QUAD_PX` — below that floor, `wrong_site` fires directly with the dominant reason *off-thigh / no quadriceps muscle in frame*. **Prerequisite:** add `LABEL_VL = 3` and `LABEL_VM = 4` constants to `labeling_app/measurements.py` (currently only `LABEL_VI` is exported there) — a two-line edit. The human signal also remains: a Phase-4 "Use for analysis: No" with reason `wrong_site` is always honoured.

**Threshold.** `rf_fraction < 0.5` is a sensible starting point; `MIN_QUAD_PX` (the off-thigh floor, audit fix H7) is a small fixed pixel count. **Threshold-tier B** — both need cohort calibration (§ 11): a dominant-but-not-majority RF frame is a judgement call, and `MIN_QUAD_PX` must be set so a legitimately small in-frame RF belly is not misread as off-thigh.

**Measurements tainted.** All.

**Override.** Operator confirms the anatomy is genuinely RF despite a low `rf_fraction` (e.g. a legitimately small RF belly in-frame). Logged.

**Note.** `segmenter_mislabel_wrong_class` (F2) is the residual case this detector *cannot* catch — the segmenter confidently calls a non-RF frame "RF." That stays **[Named-deferred]**: detection needs cross-class confidence or the Step-5 learned grader.

#### `femur_out_of_frame` — [Active]

**Meaning.** No femur cortex line + acoustic shadow visible below the deep fascia. The frame cannot support `qmt_mm` or `star_ratio`.

**Detector.** *Already exists* as `femur_absent` in `QMT_VALIDITY_FLAG_PRIORITY`. It searches for a near-horizontal high-intensity line below the RF mask with an acoustic shadow beneath. The existing detector is column-wise; v0.2 lifts the column-wise verdict to a frame-level flag when no column finds a femur.

**Threshold.** **Threshold-tier A** by reuse — already calibrated for the QMT pipeline.

**Measurements tainted.** `qmt_mm`, `star_ratio`.

**Override.** Operator confirms femur presence despite a detector miss (rare). Means "use this frame for QMT with a logged caveat."

#### `femur_off_central_zone` — [Active]

**Meaning.** Femur present, but outside the central zone where the perpendicular fascia-to-femur measurement is reliable.

**Detector.** *Already exists* as `outside_central_zone`. **Central-zone correction (clarity audit):** the central zone is defined by a **fixed pixel margin — the constant `QMT_CENTRAL_ZONE_MARGIN_PX = 15` at [`labeling_app/measurements.py:47`](../labeling_app/measurements.py#L47)** — *not* the "[W/4, 3W/4] quartile band" the v0 contract incorrectly stated. It is a 15-pixel inset, and this plan corrects the v0 error.

**Threshold.** **Threshold-tier A** — `QMT_CENTRAL_ZONE_MARGIN_PX = 15`, already calibrated.

**Measurements tainted.** `qmt_mm` (degrades from standard to a femur-supported proxy), `star_ratio`.

**Override.** Operator accepts the proxy QMT despite the off-centre femur.

#### `edge_clip_lateral` / `edge_clip_vertical` — [Active]

**Meaning.** A muscle (or femur) mask touches the left/right (lateral) or top/bottom (vertical) frame edge — meaning the structure is *cut by the frame boundary* and continues outside it. `edge_clip_vertical` matters specifically because a measurement-bearing structure — the superficial fascia, the deep fascia, or the femur — is itself truncated, so a thickness or QMT span loses an endpoint. If a structure merely ends *near* an edge with clear frame margin around it, the mask does not reach the boundary ring and the detector does not fire — a frame whose fascia is fully captured carries no flag (Comment 10).

**Detector.** Extends the existing `touches_edge` boolean ([`scripts/preflight_helpers/post_processing.py:106`](../scripts/preflight_helpers/post_processing.py#L106)) into directional flags: a mask pixel in the outermost N-pixel ring of a left/right column → `edge_clip_lateral`; of a top/bottom row → `edge_clip_vertical`.

**Threshold.** Boundary contact within the outermost N-pixel ring; **`N = 1` for both `edge_clip_lateral` and `edge_clip_vertical`** — the existing `touches_edge` behaviour. **Threshold-tier A**, universal. The technical audit's proposed N-tiering is dropped for the lateral case (per the C11 rule below) and is *not* adopted for the vertical case either — v0.2 uses `N = 1` uniformly (audit fix M7).

**Measurements tainted — and the coverage-limitation rule (Akadir redline C11).** `edge_clip_lateral` is a **measurement-specific coverage limitation, not a frame-level failure.** It invalidates only the measurements whose definition *requires the complete in-frame RF cross-section* — `rf_area_mm2` and `full_area_mm2`. It does **not** invalidate `qmt_mm`, `star_ratio`, central `rf_thickness_mm`, or the intensity metrics when their anchor structures and ROIs remain visible. Rationale: in this acquisition set only a handful of scans capture the full RF cross-section; treating lateral clip as a frame-fail would invalidate most of the cohort. `edge_clip_vertical` keeps the broader taint set (it threatens the fascia the thickness measurements need). This rule **overrides** the technical audit's proposed N-tiering for the lateral case. **`edge_clip_vertical` is not redundant with `femur_out_of_frame` (Comment 10):** `femur_out_of_frame` fires when *no* femur is found anywhere in the frame; `edge_clip_vertical` fires when a structure *is* present but is clipped at the boundary — e.g. the superficial fascia runs off the top edge, so `rf_thickness_mm` loses its upper caliper endpoint even though the femur, and therefore QMT, may be perfectly fine. The two flags catch different failures; a frame can carry either, both, or — when every structure is fully in-frame — neither.

**Override.** Operator confirms the clipped region is anatomically empty (the muscle genuinely ends in-frame). Rare.

#### `saturation_high_global` / `saturation_high_regional` — [Active]

**Meaning.** Pixels clipped at the 255 rail. The v0 single `saturation_high` is **split into two detectors** per the technical audit, because *where* the saturation lands changes which measurements die.

**Detector (verbatim, technical audit § 1 / § 3).** *"`saturation_high` should split into `saturation_high_global` (>2% ROI-wide) vs `saturation_high_regional` (>10% in any 32×32 block); regional saturation in the fascia band kills `rf_thickness_mm` but leaves `rf_echo` mostly intact — different gating."* The pixel cut-off is `≥250` grayscale.

**Bright anatomy vs. true saturation (Comment 8).** A tendon, a strong specular fascia reflection, or a calcification is *bright* but is not *clipped* — its pixels typically sit in the ~180–245 range, below the `≥250` rail. The detector keys on the **rail**, not on brightness: only pixels pinned within ~5 grey of the 8-bit ceiling count toward the fraction, so genuinely bright anatomy does not by itself trip `saturation_high_regional`. If a very strong reflector *does* rail, the flag is still correct — railed pixels carry no recoverable intensity regardless of what produced them, so the measurements that read intensity in that patch are degraded either way.

**Threshold.** `≥250` grayscale is **Threshold-tier A** (the 8-bit rail is universal physics). The fraction cut-offs: `saturation_high_global` fires at `>2%` ROI-wide — the technical audit recommends **tightening to 1% ROI-restricted**; `saturation_high_regional` fires at `>10%` in any 32×32 block. The fraction cut-offs are **Threshold-tier B** (the audit's note that "2% globally ≠ 2% in the fascia ROI" means they want data).

**Measurements tainted.** `saturation_high_global` → `rf_echo`, all `int_*`. `saturation_high_regional` → `rf_thickness_mm` if the patch is in the fascia band; `rf_echo` mostly survives.

**Override.** Operator accepts intensity bias for geometry-only use of the frame; the UI must let the override be scoped to specific measurements (§ 8.3).

#### `dark_clip_high` — [Active]

**Meaning.** Pixels clipped at the 0 rail — gain too low, or deep attenuation.

**Detector.** Fraction of ROI pixels at `≤5` grayscale. If the dark region touches the deep fascia band, it propagates to `anisotropy_dropout_deep`.

**Threshold.** `≤5` grayscale is **Threshold-tier A**; the fraction cut-off (`>2%`, candidate tighten to 1% as for saturation) is **Threshold-tier B**.

**Measurements tainted.** `rf_echo` and all `int_*` (including `int_cv_within`) are **INV** — at the 0 rail the pixels carry no recoverable intensity, so the intensity statistics are not merely biased but unusable in the clipped region. **`qmt_mm` / `star_ratio` degrade to BDR** when the dark region reaches the deep fascia / femur band — the deep-fascia propagation noted under *Detector* above starves the QMT measurement of its deep anchor. (The INV-vs-BDR verdicts are stated explicitly here so this spec and the § 6.2 `dark_clip_high` gating row agree — audit fix H1.)

**Override.** As for saturation — geometry-only use.

#### `tgc_banding` — [Active]

**Meaning.** Time-gain compensation mis-set; horizontal bright/dark bands contaminate depth-dependent intensity.

**Detector (rewritten — technical audit § 4).** The v0 "row-mean second-difference σ" is **rejected** — it false-triggers on every healthy frame because the femur creates a large second-difference at one row. Replacement, verbatim: *"**detrend the row-mean profile** by fitting a smooth monotonic decreasing exponential (the expected attenuation curve), then measure σ of the *residual*. Bands appear as residual oscillations; the femur disappears into the trend. Also restrict to *muscle-only rows* (between superficial fascia and deep fascia from segmentation) — excludes femur, fat, skin entirely."*

**Band scale (Comment 6).** TGC mis-set produces *broad* horizontal bands — characteristically a few millimetres tall (tens of image rows), the height of the device's TGC depth segments — so the residual oscillation lives at a low vertical spatial frequency. That scale separates true gain banding from finer anatomical striation and from speckle, and it tells the σ measurement which part of the residual spectrum to look in.

**Threshold.** σ of the residual, **Threshold-tier B** — depends on the TGC curve shape and gain range; cohort-calibrated (§ 11).

**Measurements tainted.** `rf_echo`, `int_*`.

**Override.** Operator confirms the apparent banding is a real fascial layer, not gain-induced.

#### `motion_blur` — [Active]

**Meaning.** Probe or patient motion smears fascia lines and fascicles; border detection becomes unreliable.

**Detector (rewritten — technical audit § 4).** The v0 "variance-of-Laplacian" is **rejected**: speckle is the dominant high-frequency component of a static B-mode frame, so variance-of-Laplacian primarily measures *speckle intensity*, not motion (Loizou et al. 2006 — § 16 — give the speckle-confounding evidence). Replacement, verbatim: *"**Tenengrad on the *fascia band only*** (a 10-pixel band around the segmenter's superficial fascia line) — fascia is a specular structure whose sharpness is genuinely motion-degraded and not speckle-confounded. Or: **local autocorrelation FWHM** in the fascia band — a motion-blurred fascia has a wider autocorrelation peak."*

**Threshold.** Tenengrad value in the fascia band, **Threshold-tier B** — unit-free, depends on probe / gain / target resolution; cohort-calibrated.

**Measurements tainted.** All geometry measurements are **INV** — motion smears the fascia lines and mask boundaries that caliper placement depends on, so the geometric value is not recoverable from a blurred frame. `int_cv_within` is **BDR**, not INV: blur smooths texture and *biases* the contrast coefficient downward, but the statistic is still computable on the degraded ROI — degraded, not destroyed. (The INV-vs-BDR split is stated here so this spec and the § 6.2 `motion_blur` row agree — audit fix H2.)

**Override.** Operator confirms the apparent blur is anatomical (rare for RF).

#### `air_gap_reverberation` — [Active]

**Meaning.** An air bubble between probe and skin produces periodic reverberation bands and signal loss in the affected columns.

**Detector (rewritten — technical audit § 4).** The v0 "FFT magnitude ratio in the top 50 rows" is **rejected**: 50 rows ≈ 6 mm on a typical 800-row frame, well inside the subcutaneous fat layer, whose fibrous septa give a periodic-ish false signal. Replacement, verbatim: *"**restrict the FFT window to the *skin–fascia gap region* identified by segmentation** (the air gap is *above* skin label, by definition); detect periodic comb pattern there with period 3–8 px (the reverberation periodicity), not arbitrary 1–25 bins."* The detector returns the **affected column set** (needed by § 6's gating).

**Threshold.** FFT comb-peak magnitude in the 3–8 px period band, **Threshold-tier B** — depends on probe footprint and reverberation period.

**Measurements tainted.** Every measurement in the affected column set (see § 6 — geometry → INV in those columns, intensity → BDR).

**Override.** Rare — the air-gap signature is usually obvious.

#### `near_field_clutter` — [Active]

**Meaning.** Probe-element artifacts in the very near field (top 2–5 mm) produce non-anatomical structure the segmenter may absorb into the superficial-fascia or fat mask.

**Detector (technical audit § 1, new mode; window re-anchored per Comment 4).** Detect **high-frequency, non-periodic** clutter in the near field, with the search window **anchored to the segmented near-field layers** — the skin band and the subcutaneous-fat band the segmenter already labels — rather than a fixed "top 2–5 mm" pixel window. The clutter sits in and just above the skin layer; because the segmenter delineates skin, fat, and fascia as distinct classes, the detector keys on those labels and is robust to patients whose near-field anatomy sits at different depths (Comment 4 — "the segmenter segments each of them, so we can be more granular"). It is deliberately a separate code from `air_gap_reverberation` because that signal is *periodic* (an FFT comb peak) while near-field clutter is *high-frequency broadband* — different detector, different layer of the failure stack.

**Threshold.** Clutter-energy metric within the segmented near-field layers (skin → fat), **Threshold-tier B**.

**Measurements tainted.** Superficial geometry (`rf_thickness_mm`, `full_thickness_q50_mm`), and `rf_area_mm2` — the cross-sectional area depends on the superficial mask boundary that the clutter corrupts; all BDR.

**Override.** Operator confirms the near-field structure is anatomical.

#### `vessel_shadow_in_roi` — [Active]

**Meaning.** A small artery or calcified plaque casts a vertical shadow column downstream of itself.

**Detector (technical audit § 1, new mode).** Detect a **near-zero-intensity vertical column with no horizontal (TGC) pattern**. Mechanistically distinct from `dark_clip_high` (which is a full-frame gain-clip) and from TGC banding (horizontal). It will *mimic* `anisotropy_dropout_deep` to the deep-fascia detector — § 11 names the precedence rule.

**Threshold.** Column-darkness + verticality metric, **Threshold-tier B**.

**Measurements tainted.** Every *geometry and intensity* measurement computed in the shadowed column. **`muscle_fat_ratio` is exempt:** the vessel or calcification that casts the shadow sits *deep to* the subcutaneous-fat layer, so the shadow falls in the muscle and below — the fat-thickness input to `muscle_fat_ratio` is above the shadow's origin and untouched. (This exemption is stated here so the spec and the § 6.2 `vessel_shadow_in_roi` row — which blanks `muscle_fat_ratio` — agree; audit fix H4.)

**Override.** Operator confirms the dark column is anatomical or accepts the affected measurement.

#### `posterior_acoustic_enhancement` — [Named-deferred]

**Meaning.** Sound passing through a fluid / echo-poor region (e.g. a vessel) over-brightens the tissue beneath it — a regional, anatomy-located gain artifact, *not* the depth-uniform TGC banding.

**Detector.** Named for taxonomy completeness; rare at RF mid-thigh. *Owner: TBD; trigger: a v0.x detector pass.* Visual signature documented (a locally brighter vertical strip beneath an echo-poor structure) so an operator can flag it manually via the Phase-4 "Other" field in the meantime.

**Measurements tainted.** `int_mean`, `rf_echo` in narrow vertical strips.

#### `subq_fat_compressed` — [Active]

**Meaning.** Probe compression has flattened the subcutaneous fat layer; `muscle_fat_ratio` is biased and thicknesses are slightly underestimated.

**Detector.** Subcutaneous fat *is* a segmented class (`LABEL_FAT = 8`, [`labeling_app/measurements.py:36`](../labeling_app/measurements.py#L36)), so the *measurement* is **deterministic** (decision D8): the median fat-layer thickness across the central-zone columns is read straight from the mask. **But the hard part is not measuring fat thickness — it is deciding "compressed" (Comment 3 / Comment 20).** A thin fat layer on a single frame is ambiguous: it can mean probe compression *or* a genuinely lean patient, and one frame does not carry the information to tell them apart. v0.2 ships the single-frame threshold detector but treats its verdict as provisional (see *Measurements tainted*). **The real disambiguator is sweep-level:** subcutaneous-fat thickness that drops sharply across consecutive sweep frames at a roughly fixed anatomical point signals compression, whereas a uniformly thin layer across the sweep signals a lean patient. That sweep-consistency check is the named follow-on (§ 17 future-work register — the sweep-level workstream) and is out of v0.2's ratified per-frame scope.

**Threshold.** Median central-zone fat thickness in mm, **Threshold-tier B** — needs a clinical anchor or cohort distribution for the "compressed" cut-off (a 3 mm starting point is plausible but not physics-grounded). Do not deploy without § 11 calibration.

**Measurements tainted.** `muscle_fat_ratio` primarily. **`qmt_mm` / `star_ratio` are marked BDR — provisionally.** The mechanism is sound (probe compression shortens the measured fascia-to-femur distance), but the magnitude that would justify a hard BDR cell — "~1–3 mm QMT bias per 5 N extra force" — traces only to "Tyler et al. 2024" as cited in the technical audit, a reference with no verified bibliographic form (the Appendix A.2 citation gap). Until that citation is sourced or replaced, the `subq_fat_compressed → qmt_mm` / `star_ratio` cells are **provisional** (so marked in § 6.2): the BDR verdict rests on the mechanism, not on a verified effect size — audit fix M4. Other thicknesses are affected to a lesser, second-order degree.

**Override.** Operator confirms a genuinely thin fat layer (a lean patient), not compression. Log this as a *demographic* signal, not a quality fault.

### 5.3 — Tier-1 detectors (ultrasound-specific — [Conditional])

The three confidence-map detectors and `oblique_plane`'s parallelism component are **[Conditional]** per Akadir's ratified Tier-1 deferral (§ 2.3): they are deferred until the Step-2 cohort run shows Tier-0 misses clinically meaningful cases, and they require the one-page Tier-1 technical spec before any engineering begins.

#### `oblique_plane` — [Conditional]

**Meaning.** The probe is rotated off the muscle's long axis; fascia lines converge and fascicular striations go out of plane.

**Detector — and the v0 reuse that is dropped (technical audit § 3, highest-leverage change #3).** The v0 contract proposed reusing `QMT_FASCIA_SLOPE_MAX_ABS = 0.25` ([`labeling_app/measurements.py:50`](../labeling_app/measurements.py#L50)). **This is dropped.** Verbatim audit reasoning: *"QMT's slope check is on the deep fascia in the central zone where femur anchors; oblique-plane detection needs *parallelism between superficial and deep* fascia, not absolute slope of one. A 0.25 deep-fascia slope with a 0.25 superficial-fascia slope (both tilted same direction) means the muscle has a natural tilt, *not* probe rotation… Don't ship `oblique_plane` against the QMT threshold; either implement Radon now or hold the detector."* **v0.2 disposition: hold the detector** rather than ship an invalid reuse. The replacement is a Radon-transform parallelism check (compare the dominant orientations of the superficial and deep fascia; flag divergence) — Tier-1 work.

**Threshold.** Radon-orientation divergence, **Threshold-tier B**. *Condition for activation: the Radon component is implemented; owner: TBD per the Step-3 one-page spec; trigger: the Step-2 readout.*

**Measurements tainted.** `rf_thickness_*`, `qmt_mm`, `rf_area_*`, `full_*`, `int_cv_within`; **`rf_echo` is BDR**: off-axis insonation under-illuminates the specular deep fascia, biasing the ROI mean intensity. The "~5–10%" mean-intensity figure (technical-audit "flip #3") is an **order-of-magnitude estimate, not a measured value** — flagged as such here (audit fix M3) and to be replaced by the cohort-measured bias once the § 11 calibration runs. The BDR verdict itself rests on the *mechanism* — specular reflection is angle-dependent, so an off-axis plane genuinely under-lights the deep fascia — which is not in doubt; only the magnitude is provisional.

#### `anisotropy_dropout_superficial` / `anisotropy_dropout_deep` — [Conditional]

**Meaning.** A contiguous segment of one fascia darkens or disappears due to the angle-dependence of specular reflection; caliper placement there becomes ambiguous.

**Detector.** Class C — a Karamalis-style ultrasound confidence map (Karamalis et al. 2012, § 16), sampled in a ±N-pixel band around the superficial (or deep) fascia line; flag when the fraction of band columns with confidence below threshold exceeds M%.

**Threshold.** Confidence threshold + band-coverage %, **Threshold-tier B** — set after the confidence-map module exists.

**Measurements tainted.** `rf_thickness_mm` if a column hits the dropout; `qmt_mm` for deep dropout; `rf_area_mm2`, `full_*` if the dropout breaks the mask contour.

#### `deep_attenuation_high` — [Conditional]

**Meaning.** A deep / high-BMI patient: the beam attenuates before reaching deep structures; the deep aponeurosis and femur are faint.

**Detector.** Class C — confidence map in the deep half of the frame; flag when mean deep-half confidence is below threshold *and* the femur detector struggles.

**Precedence rule (technical audit).** `deep_attenuation_high` and `anisotropy_dropout_deep` overlap — one confidence map fires both. The precedence rule: **attenuation is global-deep; anisotropy is local-band.** If low confidence spans the whole deep half → `deep_attenuation_high`; if confined to a band segment → `anisotropy_dropout_deep`.

**Threshold.** Deep-half mean confidence, **Threshold-tier B**.

**Measurements tainted.** `qmt_mm`, deep `full_*`.

### 5.4 — Tier-2 detector (model-derived — [Active] in v0.2)

#### `segmentation_low_confidence` — [Active]

**Meaning.** The segmentation model itself is uncertain about the mask — a free signal that catches "weird" frames.

**Detector.** The detector reads the segmenter's per-pixel class probabilities (the softmax over classes) and computes the entropy of that distribution in a band around the mask boundary — high boundary entropy means the model itself is unsure where the muscle edge is.

**v0.2 promotion (Comment 2; segmenter feasibility audit, 2026-05-20).** The v0.1 framing — "a genuinely new code path" — over-stated the cost. Verified against the code: `predict_mask` ([`scripts/preflight_helpers/segmenter.py:209`](../scripts/preflight_helpers/segmenter.py#L209)) runs `out = learn.model(x)`, producing the full **logits tensor** `(1, n_classes, 512, 256)` on the GPU, then collapses it with `out.argmax(dim=1)` at line 224. The expensive object — the logits — *already exists in scope*; softmax is not "computed then discarded," it is simply one absent `out.softmax(dim=1)` call. Exposing the boundary-entropy signal is a **~15-LOC addition inside `predict_mask`**: softmax the logits, gather the boundary band, reduce to a per-pixel-or-scalar entropy, return it through an optional out-parameter — the idiom the codebase already uses for `final_u8_out` in `_load_or_predict_masks` ([`run_pipeline.py:222`](../scripts/preflight_helpers/run_pipeline.py#L222)), so **no caller breaks** and behaviour is byte-identical when the parameter is omitted. The reduction runs on-GPU before `out` leaves scope, so memory cost is negligible (a scalar, or a ~0.5 MB single-channel confidence map — never the ~5 MB full tensor). Signal-exposure is therefore `[Active]` for v0.2 implementation.

**Surfacing (Comment 2).** Because the detector can return a *per-pixel* confidence map and not only a scalar, the QA UI can render it as a heatmap and aggregate confidence by anatomical region (superficial fascia / muscle body / deep fascia / femur) — Akadir's request that this "most analytically rigorous signal" be shown, not buried. That surfacing is a v0.2 UI item, specified with the other QA components in § 8; the detector's job here is to expose the map.

**Threshold.** Mask-boundary entropy cut-off, **Threshold-tier B** — cohort-calibrated, exactly like `wrong_site` and `motion_blur`. This is the one genuine gate: the *signal* is `[Active]` (the ~15-LOC change above), but the *detector deploys* only once § 11 calibration sets the entropy threshold against labelled data — signal-exposure is a few hours, a trustworthy threshold waits on the calibration substrate. No guessed entropy number ships (§ 8.6 pre-calibration UI state).

**Measurements tainted.** All geometry (mask-derived).

**Override.** Operator visually confirms the mask is correct despite model uncertainty.

### 5.5 — Detector entry point and the calibration cross-reference

A new module — `labeling_app/image_quality.py` — exposes `compute_image_quality_flags(img_gray, mask)` which runs every [Active] detector and returns `{'flags': tuple[str, ...] ordered by IMAGE_QUALITY_FLAG_PRIORITY, 'detector_metadata': dict}` (raw scores, for the UI rationale and for calibration). The backend wiring is § 7; the UI surfacing is § 8.

Every **Threshold-tier B** value above ships as a *starting point only* and is set properly by the five-step calibration process — **see § 11.2 (the calibration steps) and § 11.4 (the calibration script)**. No Threshold-tier B detector is deployed against an un-calibrated threshold; § 8.6 specifies the "ungraded — pending calibration" UI state for that case.

---

## § 6 — Per-measurement validity rule and gating table

This section turns the per-frame flags from § 5 into a per-measurement verdict — `valid`, `borderline`, or `invalid` — and then into the single aggregate badge the operator sees. It is the bridge from "what is wrong with the frame" (§ 4–§ 5) to "what the operator and the aggregation pipeline do about it" (§ 7–§ 8).

### 6.1 — The `image_quality_tier()` function

The validity rule is a function modelled on the existing `qmt_tier()` ([`scripts/preflight_helpers/aggregation.py:80`](../scripts/preflight_helpers/aggregation.py#L80) — note the v0 contract cited `:84`; the function starts at `:80`), but applied **per measurement** rather than once per frame:

```python
def image_quality_tier(measurement: str, flags: tuple[str, ...]) -> str:
    """
    Return one of 'valid' | 'borderline' | 'invalid' for a single measurement,
    given the frame's set of image-quality flags.

    Each measurement has a defined set of flags that INVALIDATE it and a set
    that DEGRADE it to borderline. A flag in neither set is transparent to
    that measurement. The mapping is the gating table in § 6.2.
    """
```

The mapping the function encodes is the gating table below. The rule per cell: if any flag set on the frame is `INV` for the measurement → `invalid`; else if any is `BDR` → `borderline`; else → `valid`.

**`image_quality_tier()` is the raw per-measurement map and is *not* modified by the compound false-positive discipline** (§ 6.3, § 11.5). A single `INV` flag still makes that measurement `invalid` here — because several flags are legitimate single-detector killers (`femur_out_of_frame` alone genuinely kills `qmt_mm`; there is no "second detector" to wait for). The two-detector-concurrence rule applies one layer up, at the § 6.3 *aggregate badge*, which is where false-positive contamination must be controlled — not by weakening this per-measurement verdict, which also feeds the pooling exclusion in § 7.3.

### 6.2 — The gating table

**How to read a cell.** `INV` = this flag invalidates this measurement. `BDR` = this flag degrades this measurement to borderline. Blank = this flag is transparent to this measurement. Every cell below is derived from, and cross-checked against, the "measurements tainted" line of the corresponding § 5 detector spec.

The eight columns are the seven measurements surfaced on the result card (§ 3.2) plus `rf_echo`, which stands in for the intensity-supporting family (`int_mean`, `int_std`, `int_median`, `int_entropy`) — they all gate identically to `rf_echo`. `full_area_mm2` gates identically to `rf_area_mm2` (both are full-cross-section area features) and is not shown as a separate column.

| flag ↓ \ measurement → | rf_thickness_mm | qmt_mm | star_ratio | full_thickness_q50_mm | rf_area_mm2 | int_cv_within | rf_echo | muscle_fat_ratio |
|---|---|---|---|---|---|---|---|---|
| `wrong_site` | INV | INV | INV | INV | INV | INV | INV | INV |
| `segmenter_mislabel_wrong_class` ◇ | INV | INV | INV | INV | INV | INV | INV | INV |
| `femur_out_of_frame` | | INV | INV | | | | | |
| `femur_off_central_zone` | | BDR | BDR | | | | | |
| `edge_clip_lateral` ‡ | | | | | INV | | | |
| `edge_clip_vertical` | INV | BDR | BDR | INV | | | | |
| `saturation_high_global` | | | | | | INV | INV | |
| `saturation_high_regional` | BDR | | | | | BDR | | |
| `dark_clip_high` | | BDR | BDR | | | INV | INV | |
| `tgc_banding` | | | | | | BDR | BDR | |
| `motion_blur` | INV | INV | INV | INV | INV † | BDR | | |
| `air_gap_reverberation` § | INV | INV | INV | INV | INV | BDR | BDR | INV |
| `near_field_clutter` | BDR | | | BDR | BDR | | | |
| `vessel_shadow_in_roi` § | INV | INV | INV | INV | INV | BDR | BDR | |
| `posterior_acoustic_enhancement` ◇ | | | | | | BDR | BDR | |
| `subq_fat_compressed` | | BDR ¶ | BDR ¶ | | | | | INV |
| `oblique_plane` ◆ | INV | INV | INV | INV | INV | BDR | BDR ◊ | |
| `anisotropy_dropout_superficial` ◆ | BDR | | | BDR | BDR | | | |
| `anisotropy_dropout_deep` ◆ | BDR | INV | INV | BDR | BDR | | | |
| `deep_attenuation_high` ◆ | | BDR | BDR | BDR | | | | |
| `segmentation_low_confidence` | BDR | BDR | BDR | BDR | BDR | | | BDR |
| `probe_orientation_flipped` ◇ | | | | | | | | |

**Cell footnotes — the four technical-audit flips and the C11 coverage rule, all reconciled:**

- **‡ `edge_clip_lateral` — coverage-limitation rule (Akadir redline C11).** It is `INV` for `rf_area_mm2` (and `full_area_mm2`) *only* — the measurements whose definition needs the complete in-frame RF cross-section. Every other cell in the row is blank: lateral truncation does **not** invalidate QMT, STAR, central RF thickness, or intensity when their anchor structures remain visible. This overrides the technical audit's proposed uniform N-tiering. Rationale: § 5.2 `edge_clip_lateral`.
- **† `motion_blur` → `rf_area_mm2` = INV** (technical-audit flip #2, was `BDR` in v0). Motion smears the mask contour by ≥3 px; on a ~150-px-wide RF that is a ~4% area bias, exceeding clinical tolerance for cohort placement.
- **§ `air_gap_reverberation` and `vessel_shadow_in_roi` — column-set-based** (technical-audit flip #1). These detectors return the *affected column set*. Geometry measurements computed in those columns are `INV` (zero signal there) — including `muscle_fat_ratio`, which is geometry-derived and whose subcutaneous-fat input sits in the top-of-frame zone an air gap corrupts directly; intensity statistics (`int_cv_within`, `rf_echo`) are `BDR`. The table shows the verdict *when the affected columns overlap the ROI by more than the calibrated fraction*; below that overlap, the flag is transparent. This is more precise than v0's "BDR uniformly." (`vessel_shadow_in_roi` blanks `muscle_fat_ratio` — a thin vessel shadow does not reach the fat layer.)
- **◊ `oblique_plane` → `rf_echo` = BDR** (technical-audit flip #3, was blank in v0). Off-axis insonation under-illuminates the specular deep fascia, biasing the ROI intensity mean; the eroded ROI contains a fraction of that under-lit band, so a blank cell would wrongly assert no effect. The magnitude is provisional — the "~5–10%" figure is an order-of-magnitude estimate pending § 11 calibration (§ 5.3). *(v0.2 re-keyed this footnote from ◆ to ◊ — ◆ was colliding with the `[Conditional]`-marker symbol below.)*
- **¶ `subq_fat_compressed` → `qmt_mm` / `star_ratio` = BDR — provisional** (technical-audit bonus flip, was blank in v0). Probe compression shortens the *measured* fascia-to-femur distance; the mechanism is sound, but the magnitude ("~1–3 mm QMT bias per 5 N extra force") traces only to an unverified "Tyler et al. 2024" (the Appendix A.2 citation gap). The cell is **provisional** until that citation is sourced or replaced — the BDR verdict stands on mechanism, not on a verified effect size (§ 5.2; audit fix M4).
- **◇ marks a `[Named-deferred]` flag** — `segmenter_mislabel_wrong_class`, `posterior_acoustic_enhancement`, `probe_orientation_flipped`. No detector fires these in v0.2; their rows are specified so the gating semantics are ready when the detector lands. `probe_orientation_flipped` has an all-blank row because no current measurement is directional — STAR and the thickness/area measurements are orientation-invariant; the row goes live only when a directional measurement (e.g. fascicle angle) is added.
- **◆ marks a `[Conditional]` Tier-1 flag** — `oblique_plane`, `anisotropy_dropout_*`, `deep_attenuation_high`. Their detectors are deferred per § 2.3 / § 5.3; the gating rows are specified now so activation is a detector-side change only. (`segmentation_low_confidence` carried ◆ in v0.1; v0.2 promotes it to `[Active]` — § 5.4 — so its row is now unmarked and live in v0.2.)

### 6.3 — The aggregate frame tier

The picker badge (§ 8) shows one aggregate tier per frame, derived from the per-measurement tiers:

- **Valid** — every headline-4 measurement (`star_ratio`, `full_thickness_q50_mm`, `int_cv_within`, `rf_thickness_mm`) is `valid`, **and** at least 60% of the **eight gating-table measurements** (the § 6.2 columns — `rf_thickness_mm`, `qmt_mm`, `star_ratio`, `full_thickness_q50_mm`, `rf_area_mm2`, `int_cv_within`, `rf_echo`, `muscle_fat_ratio`, each counted once) are `valid` — i.e. at least 5 of the 8.
- **Borderline** — at least one headline-4 measurement is `borderline`, and none is `invalid`. (Also reached when a non-headline measurement is `borderline` or `invalid` but all headline-4 are `valid`.)
- **Invalid** — any headline-4 measurement is `invalid`.

**Why the headline-4 gate hard while secondary measurements do not.** The headline-4 are the measurements shown on the operator's result card (§ 3.2) — a failure in one is directly operator-visible and is what gets paper-reported, so it must drive the aggregate tier. A degraded *secondary* measurement is real information but is not a headline number, so it should not by itself condemn the frame.

**Why 60%, and over what** (clarity-audit derivation, sharpened in v0.2 — audit fix H3): the denominator is the **eight § 6.2 gating-table columns**, each counted once — `rf_echo` already stands in for the whole `int_*` family and `rf_area_mm2` for `full_area_mm2`, so there is no larger hidden set. 60% of 8 is 4.8, so the rule requires at least 5 of the 8 `valid`. The 60% floor is chosen so that *any single secondary measurement can fail without demoting the whole frame* — a frame should not drop a tier merely because one non-headline measurement is degraded. The exact figure is a starting point and is **calibrated in Step 2** (§ 14) against the cohort distribution; `[Threshold-tier B]`.

**Compound false-positive discipline.** With 14 Tier-0 detectors each at imperfect specificity, a *clean* frame can still trip one or more false flags — left unmanaged, most clean frames would carry a spurious badge and the operator gets badge-fatigue. The aggregate tier is therefore computed under the compound-FPR discipline specified in **§ 11.5** (the v0.2 operating rule: two-detector concurrence before an `INV`-driven aggregate badge; the math and alternatives are in § 11.5). This control acts *here*, at the aggregate step — **not** inside § 6.1's per-measurement `image_quality_tier()`, which stays the raw map (see the note in § 6.1). § 6 defines the *mapping*; § 11.5 defines the *false-positive control* applied on top of it.

### 6.4 — Tier semantics — what each verdict means downstream

- **Valid** — the measurement is used in pooling, cohort placement, and paper reporting with no caveat.
- **Borderline** — the measurement is still used, but the value carries a flag in every downstream report; cohort placement carries a "borderline-input" annotation; aggregation prefers `valid` frames when enough are available (the existing tier-preferring, never-pool-across-tiers discipline of `_aggregate_qmt`, generalised — § 7).
- **Invalid** — the measurement is **not** pooled into the aggregate. Critically, per Transparency Rule 1 (§ 9), the frame is **not** hidden and the value **is still computed and displayed** — it simply does not contribute to the aggregate. "Invalid" means "excluded from the pooled number," never "erased from view."

---

## § 7 — Backend integration

This section specifies how the QA layer plugs into the existing assessment pipeline: where the labels and grades flow (§ 7.0), and the six concrete code touchpoints (§ 7.1–§ 7.6). The UI surfacing of all this is § 8; § 7 is the data and control plumbing beneath it.

### 7.0 — The label pipeline overview — capture → storage → calibration → learned grader → wire-back

A recurring question through the design reviews was: *where do the labels and grades actually go, who keeps them, and how do they become a working detector?* This subsection answers it end-to-end. There are two distinct data tracks (the D4-vs-D7 distinction from § 2.1): an **inference track** (detectors auto-grade every frame, no human) and a **label-capture track** (humans grade a sampled set, producing the ground truth that calibrates the detectors). They meet at calibration.

**Stage 1 — Capture.** During Phase-4 frame review ([`p04_frame_review.py`](../labeling_app/assessment_pages/p04_frame_review.py)), the operator's gate answers, structured drop-reason checkboxes, and any QA overrides are held in `st.session_state.gates`, keyed by frame. § 7.4 specifies the form change. The algorithmic grades (the inference track) are computed by the detector module (§ 7.2) and pre-fill the form (§ 8.3).

**Stage 2 — Storage.** When the operator clicks "Accept run," the gates are embedded into the run record and persisted. **Verified storage paths (2026-05-20):** the finalised record is appended to **`data/runs.jsonl`** — an append-only ledger, one JSON object per run — and copied to **`data/runs/<run_id>/record.json`**. In-flight runs live as drafts at **`data/runs_drafts/<run_id>.json`** and are auto-swept after 24 hours. As of 2026-05-20 the ledger holds 8 finalised runs across 4 cohort IDs (ACL762, AKA9001, BHW541, DFN534).

**Stage 3 — Calibration consumes the ledger.** The five-step calibration process (§ 11.2) reads `data/runs.jsonl`, and pairs every frame's detector scores (inference track) with its structured Phase-4 labels (label-capture track). This is where the two tracks meet. The calibration script (§ 11.4) reads the ledger and the production-feedback parquet files (§ 12.2) automatically — no manual export step.

**Stage 4 — Learned-grader training.** The Tier-2 learned grader (Build-sequence Step 5, § 14) draws its training pairs from the same ledger. It does not train until a per-reason-code minimum is met (≥200 labelled positives per code; § 11, § 14) — `[Conditional]`.

**Stage 5 — Wire-back.** The per-measurement tiers feed the aggregation hook (§ 7.3), which excludes `invalid`-tier frames from pooled measurements. This closes the loop: a graded frame's verdict actually changes the assessment number.

**Three constraints that must be named (verified properties of the current ledger):**

- **Gates are read-only after Accept.** Once a run is finalised into `data/runs.jsonl`, its gates cannot be edited — the ledger is append-only. Consequence: any future retrospective re-labelling capability (e.g. re-coding historical free-text drop reasons into the structured taxonomy, § 11) cannot mutate the ledger; it must write a *separate side-table* joined on `frame_base`. This is a `[Named-deferred]` constraint — *owner: TBD; trigger: the § 11 retrospective re-coding task.*
- **PHI guardrail.** The run record carries a `_phi` sub-block (patient name, numeric ID) that is gitignored and local-only and never reaches any UI. Calibration analytics read only `study_code` and the parsed demographics — never `_phi`.
- **No per-study isolation of gates.** Gates live *inside* run records and are queried by `run_id`, not `study_code`. A study with multiple runs has independent per-run gate sets. Cross-study label pooling for calibration is therefore unguarded — calibration must dedupe deliberately if a frame appears in multiple runs.

**One circularity to guard against** (path-to-rigor finding): Phase-4 pre-fills its checkboxes from detector output (§ 8.3), and § 11.2 then calibrates those same detectors against the Phase-4 labels. An operator who accepts a pre-fill unchanged produces a label that simply echoes the detector — the detector would "validate against itself." The mitigation lives in § 11.2: **calibration excludes frames where the operator accepted the pre-fill unchanged**, counting only frames the operator actively edited or confirmed. Named here because it is a property of the label pipeline; specified in § 11.

### 7.1 — The `ImageQualityFeatures` dataclass

**Q3 resolved (Comment 26): the five image-quality fields live in a *separate* `ImageQualityFeatures` dataclass, not as an extension of `FrameFeatures`.** The v0.1 spec extended `FrameFeatures` "because it is simpler"; Comment 26 asked for the call to be made on SWE principles, practicality, and auditability — and those favour separation: the QA schema versions independently (the `contract_version` field, § 12.3), the QA fields churn on a different cadence from the measurement fields, and a self-contained QA record is cleaner to audit and to re-grade. The new dataclass joins to the per-frame record on `frame_base`.

```python
@dataclass(frozen=True)
class ImageQualityFeatures:
    """Per-frame QA grade. Lives beside FrameFeatures, joined on frame_base.
    Produced by the QA layer (§ 5.5, § 7.2)."""
    frame_base: str                                        # join key to FrameFeatures
    image_quality_validity_flags: tuple[str, ...]          # EVERY flag raised, IMAGE_QUALITY_FLAG_PRIORITY order
    image_quality_pending_calibration_flags: tuple[str, ...]  # the un-calibrated subset of the above (gate H1)
    image_quality_detector_metadata: dict                  # raw per-detector scores, for UI rationale + calibration
    image_quality_tier_per_measurement: dict[str, str]     # measurement -> 'valid'|'borderline'|'invalid'
    image_quality_aggregate_tier: str                      # the DEPLOYED verdict — § 6.3 over the calibrated flags
    image_quality_contract_version: str                    # the contract version this grade was produced under (§ 12)
```

The QA layer produces one `ImageQualityFeatures` per frame; aggregation (§ 7.3) joins it to `FrameFeatures` by `frame_base`. Wherever the rest of this plan says "the `image_quality_*` fields," read it as "the `ImageQualityFeatures` record joined to that frame" — Q3 moved them into this self-contained dataclass without changing any field's meaning; the one later addition is the H1 field below.

**Deployed-vs-raw tier semantics — the `image_quality_pending_calibration_flags` field (Build-Step 2 item-10a gate H1, ratified 2026-05-22).** `image_quality_aggregate_tier` is the *deployed* verdict: § 6.3 applied to the **calibrated** flag subset only. Build-Step 2's Finding B established that an un-calibrated Threshold-tier B detector — today 11 of 15, `wrong_site` (which fires on 79.8 % of the cohort) among them — is fully recorded but never drives a hard verdict. The consequence is a dual semantic: a frame can read `aggregate_tier = "valid"` while `image_quality_validity_flags` visibly carries `wrong_site`. Item 10's UI cannot render a badge over an ambiguous field, so the schema makes the split explicit: `image_quality_pending_calibration_flags` is the subset of `image_quality_validity_flags` whose detector is still pending calibration — the flags that fired, are recorded, but are excluded from the verdict. It is an additive, back-compatible field (a pre-H1 record loads it as `()`), and a *materialized view* — `calibrated_flags()` (§ 8.6 / § 11.1) derives the same partition from `validity_flags` + `detector_metadata` — persisted so a consumer reads the dual semantic directly rather than re-deriving it. `CONTRACT_VERSION` is unchanged: the field adds no information the record did not already carry, and no existing field's meaning changes. The operator surface (§ 8.3.1) renders the deployed tier plus a "+N pending" count from this field; Build-Step 2 item 10b makes it the operator-reactable calibration surface. Akadir ratified the additive field over a rename of `image_quality_aggregate_tier` (breaks every existing ledger record) and over a companion raw-aggregate-tier field (a second verdict the operator must reconcile, and one that still names no detector).

**Name-collision note — resolved in Build-Step 1.** `FrameFeatures` has a field `drop_reasons` (field 23 — the *automated*, mask-derived measurement-validity tuple); the Phase-4 gates dict (§ 7.4) needed a *separate* structured operator-entered reason set. These are genuinely different data (machine-computed mask validity vs in-session human QA input), so — per this note's original instruction to rename if distinct — the Build-Step-1 implementation gave the gate key a distinct name: **`drop_reason_codes`** (pairing with `drop_reason_other`). `FrameFeatures.drop_reasons` is untouched, and the QA flags live in `ImageQualityFeatures.image_quality_validity_flags`; no two layers now share the name. The collision did not ship un-noticed.

### 7.2 — Detector entry point

A new module — **`labeling_app/image_quality.py`** — houses the detectors. Its entry point (already sketched in § 5.5):

```python
def compute_image_quality_flags(img_gray: np.ndarray, mask: np.ndarray) -> dict:
    """Run every [Active] detector on the _final.png grayscale + segmentation mask.
    Returns {'flags': tuple[str, ...] ordered by IMAGE_QUALITY_FLAG_PRIORITY,
             'detector_metadata': dict of raw per-detector scores}."""
```

Naming discipline (brainstorm § 5 Item 1): the module is **not** placed in, and does not share a name with, the existing `rf_quality` package — `rf_quality` is a measurement/geometry library that does not do quality assessment despite its name. Putting QA code there would be a lasting source of confusion.

### 7.3 — Aggregation hook

The existing `_aggregate_qmt` function ([`scripts/preflight_helpers/aggregation.py:80`](../scripts/preflight_helpers/aggregation.py#L80)) already implements the exact discipline the QA layer needs: **prefer the better tier, fall back to the worse, never pool across tiers**. The QA layer generalises this pattern — a new `_aggregate_with_image_quality`, per measurement, partitions the feeding frames by `image_quality_tier_per_measurement[measurement]` and applies the full three-tier rule § 6.4 specifies:

1. **`invalid`-tier frames are excluded** from that measurement's pool outright — never pooled, though the frame and its value stay fully visible (§ 6.4, Transparency Rule 1).
2. **`valid`-tier frames are preferred:** when enough are available — the same minimum-frame logic `_aggregate_qmt` already applies — the pool is built from `valid` frames alone.
3. **`borderline`-tier frames are the fallback:** only when `valid` frames are too few are `borderline` frames included, and the aggregate then carries the "borderline-input" annotation § 6.4 requires.

It never silently pools `valid` and `borderline` together and never crosses into `invalid` — the same prefer-better / fall-back-to-worse / never-pool-across-tiers discipline `_aggregate_qmt` runs, with `image_quality_tier_per_measurement` as the second tier source. This is Build-sequence Step 6 (§ 14); the integration is clean precisely because the tier-preferring aggregation already exists. *(v0.2 — § 7.3 now states the full three-tier behaviour, not just `invalid`-exclusion, to match § 6.4; audit fix M5.)*

### 7.4 — Phase-4 form change

Edit [`p04_frame_review.py`](../labeling_app/assessment_pages/p04_frame_review.py). Two changes:

**(a) The gates dict grows.** The current initialiser at [`p04_frame_review.py:108-111`](../labeling_app/assessment_pages/p04_frame_review.py#L108) has four keys (`usable`, `segmentation_perfect`, `use_for_analysis`, `drop_reason`). It becomes:

```python
gates = st.session_state.gates.setdefault(fb, {
    "usable": None, "segmentation_perfect": None, "use_for_analysis": None,
    "drop_reason_codes": set(),  # NEW — structured checkboxes from IMAGE_QUALITY_FLAG_PRIORITY
    "drop_reason_other": "",     # NEW — free text, for reasons not yet in the taxonomy
    "qa_overrides": dict(),      # NEW — operator overrides of algorithmic flags (schema in § 7.6)
})
```

**(b) The drop-reason input is restructured.** The single free-text `st.text_input("Drop reason (optional)", ...)` at [`p04_frame_review.py:150-153`](../labeling_app/assessment_pages/p04_frame_review.py#L150) is replaced by a **structured checkbox group** whose options are the reason codes from `IMAGE_QUALITY_FLAG_PRIORITY` (§ 5.1), pre-filled from the algorithmic flags, plus a free-text "Other" field (`drop_reason_other`) for reasons not yet in the taxonomy. The "Other" entries are the taxonomy-growth channel (§ 12.2 Channel C). This is Build-sequence Step 1 (§ 14); the UI of the checkbox group is § 8.3.

**(c) "Lock gates" is gated on review completeness.** The Phase-4 "Lock gates + run assessment" action is disabled until **every** picked frame is decided Yes/No on every gate it was shown — no gate left on the "—" pending state — **and** every frame gated `Usable = No` carries a drop reason: at least one structured code, or free text in `drop_reason_other`. A frame dropped with no recorded "why" is precisely the gap Build-Step 1 exists to close (§ 15.2 — "the structured checkbox capture exists so this 'why' is never lost again"); an optional capture would silently re-open it. The operator is never dead-ended — "Other" accepts any reason not yet in the taxonomy, so the requirement is always satisfiable in one click or a short note.

### 7.5 — Picker render change

Edit [`p03_frame_picker.py`](../labeling_app/assessment_pages/p03_frame_picker.py) to add a per-thumbnail QA badge rendering `image_quality_aggregate_tier`, plus the click-to-expand drilldown. The badge extends the existing `_FEMUR_BADGE` pattern ([`p03_frame_picker.py:39-43`](../labeling_app/assessment_pages/p03_frame_picker.py#L39)). The full component spec is § 8.3.

### 7.6 — The `qa_overrides` schema and why it is load-bearing

When an operator disagrees with an algorithmic flag — keeps a frame the system flagged `invalid`, or scopes an override to specific measurements — that disagreement is captured in the `qa_overrides` dict on the gates record:

```python
gates[fb]["qa_overrides"] = {
    "qmt_mm": {                              # keyed by measurement
        "algo_tier": "invalid",              # what the detectors said
        "algo_flags": ("femur_out_of_frame",),  # the flags that drove it
        "human_decision": "use_anyway",      # 'use_anyway' | 'drop' | 'use_for_geometry_only'
        "rationale": "femur visible at column 240, sufficient for the QMT span",
        "timestamp_utc": "2026-05-20T14:23:00Z",
        "contract_version": "v0.2",
        "operator_id_hash": "...",
    },
    # ...one entry per measurement the operator overrode
}
```

**Why this schema is load-bearing — the explicit dependency** (the prior design draft asserted "blocks Step 1 and Step 5" without saying *how*; here is the mechanism):

- **Step 1 (label capture) depends on it** because § 11.2's calibration substrate reads `qa_overrides[measurement]["human_decision"]` paired with `["algo_tier"]` to build the operator-versus-algorithm agreement matrix — the data from which every detector's sensitivity and specificity is computed. Without `human_decision` and `algo_tier` persisted, the calibration has no labelled comparison and § 11 cannot run.
- **Step 5 (learned grader) depends on it** because the Tier-2 grader trains on `algo_flags` as input features and `human_decision` as the supervised label. Without these fields in the ledger, the grader has no training pairs.

So the schema is not bookkeeping — it is the literal input to the two data-driven stages. It is also the source of § 12.2's Channel-A feedback parquet. The `contract_version` field is what lets a historical override be re-interpreted under a later contract (§ 12.3).

### 7.7 — Cache invalidation for the QA grades

The QA grades must be recomputed when their inputs change. The pipeline's existing signature-based cache-invalidation pattern is the model — **verified location (2026-05-20): the mask-cache signature logic is [`p04_frame_review.py:67-71`](../labeling_app/assessment_pages/p04_frame_review.py#L67) and the run-cache signature is [`p05_assessment_view.py:64-67`](../labeling_app/assessment_pages/p05_assessment_view.py#L64)** (an earlier draft mis-cited `assessment_app.py:68-71`, which are merely state initialisers — corrected here). The QA layer adds a `qa_input_sig` = a hash of `(mask signature, contract version, detector-config hash)`; when it changes, the cached grades are dropped and re-computed. Performance budget and caching detail are in § 8.7.

---

## § 8 — UI/UX integration design

This section is a load-bearing peer of the backend specification, not an afterthought. The governing principle (Akadir, 2026-05-19): *"something we can't or don't show is something that doesn't exist."* The backend rigor of § 4–§ 7 produces operator value only if the operator can see the grade, see *why*, and act on it. § 8 specifies that surface with the same precision § 5 specifies detectors — each component below carries an interface contract, a layout, an accessibility surface, and its error and loading behaviour.

### 8.0 — Design principles

Four principles bind every component in § 8.3. They sit on top of the four transparency rules (§ 9), which they operationalise.

**(1) The explainability primitive — the verdict-reason-drilldown triple.** No QA verdict ships alone. Every surface that shows a QA grade shows three things together: **(a)** the verdict (a `valid` / `borderline` / `invalid` / `ungraded` badge), **(b)** the dominant reason (the highest-priority flag from `IMAGE_QUALITY_FLAG_PRIORITY`, § 5.1), and **(c)** a click-to-expand "why" — the detector that fired, the threshold it tripped with the real number, the measurements tainted, and a link to the calibration provenance. A bare verdict the operator cannot interrogate is not actionable; the triple is the minimum unit. This is why a badge alone (verdict only) is never sufficient — it must always pair with reason and drilldown.

**(2) Accessibility is specified, not assumed.** Every status badge carries an ARIA label (the screen-reader text alternative — see § 0.3) so the visual colour/symbol is not the only channel. The drilldown panel is keyboard-navigable (`Tab` to traverse, `Enter` to expand, `Esc` to close, arrow keys to move between frames). Any purely visual abstraction — e.g. the three-dot structure-group indicator — carries a hidden text equivalent. Colour is never the sole carrier of meaning: every tier has a colour *and* a symbol *and* a word.

**(3) Performance budget.** Auto-grading every frame (decision D4) means running 14 Tier-0 detectors over every frame of a sweep. Budget: **≤ 2 seconds for a 30-frame sweep on the DGX**, **≤ 5 seconds on a modest laptop deployment**. If a grading pass would exceed the budget, the picker shows a progress indicator (§ 8.3.7) rather than blocking silently. Caching keeps re-grades near-instant (§ 8.7).

**(4) Iteration discipline.** Per decision D5, the contract carries the full rigor; the UI is a *calibrated summarisation* of it. v0.2 ships the thin, minimum-viable surface specified here, and UI depth iterates from observed operator reactions — never from speculation. Any § 8 component tagged `[Future-direction]` does not ship in v0.2; the § 8.8 table is explicit about what ships and what waits.

### 8.1 — Operator journey: before QA, and after

The assessment workflow is five phases (§ 1.1). The QA layer is **additive** — it overlays the existing five-phase skeleton, changing no phase's fundamental purpose. The before/after, phase by phase:

| Phase | Before QA (today) | After QA (v0.2) |
|---|---|---|
| **1 — Study select** | Operator picks a study. | Unchanged — QA is per-frame, not per-study. |
| **2 — Model select** | Operator loads a segmentation model bundle. | Unchanged, except the loaded contract version is stamped for the ledger (§ 7, § 12.3). |
| **3 — Frame picker** | A thumbnail grid; each thumbnail carries only a femur-tier badge (the existing `_FEMUR_BADGE`, 3-state). Operator picks up to 9 frames by eye. | Each thumbnail *also* carries a **QA aggregate badge** (4-state: valid/borderline/invalid/ungraded). Opt-in **filter and sort** controls appear in the sidebar. Clicking a thumbnail opens a **drilldown panel** with the per-measurement breakdown. The operator still picks every frame by hand — QA informs, never auto-picks. |
| **4 — Frame review** | Three human gates (usable? / segmentation perfect? / use-for-analysis?) via radio buttons; a free-text "drop reason" box. | Gate 1 (usable?) **pre-fills** from the QA verdict, with the reasoning shown inline and an explicit override path. The free-text drop-reason box becomes a **structured checkbox group** pre-filled from the flags. A "QA notes for this frame" panel and an **override action** are added. |
| **5 — Assessment view** | The result card: headline-4 measurements, secondary-3, cohort placement box-plots, run-comparison panel, a deterministic story caption. The QMT provenance gate already shows a green/orange/red badge. | The headline cards gain an inline **QA badge** beside the existing QMT-provenance badge. The cohort-placement panel gains a **QA caveat** when a feeding frame was borderline/invalid. The run-history panel gains a **QC-status column**. |

A reader from outside the project can read this table alone and understand exactly what the QA layer changes: it adds visibility and an override path at the picker and review phases, and an honest caveat at the result phase. It removes nothing and hides nothing.

### 8.2 — Per-phase integration spec

For each phase: what stays, what v0.2 adds, what is deferred. File:line anchors are the verified 2026-05-20 set.

**Phase 1 — `p01_study_selector.py`.** *Stays:* everything. *Adds:* nothing. *Deferred:* nothing. QA does not touch study selection.

**Phase 2 — `p02_model_selector.py`.** *Stays:* the bundle/model load. *Adds:* the active `CONTRACT_VERSION` (§ 12.3) is recorded into session state so every grade and every ledger row is stamped with the contract version it was produced under. *Deferred:* nothing.

**Phase 3 — `p03_frame_picker.py`.** *Stays:* the thumbnail grid, the existing `_FEMUR_BADGE` ([`p03_frame_picker.py:39-43`](../labeling_app/assessment_pages/p03_frame_picker.py#L39)) — the femur-tier signal and the QA aggregate signal are complementary and both shown — and the manual anchor-marking flow. *Adds:* the QA aggregate badge per thumbnail (§ 8.3.1), the filter/sort controls (§ 8.3.3), the drilldown panel on click (§ 8.3.2), and the auto-grade progress indicator (§ 8.3.7). *Deferred:* the diversity-aware "recommend N best frames" star — `[Future-direction]`, Build-sequence Step 7.

**Phase 4 — `p04_frame_review.py`.** *Stays:* the three-gate structure via `_gate_block` ([`p04_frame_review.py:106-157`](../labeling_app/assessment_pages/p04_frame_review.py#L106)) — pre-fill is a *suggested* value, never an auto-decision (Transparency Rule 4). *Adds:* Gate-1 pre-fill with inline reasoning (§ 8.3.4); the structured drop-reason checkboxes replacing the free-text box at [`p04_frame_review.py:150-153`](../labeling_app/assessment_pages/p04_frame_review.py#L150) (§ 8.3.5); the override action with `qa_overrides` capture (§ 8.3.6); and a "QA notes for this frame" collapsible attached after the per-frame QMT tier display at [`p04_frame_review.py:255-260`](../labeling_app/assessment_pages/p04_frame_review.py#L255). *Deferred:* nothing material.

**Phase 5 — `p05_assessment_view.py`.** *Stays:* the headline-4 stack ([`p05_assessment_view.py:663-666`](../labeling_app/assessment_pages/p05_assessment_view.py#L663)), the secondary-3 expander, the cohort box-plots (`_cohort_box_figure`, [`:275-349`](../labeling_app/assessment_pages/p05_assessment_view.py#L275)), the deterministic story caption, and the existing QMT-provenance badge (`_qmt_provenance_note`, [`:232-261`](../labeling_app/assessment_pages/p05_assessment_view.py#L232)) — the QA badge sits *beside* the provenance badge, not replacing it. *Adds:* the per-headline QA badge on the card row ([`:663-666`](../labeling_app/assessment_pages/p05_assessment_view.py#L663)); a QA caveat line in the cohort-placement panel ([`:673-712`](../labeling_app/assessment_pages/p05_assessment_view.py#L673)) when any feeding frame was borderline/invalid; a QC-status column in the run-history panel ([`:715-717`](../labeling_app/assessment_pages/p05_assessment_view.py#L715), expander defined in `_run_comparison_panel` at `:506`). *Deferred:* nothing material.

### 8.3 — Component specifications

Nine components. Each has an **interface contract** (inputs → outputs, side effects), a **layout** (ASCII mockup where spatial arrangement matters, pseudocode where the API matters), an **accessibility surface**, and **error/loading behaviour**.

#### 8.3.1 — `qa_aggregate_badge`

*Purpose.* The per-thumbnail verdict in Phase 3. Extends the existing `_FEMUR_BADGE` markdown-dict pattern.

```
Contract:
  qa_aggregate_badge(aggregate_tier, dominant_reason) -> markdown str
    aggregate_tier  : 'valid' | 'borderline' | 'invalid' | 'ungraded'
    dominant_reason : the highest-priority flag, or None
  side effects: none (pure render)
```

Layout — the four states, in the `_FEMUR_BADGE` visual idiom:

```
:green[● valid]                      :orange[● borderline · edge_clip_lat]
:red[● invalid · femur_out_of_frame]  :gray[○ not yet graded]
```

The text after the dot is the dominant reason — the verdict-reason half of the explainability triple (§ 8.0); the drilldown (§ 8.3.2) is the third part. *Accessibility:* `aria-label="image quality: invalid — femur out of frame"`; the tier is carried by colour **and** the ●/○ glyph **and** the word, so colour-blind and screen-reader users lose nothing. *Error/loading:* while detectors run, the badge shows the gray `○ grading…` state; if a detector errors, it shows `gray ○ QA unavailable` (§ 8.3.7).

#### 8.3.2 — `drilldown_panel`

*Purpose.* The "why" — the third part of the explainability triple. Opens on thumbnail click; shows the per-measurement breakdown and the override actions.

*Layout* — a right-side, sticky-scrolling drawer; the thumbnail grid dims behind it:

```
┌─ Frame 0044 — QA detail ───────────────── [✕ Esc] ┐
│ Contract v0.2 · graded 2026-05-20 14:23 UTC        │
│ Aggregate: INVALID — 4 of 8 measurements invalid   │
│ Dominant reason: femur_out_of_frame                │
├────────────────────────────────────────────────────┤
│ GEOMETRY                                           │
│  rf_thickness_mm        ✓ valid                    │
│  qmt_mm                 ✗ invalid                  │
│     └ femur_out_of_frame  (Tier-0 / Class D)        │
│       Detector: column-wise femur-cortex search    │
│       found no cortex+shadow below the deep fascia. │
│       Threshold: 0 of 64 central columns found      │
│       femur (Threshold-tier A, reused QMT default). │
│       [view calibration ▸ runs/calibration/...]     │
│  rf_area_mm2            ⚠ borderline — edge_clip_lat │
│  full_thickness_q50_mm  ✓ valid                     │
│ INTENSITY                                           │
│  rf_echo, int_cv_within ✓ valid                     │
├────────────────────────────────────────────────────┤
│ Override:  [ Use this frame anyway ]                │
│  Scope:  ◯ blanket   ◯ geometry only   ◯ intensity  │
│          only        [rationale: ______________ ]  │
│ [ Drop with reason ▾ ]                              │
└────────────────────────────────────────────────────┘
```

*Mockup note.* The numbers shown are illustrative of one frame's detector metadata — e.g. "0 of 64 central columns" means the femur detector searched this frame's 64 central-zone columns and found a cortex in none; "64" is that frame's central-zone column count, not a fixed constant (audit fix L3).

*Contract:* `drilldown_panel(frame_features) -> None` — reads `image_quality_tier_per_measurement` and `image_quality_detector_metadata` (§ 7.1); writes nothing until the operator acts, at which point it calls `override_action` (§ 8.3.6). *Accessibility:* `Esc` closes; `↑`/`↓` move between flagged measurements; `←`/`→` move to the previous/next frame without closing the drawer; each measurement row is a focusable element with a full screen-reader summary ("qmt_mm: invalid, femur out of frame, Tier-0 deterministic detector"). *Error/loading:* if a detector's metadata is missing (it errored), that measurement row reads `⚠ QA unavailable — <reason>` rather than a verdict.

#### 8.3.3 — `filter_sort_controls`

*Purpose.* Opt-in filtering and sorting of the picker grid. **Opt-in is mandatory** — the default view shows every frame (Transparency Rule 1); filtering is never applied unless the operator turns it on.

*Layout* — a sidebar block:

```
Sort:  [ Tier ▾ ]      ( Tier | Frame order | Femur tier )
Show:  ☑ Valid (24)   ☑ Borderline (11)   ☑ Invalid (8)
Filter by measurement:  [ none ▾ ]   ( none | QMT-valid only | Intensity-OK only )
```

*Contract:* `filter_sort_controls() -> FilterState`; the state persists in `st.session_state["picker_filter_state"]` so it survives a grid scroll or a re-render. All "Show" checkboxes are **checked by default** — turning one off is the operator's explicit, logged choice to hide a tier. *Accessibility:* standard form controls; the dynamic counts are in the label text, so a screen reader announces "Show invalid, 8 frames". *Error/loading:* counts show `…` until grading completes.

#### 8.3.4 — `phase4_prefill_gate`

*Purpose.* Phase-4 Gate 1 ("Is this frame usable?") pre-filled from the QA verdict — *suggested*, never decided (Transparency Rule 4).

*Layout:*

```
1. Is this frame usable?
   ◉ No   ◯ Yes   ◯ Unknown — frame predates QA grading
      └ pre-filled from QA:  :red[invalid — femur_out_of_frame, edge_clip_lateral]
        Disagree? choose Yes and add a rationale:
        [ rationale (required to override): ____________________ ]
```

*Contract:* `phase4_prefill_gate(fb, qa_verdict) -> 'Yes'|'No'|'Unknown'` — `qa_verdict` is `None` for a frame graded before the QA layer existed, which selects the third radio state (§ 8.6). The pre-filled radio is a *starting position*; the operator must actively confirm or change it, and an override to "Yes" requires a typed rationale that is captured into `qa_overrides` (§ 7.6). *Accessibility:* the radio group has a fieldset legend; the pre-fill reasoning is associated text, not a tooltip-only element. *Error/loading:* if grading has not completed, the gate shows no pre-fill and reads "QA grading in progress — gate not yet pre-filled."

#### 8.3.5 — `structured_drop_reasons`

*Purpose.* Replace the free-text drop-reason box with a structured checkbox group — the Step-1 label-capture instrument (§ 7.4, § 14).

*Layout:*

```
Drop reasons (pre-filled ☑ from QA flags — uncheck any you disagree with):
  ☑ femur_out_of_frame      ☑ edge_clip_lateral
  ☐ motion_blur             ☐ saturation_high_regional
  ☐ oblique_plane           ☐ wrong_site            ...(full taxonomy)
  ☐ Other: [______________________]   ← grows the taxonomy (§ 12.2 Channel C)
```

*Contract:* `structured_drop_reasons(fb, prefill_flags) -> (set[str], str)` — returns the checked reason codes and the free-text "Other". Pre-filled boxes are marked with a small "AI" tag; an operator *un*checking a pre-filled box, or checking one the detector missed, is a labelled disagreement (§ 12.2 Channel B). *Accessibility:* a checkbox group with a fieldset legend; each box is independently focusable. *Error/loading:* if grading has not completed, the group renders un-pre-filled with a note "pre-fill pending QA grading."

*Mandatory for a dropped frame.* The capture is not optional: Phase-4 "Lock gates" is disabled until a `Usable = No` frame carries at least one checked code or non-empty "Other" (§ 7.4(c)). Because "Other" accepts any free-text reason, the operator can always comply — the rule guarantees a recorded "why" without ever dead-ending the workflow.

#### 8.3.6 — `override_action`

*Purpose.* Capture an operator's disagreement with the algorithmic verdict into the `qa_overrides` schema (§ 7.6) — the load-bearing input to calibration and the learned grader.

```
Contract:
  override_action(fb, measurement, algo_tier, human_decision, rationale) -> dict
    human_decision : 'use_anyway' | 'drop' | 'use_for_geometry_only'
    side effects: appends an entry to st.session_state.gates[fb]['qa_overrides'];
                  on Accept, that entry is persisted into data/runs.jsonl (§ 7.0).
```

The **scoped** decisions (`use_for_geometry_only`, and an intensity-only equivalent) exist because § 6's gating is per-measurement — `saturation_high_regional`, for instance, kills `rf_thickness_mm` but leaves `rf_echo` usable, so the operator can rationally accept the frame for one measurement family and not the other. *Accessibility:* the scope radio group and the rationale field are standard labelled controls; a blanket override and a scoped override are distinct, announced options. *Error/loading:* not applicable — this is a write action, available only once grading has produced a verdict to override.

#### 8.3.7 — `error_and_loading_states`

*Purpose.* Make the three non-happy-path states explicit and honest, never silent.

- **Loading.** While detectors run on a sweep, the picker shows `st.progress("Grading frames for image quality … 18/30")`. Frames already graded show their badge; ungraded frames show the gray `○ grading…` state.
- **Detector error.** If the segmenter failed on a frame, or a detector raised, the badge is gray `○ QA unavailable` and the drilldown row reads `⚠ QA unavailable — segmentation failed` (or the specific reason). The frame is **never** blocked or hidden; the operator picks on their own judgement. Aggregation simply skips an ungradable frame.
- **Pre-calibration.** A Threshold-tier B detector whose threshold has not yet been cohort-calibrated (§ 11) does **not** fire against a guessed number. Its contribution renders as `ungraded — Threshold-tier B pending calibration` in the drilldown, with a link to § 11. Honest about what cannot yet be graded.

#### 8.3.8 — `calibration_link`

*Purpose.* The provenance end of the explainability triple — from a fired flag in the drilldown, a link to the detector's calibration record.

*Contract:* renders a link to `runs/calibration/<date>/<detector>.json` and the ROC curve image (§ 11.4). For a Threshold-tier A detector (physics-grounded, uncalibrated by design) the link instead points to the § 11.1 justification. This is what makes "always show why" (Transparency Rule 2) reach all the way down to the evidence, not stop at the flag name.

#### 8.3.9 — `accessibility_primitives`

*Purpose.* The shared accessibility substrate the other eight components draw on.

- **ARIA labels** on every badge: `aria-label="image quality {tier} — {dominant_reason}"`.
- **Keyboard contract:** `Tab` traverses the picker grid in order; `Enter` opens a thumbnail's drilldown; `Esc` closes it; `↑`/`↓` move within the drilldown; `←`/`→` move between frames inside the drilldown.
- **Screen-reader text alternatives** for every visual abstraction — most importantly the three-dot structure-group indicator (geometry / femur / intensity), which carries a hidden `geometry: valid; femur: invalid; intensity: valid` text span.
- **No colour-only meaning:** every tier = colour + glyph + word, always.

### 8.4 — State flow and data plumbing

The data path, end to end (the backend half is § 7; here is how it reaches and returns from the UI):

```
segmenter ──> mask ──> compute_image_quality_flags(img_gray, mask)   [§ 7.2]
                          │
                          ├─> ImageQualityFeatures record [§ 7.1] — joined on frame_base
                          │
       Phase-3 picker  <───┤   qa_aggregate_badge + drilldown_panel    [§ 8.3.1/2]
       Phase-4 review  <───┤   phase4_prefill_gate + structured_drop_reasons
                          │
   operator acts ─────────┘
       │
       └─> override_action ──> gates[fb]['qa_overrides']  ──(on Accept)──>  data/runs.jsonl
                                                                              │
                                          calibration (§ 11) + learned grader (Step 5) <┘
```

Cache invalidation follows the verified pipeline pattern (§ 7.7): a `qa_input_sig` = hash of `(mask signature, contract version, detector-config hash)`; when it changes, cached grades are dropped and re-computed. The `qa_overrides` schema is defined once in § 7.6 and referenced — not redefined — by § 8.3.6, § 11, and § 12.2.

### 8.5 — Transparency-rule enforcement matrix

The four transparency rules (§ 9) are enforced concretely, per phase. This 4-rule × 5-phase matrix (20 cells) names the enforcing mechanism in each cell; "n/a" means the rule has no surface in that phase.

| Rule ↓ \ Phase → | 1 Study | 2 Model | 3 Picker | 4 Review | 5 Assessment |
|---|---|---|---|---|---|
| **1 — Never hide a frame** | n/a | n/a | filter is opt-in; default shows all tiers; turning a "Show" box off is logged | the frame stays in the review set and its value is still computed even when gated `invalid`; Gate-1's "Unknown" third state means no auto-drop | `invalid` frames still rendered, with the value shown and a caveat (§ 6.4) |
| **2 — Always show the "why"** | n/a | contract version visible | badge dominant-reason + drilldown panel + calibration link | pre-fill reasoning shown inline beside the gate | per-card QA badge; cohort-placement caveat names the cause |
| **3 — Always allow override** | n/a | n/a | drilldown "Use this frame anyway" CTA | structured-checkbox uncheck; scoped `override_action` | an override surfaces as a ⚠ in the run-comparison panel |
| **4 — Pre-fill, don't pre-decide** | n/a | n/a | the badge is information, not a gate — it never picks a frame | Gate-1 pre-fill is a suggested radio position; operator must confirm | the QMT-provenance gate already follows this discipline; the QA badge matches it |

### 8.6 — Edge cases

Five non-happy-path situations, each with its specified behaviour:

- **Legacy frames — graded before the QA layer existed.** `image_quality_aggregate_tier` is `ungraded`; the picker badge is the gray `○ not yet graded`; Phase-4 Gate 1 offers the third radio state "Unknown — frame predates QA grading." These frames are never silently treated as `valid`. *Owner: TBD; trigger: a v0.x retrospective re-grading pass.* `[Named-deferred]`.
- **Detector error / segmenter failure.** Gray `○ QA unavailable` badge, specific reason in the drilldown, frame never blocked (§ 8.3.7).
- **Pre-calibration state.** A Threshold-tier B detector with no calibrated threshold renders `ungraded — pending calibration`, never fires a guessed number (§ 8.3.7).
- **Contract-version bump mid-run.** If the loaded `CONTRACT_VERSION` differs from the version a cached grade was produced under, the UI warns: "this run was graded under v0.2; v0.x is now active — re-grade for consistency?" The operator chooses; the run records which version it actually used (§ 12.3).
- **Override re-edit.** Before "Accept run," an override can be freely changed. After Accept it is immutable — the ledger is append-only (§ 7.0). A later correction is a *new* side-table row joined on `frame_base`, not an edit. `[Named-deferred]` — *owner: TBD; trigger: the § 11 retrospective re-coding work.*

### 8.7 — Performance and caching

- **Latency budget:** ≤ 2 s for a 30-frame sweep on the DGX, ≤ 5 s on a modest laptop — an **engineering target to validate in Build-Step 2** (§ 14), not a proven bound (audit fix H5). Most Tier-0 detectors are an O(H × W) pass over the mask and grayscale arrays, but a few are not — `tgc_banding` fits an attenuation curve, `air_gap_reverberation` runs a windowed FFT, `motion_blur` computes a fascia-band Tenengrad or autocorrelation. These remain cheap at frame scale (a windowed FFT is O(W log W); the curve fit is a short iterative solve), so the target is realistic — but it is stated as a target, and § 8.3.7's progress indicator covers the case where a first grading pass exceeds it.
- **Caching:** grades are cached in `st.session_state`, keyed by `qa_input_sig` (§ 8.4, § 7.7). A re-render with unchanged inputs is a cache hit and is instant; the budget above applies only to the first grading pass of a sweep.
- **No `st.cache_data` decorator:** the existing pipeline caches manually via `st.session_state` dicts; the QA layer follows that established pattern rather than introducing a second caching mechanism.

### 8.8 — Iteration plan — what ships in v0.2 and what waits

Per design principle (4), v0.2 ships a thin minimum-viable surface and iterates from operator reactions. The split:

| Component | v0.2 status |
|---|---|
| `qa_aggregate_badge`, `drilldown_panel`, `filter_sort_controls` | **[Active]** — ship in v0.2 |
| `phase4_prefill_gate`, `structured_drop_reasons`, `override_action` | **[Active]** — ship in v0.2 (the Step-1 label-capture surface) |
| `error_and_loading_states`, `accessibility_primitives` | **[Active]** — ship in v0.2 (not optional; honesty and access are baseline) |
| `calibration_link` | **[Conditional]** — links go live when § 11.4's calibration script has produced its first report; until then the link points to the § 11.1 justification |
| Diversity-aware "recommend N best frames" star | **[Future-direction]** — Build-sequence Step 7 |
| In-app calibration-ROC viewer | **[Future-direction]** — v0.x; for now `calibration_link` opens the JSON/PNG externally |
| Full WCAG accessibility audit | **[Named-deferred]** — v0.2 ships the ARIA + keyboard + text-alternative substrate of § 8.3.9; a full audit is *owner: TBD; trigger: a pre-clinical-deployment review* |

**How UI depth iterates — from data, not vibes.** Every operator interaction against a QA surface is structurally captured (§ 12.2): an override against a flag is Channel A; a Phase-4 checkbox edit against the pre-fill is Channel B; an "Other" free-text reason is Channel C; a confirmed agreement is Channel D (§ 8.9). UI changes after v0.2 are proposed against that captured evidence — "operators overrode `tgc_banding` 60% of the time, the detector or its surfacing needs work" — never against an unmeasured impression. This is the operational form of decision D5: the contract carries the rigor; the UI earns its depth from observed use.

### 8.9 — The QA surface as a data-collection instrument

A theme ran through the whole v0.1 review (Comments 9, 14, 19, 20, 21, 22): the QA UI is **not** a passive display of grades — from day one it is the *instrument that collects the data the QA layer needs to become load-bearing*. Akadir, Comment 22: *"I am not thinking the UI only as a passive end-user [surface]; it should also help us gather the data we want, as we show the results, asking the right questions at the exact moment we surface a parameter or a decision … otherwise it becomes a toy."* The transition from toy thresholds to load-bearing artifacts runs entirely through what this surface captures. The mechanics are already specified piecemeal across § 8.3 and § 12.2; v0.2 names the dimension explicitly and adds the three pieces that were missing.

**(1) Capture is a first-class job of every QA surface — including on agreement.** Every § 8.3 component that *shows* a grade also *asks and records*: the override action (§ 8.3.6), the structured drop-reason checkboxes (§ 8.3.5), and the Gate-1 pre-fill confirm/change (§ 8.3.4) are each simultaneously a display and a capture point. In v0.2, a QA component that shows a verdict with no path to capture the operator's response is incomplete. One addition closes a gap Comment 9 named: capture is **symmetric**. v0.1 records *disagreement* (Channel A overrides, Channel B checkbox deltas — § 12.2) but treats *agreement* as silence. v0.2 adds a path for an operator to **explicitly confirm** a grade, optionally with a note — a confirmed-correct grade is a labelled datapoint, not an absence. This becomes **Channel D** in § 12.2, and it composes cleanly with the § 11.2 pre-fill-exclusion rule: a *silently* accepted pre-fill is still excluded from calibration (it only echoes the detector), but an *explicitly confirmed* grade — the operator actively affirmed it — is a usable positive label.

**(2) Progressive-disclosure explainability (Comments 15, 16).** A QA surface routinely shows technical concepts an operator may not have at hand — compound-FPR, threshold-tier, Cohen's κ, the academic method anchors. These must be explainable *in the UI*, but **layered**, never dumped. The v0.2 pattern — the `explain` affordance — has three depths: a small hover **"(i)"** giving one plain sentence; an expand to a **short paragraph**; and a **deep-dive** link to the full § reference or the calibration provenance (§ 8.3.8). Comment 16: *"a small hover (i), then maybe if requesting more, a relevant paragraph."* This is the operational form of decision D5 — the contract carries the full rigor, the UI is a *calibrated, layered* summarisation of it, and the operator pulls as much depth as they want. Per Comment 15, the § 11.6 academic anchors (Karamalis, Loizou, …) belong in this background layer: surfaced through the same `explain` affordance, they move the app toward "a canonical muscle-USG practitioner aide" — a `[Future-direction]` ambition (§ 17), but the three-layer affordance itself ships in v0.2.

**(3) Deliberate expert review (Comment 22).** Akadir's concrete vision: *"I show this app to 3–5 different medical domain experts and gather their unbiased feedback — wouldn't that be really load-bearing."* v0.2 names an **expert-review mode**: a flow that runs a domain expert through a curated frame set and captures their grades and commentary structurally — distinct from routine operator assessment, and the highest-density signal for converting toy thresholds into load-bearing ones. The *capture plumbing* already exists (the `qa_overrides` schema § 7.6, the § 12.2 channels); the dedicated expert-review UI flow is a near-term `[Future-direction]` item — *owner: Akadir; trigger: the first external-expert review session* — carried in the § 17 register.

**(4) The captured stream calibrates the whole pipeline, not just thresholds (Comment 20).** Comment 20: *"calibrate, not only the thresholds but the whole pipeline."* The same captured stream feeds more than § 11's numeric threshold-fitting: Channel C ("Other" reasons) tests whether the **taxonomy** is complete; expert review of flagged frames tests whether the **§ 6 gating table** matches clinical reality (open question Q1); § 11.2 step 5's false-positive / false-negative re-review tests whether a **detector's mechanism** is sound. v0.2's point is that one disciplined capture surface, used from day one, is what makes every later layer — taxonomy, gating, detector design, thresholds — refinable on evidence rather than on impression.

**The principle.** A QA surface that only displays is half-built; the other half is capture. It is realised concretely by § 8.3.4–8.3.6 (the capture components), the `explain` affordance above plus § 8.3.8 (layered explainability), § 12.2 Channels A–D (the structured feedback paths), and the § 17 expert-review-mode item — and it is, per Akadir's review, what keeps the QA layer from being a toy.

---

## § 9 — The transparency-rule contract

Four rules bind every UI surface the QA layer touches. They were ratified in-session (decision D6, § 2.1) and are non-negotiable design constraints — they exist so that every QA-driven decision is *auditable*: a reviewer, an operator, or a future model can always reconstruct what the system said, why, and what the human did about it. § 8 operationalises them component by component; § 8.5 gives the per-phase enforcement matrix. This section is their canonical statement.

**Rule 1 — Never hide a frame for QA reasons.** The operator sees every frame in the picker, always. Filtering is opt-in — an operator-driven control, never a default (§ 8.3.3). An `invalid` verdict means "excluded from the *pooled aggregate*" (§ 6.4), never "removed from view." *Why:* silent filtering produces decisions no one can audit — a frame that was never shown cannot be questioned. *Enforced at:* § 8.3.3 (opt-in filter), § 6.4 (invalid still displayed), § 8.6 (legacy frames get a tier, not a disappearance).

**Rule 2 — Always show the "why."** Every flag carries its rationale: the detector that fired, the threshold it tripped *with the real number*, the measurements it taints, and a link to the calibration provenance. No black-box badges. *Why:* a verdict an operator cannot interrogate cannot be trusted or corrected. *Enforced at:* § 8.0 principle 1 (the verdict-reason-drilldown triple), § 8.3.2 (drilldown panel), § 8.3.8 (calibration link).

**Rule 3 — Always allow override.** The operator can accept a frame the system flagged `invalid`, or scope an override to a measurement family. Every override is logged into `qa_overrides` (§ 7.6). *Why:* the operator is the clinical authority; their disagreement is the highest-density training signal the system has (§ 12.2 Channel A), not noise to suppress. *Enforced at:* § 8.3.6 (override action), § 7.6 (the schema), § 12.2 (the feedback loop).

**Rule 4 — Pre-fill, don't pre-decide.** Phase-4's gates receive *suggested* values from the QA layer with the reasoning shown alongside; the human confirms or changes each. A suggestion is a hypothesis, never a verdict. *Why:* an auto-decided gate would make the operator a rubber stamp and would contaminate the very labels calibration depends on (the pre-fill circularity, § 7.0 / § 11.2). *Enforced at:* § 8.3.4 (pre-filled Gate 1 the operator must confirm), § 8.3.5 (pre-filled checkboxes the operator edits), § 11.2 (calibration excludes un-edited pre-fills).

---

## § 10 — Open questions

Of the five questions open in v0.1, **four are resolved** by Akadir's 2026-05-20 review (Comments 24–28); **Q2 remains open** with a confirmed trigger. None was ever a blocker for starting the build sequence (§ 14). The resolved questions' downstream commitments — Anonaao's clinical review, the parked Step 7, the v2 speed-of-sound question — are carried in the § 17 future-work register.

| # | Question | Resolution (Akadir's 2026-05-20 review) |
|---|---|---|
| **Q1** | Does the § 6 per-measurement gating table (the INV / BDR / blank cell assignment) match clinical reality? Its clinical correctness is load-bearing — it decides which measurements a flagged frame still supports. | **Resolved — proceed.** Akadir ratified the § 6.2 table (Comment 1: *"let's go with this table; we can reassess along the course — no showstopper"*). Anonaao's clinical sanity-check of § 4 + § 6 is **no longer a release blocker**: it is a tracked item in the § 17 register, to be handled "at the right point" (Comment 28). Owner: Anonaao. |
| **Q2** | Does the `IMAGE_QUALITY_FLAG_PRIORITY` order (§ 5.1) capture the right "dominant reason" for UI surfacing — should `wrong_site` stay at the top, or be demoted so it never pre-empts an algorithmic flag? (The tuple controls *UI prominence* only — every flag stays inspectable.) | **Open — trigger confirmed.** Akadir (Comment 27): *"best decided when the UI is on, on a real case study."* Q2 stays open; trigger = first live-UI use of the Phase-3 drilldown. Owner: Akadir. |
| **Q3** | Should the five `image_quality_*` fields extend `FrameFeatures`, or be a separate `ImageQualityFeatures` dataclass? | **Resolved — separate dataclass.** Comment 26 asked for the call on SWE principles + practicality + auditability; those favour a separate `ImageQualityFeatures` dataclass joined on `frame_base`. Spec updated — § 7.1. |
| **Q4** | Should Build-Step 7 (the diversity-aware multi-frame recommender) be scoped now, or parked? | **Resolved — park it, v1-committed.** Akadir (Comment 25): *"park it, so we see the quality metrics and decisions play out, then decide — but certainly part of v1, a small sub-sequencing."* Carried in the § 17 register as v1-committed, parked pending Steps 1–6. Owner: Akadir. |
| **Q5** | **(surfaced 2026-05-19)** Is speed-of-sound mismatch a measurable confound in high-BMI RF studies? The system assumes ~1540 m/s; subcutaneous fat propagates at ~1450 m/s, a small systematic geometric distortion of deep structures — distinct from `deep_attenuation_high`, an amplitude effect, not a propagation-speed one. | **Resolved — deferred to v2.** Akadir (Comment 24) holds the device SDK paper; *"better to leave it to v2."* Tagged `[Future-direction]`, v2; carried in the § 17 register. Owner: Akadir. |

---

## § 11 — Calibration methodology

*Akadir, 2026-05-18: "We don't have the numbers. Do we have the process? Robust, accurate, practical?" — this section is the answer.* It specifies how every detector's threshold is set, validated, and reported, so a deployed detector's accuracy claim survives professional scrutiny.

### 11.0 — The publish standard (the bar every detector must clear)

Before the method, the target. At deployment, every Threshold-tier B detector must be reportable in this exact form — the numbers in the example below are **illustrative of the format**, not a real calibration result and not the real cohort (audit fix M1):

> *"`motion_blur` Tier-0 detector. Threshold: fascia-band Tenengrad < 47.3 (calibrated on 4,800 frames from 280 studies, 2026-06-XX). Held-out validation (1,200 frames, 39 studies): sensitivity 86% (Wilson 95% CI 83–89%), specificity 78% (CI 75–81%). Inter-rater κ on this label: 0.71. Detector sensitivity is above the κ-implied noise ceiling; the values are meaningful."*

Anything less specific — a bare "86% accurate," a threshold with no cohort, a sensitivity with no confidence interval — is hand-waving and does not ship. § 11.1–§ 11.6 are the process that produces this statement; § 11.7 names what honestly cannot reach this bar.

### 11.1 — Two tiers of threshold provenance

Not every threshold needs cohort data. Detectors split by where their threshold comes from — the **Threshold-tier** axis of § 0.3 (distinct from the detector Tier-0/1/2 axis).

**Threshold-tier A — physics-grounded universals.** The threshold derives from signal physics, not from this cohort. These ship with a sensible default on day one and run on the cohort *before* any labels exist.

| Detector / threshold | Value | Justification |
|---|---|---|
| `saturation_high_*` pixel rail | ≥ 250 grayscale | 255 is the 8-bit saturation rail, cohort-independent. |
| `dark_clip_high` pixel rail | ≤ 5 grayscale | The same physics at the opposite rail. |
| `edge_clip_lateral` / `_vertical` | a mask pixel in the outermost N-pixel ring, N=1 | N=1 is the existing `touches_edge` behaviour ([`post_processing.py:106`](../scripts/preflight_helpers/post_processing.py#L106)); universal. |
| `femur_out_of_frame`, `femur_off_central_zone` | reuse existing QMT detector thresholds; central zone = `QMT_CENTRAL_ZONE_MARGIN_PX = 15` ([`measurements.py:47`](../labeling_app/measurements.py#L47)) | Already calibrated for the QMT pipeline. (Corrects the v0 contract's wrong "[W/4, 3W/4] quartile" definition — see § 5.2.) |
| `frame_rf_consistency` outlier cut | modified Z-score > 3.5; minimum sweep size 4 frames | The Iglewicz-Hoaglin modified-Z standard cut — scale-invariant by construction, a published statistical convention, not a cohort fit (Build-Step 2 item 9 / § 14 Step 4). |

**An honesty caveat on Threshold-tier A** (path-to-rigor latent assumption — carried into § 13). The *pixel rails* (≥250, ≤5) are genuine physics. But the *fraction* cut-offs paired with them — "> 2% of ROI pixels" for saturation and dark-clip — are **not** saturation physics; the 2% is borrowed from the QMT muscle-occupancy tolerance, a cohort-derived number for a different purpose. The label "physics-grounded" is doing rhetorical work for the fraction. The fraction cut-offs are therefore treated as Threshold-tier B in practice, and **§ 13 records as a named limitation that Threshold-tier A items bypass calibration entirely and could persist unexamined for the project's lifetime** — § 12.6 institutes a recurring Threshold-tier-A audit as the counter-discipline.

Conversely, the `frame_rf_consistency` modified-Z cut (3.5) added by Build-Step 2 item 9 is a *clean* Tier-A entry on the operative "universal, not cohort-derived" test: the modified Z-score is scale-invariant by construction and 3.5 is a published statistical standard, borrowed from no cohort — a better-behaved Tier-A citizen than the saturation fractions above. It is named here, like every Tier-A value, so the § 12.6 recurring audit covers it. (Two refinements of the gate — a tiny-MAD over-fire floor and a small-N sharpening of the cut — are Build-Step 2b calibration items, recorded against `frame_rf_consistency__modified_z_max` in `qa_thresholds.py`.)

**Threshold-tier B — cohort-calibrated.** The threshold depends on scale, cohort, or implementation specifics and must come from data: `motion_blur` (fascia-band Tenengrad), `tgc_banding` (detrended-residual σ), `air_gap_reverberation` (comb-peak magnitude), `oblique_plane` (Radon-orientation divergence), `subq_fat_compressed` (fat-thickness mm), `saturation_high_*` / `dark_clip_high` fraction cut-offs, `wrong_site` (`rf_fraction` cut-off), and every Tier-1 confidence-map threshold.

### 11.2 — The five-step calibration process (for every Threshold-tier B detector)

1. **Generate scores.** Run the detector on every available frame and pair each frame's raw score with its structured Phase-4 label (§ 7.0). **Be precise about *which* substrate this is** — two counts v0.1 conflated:
   - *Frames a detector can be scored on* — the measurement cohort. Large, but un-labelled: a raw score with no paired label calibrates nothing.
   - *Frames that carry a Phase-4 QA label* — the actual calibration substrate. As of 2026-05-20 this is **8 finalised runs** in `data/runs.jsonl` (§ 7.0) — far too small to calibrate a Threshold-tier B detector to the § 11.0 bar.

   The labelled substrate does **not** grow on its own (Comment 18). It grows by two deliberate routes: **(i)** Build-Step 1's cohort assessment pass, run over the *existing* cohort so every studied frame gets its structured Phase-4 capture; and **(ii)** the **Round-3 data collection** (the `Görüntü Alma Usulleri v3` acquisition protocol — § 17 register), itself focused on cleaner, better-recorded data rather than more patients. Until one of those has run, calibration has 8 runs to work with and the § 11.0 publish bar cannot be met — that is the honest state, and the build sequence (§ 14) reflects it. *(v0.2 — corrects the v0.1 "~319 studies × ~14 ≈ 4,500 frames, growing toward ~6,000" claim, which conflated scoreable frames with labelled frames and asserted an organic growth that will not happen; Comment 18, audit finding M1.)*
2. **Plot the score distribution by label class.** Three histograms on one axis: all frames; frames the operator dropped *for this specific reason code* (positives); frames the operator accepted (negatives). Separation between positives and negatives is the strongest single indicator of whether the detector is useful at all — heavy overlap means a fundamentally weak detector, and it is far better to learn that now than after deployment.
3. **Pick the operating point via ROC.** Compute sensitivity and specificity across candidate thresholds. QA biases toward sensitivity over specificity — a false flag costs an operator override (Transparency Rule 3), a missed real fault silently contaminates a measurement. **Sensitivity targets are differentiated by downstream cost** (technical audit): **structural-killers** (`femur_out_of_frame`, `wrong_site` — a miss poisons every QMT aggregate) target **sensitivity ≥ 95%**; **intensity-statistic killers** (`saturation_high_*`, `tgc_banding` — a miss biases one frame's intensity only) target **sensitivity ≥ 80%**. The overall floor: **sensitivity ≥ 85% at specificity ≥ 80%**, tighter than the v0 contract's "80–90% at 70–85%" which the technical audit judged too soft for the cohort sizes claimed.
4. **Hold out and validate.** Split the labelled set 70/30 into calibration/validation. Fit thresholds on the calibration split only; report sensitivity/specificity on the held-out validation split. Never report numbers on the data the threshold was fit on — that is the training-set overfit trap.
5. **Sanity-check with the operator.** Per detector: sample 10 false positives (detector flagged, operator accepted) and 10 false negatives (operator dropped, detector silent); the operator re-reviews each. Is the detector catching something real, or firing on a benign feature? Is a miss a threshold problem or a genuinely different, un-catalogued failure mode? This step is the practical accuracy floor — it catches detector *design* errors that ROC analysis alone cannot.

**The pre-fill exclusion rule (path-to-rigor finding — load-bearing).** Phase-4 pre-fills its checkboxes from detector output (§ 8.3.5); calibration step 1 then uses Phase-4 labels to calibrate the same detectors. An operator who clicks "Save" without editing a pre-fill produces a label that merely echoes the detector — the detector would validate against itself, and § 11.0's published sensitivity would be inflated by self-agreement. **Therefore: calibration excludes every frame where the operator accepted the pre-fill unchanged, counting only frames the operator actively edited or explicitly confirmed.** Without this exclusion the published numbers are not real.

### 11.3 — Robustness disciplines

**Inter-rater reliability with Cohen's κ — not raw % agreement** (technical audit). Two operators independently assess the same sample. Detector accuracy claims are bounded by inter-rater agreement: if two humans disagree on a label 20% of the time, no detector can be reported as ">80% accurate" on it — that is noise, not signal. But the statistic must be **Cohen's κ (or Krippendorff's α), not raw % agreement** — % agreement is inflated by base-rate concordance on rare flags (if 95% of frames have no `air_gap_reverberation`, two raters who simply never flag it score 95% "agreement" while having demonstrated nothing). For categorical reason codes, **κ ≥ 0.6 per code is the publishable floor.** Sample size: ~50 frames is undersized — κ confidence intervals span ±0.25 on rare codes — so the IRR study uses **150 frames stratified by predicted positive rate** (oversample the rare codes from Tier-0 to get adequate per-code N). The IRR study gates the *publish claim* — no detector accuracy is reported at the § 11.0 bar before κ is known — but it does not block building or iterating detectors (Build-sequence Step 2.5, § 14).

**Confidence intervals — Wilson, not normal-approximation.** Every published sensitivity and specificity carries a **Wilson score 95% confidence interval**. The normal approximation breaks down for the small-prevalence reason codes (some rare flags are < 2% prevalent); the Wilson interval stays valid there.

**Cross-cohort generalisation.** If the cohort changes — new ultrasound machine, new operator, new patient population — re-run calibration. Threshold-tier A survives a cohort change; Threshold-tier B may not.

**Drift tracking.** A re-runnable calibration script (§ 11.4) reports, per detector, the current vs. recommended threshold and the sensitivity/specificity at the current threshold on the validation set, and raises a drift alert if the recommended threshold has moved more than a set fraction from the current one.

### 11.4 — The calibration script

`scripts/calibrate_image_quality_detectors.py` (ships with Build-sequence Step 2). It reads `data/runs.jsonl` and the feedback parquet files (§ 12.2) automatically — no manual export — and outputs per detector: a ROC curve PNG, a score-distribution histogram PNG, and a calibration report JSON (`runs/calibration/<date>/report.json`) carrying the threshold, sensitivity/specificity with Wilson CIs, the cohort metadata, and the IRR κ baseline. Cheap, idempotent, re-runnable in minutes once the cohort and labels are loaded.

### 11.5 — Compound false-positive discipline

A single detector at 80% specificity is fine. Fourteen of them are not. With the 14 `[Active]` Tier-0 detectors (§ 4.2) each at 80% specificity (a 20% false-positive rate), *if their false positives were statistically independent* the probability that a genuinely *clean* frame trips **zero** false flags would be 0.80^14 ≈ **4%** — i.e. ~96% of clean frames carrying at least one spurious flag, and badge-fatigue on day one. **Two honest caveats on that 4% (audit fix H6).** First, it is an *independence-assuming* figure, and the detectors are **not** independent — several key off shared structures (`near_field_clutter` and `air_gap_reverberation` both read the near field; the geometry detectors all read the same mask), so correlated false positives make the true clean-pass rate somewhat higher than 4%. Second, the count is approximate — `segmentation_low_confidence` (Tier-2, now `[Active]` — § 5.4) becomes a fifteenth firing detector once its threshold is calibrated. Neither caveat changes the conclusion: with this many detectors the multiple-testing inflation is real and must be managed explicitly. 0.80^14 ≈ 4% is the order-of-magnitude motivation, not a precise rate.

| Approach | Status | What it is |
|---|---|---|
| **Two-detector concurrence** | **[Active]** — v0.2 operating rule | Require two independent detectors to fire before an `invalid`-driven aggregate badge. Simplest, most explainable, override-friendly. This is the v0.2 default. |
| **Frame-level compound-FPR target ≤ 25%** | **[Active]** — monitoring metric | Report the frame-level compound false-positive rate on the cohort histogram; ≤ 25% (one clean frame in four trips a spurious flag) is the health target. Back-solves to a per-detector specificity ≥ 97% if pursued via tightening alone. |
| **Benjamini-Hochberg FDR control, q = 0.10** | **[Future-direction]** | A formal false-discovery-rate control over the frame's reason-code stack. Adopt in v0.x if Step-2 data shows two-detector concurrence is too blunt. *Owner: TBD; trigger: a Step-2 compound-FPR readout showing concurrence is insufficient.* |

**Comorbidity and precedence notes** (technical audit): `motion_blur` and `oblique_plane` are **commonly comorbid** — they fire together on roughly 30% of frames per published correlated-artifact studies; not redundant (the mechanisms differ) but the calibration must expect and report the overlap. `deep_attenuation_high` and `anisotropy_dropout_deep` **overlap operationally** — one Karamalis confidence map fires both — so a precedence rule is required: **attenuation is global-deep, anisotropy is local-band** (if low confidence spans the whole deep half → attenuation; if confined to a band segment → anisotropy).

### 11.6 — Academic anchors

The methods above are not invented here; they have a literature. Five papers anchor the load-bearing method choices (full bibliographic form in Appendix A):

- **Karamalis et al. 2012** — ultrasound confidence maps via random walks. The canonical method for the Tier-1 Class-C detectors (`anisotropy_dropout_*`, `deep_attenuation_high`). `[Active]` anchor for § 5.3.
- **Loizou et al. 2006** — carotid-ultrasound quality evaluation. Direct evidence that Laplacian-family sharpness metrics conflate with speckle in B-mode ultrasound — the justification for replacing `motion_blur`'s variance-of-Laplacian with fascia-band Tenengrad (§ 5.2). `[Active]` anchor.
- **Mittal et al. 2012 (BRISQUE)** — no-reference image-quality assessment. `[Future-direction]` — the comparison baseline if a Tier-2 learned grader is built.
- **Baumgartner et al. 2017 (SonoNet)** — real-time fetal standard-plane detection in freehand ultrasound. `[Future-direction]` — the closest prior art for a learned `wrong_site` / view classifier (Build-sequence Step 5).
- **Abdi et al. 2017** — CNN echocardiogram quality assessment. `[Future-direction]` — the prior art the per-measurement structure (decision D1) is deliberately arguing *against* (Abdi uses a single 0–5 scalar); cited as the foil.

### 11.7 — What cannot be calibrated honestly

Honesty about what the methodology *cannot* deliver is itself a quality dimension. These items will not reach the § 11.0 publish bar in v0.2 and the plan does not pretend otherwise:

- **`segmenter_mislabel_wrong_class`** — the residual `wrong_site` case where the segmenter confidently mis-labels a non-RF frame as RF. The class-composition `wrong_site` detector (§ 5.2) *is* calibratable and does reach the bar; this residual case is not — it needs a cross-class confidence signal or the Step-5 learned grader. `[Named-deferred]`.
- **The Tier-2 learned grader** — needs ≥ 200 labelled positives *per reason code* before training is honest (§ 14, Step 5). One cohort assessment pass of ~6,000 frames will not yield that for rare codes (`air_gap_reverberation`, `wrong_site`). Do not deploy until the per-class minimum is met — gated on a per-class count, not on "Step 1 has happened."
- **`segmentation_low_confidence`** — needs the segmenter to expose a softmax/uncertainty signal first; verified 2026-05-20 that it currently exposes only the argmax mask (§ 5.4). Calibration is gated on that code path landing. `[Conditional]`.

Each of these is surfaced as "known-uncalibrated" in any UI rendering (§ 8.3.7 pre-calibration state) and in any paper write-up.

---

## § 12 — Continuous-refinement discipline

*Akadir, 2026-05-18: "I don't want to make this QA a one-time effort, we should be able to refine, the process should be there and implementable, practically." — this section is the answer. § 11 says how thresholds get calibrated the first time; § 12 says how the contract stays alive after deployment. (Section content is carried from the audit's proposed § 12; its internal cross-references have been re-pointed to this plan's section numbering.)*

### 12.1 — Experiment design discipline

Every threshold change, new detector, or taxonomy addition follows the same five-part shape. The discipline exists because the failure mode is universal: "let me just tweak the threshold and see if it looks better" produces a system that ratchets to the most recently inspected sample and silently breaks on the previous one.

| Part | What it means | Failure mode if skipped |
|---|---|---|
| **Hypothesis** | One sentence, pre-registered before any data is plotted. *"Lowering `motion_blur` Tenengrad threshold from 47.3 → 38.0 will raise sensitivity from 86% → ≥90% while specificity remains ≥75%."* | Post-hoc rationalization. The "experiment" becomes a write-up of whatever happened. |
| **Held-out data** | The validation split from § 11.2 step 4 is sacred. Calibration changes are fit on the calibration split only; the validation split is touched once, after the threshold is frozen. | Validation set contaminates into calibration → reported numbers don't survive the next cohort. |
| **Before/after on the same data** | Run *current* threshold and *proposed* threshold on the identical frame set. Report deltas: Δsens, Δspec, Δfalse-flag rate, Δoperator-override rate (if production data is available). | "It's better on the new cohort" hides regressions on the old cohort. |
| **Pre-stated decision rule** | Written *before* looking at the numbers. *"Accept if Δsens ≥ +3pp AND Δspec ≥ −5pp on the validation split. Reject otherwise — including 'marginal improvement, ship it anyway'."* | The decision rule moves to fit the result; you can no longer distinguish a real improvement from motivated reasoning. |
| **Negative-result disposition** | An experiment that *rejects* the hypothesis is a finding, not a failure. Record it in the CHANGELOG (§ 16) with the same weight as accepted changes — future work will otherwise re-propose the same change. | The same losing idea cycles through every six months. |

Output of every experiment: a JSON sibling to the calibration report at `runs/calibration/<date>/<detector>_experiment_<short-id>.json` containing the five parts above + the raw before/after numbers. The decision rule lives in the JSON, not in someone's head.

### 12.2 — Feedback loops from production

Operator behaviour is the highest-density training signal the system has. The discipline is to make every operator response — disagreement *and* explicit agreement — structurally captured, not "noted in passing" or stored in a free-text field that nobody parses.

Four structured feedback channels:

**Channel A — Override-against-detector.** When an operator uses "Use this frame anyway" against a Tier-0 flag (Transparency Rule 3, § 9), that action is a *labelled false-positive against that detector*. The override gets a row in `runs/feedback/<date>/overrides.parquet`:

```python
{
    "study_id": "ACL762",                # illustrative — a verified cohort study ID
    "frame_idx": 44,
    "detector": "femur_off_central_zone",
    "detector_score_raw": 0.31,           # actual score the detector emitted
    "current_threshold": 0.25,            # what fired the flag
    "operator": "operator_id_hash",
    "override_reason": "...",             # free-text but bounded list of stems
    "captured_at": "2026-05-20T14:23:00Z",
    "contract_version": "v0.2",
}
```

The override row carries everything the next calibration run needs — raw score, fired threshold, contract version, study cohort tag. Bias toward over-capture: the marginal cost of one parquet row is nothing; the marginal value of a labelled false-positive is high. (This is the production form of the `qa_overrides` schema, § 7.6.)

**Channel B — Phase-4 reason-code corrections.** When an operator's Phase-4 checkbox set (§ 7.4, § 8.3.5) differs from the algorithmic `image_quality_validity_flags` pre-fill, the *delta* is the labelled-disagreement record. Two flavours: operator added a reason the detector missed (false negative for that detector), operator removed a reason the detector added (false positive). Both flow into `runs/feedback/<date>/phase4_deltas.parquet`.

**Channel C — Free-text "Other" reasons.** The "Other" field in the Phase-4 checkbox group (§ 7.4) is where the taxonomy grows. Every "Other" entry gets a periodic (cadence in § 12.6) review: if the same theme appears ≥5 times across operators, it is a candidate for a new reason code. The review IS the taxonomy-extension entry point.

**Channel D — Confirmed agreement (v0.2 — § 8.9).** v0.1 captured only *disagreement*; Comment 9 asked that *agreement* be a datapoint too. When an operator **explicitly confirms** a QA grade — actively affirms the pre-fill or the verdict, optionally with a note — that confirmation is written to `runs/feedback/<date>/confirmations.parquet`. It is distinct from a *silently* accepted pre-fill, which § 11.2's pre-fill-exclusion rule still discards (a silent accept only echoes the detector). An explicit confirmation is a usable positive label — it records that a human looked and agreed, which a silent pass does not. This is the symmetric-capture point of § 8.9(1).

**Closing the loop into calibration.** The calibration script (§ 11.4) reads `runs/feedback/*/overrides.parquet`, `phase4_deltas.parquet`, and `confirmations.parquet` by default — the override, delta, and confirmation rows merge into the labelled validation set automatically. No manual export step. Operators do their job; the calibration script picks it up; thresholds re-fit.

### 12.3 — Contract versioning

A frame audited under v0.2 thresholds is not directly comparable to one audited under a later contract version — calibration runs record `contract_version` (Channel A row above) so historical aggregates can be re-scored against any version on demand.

**Bump rules:**

| Bump type | Trigger | Example |
|---|---|---|
| **Patch (v0.2 → v0.2.1)** | Threshold re-calibration within an existing detector. Score formula and reason code unchanged. | `motion_blur` threshold moves 47.3 → 51.0 after a fresh 600-frame calibration. |
| **Minor (v0.2 → v0.3)** | New detector added; new reason code added to `IMAGE_QUALITY_FLAG_PRIORITY`; score formula meaningfully changed. The § 6 gating table may grow rows but existing rows unchanged. | A new `probe_pressure_variation` detector; `tgc_banding` score moves to a Fourier-based metric. |
| **Major (v0.x → v1.0)** | Per-measurement gating semantics change (a cell INV ↔ BDR ↔ blank shifts); aggregate-tier definition changes; reason-code priority re-ordered; a detector's Tier classification changes. | The aggregate-tier rule moves from "60% of measurements valid" to a headline-weighted scheme. |

**Where the version lives.** A single `CONTRACT_VERSION = "v0.2"` constant in `labeling_app/measurements.py` adjacent to `IMAGE_QUALITY_FLAG_PRIORITY`. Stamped into: every `FrameFeatures` instance (§ 7.1 — `image_quality_contract_version`); every calibration report JSON (§ 11.4); every override / phase4-delta row (§ 12.2); the picker badge tooltip ("audited under contract v0.2"). The live, growing CHANGELOG is § 16 of this document.

### 12.4 — Threshold drift management — the registry

Thresholds are not magic numbers sprinkled across detector code. They live in **one place**, with provenance:

```python
# labeling_app/qa_thresholds.py (proposed; ships with Step 2)

@dataclass(frozen=True)
class ThresholdEntry:
    value: float | int
    units: str                       # "fascia-band Tenengrad (unit-free)", "mm", etc.
    tier: Literal["A", "B"]          # § 11.1 Threshold-tier nomenclature
    calibrated_on: str | None        # ISO date; None for Threshold-tier A defaults
    cohort_id: str | None            # e.g. "v1_cohort_319studies"; None for Threshold-tier A
    cohort_n_frames: int | None
    sens_at_value: float | None      # held-out validation, § 11.2 step 4
    spec_at_value: float | None
    calibration_report_path: str | None
    contract_version: str            # which contract version introduced this value

THRESHOLDS: dict[str, ThresholdEntry] = {
    # The entries below are ILLUSTRATIVE — worked examples of the schema, not
    # real calibration results. The motion_blur numbers (47.3, the cohort id,
    # 4800 frames, 0.86 / 0.78) are placeholders pending the § 11.2 calibration;
    # the real (small) calibration substrate is described honestly in § 11.2 (audit M1).
    "motion_blur__fascia_band_tenengrad_min": ThresholdEntry(
        value=47.3, units="fascia-band Tenengrad",
        tier="B", calibrated_on="2026-06-15",
        cohort_id="v1_cohort_319studies", cohort_n_frames=4800,
        sens_at_value=0.86, spec_at_value=0.78,
        calibration_report_path="runs/calibration/2026-06-15/report.json",
        contract_version="v0.2",
    ),
    "saturation_high__pixel_value_min": ThresholdEntry(
        value=250, units="grayscale", tier="A",
        calibrated_on=None, cohort_id=None, cohort_n_frames=None,
        sens_at_value=None, spec_at_value=None,
        calibration_report_path=None, contract_version="v0.2",
    ),
    # ...one entry per detector threshold
}
```

Detectors look up by name; no detector hardcodes a number. The registry is the single source of truth — `grep "47.3" labeling_app/` should match exactly one line, in the registry. The calibration script (§ 11.4) writes proposed updates to `runs/calibration/<date>/threshold_diff.json` and never mutates the registry itself; promotion to the registry is a separate human commit that triggers the § 12.5 propagation checklist.

### 12.5 — Change-propagation checklist

The bug class is *"changed the threshold, forgot to update the UI tooltip / cohort stats / pre-fill logic, now operators see stale rationale."* The checklist converts this from "remember to do all these" into a literal list that PR review checks against — each row a touchpoint that must be *considered* (not always changed, but explicitly considered) for the bump type.

| Touchpoint | Path / anchor | Patch | Minor | Major |
|---|---|---|---|---|
| Threshold registry | `labeling_app/qa_thresholds.py` (§ 12.4) | UPDATE | UPDATE | UPDATE |
| Detector implementation | `labeling_app/image_quality.py` (§ 7.2) | — | UPDATE | UPDATE |
| Reason-code priority tuple | `IMAGE_QUALITY_FLAG_PRIORITY` (`measurements.py`) | — | UPDATE if new code | UPDATE if reordered |
| Per-measurement gating table | § 6 of this doc + the function | — | UPDATE if new code | UPDATE |
| `FrameFeatures` dataclass | `feature_compute.py:77` | — | UPDATE if new field | UPDATE if shape changes |
| Aggregation policy | `aggregation.py` | — | CONSIDER | UPDATE |
| Cohort stats precompute | `scripts/cohort_stats_precompute.py` — re-run required | RE-RUN | RE-RUN | RE-RUN |
| Phase-4 pre-fill | `p04_frame_review.py` | — | UPDATE if checkbox set grows | UPDATE if semantics change |
| Picker badge logic | `p03_frame_picker.py` | — | UPDATE if new tier semantics | UPDATE |
| UI rationale tooltip | drilldown panel (§ 8.3.2) | UPDATE — show new threshold value | UPDATE | UPDATE |
| Contract document § 5, § 6, § 11.1 | this file | UPDATE § 11.1 row | UPDATE detector spec § 5 | UPDATE gating § 6 |
| CHANGELOG | this file § 16 | APPEND row | APPEND row | APPEND row |
| Re-grade historical cohort | `scripts/regrade_cohort.py` (proposed) | RUN (compare deltas) | RUN | RUN — historical aggregates change |

**Discipline rule:** the PR description for any contract change carries this checklist with each row marked ✓ DONE / N/A / DEFERRED-TO-<ticket>. A row marked DEFERRED is a tracked debt, not a silent skip.

### 12.6 — Practical cadence

Calibration is cheap (§ 11.4 — minutes once data is loaded), so cadence is bounded by *when does anything material change*, not by re-run cost. Four triggers:

| Trigger | What happens | Cadence in practice |
|---|---|---|
| **Cohort growth ≥ 50 new studies** | Re-run § 11.4 calibration; emit `threshold_diff.json`; human reviews per detector. | ~once per 2 months at current intake. |
| **Segmenter / model release** | Re-run all of § 11.4 — mask geometry changed, so Threshold-tier B may shift. | Event-driven. |
| **Phase-4 batch close (≥ 100 new reviews)** | The feedback parquets (§ 12.2) merge into the calibration substrate; a delta calibration runs. | ~monthly. |
| **Default rhythm + Threshold-tier-A audit** | Quarterly health-check: confirm no silent drift; surface a soft alert if a recommended threshold moved > 10%. **This quarterly slot also runs the recurring Threshold-tier-A audit** — re-examining whether the "physics-grounded" defaults (§ 11.1) still hold, the counter-discipline for limitation L3 (§ 13). | Quarterly. |

### 12.7 — The anti-stale discipline

Contracts rot in predictable ways; each gets a counter-discipline.

- **Rot mode 1 — Code drifts, doc doesn't.** A threshold is tweaked in a hotfix; the doc still cites the old value. *Counter:* the threshold registry (§ 12.4) is the source of truth; the doc cites the registry by symbol where practical, and a CI check (`scripts/audit_contract_vs_thresholds.py`) compares every literal number in this document against the registry and fails the build on divergence.
- **Rot mode 2 — New failure modes appear; taxonomy doesn't grow.** *Counter:* the quarterly "Other"-field review (§ 12.2 Channel C) — output is either a new reason-code proposal or an explicit "reviewed, not a pattern" CHANGELOG note.
- **Rot mode 3 — Detectors silently degrade.** Cohort drifts; Threshold-tier B no longer fits. *Counter:* the quarterly health-check (§ 12.6) surfaces the > 10% drift alert even when no other trigger fired.
- **Rot mode 4 — Operator-bias contamination of the training labels.** Operators are themselves the labelling instrument; their overrides may carry systematic bias (limitation L2, § 13). *Counter:* an adversarial re-labelling pass over a sample of historical overrides before the Step-5 learned grader trains — `[Named-deferred]`, *owner: TBD; trigger: before Build-sequence Step 5*.
- **Audit cadence — doc-vs-code reconciliation, and the future-work register sweep.** Once per minor bump: walk the doc top to bottom, confirm every code anchor still resolves, confirm every detector named in § 5 has a registry entry, **and re-check every § 17 future-work and § 13 limitation trigger — migrate any fired item to § 14.** A fired trigger left sitting in § 17 is precisely the silent scope-loss the register exists to prevent, so this sweep is the register's keeper. A half-day task; not skippable; tracked with a ticket created at each minor bump.

**Bottom line.** The contract is a living artifact: thresholds in a registry, every disagreement structurally captured, version bumps with a CHANGELOG, a propagation checklist on every change, named cadences for re-calibration, and a quarterly anti-stale audit. The discipline is not heroic — it is "do this list every time" — and that is the point. Heroism does not scale; checklists do.

---

## § 13 — Limitations register

Per the project discipline of naming what could be wrong *before* an external reviewer does, this register lists every known limitation and latent assumption. Each carries a status (§ 0.2), a mitigation, and an owner. An expandable register: new limitations are added as surfaced; existing ones have their status updated as a v0.x revision addresses them.

| # | Limitation / latent assumption | Status | Mitigation | Owner |
|---|---|---|---|---|
| **L1** | **Monotonic-sweep assumption.** The 5 sweep-stratified unpicked frames (decision D7) sample equal *temporal* zones of the sweep, which equal *spatial* zones only if the operator swept the probe monotonically. If sweeps backtrack, the stratified sample mis-represents spatial coverage, and a Step-5 learned grader could learn a partly-noise spatial-coverage signal. No build step currently tests sweep monotonicity on the cohort. | **[Named-deferred]** | Add an explicit cohort-level sweep-monotonicity test as a prerequisite to Build-sequence Step 7. | *owner: TBD; trigger: before Step 7* |
| **L2** | **Operator-override-as-ground-truth.** Transparency Rule 3 logs operator overrides as training data; § 11.2 step 5 treats operator re-review as truth. But operators are sometimes wrong, hurried, or anchored by the pre-fill. No step does adversarial re-labelling of historical overrides — the Step-5 grader could learn operator biases as features. | **[Named-deferred]** | Adversarial re-labelling pass over a sample of historical overrides before Step 5 trains (§ 12.7 rot mode 4). | *owner: TBD; trigger: before Step 5* |
| **L3** | **Threshold-tier A "universals" may persist unexamined.** § 11.1 ships physics-grounded defaults that bypass calibration. The pixel rails (≥250, ≤5) are genuine physics, but the paired fraction cut-offs (">2% of ROI") are borrowed cohort numbers wearing a "physics-grounded" label. Without a discipline, these could persist for the project's lifetime without empirical validation. | **[Active]** | The recurring quarterly Threshold-tier-A audit instituted in § 12.6 re-examines whether each "universal" still holds. *(v0.2 — status corrected from the undefined "[Active mitigation]" to the § 0.2 tag `[Active]`: the mitigation is implemented; audit fix M2.)* | *owner: whoever runs the § 12.6 cadence* |
| **L4** | **D3 — `_final.png` modality held, not closed.** QA runs on the despeckled `_final.png`, not the raw `.jpg`; the raw-`.jpg` alternative was deliberated and held (§ 2.2), not eliminated. | **[Held]** | Revisit trigger (§ 2.2): a Step-2 cohort calibration showing poor positive/negative separation for the high-frequency detectors (`motion_blur`, `tgc_banding`, `int_cv_within`) — and, **already realized**, the Build-Step 2 Phase-2 finding that `saturation_high_*` is inert on the `_final.png` 244-ceiling modality (L9 / FW-25). Intermediate option: raise segmenter input resolution rather than the full canonical-array swap. | *owner: Akadir; trigger: the Step-2 separation result* |
| **L5** | **Gates are read-only after Accept.** The `data/runs.jsonl` ledger is append-only (§ 7.0). Any retrospective re-labelling — e.g. re-coding historical free-text drop reasons into the structured taxonomy — cannot mutate the ledger; it must write a separate side-table joined on `frame_base`. | **[Named-deferred]** | A side-table joined on `frame_base`, designed when the retrospective re-coding task is scoped. | *owner: TBD; trigger: the § 11 retrospective re-coding task* |
| **L6** | **Legacy frames carry no QA grade.** Frames assessed before the QA layer existed have `image_quality_aggregate_tier = 'ungraded'`; they are never silently treated as `valid` (§ 8.6), but they are also not graded. | **[Named-deferred]** | A v0.x retrospective re-grading pass that runs the detectors over the cached legacy frames. | *owner: TBD; trigger: a v0.x re-grading pass* |
| **L7** | **The calibration substrate is minimal until a cohort assessment pass runs** *(surfaced in v0.2 — Comment 18, audit M1)*. As of 2026-05-20 the labelled QA substrate is 8 finalised runs in `data/runs.jsonl` (§ 7.0, § 11.2), and it does not grow on its own. Every Threshold-tier B detector is un-deployable at the § 11.0 publish bar until the substrate exists, so much of the QA layer ships dormant ("ungraded — pending calibration", § 8.6) until then. | **[Named-deferred]** | Build-Step 1's cohort assessment pass over the existing cohort, and/or the Round-3 data collection (the `Görüntü Alma Usulleri v3` protocol — § 17). | *owner: Akadir + implementer; trigger: Build-Step 1 cohort assessment pass* |
| **L8** | **Single-frame "compressed" detection is intrinsically weak** *(surfaced in v0.2 — Comments 3 / 20)*. A thin subcutaneous-fat layer on one frame cannot be distinguished from a genuinely lean patient (`subq_fat_compressed`, § 5.2); the v0.2 single-frame detector ships, but its `qmt_mm` / `star_ratio` gating cell is provisional (§ 6.2). | **[Named-deferred]** | The sweep-consistency disambiguator — subcutaneous-fat thickness across consecutive sweep frames — i.e. the sweep-level workstream in the § 17 register. | *owner: TBD; trigger: the sweep-level workstream* |
| **L9** | **`saturation_high_*` is inert on the `_final.png` modality** *(surfaced by the Build-Step 2 Phase-2 cohort histogram, 2026-05-21)*. `saturation_high_global` / `_regional` key on a ≥ 250 grayscale rail (§ 5.2), but the `_final.png` modality QA runs on (decision D3) caps at pixel value 244 cohort-wide (956/956 transport frames; the device `_original.jpg` caps there too — a device-LUT ceiling). The rail sits above the ceiling, so those detectors can never fire on the canonical QA modality — a genuinely gain-saturated scan produces no flag. (`dark_clip_high` is unaffected: the `_final.png` compresses only the bright end, so the ≤ 5 rail is reachable — it fires ~18 %.) | **[Named-deferred]** | A Build-Step 2b design-review gate (§ 14 Step 2b, FW-25): re-pin the rail to the measured modality ceiling — which reclassifies it Threshold-tier B / modality-calibrated — or run saturation QA on the `_raw.png` (which does reach 254), partly re-opening D3 (§ 2.2 / L4). | *owner: Akadir + implementer; trigger: Build-Step 2b* |

---

## § 14 — Build sequence

The implementation roadmap — Steps 0–7. **Read it as an ordered list of deliverables, not a set of locked gates** (Akadir, Comment 13: *"I don't want to put artificial barriers/steps; keep our pace, and keep our pace informed by the ongoing implementation itself"*). The only genuinely hard ordering constraint is **data-dependency** — a detector cannot be calibrated before the labels it calibrates against exist — and that is physics, not bureaucracy. Everything else runs continuously and is informed by the build itself: a later step may start before an earlier one is "finished" wherever no data-dependency forbids it. The lettered facets (2a / 2b, and the half-numbered 4.5 / 6.5) are *parts of* their parent step, surfaced for clarity — not extra stages to clear. Each row carries a **status**, a **validation criterion** (the measurable test for "done"), an **honest estimate** (engineering wall-clock kept distinct from operator-assessment calendar time — different timelines, and conflating them is dishonest), and an **owner**.

| Step | What it delivers | Status | Validation criterion ("done" =) | Honest estimate | Owner |
|---|---|---|---|---|---|
| **0** | **This canonical plan** — the QA contract, detector specs, gating, UI design, calibration methodology, build sequence. v0.2 folds in Akadir's 28-comment review + an independent critical-reader audit. | **[Active]** — you are reading the deliverable (v0.2) | Akadir ratifies v0.2. Q1 (Anonaao's § 4 + § 6 clinical check) is no longer a Step-0 blocker — it is tracked in § 10 / § 17. | ~half a day (done) | Claude + Akadir |
| **1** | Phase-4 structured capture: the checkbox drop-reason group + `qa_overrides` + the gates-dict extension (§ 7.4, § 8.3.5), **plus the v0 assessor's guide** — the ~2-page rubric the cohort assessment pass runs against (per-gate decision rules, one line per reason code, the per-study workflow, 3–4 worked examples; FW-23 carries its later migration into the in-UI `explain` affordance). | **[Active]** | The form **and the v0 guide** ship, **and** one full cohort assessment pass has completed against them (the audit flagged the form-vs-fill ambiguity — *Step 1 is "done" only when the cohort assessment pass has run*, not when the form merely ships). | **Two distinct timelines, do not conflate:** the form refactor + the v0 guide is ~1.5 engineering days; the cohort assessment pass is ~50–100 operator-hours — weeks of operator time on a separate calendar. | implementer (form + guide draft); Anonaao (guide clinical review, folds into FW-2); operators (the cohort assessment pass) |
| **2a** | Threshold-tier A detectors ship + the cohort-wide failure-mode histogram. | **[Active]** | All Tier-A detectors run on the full cohort and the failure-mode-distribution histogram is produced. No labels needed. | ~2–3 engineering days; runs day-one. | implementer |
| **2b** | Threshold-tier B post-label calibration — the § 11.2 five-step process for every Tier-B detector. **A design-review gate precedes the threshold fit for two detectors, surfaced by the Build-Step 2 Phase-2 cohort histogram:** `wrong_site` — review the `rf_fraction` denominator before calibrating the cut-off (VI is anatomically normal in every RF frame yet sits in the denominator, inflating the cohort fire rate to 79.8 %; FW-24, § 5.2); and `saturation_high_*` — decide the QA modality before calibrating the fraction (the ≥ 250 rail is inert on the `_final.png` 244-ceiling modality; FW-25, § 13 L9). | **[Active]** — needs Step-1 labels (a data-dependency, not a bureaucratic gate) | Every Tier-B detector has a calibration report at the § 11.0 publish bar (threshold + Wilson-CI sens/spec + κ baseline); the two design-review gates are resolved before their detectors are calibrated. | ~3–5 engineering days *after* the Step-1 cohort assessment pass completes. | implementer |
| **2.5** | **IRR baseline — the inter-rater reliability study.** Two operators independently assess 150 stratified frames; Cohen's κ ≥ 0.6 per code (§ 11.3). | **[Active]** | κ is reported per reason code. This is a **publish-claim gate, not a build-progress gate** (Comment 13): it does not block *building* or *iterating* detectors — it gates only the *claim*, since a detector's sensitivity/specificity cannot be reported at the § 11.0 bar until the κ ceiling it is measured against is known. The build proceeds in parallel; the IRR study is real methodology, not a barrier. | ~1 operator-day per assessor. | implementer + a second assessor (Anonaao or a second operator — named before the study runs) |
| **3** | Tier-1 confidence-map detectors (`anisotropy_dropout_*`, `deep_attenuation_high`, `oblique_plane` Radon component). | **[Conditional]** — per § 2.3 deferral | A one-page Tier-1 technical spec exists first (§ 2.3 handoff requirement); then each detector reaches a stated threshold-quality bar (sensitivity at the § 11.2 step-3 targets) that gates Step 3 → 4. | 5–10 engineering days — confidence-map implementation is non-trivial. *Condition: a Step-2 readout shows Tier-0 misses clinically meaningful anisotropy/attenuation cases.* | owner: TBD per the one-page spec |
| **4** | Promote `frame_rf_consistency` from a passive metric to a gate; generalise the QMT-validity pattern to the other measurements. | **[Done]** — Build-Step 2 Phase 3 items 8 + 9a | A measurable acceptance test: `frame_rf_consistency` actually gates a frame in the aggregation, and the generalised pattern is applied to ≥1 non-QMT measurement with a passing integration test. **Met:** item 8 generalised the validity pattern (`_aggregate_with_image_quality`, proven on `int_cv_within` + `rf_thickness_mm`); item 9a added the `frame_rf_consistency` per-run modified-Z outlier gate (`_rf_consistency_report`). | ~2 engineering days. | implementer |
| **4.5** | Post-Tier-1 retrospective re-calibration. Tier-1 introduces new reason codes; some Step-1 "valid" frames become re-classifiable, so the Step-2b ROC operating points are stale. | **[Conditional]** — fires only if Step 3 shipped | The § 11.2 calibration is re-run after Step 3, against a label set that now includes the Tier-1 reason codes. | ~1–2 engineering days. | implementer; *condition: Step 3 shipped* |
| **5** | The Tier-2 learned grader. | **[Conditional]** — gated on per-class data | **Gated on a per-reason-code minimum: ≥ 200 labelled positives per code**, not on "Step 1 has happened" — a single cohort assessment pass will not yield 200 positives for the rare codes. Validated against the Tier-0 gate: they must agree on gross cases; the learned grader earns its keep only on the borderline band. | Training is ~1–2 weeks; **that is training time, not the labels-accumulating wall-clock** — reaching ≥200/code for rare codes may take several cohort assessment passes across months. | implementer; operators (the cohort assessment pass) |
| **6** | Wire `image_quality_tier()` into the provenance-gate aggregation (§ 7.3). | **[Active]** | An integration test: a frame flagged `invalid` for a measurement is actually excluded from that measurement's aggregate, and the aggregate re-pools correctly. | ~1 engineering day once Steps 2–4 land. | implementer |
| **6.5** | Operator-acceptance / clinical sign-off. An operator-blinded acceptance round on N held-out studies — do operators agree with the system's final integrated tier assignments? (Distinct from § 11.2 step 5, which is per-detector; this is system-level.) | **[Active]** | Operator-system agreement on the final tier assignments for N held-out studies meets a pre-stated bar. | ~1–2 operator-days. | **owner: Anonaao** (clinical sign-off) |
| **7** | Diversity-aware multi-frame recommender — auto-suggest 3–5 frames that are individually good *and* collectively spread across the muscle. | **[Future-direction]** for v0.2; **v1-committed** — Q4 resolved (Comment 25: parked now, but part of v1, a small sub-sequencing) | — (parked, not scoped in v0.2 — § 10 Q4). Prerequisite: the L1 sweep-monotonicity cohort test (§ 13). | TBD | *owner: Akadir; trigger: Steps 1–6 complete* |

**The one real ordering constraint — data-dependency, not gates.** Step 1's label capture must exist before Step 2b's calibration: calibration with nothing to validate against is meaningless. That is the single hard sequencing rule, and it is a data-dependency, not a checkpoint. The IRR study (2.5) gates the *publish claim*, not build progress (see its row). And the brainstorm's "Step 5: do **not** start here" stands — a learned grader built before Steps 0–1 would hard-code a guessed taxonomy. Beyond these, the build keeps its own pace and is informed by the implementation itself (Comment 13) — steps overlap freely wherever no data-dependency forbids it.

---

## § 15 — Worked examples

This section walks two real frames through the contract end to end — § 4 catalogue → § 5 detector → § 6 gating → § 6.3 aggregate → § 8 surface. It is the plan's highest-density explainability artifact: a reader who follows both examples has seen every moving part connect.

**Honesty frame — read before the examples.** v0.2 is a *plan*; the Tier-0 detectors are Build-sequence Step 2 (§ 14) and are **not yet implemented**. So these examples walk the *contract logic*, not recorded detector output. What is real: the **frames, studies, and measurement values** are live data from `data/runs.jsonl`, verified 2026-05-20. What is illustrative: the **QA flags** — every flag below is marked `[spec'd]` (the outcome the contract specifies for that frame state) or `[illustrative]` (a plausible flag chosen to exercise the gating). **No detector score is invented.** When Step 2 lands, both walkthroughs are re-run against real detector output — that re-run is the worked-example reality check.

### 15.1 — Example A: a valid frame (the silent path)

**The frame.** Study **ACL762**, anchor frame **`20260101_114816166`**. In Phase 4 the operator accepted it (gates: usable = true, use-for-analysis = true). It anchors a 3-frame aggregate; its QMT provenance is `qmt_tier1_standard` — the clean, femur-anchored tier, meaning the femur was found within the central zone.

**Real measurements** (the run's aggregate, with this frame as anchor): `qmt_mm` 36.88 mm · `star_ratio` 1.71 · `rf_thickness_mm` 18.03 mm · `full_thickness_q50_mm` 15.95 mm · `int_cv_within` 0.623 · `muscle_fat_ratio` 2.45 · `rf_area_mm2` 635.8 mm².

**The walkthrough.**

1. **§ 5 — detectors.** `compute_image_quality_flags(img_gray, mask)` runs every `[Active]` Tier-0 detector. For an operator-accepted frame whose QMT is `tier1_standard` (femur present and central), the contract-specified outcome `[spec'd]` is that **no flag fires**: the femur is in the central zone (so neither `femur_out_of_frame` nor `femur_off_central_zone`), the RF mask does not touch a lateral border, intensity is within the rails, the fascia is sharp. Result: `image_quality_validity_flags = ()`.
2. **§ 6 — gating.** `image_quality_tier(measurement, ())` — with an empty flag tuple, no cell in the § 6.2 table is INV or BDR for any measurement → **every measurement is `valid`**.
3. **§ 6.3 — aggregate.** All four headline measurements valid, and all 8 of the 8 gating-table measurements valid (100% — well above the 60%, i.e. 5-of-8, floor; § 6.3) → **aggregate tier = Valid**.
4. **§ 8.3.1 — badge.** The Phase-3 thumbnail shows `:green[● valid]`. No dominant reason (no flags).
5. **§ 8.3.2 — drilldown** (if the operator clicks): every measurement row reads ✓ valid; header "Aggregate: VALID — 0 flags."
6. **§ 8.3.4 — Phase-4 pre-fill.** Gate 1 ("Is this frame usable?") pre-fills to **"Yes"** with no reasons attached; the operator confirms.

**Teaching point.** The valid path is *silent by design*. A good frame earns a green badge and a pre-confirmed gate, and the operator's workflow is barely touched — which is exactly right. The QA layer must not tax the operator on the frames that are fine; it earns its keep on the frames that are not. Here, the QA layer and the human agreed (both said "use it"), the common and frictionless case.

### 15.2 — Example B: a borderline frame (the gating chain, and a real divergence)

**The frame.** Study **AKA9001**, frame **`20260514_181856256`**. This is a real frame the operator **dropped** in Phase 4 (gates: usable = false, use-for-analysis = false) — one of 3 frames dropped from an 8-frame selection, leaving 5 in the aggregate. Critically: the operator's **`drop_reason` field is `null`** — the free-text box was left empty. *From the ledger we cannot tell why the operator dropped this frame.* **That blank is the precise motivation for Build-sequence Step 1** (§ 14): the structured checkbox capture exists so this "why" is never lost again.

**The walkthrough** `[illustrative]` — the detectors are Step-2 work; suppose the QA layer graded this frame and `compute_image_quality_flags` returned `image_quality_validity_flags = ('femur_off_central_zone', 'edge_clip_lateral')`. This is a plausible pair for a dropped frame — the femur drifted toward one side, and the RF mask touches the lateral border — and it is chosen because it exercises the two subtlest parts of § 6.

1. **§ 6.2 — gating, per measurement.**
   - `femur_off_central_zone` → `qmt_mm` **BDR**, `star_ratio` **BDR** (the femur is present but off-centre, so QMT degrades to a proxy rather than failing outright).
   - `edge_clip_lateral` → `rf_area_mm2` **INV**, and *nothing else* — this is the coverage-limitation rule (§ 5.2, Akadir redline C11): lateral truncation invalidates only the full-cross-section area features; it does **not** touch `qmt_mm`, `star_ratio`, `rf_thickness_mm`, or the intensity measurements.
   - Combined per-measurement tiers: `rf_thickness_mm` **valid** · `qmt_mm` **borderline** · `star_ratio` **borderline** · `full_thickness_q50_mm` **valid** · `rf_area_mm2` **invalid** · `int_cv_within` **valid** · `rf_echo` **valid** · `muscle_fat_ratio` **valid**.
2. **§ 6.3 — aggregate.** The headline-4 are `star_ratio` (borderline), `full_thickness_q50_mm` (valid), `int_cv_within` (valid), `rf_thickness_mm` (valid). At least one headline-4 is borderline and none is invalid → **aggregate tier = Borderline**. Note `rf_area_mm2` is *invalid* — but it is a secondary measurement, not headline-4, so it does **not** force the aggregate to Invalid. This is the coverage-limitation rule doing its job: a laterally-clipped frame is not condemned wholesale; only its area feature drops out of the pool, while QMT, STAR, and thickness remain usable.
3. **§ 5.1 — dominant reason.** `femur_off_central_zone` sits higher than `edge_clip_lateral` in `IMAGE_QUALITY_FLAG_PRIORITY`, so it is the dominant-reason badge text.
4. **§ 8.3.1 — badge.** `:orange[● borderline · femur_off_central_zone]`.
5. **§ 8.3.2 — drilldown.** GEOMETRY block: `rf_thickness_mm` ✓ valid; `qmt_mm` ⚠ borderline — *femur_off_central_zone*; `rf_area_mm2` ✗ invalid — *edge_clip_lateral (lateral truncation; QMT, STAR and thickness are unaffected — coverage limitation, § 5.2)*; `full_thickness_q50_mm` ✓ valid. INTENSITY block: all valid.
6. **§ 8.3.4 — Phase-4 pre-fill.** The aggregate is Borderline, not Invalid → Gate 1 pre-fills to **"Yes"** (borderline means use-with-annotation, not drop), with the two borderline/invalid reasons shown inline for the operator to confirm or override.

**The divergence — and why it is the whole point.** The contract grades this frame **Borderline / usable-with-caveat**. The real operator **dropped it entirely**. They disagree — and neither is "wrong." The operator may have seen a third failure mode the two illustrative detectors did not model, or may simply hold a stricter bar than the contract. **Today we cannot tell, because `drop_reason` is `null`.** Under v0.2 we would: the operator's override is captured by `override_action` (§ 8.3.6) as a `human_decision` against the algorithmic `algo_tier`, and that record flows through § 12.2 Channel A into calibration. Frame by frame, that closes the gap between what the detectors see and what the clinical eye sees — which is the entire reason the QA layer exists.

### 15.3 — What the two examples together demonstrate

Example A shows the valid path is silent and frictionless. Example B shows the gating chain in full — the per-measurement INV/BDR mapping, the C11 coverage-limitation rule (a clipped frame keeps its non-area measurements), the priority-ordered dominant reason, the Borderline aggregate, the pre-fill — *and* the operator/contract divergence that is the QA layer's reason for being. A reader who has followed both has seen § 4 through § 8 connect on real data.

---

## § 16 — CHANGELOG

Every revision of this contract appends a row. Each row points to the evidence that justifies the change (per § 12.3).

| Version | Date | Author | Change summary | Source / rationale |
|---|---|---|---|---|
| v0 | 2026-05-18 | Claude Opus 4.7 + Akadir | Initial Step-0 design pass — the RF frame image-quality QA contract: failure-mode catalogue, detector sketches, per-measurement gating, the four transparency rules, calibration methodology. | commit `ed22f9f` |
| **v0.1** | **2026-05-20** | **Claude Opus 4.7 + Akadir** | **Canonical plan — a single self-sufficient artifact** absorbing v0 + the four-agent audit + Akadir's 2026-05-19 redline read-pass + the four-critic review. Adds the load-bearing § 8 UI/UX integration section (drafted in full); applies the § 0.2 status taxonomy throughout; adds § 12 continuous-refinement discipline and § 13 limitations register; absorbs the brainstorm's A–G detector-class taxonomy and build order; carries the technical-audit detector rewrites verbatim (`motion_blur` → fascia-band Tenengrad, `tgc_banding` → detrended residual, `air_gap_reverberation` → segmentation-anchored FFT); splits `saturation_high` into global/regional; adds 4 failure modes and `wrong_site`'s Tier-0/B detector; corrects code anchors (`FrameFeatures` 24 fields, `aggregation.py:80`, the cache-invalidation anchors); reconciles the gating table to the C11 coverage-limitation rule. **Supersedes** the v0 contract, the redline doc, the integration prompt, and the brainstorm. | `docs/_audits/contract_v0_audit/` (audits 01–04 + `05_akadir_redlines.md` + `INTEGRATION_PROMPT.md`) |
| **v0.2** | **2026-05-20** | **Claude Opus 4.7 + Akadir** | **Review-and-audit revision.** Folds in Akadir's 28-comment review of v0.1 (a Google-Doc review pass) and an independent critical-reader audit (7 HIGH / 10 MED / 5 LOW findings, all dispositioned). Promotes `segmentation_low_confidence` to `[Active]` (Comment 2 — softmax exposure is a ~15-LOC change, confirmed by a segmenter feasibility audit); adds § 0.4 nomenclature key (Comment 23); enriches the § 4.2 catalogue with detector mechanism + threshold-tier (Comments 5/7/8/9); adds § 8.9 — the QA surface as a data-collection instrument — plus feedback Channel D (Comments 9/14/15/16/19/20/21/22); reframes § 14 so gates bind the publish-claim, not build progress (Comment 13); corrects the § 11.2 calibration substrate (labelled substrate is 8 runs, not "~4,500 growing to ~6,000" — Comment 18); refines `near_field_clutter`, `subq_fat_compressed`, `tgc_banding`, `saturation_high_regional`, `edge_clip_vertical`, `wrong_site`; commits Q3 to a separate `ImageQualityFeatures` dataclass; resolves open questions Q1–Q5 (Comments 24–28); adds § 17 future-work register (Comment 22); applies all 7 HIGH and 10 MED audit findings (gating-table/spec reconciliation, the 60% denominator, the compound-FPR independence caveat, the performance-budget overclaim, the `wrong_site` off-thigh gap, et al.); disambiguates the version axis — the plan is the contract and ships as v0.2, with `CONTRACT_VERSION` and all build references renumbered off the stale v0.1 (final-audit pass). **Supersedes v0.1.** Per-comment dispositions and audit findings: `docs/_audits/contract_v0_audit/07_v0_2_comment_dispositions.md`. *(2026-05-21 editorial: the v0 assessor's guide is scoped into Build-Step 1 (§ 14); FW-23 records its deferred in-UI maturation; § 12.7's audit cadence is extended to sweep the § 17 / § 13 triggers — naming the register's keeper; and the plan's wording is recast from "labelling round" to "cohort assessment pass" — the guide is the "assessor's guide" — so the language matches the app's assessment identity, per Akadir's steer.)* | Akadir's review Google Doc; `07_v0_2_comment_dispositions.md`; segmenter feasibility + critical-reader audits |
| **v0.2** | **2026-05-21** | **Claude Opus 4.7 + Akadir** | **Build-Step 2 Phase-2 findings folded into the pipeline.** Build-Step 2's detector implementation + the cohort failure-mode histogram (956 transport frames) surfaced findings that defer to Build-Step 2b; they are now explicit, tracked pipeline items, not loose notes. § 14 Step 2b names a `wrong_site` denominator design-review gate and a `saturation_high_*` modality gate that precede the threshold fit; § 13 adds L9 (`saturation_high_*` inert on the `_final.png` 244-ceiling modality) and updates L4's D3-revisit trigger; § 17 adds FW-24 (`wrong_site` denominator) and FW-25 (`saturation_high_*` modality). No contract-schema change — `CONTRACT_VERSION` unchanged. | Build-Step 2 Phase-2 cohort histogram (`data/cohort_stats/qa_failure_mode_histogram.json`); `HANDOFF.md` Findings B + C |
| **v0.2** | **2026-05-22** | **Claude Opus 4.7 + Akadir** | **Build-Step 2 Phase 3 item 9 — the `frame_rf_consistency` gate (§ 14 Step 4 delivered).** `frame_rf_consistency` is promoted from a passive sweep-σ metric to an active *per-run* outlier gate — an Iglewicz-Hoaglin modified-Z test on `rf_thickness_mm` that drops a sweep-outlier frame from the aggregate pool (§ 3.4's `[Named-deferred]` resolved; § 14 Step 4 → `[Done]`). § 11.1 gains a Threshold-tier-A row for the modified-Z cut (3.5) — a scale-invariant published statistical standard — with a caveat note placing it under the § 12.6 Threshold-tier-A audit. An independent implementation audit tightened the gate: scoped to per-run aggregates only (out of the N5 bootstrap pool, where the sweep-sibling premise fails and a duplicate `frame_base` can occur); the audit payload records `range_mm` + `trimmed_median_mm`; tiny-MAD over-fire and small-N coarseness are recorded as Build-Step 2b calibration items. No contract-schema change — `CONTRACT_VERSION` unchanged. | Build-Step 2 Phase 3 item 9; `HANDOFF.md`; the item-9 implementation audit |
| **v0.2** | **2026-05-22** | **Claude Opus 4.7 + Akadir** | **Build-Step 2 Phase 3 item 10a — the deployed-vs-raw tier schema (gate H1).** § 7.1's `ImageQualityFeatures` gains one field — `image_quality_pending_calibration_flags`, the subset of `image_quality_validity_flags` whose detector is un-calibrated (Finding B's excluded set). It resolves the named item-10 hard gate: `image_quality_aggregate_tier` is the *deployed* verdict (§ 6.3 over the calibrated flags only), and a UI badge must not render over that ambiguity until the schema names the excluded set. Akadir ratified the additive-field option over a rename or a companion raw-tier. The field is back-compatible (a pre-H1 ledger record loads it as `()`) and a materialized view of the existing `calibrated_flags()` partition — so `CONTRACT_VERSION` is unchanged. | Build-Step 2 Phase 3 item 10a; `docs/qa_ui_surfacing_plan.md` § H1; `HANDOFF.md` hard gates |

---

## § 17 — Future-work register

Comment 22 asked for "a canonical entry for the future work … a checklist … so we can keep track of what we did and how we are going to expand/refine." This section is it: one consolidated index of everything the plan defers, parks, holds, or conditions — every `[Named-deferred]`, `[Future-direction]`, `[Conditional]`, and `[Held]` item, plus the data-collection and clinical-review workstreams. Current v0.2 build work is **not** here — that is § 14. This register is an *index*: each item's authoritative owner and trigger live at its home section (per the § 0.2 global rule); where they differ, the home section governs. When an item moves into active build it migrates to § 14, and the move is recorded in the § 16 CHANGELOG. The register does not track itself: its keeper is the § 12.7 audit sweep — at every minor bump each item's trigger is re-checked and any fired item migrated. Items written with a *hard, event-based* trigger (a build step reached, a cohort assessment pass completed) self-surface at that event; the sweep is the backstop for the rest. A trigger phrased as "someday" or "as things mature" is the rot-prone kind — prefer a concrete event.

| # | Item | Status | Owner | Trigger | Home § |
|---|---|---|---|---|---|
| FW-1 | Round-3 data collection — the `Görüntü Alma Usulleri v3` acquisition protocol (cleaner, better-recorded data + multi-operator and 24 h test-retest sub-cohorts); one of the two routes that grow the QA calibration substrate | [Future-direction] | Akadir + Dr. M | Dr. M ratifies the v3 protocol | § 11.2; § 13 L7 |
| FW-2 | Anonaao's clinical sanity-check of § 4 (failure catalogue) + § 6 (gating table) — open question Q1 | [Named-deferred] | Anonaao | Anonaao's review pass, handled "at the right point" | § 10 |
| FW-3 | Tier-1 confidence-map detectors — `anisotropy_dropout_*`, `deep_attenuation_high`, `oblique_plane` Radon component | [Conditional] | TBD per the one-page Tier-1 spec | Step-2 readout shows Tier-0 misses clinically meaningful cases; one-page Tier-1 spec written first | § 2.3; § 5.3; § 14 Step 3 |
| FW-4 | Tier-2 learned grader (Build-Step 5) | [Conditional] | implementer + operators | ≥ 200 labelled positives per reason code | § 11.7; § 14 Step 5 |
| FW-5 | `segmenter_mislabel_wrong_class` (F2) — the residual wrong-site case the class-composition detector cannot catch | [Named-deferred] | TBD | Step-5 learned grader, or a cross-class confidence detector | § 5.2 |
| FW-6 | `posterior_acoustic_enhancement` (F15) — detector named for taxonomy completeness, not built | [Named-deferred] | TBD | a v0.x detector pass | § 5.2 |
| FW-7 | `probe_orientation_flipped` (F22) — detection of a mirrored-but-correct muscle | [Named-deferred] | TBD | a directional measurement (e.g. fascicle angle) is added | § 5.2 |
| FW-8 | Sweep-level workstream — sweep-aggregate features, sweep-consistency (incl. the `subq_fat_compressed` compressed-vs-lean disambiguator), sweep-level stats feeding the recommender; Round-3 preserves sweep MP4s for exactly this | [Future-direction] | TBD | v0.2 per-frame QA validated on the cohort | § 1.3; § 5.2; § 13 L8 |
| FW-9 | Diversity-aware multi-frame recommender (Build-Step 7) | [Future-direction] for v0.2; **v1-committed** (Q4) | Akadir | Build Steps 1–6 complete | § 14 Step 7; § 10 Q4 |
| FW-10 | Expert-review mode — a curated flow that runs 3–5 domain experts through frames and captures their grades structurally | [Future-direction] | Akadir | first external-expert review session | § 8.9 |
| FW-11 | "Practitioner-aide" UI scope — fully surfacing the academic / theoretical background; the progressive-disclosure `explain` affordance ships in v0.2, the full scope is later | [Future-direction] | Akadir | post-v0.2 UI iteration | § 8.9 |
| FW-12 | In-app calibration-ROC viewer (for now `calibration_link` opens the JSON/PNG externally) | [Future-direction] | TBD | post-v0.2 UI iteration | § 8.8 |
| FW-13 | Full WCAG accessibility audit (v0.2 ships the ARIA + keyboard + text-alternative substrate) | [Named-deferred] | TBD | a pre-clinical-deployment review | § 8.3.9; § 8.8 |
| FW-14 | Acquisition-time QA — the taxonomy run at scan time (decision D2); the Round-3 protocol's §10 operator QA checklist is already a partial form | [Future-direction] | TBD | a dedicated acquisition-side workstream | § 1.3; § 2.1 D2 |
| FW-15 | Speed-of-sound mismatch (open question Q5) — a possible high-BMI geometric confound | [Future-direction] / v2 | Akadir (holds the device SDK paper) | v2 scoping | § 10 Q5 |
| FW-16 | BH-FDR compound-FPR control — a formal false-discovery-rate control over the reason-code stack, if two-detector concurrence proves too blunt | [Future-direction] | TBD | Step-2 compound-FPR readout shows concurrence insufficient | § 11.5 |
| FW-17 | Retrospective re-coding of historical free-text drop reasons into the structured taxonomy — needs a side-table joined on `frame_base` | [Named-deferred] | TBD | the § 11 retrospective re-coding task | § 7.0; § 13 L5 |
| FW-18 | Legacy-frame re-grading — run the detectors over frames assessed before the QA layer existed | [Named-deferred] | TBD | a v0.x re-grading pass | § 8.6; § 13 L6 |
| FW-19 | Adversarial re-labelling of historical overrides before the learned grader trains (guards operator-bias contamination) | [Named-deferred] | TBD | before Build-Step 5 | § 12.7; § 13 L2 |
| FW-20 | Cohort sweep-monotonicity test — verify sweeps do not backtrack before the recommender relies on temporal-zone sampling | [Named-deferred] | TBD | before Build-Step 7 | § 13 L1 |
| FW-21 | "Tyler et al. 2024" citation — source it with full bibliographic form, or replace the `subq_fat_compressed → qmt_mm` magnitude claim | [Named-deferred] | TBD | any paper write-up or clinical-deployment review | Appendix A.2; § 5.2; § 6.2 |
| FW-22 | D3 — raw-`.jpg` vs `_final.png` modality: held, not closed | [Held] | Akadir + implementer | Step-2 separation result for the high-frequency detectors | § 2.2; § 13 L4 |
| FW-23 | Assessor's guide — in-UI maturation: the v0 guide (drafted in Build-Step 1) migrates into the § 8.9(2) progressive-disclosure `explain` affordance as operator feedback from the first cohort assessment pass accumulates. Cross-refs FW-11 (practitioner-aide UI scope). | [Future-direction] | Akadir + implementer | the first Build-Step 1 cohort assessment pass completes — operator feedback on the v0 guide in hand | § 8.9; § 14 Step 1 |
| FW-24 | `wrong_site` denominator design-review — the Build-Step 2 Phase-2 cohort histogram showed `wrong_site` fires on 79.8 % of correct RF frames because VI (anatomically normal directly under RF — part of the RF+VI QMT stack) sits in the `rf_fraction` denominator. A design-review gate decides the denominator (candidate: drop VI — necessary but not sufficient: at the 0.5 cut-off it still fires 24.6 %, so the cut-off must also be re-calibrated) before Build-Step 2b fits the threshold. | [Named-deferred] | implementer + Akadir | Build-Step 2b — precedes the `wrong_site` threshold fit | § 5.2; § 14 Step 2b |
| FW-25 | `saturation_high_*` modality fix — the `_final.png` caps at pixel value 244 (§ 13 L9), below the 250 saturation rail, so `saturation_high_global` / `_regional` are inert on the canonical QA modality. Decide: re-pin the rail to the measured modality ceiling, or move saturation QA to the `_raw.png` (which reaches 254). | [Named-deferred] | Akadir + implementer | Build-Step 2b — precedes the `saturation_high_*` calibration | § 13 L9; § 2.2; § 14 Step 2b |

---

## Appendix A — Source materials and references

### A.1 — Lineage and superseded artifacts

This canonical plan is the convergence of a documented design arc. It **absorbs and supersedes** four documents — their load-bearing content is carried into this plan in full, so a reader never needs to open them, and each receives a one-line `SUPERSEDED` header pointing here:

- `docs/rf_frame_qa_contract_v0.md` — the v0 contract (commit `ed22f9f`).
- `docs/_audits/contract_v0_audit/05_akadir_redlines.md` — Akadir's 2026-05-19 redline read-pass (16 anchored comments + 2 added paragraphs).
- `docs/_audits/contract_v0_audit/INTEGRATION_PROMPT.md` — the v0→v0.1 integration prompt.
- `docs/rf_frame_qa_qc_brainstorm_en_tr.md` — the brainstorm; origin of the A–G detector-class taxonomy (§ 4.1) and the Step-0–6 build order (§ 14).

The four agent audits — `01_clarity_audit.md`, `02_technical_audit.md`, `03_path_to_rigor_audit.md`, `04_proposed_section_12.md` — are retained **unchanged** as the audit trail. Their load-bearing findings are absorbed into this plan (so it is self-sufficient); they remain on disk only as provenance, referenced from here, never as live dependencies a reader must follow.

### A.2 — Academic references

Five papers anchor the load-bearing method choices. Citation status (`[Active]` / `[Future-direction]`) is per § 11.6.

1. **Karamalis A., Wein W., Klein T., Navab N.** "Ultrasound confidence maps using random walks." *Medical Image Analysis* 16(6):1101–1112, 2012. — `[Active]` anchor for the Tier-1 Class-C confidence-map detectors (§ 5.3).
2. **Loizou C.P., Pattichis C.S., Pantziaris M., Tyllis T., Nicolaides A.** "Quality evaluation of ultrasound imaging in the carotid artery based on normalization and speckle reduction filtering." *Medical & Biological Engineering & Computing* 44(5):414–426, 2006. — `[Active]` anchor for replacing `motion_blur`'s variance-of-Laplacian with fascia-band Tenengrad (§ 5.2): direct evidence that Laplacian-family metrics conflate with speckle in B-mode ultrasound.
3. **Mittal A., Moorthy A.K., Bovik A.C.** "No-Reference Image Quality Assessment in the Spatial Domain" (BRISQUE). *IEEE Transactions on Image Processing* 21(12):4695–4708, 2012. — `[Future-direction]`: the comparison baseline if a Tier-2 learned grader is built.
4. **Baumgartner C.F. et al.** "SonoNet: Real-Time Detection and Localisation of Fetal Standard Scan Planes in Freehand Ultrasound." *IEEE Transactions on Medical Imaging* 36(11):2204–2215, 2017. — `[Future-direction]`: the closest prior art for a learned `wrong_site` / view classifier (Build-sequence Step 5).
5. **Abdi A.H. et al.** "Automatic Quality Assessment of Echocardiograms Using Convolutional Neural Networks: Feasibility on the Apical Four-Chamber View." *Ultrasound in Medicine & Biology* 43(7):1395–1404, 2017. — `[Future-direction]`: the prior art the per-measurement structure (decision D1) deliberately argues against (Abdi uses a single 0–5 scalar); cited as the foil.

**Citation gap to close before any paper write-up.** The `subq_fat_compressed` → `qmt_mm` gating (§ 5.2, § 6.2) cites "Tyler et al. 2024" for the ~1–3 mm QMT bias per 5 N of probe compression force. That reference appears in `02_technical_audit.md` *without* full bibliographic form (no title, venue, or DOI). It is `[Named-deferred]` as a citation — *owner: TBD; trigger: any paper write-up or clinical-deployment review must source it properly or replace the claim with one that has a verified anchor.*

### A.3 — How v0.1 was produced and verified

v0.1 was produced by: (a) the four-agent audit of v0 (clarity, technical, path-to-rigor, proposed § 12); (b) Akadir's 2026-05-19 redline read-pass; (c) a four-critic review of the integration meta-plan (explainability, consistency, weak-association, completeness); (d) a four-subagent absorption pass that carried the audit and brainstorm content into this plan and verified every code anchor against the live repository (2026-05-20). Before being declared canonical, v0.1 is checked against a five-layer verification ladder — self-audit, code-anchor verification, worked-example reality, audience-routing coherence, and a STEM-outsider acid test — and a 25-question critical-reader checklist. The verification record is held in `docs/_audits/contract_v0_audit/`.

---

*End of RF Image-Quality QA/QC Canonical Plan v0.2.*

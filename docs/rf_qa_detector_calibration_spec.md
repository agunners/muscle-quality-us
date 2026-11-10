# RF QA — per-detector calibration spec (v0.2)

*The contract for the per-detector calibration loop. This doc IS the source of truth for what the operator is asked, what they see while answering, how the score is revealed, and how the label is fit into a threshold. Subsystems A–K of the calibration loop (see `~/.claude/plans/ok-now-we-plan-adaptive-yeti.md`) implement what is specified here; the Python-module spec at `labeling_app/qa_calibration_spec.py` mirrors this doc machine-readably and loud-fails at import on drift.*

*Created 2026-05-27; v0.2 2026-05-28 folds Akadir's review comments (see changelog). Status: ratified for build — the agreed dispositions are in § 12. Operator-question wording (§ 8) is kept as drafted and validated live on the UI per O1.*

*Where this doc and the canonical plan (`docs/rf_qa_canonical_plan_v0_1.md`) disagree on scope, this doc is the newer read for the calibration mechanics. The canonical plan still governs the data contract (§ 5–§ 7), the four transparency rules (§ 9), and the academic anchors (§ 11.6). Where this doc and `docs/qa_data_collection_plan.md` disagree on candidate selection (§ E of that doc), this doc supersedes — the data-collection plan was written 2026-05-21, before v3's wrong_site redesign and before the semantics-first lessons of the 2026-05-26 click-through pivot.*

> **⚠ SCOPE RECONCILIATION (2026-06-01) — read before § 8/§ 9.** The § 8 Round-1 cards (`motion_blur`, `tgc_banding`, `air_gap_reverberation`, `near_field_clutter`, `vessel_shadow_in_roi`, `dark_clip_high`, `segmentation_low_confidence`) predate the **option-A pivot** (`docs/rf_qa_v2_plan.md`). The 272-study sweep-pool probe found the **acquisition-failure** detectors among them are **positive-starved** on this corpus (confirmed on the alive AND the 47-study rejected cohorts), so their per-detector campaign is **PARKED** (blocked on a deliberate failure-mode dataset, not on engineering — see Part 5 of the v2 plan). The **live near-term scope is the TWO open cuts** — `dark_clip_high` (roi_dark_fraction, deployed 0.02) and `wrong_site` (rf_column_coverage, deployed 0.5) — calibrated from labels the **p04 selection-capture** (`_dark_override_capture` / `_capture_selection_labels`) and **p4x** produce, consumed by the **minimal fitter** `scripts/calibration/fit_cut.py` (Youden-J + sensitivity-target per § 8.0 / v2-plan Part 4.2). The full multi-detector consumer (compound-FPR backstop § 6, promoter, round-state ledger) stays parked behind that producer; the §-6 concurrence arithmetic is also wrong by ~500× and needs a statistician before any composite verdict (v2-plan Part 4.4). The § 8 cards below remain the design-of-record for when the starved detectors are revived.*

---

## § 1 — Purpose

The RF image-quality QA layer ships 15 detectors. **5 are calibrated and trustworthy** (femur_out_of_frame, femur_off_central_zone, edge_clip_lateral, edge_clip_vertical, wrong_site — the last redesigned to Tier-A column-coverage in commit `246e2ec`). **10 are not** — each carries either a starting-point guess or a NaN safety belt; none drives the deployed verdict (Finding B). Of those 10, **7 are in scope for Round 1** (§ 3).

Calibrating means two distinct outcomes:

- **Threshold fit** — a data-derived cut-off that replaces the starting guess + provenance (`cohort_n_frames`, `sens_at_value`, `spec_at_value`).
- **Mechanism validation** — the operator's eye confirms the detector's primitive measures the right thing. If the detector says "yes problem" when the operator says "no" 40 % of the time, the primitive is wrong — not the threshold. Mechanism validation is a first-class outcome, not a side effect; it triggers `under_mechanism_review` rather than `calibrated_provisional`.

This doc specifies the per-detector contract that gets us both.

---

## § 2 — Three semantic anchors

These hold across all 7 in-scope detectors. They are non-negotiable.

**(A) Different detectors need different visual context AND different questions.** Two halves of one idea: tune the whole cell to the detector's primitive, not to visual or verbal completeness.
- *Visual:* for motion_blur the operator's judgement lives in the raw image's fascia edge sharpness — the colored segmentation overlay hides exactly what they need to see. For tgc_banding the signal is a 1-D depth-band residual that no overlay renders. For segmentation_low_confidence the score IS the confidence heatmap. A one-size cell will produce noisy labels. Each detector's `default_layers` are chosen to surface its primitive.
- *Question:* (Akadir 2026-05-28) different detectors also need *different questions* that point the operator at the real thing the detector measures. Otherwise we ask one thing and the operator answers another — a question/primitive mismatch that silently poisons every label. The per-detector `operator_question` (§ 8) is therefore load-bearing, not cosmetic: a wrong question makes the label useless even when the operator answers honestly. This is the single failure mode most likely to make the whole calibration not work.

**(B) Surface + ask explicitly + record the right scales/metrics — or it won't work.** (Akadir 2026-05-28.) Labels are usable for calibration only when the operator's eye is the source — never when the operator passively accepted what the detector said. Three conditions, all required:
- *Surface* — the operator must see the thing being judged (anchor A's visual half).
- *Ask explicitly* — a clear per-detector question, blind by default: the operator answers Yes / No / Unsure before seeing the score. After the answer, the score + threshold are revealed with a "would you change your mind?" three-button confirmation. Labels that survive the confirmation are tagged differently from labels silently locked in (§ 5).
- *Record the right scales/metrics* — the persisted label snapshots the detector's **full metadata dict** at label time (every intermediate quantity it computed), not just the headline scalar (§ 5). This is what lets calibration later discover whether a *different* quantity from the same detector is the better discriminator, and it is the complete audit/compliance trail. Recording only yes/no, or only the one scalar, is the silent way this fails.

**(C) Mechanism is upstream of threshold.** Every fitter run reports both the sensitivity-target operating point AND the Youden-J operating point. Divergence > 5 % is flagged. If the per-detector sensitivity target is unreachable at any threshold (heavy overlap between positive and negative score distributions), the fitter refuses to emit a threshold — instead the detector enters `under_mechanism_review` (§ 4) and the disagreement-audit CLI surfaces the misfire cases for redesign. **No detector is pre-judged into mechanism review** — the data decides (Akadir 2026-05-28: pre-flagging near_field_clutter before Round 1 was dropped; it goes through Round 1 like every other detector, and enters mechanism review only on evidence).

---

## § 3 — Scope

**In scope for Round 1 — 7 detectors:**

| Detector | Current tier | Why in scope |
|---|---|---|
| motion_blur | Tier-B NaN-belted | Audit-rewritten Tenengrad-on-fascia primitive; needs operator mechanism-validation more than any other |
| tgc_banding | Tier-B NaN-belted | Detrended-row-residual primitive; needs the auxiliary 1-D plot to make sense |
| air_gap_reverberation | Tier-B NaN-belted | FFT comb-peak primitive; raw image suffices for operator judgement |
| near_field_clutter | Tier-B NaN-belted | Broadband Laplacian; needs the segmented skin+fat region surfaced |
| vessel_shadow_in_roi | Tier-B NaN-belted | Darkest-column primitive; needs column-marker aux to locate the candidate |
| dark_clip_high | Tier-B starting point | Fires on 18 % of cohort today; pixel rail is Tier-A, only the ROI fraction is calibration-pending |
| segmentation_low_confidence | Tier-B NaN-belted | Boundary-entropy from segmenter softmax; live on every UI run since commit `510c48c` |

**Out of scope for Round 1 — 3 detectors, explicit exclusion rationale:**

- **subq_fat_compressed** — § 5.2 limitation L8 says single-frame compress-vs-anatomy discrimination is structurally weak; the real disambiguator is sweep-level. Calibrating it now commits operator effort to a detector whose ceiling is a different workstream. Re-enter after the sweep-level work.
- **saturation_high_global / saturation_high_regional** — modality-inert per Finding FW-25. The `_final.png` cohort caps at pixel value 244, below the 250 rail; the detectors structurally cannot fire on the QA modality. Labels would be wasted until the D3 modality decision is closed (re-pin the rail to 244 OR move saturation QA to `_raw.png`). Re-enter when FW-25 is closed.

---

## § 4 — Detector lifecycle ladder

Each detector carries a `detector_status` field in `labeling_app/qa_thresholds.py`'s `ThresholdEntry`. The status transitions per the ladder:

```
  pending_calibration
        │
        │  Round 1 fit succeeds (n ≥ 50 positives, 50 negatives, 100 total;
        │  sens-target met; Youden-J divergence < 5%)
        ▼
  calibrated_provisional   ◄── Round 2 re-fit on growing substrate ──┐
        │                                                            │
        │  Round 2 fresh-cohort regression validation                │
        │  holds within Wilson CI (n ≈ 150)                          │
        ▼                                                            │
  calibrated_published     ────────────────────────────────────────► (loop)
        │
        │  Mechanism-audit CLI flags > X % score-says-yes, operator-says-no;
        │  OR Round-N fit's sens-target unreachable at any threshold
        ▼
  under_mechanism_review
        │
        │  Detector redesigned (e.g., wrong_site → rf_column_coverage v3);
        │  re-enters as pending_calibration with fresh primitive
        │  OR redesign confirmed unrecoverable
        ▼
  retired   (deprecated from IMAGE_QUALITY_FLAG_PRIORITY next minor contract bump)
```

The status is the primary feed for the round-state ledger at `data/calibration_rounds.jsonl`; p4x's entry page reads it to drive "what to work on now"; the metrics CLI reads it as its primary readiness signal.

---

## § 5 — Label-source semantics and the pre-fill exclusion rule

Two label sources exist, and the fitter (subsystem F) treats them as different evidence weights:

- **`label_source = "p4x"`** — collected by the calibration capture page on a candidate frame that was deliberately selected for its score (above-threshold / borderline / below). The operator answers a per-detector question with the score hidden, then sees the score on the reveal step. **Sampling bias:** these labels are over-sampled near the threshold by design; published sensitivity / specificity numbers on this substrate are biased toward the borderline band, NOT cohort-natural-frequency.
- **`label_source = "cohort_pass"`** — collected by the cohort assessment pass (the assessors-guide v0 work, `docs/assessors_guide_v0.md`). The operator looks at every frame in their natural-frequency view and ticks reason codes when dropping. These labels are unbiased samples of the cohort's real failure-mode distribution.

The fitter reports sensitivity / specificity **per label-source separately**, with the gap as an honesty signal. A detector whose p4x sensitivity is 90 % but cohort-pass sensitivity is 60 % is failing on the natural-frequency case despite passing on the borderline cohort — the calibration is non-portable.

**Pre-fill exclusion rule** (§ 11.2 of the canonical plan) — the discipline that prevents the detector validating against itself:

- p4x labels are **by construction blind**: the operator answers before seeing the score. No pre-fill applies. The post-reveal "change your mind?" prompt captures a separate `post_reveal_confirmation` field — labels where the operator changed to match the detector after seeing the score are FLAGGED as anchored, and the metrics CLI surfaces the rate per detector (M8). If M8 > 10 % on a detector, the score-reveal-after policy is anchoring the operator and needs to be reconsidered (e.g., switch that detector to `score_reveal=never`).
- cohort_pass labels are blind in a different sense: the operator picks reason codes against the assessor's-guide menu, not against any specific detector's output. The rule's literal form ("calibration excludes every frame where the operator accepted the pre-fill unchanged") applies in the Phase-4 future when § 8.3.5 pre-fills the checkbox group from `image_quality_validity_flags` — those pre-fills will need active-edit-only counting per § 11.2. For Round 1 (no Phase-4 pre-fill yet), all cohort_pass labels count.

**What each label records (anchor B's "right scales/metrics").** A persisted label snapshots, in addition to the answer:
- `score_at_label_time` — the detector's headline scalar (audit snapshot; the fitter joins against current cohort scores, not this — see § 7).
- `detector_metadata_at_label_time` — the **full** `metadata` dict the detector emitted on this frame (every intermediate quantity — e.g. vessel_shadow's `darkest_column` + `median_column_mean`, tgc_banding's `muscle_band_rows` + residual σ, saturation's `worst_block`). Snapshotting the whole dict (not just the scalar) is what lets a later analysis discover that a *different* quantity from the same detector discriminates better — and it is the complete audit/compliance trail (Akadir 2026-05-28).
- `notes` — an OPTIONAL free-text field the operator can fill per cell (Akadir 2026-05-28: "let's put it there, but make it optional, especially for compliance"). Empty by default; never blocks the Yes/No/Unsure flow.
- the full label schema (with the source/reveal/relabel fields) is in subsystem E of the plan file.

---

## § 6 — Compound-FPR rule (§ 11.5 operationalised)

The aggregate verdict computation in `image_quality_aggregate_tier_from_tiers` requires **≥ 2 calibrated detectors to concur on `invalid`** before downgrading the aggregate. If fewer than 2 calibrated detectors fire `invalid`, the aggregate stays in its pre-flag state.

Why: at 80 % per-detector specificity (the publish-floor target), 14 independent Tier-0 detectors yield 0.80^14 ≈ 4 % probability that a clean frame trips zero false flags. ~96 % of clean frames would trip at least one spurious flag, producing badge-fatigue and training the operator to ignore the QA panel. The two-detector concurrence collapses this to (1 - 0.96)^2 = 0.0016 of clean frames being false-positive-flagged, an honest signal.

Per-frame visibility in the drilldown is unchanged — § 8.5 "Never hide a frame" is preserved. The rule only changes whether the AGGREGATE downgrades. With 1 calibrated detector this is silently a no-op for the aggregate (single-detector concurrence with itself is structurally impossible); the rule activates the moment the second detector is calibrated.

**Hard sequencing gate:** the compound-FPR backstop MUST land before any detector promotes from `pending_calibration` to `calibrated_provisional`. Without it, the first calibrated detector would fire alone on ~20 % of frames and the operator would see badge-fatigue exactly as § 11.5 protects against. The plan sequences this at step 10, before step 11's first promotion.

---

## § 7 — Drift policy (P1)

Detector code drifts. A label collected when motion_blur scored 1.4 against contract v0.2 is invalid if motion_blur is now scoring 2.3 on the same frame under contract v0.3. Policy:

- The label's `score_at_label_time` field is a **snapshot artefact for audit**, not a calibration input.
- The fitter joins labels against **current** cohort scores (subsystem B's parquet), not against the historical snapshot.
- Cohort-score table carries a `code_sig` field — sha256 of detector source files. The fitter refuses to run when the live code's `code_sig` differs from the cohort table's `code_sig`, with a clear regen instruction.
- A detector-code change (a "minor" contract bump per § 12.3) triggers (i) cohort-table regen, (ii) re-fit on the existing labels against new scores; labels are NOT discarded.
- A detector-primitive change so substantial that the operator question itself needs rewording is a `spec_version` bump (§ 8 per-detector); labels collected against the old spec are filtered out by the fitter via `spec_version_at_label_time`.

Net: labels are durable across mechanical detector-code changes; only a fundamental primitive change destroys labels.

---

## § 8 — Per-detector cards (the load-bearing review section)

Each card carries the contract for one detector. **The `Operator question` field is what Akadir reviews line by line — the wording must be operator-tractable, unambiguous, and answerable without domain expertise the operator may not have.**

---

### 8.1 — motion_blur

**Code:** [`_detect_motion_blur`](../labeling_app/image_quality.py#L811) — fascia-band Tenengrad (mean squared gradient magnitude in the segmented fascia layer).
**Threshold registry:** `motion_blur__fascia_band_tenengrad_min` — currently NaN-belted; **fires when Tenengrad < threshold** (low gradient magnitude = soft edges = blur).
**Current state:** `pending_calibration`.

**Primitive:** Mean squared gradient magnitude over pixels segmented as fascia. Sharp fascia = high score; blurred fascia = low score.

**Operator question:** *"Does the fascia layer look crisp, or blurred / smeared?"*

**Why this question:** Fascia is the specular boundary the segmenter labels as `LABEL_FASCIA`; it is the brightest thin band at the muscle's top edge. When the probe or patient moves during acquisition, the fascia loses its hard edge — it smears across the boundary. The operator can judge "crisp vs smeared" without knowing what Tenengrad is.

**Default visual layers (always-on):** Layer 0 (raw grayscale frame) + fascia contour outline.
**Why:** the signal lives in raw-pixel edge sharpness; a colored mask fill would obscure the very pixels the operator needs to see. The contour traces the fascia band so the operator knows WHERE to look without coloring over it.

**Toggleable layers (operator-controlled):** segmentation colored fill (off by default; some operators may want to confirm the fascia segmentation), confidence wash (off by default; useful when segmenter struggled).

**Auxiliary widget:** `_aux_motion_blur` — inline tag showing the segmenter's mean confidence on this frame (e.g., *"AI confidence on this frame: 0.74"*). This guards against the R11 confound: blurred frames degrade the segmenter, so a low-Tenengrad-low-confidence frame might be ambiguous-due-to-segmenter-degradation rather than genuinely blurred. The operator can flag those cases with "Unsure."

**Score reveal:** `hide_before` — score and threshold are hidden until after the operator answers. The post-reveal step shows *"detector measured fascia sharpness = 23.7; current threshold is not yet set. Would you change your mind?"*

**Fit objective:** sensitivity ≥ 80 % at specificity ≥ 85 % (intensity-statistic class per § 11.0). Youden-J reported alongside.

**Quirks:** Tenengrad is unit-free and scale-dependent on probe gain. NaN-belted today — does not fire on any frame; needs calibration to begin firing. Fascia mask must be present; a frame with empty fascia silently skips this detector.

---

### 8.2 — tgc_banding

**Code:** [`_detect_tgc_banding`](../labeling_app/image_quality.py#L833) — standard deviation of detrended row-mean intensity profile within the RF band (residual oscillation after subtracting a smooth 3rd-order polynomial trend).
**Threshold registry:** `tgc_banding__detrended_residual_sigma_max` — currently NaN-belted; **fires when residual σ > threshold**.
**Current state:** `pending_calibration`.

**Primitive:** Row-mean intensity profile across the muscle depth range, with a smooth depth-attenuation trend subtracted. What remains is the "residual" — if there are horizontal bright/dark bands superimposed on the smooth attenuation, the residual oscillates and its σ rises.

**Operator question:** *"Are there horizontal brightness bands that look like gain mis-set across depth?"*

**Why this question:** TGC ("time-gain compensation") is the depth-dependent gain curve the operator sets on the ultrasound device. When mis-set, the image shows visible horizontal stripes — bright at certain depths, dark at others, that don't correspond to anatomy. The operator may not know "TGC" by name, but they recognise "horizontal brightness bands" visually.

**Default visual layers:** Layer 0 + RF outline.
**Why:** the bands sometimes show up in the raw image but can be subtle. The auxiliary widget (below) is the load-bearing visual aid for this detector — operators cannot reliably judge tgc_banding from the image alone.

**Toggleable layers:** segmentation fill (off by default).

**Auxiliary widget:** `_aux_tgc_banding` — a 1-D matplotlib plot below the image showing: (a) the row-mean intensity profile (y-axis: depth row index; x-axis: mean intensity), (b) the fitted 3rd-order polynomial trend (smooth curve), (c) the residual (intensity - trend) plotted separately, with σ marked. **The score IS this residual's σ.** Showing the visualisation is showing the score; the operator does not need a separate scalar reveal.

**Score reveal:** `visualised` — the auxiliary widget shows the score in plot form. Post-reveal step shows the threshold value: *"current threshold is not yet set. Does the residual plot match what you saw in the image?"*

**Fit objective:** sensitivity ≥ 80 % at specificity ≥ 85 %. Youden-J reported alongside.

**Quirks:** Detrending kills any genuinely-smooth depth gradient (correct behaviour) but can also miss low-frequency banding that looks polynomial. Silent on frames with RF band < 8 pixels (muscle too thin to fit a meaningful trend). Scale-dependent on gain.

---

### 8.3 — air_gap_reverberation

**Code:** [`_detect_air_gap_reverberation`](../labeling_app/image_quality.py#L863) — average normalised comb-peak magnitude across columns in the near-field band (FFT magnitude in the 3-8 pixel reverberation-period band).
**Threshold registry:** `air_gap_reverberation__comb_peak_magnitude_min` — currently NaN-belted; **fires when comb peak > threshold**.
**Current state:** `pending_calibration`.

**Primitive:** Per-column FFT of the near-field intensity profile (above the skin band), looking for periodic structure in the 3-8 pixel period range. Air-gap reverberations between probe and skin produce a characteristic periodic comb pattern.

**Operator question:** *"Are there bright periodic horizontal fringes near the skin surface?"*

**Why this question:** When the probe loses contact with the skin (gel dries up, probe lifts), ultrasound bounces between the probe face and the skin, producing visible reverberant fringes — parallel bright stripes spaced regularly. The fringes are usually visible in the raw image if the operator looks for them.

**Default visual layers:** Layer 0 + skin contour.
**Why:** the fringes are in the raw near-field region; the skin boundary marks the depth where reverberation would appear above. Operator focuses attention there.

**Toggleable layers:** segmentation fill (off), confidence wash (off — confidence usually low in the air-gap region but not informatively).

**Auxiliary widget:** none for Round 1. (Future option: FFT spectrum plot for the operator to confirm the 3-8 px periodicity.)

**Score reveal:** `hide_before`. Post-reveal: *"detector measured comb-peak magnitude = 0.42 in the 3-8 pixel period band. Would you change your mind?"*

**Fit objective:** Youden-J — no published sensitivity target for this detector class, so the data-derived optimum is the operating point.

**Quirks:** Silent if near-field band depth < 16 pixels. Probe-frequency-dependent; the 3-8 px period range is tuned to typical probes and may need adjustment.

---

### 8.4 — near_field_clutter

**Code:** [`_detect_near_field_clutter`](../labeling_app/image_quality.py#L902) — mean squared Laplacian (broadband high-frequency energy) over pixels in segmented skin + fat regions combined.
**Threshold registry:** `near_field_clutter__clutter_energy_max` — currently NaN-belted; **fires when clutter energy > threshold**.
**Current state:** `pending_calibration`.

**Primitive:** Broadband high-frequency energy in the near-field anatomical layers (skin + fat). Probe-artefact texture is high-frequency; normal speckle is also high-frequency. The detector conflates these.

**Operator question:** *"Is the layer above the muscle full of high-frequency artefacts (not normal speckle)?"*

**Why this question:** Probe artefacts in the near field look "wrong" to an experienced operator — they're sharper, more regular, or more textured than normal speckle. The question explicitly distinguishes artefact from speckle so the operator knows what we're asking about. May be the hardest detector to label reliably; high Unsure-rate expected.

**Default visual layers:** Layer 0 + skin + fat outlines (both).
**Why:** the detector measures broadband energy specifically over segmented skin + fat. The operator should see which pixels are classified as those layers to judge whether the high-frequency content is genuinely in those regions.

**Toggleable layers:** segmentation fill (off).

**Auxiliary widget:** none. (Future option: Laplacian heatmap overlay, but risks coloring over the very signal we want the operator to see.)

**Score reveal:** `hide_before`. Post-reveal: *"detector measured broadband Laplacian energy = 89.4 in the skin+fat region. Would you change your mind?"*

**Fit objective:** Youden-J — no target named.

**Quirks:** Conflates real probe clutter with normal speckle (canonical-plan limitation). Operator labels are the only signal that disambiguates the two. Silent if both skin and fat regions are absent. This is the highest-risk detector for "operator says no consistently" → mechanism review.

---

### 8.5 — vessel_shadow_in_roi

**Code:** [`_detect_vessel_shadow_in_roi`](../labeling_app/image_quality.py#L925) — mean intensity of the darkest RF-ROI column versus the median of all RF-ROI columns.
**Threshold registry:** `vessel_shadow_in_roi__column_darkness_max` — currently NaN-belted; **fires when darkest column mean < threshold**.
**Current state:** `pending_calibration`.

**Primitive:** Per-column mean intensity across the RF region; find the darkest column. A blood vessel sitting above the muscle casts a dark vertical shadow through it (acoustic shadowing); the darkest column will be markedly below the median.

**Operator question:** *"Is there a vertical dark column running through the muscle that looks like a vessel shadow?"*

**Why this question:** Vessel shadows are visually distinctive — a narrow dark vertical band that doesn't correspond to a true muscle structure. The operator recognises them as artefacts because blood vessels appear above the band and the shadow falls directly below. The question primes them to look for that specific pattern.

**Default visual layers:** Layer 0 + RF outline.

**Toggleable layers:** segmentation fill (off).

**Auxiliary widget:** `_aux_vessel_shadow` — raw image with a vertical line marker at the `darkest_column` coordinate (from `detector_metadata`), plus a per-column mean intensity profile plotted below the image showing the dip at the marked column relative to the median. Locates the candidate quickly without forcing the operator to scan column-by-column.

**Score reveal:** `hide_before`. Post-reveal: *"detector found darkest column at x=187 with mean intensity 38; median column intensity is 92. Would you change your mind?"*

**Fit objective:** sensitivity ≥ 80 % at specificity ≥ 85 %. Youden-J reported alongside.

**Quirks:** Column-level granularity means a true horizontal artefact (banding, global dimming) is correctly NOT flagged (they affect many columns uniformly, not one column markedly). Silent if RF ROI is absent.

---

### 8.6 — dark_clip_high

**Code:** [`_detect_dark_clip_high`](../labeling_app/image_quality.py#L693) — fraction of RF-region pixels with grayscale value ≤ 5 (the 0 rail — probe-side clipping or deep attenuation).
**Threshold registry:** `dark_clip_high__pixel_value_max=5` (Tier-A physics rail), `dark_clip_high__roi_fraction_max=0.02` (Tier-B starting point — fires when ROI dark-fraction > 0.02).
**Current state:** `pending_calibration` (only the ROI fraction is calibration-pending; the pixel rail is fixed).
**Cohort firing rate:** 18.0 % at the starting threshold.

**Primitive:** Fraction of pixels inside the RF muscle region that are at or below grayscale 5 — i.e., effectively black. Large dark regions inside the muscle mean either gain is too low or deep tissue is attenuating signal away.

**Operator question:** *"Is a large part of the RF muscle region too dark to read (clipped to black)?"*

**Why this question:** Dark-clipping is operator-tractable visually — they see black or near-black patches inside what should be the muscle echo. The question phrases it as "too dark to read" so the operator judges *practical usability*, not the literal pixel rail.

**Default visual layers:** Layer 0 + RF contour outline.
**Why:** the signal is raw-pixel darkness inside the RF region; the contour marks the boundary so the operator sees what's "RF" without coloring over the dark pixels. A segmentation fill would hide the very dark pixels we're asking about.

**Toggleable layers:** segmentation fill (off — would obscure the signal).

**Auxiliary widget:** none. (Future option: intensity histogram of the ROI showing the rail-fraction.)

**Score reveal:** `hide_before`. Post-reveal: *"detector measured dark-fraction = 0.024 of RF muscle pixels at value ≤ 5 (current threshold: 0.02 — fires). Would you change your mind?"*

**Fit objective:** sensitivity ≥ 80 % at specificity ≥ 85 %.

**Quirks:** Cannot distinguish probe-side gain miscalibration from genuine deep-tissue attenuation just by counting dark pixels. The operator's judgement is meaningful here — they can tell from the pattern whether it's a global-dim (gain) or depth-localised (anatomy). Mechanism review may surface that the primitive should distinguish position.

---

### 8.7 — segmentation_low_confidence

**Code:** [`detect_segmentation_low_confidence`](../labeling_app/image_quality.py#L960) — boundary entropy of the segmenter's softmax output at mask boundaries (cross-entropy reduction in the edge region).
**Threshold registry:** `segmentation_low_confidence__boundary_entropy_max` — currently NaN-belted; **fires when boundary entropy > threshold**.
**Current state:** `pending_calibration`. Lives outside the main detector registry — runs from `compute_image_quality_features` when `boundary_entropy` is supplied (predicted-mask runs only, which means every live UI session per commit `510c48c`).

**Primitive:** Mean softmax entropy at the boundary pixels of the segmentation mask. A confident segmenter has near-zero entropy at boundaries (it's sure which class each pixel belongs to). An uncertain segmenter has high entropy — the boundaries are smeared across multiple class probabilities.

**Operator question:** *"Does the AI look unsure where the muscle edges are? (Look at the confidence wash — red = unsure.)"*

**Why this question:** This is the only detector where the operator's task is judging the AI's uncertainty directly, not judging a physical image property. The question is explicitly about the AI ("the AI looks unsure"). The confidence wash is what the operator looks at — it IS the score, visualised per-pixel.

**Default visual layers:** Layer 0 (raw) + Layer 2 (confidence wash — the heatmap from `compose_layer_stack`).
**Why:** the heatmap renders per-pixel entropy as a red wash with intensity proportional to (1 - confidence). Where the segmenter is sure, the wash is invisible. Where it's unsure, red. The operator's whole task is reading this heatmap.

**Toggleable layers:** segmentation outline (off by default — when ON, the operator can confirm the boundary the entropy is measured at).

**Auxiliary widget:** none — Layer 2 IS the score visualisation.

**Score reveal:** `visualised` — the heatmap shows the score in per-pixel form. Post-reveal step shows the scalar mean boundary entropy + threshold: *"detector measured mean boundary entropy = 0.42; threshold not yet set. Does the heatmap match what you saw?"*

**Fit objective:** Youden-J — no published sensitivity target for this detector class.

**Quirks:** Score is model-internal, not derivable from the argmax mask alone. **Now fully working (2026-05-28 mask-source fix):** p4x grades on PREDICTED masks (the segmenter runs live in the cell), so boundary_entropy is available, and the cohort score table is built on predicted masks (`scripts/calibration/build_predicted_score_table.py` — 956/956 frames carry a seg score). The earlier "needs annotator-mask-only" limitation is resolved.

---

## § 9 — Round 1 protocol

**Goal:** for each of the 7 in-scope detectors, accumulate ≥ 50 positive labels + ≥ 50 negative labels (≥ 100 total per detector) and run the fitter end-to-end on at least one detector (motion_blur, per the pilot sequencing).

**Pre-conditions:**
- Spec doc (this doc) ratified by Akadir; the Python-module spec mirror written.
- Cohort score table (subsystem B) built; `code_sig` matches live code.
- One-time predicted-mask cohort pass complete for segmentation_low_confidence.
- p4x UI built; AppTest pilot green; live pilot on motion_blur (~20 cells) UX-validated.
- Compound-FPR backstop landed (it's no-op while 0 detectors are calibrated, but the gate must exist before the first promotion).

**Per-session protocol:**
- Operator enters p4x from p04 ("→ Help calibrate (optional)" button).
- p4x entry page reads the round-state ledger and shows per-detector progress bars; operator picks the detector to work on.
- Candidate selector produces 6 cohort frames + 1 picked frame per session (per-detector spec).
- Operator labels each cell: see image + aids + question → answer Yes / No / Unsure → see score reveal → confirm / change-mind.
- Labels persist to `data/calibration_labels.jsonl` with full schema (§ E of the plan file).
- Operator can exit at will via "Done for now" button; session bookmark records where they were.

**Resumability:** the bookmark records (detector, frame_index, answer-pending-confirmation). Next session resumes at the same cell. Per-detector progress bar on the entry page shows accumulated n_yes / n_no / n_unsure vs the 50-positive target.

**Pacing:** ~6-10 operator-hours total spread across multiple sessions (R1 — operator fatigue is the dominant risk). No more than ~50 cells per session; concentrated labelling degrades IRR per the published literature.

**Test-retest sub-cohort:** 30 frames per detector are tagged `relabel_target=True` at first labelling. After a 2-week interval, the operator re-labels those 30 blind to their first answer (the bookmark hides prior labels). Test-retest κ is the within-operator workaround for the IRR study (§ 11.3) until Anonaao joins as a second operator.

**Revisit reminder mechanism (hybrid — Akadir 2026-05-28).** The 2-week revisit is worthless if no one remembers it. Two complementary nudges:
- *In-app (the functional path):* when a frame is tagged `relabel_target=True`, the round-state ledger (`data/calibration_rounds.jsonl`, subsystem K) stores `relabel_due_utc = labelled_at + 14 days`. The p4x entry page reads the ledger and surfaces a banner — *"N frames are due for test-retest re-labelling"* — once any due date has passed. This is what actually drives the re-label, because it lives where the operator does the work.
- *Calendar (the nudge back in):* at the moment the first relabel_target of a round is tagged, a Google Calendar event is created via `gws` (`calendar.events.create`) for the +14-day mark — *"RF QA: test-retest re-label due (N detectors)"* — so the operator gets pulled back into the app even when not already in it. Writes are confirmed with the operator before creation (calendar events are externally visible).

The in-app banner is the source of truth for what is due; the calendar event is the reminder to open the app at all.

---

## § 10 — Round 2 protocol (regression validation)

**When:** ~2 weeks after Round 1 completes (per detector). Independent of when other detectors finish Round 1.

**Per-detector procedure:**
- Re-run the candidate selector to draw 50 FRESH frames per detector — NOT the same frames as Round 1.
- Operator labels using the same p4x UI (same spec_version; same operator question; same visual aids).
- Fitter runs on the combined Round-1 + Round-2 substrate.
- The detector's `sens_at_value` and `spec_at_value` on the Round-2 subset alone are computed.
- If Round-2 sens/spec falls within the Round-1 Wilson 95 % CI → the detector promotes from `calibrated_provisional` to `calibrated_published`.
- If outside the CI → the detector returns to `pending_calibration` and Round 3 is scheduled; the disagreement-audit CLI is run to surface what changed.

**Why two rounds:** the Round-1 70/30 train/validate split is in-cohort — it only validates on different frames sampled by the same selector strategy. A genuinely out-of-cohort sample (Round 2's fresh draw, weeks later) is the regression test that the fitted threshold isn't an artefact of one labelling session's framing.

---

## § 11 — Cross-references

**Plan file (the workplan):** `~/.claude/plans/ok-now-we-plan-adaptive-yeti.md` — subsystems A–K, sequencing, metrics M1–M9, risks R1–R15.

**Canonical plan (the contract):** [`docs/rf_qa_canonical_plan_v0_1.md`](rf_qa_canonical_plan_v0_1.md) — § 5 (detector taxonomy), § 6 (gating tables), § 7 (data contract), § 8 (UI components), § 9 (transparency rules), § 11 (calibration methodology), § 12.4 (registry promotion). Where this doc and the canonical plan disagree on scope, this doc supersedes for calibration mechanics; the canonical plan still governs the data contract.

**Data-collection plan (the substrate):** [`docs/qa_data_collection_plan.md`](qa_data_collection_plan.md) — § B (50-positives target), § D (two channels: campaign + per-run), § E (candidate selection — superseded by § 8 of this doc for the per-detector strategy), § F (Round 1 outline — superseded by § 9 of this doc).

**Assessor's guide (the cohort-pass rubric):** [`docs/assessors_guide_v0.md`](assessors_guide_v0.md) — defines the 20 structured reason-code taxonomy used by cohort_pass labels. p4x labels do not use this taxonomy — p4x asks a per-detector question and records yes/no/unsure.

**Detector code:** [`labeling_app/image_quality.py`](../labeling_app/image_quality.py) — every detector function lives here.
**Threshold registry:** [`labeling_app/qa_thresholds.py`](../labeling_app/qa_thresholds.py) — `THRESHOLDS` dict + `ThresholdEntry` dataclass. The lifecycle ladder's `detector_status` field is added here as part of subsystem K.
**Overlay primitives:** [`labeling_app/visualization.py`](../labeling_app/visualization.py) — `compose_layer_stack` (raw / segmentation / confidence / change_map layers) and `confidence_heatmap_overlay` (the red-wash heatmap used by segmentation_low_confidence's Layer 2).

---

## § 12 — Open decisions

| # | Decision | Status |
|---|---|---|
| O1 | Per-detector operator-question wording (§ 8) | **RESOLVED 2026-05-28** — keep questions as drafted; validate live on the UI (Akadir: "let's keep like that, we will see on the UI") |
| O2 | Mechanism-review trigger rate threshold (the disagreement-audit CLI's "score-says-yes, operator-says-no > X %" gate) — provisionally 30 % | open — will be informed by Round-1 data |
| O3 | Test-retest κ acceptance bar — provisionally 0.6 | open — informed by Round-1 + 2-week relabel results |
| O4 | Operator-fatigue session cap — provisionally 50 cells / 2 hours | open — measured against M5 (survival rate) in pilot |
| O5 | Whether p4x should also surface a small "feedback notes" textbox per cell beyond Yes/No/Unsure (cheap free-text capture) | **RESOLVED 2026-05-28 → YES, optional** — add it; non-blocking; valuable for compliance (Akadir). Recorded as `notes` in the label schema (§ 5). |
| O6 | Whether `near_field_clutter` should enter Round 1 pre-flagged into mechanism review | **RESOLVED 2026-05-28 → NO special treatment** — pre-judging biases the process; near_field_clutter goes through Round 1 like every other detector and enters mechanism review only on evidence (Akadir: "I didn't quite get why to special-treat this"). Anchor C amended accordingly. |

---

## Changelog

| Version | Date | Change | Source |
|---|---|---|---|
| v0.1 | 2026-05-27 | Initial draft for Akadir review. 7-detector scope ratified via AskUserQuestion 2026-05-26 (Round-1 scope, three-button cardinality, hide-before-reveal-after score policy). | Plan file `~/.claude/plans/ok-now-we-plan-adaptive-yeti.md`; Plan-agent critique 2026-05-26; detector audit Agent 2026-05-26. |
| v0.2 | 2026-05-28 | Folded Akadir's 7 review comments (Google Docs `1VjPPXR…` spec + `1Fp83…` plan). Anchor A amended → "different visual context AND different questions" (a wrong question poisons the label). Anchor B amended → "surface + ask explicitly + record the right scales/metrics"; label now snapshots the FULL detector metadata dict, not just the scalar (§ 5). Anchor C amended → no detector is pre-judged into mechanism review. O1 resolved (keep questions, validate live). O5 resolved → add optional `notes` textbox. O6 resolved → near_field_clutter gets no special treatment. Test-retest revisit reminder added (§ 9, hybrid: in-app banner + Google Calendar nudge). | Akadir review comments 2026-05-28 |

# RF QA — next-phase plan: de-risk before build (probe → audit → gate)

*Written 2026-05-29. The controlling plan for what comes **after** the wrong_site cleanup + audit-prep. It exists to carry the connected decisions made this session into the next one without loss. Read it after `HANDOFF.md`; it sits above the calibration-loop workplan (`~/.claude/plans/ok-now-we-plan-adaptive-yeti.md`, subsystems A–K), which it gates.*

**One-line direction:** two cheap de-risking moves — an empirical **probe** and a focused **audit** — gate the expensive calibration build (the F–K consumer + a multi-week operator labeling campaign). Run the **probe first**, then the audit, then decide **build-or-pivot**. The broad app-structure audit comes after that.

---

## Where we are (2026-05-29)

- **wrong_site provenance cleanup — DONE, committed `2655e63`.** Tier-A registry entry `wrong_site__rf_column_coverage_min` resolved via `get()`; hardcoded `0.5` gone; orphaned `rf_fraction` entries deleted; the histogram script's dead `rf_fraction` residue swept. Full suite **599 green**; trace_frame AGZ855 4/4.
- **Audit material — ready, committed `2655e63` (+ uncommitted modality row).** `docs/rf_qa_audit_orientation.md` (anchor-free) + `docs/rf_qa_audit_guidelines.md` (flexible).
- **Still true:** the calibration substrate in the repo is the **wrong pool** (956 clean ~3/study frames); the **consumer is unbuilt** (no fitter / promoter / compound-FPR / ledger); **the loop has never closed** — zero detectors calibrated through the operator-label loop. The 5 "calibrated" detectors got there by reuse + one redesign, not the loop.

---

## The connected decisions (do not lose these)

These were made across this session and only make sense together.

**D1 — Audit prep is anchor-free orientation + flexible guidelines, not a prescriptive plan or a tome.** *Why:* the auditor is a model that self-validates against shared training priors; a prescriptive plan re-encodes our blind spots. So we fix the auditor's *stance, coverage axes, and independence* — not the findings — and let its own intelligence bring attacks.

**D2 — wrong_site is now legitimately calibrated, not by accident.** *Why:* its "calibrated" status had rested on the `@_detector` wrapper's `any(is_starting_point for n in [])` being vacuously False because the detector hardcoded its cut and resolved no registry entry. Fixed (D-status: done, `2655e63`).

**D3 — Focused QA/QC audit FIRST; broad app-structure audit LATER.** *Why:* the QA/QC audit material is ready now (the broad one would need days of its own prep); QA/QC is the self-identified weakest link, the active workstream, and the thing with the live expensive decision in front of it; a bounded audit goes deep where breadth goes shallow. **Condition:** the audit must genuinely exercise its first coverage axis — *end-goal & simpler alternatives* — so it doubles as a **prioritization check** (is QA the right lever? is per-detector right vs one learned grader / one overall gate? is QA even the bottleneck vs the measurement core / segmenter / cohort?). **Named risk:** QA/QC-first can perfect the wrong thing; the mitigation only works if we go in *willing to act on* a verdict of "QA is over-invested, refocus on the core."

**D4 — Run the PROBE before the audit** (revised from the earlier "parallel" call). *Why:* the dependency is **asymmetric** — the probe's per-detector candidate-availability readout grounds the audit's most consequential questions (viability, simpler-path), while the audit's reasoning cannot make the probe's data better. The probe can even **refocus** the audit (if it shows positive-starvation, the audit's headline becomes "salvage per-detector or pivot?"). **Hedge:** if the probe balloons past ~2 days, fall back to running the audit in parallel rather than letting a ready audit sit idle.

**D5 — The substrate rebuild is scoped as a go/no-go PROBE, not step-1-of-the-march.** *Why:* it is the cheapest thing that answers "can this cohort support per-detector calibration at all?" — a *necessary-condition* check — before committing the expensive consumer build + month-long campaign. **Do not start the F–K consumer or the labeling campaign before the gate (Step 3).**

**D6 — Frame-count / modality correction.** Each capture frame exists in **3 modalities** (raw, before_DSC, final.jpg); `_final.png` is the PNG of the final, **resized to the raw/before_DSC dimensions**. So the sweep pool is ~1/3 of the raw file count (~110–200 frames/sweep, order **~5–20k frames after 5 fps**, not 30–50k). The **p03 picker already enumerates + segments sweep frames** in this modality — that machinery is **reuse**, not greenfield.

**D7 — Modality-correctness is an explicit audit verification target** (added to the orientation's claims→evidence index). A `raw` / `before_DSC` / wrong-size mixup in the rebuild yields scores **incomparable** to the deployed cohort scores — same surface as Finding C's 244-ceiling.

**D8 — Feed the probe's results to the audit as data-to-re-derive, not as interpretation.** *Why:* hand over the parquet + the per-detector readout framed as "here's what the cohort contains, judge it," never "see, it works." The orientation/guidelines already enforce "re-derive, trust nothing," so the probe enters as evidence to verify — keeping it anchor-free.

**D9 — Deferred (named, not dropped):** the canonical plan v0.3 reconciliation (it predates the v3–v6 redesigns; still documents wrong_site's `rf_fraction` design); and the `code_sig` bump from the wrong_site fix means the existing cohort parquet/JSON regenerate with the rebuild (expected).

---

## The sequence

| Step | What | Why | Deliverable | Who |
|---|---|---|---|---|
| **1. Probe** | Rebuild the score table over the 272-study full-sweep pool at 5 fps; produce a per-detector **candidate-availability readout** | Empirically answer "can this cohort support per-detector calibration?" before spending the month | The new parquet + the readout (go/no-go data) | Claude, in-repo |
| **2. Focused audit** | Hand a fresh session (ideally a **different model family**) the orientation + guidelines + the probe readout + the adversarial mandate | Reasoning verdict on approach + proportionality, now grounded in the probe's data | Audit report (kill-arguments per axis) | Separate session |
| **3. Decision gate** | Combine probe verdict + audit verdict | Decide before the expensive build | Go (build) / pivot (simpler gate or refocus) | Akadir |
| **4. Build-or-pivot** | If go: build F–K consumer (compound-FPR backstop *before* any promotion), then run the labeling campaign. If pivot: simpler gate / refocus on the measurement core | This is the expensive part — only enter it past the gate | Closed loop, or a deliberately simpler design | Claude + Akadir + Anonaao |
| **5. Broad audit** | Whole-app structure audit, QA/QC as a now-validated (or descoped) component | The MVP core was never audited; do it once QA/QC is settled | Broad audit material + report | Later |

---

## Step 1 detail — the rebuild-probe spec

**It is data-only. No operator labeling.** The probe measures whether a *candidate population* of failure-mode frames exists; it does not confirm positives (that is the campaign, post-gate). No candidate tail → the campaign can't possibly reach 50 positives → dead. A fat candidate tail → maybe (the campaign decides).

**Build (mostly re-wiring existing pieces):**
1. **Cohort** — the 272 alive studies: `exports/decision_log_full_3operators.csv` filtered to `operator_id=='akadir' AND decision_status=='completed'`, dedup `study_code` (274 rows → 272).
2. **Enumerate** — full sweeps via `preflight_helpers.study_discovery.list_frame_bases` over `data/studies_root/<center>/.../RawImagesForResearch_*/` (the path the p03 picker already uses; verify the exact entry point at build time).
3. **Modality** — synthesize the **`_final`** modality per frame (`synthesize_final_u8`), at the deployed size — **not** raw / before_DSC (D6/D7). Each frame is in 3 modalities; pick `_final`, do not triple-count.
4. **Sample 5 fps** — the `frame_base` names are millisecond timestamps (`20260127_151046404`), so subsample to ≥200 ms spacing. No new metadata needed.
5. **Per frame** — segment (`predict_mask`) → run all 15 detectors (`compute_image_quality_features`) → score.
6. **Robustness** — gracefully skip + log frames where the segmenter fails or returns a garbage mask. **The skip rate is itself a result** (it bounds gradeability — see the chicken-and-egg risk below).
7. **Write** — the canonical predicted score table (`data/cohort_stats/qa_per_frame_detector_scores.parquet`); re-point `candidate_selector._PARQUET` only if the path changes. Bumps `code_sig` (expected; D9).

**Deliverable — the per-detector candidate-availability readout** (for the 7 Round-1 detectors: motion_blur, tgc_banding, air_gap_reverberation, near_field_clutter, vessel_shadow_in_roi, dark_clip_high, segmentation_low_confidence):
- score distribution over the pool, on **segmentable** frames;
- estimated **candidate-tail size** — # frames scoring where the failure mode is plausibly present;
- the **segmenter-usable fraction** (how many frames the detector could even score).

**Go/no-go heuristics (set the exact cuts when the distributions are in view):**
- **GREEN** — a clear candidate tail (≈≥150–200 gradeable frames), so the campaign can plausibly net ≥50 confirmed positives even after false candidates. dark_clip_high (already fires 18%) and segmentation_low_confidence (signal on every frame) are the expected easy greens.
- **AMBER** — a thin tail (tens). Calibration marginal; the 50-target is at risk.
- **RED** — no meaningful tail / scores clustered clean / segmenter fails on most candidate frames. Can't calibrate this detector from this cohort → pivot signal for it.
- **Aggregate:** most GREEN → proceed to consumer + campaign. Several RED → the per-detector approach is in trouble and the audit's "simpler alternatives" axis becomes decisive.

**Cost:** ~a day or two of wiring (most blocks exist) + ~1–2 h GPU. Frame `_final` synthesis is the likely bottleneck, not the segmenter.

---

## The iceberg — what the gate is protecting against (the rebuild is necessary, not sufficient)

Even a perfect 272-study score table calibrates **nothing** until:

1. **The consumer (subsystems F–K) is built** — fitter, promoter, **compound-FPR backstop (must land before any promotion)**, disagreement-audit CLI, metrics CLI, round-state ledger. ~1–2 weeks of engineering (structure-based estimate; not built, so soft).
2. **The operator labeling campaign runs** — the dominant, human- and calendar-bound cost: ≥50 positive + ≥50 negative labels per detector, **6–10 operator-hours per round**, ≤50 cells/session (fatigue is the named dominant risk), a **2-week test-retest gap**, then **Round 2** on fresh frames weeks later. **A month-plus of elapsed time**, gated on operator availability. No engineering shrinks this.
3. **Clinical + statistical sign-off** — the § 6 gating tables need Anonaao; the fit method needs a statistician. External humans; they gate *trust* in the result.

---

## Make-or-break risks (what the probe is really testing)

1. **It's a *good* cohort.** These sweeps were acquired by trained operators for a muscle-measurement study — deliberately clean. The acquisition-failure detectors (tgc_banding, air_gap_reverberation, near_field_clutter) may be **positive-starved even in the full sweep**. A clean research cohort is the wrong place to find acquisition failures.
2. **The segmenter-on-bad-frames chicken-and-egg.** Detectors grade on **predicted** masks, but the failure-mode frames are exactly where the segmenter is least reliable (motion blur degrades segmentation — spec § 8.1 R11). So a chunk of genuine positives produce garbage/ungradeable scores, shrinking the usable pool below the 50-target. You might have 100 motion-blur frames and only 40 the detector can score.

If both bite for several detectors, per-detector calibration stalls for them → synthetic augmentation, a failure-rich data source, or pivot toward the "one overall gate / learned grader" alternatives the audit orientation flags.

---

## Disciplines carried (won't be obvious from the artifacts)

- **Commit/push only on Akadir's explicit word.** Nothing pushes without it.
- **The audit runs in a separate fresh session, ideally a different model family** — the same-model-bias caveat is in the guidelines; this audit can't catch family-shared blind spots or clinical correctness (→ Anonaao, a statistician).
- **Probe results → audit as data, not interpretation** (D8).
- **Trace-frame pre-claim ritual** — before claiming any milestone done, run `scripts/trace_frame.py` on canonical AGZ855 frames + the full suite (memory `feedback_trace_frame_pre_claim_ritual`).
- **PHI** — folder names (patient names) never reach UI/docs; only `study_code` + parsed demographics are safe. The probe readout carries counts/distributions, no PHI.
- **The app is an assessment app**; the QA/QC capture (p4x) is a sub-process woven into it, entered from review page 4 — never reframe it as a "labeling app".

---

## Execution note — running this as a single-session workflow

A single fresh-session prompt can drive a workflow that does **the probe (Step 1) + a multi-agent internal adversarial review (per `rf_qa_audit_guidelines.md`) + a go/no-go synthesis** — that is the right scope, and it must **STOP at the human decision gate (Step 3)**. Three things are deliberately **out** of any same-session workflow, and the workflow's output must say so:

- **The definitive audit needs a *different model family* + a human** (Anonaao clinically, a statistician for the fit). A Claude workflow reviewing Claude's work is strong on evidence-tracing, coherence, and implementation but blind to family-shared priors and clinical correctness — it is a **complement to the external audit, not a replacement** (this is the same-model-bias caveat from the guidelines, applied to ourselves).
- **The decision gate (build-or-pivot) is the human's call** — the workflow hands over a decision package, it does not decide.
- **The F–K consumer build and the labeling campaign are post-gate and human-gated** (the campaign is multi-week operator time) — not one-shot automatable.

So the shape is: one prompt → **probe → internal review → synthesis → STOP** for the human gate and the external cross-family audit. Bundling the definitive audit or the downstream build *into* the workflow would defeat the audit's independence and try to automate decisions and a human campaign that cannot be automated.

---

## Gate direction — RATIFIED 2026-05-30 (Akadir)

**Decision: next phase = A — study-level, robustness-filter QA. PIVOT away from the 15-detector per-detector calibration campaign. The ungradeable/end-of-sweep gate (Q1) is DEFERRED — optional, later, not immediate.** Effectively "A now; B a later *conditional* phase" — B is parked-recoverable, blocked on data not design (below).

**A ships WITHOUT a labeling campaign.** The small set functions on reuse + defaults: femur/edge geometry + QMT-provenance cap + `frame_rf_consistency` need **no data** (reuse-validated / statistical); `wrong_site` functions calibrated-by-fiat; `dark_clip_high` functions at its 0.02 default. Data collection for `dark_clip_high` (calibrate the cut) and `wrong_site` (validate the 0.5 cut) is for the **TRUST/PUBLISH bar only** — small, targeted, optional, later — never a functional blocker. MVP/demo → ship on reuse + defaults.

**p4x — KEEP + re-scope (Akadir 2026-05-30).** The capture page's original job (the 7 now-parked Round-1 detectors) is mooted, but keep it and re-point it at the focus subset: serve `dark_clip_high` candidates (near 0.02) + `wrong_site` candidates (near 0.5, esp. the end-of-sweep vs true-off-site boundary) for blind-then-reveal agree/disagree. Live use today (the optional validation channel for the 2 live detectors; also generates the labels that would settle Q1), and it stays the ready-made labeling surface for B / the user-facing-data channel later. Needs the same slim `frame_loader`/`candidate_selector` re-wire A needs. Built + tested, so ~free to keep. Non-blocking.

**NOTED, not actioned (Akadir 2026-05-30) — the user-facing-app data channel.** This may become a user-facing app with real, possibly non-professional users. Such users produce the messy, failure-mode-rich captures (motion blur, air gap, vessel shadow, …) the trained-operator research cohort lacks — i.e. **a user-facing app is the natural acquisition-of-positives channel that unblocks subsystem B** (B's binding prerequisite #1 below). Worry later; recorded here only so it is not lost.

**Why (the evidence chain):**
- Probe over the 272 alive studies' full sweep pool (18,332 frames): only `dark_clip_high` is a clean GREEN (3,629 fires / 108 studies); the acquisition-failure catalogue is positive-STARVED — `motion_blur` / `vessel_shadow` zero-tail, `tgc` / `near_field` heavy-tail-not-separable, `air_gap` separable but 18-study-concentrated + 16% unscoreable.
- The 47-study `quality_failed` (128LH) re-run confirmed starvation is **REAL, not a clean-cohort selection artifact** (acquisition detectors still starved in the rejected cohort; `air_gap` worse at 37% scoreable).
- The internal review's "single most dangerous finding" (`wrong_site` mislabeling good frames) was **over-read**: the seg-collapse tail is 97% end-of-sweep probe ramp-off (median sweep position 0.95), so `wrong_site` firing there is ~correct, not a dangerous mislabel. Both the builder narrative and the 17-agent review missed the position-in-sweep covariate; corrected 2026-05-30.

**The small validated layer (option A):** the gates that are reuse-validated or label-validatable — `dark_clip_high` (calibrate its fraction cut), `wrong_site` (label-validate the 0.5 cut, separating true off-site from benign end-of-sweep), the reused femur/edge geometry, the QMT-provenance cap, and the `frame_rf_consistency` sweep-outlier gate — feeding the **study-level pooled measurement (p05)** with an honest "pooled from X of Y frames; Z set aside (reasons)" disclosure; the **p03 picker** surfaces the calibrated/validated subset as the per-thumbnail cue (extending the existing femur cue). At study-level a false fire only drops one frame from a robust pool (low harm), so the **compound-FPR concurrence gate is far less load-bearing** here; revisit only if a composite per-frame verdict is surfaced.

**Parked — subsystem B (operator-facing per-frame failure-mode coach). NOT abandoned; blocked on DATA, not design. To revive B:**
1. **An acquisition-of-positives dataset** — deliberately-collected examples of the starved modes (motion blur, air gap, vessel shadow, TGC banding, near-field clutter); the research cohort lacks them (confirmed in both the alive and the rejected cohorts). This is the binding prerequisite.
2. **The consumer machinery** (fitter / promoter / compound-FPR backstop) — built only when B is revived, before any promotion, and the backstop built CORRECTLY: the spec's § 6 arithmetic is wrong by ~500× ((1−0.96)² computes the wrong quantity; the real P(≥2 of 14 fire falsely) ≈ 80%), so the concurrence rule + specificity targets must be re-derived by a statistician.
3. **The per-frame coach surfacing** on p04 (per-frame "bad because X" callouts).

**The ungradeable / end-of-sweep gate: DEFERRED in A.** Low operator value here (`wrong_site` already excludes these end-of-sweep frames; the study number is unchanged); real value is behind-the-scenes — clean `wrong_site` validation + latent safety on messier cohorts. Revisit if per-frame reasons are surfaced prominently or on a messier data source.

**Open follow-ups for A:** `wrong_site` label-validation + `dark_clip_high` calibration (a small targeted labeling pass — needs the slim `frame_loader` / `candidate_selector` re-wire to serve sweep frames, not the full transport-only path); Anonaao to confirm the § 6 gating-table semantics; a statistician only if a composite verdict is surfaced; a different model family to re-check the probe's separability heuristic.

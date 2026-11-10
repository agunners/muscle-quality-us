# RF QA — calibration data-collection plan (v0)

*The data-collection design for the RF image-quality QA layer: **what to label, how much, sampled how, through which channels, and over what cadence** — so the Build-Step 2b calibration has a defined, sufficient substrate to work from instead of a random pile of images.*

*This document **quantifies and sequences** the collection; it does not redefine the calibration method. The canonical plan governs — `docs/rf_qa_canonical_plan_v0_1.md` (internal v0.2): § 11.2 is the five-step calibration process, § 11.0 the publish bar, § 2.5 the IRR study, § 7.0 the label-pipeline + the pre-fill circularity, § 14 Steps 1 / 2b the build sequence. If this file and the plan disagree, the plan wins — fix this file. The one parameter this document originates is the **initial "enough" target** in § B, ratified by Akadir 2026-05-21.*

*Created 2026-05-21. Status: v0 draft — § I lists what still needs ratification (notably Anonaao's clinical review of the per-code targets).*

---

## § A — Why a sampling plan (the problem it solves)

Ten of the 16 QA detectors are **pending calibration**: each compares a computed score against a cutoff, and that cutoff is currently a placeholder guess (§ 5.2 / `labeling_app/qa_thresholds.py`). Calibration replaces the guess with a cutoff fitted to data — and that needs **labelled images**: real frames a human expert has independently graded "problem present" / "fine", paired with the detector's score (§ 11.2).

Human labelling time is the scarce resource, so *which* frames go to the labeller decides everything. Labelling a random batch fails twice: **rare problems stay rare** (a 1 %-prevalence problem yields only ~60 positives in a 6,000-frame random batch — too few to fit a reliable cutoff), and **most of a random batch is uninformative** (the frames that pin down *where* the cutoff belongs are the borderline close-calls; a random batch is mostly obvious cases). A sampling plan is the deliberate recipe that spends the labelling budget where it calibrates.

## § B — The target: "enough" = 50 confirmed positives per code

**Initial target: 50 confirmed positives per detector** (ratified — Akadir, 2026-05-21). Calibration precision is set by the count of *positives* — negatives are abundant (every fine frame is a negative for a given detector), never the constraint. What the positive count buys, as the 95 % confidence interval on a detector's measured sensitivity:

| positives / code | 95 % CI on a ~90 %-observed sensitivity | what it unlocks |
|---|---|---|
| ~20 | ≈ 70–97 % | triage only — spot a hopeless detector; cannot confirm a good one |
| **~50** | **≈ 79–96 %** | **the initial target — a real deploy / hold / redesign call, per detector** |
| ~150–200 | ≈ 85–94 % | publish-grade accuracy claim; the Tier-2 learned-grader substrate (§ 14 Step 5) |

50 is the lowest count at which "assess" is honest — below it the interval is too wide to tell a mediocre detector from a good one. The 50 are **well-chosen** positives (drawn per § E, weighted to the borderline band), so they outperform a larger random set.

**What "confirmed positive" means.** A frame a human labeller, assessing it against the assessor's guide (`docs/assessors_guide_v0.md`), *independently* records as carrying that problem. Per § 7.0's circularity rule, a label that merely accepts a detector's pre-fill unchanged does **not** count — it echoes the detector. Channel-1 labels (§ D) are collected blind to detector output, so every channel-1 label counts; channel-2 labels count only when actively edited or added.

**Rolling, not a single gate.** Detectors cross 50 independently — the plan does not wait for all of them. A detector that reaches 50 is calibrated (§ 11.2) and moves from *pending* to **calibrated-provisional**: a data-fitted cutoff that now drives the QA verdict. What is given up versus the ~200 bar is only the *published claim* (the accuracy figure carries a wide error bar); the cutoff itself far beats the current guess, and is re-fitted upward as data accrues — calibration is a data-update, not a rebuild (§ 12.4).

**Provisional calibration drives the verdict, and it is free to adopt.** A cutoff fitted on 50 examples sits on the *data-derived* side of the Finding-B line (it is not a guess), so the detector's `pending_calibration` flag correctly flips to False and it begins driving the aggregate verdict. The threshold registry already records `cohort_n_frames` (`ThresholdEntry`), so "provisional (n≈50)" vs "publish-grade (n≈200)" is just that field's value — no schema change, no code change. The § 11.5 two-detector-concurrence rule (a hard gate before 2b) is the backstop against any single provisional detector over-condemning.

## § C — The detector roster: who needs data

Of the 16 detectors: **6 are already calibrated** (Threshold-tier A or reuse — no data needed: `wrong_site`, the two femur detectors, the two edge-clip detectors, and the new fascia-continuity detector `anisotropy_dropout_superficial`); **2 are blocked** behind a design-review gate; **8 are live data-collection targets**. Cohort firing rates are from the Build-Step 2 Phase-2 histogram (`data/cohort_stats/qa_failure_mode_histogram.json`, 956 transport frames).

| detector | tier | calibration status | cohort firing rate | data-collection status |
|---|---|---|---|---|
| `femur_out_of_frame` | A (reuse) | calibrated | 15.0 % | — already trustworthy |
| `femur_off_central_zone` | A (reuse) | calibrated | 0.1 % | — already trustworthy |
| `edge_clip_lateral` | A | calibrated | 98.4 % | — already trustworthy |
| `edge_clip_vertical` | A | calibrated | 0.0 % | — already trustworthy |
| `dark_clip_high` | A+B | pending | 18.0 % | **target 50** — flag-fires |
| `wrong_site` | B | pending | 79.8 % | **target 50** — fit gated on FW-24 (denominator review) |
| `tgc_banding` | B | pending | 0.0 % (belted) | **target 50** — score-ranked |
| `motion_blur` | B | pending | 0.0 % (belted) | **target 50** — score-ranked |
| `air_gap_reverberation` | B | pending | 0.0 % (belted) | **target 50** — score-ranked; likely a shortfall code |
| `near_field_clutter` | B | pending | 0.0 % (belted) | **target 50** — score-ranked |
| `vessel_shadow_in_roi` | B | pending | 0.0 % (belted) | **target 50** — score-ranked |
| `subq_fat_compressed` | B | pending | 0.6 % | **target 50** — score-ranked; likely a shortfall code |
| `segmentation_low_confidence` | B | pending | not run (needs predicted masks) | **target 50** — predicted-mask pass first |
| `saturation_high_global` | A+B | pending | 0.0 % | **blocked** — modality-inert on `_final.png`; FW-25 |
| `saturation_high_regional` | A+B | pending | 0.0 % | **blocked** — modality-inert on `_final.png`; FW-25 |

"Belted" = the Tier-B threshold ships as NaN, so the detector cannot fire — but it still computes a raw *score* every frame (carried in its metadata), and that score is what § E ranks on.

## § D — The two collection channels

**Channel 1 — the designed campaign (the substrate).** Operators label a deliberately-sampled frame set against the assessor's guide. Friction-tolerant (a planned effort, not a clinical workflow), so it carries the bulk of the substrate and is the only channel that *guarantees* attention to the rare codes. Its rigour is the sampling design (§ E) and blind labelling (§ B). This is the § 14 Step-1 cohort assessment pass, structured into rounds by this plan.

**Channel 2 — per-run capture (the continuous trickle).** Every real assessment run already persists the operator's gates + structured drop-reason codes + `qa_overrides` into `data/runs.jsonl` (§ 7.0). It is not sampled — it is whatever real patients bring — and friction must stay minimal, so its highest-value datum is the **override** (the operator actively contradicting a flag: non-circular, exactly the operator-vs-algorithm pair § 11.2 needs). Channel 2 is not designed; it is *monitored* — the ledger's per-code positive counts are tallied, and they count toward the 50. For common codes channel 2 alone may reach 50 over time; for rare codes it will not — which is why channel 1 exists.

Both channels feed the same per-code positive counter. The 50 target is met by their sum.

## § E — Candidate selection (the sampling mechanism)

The campaign does not label random frames — it labels **candidates**: frames a detector's score marks as likely informative. A cheap, imperfect detector score routes the expensive, accurate human attention (as an airport scan routes the hand-search). Three selection modes, by detector class:

- **Flag-fires** (`dark_clip_high`, `wrong_site`, `subq_fat_compressed`) — the flagged frames are the candidate pool. To pin the cutoff, also include frames scoring just below the current flag threshold (the borderline band).
- **Belted / score-only** (`tgc_banding`, `motion_blur`, `air_gap_reverberation`, `near_field_clutter`, `vessel_shadow_in_roi`) — no flag, so rank every cohort frame by the detector's raw score: the candidate set is the top scorers (likely positives, for the count) plus a spread through the mid-range (where the cutoff will land, for the borderline band).
- **Pipeline-context** (`segmentation_low_confidence`) — its score (the segmenter boundary-entropy) exists only on predicted-mask runs; the annotator-mask cohort cannot produce it. Candidate selection needs a predicted-mask pass over the cohort first (run the segmenter, capture `boundary_entropy` per frame).

**Prerequisite tool.** Candidate ranking needs a per-frame score table — `qa_cohort_failure_histogram.py` tallies aggregates but does not dump per-frame scores. A small extension (or sibling script) writes `frame_base` + each detector's raw score per cohort frame; that table is the ranking input.

**Two labelling disciplines:**
- **Blind** — the labeller assesses each candidate against the guide without seeing which detector selected it or what it scored, so the label is an independent ground truth, free of anchoring. (Needs a campaign UI mode with the Phase-4 pre-fill suppressed — open decision I3.)
- **Full-guide** — the labeller grades each frame against the *whole* taxonomy, not only the code it was a candidate for. One labelling action then yields a verdict on every detector for that frame: candidate selection concentrates the positives, and the negatives for every other code come free.

## § F — Round 1: the concrete first pass

1. **Build the per-frame score table** (the § E prerequisite tool) over the cohort.
2. **Run the predicted-mask pass** for `segmentation_low_confidence`'s boundary-entropy scores — or defer that detector to round 2 (decision I5).
3. **Select ~100–150 candidates per target detector** by its § E mode; for the likely-rare codes (`air_gap_reverberation`, `subq_fat_compressed`, `wrong_site`), take whatever candidates exist and accept that the round-1 yield may fall short.
4. **Dedupe across detectors** — a degraded frame is usually a candidate for several detectors, so the unique frame count is well below the per-detector sum. Estimate: **~600–800 unique frames** for round 1.
5. **Double-label a ~150-frame stratified subset** with a second operator — this *is* the § 2.5 IRR study, folded into round 1: one effort, two deliverables (calibration data + the κ baseline).
6. **Label blind, full-guide.** Estimate **~10–15 operator-hours** at roughly 1 min/frame — refine after a ~50-frame pilot. (For comparison, the full § 14 Step-1 figure is ~50–100 operator-hours; round 1 is the front-loaded, highest-value slice of it.)
7. **Outcome — per detector:** count confirmed positives. **≥ 50 →** calibrate it (§ 11.2) — it becomes calibrated-provisional and starts driving the verdict. **< 50 →** carry to round 2, or escalate to FW-1 (§ G).

Round 1 should calibrate `dark_clip_high` outright (its ~172 flagged frames alone likely exceed 50) and, if the cohort holds the positives, the moderate-prevalence belted detectors (`motion_blur`, `tgc_banding`, `near_field_clutter`, `vessel_shadow_in_roi`). The rare codes are expected to fall short — that shortfall is a planned output, not a failure (§ H).

## § G — Cadence and the rolling model

1. **Round 1** → calibrate each detector that reached 50, the moment it does (rolling — not a batch gate). The QA layer starts genuinely working within the first pass instead of sitting "pending" for months.
2. **Round 2** → targeted top-up: label more candidates only for the detectors that fell short of 50.
3. **Channel 2** runs continuously underneath — monitor the ledger's per-code counts; it tops up the common codes over time.
4. **Re-calibrate upward** — as a code's count grows past 50 toward ~150–200, re-fit the cutoff (a data-update, § 12.4 — no code change), tightening the CI toward the publish bar.
5. **Escalate the persistent shortfalls** — a detector still below 50 after rounds 1–2 plus channel-2 accrual is data-starved in the existing cohort: route it to FW-1 (the Round-3 `Görüntü Alma Usulleri v3` acquisition protocol). It needs *new* data, not more labelling of old data.

## § H — Honest limits — what this plan does not solve

- **The rare codes likely will not reach 50 from the existing cohort.** `air_gap_reverberation`, `subq_fat_compressed`, and true `wrong_site` positives (a genuinely-not-RF frame is rare in an RF-study cohort) are expected shortfalls — they route to FW-1. The plan surfaces *which* detectors are data-starved; it does not manufacture their data.
- **`segmentation_low_confidence` needs a predicted-mask pass** — an extra prerequisite the annotator-mask cohort cannot satisfy directly.
- **`saturation_high_global` / `_regional` are excluded from round 1** — collecting positives on the `_final.png` modality is futile while the detectors are modality-inert there (Finding C). They re-enter once FW-25 settles the modality.
- **`wrong_site` positives are collectable now, but its threshold fit waits on FW-24** — the `rf_fraction` denominator design-review precedes calibrating the cut-off.
- **The Tier-2 learned grader is out of scope here** — it needs ~200 positives *per code* (§ 14 Step 5) and is a longer, acquisition-gated horizon (§ 11.7).
- The per-code target is a uniform 50 in v0; § I notes the clinical review that may adjust it.

## § I — Open decisions / needs ratification

| # | Decision | Status |
|---|---|---|
| I1 | Initial "enough" = 50 confirmed positives per code | **ratified** — Akadir, 2026-05-21 |
| I2 | Per-code target adjustments (a safety-critical or harder-to-judge code may warrant more than 50) | open — Anonaao's clinical review |
| I3 | Channel-1 campaign labels collected **blind** to detector output (needs a pre-fill-suppressed UI mode) | open — recommended here; ratify |
| I4 | The publish-grade count (~150–200) and whether it differs for threshold calibration vs the learned grader | open — confirm against § 11.0 |
| I5 | Round 1 includes the predicted-mask pass for `segmentation_low_confidence`, or defers it to round 2 | open — implementer call |

---

## Changelog

| Version | Date | Change | Source |
|---|---|---|---|
| v0 | 2026-05-21 | Initial draft — the two channels, the 50-positive target, the candidate-selection mechanism, round 1, the rolling cadence. | Akadir's ratification of the 50 target; the Build-Step 2 Phase-2 cohort histogram |

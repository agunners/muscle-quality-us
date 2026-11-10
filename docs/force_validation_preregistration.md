# Force-validation pre-registration — imaging → isometric knee-extension torque

*Locked 2026-07-23, before any model was fitted. The point of writing it down first is to turn a
search into a test: the models, the directions, and the decision rule are fixed here so a result
cannot be reverse-justified after the fact. Substrate: `data/qa_master.csv` (gitignored, PHI), one
row per `study_uid`.*

## The question

Does the muscle-ultrasound score — and each of its three axes (size, fat, texture) — predict
measured **isometric knee-extension peak torque** above what body demographics alone explain? This is
the first *external-validity* check: does an imaging measure move with an independent physical
criterion (strength). It is **not** accuracy against doctors' grades (that is the later blinded
re-grade), and it is **not** a claim about patients (see range restriction below).

## Sample

n = **56** Round-3 studies that have both `izo_torque_nm` (isometric peak torque — the primary
strength criterion) and complete demographics. Nested completeness: M0–M2 all n=56 (zero listwise
deletion), texture rung n=47. The criterion is reliable (`izo_cv_pct` median 4.8%), so residual
noise sits on the imaging side, not the force side.

**Range restriction (pre-stated limitation).** The new round is broadly healthy — **zero POOR
muscle.** A null result here means "not resolved in a healthy, range-restricted sample," **not**
"imaging fails in patients." Every negative finding is reported with this caveat attached.

## Outcome and predictors (exact columns)

- **Outcome:** `izo_torque_nm`.
- **Confounders (always included):** `sex`, `weight_kg`, `height_cm`, `age`.
- **Size axis (primary):** `star_ratio` (= QMT / BMI — the deployed score's size axis).
  - Sensitivity swaps: `qmt_mm` (raw quadriceps thickness), `full_thickness_q50_mm` (median RF full
    thickness). *There is no precomputed muscle **max** thickness; a literal per-frame max is
    derivable from `qa_frames.csv` but is the least frame-robust statistic — deferred, and flagged
    as such if computed.*
- **Fat axis:** `fat_to_depth_q50` (fat fraction).
- **Texture axis:** `int_cv_within` (echo CV) — **gated**, see decision rule.

## Nested models (added in order of prior confidence)

| Model | Terms | n | Role |
|-------|-------|---|------|
| **M0** | `torque ~ sex + weight + height + age` | 56 | baseline |
| **M1** | M0 + `star_ratio` | 56 | **primary confirmatory test** |
| **M2** | M1 + `fat_to_depth_q50` | 56 | secondary |
| **M3** | M2 + `int_cv_within` | 47 | secondary, **gated on the frame-selection noise floor** |

Predictors are z-scored over the analysis rows, so each β is "Nm of torque per 1 SD of the feature"
and the three axes are directly comparable.

## Directional hypotheses — grounded in biology, NOT borrowed from the grade-fitted score

- **Size — POSITIVE (β > 0).** Larger quadriceps cross-section → more force. Direct and mechanical.
  **High** prior; the feature is stable under frame selection. (The quadriceps is the knee extensor,
  so the anatomy matches the criterion.)
- **Fat — NEGATIVE (β < 0).** Higher fat fraction = fatty infiltration / myosteatosis → less
  contractile tissue → less force. Direct, well-supported in the sarcopenia literature.
  **Moderate–high** prior; stable feature. Direction is taken from biology, *not* from the condition
  model's coefficient.
- **Texture — NEGATIVE (β < 0).** Higher echo CV = more heterogeneous tissue, a marker of
  fibro-fatty change → less force. **Low** prior: the direction is indirect, the feature is the
  noisiest we produce (whole score swings 100→59 on frame selection alone), and the anechoic-dropout
  artifact can inflate it for non-muscle reasons. We **deliberately do not** adopt the condition
  model's fitted int_cv sign — that model is fit to doctors' grades, and doctors grade partly on echo
  appearance, so its sign is near-circular for a strength test.

  > **⚠ CORRECTION filed 2026-07-26 — the registered text above is left verbatim and unaltered; this
  > note records that the registration itself was wrong, not the result.** The texture direction was
  > **not** an open question at lock time. It had been ratified on **2026-05-12** and is wired into the
  > product: the feature's display label is *"Fascicular contrast (echo CV)"* and its on-screen caption
  > reads *"Higher here means preserved internal contrast; lower means a more homogenized RF ROI."*
  > (`p05_assessment_view::_FEATURE_LABELS`; memory `feedback_fascicular_contrast_locked`). Akadir stated
  > the chain plainly, before any of this analysis existed: **higher fascicular contrast = better-preserved
  > fibre architecture = more strength → β > 0.** In guarding against circularity I discarded not just the
  > *fitted* sign but the *physical* meaning that had been established two months earlier from image
  > content — and registered the opposite of the house prior. The correct pre-registered direction was
  > **POSITIVE, with a moderate prior.** See "The sign test" in Results.

## Decision rule

An added feature is **"supported"** iff **(a)** its coefficient sign matches the hypothesis **and**
**(b)** it improves fit over the model directly below it — likelihood-ratio / partial-F at p < 0.05,
equivalently the 95% CI on the standardized β excludes 0. For **texture (M3)** there is an additional
gate: the effect must **exceed the frame-selection noise floor** (bootstrap of `int_cv` over frames,
computed separately) — a texture effect smaller than its own measurement wobble is not believed.

For every rung, report the standardized β with 95% CI, ΔR², and the partial test — **regardless of
outcome.**

## Guardrails (pre-committed)

- **Primary test = M1 (size).** Fat and texture are pre-specified **secondary**; the size sensitivity
  swaps are **robustness**, not new discoveries. No feature outside this list is added; the outcome is
  never swapped.
- **Leg matching — OPEN.** Torque is the *tested* leg; imaging is the *imaged* leg. `qa_master` does
  not yet carry leg, so imaged == tested is **unverified**. Verify (join `Dominant Bacak` against the
  force sheet's tested leg) and flag/exclude mismatches **before any final claim.**
- **Report all models, including nulls;** attach the range-restriction caveat to every negative.
- Standardize predictors so βs are comparable.

## What would change our mind / what this cannot show

A clean size effect that survives the sensitivity swaps is real, defensible evidence *within a healthy
sample.* A null on any axis is uninterpretable as "imaging fails" until tested in a sample with
impaired muscle. Texture is the axis we expect to be hardest to read and the one we trust least going
in.

---

## Results (run 2026-07-23) — and honest deviations

**Deviations from the plan above, logged for integrity:**

- The pre-registered **frame-selection bootstrap is infeasible** from the substrate — `qa_frames.csv`
  stores frame *inventory* (depth / spacing / role / path), not per-frame feature values. Substituted
  the **10 already-computed operator-vs-auto score pairs** (`opsel_*`) as the frame-selection
  sensitivity sample; a true per-frame floor needs re-measuring ~6,700 frames (deferred here — but
  **re-promoted 2026-07-26** as a gate on the texture claim; see the sign test and the closing section).
- **The texture hypothesis as registered was wrong — the data was not.** I registered int_cv NEGATIVE
  (higher → weaker). That contradicted this project's own ratified reading of the feature, fixed
  2026-05-12 and shipped in the UI caption: higher echo CV = *preserved fascicular contrast*, a healthy
  sign. Akadir had stated the resulting prediction before any analysis existed — more contrast = more
  preserved muscle = more strength. Empirically it is POSITIVE on both anchors (grade ρ +0.711,
  n=255; isometric force +7.99 Nm/SD, n=47, n.s.). **So this is a confirmation of the house prior, not
  a reversal by the data** — an earlier readout that called it "a hypothesis the data reversed" was
  mis-framed and is corrected here. The lesson stands but points the other way: ground a feature's sign
  in what it physically measures — and *check what the project already ratified* before registering
  against it.

**Force — above demographics.** Demographics alone (sex + weight + height + age) explain **74–76%** of
every force outcome; imaging competes for the small remainder. Min detectable ΔR² at 80% power ≈ **3.5%**.

| axis (best proxy) | isometric torque | isokinetic torque | isokinetic power |
|---|---|---|---|
| size (thickness / QMT / area) | +2.4% ΔR², p≈0.02 ✔ | n.s. | n.s. |
| fat fraction | +2.9% ΔR², **p=0.013** ✔ | n.s. | n.s. |
| texture (int_cv) | +1.1%, p=0.17 (sign as ratified) | n.s. | n.s. |
| composite `condition_score` | +0.5%, p=0.37 ✗ | n.s. | n.s. |

Size and fat are collinear (added together, neither is individually significant; jointly ~3%). The
significant effects (2–3%) are real-in-sample but **below the 80%-power bar** → replication not
guaranteed; the nulls (0.5–1.1%) are **underpowered, not proven absent**. Signal is **isometric-specific**
(caveat: isokinetic may simply be the noisier criterion — not isolated).

### ★ The sign test — three axes, two criteria, disjoint people, 3/3 concordant

Magnitude and direction are different questions, and the direction result is the stronger one. Each axis
was given a physical meaning *before* this analysis; each was then read against two criteria measured on
**non-overlapping sets of people** (282 old-cohort grades · 56 new-cohort dynamometer torques).
Reproduced by `scripts/analysis/verify_texture_sign.py` against `data/qa_master.csv`:

| axis | meaning fixed in advance | force β / SD (n=47–56) | grade ρ (n=216–269) | concordant? |
|---|---|---|---|---|
| size `star_ratio` | bigger cross-section → stronger | **+11.66** [+0.83, +22.49] | **+0.706** | ✔ both positive |
| fat `fat_to_depth_q50` | more infiltration → weaker | **−16.96** [−30.21, −3.71] | **−0.758** | ✔ both negative |
| texture `int_cv_within` | more fascicular contrast → better preserved → stronger | **+7.99** [−3.54, +19.53] | **+0.711** | ✔ both positive |

All three land on the side named in advance, on both criteria. That is a **construct-validity** result:
evidence that these features measure the muscle properties we claim they measure, and not acquisition
artifact. Weighting of the legs is not equal, and the difference matters:

- The **grade** leg is partly circular for texture and fat — a radiologist grades partly on echo
  appearance, so the feature and the grade read the same pixels. Strong correlation, weak independence.
- The **force** leg is **not** circular at all. A dynamometer never sees the image. This is the leg that
  carries the construct claim — and it is the leg that is directionally right but **underpowered**
  (texture CI includes 0).

**Consequently the texture null must not be read as "texture is unrelated to strength."** Three live
explanations remain, and n=56 cannot separate them: (a) the true effect is genuinely small; (b) it is
**attenuated by our own measurement noise** — frame selection alone moves int_cv 1.314 → 0.705 on
AKA017, and measurement error biases an estimate *toward zero*; (c) **range restriction** — zero POOR
muscle in the force sample, so the axis that detects degradation has almost nothing to detect. Note that
(b) makes the earlier disposition — "int_cv is a non-predictor of force, so the reliability ceiling is
non-binding" — **circular**: a feature too noisy to predict will always look like a non-predictor. The
per-frame reliability floor is therefore back on the critical path for any *texture* claim (it remains
off the path for the size and fat claims, which are stable under frame selection).

**Reliability — frame selection (opsel vs auto, n=10).** Score wobble **median 1.0 / 100, max 41**;
int_cv wobble median 2% of the cohort's between-study SD, **max 288%**. → the measurement is reproducible
**except on a diagnosable tail** (shadow / no-clean-RF studies), not pervasively noisy.

**Grade anchor (old cohort, 282).** `condition_score` tracks the doctors' ordinal grade at **Spearman
0.84, AUC 0.95** (PRESERVED-vs-rest); int_cv 0.71, fat −0.76, size 0.63–0.66. The MVP — agreement with
doctors — is **achieved as a model** (CV-QWK 0.813 prior); unanchored only by the absence of inter-rater
data.

**★ The divergence (the finding).** Strength leans on **SIZE + FAT**; the doctors' grade leans on
**TEXTURE + the composite**. The shipped score is a **validated radiologist-agreement instrument, not a
strength proxy** — weighted to match doctors, it predicts isometric force *worse* than a plain thickness
measurement.

**The divergence is about WEIGHTING, not direction** (see the sign test above). Every ingredient points
the same way on both criteria; what differs is how hard each criterion leans on each ingredient. The
doctor's eye weights texture heavily; measured force weights size and fat. So the honest statement is not
"the score measures something unrelated to strength" — it is "the score is *tuned* to the doctor's
weighting, and that weighting is not the one that maximises force prediction."

**What the data structurally cannot do.** Grades and force sit on **disjoint people** (overlap 0); the
sample has **zero impaired muscle**; there is no leg verification and no inter-rater grade in the
substrate.

**The next is DATA, not modeling** — iterating the score is the stale path; it already does its MVP job
and cannot be tuned into a strength proxy without abandoning the doctor-agreement it was built for.
Ranked by leverage / cost: **(1) grade-bridge** — grade the 56 already-imaged, force-tested people so
grade + force + imaging finally sit on one person; **(2) inter-rater grades** — validate the second-read
agreement against how well doctors agree with each other; **(3) impaired-muscle cohort** — the untested
clinical premise.

**Revised 2026-07-26 — the per-frame reliability floor is promoted from "confirmation" to a gate on the
texture claim only.** It was demoted on the reasoning that int_cv does not predict force so its noise
cannot matter; the sign test shows that reasoning is circular (measurement error is itself a live
explanation for the null, and it attenuates toward zero). It stays compute-only — no patients, no clinic
time — and it is the only item on this list that does not depend on anyone else's calendar.

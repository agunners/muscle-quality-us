# Turning Eight Numbers Into One: The Muscle-Score Question

A briefing for the team. Discussion draft, 2026-06-11.

**Bottom line.** Yes, we can give people a single "muscle score" — and the honest way to build it is to make it *our best reconstruction of what your doctors would say when they look at the scan*. We can do that today, and we have evidence it reproduces our doctors' grades reliably. Two rules keep it honest: call it a **condition** score, not a "quality" score — because it includes muscle size, on purpose (the doctors' grades track size, so a faithful reconstruction does too) — and show a small **breakdown** beneath it, so anyone can see whether a low score means "small muscle" or "unhealthy tissue." What the number cannot yet do — and no amount of math will fix this — is prove it predicts strength or recovery, or tell whether a patient is genuinely improving. Those need the pilot study and an outside measurement (a strength test, an MRI). So: build the honest one-number version now, and keep the pilot as the thing that turns "matches our doctors" into "predicts what matters."

This document is layered on purpose. The next page is the whole story in six minutes. After that comes the reasoning in plain language. The statistics that back it all up are in a clearly-marked appendix at the end — skip them unless you want to check our work.

---

## The short version

The assessment page shows eight measurements of a person's muscle, each as a percentile — "you score higher than 80% of comparable people." The natural wish is to combine those eight into one headline "muscle score." We can meet that wish — but *how* you combine them matters enormously, and the obvious ways are wrong.

**The eight numbers are not eight separate facts.** They are eight views of about *two* things: how **big** the muscle is, and how **healthy the tissue** is. Five of the eight measure size, two measure fat, and only one measures the texture of the muscle tissue itself. When your doctors grade a scan, their grades reflect both — a bigger, leaner-looking muscle reads as "better." So the number we want is the one that captures their *overall* read, size included. We will call it a **muscle-condition** score, and we will reserve the word "quality" for the tissue-only part — because in medicine "quality" means the tissue specifically, not the size, and we do not want to mislabel a mostly-size number as "quality."

**You cannot get there by averaging the eight.** Several of them are literally the same measurement: one ("STAR") is just another ("QMT", the whole-quadriceps thickness) divided by body size; the muscle's thickness shows up three times in near-perfect agreement. So averaging counts size four or five times and tissue once. The everyday version: score someone's fitness by averaging their height, leg length, arm span, and one running test — you have measured size three times and fitness once, and the tallest person "wins."

**You cannot get there by weighting features by how well each predicts the grade, either.** The feature that *looks* like the best predictor — fat fraction — falls apart when you test all eight together. Not because fat doesn't matter, but because we measure fat two nearly-identical ways and the math can't tell which deserves the credit. It's like two suspects always seen together: you know one of them did it, but you can't pin it on either. So the two simplest recipes — equal weights, or rank-by-prediction — both break on these overlapping numbers.

**The fix is to let a proper model do the combining.** Instead of hand-picking weights, you fit a model whose only job is to reproduce your doctors' grade from the measurements. It untangles the overlap on its own, and the score it produces is, by construction, "the measurements' best guess at what your doctors would say." We tested this: it reproduces the grade about as well as the grade can be reproduced at all — close to the ceiling set by how much three doctors even agree with each other in the first place.

**The honest limit.** That grade is an expert looking at the *same picture* the measurements come from. So the score reproduces an expert's **eye**, not an **outcome**: it cannot, on this data, show it predicts strength, mobility, or recovery, and it cannot yet tell whether a change in the number is real improvement or just scanner noise. That's genuinely useful — a fast, consistent second read — but it is not a clinical prediction. Closing that gap is the pilot's job, not the model's.

**So the recommendation is:** build the one number now as "predicted muscle condition," show the size / fat / tissue breakdown underneath it, label it plainly as a reproduction of an expert read, and put the real effort into the pilot and an outside yardstick — the only thing that turns "matches our doctors" into "predicts what happens to the patient."

---

## How to understand the problem

### The page shows eight measurements

Each of the eight is turned into a percentile by comparing the patient to a reference group of 282 earlier scans. A percentile of 80 means "higher than 80% of comparable people." Here is what the eight actually measure:

| Measurement (app label) | What it physically is | This is really… |
|---|---|---|
| RF thickness — median | How thick the rectus femoris muscle is | **Size** |
| RF thickness — peak | The thickest point of the same muscle | **Size** |
| Visible RF area | The cross-sectional area of the same muscle | **Size** |
| QMT | Whole-quadriceps thickness | **Size** |
| STAR | Quadriceps thickness divided by body-mass index | **Size** (body-adjusted) |
| Fat thickness | How thick the fat layer over the muscle is | **Fat** |
| Fat fraction | Fat layer as a share of total depth | **Fat** |
| Fascicular contrast | How varied the muscle's internal texture looks | **Tissue quality** |

Five are size, two are fat, one is tissue quality. That imbalance is what makes a naive single number dangerous.

### What you're actually choosing: "condition" or "quality"

There are two different questions you could turn into a number, and they are not the same:

- **"What overall shape is this muscle in?"** — this includes size. A bigger, leaner muscle scores better. This is what your doctors' grades actually track.
- **"How healthy is the tissue itself?"** — this excludes size; a small muscle can have perfectly healthy tissue. In medicine, this stricter question is what "muscle quality" means.

A car analogy: the *overall condition* of a car includes its engine size and its tune and its mileage; the *quality of the engine* is a narrower thing. Both are real, and they answer different questions.

We are building the first one — overall condition — because it matches your doctors and it is what a patient intuitively expects from a "muscle score." There is one firm rule that comes with that choice: **do not call it "quality."** A number that includes size is a *condition* number. Calling it "quality" would quietly claim it measures tissue health when it is mostly measuring size — exactly the confusion we are trying to avoid. Name it for what it is, and keep a separate tissue-only readout for the clinician who wants the strict sense.

### Several of the numbers are the same measurement

This is why you cannot just average. Some of the eight are not merely related; they are *the same thing measured again*. STAR is exactly QMT divided by body size. The muscle's thickness appears three times — median thickness, peak thickness, and area — and those three rise and fall together almost perfectly. The two fat numbers share the same underlying fat-layer measurement.

So an average does not give each idea an equal vote. It gives **size four or five votes, fat two, and tissue one**, and it pulls the result toward whatever the size measurements share. Whatever single number we build has to *undo* this overlap, not bake it in — and that is something a model does automatically and an average cannot.

### The surprising finding — why even "smart" weighting fails

Here is the result that genuinely surprised us, and it is the reason we recommend a model rather than any hand-built formula.

If you can't average, the natural next idea is: weight each feature by how strongly it predicts the doctors' grade on its own. By that measure, **fat fraction looks like the best feature**. But that ranking is misleading. When we put all eight into one proper test — asking each "how much do you add *that the others don't already explain*?" — fat fraction's apparent power **vanished**, because its near-twin (fat thickness) was already in the room and the test couldn't tell them apart. Meanwhile the muscle's internal **texture** stood out as a genuinely independent signal.

The lesson: simple recipes lie when features overlap. You cannot rank-and-weight by hand. You need a model that accounts for the overlap — which is exactly what "fit a model to reproduce the grade" does.

---

## What the number can and can't promise

Everything above is about building the score sensibly. This is about the harder limits — the ones that should shape how loudly we sell it.

**Matching the doctors is the goal, not a flaw.** The grade we train against includes the doctors' sense of size. Under a strict "pure quality" goal that would be a contamination. Under the goal you have chosen — reproduce the doctors' overall read — it is exactly right. So that complication simply goes away. Two harder limits remain.

**The score reproduces an eye, not an outcome.** The grade is an expert looking at the same ultrasound image the features come from. So the best the score can do is agree with an experienced reader. It cannot, on this data, show that it predicts anything a patient feels — strength, function, recovery, sarcopenia risk. To show *that*, we need an outside yardstick: a strength test, or an MRI that measures fat infiltration directly. We have none yet. This is why the ceiling here is **data, not math**: a cleverer model still only reproduces the expert; the upgrade comes from the pilot.

**We can't yet use it to track change.** A score meant to show "this patient improved" must know how much the number jiggles on its own when you simply re-scan an unchanged muscle. We have almost no repeat-scan data to measure that jiggle. Until the planned repeat-scan cohort exists, we cannot honestly tell a clinician whether a 5-point move is real or noise.

---

## What to do, and what not to do

### Do

1. **Build the headline as a model that reproduces your doctors' overall grade** — a "predicted muscle-condition" score. Let the model sort out the overlap; do not hand-pick weights.
2. **Show a breakdown beneath it — size, fat, tissue texture** — so a low score can be read as "small muscle" versus "degraded tissue." A bare condition number can mislead; with the breakdown, it is safe to act on. This is the safeguard that makes one number trustworthy, not a second headline.
3. **Name it "condition," not "quality,"** and offer a separate tissue-only "quality" readout as a secondary view.
4. **Label it provisional, in plain words:** "a fast reconstruction of an experienced reader's overall impression; not yet checked against strength or MRI, and not yet able to track change over time."
5. **Choose the feature set for coverage, not just accuracy.** An all-eight-feature model works on only about two-thirds of patients (some scans are missing the femur-based measurements); a leaner set reaches far more. And re-fit it on a representative sample before shipping — the data we analyzed skews older and more "poor-grade" than the full population (appendix).

### Do not

1. **Don't average the eight percentiles.** It counts size four-to-five times and buries everything else.
2. **Don't weight features by how well each predicts the grade.** Overlap makes that ranking lie; it would crown a feature we can't even credit on its own.
3. **Don't call it "quality."** It includes size; that word claims tissue-health it doesn't deliver.
4. **Don't claim it's validated against strength, function, or MRI, or that it tracks improvement.** It isn't, and it doesn't — yet.
5. **Don't pour weeks into making the model fancier.** It already reproduces the grade near the ceiling. The gains now are in data — the pilot — not in math.

### A secondary readout to keep in your pocket

There is a stricter "tissue-quality" number — overall condition with the size effect statistically removed, answering "is the tissue worse than this muscle's size would predict?" It is worth showing to clinicians as a secondary view, but not as the headline: it is a statistical residual that is harder to explain at the bedside and noisier than the condition score. Offer it; don't lead with it.

---

## The menu of options, with trade-offs

From simplest to most rigorous. The trade is always *speed and simplicity* versus *honesty and defensibility*.

| Option | What it is | Why it's tempting | Why it falls short |
|---|---|---|---|
| Keep eight tiles, no score | Show the eight as they are | Honest; nothing to defend | The "people want one number" wish goes unmet |
| Average the eight | One percentile from the eight | One line of code | Mostly a size score; double-counts; mislabels size as quality |
| Weight by prediction strength | Weight each by how well it predicts the grade | Feels data-driven | **Wrong** — overlap makes the ranking lie |
| **Predicted-condition model** (recommended) | One number = a model's reconstruction of the doctors' overall grade, plus a size/fat/tissue breakdown | Matches what your doctors do; untangles the overlap automatically; honestly named | Reproduces an expert read, not an outcome; needs a coverage-robust re-fit; can't track change yet |
| Externally-validated index | Built and checked against strength / MRI | The *only* version that earns the word "validated" | Needs the pilot data — months away, but it's the real prize |

---

## What we can and cannot claim

Put this verbatim into any stakeholder-facing material, so the score is never oversold.

**We can say:**
- The condition score is our best reconstruction of how an experienced reader would grade this scan overall, built from one representative measurement per clinical axis — size, fat, and tissue texture — and compared against a reference group of 282 prior scans.
- It reproduces that expert grade reliably — close to the ceiling set by how much the graders themselves agree.
- A breakdown shows what drives each score: muscle size, fat, and tissue texture.

**We cannot yet say:**
- That a *change* in the score over time is real rather than scanner noise — we have no repeat-scan data yet.
- That the score predicts **strength, function, sarcopenia, or any clinical outcome** — that comparison hasn't been done.
- That it matches **MRI or biopsy**, or that it is a **diagnosis**. It reproduces an expert's read; it is not a medical verdict.

**One honest sentence for the screen:** *"Provisional muscle-condition score — our best reconstruction of an expert's overall read of this scan; not yet validated for tracking change over time or against strength/MRI."*

---

## Technical appendix

*Stakeholders can stop above. What follows is the statistical backing, for anyone who wants to check the reasoning. Every number on our own data is reproducible by running [`scripts/score_design_analysis.py`](scripts/score_design_analysis.py) on [`data/cohort_stats/per_study_cohort.csv`](data/cohort_stats/per_study_cohort.csv) (n = 282; the multivariable, factor, and PCA results run on the 184 studies that have all eight features).*

### The eight features and their structure

Direction of each feature is taken from the app's own code ([`cohort_placement.py:217`](scripts/preflight_helpers/cohort_placement.py#L217); the four raw size features are tagged `neutral` — *"bigger is not better, it is just bigger"*, [`cohort_placement.py:220`](scripts/preflight_helpers/cohort_placement.py#L220)). STAR is computed as QMT ÷ BMI ([`measurements.py:1465`](labeling_app/measurements.py#L1465); on-cohort identity check: Spearman = 1.000). Feature list and labels: [`feature_catalog.py:36`](scripts/preflight_helpers/feature_catalog.py#L36).

**Collinearity (how much the features overlap).** Variance-inflation factors reach 40 (fat fraction) and 26 (fat thickness); anything over 10 is "severe." Spearman overlaps: RF thickness median↔peak 0.96, thickness↔area 0.94, STAR↔QMT 0.82, the two fat features 0.93. A principal-component analysis (a standard "how many real dimensions are here" test) puts **66% of all variation on one combined dimension**, 83% on two, 91% on three — eight tiles, roughly two-to-three real dimensions.

**Factor structure.** A 3-factor exploratory factor analysis (varimax rotation) splits the eight into two correlated size factors plus one composition factor:

| Feature | Size (RF) | Composition | Size (quad) |
|---|---|---|---|
| RF thickness median | **0.93** | −0.27 | 0.25 |
| RF thickness peak | **0.87** | −0.26 | 0.28 |
| RF area | **0.86** | −0.16 | 0.30 |
| QMT | 0.45 | −0.23 | **0.85** |
| STAR | 0.34 | −0.40 | **0.67** |
| Fat thickness | −0.21 | **0.97** | −0.12 |
| Fat fraction | −0.36 | **0.83** | −0.40 |
| Fascicular contrast (texture) | 0.14 | **−0.66** | 0.22 |

The texture feature lands on the composition (fat) factor rather than its own. Read this with care: there is only **one** texture feature, and a single indicator can never define its own factor — it must load onto its nearest correlate (fat, at ρ = 0.74). Texture also has the lowest communality of the eight (≈ 0.52; about half its variance is left unexplained) and starts to claim its own dimension if a fourth factor is allowed. So "texture and fat are inseparable" is **under-instrumentation, not demonstrated biology**; the test is to enrich the texture family (the companion study has fat-corrected texture features). What the data does cleanly show is the top-level size-vs-composition split, which matches the EWGSOP2 sarcopenia consensus's quantity-vs-quality distinction — and is what the size/fat/tissue breakdown beneath the headline reports.

Internal-consistency note: the five size features have Cronbach's α = 0.93 (so alike they're effectively one measurement). We deliberately do *not* quote an α for the full eight — α is not direction-invariant, so the raw value (0.51) only reflects that fat opposes size; reverse-code the fat features and it jumps to 0.93. Dimensionality is the factor analysis's job, not α's.

### Why univariate weighting fails (the surprising finding, quantified)

Univariate Spearman ρ against the grade: fat fraction 0.76, fascicular contrast (texture) 0.73, STAR 0.71, fat thickness 0.68, QMT 0.64, RF thickness median 0.60, RF thickness peak 0.58, RF area 0.52. Fat fraction leads.

In a multivariable proportional-odds ordinal regression (the right model for an ordered grade; standardized features; n = 184), the picture inverts:

| Feature | Univariate ρ | Multivariable β | p | Read |
|---|---|---|---|---|
| Fascicular contrast (texture) | −0.73 | **−2.38** | &lt; 0.001 | independent, strong (VIF 2.2) |
| STAR (body-adjusted) | −0.71 | **−2.17** | &lt; 0.001 | independent, strong (VIF 3.6) |
| Fat fraction | +0.76 | +2.15 | 0.13 | **unidentifiable** (VIF 40) |
| Fat thickness | +0.68 | −0.58 | 0.61 | redundant (VIF 26) |
| RF thickness median | −0.60 | −0.97 | 0.29 | redundant (VIF 17) |
| QMT | −0.64 | +1.03 | 0.11 | redundant/unstable (sign flip) |
| RF thickness peak | −0.58 | −0.08 | 0.91 | redundant (VIF 10) |
| RF area | −0.52 | −0.15 | 0.79 | redundant |

Fat fraction's β stays the right sign and is one of the largest, but its standard error balloons (VIF 40, ρ = 0.93 with fat thickness), so p = 0.13. That is *unidentifiable* — its credit can't be separated from its collinear twin — **not zero**. A fairer importance measure that shares credit across collinear features (an exact Shapley decomposition of R²) gives: texture 23%, fat fraction 17%, STAR 16%, fat thickness 13%, the four raw-size features 6–9% each; by axis, size 47% and composition 53%. So fat is real *collectively*; it just can't be pinned to one feature. The practical upshot for the build: a model handles all of this natively — which is exactly why the recommendation is "fit a model to the grade," not "assign weights."

Robustness: bootstrapping the model 300 times, texture and STAR keep their sign 100% of the time, fat fraction 93%, and the four raw-size features flip 9–45% — the two survivors are stable, not a fluke. The proportional-odds assumption was checked (Brant test, not rejected). One honest caveat on STAR: because STAR ≡ QMT/BMI, its signal once QMT is held fixed is essentially inverse-BMI (its QMT-residual correlates 0.99 with 1/BMI) — so STAR's "independent" pull is partly body composition, not muscle size as such. The companion 98-feature screen agrees with the overall picture: under demographic adjustment its leading raw-size feature loses 91% of its effect while texture holds (loses 21%) — [`stage_e_track_b.csv`](../muscle_quality_exploration/runs/20260503/034929_default_infer/analysis/stage_e_track_b.csv).

### The ceiling, the criterion, and the missing data

Framing note: because the chosen headline is "reproduce the doctors' overall grade," the model landing near that grade's reliability ceiling is the **target met**, not a shortfall — we reproduce the target about as well as the target itself permits. The cautions below are about what that success does and does not license.

The grade is the 3-rater consensus. Single-rater agreement is Krippendorff's α ≈ 0.696 for the `quality_label` we use (weighted κ 0.63–0.73; [`irr_audit.json:10`](../muscle_quality_exploration/runs/20260503/034929_default_infer/analysis/irr_audit.json#L10) — note the sibling `preliminary_label` reads 0.706, a different label). Because the modeled label is a 3-rater consensus, its reliability steps up (Spearman-Brown ≈ 0.87), though the raters share one image so errors correlate and the truth is a band. The ceiling on achievable correlation is √(reliability), a band of ≈ 0.83–0.93; the eight-feature model's cross-validated reproduction of the grade (correlation ≈ 0.84, i.e. CV R² ≈ 0.71) sits at the low end of that band — solidly "reproduces the expert."

Two cautions matter more than the exact ceiling. (1) The in-sample R² of 0.74 is optimistic — 8 predictors, n = 184, no cross-validation; the honest figure is ≈ 0.71 (10-fold CV), and the in-sample value *exceeding* the single-rater reliability is itself a sign of over-fit, so don't quote 0.74. (2) The criterion and the features come from the **same image** (common-method bias), which inflates the reproduction relative to how the score would track an *external* truth. That, more than the ceiling, is why a clinical claim needs an external criterion, not more features.

No test-retest data exists yet (one subject with repeats; the minimal-detectable-change pilot is flagged "below-useful-estimate-floor", [`mdc95_pilot.json`](../muscle_quality_exploration/runs/20260503/034929_default_infer/analysis/mdc95_pilot.json)); a 24-hour repeat cohort is planned for Round 3.

### Limits of the analysis itself

- **Coverage.** An all-eight-feature score is defined on only **65%** of the cohort (QMT/STAR are missing on no-femur and proxy studies, plus some texture gaps); a texture-plus-fat score reaches 82%, texture alone 87%. Fewer features buys more patient coverage — a real consideration when choosing the model's inputs.
- **Complete-case bias.** The 184 fully-measured studies are **not representative**: more POOR-grade (20.7% vs 16.7% cohort-wide) and older (median age 53 vs 44.5; label χ² p = 0.006, age p = 0.002), because features are missing preferentially on younger, healthier scans. The model's weights were learned on that biased subset and should be re-fit on a missingness-robust feature set before anything ships.

### Validation roadmap (COSMIN framework)

A composite measure is validated in a set order. The useful point: most rungs are reachable now; only the clinically-loudest two need new data.

| Rung | Status |
|---|---|
| Structural validity (does the factor structure hold?) | **Done** — size-vs-composition split confirmed |
| Internal consistency | **Done** — size block coheres; the eight are two constructs |
| Measurement error / minimal detectable change | **Blocked — Round 3** (no repeat scans) |
| Construct validity (does the score behave as expected, e.g. tissue signal survives size adjustment?) | **Partly now** — the multivariable result is the test |
| Criterion validity (vs strength / MRI) | **Blocked — Round 5** (no outside yardstick) |
| Responsiveness (does it move when the muscle truly changes?) | **Blocked — Round 5+** |

### Sourcing and citation status

Numbers on our data are computed this session and reproducible via the script above. Reliability and the 98-feature corroboration are file-cited to the companion `muscle_quality_exploration` study. The published works that set the *definitions and the checklist* — the EWGSOP2 sarcopenia consensus (Cruz-Jentoft et al. 2019), the specific-force definition of muscle quality, the Heckmatt (1982) and Pillen & van Alfen echo-intensity tradition, and the COSMIN validation framework (Mokkink et al.) — are cited from general knowledge; I have not opened those papers, so no specific number above depends on them. Young 2015, IBSI, Molinger 2024, López-Gómez 2025, and Khan 2024 are in the companion corpus and cited as such. Toward publication, the five framework references should be read and cited properly.

---

## Implementation methodology — how the single score was actually built

*This section documents the build that the analysis above motivates: the model, the choices made, what was tried and rejected, and the caveats that travel with the shipped artifact. Reproduce the comparison with [`scripts/muscle_condition_model.py`](scripts/muscle_condition_model.py) and [`scripts/condition_options_compare.py`](scripts/condition_options_compare.py); the frozen production scorer and its self-test are [`scripts/preflight_helpers/condition_model.py`](scripts/preflight_helpers/condition_model.py).*

**Plain-language key (the terms used below).**

- **Fit, then freeze.** "Fitting" the model = letting it learn, from the 282 already-graded scans, how much each measurement should count. "Freezing" = doing that learning **once, offline**, and saving the result — a handful of numbers — to a small file. The live app never re-learns; it reads that file and does arithmetic, so every patient gets the *exact same* rule, instantly and identically.
- **Models A, B, C** are three **recipes** for the same score. They differ in one ingredient — which measurement stands in for "muscle size": **A** uses raw muscle thickness, **B** throws in all eight measurements, **C** uses thickness *divided by body size* (a number called STAR). We built all three, raced them fairly, and ship **C** as the headline.
- **"Ordinal" model** = a model built for an *ordered* grade (preserved is better than intermediate is better than poor), not a plain number and not three unrelated boxes.
- **QWK** (the accuracy yardstick) = an agreement score from 0–1 where a near-miss is forgiven more than a big miss. We judge the model against how well the *doctors agree with each other* (QWK 0.63–0.73).
- **Out-of-fold** = every accuracy figure is measured on patients the model was *not* trained on — so the numbers can't flatter themselves.
- **VIF** (overlap) = how much the measurements double-count each other; above 10 is bad, under 3 is clean.

### What was built

The score is an **ordinal-logistic model that reconstructs the doctors' three-level grade** (PRESERVED / INTERMEDIATE / POOR) from a handful of standardized features. It is fit once, offline, frozen to a small JSON artifact, and served at assessment time with pure NumPy — no live re-fit, deterministic.

- **Fit / freeze:** [`condition_model.py`](scripts/preflight_helpers/condition_model.py) fits each option with `statsmodels` `OrderedModel` (proportional-odds logit) on [`per_study_cohort.csv`](data/cohort_stats/per_study_cohort.csv) and writes the standardization (μ, σ), the slope vector (β), the two cutpoints, the band boundaries, and the cohort score-box to [`condition_models.json`](data/cohort_stats/condition_models.json).
- **Serve:** at scoring time that file is read and the class probabilities are recomputed in pure NumPy. A self-test asserts the NumPy path reproduces `statsmodels` to a **maximum absolute probability error of 0.0** — the frozen scorer *is* the model, not an approximation of it.
- **Deployed weights use the full cohort.** Cross-validation (below) is used only to *report* accuracy honestly; the coefficients that ship are fit on every study that has the model's inputs — you validate out-of-fold, you deploy on all the data.
- **On the page** (the figures from [`condition_panel.py`](labeling_app/assessment_pages/condition_panel.py) wired into p05): a coloured **band** plus the 0–100 number on a strip marked with the model's own cut-lines, and an opt-in **three-recipe comparison** so the model choice is visible. The size / fat / tissue detail *behind* the score is the page's **existing measurement cards** — reused, not redrawn as a second copy. (The "muscle relative to body size" note is now on the live page, as a caption under the headline; only the standalone preview's separate size/fat/texture *bars* remain demo-only — see caveat 7.) The integration is **additive and fail-silent**: a scan the model cannot score shows nothing and never disturbs the rest of the page.

### The model

- **Target:** the 3-rater consensus `quality_label`, mapped PRESERVED → 0, INTERMEDIATE → 1, POOR → 2.
- **Form:** proportional-odds **ordinal** logistic regression — the right model for an *ordered* grade (a linear regression would treat the grade as continuous; a multinomial would discard the ordering). Inputs are standardized on the cohort; one shared slope vector, two cutpoints.
- **From probabilities to a number:** the model returns P(PRESERVED), P(INTERMEDIATE), P(POOR); the expected grade `e = p·[0,1,2]` becomes a **condition score `(1 − e/2)·100`** on 0–100, higher = better (PRESERVED = 100). The score is binned to a band at boundaries derived from the cohort's *own* predicted scores (the midpoint between where the model's most-likely class flips). For the headline model these are **POOR < 25.5 · INTERMEDIATE · ≥ 75.6 PRESERVED**.
- **Why both a band and a number:** the **band** is what we stand behind (off-by-two misclassification is 0% in cross-validation); the raw 0–100 number is shown only as supporting detail, because it is finer than the model truly resolves.

### The calculation, with the actual numbers

This is the whole score written out, so anyone can reproduce a patient's number by hand. It is five steps; every constant appears in the tables below (and in [`condition_models.json`](data/cohort_stats/condition_models.json)), and the serving code is exactly these five steps in pure NumPy.

**Why this shape, and not a plain regression.** The doctors' grade has only three rungs, in order — PRESERVED, INTERMEDIATE, POOR. An ordinary linear regression would treat those as the evenly-spaced numbers 0, 1, 2 and cheerfully predict 2.7 or −0.3, off the ends of a scale that has no "off the ends." So the model instead predicts the *probability* of each rung, using the standard tool for ordered categories — an **ordinal logistic regression**. The five steps below turn a patient's measurements into those three probabilities, then into one number.

**Step 1 — put every feature on a common scale.** The features come in different units: STAR is a ratio near 1.2, muscle thickness is about 15 mm, cross-sectional area is about 560 mm². You cannot weigh a millimetre against a ratio directly, so each feature value `x` is first converted to a **z-score** — how many standard deviations it sits above or below the cohort average:

```
z = (x − μ) / σ
```

`μ` is that feature's cohort average and `σ` is its spread (standard deviation); both are fixed numbers, listed per feature in the tables. A z of 0 means exactly average, +1 means one standard deviation above average.

**Step 2 — combine the features into a single total.** Each standardized feature gets a **weight `β`** (beta), learned when the model is fit. Multiply each z-score by its weight and add them up:

```
η = β₁·z₁ + β₂·z₂ + … + β_k·z_k
```

The result `η` ("eta") is the model's one-number summary of the patient. The *sign* of each weight tells the whole story of how that feature behaves: a **negative** weight means "more of this is healthier" (it pushes the patient toward PRESERVED), a **positive** weight means "more of this is worse." There is no separate intercept term — the two cutpoints in the next step do that job.

**Step 3 — turn the total into three probabilities.** Two **cutpoints**, `c₁` and `c₂` (fitted alongside the weights), mark where the grade boundaries sit on the η scale. They are combined with η through the **logistic function** `σ(t) = 1 / (1 + e⁻ᵗ)` — the S-shaped curve that squashes any number into a probability between 0 and 1:

```
P(PRESERVED)    = σ(c₁ − η)
P(INTERMEDIATE) = σ(c₂ − η) − σ(c₁ − η)
P(POOR)         = 1 − σ(c₂ − η)
```

The "proportional-odds" name means the same weights `β` are used at *both* boundaries — the model may slide a patient toward "worse," but it is not allowed to re-weight the features differently for the PRESERVED-vs-rest decision and the POOR-vs-rest decision. That restraint is exactly what lets the score carry one interpretable set of weights instead of two.

**Step 4 — collapse the three probabilities into one 0–100 number.** Take the probability-weighted average of the grade values (PRESERVED = 0, INTERMEDIATE = 1, POOR = 2) to get an **expected grade `E`** between 0 (best) and 2 (worst), then flip and rescale so that higher reads better:

```
E = 0·P(PRESERVED) + 1·P(INTERMEDIATE) + 2·P(POOR)
condition = (1 − E / 2) × 100
```

A patient the model is certain is PRESERVED scores 100; one it is certain is POOR scores 0; everyone else lands in between.

**Step 5 — read off the band.** Compare the 0–100 score to two thresholds — the model's *own* decision points, placed where its most-likely grade flips across the cohort, not external clinical cut-offs. Which side of the thresholds the score falls on is the band the headline shows.

**The fitted numbers.** Each option below is a complete, self-contained model — its own features, weights, standardization, cutpoints, and band thresholds. You cannot lift a weight from one option and use it in another. Read each row as "take this feature, standardize it with this μ and σ, multiply by this β." Remember the sign rule: a negative β means higher-is-healthier.

*Model C — STAR / Fat / Texture (the shipped headline), fit on n = 197 studies*

| Feature (standardized) | weight β | cohort mean μ | cohort spread σ |
|---|---|---|---|
| STAR (`star_ratio`) — muscle ÷ body size | −1.896551 | 1.244631 | 0.350869 |
| Fat fraction (`fat_to_depth_q50`) | +1.715812 | 0.264802 | 0.109984 |
| Fascicular contrast (`int_cv_within`) | −2.069097 | 0.506685 | 0.174569 |

Cutpoints c₁ = −2.826770, c₂ = 4.680310. Band thresholds: **POOR below 25.5, PRESERVED at or above 75.6** (INTERMEDIATE between). Reading the signs: more relative muscle (STAR) and more fascicular contrast both push toward PRESERVED (negative weights); more fat pushes toward POOR (positive weight) — and fascicular contrast carries the single largest pull.

*Model A — RF-thickness / Fat / Texture (raw thickness instead of body-adjusted STAR; shown for completeness), n = 213*

| Feature (standardized) | weight β | cohort mean μ | cohort spread σ |
|---|---|---|---|
| RF thickness (`full_thickness_q50_mm`) | −1.278409 | 14.783208 | 3.693711 |
| Fat fraction (`fat_to_depth_q50`) | +1.465145 | 0.267568 | 0.108049 |
| Fascicular contrast (`int_cv_within`) | −1.812933 | 0.499768 | 0.170170 |

Cutpoints c₁ = −2.457864, c₂ = 4.055976. Band thresholds: POOR below 25.0, PRESERVED at or above 74.1.

*Model B — all eight features (the accuracy reference, not shipped), n = 191*

| Feature (standardized) | weight β | cohort mean μ | cohort spread σ |
|---|---|---|---|
| STAR (`star_ratio`) | −2.172557 | 1.243230 | 0.350480 |
| RF thickness, median (`full_thickness_q50_mm`) | −0.685201 | 14.738165 | 3.661340 |
| Fascicular contrast (`int_cv_within`) | −2.101968 | 0.507924 | 0.175143 |
| RF thickness, peak (`rf_thickness_mm`) | −0.181701 | 17.401209 | 3.677233 |
| Fat thickness (`fat_thickness_q50_mm`) | −0.281897 | 12.059927 | 5.242535 |
| QMT (`qmt_mm`) | +0.992319 | 32.600513 | 7.390300 |
| RF area (`visible_rf_area_mm2`) | −0.337563 | 560.544126 | 154.869634 |
| Fat fraction (`fat_to_depth_q50`) | +1.850533 | 0.264510 | 0.108899 |

Cutpoints c₁ = −3.122172, c₂ = 4.979494. Band thresholds: POOR below 25.7, PRESERVED at or above 74.4. Notice that QMT carries a *positive* weight here while STAR is strongly negative — yet both measure muscle size, and bigger muscle is better. That sign-flip is the collinearity problem made visible: because STAR is just QMT divided by body size, the two are nearly redundant, so the fit splits their shared credit into a large negative and a compensating positive that swing from sample to sample. It is precisely why model B is the accuracy reference and not the headline.

**A worked example, by hand.** Study ACL762, which the doctors graded PRESERVED, run through model C:

| Feature | value x | z = (x − μ) / σ | contribution β·z |
|---|---|---|---|
| STAR | 1.7197 | +1.3540 | −2.5679 |
| Fat fraction | 0.2353 | −0.2683 | −0.4603 |
| Fascicular contrast | 0.6254 | +0.6799 | −1.4069 |

Add the contributions: η = −2.5679 − 0.4603 − 1.4069 = **−4.4351**. Then the probabilities: P(PRESERVED) = σ(−2.8268 − (−4.4351)) = σ(1.608) = **0.833**, P(INTERMEDIATE) = **0.167**, P(POOR) = **0.000**. The expected grade is E = 0·0.833 + 1·0.167 + 2·0.000 = 0.167, so the score is (1 − 0.167 / 2) × 100 = **91.7 / 100** — above 75.6, hence **PRESERVED**, matching the doctors.

That example is also the correctness check we run on every patient: the frozen pure-NumPy scorer reproduces the original `statsmodels` fit to a maximum probability error of **0.0**. The tables above are not a *description* of the model — they *are* the model.

### The central choice: one feature per clinical axis

The analysis above shows the eight features are roughly two-to-three real dimensions, with variance-inflation up to 40. So the score is **not** fit on all eight (accurate but unstable, uninterpretable, low coverage). It is fit on the **three clinical axes the features span — size, fat, tissue texture — one measurement each.** That makes the model's inputs **one per clinical axis — the same size / fat / tissue split** the page's breakdown reports: the headline and its breakdown are one coherent object, not two parallel computations.

The representative chosen per axis ([`muscle_condition_model.py:45`](scripts/muscle_condition_model.py#L45)):

| Axis | Feature | Why this one |
|---|---|---|
| Size | `full_thickness_q50_mm` (RF thickness median) **or** `star_ratio` (QMT ÷ BMI) | RF-thickness loads 0.93 on the size factor; STAR is the body-size-normalized alternative (see the option choice below) |
| Fat | `fat_to_depth_q50` (depth-normalized fat fraction) | better cohort coverage than fat *thickness*; the cleaner fat marker |
| Texture | `int_cv_within` (fascicular contrast) | the only tissue-texture feature in the set |

### What was tried — the bake-off

All accuracy is **10-fold out-of-fold** (no in-sample optimism), benchmarked against **human rater-vs-rater QWK 0.63–0.73** (companion study). Two passes: a fair head-to-head on the same complete-eight rows (so only the *number of features* varies), then each shippable model on its own (larger) coverage. Models tried ([`muscle_condition_model.py:116`](scripts/muscle_condition_model.py#L116)): a mid-grade baseline (κ = 0), texture-only, the 3-construct candidate, the 8-feature ordinal (accuracy-ceiling reference, not shippable), and an 8-feature L2-ridge ("just regularize the kitchen sink?").

The three deployable options, each on its own coverage ([`condition_options.json`](data/cohort_stats/condition_options.json)):

| Recipe | Inputs | Patients | Coverage | Agreement (QWK) | Exact hit | Two-grades-off | Overlap (VIF) |
|---|---|---|---|---|---|---|---|
| A | RF-thickness / Fat / Texture | 213 | 75.5% | 0.771 | 79.8% | 0.0% | 2.8 |
| B | all eight | 191 | 67.7% | 0.800 | 80.6% | 0.0% | 41.0 |
| **C** | **STAR / Fat / Texture** | 197 | 69.9% | **0.811** | 82.2% | 0.0% | 2.9 |

(QWK = quadratic weighted kappa. The appendix's multivariable tables ran on an earlier 184-study snapshot of the same complete-case set; B here is the current freeze at n = 191 — the gap is cohort growth between the two runs, and a reminder to refresh the appendix's *n*.)

**The punchline that makes the choice low-stakes:** on the 191 patients all three options can score, they land in the **same band 85% of the time** (A and C agree 87%). The answer barely depends on which option you pick — so ship the cleanest, most interpretable, most accurate one.

### Why C is the headline

- **C wins on cross-validated accuracy** (QWK 0.811) *and* keeps collinearity tiny (worst VIF 2.9). The all-eight model B reaches only 0.800 *and* carries VIF 41 — unstable, uninterpretable weights, worst coverage — so it is the accuracy *reference*, not a shippable model.
- **C over A** is the body-size question. C's size axis is **STAR = QMT ÷ BMI**, i.e. muscle *relative to body size* — how the doctors grade (the sarcopenia "relative muscle" view) — and it is the accuracy winner. The cost is reach: STAR needs the femur view (to measure QMT), so C scores 69.9% of scans vs A's 75.5% — the ~30% of scans C can't score get **no C headline today** (about 21% truly lack the femur view STAR needs, the rest are fat/texture gaps; an honest blank — *never* a wrong number; a fall-back to recipe A for those scans is proposed but **not yet built**).
- **Ratified 2026-06-11** (Akadir): headline = C; A and B stay visible in the opt-in comparison so the choice is transparent.
- **The three-input recipe is sturdy.** Re-run on 300 resampled datasets (a "bootstrap"), the texture and STAR weights keep their direction **100%** of the time and stay un-overlapped (VIF < 3) — whereas the all-eight model's size weights flip direction 9–45% of the time (you can't trust a weight whose sign won't sit still). The shipped headline weights are **texture −2.07, STAR −1.90, fat +1.72** — texture and body-adjusted size carry the score, fat is the lighter third.

### The real caveats

In rough order of how much each constrains what the score may claim:

1. **It reproduces an expert's *eye*, not an *outcome*.** The grade it learns and the features it learns from come from the *same image* (common-method bias), which inflates the reproduction relative to any external truth. It cannot claim to predict strength, function, sarcopenia, MRI, or biopsy — that needs the pilot and an outside criterion (the appendix's validation roadmap).
2. **Near the reliability ceiling, by design.** The target is "reproduce the doctors' grade," and the cross-validated reproduction already sits in the band set by how much the raters agree (≈ 0.83–0.93; the eight-feature model reaches CV R² ≈ 0.71 / correlation ≈ 0.84 at its low end, and the headline C reaches QWK 0.811 against rater-vs-rater 0.63–0.73). That is the target *met* — but it also means **more features will not help**; only a better criterion (the pilot) unlocks more.
3. **Complete-case / coverage bias.** The deployed weights are fit on the studies that *have* the model's inputs (197 for C). That subset skews **older and more POOR-grade** (features go missing on younger, healthier scans), so the learned weights inherit the bias and should be **re-fit on a missingness-robust feature set** as coverage grows.
4. **No test-retest data → change over time is unvalidated.** A score *difference* between two scans cannot yet be distinguished from scanner noise; the minimal-detectable-change pilot is a Round-3 item.
5. **Same yardstick as the rest of the page (the subtle one).** The score is calibrated on the **same cohort table** the page's existing percentile comparisons already use ([`cohort_placement.py:44`](scripts/preflight_helpers/cohort_placement.py#L44)) — so it is **consistent by construction** with the comparisons already on screen, not a separate, second calibration. The honest limit: that table's **fat / texture / STAR columns were assembled from analysis sources** (a frozen analysis run for texture, the rater log for STAR's QMT, a fat back-fill whose generator script is no longer in the tree) — **not** re-measured through the live, per-frame app path the way the *size* columns were (the landmark work). So the score — exactly like every percentile already on the page — compares a patient against a baseline that was not fully re-measured the way patients are actually scored. This is a property of the **whole placement system**, carried at the tolerance the page already ships; re-measuring fat / texture / STAR through the app path (as was done for size) would improve the page and the score together. One nuance specific to the score: a percentile only needs a measurement to *rank* correctly, so it shrugs off a uniform scale shift; the score's weighted sum is a little more sensitive to scale — so it is worth **measuring the gap once** (compare a handful of studies' live values to their cohort rows) to confirm it sits within tolerance. A confirmation, not a blocker.
6. **The accuracy numbers are measured on cohort features**, which (per 5) are not identical to the app's live per-frame measurements; **field accuracy is therefore unverified** until the pilot reconciles the two.
7. **STAR leans partly on body size.** Because STAR = thickness ÷ BMI, part of its pull is really an inverse-body-size effect (hold thickness fixed and it tracks 1/BMI almost perfectly) — so the "size" axis is partly body composition. This is **intended** (it's the relative-muscle view the doctors use), but it means a muscular-but-heavy person can read low, and that has to be said plainly *on the page* (a "muscle relative to body size" note). That note is now on the live assessment page — a "muscle relative to body size" caption under the headline (the standalone preview's separate size/fat/texture *bars* remain demo-only).
8. **The band boundaries are the cohort's own decision points**, not clinical thresholds — they move if the cohort changes. The freeze was made while the cohort CSV changed more than once during development, so the artifact should be **coupled to the cohort with a staleness check** before it is relied on (a recommended hardening, not yet wired).

### Superseded and set aside

Earlier scorers `condition_score.py` and `model_variant_comparison.py` are superseded by `condition_model.py` + `condition_options_compare.py`. A stricter **size-adjusted "tissue-quality" residual** (condition with the size effect statistically removed) was considered and deliberately **not** made the headline — it is a statistical residual, harder to explain at the bedside and noisier than the condition score; it is kept as an optional secondary readout, not the lead.

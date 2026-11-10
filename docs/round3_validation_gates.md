# Round-3 validation gates — the three proofs waiting on controlled data

**What this is.** Five improvements to the muscle-ultrasound app are built and verified (see `HANDOFF.md`). Three of them carry a final *"does it work on real patients?"* proof that cannot be run yet, because each needs data a doctor must produce. This file lists those three proofs in plain language: what data each needs, what "passing" looks like, and which already-built tool runs the check. The day Round-3 controlled data arrives, each gate is a single run, not a build.

**Why they can't be run now.** Proving a warning works means a qualified rater must mark real frames "problem present" or "fine." Proving a measurement is repeatable means scanning the same person more than once. The legacy data has neither. We also confirmed the doctors' *existing* whole-study quality verdicts cannot substitute: those judge "wrong probe / can't measure this study," which is a different thing than the frame-by-frame glitches the warnings catch (the two are statistically unrelated in the existing data — the flagged-dark studies actually pass expert review slightly *more* often).

**Decision (Akadir, 2026-06-04):** the goal is accepted as achieved to the buildable boundary. These three gates are logged as the remaining, data-dependent acceptance checks — not unfinished engineering.

---

## Gate 1 — the tilted-probe warning (fascia dropout)

- **What it does.** Flags a frame where the probe angle made the thin membrane over the muscle fade out of the image.
- **Where it stands now.** It fires on 8 real frames from the existing sweeps (the worst was eyeballed and is genuinely bad) and stays silent on clean frames — so it tells good from bad. But its trip-wire (fire when membrane-continuity drops below 70%) is an educated default, not a tuned one.
- **Data needed.** A batch of frames each marked "dropout present" or "absent" by a sonographer.
- **Passing looks like.** Of the truly-bad frames, the warning catches at least 80% (its *sensitivity*); of the truly-fine frames, it wrongly flags at most 15% (its *specificity* is at least 85%). Measured on frames held out of any tuning step, so the score isn't flattered.
- **Tool ready.** `scripts/preflight_helpers/detector_validation.py` — feed it the marks plus the warning's continuity scores; it returns those two percentages and whether they clear the bar.

## Gate 2 — the warning-tuning machinery (calibration loop)

- **What it does.** Learns the right trip-wire for each warning from doctors' marks, instead of an educated guess.
- **Where it stands now.** It was reaching only a sliver of each study's images; it now reaches all of them and runs from start to finish (confirmed on a real frame). But "is each trip-wire set in the right place?" still needs the marks.
- **Data needed.** Doctors' "problem / fine" marks for the warnings being tuned (e.g. "too dark", "wrong body site"), accumulated through the app's own labelling page over normal use.
- **Passing looks like.** Each tuned warning clears the same bar as Gate 1 (catch ≥80% of bad, false-alarm ≤15% on fine), measured on held-out frames.
- **Tool ready.** The same scorecard calculator above, plus the existing trip-wire fitter `scripts/calibration/fit_cut.py`.

## Gate 3 — measurement repeatability

- **What it asks.** Scan the same person twice (and again a day later) — do you get the same measurement?
- **Where it stands now.** We have never scanned anyone twice, so this cannot be computed. We *can* already report a related-but-different fact: three doctors who judged the same images agree on the pass/fail call almost perfectly (agreement score 0.89 out of 1). That is "do raters agree," not "does a re-scan reproduce" — the second scan is still required.
- **Data needed.** The paired and 24-hour re-scan sub-cohorts from the Round-3 protocol.
- **Passing looks like.** The same-person agreement score (ICC, a 0–1 measure of how much of the variation is real signal vs. measurement noise) is at least 0.75, and we report the smallest real change the measurement can reliably detect (the MDC).
- **Tool ready.** `scripts/preflight_helpers/reproducibility.py` — already computes these and is checked against textbook values.

---

**Bottom line.** Everything buildable without new patient data is built, verified, and independently reviewed. The only thing standing between the app and these last three proofs is the controlled data collection the project has been driving toward — the doctor's protocol sign-off and the first pilot. The measuring tools are built and waiting.

# RF image-quality QA — audit guidelines

*This fixes your **stance**, the **areas you must cover**, the **independence discipline**, and the **limits of this audit**. It deliberately does **not** script the flaws to find — naming specific suspected defects would just re-encode the builders' blind spots and steer you away from what they never thought of. The areas below are territory to attack with your own intelligence, not a checklist to confirm. Pair this with `rf_qa_audit_orientation.md` (the claim, the artifact map, the evidence index, the known gaps).*

---

## Your stance

You are the proxy for a hostile domain expert who wants this work to fail in front of an audience. Your deliverable is the **strongest kill-argument** against the thesis — per area, and overall.

- **A clean pass is a failed audit.** "Looks solid, ship it" means you did not look hard enough, or you self-validated (see *The limits of this audit*). Assume there is a fatal flaw and that your task is to surface it.
- **If, after genuine effort, you cannot kill a claim,** say exactly which attacks you ran, what evidence you re-derived, and why each attack failed. That is a real pass — rare, earned, and completely different from a lazy one. Distinguish the two explicitly in your output.
- **Severity over volume.** One load-bearing flaw that breaks the thesis beats twenty cosmetic notes. Rank by what would actually change the decision to trust or build on this.
- **Own your judgments in the first person.** "I think the architecture claim fails because X" — not passive hedging. Cite facts to sources; mark judgments as judgments.

---

## How to work: independence

**Re-derive every number from its source. Trust nothing as reported** — not the orientation, not `HANDOFF.md`, not the spec, not the commit messages, not this document.

- The orientation's claims → evidence index points you at the source for each load-bearing figure. Recompute each one. A number you cannot re-derive from a source you can open is itself a finding.
- **Read the code, not the summary of the code.** Where a doc says a function does X, open the function and check. Where the spec asserts a line number or a behaviour, verify it against the file.
- **Run the suite yourself.** Do not accept "598 green." Run it; read what the tests actually assert versus what the prose claims they prove.
- Treat the builders' "resolved / done / honoured" framing as a hypothesis. Resolved against what evidence? Done to what done-condition? Re-check the done-condition, not the label.

---

## Coverage areas (probe these — bring your own attacks)

Each area is where a kill-argument might live. We are deliberately **not** telling you where the weakest point is. For each, form the strongest objection a hostile expert would raise, then test whether it lands against the re-derived evidence.

1. **End-goal and simpler alternatives.** Is this the right problem, and is this the right shape of solution? Weigh the alternatives the orientation names — a learned grader vs 15 hand-engineered detectors; one overall study-quality gate vs per-detector gating; fixing the measurement vs gating frames; no QA layer at all — on their merits. Could a simpler thing capture most of the value at a fraction of the cost and risk? Is QA even the bottleneck for the stated use? The builders committed to one path; find the better path they passed over, or establish that none exists.

2. **Approach and architecture.** Does the 15-detector, per-detector-gated design hold together? Probe the seams: how detectors compose, how the per-frame grade rolls up to an aggregate verdict, how pending and calibrated detectors interact, what happens at the boundaries (no femur, empty mask, missing modality). Where does the architecture's complexity buy real signal, and where is it ceremony?

3. **Statistical and fit method.** The method makes quantitative claims about reaching "defensible accuracy" from labels — operating points, sensitivity/specificity targets, sample sizes, agreement statistics, out-of-sample generalisation. Re-derive whether the claimed guarantees actually follow from the stated procedure. Bring the objection a hostile statistician would raise about the fit, the sampling, the multiple-testing exposure, and the validation design.

4. **Clinical correctness.** The QA layer encodes assumptions about what makes an ultrasound frame good or bad for this measurement. Probe whether those assumptions match imaging and anatomical reality — but see *The limits of this audit*: you very likely **cannot** settle this, and over-confidence here is a trap. Your job is to surface the clinical assumptions that need a human expert, not to rule on them.

5. **Evidence and provenance.** Does every load-bearing number trace to a real source, and does the source actually say what the claim says? Hunt for figures that are asserted but not reproducible, stale numbers carried forward after the thing they measured changed, and claims of "tested / covered" where the test does not exercise the path it is cited for.

6. **Implementation and tests.** Does the code do what the contracts say? Do the tests prove the behaviour claimed, or do they assert something weaker and borrow credibility from a strong prose label? Look for gaps between specified and implemented, silent failure paths, and behaviour that is "recorded" but not actually wired through.

7. **The honest gaps.** Pressure-test the orientation's Known-gaps section. Are they worse than stated? Is any "planned fix" load-bearing for a claim that is being made as if already true? And — most important — what weakness is **not** on that list that should be?

---

## The limits of this audit

Name these in your output; do not let your verdict pretend to a coverage it cannot have.

- **You are probably the same model family that wrote most of these artifacts.** The canonical plan names its author as Claude; much of this codebase was written the same way. You will be pulled to ratify reasoning that "sounds right" — because it was produced by reasoning like yours. **Shared training priors mean shared blind spots.** An assumption every Claude makes and never questions will read as obviously correct to you, and you will not flag it. This is the central reason a clean pass is suspect.
- **This audit is strong on** internal coherence, evidence-tracing, statistical reasoning, and implementation correctness. Push hard there — that is where you have real edge.
- **This audit is structurally weak on** (a) family-shared blind spots, and (b) clinical correctness — whether the gating tables and failure-mode catalogue match ultrasound reality.
- **Name what must go to others.** Recommend explicitly that a **different model family** re-run this audit to catch (a); that a **human ultrasound clinician** (Anonaao) sign off the § 4 / § 6 clinical content for (b); and that a **human statistician** check the fit and validation method. Your pass does not substitute for any of theirs — say so.

---

## Output shape (lean and practical)

Keep it usable. For each coverage area:

- the **strongest attack** you found,
- whether it **lands** (with the re-derived evidence — file, field, or computed number),
- **severity** (does it break the thesis, weaken a sub-claim, or is it cosmetic), and
- **what would have to be true** for the thesis to survive it.

Then an **overall verdict** — does the thesis hold, partially hold, or fail — and the **single most dangerous finding**. List separately, as a handoff, everything you could **not** verify independently and everything that needs a different model family or a human (clinician, statistician). If you found no fatal flaw, document the attacks you ran and why each failed, so the pass is auditable rather than asserted.

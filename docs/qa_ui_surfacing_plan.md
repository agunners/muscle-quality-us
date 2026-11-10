# Item 10 — the QA UI surfacing plan

*The re-scoped execution plan for `build_step_2_plan.md` **item 10** — the § 8 UI surface of the RF image-quality QA layer. It supersedes item 10's one-row spec in `build_step_2_plan.md` and the pre-implementation § 8.3 sketch in the canonical plan: those were written before Build-Step 2 items 8 and 9 existed, and items 8–9 each added consequential hidden decisions that item 10 must now surface. This doc inventories every such decision, tiers each by consequence, and splits item 10 into a testable sequence.*

*This is a **scoping and framing** doc, not a finished UI spec. The pixel-level design happens inside item 10 — and, per § G, ideally against an operator prototype. Created 2026-05-22, alongside Build-Step 2 Phase 3 item 9. Governs item 10; where it and the canonical plan § 8 disagree on *scope*, this doc is the newer read — but the canonical plan still governs *contract* (schema, gating semantics).*

---

## § A — The principle this plan is built on

Akadir, 2026-05-22, restated as a design axis: **anything the QA layer filters, removes, prioritizes, or changes and does not surface will not be seen, audited, or used for calibration.** An un-surfaced detector generates no operator label; § 11.2 calibration pairs detector scores with operator labels; so an un-surfaced detector is uncalibratable — frozen "pending" forever. The hidden thing cannot generate the data that would un-hide it.

The trap: **"recorded in the run record" is not "surfaced."** Recording satisfies one property — *Auditable* (a teammate can reconstruct it later). Surfacing to the operator requires four more, and each decision below is a checklist against them:

- **Visible** — on a screen the operator passes through in the normal flow.
- **Legible** — understandable without domain expertise (calibrate to a smart non-expert).
- **Attributable** — shown *with its evidence*, not just its verdict.
- **Contestable** — a disagreement path of cost *symmetric* with agreeing, where *silence is not assent*.

Through item 9, the QA layer is an **A on Auditable and a zero on the other four** — everything is in `runs.jsonl`, nothing is on a screen. Item 10 is the entire Visible/Legible/Attributable/Contestable job, and it carries the debt of items 8–9. This plan is how item 10 discharges it without overwhelming the operator.

The resolution of the completeness-vs-overwhelm-vs-feedback trilemma is two moves: **layer by consequence** (§ C — overwhelm is a tiering failure, not a volume failure) and **design disagreement as the product** (§ E — the divergence between the operator's eye and the detector is the calibration data we cannot get any other way).

---

## § B — The surfacing inventory

Every filter / removal / prioritization / change the QA layer makes per run, as of item 9. All eleven are today **recorded only** — Auditable, but not Visible / Legible / Attributable / Contestable. Item 10 owes each row the four properties.

| # | The decision | Verb | Tier (§ C) | Item 10 must let the operator see… |
|---|---|---|---|---|
| 1 | `image_quality_aggregate_tier` — the per-frame badge (§ 6.3) | prioritize | 0 | the headline verdict, at a glance, on the picker |
| 2 | `dominant_reason` — one flag shown, the rest demoted (§ 5.1 priority) | prioritize | 0 | the one-line "why" — and that other reasons exist |
| 3 | `ungraded` — no `_final.png` / detector error (§ 8.6) | filter | 0 | that the frame was **not graded** — never read as "valid" |
| 4 | `image_quality_tier_per_measurement` — 8 per-measurement valid/borderline/invalid (§ 6.2) | change | 1 | which measurements are usable on this frame, and which are not |
| 5 | `_aggregate_with_image_quality` — a frame dropped from a measurement's pool (§ 7.3) | remove | 1 | that a frame left a measurement's aggregate, and why |
| 6 | `_aggregate_qmt` — QMT/STAR pooled within one provenance tier, never across | remove | 1 | which provenance tier the QMT number came from |
| 7 | QMT provenance floor — a silent `valid`→`borderline` demotion of qmt_mm/star_ratio | change | 1 | that the QMT verdict was capped by provenance, not by a flag |
| 8 | `frame_rf_consistency` gate (item 9) — a sweep-outlier dropped from `rf_thickness_mm`; the anchor re-pool | remove + change | 1 | that a sweep-outlier was flagged; **if the anchor was the outlier, that the headline number came from other frames** |
| 9 | `anchor_primary` vs pooled-median — which number is the headline | prioritize | 1 | which frame (or median) the headline measurement came from |
| 10 | the 4 **calibrated** detectors that fired — they drove the verdict | detect | 1 | the trustworthy detector calls behind the badge, with evidence |
| 11 | the 11 **pending-calibration** detectors that fired — recorded, excluded from the verdict (Finding B) | prioritize | 2 | that they ran, what they think — as a *reactable* surface (§ E) |

Two rows are the load-bearing risks. **Row 11** is the calibration bootstrap: the badge (#1) is computed from 4 of 15 detectors; if item 10 surfaces the badge well and the 11 pending detectors poorly, the calibrated set is frozen at 4 forever. **Row 8's anchor re-pool** and **row 7's floor** are *silent changes* — nothing visibly leaves, a number just quietly becomes a different number; these are the easiest to forget and the most disorienting if unseen.

---

## § C — The tier model: layer by consequence

Overwhelm comes from surfacing a decision at the wrong altitude, not from surfacing too much. Four tiers; the operator chooses depth.

- **Tier 0 — the headline.** Unsolicited screen space, one glance: the aggregate badge, the dominant reason, the `ungraded` state. On the picker (`p03`) and the result card (`p05`).
- **Tier 1 — "what changed."** One click — a drilldown panel listing every decision that *altered a number or removed a frame* (#4–#10), each a one-line *"X was done, because Y, evidence Z."* This is where Attributable is won.
- **Tier 2 — "noticed, didn't bite."** The 11 pending detectors that fired (#11). They did not change a number — by design (Finding B) — but they must be Visible and Contestable or they never calibrate. A quiet, scannable, *optionally-reactable* surface — see § E.
- **Tier 3 — the raw record.** Full `ImageQualityFeatures` + `detector_metadata` + `aggregate["_rf_consistency"]`. For audit, not the workflow.

Every decision in § B is surfaced; the tier matches its consequence. A pending detector shown as a Tier-0 alarm would overwhelm; shown nowhere it never calibrates; shown as Tier-2 it is right.

---

## § D — The 10a / 10b split

Item 10 as one mega-item ending in one operator click-through is too large and feeds back too late. Split it:

**10a — the read surfaces.** Tiers 0, 1, 3 — the picker badge, the result-card badge + cohort caveat, the "what changed" drilldown, the raw-record view, the segmentation-confidence heatmap. Wins Visible / Legible / Attributable. *Done-when:* a Layer-3 operator click-through shows, for every graded frame, the verdict + dominant reason + a drilldown that names every § B row #4–#10 with its evidence; `ungraded` never renders as `valid`; the heatmap renders.

**10b — the capture surfaces.** Tier 2 + the Phase-4 pre-fill via the `prefill_flags` seam + the `override_action` UI (§ 8.3.6) + Channel D explicit-confirm + the pending-detector calibration queue (§ E). Wins Contestable. *Done-when:* an operator can react to a verdict and to a pending detector; the reaction produces a § 11.2-usable label; the § 7.0 pre-fill circularity rule holds (a silently-accepted pre-fill is excluded from calibration; an explicit confirm or an override is a usable label).

10a is where overwhelm risk lives and it is testable early; 10b should be designed from what 10a's prototype teaches. Build 10a, prototype it (§ G), then 10b.

---

## § E — The pending-detector surface: design disagreement as the product

The instinct is to build the capture UI to *confirm* the verdict. That is backwards. We already have the verdict; what we lack is where the operator's eye **diverges** from the detector. An operator who says "no — the muscle really does taper there, that frame is fine" has produced a label we could not generate any other way. So 10b must make disagreement *cheap and structured*, and a silent non-action must never count as agreement (§ 12.2 Channel D).

The pending detectors are noisy by construction — `wrong_site` fires on 79.8 % of cohort frames. Surfaced inline per-frame, they train the operator to ignore the QA panel by day two, poisoning the channel. **Design fork (for Akadir — § H):** pending detectors appear (a) inline in the per-frame drilldown, (b) only in a separate **calibration queue** — a deliberate labelling surface the operator visits on purpose, decoupled from the assessment flow, or (c) both. Recommendation: **(b), trending (c)** — a separate queue protects the assessment flow from noise *and* yields cleaner labels; it is also `qa_data_collection_plan.md`'s channel-1 / decision I3.

The narrative throughout: the QA layer is **a junior colleague showing its working** — "here's what I noticed, here are the two things I moved, tell me where I'm wrong" — not an oracle issuing grades. A colleague who narrates every observation is exhausting; one who leads with the headline and offers the working is good. The narrative tone is the anti-overwhelm mechanism, and it is audience-layered: the operator narrative is the *collaboration* story (and the only one that generates calibration data); a boss/clinician narrative is a separate, thinner compression.

---

## § F — The interaction budget

"Don't overwhelm" is a platitude until it is a measured constraint. Item 10 carries an explicit budget, set provisionally now and measured during the cohort assessment pass:

- A clean (badge = valid) frame costs the operator **≤ X s** of QA attention.
- A flagged frame costs **≤ Y s**.
- The QA layer yields **≥ 1 calibration-usable label per N frames**.

X / Y / N are unset — no operator has used the surface yet. Item 10 sets provisional values and the cohort assessment pass measures the real ones. Without a budget, item 10's drilldown will sprawl; with one, "don't overwhelm" becomes an acceptance test.

---

## § G — Get an operator in front of a prototype early

Every claim in this doc about "the operator will understand X" is untested theory — no operator has seen any QA surface. The cost of being wrong about what overwhelms is a rebuilt item 10. So: **before 10a is called done, put a rough prototype — even a Streamlit-crude drilldown on five real frames — in front of one operator.** This is the single highest-value de-risk, and it is the canonical plan's own Comment-13 philosophy ("keep our pace informed by the ongoing implementation"). The cohort assessment pass is the larger validation; the early prototype is the cheap course-correction.

---

## § H — Open decisions for Akadir

1. **The deployed-vs-raw tier schema — RESOLVED 2026-05-22** (was a HANDOFF hard gate before item 10). `image_quality_aggregate_tier` is the *calibrated-flags-only* (deployed) verdict — a frame can read `valid` while a visible un-calibrated `wrong_site` sits in `image_quality_validity_flags`. Akadir ratified the **additive field**: § 7.1's `ImageQualityFeatures` gains `image_quality_pending_calibration_flags` (the un-calibrated subset of the fired flags) — chosen over a rename of `aggregate_tier` (breaks every existing ledger record) and over a companion raw-aggregate-tier (a second verdict to reconcile, naming no detector), because it is back-compatible and hands 10b's Tier-2 surface the data directly. Shipped in item 10a; canonical plan § 7.1 + § 16 synced.
2. **The pending-detector surface** — fork (a)/(b)/(c) of § E. Recommendation (b)→(c).
3. **The interaction budget** — X / Y / N of § F: provisional values to set, then measure.
4. **The consistency gate's advisory outliers** — when `frame_rf_consistency` flags a *non-anchor* frame the headline does not change (item 9, by design); does that advisory get any Tier-0 visibility, or is it Tier-1-only? Recommendation: Tier-1 only — it changed no number.
5. **§ 11.5 compound false-positive discipline** — a hard gate before Build-Step 2b, not item 10, but item 10's badge logic should leave room for it.

---

## § I — Read order for the item-10 session

1. This doc.
2. `HANDOFF.md` — current state; the "Hard gates before item 10".
3. Canonical plan § 8 / § 8.3 / § 8.6 (the UI design + the "pending calibration" honest state), § 7.1 (the record schema — for decision H1).
4. `docs/qa_data_collection_plan.md` — item 10's UI is a data-collection instrument; the calibration queue (§ E) is its channel 1.
5. The data item 10 renders: `ImageQualityFeatures` (per frame), `aggregate["_qa_tier"]` / `["_rf_consistency"]` / `["_policies"]` / `["qmt_provenance"]` (per run) — the § B inventory's source fields.
6. `labeling_app/assessment_pages/p03_frame_picker.py`, `p04_frame_review.py` (the `prefill_flags` seam), `p05_assessment_view.py` — the three UI files item 10 touches.

---

*Item 10 is the build's highest-risk item: it carries all of the QA layer's surfacing debt and it is where the calibration loop is either bootstrapped or frozen. This plan exists so it starts re-scoped — from the surfacing inventory — rather than from a one-line table cell.*

# RF Frame QA — Assessor's Guide (v0)

*The rubric for a cohort assessment pass with the muscle_mania assessment app. Drafted alongside Build-Step 1 of the RF Image-Quality QA/QC plan (`docs/rf_qa_canonical_plan_v0_1.md`, § 14 Step 1). This is the **v0** guide — pending Anonaao's clinical review (future-work FW-2). It will later migrate into the in-app `explain` affordance as operator feedback accumulates (FW-23).*

---

## 1. What you are doing, and why it matters

The app assesses thigh-muscle health from rectus-femoris (RF) ultrasound. A **cohort assessment pass** means working each study through the five-phase flow, picking frames, answering the three gates on each, and — for every frame you drop — recording *why* with the structured reason-code checkboxes.

That "why" is the point. The QA layer's detectors (built in a later step) are calibrated against the reasons you record here: your structured drop-reasons are the ground truth. A frame dropped with no reason recorded is lost signal. Be deliberate and consistent.

**Note for this v0 pass:** the reason-code checkboxes are **not pre-filled** — there are no detectors yet. Every box you check is your own judgement. That is intended: an un-prompted human label is the cleanest calibration input.

## 2. The per-study workflow

1. **Phase 1–2:** open the study, load the segmentation model.
2. **Phase 3 — picker:** pick the frames you would actually use for an assessment of this study (up to 9).
3. **Also review 5 extra unpicked frames** — one from each fifth of the sweep (early, early-mid, mid, late-mid, late). This makes the labelled set span the *whole* sweep, not only its good middle. About 30 seconds extra per study. *(Decision D7. For this v0 pass, choose these five by hand from the sweep.)*
4. **Phase 3 — review:** for every frame — picked **and** the five extras — answer the three gates.
5. For every frame you mark **Usable = No**, check every reason code that applies (Section 4), and use **Other** for anything not in the list.
6. **Phase 4–5:** lock the gates, then **Accept run** so the record reaches the ledger. Do not leave any frame on the pending "—" state at Accept.

## 3. The three gates — decision rules

| Gate | The question | Answer **Yes** when… | Answer **No** when… |
|---|---|---|---|
| **Usable?** | Is this a real, gradeable RF frame, fit to take into an assessment? | The frame shows RF, the structures an assessment needs are visible, and there is no gross failure — a frame you would be willing to measure. | A gross problem makes it unfit — wrong muscle, missing femur, severe blur, severe clipping, off-axis plane. **Answering No opens the reason-code checkboxes — fill them in.** |
| **Segmentation perfect?** | Does the model's mask match the anatomy with no visible error? | The mask edges sit exactly on the fascia/muscle boundaries — this frame is a clean segmenter training candidate. | Any visible mask error. (Shown only when Usable = Yes.) |
| **Use for analysis?** | Should this frame's measurements feed the pooled assessment result? | You want this frame's numbers in the study's pooled result. | You reviewed the frame but do not want it pooled — e.g. one of the five sweep-extras, or you already have enough stronger frames. (Shown only when Usable = Yes.) |

A frame can be **Usable = Yes** but **Use for analysis = No** — usable, just not pooled. The reason-code capture is about *image quality* and fires on **Usable = No**; mask-quality is Gate 2.

**The app enforces completeness.** It will not lock the run until every picked frame is decided Yes or No on every gate it shows you — nothing left on the "—" state — and every frame you marked **Usable = No** has at least one drop reason recorded. If a frame's real reason is not in the list, type it into **Other**: that always satisfies the requirement, and it grows the taxonomy.

## 4. The reason codes — when to check each

Check **every** code you can see; a frame often has more than one. The codes are grouped as in the plan's § 5.1 priority order.

**Orthogonal — the whole frame is wrong**
- `wrong_site` — the frame is not rectus femoris (it is vastus lateralis / medialis / intermedius, or off-thigh entirely).
- `segmenter_mislabel_wrong_class` — the mask looks self-consistent but the model labelled a non-RF muscle as RF. Rare and usually invisible; check only with outside knowledge.

**Femur**
- `femur_out_of_frame` — no femur (thigh-bone) cortex line with a clean dark shadow beneath it, anywhere below the muscle.
- `femur_off_central_zone` — the femur is visible but off to one side, not under the central measurement zone.

**Framing**
- `edge_clip_lateral` — the RF muscle runs off the left or right edge of the frame.
- `edge_clip_vertical` — a measurement structure (superficial or deep fascia, or the femur) is cut off at the top or bottom edge.

**Intensity**
- `saturation_high_global` — pixels clipped bright (flat white) across the whole region; gain set too high.
- `saturation_high_regional` — a bright flat-white clipped patch in one area, often in the fascia band.
- `dark_clip_high` — pixels clipped to flat black; gain too low, or the beam attenuated away.
- `tgc_banding` — horizontal bright/dark stripes that do not follow the anatomy (mis-set time-gain compensation).

**Sharpness**
- `motion_blur` — fascia lines and fascicles smeared and soft, not crisp; probe or patient moved during capture.

**Coupling / near-field / artifact**
- `air_gap_reverberation` — a repeating comb of bright bands at the probe-contact zone; an air bubble.
- `near_field_clutter` — non-anatomical noise in the top few millimetres, distorting the skin/fat/fascia layers.
- `vessel_shadow_in_roi` — a dark vertical column cast downward by a vessel or calcification inside the muscle.
- `posterior_acoustic_enhancement` — a locally over-bright vertical strip *beneath* an echo-poor structure (e.g. a vessel).

**Anatomical**
- `subq_fat_compressed` — the subcutaneous fat layer is flattened thin by probe pressure.

**Off-axis / beam-angle**
- `oblique_plane` — the probe is rotated off the muscle's long axis; fascia lines converge or fade across the width.
- `anisotropy_dropout_superficial` — one stretch of the *superficial* fascia darkens or disappears (a beam-angle effect, not a real gap).
- `anisotropy_dropout_deep` — the same, on the *deep* fascia.
- `deep_attenuation_high` — the deep fascia and femur are faint and low-contrast; the beam ran out of energy before reaching them.

**Model-derived**
- `segmentation_low_confidence` — the model itself is unsure of the mask; usually shows as a wobbly or implausible mask edge.

**Orientation**
- `probe_orientation_flipped` — the right muscle, but mirrored. No single-frame signature; check only with outside knowledge.

**`Other`** — any image-quality problem not in the list above. Type it in free text. Recurring "Other" entries are how the taxonomy grows.

## 5. Worked examples

**A — a clean frame.** RF clearly imaged, femur cortex and shadow visible centrally, fascia crisp, no clipping. → Usable **Yes**, Segmentation perfect **Yes** (mask is exact), Use for analysis **Yes**. No reason codes.

**B — a blurred frame.** The operator moved the probe; fascia lines are soft and the fascicle texture is smeared. The measurements cannot be trusted. → Usable **No**. Check `motion_blur`. If the plane also looks rotated (fascia converging across the width), check `oblique_plane` too — the two are commonly comorbid.

**C — wrong muscle.** The belly filling the frame is vastus lateralis, not RF — the operator was off-target. → Usable **No**. Check `wrong_site`. Every measurement from this frame would be silently wrong, so this is a hard drop.

**D — laterally clipped, but kept.** The RF belly runs off the right edge, so a whole-cross-section *area* cannot be measured — but the femur is central, the fascia is crisp, and QMT and thickness are perfectly measurable. → Usable **Yes**, Use for analysis **Yes**. No drop, no reason codes: a clipped edge is a coverage limitation, not a frame failure. (If you were dropping it for another reason, you could still note `edge_clip_lateral` — but on its own it does not force a drop.)

---

*End of RF Frame QA Assessor's Guide v0.*

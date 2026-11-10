# The refine surface — withhold→release AND valid-study refinement, one mechanism

*Proposed by Akadir in the 2026-08-14 review doc (`1REDACTEDDRIVEID0000000000000000000000000000`,
item 4.b.II), generalized by his same-day follow-up ("Notice this is an extended version of the
un-withhold. Maybe a single unified interface … withheld studies (adding new frames/numbers
unblocks) and existing valid studies (adding new frames/numbers refines the number)"). Status:
**DESIGN AGREED — both open decisions resolved by Akadir 2026-08-14 (§4); build is next.***

## 1 · The proposal, in Akadir's words

> "for these withhold studies, we can also add a checkmark if to un-withold from text and/or
> size (so two checkboxes maybe). And if the user checks them to un-withold, and then 'accepts
> the run' … ⇒ we keep the flag, but now add this to the analysis, as cleared by the accepted
> run. So maybe another column with the sheet we keep about flags: whether there was an accepted
> run and the operator did raise the 'withhold' decision → for those runs we have the numbers
> and these numbers can be used → in the next freeze/unfreeze, for placement … an accepted run
> where a future unwithold checkboxes are checked only permits the measurements (size and/or
> texture) from this very frame combination directly calculated right there in the app."

## 2 · Assessment: yes — and for the refined studies it is the ONLY correct instrument

Two release situations exist, and they need different values:

- **Plain release** (the study's standard texture is fine — 12 of the 17 reviewed): the value the
  cohort would serve anyway (the canonical top-5-frame median) is the value Akadir validated. A
  ledger CLEAR suffices; **these 12 are already recorded** (2026-08-14) and the freeze replay
  proves they apply (texture 107 → 118 at the next freeze, none stale).
- **Refined release** (AKA072, AKA036, AKA080, AKA160): Akadir vetted a *bounded* RF region on
  the review page; the number he validated is the run's refined `int_cv` (verified: the pipeline
  overrides the aggregate from the vetted region — `feature_routing.py:148`). Releasing the AUTO
  value here would admit the very contamination he excluded. **The release must carry the
  accepted run's number** — exactly his design. **DONE 2026-08-14, no new runs needed:** all
  four review runs were already finalized on disk with the refinement recorded, so their
  releases are in the ledger as run-bound clears (`released_value` + `source_run_id`), and the
  freeze serves the released value (built + tested same day). Rehearsal: r5 = 124 studies,
  texture 123, 17 clears, 0 stale, withheld = 1 (AKA017). The checkbox UI (§3.1–3.2) remains
  the go-forward convenience so future releases happen at Accept-run without an engineer.

## 3 · The wiring (build plan)

1. **p05, withheld state:** under the trust strip, two checkboxes — *"Release texture from this
   run"* and *"Confirm size from the femur view"* — enabled only when the page shows an
   assessable run. Checked boxes arm **Accept run**.
2. **Accept run with armed boxes** appends feature-grain **CLEAR** entries to the flag→kill
   ledger, extended with two fields: `released_value` (the run's number for that feature) and
   `source_run_id`. `values` snapshot = the run's per-view max thicknesses (the existing
   stale-detection contract). The flag history stays — nothing is deleted.
3. **The freeze** honors a run-bound clear by serving `released_value` for that feature (not the
   auto landmark median), provenance-marked `released_by_run`. Plain clears (no
   `released_value`) keep serving the auto value, as today.
4. **The census/flags surface** gains the column Akadir asked for: cleared-by-accepted-run, with
   the run id — visible in `--status` and the qa sheet.
5. **The trust strip** already shows "cleared by Akadir — applies at the next freeze"; it gains
   the run id when the clear is run-bound.

## 4 · The two decisions — RESOLVED (Akadir, 2026-08-14)

- **A — size.** *"Ok, we never switch to RF for measurements. Femur is good."* The size
  checkbox = **"reviewed — femur numbers confirmed"**: consent recorded, caveat clears, no value
  ever switches source.
- **B — the auto-spine.** *"For the auto-spine → it should continue."* The automatic landmark
  pipeline remains the cohort's spine. Operator contributions never replace it wholesale — they
  JOIN it, per §4b.

## 4b · The unified extension — operator-vetted frames join the evidence pool

Akadir's generalization: the auto-spine is a black box (segmentation + follow-on measurements
nobody sees per-study), and the surface should let him **see** its numbers next to the app's
selected-frame numbers, **compare**, and — when he trusts them — **contribute** ("add to the
analysis as the 5+xth frame … this will refine any number that might have a broken background
calculation").

**One rule covers both cases: a feature's served value = the median over its admissible
evidence pool.**

| Study state | Auto contribution | Operator contribution (accepted run) | Effect |
|---|---|---|---|
| valid | admissible (the canonical top-5) | vetted frames join the pool | the number **refines** |
| withheld feature | inadmissible (that's what the withhold means) | vetted frames are the pool | the number **unblocks** |

Trust-gating: contributions come only from **accepted** runs; each contribution is a ledger
entry (run id, frames, values snapshot); the freeze provenance lists every study's contributions
— nothing enters silently, nothing is deleted.

**BUILT 2026-08-14 EVE ("contribute-frames path, ready,, or?").** The mechanics, as shipped:
a fourth ledger grain `contribution` (keyed per run — several runs coexist; `revoked` withdraws
one) · the evidence expander's third checkbox ("Contribute this run's frames to the canonical
pool — the 5+xth frame"), armed by Accept run like the others · the freeze pools per role:
femur frames refine the geometry/fat/QMT medians, RF frames the texture median; the automatic
side enters as the REAL top-5 per-frame values (the landmark JSON now stores `top_frames`;
pre-existing JSONs fall back to the auto median carrying its K=5 weight — the anchor holds
against a single outlier frame by construction) · records that predate per-frame persistence
contribute their aggregate value once, flagged in provenance. **Precedence rules, pinned by
tests:** the 3-way trust gate reads AUTOMATIC values only (a contribution can never flip a
trust decision or stale a recorded clear — `full_max_thickness_mm` is not poolable), and an
explicit run-bound release outranks int_cv pooling (verdict > evidence addition). Provenance
names every contributing run and frame count per study.

**Where the surface lives — decision (mine, per his "you pick, just explain why"):** it builds
ON the trust strip. The strip line stays the calm summary; a **"Measurement evidence" expander
directly beneath it** opens the numbers table — per feature: canonical value (+ frame count) ·
this run's value (+ frames, refined tag) · Δ · the action (release / confirm / contribute).
Three reasons: the strip is already the page's per-number trust surface, and canonical-vs-run
comparison IS trust information — a second home would mean two vocabularies for one question;
p05's standing design rule is a calm summary with opt-in density (Akadir's own earlier rulings),
which an expander honors while giving him the density he explicitly wants; and the actions
belong physically next to the numbers they act on and next to **Accept run**, which arms them.
His band-visual ask (show the refined RF band on the assessment page's centre-RF view,
transparent outside it) ships in the same build — same page, one coherent change.

## 5 · Adjacent gap, same surface — FIXED (2026-08-14)

The run-history **study bootstrap pooled 0 frames for every v8 study** (it read the legacy
`frame_features` list; routed runs store `routed_picks`, which carry frame bases + gates but no
per-frame features). Fixed: with routed runs present, the bootstrap is the per-feature **median
over the runs' routed aggregates** — a median of complete, correctly-routed measurements —
labeled as its own mode in the panel. Pinned by `tests/preflight/test_study_bootstrap_v8.py`,
including the real AKA016 case (3 runs, median int_cv ≈ 0.52 — the exact review screenshot).
Frame-level pooling for v8 arrives with §4b's per-frame persistence.

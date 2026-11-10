# The Strength card — serving spec

*Pinned 2026-08-12, before the build. Decisions here are the contract; the panel renders what this
file says and nothing it does not. Deviations get a dated amendment paragraph, never a silent edit.
Direction source: Akadir 2026-08-11 — "force into the app, not into the equation"; prioritised over
the embeddings rung by Akadir 2026-08-12 ("I want this 'Strength card' in the app. I prioritize it").*

## What the card is

One section on the assessment page (new-round studies only) answering three questions a reader
actually has:

1. **What did this person measure?** Isometric knee-extension peak torque, from the dynamometer
   session — with its own session repeatability shown, never as a bare point.
2. **What would we expect from this body and this muscle?** A prediction **range** from the frozen
   force model (demographics + geometry ridge) — intervals, never points (Akadir's display rule,
   2026-08-11).
3. **Do they agree?** A concordance line: measured strength below / within / above the range the
   profile usually produces.

It is a *context surface for the force axis*, not a new score and not an input to the condition
score ("force into the app, not into the equation"). It puts the project's two candidate products —
the condition score and the force proxy — side by side on the experimenting surface, which is the
evidence the DIRECTION-level "reconcile or pick one" question needs.

## The served model — pinned

| decision | value | receipt |
|---|---|---|
| model | ridge (StandardScaler → Ridge), α tuned on the fit set over `logspace(−3,3,25)` | prereg `docs/force_ladder_prereg.md` |
| features | B1 `age, sex(M=1,F=0), height_cm, weight_kg` + B2 `rf_thickness_mm, full_thickness_q50_mm, visible_rf_area_mm2, qmt_mm` — the prereg's exact rung-2 columns | prereg §Blocks |
| criterion | `izo_torque_nm` (isometric peak torque, Nm) | prereg §Substrate |
| fit rows | **the prereg's SENS_A row set, as the harness defines it**: new-round `qa_master.csv` rows complete on every block column *except* `int_cv_within` and on the criterion — so B3 fat completeness IS required even though the model consumes no fat (Amendment 1c: coincident with B1+B2-complete at today's n=103; binds if a fat-incomplete forced row ever appears). n=103 (57 M / 46 F) at freeze | harness `row_sets()`, reused verbatim |
| honest error | OOF R² **.6999 ± .0177**, RMSE **39.78 Nm**, MAE 31.6 — the SENS_A.R2.ridge receipt, 25-seed nested CV | `scripts/analysis/results/force_ladder_summary.json` |
| band construction | pooled out-of-fold residuals from the same 25-seed nested CV, **sex-stratified quantiles**; the served band is the central 80% (q10–q90) around the ridge prediction. 95% quantiles stored for the (i) detail. Display floor at 0 Nm | builder writes per-sex coverage into the artifact |
| envelope refusal | any B1/B2 feature outside the fit set's [min, max] ⇒ the band is **refused**, naming the feature and the bound (metrology: the model does not speak outside where it was fit). Fit envelope at freeze: age 18–65, height 150–190, weight 45–105, RF 13.2–35.6 mm, … (stored in the artifact) | artifact `envelope` |
| artifact | `data/cohort_stats/force_model.json` — scaler mean/scale, coefficients, intercept, α, residual quantiles, envelope, fit uids, sample predictions, provenance (git head, substrate sha256, date). Serve-time predict is a pure-numpy dot product — no sklearn in the app | builder self-test: stored params reproduce sklearn `predict` to 1e-9 |

**Relationship to the pre-registered baseline, stated once so nobody confuses the numbers:** the
experiment ladder's headline stays **PRIMARY R2 ridge OOF R² .690, n=88** — that is what the
embeddings rung is compared against, untouched. The *served* model is a product component, not the
experiment: same rung, same harness discipline, fit on the larger no-texture-requirement set
(SENS_A, n=103) whose own receipt is R² .6999 / RMSE 39.78. The card quotes the SENS_A numbers
because they are the numbers of the model actually serving.

**The card never invents data:** it reads `qa_master.csv` (the ledger-gated analysis substrate) at
serve time, joined by `study_uid`. A row the measurement gate holds serves **no** force cells there
by construction (Akadir's no-force rule, 2026-08-12) — so the card physically cannot show a held
value. Columns read are whitelisted; `study_name`/`rel_path`/operator identities are never loaded
(PHI discipline).

## Measured-side display tiers — the CV gate's first real consumer

Ratified policy (2026-08-11): *"Use CV≤10% only where a per-subject criterion value is displayed;
flag 10–15, drop nothing."* The card is exactly such a display. Tiers, with today's census (125
new-round rows):

| tier | rule | shown as | today |
|---|---|---|---|
| quotable | `izo_cv_pct ≤ 10` | value ± its own session SD (cv × value) | 95 |
| marginal | `10 < cv ≤ 15` | value ± session SD, amber flag "above the ≤10% repeatability target" | 6 |
| unstable | `cv > 15` | **value not quoted** — "session repeats varied ±N% — beyond the display limit"; concordance refused | 2 (CV 15.1, 19.2) |
| held | `measurement_flags` non-empty | "a recorded measurement is **held by the measurement gate** pending human verdict" — no number, band still shown, concordance refused | 3 (sheet rows 24, 87, 92) |
| no session | criterion empty, no flag | "no dynamometer session recorded" — band only | 19 |

The `unstable` tier is my completion of the ratified sentence (it named ≤10 and 10–15 and said
nothing about >15; displaying a >15 value as a quotable number would defeat the gate's point).
**Akadir veto point #1** — two studies affected; flipping them to "marginal with a red flag, value
shown" is a two-line change.

"Drop nothing" is untouched: all tiers stay in every analysis; these rules govern *display only*.

## Concordance line

Only when a quotable/marginal measured value AND a band both exist:

- **within** [lo, hi] → "measured strength is consistent with what this profile usually produces."
- **below** lo → "measured strength sits below the 8-in-10 range for this profile (by N Nm).
  Effort, pain, a neuromuscular limit — or an entry error — can all do this; it is a flag for a
  look, not a verdict."
- **above** hi → "above the 8-in-10 range for this profile (by N Nm)."

Always accompanied by the two honesty numbers: the band is an **8-in-10** range (2 in 10
measurements like these fall outside it by construction), and **within-session** repeat wobble on
the dynamometer is **±10.5 Nm** (mean within-subject same-session SD, n=88 receipt
`target_stats_primary`; between-day repeatability is NORTHSTAR R3 and does not exist yet — the copy
must not imply it). No hysteresis in v1: the verdict is position vs band, the wobble is printed
context.

## Card states (exhaustive)

| state | condition | render |
|---|---|---|
| full | measured (quotable/marginal) + band | figure (band + marker) + measured line + expected line + concordance |
| expected-only | no session, or unstable CV, or held | figure (band only) + expected line + the honest reason for the missing measured side |
| measured-only | band refused (envelope / missing feature) | measured line + refusal reason for the band |
| refused | neither side computable | the two reasons, plainly |
| not-in-substrate | `study_is_new_round()` true, uid absent from `qa_master` new-round rows | one caption: "not in the analysis substrate yet — run ingest" |
| absent | `study_is_new_round()` false (legacy/old-round) | no card at all (v1 scope; old round has no dynamometer data and a cross-protocol expected-band would reopen the old→new comparability question for no reader benefit) |

**Akadir veto point #2 — scope:** new-round only in v1.

## Placement & idiom

Full-width section directly under the frame/score two-column block on the assessment page
(`p05_assessment_view.py`), before "Secondary features": the third headline element after the
measurement cards and the condition score. Figure = one horizontal Nm strip in the page's plotly
idiom (shaded 80% band, fainter 95% band, measured diamond), `band_strip_figure` style. All logic in
`scripts/preflight_helpers/force_card.py` (pure, headless, unit-tested); all rendering in
`labeling_app/assessment_pages/force_panel.py` (`st` passed in, importable headlessly);
`p05` calls one function. A `direction_notes` entry ships with it (PROVISIONAL).

The (i) popover carries the model card: features, n=103 (57 M / 46 F), OOF R² .6999 ± .0177 /
RMSE 39.8 Nm (25-seed nested CV), torque range 65–407 Nm in the fit set, what the band means, and
the claim boundary — *ranking-strength good (R² ≈ .70), individual prediction honest at ±40 Nm;
demographics carry most of the prediction (block-Shapley .39 vs geometry .16 on the primary set),
so the copy says "this body and this muscle", never "your muscle alone"; not validated
longitudinally; the criterion itself is effort-dependent.*

## What the card is NOT (claim boundary)

- Not a score input; the condition score is untouched.
- Not a diagnosis; the below-range copy explicitly lists mundane causes.
- Not a longitudinal claim; nothing here tracks change.
- Not an isokinetic surface: `isok_*` is a second criterion, prereg-excluded, pending its own
  registration if ever.
- Not served for old-round studies (v1).

## Verification contract (PROVE-IT)

Preflight: every state above exercised on the real substrate + synthetic rows; the three ledger-held
uids must yield no number through the real path; payload dicts must never carry a PHI key; stored
model reproduces sklearn on the stored samples. Browser (render + LOOK, studies from the ✅ verdict
set): one quotable, one marginal (CV 10–15), one unstable (CV>15), one held, one no-session, one
legacy-absent — six screenshots, and one card's numbers re-derived by hand from `qa_master` +
`force_model.json` and matched against the pixels.

## Amendment 1 — 2026-08-12, same day, after the red-team review (nothing silent; every body
## correction above is declared here)

The build ran the repo's adversarial review (independent 25-seed recompute reproduced every
number; the no-force rule held on every path it attacked). Its findings, folded in:

- **(1a · was BLOCKER)** The legacy-absence gate first shipped on `study_modalities()` truthiness —
  which reports a `sweep` role for *every* study with raw frames, so legacy studies (AGZ855
  included) rendered the card with wrong "run ingest" advice. Caught independently by the
  browser render-and-look AND the review. Now `force_card.should_render` on
  `study_is_new_round()` — the docstring-owned legacy/new predicate — regression-tested and
  re-proven in the live browser.
- **(1b · wobble label)** "Day-to-day wobble ±10.5 Nm" mislabeled a **within-session** repeat SD
  (the receipt's own definition); between-day repeatability is R3 and unmeasured. Copy and the
  concordance section above corrected; the caption now says so explicitly.
- **(1c · fit-set definition)** The body originally said "complete on B1+B2+criterion"; the
  harness's SENS_A additionally requires B3 fat completeness. Coincident at today's 103; the
  table above now states the harness definition, which is the binding one.
- **(1d · builder substrate pin)** The freeze now aborts unless `qa_master.csv`'s sha256 and the
  row-set size equal the committed receipts' — the metric re-check alone could pass on an edited
  substrate (R² insensitive to a single-cell change at 4dp).
- **(1e · structural withhold)** The unstable tier's payload now carries **no value at all**
  (`value`/`session_sd_nm` = None), so no consumer can leak what it does not hold.
- **(1f · honesty additions to the (i) popover)** in-sample coverage runs 86% for fit-set studies
  (8-in-10 is the out-of-fold expectation for a new study); band edges carry ~±10 Nm sampling
  uncertainty at n=103; "healthy measurements" → "measurements like these" (no receipt licenses
  "healthy"); imaging's contribution labeled per set (+4.0 PRIMARY / +3.4 this fit set).
- **(1g · robustness)** A corrupt/unreadable artifact or schema drift now degrades to the visible
  "missing or unreadable" caption instead of vanishing inside the page's fail-silent wrapper;
  non-finite criterion values parse as absent.

## Later (recorded, not built)

- Embeddings rung (queued analysis item — Akadir's protected dimension): if it beats .690 and earns
  serving, the artifact gets a versioned successor; the card design does not change.
- Peers-band variant (k-nearest structures' measured range) — the empirical sibling of the model
  band; revisit when n grows.
- Isokinetic surface; old-round expected-band; concordance hysteresis against session wobble;
  refusal on thin neighborhoods (the ridge band refuses on envelope instead).

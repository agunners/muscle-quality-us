# v8 Implementation — Assumptions, Decisions, Open Issues

A running record of the **v8 data-ingestion + 3-modality assessment** build (started 2026-06-25), so
we know what we did and can revisit each assumption deliberately. The *how-the-code-runs* layer is
`CLAUDE.md`; *where-we-are* is `HANDOFF.md`; the *design narrative* is the auto-memory
`project_three_view_feature_mapping`. This file is the **assumptions + issues ledger** beneath them.

## The pipeline, end to end

```
Drive goruntuler/  --pull-only mirror-->  data/goruntuler_mirror/  (PHI, gitignored)
   |  sync_records (datasheet + ledger)
   |  materialize_studies  (depth-rank DICOM matching)
   v
data/studies_root/<op>/<session>/<study>/      sweep at root (discoverable)
   |   captures/c1_femur_deep/  c2_rf_shallow/  (femur + center-RF)
   |   study_discovery.study_modalities()  ->  {femur, rf, sweep}
   v
3-phase picker (p03 tabs)  ->  per-modality review (p04)  ->  feature routing  ->  condition score
```

## Assumptions (each is a deliberate choice we can revisit)

**A1 — Three modalities per study, and the depth of each is *measured, not assumed.*** A v8 study is
a center-femur cine (deep), a center-RF cine (shallow), and a long lateral→medial sweep. Depth =
`Rows × PixelSpacing` from each capture's own DICOM. *Why it matters:* probe-831 operators deepen the
shallow plane for larger patients (e.g. anonaan BMI 38 → RF/sweep at 6.9 cm, not 4.9), so any fixed-depth
assumption wrongly rejects valid studies.

**A2 — Materialize matches DICOMs to captures by RELATIVE RANK, not fixed protocol depth.** The
deepest DICOM → femur (only when it clears a ≥1 cm plane gap), the shallowest remaining → sweep then
RF (`materialize_studies._assign_dcms`). *Assumption:* the depth presets are ~2 cm apart, so a 1 cm
gap distinguishes the femur plane from the shallow plane. *Guard:* a shallow capture whose nearest
DICOM is within 1 cm of the femur's is left unscaled and the sweep HOLDs — this stops a femur-retake's
deep DICOM from wrong-scaling a sweep whose own DICOM was skipped (reviewer-found). *Residual:* a
study with NO femur capture but only deep DICOMs + a skipped sweep DICOM is not caught (would need the
width→depth table — future work).

**A3 — Deconfliction classifies modality by RELATIVE width WITHIN the study, never an absolute
threshold.** Longest clip = sweep; a cine strictly narrower (deeper) than the study's shallowest
capture = femur; a cine at the shallowest width = center-RF (`study_deconfliction`). *Why:* probe-829's
femur (285 px) and probe-831's RF (285 px) share a width but are different modalities — only the
relation holds. *Assumption:* the femur is always the deepest capture (Akadir confirmed). Width comes
from a content `.jpg` frame, not the 256-px auxiliary `_raw.png` the device also writes.

**A4 — Feature → modality routing map.** geometry (thicknesses, area) + fat + **STAR (=QMT/BMI)** +
QMT ← FEMUR; `int_cv_within` and the other intensity features ← center-RF; everything else ← sweep.
*Why STAR/QMT must be the femur:* they need the femur in view to measure, and the segmenter finds the
femur class (7) in the femur cine but NOT in the shallow sweep (verified) — the sweep's QMT is only a
degraded no-femur proxy. STAR is geometry, not echo-intensity (`feature_catalog.py:37` "STAR
(QMT/BMI)") — corrected from an earlier mis-grouping.

**A5 — Routing the score to the femur is the SAME quantities from a cleaner view — comparable, NOT
out-of-distribution (Akadir's elevation, 2026-06-25).** The features are the same anatomical quantities
(QMT, RF/full thickness, fat, STAR). The legacy cohort **already** measured femur-bounded quantities:
QMT/STAR come only from femur-bearing frames (the `qmt_provenance` 3-tier gate; femurless studies fall
to the no-femur tier), so the model's `mu`/`sd` are already about the same measurements the femur cine
gives. The frame-GROUP differs (a dedicated center-femur cine vs a sweep frame that happened to include
the femur) — a MODEST measurement-view shift, not a distribution break; they share more than they
deviate. So the model applies and the headline shifts only modestly. *Evidence (even-sampled frames,
indicative):* anonaan 0→12 (POOR→POOR), anonace 98→100 (PRESERVED→PRESERVED) — bands held, and the femur
values were the SANER ones (the sweep's fat for anonaan was 1.43, impossible; the femur gives 0.52; the
sweep's QMT is a no-femur proxy). **Decision (Akadir): route everything incl the headline; surface the
shared semantics + ONE honest caveat for the view difference; refine when the new cohort is rich. While
new data is scarce this is a sound, RECORDED approximation — not apples-and-oranges, because we know and
record what we do. Do NOT gate, re-calibrate, or treat each difference as a blocker.**

**A6 — Each modality medians its selected frames** (replacing the legacy single anchor-primary frame).
A cheap auto median-of-top-k clean femur frames is kept as a selection-independent cross-check (the
re-pointed landmark).

**A7 — Substitution is asymmetric.** A femur (geometry) capture is gain-agnostic and substitutable
across sessions (Akadir swapped anonaam's + anonace's femur from a cleaner shot). An RF or sweep (intensity)
capture is gain-SENSITIVE and must be the protocol-gain (30/35 dB) capture, never borrowed. *Open:*
gain is not machine-readable yet (operator discipline; ask Leltek if it is a DICOM tag).

**A8 — The center-femur-vs-legacy-sweep geometry comparability is the load-bearing assumption.** Ruled
comparable (Akadir), surfaced via the new/old/mix cohort selector. The long sweep is the legacy-
comparable bridge (same view as the 282 cohort).

**A9 — The sweep stays the discoverable root primary (backward-compat).** The current scorer + the 282
cohort are sweep-trained, so discovery + the score path are byte-identical for the sweep; the femur/RF
live under `captures/` and are addressed via `study_modalities`. The femur becoming the *geometry
anchor* is the routing (A4), not the on-disk primary.

## 3-phase selection — what's built vs deferred

| Step | State | Note |
|---|---|---|
| modality loader (`study_modalities`) | DONE (`99b125f`) | reads manifest + captures/; legacy → sweep-only |
| picker tabs (p03) | DONE (`3187c1b`) | sweep keeps legacy keys (no regression); femur/RF get own keys |
| per-modality review (p04) | DONE (`4106ae6`) | segments femur/RF picks, shows overlay; display-only |
| feature routing into the live score | DONE | `feature_routing.py` (route map + per-modality compute); p05 routes the aggregate + recomputes placements + a caveat banner; browser-verified on anonaan (routed headline 4/POOR, banner shown); legacy single-modality unchanged |
| sweep-EI (sweep-level fascicular contrast) | DEFERRED | additive/reference, not cohorted yet |
| new-cohort accumulation (`per_study_cohort_new.csv`) | DEFERRED | from the routed features |

## Open issues / follow-ups

| # | Issue | Impact | Fix direction |
|---|---|---|---|
| I1 | `check_study` depth-bucketing hard-codes 6.9/4.9 | the 831 operator verdict mislabels RF as "rf0" | same relative-depth fix as materialize |
| I2 | deconflictor DICOM attachment is time-based | orphans a cross-session femur DICOM (benign — materialize re-matches by depth) | make it depth-based per the locked design |
| I3 | model fit on legacy sweep cohort | A5 score shift | refit on the new cohort once N is enough |
| I4 | mp4-recovery for a missing frame folder | can't recover frames as-is | the mp4 is 1920×1080 full screen → needs sector-fan crop + a geometry-match gate |
| I5 | `_assign_dcms` femur-less + only-deep-DICOM residual | rare doubly-degenerate zip could mis-scale a sweep | the width→depth table |
| I6 | MEMORY.md index near its load cap | tail entries truncate | periodic compaction (done once 2026-06-25) |
| I7 | **role assigned to an orphan capture** (AKA017: a stills-only 6.9cm group with no mp4 became the RF; the real 7.9cm mp4 RF was demoted to a 2nd femur) | the score's RF rides on a stray the pipeline already flagged | see the capture-association fix below (role-agreement + surface the flag) |
| I8 | **`_assign_dcms` rank-fills a DICOM without a depth-agreement check** (AKA017: a 7.9cm DICOM on 6.9cm RF frames → 14.5% inflation; passes the femur-gap guard because the femur is 9.9cm) | RF/fat-thickness/area inflated on any capture whose true-depth DICOM is absent | depth-agreement matching (below) — generalises I5's "width→depth table" |

## Capture-association fix (designed 2026-07-01, NOT applied — [[project_capture_association_audit]])

The 2026-07-01 audit found AKA017's scored RF rides on a mislabeled capture. Two ordered fixes; the
**role** one is primary. Audit tool that detects it (torch-free, cohort-wide): `scripts/audit_capture_consistency.py`.

**1 — Role-association: don't SILENTLY score a dubious capture, and propagate the flag.** `study_deconfliction`
must not let an **orphan stills group (no `.mp4`) be a scored role unflagged**. The default candidate for a role
is a proper mp4-backed cine at a plausible depth (AKA017: c3 @7.9), but the choice is the **operator's** — c1
@6.9 with a corrected ruler is also defensible (it's the shallowest plane), so **flag-for-review, do NOT
auto-swap**. Materialize already writes an `ambiguous_captures` entry (AKA017: *"the only rf take —
borrowed/missing video?"*), but it dies in the manifest; it must **reach `study_modalities` → the scorer → the
UI**, so a study scores on a flagged role only after the operator confirms — never silently. Signals available with no model: mp4-presence per
capture, frame-width depth (`1967/width`, jpg H=512 const), duplicate-role count.

**2 — DICOM-association: `_assign_dcms` matches by depth-AGREEMENT, not rank-fill.** For each capture compute
its intrinsic frame-width depth; match the DICOM whose depth agrees within ~0.35 cm (ties allowed — two 7.9cm
captures ↔ two 7.9cm DICOMs). Edge cases the naive zip-of-ranked-lists misses (all real on the pilot): (i)
**fewer DICOMs than captures** (AKA017: 3 vs 4) → the unmatched capture records `spacing_source:"frame_width"`
and derives `sy = frame_depth×10/rows_const`, never steals a wrong DICOM; (ii) **no DICOM at a capture's
depth** (AKA017 RF @6.9) → width-derive + flag; (iii) **duplicate captures of a role** → each matches by its
own depth. The current femur-gap guard is subsumed (a shallow capture can't get a deep DICOM — depths won't
agree). This is I5's deferred "width→depth table", generalised.

**3 — Serve-time reconcile.** At `_spacing_row_from_dicom` / `feature_routing`, if an already-materialized
capture's attached DICOM disagrees with its frame-width depth, prefer the width-derived spacing — so a study
materialized before the fix is corrected at score time, not only on re-materialization.

**4 — Data (gated, authorized step).** Re-materialize AKA017 + re-score once the guard lands. First confirm
from the source zip whether a 6.9cm RF DICOM was ever saved (→ re-assign) or is genuinely absent (→
width-derive is the only correct recourse). AKA017 disk shows only {7.9, 7.9, 9.9} DICOMs — none at 6.9.

### Implementation handles (read before coding — the analysis, pinned)

**Exact fix sites, in dependency order.**
- **ROOT of the role bug** = `study_deconfliction._classify_modalities` (l.118-153), NOT `study_discovery`
  (which only reads the folder names the classifier wrote). Line 131 `shallow_w = max(widths)` takes the
  **widest** capture as the shallow anchor, and `_is_femur` (l.133-140) calls every cine **strictly narrower
  than that anchor** a femur. On AKA017 (widths c1 285 / c2 199 / c3 249 / sweep 249) the widest is the
  **orphan c1** → c1 becomes RF and **both** c2 and c3 become femur (the DUPLICATE_ROLE the audit catches;
  the code already warns l.154 but doesn't reconsider the anchor). **Fix:** the shallow anchor must ignore a
  no-mp4 stills group; and when >1 femur survives, the cine at the sweep's depth is the RF, not a 2nd femur.
- **DICOM match** = `materialize_studies._assign_dcms` (l.141) rank-fill → depth-agreement (fix #2 above).
- **Role read (downstream)** = `study_discovery.study_modalities` (l.184-196): substring role assign, and
  `role in out: continue` (l.191) **silently drops a duplicate role** — after the classifier fix this
  resolves, but the silent drop is a latent robustness gap.
- **Flag propagation** = materialize writes `ambiguous_captures` to the manifest; `study_modalities` does
  NOT read it, so it dies there. Carry it onto the returned object → scorer (`feature_routing`) + p05.
- **Serve-time reconcile** = `run_pipeline._spacing_row_from_dicom`: prefer width-derived `sy` when the
  attached DICOM's depth disagrees with the frame-width depth.

**Device facts + the stale code that contradicts them (get this right or the fix is unsound).**
- **FOV-1967** (`width_px × depth_cm`, 128L) is now **device-VALIDATED for probe 829**: AKA017 widths 249→7.9
  and 199→9.9 match the MP4 burned-in "Depth" readouts *and* the DICOMs to **<0.02 cm**. Width→absolute-cm is
  trustworthy now.
- BUT `leltek_deconfliction.py` NOTE (l.~20) still says *"treat width as a DISCRIMINATOR, not an absolute-cm
  source, until FOV_CONST/ROWS are re-derived against more confirmed captures"* — that caveat is now stale
  (the confirmed captures exist); **update it** or an implementer will distrust the width-derive. Its
  `ROWS = 585` (l.~30) is also **stale** — round-3 probe-829 DICOMs are **Rows = 570**; always read rows from
  the study's own DICOM, never the constant.
- **The distinction that reconciles with Akadir's "relative, not absolute" ruling** (`study_deconfliction`
  l.121, 2026-06-25): absolute **WIDTH** is probe-dependent (829 femur 285px == 831 RF 285px) → NOT usable, as
  he ruled. Absolute **DEPTH** (via the probe-specific FOV constant) IS comparable and now validated → usable
  as a sanity/tiebreaker. The fix leans on absolute **depth**, not width — consistent with his decision.

**Ground-truth arbiter (how to settle c1-vs-c3, and to re-validate FOV on a new probe).** The MP4s are
1920×1080 full device screen with a burned-in "Depth X cm" readout. Extract + read a frame:
`ffmpeg -v error -i <mp4> -vf select=eq(n\,10) -vframes 1 out.png`. MP4s live at the study root (sweep) and
`captures/c*/`. This is what settled the depths this session.

**Regression surface / blast radius.** After the fix: **re-materialize all 18 studies + re-run
`audit_capture_consistency.py` → only AKA017 may change; the 17 clean studies must get IDENTICAL DICOM
assignments** (diff the manifests); AKA004 is the known-good control. `study_modalities` is consumed by the
scorer, the audit, AND p05 — ADD the flag, don't alter existing fields. Legacy single-sweep studies (no
`captures/`) must stay byte-identical. This fix **extends recently-hardened code — read it first**:
`_assign_dcms` (reviewer-hardened 2026-06-25, depth-rank + wrong-scale guard — [[project_intake_pipeline_bridge]])
and `study_deconfliction` (the 2026-06-30 pairing fix — [[project_deconfliction_pairing_fix]]); the same
hard-coded-depth root also lives in `check_study` (I1) — fix together.

**Prove the fix (not just green tests).** Re-materialize AKA017 → RF resolves to the chosen capture with a
matching-depth ruler; audit → AKA017 CLEAN or correctly HELD-for-review (never silently scored); re-run
`audit_measurement_derivation.py` + `audit_score_impact.py` → RF cards + Models A/B move, C unchanged; 17
others unchanged; full suite green; if the flag surfaces in p05 → render + LOOK.

## Verification done (not just claimed)

- All 4 pilots materialize (probe-831 at 6.9 cm by rank, was held); 584 preflight/UI green.
- Deconflictor + materialize: akadir-reviewer-hardened (relative classification, wrong-scale guard).
- Picker tabs + per-modality review: browser-verified on the real anonaan study (femur tab 9.9 cm; femur
  segmentation overlay with the QMT caliper to the bone).
- Routing: standalone-scored sweep vs routed on anonaan + anonace (A5 evidence above); browser-verified the
  live p05 (routed headline + caveat banner); akadir-reviewer-hardened — two real bugs fixed: (1) the
  femur/RF picks now invalidate the run cache (`current_input_sig`) so a femur re-pick re-scores; (2) the
  routed aggregate + placements now PERSIST to the draft/ledger (was session-only, so Accept/reload had
  silently reverted to the sweep score) and the caveat reloads with it.

## Commit trail (2026-06-25)

`70657b9` STAR=geometry · `0ab15a1` deconflictor relative classification · `35d2a51` gitignore/HANDOFF
· `6c71cde` materialize depth-rank + guard · `99b125f` modality loader · `3187c1b` picker tabs ·
`4106ae6` per-modality review · (feature routing — pending).

# DATA LIFECYCLE — from a probe on a thigh to a number you can defend

*Written 2026-07-30. The explicit account of how a study becomes usable evidence: every stage, every
gate, who decides what, and — honestly — what is still not covered. Technical, but core product
development: this is the machinery that lets us say a number means something.*

**The one sentence.** Ingestion is not finished when files land on disk; it is finished when the data
is **usable by analysis** — and the pipeline must be able to state which of those two it is in.

Run it: **`python scripts/ingest_all.py --status`** answers "where do we stand?" and runs nothing.

---

## 1 · Why this needs to exist at all

On 2026-07-30, 27 studies were ingested. Five stages ran, every one reported success — and two were
silently owed. `qa_master.csv`, the table analysis actually reads, stayed at 348 rows from nine days
earlier while 98 studies sat on disk. **An analysis run that afternoon would have used n=66 while
believing it had everything.** Nobody was told; it surfaced only because a parallel piece of work
tripped over it.

The cause was not carelessness. It was that ingestion existed as seven separate commands whose correct
order lived in one person's head. A pipeline that can silently stop two-thirds of the way through and
still look successful is not robust, however good each individual step is.

## 2 · The eleven stages

| # | stage | command | what it establishes | if skipped |
|---|---|---|---|---|
| 1 | **Arrival** | *(operator uploads a zip)* | the data physically exists | — |
| 2 | **Registration** | `sync_records` | the study has a datasheet row | **invisible to every later stage** — both are row-gated |
| 3 | **Acceptance** | operator sets `Dosyalar Tam mı`; `check_studies` writes the verdict | a human vouches the capture is complete | never ingested |
| 4 | **Criterion** | `sync_force_measures` + the **measurement gate** | dynamometer values joined into the datasheet AND checked at entry (`Ölçüm Kontrol`) | **the criterion silently carries entry errors** — the kgf·m case, §7 |
| 5 | **Materialization** | `ingest` | frames on disk, roles deconflicted | not measurable |
| 6 | **Integrity** | `audit_capture_consistency` | role/DICOM errors surfaced | silent measurement errors |
| 7 | **Qualification** | `classify_probe` | which instrument shot it | **incomparable data mixed into the reference** |
| 8 | **Population / Reference** | `derive_new_round_cohort` · `refreeze_cohort` | what a patient is compared *against* | new studies invisible; stale reference |
| 9 | **Provenance** | `cohort_freeze_log` | what the re-freeze *moved* — and, since 2026-09-03, its per-frame evidence: `new_round_landmark_r<N>.json` beside the r<N> CSV (which frames made each role's top-5, uid-keyed, name-free) | the reference shifts unrecorded and unquestionable; nothing can say which frames a served number came from |
| 10 | **Identity** | `study_registry` | a permanent `study_uid`, and cohort membership | cannot be joined to anything |
| 11 | **Substrate** | `build_qa_sheet` | `qa_master.csv` — what analysis reads; ledger-held cells arrive **blanked with a receipt** | **analysis silently runs on old data** |

Stages 1–4 need other people's time (the capture, the row, the dynamometer session); 5–11 need
nobody. The gate inside stage 4 re-runs at stage 11 too, so a value typed straight into the
datasheet — never synced — is still checked before analysis can read it.

**Identity comes AFTER Population, and that ordering is load-bearing** (corrected 2026-08-03; it used to
sit at 5, before the reference was re-derived). `study_registry` assigns each study's cohort by
*membership* in `per_study_cohort_new.csv`, so running it first assigns against the previous reference
and stamps every newly-frozen study `unknown` — placeable, therefore failing the completion contract,
therefore un-COMPLETE-able in a single `--apply`. It cost a manual second pass on 2026-07-30 (28 studies)
and again on 08-03 (23). Nothing between Materialization and Population reads the registry, so the only
real edges in the graph are Population → Identity → Substrate.

## 3 · The four gates that stop and ask

A pipeline that decides silently is not practical; a pipeline that asks about everything is not usable.
Four decisions are genuinely a human's:

1. **Writing rows to the operator datasheet** — outward-facing; other people read it.
2. **Writing verdicts to the operator datasheet** — same, and it is their feedback signal.
3. **Writing force values + measurement flags to the operator datasheet** — same surface; the
   `Ölçüm Kontrol` column tells an operator their dynamometer entry looks like a unit error.
4. **Re-deriving the new-round cohort** — a **freeze**: it moves the placement reference the app
   serves, so percentiles change for everyone.

Declining any of them is always allowed. Being *unaware* that you declined is not — the completion
contract reports the cost.

## 4 · What "freeze" means, and why it is not just a rebuild

A **frozen artifact** is one the product serves. Changing it changes numbers a clinician sees, so it is
versioned, never edited in place, and the previous version stays on disk for rollback.

| artifact | what it decides | current |
|---|---|---|
| `per_study_cohort_v1.3.csv` | the old-round percentile reference | **v1.3**, 271 studies |
| `per_study_cohort_new.csv` | the new-round percentile reference | 66 studies, frozen 2026-07-14 |
| `condition_models_v1.3.json` | the score weights | Tissue 43 / STAR 33 / Fat 25 |
| `triple_placement.json` | the L1.3 regression reference | frozen 2026-06-10 |

**The freeze protocol** — followed for v1.1→v1.2 and again for v1.2→v1.3:
report the before/after **first** · write a new version, never in place · keep the old for rollback ·
check the stop-rule (CV-QWK ≥ 0.791) · record provenance in `rebase_provenance.json` · repoint
`cohort_placement` · run the floor · render and look.

Worked example, the v1.3 probe re-freeze: cohort 282→271, fit 216→215, CV-QWK 0.813→0.811, exact%
unchanged, axis shares identical. **Excluding the wrong-probe studies cost nothing measurable** — which
is the strongest form of such a decision, and the reason to quantify before freezing rather than after.

## 5 · The completion contract

Ingestion reports success only when all of these hold. Each one would have caught a real failure that
actually happened:

- every Drive zip has a datasheet row *(8 did not, uploaded mid-afternoon)*
- every ready datasheet row became a study on disk *(2 were held silently on 2026-08-03; rows
  pronounced DEAD in `data/kill_list.jsonl` are excluded and named, never silently dropped)*
- every study on disk is registered
- no study on disk is left `cohort=unknown` *(28 were)*
- the substrate covers every v8 study on disk *(66 of 93)*
- the new-round cohort is not older than the newest study *(frozen 07-14, newest study 07-30)*
- the substrate is not older than the newest study *(built 07-21)*
- the freeze log describes the cohort actually being served *(a reference that moves unrecorded is
  one nobody can question afterwards)*
- the substrate carries what only the datasheet can supply *(all five force columns once arrived
  blank while the contract read COMPLETE 8/8)*
- held measurements are actually held *(a cell the flag→kill ledger holds must be blank in
  `qa_master` — an open flag is a normal pending state and never fails the contract; a HELD value
  leaking through means the substrate stopped consuming the ledger)*
- no set-aside (128LH) study is in the active cohort *(11 were, now 0)*
- every legacy study has a determined probe *(19 had none, now 0)*

An invariant whose input is missing is **omitted, never auto-passed** — an unknown must not read as a
success. The contract first read **COMPLETE** on 2026-08-11 (cohort r4, 125 studies).

## 6 · Two exclusion reasons that must not be confused

*(The full instrument-by-instrument view — every rule, what it holds, who acts — is §10; the
number legend, what each denominator counts, is §11.)*

This distinction cost real time to recover and is the subtlest thing here.

- **Wrong instrument** — deterministic, from geometry, no judgement. The legacy corpus was shot on two
  Leltek probes; 128LH has a different field of view and echo texture, and the confound lands on the
  axis carrying 43% of the score. **A beautifully clear 128LH image is still excluded.** Clarity is
  irrelevant to comparability, which is exactly why 11 such studies passed human review and sat in the
  reference for months.
- **Bad acquisition** — a human judgement about whether an image can be measured at all.
- **Dead recording** — an export that can never materialize, however long we wait (the founding case:
  a zip carrying videos and DICOMs but **zero RawImages frames** — there is nothing to measure and no
  later step can conjure it). Pronounced dead **by decision, not by code** (Akadir, 2026-08-11:
  *"kill it and just move"*), recorded in `data/kill_list.jsonl` keyed by a PHI-free hash of the
  normalized study name. A killed row stays visible in the operators' sheet and in the census line —
  **ignored, never invisible** — and stops counting in the contract. Death is permanent; a re-recorded
  patient arrives as a new study, never by reviving a killed row.

They were conflated in one `quality_failed` flag. The consequence: of 319 legacy studies, all 37 that
three raters unanimously failed are 128LH, and **no 128L study was ever unanimously failed**. So that
set is a pure *instrument* signal — and **must not be reused as refusal (R5) ground truth**, because it
would teach the detector to reject good images from an unfamiliar probe. The genuine
bad-image-on-the-correct-probe examples number **two**.

Below the study grain, the same ledger carries two finer verdicts (2026-08-12,
`preflight_helpers/flag_kill_ledger.py` — one reader, three grains):

- **Suspect measurement** — a CELL triggers it, but the recording and every image-derived number
  stand; only the dynamometer side is in question. FLAG is automatic (the sync-time gate), and a
  flagged row is **no-force equivalent** (Akadir's rule, 2026-08-12): *every* criterion cell is
  blanked in `qa_master` with a receipt in `measurement_flags`, whatever the tier — hard/soft is
  audit priority, never hold policy. Using a flagged value is always an **explicit, recorded human
  consent**: KILL/CLEAR via `scripts/measurement_verdict.py` — CLEAR consents to the values as
  recorded and releases the row; KILL holds its fields forever and releases the rest (the human
  has ruled). A verdict binds to the flagged *values* — an edited cell re-enters the gate.
- **Withheld feature** — one feature untrustworthy for one study (AKA017's texture): recorded in the
  ledger at feature grain; enforced by the freeze (`TEXTURE_WITHHOLD`), with a preflight test
  guarding the two against drift.

## 7 · What is NOT covered — the honest list

Recorded so nobody assumes the pipeline is more complete than it is.

1. **Two gates are described but not implemented**: a study that is ingest-ready while carrying a ❌
   verdict, and a probe or operator the protocol does not know. `PROBE_NUMBERS` is a *parser*
   whitelist, not a protocol gate — probe **830** appeared in July and nothing asked whether it was
   in-protocol. (It is: all 15 studies read femur 6.9 / RF 4.9 / sweep 4.9.)
2. **No probe verification for the new round.** Legacy probes are proven from geometry; v8 relies on
   the probe id an operator typed into the filename. Nothing cross-checks the two.
3. **The capture-QA watcher is still not installed**, so operators get feedback only when someone runs
   `check_studies` by hand. It was built on 2026-06-30.
4. **R6 comparability is unautomated.** A code change that moves a measurement is caught only if
   someone runs the L1.3 ladder step; today it reports 3 of 7 features out of tolerance against a
   reference frozen 2026-06-10.
5. **No subject key (R7).** A repeat scan of one person cannot be *stored* as a repeat. This blocks
   every longitudinal claim and must be settled before anyone is scanned twice.
6. **Nothing notifies.** `--status` tells the truth, but only when asked.
7. **`condition_models` is fit on the old round only** — new studies never change the score. Deliberate,
   but worth stating.
8. **~~Dynamometer values enter the datasheet unchecked~~ — CLOSED 2026-08-12: the measurement gate.**
   The proven cost that motivated it: two kgf·m-as-Nm entries (isometric ~1/9 of the same subjects'
   isokinetic torque, CV exactly 0.0) sat in the criterion for weeks and, kept in, would have nearly
   halved the scan's measured strength value (quarantined at the force-ladder prereg, Amendment 2).
   Now: `sync_force_measures` evaluates every datasheet row carrying a criterion value on every pass
   (stage 4), and `build_qa_sheet` re-evaluates at stage 11 — so a value typed straight into the
   sheet is caught too. Tiered, ratio-based criteria (`preflight_helpers/torque_plausibility.py`,
   thresholds pinned to the real cases): hard = entry-error signature (CV exactly 0 · iso/isok < 0.30
   · iso×9.81 ≈ isok · >6 Nm/kg · torque ≤ 0); soft = atypical (ratio < 0.70, unparseable). **Any
   open flag makes the row no-force equivalent** — all five criterion cells held out of `qa_master`
   pending an explicit human verdict; hard/soft is audit priority, never hold policy (Akadir's
   rule, 2026-08-12). Verdicts: `scripts/measurement_verdict.py` (kill/clear, human-only — CLEAR
   is the recorded consent that puts values back in). Acceptance run on the live sheet reproduced
   the 08-12 sweep exactly: rows 87+92 hard, row 24 soft, zero false positives on the other 103
   valued rows; all three rows serve no force pending the audit. **Still owed: Akadir's source
   audit of sheet rows 87, 92 and 24, then verdicts.** Honest scope: the gate covers the five
   dynamometer columns (+ Nm/kg via weight); demographics and other manual columns still enter
   unchecked.

## 8 · The tables — one map, roles not counts

Every number in this project lives in exactly one of the tables below. **Counts belong to
`ingest_all.py --status`, never to this map** — the map holds each table's *role*, so it cannot go
stale by numbers. The standing rule: **no new table without a builder script, a key, and a row here.**

| table | role | written by | key | changes when |
|---|---|---|---|---|
| `hasta_kayit` (Google Sheet) | LIVE operator source: recordings, demographics, dynamometer, verdicts | operators; `sync_records` / `check_studies` (gated, surgical) | Study Adı | operators work |
| `data/study_registry.csv` *(gitignored, PHI)* | identity: every study ever seen, one uid | `study_registry.py` (after the freeze — it binds membership) | `study_uid` | registry stage |
| `data/qa_master.csv` · `data/qa_frames.csv` · the lean sheet tab | THE analysis projections — per study, per frame, for humans | **one builder**: `build_qa_sheet.py` (regenerated whole, never hand-edited; `qa_sheet_preview.tsv` is its dry-run artifact, not a table) | `study_uid` | substrate stage |
| `data/cohort_stats/per_study_cohort_new.csv` (+ `_r1..rN` snapshots, freeze log, and per freeze since 09-03 `new_round_landmark_r<N>.json` = which frames + `new_round_cohort_provenance_r<N>.json`) | FROZEN new-round placement reference the app serves | `derive_new_round_cohort.py`, only through the gated FREEZE | `study_code` (= uid) | a deliberate freeze |
| `data/cohort_stats/per_study_cohort*.csv` (v1.x) + `condition_models*.json` | FROZEN old-round reference + score weights | offline fit/freeze (`condition_model.py`) | study code | a deliberate re-base |
| `data/cohort_stats/probe_classification.csv` | legacy probe assignment (128L vs set-aside 128LH) | `classify_probe.py --write` | study code | probe evidence changes |
| `scripts/analysis/results/capture_features.csv` (+ provenance) | the repeatability / feature-screen substrate, from raw pixels | `build_capture_features.py` (GPU; mind `scripts/analysis/cache`) | `study_uid × role` | deliberate rebuild |
| `scripts/parity_baseline.json` | cross-machine truth for "same data" | `check_parity.py --save-baseline`, only at contract COMPLETE, then committed | — | after COMPLETE |
| `data/kill_list.jsonl` | the **flag→kill ledger**: graded verdicts at three grains — study kills · measurement flags/kills · feature withholds; excluded/held, shown, append-only | flags: the measurement gate (automatic) · kill/clear: human decision only (`measurement_verdict.py`); reader = `preflight_helpers/flag_kill_ledger.py` | PHI-free name hash (study) / `study_uid` (measurement, feature) | a flag fires or a verdict lands |
| `data/calibration_labels.jsonl` | operator threshold labels | the labeling UI | frame | stays uncommitted (standing call) |

## 9 · For a stakeholder, in five lines

We do not accept a scan because it looks good. We accept it when it is **complete** (a human vouches),
**intact** (roles and geometry check out), **comparable** (shot on the instrument the protocol
specifies), **placed** (against a reference we froze deliberately and can roll back), and **visible to
analysis** (in the table our numbers are computed from). One command reports which of those five a
batch has reached, and refuses to call it done until all five hold. That is what lets us attach an
error bar to a number instead of an opinion.

## 10 · The decision table — every rule, what it holds, who acts

One place for the whole go / flag / check / no-go mechanism (Akadir's ask, 2026-08-13). The grain
column is the ledger's (`data/kill_list.jsonl`, append-only, latest entry wins per cell-set). The
human instrument for every verdict is ONE tool: `scripts/measurement_verdict.py` (`--scope
measurement | feature | study`). Two standing frames govern everything below: **the no-force
rule** (a row carrying ANY open measurement flag serves zero criterion cells, any tier — using a
flagged value is always a recorded human consent, never a default) and **freeze semantics**
(percentile placement moves only at freezes; kills leave qa_master immediately but leave the
frozen reference only at the next freeze).

| Rule | Grain / who fires | Automatic effect | Human action | Touches / never touches |
|---|---|---|---|---|
| Study kill (dead recording, duplicate ingest) | study / human only | out of census, substrate now, cohort at next freeze; permanent | `--scope study --kill` | everything / nothing survives |
| Torque plausibility, hard (CV=0 · kgf·m signature · ratio<0.30 · >6 Nm/kg · torque≤0 · **isometric CV>15**) | measurement / gate, every forcesync + substrate build | all five criterion cells held (no-force rule) | `--kill` or `--clear` per thread | force only / imaging, texture, score |
| Torque plausibility, soft (ratio<0.70 · unparseable) | measurement / gate | same hold — tier is audit priority, never hold policy | same | same |
| Measurement KILL | measurement / human | killed fields blank forever; the REST of the row releases | recorded, permanent for those values | named cells / the rest |
| Measurement CLEAR | measurement / human | values return to analysis; an edited cell re-opens the thread | recorded consent | — |
| 3-way thickness gate (femur vs centre-RF, ±10% `AGREE_TOL`) | feature / auto at freeze | texture blanked; thickness femur-sourced; condition score unavailable | p04 boundary fix, or a feature CLEAR | texture + score / force, lengths |
| Feature KILL (the AKA017 pattern) | feature / human | texture blank even when the gate passes | `--scope feature --kill` | same |
| Feature CLEAR (the un-withhold, 2026-08-13) | feature / human | texture returns at the next freeze — while the per-view thicknesses still match the clear's snapshot | `--scope feature --clear` | same |
| Kontrol verdict ✅⚠️❌ | study / operator | **nothing** — advisory; the freeze never reads it | informs review | display only |
| Card display tiers (CV ≤10 quote · 10–15 flag) | display / serve time | value quoted or flagged on the Strength card; >15 is now held upstream by the gate rule | Akadir's veto | card only / analysis |
| Envelope refusal | display / serve time | no expected band, honest caption | none | card only |
| Duplicate watch | census / every `--status` | contract FAILS on an undecided same-recording pair | kill the twin | blocks "COMPLETE" |
| Change watch | census / every forcesync | in-place edits of criterion cells printed since the last applied pass; verdicts re-open on changed values | read the report | visibility only |
| Display-code collision | display / picker | colliding codes get a uid-tail suffix (`AKA114-0D53`); census counts them | none needed | labels only |

## 11 · The number legend — what each denominator counts

Numbers move; the *meanings* don't. Each row says what the number is and where the current value
is read (values here are the 2026-08-13 state, as an example).

**Row-number convention (pinned 2026-08-24).** Every sheet-row number this pipeline prints or
records — the census, the completion contract, the ledger's `sheet_row_at_flag` — is the
**physical sheet row, with the header as row 1**. The datasheet's own `Sıra` column runs one
behind (physical row 168 = Sıra 167). The two were conflated once, mis-identifying a held row;
the contract now prints both forms ("168 (Sıra 167)") so neither reader trips.

| Number (today) | What it counts | Where to read the current one |
|---|---|---|
| 130 on disk | every materialized study, killed twins included (folders are never deleted) | census "on disk" |
| 125 frozen | the r4 placement reference = flat view = viewer = picker "new round" | census "new-round cohort" |
| 124 substrate new rows | living new-round studies analyses can read (kills leave immediately) | census "substrate" |
| 107 / 125 texture | studies whose fascicular contrast is served (18 withheld: 17 gate + 1 by verdict) | cohort provenance `texture_withheld` |
| ≤ texture count | condition scores (every recipe needs texture) | panel "score unavailable" |
| 106 → 101 force | criterion values entered → served (2 killed + 1 soft-held + 2 CV>15 held) | census gate line |
| n=86 / n=101 | research ladder PRIMARY (complete-case) / SENS_A no-texture — the served card model's fit set | `force_ladder_summary.json` |
| 271 / 282 old | legacy placement reference (v1.2 active / full) | census cohort split |

---

*Pipeline: `scripts/ingest_all.py`. Scope: `docs/DIRECTION.md`. Claims and gates: `docs/NORTHSTAR.md`.
Current state: `HANDOFF.md`.*

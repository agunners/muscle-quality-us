# The grading table — requirements, version 1

Prepared 2026-09-03 on the DGX. Status: draft for Akadir's sign-off. Owner of the document: engineering (this repo). Everything in it is grounded in files that exist today; each number names its source. Studies appear only as study codes (`AKA…`, `NR…`), never as names.

## 0 · Who reads what

| Reader | Minutes | Read |
|---|---|---|
| Boss, business, clinical lead | 6 | §1 (why), §2 (what was decided), §3 (the product on one page), §9 (one label per study), §13 (cost) |
| The three raters | 10 | §3, §4 (the words we use), §7 (what a sitting looks like), Appendix A (the rubric) |
| Engineering | 40 | §5–§12 (the table, the process, the analysis contract, the label rule, the requirements) |
| Analysis | 20 | §5, §8, §9, §10.5 |

## 1 · Why this exists

The muscle-condition score in the app was built against the doctors' grades of the **old** cohort: 216 studies carry a three-rater consensus grade with complete features, 37 POOR / 105 INTERMEDIATE / 74 PRESERVED (`docs/NORTHSTAR.md` §2). The **new** cohort, frozen as r5 on 2026-08-23, holds 175 studies from a different device and protocol and carries **zero** grades. Everything we know about the old-to-new gap comes from the pipeline's own numbers: thickness reads +2.26 mm higher in 10 of the 11 people scanned under both protocols (`project_old_new_same_person_pairs`), and the pipeline's texture axis sits +1.05 SD off the frozen reference. Nobody has yet looked at a new-protocol image and said what grade it is.

The grading table closes that hole, and it does so **as a living reference, not a one-off round**. Akadir's brief (2026-09-03, verbatim):

> "goal is to make a UI, that feeds into a single table, and when the pool gets bigger, we continue to label and add to newer versions of the datatable. so the process will not be single step, but dynamic, where we refine when more items come. we should also be able to refine existing, in a sense not like a traditional labeling app."

And, in the same session:

> "as the legacy app, new annotators might come in, and what they annotate will also be part of the data table. then we can also talk about how we can use this table, to label one study, finally."

So the rater set is open, not three people fixed for ever, and the table's end product is **one label per study** derived by a stated rule (§9), not a pile of rows.

What the grades are for, in order:

1. **The bridge.** Do doctors grade the same person differently under the new protocol? Eleven people were scanned under both; their old and new studies are graded blind. A non-zero offset means the old and new eras cannot be pooled without an era term (`docs/reports/2026-08-31_the_meter_face_to_face.html` §E2).
2. **A human reference for the new cohort.** Every downstream question about the new device ("does the texture axis still track what a doctor sees?") needs graded new studies. Today it has none.
3. **Rater reliability, measured.** Two doctors who disagree with each other 40% of the time cannot certify anything. The table measures agreement on every version, and the acceptance band is stated before anyone grades (§8).

What the grades are **not** for: retuning the condition score toward doctor agreement. That activity is closed by direction (`docs/DIRECTION.md` §4: "Stop optimising the condition score toward radiologist agreement"). The grade is a reference and a check, never the target of a fit.

## 2 · What has been decided

Recorded verbatim where Akadir wrote it; my recommendations where he said "overall I agree with your decisions" (2026-09-03).

| # | Decision | Value | Who |
|---|---|---|---|
| D1 | Freeze the first batch on | **r5** (175 new studies, 2026-08-23) | Akadir: "ok, we can go with r5 now" |
| D2 | Overlays in Phase 1 | **none**, for old and new items alike | recommended, agreed |
| D3 | What raters see as an identifier | **per-position item code** (G-001…), never a study code | recommended, agreed |
| D4 | Old-cohort sampling frame | **the 270 triple-rated old studies** (45/129/96), not the 216 Tier-1 subset | recommended, agreed |
| D5 | Third rater | Akadir's call, open | — |
| D6 | The tool is a **living table with versions**, refinement allowed | see §1 quote | Akadir |
| D7 | Sitting length | two sittings per batch, about two hours each, different days | recommended, agreed |
| D8 | Rater set | **open**: new annotators can join later; their rows are part of the same table | Akadir: "new annotators might come in, and what they annotate will also be part of the data table" |
| D9 | End product | **one label per study**, derived from the table by a written rule, versioned with the table | Akadir: "how we can use this table, to label one study, finally"; rule proposed in §9, for discussion |

## 3 · The product on one page

**What a rater does.** Logs in with their own credentials, sees one item at a time in a fixed order, and grades it in two locked steps. Step 1 shows only images and videos and asks for a grade (PRESERVED / INTERMEDIATE / POOR) plus one line of impression. Step 2 then reveals the pipeline's numbers for that study and asks for the final grade, with a stated reason if it changed. Nothing identifies the patient; the screen shows "G-137, 137 of 291". Some items are the same study shown twice, far apart, without saying so. A sitting ends at a fixed position; the second sitting is unlocked by the lead on another day.

**What the table is.** One table, one row per save, never edited, never deleted. A row says who graded which study, in which batch and mode, what they said in step 1 and step 2, and which frames were on the screen. Every derived number (a rater's current grade for a study, the three-rater consensus, agreement statistics) is computed from that table, never stored beside it by hand.

**What "versions" are.** When a batch of grading closes, the table is exported as an immutable version: `grades_v1`, `grades_v2`, … Each version carries a manifest (which freeze, which item lists, how many rows, checksums). Analysis runs on versions, never on the live table. A new version is created when new studies are graded (the pool grew) or when existing grades are refined (a review campaign). Old versions stay reproducible forever.

**What "refine" means.** A rater may reopen items they have already graded, see their own earlier grade and reason, and record a new one with a reason code. The earlier row stays. Reliability statistics use only first exposures, so refinement can improve the reference without contaminating the measurement of how consistent raters are. Refinement is opened per campaign by the lead, with a stated reason (a guideline change, a disagreement queue), not left permanently open.

**Who grades.** Anyone the lead registers as a rater. A new rater first works through an onboarding slice (blind, with repeats and settled items inside it) and qualifies on the same agreement bar as everyone else; from then on their rows count like any other rater's. Three raters start; the table does not care whether the tenth arrives a year later.

**What comes out.** For every study, one label with a status: CONSENSUS (qualified raters agree by majority), PROVISIONAL (too few raters yet), ADJUDICATED (the lead resolved a split), or OPEN. The label, its status and its agreement level are exported with every version, so a downstream use always knows how solid a given study's label is.

**What it costs.** About 5 to 6 engineering sessions before the first rater logs in (the readiness audit's 3–4 sessions for a one-off round, plus roughly 1.5 sessions for the versioned table, the review mode, the open rater set and the label rule), then about two hours per sitting per rater, two sittings per batch. Details in §13.

## 4 · The words we use

| Word | Meaning here |
|---|---|
| **Study** | One imaging visit of one person: for the new protocol a femur cine, a centre-RF cine and a sweep; for the old protocol three stills and a sweep video. |
| **study_uid** | The permanent, name-free identifier of a study (`NR` + 8 hex characters). The only key the table joins on. Display codes (`AKA012`) collide: 26 codes are shared by 57 studies (`ingest_all.py --status`, 2026-09-03). |
| **Era** | Old cohort (282 studies, 2025 device and protocol) or new cohort (r5: 175 studies, 2026 device and protocol). Raters can see the era from the rendering; they are told so. |
| **Freeze r<N>** | A dated, numbered snapshot of the new cohort's served numbers. r5 = 175 studies on 2026-08-23. Since 2026-09-03 each freeze also records which five frames per cine its numbers came from (`data/cohort_stats/new_round_landmark_r<N>.json`). |
| **Grading pack** | The frozen, renamed, name-free bundle of frames and videos the app serves for one batch, bound to one freeze by checksum. |
| **Item** | One thing shown to one rater at one position in the queue. A study can be several items (its first showing, a repeat, a review). |
| **Batch** | One sealed set of items per rater, bound to one freeze. Batch 1 = r5. |
| **Mode** | How an item was graded: GRADE (first exposure, blind), REPEAT (a scheduled second exposure, blind), REVIEW (reopened by the rater, prior grade visible), ADJUDICATE (the lead resolves a disagreement). |
| **Phase lock** | Step 1 (images only) must be saved before step 2 (numbers revealed) exists. Prevents the numbers from anchoring the visual grade. |
| **Effective grade** | A rater's current grade for a study: the latest step-2 grade across GRADE and REVIEW rows. |
| **Consensus** | The grade a study carries in the reference: majority of the three effective grades; a three-way split goes to adjudication. |
| **κ (kappa)** | Agreement corrected for chance, from −1 to 1. 0 = no better than guessing, 1 = perfect. Intra-rater κ: one rater against themself on repeats. Inter-rater κ: three raters against each other. The band is κ ≥ 0.6, the same bar the frame-labelling session W2 uses (`docs/NORTHSTAR.md` §5). |
| **Version** | An immutable export of the table plus its manifest. |
| **Rater** | Anyone registered in the rater registry with an id, a role and a qualification status. The set is open (D8). |
| **Qualified** | A rater whose onboarding slice met the agreement bar (§6.5). Only qualified raters' rows enter consensus; every row is kept regardless. |
| **Study label** | The one grade a study carries at a given version, with a status and an agreement level (§9). |

## 5 · The single table

### 5.1 One row per save

The chassis already writes an append-only log (SQLite with a derived CSV, 73 columns, `labeling_app/state.py:14–88` in the deployed chassis). The grading table keeps that discipline and changes the columns. The minimum set:

| Column | What it holds | Why it is there |
|---|---|---|
| `event_id` | unique id of this row | every derived number cites rows |
| `saved_at_utc` | timestamp | ordering, sitting reconstruction |
| `rater_id` | R1 / R2 / R3 (+ LEAD for adjudication) | per-rater analysis |
| `batch_id`, `freeze_id` | e.g. `b1`, `r5` | which pool, which served numbers |
| `item_code`, `position`, `sitting` | G-137, 137, 1 | what the rater saw; blinding audit |
| `study_uid`, `display_code`, `legacy_code`, `era` | NR…, AKA012-8AB2 or LLLDDD, old/new | the join, logged privately, never shown in GRADE mode |
| `mode` | GRADE / REPEAT / REVIEW / ADJUDICATE | which rows count for what (§8) |
| `repeat_of_event_id` | for REPEAT rows | intra-rater pairs |
| `supersedes_event_id` | for REVIEW / ADJUDICATE rows | the refinement chain |
| `quality_gate` | PASS / FAIL | FAIL rows are excluded from κ and counted |
| `preliminary_grade`, `preliminary_impression` | step 1 | the visual-only grade |
| `final_grade`, `changed`, `change_reason_code`, `change_rationale` | step 2 | the grade after numbers; why it moved |
| `shown_frame_bases` | pipe-joined frame identities per role | "shown = served", auditable per row |
| `pack_sha256`, `item_list_sha256`, `rubric_version`, `app_version` | checksums | reproducibility of what was on screen |
| `seconds_on_item`, `sweep_opened`, `phase2_opened_at` | telemetry | effort, and whether the sweep changed minds |

Dropped from the old schema on purpose: the 30 mask-derived measurement columns (new studies have no annotator masks; the served row is joined at analysis time by `study_uid` and `freeze_id`, not copied into every row) and the demographic columns (height, weight, comorbidities; see §11).

A second, small table, `rater_registry`, holds one row per rater: `rater_id`, display initials, role (doctor / lead / engineer), era experience (rated the old round: yes/no), `registered_at`, `qualified_at`, `qualification_event_ids`. It is the only place a rater's status lives; the event table never repeats it.

### 5.2 Derived views, never hand-edited

- `effective_grades`: one row per (rater, study): the latest `final_grade` over GRADE and REVIEW rows with `quality_gate = PASS`.
- `first_exposures`: one row per (rater, study): the first completed GRADE row. The only input to κ.
- `consensus`: one row per study: majority of the three effective grades, else the ADJUDICATE row, else "open".
- `repeat_pairs`: (rater, study, first grade, second grade, positions apart).
- `study_labels`: one row per study: the label, its status, the number of qualified raters, the agreement fraction, the table version, the rubric version (§9).

### 5.3 Versions

A version is written by one command (`scripts/grading/export_version.py`) and contains: the full event table as CSV, the four derived views, and `manifest.json` (version number, date, freeze ids covered, batch ids, item-list checksums, pack checksums, row counts per mode, rubric version, the sha256 of every file). Versions are committed to the repo (they carry no names). A version is never modified; a correction is a new version with a note.

Version triggers:

| Trigger | Example | New version |
|---|---|---|
| A batch closes | batch 1 on r5, all three raters through both sittings | v1 |
| The pool grew and a delta batch closes | r6 adds 18 studies; batch 2 = those 18 + repeats + drift anchors | v2 |
| A review campaign closes | the guideline gained a sentence about fascia dropout; raters re-check 40 items | v3 |
| Adjudication closes | the lead resolves the 12 three-way splits | v4 |

## 6 · The process: batches that grow, grades that can be refined

### 6.1 Batch 1 on r5 (counts from the readiness audit, `docs/grading_round_readiness_2026-09-02.gdoc` §Q4)

| Component | Count | Rule |
|---|---|---|
| New studies | 175 | all of r5 |
| Old studies | 58 | 20% of the total; stratified by consensus grade from the 270 triple-rated frame: 10 POOR / 28 INTERMEDIATE / 20 PRESERVED; forced to include the 11 pair-people's old studies (1 / 7 / 3) |
| Repeats | 58 | 44 new + 14 old, stratified; each shown ≥ 30 positions after its first showing; the same set for all raters |
| Items per rater | **291** | sittings of 146 and 145 |

Identical unique set and repeat set for every rater, different orders per rater, era runs capped at 6 in a row.

### 6.2 Batch k, when the pool grows

When a later freeze r<N> adds studies, batch k contains: every new study not yet graded; repeats at 20% of the batch, ≥ 30 apart; and **drift anchors**: a fixed set of 20 items (10 old, 10 new) from batch 1, chosen once, re-shown blind in every later batch. The anchors answer "did the raters drift between batches?" with a number, which a one-off round cannot ask. Each batch is sealed with its own item lists and a one-paragraph addendum to the pre-registration stating the counts. Nothing in an earlier batch is changed by a later one.

### 6.3 The four modes and what each may show

| Mode | Who opens it | Navigation | Prior grade visible | Other raters visible | Served numbers | Counts toward |
|---|---|---|---|---|---|---|
| GRADE | the item list | linear, locked | no | no | Phase 2 only | κ, consensus |
| REPEAT | the item list (hidden) | linear, locked | no | no | Phase 2 only | intra-rater κ |
| REVIEW | the lead, per campaign | free over the rater's own completed items | own, yes | no | Phase 2 only | consensus, revision rate |
| ADJUDICATE | the lead | the disagreement queue | all three, yes | yes | yes | consensus |

The two rules that keep the measurement honest: **first exposures only** enter κ, and **no rater ever sees another rater's grade** outside adjudication.

### 6.4 Refinement guardrails

- A REVIEW save requires a reason code from a fixed list (guideline clarified; second look; quality reconsidered; adjudication requested; other) plus free text of at least ten characters, the same floor the old rubric used (`labeling_app/app.py:58`).
- REVIEW is a campaign with a start, an end, a scope (which items) and a stated reason; it produces a version.
- A rubric change bumps `rubric_version`; rows graded under different rubric versions are never pooled in a κ without saying so.

### 6.5 New raters join (D8)

A rater who joins after batch 1 does not get batch 1's list; they get an **onboarding list** and then the open pool.

1. **Onboarding slice, blind:** the 20 drift anchors + 40 items drawn from studies whose label is already CONSENSUS (stratified by label and era) + 20 scheduled repeats of items inside the slice, ≥ 30 apart. 80 positions, one sitting.
2. **Qualification, computed by the analysis script, not by eye:** intra-rater κ on the 20 repeats ≥ 0.6 and weighted κ against the existing CONSENSUS labels on the 40 settled items ≥ 0.6. Below the bar: a feedback session against the guideline and a second 40-item slice; below again: the rater's rows stay in the table, flagged `qualified = false`, and never enter consensus.
3. **Then the open pool:** every study the rater has not graded, in a locked linear queue at their own pace, sittings of about 146; 20% scheduled repeats as for everyone. Their rows join the same table; the next version recomputes consensus with them in it.

Two consequences are stated up front. Agreement statistics must tolerate a different number of raters per study, which is why ordinal Krippendorff's α is the primary inter-rater measure from the moment the fourth rater arrives (Fleiss' κ needs the same raters on every item and stays as a check on the fully crossed subset). And a study's label can change when a new rater's vote lands; every version ships a label changelog (§9.4).

## 7 · What a sitting looks like (rater view)

1. Log in (own credentials). The queue shows "Sitting 1 · item 37 of 146".
2. **Phase 1.** Item code, the quality gate (PASS / FAIL), the frames and videos. New item: five femur frames on one row, five centre-RF frames on the next, femur video and RF video, the sweep behind a click. Old item: three stills on one row and the sweep video. No overlays, no numbers, no age, no sex. The rater picks a grade and types one line, then saves. The save is final for Phase 1.
3. **Phase 2.** The served row appears: age band, sex, BMI, RF thickness in mm, QMT with its provenance, STAR, the fat measures, the texture value or "withheld — reason", and the served segmentation overlay (annotator overlay for old items). The rater confirms or changes the grade; a change needs a reason.
4. Next item. Completed items are locked in GRADE mode.
5. The sitting stops at position 146. The lead unlocks sitting 2 on another day.

Time: 30–45 seconds per item at the old round's pace (the readiness audit's estimate from about 320 items per rater-day), so a sitting is about two hours with breaks.

## 8 · The analysis contract (pre-registered per version)

Frozen before any rater sees an image, as a committed page with the item-list and pack checksums; any later change is a dated amendment. The draft for batch 1 is `docs/grading_round_readiness_2026-09-02.gdoc` §Q5; it carries over with these version rules added.

**Primary endpoints per version.**
1. Intra-rater κ (linearly weighted) on the repeat pairs, per rater and pooled.
2. Inter-rater agreement on first exposures of the unique items: Fleiss' κ while every item has the same three raters (batch 1); ordinal Krippendorff's α becomes primary once raters differ per item (D8), with Fleiss on the fully crossed subset as a check. Per-rater reliability: weighted κ of each rater against the leave-one-out consensus.
3. Old-to-new offset on the 11 pair-people: mean signed difference (new − old) per rater with a bootstrap 95% interval; Wilcoxon on the 33 rater-by-pair differences.
4. Drift across batches (from v2 on): κ of each rater against themself on the 20 drift anchors, batch k vs batch 1.

**Secondary.** Revision rate Phase 1 → Phase 2 (old round: 6.6% of 831 completed); quality-FAIL rate by era (old round: 13.4%); REVIEW revision rate and reason-code distribution; seconds per item; whether opening the sweep predicts a revision; Spearman of the consensus grade with served RF thickness, QMT, texture and fat-to-depth on new items (descriptive only).

**Acceptance bands and their precision.** κ_intra ≥ 0.6 per rater and κ_inter ≥ 0.6 certify a version's grades. With 58 repeats and three roughly balanced categories the 95% interval half-width on a κ near 0.7 is about ±0.15: it separates 0.6 from 0.3, not 0.6 from 0.5. With 233 unique items the inter-rater half-width is about ±0.08. The pair offset at 11 pairs × 3 raters detects about 0.4 of a grade step; smaller offsets are reported as inconclusive, never as absent.

**Actions per outcome.** A rater below 0.6 intra: a 20-item feedback recalibration and a 40-item re-rate slice; still below → excluded from consensus for that version. Inter below 0.6: no consensus issued; guideline revised; a 60-item slice re-run as a REVIEW campaign before pooling. Pair-offset interval excluding zero: the old-to-new bridge carries an era term on grade. Drift κ below 0.6 on the anchors: the batch's grades are flagged and the raters recalibrate before the next batch.

**Who is excluded from what.** Quality-FAIL rows: excluded from κ, counted. REVIEW and ADJUDICATE rows: excluded from κ, included in consensus. One rater (Akadir) also rated the old round and knows the pipeline; his grades are reported separately as a sensitivity analysis.

## 9 · From the table to one label per study (D9, proposed for discussion)

The rows are the evidence; the label is a derived fact with a stated rule. The rule below is my recommendation; the alternatives and when they become better are named so the decision is a real one.

### 9.1 The rule

For a study at table version v:

1. Take the **effective grade** of every **qualified** rater (§5.2): their latest step-2 grade across GRADE and REVIEW rows with the quality gate at PASS. Unqualified raters' rows are kept but not counted.
2. **n** = the number of such grades.
3. If an ADJUDICATE row exists for this study at this rubric version: label = the adjudicated grade, status **ADJUDICATED**, agreement = the pre-adjudication fraction.
4. Else if n ≥ 3 and one grade holds a strict majority: label = that grade, status **CONSENSUS**, agreement = share of raters on it (1.00 for 3 of 3, 0.67 for 2 of 3, 0.75 for 3 of 4, and so on).
5. Else if n ≥ 3 and no strict majority (a three-way split, or 2 against 2): status **OPEN**, queued for adjudication; label = none.
6. Else (n = 1 or 2): label = the grade if all agree, status **PROVISIONAL**; if the two disagree, status OPEN. Provisional labels are exported but flagged; downstream uses that need certainty filter on CONSENSUS or ADJUDICATED.

Ties on the ordinal scale are not averaged: a study is never "INTERMEDIATE-and-a-half". Adjudication is the only path through a split.

### 9.2 What a label carries

`study_labels_v<k>.csv`: `study_uid`, `era`, `label`, `status`, `n_qualified_raters`, `agreement`, `rubric_version`, `table_version`, `last_event_id`, `changed_since_previous_version` (yes/no, with the previous label). A study's label is never edited by hand; a wrong label is fixed by a REVIEW campaign or an adjudication, which produces the next version.

### 9.3 Alternatives, and when to switch

| Rule | What it does | When it beats the majority rule |
|---|---|---|
| Majority of qualified raters (recommended) | transparent; every label explainable by pointing at rows | always, at 3–5 raters and up to about 500 studies |
| Reliability-weighted vote | each rater's vote weighted by their leave-one-out κ | when one rater is markedly weaker but still qualified; keep as a reported sensitivity, not as the label |
| Latent-class model (Dawid–Skene) | estimates each rater's confusion matrix and the most probable true label | when raters ≥ 6 and studies ≥ 300 with sparse overlap; revisit at that point, never before |

### 9.4 What the label feeds, and the changelog

- The **bridge**: the 11 pair-people's old and new labels, by rater, are the grade-side offset (§8, endpoint 3); the study label is the reporting unit.
- The **new-cohort reference**: only CONSENSUS and ADJUDICATED labels enter any comparison with the pipeline's axes; PROVISIONAL labels are shown in tables but excluded from tests.
- The **changelog**: every version lists the studies whose label or status changed since the previous version, with the cause (new rater, review campaign, adjudication, rubric change). A reference that changes silently is the one failure this design refuses.

## 10 · Requirements

Each requirement has an ID, a statement, a ground (why, with its source), an acceptance test, and a status: **exists** in the chassis, **adapt**, or **build**.

### 10.1 Data and identity (DR)

| ID | Requirement | Ground | Acceptance | Status |
|---|---|---|---|---|
| DR-1 | Every row keys on `study_uid`; display codes are logged, never used as keys | 26 display codes shared by 57 studies; the registry keys on uid | a join of the table to `per_study_cohort_new_r5.csv` on uid loses zero rows | build |
| DR-2 | Every row carries `freeze_id`, `pack_sha256`, `item_list_sha256`, `rubric_version`, `shown_frame_bases` | "shown = served" must be auditable per row; the freeze now persists frame identities (`scripts/derive_new_round_cohort.py`, `top_frame_bases`) | for 20 random rows the frames named in the row exist in the pack and their values reproduce the freeze's landmark JSON | build |
| DR-3 | The table is append-only: no update, no delete | the chassis's existing discipline (`state.py:335–360`); refinement is a new row | a test attempts an update through the app's API and fails | exists / adapt |
| DR-4 | Versions are immutable exports with a manifest and checksums, committed to the repo | analysis on a moving table cannot certify anything | re-running `export_version.py` on the same inputs yields byte-identical files | build |
| DR-5 | The pack is bound to one freeze; the app refuses to start if `pack.manifest.freeze_id` ≠ `item_list.freeze_id` | r6 landing mid-batch would silently change the served numbers behind the screen | start the app with a mismatched list: it exits with the reason | build (mirrors `data.py:439–446`) |
| DR-6 | The served row for Phase 2 comes from `per_study_cohort_new_r<N>.csv` (new) and `per_study_cohort.csv` (old) plus the archived provenance `new_round_cohort_provenance_r<N>.json`; withheld texture renders as "withheld — <reason>" | 9 texture withholds at r5, 2 after the 7 pending releases; provenance is archived per freeze since 2026-09-03 | AKA017's Phase 2 shows "withheld — centre-RF at the femur plane" not a blank | build |

### 10.2 Blinding and queue (BR)

| ID | Requirement | Ground | Acceptance | Status |
|---|---|---|---|---|
| BR-1 | GRADE and REPEAT items are served in a pre-registered linear order; completed items lock | the chassis lets a rater pick any study and re-save (`app.py:663–699`); free navigation breaks repeat spacing | a rater cannot open position 40 before 39 is saved; a saved item is view-only | build |
| BR-2 | The screen shows only the item code and position; never uid, study code, era label, prior grade, repeat status, or served numbers before Phase 2 | code formats reveal era and repeats (`AKA…` vs `LLLDDD`) | screenshot audit of Phase 1 on one new, one old, one repeat item: no identifier visible | build |
| BR-3 | Repeats are ≥ 30 positions after the first showing and identical across raters | intra-rater κ needs blind, spaced re-exposure; inter-rater κ needs identical items | the item-list generator's tests assert spacing and set equality | build |
| BR-4 | No overlays in Phase 1 for either era (D2) | model masks are an output of the pipeline the round judges | Phase 1 render on a new item shows bare frames | adapt (`app.py:447–455`) |
| BR-5 | The dynamic anchor mechanism is switched off | it produced 0 anchor rows in the real old round (960 rows) | config shows anchors disabled; no `is_anchor_study` rows appear | adapt |
| BR-6 | Sitting boundaries are enforced by the queue; sitting 2 is unlocked by the lead | order effects, fatigue, and the within/across-sitting split of repeats | a rater at position 146 sees "sitting complete"; the next item opens only after the lead's unlock | build |

### 10.3 Refinement and versions (RR)

| ID | Requirement | Ground | Acceptance | Status |
|---|---|---|---|---|
| RR-1 | REVIEW mode lists the rater's own completed items with their own last grade and rationale; a save requires a reason code and ≥ 10 characters | D6; the old rubric's 10-character floor | saving without a reason code is refused | build |
| RR-2 | REVIEW is opened by the lead per campaign with a scope and a reason; closing it triggers a version | refinement must be a named event, not a permanent state | the manifest of the resulting version names the campaign and its scope | build |
| RR-3 | ADJUDICATE lists studies whose three effective grades disagree; the lead records one grade with rationale | consensus needs a rule for splits | after adjudication the `consensus` view has no "open" rows for that batch | build |
| RR-4 | A rubric change bumps `rubric_version` and opens a REVIEW campaign on a slice | grades under different rubrics must not be silently pooled | κ reports state the rubric version(s) they include | build |
| RR-5 | Batch k's item list includes the 20 drift anchors from batch 1, blind | drift across batches is otherwise unmeasurable | the generator's test asserts the anchors' presence and blindness | build |
| RR-6 | A rater registry with an open set; the lead registers a rater in one command; a new rater receives the onboarding list (§6.5) automatically | D8 | registering a fourth rater produces `item_list_onboarding_<rater>.csv` with 80 positions | build |
| RR-7 | Qualification is computed by the analysis script from the onboarding rows and written to the registry; unqualified raters' rows are stored but excluded from consensus | §6.5 | a synthetic rater below the bar yields `qualified = false` and no effect on `study_labels` | build |

### 10.4 Pack and content (PR)

| ID | Requirement | Ground | Acceptance | Status |
|---|---|---|---|---|
| PR-1 | New items show the five femur frames and five centre-RF frames the freeze served, best-first, captioned "Femur 1–5 / Centre-RF 1–5", plus femur, RF and sweep videos (sweep behind a click) | the served numbers are medians over exactly these frames | the pack's per-item frame list equals the freeze's `top_frame_bases` (r6+) or reproduces its `top_frames` values (r5) | build |
| PR-2 | Old items show their three stills and sweep video, in the same grid | era-consistent presentation | render check on one old item | adapt |
| PR-3 | 54 of 175 new studies have no sweep video; the pack substitutes a six-frame contact sheet and the row records `sweep_source = contact_sheet` | census of `data/studies_root` (readiness audit Q1 extra 11) | those items render without an error | build |
| PR-4 | Every file in the pack is renamed under `study_uid`; a grep for any registry folder-name token over the pack is a release gate | cine and sweep file names carry patient names | the gate script reports 0 hits before deployment | build |
| PR-5 | Pack size ≈ 5.1 GB with sweep videos (1.4 GB without); built on the DGX or locally, never on the server | Hetzner has no GPU and python 3.10 (`docs/DEPLOYMENT_RUNBOOK.md` §1 in the chassis) | rsync of the pack completes; the app imports nothing from the torch stack | build |

### 10.5 Analysis (AR)

| ID | Requirement | Ground | Acceptance | Status |
|---|---|---|---|---|
| AR-1 | `analyze_version.py` computes every §8 endpoint from a version directory alone and writes one results JSON with each number's n | reproducibility; no hand steps | running it twice on v1 yields identical JSON | build |
| AR-2 | κ uses first exposures only; REVIEW, ADJUDICATE and quality-FAIL rows are excluded and counted | §8 | a unit test with a synthetic table checks the row selection | build |
| AR-3 | The pre-registration page for each batch is committed before the first rater logs in, with the checksums | §E2 of the 08-31 report; Akadir approves the page | git log shows the page before the first row's timestamp | process |
| AR-4 | Inter-rater agreement tolerates a different number of raters per item (ordinal Krippendorff's α primary once raters differ; Fleiss on the fully crossed subset) | D8 | a synthetic table with a missing fourth rater on half the items yields α without dropping items | build |

### 10.6 The study label (LR)

| ID | Requirement | Ground | Acceptance | Status |
|---|---|---|---|---|
| LR-1 | `study_labels_v<k>.csv` is derived by the §9.1 rule from the version's rows alone; never hand-edited | D9 | a unit test covers every status branch (3 of 3, 2 of 3, 2 against 2, three-way split, adjudicated, n = 1, n = 2 agree and disagree) | build |
| LR-2 | Every label carries status, `n_qualified_raters`, `agreement`, `rubric_version`, `table_version`, `last_event_id` | a downstream use must know how solid a label is | the CSV schema test | build |
| LR-3 | Every version ships a label changelog naming each changed study and the cause | a reference that changes silently is refused | v2's changelog lists exactly the studies whose label or status differs from v1 | build |
| LR-4 | Downstream comparisons with the pipeline's axes use CONSENSUS and ADJUDICATED labels only | §9.4 | the analysis script's filter is unit-tested | build |

### 10.7 Non-functional (NR)

| ID | Requirement | Ground | Acceptance | Status |
|---|---|---|---|---|
| NR-1 | No patient identifier on any screen, in the table, in the pack, in the logs, or in any committed file; Phase 2 shows age band (5-year), sex, BMI only | `docs/DIRECTION.md` §2; height, weight and comorbidities are quasi-identifiers | grep gates on the pack and the export; screenshot audit | build |
| NR-2 | Runs on the existing Hetzner host: python 3.10, streamlit 1.52.2, no GPU, Traefik basic-auth per rater | the deployed chassis's constraints | `cli doctor` passes on the server | exists / adapt |
| NR-3 | Three concurrent raters without lost writes | SQLite WAL in the chassis | a concurrency test with three writers loses no rows | exists |
| NR-4 | Daily backup of the SQLite file and the CSV export to the repo's data area; a version export is possible at any time | a lost table is a lost round | restore drill once before batch 1 | build |
| NR-5 | The chassis repository is under version control before adaptation starts | today it has no `.git`, no remote, tarball deploys | `git log` exists; PHI-bearing local files (`decision_log.csv`, `configs/study_codes.csv`, `transport/`) are ignored | build |

### 10.8 Operations (OR)

| ID | Requirement | Ground | Acceptance |
|---|---|---|---|
| OR-1 | The lead can: unlock a sitting, open and close a REVIEW campaign, open ADJUDICATE, export a version | §6 | each is one command or one admin button, logged |
| OR-2 | A dry run: the lead grades 10 items including one scheduled repeat on the live URL; export; analysis runs end to end | the readiness audit's step 8 | the dry-run version is discarded and its rows flagged `dry_run` |
| OR-3 | An operator guideline (rubric verbatim, the six new-cohort facts, the four modes) is handed to raters with the pre-registration | continuity with the old round | raters sign that they read it |

## 11 · Surfaced to other verticals, not solved here

Recorded per `docs/DIRECTION.md` §2.

- **Consent and ethics for showing images to raters**, in particular an external third rater (D5): the ethics/consent layer decides; we assume the same standing as the old round until told otherwise.
- **Quasi-identifiers on the Phase 2 screen** (age band, sex, BMI): we minimise by design; the PHI layer confirms the set.
- **Where the server and the pack may live** (Hetzner, EU): unchanged from the old round; the PHI layer confirms.

## 12 · What exists and what is built

| Piece | Exists in the chassis | Built or adapted |
|---|---|---|
| Phase-locked rubric, 3 levels, quality gate, rationale floor | yes (`app.py`) | keep |
| Append-only log, SQLite WAL, CSV export | yes (`state.py`) | new columns (§5.1); modes; versions |
| Transport manifest loader (old studies) | yes (`data.py`) | replaced by a pack loader (new and old) |
| Frame panel and one video | yes | role-grouped panel, three videos, contact-sheet fallback |
| Free navigation, filters, every-15 anchors | yes | replaced by the locked queue; anchors off |
| Metrics from annotator masks at view time | yes | replaced by the served-row panel with withheld state |
| Deploy tree, runbooks, systemd, Traefik | yes | reuse; new release |
| Item-list generator, drift anchors, version export, analysis | no | new scripts under `scripts/grading/` in this repo |
| Pack builder with the freeze receipt | no | new, uses `measure_study` from the freeze script |

## 13 · Effort and phases

| Phase | Delivers | Effort |
|---|---|---|
| A · Batch 1 on r5 | pack builder + receipt; item lists; chassis adaptation (queue, panel, served row, item codes, columns); pre-registration page; reviewer pass; redeploy; dry run | 3–4 sessions (16–24 h), per the readiness audit |
| B · The living table | modes, REVIEW campaign, sitting unlock, version export, drift anchors in the generator, rater registry + onboarding lists + qualification, study-label derivation + changelog, analysis script | about 1.5 sessions (8–12 h) |
| C · Adjudication and consensus release | ADJUDICATE queue, consensus view, v-release note | about 0.5 session (3 h) |
| Raters | two sittings per batch per rater | about 4–5 h each per batch |
| Analysis and report per version | | 0.5 session |

Phase A and B are engineering on the DGX and locally; only the redeploy needs the server. Raters can start after Phase A; Phase B must be in place before the first REVIEW campaign or the first delta batch.

## 14 · Open for sign-off

1. D5, the third rater.
2. The 20 drift anchors: chosen by stratified random draw from batch 1 (my recommendation) or hand-picked.
3. Age shown in Phase 2 as a 5-year band (my recommendation) or exact.
4. Whether Akadir rates in batch 1 as R3 (his grades reported separately either way).
5. The label rule (§9.1): majority of qualified raters with adjudication for splits, as proposed, or a weighted vote.
6. The qualification bar for a new rater (§6.5): κ ≥ 0.6 on both the repeats and the settled items, as proposed.
7. Minimum raters for a CONSENSUS label: three, as proposed.

## Appendix A · The rubric, verbatim from the old round (`labeling_app/app.py:44–57`)

- **PRESERVED**: RF + VI clearly demarcated, homogeneous echo texture, no visible fat infiltration, fascial planes intact.
- **INTERMEDIATE**: Partial fat infiltration visible, slightly heterogeneous echo, tissue boundaries still discernible.
- **POOR**: Significant fat replacement, loss of tissue homogeneity, boundaries poorly defined or absent.

Quality gate: PASS / FAIL. Rationale: at least 10 characters. Grade options in the order shown: PRESERVED, INTERMEDIATE, POOR.

## Appendix B · Sources

- `docs/grading_round_readiness_2026-09-02.gdoc`: the chassis audit, the item-list algorithm (§Q4), the pre-registration draft (§Q5), the effort table (§Q6).
- `docs/reports/2026-08-31_the_meter_face_to_face.html` §E1–E3: the original proposal and the six new-cohort absorptions.
- `docs/NORTHSTAR.md` §1, §2, §5 and `docs/DIRECTION.md` §2, §4: what the grade is for and what it is not for.
- `data/cohort_stats/per_study_cohort_new_r5.csv`, `new_round_landmark_r5.json`, `new_round_cohort_provenance_r5.json`: the r5 pool, its frame evidence and its provenance.
- `scripts/derive_new_round_cohort.py` (`measure_study`, `top_frame_bases`), `scripts/cohort_freeze_log.py`: frame identity per freeze (2026-09-03).
- The deployed chassis `~/dev/muscle_quality_labeling_exercise_claude_PROMPTED`: `labeling_app/app.py`, `state.py`, `data.py`, `deploy/`, the runbooks.

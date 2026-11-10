# Round-3 Data Collection — v5 → v6 Change Spec

*Working worklist. Consolidates: the external auditor's findings, our accuracy/robustness/practicality discussion, the DICOM-prefill investigation, and Akadir's review comments + decisions. **Status: DRAFT — Akadir still reviewing the v5 docs; more changes expected.** v6 will be built as NEW docs (not in-place edits on v5) once the review settles. Created 2026-06-02.*

## 1. Resolved decisions (Akadir, 2026-06-02)

Governing directive: **find the absolute necessary set.** Five operators will push back on burden; a leaner protocol is followed more faithfully → better data. The cuts below are deliberate.

- **Fat/tissue QA → "subcutaneous fat + fascial planes visible" (a technical-gradeability check).** Replaces the ambiguous "fat-muscle contrast." Catches *acquisition* failure (under-gain, blur, poor coupling — can't see tissue structure); does NOT flag *biological* fat-infiltration (a fat-infiltrated poor muscle looks fatty — that's the signal we measure, not a reject). Folds into the existing "fasciae sharp — OLMAZSA OLMAZ" gate.
- **Left/right leg → marker held to the patient's RIGHT (absolute), not "lateral."** "Always lateral" is mirror-symmetric (both legs look identical → L/R unrecoverable). Absolute "patient's right" makes the leg recoverable two independent ways: VL/VM position relative to the marker (segmenter), and sweep-direction relative to the marker. Also a simpler operator rule. Verify the device marker→image mapping + VL/VM segmentation on the pilot.
- **ITB horizontal anchor → DROPPED.** Horizontal position is anchored by good vertical positioning (ASIS-patella 50% mark) + the operator aiming for the LARGEST RF cross-section in frame + the sweep (whose fullest frame IS the anchor). The ITB tape measurement added complexity for redundant value. Removes §5.2, the ITB datasheet column, and the standing ITB ratify.
- **Skin-mark photo → DROPPED.** The ruler photo was a secondary longitudinal/audit aid; actual positioning is the ASIS-patella re-measurement + largest-RF. Removing it lightens marking + kills file-management burden.
- **Case-study re-acquisition → REMOVED from operator instructions (not surfaced).** Within-subject data is harvested opportunistically: the 5 operators scan themselves/each other first → natural repeats, matched post-hoc by patient metadata. (Convenience-sampled, likely preserved-quality; the formal noise floor stays the 24h test-retest.)
- **2/3 anchor → DROPPED** (earlier): algorithmically indistinguishable from an RF still; speculative payoff.
- **Probe → Leltek 128L only** (earlier).

## 2. Speak-up: what the stripping costs, and the one thing NOT to cut

The cuts (2/3, ITB, photo) all lighten the operator — good for adoption. But together they shift *all* longitudinal re-finding onto "re-measure ASIS-patella 50% + aim largest-RF + sweep," with no metric cross-check (ITB) and no visual record (photo). That is **defensible and testable**: the **24-hour test-retest sub-cohort is now the validation** that the simplified anchoring is reproducible enough for longitudinal change. So as the protocol strips down, **keep the 24h test-retest** — it's the safety net that proves the minimal anchoring works (or flags it before Round-4 leans on it). That is the one not to cut.

## 3. Change table (v5 → v6, by artifact)

| # | Change | Artifact(s) | Category |
| :- | :- | :- | :- |
| 1 | Reconcile the decision count to ONE number | Strategy | accuracy |
| 2 | 128L-only: fix probe in §4 settings; default it in the sheet; never mix | Operator, Strategy, Sheet | accuracy |
| 3 | Marker held to patient's-RIGHT (absolute) — replaces "always lateral" | Operator §4, Strategy | accuracy |
| 4 | Drop the 2/3 anchor everywhere | all | simplify |
| 5 | Fat/tissue QA → "subq fat + fascial planes visible" gradeability; fold into the fascia gate | Operator, Sheet, Strategy | accuracy |
| 6 | Drop the ITB horizontal anchor (§5.2 + ITB column + its ratify) | Operator, Sheet, Strategy | simplify/practicality |
| 7 | Drop the skin-mark photo | Operator, Sheet, Strategy | practicality |
| 8 | Case-study re-acquisition removed from operator instructions; harvest natural repeats post-hoc | Operator (remove), Strategy (note) | practicality |
| 9 | Real operator id in `Çekim Operatörü` (not "A/B"); role via suffix | Operator, Sheet | accuracy |
| 10 | Add `Kayıt Tipi` column (baseline / op2 / v2 / reference) | Sheet, Operator | robustness |
| 11 | Add `Export Tam (Y/N)` + Drive-folder field, ticked after upload confirmed | Sheet, Operator | robustness |
| 12 | State one-study-per-patient + fixed capture order + **no display-zoom** (so dy/width deconfliction holds) | Operator | robustness |
| 13 | Document the sweep as the horizontal reference (fullest frame) — now primary, not a backup | Strategy, Operator | robustness |
| 14 | Prefill from DICOM (Tarih, Cinsiyet, Yaş, Operatör); fixed defaults (Preset MSK, Probe 128L) | Sheet/ingestion, Operator | practicality |
| 15 | Two-operator subset n → ~20–25; **same probe** both operators | Strategy, Operator | accuracy |
| 16 | Add the pilot (3–5 real + one mock row per record type) before scaling | Strategy | robustness |
| 17 | Mark the addendum "history only — superseded by v6" | Brief, Strategy | consistency |
| 18 | Update the launch ballot: RAW CINE removed (settled); ITB removed (no longer a gate) | Strategy | consistency |

## 4. DICOM prefill + the minimal manual set

Verified on the backup (60 sampled studies):
- **Prefill from DICOM (build into ingestion):** `Tarih` ← StudyDate · `Cinsiyet` ← PatientSex · `Yaş` ← patient-info middle-field parse · `Çekim Operatörü` ← ScheduledPerformingPhysicianName. All 60/60 populated.
- **NOT in DICOM (confirmed 0/60):** device serial, software version, model, preset, transducer — but these are Round-3 CONSTANTS → set as **fixed defaults** in the sheet (Preset=MSK, Probe=128L); operator flags deviations only (`Settings Per Protocol = N` + note).
- **Net minimal manual work per patient:** position the patient → per-leg sweep+still sequence → the fascia/fat-gradeability gate → lock the row. Almost everything else is prefilled or defaulted.

## 5. Open / more to come
- Akadir still reviewing the v5 docs — expect more changes (this is a living list).
- Pilot before scaling — still on.
- Confirm on pilot: marker→image mapping, VL/VM segmentation reliability, the exact "5"/"7" depth device-steps.
- v6 built as new docs once review settles.

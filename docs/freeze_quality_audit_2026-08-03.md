# Does the freeze exclude bad studies? — audit, 2026-08-03

**Answer: no. Nothing in the freeze consults the operator's quality verdict.**

*Written on the Acer while a parallel session on the DGX audited the same chain. This file is
EVIDENCE ONLY — it contains no fix and touches no pipeline code, deliberately, so that whichever
machine holds the working copy of `ingest_all.py` can act without a merge conflict. Every number
below is reproducible with the command beside it; nothing here needs re-deriving.*

**If you are the DGX session: you do not need to redo this.** Your contract item (`SHEET_ONLY_COLUMNS`
/ "the substrate carries what only the datasheet can supply") catches a *failed sheet read*. That is a
real and different hole from the one below, and both are worth having. Skip to §5 for the part that
is still undecided.

---

## 1. The three cycles, and which one has the gap

| | what it does | what gates it |
|---|---|---|
| **Ingestion** — `ingest.py` | Drive → disk, roles deconflicted | `Dosyalar Tam mı = Evet` — the operator says *files are complete* |
| **Freeze** — `derive_new_round_cohort.py` | segments every v8 study → `per_study_cohort_new.csv` | **nothing quality-related except `int_cv_trusted`** (below) |
| **Substrate** — `build_qa_sheet.py` | registry + disk + live sheet → `qa_master.csv` | nothing; this is where the verdict finally *appears*, as a column |

`check_studies.py` writes a **separate** automated recording verdict (✅/⚠️/❌) into the datasheet.
It is not the same thing as `Dosyalar Tam mı`, and **nothing gates on it**. `ingest_all.py`'s own
docstring says so: *"NOT yet gated, and listed as open in `docs/DATA_LIFECYCLE.md`: a study that is
ingest-ready but carries a ❌ verdict."*

The freeze runs **upstream** of the only place the verdict lands. That is the structural gap.

## 2. What the frozen 116 actually contains

```
✅ clean                                                    84
(no verdict at all)                                         23   ← ingested 2026-08-03, never checked
⚠️ RF hafif derin (5.92cm) / hafif sığ (3.94cm)              6
❌ gerçek RF yok — RF 6.9cm / 7.9cm femur düzlemi            3   ← AKA012, AKA016, AKA017
```

**32 of 116 (28%)** of the placement reference is either unverified or flagged.
The 3 ❌ studies are **not new** — they are present in **r1, r2 and r3**, i.e. since the first freeze
on 2026-07-14. The 23 verdict-less ones are new: `check_studies` was deliberately skipped on 08-03
because it writes into the shared operator datasheet, which is not an agent's call to make.

## 3. The one quality filter the freeze has — and how it failed

`derive_new_round_cohort.py` admits `int_cv` only when `resolve_rf_source`'s **`int_cv_trusted`**
holds (a 3-way agreement between femur / center-RF / sweep); otherwise texture is withheld (blank),
never faked. Across the 116, texture is withheld for 16.

For the three ❌ studies it fired **once**:

| | int_cv | admitted? |
|---|---|---|
| AKA016 | (blank) | withheld — the filter worked |
| AKA012 | 0.605 | **admitted** |
| AKA017 | 1.314 | **admitted** |

**Why it misses:** `int_cv_trusted` asks *"do the three views agree?"*. The operator's ❌ says *"the RF
cine is at the femur plane — there is no real RF here."* A consistently-wrong plane can be internally
consistent, so it passes. The two checks ask different questions and only the weaker one is wired in.

## 4. The consequence, quantified

`int_cv_within` across the 100 studies that contribute texture:

```
min 0.237 · p05 0.406 · p25 0.566 · median 0.703 · p75 0.817 · p95 1.106 · max 1.314
mean 0.718 · sd 0.205
```

**The maximum of the distribution is the study the operator rejected.**

```
rank  study    int_cv   operator verdict
  1   AKA017   1.314    ❌ gerçek RF yok — RF 7.9cm femur düzlemi     z = +2.91, p99
  2   AKA148   1.182    (NO VERDICT — never checked)
  3   AKA055   1.128    ✅ tam
  4   AKA140   1.117    (NO VERDICT — never checked)
  5   AKA053   1.110    ✅ tam
```

Three of the top five texture values come from studies that were flagged or never checked.
`int_cv` carries **43% of the condition score** (Tissue 43 / STAR 33 / Fat 25).

AKA012 sits at p30 (z −0.55), so its admission is far less consequential than AKA017's.

**Independent corroboration for AKA017:** memory `project_capture_association_audit` already flags it —
*"the score's RF is on the wrong capture"* — found on 2026-07-01 from a completely different direction.
The ❌ verdict and that audit are the same fact, twice.

**And the r3 outliers are the unverified batch.** All six studies that r3's freeze log flags as
outliers on ≥4 of 12 features — AKA148, AKA158, AKA132, AKA140, AKA090, AKA128 — carry **no verdict**.
The batch that moved the fat axis hardest is exactly the batch nobody has vouched for. That does not
make them wrong; it means the one signal that would explain them is the signal that was skipped.

## 5. Open — Akadir's calls, not an agent's

1. **Exclude, or withhold — and a third option.** ~~Withhold its *texture*, keeping its geometry, which
   the ❌ does not impugn.~~ **That premise was wrong and it was mine.** The DGX session refuted it with
   data: the three ❌ studies are **ranks 1, 2 and 3 of the FAT distribution** too, and BMI does not
   explain them (residuals +18.66 / +14.27 / +11.20 mm; z = 3.51 / 2.68 / 2.11). Withholding texture
   alone leaves all three anchoring the top of the fat axis, which carries **25%** of the score. The
   third option is **withhold texture AND treat fat as depth-suspect** — but see §5b: exclusion may
   invert the problem rather than fix it, and the direction is not yet known.
2. **Run `check_studies` on the 23?** Until it runs, those studies — including all six r3 outliers —
   have no verdict, so no exclusion rule can even see them. It writes into the shared operator sheet.
3. **AKA017 specifically.** It is simultaneously the ❌, the maximum of the texture distribution, and
   the subject of a standing capture-association finding. Three independent signals, one study.

## 5b. The fat–depth confound: what removing the three studies actually does

*Added from the Acer after the DGX session found the fat–depth relationship. This is the part that
was missing, and it changes the recommendation. Nothing here is a fix; it is an attribution.*

### The three ❌ studies ARE the deep end of the ladder

| study | BMI | femur depth | fat_q50 |
|---|---|---|---|
| AKA016 | 38.3 | **9.9 cm** | 29.20 mm |
| AKA017 | 37.5 | **9.9 cm** | 31.90 mm |
| AKA012 | 27.7 | **8.9 cm** | 31.81 mm |

They are BOTH 9.9 cm captures plus one of the four at 8.9. Cohort BMI: median 24.8, p90 30.7, max 38.3
— so **AKA016 and AKA017 are the two most obese patients in the cohort, at its two deepest captures.**

### Removing them does not fix the confound — it deletes the range that measures it

```
                        BEFORE                        AFTER removing the three
  6.9 cm   n=100   median fat 11.05 mm      6.9 cm   n=100   11.05 mm   (unchanged)
  7.9 cm   n= 10               13.12 mm     7.9 cm   n= 10   13.12 mm   (unchanged)
  8.9 cm   n=  4               18.95 mm     8.9 cm   n=  3   14.60 mm
  9.9 cm   n=  2               30.55 mm     (gone)

  fat ~ femur depth:  r = +0.459   ->   r = +0.142
```

The drop is **range restriction, not repair**. The best-powered step — 6.9 → 7.9 cm, **+18.7%**, n=10 —
is **identical before and after** and involves none of the three. Every remaining patient is still
placed against a fat axis that moves ~19% per depth step; you would simply have discarded the evidence
that it does.

### Per-study attribution — the two obese studies drive it, not the anomalous one

```
  all 116                                r = +0.459
  minus AKA012 only                      r = +0.404     <- barely moves
  minus AKA016 + AKA017 only             r = +0.263     <- halves it
  minus all three                        r = +0.142
```

So "the depth confound" and "AKA012 looks wrong" are **two different questions**:

- **AKA016 / AKA017** — BMI 38.3 and 37.5. 29–32 mm of SAT is entirely plausible at that BMI, and the
  operator went deep because they had to. These support the CENSORING hypothesis: at 6.9 cm the fat
  layer would not fit in the field.
- **AKA012** — BMI **27.7**, near the cohort median, yet **31.81 mm**, the highest in the whole 26–30
  BMI band by 9 mm (next: AKA021 23.45, AKA136 22.10, AKA120 20.01 — all at 6.9 cm). Nearly 3× the
  cohort median on a near-median BMI. **This one may be a measurement, not a patient.**

### Which means the direction question is still open, and exclusion may invert it

If the shallow 6.9 cm view CROPS the fat layer, the deep readings are the accurate ones and the 100
studies at 6.9 cm are censored underestimates. Excluding AKA016/AKA017 would then delete the only
correctly-measured obese patients while leaving 100 under-reporting ones in the reference.

**Neither inspection nor exclusion settles this. Re-measuring one patient at two depths does.** It is a
small acquisition task and it is the only thing that resolves inflation-vs-cropping.

### What IS decidable by looking — AKA012 only

`fat_thickness_q50` = the dominant connected fat component above the muscle, hole-filled; thickness is
the median per-column top-to-bottom extent; **the cyan caliper is drawn at the q50 column showing that
region's own top and bottom**, so the overlay is exactly what was measured (`labeling_app/fat_features.py`).

On p05, for AKA012:
1. **Has the dominant component swallowed something?** At 8.9 cm the field is deep and dark; if the SAT
   merged with a hypoechoic band below it (deep fascia, an intramuscular plane) the blob spans two
   structures and the caliper visibly straddles a boundary. 3× median on a normal BMI is what that looks like.
2. **Do the caliper endpoints land on edges you would point to yourself?**
3. **Compare against AKA021 / AKA136** — same BMI band, 6.9 cm, 22–23 mm. If AKA012 *looks* like them but
   measures 9 mm thicker, the difference is in the segmentation, not the patient.

For AKA016 / AKA017 the question is the opposite — is skin clearly visible above the fat with room to
spare? Then check a high-BMI study at 6.9 cm: **if its fat touches the top edge, the shallow readings
are censored**, and that inverts the whole decision.

⚠ **AKA012 is also the study `CLAUDE.md` names for the PROVE-IT render loop.** We have been verifying
the app against a protocol-failed study. Separate problem, same study.

### 5b-i. Akadir's visual audit of the three (2026-08-03) — the decisive evidence

*Source: Akadir, looking at the three studies in the assessment app. These are OBSERVATIONS, not
inferences, and they settle two things that no amount of correlation could.*

> "I checked and AKA012 is fine. You might think the fat should be comparable to studies like AKA136
> with similar BMI, but age is different. AKA012 is older and fat is yes, fat. In AKA017, shallow view
> is the problem. It affects the fascicular contrast, not the length measurements. So I would say for
> AKA017 fascicular contrast is unreliable. For AKA016, I didn't recognize any issues."

**What each settles:**

| study | verdict says | observed | consequence |
|---|---|---|---|
| AKA012 | ❌ no real RF | fat is **real**; age explains it | keep. The Acer's "segmentation defect" hypothesis is DEAD |
| AKA017 | ❌ no real RF | **contrast unreliable, lengths fine** | the ❌ is AXIS-SPECIFIC — withhold texture, keep geometry |
| AKA016 | ❌ no real RF | no issues | keep — and it is the cohort's OLDEST patient (66) |

**The age point, verified.** The Acer compared on BMI alone and never looked at age. In the 26–30 BMI
band: AKA012 is **BMI 27.7, age 64, fat 31.81 mm** — while AKA136 at the *same* BMI 27.7 but **age 19**
reads 22.10 mm. 45 years apart, 10 mm of fat. Cohort age: median 30, p90 54, max 66. **AKA012 (64) and
AKA016 (66) are the two oldest studies in the cohort.**

**So the three ❌ are three different cases, not one class** — which argues against any blanket rule and
for PER-AXIS withholding: the mechanism `int_cv_trusted` already implements, triggered by the right
signal. It also re-opens the option §5 struck: for AKA017 specifically, texture-out / geometry-in is now
supported by *observation* rather than by the Acer's assumption.

⚠ **Excluding AKA016 would delete the study closest to the clinical range the project says it lacks**
([[project_cohort_coverage_gap]] — the standing problem is nobody in the older, heavier population).

### 5b-ii. Age does NOT rescue the depth confound — it survives everything

The obvious next hypothesis, once age explained AKA012, is confounding-by-indication: the operator goes
deeper for patients who genuinely carry more fat, and BMI is an imperfect proxy for who those are.
**Tested on all 116, and it is wrong:**

```
  fat ~ depth,  raw                     r = +0.459
  fat ~ depth,  controlling BMI         r = +0.354
  fat ~ depth,  controlling BMI + AGE   r = +0.354   <- IDENTICAL. age adds nothing.

  fat ~ age,    controlling BMI         r = -0.014   <- no cohort-wide age effect at all
  depth ~ age,  controlling BMI         r = -0.061
```

Both things are true at once: **age explains AKA012 individually and explains nothing across the
cohort.** So the residual +0.354 stands on its own and is not an adjustment artefact.

**The depth confound is therefore a separate, live problem from the three ❌ studies.** It involves the
whole cohort, its best-powered evidence (6.9 → 7.9 cm, +18.7%, n=10) involves none of the three, and
neither inspection nor exclusion resolves it. **One patient measured at two depths does.**

## 6. Reproduce any of it

```bash
# verdict distribution across the frozen cohort
.venv/bin/python -c "import csv,collections;rows=[r for r in csv.DictReader(open('data/qa_master.csv',encoding='utf-8')) if (r.get('cohort_round') or '')!='old'];print(collections.Counter((r.get('kontrol_verdict') or '(none)').strip() or '(none)' for r in rows))"

# what the ❌ studies contribute
.venv/bin/python -c "import csv;reg={r['study_uid']:r.get('display_code') for r in csv.DictReader(open('data/study_registry.csv',encoding='utf-8'))};c2u={v:k for k,v in reg.items() if v};coh={r['study_code']:r for r in csv.DictReader(open('data/cohort_stats/per_study_cohort_new.csv',encoding='utf-8'))};[print(k,coh.get(c2u[k],{}).get('int_cv_within','')) for k in ('AKA012','AKA016','AKA017')]"

# the freeze log's outlier concentration
.venv/bin/python scripts/cohort_freeze_log.py --report
```

Verified identical on both machines: `per_study_cohort_new.csv` sha256 begins `4a01533526c07c72`,
and all 46 files in `data/cohort_stats/` are byte-identical Acer ↔ DGX.

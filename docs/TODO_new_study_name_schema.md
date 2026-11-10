# TODO — support the new study-name schema (ASIS-patella + probe) in intake + datasheet

**Status:** DONE 2026-06-16, then EVOLVED 2026-06-17 — this doc is the ORIGINAL spec (the
tail-placement `…_ASIS_probe` layout below is now SUPERSEDED). The live design as of 2026-06-17:
- **Canonical format** (logistics up front, patient at the back, count optional):
  `<date>_<count>_<probe>_<soyad>_<ad>_<age><sex>_<boy>_<kilo>_<ASIS><leg>` — e.g.
  `16jun26_012_831_anonadx_anonabm_67k_163_70_041.5r`. Probe moved next to the count; ASIS at the
  tail; an optional imaged-leg letter glued to ASIS (`r`=SAĞ, `l`=SOL) → fills Dominant Bacak.
- Parsing CONSOLIDATED into one grammar `scripts/preflight_helpers/study_name_grammar.py`
  (probe found by value; intake/app/in-sheet/viewer are adapters/mirrors). Committed `757137b`.
- Manual operator dropdowns default to **`Seçiniz`** (replacing the empty default) — readers use
  `datasheet_schema.is_unanswered()`.
See HANDOFF.md (top two banners) + the auto-memory `project_new_study_name_schema` for the current
as-built notes, edge cases, and deploy steps. NOT yet pushed to the live sheet.
**Owner action (done):** Akadir asked 2026-06-16/17; built same sessions.

## Why

The study `.zip` / folder naming format changed (more fields encoded). **Old and new
coexist** — older studies keep the old names, so the parser must accept BOTH. Two extra
fields are now appended: **ASIS–patella distance** and **probe number**. The datasheet
must (a) keep auto-filling the existing fields off `Study Adı`, and (b) additionally
fill the **ASIS–patella** and **Probe No** fields when the name carries them — *without
removing the old behavior* (old names that lack these fields must still work).

## The two schemas

```
OLD: date _ [id] _ name… _ AGEsex _ height _ weight
     10jun26_001_anonaam_anonacu_25e_174_70
NEW: date _ id _ name… _ AGEsex _ height _ weight _ ASIS-patella _ probe
     16jun26_012_anonadx_anonabm_67k_163_70_041.5_831
```

- Underscores delimit features; **SEX rides with AGE** in one token: `67k` = 67y female
  (kadın), `25e` = 25y male (erkek).
- Field order after AGEsex: **height, weight, ASIS-patella, probe**.
- New probe values include **`831`** (the live Probe No dropdown only lists 829/830 — add 831).

## Parse rules (exactly as implemented in the viewer)

1. **Anchor on the AGEsex token** (`^\d{1,3}[eEkKmMfF]$`) — it's unambiguous. Everything
   before it (after the date) is id (a numeric token right after the date) + name
   (alphabetic tokens, Unicode-aware so Turkish çığöşü pass). Everything after it is the
   positional numeric run: height, weight, ASIS-patella, probe.
2. **Decimals are kept ONLY for ASIS-patella.** Every other numeric is floored
   (`int(float(anonadn))`). Operators misplace the dot — this already happened on disk:
   `16jun26_011_anonace_anonacn_50e_175_85.5_045_829` has **weight `85.5` → must floor to 85**;
   without flooring the value is corrupted (the viewer bug this fixed showed the ASIS
   value as the weight). ASIS may legitimately be integer-valued (`048` → 48.0) or
   decimal (`041.5` → 41.5); keep whatever it is.
3. Missing trailing fields → leave them `None`/blank (old names have no ASIS/probe).

**Reference implementation (done + unit-tested):**
`/home/akadir/Downloads/june_protocol_goruntuler/_viewer/discovery.py` →
`parse_study_name()` and its tests in `_viewer/test_smoke.py::test_name_parsing_both_schemas`.
Mirror its logic; it already handles every real folder on disk.

## What to change in muscle_mania (file by file)

1. **`scripts/preflight_helpers/study_metadata.py` → `parse_folder_demographics()`**
   — extend to also return `asis_patella` (float, decimal kept) and `probe` (int, floored),
   using the AGEsex-anchored positional read above. Keep all existing fields
   (study_adi / cinsiyet / boy_cm / kilo_kg / yaş) unchanged for old names.

2. **`scripts/preflight_helpers/datasheet_schema.py`**
   - Add **`"831"`** to the `Probe No` dropdown `options` (currently `("829","830")`,
     `allow_other=True`).
   - Flow the parsed values into the sheet **without breaking old rows**: fill `Probe No`
     and **BOTH** `ASIS-Patella Sol (cm)` + `ASIS-Patella Sağ (cm)` (the single value into
     both — see the decision below) **from the parse when present, else leave blank for
     manual entry** (preserve the current `source="manual"` behavior as the fallback). Wire
     this into `_FILENAME_VALUE` / `build_row` so a new-schema row auto-fills and an
     old-schema row stays manual.

3. **`scripts/sync_records.py`** — after the schema change, push it to the LIVE sheet
   non-destructively: `python scripts/sync_records.py --sync-structure` (adds columns /
   repairs validations, never clears a value). `Study Adı` stays the idempotency key.

## ASIS-patella mapping — DECIDED (Akadir 2026-06-16)

The folder encodes **one** ASIS-patella value, but the datasheet has **two** columns
(`ASIS-Patella Sol (cm)` and `ASIS-Patella Sağ (cm)`) and the name doesn't say which leg.
**Decision: write the single parsed value into BOTH columns.** Rationale: it's the safe
bet — the value is never dropped, and there are downstream tools to determine which leg
it actually belongs to. Implementation note: when that differentiation later runs, it
should resolve/clear the non-applicable column so the duplicated value isn't mistaken for
two independent measurements.

## Pointers

- Datasheet: https://docs.google.com/spreadsheets/d/1REDACTEDDRIVEID0000000000000000000000000000/edit
- Intake architecture context: the auto-memory `project_v7_intake_architecture`.
- Real new-schema folders to test against (already on disk in the viewer repo):
  `16jun26_009…_045.5_829`, `…_010…_048_829`, `…_011…_85.5_045_829`, `…_012…_041.5_831`.

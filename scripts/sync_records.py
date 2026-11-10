#!/usr/bin/env python3
"""Round-3 intake — the single CLI that 'triggers all' (Görüntü Alma Kılavuzu v7, EK-2).

One run: scan every operator subfolder under `goruntuler/` for `.zip` studies, skip any
already in the `records_list` ledger, and for each NEW zip — count its artifacts
(mp4 / RawImages folders / dcm), write the row's id + operator + artifacts + editable
defaults to `hasta_kayit_v7` (fill-missing-only), and register it in `records_list`.

The id-derived demographics (Tarih, Ad, Soyad, Cinsiyet, Yaş, Boy, Kilo, BMI, Sıra) are
LIVE sheet formulas keyed off the Study Adı cell — set up by `--setup`. So a row auto-fills
the instant the id is present, whether an operator typed it or this CLI wrote it; the CLI
never touches those columns.

  python scripts/sync_records.py --setup --apply     # one-time: build structure + formulas
  python scripts/sync_records.py                      # dry-run: show what intake WOULD write
  python scripts/sync_records.py --apply              # do it
  python scripts/sync_records.py --operator anonacl --limit 1 --apply

Default is DRY-RUN. All Drive/Sheets I/O goes through the authenticated `gws`.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for `preflight_helpers`

from preflight_helpers import datasheet_schema as ds
from preflight_helpers import gws_io as io
from preflight_helpers.study_artifacts import count_zip_artifacts
from preflight_helpers.study_filename import StudyFilenameError, parse_study_filename
from sync_force_measures import _norm   # the shared normalized key (collapses '__' typo / case / Turkish fold)

REPO_ROOT = Path(__file__).resolve().parents[1]
RECORDS_LIST_HEADERS = ["Study Adı", "Operator", "Drive File ID", "Boyut (bytes)",
                        "Artifacts (mp4/RawImages/dcm)", "İşlenme Zamanı", "hasta_kayit İşlendi"]
LAST_COL = ds.col_letter(len(ds.HEADERS) - 1)  # now "AC" (29 cols) — derived, tracks the schema
# The whole non-key middle — everything between Study Adı and the Kontrol verdict — folded into ONE
# collapsed column group, so the operator tablet defaults to the minimal Study Adı + Kontrol + force
# and expands the block (its + toggle) to fill. ONE group spanning Hasta Adı … Dosyalar Tam mı (the
# auto-derived demographics AND the judgment + acquisition dropdowns): Google Sheets MERGES two
# adjacent same-depth column groups into one, so a separate demographics-only + judgment-only PAIR
# isn't possible without a column reorder — one fold over the whole block is the clean, achievable
# view. By HEADER (not index) so it tracks the schema. (List form keeps the multi-fold builder.)
COLLAPSE_FOLDS = (
    ("Hasta Adı", "Dosyalar Tam mı"),
)


def load_config(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def locale_sep(meta: dict) -> str:
    """tr_TR sheets use ';' as the formula arg separator (',' is the decimal mark)."""
    loc = (meta.get("properties", {}) or {}).get("locale", "") or ""
    return ";" if loc.lower().startswith("tr") else ","


# ---------- structure ----------
def _structure_requests(sheet_id: int, operators, sw_versions=()) -> list:
    """Freeze header + dropdown validations + plain-text format for the literal columns.
    Operator + Software Version dropdown options are seeded from config (rest are static)."""
    reqs = [{"updateSheetProperties": {
        "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
        "fields": "gridProperties.frozenRowCount"}}]
    dynamic = {"Operator": list(operators), "Software Version": [v for v in sw_versions if v]}
    for i, col in enumerate(ds.COLUMNS):
        opts = dynamic.get(col.header, list(col.options))
        if not opts:
            continue
        # Operator-editable dropdowns get a 'Seçiniz' default row + strict=False so a mis-pick can
        # be re-picked/cleared; the schema owns that policy (datasheet_schema.dropdown_values).
        values, strict = ds.dropdown_values(col, tuple(opts))
        reqs.append({"setDataValidation": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 1000,
                      "startColumnIndex": i, "endColumnIndex": i + 1},
            "rule": {"condition": {"type": "ONE_OF_LIST",
                                   "values": [{"userEnteredValue": o} for o in values]},
                     "showCustomUi": True, "strict": strict}}})
    for i, col in enumerate(ds.COLUMNS):  # text-format literals (NOT the formula columns)
        if col.header not in ds.TEXT_FORMAT_HEADERS:
            continue
        reqs.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 1000,
                      "startColumnIndex": i, "endColumnIndex": i + 1},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "TEXT"}}},
            "fields": "userEnteredFormat.numberFormat"}})
    return reqs


def _protection_requests(sheet_id: int, owner_email: str, existing_ids) -> list:
    """Clear any existing protections, then guard the auto-formula columns two ways:

    (1) HARD-LOCK every auto column to the owner (warningOnly=False, editors=[owner]) so a
        non-owner OPERATOR can't type into a spilled cell and break the ARRAYFORMULA (the #REF!
        corruption). Study Adı + the manual dropdowns stay freely editable.
    (2) A WARNING-ONLY guard over just the ANCHOR ROW (sheet row 2 = 0-based index 1) of each auto
        column. The owner is an editor on the hard-locks, so they BYPASS (1) silently — which is
        exactly how the row-2 anchors once got wiped by an accidental Delete on the first data row:
        each auto column is a SINGLE ARRAYFORMULA living in row 2 whose output spills down, so
        clearing that one cell blanks the entire column. warningOnly ranges carry NO editor list,
        so this prompts EVERYONE — owner included — with an 'edit anyway?' confirm before the anchor
        can be cleared. It overlaps (1); on an overlap Sheets enforces the stricter rule, so for an
        operator the hard block still wins on row 2 and this only ADDS a confirm for the
        otherwise-exempt owner (the one the incident proved unprotected).

    With no owner_email we return NO requests at all (not even the deletes): deleting the
    existing protections without re-adding any would silently UNPROTECT the formula columns —
    the opposite of this function's intent — so a missing owner_email leaves protection as-is."""
    if not owner_email:
        return []
    reqs = [{"deleteProtectedRange": {"protectedRangeId": pid}} for pid in existing_ids]
    for start, end in ds.auto_column_ranges():  # (1) hard-lock the whole auto column to the owner
        reqs.append({"addProtectedRange": {"protectedRange": {
            "range": {"sheetId": sheet_id, "startRowIndex": 0, "endRowIndex": 1000,
                      "startColumnIndex": start, "endColumnIndex": end},
            "description": "Otomatik (formül) sütun — elle değiştirmeyin",
            "warningOnly": False,
            "editors": {"users": [owner_email]}}}})
    for start, end in ds.auto_column_ranges():  # (2) anchor-row seatbelt — warns even the owner
        reqs.append({"addProtectedRange": {"protectedRange": {
            "range": {"sheetId": sheet_id, "startRowIndex": 1, "endRowIndex": 2,
                      "startColumnIndex": start, "endColumnIndex": end},
            "description": "Formül çapası (satır 2) — silmeyin: tüm sütunu boşaltır",
            "warningOnly": True}}})
    return reqs


def _column_group_requests(sheet_id: int, existing) -> list:
    """Fold each COLLAPSE_FOLDS block into a collapsed column group, so the tablet shows the minimal
    Study Adı + Kontrol + force by default (expand a block via its + toggle to fill it). Idempotent:
    delete EVERY existing column group FIRST (an addDimensionGroup over an already-grouped span stacks
    a second, deeper group instead of replacing it), then add + collapse each block. The blocks are
    disjoint + adjacent, so they land as sibling groups (each its own toggle). `existing` = the prior
    groups from io.sheets_column_groups(); pass [] on a fresh sheet."""
    reqs = [{"deleteDimensionGroup": {"range": {"sheetId": sheet_id, "dimension": "COLUMNS",
             "startIndex": g["startIndex"], "endIndex": g["endIndex"]}}} for g in existing]
    for start_h, end_h in COLLAPSE_FOLDS:
        rng = {"sheetId": sheet_id, "dimension": "COLUMNS",
               "startIndex": ds.col_index(start_h), "endIndex": ds.col_index(end_h) + 1}
        reqs.append({"addDimensionGroup": {"range": rng}})
        reqs.append({"updateDimensionGroup": {
            "dimensionGroup": {"range": rng, "depth": 1, "collapsed": True},
            "fields": "collapsed"}})
    return reqs


def setup_sheets(cfg: dict, apply: bool, force: bool = False) -> None:
    hk_id, rl_id = cfg["sheets"]["hasta_kayit"], cfg["sheets"]["records_list"]
    hk_meta = io.sheets_meta(hk_id)
    hk_props = io.first_sheet(hk_meta)
    hk_title, hk_sid = hk_props["title"], hk_props["sheetId"]
    rl_props = io.first_sheet(io.sheets_meta(rl_id))
    rl_title, rl_sid = rl_props["title"], rl_props["sheetId"]
    sep = locale_sep(hk_meta)
    formulas = ds.array_formulas(sep)

    print(f"[setup] hasta_kayit '{hk_title}': {len(ds.HEADERS)} cols; {len(formulas)} id-derived "
          f"formula columns (auto-fill off Study Adı); dropdowns; frozen header")
    print(f"[setup] records_list '{rl_title}': {len(RECORDS_LIST_HEADERS)} cols")
    if not apply:
        print("[setup] DRY-RUN — pass --apply to write structure")
        return
    existing = io.sheets_values_get(hk_id, f"{hk_title}!A2:C2")
    if existing and any((c or "").strip() for c in existing[0]) and not force:
        print(f"[setup] ABORT — the sheet already has data rows, and --setup CLEARS A1:{LAST_COL}1000 "
              "(it would destroy them). Use a surgical structure update instead, or re-run with "
              "--force only to deliberately rebuild from scratch.")
        return
    io.sheets_values_clear(hk_id, f"{hk_title}!A1:{LAST_COL}1000")
    io.sheets_values_update(hk_id, f"{hk_title}!A1", [list(ds.HEADERS)])
    for header, formula in formulas.items():
        cell = f"{hk_title}!{ds.col_letter(ds.col_index(header))}2"
        io.sheets_values_update(hk_id, cell, [[formula]])
    ops, op_notes = resolve_operators(cfg)
    for n in op_notes:
        print(f"  [operators] {n}")
    io.sheets_batch_update(hk_id, _structure_requests(hk_sid, ops,
                                                       [cfg.get("software_version", "")]))
    prot = _protection_requests(hk_sid, cfg.get("owner_email", ""),
                                io.sheets_protected_range_ids(hk_id, hk_sid))
    if prot:
        io.sheets_batch_update(hk_id, prot)
        print(f"[setup] protected {len(ds.auto_column_ranges())} auto-column range(s) -> "
              f"{cfg.get('owner_email','')} only")
    io.sheets_values_clear(rl_id, f"{rl_title}!A1:Z1000")
    io.sheets_values_update(rl_id, f"{rl_title}!A1", [RECORDS_LIST_HEADERS])
    rl_reqs = [{"updateSheetProperties": {
        "properties": {"sheetId": rl_sid, "gridProperties": {"frozenRowCount": 1}},
        "fields": "gridProperties.frozenRowCount"}}]
    for j in range(len(RECORDS_LIST_HEADERS)):  # text-format so "8/8/8" isn't date-coerced
        rl_reqs.append({"repeatCell": {
            "range": {"sheetId": rl_sid, "startRowIndex": 1, "endRowIndex": 1000,
                      "startColumnIndex": j, "endColumnIndex": j + 1},
            "cell": {"userEnteredFormat": {"numberFormat": {"type": "TEXT"}}},
            "fields": "userEnteredFormat.numberFormat"}})
    io.sheets_batch_update(rl_id, rl_reqs)
    print("[setup] applied.")


def sync_structure(cfg: dict, apply: bool) -> None:
    """NON-DESTRUCTIVE structure update — the safe way to push a schema change onto a LIVE
    sheet that already holds operator data. Writes the header row, refreshes dropdown
    validations + text-formats (replacing any stale rule — e.g. the Software Version one), and
    freezes the header. It NEVER clears a value, so it adds columns and repairs validations
    without touching a single data cell. Use this instead of --setup once the sheet has rows."""
    hk_id = cfg["sheets"]["hasta_kayit"]
    hk_meta = io.sheets_meta(hk_id)
    hk_props = io.first_sheet(hk_meta)
    hk_title, hk_sid = hk_props["title"], hk_props["sheetId"]
    last = ds.col_letter(len(ds.HEADERS) - 1)
    sep = locale_sep(hk_meta)
    print(f"[sync-structure] {hk_title}: header A1:{last}1 ({len(ds.HEADERS)} cols incl "
          f"'Kontrol Sonucu'); refresh the row-2 id-derived ARRAYFORMULAs (so a parser/schema fix "
          f"reaches the live sheet); refresh dropdowns (operator dropdowns -> non-strict + a "
          f"'Seçiniz' default row the operator must pick / re-pick to clear; Probe No adds 831) + "
          f"text-format; freeze header. NO data cleared.")
    if not apply:
        print("[sync-structure] DRY-RUN — pass --apply to write structure")
        return
    io.sheets_values_update(hk_id, f"{hk_title}!A1", [list(ds.HEADERS)])
    # Refresh the row-2 ARRAYFORMULAs. NON-destructive: these AUTO columns are formula-owned
    # (the operator can't type into them), so rewriting row 2 just re-spills the corrected
    # formula over the data rows — it touches no operator-entered cell. This is how a parser fix
    # (e.g. the AGEsex-anchored demographics regex for the new schema) actually lands on the live
    # sheet; `--setup` would do it too but CLEARS all data, so it's unusable once rows exist.
    for header, formula in ds.array_formulas(sep).items():
        cell = f"{hk_title}!{ds.col_letter(ds.col_index(header))}2"
        io.sheets_values_update(hk_id, cell, [[formula]])
    ops, op_notes = resolve_operators(cfg)
    for n in op_notes:
        print(f"  [operators] {n}")
    io.sheets_batch_update(hk_id, _structure_requests(hk_sid, ops,
                                                      [cfg.get("software_version", "")]))
    prot = _protection_requests(hk_sid, cfg.get("owner_email", ""),
                                io.sheets_protected_range_ids(hk_id, hk_sid))
    if prot:
        io.sheets_batch_update(hk_id, prot)
    io.sheets_batch_update(hk_id, _column_group_requests(hk_sid, io.sheets_column_groups(hk_id, hk_sid)))
    print(f"[sync-structure] applied (no data touched): header + {len(ds.array_formulas())} "
          f"row-2 formulas + dropdowns + protection + the middle fold (demographics + judgment).")


# ---------- read state ----------
def read_processed(cfg: dict) -> set:
    rl_id = cfg["sheets"]["records_list"]
    title = io.first_sheet(io.sheets_meta(rl_id))["title"]
    rows = io.sheets_values_get(rl_id, f"{title}!A1:A1000")
    return {r[0].strip() for r in rows[1:] if r and r[0].strip()}


def read_existing_hasta(cfg: dict):
    hk_id = cfg["sheets"]["hasta_kayit"]
    title = io.first_sheet(io.sheets_meta(hk_id))["title"]
    values = io.sheets_values_get(hk_id, f"{title}!A1:{LAST_COL}1000")
    header = values[0] if values else list(ds.HEADERS)
    try:
        key_idx = header.index(ds.KEY_COLUMN)
    except ValueError:
        key_idx = ds.col_index(ds.KEY_COLUMN)
    study_to_row, study_to_vals = {}, {}
    for r_off, row in enumerate(values[1:], start=2):
        if len(row) <= key_idx or not row[key_idx].strip():
            continue
        study = row[key_idx].strip()
        # Key by the SHARED normalized name (not the raw string) so a Drive-filename typo — e.g.
        # anonabb 'Anonaco__35e' vs this row's 'Anonaco_35e' — resolves to UPDATE, never a duplicate NEW.
        study_to_row[_norm(study)] = r_off
        study_to_vals[_norm(study)] = {h: (row[i] if i < len(row) else "") for i, h in enumerate(header)}
    return title, len(values), study_to_row, study_to_vals


def resolve_operators(cfg: dict) -> tuple[list[str], list[str]]:
    """Return (operators, notes) for the sheet's Operator dropdown — derived, not hard-coded.

    **The Drive layout IS the roster** (Akadir 2026-07-30): `goruntuler/<operator>/` is where each
    operator deposits studies, so every subfolder name is an operator — including one that is still
    empty because they have not uploaded yet. Enumerating the folders is therefore the capture-
    without-fail mechanism: a new operator appears the moment their folder does, with no config
    edit. `anonaav` shipped 13 studies while missing from the config and got no dropdown entry;
    `anonacr` and `anonade` were empty and would have been missed by a has-studies test.

    The roster is Drive ∪ {operators already used by existing rows}. The in-use union is the
    safety catch: it can never invalidate a row that is already on the sheet, whoever created it.
    `cfg["operators"]` is only a SEED — it is the offline fallback when Drive is unreachable, and a
    seed name with neither a folder nor any rows is deliberately DROPPED rather than kept alive
    (that is how `anonaad` and `anonaby`, both long gone, stay out; the live sheet's own dropdown
    already excluded them, so honouring the seed here would have re-added two departed operators).
    """
    seed = {o for o in (cfg.get("operators") or ()) if o}
    notes: list[str] = []
    try:
        subfolders = io.drive_list_children(cfg["drive"]["goruntuler_folder"], folders_only=True)
    except Exception as e:                                   # noqa: BLE001 - Drive is optional here
        notes.append(f"Drive unreachable ({e}); Operator dropdown = config seed only")
        return sorted(seed), notes
    roster = {f["name"] for f in subfolders}
    for name in sorted(roster - seed):
        notes.append(f"operator '{name}' self-registered from its Drive folder")

    in_use: set[str] = set()
    try:
        in_use = _operators_in_use(cfg)
    except Exception as e:                                   # noqa: BLE001 - advisory only
        notes.append(f"could not read in-use operators ({e}); roster = Drive folders only")
    for name in sorted(in_use - roster):
        notes.append(f"operator '{name}' has no Drive folder but existing rows use it — kept "
                     f"so those rows stay valid")
    for name in sorted(seed - roster - in_use):
        notes.append(f"config-seed operator '{name}' has no Drive folder and no rows — not "
                     f"offered in the dropdown")
    return sorted(roster | in_use), notes


def _operators_in_use(cfg: dict) -> set[str]:
    """Operator values already present on the datasheet — never invalidate an existing row.

    Indexes the column BY HEADER NAME rather than slicing an A1 range: a single-column range makes
    the caller depend on the API returning exactly that column, and the first version of this
    read column A and reported the header 'Sıra' as an operator.
    """
    hk_id = cfg["sheets"]["hasta_kayit"]
    title = io.first_sheet(io.sheets_meta(hk_id))["title"]
    vals = io.sheets_values_get(hk_id, f"{title}!A1:{LAST_COL}1000") or []
    if not vals or "Operator" not in vals[0]:
        return set()
    i = vals[0].index("Operator")
    out: set[str] = set()
    for r in vals[1:]:
        v = (r[i] if len(r) > i else "").strip()
        if v and v != "Seçiniz":
            out.add(v)
    return out


# ---------- sync ----------
def sync(cfg: dict, apply: bool, *, operator_filter=None, limit=None, do_download=True) -> int:
    hk_id = cfg["sheets"]["hasta_kayit"]
    processed = read_processed(cfg)
    hk_title, n_rows, study_to_row, study_to_vals = read_existing_hasta(cfg)
    next_new_row = n_rows + 1 if n_rows else 2

    subfolders = io.drive_list_children(cfg["drive"]["goruntuler_folder"], folders_only=True)
    known = set(cfg["operators"])
    plans, ledger_adds, warnings, errors = [], [], [], []

    for fol in subfolders:
        operator = fol["name"]
        if operator_filter and operator != operator_filter:
            continue
        zips = io.drive_list_children(fol["id"], zips_only=True)
        # Every goruntuler subfolder IS an operator — see resolve_operators(). The config list is a
        # SEED, so an unlisted folder is a new operator to register, never a folder to skip.
        if operator not in known:
            warnings.append(f"operator '{operator}' not in the config seed — self-registered from "
                            f"its Drive folder ({len(zips)} study zip(s))")
        for zf in zips:
            study = Path(zf["name"]).stem
            if study in processed:
                continue
            if limit is not None and len(plans) >= limit:
                break
            try:
                parsed = parse_study_filename(zf["name"], name_order=cfg.get("name_order", "soyad_ad"))
            except StudyFilenameError as e:
                errors.append(f"{operator}/{zf['name']}: {e}")
                continue
            for w in parsed.warnings:
                warnings.append(f"{study}: {w}")

            artifacts = ""
            if do_download:
                rel = f"{cfg['cache_dir']}/{study}.zip"
                if not (REPO_ROOT / rel).exists():
                    io.drive_download(zf["id"], rel)
                counts = count_zip_artifacts(str(REPO_ROOT / rel))
                artifacts = counts.as_str()
                if counts.excluded:
                    warnings.append(f"{study}: excluded {counts.excluded} trashed/hidden artifact(s)")

            key = _norm(study)   # resolve against the datasheet by the shared normalized key
            mode = "UPDATE" if key in study_to_row else "NEW"
            row_no = study_to_row.get(key, next_new_row)
            if mode == "NEW":
                next_new_row += 1
            built = ds.build_row(parsed, operator, artifacts, row_1based=row_no)
            plans.append({"mode": mode, "row": row_no, "operator": operator, "study": study,
                          "built": built, "existing": study_to_vals.get(key, {}), "artifacts": artifacts})
            ledger_adds.append([study, operator, zf["id"], zf.get("size", ""), artifacts,
                                datetime.now().isoformat(timespec="seconds"), "Evet"])

    _print_plan(plans, warnings, errors, processed)
    if apply and plans:
        wrote = 0
        for p in plans:
            for header, val in ds.cli_cells(p["built"]).items():
                if p["mode"] == "UPDATE" and (p["existing"].get(header, "") or "").strip():
                    continue  # fill-missing: never clobber an operator's entry
                if not str(val).strip():
                    continue  # nothing name-encoded for this column (e.g. ASIS/probe, old schema)
                io.sheets_values_update(hk_id, f"{hk_title}!{ds.col_letter(ds.col_index(header))}{p['row']}", [[val]])
                wrote += 1
        rl_id = cfg["sheets"]["records_list"]
        rl_title = io.first_sheet(io.sheets_meta(rl_id))["title"]
        rl_next = len(io.sheets_values_get(rl_id, f"{rl_title}!A1:A1000")) + 1
        io.sheets_values_update(rl_id, f"{rl_title}!A{rl_next}", ledger_adds)
        print(f"\n[apply] wrote {wrote} cell(s) across {len(plans)} study row(s) "
              f"+ {len(ledger_adds)} ledger entr(ies). Demographics fill via the sheet formulas.")
    elif plans:
        print("\n[dry-run] no writes. Re-run with --apply to commit.")
    return len(plans)


def _print_plan(plans, warnings, errors, processed):
    print(f"\n=== INTAKE PLAN ===  already-processed: {len(processed)}  |  new/updated: {len(plans)}")
    for p in plans:
        b = p["built"]
        print(f"  [{p['mode']}] row {p['row']}  {p['operator']}/{p['study']}  "
              f"=> {b['Hasta Adı']} {b['Hasta Soyadı']}, {b['Cinsiyet']}/{b['Yaş']}, "
              f"{b['Boy (cm)']}/{b['Kilo (kg)']}, artifacts {p['artifacts'] or '-'}  "
              f"(demographics via formula)")
    for w in warnings:
        print(f"  [warn] {w}")
    for e in errors:
        print(f"  [ERROR] {e}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Round-3 study intake → hasta_kayit_v7 + records_list (v7 EK-2).")
    ap.add_argument("--config", default="configs/round3_intake.json")
    ap.add_argument("--setup", action="store_true", help="(re)build sheet structure + id-derived formulas (DESTRUCTIVE: clears data)")
    ap.add_argument("--sync-structure", action="store_true", help="non-destructive: push schema (headers/validations) onto a live sheet, no data cleared")
    ap.add_argument("--force", action="store_true", help="allow --setup to clear a sheet that has data rows")
    ap.add_argument("--apply", action="store_true", help="perform writes (default: dry-run)")
    ap.add_argument("--operator", default=None, help="restrict to one operator subfolder")
    ap.add_argument("--limit", type=int, default=None, help="process at most N new studies")
    ap.add_argument("--no-download", action="store_true", help="skip artifact counting (no zip download)")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    if args.setup:
        setup_sheets(cfg, apply=args.apply, force=args.force)
    if args.sync_structure:
        sync_structure(cfg, apply=args.apply)
        return 0
    sync(cfg, apply=args.apply, operator_filter=args.operator,
         limit=args.limit, do_download=not args.no_download)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

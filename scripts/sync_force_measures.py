#!/usr/bin/env python
"""Join the force/strength datasheet into hasta_kayit's 5 force columns (Y–AC).

Source of truth = a SEPARATE force Google Sheet (config `sheets.force_measures`; its first tab):
col A = Study Adı (same format as kayit), cols B–F = isometric peak torque, isometric CoV, isokinetic
(konsantrik) peak torque, isokinetic average power, isokinetic CoV. Each row is matched to a kayit row by a
NORMALISED Study Adı (strip + casefold — the force sheet carries trailing newlines and capital-letter
variants like 'Anonadp'/'anonabb' that an exact match would miss). Then FILL-MISSING: a force value
is written only into a kayit cell that is currently blank — an existing value is never clobbered
(mirrors datasheet_schema.merge_fill_missing / sync_records). Dry-run by default; --apply writes via
gws with valueInputOption=RAW so a value like 26.8 / 3 stays literal in the text-formatted cell.

This is also where the MEASUREMENT GATE runs (DATA_LIFECYCLE §7 item 8, closed 2026-08-12):
`check_studies` validates recordings; nothing validated the dynamometer NUMBERS, and two kgf·m-as-Nm
entries sat in the criterion for weeks. Every pass evaluates the POST-SYNC state of every kayit row
carrying a criterion value — including values typed straight into kayit, never synced — against
`torque_plausibility`'s tiered rules. Under --apply, fresh findings are recorded in the flag→kill
ledger (`data/kill_list.jsonl`; hard tier = held out of new analyses by default) and the
operator-facing 'Ölçüm Kontrol' column is written surgically (changed cells only) so the operator
sees the flag — and any human kill/clear verdict (scripts/measurement_verdict.py) — within one pass.

  python scripts/sync_force_measures.py            # dry-run report (no writes)
  python scripts/sync_force_measures.py --apply    # write the blanks + record flags + the check column
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from preflight_helpers import datasheet_schema as ds

# Force-sheet value columns B–F (after the Study Adı key in col A), in order, -> kayit header. Must
# match datasheet_schema.COLUMNS exactly (guarded by tests/preflight/test_sync_force_measures.py).
# Each mode's CoV sits right after its torque (grouped 2026-07-01 at Dr. M's request), mirroring the
# datasheet. "konsantrik" (concentric) = the isokinetic CON/CON test; anonabb reads isokinetic
# CoV 26.8 vs isometric CoV 7.1 — isometric is the more reproducible mode, so its CoV is typically low.
FORCE_VALUE_HEADERS = (
    "İzometrik Tepe Tork (Nm)",        # B  izometrik peak tork
    "İzometrik CV (%)",                # C  izometrik CoV
    "İzokinetik Tepe Tork Ekst (Nm)",  # D  konsantrik peak tork
    "İzokinetik Ort. Güç Ekst (W)",    # E  konsantrik average power
    "İzokinetik CV (%)",               # F  CoV (isokinetic)
)

# The force sheet's header row MUST equal this (col A = the Study Adı key, cols B..F = the value headers
# in order) — its names were aligned to the datasheet 2026-07-01. The join is POSITIONAL, so a source
# rename / reorder / inserted column would silently mis-map values into the wrong datasheet column;
# `assert_force_header` turns that into a loud, clear error before any write.
EXPECTED_FORCE_HEADER = ("Study Adı",) + FORCE_VALUE_HEADERS


def assert_force_header(header_row) -> None:
    """Raise ValueError unless the force-sheet header row (cols A..F) equals EXPECTED_FORCE_HEADER.
    Extra columns past F are ignored; only A..F are contract-checked."""
    got = [(h or "").strip() for h in (header_row or [])][:len(EXPECTED_FORCE_HEADER)]
    if got != list(EXPECTED_FORCE_HEADER):
        raise ValueError(
            "force sheet header row does not match the datasheet force columns.\n"
            f"  expected A..F: {list(EXPECTED_FORCE_HEADER)}\n"
            f"  got      A..F: {got}\n"
            "Fix the force sheet header row so it equals the datasheet's force column names.")


# Turkish letters -> ASCII, so the SAME patient joins whether a name was typed with the real letter,
# its ASCII stand-in, or a different case. The same name diverges across two sheets for two reasons:
# str.casefold() is not Turkish-aware ('ANONACB'->'i̇pek', 'ANONACG'->'anonacg'), AND operators ASCII-type the
# accents ('Anonacq' for 'Anonacq', 'anonabp' for 'anonabp', 'Anonadp' for 'Anonadp'). Folding all of them resolves both.
_TR_FOLD = str.maketrans({"İ": "i", "I": "i", "ı": "i", "Ş": "s", "ş": "s", "Ç": "c", "ç": "c",
                          "Ğ": "g", "ğ": "g", "Ö": "o", "ö": "o", "Ü": "u", "ü": "u"})


def _norm(study_adi: str) -> str:
    """Match key: strip (incl. the force sheet's trailing newline), fold Turkish letters to ASCII
    (_TR_FOLD), collapse a stray repeated underscore ('Anonaco__35e' -> 'Anonaco_35e' — an extra '_' is
    an empty field, never meaningful), then casefold — so 'Anonacq'/'Anonacq', 'ANONACB'/'anonacb', 'anonacg'/'ANONACG',
    'anonabp'/'anonabp', and a double-underscore typo all join. SAFE against merging two DIFFERENT patients: the
    key is the FULL study name (date_count_probe_surname_name_age_sex_height_weight_ASIS), so distinct
    people differ in far more than a separator; the audit's collision scan still catches a real reuse."""
    s = (study_adi or "").strip().translate(_TR_FOLD)
    return re.sub(r"_+", "_", s).casefold()


def parse_force_rows(values):
    """Force-sheet rows (each [Study Adı, v_B, v_C, v_D, v_E, v_F]) -> {norm_key: (raw_study, [5 vals])}
    for rows with a non-blank key. Values are stripped; blanks kept as '' (the per-cell fill decision
    happens in plan_writes). A duplicate key keeps the last occurrence."""
    out = {}
    for row in values:
        key = (row[0] if row else "").strip()
        if not key:
            continue
        vals = [(row[i] if i < len(row) else "").strip() for i in range(1, 6)]
        out[_norm(key)] = (key, vals)
    return out


def plan_writes(force_by_norm, study_to_row, study_to_vals):
    """Decide each cell write. Returns (writes, skipped_filled, blank_force, orphans):
      writes        : [(kayit_study, row, header, value)] — matched, value present, kayit cell blank
      skipped_filled: [(force_study, row, header)]        — kayit cell already has a value (kept)
      blank_force   : [(force_study, row)]                — matched but the force row has no values
      orphans       : [force_study]                       — force study has no kayit row
    """
    norm_to_kstudy = {_norm(s): s for s in study_to_row}
    writes, skipped_filled, blank_force, orphans = [], [], [], []
    for nkey, (fstudy, vals) in force_by_norm.items():
        kstudy = norm_to_kstudy.get(nkey)
        if kstudy is None:
            orphans.append(fstudy)
            continue
        row = study_to_row[kstudy]
        if not any(vals):
            blank_force.append((fstudy, row))
            continue
        cur = study_to_vals.get(kstudy, {})
        for header, val in zip(FORCE_VALUE_HEADERS, vals):
            if not val:
                continue
            if (cur.get(header, "") or "").strip():
                skipped_filled.append((fstudy, row, header))
                continue
            writes.append((kstudy, row, header, val))
    return writes, skipped_filled, blank_force, orphans


def gate_states(study_to_row, study_to_vals, writes):
    """({study_uid: (sheet_row, value snapshot)}, {study_uid: current check cell}) for the
    measurement gate — the POST-SYNC state: each kayit row's current force cells with this pass's
    planned writes overlaid. Every row is included (not only newly-written ones), so a value typed
    straight into kayit is checked too."""
    from preflight_helpers.study_metadata import study_uid_for_dirname

    planned = {(row, header): val for _, row, header, val in writes}
    header_to_field = {h: f for f, h in zip(
        ("izo_torque_nm", "izo_cv_pct", "isok_torque_ext_nm", "isok_mean_power_ext_w", "isok_cv_pct"),
        FORCE_VALUE_HEADERS)}
    states, current_cells = {}, {}
    for nk, row in study_to_row.items():
        vals = study_to_vals.get(nk, {})
        raw_name = (vals.get("Study Adı") or "").strip()
        if not raw_name:
            continue
        snap = {}
        for header, fld in header_to_field.items():
            cur = (vals.get(header) or "").strip()
            snap[fld] = planned.get((row, header), cur)
        snap["weight_kg"] = (vals.get("Kilo (kg)") or "").strip()
        # the same uid function the substrate join uses (load_sheet_rows), so a flag recorded here
        # is found by build_qa_sheet's hold enforcement with a plain dict lookup
        uid = study_uid_for_dirname(raw_name)
        states[uid] = (row, snap)
        current_cells[uid] = (vals.get("Ölçüm Kontrol") or "").strip()
    return states, current_cells


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Join the force datasheet into hasta_kayit cols Y–AC (fill-missing, never clobber).")
    ap.add_argument("--config", default="configs/round3_intake.json")
    ap.add_argument("--apply", action="store_true", help="perform writes (default: dry-run)")
    args = ap.parse_args(argv)

    from preflight_helpers import gws_io as io
    import sync_records

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    force_id = cfg["sheets"]["force_measures"]
    ftitle = io.first_sheet(io.sheets_meta(force_id))["title"]
    fvals = io.sheets_values_get(force_id, f"{ftitle}!A1:F1000")
    assert_force_header(fvals[0] if fvals else [])
    force_by_norm = parse_force_rows(fvals[1:])

    hk_title, _, study_to_row, study_to_vals = sync_records.read_existing_hasta(cfg)
    writes, skipped_filled, blank_force, orphans = plan_writes(force_by_norm, study_to_row, study_to_vals)

    n_studies = len({row for _, row, _, _ in writes})
    print(f"[force-sync] {len(force_by_norm)} force rows · kayit has {len(study_to_row)} studies")
    print(f"  WRITE  {len(writes)} cells across {n_studies} studies")
    print(f"  SKIP   {len(skipped_filled)} cells already filled in kayit (kept)")
    print(f"  BLANK  {len(blank_force)} force rows with no values: {[s for s, _ in blank_force]}")
    print(f"  ORPHAN {len(orphans)} force studies not in kayit: {orphans}")
    for kstudy, row, header, val in writes:
        col = ds.col_letter(ds.col_index(header))
        print(f"    {col}{row:<4d}= {val:>7}   {header[:26]:26}  {kstudy[:38]}")

    # ---- the measurement gate: evaluate the post-sync state of EVERY row with a criterion value.
    # Report on every pass; record flags + write the check column only under --apply.
    from preflight_helpers import measurement_gate as mg
    states, current_cells = gate_states(study_to_row, study_to_vals, writes)
    evaluated = mg.evaluate(states)
    flagged = [sf for sf in evaluated if sf.findings]
    planned = mg.plan_entries(evaluated)
    print(f"\n[measurement-gate] {len(evaluated)} rows carry criterion values · "
          f"{len(flagged)} with findings · {len(planned)} fresh ledger entries")
    for sf in sorted(flagged, key=lambda s: s.sheet_row or 0):
        for f in sf.findings:
            icon = "‼" if f.tier == "hard" else "⚠"
            print(f"    {icon} row {sf.sheet_row:<4} [{f.tier:4}] {f.code}: {f.evidence}")
    if flagged:
        print("    → a flagged row is NO-FORCE equivalent: every criterion cell is held out of "
              "analysis (any tier)")
        print("      until a human verdict consents: scripts/measurement_verdict.py "
              "--clear (use as recorded) | --kill (dead)")

    # ---- the change watch: every in-place edit of an existing criterion value since the last
    # APPLIED pass (Akadir 2026-08-13: "operators sneak some inputs — I should at least
    # see/get-noticed-by them"). The gate above re-flags a changed value only when the new value
    # is implausible; this reports the edit itself. Reported every pass; the baseline snapshot
    # advances only under --apply, so a dry-run never swallows a change unseen.
    snap_path = Path("data/audit/force_cells_snapshot.json")
    prev_snap = json.loads(snap_path.read_text(encoding="utf-8")) if snap_path.exists() else {}
    cur_cells = {uid: snap for uid, (_row, snap) in states.items()}
    changes = mg.diff_snapshots(prev_snap.get("cells", {}), cur_cells)
    if not prev_snap:
        print("\n[change-watch] no baseline yet — this pass records one under --apply")
    elif changes:
        print(f"\n[change-watch] {len(changes)} criterion cell(s) EDITED IN PLACE since the "
              f"{prev_snap.get('date', '?')} pass:")
        for uid, fld, old, new in changes:
            row = states[uid][0]
            new_txt = repr(new) if new else "BLANK"
            print(f"    row {row:<4} {uid} {fld}: {old!r} → {new_txt}")
        print("    → the gate re-evaluated the new values above; an edit to a KILLED/CLEARED "
              "cell re-opens its thread automatically")
    else:
        print(f"\n[change-watch] no in-place edits since the {prev_snap.get('date', '?')} pass")

    if not args.apply:
        if planned:
            print("    [dry-run] nothing recorded — under --apply the ledger gains the entries "
                  f"above and '{mg.MEASUREMENT_CHECK_COL}' is written for the flagged rows.")
        print("\n[dry-run] no writes. Re-run with --apply to commit.")
        return 0

    hk_id = cfg["sheets"]["hasta_kayit"]
    for kstudy, row, header, val in writes:
        cell = f"{hk_title}!{ds.col_letter(ds.col_index(header))}{row}"
        io.sheets_values_update(hk_id, cell, [[val]], value_input="RAW")
    print(f"\n[applied] wrote {len(writes)} cells; {len(skipped_filled)} existing values untouched.")

    if planned:
        mg.record(planned)
        print(f"[measurement-gate] {len(planned)} entries appended to data/kill_list.jsonl")
    # 'Ölçüm Kontrol' renders CURRENT ledger state (just-recorded flags AND human kill/clear
    # verdicts), surgically: the header once if absent, then changed cells only.
    #
    # The column is located on the LIVE sheet, never by schema index alone: the sheet is shared
    # and operators add columns the schema does not know — "Force Operator" occupied the schema's
    # intended slot for ~120 rows before anyone's tooling saw it (2026-08-12, caught by Akadir's
    # "have you checked?" one message before a clobbering write). Placement rule: the header's
    # own column if it exists; otherwise the first column past every occupied header cell,
    # verified EMPTY top to bottom before it is claimed, growing the grid when the sheet has no
    # spare column. Refuse loudly rather than guess.
    meta_first = io.first_sheet(io.sheets_meta(hk_id))
    live_hdr = (io.sheets_values_get(hk_id, f"{hk_title}!A1:AZ1") or [[]])[0]
    live_hdr = [h or "" for h in live_hdr]
    if mg.MEASUREMENT_CHECK_COL in live_hdr:
        check_idx = live_hdr.index(mg.MEASUREMENT_CHECK_COL)
    else:
        check_idx = len(live_hdr)
        check_col = ds.col_letter(check_idx)
        n_cols = meta_first.get("gridProperties", {}).get("columnCount", 0)
        if check_idx >= n_cols:
            # the target column is past the grid edge — it does not exist, so it cannot hold
            # data (reading it would 400 with "exceeds grid limits"); grow the grid to claim it
            io.sheets_batch_update(hk_id, [{"appendDimension": {
                "sheetId": meta_first["sheetId"], "dimension": "COLUMNS",
                "length": check_idx - n_cols + 1}}])
            # an appended column INHERITS the previous last column's data validation — the gate's
            # first write landed inside 'Force Operator's name-dropdown and rendered as a picker
            # (Akadir's catch, 2026-08-14). Strip validation so the column is a plain text box.
            io.sheets_batch_update(hk_id, [{"setDataValidation": {"range": {
                "sheetId": meta_first["sheetId"], "startRowIndex": 0, "endRowIndex": 1000,
                "startColumnIndex": check_idx, "endColumnIndex": check_idx + 1}}}])
            print(f"[measurement-gate] grid grown {n_cols} → {check_idx + 1} columns "
                  f"(inherited data validation stripped)")
        else:
            # column exists in the grid but has no header cell — verify it is truly empty
            # before claiming it (a headerless column can still carry data)
            body = io.sheets_values_get(hk_id, f"{hk_title}!{check_col}1:{check_col}1000") or []
            occupied = [(i, v[0]) for i, v in enumerate(body, start=1)
                        if v and (v[0] or "").strip()]
            if occupied:
                raise SystemExit(
                    f"[measurement-gate] REFUSING to claim column {check_col}: headerless but "
                    f"{len(occupied)} cell(s) hold data (first at row {occupied[0][0]}). A human "
                    f"must resolve what that column is before the gate writes anywhere.")
        io.sheets_values_update(hk_id, f"{hk_title}!{check_col}1",
                                [[mg.MEASUREMENT_CHECK_COL]], value_input="RAW")
        print(f"[measurement-gate] header '{mg.MEASUREMENT_CHECK_COL}' written at {check_col}1")
    check_col = ds.col_letter(check_idx)
    n_cells = 0
    for uid, (row, _snap) in states.items():
        desired = mg.desired_cell(uid)
        if desired is None or desired == (current_cells.get(uid) or "").strip():
            continue
        io.sheets_values_update(hk_id, f"{hk_title}!{check_col}{row}", [[desired]],
                                value_input="RAW")
        n_cells += 1
    print(f"[measurement-gate] {n_cells} '{mg.MEASUREMENT_CHECK_COL}' cell(s) updated")

    from datetime import date as _date
    snap_path.parent.mkdir(parents=True, exist_ok=True)
    snap_path.write_text(json.dumps({"date": _date.today().isoformat(), "cells": cur_cells},
                                    ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[change-watch] baseline snapshot advanced ({len(cur_cells)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

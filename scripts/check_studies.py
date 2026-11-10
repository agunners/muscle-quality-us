#!/usr/bin/env python3
"""Operator self-check watcher — the inverse of sync_records.py.

sync_records pulls Drive -> sheet. This pulls the SHEET's intent -> a verdict: it finds rows
the operator has filled (Study Adı + Operator) but not yet checked, resolves that study's zip
from the operator's Drive subfolder, runs the staged completeness/accuracy check, and writes a
one-line verdict back into the 'Kontrol Sonucu' column — so the operator gets feedback within
a poll of finishing the row, while the patient is (post-visit upload) still in reach.

The trigger is a STATE, not an event: {Study Adı & Operator present} AND {Kontrol Sonucu empty
or still pending}. That makes it idempotent and self-healing — no webhook, no public endpoint,
only the same outbound gws auth sync_records already uses. A not-yet-uploaded zip writes a
pending marker that is retried every poll until the file appears (and only then becomes a
terminal ✅/⚠️/❌).

  python scripts/check_studies.py                       # dry-run: show pending rows + verdicts
  python scripts/check_studies.py --apply               # write verdicts once
  python scripts/check_studies.py --watch --apply       # poll forever (default 45s)
  python scripts/check_studies.py --operator anonacl --limit 1
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for `preflight_helpers`

from preflight_helpers import gws_io as io
from preflight_helpers import datasheet_schema as ds
from preflight_helpers.check_study import check_study, study_protocol, _mean_brightness
from study_qa import study_qa, render_cell
from sync_force_measures import _norm   # the shared normalized key (Turkish/case/underscore-fold)

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULT_COL = "Kontrol Sonucu"
DETAIL_COL = "Kontrol Detayı"
KEY_COL = "Study Adı"
PENDING_PREFIX = "⏳"   # a verdict starting with this is re-evaluated next poll
BACKUP_DIR = "data/datasheet_backups"   # timestamped CSV each apply-pass (gitignored)


def load_config(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _col_letter(idx0: int) -> str:
    s, n = "", idx0
    while True:
        s = chr(ord("A") + n % 26) + s
        n = n // 26 - 1
        if n < 0:
            break
    return s


# ---------- sheet state ----------
def read_sheet(cfg: dict):
    """Return (title, header, rows). rows are dicts {header: value} + '_row' (1-based)."""
    hk_id = cfg["sheets"]["hasta_kayit"]
    title = io.first_sheet(io.sheets_meta(hk_id))["title"]
    # Read the FULL schema width (not a hardcoded Z) so the header includes every column — then the
    # by-name column resolution + its append-fallback can never collide with a real column past Z
    # (e.g. the force columns Y..AC). LAST_COL tracks the schema (now AC).
    last_col = ds.col_letter(len(ds.HEADERS) - 1)
    values = io.sheets_values_get(hk_id, f"{title}!A1:{last_col}1000")
    header = values[0] if values else []
    rows = []
    for r_off, row in enumerate(values[1:], start=2):
        d = {h: (row[i] if i < len(row) else "") for i, h in enumerate(header)}
        d["_row"] = r_off
        rows.append(d)
    return title, header, rows


def _col_for(header: list, name: str, fallback_offset: int = 0) -> str:
    """Column letter for a header — found by name, else appended past the end."""
    if name in header:
        return _col_letter(header.index(name))
    return _col_letter(len(header) + fallback_offset)


def is_pending(row: dict) -> bool:
    """A row needs checking when it's filled but its verdict is empty or still a pending marker."""
    if not (row.get(KEY_COL, "").strip() and row.get("Operator", "").strip()):
        return False
    cur = (row.get(RESULT_COL, "") or "").strip()
    return cur == "" or cur.startswith(PENDING_PREFIX)


def declared_profile(row: dict):
    # Positive whitelist: only evet/hayır count as a declaration. Anything else — blank OR the
    # 'Seçiniz' default seeded into the dropdown — returns None (unanswered), so a not-yet-picked
    # row is flagged "please fill" rather than mistaken for a real cohort.
    v = (row.get("Alt-kohort mu", "") or "").strip().lower()
    if v in ("evet", "yes", "true"):
        return "subcohort"
    if v in ("hayır", "hayir", "no", "false"):
        return "main"
    return None


# ---------- zip resolution ----------
def resolve_zip(cfg: dict, operator: str, study: str):
    """Find the study's zip in the operator's Drive subfolder, cache it locally under the CANONICAL
    (datasheet) name, return the path. The Drive filename is matched by the NORMALIZED key (`_norm`:
    Turkish / case / underscore-fold) — the SAME tolerance ingest uses — so a stray-underscore typo in
    the operator's filename (e.g. 'Anonaco__35e' vs the row's 'Anonaco_35e') still resolves instead of
    leaving the row stuck ⏳. None (with a reason) if the zip isn't uploaded yet or is still truncated."""
    rel = f"{cfg['cache_dir']}/{study}.zip"
    local = REPO_ROOT / rel
    if local.exists() and _zip_ok(local):
        return str(local), None
    subfolders = io.drive_list_children(cfg["drive"]["goruntuler_folder"], folders_only=True)
    fol = next((f for f in subfolders if f["name"] == operator), None)
    if not fol:
        return None, f"operator klasörü yok: {operator}"
    match = next((z for z in io.drive_list_children(fol["id"], zips_only=True)
                  if _norm(Path(z["name"]).stem) == _norm(study)), None)
    if not match:
        return None, "zip Drive'da yok — yükleyin"
    io.drive_download(match["id"], rel)
    if not _zip_ok(local):
        return None, "zip eksik yüklenmiş (yükleme sürüyor?)"
    return str(local), None


def _zip_ok(path: Path) -> bool:
    """Central directory readable -> the upload finished and the file isn't truncated. A
    partial upload has no End-Of-Central-Directory record, so the constructor raises — which
    is the cheap check we want (no full-CRC scan of a 264MB file every poll)."""
    try:
        with zipfile.ZipFile(path) as zf:
            zf.namelist()
            return True
    except Exception:
        return False


# ---------- one row ----------
def verdict_for(cfg: dict, row: dict, *, do_download: bool = True):
    """Return (Kontrol Sonucu, Kontrol Detayı) for a row — the one-line verdict + the depth
    multiset / brightness / full detail behind it."""
    study, operator = row[KEY_COL].strip(), row["Operator"].strip()
    if not do_download:
        return f"{PENDING_PREFIX} (dry, indirme yok)", ""
    zip_path, reason = resolve_zip(cfg, operator, study)
    if zip_path is None:
        return f"{PENDING_PREFIX} {reason}", ""
    proto = cfg.get("protocol") or study_protocol(zip_path)   # per-study, by recording date
    if proto == "v8":
        # the single-leg pilot -> the UNIFIED relative-rank QA (study_qa). ONE computation, so this
        # operator cell and the analyst audit read the same answer, and a tweaked-deeper femur counts as
        # the femur (no false "femur eksik" from the old absolute-depth bucketing). Legacy v7/subcohort
        # stays on check_study below (study_qa also self-defers a subcohort/unparseable zip).
        qa = study_qa(zip_path, operator, study=study, declared_profile=declared_profile(row),
                      brightness=_mean_brightness(zip_path))
        return render_cell(qa), " · ".join(qa.reasons + qa.notes)
    rpt = check_study(zip_path, declared_profile=declared_profile(row),
                      protocol=proto, study=study)
    detail = " · ".join(rpt.depth_notes + rpt.param_notes + rpt.details)
    return rpt.as_cell(), detail


def _backup_sheet(title: str, header: list, rows: list) -> None:
    """Timestamped CSV snapshot of the sheet — cheap recovery from an accidental row/sheet
    deletion (alongside Drive's own version history)."""
    import csv
    from datetime import datetime
    d = REPO_ROOT / BACKUP_DIR
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(d / f"{title}_{stamp}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow([r.get(h, "") for h in header])


# ---------- pass / watch ----------
def run_once(cfg: dict, *, apply: bool, operator_filter=None, limit=None,
             do_download=True, force=False) -> int:
    title, header, rows = read_sheet(cfg)
    rcol, dcol = _col_for(header, RESULT_COL), _col_for(header, DETAIL_COL, 1)
    # Default picks up only 'pending' rows (empty/⏳ verdict). --force RE-evaluates rows that
    # already carry a verdict too — needed to refresh a column written by an older classifier
    # (e.g. the stale absolute-depth 'femur eksik'); a real row = study + operator both present.
    def _has_id(r):
        return bool(r.get(KEY_COL, "").strip() and r.get("Operator", "").strip())
    pend = [r for r in rows if (is_pending(r) or (force and _has_id(r)))
            and (not operator_filter or r.get("Operator", "").strip() == operator_filter)]
    if limit is not None:
        pend = pend[:limit]
    label = "re-eval (force)" if force else "pending"
    print(f"=== CHECK PASS === rows: {len(rows)} | {label}: {len(pend)} | cols: {rcol}/{dcol}")
    hk_id = cfg["sheets"]["hasta_kayit"]
    if apply:
        _backup_sheet(title, header, rows)
        if RESULT_COL not in header:
            io.sheets_values_update(hk_id, f"{title}!{rcol}1", [[RESULT_COL]])
        if DETAIL_COL not in header:
            io.sheets_values_update(hk_id, f"{title}!{dcol}1", [[DETAIL_COL]])
    n_changed = 0
    for r in pend:
        cell, detail = verdict_for(cfg, r, do_download=do_download)
        flag = "fail" if cell.startswith("❌") else ("warn" if cell.startswith("⚠️")
                else ("pending" if cell.startswith(PENDING_PREFIX) else "ok"))
        # Only a genuine change (vs the verdict already on the sheet) is worth a write — so
        # --force --apply is surgical: it refreshes the rows an older classifier got wrong and
        # leaves every already-correct row (incl. a manual edit it agrees with) untouched.
        # A BLANK cell counts as a change: that is the watcher's primary job (first verdict on
        # a row the operator just filled). Guarding on `bool(cur)` silently disabled it.
        cur = (r.get(RESULT_COL, "") or "").strip()
        changed = cell.strip() != cur
        if changed:
            n_changed += 1
        print(f" {'Δ' if changed else ' '} row {r['_row']:>3} {r['Operator']:<12} "
              f"{r[KEY_COL]:<42} [{flag}] {cell}")
        if changed and cur:
            print(f"           was: {cur}")
        if detail:
            print(f"           ↳ {detail}")
        if apply and changed and not cell.startswith(f"{PENDING_PREFIX} (dry"):
            io.sheets_values_update(hk_id, f"{title}!{rcol}{r['_row']}", [[cell]])
            io.sheets_values_update(hk_id, f"{title}!{dcol}{r['_row']}", [[detail]])
    print(f"=== {n_changed} row(s) differ from the sheet"
          + (" — written" if apply else " — dry-run, no writes; re-run with --apply --force") + " ===")
    return len(pend)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Operator study self-check watcher (writes 'Kontrol Sonucu').")
    ap.add_argument("--config", default="configs/round3_intake.json")
    ap.add_argument("--apply", action="store_true", help="write verdicts (default: dry-run)")
    ap.add_argument("--watch", action="store_true", help="poll forever")
    ap.add_argument("--interval", type=int, default=45, help="poll seconds (with --watch)")
    ap.add_argument("--operator", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-download", action="store_true", help="don't fetch zips (list pending only)")
    ap.add_argument("--force", action="store_true",
                    help="re-evaluate rows that already carry a verdict (default: only 'pending' rows)")
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    if not args.watch:
        run_once(cfg, apply=args.apply, operator_filter=args.operator,
                 limit=args.limit, do_download=not args.no_download, force=args.force)
        return 0
    print(f"[watch] every {args.interval}s — Ctrl-C to stop")
    while True:
        try:
            run_once(cfg, apply=args.apply, operator_filter=args.operator,
                     limit=args.limit, do_download=not args.no_download)
        except Exception as e:  # a poll failure must not kill the watcher
            print(f"[watch] pass error (will retry): {e}")
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())

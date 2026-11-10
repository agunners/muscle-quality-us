#!/usr/bin/env python3
"""Gated ingest — the spine of the pilot pipeline.

Pull a study from Drive and materialize it into `data/studies_root` (the ONE tree the assessment
app and the viewer read) ONLY when its datasheet row is filled. That gate is what keeps
source <-> datasheet <-> processing consistent BY CONSTRUCTION — no filled row, no pull — so there
is no drift left to reconcile after the fact.

    python scripts/ingest.py                 # DRY-RUN: which filled rows would ingest + the seams
    python scripts/ingest.py --apply         # pull + materialize the filled rows -> studies_root
    python scripts/ingest.py --apply --force  # re-materialize studies already on disk
    python scripts/ingest.py --apply --limit 1

The gate (a FILLED row) = `Study Adı` + `Operator` present AND `Dosyalar Tam mı = Evet` (the
operator's explicit "this study is ready" signal). Kontrol ✅ is NOT required — ingest works even
if the DGX watcher is down. Reuses `check_studies.read_sheet` + `resolve_zip` (the Drive pull into
`cache_dir`) and `materialize_studies.plan_zip/write_plan`. NEVER writes the sheet; the source zip
is never mutated. The one seam the gate can't fix — a Drive zip with no filled row (a name typo or
a not-yet-logged study) — is surfaced, not silently dropped.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for `preflight_helpers`

import check_studies as cs
import materialize_studies as ms
from preflight_helpers import env
from preflight_helpers import gws_io as io
from sync_force_measures import _norm

REPO_ROOT = Path(__file__).resolve().parents[1]
READY_COL = "Dosyalar Tam mı"
_READY_YES = ("evet", "yes", "true")
FLAT_ROOT = REPO_ROOT / "data" / "studies_root_flat"   # the flat view the viewer reads (gitignored)


def is_filled(row: dict) -> bool:
    """The pull gate. A row is FILLED — eligible to pull — when Study Adı + Operator are present
    AND `Dosyalar Tam mı = Evet` (the operator's explicit ready signal). Anything else (blank,
    'Seçiniz', or 'Hayır') is NOT ready and is never pulled."""
    if not (row.get(cs.KEY_COL, "").strip() and row.get("Operator", "").strip()):
        return False
    return (row.get(READY_COL, "") or "").strip().lower() in _READY_YES


def rebuild_flat_view(studies_root: Path, flat_root: Path = FLAT_ROOT) -> int:
    """Rebuild the flat `<study> -> <op>/<session>/<study>` symlink view the viewer reads
    (VIEWER_DATA_ROOT): the viewer expects study folders at the top level, but studies_root nests
    operator/session/study. Idempotent — clears stale symlinks, relinks every materialized study.

    `.resolve()` is load-bearing, not tidiness. A RELATIVE studies_root produces relative symlinks,
    which resolve against flat_root and therefore point nowhere — 116 links, all dangling, and the
    viewer discovers 0 studies. That happened on the DGX on 2026-08-03 and looked completely healthy
    from outside: the link count was right, the census read `flat links 116`, and nothing failed until
    the viewer was actually asked to list a study."""
    studies_root, flat_root = Path(studies_root).resolve(), Path(flat_root).resolve()
    flat_root.mkdir(parents=True, exist_ok=True)
    for link in list(flat_root.iterdir()):
        if link.is_symlink():
            link.unlink()
    n = 0
    for manifest in sorted(studies_root.glob("*/*/*/materialize_manifest.json")):
        study_dir = manifest.parent
        (flat_root / study_dir.name).symlink_to(study_dir)
        n += 1
    return n


def _drive_zip_index(cfg: dict) -> dict:
    """{norm_study: (operator, zip_study, file_id)} for every zip across the goruntuler operator
    subfolders. Keyed by the NORMALIZED name so a stray-underscore / accent / case typo in the
    operator's filename still resolves (the datasheet name is the source of truth — see below)."""
    out: dict = {}
    for fol in io.drive_list_children(cfg["drive"]["goruntuler_folder"], folders_only=True):
        for z in io.drive_list_children(fol["id"], zips_only=True):
            study = Path(z["name"]).stem
            out[_norm(study)] = (fol["name"], study, z["id"])
    return out


def _resolve_zip_canonical(cfg: dict, drive_index: dict, study: str):
    """Resolve a CANONICAL datasheet study to its Drive zip by NORMALIZED key — so a stray underscore
    or accent in the operator's zip FILENAME (e.g. 'Anonaco__35e' for the datasheet 'Anonaco_35e')
    still links — and download it under the CANONICAL name, so the materialized study + everything
    downstream carries the datasheet's name, never the typo'd filename (which also rides in the zip's
    INTERNAL folder/file names). Returns (local_path|None, note); note is set when the filename
    differed, so the operator typo is surfaced even though it is auto-canonicalized."""
    hit = drive_index.get(_norm(study))
    if hit is None:
        return None, "Drive'da eşleşen zip yok — yükleyin / adı düzeltin"
    _operator, zip_study, file_id = hit
    rel = f"{cfg['cache_dir']}/{study}.zip"   # CANONICAL name -> materialize stem = the datasheet name
    local = REPO_ROOT / rel
    if not (local.exists() and cs._zip_ok(local)):
        io.drive_download(file_id, rel)
        if not cs._zip_ok(local):
            return None, "zip eksik yüklenmiş (yükleme sürüyor?)"
    note = (f"zip filename '{zip_study}' ≠ datasheet — materialized as canonical"
            if zip_study != study else "")
    return str(local), note


def ingest(cfg: dict, *, apply: bool, limit=None, force: bool = False, check_drive: bool = True):
    title, header, rows = cs.read_sheet(cfg)
    filled = [r for r in rows if is_filled(r)]
    not_ready = [r for r in rows if r.get(cs.KEY_COL, "").strip() and not is_filled(r)]
    sel = filled[:limit] if limit is not None else filled
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"=== INGEST ({mode}) === rows {len(rows)} | filled {len(filled)} | not-ready {len(not_ready)}")

    studies_root = Path(env.STUDIES_ROOT)
    # one Drive listing, shared by the canonical pull (typo-tolerant) and the unmatched seam below.
    drive_index: dict = {}
    if apply or check_drive:
        try:
            drive_index = _drive_zip_index(cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"  [drive listing skipped: {type(exc).__name__}: {exc}]")
    materialized = held = nozip = skipped = 0
    for r in sel:
        study, operator = r[cs.KEY_COL].strip(), r["Operator"].strip()
        if not apply:
            print(f"  would ingest   {operator:<12} {study}")
            continue
        zip_path, note = _resolve_zip_canonical(cfg, drive_index, study)
        if zip_path is None:
            print(f"  NO ZIP         {operator:<12} {study}  ({note})")
            nozip += 1
            continue
        if note:
            print(f"  ~ name fixup   {study}  ({note})")
        p = ms.plan_zip(zip_path, operator)
        if not p.materialize:
            print(f"  HOLD           {study}  ({p.reason})")
            held += 1
            continue
        ok, msg = ms.write_plan(p, studies_root, force=force)
        print(f"  {'OK' if ok else '..':<2}            {study}  {msg}")
        if ok:
            materialized += 1
        elif "exists" in msg:
            skipped += 1
    if apply:
        n = rebuild_flat_view(studies_root)
        print(f"  flat view -> data/studies_root_flat: {n} study link(s)  (point VIEWER_DATA_ROOT here)")
        print(f"  [ingest] {materialized} materialized, {skipped} already-on-disk, {held} held, "
              f"{nozip} no-zip (of {len(sel)} filled)")

    # The one seam the gate can't fix: a Drive zip with NO filled row — a name typo, or a study the
    # operator recorded but hasn't logged/marked ready yet. Surfaced so it's never silently lost.
    if check_drive:
        filled_keys = {_norm(r[cs.KEY_COL].strip()) for r in filled}
        unmatched = [(op, st) for k, (op, st, _fid) in drive_index.items() if k not in filled_keys]
        if unmatched:
            print(f"  UNMATCHED Drive zips — no filled row (recorded but not yet logged/ready): {len(unmatched)}")
            for op, st in sorted(unmatched)[:20]:
                print(f"    {op:<12} {st}")

    if not apply:
        print("[dry-run] no pull/materialize/symlink. Re-run with --apply.")
    return {"filled": len(filled), "materialized": materialized, "skipped": skipped,
            "held": held, "nozip": nozip}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Gated ingest: pull + materialize FILLED-row studies into studies_root (no row, no pull).")
    ap.add_argument("--config", default="configs/round3_intake.json")
    ap.add_argument("--apply", action="store_true", help="pull + materialize (default: dry-run)")
    ap.add_argument("--force", action="store_true", help="re-materialize studies already on disk")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-drive-check", action="store_true", help="skip the unmatched-Drive-zip seam check")
    args = ap.parse_args(argv)
    cfg = cs.load_config(args.config)
    ingest(cfg, apply=args.apply, limit=args.limit, force=args.force,
           check_drive=not args.no_drive_check)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

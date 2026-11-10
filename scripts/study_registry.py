#!/usr/bin/env python3
"""THE canonical study registry — one id, one table that resolves it.

Four identifier schemes coexist in this repo, and `study_code` means two
different things depending on which file you read (an NR uid in
`per_study_cohort_new.csv`; a cohort code like ACL762 or an AKA<seq> pseudonym
in `runs.jsonl`). That is a live mis-join footgun. This module fixes the ANCHOR:

  CANONICAL ID = `study_uid` = "NR" + sha256(dirname)[:8].upper()
                 (`study_metadata.study_uid_for_dirname` — the ONE producer;
                  `derive_new_round_cohort._study_code` mints the same scheme)

The uid is deterministic from the folder name, so COMPUTING it needs no table.
What needs a table is RESOLVING it back to a human identity (which operator,
which session, which display code, which legacy cohort code) — that table is
`data/study_registry.csv`, built here.

The registry PERSISTS across rebuilds: `first_seen_utc` is preserved, a uid that
vanishes from disk becomes `status=missing` rather than being deleted, and the
rebuild prints a DRIFT report (new / missing / candidate renames). A rename mints
a new uid — that is the known fragility of a dirname-derived id — so it is
surfaced as a candidate rename (a missing uid and a new uid sharing
operator+session), never silently absorbed.

PHI: the registry binds study_uid -> RAW DIRNAME, which carries patient
identifiers. `data/study_registry.csv` is gitignored, and the writer ASSERTS the
output path is git-ignored before writing. Nothing here ever writes a dirname to
a committed path.

CLI
  python scripts/study_registry.py                 # rebuild + print drift/collision report
  python scripts/study_registry.py --report-only   # report, write nothing

Torch-free and importable; `resolve()` / `uid_for()` are the one door other code
should use to go from any identifier to the canonical uid.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))  # for `preflight_helpers`

from preflight_helpers import env, study_metadata  # noqa: E402

# The registry's column order (stable — other code may read positionally).
COLUMNS = [
    "study_uid",           # canonical id — the ONLY key
    "dirname",             # PHI: raw folder name (gitignored artifact only)
    "operator",
    "session",
    "rel_path",            # operator/session/dirname
    "display_code",        # app label (AKA<seq>) — a LABEL, never a key
    "legacy_study_code",   # from configs/study_codes.csv; blank for the new round
    "cohort",              # old | new | both | unknown
    "has_finalized_run",   # bool — the study has a finalized run in the ledger
    "first_seen_utc",
    "last_seen_utc",      # last scan in which the study was seen ON DISK (blank = never)
    "status",              # present (on disk this scan) | missing
]

REGISTRY_CSV = env.DATA_ROOT / "study_registry.csv"
NEW_COHORT_CSV = env.COHORT_STATS_DIR / "per_study_cohort_new.csv"
OLD_COHORT_CSV = env.COHORT_STATS_DIR / "per_study_cohort.csv"


def utc_now_iso() -> str:
    """The scan timestamp. Kept OUT of the merge logic on purpose — `merge_rows`
    takes `now_utc` as a parameter so it stays pure and testable."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #
def _is_study_dir(path: Path) -> bool:
    """A study dir = one carrying a materialize manifest and/or the raw capture
    dir. Both markers, because the 5 legacy folders on disk predate the
    manifest."""
    try:
        if (path / "materialize_manifest.json").is_file():
            return True
        return any(path.glob("RawImagesForResearch_*"))
    except OSError:
        return False


def scan_disk(studies_root: Path = None) -> dict[str, dict]:
    """DISK is authoritative for what EXISTS: studies_root/<operator>/<session>/<dirname>."""
    studies_root = Path(studies_root or env.STUDIES_ROOT)
    out: dict[str, dict] = {}
    if not studies_root.is_dir():
        return out
    for operator_dir in sorted(p for p in studies_root.iterdir() if p.is_dir()):
        for session_dir in sorted(p for p in operator_dir.iterdir() if p.is_dir()):
            for study_dir in sorted(p for p in session_dir.iterdir() if p.is_dir()):
                if not _is_study_dir(study_dir):
                    continue
                dirname = study_dir.name
                uid = study_metadata.study_uid_for_dirname(dirname)
                out[uid] = {
                    "study_uid": uid,
                    "dirname": dirname,
                    "operator": operator_dir.name,
                    "session": session_dir.name,
                    "rel_path": f"{operator_dir.name}/{session_dir.name}/{dirname}",
                    "display_code": _display_code(study_dir),
                }
    return out


def _display_code(study_path: Path) -> str:
    """The app's label for a study. Never a key: AKA<seq> repeats across
    operators/rounds. Degrades to '' if the name grammar cannot parse."""
    try:
        return str(study_metadata.lookup_study(Path(study_path)).study_code or "")
    except Exception:
        return ""


def load_legacy(codes_csv: Path = None) -> dict[str, dict]:
    """configs/study_codes.csv — the old-cohort code table. Contributes
    `legacy_study_code` and covers studies that may no longer be on disk."""
    codes_csv = Path(codes_csv or env.STUDY_CODES_CSV)
    out: dict[str, dict] = {}
    if not codes_csv.is_file():
        return out
    try:
        with codes_csv.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                dirname = (row.get("study_dirname") or "").strip()
                if not dirname:
                    continue
                rel = (row.get("relative_frame_dir") or "").strip()
                parts = rel.split("/")
                out[study_metadata.study_uid_for_dirname(dirname)] = {
                    "dirname": dirname,
                    "operator": (row.get("annotator") or (parts[0] if len(parts) > 2 else "")).strip(),
                    "session": (row.get("session") or (parts[1] if len(parts) > 2 else "")).strip(),
                    "rel_path": rel,
                    "legacy_study_code": (row.get("study_code") or "").strip(),
                }
    except OSError:
        return out
    return out


def load_runs(runs_jsonl: Path = None) -> dict[str, dict]:
    """data/runs.jsonl — which display/legacy code each record actually used, and
    whether the study has a finalized run. Joined via `study_metadata.run_study_uid`
    (the ONE ledger uid producer); records with no study_path are skipped."""
    runs_jsonl = Path(runs_jsonl or env.RUNS_JSONL)
    out: dict[str, dict] = {}
    if not runs_jsonl.is_file():
        return out
    try:
        lines = runs_jsonl.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(rec, dict):
            continue
        # Prefer the stamped fact; fall back to deriving it (legacy records).
        uid = rec.get("study_uid") or study_metadata.run_study_uid(rec)
        if not uid:
            continue
        slot = out.setdefault(uid, {"has_finalized_run": False, "run_codes": set()})
        if str(rec.get("lifecycle") or "finalized") == "finalized":
            slot["has_finalized_run"] = True
        code = rec.get("study_code")
        if code:
            slot["run_codes"].add(str(code))
    return out


def load_new_cohort(cohort_csv: Path = None) -> set[str]:
    """per_study_cohort_new.csv — its `study_code` column holds NR uids."""
    cohort_csv = Path(cohort_csv or NEW_COHORT_CSV)
    uids: set[str] = set()
    if not cohort_csv.is_file():
        return uids
    try:
        with cohort_csv.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                val = (row.get("study_code") or "").strip()
                if val:
                    uids.add(val)
    except OSError:
        pass
    return uids


def load_old_cohort(cohort_csv: Path = None) -> set[str]:
    """per_study_cohort.csv — its `study_code` column holds LEGACY cohort codes
    (ABQ555-style), so membership resolves through `legacy_study_code`."""
    cohort_csv = Path(cohort_csv or OLD_COHORT_CSV)
    codes: set[str] = set()
    if not cohort_csv.is_file():
        return codes
    try:
        with cohort_csv.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                val = (row.get("study_code") or "").strip()
                if val:
                    codes.add(val)
    except OSError:
        pass
    return codes


def load_registry(path: Path = None) -> list[dict]:
    """Read the persisted registry (empty on first build)."""
    path = Path(path or REGISTRY_CSV)
    if not path.is_file():
        return []
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            return [dict(r) for r in csv.DictReader(fh)]
    except OSError:
        return []


# --------------------------------------------------------------------------- #
# Merge (pure)
# --------------------------------------------------------------------------- #
def _as_bool(v) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes"}


def merge_rows(
    *,
    disk: dict[str, dict],
    legacy: dict[str, dict],
    runs: dict[str, dict],
    new_cohort_uids: set[str],
    old_cohort_codes: set[str],
    existing: Iterable[dict],
    now_utc: str,
) -> list[dict]:
    """One row per study, keyed study_uid. PURE — no clock, no I/O.

    Merge order: DISK wins on identity fields (it is authoritative for what
    exists), legacy fills gaps and contributes rows never on disk, the previous
    registry preserves `first_seen_utc` and any identity we can no longer see.
    """
    prior = {r.get("study_uid"): r for r in existing if r.get("study_uid")}
    uids = set(disk) | set(legacy) | set(runs) | set(new_cohort_uids) | set(prior)

    rows: list[dict] = []
    for uid in sorted(uids):
        d = disk.get(uid, {})
        lg = legacy.get(uid, {})
        pr = prior.get(uid, {})
        rn = runs.get(uid, {})

        def pick(field: str) -> str:
            for src in (d, lg, pr):
                val = (src.get(field) or "").strip()
                if val:
                    return val
            return ""

        legacy_code = (lg.get("legacy_study_code") or pr.get("legacy_study_code") or "").strip()
        in_new = uid in new_cohort_uids
        in_old = bool(legacy_code) and legacy_code in old_cohort_codes
        cohort = (
            "both" if (in_new and in_old)
            else "new" if in_new
            else "old" if in_old
            else "unknown"
        )
        # `last_seen_utc` means seen ON DISK — disk is the only source that says a
        # study EXISTS (a paper source like study_codes.csv keeps naming a study
        # forever). Stamping it from any source made every missing row carry the
        # current scan's time, so the date a study disappeared was unrecoverable.
        # A row never on disk keeps a blank last_seen_utc.
        on_disk = uid in disk
        rows.append({
            "study_uid": uid,
            "dirname": pick("dirname"),
            "operator": pick("operator"),
            "session": pick("session"),
            "rel_path": pick("rel_path"),
            "display_code": pick("display_code"),
            "legacy_study_code": legacy_code,
            "cohort": cohort,
            "has_finalized_run": bool(rn.get("has_finalized_run") or _as_bool(pr.get("has_finalized_run"))),
            "first_seen_utc": (pr.get("first_seen_utc") or "").strip() or now_utc,
            "last_seen_utc": now_utc if on_disk else (pr.get("last_seen_utc") or "").strip(),
            "status": "present" if on_disk else "missing",
        })
    return rows


def drift_report(existing: Iterable[dict], merged: Iterable[dict]) -> dict:
    """NEW / MISSING / CANDIDATE RENAMES, against the previous registry.

    MISSING is LEVEL-triggered, not edge-triggered: every uid that was once on
    disk (it carries a `last_seen_utc`) and is not there now is reported on EVERY
    rebuild. An edge-triggered report said it once and then went quiet, so an
    unattended rebuild (cron) could leave a disappearance with no report line at
    all. `newly_missing` is the edge subset — what changed since the last scan.

    A rename mints a NEW uid (the uid derives from the dirname) — the known
    fragility. So a missing uid and a new uid sharing operator+session are
    surfaced as a candidate rename rather than silently read as one gone + one
    born. Pairing is per operator+session SLOT and never a cross product: a slot
    with exactly one missing and one new yields a confident pair; any other slot
    yields ONE ambiguous entry listing both sides (a re-materialized 20-study
    session used to emit 400 pair lines — the report degraded exactly when drift
    was largest).
    """
    prior = {r.get("study_uid"): r for r in existing if r.get("study_uid")}
    merged_rows = list(merged)
    prior_present = {u for u, r in prior.items() if str(r.get("status", "")) == "present"}

    new_uids, missing_uids, newly_missing = [], [], []
    by_uid = {}
    for r in merged_rows:
        by_uid[r["study_uid"]] = r
        uid = r["study_uid"]
        if uid not in prior:
            new_uids.append(uid)
            continue
        if r["status"] != "missing":
            continue
        # Was it ever actually on disk? `last_seen_utc` is stamped from disk only.
        if r.get("last_seen_utc") or uid in prior_present:
            missing_uids.append(uid)
            if uid in prior_present:
                newly_missing.append(uid)

    def slot(uid: str) -> tuple[str, str]:
        r = by_uid.get(uid) or prior.get(uid) or {}
        return (str(r.get("operator", "")), str(r.get("session", "")))

    # Pair within slots only, and only against what changed THIS scan — a
    # long-standing missing row must not re-pair with every future arrival.
    slots: dict[tuple[str, str], dict[str, list[str]]] = {}
    for uid in newly_missing:
        s = slot(uid)
        if s != ("", ""):
            slots.setdefault(s, {"missing": [], "new": []})["missing"].append(uid)
    for uid in new_uids:
        s = slot(uid)
        if s in slots:
            slots[s]["new"].append(uid)

    renames = []
    for (operator, session), side in sorted(slots.items()):
        miss, new = sorted(side["missing"]), sorted(side["new"])
        if not new:
            continue
        if len(miss) == 1 and len(new) == 1:
            renames.append({
                "missing_uid": miss[0], "new_uid": new[0],
                "operator": operator, "session": session, "ambiguous": False,
            })
        else:
            renames.append({
                "missing_uid": None, "new_uid": None,
                "missing_uids": miss, "new_uids": new,
                "operator": operator, "session": session, "ambiguous": True,
            })
    return {
        "new": sorted(new_uids),
        "missing": sorted(missing_uids),
        "newly_missing": sorted(newly_missing),
        "candidate_renames": renames,
        "n_total": len(merged_rows),
        "n_present": sum(1 for r in merged_rows if r["status"] == "present"),
    }


def collision_report(rows: Iterable[dict]) -> dict:
    """display_code -> >1 uid is EXPECTED (that is exactly why AKA<seq> must never
    be a key). legacy_study_code -> >1 uid should be ZERO — flag it loudly."""
    disp: dict[str, list[str]] = {}
    leg: dict[str, list[str]] = {}
    for r in rows:
        if r.get("display_code"):
            disp.setdefault(r["display_code"], []).append(r["study_uid"])
        if r.get("legacy_study_code"):
            leg.setdefault(r["legacy_study_code"], []).append(r["study_uid"])
    return {
        "display_code_collisions": {k: sorted(v) for k, v in sorted(disp.items()) if len(v) > 1},
        "legacy_code_collisions": {k: sorted(v) for k, v in sorted(leg.items()) if len(v) > 1},
    }


# --------------------------------------------------------------------------- #
# Write (PHI-guarded)
# --------------------------------------------------------------------------- #
def _assert_git_ignored(path: Path) -> None:
    """The registry binds uid -> RAW DIRNAME (PHI). Refuse to write it to a path
    git would track — fail loudly rather than leak."""
    try:
        rc = subprocess.run(
            ["git", "check-ignore", "-q", str(path)],
            cwd=str(env.REPO_ROOT), capture_output=True,
        ).returncode
    except OSError as exc:                                  # no git → refuse
        raise RuntimeError(f"PHI guard: cannot verify git-ignore status of {path}: {exc}")
    if rc != 0:
        raise RuntimeError(
            f"PHI guard: {path} is NOT git-ignored. The registry carries raw study "
            f"dirnames (patient identifiers); add it to .gitignore before writing."
        )


def write_registry(rows: list[dict], path: Path = None) -> Path:
    path = Path(path or REGISTRY_CSV)
    _assert_git_ignored(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLUMNS})
    # The resolve door caches rows for the life of the process (Streamlit is
    # long-lived) — a rebuild in-process must not keep serving pre-rebuild rows.
    invalidate_cache()
    return path


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #
def build(
    *,
    studies_root: Path = None,
    codes_csv: Path = None,
    runs_jsonl: Path = None,
    new_cohort_csv: Path = None,
    old_cohort_csv: Path = None,
    registry_csv: Path = None,
    now_utc: str = None,
    write: bool = True,
) -> tuple[list[dict], dict, dict]:
    """Scan + merge every source into one row per study. Returns
    (rows, drift, collisions). Every source degrades to empty if absent."""
    registry_csv = Path(registry_csv or REGISTRY_CSV)
    existing = load_registry(registry_csv)
    rows = merge_rows(
        disk=scan_disk(studies_root),
        legacy=load_legacy(codes_csv),
        runs=load_runs(runs_jsonl),
        new_cohort_uids=load_new_cohort(new_cohort_csv),
        old_cohort_codes=load_old_cohort(old_cohort_csv),
        existing=existing,
        now_utc=now_utc or utc_now_iso(),
    )
    drift = drift_report(existing, rows)
    collisions = collision_report(rows)
    if write:
        write_registry(rows, registry_csv)
    return rows, drift, collisions


# --------------------------------------------------------------------------- #
# Resolve API — the one door
# --------------------------------------------------------------------------- #
_rows_cache: Optional[list[dict]] = None


def invalidate_cache() -> None:
    """Drop the cached registry rows. Called by `write_registry`; call it after any
    out-of-band rewrite so a long-lived process re-reads from disk."""
    global _rows_cache
    _rows_cache = None


def _rows(registry_csv: Path = None) -> list[dict]:
    global _rows_cache
    if registry_csv is not None:
        return load_registry(registry_csv)
    if _rows_cache is None:
        _rows_cache = load_registry(REGISTRY_CSV)
    return _rows_cache


def resolve(study_uid: str, *, registry_csv: Path = None) -> Optional[dict]:
    """uid -> its registry row (or None). The canonical lookup."""
    if not study_uid:
        return None
    for r in _rows(registry_csv):
        if r.get("study_uid") == study_uid:
            return r
    return None


def uid_for(
    *,
    dirname: str = None,
    display_code: str = None,
    legacy_code: str = None,
    path=None,
    registry_csv: Path = None,
) -> Optional[str]:
    """Any identifier -> the canonical uid, or None.

    `dirname`/`path` need no table (the uid is a hash of the folder name).
    `legacy_code` resolves through the registry. `display_code` is a LABEL and
    can map to several studies — an ambiguous one returns None rather than
    guessing (check `collision_report` to see why).
    """
    if path is not None:
        dirname = Path(path).name
    if dirname:
        return study_metadata.study_uid_for_dirname(dirname)
    rows = _rows(registry_csv)
    if legacy_code:
        hits = sorted({r["study_uid"] for r in rows if r.get("legacy_study_code") == legacy_code})
        return hits[0] if len(hits) == 1 else None
    if display_code:
        hits = sorted({r["study_uid"] for r in rows if r.get("display_code") == display_code})
        return hits[0] if len(hits) == 1 else None      # ambiguous -> None, never a guess
    return None


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
_PRINT_CAP = 20      # every drift list is capped; --json emits them in full


def _print_report(rows: list[dict], drift: dict, collisions: dict, *, wrote: Optional[Path]) -> None:
    n_present = drift["n_present"]
    print(f"study registry — {len(rows)} rows  ({n_present} present on disk, "
          f"{len(rows) - n_present} missing)")
    by_cohort: dict[str, int] = {}
    for r in rows:
        key = r.get("cohort") or "unknown"          # tolerate an older/hand-edited CSV
        by_cohort[key] = by_cohort.get(key, 0) + 1
    print("  cohort: " + ", ".join(f"{k}={v}" for k, v in sorted(by_cohort.items())))
    print(f"  finalized runs: {sum(1 for r in rows if _as_bool(r.get('has_finalized_run')))}")

    newly = set(drift.get("newly_missing") or [])
    print(f"\nDRIFT  new={len(drift['new'])}  missing={len(drift['missing'])} "
          f"(newly={len(newly)})  candidate_renames={len(drift['candidate_renames'])}")

    def _more(items: list, label: str) -> None:
        # Cap EVERY list, not just `new` — an uncapped side buries the report
        # exactly when drift is largest.
        if len(items) > _PRINT_CAP:
            print(f"  … {len(items) - _PRINT_CAP} more {label} (use --json for all)")

    for uid in drift["new"][:_PRINT_CAP]:
        print(f"  + NEW      {uid}")
    _more(drift["new"], "new")
    for uid in drift["missing"][:_PRINT_CAP]:
        print(f"  - MISSING  {uid}{'  (new this scan)' if uid in newly else ''}")
    _more(drift["missing"], "missing")
    for cr in drift["candidate_renames"][:_PRINT_CAP]:
        if cr.get("ambiguous"):
            print(f"  ~ RENAME?  AMBIGUOUS in {cr['operator']}/{cr['session']}: "
                  f"{len(cr['missing_uids'])} missing vs {len(cr['new_uids'])} new "
                  f"— pairing not attempted")
        else:
            print(f"  ~ RENAME?  {cr['missing_uid']} -> {cr['new_uid']}  "
                  f"({cr['operator']}/{cr['session']})")
    _more(drift["candidate_renames"], "candidate renames")

    dc = collisions["display_code_collisions"]
    lc = collisions["legacy_code_collisions"]
    print(f"\nCOLLISIONS  display_code={len(dc)} (expected — a LABEL, never a key)  "
          f"legacy_study_code={len(lc)} (should be 0)")
    for code, uids in list(dc.items())[:15]:
        print(f"  display_code {code} -> {len(uids)} uids: {', '.join(uids)}")
    if len(dc) > 15:
        print(f"  … {len(dc) - 15} more display_code collisions")
    for code, uids in lc.items():
        print(f"  !! legacy_study_code {code} -> {len(uids)} uids: {', '.join(uids)}")

    print(f"\n{'wrote ' + str(wrote) if wrote else 'report-only — nothing written'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--report-only", action="store_true",
                    help="print the drift/collision report without writing the registry")
    ap.add_argument("--registry", default=None, help="registry CSV path (must be git-ignored)")
    ap.add_argument("--json", action="store_true", help="emit the drift/collision report as JSON")
    args = ap.parse_args(argv)

    registry_csv = Path(args.registry) if args.registry else REGISTRY_CSV
    rows, drift, collisions = build(registry_csv=registry_csv, write=not args.report_only)
    if args.json:
        print(json.dumps({"n_rows": len(rows), "drift": drift, "collisions": collisions}, indent=2))
    else:
        _print_report(rows, drift, collisions,
                      wrote=None if args.report_only else registry_csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

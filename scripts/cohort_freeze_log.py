#!/usr/bin/env python
"""Record what each new-round re-freeze DID to the placement reference.

Why (Akadir, 2026-08-02/03). New studies arrive daily, every ingest forces a re-freeze, and a
re-freeze moves the reference every patient is placed against. Backwards reproducibility is NOT the
goal here — the goal is to SEE whether a batch moved the reference, and whether it moved it because
the new studies are genuinely different (outliers) rather than merely more numerous.

So each freeze gets three things:

  1. an ARCHIVED copy — `per_study_cohort_new_r<N>.csv`, kept forever — and, since 2026-09-03, its
     landmark evidence `new_round_landmark_r<N>.json` (per-frame values + which frames, uid-keyed,
     name-free) and `new_round_cohort_provenance_r<N>.json`, because the working landmark JSON and
     the provenance file are both overwritten by every apply. `r` for refreeze, deliberately
     NOT `v`: the v1.x ladder is the OLD-round VERSION axis (v1.0 -> v1.3, successive re-bases of the
     same 282), and the new round is the POPULATION axis. Reusing `v` is what made the two look like
     one ladder in the first place.
  2. a LOG ROW in `new_round_freeze_log.jsonl` — n, what was added, and per-feature distribution stats.
  3. the two numbers that answer "did it change a lot":
       * SHIFT   — for studies present in BOTH references, how far their percentile moved.
                   A big shift means the reference itself moved under everyone's feet.
       * OUTLIER — how many of the ADDED studies fall outside the PREVIOUS reference's p05..p95.
                   A high count means this batch is genuinely a different population, not just more of it.

No threshold is hard-coded for "a lot". Two or three freezes of real numbers should set it; a
threshold invented before the data is a guess wearing a uniform.

    python scripts/cohort_freeze_log.py --record            # archive + log the CURRENT cohort file
    python scripts/cohort_freeze_log.py --record --from-git 89defb0   # backfill a historical freeze
    python scripts/cohort_freeze_log.py --report            # human-readable, newest transition
    python scripts/cohort_freeze_log.py --report --all      # every transition
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from statistics import median

REPO = Path(__file__).resolve().parent.parent
STATS = REPO / "data" / "cohort_stats"
LIVE = STATS / "per_study_cohort_new.csv"
LOG = STATS / "new_round_freeze_log.jsonl"
ARCHIVE = "per_study_cohort_new_r{n}.csv"

# Columns that are labels, not measurements. Everything else numeric is tracked — the point is to
# catch a shift nobody predicted, so narrowing to the three score constructs would defeat it.
NON_NUMERIC = {"study_code", "sex", "quality_label", "qmt_provenance", "triple_rated"}


# --------------------------------------------------------------------------- reading
def _rows(text: str) -> list[dict]:
    return list(csv.DictReader(text.splitlines()))


def _read_live() -> list[dict]:
    return _rows(LIVE.read_text(encoding="utf-8"))


def _read_git(ref: str) -> list[dict]:
    out = subprocess.run(["git", "show", f"{ref}:data/cohort_stats/per_study_cohort_new.csv"],
                         cwd=REPO, capture_output=True, text=True)
    if out.returncode != 0:
        sys.exit(f"cannot read the cohort file at {ref}: {out.stderr.strip()}")
    return _rows(out.stdout)


def _numeric(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    cols = []
    for c in rows[0]:
        if c in NON_NUMERIC:
            continue
        vals = [r.get(c) for r in rows]
        if any(_f(v) is not None for v in vals):
            cols.append(c)
    return cols


def _f(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if x != x else x        # NaN out


# --------------------------------------------------------------------------- stats
def _pct(sorted_vals: list[float], q: float):
    """Plain nearest-rank percentile; no numpy dependency, and ties handled the same way both sides."""
    if not sorted_vals:
        return None
    i = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[i]


def _summary(rows: list[dict], col: str) -> dict | None:
    vals = sorted(v for v in (_f(r.get(col)) for r in rows) if v is not None)
    if not vals:
        return None
    n = len(vals)
    mean = sum(vals) / n
    sd = (sum((x - mean) ** 2 for x in vals) / (n - 1)) ** 0.5 if n > 1 else 0.0
    return {"n": n, "mean": round(mean, 4), "sd": round(sd, 4),
            "p05": _pct(vals, .05), "p25": _pct(vals, .25), "p50": _pct(vals, .50),
            "p75": _pct(vals, .75), "p95": _pct(vals, .95)}


def _percentile_of(value: float, ref_sorted: list[float]) -> float:
    """Where `value` sits in a reference distribution, 0..100 — the same 'fraction below' the app's
    placement uses. Computed identically against both references so the DIFFERENCE is meaningful."""
    if not ref_sorted:
        return float("nan")
    below = sum(1 for x in ref_sorted if x < value)
    return 100.0 * below / len(ref_sorted)


def shift(prev: list[dict], curr: list[dict], cols: list[str]) -> dict:
    """For studies in BOTH references, how far did their percentile move? This is the number that
    says whether the reference moved under everyone's feet."""
    pcodes = {r["study_code"] for r in prev}
    both = [r for r in curr if r["study_code"] in pcodes]
    out = {}
    for c in cols:
        pref = sorted(v for v in (_f(r.get(c)) for r in prev) if v is not None)
        cref = sorted(v for v in (_f(r.get(c)) for r in curr) if v is not None)
        if not pref or not cref:
            continue
        deltas = []
        for r in both:
            v = _f(r.get(c))
            if v is None:
                continue
            deltas.append(abs(_percentile_of(v, cref) - _percentile_of(v, pref)))
        if deltas:
            deltas.sort()
            out[c] = {"n": len(deltas), "median_abs": round(median(deltas), 2),
                      "p95_abs": round(_pct(deltas, .95), 2), "max_abs": round(max(deltas), 2)}
    return out


def outliers(prev: list[dict], added: list[dict], cols: list[str]) -> dict:
    """How many ADDED studies fall outside the PREVIOUS reference's p05..p95, per feature. High counts
    mean this batch is a different population, not simply more of the same one."""
    out = {}
    for c in cols:
        s = _summary(prev, c)
        if not s or s["p05"] is None:
            continue
        lo, hi, n = s["p05"], s["p95"], 0
        codes = []
        for r in added:
            v = _f(r.get(c))
            if v is not None and (v < lo or v > hi):
                n += 1
                codes.append(r["study_code"])
        if n:
            out[c] = {"n_outside_p05_p95": n, "of_added": len(added), "study_codes": sorted(codes)}
    return out


# --------------------------------------------------------------------------- log
def _log_rows() -> list[dict]:
    if not LOG.exists():
        return []
    return [json.loads(ln) for ln in LOG.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _next_n() -> int:
    existing = [int(m.group(1)) for p in STATS.glob("per_study_cohort_new_r*.csv")
                if (m := re.fullmatch(r"per_study_cohort_new_r(\d+)\.csv", p.name))]
    return max(existing, default=0) + 1


PROV_LIVE = STATS / "new_round_cohort_provenance.json"
PROV_ARCHIVE = "new_round_cohort_provenance_r{n}.json"


def _build_landmark_snapshot(n: int, date: str, uids: set):
    """The r<N> landmark evidence (per-frame values + frame identities, uid-keyed, name-free) —
    VALIDATED before the archive and the log row are written, so a refusal (a name would leak, the
    working JSON does not describe this cohort, an r<N> file already exists) leaves nothing half
    recorded and the idempotency guard cannot lock the retry out. None when the working JSON is
    absent (a cohort assembled elsewhere) — printed, not fatal."""
    sys.path.insert(0, str(REPO / "scripts"))
    from derive_new_round_cohort import (LANDMARK_JSON, build_landmark_snapshot,
                                         landmark_snapshot_path)
    src = REPO / LANDMARK_JSON
    if not src.exists():
        print(f"  (no {LANDMARK_JSON} — landmark snapshot for r{n} not written)")
        return None
    out = landmark_snapshot_path(n, STATS)
    if out.exists():
        raise FileExistsError(f"{out} already exists before r{n} was recorded — a stale manual "
                              f"--snapshot? remove that file, then re-record (a forced manual "
                              f"snapshot leaves it in place and this check fires again)")
    return build_landmark_snapshot(json.loads(src.read_text(encoding="utf-8")),
                                   refreeze=n, date=date, expect_uids=uids)


def _write_freeze_evidence(n: int, doc) -> None:
    """After the log row: the landmark snapshot, and the provenance file archived per freeze (it is
    overwritten by every apply, yet it is what says which served numbers pooled contributed frames or
    a run-bound texture release — without it "shown = served" cannot be audited for r<N> later)."""
    from derive_new_round_cohort import write_landmark_snapshot
    if doc is not None:
        out = write_landmark_snapshot(doc, out_dir=STATS)
        print(f"  landmark snapshot -> {out.name}  ({doc['n_studies']} studies, "
              f"frame_identity {doc['frame_identity']})")
    if PROV_LIVE.exists():
        dst = STATS / PROV_ARCHIVE.format(n=n)
        dst.write_text(PROV_LIVE.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  provenance archived -> {dst.name}")


def record(rows: list[dict], *, date: str, note: str, snapshot: bool = True) -> dict:
    n = _next_n()
    prior = _log_rows()
    prev_rows: list[dict] = []
    if prior:
        p = STATS / ARCHIVE.format(n=prior[-1]["refreeze"])
        if p.exists():
            prev_rows = _rows(p.read_text(encoding="utf-8"))

    cols = _numeric(rows)
    pcodes = {r["study_code"] for r in prev_rows}
    ccodes = {r["study_code"] for r in rows}
    added = [r for r in rows if r["study_code"] not in pcodes]

    entry = {
        "refreeze": n, "date": date, "note": note,
        "n_studies": len(rows),
        "n_added": len(added), "n_removed": len(pcodes - ccodes),
        "added_study_codes": sorted(r["study_code"] for r in added),
        "removed_study_codes": sorted(pcodes - ccodes),
        "prev_refreeze": prior[-1]["refreeze"] if prior else None,
        "prev_n": len(prev_rows) or None,
        "features": {c: _summary(rows, c) for c in cols},
        "shift_vs_prev": shift(prev_rows, rows, cols) if prev_rows else {},
        "outliers_vs_prev": outliers(prev_rows, added, cols) if prev_rows and added else {},
    }
    # evidence is validated BEFORE the first write (see _build_landmark_snapshot)
    snap_doc = _build_landmark_snapshot(n, date, ccodes) if snapshot else None
    (STATS / ARCHIVE.format(n=n)).write_text(LIVE.read_text(encoding="utf-8")
                                             if rows is not None and LIVE.exists() else "",
                                             encoding="utf-8")
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    if snapshot:
        _write_freeze_evidence(n, snap_doc)
    return entry


def _archive_from(rows: list[dict], n: int) -> None:
    """Write an archive copy for a backfilled (git-sourced) freeze, since LIVE is not its content."""
    if not rows:
        return
    p = STATS / ARCHIVE.format(n=n)
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


# --------------------------------------------------------------------------- report
def report(entries: list[dict], *, top: int = 8) -> None:
    for e in entries:
        print(f"\n{'=' * 78}")
        print(f"  refreeze r{e['refreeze']}   {e['date']}   n={e['n_studies']}"
              f"   (+{e['n_added']} / -{e['n_removed']})   {e['note']}")
        if e["prev_refreeze"] is None:
            print("  (first recorded freeze — no prior reference to compare against)")
            continue
        print(f"  vs r{e['prev_refreeze']} (n={e['prev_n']})")

        sh = e["shift_vs_prev"]
        print("\n  SHIFT — |Δ percentile| for studies present in BOTH references")
        if not sh:
            print("     (none computable)")
        else:
            worst = sorted(sh.items(), key=lambda kv: -kv[1]["max_abs"])[:top]
            print(f"     {'feature':<28} {'median':>8} {'p95':>8} {'max':>8}")
            for c, s in worst:
                print(f"     {c:<28} {s['median_abs']:>8.2f} {s['p95_abs']:>8.2f} {s['max_abs']:>8.2f}")
            allmax = max(s["max_abs"] for s in sh.values())
            allmed = median([s["median_abs"] for s in sh.values()])
            print(f"     -> across all {len(sh)} features: median-of-medians {allmed:.2f} pts,"
                  f" worst single move {allmax:.2f} pts")

        ol = e["outliers_vs_prev"]
        print(f"\n  OUTLIERS — added studies outside r{e['prev_refreeze']}'s p05..p95")
        if not ol:
            print("     none — the added batch sits inside the previous distribution on every feature")
        else:
            worst = sorted(ol.items(), key=lambda kv: -kv[1]["n_outside_p05_p95"])[:top]
            for c, s in worst:
                print(f"     {c:<28} {s['n_outside_p05_p95']:>3}/{s['of_added']} added outside"
                      f"   e.g. {', '.join(s['study_codes'][:4])}")
            # Per-feature counts say a batch differs; they do NOT say whether that is diffuse or a
            # handful of studies. On r3 it was six studies of twenty-three, three of them outside on
            # SEVEN of twelve features and all from one operator's adjacent sessions — a shape the
            # per-feature view cannot show, and the one worth opening in the app.
            per_study: dict = {}
            for c, s in ol.items():
                for code in s["study_codes"]:
                    per_study.setdefault(code, []).append(c)
            multi = sorted(per_study.items(), key=lambda kv: -len(kv[1]))
            n_feat = len(e["features"])
            heavy = [(k, v) for k, v in multi if len(v) >= 4]
            print(f"\n  CONCENTRATION — {len(heavy)} of {e['n_added']} added studies are outliers on "
                  f">=4 of {n_feat} features"
                  + (f"; {len(per_study)} appear at least once" if per_study else ""))
            for code, feats in multi[:top]:
                print(f"     {code}  {len(feats):>2}/{n_feat}  {', '.join(sorted(feats)[:5])}"
                      + (" …" if len(feats) > 5 else ""))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--record", action="store_true", help="archive + log a freeze")
    ap.add_argument("--from-git", metavar="REF",
                    help="backfill: take the cohort file from this git ref instead of the live one")
    ap.add_argument("--date", help="freeze date (default: the live file's mtime, or today)")
    ap.add_argument("--note", default="", help="one line on what this freeze was")
    ap.add_argument("--report", action="store_true", help="print the human-readable comparison")
    ap.add_argument("--all", action="store_true", help="report every transition, not just the newest")
    a = ap.parse_args(argv)

    if a.record:
        rows = _read_git(a.from_git) if a.from_git else _read_live()
        date = a.date or (time.strftime("%Y-%m-%d", time.localtime(LIVE.stat().st_mtime))
                          if LIVE.exists() and not a.from_git else time.strftime("%Y-%m-%d"))
        # Idempotent: the pipeline runs this after EVERY cohort stage, including re-runs and a
        # declined freeze. Recording an unchanged reference again would mint an r(N+1) with
        # n_added=0 and make the log's own row count a lie about how often the reference moved.
        prior = _log_rows()
        if prior:
            last = STATS / ARCHIVE.format(n=prior[-1]["refreeze"])
            if last.exists():
                same = ({r["study_code"] for r in _rows(last.read_text(encoding="utf-8"))}
                        == {r["study_code"] for r in rows})
                if same:
                    print(f"unchanged since r{prior[-1]['refreeze']} "
                          f"(n={prior[-1]['n_studies']}) — nothing recorded")
                    return 0
        n_before = _next_n()
        e = record(rows, date=date, note=a.note, snapshot=not a.from_git)
        if a.from_git:                      # LIVE is not this freeze's content — rewrite the archive
            _archive_from(rows, n_before)
        print(f"recorded r{e['refreeze']}  n={e['n_studies']}  (+{e['n_added']}/-{e['n_removed']})"
              f"  -> {LOG.name} + {ARCHIVE.format(n=e['refreeze'])}")
        return 0

    if a.report:
        entries = _log_rows()
        if not entries:
            return print("no freezes recorded yet — run --record") or 1
        report(entries if a.all else entries[-1:])
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python
"""Classify every legacy study by PROBE, from geometry — and name the 128LH set that is set aside.

Why. The legacy backup was collected on TWO Leltek probes, **128L** and **128LH**, and the probe is
NOT in the DICOM — it is recoverable only from the field of view. The two differ in echo texture, so
mixing them is a texture confound, and `int_cv_within` carries 43% of the condition score. Round-3
(protocol v8) is 128L only; the legacy corpus is mixed. The 128LH studies are therefore SET ASIDE.

This was known on 2026-05-29 but the only record was a line inside `HANDOFF_ARCHIVE_2026-06-18.md`,
a frozen archive that `CLAUDE.md` tells a fresh session not to read, plus two constants in
`leltek_deconfliction.py`. No memory, no canon doc, and no machine-readable list of WHICH studies —
so a later reader met 37 unexplained `cohort=unknown` rows with no way to learn why. This script is
that list, regenerated from evidence rather than remembered.

The test is objective. `FOV = jpg_width_px x depth_cm`, where `depth_cm = dcm_rows x pixel_spacing_y / 10`,
is a probe constant: ~1967 for 128L, ~1310 for 128LH (`leltek_deconfliction.FOV_CONST_128L`). On the
3-rater log the two clusters are tight and DISJOINT — 128L spans 1956-1973, 128LH spans 1300-1319 —
so the assignment is unambiguous wherever the geometry fields are present.

    python scripts/classify_probe.py            # report
    python scripts/classify_probe.py --write     # + data/cohort_stats/probe_classification.csv
"""
from __future__ import annotations

import argparse
import collections
import csv
import statistics
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

DECISION_LOG = REPO / "exports" / "decision_log_full_3operators.csv"
OLD_COHORT = REPO / "data" / "cohort_stats" / "per_study_cohort.csv"
OUT = REPO / "data" / "cohort_stats" / "probe_classification.csv"

FOV_128L = 1967        # width_px * depth_cm — mirrors leltek_deconfliction.FOV_CONST_128L
FOV_128LH = 1310
SET_ASIDE = "128LH"    # the probe Round-3 does not use


def fov_of(row: dict) -> float | None:
    """width_px x depth_cm, or None when the log lacks the geometry fields."""
    try:
        w = float(row["jpg_width"])
        dy = float(row["pixel_spacing_y"])
        rows = float(row["dcm_rows"])
    except (KeyError, TypeError, ValueError):
        return None
    if not (w and dy and rows):
        return None
    return w * (rows * dy / 10.0)


def classify(fov: float) -> str:
    return "128L" if abs(fov - FOV_128L) < abs(fov - FOV_128LH) else "128LH"


def geometry_map(log: list[dict]) -> dict[float, tuple[str, str]]:
    """jpg_width -> the (pixel_spacing_y, dcm_rows) it always comes with.

    Some decision-log rows have `jpg_width` but blank `pixel_spacing_y`/`dcm_rows`, so FOV cannot be
    computed for them — 19 studies, all inside the frozen 282, whose DICOMs are not in the backup.
    They are recoverable because the device offers a discrete depth ladder: on this corpus **each of
    the 14 distinct widths maps to exactly ONE (spacing, rows) pair**, with no exceptions and with
    3-332 observations behind each. So the missing cells can be refilled EXACTLY rather than guessed.

    Built at runtime and only from unambiguous widths: if a width were ever seen with two different
    pairs it is dropped, and studies needing it stay `unknown`. The decision log itself is never
    written to — it is a historical export; the refill lives here and is recorded per row in
    `classified_by`.
    """
    seen: dict[float, set] = {}
    for r in log:
        try:
            w = float(r["jpg_width"])
            dy = (r["pixel_spacing_y"] or "").strip()
            rows = (r["dcm_rows"] or "").strip()
        except (KeyError, TypeError, ValueError):
            continue
        if w and dy and rows:
            seen.setdefault(w, set()).add((dy, rows))
    return {w: next(iter(v)) for w, v in seen.items() if len(v) == 1}


def build() -> list[dict]:
    log = list(csv.DictReader(open(DECISION_LOG, encoding="utf-8")))
    cohort = {r["study_code"] for r in csv.DictReader(open(OLD_COHORT, encoding="utf-8"))}

    per: dict[str, list] = collections.defaultdict(list)
    status: dict[str, dict] = collections.defaultdict(dict)
    for r in log:
        f = fov_of(r)
        if f is not None:
            per[r["study_code"]].append(f)
        status[r["study_code"]][r["operator_id"]] = r["decision_status"]

    # Refill the missing geometry from the width map, then classify EVERYTHING by one criterion —
    # FOV — rather than keeping a second, weaker rule for the rows that were incomplete.
    gmap = geometry_map(log)
    refilled: dict[str, bool] = {}
    for r in log:
        if fov_of(r) is not None:
            continue
        try:
            w = float(r["jpg_width"])
        except (KeyError, TypeError, ValueError):
            continue
        pair = gmap.get(w)
        if not pair:
            continue
        f = w * (float(pair[1]) * float(pair[0]) / 10.0)
        per[r["study_code"]].append(f)
        refilled[r["study_code"]] = True

    out = []
    for code in sorted(status):
        vals = per.get(code) or []
        fov = statistics.median(vals) if vals else None
        probe = classify(fov) if fov is not None else "unknown"
        how = ("fov(refilled)" if refilled.get(code) else "fov") if fov is not None else ""
        verdicts = status[code]
        all_failed = bool(verdicts) and all(v == "quality_failed" for v in verdicts.values())
        out.append({
            "study_code": code,
            "probe": probe,
            "fov_const": f"{fov:.0f}" if fov is not None else "",
            "classified_by": how,
            "n_frames_measured": len(vals),
            "raters_all_quality_failed": str(all_failed),
            "in_old_cohort_282": str(code in cohort),
            # A study is set aside for the PROBE, not for a rater's verdict: the geometry is the
            # evidence and it does not depend on who reviewed the study or how carefully.
            "set_aside": str(probe == SET_ASIDE),
            "reason": ("wrong probe (Leltek 128LH; Round-3 and the protocol are 128L only) — "
                       "different FOV and echo texture, a confound for int_cv_within"
                       if probe == SET_ASIDE else
                       "unknown probe — no FOV and no unambiguous width"
                       if probe == "unknown" else ""),
        })
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true", help=f"write {OUT.relative_to(REPO)}")
    a = ap.parse_args(argv)

    rows = build()
    by_probe = collections.Counter(r["probe"] for r in rows)
    print(f"=== PROBE CLASSIFICATION === {len(rows)} legacy studies in the 3-rater log")
    for p, n in sorted(by_probe.items()):
        vals = [float(r["fov_const"]) for r in rows if r["probe"] == p and r["fov_const"]]
        span = f"  FOV {min(vals):.0f}–{max(vals):.0f}" if vals else ""
        print(f"   {p:<9} {n:>4}{span}")

    aside = [r for r in rows if r["set_aside"] == "True"]
    print(f"\n   SET ASIDE (128LH): {len(aside)}")
    print(f"   of which all 3 raters also failed: "
          f"{sum(1 for r in aside if r['raters_all_quality_failed'] == 'True')}")

    contaminated = [r for r in aside if r["in_old_cohort_282"] == "True"]
    unknown_in = [r for r in rows if r["probe"] == "unknown" and r["in_old_cohort_282"] == "True"]
    print(f"\n   ⚠ 128LH studies INSIDE the frozen 282 cohort: {len(contaminated)}")
    if contaminated:
        print(f"     {', '.join(sorted(r['study_code'] for r in contaminated))}")
    print(f"   ⚠ unknown-probe studies inside the 282: {len(unknown_in)}")

    if a.write:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        with open(OUT, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\n   wrote {OUT.relative_to(REPO)}")
    else:
        print(f"\n   (report only — rerun with --write to emit {OUT.relative_to(REPO)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

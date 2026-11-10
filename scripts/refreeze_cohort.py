#!/usr/bin/env python
"""Re-freeze the placement cohort with the set-aside (wrong-probe) studies removed.

Why. `per_study_cohort_v1.2.csv` is the reference EVERY patient's percentile is computed against, and
it contains 11 studies shot on the Leltek **128LH** probe — a different field of view and a different
echo texture, on the axis that carries 43% of the condition score. They are there because a rater
judged them CLEAR, which is a quality judgement; comparability is a separate question and the geometry
answers it (`scripts/classify_probe.py`). Round-3 is 128L only, so the legacy reference should be too.

This is a FREEZE: it changes numbers the app serves. So it is versioned, never in-place — the previous
cohort, models and options stay on disk for rollback, exactly as the v1.1 -> v1.2 re-base did — and it
REPORTS the before/after before writing anything.

    python scripts/refreeze_cohort.py                 # report only: what would change
    python scripts/refreeze_cohort.py --apply         # write the new version
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
STATS = REPO / "data" / "cohort_stats"
PROBE = STATS / "probe_classification.csv"
PY = str(REPO / ".venv" / "bin" / "python")


def set_aside_codes() -> set[str]:
    if not PROBE.is_file():
        sys.exit(f"missing {PROBE.relative_to(REPO)} — run scripts/classify_probe.py --write first")
    return {r["study_code"] for r in csv.DictReader(open(PROBE, encoding="utf-8"))
            if r["set_aside"] == "True"}


def fit(cohort: Path, models: Path, options: Path) -> dict:
    """Run the project's own freeze + options harness against `cohort`, capturing the metrics."""
    env = dict(os.environ,
               CONDITION_COHORT_CSV=str(cohort),
               CONDITION_MODEL_OUT=str(models),
               CONDITION_OPTIONS_OUT=str(options))
    out = {}
    for script in ("scripts/preflight_helpers/condition_model.py", "scripts/condition_options_compare.py"):
        p = subprocess.run([PY, script], cwd=REPO, env=env, capture_output=True, text=True)
        if p.returncode != 0:
            sys.exit(f"{script} failed:\n{p.stdout}\n{p.stderr}")
        out[script] = p.stdout
    return out


def metrics(models: Path, options: Path) -> dict:
    """Pull the numbers the freeze is judged on out of the two artifacts."""
    m = json.loads(models.read_text())
    head = m["headline"]
    fr = m["models"][head]
    boss = json.loads(options.read_text()).get("boss_table", {}) if options.is_file() else {}
    row = next((v for k, v in boss.items() if k.startswith(f"{head}:")), {})
    # The axis shares are |beta| normalised — the Tissue/STAR/Fat mix the whitepaper and UI cite.
    b = [abs(x) for x in fr["beta"]]
    tot = sum(b) or 1.0
    shares = {c: round(100 * x / tot) for c, x in zip(fr["cols"], b)}
    return {"n_fit": fr["n_fit"], "n_cohort": fr["n_cohort"], "qwk": row.get("qwk"),
            "exact_pct": row.get("exact_pct"), "shares": shares,
            "bands": [fr["boundary_int_poor"], fr["boundary_pres_int"]]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="per_study_cohort_v1.2.csv")
    ap.add_argument("--version", default="v1.3", help="version tag for the new artifacts")
    ap.add_argument("--apply", action="store_true", help="write the new cohort/model/options")
    a = ap.parse_args(argv)

    src = STATS / a.source
    rows = list(csv.DictReader(open(src, encoding="utf-8")))
    aside = set_aside_codes()
    keep = [r for r in rows if r["study_code"] not in aside]
    dropped = [r["study_code"] for r in rows if r["study_code"] in aside]

    def eligible(rs):
        return sum(1 for r in rs
                   if (r.get("star_ratio") or "").strip() and (r.get("quality_label") or "").strip())

    print(f"=== RE-FREEZE {a.source} -> {a.version} ===")
    print(f"  source rows          {len(rows)}   fit-eligible {eligible(rows)}")
    print(f"  set aside (128LH)    {len(dropped)}   {', '.join(sorted(dropped))}")
    print(f"  new cohort rows      {len(keep)}   fit-eligible {eligible(keep)}")

    dst = STATS / f"per_study_cohort_{a.version}.csv"
    models = STATS / f"condition_models_{a.version}.json"
    options = STATS / f"condition_options_{a.version}.json"
    tmp = STATS / f".refreeze_tmp_{a.version}.csv"

    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(keep)

    print(f"\n  fitting {a.version} …")
    fit(tmp, models if a.apply else STATS / f".tmp_models_{a.version}.json",
        options if a.apply else STATS / f".tmp_options_{a.version}.json")
    new = metrics(models if a.apply else STATS / f".tmp_models_{a.version}.json",
                  options if a.apply else STATS / f".tmp_options_{a.version}.json")
    src_tag = a.source.replace("per_study_cohort_", "").replace(".csv", "")
    old = metrics(STATS / f"condition_models_{src_tag}.json",
                  STATS / f"condition_options_{src_tag}.json")

    print("\n  ── BEFORE vs AFTER (headline model) " + "─" * 30)
    print(f"     {'':<12}{'before':>22}{'after':>22}")
    for k in ("n_cohort", "n_fit", "qwk", "exact_pct", "bands"):
        print(f"     {k:<12}{str(old.get(k)):>22}{str(new.get(k)):>22}")
    print(f"     shares      {str(old.get('shares')):>22}\n                 {str(new.get('shares')):>22}")

    if a.apply:
        tmp.replace(dst)
        print(f"\n  wrote {dst.relative_to(REPO)} · {models.name} · {options.name}")
        print(f"  NEXT: repoint cohort_placement._COHORT_FILE/_MODEL_FILE/_OPTIONS_FILE to {a.version},"
              f" keep {a.source} for rollback, then run the floor + render.")
    else:
        tmp.unlink(missing_ok=True)
        for f in (STATS / f".tmp_models_{a.version}.json", STATS / f".tmp_options_{a.version}.json"):
            f.unlink(missing_ok=True)
        print("\n  report only — nothing written. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

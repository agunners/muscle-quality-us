"""Same-person pairs across the old (282) and new (v8) cohorts — the cross-device bridge census.

Receipt script for the 2026-08-17 finding (docs/reports/2026-08-17_answers_state_of_the_meter.html,
"What the cross-examination itself produced"): 11 people appear in BOTH collection rounds, so the
old->new offset can be measured within-person. First numbers (2026-08-17, qa_master 2026-08-14 build):
full thickness +2.26 mm (10/11 positive; paired t p=.014, Wilcoxon p=.024), QMT +9.69 mm (8/8),
int_cv +0.090 = +0.46 old-cohort SD (6/9, Wilcoxon p=.074 — not significant at n=9).

This is a CENSUS, not the analysis of record: the pre-registered bridge analysis (leg-side parity,
capture-role parity, per-feature paired tests) is owed before these numbers travel further.

PHI: reads patient-bearing study_name ONLY to join; prints counts, uid prefixes and numbers — never a
name or name token. Keep it that way.

Run:  python3 scripts/analysis/same_person_pairs.py     (stdlib only; needs data/qa_master.csv)
"""
from __future__ import annotations

import csv
import math
import pathlib
import re
import statistics as st
import unicodedata

QA_MASTER = pathlib.Path(__file__).resolve().parents[2] / "data" / "qa_master.csv"

# Tokens the new-round naming schema adds around the person's name (leg, probe, boilerplate).
SCHEMA_TOKENS = {"sag", "sol", "left", "right", "pro", "probe", "study", "final", "kayit"}

FEATURES = [
    "int_cv_within",
    "full_thickness_q50_mm",
    "rf_thickness_mm",
    "fat_thickness_q50_mm",
    "qmt_mm",
]


def norm_tokens(name: str) -> frozenset[str]:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    toks = [t for t in re.split(r"[^a-z]+", s) if len(t) >= 3]
    return frozenset(t for t in toks if t not in SCHEMA_TOKENS)


def fval(row: dict, col: str):
    v = (row.get(col) or "").strip()
    return float(v) if v and v != "nan" else None


def main() -> None:
    rows = list(csv.DictReader(open(QA_MASTER)))
    old = [r for r in rows if r.get("cohort_round") == "old"]
    new = [r for r in rows if r.get("cohort_round") != "old"]

    pairs = []
    for nr in new:
        nn = norm_tokens(nr["study_name"])
        if not nn:
            continue
        for orow in old:
            on = norm_tokens(orow["study_name"])
            # same person: >=2 shared tokens (name+surname) and the smaller set fully contained
            if len(nn & on) >= 2 and min(len(nn), len(on)) == len(nn & on):
                pairs.append((nr, orow))

    print(f"old rows {len(old)} · new rows {len(new)} · same-person pairs {len(pairs)}")
    for nr, orow in pairs:
        sex_ok = (nr.get("sex") or "?") + "/" + (orow.get("sex") or "?")
        print(f"  pair {nr['study_uid'][:6]} <- {orow['study_uid'][:6]}  sex {sex_ok}"
              f"  age {nr.get('age')}/{orow.get('age')}")

    for col in FEATURES:
        old_all = [v for v in (fval(r, col) for r in old) if v is not None]
        sd_old = st.stdev(old_all) if len(old_all) > 1 else float("nan")
        ds = [(fval(n, col), fval(o, col)) for n, o in pairs]
        ds = [(a, b) for a, b in ds if a is not None and b is not None]
        if not ds:
            print(f"{col:24} no paired values")
            continue
        diffs = [a - b for a, b in ds]
        md = st.mean(diffs)
        sdd = st.stdev(diffs) if len(diffs) > 1 else float("nan")
        pos = sum(1 for d in diffs if d > 0)
        t = md / (sdd / math.sqrt(len(diffs))) if sdd and sdd > 0 else float("nan")
        print(f"{col:24} n_pair={len(ds):2}  mean new-old {md:+.3f}  (SD_diff {sdd:.3f}, t={t:+.2f})"
              f"  in old-SD units {md / sd_old:+.2f}  sign +{pos}/{len(ds)}")


if __name__ == "__main__":
    main()

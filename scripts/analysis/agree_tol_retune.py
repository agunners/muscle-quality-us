"""AGREE_TOL retune — replace the provisional 10% with a measured tolerance, and rank the review.

rf_reliability.AGREE_TOL (0.10, "provisional; retune from the RF max-thickness MDC") gates texture
trust: femur vs centre-RF max FULL thickness must agree within it. This script measures what an
HONEST disagreement looks like, so the tolerance is a quantile of measured noise instead of a
round number, and Akadir's manual review of the withheld studies runs worst-first.

Inputs (both already on disk — no GPU, no recompute):
  * scratchpad/new_round_landmark_full.json — the freeze's own per-study, per-view max-thickness
    values: EXACTLY the numbers the gate compared (125 studies).
  * scripts/analysis/results/repeatability_n116.csv — the tracked repeatability harness's receipt
    for `full_max_thickness_mm` (the gate's anchor measure): within-study CV and MDC95 at n=116.

Method, in the gate's own units (relative difference d = |a-b| / max(a,b), rf_reliability._rel):
  1. same-view noise from the RECEIPT: cvw for full_max_thickness_mm. Two independent honest
     views then differ with sigma_d = sqrt(2)*cvw, so the chance-failure rate of the gate at
     tolerance t is 2*(1-Phi(t/sigma_d)) — computed per candidate tolerance, times n studies.
  2. cross-view bias: on gate-VALIDATED studies (d <= 10%), the signed (center-femur)/femur
     distribution — the systematic offset two healthy views carry (resolution, boundary
     rendering). Any real bias makes the chance-failure estimate CONSERVATIVE (worse).
  3. the empirical cross-view d distribution over all studies, and what each candidate tolerance
     would withhold — including where the current 17 sit (worst-first ranking, clip side named).

Output: results/agree_tol_retune.json + a printed report. Pure read-only analysis.

  source scripts/env.sh && .venv/bin/python scripts/analysis/agree_tol_retune.py
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
LANDMARK = REPO / "scratchpad" / "new_round_landmark_full.json"
REPEAT = REPO / "scripts" / "analysis" / "results" / "repeatability_n116.csv"
REGISTRY = REPO / "data" / "study_registry.csv"
QA = REPO / "data" / "qa_master.csv"
OUT = Path(__file__).resolve().parent / "results" / "agree_tol_retune.json"

CANDIDATE_TOLS = (0.08, 0.10, 0.12, 0.15, 0.18)


def _rel(a: float, b: float) -> float:
    m = max(abs(a), abs(b))
    return abs(a - b) / m if m > 0 else 0.0


def _uid(dirname: str) -> str:
    return "NR" + hashlib.sha256(dirname.encode("utf-8")).hexdigest()[:8].upper()


def main() -> None:
    # ---- display-code map (uids only in the output; codes for Akadir's reading)
    code = {}
    with open(REGISTRY, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r.get("study_uid"):
                code[r["study_uid"]] = r.get("display_code") or "?"

    # ---- 1. same-view noise from the repeatability harness's own receipt
    rep = next(r for r in csv.DictReader(open(REPEAT, encoding="utf-8"))
               if r["stem"] == "full_max_thickness_mm")
    cvw = float(rep["cvw"]) / 100.0                      # within-study CV, one view
    sigma_d = math.sqrt(2.0) * cvw                       # honest cross-view relative diff SD
    def chance_fail(t: float) -> float:                  # P(|d| > t) under noise alone
        return 2.0 * (1.0 - 0.5 * (1.0 + math.erf((t / sigma_d) / math.sqrt(2.0))))
    noise = {"receipt": "repeatability_n116.csv full_max_thickness_mm",
             "cvw_one_view": round(cvw, 4), "mdc95_mm": round(float(rep["mdc95"]), 3),
             "n_receipt": int(rep["n"]), "sigma_d_two_views": round(sigma_d, 4),
             "note": "assumes independent view errors and no true difference; any real "
                     "cross-view bias makes the chance-failure rate HIGHER than this"}

    # ---- 2 + 3. the gate's own inputs, all studies
    rows = json.load(open(LANDMARK))
    per_study = []
    for r in rows:
        f = ((r.get("vals") or {}).get("femur") or {}).get("full_max_thickness_mm")
        c = ((r.get("vals") or {}).get("rf") or {}).get("full_max_thickness_mm")
        s = ((r.get("vals") or {}).get("sweep") or {}).get("full_max_thickness_mm")
        uid = _uid(r["study"])
        if f is None or c is None:
            per_study.append({"uid": uid, "code": code.get(uid, "?"), "d": None,
                              "note": "missing view (femur)" if f is None else "missing centre-RF"})
            continue
        d = _rel(f, c)
        side = "center SMALLER (clip signature)" if c < f else "center larger (atypical)"
        arb = None
        if s is not None:
            if _rel(s, f) <= 0.10:
                arb = "sweep sides with femur"
            elif _rel(s, c) <= 0.10:
                arb = "sweep sides with center"
            else:
                arb = "all three disagree"
        per_study.append({"uid": uid, "code": code.get(uid, "?"), "d": round(d, 4),
                          "femur_mm": round(f, 2), "center_mm": round(c, 2),
                          "sweep_mm": None if s is None else round(s, 2),
                          "signed_center_vs_femur": round((c - f) / f, 4),
                          "side": side, "arbiter": arb})

    have = [p for p in per_study if p["d"] is not None]
    validated = [p for p in have if p["d"] <= 0.10]
    signed = np.array([p["signed_center_vs_femur"] for p in validated])
    bias = {"n": len(validated), "mean": round(float(signed.mean()), 4),
            "sd": round(float(signed.std()), 4),
            "note": "signed (center-femur)/femur on gate-validated studies"}

    d_all = np.array([p["d"] for p in have])
    what_if = {f"{t:.2f}": {"withheld": int((d_all > t).sum()),
                            "chance_fail_rate": round(chance_fail(t), 4),
                            "expected_by_noise_alone": round(chance_fail(t) * len(have), 1)}
               for t in CANDIDATE_TOLS}

    # ---- the withheld set today, worst-first (the review queue)
    withheld_uids = set()
    if QA.exists():
        for r in csv.DictReader(open(QA, encoding="utf-8")):
            if (r.get("int_cv_trust") or "") == "withheld":
                withheld_uids.add(r["study_uid"])
    queue = sorted((p for p in have if p["uid"] in withheld_uids),
                   key=lambda p: -p["d"])

    out = {"same_view_noise_rel_d": noise,
           "cross_view_bias_validated": bias,
           "cross_view_d_quantiles_all": {
               "n": len(have),
               "median": round(float(np.median(d_all)), 4),
               "q90": round(float(np.percentile(d_all, 90)), 4),
               "q95": round(float(np.percentile(d_all, 95)), 4)},
           "withheld_at_tolerance": what_if,
           "current_tol": 0.10,
           "review_queue_worst_first": queue,
           "missing_view_studies": [p for p in per_study if p["d"] is None]}
    OUT.write_text(json.dumps(out, indent=1))

    print(f"same-view noise (receipt): cvw {cvw:.3%} per view -> honest cross-view sigma_d "
          f"{sigma_d:.3%} (MDC95 {noise['mdc95_mm']} mm, n={noise['n_receipt']})")
    print(f"cross-view bias on validated (n={bias['n']}): {bias['mean']:+.3f} ± {bias['sd']:.3f}")
    print(f"cross-view d over all {len(have)}: median "
          f"{out['cross_view_d_quantiles_all']['median']:.3f} · q95 "
          f"{out['cross_view_d_quantiles_all']['q95']:.3f}")
    print("withheld at tolerance:", what_if)
    print(f"\nREVIEW QUEUE (worst first) — {len(queue)} withheld studies with both views:")
    for p in queue:
        print(f"  {p['code']:>12} {p['uid']}  d={p['d']:.3f}  femur {p['femur_mm']:.1f} vs "
              f"center {p['center_mm']:.1f} mm  · {p['side']} · {p['arbiter'] or 'no sweep'}")
    print("\nwrote", OUT)


if __name__ == "__main__":
    main()

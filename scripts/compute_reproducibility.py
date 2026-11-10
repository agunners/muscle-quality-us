#!/usr/bin/env python
"""E5 end-to-end: compute INTER-RATER reproducibility on the 3-operator decision
log (akadir / anonabx / anonacl each rated the annotated cohort). Writes
data/cohort_stats/reproducibility.json.

This is the achievable-now slice: inter-rater κ on the categorical quality label
+ ICC(2,1)/MDC95 on the continuous measurements across the 3 raters of the SAME
images. The intra-subject test–retest MDC on the paired / 24h re-scan sub-cohorts
is DATA-GATED on Round-3 (no re-scans exist yet) — this runner is the harness it
will reuse verbatim once those land.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "scripts"))
from preflight_helpers.reproducibility import (  # noqa: E402
    icc_2_1, mdc95, mean_pairwise_kappa, sem_from_icc,
)

# byte-identical to the repo's own copy (machine-transition audit 2026-09-05); the external path is the
# historical fallback and does not exist on a machine restored from the handover drive.
DLOG = _REPO / "configs" / "decision_log.csv"
if not DLOG.is_file():
    DLOG = Path(str(Path(__file__).resolve().parents[1] / "private" / "muscle_quality_exploration_codex_implementation/decision_log.csv"))
OUT = _REPO / "data/cohort_stats/reproducibility.json"
_QUALITY_ORDER = {"POOR": 0, "INTERMEDIATE": 1, "PRESERVED": 2}
_MEASUREMENTS = ["qmt_mm", "star_ratio", "rf_thickness_mm", "visible_rf_area_mm2",
                 "muscle_fat_ratio", "int_cv_within", "full_thickness_q50_mm"]


def _complete_triples(dl: pd.DataFrame, col: str) -> pd.DataFrame:
    """(study x operator) for `col`, keeping only studies every operator rated."""
    piv = dl.pivot_table(index="study_code", columns="operator_id", values=col,
                         aggfunc="first")
    return piv.dropna()


def main() -> None:
    if not DLOG.exists():
        print(f"decision log not found: {DLOG}", file=sys.stderr)
        sys.exit(1)
    dl = pd.read_csv(DLOG, low_memory=False)
    operators = sorted(str(o) for o in dl["operator_id"].dropna().unique())
    out: dict = {"operators": operators, "n_operators": len(operators),
                 "categorical": {}, "continuous": {},
                 "note": "INTER-RATER on the 3-operator annotated cohort; "
                         "intra-subject test-retest MDC is Round-3 data-gated."}

    # Categorical: quality label agreement (mean pairwise Cohen's kappa).
    if "quality_label" in dl.columns and len(operators) >= 2:
        ql = _complete_triples(dl, "quality_label")
        enc = ql.apply(lambda s: s.map(_QUALITY_ORDER)).dropna()
        if len(enc) >= 2:
            out["categorical"]["quality_label"] = {
                "mean_pairwise_kappa": round(mean_pairwise_kappa(enc.values), 4),
                "n_studies": int(len(enc)),
                "note": "3-category grade (POOR/INTERMEDIATE/PRESERVED) — harder "
                        "than the binary gate below",
            }

    # The BINARY expert gate (pass/fail) — the decision that actually gates a
    # study. Inter-rater agreement on it is the achievable reproducibility MOE
    # (kappa >= 0.6); the intra-subject test-retest ICC/MDC on re-scans stays
    # Round-3-gated (no re-acquisitions exist yet).
    if "quality_gate_pass" in dl.columns and len(operators) >= 2:
        qg = _complete_triples(dl, "quality_gate_pass")
        enc = qg.apply(lambda s: s.map({True: 1, False: 0, "True": 1, "False": 0})).dropna()
        if len(enc) >= 2:
            k = mean_pairwise_kappa(enc.values)
            out["categorical"]["quality_gate_pass"] = {
                "mean_pairwise_kappa": round(k, 4),
                "n_studies": int(len(enc)),
                "interpretation": ("near-perfect" if k >= 0.8 else "good" if k >= 0.6
                                   else "moderate" if k >= 0.4 else "fair/poor"),
                "moe_kappa_0_6": bool(k >= 0.6),
            }

    # Continuous: ICC(2,1) + MDC95 per measurement across the raters.
    skipped_cols = []
    for col in _MEASUREMENTS:
        if col not in dl.columns:
            skipped_cols.append(col)
            continue
        piv = _complete_triples(dl, col)
        piv = piv.apply(pd.to_numeric, errors="coerce").dropna()
        if len(piv) < 2 or piv.shape[1] < 2:
            continue
        # The decision-log measurements are pipeline-computed once per study and
        # replicated across operator rows, so they are identical across raters by
        # construction -> inter-rater ICC is trivially 1.0 and UNINFORMATIVE. Real
        # measurement reproducibility needs independent re-acquisitions (Round-3
        # test-retest). Detect and flag it rather than report a hollow "excellent".
        identical = float((piv.max(axis=1) - piv.min(axis=1)).abs().max()) < 1e-9
        icc = icc_2_1(piv.values)
        sd = float(piv.mean(axis=1).std(ddof=1))
        out["continuous"][col] = {
            "icc_2_1": round(icc, 4),
            "sd": round(sd, 4),
            "sem": round(sem_from_icc(sd, icc), 4),
            "mdc95": round(mdc95(sd, icc), 4),
            "n_studies": int(len(piv)),
            "identical_across_raters": identical,
            "interpretation": (
                "identical across raters — here pipeline-replicated (the log's "
                "measurements are computed once per study, NOT re-measured), so "
                "inter-rater ICC is uninformative; on Round-3 test-retest data the "
                "same equality would instead mean genuine perfect agreement — "
                "distinguish by provenance, not by this equality check" if identical
                else "excellent" if icc >= 0.90 else "good" if icc >= 0.75
                else "moderate" if icc >= 0.60 else "poor"),
        }

    if skipped_cols:
        out["skipped_measurements_absent_in_log"] = skipped_cols
        print(f"  (skipped — requested but absent in the log: {', '.join(skipped_cols)})",
              file=sys.stderr)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2))
    print(f"WROTE {OUT}")
    for name, q in out["categorical"].items():
        extra = f" — {q['interpretation']}" if "interpretation" in q else ""
        moe = "  [MOE kappa>=0.6 MET]" if q.get("moe_kappa_0_6") else ""
        print(f"  {name}: mean pairwise kappa = {q['mean_pairwise_kappa']} "
              f"(n={q['n_studies']}){extra}{moe}")
    for col, m in out["continuous"].items():
        tag = " [identical pipeline values — uninformative]" if m.get("identical_across_raters") else ""
        print(f"  {col}: ICC(2,1)={m['icc_2_1']} ({'excellent' if m['icc_2_1']>=0.9 and not m.get('identical_across_raters') else m['interpretation'].split(' —')[0]}), "
              f"MDC95={m['mdc95']} (n={m['n_studies']}){tag}")


if __name__ == "__main__":
    main()

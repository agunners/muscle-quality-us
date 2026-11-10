"""Does the texture axis predict measured force, and does brightness replace it?

The grade-based substitution (texture_substitution_test.py) had to take its
texture variants from the sibling repo, because the graded studies are the OLD
cohort and our own per-capture substrate covers only the NEW round. This is the
complementary test on the criterion DIRECTION prefers: isometric torque, which is
non-circular, on studies that are disjoint from the graded ones -- and here every
texture variant comes from OUR pipeline, one segmentation, one substrate.

It answers a narrower question than the grade test, and it must state whether it
has the power to answer it at all: if `int_cv_within` does not predict torque in
this sample, there is no association for a substitute to preserve and the arms
are uninformative. That verdict is printed first.

    .venv/bin/python scripts/analysis/texture_force_test.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROOT = Path(__file__).resolve().parent.parent.parent
RESULTS = ROOT / "scripts" / "analysis" / "results"
CAPS = RESULTS / "capture_features.csv"

CONFOUND = ["age", "sex_n", "height_cm", "weight_kg"]
TEXTURE = {"int_cv_within": "int_cv_within (incumbent)",
           "int_std": "int_std (raw dispersion)",
           "int_mean": "int_mean (plain brightness)"}
SIZE, FAT = "full_thickness_q50_mm", "fat_thickness_q50_mm"


def _ols_r2(X, y):
    A = np.column_stack([np.ones(len(X)), X])
    b, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ b
    return 1.0 - resid.var() / y.var()


def _adj(r2, n, k):
    return 1.0 - (1.0 - r2) * (n - 1) / (n - k - 1)


def main() -> int:
    caps = pd.read_csv(CAPS)
    rf = caps[caps.role == "rf"][["study_uid"] + list(TEXTURE)].copy()
    m = pd.read_csv(ROOT / "data" / "qa_master.csv")
    m = m[m.cohort_round == "new"]
    m["sex_n"] = m["sex"].astype(str).str.upper().str[0].map({"M": 1, "E": 1, "F": 0, "K": 0})

    df = rf.merge(m[["study_uid", "izo_torque_nm", SIZE, FAT] + CONFOUND], on="study_uid", how="inner")
    need = ["izo_torque_nm", SIZE, FAT] + CONFOUND + list(TEXTURE)
    df = df.dropna(subset=need).reset_index(drop=True)
    y = df["izo_torque_nm"].values.astype(float)
    print(f"rebuilt rf-captures {len(rf)} · new-round master {len(m)} · complete cases n={len(df)}")
    print(f"torque {y.min():.0f}-{y.max():.0f} Nm (median {np.median(y):.0f})\n")

    # ---- POWER GATE: does the incumbent predict torque here at all? ----------
    print("POWER GATE — does the incumbent texture axis predict torque in this sample?")
    inc = df["int_cv_within"].values.astype(float)
    rho, p = stats.spearmanr(inc, y)
    base = df[CONFOUND].values.astype(float)
    r2_base = _ols_r2(base, y)
    r2_inc = _ols_r2(np.column_stack([base, inc]), y)
    d_adj = _adj(r2_inc, len(y), len(CONFOUND) + 1) - _adj(r2_base, len(y), len(CONFOUND))
    # partial correlation of texture with torque given confounders
    def resid(v):
        A = np.column_stack([np.ones(len(base)), base])
        b, *_ = np.linalg.lstsq(A, v, rcond=None)
        return v - A @ b
    pr, pp = stats.pearsonr(resid(inc), resid(y))
    print(f"  spearman(int_cv, torque)      rho={rho:+.4f}  p={p:.4f}")
    print(f"  partial | {'+'.join(CONFOUND)}   r={pr:+.4f}  p={pp:.4f}")
    print(f"  adjusted dR2 over confounders  {d_adj*100:+.2f} pp   (base adj-R2 {_adj(r2_base,len(y),len(CONFOUND))*100:.1f}%)")
    powered = pp < 0.05
    print(f"  => {'POWERED' if powered else 'NOT POWERED'}: the incumbent's own force association "
          f"{'is' if powered else 'is NOT'} distinguishable from zero at n={len(df)}.")
    if not powered:
        print("     Substitution arms below are reported for completeness but cannot discriminate:\n"
              "     there is no established association for a substitute to preserve or destroy.\n")
    else:
        print()

    # ---- arms: identical rows, identical confounders, texture column varied ---
    rows = []
    for col, label in [(None, "no texture (confounders + size + fat)")] + [(c, l) for c, l in TEXTURE.items()]:
        base_cols = CONFOUND + [SIZE, FAT]
        cols = base_cols + ([col] if col else [])
        X = df[cols].values.astype(float)
        r2 = _ols_r2(X, y)
        a = _adj(r2, len(y), len(cols))
        rec = {"arm": col or "none", "label": label, "n": len(y),
               "adj_r2_pct": round(a * 100, 2)}
        if col:
            v = df[col].values.astype(float)
            rec["spearman_vs_torque"] = round(float(stats.spearmanr(v, y).statistic), 4)
            rec["partial_r_given_confounders"] = round(float(stats.pearsonr(resid(v), resid(y)).statistic), 4)
        rows.append(rec)

    none_adj = rows[0]["adj_r2_pct"]
    print("ARMS  (OLS on identical rows; adj-R2 over confounders + size + fat)")
    print(f"  {'texture column':34s} {'adjR2':>7s} {'vs none':>8s} {'rho|torque':>11s} {'partial r':>10s}")
    for r in rows:
        rho_s = f"{r['spearman_vs_torque']:+.4f}" if "spearman_vs_torque" in r else "     -"
        par_s = f"{r['partial_r_given_confounders']:+.4f}" if "partial_r_given_confounders" in r else "    -"
        print(f"  {r['label']:34s} {r['adj_r2_pct']:>6.2f}% {r['adj_r2_pct']-none_adj:>+7.2f} {rho_s:>11s} {par_s:>10s}")

    # ---- collinearity of the variants, our pipeline, one substrate -----------
    print("\nCOLLINEARITY among the variants (Spearman, our pipeline, same segmentation)")
    c = df[list(TEXTURE)].corr(method="spearman")
    for i, a in enumerate(TEXTURE):
        for b in list(TEXTURE)[i + 1:]:
            print(f"  {a:16s} vs {b:16s}  {c.loc[a,b]:+.4f}")

    payload = {"n": int(len(df)), "criterion": "izo_torque_nm",
               "substrate": "scripts/analysis/results/capture_features.csv (our pipeline, role=rf)",
               "power_gate": {"spearman_rho": round(float(rho), 4), "spearman_p": round(float(p), 4),
                              "partial_r": round(float(pr), 4), "partial_p": round(float(pp), 4),
                              "adjusted_delta_r2_pp": round(float(d_adj * 100), 3),
                              "powered": bool(powered)},
               "arms": rows,
               "collinearity_spearman": json.loads(c.to_json())}
    (RESULTS / "texture_force_test.json").write_text(json.dumps(payload, indent=2))
    print(f"\nwrote {RESULTS/'texture_force_test.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

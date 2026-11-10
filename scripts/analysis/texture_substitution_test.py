"""Is the texture axis texture, or is it brightness? A controlled substitution.

`int_cv_within` carries 33.6% of the served score. The sibling repo's canonical
run says its agreement with the clinical grade is 88-95% shared with plain ROI
brightness. This refits the frozen model's structure varying ONLY the texture
column and compares 10-fold out-of-fold CV-QWK.

The design point that makes it interpretable: every texture variant is taken from
ONE substrate (the sibling run), so the arms differ in the FEATURE, never in the
pipeline that measured it. Size and fat are held at their frozen values and are
byte-identical across arms. Arms:

  0  frozen      our frozen int_cv_within          reproduction check vs published CV-QWK
  1  bridge      sibling int_cv_within             same feature, their pipeline
  2  test        sibling int_std                   the raw dispersion (actual texture)
  3  mechanism   sibling int_mean                  plain brightness
  F  floor       no texture column at all          what size+fat alone already buy

Arm 1 is the load-bearing control: if it does not land on arm 0, the substrate
swap is not sound and arms 2-3 say nothing. Arm F is the other control: if it
lands on arm 0, no texture variant matters and the question is moot.

    .venv/bin/python scripts/analysis/texture_substitution_test.py
"""
from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
RESULTS = ROOT / "scripts" / "analysis" / "results"

COHORT = ROOT / "data" / "cohort_stats" / "per_study_cohort_v1.3.csv"
SIB = Path(str(Path(__file__).resolve().parents[2] / "private" / "muscle_quality_exploration/runs/20260503/034929_default_infer"))
SIB_FEATURES = SIB / "study_features.csv"

ORD = {"PRESERVED": 0, "INTERMEDIATE": 1, "POOR": 2}
BASE = ["star_ratio", "fat_to_depth_q50"]          # held fixed across every arm

# arm key -> (label, column in the merged frame, provenance)
ARMS = {
    "0_frozen":    ("our int_cv_within (frozen)",  "int_cv_within",                     "muscle_mania v1.3"),
    "1_bridge":    ("sibling int_cv_within",       "final_rf_int_cv_within_agg_median", "sibling canonical run"),
    "2_int_std":   ("sibling int_std",             "final_rf_int_std_agg_median",       "sibling canonical run"),
    "3_int_mean":  ("sibling int_mean",            "final_rf_int_mean_agg_median",      "sibling canonical run"),
}


def main() -> int:
    from condition_options_compare import _qwk, _folds, _oof, _vif_worst

    coh = pd.read_csv(COHORT)
    sib = pd.read_csv(SIB_FEATURES)
    sib_cols = [c for _, c, _ in ARMS.values() if c in sib.columns]
    df = coh.merge(sib[["study_code"] + sib_cols], on="study_code", how="inner")
    df["q"] = df["quality_label"].map(ORD)
    print(f"cohort {len(coh)} rows · sibling {len(sib)} rows · joined {len(df)} rows\n")

    # ---- reproduction check on the FULL frozen-eligible set (not the common subset)
    full = df[BASE + ["int_cv_within", "q"]].dropna()
    y_f = full["q"].astype(int).values
    pred_f, _ = _oof(full[BASE + ["int_cv_within"]].values.astype(float), y_f, _folds(y_f))
    qwk_full = _qwk(y_f, pred_f)
    print("REPRODUCTION CHECK — frozen model C on its own eligible rows")
    print(f"  n={len(full)}   CV-QWK={qwk_full:.3f}   (published for v1.3: 0.811)")
    print(f"  {'MATCH' if abs(qwk_full - 0.811) < 0.02 else 'DOES NOT REPRODUCE'}"
          f" — delta {qwk_full - 0.811:+.3f}\n")

    # ---- the common row set: every arm sees exactly the same patients ----------
    need = BASE + ["q"] + [c for _, c, _ in ARMS.values()]
    common = df[need].dropna().reset_index(drop=True)
    y = common["q"].astype(int).values
    folds = _folds(y)
    dist = {k: int(v) for k, v in pd.Series(y).map({v: k for k, v in ORD.items()}).value_counts().items()}
    print(f"COMMON ROW SET  n={len(common)}  grades {dist}")
    print(f"  (dropped {len(df) - len(common)} of {len(df)} joined rows for a null in some arm)\n")

    rows = []
    # floor arm: size + fat, no texture at all
    raw = common[BASE].values.astype(float)
    pred, _ = _oof(raw, y, folds)
    rows.append({"arm": "F_floor", "label": "size + fat only (no texture)", "provenance": "-",
                 "n": len(y), "qwk": round(_qwk(y, pred), 3),
                 "exact_pct": round((pred == y).mean() * 100, 1),
                 "worst_vif": round(_vif_worst(raw), 1), "rho_vs_grade": None})

    for key, (label, col, prov) in ARMS.items():
        cols = BASE + [col]
        raw = common[cols].values.astype(float)
        pred, _ = _oof(raw, y, folds)
        rho = stats.spearmanr(common[col].values, y).statistic
        rows.append({"arm": key, "label": label, "provenance": prov, "n": len(y),
                     "qwk": round(_qwk(y, pred), 3),
                     "exact_pct": round((pred == y).mean() * 100, 1),
                     "worst_vif": round(_vif_worst(raw), 1),
                     "rho_vs_grade": round(float(rho), 4)})

    out = pd.DataFrame(rows)
    print("ARMS  (10-fold out-of-fold, identical rows and identical folds)")
    print(f"  {'arm':11s} {'texture column':28s} {'QWK':>6s} {'exact':>7s} {'wVIF':>5s} {'rho|grade':>10s}")
    for r in rows:
        rho = "     -    " if r["rho_vs_grade"] is None else f"{r['rho_vs_grade']:>10.4f}"
        print(f"  {r['arm']:11s} {r['label']:28s} {r['qwk']:>6.3f} {r['exact_pct']:>6.1f}% "
              f"{r['worst_vif']:>5.1f} {rho}")

    # ---- collinearity among the texture variants themselves -------------------
    print("\nCOLLINEARITY among the texture variants (Spearman, common rows)")
    tex_cols = [c for _, c, _ in ARMS.values()]
    corr = common[tex_cols].corr(method="spearman")
    for i, a in enumerate(tex_cols):
        for b in tex_cols[i + 1:]:
            print(f"  {a:36s} vs {b:36s}  {corr.loc[a, b]:+.4f}")

    # ---- partial: texture vs grade controlling brightness, on THIS row set ----
    mean_col = ARMS["3_int_mean"][1]
    print("\nPARTIAL — texture vs grade, controlling brightness (rank residuals, common rows)")
    for key in ("0_frozen", "1_bridge", "2_int_std"):
        col = ARMS[key][1]
        rx = stats.rankdata(common[col].values)
        rb = stats.rankdata(common[mean_col].values)
        ry = stats.rankdata(y)
        ex = rx - np.polyval(np.polyfit(rb, rx, 1), rb)
        ey = ry - np.polyval(np.polyfit(rb, ry, 1), rb)
        raw_rho = stats.spearmanr(common[col].values, y).statistic
        par = stats.pearsonr(ex, ey).statistic
        print(f"  {ARMS[key][0]:28s} raw {raw_rho:+.4f} -> partial {par:+.4f} "
              f"(rho^2 {raw_rho**2:.3f} -> {par**2:.3f}, "
              f"{100*(1 - par**2/max(raw_rho**2,1e-9)):.0f}% of rank-variance shared with brightness)")

    # ---- fold-seed stability: the arms differ by as little as 0.001, so a point
    #      comparison is meaningless without the spread CV itself induces --------
    from sklearn.model_selection import StratifiedKFold

    def folds_seeded(yy, seed):
        f = np.full(len(yy), -1)
        for k, (_, te) in enumerate(StratifiedKFold(10, shuffle=True, random_state=seed).split(yy, yy)):
            f[te] = k
        return f

    SEEDS = list(range(25))
    arm_specs = [("F_floor", BASE)] + [(k, BASE + [c]) for k, (_, c, _) in ARMS.items()]
    spread: dict[str, list[float]] = {k: [] for k, _ in arm_specs}
    for s in SEEDS:
        fs = folds_seeded(y, s)
        for k, cols in arm_specs:
            pr, _ = _oof(common[cols].values.astype(float), y, fs)
            spread[k].append(_qwk(y, pr))

    print(f"\nFOLD-SEED STABILITY  ({len(SEEDS)} seeds, same rows)")
    print(f"  {'arm':11s} {'mean':>6s} {'sd':>6s} {'min':>6s} {'max':>6s}   vs floor (mean delta)")
    stab = {}
    floor_mean = float(np.mean(spread["F_floor"]))
    for k, _ in arm_specs:
        v = np.array(spread[k])
        stab[k] = {"mean": round(float(v.mean()), 4), "sd": round(float(v.std(ddof=1)), 4),
                   "min": round(float(v.min()), 4), "max": round(float(v.max()), 4),
                   "delta_vs_floor": round(float(v.mean() - floor_mean), 4)}
        print(f"  {k:11s} {v.mean():>6.3f} {v.std(ddof=1):>6.3f} {v.min():>6.3f} {v.max():>6.3f}"
              f"   {v.mean()-floor_mean:+.3f}")

    # paired per-seed deltas -> the only honest way to rank two arms
    print("\n  PAIRED per-seed deltas (arm minus arm, same folds each seed)")
    def paired(a, b):
        d = np.array(spread[a]) - np.array(spread[b])
        wins = int((d > 0).sum())
        return d.mean(), d.std(ddof=1), wins
    for a, b in [("1_bridge", "3_int_mean"), ("1_bridge", "2_int_std"),
                 ("3_int_mean", "2_int_std"), ("2_int_std", "F_floor"),
                 ("3_int_mean", "F_floor"), ("0_frozen", "3_int_mean")]:
        m, sd, w = paired(a, b)
        print(f"    {a:11s} - {b:11s}  {m:+.4f} +/- {sd:.4f}   {a} wins {w}/{len(SEEDS)} seeds")

    # share of texture's contribution that brightness alone reproduces
    tex_gain = stab["1_bridge"]["mean"] - floor_mean
    print(f"\n  Within the sibling substrate: texture adds {tex_gain:+.3f} QWK over the floor.")
    for k, nm in [("3_int_mean", "plain brightness"), ("2_int_std", "raw dispersion")]:
        g = stab[k]["mean"] - floor_mean
        print(f"    {nm:18s} alone recovers {g:+.3f} = {100*g/tex_gain:.0f}% of that gain")

    RESULTS.mkdir(parents=True, exist_ok=True)
    payload = {
        "fold_seed_stability": {"n_seeds": len(SEEDS), "per_arm": stab},
        "design": "Model C structure, size+fat held fixed, texture column varied; "
                  "all texture variants from one substrate; identical rows and folds",
        "cohort_csv": str(COHORT.relative_to(ROOT)),
        "sibling_run": str(SIB),
        "reproduction_check": {"n": int(len(full)), "cv_qwk": round(float(qwk_full), 4),
                               "published_v13": 0.811},
        "common_n": int(len(common)), "grade_distribution": dist,
        "arms": rows,
        "texture_collinearity_spearman": json.loads(corr.to_json()),
    }
    (RESULTS / "texture_substitution_test.json").write_text(json.dumps(payload, indent=2))
    out.to_csv(RESULTS / "texture_substitution_test.csv", index=False)
    print(f"\nwrote {RESULTS/'texture_substitution_test.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

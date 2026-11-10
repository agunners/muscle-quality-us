"""What geometry buys the SERVED band — computed, not derived from pooled RMSE.

The Strength card's band is sex-stratified q10-q90 of pooled out-of-fold residuals
(force_model.py). Scaling band width by the pooled RMSE ratio (42.0 -> 39.8, "~5%")
is WRONG for a sex-stratified band — the 2026-08-12 day record shipped that derived
figure into review, and the analyst cold-read caught it (prove-it ledger, ESCAPE).
This script computes the honest comparison with the exact harness construction:

  * R1 band: demographics-only model, same seeds/folds/tuning, per-sex q10-q90.
  * R2 band: demographics+geometry (must reproduce force_model.json's quantiles).
  * Subject-level bootstrap of the R2 band edges (the spec Amendment 1f "~+-10 Nm"
    figure, receipted here with its method).

Writes scripts/analysis/results/force_band_r1_vs_r2.json.
    .venv/bin/python scripts/analysis/force_band_geometry_effect.py
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "results" / "force_band_r1_vs_r2.json"
SEED_BOOT = 20260812
N_BOOT = 400


def _harness():
    p = REPO / "scripts" / "analysis" / "force_ladder.py"
    spec = importlib.util.spec_from_file_location("force_ladder", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def oof_residuals(fl, rows, blocks):
    from sklearn.model_selection import KFold
    X, y = fl.matrices(rows, blocks)
    res = np.empty((len(list(fl.SEEDS)), len(y)))
    for seed in fl.SEEDS:
        pred = np.empty_like(y)
        for tr, te in KFold(5, shuffle=True, random_state=seed).split(X):
            gs = fl.make_model("ridge", seed)
            gs.fit(X[tr], y[tr])
            pred[te] = gs.predict(X[te])
        res[seed] = y - pred
        print(f"  {'+'.join(blocks)} seed {seed} done", flush=True)
    return res


def band(res, mask):
    pool = res[:, mask].ravel()
    return {"q10": round(float(np.percentile(pool, 10)), 2),
            "q90": round(float(np.percentile(pool, 90)), 2),
            "width": round(float(np.percentile(pool, 90) - np.percentile(pool, 10)), 2)}


def main() -> None:
    fl = _harness()
    rows = fl.row_sets(fl.load_rows())["SENS_A_no_texture"]
    sex_m = np.array([r["sex_m"] for r in rows]) == 1.0
    out = {"rows": "SENS_A_no_texture", "n": len(rows),
           "construction": "per-sex q10-q90 of OOF residuals pooled over seeds 0-24, "
                           "harness-identical (force_ladder make_model/folds)"}

    r1 = oof_residuals(fl, rows, ["B1_demographics"])
    r2 = oof_residuals(fl, rows, ["B1_demographics", "B2_geometry"])
    for name, mask in (("M", sex_m), ("F", ~sex_m)):
        b1, b2 = band(r1, mask), band(r2, mask)
        out[name] = {"R1_demographics_only": b1, "R2_served": b2,
                     "width_change_pct": round(100 * (b2["width"] - b1["width"]) / b1["width"], 1)}

    # the served artifact must agree with this recompute, or the receipt is void
    art = json.load(open(REPO / "data" / "cohort_stats" / "force_model.json"))
    for s in ("M", "F"):
        for q in ("q10", "q90"):
            d = abs(out[s]["R2_served"][q] - art["residual_quantiles"][s][q])
            assert d < 0.02, (s, q, d)
    out["r2_matches_served_artifact"] = True

    # subject-level bootstrap of the served band edges (Amendment 1f's method receipt):
    # resample SUBJECTS with replacement within sex; each subject contributes its 25
    # per-seed residuals; recompute q10/q90 per resample.
    rng = np.random.default_rng(SEED_BOOT)
    boot = {}
    for name, mask in (("M", sex_m), ("F", ~sex_m)):
        idx = np.where(mask)[0]
        q10s, q90s = [], []
        for _ in range(N_BOOT):
            take = rng.choice(idx, size=len(idx), replace=True)
            pool = r2[:, take].ravel()
            q10s.append(np.percentile(pool, 10))
            q90s.append(np.percentile(pool, 90))
        boot[name] = {"q10_sd": round(float(np.std(q10s)), 1),
                      "q90_sd": round(float(np.std(q90s)), 1), "n_boot": N_BOOT,
                      "seed": SEED_BOOT}
    out["edge_bootstrap"] = boot

    OUT.write_text(json.dumps(out, indent=1))
    print(json.dumps({k: out[k] for k in ("M", "F", "edge_bootstrap")}, indent=1))
    print("wrote", OUT)


if __name__ == "__main__":
    main()

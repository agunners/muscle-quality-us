"""Offline freeze of the served force model — the Strength card's expected-range.

Fits the pre-registered rung-2 ridge (demographics + geometry -> isometric torque) on the
prereg's SENS_A row set (no texture requirement — the card's model consumes none) and writes
`data/cohort_stats/force_model.json`: coefficients + scaler, sex-stratified out-of-fold residual
quantiles (the band), the fit envelope (refusal bounds), and provenance. Serve time is a pure
numpy dot product — the app never imports sklearn for this (see force_card.py).

Everything that defines the fit is IMPORTED from the pre-registered harness
(scripts/analysis/force_ladder.py): row loading, row sets, blocks, alpha grid, model
construction, fold structure. This builder adds nothing statistical of its own — it keeps the
out-of-fold PREDICTIONS the harness only summarised, and refits once on the full set to freeze
coefficients. The recomputed OOF metrics are asserted against the committed SENS_A receipts
(force_ladder_summary.json); a mismatch aborts the freeze, because it would mean the substrate
changed since the receipts were pinned.

Build/refresh (CPU, ~1 min; run in background per repo convention):
    .venv/bin/python scripts/preflight_helpers/force_model.py

Contract: docs/force_card_spec.md. Prereg: docs/force_ladder_prereg.md.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from datetime import date
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
ARTIFACT = REPO / "data" / "cohort_stats" / "force_model.json"
RECEIPTS = REPO / "scripts" / "analysis" / "results" / "force_ladder_summary.json"
ROW_SET = "SENS_A_no_texture"          # the card's fit set — see the spec's "fit rows" pin
RUNG_BLOCKS = ["B1_demographics", "B2_geometry"]
QUANTILES = {"q2_5": 2.5, "q10": 10.0, "q90": 90.0, "q97_5": 97.5}
RECEIPT_TOL = {"r2": 0.005, "rmse": 0.25}   # recompute must land on the committed receipt


def _load_harness():
    p = REPO / "scripts" / "analysis" / "force_ladder.py"
    spec = importlib.util.spec_from_file_location("force_ladder", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> None:
    fl = _load_harness()
    rows = fl.row_sets(fl.load_rows())[ROW_SET]

    # The primary substrate pin (red-team M2): the metric re-check below is NECESSARY but not
    # sufficient — a value edit can leave R² unchanged to 4dp. The receipts are only valid for
    # the exact bytes they were computed on, so assert sha + row-set size BEFORE fitting.
    summary = json.load(open(RECEIPTS))
    cur_sha = hashlib.sha256(fl.QA.read_bytes()).hexdigest()[:16]
    if cur_sha != summary["substrate"]["sha256_16"] or len(rows) != summary["n"][ROW_SET]:
        sys.exit(f"ABORT: qa_master.csv (sha {cur_sha}, {ROW_SET} n={len(rows)}) is not the "
                 f"substrate the receipts were pinned on (sha "
                 f"{summary['substrate']['sha256_16']}, n={summary['n'][ROW_SET]}) — re-run the "
                 f"ladder (force_ladder.py) to re-pin receipts first.")

    cols = [c for b in RUNG_BLOCKS for c in fl.BLOCKS[b]]
    X, y = fl.matrices(rows, RUNG_BLOCKS)
    uids = [r["uid"] for r in rows]
    sex_m = np.array([r["sex_m"] for r in rows], dtype=float)   # 1.0 M / 0.0 F
    n_m, n_f = int(sex_m.sum()), int((1 - sex_m).sum())
    print(f"fit set {ROW_SET}: n={len(rows)} (M {n_m} / F {n_f}), features {cols}", flush=True)

    # --- out-of-fold predictions, exactly the harness's CV (outer 5-fold x seeds 0-24,
    # inner 5-fold alpha tuning), keeping what oof_metrics() discards: the predictions.
    preds = np.empty((len(fl.SEEDS), len(y)), dtype=float)
    from sklearn.model_selection import KFold
    for seed in fl.SEEDS:
        outer = KFold(5, shuffle=True, random_state=seed)
        for tr, te in outer.split(X):
            gs = fl.make_model("ridge", seed)
            gs.fit(X[tr], y[tr])
            preds[seed, te] = gs.predict(X[te])
        print(f"  seed {seed} done", flush=True)

    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2_seeds = [1.0 - float(np.sum((y - preds[s]) ** 2)) / ss_tot for s in fl.SEEDS]
    rmse_seeds = [float(np.sqrt(np.mean((y - preds[s]) ** 2))) for s in fl.SEEDS]
    oof = {"r2_mean": round(float(np.mean(r2_seeds)), 4), "r2_sd": round(float(np.std(r2_seeds)), 4),
           "rmse_mean": round(float(np.mean(rmse_seeds)), 2),
           "rmse_sd": round(float(np.std(rmse_seeds)), 2), "seeds": len(list(fl.SEEDS))}

    # --- secondary check: the recompute must land on the committed receipt (harness identity).
    receipt = summary["results"][f"{ROW_SET}.R2.ridge"]
    d_r2 = abs(oof["r2_mean"] - receipt["r2"]["mean"])
    d_rmse = abs(oof["rmse_mean"] - receipt["rmse"]["mean"])
    print(f"OOF recompute: R2 {oof['r2_mean']} (receipt {receipt['r2']['mean']}, d {d_r2:.4f}) · "
          f"RMSE {oof['rmse_mean']} (receipt {receipt['rmse']['mean']}, d {d_rmse:.3f})", flush=True)
    if d_r2 > RECEIPT_TOL["r2"] or d_rmse > RECEIPT_TOL["rmse"]:
        sys.exit(f"ABORT: OOF recompute does not reproduce the committed SENS_A receipt "
                 f"(dR2 {d_r2:.4f}, dRMSE {d_rmse:.3f}) — the substrate changed since "
                 f"force_ladder_summary.json was pinned; re-run the ladder first.")

    # --- residual quantiles: pooled over rows x seeds, sex-stratified (the display rule:
    # a sex-stratified prediction RANGE). Pooling across seeds smooths fold noise; the band
    # is the central 80% (q10-q90), 95% stored for the (i) detail.
    res = np.repeat(y[None, :], len(fl.SEEDS), axis=0) - preds          # (seeds, n)
    quant = {}
    for name, mask in (("global", np.ones_like(sex_m, bool)),
                       ("M", sex_m == 1.0), ("F", sex_m == 0.0)):
        pool = res[:, mask].ravel()
        quant[name] = {q: round(float(np.percentile(pool, p)), 2) for q, p in QUANTILES.items()}
    cov_oof = {}
    for name, mask in (("M", sex_m == 1.0), ("F", sex_m == 0.0)):
        pool = res[:, mask].ravel()
        cov_oof[name] = round(float(np.mean((pool >= quant[name]["q10"])
                                            & (pool <= quant[name]["q90"]))), 3)

    # --- final served model: the harness's own inner-CV tuning on the FULL fit set, then
    # (GridSearchCV refit) one ridge on all rows. Coefficients out, sklearn stays offline.
    gs = fl.make_model("ridge", 0)
    gs.fit(X, y)
    pipe = gs.best_estimator_
    alpha = float(gs.best_params_["m__alpha"])
    sc, ridge = pipe.named_steps["sc"], pipe.named_steps["m"]
    mean, scale = sc.mean_.tolist(), sc.scale_.tolist()
    coef, intercept = ridge.coef_.tolist(), float(ridge.intercept_)

    # serve-path self-test: the stored parameters must BE the model, to numerical identity.
    manual = (X - np.array(mean)) / np.array(scale) @ np.array(coef) + intercept
    max_diff = float(np.max(np.abs(manual - pipe.predict(X))))
    if max_diff > 1e-9:
        sys.exit(f"ABORT: stored parameters do not reproduce sklearn predict (max diff {max_diff})")

    # in-sample coverage of the served band around the FINAL model's predictions (secondary
    # receipt; the OOF coverage above is the honest one and is 0.80 by construction).
    band_lo = manual + np.where(sex_m == 1.0, quant["M"]["q10"], quant["F"]["q10"])
    band_hi = manual + np.where(sex_m == 1.0, quant["M"]["q90"], quant["F"]["q90"])
    cov_final = round(float(np.mean((y >= band_lo) & (y <= band_hi))), 3)

    # three sample predictions pinned into the artifact (serve-path regression tests).
    order = np.argsort(manual)
    samples = []
    for i in (order[0], order[len(order) // 2], order[-1]):
        samples.append({"uid": uids[i], "features": {c: X[i, j] for j, c in enumerate(cols)},
                        "predicted": round(float(manual[i]), 3)})

    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    blob = fl.QA.read_bytes()
    summary_full = summary
    art = {
        "kind": "force_expected_range", "version": "1.0", "date": date.today().isoformat(),
        "git_head": head,
        "substrate": {"file": "data/qa_master.csv",
                      "sha256_16": hashlib.sha256(blob).hexdigest()[:16], "bytes": len(blob)},
        "prereg": "docs/force_ladder_prereg.md", "spec": "docs/force_card_spec.md",
        "criterion": "izo_torque_nm", "row_set": ROW_SET,
        "features": cols, "sex_encoding": {"M": 1.0, "F": 0.0},
        "n": len(rows), "n_by_sex": {"M": n_m, "F": n_f},
        "alpha": alpha, "scaler_mean": mean, "scaler_scale": scale,
        "coef": coef, "intercept": intercept,
        "oof_recomputed": oof,
        "oof_receipt": {"r2_mean": receipt["r2"]["mean"], "r2_sd": receipt["r2"]["sd"],
                        "rmse_mean": receipt["rmse"]["mean"],
                        "source": "scripts/analysis/results/force_ladder_summary.json "
                                  f"({ROW_SET}.R2.ridge)"},
        "residual_quantiles": quant,
        "band": {"served": ["q10", "q90"], "label": "8-in-10",
                 "coverage_oof_by_sex": cov_oof, "coverage_final_in_sample": cov_final,
                 "min_band_lo_nm": round(float(band_lo.min()), 1)},
        "envelope": {c: [round(float(X[:, j].min()), 3), round(float(X[:, j].max()), 3)]
                     for j, c in enumerate(cols)},
        "target_stats": {"mean_nm": round(float(y.mean()), 1),
                         "sd_nm": round(float(y.std()), 1),
                         "min_nm": round(float(y.min()), 1), "max_nm": round(float(y.max()), 1)},
        # day-to-day context line: mean within-subject session SD, carried from the PRIMARY
        # receipt — one number, one source; the n rides along from the summary so a re-pin can
        # never leave a stale count in a shipped receipt (red-team 2026-08-13).
        "within_subject_sd_nm": summary_full["target_stats_primary"]["within_subject_sd_nm_mean"],
        "within_subject_sd_provenance": ("force_ladder_summary.json target_stats_primary "
                                         f"(n={summary_full['target_stats_primary']['n']})"),
        "fit_uids": uids,
        "sample_predictions": samples,
        "self_test": {"max_abs_diff_vs_sklearn": max_diff},
    }
    ARTIFACT.write_text(json.dumps(art, indent=1))
    print(f"\nwrote {ARTIFACT}", flush=True)
    print(f"alpha {alpha:.4g} · band M [{quant['M']['q10']}, {quant['M']['q90']}] "
          f"F [{quant['F']['q10']}, {quant['F']['q90']}] Nm · OOF cov M {cov_oof['M']} "
          f"F {cov_oof['F']} · final in-sample cov {cov_final} · min band-lo "
          f"{art['band']['min_band_lo_nm']} Nm", flush=True)


if __name__ == "__main__":
    main()

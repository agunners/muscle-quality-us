"""Per-sex out-of-fold readout + log-target sensitivity arm — prereg Amendments 6a/6b (2026-08-23).

Registered BEFORE any result existed (docs/force_ladder_prereg.md, Amendment 6): for PRIMARY and
SENS_A_no_texture, rungs R1 (demographics) and R2 (demographics+geometry), ridge only,
harness-identical folds/tuning (force_ladder make_model/matrices, seeds 0-24):

  (a) per-sex OOF R^2 (1 - SS_res/SS_tot with the SEX-SPECIFIC mean, i.e. variance explained
      *within* each sex) and per-sex q10-q90 residual band widths, in Nm;
  (b) the same construction with criterion log(izo_torque_nm) — log residuals read as relative
      error, so the per-sex bands become scale-free. Decision rule in the amendment: female R1->R2
      narrowing under log where Nm showed none => the flatness is partly loss-weighting.

Also emits per-study mean OOF R2 residuals (Nm arm, SENS_A) — the "specific strength" signal
(torque unexplained by demographics+size), answering his #6 with the statistically clean form.

Writes scripts/analysis/results/force_sex_split.json. PHI-free (uids only).
    .venv/bin/python scripts/analysis/force_sex_split.py
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent / "results" / "force_sex_split.json"


def _harness():
    p = REPO / "scripts" / "analysis" / "force_ladder.py"
    spec = importlib.util.spec_from_file_location("force_ladder", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def oof_residuals(fl, rows, blocks, log_target: bool):
    from sklearn.model_selection import KFold
    X, y = fl.matrices(rows, blocks)
    if log_target:
        y = np.log(y)
    res = np.empty((len(list(fl.SEEDS)), len(y)))
    for seed in fl.SEEDS:
        pred = np.empty_like(y)
        for tr, te in KFold(5, shuffle=True, random_state=seed).split(X):
            gs = fl.make_model("ridge", seed)
            gs.fit(X[tr], y[tr])
            pred[te] = gs.predict(X[te])
        res[seed] = y - pred
    return y, res


def per_sex(y, res, mask):
    """OOF R^2 within the sex (sex-specific mean) + pooled q10-q90 band."""
    ys, rs = y[mask], res[:, mask]
    ss_tot = float(np.sum((ys - ys.mean()) ** 2)) * rs.shape[0]
    ss_res = float(np.sum(rs ** 2))
    pool = rs.ravel()
    q10, q90 = np.percentile(pool, 10), np.percentile(pool, 90)
    return {"n": int(mask.sum()), "oof_r2_within_sex": round(1 - ss_res / ss_tot, 4),
            "q10": round(float(q10), 4), "q90": round(float(q90), 4),
            "width": round(float(q90 - q10), 4)}


def main() -> None:
    fl = _harness()
    sets = fl.row_sets(fl.load_rows())
    out = {"prereg": "docs/force_ladder_prereg.md Amendment 6 (2026-08-23, pre-run)",
           "construction": "ridge, harness-identical folds/tuning, OOF residuals pooled over "
                           "seeds 0-24; R^2 per sex uses the sex-specific mean (within-sex "
                           "variance explained)"}
    rungs = {"R1": ["B1_demographics"], "R2": ["B1_demographics", "B2_geometry"]}

    for set_name in ("PRIMARY", "SENS_A_no_texture"):
        rows = sets[set_name]
        sex_m = np.array([r["sex_m"] for r in rows]) == 1.0
        blk = {"n": len(rows), "n_M": int(sex_m.sum()), "n_F": int((~sex_m).sum())}
        for arm, log_t in (("nm", False), ("log", True)):
            for rung, blocks in rungs.items():
                y, res = oof_residuals(fl, rows, blocks, log_t)
                for sname, mask in (("M", sex_m), ("F", ~sex_m)):
                    blk[f"{arm}.{rung}.{sname}"] = per_sex(y, res, mask)
                if set_name == "SENS_A_no_texture" and arm == "nm" and rung == "R2":
                    # per-study specific-strength residual (his #6): mean OOF residual in Nm
                    mean_res = res.mean(axis=0)
                    blk["specific_strength_residuals"] = {
                        rows[i]["uid"]: round(float(mean_res[i]), 1) for i in range(len(rows))}
            for sname in ("M", "F"):
                b1w, b2w = blk[f"{arm}.R1.{sname}"]["width"], blk[f"{arm}.R2.{sname}"]["width"]
                blk[f"{arm}.width_change_pct.{sname}"] = round(100 * (b2w - b1w) / b1w, 1)
        out[set_name] = blk
        print(set_name, {k: v for k, v in blk.items() if "width_change" in k}, flush=True)

    OUT.write_text(json.dumps(out, indent=1))
    print("wrote", OUT)


if __name__ == "__main__":
    main()

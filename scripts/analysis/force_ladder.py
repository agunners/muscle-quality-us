"""The pre-registered force ladder — docs/force_ladder_prereg.md (locked 149b384).

Blocks demographics -> +geometry -> +fat -> +texture against isometric torque, ridge +
shallow GBM, nested 5x5 CV over seeds 0-24, on identical rows and folds per row set.
`--permute` runs the leakage smoke (shuffled criterion; every rung must read R^2 ~ 0)
and MUST pass before the real run. CPU-only, deterministic, PHI-free outputs.
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
from pathlib import Path

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.model_selection import GridSearchCV, KFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

REPO = Path(__file__).resolve().parents[2]
QA = REPO / "data" / "qa_master.csv"
OUT = Path(__file__).resolve().parent / "results"

CRIT = "izo_torque_nm"
# Prereg Amendment 2 (2026-08-11): near-certain kgf·m-as-Nm entry errors at the source sheet —
# isometric ~1/9 of the same subject's isokinetic torque, CV exactly 0.0. Quarantined from every
# row set pending Akadir's source audit; SENS_C keeps them exactly as recorded.
QUARANTINE = {"NR0D53363E", "NR245F7FC5"}
BLOCKS = {  # exact prereg columns, nothing else
    "B1_demographics": ["age", "sex_m", "height_cm", "weight_kg"],
    "B2_geometry": ["rf_thickness_mm", "full_thickness_q50_mm", "visible_rf_area_mm2", "qmt_mm"],
    "B3_fat": ["fat_thickness_q50_mm", "fat_thickness_max_mm", "fat_to_depth_q50"],
    "B4_texture": ["int_cv_within"],
}
RUNGS = [("R1", ["B1_demographics"]),
         ("R2", ["B1_demographics", "B2_geometry"]),
         ("R3", ["B1_demographics", "B2_geometry", "B3_fat"]),
         ("R4", ["B1_demographics", "B2_geometry", "B3_fat", "B4_texture"])]
SEEDS = range(25)
RIDGE_ALPHAS = np.logspace(-3, 3, 25)
HGB_GRID = {"max_depth": [2, 3], "learning_rate": [0.03, 0.1], "max_iter": [100, 300]}


def load_rows() -> list[dict]:
    rows = []
    for r in csv.DictReader(open(QA, encoding="utf-8")):
        if r.get("cohort_round") != "new":
            continue
        d = {"uid": r.get("study_uid", "")}
        for k in {c for cols in BLOCKS.values() for c in cols} | {CRIT, "izo_cv_pct"}:
            src = "sex" if k == "sex_m" else k
            v = (r.get(src) or "").strip()
            if k == "sex_m":
                if v not in ("M", "F", ""):
                    raise ValueError(f"unexpected sex value {v!r} for {d['uid']}")
                d[k] = {"M": 1.0, "F": 0.0}.get(v)
            else:
                try:
                    x = float(v) if v not in ("", "None") else None
                except ValueError:
                    x = None
                d[k] = x if (x is None or np.isfinite(x)) else None
        rows.append(d)
    return rows


def row_sets(rows: list[dict]) -> dict:
    every = [c for cols in BLOCKS.values() for c in cols]
    def complete(r, cols): return all(r[c] is not None for c in cols)
    full = [r for r in rows if complete(r, every) and r[CRIT] is not None]
    primary = [r for r in full if r["uid"] not in QUARANTINE]
    sens_a = [r for r in rows if r["uid"] not in QUARANTINE
              and complete(r, [c for c in every if c != "int_cv_within"]) and r[CRIT] is not None]
    sens_b = [r for r in primary if r["izo_cv_pct"] is not None and r["izo_cv_pct"] <= 10.0]
    return {"PRIMARY": primary, "SENS_A_no_texture": sens_a, "SENS_B_cv10": sens_b,
            "SENS_C_with_suspects": full}


def target_stats(rows: list[dict]) -> dict:
    """Criterion-side statistics of the PRIMARY set — produced BY the harness so a re-run can
    never drop them (the 2026-08-12 briefing edition added this block by hand; the 08-13 re-run
    lost it and the card freeze aborted on the gap). force_model.py reads
    within_subject_sd_nm_mean; the rest is the briefing provenance."""
    y = np.array([r[CRIT] for r in rows], dtype=float)
    wob = [r["izo_cv_pct"] / 100.0 * r[CRIT] for r in rows if r["izo_cv_pct"] is not None]
    return {
        "definition": "izo_torque_nm over the PRIMARY row set (all blocks + criterion "
                      "complete, quarantine excluded)",
        "n": len(rows), "mean_nm": round(float(y.mean()), 1),
        "sd_population_nm": round(float(y.std()), 1),
        "min_nm": round(float(y.min()), 1), "max_nm": round(float(y.max()), 1),
        "sex": {"M": int(sum(r["sex_m"] == 1.0 for r in rows)),
                "F": int(sum(r["sex_m"] == 0.0 for r in rows))},
        "age_range": [int(min(r["age"] for r in rows)), int(max(r["age"] for r in rows))],
        "within_subject_sd_nm_mean": round(float(np.mean(wob)), 1),
        "within_subject_sd_note": "mean over subjects of izo_cv_pct/100 * izo_torque_nm "
                                  "on the primary set",
    }


def matrices(rows: list[dict], blocks: list[str]):
    cols = [c for b in blocks for c in BLOCKS[b]]
    X = np.array([[r[c] for c in cols] for r in rows], dtype=float)
    y = np.array([r[CRIT] for r in rows], dtype=float)
    return X, y


def make_model(kind: str, seed: int):
    inner = KFold(5, shuffle=True, random_state=seed)
    if kind == "ridge":
        pipe = Pipeline([("sc", StandardScaler()), ("m", Ridge())])
        return GridSearchCV(pipe, {"m__alpha": RIDGE_ALPHAS}, cv=inner,
                            scoring="neg_mean_squared_error", n_jobs=-1)
    return GridSearchCV(
        HistGradientBoostingRegressor(min_samples_leaf=5, early_stopping=False,
                                      random_state=seed),
        HGB_GRID, cv=inner, scoring="neg_mean_squared_error", n_jobs=-1)


def oof_metrics(X, y, kind: str, seed: int) -> dict:
    outer = KFold(5, shuffle=True, random_state=seed)
    pred = np.empty_like(y)
    for tr, te in outer.split(X):
        gs = make_model(kind, seed)
        gs.fit(X[tr], y[tr])
        pred[te] = gs.predict(X[te])
    res = y - pred
    ss = float(np.sum((y - y.mean()) ** 2))
    return {"r2": 1.0 - float(np.sum(res ** 2)) / ss,
            "rmse": float(np.sqrt(np.mean(res ** 2))),
            "mae": float(np.mean(np.abs(res)))}


def shapley_ridge(rows: list[dict], seed: int) -> dict:
    """Exact block-Shapley on ridge: pooled OOF R^2 of all 2^4 block subsets, same folds."""
    names = list(BLOCKS)
    vals = {}
    _, y = matrices(rows, names)
    outer = KFold(5, shuffle=True, random_state=seed)
    splits = list(outer.split(np.zeros(len(y))))
    for mask in range(16):
        subset = [names[i] for i in range(4) if mask >> i & 1]
        if not subset:
            pred = np.empty_like(y)
            for tr, te in splits:
                pred[te] = y[tr].mean()
        else:
            X, _ = matrices(rows, subset)
            pred = np.empty_like(y)
            for tr, te in splits:
                gs = make_model("ridge", seed)
                gs.fit(X[tr], y[tr])
                pred[te] = gs.predict(X[te])
        vals[frozenset(subset)] = 1.0 - float(np.sum((y - pred) ** 2)) / float(np.sum((y - y.mean()) ** 2))
    import math
    phi = {}
    for b in names:
        total = 0.0
        others = [n for n in names if n != b]
        for mask in range(8):
            s = [others[i] for i in range(3) if mask >> i & 1]
            w = math.factorial(len(s)) * math.factorial(3 - len(s)) / math.factorial(4)
            total += w * (vals[frozenset(s + [b])] - vals[frozenset(s)])
        phi[b] = total
    return phi, vals[frozenset()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--permute", action="store_true",
                    help="leakage smoke: shuffle the criterion, seeds 0-4, expect R^2 ~ 0")
    a = ap.parse_args()

    sets = row_sets(load_rows())
    head = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    print({k: len(v) for k, v in sets.items()}, "git", head, flush=True)

    seeds = range(5) if a.permute else SEEDS
    plan = [("PRIMARY", RUNGS), ("SENS_A_no_texture", RUNGS[:3]), ("SENS_B_cv10", RUNGS),
            ("SENS_C_with_suspects", RUNGS)]
    if a.permute:
        plan = [("PRIMARY", RUNGS)]
    else:
        smoke_file = OUT / "force_ladder_smoke.json"
        if not smoke_file.exists() or not json.load(open(smoke_file)).get("pass"):
            raise SystemExit("no PASSING smoke receipt (results/force_ladder_smoke.json) — "
                             "run --permute first; the real run refuses without it (prereg gate 1)")

    recs = []
    for set_name, rungs in plan:
        rows = sets[set_name]
        for rung, blocks in rungs:
            X, y = matrices(rows, blocks)
            for kind in ("ridge", "hgb"):
                for seed in seeds:
                    yy = y.copy()
                    if a.permute:
                        rng = np.random.default_rng(1000 + seed)
                        yy = rng.permutation(yy)
                    m = oof_metrics(X, yy, kind, seed)
                    recs.append({"row_set": set_name, "rung": rung, "model": kind,
                                 "seed": seed, "n": len(rows), **{k: round(v, 6) for k, v in m.items()}})
            print(f"[{set_name}:{rung}] done", flush=True)

    if a.permute:
        by = {}
        for r in recs:
            by.setdefault((r["rung"], r["model"]), []).append(r["r2"])
        print("\nPERMUTED-TARGET SMOKE (one-sided per the 2026-08-11 prereg amendment:")
        print("leakage reads POSITIVE on noise; a flexible model going negative is expected):")
        ok = True
        for (rung, model), v in sorted(by.items()):
            med = float(np.median(v))
            flag = "PASS" if med <= 0.05 else "FAIL"
            ok &= med <= 0.05
            print(f"  {rung} {model}: median R^2 {med:+.4f}  {flag}")
        print("SMOKE:", "PASS — the real run may start" if ok else "FAIL — do NOT run real")
        OUT.mkdir(exist_ok=True)
        json.dump({"pass": bool(ok), "git_head": head, "seeds": list(seeds),
                   "row_set": "PRIMARY", "n": len(sets["PRIMARY"]),
                   "medians": {f"{rung}.{model}": round(float(np.median(v)), 4)
                               for (rung, model), v in sorted(by.items())}},
                  open(OUT / "force_ladder_smoke.json", "w"), indent=1)
        return

    OUT.mkdir(exist_ok=True)
    with open(OUT / "force_ladder_results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(recs[0]))
        w.writeheader()
        w.writerows(recs)

    def agg(rs, rung, model):
        v = [r for r in recs if r["row_set"] == rs and r["rung"] == rung and r["model"] == model]
        out = {}
        for k in ("r2", "rmse", "mae"):
            xs = np.array([x[k] for x in v])
            out[k] = {"mean": round(float(xs.mean()), 4), "sd": round(float(xs.std()), 4),
                      "p2_5": round(float(np.percentile(xs, 2.5)), 4),
                      "p97_5": round(float(np.percentile(xs, 97.5)), 4)}
        return out

    import hashlib
    blob = QA.read_bytes()
    summary = {"git_head": head, "n": {k: len(v) for k, v in sets.items()},
               "substrate": {"file": "data/qa_master.csv",
                             "sha256_16": hashlib.sha256(blob).hexdigest()[:16],
                             "bytes": len(blob)},
               "quarantined_uids": sorted(QUARANTINE),
               "band_meaning": "seed/fold-split variability on a fixed cohort — NOT a sampling CI",
               "prereg": "docs/force_ladder_prereg.md", "results": {}, "deltas": {}, "tripwire": {},
               "target_stats_primary": target_stats(sets["PRIMARY"])}
    for set_name, rungs in plan:
        for rung, _ in rungs:
            for kind in ("ridge", "hgb"):
                summary["results"][f"{set_name}.{rung}.{kind}"] = agg(set_name, rung, kind)
    for set_name, rungs in plan:
        for (r_hi, _), (r_lo, _) in zip(rungs[1:], rungs[:-1]):
            for kind in ("ridge", "hgb"):
                d = []
                for seed in SEEDS:
                    hi = next(x["r2"] for x in recs if x["row_set"] == set_name
                              and x["rung"] == r_hi and x["model"] == kind and x["seed"] == seed)
                    lo = next(x["r2"] for x in recs if x["row_set"] == set_name
                              and x["rung"] == r_lo and x["model"] == kind and x["seed"] == seed)
                    d.append(hi - lo)
                dd = np.array(d)
                summary["deltas"][f"{set_name}.{r_lo}->{r_hi}.{kind}"] = {
                    "mean": round(float(dd.mean()), 4), "sd": round(float(dd.std()), 4),
                    "p2_5": round(float(np.percentile(dd, 2.5)), 4),
                    "p97_5": round(float(np.percentile(dd, 97.5)), 4),
                    "seeds_positive_of_25": int((dd > 0).sum())}
    print("shapley (ridge, PRIMARY, 25 seeds)...", flush=True)
    phis, v_emptys = [], []
    for s in SEEDS:
        phi, v_empty = shapley_ridge(sets["PRIMARY"], s)
        phis.append(phi)
        v_emptys.append(v_empty)
    summary["shapley_ridge_primary"] = {
        b: {"mean": round(float(np.mean([p[b] for p in phis])), 4),
            "sd": round(float(np.std([p[b] for p in phis])), 4)} for b in BLOCKS}
    summary["shapley_ridge_primary"]["v_empty_oof_mean_predictor"] = {
        "mean": round(float(np.mean(v_emptys)), 4), "sd": round(float(np.std(v_emptys)), 4),
        "note": "phi sums to v(full) - v(empty); v(empty) is the OOF train-mean predictor, slightly negative"}
    floor = min(v["rmse"]["mean"] for k, v in summary["results"].items())
    summary["tripwire"]["min_mean_rmse_nm"] = round(floor, 2)
    summary["tripwire"]["criterion_floor_breached"] = bool(floor < 12.0)
    json.dump(summary, open(OUT / "force_ladder_summary.json", "w"), indent=1)
    print("wrote", OUT / "force_ladder_results.csv", "and force_ladder_summary.json", flush=True)
    if summary["tripwire"]["criterion_floor_breached"]:
        print("!! TRIPWIRE: RMSE below the ~12 Nm criterion floor — audit for leakage before reporting")


if __name__ == "__main__":
    main()

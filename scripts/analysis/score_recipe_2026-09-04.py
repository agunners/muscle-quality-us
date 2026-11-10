"""The score sheet — does a RECIPE score (fixed weights over sex-matched percentiles in a named pool) fix the
"two rulers" contradiction of the served condition score, and what does it cost? (Akadir 2026-09-04: "the
single score … should bear a defendible meaning … let's try that, properly.")

The served score (option A, frozen 2026-08-04) is an ordinal logit on three axes — RF full thickness,
fat-to-depth, texture (int_cv_within) — standardised with the OLD cohort's mean/sd and read as a probability
on the old doctors' scale; the app's placement ranks the same axes inside a NAMED pool. On the new device
the axes shift, so a study can rank near the bottom of its new-cohort peers and still read INTERMEDIATE.

The recipe: score = 100 × Σ_i w_i · q_i, where q_i is the study's oriented sex-matched percentile on axis i
in the named pool (size: higher is better; fat: lower is better; texture: higher contrast is better) and
w_i ∝ |β_i| of the frozen model, so the weights are inherited, not refit. Bands come from two cut-points
that reproduce the old doctors' consensus best (quadratic weighted κ, cross-validated) — a monotone map,
not a weight fit — and the grading table calibrates the same cut-points for the new era.

Writes scripts/analysis/results/score_recipe_2026-09-04.json. Names never enter: study codes only.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "scripts")
import preflight_helpers.env_bootstrap  # noqa: F401,E402
from preflight_helpers import condition_model as cm  # noqa: E402

ROOT = Path(".")
OLD_CSV = ROOT / "data/cohort_stats/per_study_cohort.csv"
NEW_CSV = ROOT / "data/cohort_stats/per_study_cohort_new_r5.csv"
OUT = ROOT / "scripts/analysis/results/score_recipe_2026-09-04.json"
ORD = {"PRESERVED": 0, "INTERMEDIATE": 1, "POOR": 2}
BAND = {0: "PRESERVED", 1: "INTERMEDIATE", 2: "POOR"}
MIN_SEX_POOL = 20


def qwk(a, b, k=3) -> float:
    a, b = np.asarray(a, int), np.asarray(b, int)
    n = len(a)
    if n == 0:
        return float("nan")
    o = np.zeros((k, k))
    for i, j in zip(a, b):
        o[i, j] += 1
    o /= n
    e = np.outer(o.sum(1), o.sum(0))
    w = np.array([[((i - j) ** 2) / ((k - 1) ** 2) for j in range(k)] for i in range(k)])
    return float(1 - (w * o).sum() / (w * e).sum()) if (w * e).sum() > 0 else 1.0


def pct_in_pool(value: float, pool: np.ndarray) -> float:
    """Percentile rank (0–100) of value among pool values (mid-rank for ties)."""
    pool = pool[~np.isnan(pool)]
    if len(pool) == 0 or np.isnan(value):
        return float("nan")
    return 100.0 * ((pool < value).sum() + 0.5 * (pool == value).sum()) / len(pool)


def oriented_pct(df: pd.DataFrame, pool: pd.DataFrame, col: str, better_high: bool) -> np.ndarray:
    out = np.full(len(df), np.nan)
    for i, (_, r) in enumerate(df.iterrows()):
        sex = r.get("sex")
        sub = pool[pool["sex"] == sex] if sex in ("M", "F") and (pool["sex"] == sex).sum() >= MIN_SEX_POOL else pool
        p = pct_in_pool(float(r[col]) if pd.notna(r[col]) else np.nan, sub[col].to_numpy(float))
        out[i] = p if better_high else (100.0 - p if not np.isnan(p) else np.nan)
    return out


def best_cuts(score: np.ndarray, grade: np.ndarray) -> tuple:
    """Two cut-points (poor|int, int|pres) on a 0–100 score maximising QWK vs the ordinal grade."""
    best, arg = -9, (33.0, 66.0)
    grid = np.arange(5, 96, 1.0)
    for c1 in grid:
        for c2 in grid:
            if c2 <= c1 + 4:
                continue
            band = np.where(score < c1, 2, np.where(score < c2, 1, 0))
            k = qwk(grade, band)
            if k > best:
                best, arg = k, (float(c1), float(c2))
    return arg, best


def band_of(score, cuts):
    c1, c2 = cuts
    return np.where(score < c1, 2, np.where(score < c2, 1, 0))


def main() -> None:
    art = cm.load()
    A = art["models"]["A"]
    cols, beta = list(A["cols"]), np.array(A["beta"], float)
    axes = {ax["col"]: ax for ax in A.get("axes", [])} or {c: {"col": c, "better_high": bh, "name": nm}
                                                              for (c, bh, nm) in cm.OPTIONS["A"][2]}
    w = np.abs(beta) / np.abs(beta).sum()
    old = pd.read_csv(OLD_CSV)
    new = pd.read_csv(NEW_CSV)
    old["era"], new["era"] = "old", "new"

    def legacy(df):
        conds, bands = [], []
        for _, r in df.iterrows():
            s = cm.score_patient(art, {c: (float(r[c]) if pd.notna(r[c]) else None) for c in cols})
            o = s["options"]["A"]
            conds.append(o["condition"] if o["available"] else np.nan)
            bands.append(ORD[o["band"]] if o["available"] else -1)
        return np.array(conds, float), np.array(bands, int)

    for df in (old, new):
        df["legacy"], df["legacy_band"] = legacy(df)
    # oriented percentiles: in the OWN pool, and (for new) in the OLD pool = the legacy ruler
    for df, pool, tag in ((old, old, "own"), (new, new, "own"), (new, old, "oldpool")):
        for c in cols:
            df[f"q_{c}_{tag}"] = oriented_pct(df, pool, c, bool(axes[c]["better_high"]))
        q = np.vstack([df[f"q_{c}_{tag}"].to_numpy() for c in cols]).T
        df[f"recipe_{tag}"] = np.where(np.isnan(q).any(1), np.nan, (q * w).sum(1))

    # cut-points from the old doctors' consensus (triple-rated rows with a complete recipe)
    lab = old[(old["triple_rated"] == True) & old["quality_label"].isin(ORD) & old["recipe_own"].notna()].copy()  # noqa: E712
    lab["grade"] = lab["quality_label"].map(ORD).astype(int)
    cuts, k_in = best_cuts(lab["recipe_own"].to_numpy(), lab["grade"].to_numpy())
    # 5-fold cross-validated QWK of the recipe bands (cuts fit on 4 folds, scored on the 5th)
    rng = np.random.default_rng(20260904)
    idx = np.arange(len(lab)); rng.shuffle(idx)
    folds = np.array_split(idx, 5)
    cv_pred = np.zeros(len(lab), int)
    for f in folds:
        train = np.setdiff1d(idx, f)
        c_f, _ = best_cuts(lab["recipe_own"].to_numpy()[train], lab["grade"].to_numpy()[train])
        cv_pred[f] = band_of(lab["recipe_own"].to_numpy()[f], c_f)
    k_cv = qwk(lab["grade"].to_numpy(), cv_pred)
    lab_leg = lab[lab["legacy_band"] >= 0]
    k_legacy_in = qwk(lab_leg["grade"].to_numpy(), lab_leg["legacy_band"].to_numpy())
    # the recipe's agreement with the doctors, per axis alone (what each axis buys)
    per_axis = {}
    for c in cols:
        s = lab[f"q_{c}_own"].to_numpy()
        cc, kk = best_cuts(s, lab["grade"].to_numpy())
        per_axis[c] = {"qwk_in_sample": round(kk, 3), "cuts": cc}

    # bands + contradictions
    for df, tag in ((old, "own"), (new, "own"), (new, "oldpool")):
        df[f"recipe_band_{tag}"] = np.where(df[f"recipe_{tag}"].isna(), -1, band_of(df[f"recipe_{tag}"].fillna(50).to_numpy(), cuts))

    def contradiction_census(df, tag):
        ok = (df["legacy_band"] >= 0) & (df[f"recipe_band_{tag}"] >= 0)
        d = df[ok]
        diff = (d["legacy_band"] != d[f"recipe_band_{tag}"])
        two = (np.abs(d["legacy_band"] - d[f"recipe_band_{tag}"]) == 2)
        # the "p6 but INTERMEDIATE" pattern: legacy says INT or PRES while ALL three own-pool percentiles are below 25
        low_all = (d[[f"q_{c}_{tag}" for c in cols]] < 25).all(1)
        high_all = (d[[f"q_{c}_{tag}" for c in cols]] > 75).all(1)
        return {"n": int(ok.sum()), "band_differs": int(diff.sum()), "two_bands_apart": int(two.sum()),
                "legacy_not_poor_but_all_axes_below_p25": int((low_all & (d["legacy_band"] < 2)).sum()),
                "legacy_not_preserved_but_all_axes_above_p75": int((high_all & (d["legacy_band"] > 0)).sum()),
                "legacy_bands": {BAND[b]: int((d["legacy_band"] == b).sum()) for b in (0, 1, 2)},
                "recipe_bands": {BAND[b]: int((d[f"recipe_band_{tag}"] == b).sum()) for b in (0, 1, 2)}}

    # the era shift, in the recipe's own units: new studies scored in the OLD pool vs in their own pool
    shift = new["recipe_oldpool"] - new["recipe_own"]
    # illustrative new studies: largest disagreement between the legacy band and the own-pool recipe band
    new["gap"] = (new["legacy_band"] - new["recipe_band_own"]).abs()
    ex = new[(new["legacy_band"] >= 0) & (new["recipe_band_own"] >= 0)].sort_values(["gap", "recipe_own"], ascending=[False, True]).head(8)
    examples = [{"study_code": r["study_code"], "sex": r["sex"], "age": int(r["age"]) if pd.notna(r["age"]) else None,
                 "legacy_score": round(float(r["legacy"]), 0), "legacy_band": BAND[int(r["legacy_band"])],
                 "size_pct": round(float(r[f"q_{cols[0]}_own"]), 0), "fat_pct_good": round(float(r[f"q_{cols[1]}_own"]), 0),
                 "texture_pct": round(float(r[f"q_{cols[2]}_own"]), 0),
                 "recipe": round(float(r["recipe_own"]), 0), "recipe_band": BAND[int(r["recipe_band_own"])]}
                for _, r in ex.iterrows()]
    # the two men of his example, found in the data: a lean small clean muscle vs a big fatty washed-out one
    def pick(df, cond):
        d = df[cond & (df["legacy_band"] >= 0)]
        return None if d.empty else d.iloc[0]
    athlete = pick(new, (new[f"q_{cols[0]}_own"] < 40) & (new[f"q_{cols[1]}_own"] > 70) & (new[f"q_{cols[2]}_own"] > 70))
    beast = pick(new, (new[f"q_{cols[0]}_own"] > 75) & (new[f"q_{cols[1]}_own"] < 30) & (new[f"q_{cols[2]}_own"] < 30))
    def row_of(r):
        return None if r is None else {"study_code": r["study_code"], "sex": r["sex"], "legacy_score": round(float(r["legacy"]), 0),
                                       "legacy_band": BAND[int(r["legacy_band"])], "size_pct": round(float(r[f"q_{cols[0]}_own"]), 0),
                                       "fat_pct_good": round(float(r[f"q_{cols[1]}_own"]), 0), "texture_pct": round(float(r[f"q_{cols[2]}_own"]), 0),
                                       "recipe": round(float(r["recipe_own"]), 0), "recipe_band": BAND[int(r["recipe_band_own"])]}
    res = {
        "served_option": "A", "axes": [{"col": c, "name": axes[c].get("name"), "better_high": bool(axes[c]["better_high"]),
                                       "beta": float(b), "weight": round(float(x), 3)} for c, b, x in zip(cols, beta, w)],
        "legacy_boundaries": {"int_poor": A["boundary_int_poor"], "pres_int": A["boundary_pres_int"]},
        "recipe_cuts_from_old_consensus": {"poor_below": cuts[0], "preserved_from": cuts[1], "n_labelled": int(len(lab)),
                                           "qwk_in_sample": round(k_in, 3), "qwk_5fold_cv": round(k_cv, 3)},
        "legacy_vs_old_consensus_qwk_in_sample": {"qwk": round(k_legacy_in, 3), "n": int(len(lab_leg)),
                                                  "note": "the frozen model was fit on these rows; optimistic by construction"},
        "per_axis_alone_vs_old_consensus": per_axis,
        "contradictions": {"old_own_pool": contradiction_census(old, "own"), "new_own_pool": contradiction_census(new, "own"),
                           "new_old_pool": contradiction_census(new, "oldpool")},
        "era_shift_recipe_units": {"median": round(float(shift.median()), 1), "p25": round(float(shift.quantile(.25)), 1),
                                   "p75": round(float(shift.quantile(.75)), 1), "n": int(shift.notna().sum()),
                                   "meaning": "recipe in the OLD pool minus recipe in the own pool, per new study (positive = the old ruler flatters)"},
        "new_recipe_distribution_own_pool": {k: round(float(v), 1) for k, v in new["recipe_own"].describe().items()},
        "old_recipe_distribution": {k: round(float(v), 1) for k, v in old["recipe_own"].describe().items()},
        "examples_new_largest_disagreement": examples,
        "his_two_men_in_the_data": {"lean_small_clean": row_of(athlete), "big_fatty_washed_out": row_of(beast)},
        "n": {"old": int(len(old)), "new": int(len(new)), "old_scored": int((old["legacy_band"] >= 0).sum()),
              "new_scored": int((new["legacy_band"] >= 0).sum()), "new_recipe": int(new["recipe_own"].notna().sum())},
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("axes", "recipe_cuts_from_old_consensus", "legacy_vs_old_consensus_qwk_in_sample",
                                          "contradictions", "era_shift_recipe_units", "n")}, indent=1))
    print("examples:", json.dumps(res["examples_new_largest_disagreement"][:4], indent=1))
    print("two men:", json.dumps(res["his_two_men_in_the_data"], indent=1))


if __name__ == "__main__":
    main()

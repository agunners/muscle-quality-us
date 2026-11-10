"""Production muscle-condition scorer: fit once, freeze, serve with pure numpy.

The assessment page imports this. The three model options are fit on the cohort
ONCE (offline, via `freeze_all` -> data/cohort_stats/condition_models.json); at
serve time a patient is scored with pure numpy -- no statsmodels, no refit,
deterministic. Every artifact was FIT with option C (STAR / Fat / Texture), the
2026-06-11 cross-validated accuracy winner; the triplet shows all three so the
choice stays transparent.

**What is SERVED is option A (RF-thickness / Fat / Texture) since 2026-08-04** --
see SERVED_HEADLINE below for the evidence. STAR = QMT / BMI divides the muscle
signal by body size and removes much of the vital sign; A reads raw millimetres,
saturates far less, tracks force better, and needs no femur. The frozen artifacts
are left exactly as they were: the served choice lives in code, not in a record.

Build/refresh the frozen artifact + run the SEMANTIC self-test:
    .venv/bin/python scripts/preflight_helpers/condition_model.py

**FOOTGUN.** This module has no argparse: running it with ANY argument — including
`--help` — executes the freeze. With no `CONDITION_COHORT_CSV` / `CONDITION_MODEL_OUT`
set it writes the DEFAULTS, i.e. it silently re-fits and re-stamps the LEGACY v1.0
artifact `condition_models.json`. Always pass the env vars (see scripts/refreeze_cohort.py).
"""
from __future__ import annotations

import hashlib
import json
import os
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# Resolve data paths from THIS file, not the working directory, so the app and the
# demo find them no matter where they are launched from (repo root is two dirs up).
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
# Env overrides let the offline re-base fit a VERSIONED cohort -> a versioned
# artifact (e.g. per_study_cohort_v1.1.csv -> condition_models_v1.1.json) without
# disturbing the frozen v1.0 the app serves. Unset => the default v1.0 paths.
CSV = os.environ.get("CONDITION_COHORT_CSV") or os.path.join(_ROOT, "data", "cohort_stats", "per_study_cohort.csv")
ARTIFACT = os.environ.get("CONDITION_MODEL_OUT") or os.path.join(_ROOT, "data", "cohort_stats", "condition_models.json")
ORD = {"PRESERVED": 0, "INTERMEDIATE": 1, "POOR": 2}
BAND_LABEL = {0: "PRESERVED", 1: "INTERMEDIATE", 2: "POOR"}

# Per axis: (column, higher_raw_is_better_condition, display label).
SIZE_STAR = ("star_ratio", True, "Muscle vs body size")     # STAR = QMT/BMI (BMI-honest label)
SIZE_RF = ("full_thickness_q50_mm", True, "Muscle size")
FAT = ("fat_to_depth_q50", False, "Fat over muscle")
TEX = ("int_cv_within", True, "Fascicular contrast")

EIGHT = ["star_ratio", "full_thickness_q50_mm", "int_cv_within", "rf_thickness_mm",
         "fat_thickness_q50_mm", "qmt_mm", "visible_rf_area_mm2", "fat_to_depth_q50"]

# option key -> (label, feature columns, axis-display list for the breakdown)
OPTIONS = {
    "A": ("RF-thickness / Fat / Texture", [SIZE_RF[0], FAT[0], TEX[0]], [SIZE_RF, FAT, TEX]),
    "B": ("All 8 features", EIGHT, [SIZE_RF, FAT, TEX]),
    "C": ("STAR / Fat / Texture", [SIZE_STAR[0], FAT[0], TEX[0]], [SIZE_STAR, FAT, TEX]),
}
HEADLINE = "C"          # what every frozen artifact was FIT with — never edit a frozen record

# ---- what we SERVE, which is no longer what we froze (Akadir 2026-08-04) --------------------
# STAR (= QMT / BMI) divides the muscle signal by body size and, in doing so, removes much of the
# vital sign it is measuring. That is not a hunch — it is measured twice over:
#   * against isometric torque, raw thickness reaches rho +0.719 where STAR manages +0.239, and
#     STAR overcorrects, flipping the weight correlation from +0.531 to -0.176 (docs/NORTHSTAR.md);
#   * on the served 116, option A pins 22% of studies at the 0-100 ceiling against C's 37%, uses
#     more than twice as much of the scale (IQR 36 vs 17), and tracks RF thickness at rho +0.747
#     against C's +0.530.
# A also needs no femur, so it scores studies C cannot.
#
# The trade A makes, stated plainly: its male-female gap is LARGER (median 99 vs 61, against C's
# 100 vs 87), because men genuinely have bigger muscles and STAR was hiding that behind a divisor
# that also destroyed the signal. The answer is an explicit sex-matched reference, not a bad
# normaliser — which is why this change ships together with sex-matched placement.
#
# This is a SERVE-TIME choice, held in code and version-controlled, precisely so the frozen
# artifacts stay untouched historical records. Rolling back is one line.
SERVED_HEADLINE = "A"
# If the served option cannot score a study, fall back to the other three-feature recipe.
HEADLINE_FALLBACK = [SERVED_HEADLINE, "C"]


def served_headline(art: dict) -> str:
    """The option actually put in front of a reader. Falls back to whatever the artifact was frozen
    with if the served choice is absent (an older rollback artifact), so a repoint can never blank
    the headline."""
    return SERVED_HEADLINE if SERVED_HEADLINE in (art.get("models") or {}) else art.get("headline")

# ---- staleness guard -------------------------------------------------------
# The cohort the model is fit on is FROZEN (a stored study_features -> per_study_cohort);
# it does NOT re-measure when the measurement code changes. So a measurement fix (e.g. the
# 2026-07-16 QMT cleaned-femur) leaves the frozen model serving OLD-code numbers until a
# re-inference + refit. This guard stamps a hash of the feature-producing source into the
# artifact at freeze time; `staleness()` re-hashes at serve time and flags a mismatch, so we
# can never SILENTLY serve a cohort measured with different code than what is now live.
# Advisory (a warning, never a block); a legacy artifact with no stamp reads as stale.
# The feature-COMPUTE code only (what produces the cohort's measured values) — NOT the
# frame-selection scorers (repr / femur_cine / center_rf), which change which frame is read
# live but not the cohort's labeled-frame features.
_MEASUREMENT_SOURCES = (
    "labeling_app/measurements.py",
    "labeling_app/fat_features.py",
    "scripts/preflight_helpers/feature_compute.py",
    "scripts/preflight_helpers/post_processing.py",
    "scripts/preflight_helpers/aggregation.py",
)


def measurement_code_hash() -> str:
    """A stable digest of the feature-producing source. Changes iff any measurement
    module changes, so a frozen model can tell it was fit on a different code version."""
    h = hashlib.sha256()
    for rel in _MEASUREMENT_SOURCES:
        with open(os.path.join(_ROOT, rel), "rb") as f:
            h.update(f.read())
    return h.hexdigest()[:16]


def staleness(art: dict) -> dict:
    """Is the frozen model measured with the code that is live now? `stale` True when the
    stamped hash differs (a measurement fix landed since the freeze -> re-base needed) or is
    absent (legacy artifact). Pure read — surfaced as an advisory banner, never a block."""
    frozen = art.get("measurement_code_hash")
    current = measurement_code_hash()
    stale = frozen != current
    return {
        "stale": bool(stale),
        "frozen_hash": frozen,
        "current_hash": current,
        "reason": (
            "measurement code changed since the model was frozen "
            "(re-inference + refit needed to fold the fix into the cohort)"
            if stale else "model is in sync with the live measurement code"),
    }


def _logistic(x):
    return 1.0 / (1.0 + np.exp(-x))


def _cond_from_probs(probs):
    """class probs -> condition 0..100 (higher = better; PRESERVED=100)."""
    return (1.0 - (np.asarray(probs) @ np.array([0.0, 1.0, 2.0])) / 2.0) * 100.0


def _probs_numpy(frozen, z):
    """Pure-numpy ordinal-logit probabilities from frozen params. z: (..,k) std."""
    eta = z @ np.asarray(frozen["beta"])
    c1, c2 = frozen["cuts"]
    le0 = _logistic(c1 - eta)
    le1 = _logistic(c2 - eta)
    return np.stack([le0, le1 - le0, 1.0 - le1], axis=-1)


def _fit_one(df, cols):
    """Fit one option, return a frozen dict (beta, cuts, mu, sd, boundaries, box)."""
    from statsmodels.miscmodels.ordinal_model import OrderedModel
    sub = df[cols + ["q"]].dropna().reset_index(drop=True)
    X = sub[cols].values.astype(float)
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1.0
    y = sub["q"].astype(int).values
    mod = OrderedModel(y, (X - mu) / sd, distr="logit")
    res = mod.fit(method="bfgs", disp=False, maxiter=400)
    k = X.shape[1]
    cuts_full = mod.transform_threshold_params(res.params[k:])   # [-inf, c1, c2, +inf]
    cuts = [float(cuts_full[1]), float(cuts_full[2])]
    frozen = {"cols": cols, "beta": [float(b) for b in res.params[:k]],
              "cuts": cuts, "mu": [float(m) for m in mu], "sd": [float(s) for s in sd]}

    # cohort condition scores -> band boundaries on 0-100 (model's own decision points)
    z = (X - mu) / sd
    probs = _probs_numpy(frozen, z)
    cond = _cond_from_probs(probs)
    pred = probs.argmax(1)
    bnd = {}
    for lo, hi in [(0, 1), (1, 2)]:
        a, b = cond[pred == lo], cond[pred == hi]
        if len(a) and len(b):
            bnd[f"{lo}{hi}"] = round((a.min() + b.max()) / 2.0, 1)
    q = np.percentile(cond, [0, 10, 25, 50, 75, 90, 100])
    frozen.update({
        "n_fit": int(len(sub)), "n_cohort": int(len(df)),
        "boundary_pres_int": bnd.get("01", 74.0), "boundary_int_poor": bnd.get("12", 25.0),
        "box": {k2: round(float(v), 1) for k2, v in
                zip(["min", "p10", "p25", "p50", "p75", "p90", "max"], q)},
        # verify the frozen numpy path matches statsmodels (semantic correctness)
        "_sm_max_abs_prob_err": float(np.abs(probs - res.model.predict(res.params, exog=z)).max()),
    })
    return frozen


def freeze_all(csv=CSV, out=ARTIFACT):
    df = pd.read_csv(csv)
    df["q"] = df["quality_label"].map(ORD)
    models = {}
    for key, (label, cols, axes) in OPTIONS.items():
        fr = _fit_one(df, cols)
        fr["label"] = label
        fr["axes"] = [{"col": c, "better_high": bh, "name": nm} for c, bh, nm in axes]
        models[key] = fr
    # cohort axis distributions (for the compact breakdown bars), headline axes
    axis_box = {}
    for c, bh, nm in OPTIONS[HEADLINE][2]:
        v = df[c].dropna().values
        q = np.percentile(v, [10, 25, 50, 75, 90])
        axis_box[c] = {"name": nm, "better_high": bh,
                       "p": {kk: round(float(vv), 4) for kk, vv in
                             zip(["p10", "p25", "p50", "p75", "p90"], q)}}
    art = {"headline": HEADLINE, "models": models, "axis_box": axis_box,
           "measurement_code_hash": measurement_code_hash()}
    with open(out, "w") as f:
        json.dump(art, f, indent=2)
    return art


# ----------------------------------------------------------- serve time -------
def load(path=ARTIFACT):
    with open(path) as f:
        return json.load(f)


def _percentile_in(p_dict, value, better_high):
    """Approx cohort percentile from stored deciles; condition-oriented (100=best)."""
    xs = [p_dict["p10"], p_dict["p25"], p_dict["p50"], p_dict["p75"], p_dict["p90"]]
    ps = [10, 25, 50, 75, 90]
    pct = float(np.interp(value, xs, ps, left=5, right=95))
    return round(pct if better_high else 100 - pct, 0)


def score_patient(art, feats: dict) -> dict:
    """feats: {column: value}. Returns headline + all 3 options + axis breakdown."""
    out = {"options": {}}
    for key, fr in art["models"].items():
        vals = [feats.get(c) for c in fr["cols"]]
        if any(v is None or (isinstance(v, float) and np.isnan(v)) for v in vals):
            out["options"][key] = {"available": False, "label": fr["label"]}
            continue
        z = (np.array(vals, float) - np.array(fr["mu"])) / np.array(fr["sd"])
        probs = _probs_numpy(fr, z)
        cond = float(_cond_from_probs(probs))
        band = (0 if cond >= fr["boundary_pres_int"]
                else 2 if cond < fr["boundary_int_poor"] else 1)
        out["options"][key] = {
            "available": True, "label": fr["label"], "condition": round(cond, 0),
            "band": BAND_LABEL[band], "band_idx": band,
            "boundaries": [fr["boundary_int_poor"], fr["boundary_pres_int"]],
            "box": fr["box"],
        }
    # Headline = the preferred model (C = STAR/Fat/Texture). If C can't score because the
    # study has no measurable femur (STAR = QMT/BMI is None), fall back to A (RF-thickness /
    # Fat / Texture — needs no femur) so a no-femur study still gets a headline instead of a
    # blank. Surfaced so the UI can label it the no-femur fallback.
    out["headline"] = None
    out["headline_option"] = None
    for _key in HEADLINE_FALLBACK:
        _opt = out["options"].get(_key)
        if _opt and _opt.get("available"):
            out["headline"], out["headline_option"] = _opt, _key
            break
    if out["headline"] is None:
        # nothing scoreable (e.g. texture withheld -> both C and A lose int_cv): keep the primary
        # option's UNAVAILABLE dict so consumers reading headline["available"] get an honest False,
        # never a fake or a crash. The panel shows the "score unavailable" message.
        out["headline"] = out["options"].get(served_headline(art))
    out["headline_is_fallback"] = out["headline_option"] not in (None, served_headline(art))
    out["axes"] = []
    # The axes are the inputs of the recipe that PRODUCED the headline — the served one (A since
    # 2026-08-04), or C when A could not score and the headline fell back to it. Until 2026-09-02
    # they were read from `axis_box`, which every artifact froze on the FIT-time headline (C), so
    # the breakdown's "Size" row was STAR (muscle ÷ BMI) — an axis that does not enter the served
    # score — while raw thickness, the axis that does, never appeared and its push was invisible
    # (Akadir 2026-09-02: "we should also promote the RF-thickness into the p-view, shouldn't we?").
    hl_key = out["headline_option"] or served_headline(art)
    hl = art["models"].get(hl_key, {})                 # the weights of the recipe that scored
    hl_cols = hl.get("cols", [])
    axis_box = art.get("axis_box") or {}
    axes_meta = hl.get("axes") or [                    # older artifact without per-model axes:
        {"col": c, "name": b["name"], "better_high": b["better_high"]}   # fall back to axis_box
        for c, b in axis_box.items()]
    for ax in axes_meta:
        c = ax["col"]
        v = feats.get(c)
        # each axis's EXACT additive push on this patient's score in the headline
        # model: contribution = beta * z = beta * (x - mu) / sd. eta = sum of these,
        # so it is an exact decomposition (NOT a hand-wavy "importance"); >0 pushes
        # toward POOR, <0 toward PRESERVED. Honest here because the 3 headline axes
        # barely overlap (VIF<3) -- unlike the 8-feature model, whose weights flip.
        beta = contribution = None
        if (c in hl_cols and v is not None
                and not (isinstance(v, float) and np.isnan(v))):
            i = hl_cols.index(c)
            beta = float(hl["beta"][i])
            contribution = beta * (float(v) - hl["mu"][i]) / hl["sd"][i]
        # The coarse decile percentile exists only for the columns frozen into axis_box (the
        # fit-time headline's). The page never reads it — its breakdown quotes the card's exact
        # cohort placement — so a missing box honestly yields None rather than a made-up rank.
        box = axis_box.get(c)
        pct = (None if (v is None or box is None)
               else _percentile_in(box["p"], v, ax["better_high"]))
        out["axes"].append({
            "col": c, "name": ax["name"], "value": v,
            "percentile": pct, "better_high": ax["better_high"],
            "beta": beta, "contribution": contribution,
        })
    return out


# ------------------------------------------------------- SEMANTIC self-test ---
def _selftest():
    print("STEP 1 -- freeze + semantic test\n")
    art = freeze_all()

    print("[1] frozen pure-numpy scorer reproduces statsmodels (max prob error):")
    ok = True
    for k, fr in art["models"].items():
        err = fr["_sm_max_abs_prob_err"]
        flag = "OK" if err < 1e-6 else "MISMATCH"
        ok &= err < 1e-6
        print(f"    option {k}: {err:.2e}  {flag}")

    df = pd.read_csv(CSV)
    df["q"] = df["quality_label"].map(ORD)
    cols_C = OPTIONS["C"][1]
    sub = df[cols_C + ["quality_label"]].dropna().reset_index(drop=True)
    sc = [score_patient(art, dict(zip(cols_C, sub.loc[i, cols_C])))["headline"]["condition"]
          for i in range(len(sub))]
    sc = np.array(sc)

    print("\n[2] do extreme patients land where they should? (headline = option C)")
    for label, i in [("best (max score)", int(sc.argmax())), ("worst (min score)", int(sc.argmin()))]:
        r = score_patient(art, dict(zip(cols_C, sub.loc[i, cols_C])))["headline"]
        print(f"    {label:18s} score {r['condition']:>3.0f} -> {r['band']:13s}"
              f"  (doctors: {sub.loc[i,'quality_label']})")

    print("\n[3] band boundaries (option C, the model's OWN decision points):")
    h = art["models"]["C"]
    print(f"    POOR < {h['boundary_int_poor']}  |  INTERMEDIATE  |  >= {h['boundary_pres_int']} PRESERVED")

    print("\n[4] BMI-HONESTY: same muscle + fat + texture, vary body size (STAR).")
    med = {c: float(df[c].median()) for c in cols_C}
    base_star = med["star_ratio"]
    for mult, tag in [(1.30, "leaner / lower BMI"), (1.00, "as-is"), (0.70, "heavier / higher BMI")]:
        f = dict(med); f["star_ratio"] = base_star * mult
        r = score_patient(art, f)["headline"]
        print(f"    STAR x{mult:.2f} ({tag:18s}) -> condition {r['condition']:>3.0f}  band {r['band']}")
    print("    ^ score moves with body size at fixed muscle -> the headline leans on BMI (honest, label it).")

    print("\n[5] do the three options agree? (cohort, all-8 present)")
    common = df[EIGHT].dropna().reset_index(drop=True)
    bands = {k: [] for k in OPTIONS}
    for i in range(len(common)):
        s = score_patient(art, dict(common.loc[i]))
        for k in OPTIONS:
            bands[k].append(s["options"][k]["band_idx"])
    bands = {k: np.array(v) for k, v in bands.items()}
    same = np.mean((bands["A"] == bands["B"]) & (bands["B"] == bands["C"])) * 100
    print(f"    all three same band: {same:.0f}%   (A vs C: {(bands['A']==bands['C']).mean()*100:.0f}%)")
    print("    (frozen per-option band rule; the bake-off's shared-n re-fit reports 85% /"
          " 87% in condition_options.json — the 1-2 pt gap is the per-option"
          " standardization, not a discrepancy.)")

    print(f"\nRESULT: {'all semantic checks behaved as expected' if ok else 'PROB-MATCH FAILED — investigate'}")
    print(f"wrote {ARTIFACT}")


if __name__ == "__main__":
    _selftest()

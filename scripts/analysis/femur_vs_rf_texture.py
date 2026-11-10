"""Femur-cine vs centre-RF-cine as the source of the texture/brightness features.

Reads the live repo files READ-ONLY (freeze r5 served, unchanged) and writes tables.md,
results.json, run.log beside itself. PHI: qa_master.study_name / rel_path and
qa_frames.rel_jpg_path / capture_dir are NEVER loaded (usecols excludes them);
no study identifier is printed anywhere.

Repeatability recipe = scripts/analysis/refute.py:69-98 `ratios()` copied verbatim
(MDC95 = 1.96*SD(diff), ICC(2,1) two-way random absolute agreement,
CVw = 100*(SD(diff)/sqrt2)/|grand mean|).
"""
from pathlib import Path
import json, warnings, sys
import numpy as np, pandas as pd
from scipy import stats
import statsmodels.api as sm
warnings.filterwarnings("ignore")

REPO = str(Path(__file__).resolve().parents[2])
CF = f"{REPO}/scripts/analysis/results/capture_features.csv"
REP = f"{REPO}/scripts/analysis/results/repeatability_n116.csv"
QA_MASTER = f"{REPO}/data/qa_master.csv"
QA_FRAMES = f"{REPO}/data/qa_frames.csv"
OUT = str(Path(__file__).resolve().parent / "results")  # receipts beside the other analyses

FEATS = ["int_cv_within", "int_mean", "int_std", "int_depth_gradient",
         "glcm_d1_contrast", "glcm_d1_homogeneity", "int_entropy"]
ROLES = ("femur", "rf", "sweep")
PAIRS = [("femur", "sweep"), ("rf", "sweep"), ("femur", "rf")]

log_lines = []
def log(*a):
    s = " ".join(str(x) for x in a)
    log_lines.append(s); print(s, flush=True)

md = []
def md_table(title, df, fmt=None, index=True):
    md.append(f"\n**{title}**\n")
    d = df.copy()
    if fmt:
        for c, f in fmt.items():
            if c in d.columns:
                d[c] = d[c].map(lambda v: "" if (v is None or (isinstance(v, float) and not np.isfinite(v))) else f.format(v))
    cols = ([d.index.name or ""] if index else []) + list(d.columns)
    md.append("| " + " | ".join(cols) + " |")
    md.append("|" + "|".join(["---"] * len(cols)) + "|")
    for idx, row in d.iterrows():
        vals = ([str(idx)] if index else []) + [str(v) for v in row.values]
        md.append("| " + " | ".join(vals) + " |")

RES = {}

# ---------------------------------------------------------------- load ----
cf = pd.read_csv(CF)
assert cf.role.isin(ROLES).all()
W = {r: cf[cf.role == r].set_index("study_uid") for r in ROLES}
UIDS = sorted(set(W["femur"].index) & set(W["rf"].index) & set(W["sweep"].index))
log(f"capture_features: {len(cf)} captures, {cf.study_uid.nunique()} studies; "
    f"studies with all three roles = {len(UIDS)}")

QM_COLS = ["study_uid", "cohort_round", "age", "sex", "height_cm", "weight_kg", "bmi", "hw_source",
           "izo_torque_nm", "izo_cv_pct", "full_thickness_q50_mm", "fat_to_depth_q50",
           "fat_thickness_q50_mm", "int_cv_within", "int_cv_trust", "texture_state", "rf_source",
           "measurement_flags", "qmt_mm", "rf_thickness_mm"]
qm = pd.read_csv(QA_MASTER, usecols=QM_COLS)      # study_name / rel_path never loaded
log(f"qa_master: {len(qm)} rows; cohort_round values: {qm.cohort_round.value_counts(dropna=False).to_dict()}")
log(f"  sex values: {qm.sex.value_counts(dropna=False).to_dict()}")
log(f"  int_cv_trust values: {qm.int_cv_trust.value_counts(dropna=False).to_dict()}")
log(f"  texture_state values: {qm.texture_state.value_counts(dropna=False).to_dict()}")
log(f"  rf_source values: {qm.rf_source.value_counts(dropna=False).to_dict()}")
log(f"  hw_source values: {qm.hw_source.value_counts(dropna=False).to_dict()}")
qm = qm.drop_duplicates("study_uid").set_index("study_uid")

qf = pd.read_csv(QA_FRAMES, usecols=["study_uid", "role", "depth_cm", "pixel_spacing_row_mm",
                                     "n_frames_in_capture", "in_auto_landmark"])
qf = qf[qf.study_uid.isin(UIDS)]
log(f"qa_frames (restricted to the {len(UIDS)} substrate studies): {len(qf)} frames")

rep = pd.read_csv(REP).set_index("stem")

# ------------------------------------------------ the refute.py recipe ----
def ratios(a, b, idx=None):
    """Canonical repeatability on a set of subject indices. Returns dict.  (refute.py:69-98 verbatim)"""
    if idx is not None:
        a, b = a[idx], b[idx]
    m = np.isfinite(a) & np.isfinite(b)
    a, b = a[m], b[m]
    n = len(a)
    if n < 8: return None
    d = a - b
    sd_diff = d.std(ddof=1)
    mdc95 = 1.96 * sd_diff                    # = 1.96*sqrt(2)*SEM, SEM=sd_diff/sqrt2
    means = (a + b) / 2
    sd_mean = means.std(ddof=1)               # SD of subject means  (denominator A)
    sd_single = np.sqrt(max(np.r_[a, b].var(ddof=1), 1e-30))  # SD of one occasion (denom B)
    # ICC(2,1) two-way random, absolute agreement
    gm = np.r_[a, b].mean()
    ms_r = 2 * ((means - gm) ** 2).sum() / (n - 1)
    ms_c = n * ((a.mean() - gm) ** 2 + (b.mean() - gm) ** 2)
    ms_e = (((a - means) ** 2).sum() + ((b - means) ** 2).sum()) / (n - 1)
    icc = (ms_r - ms_e) / (ms_r + ms_e + 2 * (ms_c - ms_e) / n) if (ms_r + ms_e) > 0 else np.nan
    # robust variants (outlier-proof): 1.4826*MAD
    rsd_diff = 1.4826 * np.median(np.abs(d - np.median(d)))
    rsd_mean = 1.4826 * np.median(np.abs(means - np.median(means)))
    return dict(n=n, mdc95=mdc95, sd_mean=sd_mean, sd_single=sd_single,
                ratio_A=mdc95 / sd_mean if sd_mean > 0 else np.nan,
                ratio_B=mdc95 / sd_single if sd_single > 0 else np.nan,
                ratio_robust=(1.96 * rsd_diff / rsd_mean) if rsd_mean > 0 else np.nan,
                icc=icc, cvw=100 * (sd_diff / np.sqrt(2)) / abs(np.r_[a, b].mean())
                        if np.r_[a, b].mean() != 0 else np.nan,
                bias_sd=(d.mean() / sd_mean) if sd_mean > 0 else np.nan)

def col(role, stem, uids=UIDS):
    return W[role][stem].reindex(uids).to_numpy(float)

def icc31(a, b):
    """ICC(3,1) consistency (two-way mixed) — bias-insensitive companion to ICC(2,1)."""
    m = np.isfinite(a) & np.isfinite(b); a, b = a[m], b[m]; n = len(a)
    means = (a + b) / 2; gm = np.r_[a, b].mean()
    ms_r = 2 * ((means - gm) ** 2).sum() / (n - 1)
    ms_c = n * ((a.mean() - gm) ** 2 + (b.mean() - gm) ** 2)
    ms_e = ((((a - means) ** 2).sum() + ((b - means) ** 2).sum()) - ms_c) / (n - 1)
    return (ms_r - ms_e) / (ms_r + ms_e)

# =================================================================== Q0 ----
served = qm["int_cv_within"].reindex(UIDS).to_numpy(float)
rf_cv = col("rf", "int_cv_within")
m = np.isfinite(served) & np.isfinite(rf_cv)
d = np.abs(served[m] - rf_cv[m])
RES["served_vs_rf_role"] = dict(n=int(m.sum()), rho=float(stats.spearmanr(served[m], rf_cv[m])[0]),
                                median_absdiff=float(np.median(d)), p90_absdiff=float(np.percentile(d, 90)),
                                n_absdiff_gt_0p01=int((d > 0.01).sum()),
                                n_served_missing_of_116=int((~np.isfinite(served)).sum()))
log("Q0 served (qa_master) vs rf-role capture value:", RES["served_vs_rf_role"])

# =================================================================== Q1 ----
rows = []
for f in FEATS:
    a, b = col("femur", f), col("rf", f)
    m = np.isfinite(a) & np.isfinite(b); a, b = a[m], b[m]
    d = a - b; mean_pair = (a + b) / 2
    sd_rf = b.std(ddof=1); sd_d = d.std(ddof=1)
    rho, rho_p = stats.spearmanr(a, b); r, _ = stats.pearsonr(a, b)
    slope, intercept, r_pb, p_pb, _ = stats.linregress(mean_pair, d)
    rr = ratios(a, b)
    rows.append(dict(feature=f, n=int(m.sum()), rho=rho, pearson_r=r,
                     mean_rf=b.mean(), sd_rf=sd_rf, mean_femur=a.mean(), sd_femur=a.std(ddof=1),
                     offset=d.mean(), offset_in_sd_rf=d.mean() / sd_rf,
                     loa_lo=d.mean() - 1.96 * sd_d, loa_hi=d.mean() + 1.96 * sd_d,
                     loa_halfwidth_in_sd_rf=1.96 * sd_d / sd_rf,
                     paired_t_p=stats.ttest_rel(a, b).pvalue,
                     prop_bias_slope=slope, prop_bias_p=p_pb,
                     icc21=rr["icc"], icc31=icc31(a, b),
                     sd_ratio_femur_over_rf=a.std(ddof=1) / sd_rf))
Q1 = pd.DataFrame(rows).set_index("feature")
RES["Q1"] = Q1.reset_index().to_dict("records")
log("\nQ1 femur vs rf agreement\n", Q1.round(4).to_string())
md.append("\n## Q1. Femur-role vs RF-role: is it the same quantity?\n")
md_table("Q1a. Agreement (paired on study, femur minus rf)",
         Q1[["n", "rho", "pearson_r", "icc21", "icc31", "mean_rf", "sd_rf", "mean_femur", "sd_femur"]],
         fmt={c: "{:.3f}" for c in ["rho", "pearson_r", "icc21", "icc31", "mean_rf", "sd_rf", "mean_femur", "sd_femur"]})
md_table("Q1b. Offset and Bland-Altman (femur minus rf; SD_rf = between-study SD of the rf value)",
         Q1[["n", "offset", "offset_in_sd_rf", "loa_lo", "loa_hi", "loa_halfwidth_in_sd_rf", "paired_t_p",
             "prop_bias_slope", "prop_bias_p", "sd_ratio_femur_over_rf"]],
         fmt={"offset": "{:+.4f}", "offset_in_sd_rf": "{:+.2f}", "loa_lo": "{:+.4f}", "loa_hi": "{:+.4f}",
              "loa_halfwidth_in_sd_rf": "{:.2f}", "paired_t_p": "{:.2g}", "prop_bias_slope": "{:+.3f}",
              "prop_bias_p": "{:.2g}", "sd_ratio_femur_over_rf": "{:.2f}"})

rows = []
covs = {"full_thickness_q50_mm (served, femur)": qm["full_thickness_q50_mm"].reindex(UIDS).to_numpy(float),
        "fat_to_depth_q50 (served)": qm["fat_to_depth_q50"].reindex(UIDS).to_numpy(float),
        "fat_thickness_mm (femur role)": col("femur", "fat_thickness_mm"),
        "int_mean (rf role)": col("rf", "int_mean"),
        "area_retained_fraction (rf role)": col("rf", "area_retained_fraction_after_erosion"),
        "full_area_mm2 femur/rf ratio": col("femur", "full_area_mm2") / col("rf", "full_area_mm2")}
for f in ["int_cv_within", "int_mean"]:
    d = col("femur", f) - col("rf", f)
    for k, z in covs.items():
        mm = np.isfinite(d) & np.isfinite(z)
        rho, p = stats.spearmanr(d[mm], z[mm])
        rows.append(dict(feature=f, correlate=k, n=int(mm.sum()), rho=rho, p=p))
Q1c = pd.DataFrame(rows)
RES["Q1c"] = Q1c.to_dict("records")
log("\nQ1c correlates of the femur-minus-rf difference\n", Q1c.round(4).to_string())
md_table("Q1c. What the femur-minus-rf difference tracks (Spearman)", Q1c.set_index("feature"),
         fmt={"rho": "{:+.3f}", "p": "{:.2g}"})

rows = []
for f in FEATS:
    rec = dict(feature=f)
    for a_, b_ in PAIRS:
        a, b = col(a_, f), col(b_, f); mm = np.isfinite(a) & np.isfinite(b)
        rec[f"rho_{a_}_{b_}"] = stats.spearmanr(a[mm], b[mm])[0]; rec[f"n_{a_}_{b_}"] = int(mm.sum())
    rows.append(rec)
Q1d = pd.DataFrame(rows).set_index("feature")
RES["Q1d"] = Q1d.reset_index().to_dict("records")
md_table("Q1d. Spearman rho for the three pairings (same recipe, same studies)",
         Q1d[["rho_femur_sweep", "rho_rf_sweep", "rho_femur_rf", "n_femur_rf"]],
         fmt={c: "{:.3f}" for c in ["rho_femur_sweep", "rho_rf_sweep", "rho_femur_rf"]})

rows = []
for r in ROLES:
    v = col(r, "int_cv_within"); v = v[np.isfinite(v)]
    rows.append(dict(role=r, n=len(v), min=v.min(), p5=np.percentile(v, 5), p50=np.median(v), p95=np.percentile(v, 95), max=v.max(),
                     mean=v.mean(), sd=v.std(ddof=1), skew=stats.skew(v), excess_kurtosis=stats.kurtosis(v)))
Q1e = pd.DataFrame(rows).set_index("role")
RES["Q1e"] = Q1e.reset_index().to_dict("records")
md_table("Q1e. int_cv_within distribution per role (n=116)", Q1e, fmt={c: "{:.3f}" for c in Q1e.columns if c != "n"})

# =================================================================== Q2 ----
md.append("\n## Q2. Repeatability-style contrast across the three pairings\n")
rows = []
for f in FEATS:
    for a_, b_ in PAIRS:
        rr = ratios(col(a_, f), col(b_, f))
        rr.update(feature=f, pairing=f"{a_} vs {b_}")
        rows.append(rr)
Q2 = pd.DataFrame(rows)[["feature", "pairing", "n", "mdc95", "sd_mean", "sd_single", "ratio_A", "ratio_B",
                         "icc", "cvw", "bias_sd"]]
RES["Q2"] = Q2.to_dict("records")
log("\nQ2 three pairings\n", Q2.round(4).to_string())
chk = []
for f in FEATS:
    mine = Q2[(Q2.feature == f) & (Q2.pairing == "rf vs sweep")].iloc[0]
    ref = rep.loc[f]
    chk.append(dict(feature=f, mdc95_mine=mine.mdc95, mdc95_ref=ref.mdc95, icc_mine=mine.icc, icc_ref=ref.icc,
                    max_reldiff=max(abs(mine.mdc95 / ref.mdc95 - 1), abs(mine.icc / ref.icc - 1))))
CHK = pd.DataFrame(chk).set_index("feature")
RES["Q2_reproduction_vs_repeatability_n116"] = CHK.reset_index().to_dict("records")
log("\nreproduction of repeatability_n116.csv (rf vs sweep):\n", CHK.round(6).to_string())
md_table("Q2a. Within-pair agreement per pairing (refute.py recipe: MDC95 = 1.96*SD(diff); ratio_B = MDC95/SD of one occasion; ICC(2,1); CVw %)",
         Q2.set_index("feature"),
         fmt={"mdc95": "{:.4f}", "sd_mean": "{:.4f}", "sd_single": "{:.4f}", "ratio_A": "{:.2f}", "ratio_B": "{:.2f}",
              "icc": "{:.3f}", "cvw": "{:.1f}", "bias_sd": "{:+.2f}"})

rows = []
for f in FEATS:
    x = {r: col(r, f) for r in ROLES}
    mm = np.isfinite(x["femur"]) & np.isfinite(x["rf"]) & np.isfinite(x["sweep"])
    V = {}
    for a_, b_ in PAIRS:
        V[(a_, b_)] = np.var(x[a_][mm] - x[b_][mm], ddof=1)
    Vfs, Vrs, Vfr = V[("femur", "sweep")], V[("rf", "sweep")], V[("femur", "rf")]
    s2 = {"femur": (Vfr + Vfs - Vrs) / 2, "rf": (Vfr + Vrs - Vfs) / 2, "sweep": (Vfs + Vrs - Vfr) / 2}
    sd_between = np.std(np.r_[x["femur"][mm], x["rf"][mm], x["sweep"][mm]], ddof=1)
    rec = dict(feature=f, n=int(mm.sum()))
    for r in ROLES:
        rec[f"err_sd_{r}"] = np.sign(s2[r]) * np.sqrt(abs(s2[r]))
        rec[f"err_sd_{r}_over_sd_between"] = rec[f"err_sd_{r}"] / sd_between
    rows.append(rec)
Q2b = pd.DataFrame(rows).set_index("feature")
RES["Q2b_grubbs"] = Q2b.reset_index().to_dict("records")
log("\nQ2b Grubbs per-role error SD\n", Q2b.round(4).to_string())
md_table("Q2b. Per-role 'noise' SD by the three-instrument (Grubbs) decomposition: err_var(role) = (V_ij + V_ik - V_jk)/2 (a negative variance is reported as a negative SD; assumes independent errors, which rf/sweep at the shared 4.9 cm may violate)",
         Q2b, fmt={c: "{:.4f}" for c in Q2b.columns if c.startswith("err_sd_") and not c.endswith("between")} |
                  {c: "{:.2f}" for c in Q2b.columns if c.endswith("between")})

rows = []
for f in FEATS:
    g = Q2b.loc[f]
    rec = dict(feature=f)
    for r in ROLES:
        v = col(r, f); v = v[np.isfinite(v)]
        own_sd = v.std(ddof=1); e = g[f"err_sd_{r}"]
        rec[f"{r}_own_sd"] = own_sd; rec[f"{r}_err_over_own_sd"] = e / own_sd
        rec[f"{r}_reliability"] = 1 - (np.sign(e) * e * e) / (own_sd ** 2)
    rows.append(rec)
Q2c = pd.DataFrame(rows).set_index("feature")
RES["Q2c_reliability"] = Q2c.reset_index().to_dict("records")
log("\nQ2c per-role reliability (1 - err_var/own between-study var)\n", Q2c.round(3).to_string())
md_table("Q2c. Per-role reliability = 1 - err_var(role)/between-study var(role) (Grubbs error variance from Q2b; own SD = that role's between-study SD)",
         Q2c[[c for r in ROLES for c in (f"{r}_own_sd", f"{r}_err_over_own_sd", f"{r}_reliability")]],
         fmt={c: "{:.4f}" for c in Q2c.columns if c.endswith("_own_sd")} | {c: "{:.2f}" for c in Q2c.columns if c.endswith("_over_own_sd")} |
             {c: "{:.2f}" for c in Q2c.columns if c.endswith("_reliability")})

# =================================================================== Q3 ----
md.append("\n## Q3. Criterion validity against isometric torque\n")
crit = qm.copy()
new_mask = crit.cohort_round.astype(str).str.lower().str.contains("new")
step = {"qa_master rows": len(crit)}
crit = crit[new_mask]; step["cohort_round = new"] = len(crit)
crit = crit[crit.index.isin(UIDS)]; step["in the 116-study substrate"] = len(crit)
crit = crit[np.isfinite(crit.izo_torque_nm.astype(float))]; step["izo_torque_nm present"] = len(crit)
crit = crit[crit.izo_cv_pct.astype(float) <= 15]; step["izo_cv_pct <= 15"] = len(crit)
hw_ok = np.isfinite(crit.height_cm.astype(float)) & np.isfinite(crit.weight_kg.astype(float))
step["height+weight present"] = int(hw_ok.sum()); step["bmi present"] = int(np.isfinite(crit.bmi.astype(float)).sum())
step["age present"] = int(np.isfinite(crit.age.astype(float)).sum())
step["sex present"] = int(crit.sex.notna().sum())
step["full_thickness_q50_mm present"] = int(np.isfinite(crit.full_thickness_q50_mm.astype(float)).sum())
step["fat_to_depth_q50 present"] = int(np.isfinite(crit.fat_to_depth_q50.astype(float)).sum())
log("Q3 n-ledger:", step)
RES["Q3_n_ledger"] = step

sex_num = crit.sex.astype(str).str.strip().str.upper().map(lambda s: 1.0 if s in ("M", "MALE", "E", "ERKEK") else (0.0 if s in ("F", "FEMALE", "K", "KADIN") else np.nan))
log("sex coding used:", crit.sex.astype(str).str.strip().str.upper().value_counts().to_dict(), "-> male=1")
use_hw = hw_ok.mean() >= 0.9
body = ["height_cm", "weight_kg"] if use_hw else ["bmi"]
COVS = ["age", "sex_num"] + body + ["full_thickness_q50_mm", "fat_to_depth_q50"]
log(f"covariates: {COVS} (height/weight coverage {hw_ok.mean():.2%} -> {'height+weight' if use_hw else 'BMI'})")
Z = pd.DataFrame({"age": crit.age.astype(float), "sex_num": sex_num,
                  "height_cm": crit.height_cm.astype(float), "weight_kg": crit.weight_kg.astype(float),
                  "bmi": crit.bmi.astype(float),
                  "full_thickness_q50_mm": crit.full_thickness_q50_mm.astype(float),
                  "fat_to_depth_q50": crit.fat_to_depth_q50.astype(float)})[COVS]
y_all = crit.izo_torque_nm.astype(float)

def partial_pearson(x, y, Zm):
    A = np.column_stack([np.ones(len(x)), Zm])
    rx = x - A @ np.linalg.lstsq(A, x, rcond=None)[0]
    ry = y - A @ np.linalg.lstsq(A, y, rcond=None)[0]
    r = stats.pearsonr(rx, ry)[0]; k = Zm.shape[1]; n = len(x)
    t = r * np.sqrt((n - k - 2) / max(1e-12, 1 - r * r)); p = 2 * stats.t.sf(abs(t), n - k - 2)
    return r, p

def partial_rank(x, y, Zm):
    rx = stats.rankdata(x); ry = stats.rankdata(y); rz = np.column_stack([stats.rankdata(Zm[:, j]) for j in range(Zm.shape[1])])
    return partial_pearson(rx, ry, rz)

def delta_r2(x, y, Zm):
    A0 = sm.add_constant(Zm); A1 = sm.add_constant(np.column_stack([Zm, x]))
    m0 = sm.OLS(y, A0).fit(); m1 = sm.OLS(y, A1).fit()
    return m0.rsquared_adj, m1.rsquared_adj, m1.rsquared - m0.rsquared, m1.pvalues[-1], m1.params[-1]

def crit_row(label, x, common_idx=None):
    x = pd.Series(x, index=crit.index)
    df = pd.concat([x.rename("x"), y_all.rename("y"), Z], axis=1).dropna()
    if common_idx is not None: df = df.loc[df.index.intersection(common_idx)]
    xv, yv, Zm = df.x.to_numpy(float), df.y.to_numpy(float), df[COVS].to_numpy(float)
    rho, rho_p = stats.spearmanr(xv, yv)
    pr, pr_p = partial_pearson(xv, yv, Zm); rr, rr_p = partial_rank(xv, yv, Zm)
    r2a0, r2a1, dr2, coef_p, coef = delta_r2(xv, yv, Zm)
    return dict(source=label, n=len(df), rho_torque=rho, rho_p=rho_p, partial_r=pr, partial_p=pr_p,
                partial_rank_r=rr, r2adj_base=r2a0, r2adj_with=r2a1, delta_r2adj_pp=100 * (r2a1 - r2a0),
                delta_r2_raw_pp=100 * dr2, coef_p=coef_p, beta_nm_per_unit=coef), df.index

sources = {}
for f in ["int_cv_within", "int_mean"]:
    for r in ROLES:
        sources[f"{f} [{r} role]"] = W[r][f].reindex(crit.index).to_numpy(float)
    fem, rf_ = W["femur"][f].reindex(crit.index).to_numpy(float), W["rf"][f].reindex(crit.index).to_numpy(float)
    sources[f"{f} [mean of femur+rf]"] = np.where(np.isfinite(fem) & np.isfinite(rf_), (fem + rf_) / 2, np.nan)
sources["int_cv_within [served, qa_master]"] = crit.int_cv_within.astype(float).to_numpy()

rows_max, rows_common = [], []
for lab, x in sources.items():
    rec, idx = crit_row(lab, x); rows_max.append(rec)
idx_sets = []
for f in ["int_cv_within", "int_mean"]:
    for r in ROLES:
        _, idx = crit_row(f"{f} [{r} role]", sources[f"{f} [{r} role]"]); idx_sets.append(set(idx))
common = set.intersection(*idx_sets)
for lab, x in sources.items():
    rec, _ = crit_row(lab, x, common_idx=pd.Index(sorted(common))); rows_common.append(rec)
Q3max = pd.DataFrame(rows_max).set_index("source"); Q3com = pd.DataFrame(rows_common).set_index("source")
RES["Q3_max_n"] = Q3max.reset_index().to_dict("records"); RES["Q3_common_n"] = Q3com.reset_index().to_dict("records")
RES["Q3_common_n_size"] = len(common)
log("\nQ3 (max n per source)\n", Q3max.round(4).to_string())
log("\nQ3 (common n)\n", Q3com.round(4).to_string())
fmt3 = {"rho_torque": "{:+.3f}", "rho_p": "{:.2g}", "partial_r": "{:+.3f}", "partial_p": "{:.2g}", "partial_rank_r": "{:+.3f}",
        "r2adj_base": "{:.3f}", "r2adj_with": "{:.3f}", "delta_r2adj_pp": "{:+.2f}", "delta_r2_raw_pp": "{:+.2f}", "coef_p": "{:.2g}",
        "beta_nm_per_unit": "{:+.1f}"}
md.append(f"\nn-ledger: " + " -> ".join(f"{k} {v}" for k, v in step.items()) + f". Covariates: {', '.join(COVS)}.\n")
md_table("Q3a. Torque validity per source (each source at its own maximal n)",
         Q3max[["n", "rho_torque", "rho_p", "partial_r", "partial_p", "partial_rank_r", "r2adj_base", "r2adj_with", "delta_r2adj_pp", "coef_p"]], fmt=fmt3)
md_table(f"Q3b. Same, on the common set of studies where all three roles and all covariates are present (n={len(common)})",
         Q3com[["n", "rho_torque", "partial_r", "partial_p", "partial_rank_r", "r2adj_base", "r2adj_with", "delta_r2adj_pp", "coef_p"]], fmt=fmt3)

rng = np.random.default_rng(20260902)
ci = pd.Index(sorted(common))
def boot_delta(f, NB=2000):
    xf = pd.Series(W["femur"][f].reindex(crit.index).to_numpy(float), index=crit.index).loc[ci].to_numpy()
    xr = pd.Series(W["rf"][f].reindex(crit.index).to_numpy(float), index=crit.index).loc[ci].to_numpy()
    yv = y_all.loc[ci].to_numpy(float); Zm = Z.loc[ci].to_numpy(float); n = len(ci)
    out = []
    for _ in range(NB):
        ix = rng.integers(0, n, n)
        try:
            df_ = delta_r2(xf[ix], yv[ix], Zm[ix]); dr_ = delta_r2(xr[ix], yv[ix], Zm[ix])
            out.append(100 * ((df_[1] - df_[0]) - (dr_[1] - dr_[0])))
        except Exception:
            pass
    out = np.array(out)
    return dict(feature=f, n=n, delta_femur_minus_rf_pp=float(np.median(out)), ci_lo=float(np.percentile(out, 2.5)),
                ci_hi=float(np.percentile(out, 97.5)), p_boot=float(2 * min((out >= 0).mean(), (out <= 0).mean())))
Q3c = pd.DataFrame([boot_delta("int_cv_within"), boot_delta("int_mean")]).set_index("feature")
RES["Q3c_boot"] = Q3c.reset_index().to_dict("records")
log("\nQ3c paired bootstrap femur - rf delta R2adj (pp)\n", Q3c.round(3).to_string())
md_table("Q3c. Paired bootstrap (2000 resamples, common set) of the femur-minus-rf difference in adjusted-R2 gain, percentage points",
         Q3c, fmt={"delta_femur_minus_rf_pp": "{:+.2f}", "ci_lo": "{:+.2f}", "ci_hi": "{:+.2f}", "p_boot": "{:.2f}"})

def winsor(x):
    lo, hi = np.nanpercentile(x, [5, 95]); return np.clip(x, lo, hi)
rows = []
for r in ROLES:
    x = W[r]["int_cv_within"].reindex(crit.index).to_numpy(float)
    for lab, xt in (("winsorized 5/95", winsor(x)), ("log", np.log(x))):
        rec, _ = crit_row(f"int_cv_within [{r} role] {lab}", xt, common_idx=pd.Index(sorted(common))); rows.append(rec)
Q3d = pd.DataFrame(rows).set_index("source")
RES["Q3d_robust"] = Q3d.reset_index().to_dict("records")
log("\nQ3d robustness\n", Q3d.round(4).to_string())
md_table("Q3d. Robustness of the torque test to leverage points (common set)",
         Q3d[["n", "rho_torque", "partial_r", "partial_p", "partial_rank_r", "delta_r2adj_pp", "coef_p"]], fmt=fmt3)

# =================================================================== Q4 ----
md.append("\n## Q4. Brightness dependence and depth profile per role\n")
rows = []
for r in ROLES:
    cv, mu = col(r, "int_cv_within"), col(r, "int_mean")
    mm = np.isfinite(cv) & np.isfinite(mu)
    rows.append(dict(role=r, n=int(mm.sum()),
                     pearson_cv_vs_mean=stats.pearsonr(cv[mm], mu[mm])[0], spearman_cv_vs_mean=stats.spearmanr(cv[mm], mu[mm])[0],
                     pearson_cv_vs_inv_mean=stats.pearsonr(cv[mm], 1 / mu[mm])[0], spearman_cv_vs_inv_mean=stats.spearmanr(cv[mm], 1 / mu[mm])[0],
                     pearson_std_vs_mean=stats.pearsonr(col(r, "int_std")[mm], mu[mm])[0],
                     mean_int_mean=mu[mm].mean(), sd_int_mean=mu[mm].std(ddof=1), cv_of_int_mean_pct=100 * mu[mm].std(ddof=1) / mu[mm].mean(),
                     mean_int_cv=cv[mm].mean(), sd_int_cv=cv[mm].std(ddof=1)))
Q4 = pd.DataFrame(rows).set_index("role")
RES["Q4"] = Q4.reset_index().to_dict("records")
log("\nQ4 brightness dependence\n", Q4.round(4).to_string())
md_table("Q4a. Brightness dependence of int_cv_within per role (n = studies with both values)",
         Q4, fmt={c: "{:+.3f}" for c in Q4.columns if "vs" in c} | {"mean_int_mean": "{:.1f}", "sd_int_mean": "{:.1f}", "cv_of_int_mean_pct": "{:.1f}",
                                                                   "mean_int_cv": "{:.3f}", "sd_int_cv": "{:.3f}"})
rows = []
for r in ROLES:
    rec = dict(role=r)
    for b in ["superficial_int_mean", "mid_int_mean", "deep_int_mean", "int_superficial_to_deep_ratio", "int_depth_gradient"]:
        v = col(r, b); v = v[np.isfinite(v)]
        rec[f"{b}_mean"] = v.mean(); rec[f"{b}_sd"] = v.std(ddof=1); rec[f"{b}_n"] = len(v)
    rows.append(rec)
Q4b = pd.DataFrame(rows).set_index("role")
RES["Q4b"] = Q4b.reset_index().to_dict("records")
log("\nQ4b depth bands\n", Q4b.round(3).to_string())
Q4b_show = pd.DataFrame({
    "superficial (mean +/- SD)": [f"{Q4b.loc[r,'superficial_int_mean_mean']:.1f} +/- {Q4b.loc[r,'superficial_int_mean_sd']:.1f}" for r in ROLES],
    "mid": [f"{Q4b.loc[r,'mid_int_mean_mean']:.1f} +/- {Q4b.loc[r,'mid_int_mean_sd']:.1f}" for r in ROLES],
    "deep": [f"{Q4b.loc[r,'deep_int_mean_mean']:.1f} +/- {Q4b.loc[r,'deep_int_mean_sd']:.1f}" for r in ROLES],
    "sup/deep ratio": [f"{Q4b.loc[r,'int_superficial_to_deep_ratio_mean']:.2f} +/- {Q4b.loc[r,'int_superficial_to_deep_ratio_sd']:.2f}" for r in ROLES],
    "int_depth_gradient (u8 per full depth)": [f"{Q4b.loc[r,'int_depth_gradient_mean']:+.1f} +/- {Q4b.loc[r,'int_depth_gradient_sd']:.1f}" for r in ROLES],
    "n": [int(Q4b.loc[r, "int_depth_gradient_n"]) for r in ROLES]}, index=pd.Index(ROLES, name="role"))
md_table("Q4b. Depth-band means (8-bit grey, eroded RF mask split column-wise into thirds) and the OLS depth gradient per role", Q4b_show)
rows = []
for b in ["superficial_int_mean", "mid_int_mean", "deep_int_mean", "int_depth_gradient"]:
    a, c = col("femur", b), col("rf", b); mm = np.isfinite(a) & np.isfinite(c)
    d = a[mm] - c[mm]
    rows.append(dict(band=b, n=int(mm.sum()), femur_minus_rf=d.mean(), sd_diff=d.std(ddof=1), p=stats.ttest_rel(a[mm], c[mm]).pvalue,
                     rho=stats.spearmanr(a[mm], c[mm])[0]))
Q4c = pd.DataFrame(rows).set_index("band")
RES["Q4c"] = Q4c.reset_index().to_dict("records")
md_table("Q4c. Paired femur-minus-rf on the depth bands", Q4c, fmt={"femur_minus_rf": "{:+.2f}", "sd_diff": "{:.2f}", "p": "{:.2g}", "rho": "{:.3f}"})

# =================================================================== Q5 ----
md.append("\n## Q5. Segmentation / consistency proxies and resolution per role\n")
rows = []
for r in ROLES:
    d = W[r].reindex(UIDS)
    seg_frac = d.n_frames_segmented / d.n_frames_in_capture
    area = d.full_area_mm2.astype(float); apx = d.full_area_px.astype(float)
    ret = d.area_retained_fraction_after_erosion.astype(float)
    rec = dict(role=r, n=len(d),
               frames_in_capture_median=float(d.n_frames_in_capture.median()),
               seg_frac_median=float(seg_frac.median()), seg_frac_min=float(seg_frac.min()), seg_frac_lt1_n=int((seg_frac < 1).sum()),
               seg_frac_lt0p9_n=int((seg_frac < 0.9).sum()),
               area_retained_median=float(ret.median()), area_retained_iqr=float(ret.quantile(.75) - ret.quantile(.25)),
               area_mm2_median=float(area.median()), area_mm2_sd=float(area.std(ddof=1)), area_mm2_cv_pct=float(100 * area.std(ddof=1) / area.mean()),
               area_mm2_iqr_over_median=float((area.quantile(.75) - area.quantile(.25)) / area.median()),
               area_px_median=float(apx.median()),
               thickness_q50_median=float(d.full_thickness_q50_mm.median()), thickness_q50_cv_pct=float(100 * d.full_thickness_q50_mm.std(ddof=1) / d.full_thickness_q50_mm.mean()))
    for f in FEATS:
        rec[f"missing_{f}"] = int(d[f].isna().sum())
    rec["missing_any_of_7"] = int(d[FEATS].isna().any(axis=1).sum())
    rec["spacing_source"] = d.spacing_source.value_counts().to_dict()
    rows.append(rec)
Q5 = pd.DataFrame(rows).set_index("role")
RES["Q5"] = Q5.reset_index().to_dict("records")
log("\nQ5 consistency proxies\n", Q5.drop(columns=["spacing_source"]).round(4).to_string())
log("spacing_source per role:", Q5.spacing_source.to_dict())
md_table("Q5a. Segmentation proxies per role (n=116 studies each)",
         Q5[["frames_in_capture_median", "seg_frac_median", "seg_frac_min", "seg_frac_lt1_n", "seg_frac_lt0p9_n", "area_retained_median", "area_retained_iqr"]],
         fmt={"frames_in_capture_median": "{:.0f}", "seg_frac_median": "{:.3f}", "seg_frac_min": "{:.3f}", "area_retained_median": "{:.3f}", "area_retained_iqr": "{:.3f}"})
md_table("Q5b. Mask size and dispersion per role",
         Q5[["area_mm2_median", "area_mm2_sd", "area_mm2_cv_pct", "area_mm2_iqr_over_median", "area_px_median", "thickness_q50_median", "thickness_q50_cv_pct"]],
         fmt={"area_mm2_median": "{:.0f}", "area_mm2_sd": "{:.0f}", "area_mm2_cv_pct": "{:.1f}", "area_mm2_iqr_over_median": "{:.2f}", "area_px_median": "{:.0f}",
              "thickness_q50_median": "{:.2f}", "thickness_q50_cv_pct": "{:.1f}"})
md_table("Q5c. Missing values per role (of 116)", Q5[[f"missing_{f}" for f in FEATS] + ["missing_any_of_7"]])

rows = []
for a_, b_ in PAIRS:
    rr = ratios(col(a_, "full_area_mm2"), col(b_, "full_area_mm2")); rr2 = ratios(col(a_, "full_thickness_q50_mm"), col(b_, "full_thickness_q50_mm"))
    rows.append(dict(pairing=f"{a_} vs {b_}", n=rr["n"], area_icc=rr["icc"], area_cvw=rr["cvw"], area_bias_sd=rr["bias_sd"],
                     thick_icc=rr2["icc"], thick_cvw=rr2["cvw"], thick_bias_sd=rr2["bias_sd"]))
Q5d = pd.DataFrame(rows).set_index("pairing")
RES["Q5d"] = Q5d.reset_index().to_dict("records")
md_table("Q5d. Geometry agreement across views (context for 'the femur cine segments more consistently')", Q5d,
         fmt={"area_icc": "{:.3f}", "area_cvw": "{:.1f}", "area_bias_sd": "{:+.2f}", "thick_icc": "{:.3f}", "thick_cvw": "{:.1f}", "thick_bias_sd": "{:+.2f}"})

rows = []
per_study = qf.groupby(["study_uid", "role"]).agg(depth_cm=("depth_cm", "median"), psr=("pixel_spacing_row_mm", "median"), nfr=("n_frames_in_capture", "max")).reset_index()
for r in ROLES:
    fr = qf[qf.role == r]; ps = per_study[per_study.role == r]
    rows.append(dict(role=r, n_frames=len(fr), n_studies=ps.study_uid.nunique(),
                     depth_cm_median=float(ps.depth_cm.median()), depth_cm_min=float(ps.depth_cm.min()), depth_cm_max=float(ps.depth_cm.max()),
                     depth_values=ps.depth_cm.round(2).value_counts().to_dict(),
                     psr_median_mm=float(ps.psr.median()), psr_min=float(ps.psr.min()), psr_max=float(ps.psr.max()),
                     px_per_mm=float(1 / ps.psr.median())))
Q5e = pd.DataFrame(rows).set_index("role")
RES["Q5e"] = Q5e.reset_index().to_dict("records")
log("\nQ5e resolution\n", Q5e.to_string())
ratio_fr = Q5e.loc["femur", "psr_median_mm"] / Q5e.loc["rf", "psr_median_mm"]
RES["mm_per_px_ratio_femur_over_rf"] = float(ratio_fr)
RES["area_px_ratio_rf_over_femur"] = float(Q5.loc["rf", "area_px_median"] / Q5.loc["femur", "area_px_median"])
log(f"mm/px ratio femur/rf = {ratio_fr:.3f}; RF-mask pixel-count ratio rf/femur = {RES['area_px_ratio_rf_over_femur']:.2f}")
md_table("Q5e. Resolution per role from qa_frames.csv (per-study medians of the DICOM row spacing; depth as recorded)",
         Q5e[["n_frames", "n_studies", "depth_cm_median", "depth_cm_min", "depth_cm_max", "psr_median_mm", "psr_min", "psr_max", "px_per_mm"]],
         fmt={"depth_cm_median": "{:.2f}", "depth_cm_min": "{:.2f}", "depth_cm_max": "{:.2f}", "psr_median_mm": "{:.4f}", "psr_min": "{:.4f}", "psr_max": "{:.4f}", "px_per_mm": "{:.1f}"})
md.append(f"\nImplied mm-per-pixel ratio femur/rf = {ratio_fr:.2f}; RF-mask pixel count (full_area_px, median) rf/femur = {RES['area_px_ratio_rf_over_femur']:.2f}.\n")

sv = qm.reindex(UIDS)
served_missing = sv.int_cv_within.isna()
fem_present = W["femur"]["int_cv_within"].reindex(UIDS).notna()
RES["coverage"] = dict(n=len(UIDS), served_int_cv_missing=int(served_missing.sum()),
                       femur_value_present_where_served_missing=int((served_missing & fem_present).sum()),
                       int_cv_trust=sv.int_cv_trust.astype(str).value_counts().to_dict(),
                       texture_state=sv.texture_state.astype(str).value_counts().to_dict())
log("coverage:", RES["coverage"])

# =================================================================== Q6 ----
md.append("\n## Q6. Comparability with the frozen reference\n")
old = qm[qm.cohort_round.astype(str).str.lower() == "old"]["int_cv_within"].astype(float).dropna()
new_served = qm[qm.cohort_round.astype(str).str.lower() == "new"]["int_cv_within"].astype(float).dropna()
rows = []
ref = dict(n=len(old), mean=old.mean(), sd=old.std(ddof=1), p5=np.percentile(old, 5), p50=np.median(old), p95=np.percentile(old, 95))
for lab, v in (("old cohort (served, single-sweep protocol)", old.to_numpy()), ("new round served (all 175, rf-cine)", new_served.to_numpy()),
               ("116 substrate, rf role", col("rf", "int_cv_within")), ("116 substrate, femur role", col("femur", "int_cv_within")),
               ("116 substrate, sweep role", col("sweep", "int_cv_within"))):
    v = v[np.isfinite(v)]
    rows.append(dict(source=lab, n=len(v), mean=v.mean(), sd=v.std(ddof=1),
                     mean_z_vs_old=(v.mean() - ref["mean"]) / ref["sd"],
                     frac_below_old_p5=float((v < ref["p5"]).mean()), frac_above_old_p95=float((v > ref["p95"]).mean()),
                     median_percentile_in_old=float(stats.percentileofscore(old, np.median(v)))))
Q6a = pd.DataFrame(rows).set_index("source")
RES["Q6a_reference_placement"] = Q6a.reset_index().to_dict("records"); RES["old_ref"] = ref
log("\nQ6a placement vs old-cohort reference\n", Q6a.round(3).to_string())
md_table("Q6a. Where each source's int_cv_within sits against the frozen old-cohort reference (the distribution the served model was calibrated on)",
         Q6a, fmt={"mean": "{:.3f}", "sd": "{:.3f}", "mean_z_vs_old": "{:+.2f}", "frac_below_old_p5": "{:.2f}", "frac_above_old_p95": "{:.2f}", "median_percentile_in_old": "{:.0f}"})

# ---------------------------------------------------------------- write ----
with open(f"{OUT}/tables.md", "w") as fh:
    fh.write("\n".join(md) + "\n")
def _clean(o):
    if isinstance(o, dict): return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)): return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)): return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.integer,)): return int(o)
    return o
with open(f"{OUT}/results.json", "w") as fh:
    json.dump(_clean(RES), fh, indent=1)
with open(f"{OUT}/run.log", "w") as fh:
    fh.write("\n".join(log_lines) + "\n")
log("DONE")

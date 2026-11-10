"""Q5: the residual as a target — reliability, correlations with image/fat/demographic features,
nested-CV prediction from image-only features with a permutation null, circularity variants."""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling modules
import warnings; warnings.filterwarnings("ignore")
from common import *; from oof import *; from features import *
from scipy import stats
SEEDS = list(range(10)); out = {"substrate": substrate_identity(), "seeds": SEEDS}
pool = pd.read_csv(WORK / "oof_residuals_sensA.csv"); A = sens_a_frame(); assert (pool.study_uid.values == A.study_uid.values).all()
y = A.izo_torque_nm.values.astype(float); sex = A.sex.values; resid = pool.resid_mean10.values; s_i = pool.within_session_sd_nm.values
Rmat = pool[[f"resid_seed{s}" for s in SEEDS]].values.T
def rel(res_var, noise_var): return float((res_var - noise_var) / res_var)
sr2_subj = float(np.var(resid)); sr2_pool = float(np.var(Rmat.ravel()))
out["a_reliability"] = {"s_res_sd_subject_mean": float(np.sqrt(sr2_subj)), "s_res_sd_pooled": float(np.sqrt(sr2_pool)), "s_noise_rms": float(np.sqrt(np.mean(s_i**2))), "s_noise_mean": float(s_i.mean()),
                        "ratio_rms_noise": rel(sr2_pool, np.mean(s_i**2)), "ratio_mean_noise": rel(sr2_pool, s_i.mean()**2),
                        "by_sex": {s: {"n": int((sex == s).sum()), "s_res_sd": float(Rmat[:, sex == s].ravel().std()), "s_noise_rms": float(np.sqrt(np.mean(s_i[sex == s]**2))), "ratio": rel(float(np.var(Rmat[:, sex == s].ravel())), float(np.mean(s_i[sex == s]**2)))} for s in ("M", "F")},
                        "fold_split_stability_corr_seeds0-4_vs_5-9": float(np.corrcoef(Rmat[:5].mean(0), Rmat[5:].mean(0))[0, 1]),
                        "note": "ratio = share of residual variance NOT explained by within-session trial noise; between-day noise unmeasured (gate R3) so this is an UPPER bound on the residual's test-retest reliability"}
rf = capture_rf(); img_all, img_nofat = capture_sets(rf); cap = rf.reindex(A.study_uid); cap.index = A.index
feats = {"int_cv_within (qa_master, served)": A.int_cv_within, "int_cv_within (capture rf)": cap.int_cv_within, "int_mean (capture rf)": cap.int_mean, "int_mean fat-corrected young_lit (capture rf)": cap.int_mean_fat_corrected_young_lit,
         "fat_to_depth_q50": A.fat_to_depth_q50, "fat_thickness_q50_mm": A.fat_thickness_q50_mm, "fat_thickness_max_mm": A.fat_thickness_max_mm, "age": A.age, "sex_m": A.sex_m, "bmi": A.bmi, "height_cm": A.height_cm, "weight_kg": A.weight_kg,
         "full_thickness_q50_mm": A.full_thickness_q50_mm, "qmt_mm": A.qmt_mm, "condition_score": A.condition_score, "izo_cv_pct": A.izo_cv_pct, "isok_torque_ext_nm (criterion-side, NOT a feature)": A.isok_torque_ext_nm}
corr = {}
for k, v in feats.items():
    m = v.notna().values; r_res = stats.spearmanr(resid[m], v.values[m]); r_y = stats.spearmanr(y[m], v.values[m])
    corr[k] = {"n": int(m.sum()), "rho_residual": float(r_res.correlation), "p_residual": float(r_res.pvalue), "rho_torque_raw": float(r_y.correlation)}
out["b_spearman"] = corr
def partial_spearman(a, b, controls):
    m = np.isfinite(a) & np.isfinite(b) & np.all(np.isfinite(controls), axis=1)
    ra, rb, rc = stats.rankdata(a[m]), stats.rankdata(b[m]), np.column_stack([stats.rankdata(c) for c in controls[m].T]); Z = np.c_[np.ones(m.sum()), rc]
    ea = ra - Z @ np.linalg.lstsq(Z, ra, rcond=None)[0]; eb = rb - Z @ np.linalg.lstsq(Z, rb, rcond=None)[0]
    r = float(np.corrcoef(ea, eb)[0, 1]); n = int(m.sum()); k = rc.shape[1]; t = r * np.sqrt((n - 2 - k) / (1 - r**2))
    return {"rho": r, "p": float(2 * stats.t.sf(abs(t), n - 2 - k)), "n": n}
fatc = A[["fat_to_depth_q50", "fat_thickness_q50_mm"]].values.astype(float)
out["b_partial"] = {"int_cv_within(qa) | fat_to_depth_q50 + fat_thickness_q50": partial_spearman(resid, A.int_cv_within.values.astype(float), fatc),
                    "int_cv_within(qa) | fat + bmi + age": partial_spearman(resid, A.int_cv_within.values.astype(float), np.c_[fatc, A.bmi.values, A.age.values]),
                    "int_mean(cap) | fat_to_depth_q50 + fat_thickness_q50": partial_spearman(resid, cap.int_mean.values.astype(float), fatc),
                    "int_cv_within(qa) vs TORQUE | fat + R2 features": partial_spearman(y, A.int_cv_within.values.astype(float), np.c_[fatc, A[R2_COLS].values.astype(float)])}
ovm = cap.int_mean.notna().values; n_ov = int(ovm.sum()); Xc = cap.loc[ovm, img_all].values.astype(float); r_ov = resid[ovm]
rhos = np.array([stats.spearmanr(r_ov, Xc[:, j]).correlation for j in range(Xc.shape[1])]); order = np.argsort(-np.abs(rhos))
rng = np.random.default_rng(7); null_max = []
for _ in range(300):
    rp = rng.permutation(r_ov); null_max.append(np.max(np.abs([stats.spearmanr(rp, Xc[:, j]).correlation for j in range(Xc.shape[1])])))
out["b_capture_screen"] = {"n": n_ov, "p_features": int(Xc.shape[1]), "top5": [{"feature": img_all[j], "rho": float(rhos[j])} for j in order[:5]],
                           "null_max_abs_rho": {"q50": float(np.median(null_max)), "q95": float(np.percentile(null_max, 95)), "n_perm": 300}, "observed_max_abs_rho": float(np.abs(rhos).max())}
def nested(Xf, t, kind="ridge", seeds=SEEDS):
    P = oof_many(Xf, t, seeds, kind); ms = [metrics(t, P[i]) for i in range(len(seeds))]
    return {"r2": summarize([m["r2"] for m in ms]), "rmse": summarize([m["rmse"] for m in ms]), "per_seed_r2": [m["r2"] for m in ms]}
def perm_null(Xf, t, n_perm=60):
    r2s = []
    for k in range(n_perm):
        rng = np.random.default_rng(500 + k); tp = rng.permutation(t); r2s.append(metrics(tp, oof_pred(Xf, tp, k, "ridge"))["r2"])
    a = np.array(r2s); return {"n_perm": n_perm, "q2_5": float(np.percentile(a, 2.5)), "q50": float(np.median(a)), "q97_5": float(np.percentile(a, 97.5)), "max": float(a.max())}
c = {}
Ximg_all = cap.loc[ovm, img_all].values.astype(float); Ximg_nofat = cap.loc[ovm, img_nofat].values.astype(float); t2 = resid[ovm]
c["IMG_ALL -> rung2 residual (n=%d)" % n_ov] = {"p": Ximg_all.shape[1], **nested(Ximg_all, t2), "perm_null": perm_null(Ximg_all, t2), "target_sd": float(t2.std())}
c["IMG_ALL -> rung2 residual, HGB"] = {"p": Ximg_all.shape[1], **nested(Ximg_all, t2, "hgb")}
c["IMG_NOFAT -> rung2 residual (n=%d)" % n_ov] = {"p": Ximg_nofat.shape[1], **nested(Ximg_nofat, t2), "perm_null": perm_null(Ximg_nofat, t2)}
pm = A.int_cv_within.notna().values; t136 = resid[pm]
Xh = A.loc[pm, QA_HAND].values.astype(float); c["QA_HAND(fat3+int_cv) -> rung2 residual (n=%d)" % int(pm.sum())] = {"p": 4, **nested(Xh, t136), "perm_null": perm_null(Xh, t136), "target_sd": float(t136.std())}
c["int_cv_within alone -> rung2 residual (n=%d)" % int(pm.sum())] = {"p": 1, **nested(A.loc[pm, ["int_cv_within"]].values.astype(float), t136)}
Pf = A[pm].reset_index(drop=True); y136 = y[pm]
P3 = oof_many(Pf[R3_COLS].values.astype(float), y136, SEEDS, "ridge"); resid3 = (y136[None, :] - P3).mean(0); ov136 = cap.loc[pm, "int_mean"].notna().values
c["rung3 residual (fat in definition): IMG_NOFAT -> residual (n=%d)" % int(ov136.sum())] = {"p": Ximg_nofat.shape[1], **nested(cap.loc[pm].loc[ov136, img_nofat].values.astype(float), resid3[ov136]), "target_sd": float(resid3[ov136].std())}
c["rung3 residual: int_cv_within alone (n=136)"] = {"p": 1, **nested(Pf[["int_cv_within"]].values.astype(float), resid3)}
c["rung3 residual: IMG_ALL incl. fat (CIRCULAR variant, for the record)"] = {"p": Ximg_all.shape[1], **nested(cap.loc[pm].loc[ov136, img_all].values.astype(float), resid3[ov136])}
c["POSITIVE CONTROL: IMG_ALL -> raw torque (n=%d)" % n_ov] = {"p": Ximg_all.shape[1], **nested(Ximg_all, y[ovm])}
c["POSITIVE CONTROL: R2 -> raw torque (n=%d)" % n_ov] = {"p": 8, **nested(A.loc[ovm, R2_COLS].values.astype(float), y[ovm])}
c["POSITIVE CONTROL: R2 + IMG_ALL -> raw torque"] = {"p": 8 + Ximg_all.shape[1], **nested(np.c_[A.loc[ovm, R2_COLS].values.astype(float), Ximg_all], y[ovm])}
out["c_nested_prediction"] = c
out["d_circularity_note"] = ("The served residual is rung 2 (torque minus demographics+geometry); fat is NOT in its definition, so fat as a predictor of it is not circular — "
                             "'IMG_ALL' includes fat, 'IMG_NOFAT' excludes fat and every fat-corrected brightness column. For a rung-3 residual (fat removed in the definition) only the "
                             "texture-only predictor set is non-circular; IMG_ALL against rung 3 is reported only to show what a circular run looks like.")
json.dump(out, open(WORK / "q5_results.json", "w"), indent=1, default=float)
print(json.dumps({k: v for k, v in out.items() if k != "seeds"}, indent=1, default=float))

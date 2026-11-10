"""Q1 (band decomposition + criterion noise + k-session averaging) and Q4 (sex x age-band bands).
Also writes the 10-seed OOF residual pool (oof_residuals_sensA.csv) that q5 consumes, and verifies
the frozen specific_strength_table against a 25-seed recomputation."""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling modules
import warnings; warnings.filterwarnings("ignore")
from common import *; from oof import *
from scipy import stats

SEEDS = list(range(10))
A = sens_a_frame()
X = A[R2_COLS].values.astype(float); y = A.izo_torque_nm.values.astype(float)
sex = A.sex.values; uid = A.study_uid.values
out = {"substrate": substrate_identity(), "row_set": "SENS_A_no_texture", "n": int(len(A)), "n_by_sex": A.sex.value_counts().to_dict(), "seeds": SEEDS}

P = oof_many(X, y, SEEDS, "ridge"); R = y[None, :] - P
per_seed = [metrics(y, P[i]) for i in range(len(SEEDS))]
out["oof_10seed"] = {k: summarize([m[k] for m in per_seed]) for k in ("r2", "rmse", "mae")}
res_mean = R.mean(axis=0); pooled = R.ravel()
out["residual_sd"] = {"pooled_over_seeds": float(pooled.std()), "per_subject_mean_over_seeds": float(res_mean.std(ddof=0))}
out["band_by_sex"] = {s: band(R[:, sex == s].ravel()) for s in ("M", "F")}
out["band_global"] = band(pooled)
med = {s: float(np.median(y[sex == s])) for s in ("M", "F")}
out["median_torque_by_sex"] = med
out["relative_width_by_sex"] = {s: out["band_by_sex"][s]["width"] / med[s] for s in ("M", "F")}

# verification: 25-seed recompute vs the frozen specific_strength_table and the served band
P25 = oof_many(X, y, range(25), "ridge"); R25 = y[None, :] - P25; res25 = R25.mean(axis=0)
sst = pd.read_csv(RESULTS / "specific_strength_table.csv").set_index("study_uid").loc[uid]
diff = np.abs(sst.specific_strength_residual_nm.values - res25)
out["frozen_table_check"] = {"definition_tested": "per-subject mean over seeds 0-24 of the rung-2 ridge OOF residual",
                             "max_abs_diff_nm": float(diff.max()), "n": int(len(diff)),
                             "band_M_25seed": band(R25[:, sex == "M"].ravel()), "band_F_25seed": band(R25[:, sex == "F"].ravel()),
                             "served_band": json.load(open(COHORT / "force_model.json"))["residual_quantiles"]}
out["corr_10seed_vs_25seed_residual"] = float(np.corrcoef(res_mean, res25)[0, 1])

# criterion noise: within-session (trial) SD implied by izo_cv_pct x peak torque
s_i = (A.izo_cv_pct.values / 100.0) * y
out["criterion_noise_nm"] = {"mean": float(s_i.mean()), "median": float(np.median(s_i)), "rms": float(np.sqrt(np.mean(s_i**2))),
                             "q25": float(np.percentile(s_i, 25)), "q75": float(np.percentile(s_i, 75)), "max": float(s_i.max()),
                             "cv_pct": {"mean": float(A.izo_cv_pct.mean()), "median": float(A.izo_cv_pct.median()), "max": float(A.izo_cv_pct.max())},
                             "by_sex_rms": {s: float(np.sqrt(np.mean(s_i[sex == s]**2))) for s in ("M", "F")},
                             "by_sex_mean": {s: float(s_i[sex == s].mean()) for s in ("M", "F")}}
def decomp(res_sd, noise):
    return {"residual_sd": res_sd, "noise_sd": noise, "noise_var_fraction": noise**2 / res_sd**2,
            "model_removable_sd_in_principle": float(np.sqrt(max(res_sd**2 - noise**2, 0.0)))}
sd_pool = float(pooled.std())
out["decomposition"] = {"pooled_all_rms_noise": decomp(sd_pool, out["criterion_noise_nm"]["rms"]),
                        "pooled_all_mean_noise": decomp(sd_pool, out["criterion_noise_nm"]["mean"]),
                        "pooled_all_median_noise": decomp(sd_pool, out["criterion_noise_nm"]["median"]),
                        "by_sex_rms_noise": {s: decomp(float(R[:, sex == s].ravel().std()), out["criterion_noise_nm"]["by_sex_rms"][s]) for s in ("M", "F")},
                        "note": "noise = per-trial within-session SD (izo_cv_pct/100 x peak torque); the served criterion is the PEAK of the trials, so this is a proxy, not a measured test-retest SD; between-day noise is unmeasured (gate R3)"}
kk = {}
for label, noise in (("rms", out["criterion_noise_nm"]["rms"]), ("mean", out["criterion_noise_nm"]["mean"])):
    s_model2 = max(sd_pool**2 - noise**2, 0.0)
    kk[label] = {f"k={k}": {"residual_sd": float(np.sqrt(s_model2 + noise**2 / k)), "width_scale": float(np.sqrt(s_model2 + noise**2 / k) / sd_pool),
                            "band_M_width": out["band_by_sex"]["M"]["width"] * float(np.sqrt(s_model2 + noise**2 / k) / sd_pool),
                            "band_F_width": out["band_by_sex"]["F"]["width"] * float(np.sqrt(s_model2 + noise**2 / k) / sd_pool)} for k in (1, 2, 3)}
out["k_session_averaging"] = kk
var_y = float(y.var()); out["r2_ceiling_if_only_within_session_noise"] = {"rms": 1 - out["criterion_noise_nm"]["rms"]**2 / var_y, "mean": 1 - out["criterion_noise_nm"]["mean"]**2 / var_y}
cv = A.izo_cv_pct.values
sp = stats.spearmanr(np.abs(res_mean), cv); out["abs_residual_vs_cv_spearman"] = {"rho": float(sp.correlation), "p": float(sp.pvalue), "n": int(len(cv))}
sp = stats.spearmanr(np.abs(res_mean), P.mean(axis=0)); out["abs_residual_vs_pred_spearman"] = {"rho": float(sp.correlation), "p": float(sp.pvalue)}
for thr in (10.0, 5.0):
    m = cv <= thr; Xs, ys, ss = X[m], y[m], sex[m]
    Ps = oof_many(Xs, ys, SEEDS, "ridge"); Rs = ys[None, :] - Ps; ms = [metrics(ys, Ps[i]) for i in range(len(SEEDS))]
    out[f"cv_le_{int(thr)}_subset"] = {"n": int(m.sum()), "n_by_sex": {s: int((ss == s).sum()) for s in ("M", "F")},
                                       "r2": summarize([q["r2"] for q in ms]), "rmse": summarize([q["rmse"] for q in ms]),
                                       "band_by_sex": {s: band(Rs[:, ss == s].ravel()) for s in ("M", "F")}, "sd_torque": float(ys.std()),
                                       "median_torque_by_sex": {s: float(np.median(ys[ss == s])) for s in ("M", "F")}}

# Q4: sex x age-band bands from the pooled residuals (+ subject-level bootstrap of the edges)
age = A.age.values
def age_band(a): return "<35" if a < 35 else ("35-55" if a <= 55 else ">55")
ab = np.array([age_band(a) for a in age]); rng = np.random.default_rng(20260902); q4 = {}
for s in ("M", "F"):
    for b in ("<35", "35-55", ">55"):
        idx = np.where((sex == s) & (ab == b))[0]
        if len(idx) == 0: continue
        pool = R[:, idx].ravel(); bd = band(pool); ws, q10s, q90s = [], [], []
        for _ in range(400):
            pick = rng.choice(idx, size=len(idx), replace=True); pr = R[:, pick].ravel(); q10, q90 = np.percentile(pr, [10, 90]); ws.append(q90 - q10); q10s.append(q10); q90s.append(q90)
        q4[f"{s}|{b}"] = {"n_subjects": int(len(idx)), **bd, "width_boot_sd": float(np.std(ws)), "q10_boot_sd": float(np.std(q10s)), "q90_boot_sd": float(np.std(q90s)),
                          "median_torque": float(np.median(y[idx])), "relative_width": bd["width"] / float(np.median(y[idx])), "mean_residual": float(pool.mean()), "rmse": float(np.sqrt(np.mean(pool**2)))}
    groups = [res_mean[(sex == s) & (ab == b)] for b in ("<35", "35-55", ">55") if ((sex == s) & (ab == b)).sum() > 1]
    q4[f"{s}|levene_p"] = float(stats.levene(*groups).pvalue)
    idx = np.where(sex == s)[0]; ws = []
    for _ in range(400):
        pick = rng.choice(idx, size=len(idx), replace=True); pr = R[:, pick].ravel(); q10, q90 = np.percentile(pr, [10, 90]); ws.append(q90 - q10)
    q4[f"{s}|ALL"] = {"n_subjects": int(len(idx)), **band(R[:, idx].ravel()), "width_boot_sd": float(np.std(ws)), "median_torque": med[s], "relative_width": out["band_by_sex"][s]["width"] / med[s]}
out["q4_sex_age_bands"] = q4
def age_band2(a): return "<30" if a < 30 else ("30-50" if a <= 50 else ">50")
ab2 = np.array([age_band2(a) for a in age]); q4b = {}
for s in ("M", "F"):
    for b in ("<30", "30-50", ">50"):
        idx = np.where((sex == s) & (ab2 == b))[0]
        if len(idx): q4b[f"{s}|{b}"] = {"n_subjects": int(len(idx)), **band(R[:, idx].ravel())}
out["q4_sensitivity_bands_30_50"] = q4b

pool_df = pd.DataFrame({"study_uid": uid, "sex": sex, "age": age, "age_band": ab, "izo_torque_nm": y, "izo_cv_pct": cv,
                        "pred_mean10": P.mean(axis=0), "resid_mean10": res_mean, "resid_mean25": res25, "within_session_sd_nm": s_i})
for i, s in enumerate(SEEDS): pool_df[f"resid_seed{s}"] = R[i]
pool_df.to_csv(WORK / "oof_residuals_sensA.csv", index=False)
json.dump(out, open(WORK / "q1_q4_results.json", "w"), indent=1, default=float)
print(json.dumps({k: out[k] for k in ("substrate", "oof_10seed", "residual_sd", "band_by_sex", "criterion_noise_nm", "decomposition", "k_session_averaging", "frozen_table_check", "abs_residual_vs_cv_spearman", "abs_residual_vs_pred_spearman", "r2_ceiling_if_only_within_session_noise")}, indent=1, default=float))
print("\nCV subsets:", json.dumps({k: {kk2: v[kk2] for kk2 in ("n", "n_by_sex", "r2", "rmse", "band_by_sex", "median_torque_by_sex")} for k, v in out.items() if k.startswith("cv_le")}, indent=1, default=float))
print("\nQ4:", json.dumps(q4, indent=1, default=float)); print("\nQ4 sens:", json.dumps(q4b, indent=1, default=float))

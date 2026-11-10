"""Q3: feature ceiling (all tabular; + capture_features on the 92 overlap), ridge + HGB, 10 seeds;
within-sex model and log-torque model relative bands."""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling modules
import warnings; warnings.filterwarnings("ignore")
from common import *; from oof import *; from features import *
from scipy import stats
SEEDS = list(range(10)); out = {"substrate": substrate_identity(), "seeds": SEEDS}
A = sens_a_frame(); y_all = A.izo_torque_nm.values.astype(float); sex_all = A.sex.values
prim_mask = A.int_cv_within.notna().values; assert prim_mask.sum() == 136
HGB_SETS = {"R2", "ALL16", "R2+IMG_ALL", "ALL15+IMG_ALL"}

def run_block(frame, y, sets: dict, kinds=("ridge",), label=""):
    res = {}; preds = {}
    for name, cols in sets.items():
        X = frame[cols].values.astype(float); assert np.isfinite(X).all(), (name, "NaN in features")
        for kind in kinds:
            if kind == "hgb" and name not in HGB_SETS: continue
            P = oof_many(X, y, SEEDS, kind); ms = [metrics(y, P[i]) for i in range(len(SEEDS))]
            res[f"{name}.{kind}"] = {"n": int(len(y)), "p": len(cols), **{k: summarize([m[k] for m in ms]) for k in ("r2", "rmse")},
                                    "per_seed_r2": [m["r2"] for m in ms], "per_seed_rmse": [m["rmse"] for m in ms]}
            preds[f"{name}.{kind}"] = P
            print(label, name, kind, "R2 %.4f RMSE %.2f" % (res[f"{name}.{kind}"]["r2"]["mean"], res[f"{name}.{kind}"]["rmse"]["mean"]), flush=True)
    return res, preds
def deltas(res, base, others):
    d = {}
    for o in others:
        for kind in ("ridge", "hgb"):
            kb, ko = f"{base}.{kind}", f"{o}.{kind}"
            if kb in res and ko in res:
                dr = np.array(res[ko]["per_seed_r2"]) - np.array(res[kb]["per_seed_r2"]); dm = np.array(res[ko]["per_seed_rmse"]) - np.array(res[kb]["per_seed_rmse"])
                d[f"{base}->{o}.{kind}"] = {"d_r2": summarize(dr), "d_rmse": summarize(dm), "seeds_r2_positive": int((dr > 0).sum())}
    return d

Pf = A[prim_mask].reset_index(drop=True); yP = y_all[prim_mask]
resP, _ = run_block(Pf, yP, {"R2": R2_COLS, "R4_harness": R4_COLS, "ALL16": ALL16, "ALL18": ALL18}, kinds=("ridge", "hgb"), label="PRIMARY")
out["primary_136"] = {"results": resP, "deltas": deltas(resP, "R2", ["R4_harness", "ALL16", "ALL18"]), "features": {"ALL16": ALL16, "ALL18": ALL18}}
resS, predS = run_block(A, y_all, {"R2": R2_COLS, "ALL15": ALL15}, label="SENS_A")
out["sens_a_143"] = {"results": resS, "deltas": deltas(resS, "R2", ["ALL15"])}
rf = capture_rf(); img_all, img_nofat = capture_sets(rf)
ov = A[A.study_uid.isin(rf.index)].reset_index(drop=True); yo = ov.izo_torque_nm.values.astype(float)
O = pd.concat([ov.set_index("study_uid"), rf.loc[ov.study_uid].add_prefix("cap_")], axis=1).reset_index()
cap_all = ["cap_" + c for c in img_all]; cap_nofat = ["cap_" + c for c in img_nofat]
resO, _ = run_block(O, yo, {"R2": R2_COLS, "R2+IMG_ALL": R2_COLS + cap_all, "R2+IMG_NOFAT": R2_COLS + cap_nofat, "ALL15+IMG_ALL": ALL15 + cap_all,
                           "IMG_ALL_only": cap_all, "IMG_NOFAT_only": cap_nofat, "R2+cap_int_cv": R2_COLS + ["cap_int_cv_within"]}, kinds=("ridge", "hgb"), label="OVERLAP92")
out["overlap_capture"] = {"n": int(len(O)), "n_by_sex": ov.sex.value_counts().to_dict(), "p_img_all": len(cap_all), "p_img_nofat": len(cap_nofat),
                          "results": resO, "deltas": deltas(resO, "R2", ["R2+IMG_ALL", "R2+IMG_NOFAT", "ALL15+IMG_ALL", "R2+cap_int_cv"]), "img_all_cols": img_all, "img_nofat_cols": img_nofat}
# within-sex and log-torque variants: RELATIVE band = width / sex median torque
X = A[R2_COLS].values.astype(float); Ppool = predS["R2.ridge"]; Rpool = y_all[None, :] - Ppool
med = {s: float(np.median(y_all[sex_all == s])) for s in ("M", "F")}
def sexstats(R, P, s):
    m = sex_all == s; r = R[:, m].ravel(); b = band(r); yy = np.tile(y_all[m], R.shape[0]); pp = P[:, m].ravel(); ratio = band(r / pp)
    return {"n": int(m.sum()), **b, "relative_width_over_median": b["width"] / med[s], "rmse": float(np.sqrt(np.mean(r**2))),
            "r2_within_sex": 1 - float(np.sum(r**2)) / float(np.sum((yy - yy.mean())**2)), "ratio_band_q10": ratio["q10"], "ratio_band_q90": ratio["q90"], "ratio_band_width": ratio["width"]}
variants = {"pooled_nm": {s: sexstats(Rpool, Ppool, s) for s in ("M", "F")}}
Pw = np.zeros_like(Ppool); cols_ns = [c for c in R2_COLS if c != "sex_m"]
for s in ("M", "F"):
    m = sex_all == s; Pw[:, m] = oof_many(A.loc[m, cols_ns].values.astype(float), y_all[m], SEEDS, "ridge")
Rw = y_all[None, :] - Pw; variants["within_sex_nm"] = {s: sexstats(Rw, Pw, s) for s in ("M", "F")}
variants["within_sex_nm"]["pooled_r2"] = summarize([metrics(y_all, Pw[i])["r2"] for i in range(len(SEEDS))]); variants["within_sex_nm"]["pooled_rmse"] = summarize([metrics(y_all, Pw[i])["rmse"] for i in range(len(SEEDS))])
Pl = oof_many(X, np.log(y_all), SEEDS, "ridge"); Pl_nm = np.exp(Pl); Rl = y_all[None, :] - Pl_nm
variants["log_model_backtransformed_nm"] = {s: sexstats(Rl, Pl_nm, s) for s in ("M", "F")}
variants["log_model_backtransformed_nm"]["pooled_r2_nm"] = summarize([metrics(y_all, Pl_nm[i])["r2"] for i in range(len(SEEDS))]); variants["log_model_backtransformed_nm"]["pooled_rmse_nm"] = summarize([metrics(y_all, Pl_nm[i])["rmse"] for i in range(len(SEEDS))])
Rlog = np.log(y_all)[None, :] - Pl; ly = np.log(y_all)
variants["log_model_logspace"] = {s: {**band(Rlog[:, sex_all == s].ravel()), "multiplicative_band": [float(np.exp(np.percentile(Rlog[:, sex_all == s].ravel(), 10))), float(np.exp(np.percentile(Rlog[:, sex_all == s].ravel(), 90)))],
                                       "r2_log_within_sex": 1 - float(np.sum(Rlog[:, sex_all == s]**2)) / float(np.sum((np.tile(ly[sex_all == s], len(SEEDS)) - ly[sex_all == s].mean())**2))} for s in ("M", "F")}
variants["log_model_logspace"]["pooled_r2_log"] = summarize([metrics(ly, Pl[i])["r2"] for i in range(len(SEEDS))])
Plw = np.zeros_like(Ppool)
for s in ("M", "F"):
    m = sex_all == s; Plw[:, m] = oof_many(A.loc[m, cols_ns].values.astype(float), np.log(y_all[m]), SEEDS, "ridge")
Plw_nm = np.exp(Plw); Rlw = y_all[None, :] - Plw_nm; variants["log_within_sex_nm"] = {s: sexstats(Rlw, Plw_nm, s) for s in ("M", "F")}
out["relative_band_variants"] = {"median_torque": med, **variants}
rm = Rpool.mean(axis=0); pm = Ppool.mean(axis=0); qs = np.percentile(pm, [0, 33.3, 66.7, 100])
out["heteroscedasticity"] = {"spearman_absresid_vs_pred": float(stats.spearmanr(np.abs(rm), pm).correlation),
                             "resid_sd_by_pred_tertile": {f"tertile{i+1}": {"pred_range": [float(qs[i]), float(qs[i+1])], "resid_sd": float(rm[(pm >= qs[i]) & (pm <= qs[i+1])].std()), "n": int(((pm >= qs[i]) & (pm <= qs[i+1])).sum())} for i in range(3)}}
json.dump(out, open(WORK / "q3_results.json", "w"), indent=1, default=float)
print(json.dumps({k: v for k, v in out.items() if k != "seeds"}, indent=1, default=float)[:30000])

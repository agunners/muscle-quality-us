"""Receipt: the harness row sets on the live substrate, the served model's fit set, the seed-0
reproduction of the frozen ladder receipt, and the capture_features overlap."""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling modules
import warnings; warnings.filterwarnings("ignore")
from common import *; from oof import *; from features import capture_rf
fl, sets = harness_sets(); out = {"substrate": substrate_identity(), "row_sets": {k: len(v) for k, v in sets.items()}}
sa = {r["uid"] for r in sets["SENS_A_no_texture"]}; prim = {r["uid"] for r in sets["PRIMARY"]}
fm = json.load(open(COHORT / "force_model.json")); out["force_model_fit_uids_eq_SENS_A"] = set(fm["fit_uids"]) == sa
A = sens_a_frame(); X = A[R2_COLS].values.astype(float); y = A.izo_torque_nm.values.astype(float)
rec = pd.read_csv(RESULTS / "force_ladder_results.csv"); rec = rec[(rec.row_set == "SENS_A_no_texture") & (rec.rung == "R2") & (rec.seed == 0)]
out["seed0_reproduction"] = {k: {"ours": metrics(y, oof_pred(X, y, 0, k)), "receipt": rec[rec.model == k][["r2", "rmse", "mae"]].iloc[0].to_dict()} for k in ("ridge", "hgb")}
rf = capture_rf(); out["capture_rf"] = {"uids": int(len(rf)), "features_kept": int(rf.shape[1]), "overlap_SENS_A": int(len(set(rf.index) & sa)), "overlap_PRIMARY": int(len(set(rf.index) & prim))}
df = qa_frame(); out["force_coverage"] = {"old_rows_with_izo": int(df[df.cohort_round != "new"].izo_torque_nm.notna().sum()), "new_rows_with_izo": int(df[df.cohort_round == "new"].izo_torque_nm.notna().sum())}
out["izo_cv_pct_SENS_A"] = {"n_gt10": int((A.izo_cv_pct > 10).sum()), "n_gt15": int((A.izo_cv_pct > 15).sum()), "max": float(A.izo_cv_pct.max())}
json.dump(out, open(WORK / "verify_rowsets.json", "w"), indent=1, default=float); print(json.dumps(out, indent=1, default=float))

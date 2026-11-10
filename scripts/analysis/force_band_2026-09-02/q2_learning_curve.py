"""Q2: learning curve of the rung-2 ridge on SENS_A (stratified-by-sex subsamples, 10 reps each),
1/n fit of the OOF MSE, extrapolation to n=200/300/inf."""
import sys, pathlib; sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))  # sibling modules
import warnings; warnings.filterwarnings("ignore")
from common import *; from oof import *
A = sens_a_frame(); X = A[R2_COLS].values.astype(float); y = A.izo_torque_nm.values.astype(float); sex = A.sex.values
N = len(A); iM = np.where(sex == "M")[0]; iF = np.where(sex == "F")[0]
sizes = [40, 60, 80, 100, 120, N]; reps = 10; rows = []
for n in sizes:
    for r in range(reps):
        if n == N: idx = np.arange(N)
        else:
            rng = np.random.default_rng(1000 * n + r); nM = int(round(n * len(iM) / N))
            idx = np.sort(np.concatenate([rng.choice(iM, nM, replace=False), rng.choice(iF, n - nM, replace=False)]))
        p = oof_pred(X[idx], y[idx], r, "ridge"); m = metrics(y[idx], p)
        rows.append({"n": n, "rep": r, "n_M": int((sex[idx] == "M").sum()), **m, "mse": m["rmse"]**2, "sd_y": float(y[idx].std())})
        print(n, r, round(m["r2"], 4), round(m["rmse"], 2), flush=True)
df = pd.DataFrame(rows); df.to_csv(WORK / "q2_learning_curve_points.csv", index=False)
g = df.groupby("n").agg(r2_mean=("r2", "mean"), r2_sd=("r2", "std"), rmse_mean=("rmse", "mean"), rmse_sd=("rmse", "std"), mse_mean=("mse", "mean"), sd_y=("sd_y", "mean")).reset_index()
Xd = np.c_[np.ones(len(df)), 1.0 / df.n.values]; a, b = np.linalg.lstsq(Xd, df.mse.values, rcond=None)[0]
def pred(a, b, n): return float(np.sqrt(max(a + b / n, 0)))
extrap = {"fit_on": "all 60 points, MSE = a + b/n (linear-predictor theory: excess MSE ~ p*sigma^2/n)", "a": float(a), "b": float(b),
          "rmse_at": {str(n): pred(a, b, n) for n in (60, 100, 143, 200, 300, 500)}, "rmse_asymptote": float(np.sqrt(max(a, 0))),
          "estimation_error_share_of_mse_at_143": float((b / N) / (a + b / N))}
Xl = np.c_[np.ones(len(df)), np.log(df.n.values)]; c, d = np.linalg.lstsq(Xl, df.rmse.values, rcond=None)[0]
extrap["loglinear_rmse (no asymptote; optimistic)"] = {"c": float(c), "d": float(d), "rmse_at": {str(n): float(c + d * np.log(n)) for n in (143, 200, 300)}}
Xs = np.c_[np.ones(len(df)), df.n.values**-0.5]; a2, b2 = np.linalg.lstsq(Xs, df.mse.values, rcond=None)[0]
extrap["sqrt_law_mse (a + b/sqrt n)"] = {"a": float(a2), "b": float(b2), "rmse_at": {str(n): float(np.sqrt(max(a2 + b2 * n**-0.5, 0))) for n in (143, 200, 300)}, "rmse_asymptote": float(np.sqrt(max(a2, 0)))}
q1 = json.load(open(WORK / "q1_q4_results.json")); scale300 = extrap["rmse_at"]["300"] / extrap["rmse_at"]["143"]
extrap["band_width_scale_143_to_300"] = float(scale300); extrap["band_widths_at_300"] = {s: q1["band_by_sex"][s]["width"] * scale300 for s in ("M", "F")}
out = {"substrate": substrate_identity(), "row_set": "SENS_A_no_texture", "N": int(N), "sizes": sizes, "reps": reps, "table": g.round(4).to_dict(orient="records"), "extrapolation": extrap}
json.dump(out, open(WORK / "q2_results.json", "w"), indent=1, default=float)
print(g.round(4).to_string()); print(json.dumps(extrap, indent=1, default=float))

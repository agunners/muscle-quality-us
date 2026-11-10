"""Recalibrate QMT_CV_FLAG — the p05 "QMT read reliable/variable" badge threshold.

WHAT it does: for every cohort study with a rater QMT, re-measures the femur-QMT
frame-to-frame dispersion (CV) — the exact quantity the badge flags on — over its
top-K largest-RF landmark frames, paired with two reliability references:
  * |delta vs rater|  — did a wobbly measurement actually land wrong?
  * sweep roughness   — an INDEPENDENT within-study wobble measure (curvature).
Then prints the CV distribution, a threshold sweep, and a recommended cut, so the
flag is set from data, not guessed.

WHEN to run it: QMT_CV_FLAG (labeling_app/femur_cine_scorer.py) is PROVISIONAL — it
was calibrated on the OLD single-sweep cohort, but the badge's real target is the
NEW-protocol femur cines. Re-run this once ~two-dozen+ new-protocol femur-cine studies
have been scored, and reset the flag from the output. (memory: project_qmt_cv_flag_calibration)

HOW to read it: pick the cut that sits in the distribution's natural GAP and gives a
FLAG RATE you can review. CV measures consistency, not accuracy — a low-CV study can
still disagree with the rater (a stable offset), so the "catches wrong / J" columns are
a loose sanity check, not the decider.

Usage:
  .venv/bin/python scripts/calibration/qmt_cv_flag_calibration.py [--topk 10] [--delta-bad 10] [--limit N]
Needs the model bundle + a GPU. Writes data/cohort_stats/qmt_cv_calibration.csv.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from statistics import median, pstdev

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO)); sys.path.insert(0, str(_REPO / "scripts"))
import preflight_helpers.env_bootstrap  # noqa: E402

import numpy as np                                                   # noqa: E402
import pandas as pd                                                  # noqa: E402
from preflight_helpers.segmenter import (                            # noqa: E402
    BundleMeta, build_voi_hist_clahe_stack, load_learner_safe, predict_mask, synthesize_final_u8)
from preflight_helpers.run_pipeline import _spacing_row_from_dicom   # noqa: E402
from preflight_helpers.model_registry import default_bundle, list_bundles  # noqa: E402
from calibration import frame_loader                                 # noqa: E402
from labeling_app.measurements import compute_all_metrics            # noqa: E402
from preflight_helpers import env                                    # noqa: E402

_OUT = env.COHORT_STATS_DIR / "qmt_cv_calibration.csv"


def scan(topk: int, limit: int | None) -> pd.DataFrame:
    coh = pd.read_csv(env.COHORT_STATS_DIR / "per_study_cohort_v1.1.csv").set_index("study_code")
    man = pd.read_parquet(env.COHORT_STATS_DIR / "qa_sweep_pool_frame_manifest.parquet")
    studies = [s for s in coh.index if pd.notna(coh.loc[s].get("qmt_mm"))]
    if limit:
        studies = studies[:limit]
    bundle = default_bundle(list_bundles(compute_hash=False))
    learn = load_learner_safe(bundle.pkl_path); bm = BundleMeta.from_pkl(bundle.pkl_path)
    print(f"[scan] {len(studies)} rater-QMT studies, top-{topk} landmark frames", flush=True)
    rows = []
    for i, study in enumerate(studies):
        ann = float(coh.loc[study]["qmt_mm"])
        frames = man[(man.study_code == study) & (man.rf_px > 0)].nlargest(topk, "rf_px").frame_base.tolist()
        spacing = None; px = []; mm = []
        for fb in frames:
            try:
                jpg = frame_loader.resolve_sweep_jpg(study, fb)
                if jpg is None:
                    continue
                final = synthesize_final_u8(jpg.parent, fb)
                mask = predict_mask(learn, build_voi_hist_clahe_stack(final, modalities=bm.modalities), bm)
                if spacing is None:
                    spacing = dict(_spacing_row_from_dicom(jpg.parent.parent, mask.shape[:2]))
                m = compute_all_metrics(center_mask=mask, center_img_gray=final, all_masks=[mask], spacing_row=spacing)
                if m.get("qmt_provenance") == "standard_qmt" and m.get("qmt_px"):
                    px.append(float(m["qmt_px"])); mm.append(float(m["qmt_mm"]))
            except Exception:
                continue
        rec = {"study": study, "n_std": len(px), "cv": None, "roughness": None, "delta_pct": None}
        if len(px) >= 2:
            med = float(median(px)) or 1.0
            rec["cv"] = round(pstdev(px) / med, 4)
            rec["delta_pct"] = round(100 * (float(median(mm)) - ann) / ann, 1)
            if len(px) >= 3:
                a = np.asarray(px, float)
                rec["roughness"] = round(float(np.median(np.abs(a[:-2] - 2 * a[1:-1] + a[2:])) / med), 4)
        rows.append(rec)
        if (i + 1) % 20 == 0:
            print(f"  [{i+1}/{len(studies)}]", flush=True)
    df = pd.DataFrame(rows); df.to_csv(_OUT, index=False)
    print(f"[wrote] {_OUT}", flush=True)
    return df


def readout(df: pd.DataFrame, delta_bad: float) -> None:
    d = df[df.cv.notna() & df.delta_pct.notna()].copy()
    d["bad"] = d.delta_pct.abs() > delta_bad
    n, nbad = len(d), int(d.bad.sum())
    print(f"\n=== CV distribution (n={n}) ===")
    print(f"  median={d.cv.median():.3f}  p75={d.cv.quantile(.75):.3f}  p90={d.cv.quantile(.9):.3f}  max={d.cv.max():.3f}")
    edges = [0, .02, .03, .04, .05, .06, .08, .10, .15, .5]
    for i in range(len(edges) - 1):
        c = int(((d.cv >= edges[i]) & (d.cv < edges[i+1])).sum())
        print(f"    {edges[i]:.2f}-{edges[i+1]:.2f}: {'#'*c} {c}")
    print(f"\n=== threshold sweep (reliability ref = |model-rater| > {delta_bad:g}%, n_wrong={nbad}) ===")
    print(f"  {'cut':>5} {'flags':>10} {'catch_wrong':>12} {'spare_good':>11} {'J':>6}")
    best = None
    for t in (0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.12, 0.15):
        flag = d.cv >= t
        tp = int((flag & d.bad).sum()); fn = int((~flag & d.bad).sum())
        fp = int((flag & ~d.bad).sum()); tn = int((~flag & ~d.bad).sum())
        sens = tp/(tp+fn) if tp+fn else 0; spec = tn/(tn+fp) if tn+fp else 0; J = sens+spec-1
        if best is None or J > best[1]:
            best = (t, J)
        print(f"  {t:>5.2f} {f'{100*int(flag.sum())/n:.0f}% ({int(flag.sum())})':>10} "
              f"{f'{100*sens:.0f}%':>12} {f'{100*spec:.0f}%':>11} {J:>6.2f}")
    print(f"\n[recommend] Youden-best cut = {best[0]:g} (J={best[1]:.2f}) — but J is usually weak here because")
    print("  CV measures CONSISTENCY, not accuracy. Prefer the value in the distribution GAP whose flag rate")
    print("  you can review. Then set QMT_CV_FLAG in labeling_app/femur_cine_scorer.py.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Recalibrate QMT_CV_FLAG (see module docstring).")
    ap.add_argument("--topk", type=int, default=10, help="largest-RF landmark frames per study")
    ap.add_argument("--delta-bad", type=float, default=10.0, help="|model-rater|%% above which a QMT is 'wrong'")
    ap.add_argument("--limit", type=int, default=None, help="scan only the first N studies (smoke test)")
    ap.add_argument("--from-csv", action="store_true", help="skip the scan; re-read the last qmt_cv_calibration.csv")
    args = ap.parse_args()
    df = pd.read_csv(_OUT) if args.from_csv else scan(args.topk, args.limit)
    readout(df, args.delta_bad)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

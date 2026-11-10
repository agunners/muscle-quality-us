"""Bridge L1 — current code on the 11 same-person pair studies, both eras, plus the
within-session view-type test (sweep-QMT vs femur-cine-QMT) on the new side.

The 2026-08-31 comment docs rejected the "old QMT was frame-clipped" diagnosis
(31agu26_comments_theMeter_deeper №2/№3, ..._state_of_the_meter_second_edition №6/№12),
and the provenance audit agreed: all 8 served pair QMTs are Tier-1 femur-anchored
(per_study_cohort_v1.2.csv qmt_provenance='standard'), so field-of-view clipping
explains none of the within-person +9.7 mm. This script measures the remaining arms
instead of arguing them:

  arm 1 (code):        v1.2 frozen values  vs  CURRENT segmenter+measurement on the
                       same old studies' RF-landmark frames (top-rf_px candidates from
                       the 5fps manifest — the v1.2 selection convention).
  arm 2 (selection):   the same current-code QMT aggregated over femur-seeking frames
                       (top femur_px) instead of RF-landmark frames — bounds what frame
                       choice alone does within an old study.
  arm 3 (view type):   NEW-round pair studies: QMT measured on the SWEEP capture's
                       RF-landmark frames (the closest analog of how the old cohort was
                       measured) vs the served femur-cine QMT of the SAME session.
                       Within-session, same person, same device, same code — isolates
                       "which view the number comes from".

Also saves, per study, the top RF-landmark frame and the top femur frame (final_u8 +
mask overlay) under scratchpad/bridge_viz/<code>/ for the side-by-side pair visual
(SV-6/SV-12: "bring me the callsigns, make me a proper visual").

PHI: reads patient-bearing names only to join; prints and writes study codes only.

Run (GPU, ~5-10 min):
  source scripts/env.sh && .venv/bin/python scripts/analysis/bridge_l1_pairs.py [--old] [--new] [--limit N]
With no flags, runs both phases.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))
import preflight_helpers.env_bootstrap  # noqa: F401,E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from preflight_helpers.segmenter import (  # noqa: E402
    BundleMeta, build_voi_hist_clahe_stack, load_learner_safe, predict_mask, synthesize_final_u8,
)
from preflight_helpers.feature_compute import compute_image_quality_features  # noqa: E402
from preflight_helpers.landmark import measure_frame_geometry  # noqa: E402
from preflight_helpers.run_pipeline import _spacing_row_from_dicom  # noqa: E402
from preflight_helpers.model_registry import default_bundle, list_bundles  # noqa: E402
from calibration import frame_loader  # noqa: E402

RESULTS = HERE / "results"
VIZ = Path("/tmp/claude-1000/-home-akadir-dev-muscle-mania/a4613285-2ac3-495d-80ce-1b58d25953a8/scratchpad/bridge_viz")
MANIFEST = REPO / "data/cohort_stats/qa_sweep_pool_frame_manifest.parquet"
SCORES = REPO / "data/cohort_stats/qa_per_frame_detector_scores__sweep_pool_5fps.parquet"
V12 = REPO / "data/cohort_stats/per_study_cohort_v1.2.csv"
QA = REPO / "data/qa_master.csv"
REGISTRY = REPO / "data/study_registry.csv"
TOP_N_CAND = 8          # candidates segmented per selection set
LANDMARK_K = 5          # geometry landmark median
QMT_K = 7               # v1.2 QMT landmark convention (top-7)

# The 11 pairs, derived 2026-08-31 by same_person_pairs.py's join, pinned here by CODE
# so this receipt never re-touches names. new AKA display code <-> old legacy code.
PAIRS = [
    ("AKA285B20", "KAK428"), ("AKA080", "VQL999"), ("AKA78", "HKR015"),
    ("AKA004", "HZB881"), ("AKA106", "QHO558"), ("AKA116", "BHW541"),
    ("AKA017", "TDO396"), ("AKA018", "ERH017"), ("AKA124", "ZQZ188"),
    ("AKA030", "GIW402"), ("AKA033", "DQB975"),
]


def _mask_overlay_png(final_u8: np.ndarray, mask: np.ndarray, path: Path) -> None:
    """final_u8 with class boundaries burned in (thin contours), saved as PNG."""
    import cv2
    img = cv2.cvtColor(final_u8, cv2.COLOR_GRAY2BGR)
    colors = {1: (80, 200, 255), 2: (0, 165, 255), 3: (255, 120, 120), 4: (200, 255, 120),
              5: (255, 200, 0), 6: (180, 100, 255), 7: (0, 255, 0), 8: (120, 120, 255)}
    for cls, col in colors.items():
        m = (mask == cls).astype(np.uint8)
        if not m.any():
            continue
        cont, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(img, cont, -1, col, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(path), img)


def _measure(learn, bm, final_u8, spacing_row):
    mask = predict_mask(learn, build_voi_hist_clahe_stack(final_u8, modalities=bm.modalities), bm)
    ff = measure_frame_geometry(mask, final_u8, spacing_row) if spacing_row else {}
    return mask, ff


def _tier1_qmt(frames: list[dict], k: int) -> tuple[float | None, int]:
    """Median standard-provenance qmt over the first k frames that have one."""
    vals = [f["qmt_mm"] for f in frames[:k]
            if f.get("qmt_provenance") == "standard_qmt" and f.get("qmt_mm")]
    return (float(median(vals)) if vals else None), len(vals)


def _agg_geometry(frames: list[dict], k: int) -> dict:
    ok = [f for f in frames if (f.get("visible_rf_area_mm2") or 0) > 0
          and not f.get("edge_clip")]
    ok.sort(key=lambda f: f["visible_rf_area_mm2"], reverse=True)
    top = ok[:k]
    def _med(key):
        v = [f.get(key) for f in top if f.get(key) is not None
             and not (isinstance(f.get(key), float) and np.isnan(f.get(key)))]
        return float(median(v)) if v else None
    return {"full_thickness_q50_mm": _med("full_thickness_q50_mm"),
            "rf_thickness_mm": _med("rf_thickness_mm"),
            "fat_thickness_q50_mm": _med("fat_thickness_q50_mm"),
            "int_cv_within": _med("int_cv_within"),
            "n_landmark": len(top)}


def run_old(learn, bm, limit=None):
    man = pd.read_parquet(MANIFEST)
    sc = pd.read_parquet(SCORES)
    fired = (sc[sc.detector.isin({"wrong_site"}) & sc.fires]
             .groupby(["study_code", "frame_base"]).size())
    fired_set = set(fired.index)
    v12 = {r["study_code"]: r for r in csv.DictReader(open(V12))}

    out = []
    todo = PAIRS[:limit] if limit else PAIRS
    for new_code, old_code in todo:
        t0 = time.time()
        m = man[(man.study_code == old_code) & (~man.get("degenerate", False))]
        if m.empty:
            out.append({"old_code": old_code, "note": "not in 5fps manifest"})
            continue
        cand_rf = m[m.rf_px > 0]
        cand_rf = cand_rf[~cand_rf.frame_base.map(lambda fb: (old_code, fb) in fired_set)]
        cand_rf = cand_rf.nlargest(TOP_N_CAND, "rf_px")
        cand_fem = m[m.femur_px > 0].nlargest(TOP_N_CAND, "femur_px")
        bases = list(dict.fromkeys(list(cand_rf.frame_base) + list(cand_fem.frame_base)))

        spacing, meas, errs = None, {}, []
        best_rf, best_fem = None, None      # (area/femur_px, fb, mask, final)
        for fb in bases:
            try:
                jpg = frame_loader.resolve_sweep_jpg(old_code, fb)
                if jpg is None:
                    errs.append(f"{fb}: unresolved")
                    continue
                final = synthesize_final_u8(jpg.parent, fb)
                if spacing is None:
                    spacing = _spacing_row_from_dicom(jpg.parent.parent, final.shape[:2])
                mask, ff = _measure(learn, bm, final, spacing)
                iqf = compute_image_quality_features(frame_base=fb, multiclass_mask=mask,
                                                     final_image_u8=final, spacing_row=spacing)
                flags = (set(iqf.to_dict().get("image_quality_validity_flags") or ())
                         | set(iqf.to_dict().get("image_quality_pending_calibration_flags") or ()))
                ff["edge_clip"] = "edge_clip_vertical" in flags
                ff["frame_base"] = fb
                ff["femur_px_may"] = int(m[m.frame_base == fb].femur_px.iloc[0])
                ff["rf_px_may"] = int(m[m.frame_base == fb].rf_px.iloc[0])
                meas[fb] = ff
                area = ff.get("visible_rf_area_mm2") or 0
                if area > 0 and (best_rf is None or area > best_rf[0]):
                    best_rf = (area, fb, mask.copy(), final.copy())
                fpx = ff["femur_px_may"]
                if fpx > 0 and (best_fem is None or fpx > best_fem[0]):
                    best_fem = (fpx, fb, mask.copy(), final.copy())
            except Exception as e:
                errs.append(f"{fb}: {type(e).__name__}: {e}"[:100])

        rf_frames = [meas[fb] for fb in cand_rf.frame_base if fb in meas]
        fem_frames = [meas[fb] for fb in cand_fem.frame_base if fb in meas]
        fem_frames.sort(key=lambda f: f.get("femur_px_may", 0), reverse=True)
        geom = _agg_geometry(rf_frames, LANDMARK_K)
        qmt_rf, n_rf = _tier1_qmt(rf_frames, QMT_K)
        qmt_fem, n_fem = _tier1_qmt(fem_frames, QMT_K)

        v = v12.get(old_code, {})
        rec = {"old_code": old_code, "new_code": new_code,
               "seconds": round(time.time() - t0, 1),
               "n_frames_measured": len(meas), "errors": errs[:4],
               "spacing_source": (spacing or {}).get("source") if isinstance(spacing, dict) else str(bool(spacing)),
               "current": {**geom, "qmt_mm_rf_landmark": qmt_rf, "n_qmt_std_rf": n_rf,
                            "qmt_mm_femur_seek": qmt_fem, "n_qmt_std_fem": n_fem},
               "v12": {k: (float(v[k]) if (v.get(k) or "").strip() not in ("", "nan") else None)
                        for k in ("full_thickness_q50_mm", "rf_thickness_mm", "qmt_mm",
                                  "fat_thickness_q50_mm", "int_cv_within") if k in v},
               "v12_qmt_provenance": v.get("qmt_provenance") or None}
        out.append(rec)
        for tag, best in (("rf_landmark", best_rf), ("femur_best", best_fem)):
            if best:
                _mask_overlay_png(best[3], best[2], VIZ / old_code / f"{tag}.png")
                np.save(VIZ / old_code / f"{tag}_mask.npy", best[2])
        print(f"[old] {old_code}: {len(meas)} frames, qmt_rf={qmt_rf}, qmt_fem={qmt_fem}, "
              f"full={geom['full_thickness_q50_mm']} ({rec['seconds']}s)", flush=True)
    return out


def run_new(learn, bm, limit=None):
    import compute_features as cfm
    reg = {r["display_code"]: r["study_uid"] for r in csv.DictReader(open(REGISTRY))
           if r.get("display_code")}
    qa = {r["study_uid"]: r for r in csv.DictReader(open(QA))}
    idx = cfm._frame_index()

    out = []
    todo = PAIRS[:limit] if limit else PAIRS
    for new_code, old_code in todo:
        uid = reg.get(new_code)
        if not uid:
            out.append({"new_code": new_code, "note": "no registry uid"})
            continue
        rec = {"new_code": new_code, "old_code": old_code, "uid6": uid[:6]}
        served = qa.get(uid, {})
        rec["served"] = {k: (float(served[k]) if (served.get(k) or "").strip() not in ("", "nan") else None)
                         for k in ("qmt_mm", "full_thickness_q50_mm", "rf_thickness_mm",
                                   "fat_thickness_q50_mm", "int_cv_within") if k in served}
        for role in ("sweep", "femur"):
            t0 = time.time()
            entry = idx.get((uid, role))
            if entry is None:
                rec[role] = {"note": "capture not in qa_frames"}
                continue
            raw_dir, frame_bases = entry
            spacing, frames = None, []
            best = None
            for fb in frame_bases:
                try:
                    final = synthesize_final_u8(raw_dir, fb)
                    mask, _ = None, None
                    mask = predict_mask(learn, build_voi_hist_clahe_stack(final, modalities=bm.modalities), bm)
                    if spacing is None:
                        spacing = _spacing_row_from_dicom(raw_dir.parent, mask.shape[:2])
                    ff = measure_frame_geometry(mask, final, spacing)
                    ff["frame_base"] = fb
                    ff["edge_clip"] = False
                    frames.append(ff)
                    key = (ff.get("visible_rf_area_mm2") or 0) if role == "sweep" else int((mask == 7).sum())
                    if key and (best is None or key > best[0]):
                        best = (key, fb, mask.copy(), final.copy())
                except Exception:
                    continue
            frames_by_area = sorted([f for f in frames if f.get("visible_rf_area_mm2")],
                                    key=lambda f: f["visible_rf_area_mm2"], reverse=True)
            qmt_lm, n_std = _tier1_qmt(frames_by_area, QMT_K)
            qmt_all, n_all = _tier1_qmt(sorted(frames, key=lambda f: f.get("qmt_mm") or 0, reverse=True), 10**6)
            rec[role] = {"n_frames": len(frames), "qmt_mm_rf_landmark": qmt_lm,
                         "n_qmt_std_landmark": n_std,
                         "qmt_mm_median_all_std": qmt_all, "n_std_all": n_all,
                         "geometry": _agg_geometry(frames, LANDMARK_K),
                         "seconds": round(time.time() - t0, 1)}
            if best:
                _mask_overlay_png(best[3], best[2], VIZ / new_code / f"{role}_best.png")
                np.save(VIZ / new_code / f"{role}_best_mask.npy", best[2])
        out.append(rec)
        print(f"[new] {new_code}: sweep qmt={rec.get('sweep', {}).get('qmt_mm_rf_landmark')} "
              f"femur qmt={rec.get('femur', {}).get('qmt_mm_rf_landmark')} "
              f"served qmt={rec['served'].get('qmt_mm')}", flush=True)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", action="store_true")
    ap.add_argument("--new", action="store_true")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    do_old = args.old or not (args.old or args.new)
    do_new = args.new or not (args.old or args.new)

    bundles = list_bundles(compute_hash=False)
    bundle = default_bundle(bundles)
    print(f"[bundle] {Path(bundle.pkl_path).stem[:70]}", flush=True)
    learn = load_learner_safe(bundle.pkl_path)
    bm = BundleMeta.from_pkl(bundle.pkl_path)

    res = {"bundle": Path(bundle.pkl_path).stem, "date": "2026-08-31",
           "pairs": PAIRS}
    if do_old:
        res["old"] = run_old(learn, bm, args.limit)
    if do_new:
        res["new"] = run_new(learn, bm, args.limit)

    RESULTS.mkdir(exist_ok=True)
    out_path = RESULTS / "bridge_l1_pairs.json"
    json.dump(res, open(out_path, "w"), indent=1, default=str)
    print(f"WROTE {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

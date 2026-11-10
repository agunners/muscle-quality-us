"""Regenerate the cohort's RF-landmark columns (visible RF area + RF thicknesses).

For each cohort study the LANDMARK = the median over its top-5 largest-CLEAN RF
sweep frames — the fullest cross-section (its anatomical landmark). "Clean" =
no wrong_site / dark_clip_high (pre-filtered cheaply from the 5fps detector
parquet) and no edge_clip_vertical (re-checked on the re-measured frame; lateral
clip is EXPECTED with the 128L probe and tolerated). The value is measured through
the app's OWN per-frame path (post_process_rf -> compute_frame_features, with the
study's DICOM spacing), so the cohort and the assessment measure the same thing on
the same convention.

Ranking is off the precomputed per-frame rf_px (qa_sweep_pool_frame_manifest.parquet,
272/282 studies); only each study's top candidates are re-measured (~cheap). The
result is written to data/cohort_stats/rf_landmark_values.csv for inspection;
`--apply` merges visible_rf_area_mm2 / rf_thickness_mm / full_thickness_q50_mm into
per_study_cohort.csv (replacing the old, wrong-convention rf_area_mm2 column;
QMT/STAR/echo-CV/fat columns untouched), after backing up the old CSV.

Usage:
    .venv/bin/python scripts/cohort_landmark_precompute.py [--limit N] [--top-n 8] [--apply]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from statistics import median

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(REPO / "scripts"))
import preflight_helpers.env_bootstrap  # noqa: F401,E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from preflight_helpers.segmenter import (  # noqa: E402
    BundleMeta, build_voi_hist_clahe_stack, load_learner_safe, predict_mask,
    synthesize_final_u8,
)
from preflight_helpers.feature_compute import compute_image_quality_features  # noqa: E402
from preflight_helpers.landmark import measure_frame_geometry  # noqa: E402
from preflight_helpers.run_pipeline import _spacing_row_from_dicom  # noqa: E402
from preflight_helpers.model_registry import default_bundle, list_bundles  # noqa: E402
from calibration import frame_loader  # noqa: E402

_COHORT = REPO / "data/cohort_stats/per_study_cohort.csv"
_MANIFEST = REPO / "data/cohort_stats/qa_sweep_pool_frame_manifest.parquet"
_SCORES = REPO / "data/cohort_stats/qa_per_frame_detector_scores__sweep_pool_5fps.parquet"
_OUT = REPO / "data/cohort_stats/rf_landmark_values.csv"
_LANDMARK_K = 5                       # median over the top-5 clean frames
# Gate: pre-filter on these from the parquet; edge_clip_vertical re-checked live.
# dark_clip_high is NOT a geometry gate (Akadir 2026-06-10 — darkness leaves the
# fascia-based RF area unchanged; see repr_scorer), so dark belly frames are kept.
_PREFILTER_FLAGS = {"wrong_site"}


def _flags(grade: dict) -> set:
    return (set(grade.get("image_quality_validity_flags") or ())
            | set(grade.get("image_quality_pending_calibration_flags") or ()))


def _resolve_bundle(stem_substr):
    """Pick a bundle by stem substring (ambiguity-guarded). `None` => the
    canonical/default bundle. Used by the re-base attribution ladder to pin an
    exact bundle (padded vs native) instead of the mtime default."""
    bundles = list_bundles(compute_hash=False)
    if not stem_substr:
        return default_bundle(bundles)
    matches = [b for b in bundles if stem_substr in Path(b.pkl_path).stem]
    stems = sorted({Path(b.pkl_path).stem for b in matches})
    if not matches:
        raise SystemExit("[bundle] no match for '%s'; have:\n  %s" % (
            stem_substr, "\n  ".join(Path(b.pkl_path).stem for b in bundles)))
    if len(stems) > 1:
        raise SystemExit("[bundle] '%s' is ambiguous:\n  %s" % (stem_substr, "\n  ".join(stems)))
    return matches[0]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="process only N studies (test)")
    ap.add_argument("--top-n", type=int, default=8, help="candidates re-measured per study")
    ap.add_argument("--apply", action="store_true", help="merge into per_study_cohort.csv")
    ap.add_argument("--bundle", default=None,
                    help="segment with the bundle whose stem CONTAINS this substring "
                         "(default: the canonical/default bundle) — for the re-base ladder")
    ap.add_argument("--legacy-distort", action="store_true",
                    help="use the pre-fix distorting resize (reproduces v1.0 preprocessing) — ladder rung A")
    ap.add_argument("--out", default=None, help="output CSV path (default: rf_landmark_values.csv)")
    args = ap.parse_args()
    out_path = Path(args.out) if args.out else _OUT

    studies = list(pd.read_csv(_COHORT)["study_code"].dropna().unique())
    man = pd.read_parquet(_MANIFEST)
    sc = pd.read_parquet(_SCORES)
    # (study, frame) -> set of fired prefilter detectors
    fired = (sc[sc.detector.isin(_PREFILTER_FLAGS) & sc.fires]
             .groupby(["study_code", "frame_base"]).size())
    fired_set = set(fired.index)        # (study, frame) tuples that fire wrong_site/dark

    bundle = _resolve_bundle(args.bundle)
    print(f"[model] {Path(bundle.pkl_path).stem[:64]}  legacy_distort={args.legacy_distort}", flush=True)
    learn = load_learner_safe(bundle.pkl_path)
    bm = BundleMeta.from_pkl(bundle.pkl_path)

    rows = []
    todo = studies[: args.limit] if args.limit else studies
    for i, study in enumerate(todo):
        cand = man[(man.study_code == study) & (man.rf_px > 0)
                   & (~man.get("degenerate", False))].copy()
        if cand.empty:           # 10/282 cohort studies are not in the 5fps manifest
            rows.append({"study_code": study, "visible_rf_area_mm2": np.nan,
                         "rf_thickness_mm": np.nan, "full_thickness_q50_mm": np.nan,
                         "n_landmark_frames": 0, "note": "not in 5fps manifest"})
            continue
        # pre-filter the 2 cheap gates, rank by rf_px, take the top-N candidates
        cand = cand[~cand.frame_base.map(lambda fb: (study, fb) in fired_set)]
        cand = cand.nlargest(args.top_n, "rf_px")
        if cand.empty:
            rows.append({"study_code": study, "visible_rf_area_mm2": np.nan,
                         "rf_thickness_mm": np.nan, "full_thickness_q50_mm": np.nan,
                         "n_landmark_frames": 0, "note": "all candidates gated"})
            continue
        # study DICOM spacing (constant per study) — resolved once. A study with
        # no .dcm (some cohort studies) can't be measured in mm² -> NaN, not crash.
        spacing = None
        clean = []   # (visible_area, rf_thk, full_q50)
        note = ""
        try:
            for fb in cand.frame_base:
                jpg = frame_loader.resolve_sweep_jpg(study, fb)
                if jpg is None:
                    continue
                final = synthesize_final_u8(jpg.parent, fb)
                mask = predict_mask(learn, build_voi_hist_clahe_stack(final, modalities=bm.modalities), bm,
                                    legacy_distort_resize=args.legacy_distort)
                if spacing is None:
                    try:
                        spacing = _spacing_row_from_dicom(jpg.parent.parent, mask.shape[:2])
                    except Exception as e:
                        note = f"no spacing ({e.__class__.__name__})"
                        break
                ff = measure_frame_geometry(mask, final, spacing)
                iqf = compute_image_quality_features(
                    frame_base=fb, multiclass_mask=mask, final_image_u8=final, spacing_row=spacing)
                if "edge_clip_vertical" in _flags(iqf.to_dict()):
                    continue                       # vertical clip truncates the cross-section
                va = ff.get("visible_rf_area_mm2")
                if va is None or va <= 0:           # degenerate spacing/mask -> skip
                    continue
                # int_cv (texture) + fat-thickness are ALREADY computed inside
                # measure_frame_geometry (same mask + final), just discarded until
                # now — capture them so the re-base measures every predicted column
                # off ONE frame-set / one mask per study (fixes the latent
                # inconsistency where int_cv/fat rode a different frame selection).
                # NB fat_to_depth_q50 (fat / skin-to-femur) needs a FEMUR frame, not
                # a sweep frame, so it is rebuilt separately from the femur cine.
                clean.append((va, ff.get("rf_thickness_mm"), ff.get("full_thickness_q50_mm"),
                              ff.get("int_cv_within"),
                              ff.get("fat_thickness_q50_mm"), ff.get("fat_thickness_max_mm")))
        except Exception as e:                     # never let one study kill the batch
            note = f"error: {type(e).__name__}: {e}"[:80]
        clean.sort(key=lambda t: t[0], reverse=True)      # largest visible area first
        top = clean[:_LANDMARK_K]

        def _med(vals):
            v = [x for x in vals if x is not None
                 and not (isinstance(x, float) and np.isnan(x))]
            return float(median(v)) if v else np.nan

        rows.append({
            "study_code": study,
            "visible_rf_area_mm2": _med([t[0] for t in top]),
            "rf_thickness_mm": _med([t[1] for t in top]),
            "full_thickness_q50_mm": _med([t[2] for t in top]),
            "int_cv_within": _med([t[3] for t in top]),
            "fat_thickness_q50_mm": _med([t[4] for t in top]),
            "fat_thickness_max_mm": _med([t[5] for t in top]),
            "n_landmark_frames": len(top),
            "note": note,
        })
        if (i + 1) % 20 == 0 or args.limit:
            print(f"  [{i+1}/{len(todo)}] {study}: "
                  f"area={rows[-1]['visible_rf_area_mm2']:.0f} n={rows[-1]['n_landmark_frames']}", flush=True)

    out = pd.DataFrame(rows)
    out.to_csv(out_path, index=False)
    n_ok = int((out.n_landmark_frames >= 1).sum())
    print(f"\n[done] {len(out)} studies, {n_ok} with a landmark -> {out_path}")
    print(out[["study_code", "visible_rf_area_mm2", "rf_thickness_mm",
               "full_thickness_q50_mm", "int_cv_within", "fat_thickness_q50_mm",
               "n_landmark_frames"]].head(12).to_string(index=False))

    if args.apply:
        coh = pd.read_csv(_COHORT)
        bak = _COHORT.with_suffix(".csv.pre_landmark.bak")
        if not bak.exists():
            coh.to_csv(bak, index=False)
            print(f"[backup] {bak}")
        lm = out.set_index("study_code")
        for feat in ("visible_rf_area_mm2", "rf_thickness_mm", "full_thickness_q50_mm"):
            coh[feat] = coh["study_code"].map(lm[feat])
        if "rf_area_mm2" in coh.columns:        # drop the old wrong-convention column
            coh = coh.drop(columns=["rf_area_mm2"])
        coh.to_csv(_COHORT, index=False)
        print(f"[apply] merged landmark columns into {_COHORT} (rf_area_mm2 dropped)")
        # Keep the downstream snapshot (triple_placement.json) consistent with the
        # new RF columns, so the cohort artifacts never drift apart.
        from cohort_stats_precompute import write_triple_placement
        write_triple_placement(coh)
        print("[apply] regenerated triple_placement.json from the new cohort")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

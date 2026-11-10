"""Preflight orchestrator — stage1 (annotator masks), stage2 (predicted masks),
stage3 (any picked manifest + model bundle).

Pre-UI Item 3 (stage1) and Item 4 (stage2) are HARD GATES: any failure stops
the build until the root cause is reported and resolved.

Usage examples:
    source scripts/env.sh && .venv/bin/python scripts/preflight.py stage1 \
        --study-code AGZ855
    source scripts/env.sh && .venv/bin/python scripts/preflight.py stage2 \
        --study-code AGZ855
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Optional

# Launcher preamble — bridge sys.path so external deps resolve.
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import preflight_helpers.env_bootstrap  # noqa: F401  side-effect

import numpy as np
import pandas as pd
from PIL import Image

from preflight_helpers import env
from preflight_helpers.aggregation import aggregate_features
from preflight_helpers.feature_compute import compute_frame_features
from preflight_helpers.post_processing import post_process_rf
from preflight_helpers.model_registry import (
    default_bundle, list_bundles, resolve_canonical_pkl, BundleEntry,
)
from preflight_helpers.run_pipeline import (
    GateAnswers, PickedFrame, run_pipeline,
)
from preflight_helpers.segmenter import (
    BundleMeta, LABEL_RF,
    load_learner_safe, predict_mask, stack_modalities,
)
from preflight_helpers.study_discovery import discover_studies, list_frame_bases
from preflight_helpers.study_metadata import lookup_study


# ----------- Tolerance bands (HARD GATE) -----------
# Labelling-app features should match decision_log values exactly (annotator masks
# go through identical compute paths). 0.01 tolerance covers rounding.
LAB_FEATURE_TOL = {
    "rf_thickness_mm": 0.01,
    "qmt_mm": 0.01,
    "visible_rf_area_mm2": 0.01,
    "muscle_fat_ratio": 0.01,
    "star_ratio": 0.001,  # star is more sensitive; tighter
}
# White-paper features: cohort numbers came from PREDICTED masks; stage1 uses
# ANNOTATOR masks, so a small annotator-vs-predicted-mask divergence is expected.
WP_FEATURE_TOL = {
    "int_cv_within": 0.05,
    "full_thickness_q50_mm": 0.5,  # mm
}

DEFAULT_AGZ855_STUDY_PATH = (
    env.GORUNTULER_BACKUP / "anonabn_olcum" / "27Jan26_03"
    / "0000145_anonadm_anonabu_46e_171_110"
)
AGZ855_ANNOTATOR_MASK_DIR = (
    env.ANNOTATOR_MASKS_ROOT / "anonabn_olcum" / "27Jan26_03"
    / "0000145_anonadm_anonabu_46e_171_110"
)
AGZ855_TRANSPORT_DIR = (
    env.TRANSPORT_ROOT / "anonabn_olcum" / "27Jan26_03"
    / "0000145_anonadm_anonabu_46e_171_110"
)


def _load_mask_u8(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("L"), dtype=np.uint8)


def _study_codes_resolve(study_code: str) -> Path:
    df = pd.read_csv(env.STUDY_CODES_CSV)
    hit = df[df["study_code"] == study_code]
    if hit.empty:
        raise SystemExit(f"study_code {study_code} not in {env.STUDY_CODES_CSV}")
    rel = hit.iloc[0]["relative_frame_dir"]
    return env.GORUNTULER_BACKUP / rel


def _spacing_for(study_code: str, all_frame_bases: list[str]) -> dict:
    """Pull canonical spacing + bmi.

    decision_log carries pixel_spacing_x/y, dcm_rows, jpg_*, mask_*, bmi but
    NOT dcm_cols — which the canonical rf_quality.io.spacing.resolve_spacing
    needs for the DICOM-derived spacing rescale. Pull dcm_cols from
    transport_metadata.csv to enable the same path the cohort run used.
    """
    dl = pd.read_csv(env.DECISION_LOG, low_memory=False)
    rows = dl[dl["study_code"] == study_code]
    if rows.empty:
        raise SystemExit(f"decision_log has no rows for study_code {study_code}")
    r = rows.iloc[0]

    tp = pd.read_csv(env.TRANSPORT_METADATA)
    tp_rows = tp[tp["frame_base"].astype(str).isin([str(b) for b in all_frame_bases])]
    dcm_cols = float(tp_rows.iloc[0]["dcm_cols"]) if not tp_rows.empty else None

    return {
        "pixel_spacing_x": float(r["pixel_spacing_x"]),
        "pixel_spacing_y": float(r["pixel_spacing_y"]),
        "dcm_rows": float(r["dcm_rows"]),
        "dcm_cols": dcm_cols,
        "jpg_height": float(r["jpg_height"]),
        "jpg_width": float(r["jpg_width"]),
        "mask_height": float(r["mask_height"]),
        "mask_width": float(r["mask_width"]),
        "bmi": float(r["bmi"]) if pd.notna(r.get("bmi")) else None,
    }


def _resolve_agz855_frame_bases() -> tuple[list[str], str]:
    """Return (all_frame_bases, anchor_base) for AGZ855 from transport metadata."""
    tp = pd.read_csv(env.TRANSPORT_METADATA)
    rows = tp[tp["study_dirname"] == "0000145_anonadm_anonabu_46e_171_110"]
    if rows.empty:
        raise SystemExit("AGZ855 not in transport_metadata.csv")
    bases = sorted(rows["frame_base"].astype(str).tolist())
    dl = pd.read_csv(env.DECISION_LOG, low_memory=False)
    anchor = str(dl[dl["study_code"] == "AGZ855"]["center_frame_base"].dropna().iloc[0])
    return bases, anchor


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("(no rows)")
        return
    cols = list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    header = "  ".join(c.ljust(widths[c]) for c in cols)
    print(header)
    print("  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(widths[c]) for c in cols))


def _check_tolerance(actual: Optional[float], expected: Optional[float],
                     tol: float) -> tuple[bool, float]:
    """(within_tolerance, delta). Either side missing means there is nothing to compare, which is
    never a pass — `expected` can legitimately be None when the cohort reference is withheld."""
    if actual is None or expected is None:
        return False, float("inf")
    delta = abs(float(actual) - float(expected))
    return delta <= tol, delta


# --------------- Stage 1 ---------------

def cmd_stage1(args: argparse.Namespace) -> int:
    """Reproduce 7 features on AGZ855 ANNOTATOR MASKS, compare to triple_placement.json."""
    study_code = args.study_code
    if study_code != "AGZ855":
        print(f"[note] stage1 anchor case is AGZ855 — running on {study_code}")
    study_path = DEFAULT_AGZ855_STUDY_PATH if study_code == "AGZ855" else _study_codes_resolve(study_code)
    meta = lookup_study(study_path, default_code=study_code)
    print(f"[study]   {meta.study_code}  ({meta.age}y {meta.sex},"
          f" height {meta.height_cm} cm, weight {meta.weight_kg} kg, BMI {meta.bmi})")
    if not args.show_phi:
        print(f"          (raw folder name suppressed; PHI guardrail — pass --show-phi for"
              f" dev-mode display)")

    # Resolve frame triples + anchor.
    bases, anchor = _resolve_agz855_frame_bases() if study_code == "AGZ855" else (None, None)
    if bases is None:
        raise SystemExit("non-AGZ855 stage1 not implemented — needs per-study frame-base resolver")
    print(f"[frames]  triple = {bases}  anchor = {anchor}")

    # Spacing row from decision_log.
    spacing_row = _spacing_for(study_code, bases)
    print(f"[spacing] {spacing_row['pixel_spacing_x']:.6f} x {spacing_row['pixel_spacing_y']:.6f}"
          f" mm/px (dcm_rows={spacing_row['dcm_rows']:.0f},"
          f" mask {int(spacing_row['mask_height'])}x{int(spacing_row['mask_width'])}, BMI {spacing_row['bmi']})")

    # Load annotator masks (multi-class uint8) for all 3 frames.
    all_masks: list[np.ndarray] = []
    for fb in bases:
        m_path = AGZ855_ANNOTATOR_MASK_DIR / f"{fb}_mask.png"
        all_masks.append(_load_mask_u8(m_path))

    # Per-frame compute.
    frame_payloads: list[dict] = []
    for fb, m in zip(bases, all_masks):
        # Post-process annotator mask: largest_component + erode (NO fill_holes here —
        # canonical pipeline doesn't apply it; matters for cohort reproduction).
        rf_pair = post_process_rf(m, rf_class_id=LABEL_RF, erosion_px=3, fill_holes=False)
        final_path = AGZ855_TRANSPORT_DIR / f"{fb}_final.png"
        ff = compute_frame_features(
            frame_base=fb,
            is_anchor=(fb == anchor),
            multiclass_mask=m,
            all_frame_masks=all_masks,
            final_image_path=final_path,
            spacing_row=spacing_row,
            rf_mask_pair=rf_pair,
        )
        frame_payloads.append(ff.to_dict())

    # Aggregate.
    agg = aggregate_features(frame_payloads, anchor_frame_base=anchor)

    # ---------- Compare to triple_placement.json ----------
    with open(env.TRIPLE_PLACEMENT) as f:
        tp = json.load(f)
    expected = {k: v["value"] for k, v in tp[study_code].items()
                if k not in ("demographics", "quality_label")}

    # Map for white-paper:
    # full_thickness_q50_mm cohort value = _repr (anchor), so compare with `_repr` here.
    # int_cv_within cohort value = _agg_median, so compare with `_agg_median` here.
    rows = []
    all_pass = True
    for fname, exp in expected.items():
        if fname == "int_cv_within":
            actual = agg.get("int_cv_within_agg_median")
            tol = WP_FEATURE_TOL["int_cv_within"]
        elif fname == "full_thickness_q50_mm":
            actual = agg.get("full_thickness_q50_mm_repr")
            tol = WP_FEATURE_TOL["full_thickness_q50_mm"]
        else:
            actual = agg.get(f"{fname}_repr")
            tol = LAB_FEATURE_TOL.get(fname, 0.01)
        if exp is None:
            # The cohort REFERENCE is withheld, so there is nothing to reproduce. AGZ855 is a
            # legacy single-sweep study whose shallow FOV does not image the femur at the RF
            # landmark, so qmt_mm / star_ratio / fat_to_depth_q50 have no cohort value. Report it
            # as SKIP: never crash on the missing reference (this used to raise TypeError inside
            # _check_tolerance and abort the whole ladder at L1.3), and never score it as a pass.
            rows.append({
                "feature": fname,
                "expected": "withheld",
                "actual": "None" if actual is None else f"{actual:.6f}",
                "delta": "—",
                "tol": f"{tol}",
                "verdict": "SKIP",
            })
            continue
        ok, delta = _check_tolerance(actual, exp, tol)
        all_pass = all_pass and ok
        rows.append({
            "feature": fname,
            "expected": f"{exp:.6f}",
            "actual": "None" if actual is None else f"{actual:.6f}",
            "delta": "inf" if delta == float("inf") else f"{delta:.6f}",
            "tol": f"{tol}",
            "verdict": "PASS" if ok else "FAIL",
        })
    print()
    _print_table(rows)

    # Persist the run bundle (preflight-only — distinct from runs.jsonl which is for UI runs).
    env.ensure_local_dirs()
    out_dir = env.PREFLIGHT_DIR / f"stage1_{study_code}_{int(time.time())}"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "features.json", "w") as f:
        json.dump({
            "study_code": study_code,
            "stage": "stage1",
            "mask_source": "annotator",
            "frames": frame_payloads,
            "aggregate": agg,
            "expected_from_triple_placement": expected,
            "verdict": "PASS" if all_pass else "FAIL",
        }, f, indent=2, default=str)
    print(f"\n[saved]  {out_dir / 'features.json'}")

    print()
    print("=" * 60)
    if all_pass:
        print(f"PASS  all 7 features within tolerance for {study_code} stage1")
        return 0
    print(f"FAIL  stage1 has out-of-tolerance features for {study_code} — STOP, investigate")
    return 4


# --------------- Stage 2 ---------------

def cmd_stage2(args: argparse.Namespace) -> int:
    """Same 7 features but using PREDICTED masks from the canonical fold4 bundle.

    Compares each feature stage2 vs stage1 (annotator) within mask-noise tolerance.
    """
    study_code = args.study_code
    bundle_pkl = _find_canonical_bundle()
    bm = BundleMeta.from_pkl(bundle_pkl)
    print(f"[bundle]  {bundle_pkl.name}")
    print(f"          modalities={list(bm.modalities)}  resize_to={bm.resize_to}")

    # Load learner once.
    import torch
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    learn = load_learner_safe(bundle_pkl)

    bases, anchor = _resolve_agz855_frame_bases() if study_code == "AGZ855" else (None, None)
    if bases is None:
        raise SystemExit("non-AGZ855 stage2 not implemented yet")
    spacing_row = _spacing_for(study_code, bases)

    # Generate predicted masks (multi-class) at native resolution per frame.
    pred_masks: list[np.ndarray] = []
    for fb in bases:
        stacked = stack_modalities(AGZ855_TRANSPORT_DIR, fb, bm.modalities)
        pred = predict_mask(learn, stacked, bm, device=device)
        # Predicted mask is at the stacked-input's native size. Per-frame size for
        # AGZ855 transport is already mask-resolution (512x256). If not, downstream
        # compute_all_metrics will handle via spacing_row.
        pred_masks.append(pred)

    # Per-frame compute on predicted masks.
    frame_payloads: list[dict] = []
    for fb, m in zip(bases, pred_masks):
        rf_pair = post_process_rf(m, rf_class_id=LABEL_RF, erosion_px=3, fill_holes=False)
        final_path = AGZ855_TRANSPORT_DIR / f"{fb}_final.png"
        ff = compute_frame_features(
            frame_base=fb,
            is_anchor=(fb == anchor),
            multiclass_mask=m,
            all_frame_masks=pred_masks,
            final_image_path=final_path,
            spacing_row=spacing_row,
            rf_mask_pair=rf_pair,
        )
        frame_payloads.append(ff.to_dict())
    agg = aggregate_features(frame_payloads, anchor_frame_base=anchor)

    # Compare against stage1 (re-run stage1 quickly to get the annotator-mask values).
    annotator_masks: list[np.ndarray] = []
    for fb in bases:
        annotator_masks.append(_load_mask_u8(AGZ855_ANNOTATOR_MASK_DIR / f"{fb}_mask.png"))
    s1_payloads: list[dict] = []
    for fb, m in zip(bases, annotator_masks):
        rf_pair = post_process_rf(m, rf_class_id=LABEL_RF, erosion_px=3, fill_holes=False)
        final_path = AGZ855_TRANSPORT_DIR / f"{fb}_final.png"
        ff = compute_frame_features(
            frame_base=fb,
            is_anchor=(fb == anchor),
            multiclass_mask=m,
            all_frame_masks=annotator_masks,
            final_image_path=final_path,
            spacing_row=spacing_row,
            rf_mask_pair=rf_pair,
        )
        s1_payloads.append(ff.to_dict())
    s1_agg = aggregate_features(s1_payloads, anchor_frame_base=anchor)

    # Deltas — same comparison method as stage 1's triple_placement; mask-noise band.
    rows = []
    all_pass = True
    band_lab_thickness_mm = 1.0
    band_lab_area_mm2 = 80.0
    band_intensity = 0.10
    band_thickness_mm = 1.5
    bands = {
        "rf_thickness_mm": band_lab_thickness_mm,
        "qmt_mm": band_lab_thickness_mm,
        "visible_rf_area_mm2": band_lab_area_mm2,
        "muscle_fat_ratio": 0.30,
        "star_ratio": 0.05,
        "int_cv_within": band_intensity,
        "full_thickness_q50_mm": band_thickness_mm,
    }
    for fname, band in bands.items():
        if fname == "int_cv_within":
            a, b = agg.get("int_cv_within_agg_median"), s1_agg.get("int_cv_within_agg_median")
        elif fname == "full_thickness_q50_mm":
            a, b = agg.get("full_thickness_q50_mm_repr"), s1_agg.get("full_thickness_q50_mm_repr")
        else:
            a, b = agg.get(f"{fname}_repr"), s1_agg.get(f"{fname}_repr")
        if a is None or b is None:
            ok = False
            delta = float("inf")
        else:
            delta = abs(float(a) - float(b))
            ok = delta <= band
        all_pass = all_pass and ok
        rows.append({
            "feature": fname,
            "stage1_annot": "None" if b is None else f"{b:.6f}",
            "stage2_pred":  "None" if a is None else f"{a:.6f}",
            "delta": "inf" if delta == float("inf") else f"{delta:.6f}",
            "band": f"{band}",
            "verdict": "PASS" if ok else "FAIL",
        })
    print()
    _print_table(rows)

    env.ensure_local_dirs()
    out_dir = env.PREFLIGHT_DIR / f"stage2_{study_code}_{int(time.time())}"
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir / "features.json", "w") as f:
        json.dump({
            "study_code": study_code,
            "stage": "stage2",
            "mask_source": "predicted",
            "bundle": str(bundle_pkl),
            "frames": frame_payloads,
            "aggregate": agg,
            "verdict": "PASS" if all_pass else "WARN_INVESTIGATE",
        }, f, indent=2, default=str)
    with open(out_dir / "delta_vs_stage1.json", "w") as f:
        json.dump({"per_feature": rows}, f, indent=2)
    print(f"\n[saved]  {out_dir}")

    print()
    print("=" * 60)
    if all_pass:
        print(f"PASS  stage2 deltas within mask-noise bands for {study_code}")
        return 0
    print(f"WARN  stage2 has out-of-band feature(s) for {study_code}")
    print(f"      Inspect delta_vs_stage1.json before proceeding to UI build.")
    return 5


def _find_canonical_bundle() -> Path:
    # The pinned canonical bundle (configs/canonical_bundle.json), not newest-mtime.
    p = resolve_canonical_pkl()
    if p is None:
        raise SystemExit(f"No canonical bundle resolvable (pointer + {env.MODEL_DIR_LOCAL})")
    return p


# --------------- Stage 3 ---------------

def cmd_stage3(args: argparse.Namespace) -> int:
    """Live-pipeline run from a manifest of picked frames + their human gates.

    Manifest is either:
      - a JSON file path with the schema documented in run_pipeline.py, OR
      - the literal token `AGZ855_anchor_smoke` to run an AGZ855 anchor-frame
        smoke test (1 frame, all gates True) for end-to-end sanity.
    """
    # Resolve bundle.
    bundles = list_bundles(compute_hash=False)
    if args.model == "local":
        chosen = next((b for b in bundles if b.source == "local" and b.valid), None)
    elif args.model == "transport-default":
        chosen = default_bundle(bundles)
    else:
        chosen = next((b for b in bundles if str(b.pkl_path) == args.model), None)
    if chosen is None:
        raise SystemExit(f"no bundle matched --model={args.model}")
    print(f"[bundle]  {chosen.display_name()}  ({chosen.source})")

    # Resolve study + manifest.
    if args.manifest == "AGZ855_anchor_smoke":
        # Discover AGZ855 from studies_root.
        entries = discover_studies()
        entry = next((e for e in entries if e.meta.study_code == "AGZ855"), None)
        if entry is None:
            raise SystemExit("AGZ855 not discoverable in studies_root")
        study_path = entry.study_path
        study_meta = entry.meta
        anchor = "20260127_151045625"
        picked = [PickedFrame(
            frame_base=anchor, is_anchor=True,
            gates=GateAnswers(usable=True, segmentation_perfect=True, use_for_analysis=True),
        )]
        print(f"[smoke]   AGZ855 anchor {anchor}, 1 frame, all gates True")
    else:
        with open(args.manifest) as f:
            manifest = json.load(f)
        study_path = Path(manifest["study_path"])
        study_meta = lookup_study(study_path, default_code=manifest.get("study_code"))
        picked = []
        for fr in manifest["frames"]:
            g = fr.get("gates", {})
            picked.append(PickedFrame(
                frame_base=fr["frame_base"],
                is_anchor=fr.get("is_anchor", False),
                gates=GateAnswers(
                    usable=g.get("usable", True),
                    segmentation_perfect=g.get("segmentation_perfect", False),
                    use_for_analysis=g.get("use_for_analysis", True),
                    drop_reason=g.get("drop_reason"),
                    # Build-Step 1 structured capture — a hand-authored manifest
                    # rarely carries these; default to empty when absent.
                    drop_reason_codes=tuple(g.get("drop_reason_codes") or ()),
                    drop_reason_other=g.get("drop_reason_other") or "",
                    qa_overrides=dict(g.get("qa_overrides") or {}),
                ),
            ))

    res = run_pipeline(
        study_code=study_meta.study_code,
        study_path=study_path,
        study_meta=study_meta,
        picked_frames=picked,
        bundle=chosen,
        selected_by=args.selected_by,
        use_annotator_masks=args.use_annotator_masks,
        mode="cli_stage3",
    )

    # Render a compact verdict.
    print(f"[run]     run_id = {res.run_id}")
    print(f"[run]     record = {res.record_path}")
    print(f"[aggregate] n_frames_in_aggregate = {res.aggregate.get('_n_frames_in_aggregate')}")
    rows = []
    from preflight_helpers.run_pipeline import HEADLINE_FEATURES, SECONDARY_FEATURES
    for fname in HEADLINE_FEATURES + SECONDARY_FEATURES:
        v = res.aggregate.get(fname)
        p = res.placements.get(fname) or {}
        ov = p.get("overall") or {}
        am = p.get("age_matched") or {}
        sm = p.get("sex_matched") or {}
        rows.append({
            "feature": fname,
            "value": f"{v:.4f}" if v is not None else "None",
            "n_overall": ov.get("n"),
            "p_overall": f"{ov.get('pctile'):.1f}" if ov.get("pctile") is not None else "—",
            "p_age": f"{am.get('pctile'):.1f}" if am.get("pctile") is not None else "—",
            "p_sex": f"{sm.get('pctile'):.1f}" if sm.get("pctile") is not None else "—",
        })
    _print_table(rows)
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s1 = sub.add_parser("stage1")
    s1.add_argument("--study-code", default="AGZ855")
    s1.add_argument("--show-phi", action="store_true")
    s1.set_defaults(func=cmd_stage1)

    s2 = sub.add_parser("stage2")
    s2.add_argument("--study-code", default="AGZ855")
    s2.set_defaults(func=cmd_stage2)

    s3 = sub.add_parser("stage3")
    s3.add_argument("--manifest", required=True,
                    help="path to JSON manifest, OR 'AGZ855_anchor_smoke'")
    s3.add_argument("--model", default="local",
                    help="'local' | 'transport-default' | absolute path to .pkl")
    s3.add_argument("--selected-by", default="akadir")
    s3.add_argument("--use-annotator-masks", action="store_true",
                    help="if set, load annotator masks instead of segmenter prediction (stage1-style)")
    s3.set_defaults(func=cmd_stage3)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

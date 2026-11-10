#!/usr/bin/env python
"""D2 of the capture-association audit — per-capture pixel->mm->feature derivation.

Segments EVERY capture of a study (not just the routed femur/rf/sweep roles — so a second
femur / an orphan still-frame group is seen too) and prints, for each feature, the fully-worked
chain under BOTH the attached-DICOM spacing and the frame-width-derived spacing, plus the
segmentation class histogram (does this capture reach the femur?). That histogram is how we tell
which capture is the real RF vs a femur vs a stray. Reuses the LIVE compute path
(`compute_frame_features`) so the numbers are exactly what the app would produce.

Run in the BACKGROUND (model load + segmentation is slow; foreground .venv python is
intermittently declined here) and read the JSON / stdout:

    /home/akadir/dev/muscle_mania/.venv/bin/python scripts/audit_measurement_derivation.py \
        data/studies_root/anonabx/21jun26_026/21jun26_026_829_anonaat_anonabz_45k_152_89_45r \
        data/studies_root/anonaad/11jun06_004/...     # a known-good control
"""
import os, sys, glob, re, json, statistics, argparse
from pathlib import Path
import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from preflight_helpers.model_registry import default_bundle, list_bundles
from preflight_helpers.segmenter import BundleMeta, load_learner_safe, LABEL_RF
from preflight_helpers.run_pipeline import (
    _load_or_predict_masks, _spacing_row_from_dicom, PickedFrame, GateAnswers)
from preflight_helpers.feature_compute import compute_frame_features
from preflight_helpers.post_processing import post_process_rf
from preflight_helpers.aggregation import aggregate_features
import torch
from PIL import Image

FOV = 1967.0                       # 128L: frame_width_px * depth_cm  (device constant)
CONTENT = re.compile(r"^\d{8}_\d{9}$")
CLASS_NAME = {0: "bg", 1: "RF", 2: "VI", 3: "VL", 4: "VM", 5: "fascia", 6: "skin", 7: "femur", 8: "fat"}
KEYS = ["rf_thickness_mm", "full_thickness_q50_mm", "fat_thickness_q50_mm",
        "fat_to_depth_q50", "qmt_mm", "star_ratio", "int_cv_within", "visible_rf_area_mm2"]


def _content_jpgs(raw_dir):
    return [p for p in glob.glob(glob.escape(str(raw_dir)) + "/*.jpg") if CONTENT.match(Path(p).stem)]


def _sample_bases(raw_dir, n):
    stems = sorted({Path(p).stem for p in _content_jpgs(raw_dir)})
    if len(stems) <= n:
        return stems
    idx = np.linspace(0, len(stems) - 1, n).round().astype(int)
    return [stems[i] for i in sorted(set(idx))]


def _median_width(raw_dir):
    ws = [Image.open(p).width for p in _content_jpgs(raw_dir)]
    return statistics.median(ws) if ws else None


def _capture_dirs(study):
    """(label, raw_image_dir, dcm_dir) for the root sweep + every captures/c* group."""
    study = Path(study)
    out = []
    for d in sorted(glob.glob(str(study) + "/RawImagesForResearch*")):
        if _content_jpgs(d):
            out.append(("sweep(root)", Path(d), study))
    for cap in sorted(glob.glob(str(study) + "/captures/c*")):
        raws = [r for r in sorted(glob.glob(cap + "/RawImagesForResearch*")) if _content_jpgs(r)]
        if raws:
            out.append((Path(cap).name, Path(raws[0]), Path(cap)))
    return out


def _study_rows_const(study):
    """dcm_rows is device-constant within a study; recover it from any .dcm so a capture that
    lacks its own .dcm (e.g. AKA017 c3) can still be width-derived."""
    import pydicom
    for p in glob.glob(str(Path(study)) + "/**/*.dcm", recursive=True):
        try:
            r = getattr(pydicom.dcmread(p, stop_before_pixels=True), "Rows", None)
            if r:
                return int(r)
        except Exception:
            pass
    return 570


def _aggregate(pfs, masks, fc, sp):
    rows = []
    for pf, mk in zip(pfs, masks):
        rfp = post_process_rf(mk, rf_class_id=LABEL_RF, erosion_px=3, fill_holes=False)
        ff = compute_frame_features(
            frame_base=pf.frame_base, is_anchor=pf.is_anchor, multiclass_mask=mk,
            all_frame_masks=masks, final_image_path=None, final_image_u8=fc[pf.frame_base],
            spacing_row=sp, rf_mask_pair=rfp).to_dict()
        rows.append(ff)
    anchor = next((pf.frame_base for pf in pfs if pf.is_anchor), None)
    return aggregate_features(rows, anchor_frame_base=anchor)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("studies", nargs="+")
    ap.add_argument("--nframes", type=int, default=9)
    args = ap.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    b = default_bundle(list_bundles())
    learn = load_learner_safe(b.pkl_path)
    meta = BundleMeta.from_pkl(b.pkl_path)
    print(f"# bundle {b.display_name()[:60]}  device={device}", flush=True)

    report = {}
    for study in args.studies:
        study = Path(study)
        scode = study.name
        rows_const = _study_rows_const(study)
        print(f"\n{'=' * 96}\n# STUDY {scode}   (device rows={rows_const})", flush=True)
        report[scode] = {"rows_const": rows_const, "captures": {}}

        for label, raw, dcmdir in _capture_dirs(study):
            w = _median_width(raw)
            fdepth = FOV / w if w else None
            bases = _sample_bases(raw, args.nframes)
            pfs = [PickedFrame(frame_base=fb, is_anchor=(i == len(bases) // 2),
                               gates=GateAnswers(usable=True, use_for_analysis=True))
                   for i, fb in enumerate(bases)]
            fc, be = {}, {}
            masks, _ = _load_or_predict_masks(
                picked_frames=pfs, raw_image_dir=raw, bundle=b, bundle_meta=meta, learn=learn,
                device=device, use_annotator_masks=False, annotator_mask_dir=None,
                final_u8_out=fc, boundary_entropy_out=be)
            mask_h = int(masks[0].shape[0])

            try:
                sp_act = _spacing_row_from_dicom(str(dcmdir), masks[0].shape[:2])
            except Exception:
                sp_act = None
            dcm_depth = None
            if sp_act and sp_act.get("pixel_spacing_y") and sp_act.get("dcm_rows"):
                dcm_depth = sp_act["pixel_spacing_y"] * sp_act["dcm_rows"] / 10.0

            # width-derived spacing: the capture's OWN frame depth, device-constant rows
            sp_wid = dict(sp_act) if sp_act else {"mask_height": mask_h, "dcm_rows": rows_const}
            if fdepth:
                sp_wid["dcm_rows"] = sp_wid.get("dcm_rows") or rows_const
                sp_wid["pixel_spacing_y"] = fdepth * 10.0 / sp_wid["dcm_rows"]
                sp_wid.setdefault("mask_height", mask_h)

            # class histogram (which anatomy does this capture actually see?)
            hist = {}
            for mk in masks:
                vals, cnts = np.unique(mk, return_counts=True)
                for v, c in zip(vals, cnts):
                    hist[int(v)] = hist.get(int(v), 0) + int(c)
            tot = sum(hist.values()) or 1
            comp = {CLASS_NAME.get(k, str(k)): round(100 * v / tot, 1)
                    for k, v in sorted(hist.items()) if k != 0 and 100 * v / tot >= 0.3}

            agg_act = _aggregate(pfs, masks, fc, sp_act) if sp_act else {}
            agg_wid = _aggregate(pfs, masks, fc, sp_wid) if sp_wid.get("pixel_spacing_y") else {}

            has_femur = "femur" in comp
            print(f"\n## {label}   width={w}px  frameDepth={fdepth:.2f}cm  "
                  f"attachedDICOMdepth={round(dcm_depth,2) if dcm_depth else None}cm  "
                  f"picks={len(bases)}  mask_h={mask_h}", flush=True)
            print(f"   seg classes(%px): {comp}   >>> reaches_femur={has_femur}", flush=True)
            for k in KEYS:
                print(f"     {k:22s} attachedDICOM={agg_act.get(k)}   width-derived={agg_wid.get(k)}",
                      flush=True)

            report[scode]["captures"][label] = {
                "width_px": w, "frame_depth_cm": round(fdepth, 3) if fdepth else None,
                "attached_dicom_depth_cm": round(dcm_depth, 3) if dcm_depth else None,
                "mask_h": mask_h, "seg_classes_pct": comp, "reaches_femur": has_femur,
                "features_attached_dicom": {k: agg_act.get(k) for k in KEYS},
                "features_width_derived": {k: agg_wid.get(k) for k in KEYS},
            }

    out = REPO / "data/audit/measurement_derivation.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, default=str))
    print(f"\n>>> wrote {out}", flush=True)


if __name__ == "__main__":
    main()

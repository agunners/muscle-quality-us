"""Per-frame QA substrate for the CENTER-RF cine — the modality int_cv is read from.

Segments every frame of each study's centre-RF capture and records, per frame, two families:

  PLACEMENT  (is the texture ROI in the right place?)  — mask topology, boundary integrity,
             superficial AND deep fascia continuity, layer ordering, and the "free" geometry
             (RF/fat thickness) that this modality measures but the score never uses.
  CONTENT    (is the signal inside the ROI clean?)     — int_cv and its own components
             (mean/std), shadow fraction, ROI depth position.

Writes ONE row per frame. No aggregation, no thresholds, no verdicts — this is substrate, so the
distribution can be looked at before any cutoff is invented. Read-only w.r.t. the repo.

Usage:  .venv/bin/python scripts/analysis/rf_frame_qa.py <out.csv> [study_uid ...]      (no uids => every materialised study)
"""
from __future__ import annotations

import csv
import hashlib
import sys
import warnings
from pathlib import Path

import numpy as np

sys.path.insert(0, "scripts")
sys.path.insert(0, "scripts/preflight_helpers")
import preflight_helpers.env_bootstrap  # noqa: F401,E402

warnings.filterwarnings("ignore")

STUDIES_ROOT = Path("data/studies_root")
FASCIA_BAND_PX = 6          # how far from the muscle edge a fascia pixel still counts
CLOSE_COLS = 2              # probe-noise gaps this wide are bridged, as the live detector does
MIN_COLS = 16               # fewer RF columns than this and continuity is not assessable


def study_uid(dirname: str) -> str:
    return "NR" + hashlib.sha256(dirname.encode("utf-8")).hexdigest()[:8].upper()


def close_gaps(b: np.ndarray, n: int) -> np.ndarray:
    """Bridge runs of <=n False between True — the live detector's probe-noise allowance."""
    out = b.copy()
    i = 0
    while i < len(out):
        if not out[i]:
            j = i
            while j < len(out) and not out[j]:
                j += 1
            if 0 < i and j < len(out) and (j - i) <= n:
                out[i:j] = True
            i = j
        else:
            i += 1
    return out


def fascia_continuity(mask, rf_big, fascia, side: str) -> float | None:
    """Fraction of RF columns carrying a fascia pixel just beyond the muscle edge.

    side='top'    -> the SUPERFICIAL fascia (what anisotropy_dropout_superficial measures)
    side='bottom' -> the DEEP fascia, the RF's lower boundary. This is the one with no detector
                     in the repo (`anisotropy_dropout_deep` is a reserved slot with no code), and
                     the one a mis-set shallow view loses first.
    """
    cols = np.where(rf_big.any(axis=0))[0]
    if cols.size < MIN_COLS:
        return None
    present = np.zeros(cols.size, dtype=bool)
    for i, c in enumerate(cols):
        rows = np.flatnonzero(rf_big[:, c])
        if side == "top":
            edge = int(rows.min())
            lo, hi = max(0, edge - FASCIA_BAND_PX), edge + 3
        else:
            edge = int(rows.max())
            lo, hi = max(0, edge - 2), min(mask.shape[0], edge + FASCIA_BAND_PX + 1)
        present[i] = bool(fascia[lo:hi, c].any())
    return float(close_gaps(present, CLOSE_COLS).mean())


def main() -> None:
    import torch  # noqa: F401
    from preflight_helpers.study_discovery import study_modalities, list_frame_bases
    from preflight_helpers.model_registry import list_bundles
    from preflight_helpers.segmenter import (BundleMeta, load_learner_safe, predict_mask,
                                             build_voi_hist_clahe_stack, synthesize_final_u8)
    from preflight_helpers.landmark import measure_frame_geometry
    from preflight_helpers.post_processing import post_process_rf
    from preflight_helpers.run_pipeline import _spacing_row_from_dicom
    from labeling_app.measurements import (LABEL_RF, LABEL_FASCIA, LABEL_FAT, LABEL_VI)
    from scipy import ndimage

    out_path = Path(sys.argv[1])
    want = set(sys.argv[2:])

    NATIVE_STEM = "native_512x256"
    bundle = next(x for x in list_bundles(compute_hash=False) if NATIVE_STEM in Path(x.pkl_path).stem)
    learn = load_learner_safe(bundle.pkl_path)
    meta = BundleMeta.from_pkl(bundle.pkl_path)
    print(f"[model] {Path(bundle.pkl_path).stem[:60]}", flush=True)

    studies = sorted({c.parent for c in STUDIES_ROOT.rglob("captures")}, key=lambda p: p.name)
    if want:
        studies = [s for s in studies if study_uid(s.name) in want]
    print(f"[studies] {len(studies)}", flush=True)

    cols = ["study_uid", "frame_base",
            # --- CONTENT: what the texture number is actually made of
            "int_cv_within", "roi_mean", "roi_std", "roi_px", "roi_row_frac", "shadow_frac",
            # --- PLACEMENT: whether the ROI sits where it should
            "rf_px", "rf_components", "rf_holes", "rf_solidity", "rf_fill_ratio",
            "fascia_cont_top", "fascia_cont_deep", "fat_below_rf_cols", "vi_above_rf_cols",
            # --- the DEEP BOUNDARY, read as anatomy rather than as one label:
            # under a real RF the column must continue into tissue (fascia, then VI). A column that
            # falls straight into BACKGROUND has no imaged tissue beneath the muscle at all — which
            # is what "the deep fascia is absent" looks like to the segmenter.
            "deep_bg_cols", "deep_vi_cols", "deep_fascia_cols", "deep_fat_cols",
            "deep_tissue_px_med", "deep_support",
            # --- the "free" geometry this modality measures but the score never uses
            "rf_thickness_mm", "fat_thickness_q50_mm", "visible_rf_area_mm2",
            "full_max_thickness_mm"]
    fh = out_path.open("w", newline="", encoding="utf-8")
    w = csv.DictWriter(fh, fieldnames=cols)
    w.writeheader()

    for n, sp in enumerate(studies, 1):
        uid = study_uid(sp.name)
        try:
            mods = study_modalities(sp)
        except Exception as e:
            print(f"[skip] {uid}: {e}", flush=True)
            continue
        m = mods.get("rf")
        if not m or not m.raw_image_dir:
            print(f"[skip] {uid}: no centre-RF capture", flush=True)
            continue
        try:
            spacing = _spacing_row_from_dicom(m.raw_image_dir.parent, None)
        except Exception:
            spacing = {}
        wrote = 0
        for fb in list_frame_bases(m.raw_image_dir):
            try:
                final = synthesize_final_u8(m.raw_image_dir, fb)
                mask = predict_mask(learn, build_voi_hist_clahe_stack(
                    final, modalities=meta.modalities), meta)
                sp2 = dict(spacing)
                sp2["mask_height"], sp2["mask_width"] = mask.shape[:2]
                ff = measure_frame_geometry(mask, final, sp2)

                rf_raw = (mask == LABEL_RF)
                if not rf_raw.any():
                    continue
                lab, ncomp = ndimage.label(rf_raw)
                sizes = ndimage.sum(rf_raw, lab, range(1, ncomp + 1))
                big = (lab == (int(np.argmax(sizes)) + 1))
                filled = ndimage.binary_fill_holes(big)
                holes, nholes = ndimage.label(filled & ~big)

                # solidity via a per-row convex span — a cheap, dependency-free stand-in for the
                # convex hull that captures the same thing: a ragged/bitten boundary scores low.
                span = 0
                for r in np.flatnonzero(big.any(axis=1)):
                    c = np.flatnonzero(big[r]);  span += int(c.max() - c.min() + 1)
                solidity = float(big.sum() / span) if span else None

                pr = post_process_rf(mask, rf_class_id=LABEL_RF, erosion_px=3)
                roi = pr.rf_eroded_mask
                vals = final[roi] if roi.any() else np.array([], dtype=np.uint8)

                fascia = (mask == LABEL_FASCIA)
                fat = (mask == LABEL_FAT)
                vi = (mask == LABEL_VI)
                # anatomical ORDER violations — hard errors, not soft statistics
                fat_below = vi_above = 0
                # ...and the DEEP BOUNDARY read as anatomy: walk down from the muscle floor and ask
                # what the column actually contains. Canonical order below RF is fascia -> VI. A
                # column that drops straight into background is one where nothing beneath the muscle
                # was imaged at all.
                d_bg = d_vi = d_fas = d_fat = 0
                d_tissue = []
                cols_rf = np.flatnonzero(big.any(axis=0))
                for c in cols_rf:
                    rows = np.flatnonzero(big[:, c])
                    lo = rows.max() + 1
                    col = mask[lo:, c]
                    if fat[lo:, c].any():
                        fat_below += 1
                    if vi[:rows.min(), c].any():
                        vi_above += 1
                    if col.size == 0:
                        d_bg += 1
                        d_tissue.append(0)
                        continue
                    nz = np.flatnonzero(col != 0)          # 0 == background
                    d_tissue.append(int(nz.size))
                    first = col[nz[0]] if nz.size else 0
                    if nz.size == 0 or nz[0] > 3:
                        d_bg += 1                          # nothing (or a gap) directly beneath
                    if (col == LABEL_VI).any():
                        d_vi += 1
                    if (col[:8] == LABEL_FASCIA).any():
                        d_fas += 1
                    if first == LABEL_FAT:
                        d_fat += 1
                nc = max(1, cols_rf.size)

                # shadow: dark ROI px with NO echo deeper in the same column (center_rf_scorer's rule)
                shadow_frac = None
                if roi.any():
                    bright = (final >= 40)
                    deeper = np.flip(np.maximum.accumulate(np.flip(bright, axis=0), axis=0), axis=0)
                    shadow_frac = float(((final <= 8) & ~deeper & roi).sum() / roi.sum())

                rows_idx = np.flatnonzero(roi.any(axis=1))
                w.writerow({
                    "study_uid": uid, "frame_base": fb,
                    "int_cv_within": ff.get("int_cv_within"),
                    "roi_mean": float(vals.mean()) if vals.size else None,
                    "roi_std": float(vals.std()) if vals.size else None,
                    "roi_px": int(roi.sum()),
                    "roi_row_frac": (float(rows_idx.mean() / mask.shape[0])
                                     if rows_idx.size else None),
                    "shadow_frac": shadow_frac,
                    "rf_px": int(rf_raw.sum()), "rf_components": int(ncomp),
                    "rf_holes": int(nholes), "rf_solidity": solidity,
                    "rf_fill_ratio": float(big.sum() / filled.sum()) if filled.any() else None,
                    "fascia_cont_top": fascia_continuity(mask, big, fascia, "top"),
                    "fascia_cont_deep": fascia_continuity(mask, big, fascia, "bottom"),
                    "fat_below_rf_cols": fat_below, "vi_above_rf_cols": vi_above,
                    "deep_bg_cols": round(d_bg / nc, 4), "deep_vi_cols": round(d_vi / nc, 4),
                    "deep_fascia_cols": round(d_fas / nc, 4), "deep_fat_cols": round(d_fat / nc, 4),
                    "deep_tissue_px_med": (float(np.median(d_tissue)) if d_tissue else None),
                    "deep_support": round((d_fas + d_vi > 0) and
                                          float(sum(1 for i, c in enumerate(cols_rf)
                                                    if (mask[np.flatnonzero(big[:, c]).max() + 1:, c]
                                                        == LABEL_VI).any()
                                                    or (mask[np.flatnonzero(big[:, c]).max() + 1:
                                                             np.flatnonzero(big[:, c]).max() + 9, c]
                                                        == LABEL_FASCIA).any()) / nc) or 0.0, 4),
                    "rf_thickness_mm": ff.get("rf_thickness_mm"),
                    "fat_thickness_q50_mm": ff.get("fat_thickness_q50_mm"),
                    "visible_rf_area_mm2": ff.get("visible_rf_area_mm2"),
                    "full_max_thickness_mm": ff.get("full_max_thickness_mm"),
                })
                wrote += 1
            except Exception as e:
                print(f"  [frame-fail] {uid} {fb}: {type(e).__name__}: {e}", flush=True)
        fh.flush()
        print(f"[{n}/{len(studies)}] {uid} -> {wrote} frames", flush=True)
    fh.close()
    print("done ->", out_path, flush=True)


if __name__ == "__main__":
    main()

"""Compute the 98 `rf_quality` registry features on a muscle_mania capture.

ONE public function::

    compute_capture_features(study_uid, role) -> {stem: value | None}

`role` ∈ {"femur", "rf", "sweep"}. The value is the MEDIAN over that capture's
top-5 largest-RF frames — the same landmark recipe the repo already uses for the
new cohort (`scripts/derive_new_round_cohort.py:measure_all`: segment every frame
of the cine, rank by `(mask == LABEL_RF).sum()` descending, keep area > 0, take
K=5, median each feature over them).

THE COMPUTE PATH — muscle_mania supplies the mask, rf_quality supplies the maths
-----------------------------------------------------------------------------
muscle_mania has no `transport/` pre-processing for the new (66-study) cohort, so
`rf_quality.pipeline.compute_frame` — which resolves frames through a
`TransportIndex` and a seg-QC logit cache — cannot be called as-is. Its *body*
can: everything after the two loads is `(img_u8, mask, spacing, cfg)` in and a
stem-keyed dict out. This module reproduces that body verbatim (same call order,
same helpers, same cfg) and feeds it the repo's own mask path:

    synthesize_final_u8 -> build_voi_hist_clahe_stack -> predict_mask   (segmenter.py)
    _spacing_row_from_dicom                                             (run_pipeline.py)

which is exactly what `feature_routing.compute_modality_aggregate` and
`derive_new_round_cohort.measure_all` do. The `final` u8 the segmenter saw is the
same array handed to the intensity features, so the pixels the mask was drawn on
are the pixels that get measured.

Class IDs are muscle_mania's, NOT `rf_quality.config.MaskCfg`'s defaults:
`femur_class_id` there is 4, which is actually VL in this palette
(memory `feedback_class_pinning_femur7`). Femur is 7. muscle/fascia/skin/fat
(1/5/6/8) agree between the two repos.

DEGRADE, NEVER RAISE — a stem that cannot be computed comes back None. A frame
that blows up is skipped; a capture with no usable frame returns all-None.

Cache: `cache/<study_uid>__<role>__k<K>__<bundle8>.json`, keyed on the bundle
sha so a segmenter swap cannot silently serve stale numbers. Idempotent: the
second call for the same key reads the JSON and does no GPU work.

    from compute_features import compute_capture_features
    feats = compute_capture_features("NRE907A110", "rf")
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from statistics import median
from typing import Optional

import numpy as np

# --- repo bootstrap -------------------------------------------------------- #
REPO = Path(__file__).resolve().parents[2]
for _p in (str(REPO), str(REPO / "scripts"), str(Path(__file__).resolve().parents[2] / "private" / "muscle_quality_exploration"),
           str(Path(__file__).resolve().parents[2] / "private" / "muscle_model_training/src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

HERE = Path(__file__).resolve().parent
CACHE_DIR = HERE / "cache"
QA_FRAMES = REPO / "data" / "qa_frames.csv"
STUDIES_ROOT = REPO / "data" / "studies_root"
LANDMARK_K = 5

# muscle_mania's palette (labeling_app/measurements.py). femur=7, NOT rf_quality's 4.
MM_MUSCLE_CLASS = 1
MM_FASCIA_CLASS = 5
MM_SKIN_CLASS = 6
MM_FEMUR_CLASS = 7
MM_FAT_CLASS = 8
EROSION_PX = 3

ROLES = ("femur", "rf", "sweep")


# --------------------------------------------------------------------------- #
# The stem universe + the honest not-computable list
# --------------------------------------------------------------------------- #
def _registry_stems() -> list[str]:
    from rf_quality.registry import REGISTRY
    return [s.stem for s in REGISTRY]


# The three `family=anchor` stems are decision-log PASSTHROUGHS in rf_quality
# (`features/anchor.py:take_anchor_values` reads decision_log.csv columns
# rf_echo / rf_thickness_mm / rf_area_mm2). The new 66-study cohort has no
# decision_log, so the passthrough has nothing to pass. Their *definitions*,
# though, are "the value the labeling app computes on this same mask+frame" —
# and muscle_mania IS the labeling app. We compute them natively through
# `preflight_helpers.feature_compute.compute_frame_features` on the identical
# (mask, final_u8, spacing). That is the anchor family's stated meaning, not a
# substitute for it.
ANCHOR_STEM_TO_MM_KEY = {
    "anchor_rf_echo": "rf_echo",
    "anchor_rf_thickness_mm": "rf_thickness_mm",
    "anchor_rf_area_mm2": "visible_rf_area_mm2",
}


# --------------------------------------------------------------------------- #
# Segmenter session (module-level, loaded once)
# --------------------------------------------------------------------------- #
_SESSION: Optional[dict] = None


def _session() -> dict:
    """{learn, meta, device, bundle_stem, bundle_sha8} — the canonical bundle,
    loaded once per process (a fastai learner load is ~5 s)."""
    global _SESSION
    if _SESSION is not None:
        return _SESSION
    import torch
    from preflight_helpers.model_registry import default_bundle, list_bundles, _sha256_of
    from preflight_helpers.segmenter import BundleMeta, load_learner_safe

    bundle = default_bundle(list_bundles(compute_hash=False))
    if bundle is None:
        raise RuntimeError("no valid segmenter bundle on this machine")
    _SESSION = {
        "learn": load_learner_safe(bundle.pkl_path),
        "meta": BundleMeta.from_pkl(bundle.pkl_path),
        "device": torch.device("cuda" if torch.cuda.is_available() else "cpu"),
        "bundle_stem": Path(bundle.pkl_path).stem,
        "bundle_sha8": _sha256_of(bundle.pkl_path)[:8],
    }
    return _SESSION


def _rf_cfg():
    """An `AppCfg` with muscle_mania's class IDs. `PathsCfg` is required by the
    dataclass but never read on the per-frame path (only `run_infer` touches it),
    so it is filled with the repo root rather than a transport tree that does not
    exist for this cohort. Built directly — `load_config` would fail-fast on the
    missing decision_log/transport paths."""
    from rf_quality.config import AppCfg, MaskCfg, PathsCfg
    return AppCfg(
        paths=PathsCfg(decision_log=REPO, transport_metadata=REPO, study_codes=REPO,
                       transport_root=REPO, runs_root=CACHE_DIR),
        mask=MaskCfg(muscle_class_id=MM_MUSCLE_CLASS, fascia_class_id=MM_FASCIA_CLASS,
                     skin_class_id=MM_SKIN_CLASS, fat_class_id=MM_FAT_CLASS,
                     femur_class_id=MM_FEMUR_CLASS, erosion_px=EROSION_PX),
    )


def _mm_spacing(spacing_row: dict, mask_h: int, mask_w: int):
    """mm-per-mask-pixel on MUSCLE_MANIA's convention, as an `rf_quality.Spacing`.

    NOT `rf_quality.io.spacing.resolve_spacing`. That helper takes the x-axis from
    the raw DICOM `Columns` tag::

        sx = pixel_spacing_x * dcm_cols / mask_width

    On this scanner `Columns` is the WHOLE scanner frame (900 px) including the UI
    chrome, while the exported ultrasound JPG is only the scan sector — 399 px wide
    at 4.9 cm, 285 px at 6.9 cm. Using 900 inflates the x-spacing by 2.03x on an
    RF/sweep capture and 2.84x on a femur capture, and `full_area_mm2` with it: on
    the proof studies it pushed the femur past the registry's 2000 mm^2 plausibility
    ceiling, so the stem silently nulled, while muscle_mania's own path measured the
    same mask at ~1020 mm^2.

    muscle_mania (`measurements.area_mm2`) instead derives the ultrasound-region
    column count from the JPG aspect ratio::

        dcm_cols_usg = jpg_width * dcm_rows / jpg_height
        mm_per_x     = pixel_spacing_x * dcm_cols_usg / mask_width
        mm_per_y     = pixel_spacing_y * dcm_rows     / mask_height

    (memory `feedback_spacing_rescale_mask_space`: "dcm_cols only in
    transport_metadata"). The y-axis is identical in both repos; only x differs.
    Using muscle_mania's puts every mm-valued rf_quality stem on the same ruler as
    the repo's own numbers, which is the point of reusing the repo's mask path.
    """
    from rf_quality.io.spacing import Spacing
    psx, psy = spacing_row.get("pixel_spacing_x"), spacing_row.get("pixel_spacing_y")
    rows, jh, jw = (spacing_row.get("dcm_rows"), spacing_row.get("jpg_height"),
                    spacing_row.get("jpg_width"))
    if psy is None or not rows or not mask_h:
        return Spacing(None, None, complete=False, source="missing")
    sy = float(psy) * float(rows) / float(mask_h)
    if psx is None or not jh or not jw or not mask_w:
        # y-only: thicknesses still resolve; areas (which need both) stay None.
        return Spacing(None, sy, complete=False, source="dicom_y_only")
    sx = float(psx) * (float(jw) * float(rows) / float(jh)) / float(mask_w)
    return Spacing(sx, sy, complete=True, source="dicom_mm_convention")


# --------------------------------------------------------------------------- #
# The per-frame feature block — rf_quality.pipeline.compute_frame's body
# --------------------------------------------------------------------------- #
def _frame_stems(img_u8: np.ndarray, mask: np.ndarray, spacing, cfg,
                 errors: Optional[list] = None) -> dict:
    """Every registry stem rf_quality can compute from (frame, mask, spacing).

    Mirrors `rf_quality.pipeline.compute_frame` lines 180-246 in the same order:
    geometry -> intensity -> hyperechoic -> glcm -> depth bands -> orientation ->
    fat -> baseline geometry -> radiomics matrices -> advanced texture ->
    fascia split -> Young-corrected t200 -> Young cascade -> plausibility gate.

    Each group runs inside `_group`, so a group that fails (a missing optional
    dependency, a degenerate mask) nulls only ITS OWN stems — the other 90-odd
    still come back. `rf_quality.pipeline` itself has no such isolation: one
    `import pywt` at the top of `features/texture_advanced.py` takes the whole
    module down, and with it 11 stems that need nothing but numpy + skimage.
    """
    from rf_quality._vendor.roi import ROIConfigLite, build_superficial_fat_mask, erode_mask, largest_component

    def _group(name: str, fn):
        """Run one feature group; on failure record it and contribute nothing."""
        try:
            return fn() or {}
        except Exception as e:
            if errors is not None:
                errors.append(f"{name}: {type(e).__name__}: {e}"[:120])
            return {}

    rf_full = largest_component(mask == cfg.mask.muscle_class_id)
    rf_eroded = erode_mask(rf_full, cfg.mask.erosion_px)
    roi_cfg = ROIConfigLite(muscle_class_id=cfg.mask.muscle_class_id,
                            fascia_class_id=cfg.mask.fascia_class_id,
                            skin_class_id=cfg.mask.skin_class_id,
                            fat_class_id=cfg.mask.fat_class_id,
                            erosion_px=cfg.mask.erosion_px)
    fat_mask = build_superficial_fat_mask(mask, cfg=roi_cfg, muscle_mask=rf_full)
    yc = cfg.features.young_correction
    feats: dict = {}

    def _geometry():
        from rf_quality.features.geometry import compute_geometry
        return compute_geometry(rf_full, rf_eroded, spacing.y_mm_per_px, spacing.x_mm_per_px)

    def _intensity():
        from rf_quality.features.intensity import compute_intensity
        return compute_intensity(img_u8, rf_eroded)

    def _hyper():
        from rf_quality.features.hyperechoic import compute_hyperechoic
        return compute_hyperechoic(img_u8, rf_eroded)

    def _glcm():
        from rf_quality.features.glcm import compute_glcm
        return compute_glcm(img_u8, rf_eroded, levels=cfg.features.glcm_levels,
                            min_pairs=cfg.features.band_min_pairs)

    def _bands():
        from rf_quality.features.depth_bands import compute_depth_bands
        return compute_depth_bands(img_u8, rf_eroded)

    def _orient():
        from rf_quality.features.orientation import compute_orientation
        return compute_orientation(img_u8, rf_eroded)

    def _fat():
        from rf_quality.features.fat import compute_fat
        return compute_fat(img_u8, rf_full, fat_mask, feats.get("int_mean"), spacing.y_mm_per_px)

    def _baseline():
        from rf_quality.features.baseline_geometry import compute_baseline_geometry
        return compute_baseline_geometry(pred_mask=mask, skin_class_id=cfg.mask.skin_class_id,
                                         fascia_class_id=cfg.mask.fascia_class_id,
                                         pixel_spacing_y_mm_per_px=spacing.y_mm_per_px)

    def _radiomics():
        from rf_quality.features.radiomics_matrices import compute_radiomics_matrices
        return compute_radiomics_matrices(img_u8=img_u8, mask=rf_eroded,
                                          levels=cfg.features.glcm_levels)

    def _advanced():
        from rf_quality.features.texture_advanced import compute_advanced_texture
        return compute_advanced_texture(img_u8=img_u8, mask=rf_eroded,
                                        pixel_spacing_y_mm_per_px=spacing.y_mm_per_px,
                                        pixel_spacing_x_mm_per_px=spacing.x_mm_per_px,
                                        levels=cfg.features.glcm_levels)

    def _fascia():
        from rf_quality.features.fascia_split import compute_fascia_split
        return compute_fascia_split(pred_mask=mask, fascia_class_id=cfg.mask.fascia_class_id,
                                    muscle_class_id=cfg.mask.muscle_class_id,
                                    pixel_spacing_y_mm_per_px=spacing.y_mm_per_px)

    def _t200_corrected():
        from rf_quality.features.hyperechoic import compute_hyperechoic_fat_corrected_t200
        return compute_hyperechoic_fat_corrected_t200(
            img_u8=img_u8, mask=rf_eroded, fat_thickness_mm=feats.get("fat_thickness_mm"),
            literature_cf_per_cm=yc.literature_cf_per_cm,
            cohort_ei_per_fat_mm_slope=yc.cohort_ei_per_fat_mm_slope)

    def _young():
        # Imported from the module that defines it — but `rf_quality.pipeline` pulls
        # in texture_advanced (and so pywt) at import time. Import the leaf helpers
        # instead so the Young cascade survives a texture-family dependency gap.
        from rf_quality.features.young_correction import (
            YOUNG_CORRECTED_STEMS_FIRST_ORDER, corrected_cv_within, corrected_ratio,
            corrected_value, young_shift_lit, young_shift_refit)
        ft = feats.get("fat_thickness_mm")
        s_lit = young_shift_lit(ft, yc.literature_cf_per_cm)
        s_ref = young_shift_refit(ft, yc.cohort_ei_per_fat_mm_slope)
        out: dict = {}
        for stem in YOUNG_CORRECTED_STEMS_FIRST_ORDER:
            out[f"{stem}_fat_corrected_young_lit"] = corrected_value(feats.get(stem), s_lit)
            out[f"{stem}_fat_corrected_young_refit"] = corrected_value(feats.get(stem), s_ref)
        for suffix, sh in (("lit", s_lit), ("refit", s_ref)):
            out[f"ei_to_fat_ratio_fat_corrected_young_{suffix}"] = corrected_ratio(
                feats.get("int_mean"), feats.get("fat_int_mean"), sh, shift_den=None)
            out[f"int_superficial_to_deep_ratio_fat_corrected_young_{suffix}"] = corrected_ratio(
                feats.get("superficial_int_mean"), feats.get("deep_int_mean"), sh, shift_den=sh)
            out[f"int_cv_within_fat_corrected_young_{suffix}"] = corrected_cv_within(
                feats.get("int_mean"), feats.get("int_std"), sh)
        return out

    # Order matters: fat needs int_mean; the corrected siblings need fat_thickness_mm.
    for name, fn in (("geometry", _geometry), ("intensity", _intensity), ("hyperechoic", _hyper),
                     ("glcm", _glcm), ("depth_bands", _bands), ("orientation", _orient),
                     ("fat", _fat), ("baseline_geometry", _baseline),
                     ("radiomics_matrices", _radiomics), ("texture_advanced", _advanced),
                     ("fascia_split", _fascia), ("hyperechoic_t200_corrected", _t200_corrected),
                     ("young_cascade", _young)):
        feats.update(_group(name, fn))

    def _gate():
        from rf_quality.qc.plausibility import gate_plausibility
        gated, _violations = gate_plausibility(feats)
        return gated
    gated = _group("plausibility_gate", _gate)
    return gated or feats


def _anchor_stems(mask: np.ndarray, final_u8: np.ndarray, spacing_row: dict) -> dict:
    """The three family=anchor stems, computed natively by the labeling app's own
    per-frame path (the family's definition). Degrades to None on any failure."""
    out = {k: None for k in ANCHOR_STEM_TO_MM_KEY}
    try:
        from preflight_helpers.landmark import measure_frame_geometry
        ff = measure_frame_geometry(mask, final_u8, spacing_row)
        for stem, mm_key in ANCHOR_STEM_TO_MM_KEY.items():
            out[stem] = ff.get(mm_key)
    except Exception:
        pass
    return out


# --------------------------------------------------------------------------- #
# Frame inventory
# --------------------------------------------------------------------------- #
_FRAME_INDEX: Optional[dict] = None


def _frame_index() -> dict:
    """{(study_uid, role): (raw_image_dir, [frame_base, ...])} from qa_frames.csv.

    PHI: `rel_jpg_path` embeds the raw patient-name folder. It is used only to
    resolve pixels on disk — never returned, printed, or cached.
    """
    global _FRAME_INDEX
    if _FRAME_INDEX is not None:
        return _FRAME_INDEX
    import pandas as pd
    df = pd.read_csv(QA_FRAMES, dtype={"frame_base": str})
    idx: dict = {}
    for (uid, role), grp in df.groupby(["study_uid", "role"]):
        raw_dir = STUDIES_ROOT / Path(grp["rel_jpg_path"].iloc[0]).parent
        idx[(uid, role)] = (raw_dir, sorted(grp["frame_base"].astype(str).tolist()))
    _FRAME_INDEX = idx
    return idx


def available_captures() -> list[tuple[str, str]]:
    """Every (study_uid, role) this module can compute. Helper, not the contract."""
    return sorted(_frame_index().keys())


# --------------------------------------------------------------------------- #
# THE public function
# --------------------------------------------------------------------------- #
def compute_capture_features(study_uid: str, role: str, *, k: int = LANDMARK_K,
                             refresh: bool = False, record_out: Optional[dict] = None) -> dict:
    """Registry stem -> value for one capture, medianed over its top-k largest-RF frames.

    Returns EVERY registry stem; a stem that could not be computed is None (never
    raised, never coerced to 0). Idempotent — the second call for the same
    (study_uid, role, k, bundle) reads the cache and touches no GPU.

    `record_out` (optional out-param, mirrors `feature_routing.compute_modality_aggregate`'s
    `frames_out`): populated with the provenance — frames used, per-stem valid
    counts, timings, the bundle, the spacing source.
    """
    if role not in ROLES:
        raise ValueError(f"role must be one of {ROLES}, got {role!r}")
    stems = _registry_stems()

    sess = _session()
    cache_path = CACHE_DIR / f"{study_uid}__{role}__k{k}__{sess['bundle_sha8']}.json"
    if cache_path.exists() and not refresh:
        rec = json.loads(cache_path.read_text())
        if record_out is not None:
            record_out.update(rec)
        return {s: rec["values"].get(s) for s in stems}

    rec: dict = {"study_uid": study_uid, "role": role, "k": k,
                 "bundle": sess["bundle_stem"], "bundle_sha8": sess["bundle_sha8"],
                 "n_frames_in_capture": 0, "n_frames_segmented": 0,
                 "landmark_frames": [], "frame_errors": [], "spacing_source": None,
                 "seconds_segment": None, "seconds_features": None, "seconds_total": None,
                 "n_valid": {}, "values": {s: None for s in stems}, "note": ""}

    entry = _frame_index().get((study_uid, role))
    if entry is None:
        rec["note"] = "capture not in qa_frames.csv"
        _write_cache(cache_path, rec, record_out)
        return {s: None for s in stems}

    raw_dir, frame_bases = entry
    rec["n_frames_in_capture"] = len(frame_bases)
    t_all = time.perf_counter()

    # --- pass 1: segment every frame, rank by RF area (derive_new_round_cohort's ranker)
    from preflight_helpers.segmenter import build_voi_hist_clahe_stack, predict_mask, synthesize_final_u8
    t0 = time.perf_counter()
    scored: list[tuple[int, str, np.ndarray, np.ndarray]] = []
    for fb in frame_bases:
        try:
            final = synthesize_final_u8(raw_dir, fb)
            mask = predict_mask(sess["learn"],
                                build_voi_hist_clahe_stack(final, modalities=sess["meta"].modalities),
                                sess["meta"], device=sess["device"])
            area = int((mask == MM_MUSCLE_CLASS).sum())
            if area > 0:
                scored.append((area, fb, mask, final))
        except Exception as e:  # one bad frame never kills the capture
            rec["frame_errors"].append(f"{fb}: {type(e).__name__}")
    rec["seconds_segment"] = round(time.perf_counter() - t0, 3)
    rec["n_frames_segmented"] = len(scored)
    if not scored:
        rec["note"] = "no frame with a non-empty RF mask"
        rec["seconds_total"] = round(time.perf_counter() - t_all, 3)
        _write_cache(cache_path, rec, record_out)
        return {s: None for s in stems}

    scored.sort(key=lambda t: t[0], reverse=True)
    top = scored[:k]
    rec["landmark_frames"] = [fb for _a, fb, _m, _f in top]

    # --- spacing: the capture's own DICOM, rescaled into mask space (mm ruler)
    from preflight_helpers.run_pipeline import _spacing_row_from_dicom
    mask_h, mask_w = top[0][2].shape[:2]
    try:
        spacing_row = _spacing_row_from_dicom(raw_dir.parent, (mask_h, mask_w))
    except Exception as e:
        spacing_row = {}
        rec["note"] = f"no spacing ({type(e).__name__}) — mm-valued stems stay None"
    spacing = _mm_spacing(spacing_row, mask_h, mask_w)
    rec["spacing_source"] = spacing.source
    rec["spacing_mm_per_px"] = {"x": spacing.x_mm_per_px, "y": spacing.y_mm_per_px}

    # --- pass 2: features on the landmark frames only
    cfg = _rf_cfg()
    t0 = time.perf_counter()
    per_frame: list[dict] = []
    for _area, fb, mask, final in top:
        one: dict = {}
        errs: list = []
        try:
            one.update(_frame_stems(final, mask, spacing, cfg, errors=errs))
        except Exception as e:
            errs.append(f"frame: {type(e).__name__}: {e}"[:120])
        for msg in errs:
            rec["frame_errors"].append(f"{fb}: {msg}")
        one.update(_anchor_stems(mask, final, dict(spacing_row, mask_height=mask_h, mask_width=mask_w)))
        per_frame.append(one)
    rec["seconds_features"] = round(time.perf_counter() - t0, 3)

    # --- median per stem over the landmark frames
    for s in stems:
        vals = [v for v in (f.get(s) for f in per_frame)
                if v is not None and isinstance(v, (int, float, np.floating, np.integer))
                and not (isinstance(v, float) and np.isnan(v))]
        rec["n_valid"][s] = len(vals)
        rec["values"][s] = float(median(vals)) if vals else None

    rec["seconds_total"] = round(time.perf_counter() - t_all, 3)
    _write_cache(cache_path, rec, record_out)
    return dict(rec["values"])


def _write_cache(path: Path, rec: dict, record_out: Optional[dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, indent=1, default=float))
    tmp.replace(path)
    if record_out is not None:
        record_out.update(rec)


# --------------------------------------------------------------------------- #
# CLI — proof run
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("study_uid")
    ap.add_argument("role", choices=ROLES)
    ap.add_argument("--refresh", action="store_true")
    a = ap.parse_args()
    r: dict = {}
    v = compute_capture_features(a.study_uid, a.role, refresh=a.refresh, record_out=r)
    n_ok = sum(1 for x in v.values() if x is not None)
    print(f"{a.study_uid} {a.role}: {n_ok}/{len(v)} stems  "
          f"frames={r['n_frames_segmented']}/{r['n_frames_in_capture']}  "
          f"t={r['seconds_total']}s (seg {r['seconds_segment']}s)")
    for s, x in v.items():
        print(f"  {s:56s} {'None' if x is None else f'{x:.6g}'}")

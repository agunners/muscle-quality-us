"""Main orchestrator — reads data, computes per-frame features, aggregates to
study level, emits all five artifacts under ``runs/<date>/<id>/``.

Five artifacts:
  1. ``frame_features.csv``       — one row per exported frame (+ QC flags + per-stem features)
  2. ``study_features.csv``       — one row per study with repr/agg columns
  3. ``decision_log_enriched.csv`` — all 960 rows preserved; derived cols null on QF
  4. ``analysis_manifest.csv``     — one row per latest (study_code, operator_id) with track flags
  5. ``feature_dictionary.csv``    — mechanical from the registry
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
from PIL import Image

from .catalog import write_feature_dictionary
from .config import AppCfg
from .features.anchor import ANCHOR_COLUMN_MAP, take_anchor_values
from .features.baseline_geometry import compute_baseline_geometry
from .features.depth_bands import compute_depth_bands
from .features.fascia_split import compute_fascia_split
from .features.fat import compute_fat
from .features.geometry import compute_geometry
from .features.glcm import compute_glcm
from .features.hyperechoic import compute_hyperechoic, compute_hyperechoic_fat_corrected_t200
from .features.intensity import compute_intensity
from .features.orientation import compute_orientation
from .features.radiomics_matrices import compute_radiomics_matrices
from .features.texture_advanced import compute_advanced_texture
from .features.young_correction import (
    YOUNG_CORRECTED_STEMS_FIRST_ORDER,
    YOUNG_CORRECTED_STEMS_RATIO_OF_MOMENTS,
    YOUNG_CORRECTED_STEMS_RATIOS,
    corrected_cv_within,
    corrected_ratio,
    corrected_value,
    young_shift_lit,
    young_shift_refit,
)
from .frames.aggregation import aggregate_study
from .io.decision_log import (
    akadir_quality_failed_studies,
    build_analysis_manifest,
    build_study_targets,
    latest_rows,
    load_decision_log,
    raw_counts,
)
from .io.spacing import resolve_spacing
from .io.transport import TransportIndex, load_transport_index
from .qc.image import score_image
from .qc.mask_discrete import score_mask
from .qc.mask_logit import LOGIT_COLUMNS, load_cache
from .qc.plausibility import gate_plausibility
from .registry import REGISTRY, Family, stems_by_family
from .run_artifacts import RunHandle, append_summary, write_metrics
from ._vendor.roi import ROIConfigLite, build_superficial_fat_mask, erode_mask, largest_component


# --------- low-level loaders ---------

def _load_u8_gray(path: Path) -> np.ndarray:
    img = Image.open(path)
    if img.mode != "L":
        img = img.convert("L")
    return np.asarray(img, dtype=np.uint8)


def _load_mask(path: Path) -> np.ndarray:
    img = Image.open(path)
    if img.mode != "L":
        img = img.convert("L")
    return np.asarray(img, dtype=np.uint8)


def _resize_to_mask(img: np.ndarray, mask_h: int, mask_w: int) -> np.ndarray:
    if img.shape == (mask_h, mask_w):
        return img
    im = Image.fromarray(img)
    im = im.resize((mask_w, mask_h), Image.BILINEAR)
    return np.asarray(im, dtype=np.uint8)


# --------- per-frame compute ---------


def _compute_young_corrected_siblings(feats: dict, yc_cfg) -> dict[str, float | None]:
    """Emit Young-corrected siblings for first-order moments + ratios.

    Inputs are read from ``feats`` (the per-frame feature dict already populated
    with raw stems). The shifts are computed from ``feats['fat_thickness_mm']``
    and ``yc_cfg.literature_cf_per_cm`` / ``yc_cfg.cohort_ei_per_fat_mm_slope``.

    Returns a new dict with keys ``<stem>_fat_corrected_young_lit`` and
    ``<stem>_fat_corrected_young_refit`` for every stem in
    ``YOUNG_CORRECTED_STEMS_ALL``. Values are ``None`` when fat_thickness_mm
    is missing or, for the refit variant, when ``cohort_ei_per_fat_mm_slope``
    is null. The ``_young_refit`` suffix is preserved across the round-2
    unit-honest rename (see ``YoungCorrectionCfg`` docstring).
    """
    fat_t_mm = feats.get("fat_thickness_mm")
    shift_lit = young_shift_lit(fat_t_mm, yc_cfg.literature_cf_per_cm)
    shift_refit = young_shift_refit(fat_t_mm, yc_cfg.cohort_ei_per_fat_mm_slope)

    out: dict[str, float | None] = {}
    for stem in YOUNG_CORRECTED_STEMS_FIRST_ORDER:
        raw = feats.get(stem)
        out[f"{stem}_fat_corrected_young_lit"] = corrected_value(raw, shift_lit)
        out[f"{stem}_fat_corrected_young_refit"] = corrected_value(raw, shift_refit)

    # ratios — Young's correction is for muscle EI only, so the fat side is
    # NOT shifted in ei_to_fat_ratio. For sup/deep, BOTH sides are muscle so
    # both get the same shift (and the ratio still changes because (s+c)/(d+c) ≠ s/d).
    out["ei_to_fat_ratio_fat_corrected_young_lit"] = corrected_ratio(
        feats.get("int_mean"), feats.get("fat_int_mean"), shift_lit, shift_den=None,
    )
    out["ei_to_fat_ratio_fat_corrected_young_refit"] = corrected_ratio(
        feats.get("int_mean"), feats.get("fat_int_mean"), shift_refit, shift_den=None,
    )
    out["int_superficial_to_deep_ratio_fat_corrected_young_lit"] = corrected_ratio(
        feats.get("superficial_int_mean"), feats.get("deep_int_mean"),
        shift_lit, shift_den=shift_lit,
    )
    out["int_superficial_to_deep_ratio_fat_corrected_young_refit"] = corrected_ratio(
        feats.get("superficial_int_mean"), feats.get("deep_int_mean"),
        shift_refit, shift_den=shift_refit,
    )

    # int_cv_within — ratio of moments; corrected via component-wise propagation.
    # The dimensionally-correct Young-corrected CV is `std / |mean + shift|`,
    # NOT `(std/|mean|) + shift` (which would add an EI-units quantity to a
    # dimensionless ratio). Siblings keep their canonical names — the in-place
    # edit is appropriate for experimental sibling stems. The 2026-04-30
    # cascade-rule fix replaces the additive-shift formula with the component-
    # wise formula under the same column names.
    # See `young_correction.corrected_cv_within` and `feature_changelog.md`.
    mean = feats.get("int_mean")
    std = feats.get("int_std")
    out["int_cv_within_fat_corrected_young_lit"] = corrected_cv_within(mean, std, shift_lit)
    out["int_cv_within_fat_corrected_young_refit"] = corrected_cv_within(mean, std, shift_refit)
    return out


def compute_frame(
    study_code: str,
    frame_base: str,
    modality: str,
    cfg: AppCfg,
    transport: TransportIndex,
    logit_cache,
    is_anchor: bool,
) -> dict | None:
    frame = transport.resolve_frame(study_code, frame_base, modality)
    if frame is None:
        return None
    try:
        img_u8 = _load_u8_gray(frame.image_path)
        mask = _load_mask(frame.mask_path)
    except FileNotFoundError:
        return None

    img_u8 = _resize_to_mask(img_u8, frame.mask_height, frame.mask_width)
    spacing = resolve_spacing(
        pixel_spacing_x=frame.pixel_spacing_x,
        pixel_spacing_y=frame.pixel_spacing_y,
        dcm_rows=frame.dcm_rows,
        dcm_cols=frame.dcm_cols,
        jpg_height=frame.jpg_height,
        jpg_width=frame.jpg_width,
        mask_height=frame.mask_height,
        mask_width=frame.mask_width,
    )

    # --- Tier-1 image QC (saturation/entropy computed on in-scan ROI) ---
    image_qc = score_image(img_u8, mask, cfg.qc)

    # --- discrete-mask derivations ---
    mask_qc = score_mask(mask, img_u8, cfg.mask, cfg.qc)
    rf_full = largest_component(mask == cfg.mask.muscle_class_id)
    rf_eroded = erode_mask(rf_full, cfg.mask.erosion_px)

    roi_cfg = ROIConfigLite(
        muscle_class_id=cfg.mask.muscle_class_id,
        fascia_class_id=cfg.mask.fascia_class_id,
        skin_class_id=cfg.mask.skin_class_id,
        fat_class_id=cfg.mask.fat_class_id,
        erosion_px=cfg.mask.erosion_px,
    )
    fat_mask = build_superficial_fat_mask(mask, cfg=roi_cfg, muscle_mask=rf_full)

    # --- features ---
    feats: dict[str, float | None] = {}
    feats.update(compute_geometry(rf_full, rf_eroded, spacing.y_mm_per_px, spacing.x_mm_per_px))
    intensity = compute_intensity(img_u8, rf_eroded)
    feats.update(intensity)
    feats.update(compute_hyperechoic(img_u8, rf_eroded))
    feats.update(compute_glcm(img_u8, rf_eroded, levels=cfg.features.glcm_levels, min_pairs=cfg.features.band_min_pairs))
    feats.update(compute_depth_bands(img_u8, rf_eroded))
    feats.update(compute_orientation(img_u8, rf_eroded))
    feats.update(compute_fat(img_u8, rf_full, fat_mask, intensity.get("int_mean"), spacing.y_mm_per_px))
    feats.update(compute_baseline_geometry(
        pred_mask=mask,
        skin_class_id=cfg.mask.skin_class_id,
        fascia_class_id=cfg.mask.fascia_class_id,
        pixel_spacing_y_mm_per_px=spacing.y_mm_per_px,
    ))
    # Round-2 (2026-05-02): radiomics matrix families (GLRLM/GLSZM/NGTDM/GLDM),
    # advanced texture (LBP/multi-Otsu/bright-speckle/blob/local-entropy/wavelet/
    # fractal-dim/Sato-sheetness/Gabor/Dubois-TAI), and fascia decomposition
    # (superficial/deep). Hand-rolled IBSI-aligned implementations
    # (PyRadiomics broken on py3.12 — see radiomics_matrices module docstring).
    feats.update(compute_radiomics_matrices(
        img_u8=img_u8, mask=rf_eroded, levels=cfg.features.glcm_levels,
    ))
    feats.update(compute_advanced_texture(
        img_u8=img_u8, mask=rf_eroded,
        pixel_spacing_y_mm_per_px=spacing.y_mm_per_px,
        pixel_spacing_x_mm_per_px=spacing.x_mm_per_px,
        levels=cfg.features.glcm_levels,
    ))
    feats.update(compute_fascia_split(
        pred_mask=mask,
        fascia_class_id=cfg.mask.fascia_class_id,
        muscle_class_id=cfg.mask.muscle_class_id,
        pixel_spacing_y_mm_per_px=spacing.y_mm_per_px,
    ))
    # Young-corrected hyperechoic_frac_t200 siblings — per-frame threshold shift.
    # Must run after compute_fat (needs fat_thickness_mm) but before the cascade
    # (the cascade no longer handles hyperechoic_frac_t200 since the additive
    # form is dimensionally invalid for fractions; see young_correction module
    # docstring). Cascade-rule fix shipped 2026-04-30 (in-place edit).
    feats.update(compute_hyperechoic_fat_corrected_t200(
        img_u8=img_u8,
        mask=rf_eroded,
        fat_thickness_mm=feats.get("fat_thickness_mm"),
        literature_cf_per_cm=cfg.features.young_correction.literature_cf_per_cm,
        cohort_ei_per_fat_mm_slope=cfg.features.young_correction.cohort_ei_per_fat_mm_slope,
    ))
    feats.update(_compute_young_corrected_siblings(feats, cfg.features.young_correction))
    feats, plausibility_violations = gate_plausibility(feats)

    # --- logit cache ---
    logit_row = logit_cache.lookup(study_code, frame_base, modality)

    # --- QC pass ---
    frame_pass = (
        not mask_qc.hard_fail
        and not mask_qc.flag_low_retained
        and not mask_qc.flag_touches_top
        and not mask_qc.flag_touches_bottom
        and not image_qc.flag_saturation
    )

    row: dict[str, float | int | bool | None | str] = {
        "study_code": study_code,
        "frame_base": frame_base,
        "modality": modality,
        "is_anchor": bool(is_anchor),
        "mask_height": frame.mask_height,
        "mask_width": frame.mask_width,
        "spacing_complete": bool(spacing.complete),
        "spacing_source": spacing.source,
        "spacing_x_mm_per_px": spacing.x_mm_per_px,
        "spacing_y_mm_per_px": spacing.y_mm_per_px,
        # Tier-1 image QC
        "qc_focus_laplacian_var": image_qc.focus_laplacian_var,
        "qc_saturation_ratio": image_qc.saturation_ratio,
        "qc_global_entropy": image_qc.global_entropy,
        "qc_flag_focus": image_qc.flag_focus,
        "qc_flag_saturation": image_qc.flag_saturation,
        "qc_flag_entropy": image_qc.flag_entropy,
        # Tier-2A discrete mask QC
        "qc_rf_full_area_px": mask_qc.rf_full_area_px,
        "qc_rf_components": mask_qc.rf_full_components,
        "qc_top1_frac": mask_qc.top1_frac,
        "qc_rf_analysis_area_px": mask_qc.rf_analysis_area_px,
        "qc_area_retained_fraction": mask_qc.area_retained_fraction,
        "qc_mask_elongation": mask_qc.mask_elongation,
        "qc_fascia_support_area_px": mask_qc.fascia_support_area_px,
        "qc_rf_touches_top": mask_qc.rf_touches_top_row,
        "qc_rf_touches_bottom": mask_qc.rf_touches_bottom_row,
        "qc_fascia_continuity_frac": mask_qc.fascia_continuity_frac,
        "qc_boundary_gradient_coherence": mask_qc.boundary_gradient_coherence,
        "qc_flag_small": mask_qc.flag_small,
        "qc_flag_small_after_erosion": mask_qc.flag_small_after_erosion,
        "qc_flag_fragmented": mask_qc.flag_fragmented,
        "qc_flag_low_retained": mask_qc.flag_low_retained,
        "qc_flag_elongation": mask_qc.flag_elongation,
        "qc_flag_low_fascia_support": mask_qc.flag_low_fascia_support,
        "qc_flag_fascia_discontinuity": mask_qc.flag_fascia_discontinuity,
        "frame_qc_pass": frame_pass,
        "plausibility_violation_count": int(len(plausibility_violations)),
    }
    # Tier-2B logit-cache fields (always present; null if cache absent)
    for col in LOGIT_COLUMNS:
        row[col] = logit_row.get(col)
    row.update(feats)
    return row


# --------- per-study aggregation ---------

def aggregate_into_study_row(frames_df: pd.DataFrame, anchor_frame_base: str, modality: str) -> dict:
    sub = frames_df[(frames_df["modality"] == modality)]
    out = aggregate_study(sub, anchor_frame_base=anchor_frame_base, modality=modality)
    out["frames_total"] = int(len(sub))
    out["frames_passing_qc"] = int(sub["frame_qc_pass"].astype(bool).sum())
    out["anchor_frame_base"] = anchor_frame_base
    return out


# --------- enrichment helpers ---------

def enrich_decision_log(decision_log: pd.DataFrame, study_features: pd.DataFrame, modality: str) -> pd.DataFrame:
    out = decision_log.copy()
    sf = study_features.set_index("study_code")
    feature_cols = [c for c in study_features.columns if c != "study_code"]
    # broadcast study-level features to every row of that study
    joined = out.join(sf[feature_cols], on="study_code", how="left")
    # force-null derived features on quality_failed rows (preserve row count & targets)
    qf_mask = joined["decision_status"] == "quality_failed"
    feature_cols_present = [c for c in feature_cols if c in joined.columns]
    joined.loc[qf_mask, feature_cols_present] = np.nan
    return joined


# --------- the infer entrypoint ---------

def run_infer(
    cfg: AppCfg,
    run: RunHandle,
    study_filter: list[str] | None = None,
) -> dict:
    t0 = time.time()

    decision_log = load_decision_log(cfg.paths.decision_log)
    counts = raw_counts(decision_log)
    latest = latest_rows(decision_log)
    excluded = akadir_quality_failed_studies(latest)
    targets = build_study_targets(latest, excluded)
    manifest = build_analysis_manifest(latest, targets, excluded)

    transport = load_transport_index(
        cfg.paths.transport_metadata, cfg.paths.study_codes, cfg.paths.transport_root
    )
    logit_cache = load_cache(cfg.paths.seg_qc_cache)

    # studies to process = completed studies (not QF) that have transport frames
    completed_latest = latest[latest["decision_status"] != "quality_failed"]
    study_candidates = completed_latest["study_code"].unique().tolist()
    if study_filter:
        study_candidates = [s for s in study_candidates if s in set(study_filter)]
    # deterministic order, honouring smoke budget if caller asked for it
    study_candidates = sorted(study_candidates)

    modality = cfg.features.modality_primary
    frame_rows: list[dict] = []
    study_rows: list[dict] = []
    missing_anchor: list[str] = []

    for study in study_candidates:
        if not transport.has_study(study):
            missing_anchor.append(f"{study}: not in transport")
            continue
        frames_avail = transport.frame_bases_for_study(study)
        # anchor-by-definition: use center_frame_base from any latest completed row
        anchor = (
            completed_latest[completed_latest["study_code"] == study]["center_frame_base"]
            .dropna()
            .astype(str)
            .iloc[0]
            if (completed_latest["study_code"] == study).any()
            else None
        )
        if anchor is None or anchor not in frames_avail:
            missing_anchor.append(f"{study}: anchor={anchor} not in transport frames")
            continue

        frame_dfs: list[dict] = []
        for fb in frames_avail:
            row = compute_frame(
                study_code=study,
                frame_base=fb,
                modality=modality,
                cfg=cfg,
                transport=transport,
                logit_cache=logit_cache,
                is_anchor=(fb == anchor),
            )
            if row is None:
                continue
            frame_rows.append(row)
            frame_dfs.append(row)
        if not frame_dfs:
            continue

        frame_study_df = pd.DataFrame(frame_dfs)
        study_row = {"study_code": study, "modality": modality}
        study_row.update(aggregate_into_study_row(frame_study_df, anchor_frame_base=anchor, modality=modality))

        # --- anchor continuity passthrough from decision_log ---
        any_row = completed_latest[completed_latest["study_code"] == study].iloc[0]
        for stem, val in take_anchor_values(any_row).items():
            study_row[f"{modality}_rf_{stem}_repr"] = val
        study_rows.append(study_row)

    frame_features = pd.DataFrame(frame_rows)
    study_features = pd.DataFrame(study_rows)

    # --- enrich decision log & write artifacts ---
    decision_log_enriched = (
        enrich_decision_log(decision_log, study_features, modality)
        if not study_features.empty
        else decision_log.copy()
    )

    frame_features.to_csv(run.root / "frame_features.csv", index=False)
    study_features.to_csv(run.root / "study_features.csv", index=False)
    decision_log_enriched.to_csv(run.root / "decision_log_enriched.csv", index=False)
    manifest.to_csv(run.root / "analysis_manifest.csv", index=False)
    write_feature_dictionary(run.root / "feature_dictionary.csv")

    duration = time.time() - t0

    metrics = {
        "counts_raw": counts.__dict__,
        "studies_attempted": len(study_candidates),
        "studies_with_features": int(len(study_features)),
        "frames_processed": int(len(frame_features)),
        "frames_passing_qc": int(frame_features["frame_qc_pass"].astype(bool).sum()) if not frame_features.empty else 0,
        "akadir_quality_failed_studies": len(excluded),
        "track_a_latest_rows": int(manifest["track_a_use"].sum()),
        "track_b_latest_rows": int(manifest["track_b_use"].sum()),
        "track_a_studies": int(manifest.loc[manifest["track_a_use"], "study_code"].nunique()),
        "track_b_studies": int(manifest.loc[manifest["track_b_use"], "study_code"].nunique()),
        "missing_anchor_count": len(missing_anchor),
        "modality": modality,
    }
    write_metrics(run, metrics, duration_sec=duration)
    append_summary(run, f"modality_primary: {modality}")
    append_summary(run, f"studies_processed: {len(study_features)} / {len(study_candidates)}")
    append_summary(run, f"akadir_quality_failed_excluded: {len(excluded)}")
    append_summary(run, f"track_A_studies: {metrics['track_a_studies']}")
    append_summary(run, f"track_B_studies: {metrics['track_b_studies']}")
    if missing_anchor:
        (run.root / "missing_anchor.txt").write_text("\n".join(missing_anchor))

    return metrics

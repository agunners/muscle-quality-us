"""Per-frame feature compute: labelling-app metrics + white-paper features.

Each feature module picks its own mask source:
  * labelling-app metrics (rf + qmt thickness, rf area, muscle_fat_ratio,
    star_ratio): compute_all_metrics() takes the **multi-class mask**
    (queries LABEL_RF, LABEL_VI, LABEL_FAT internally — no erosion). VI is
    still read internally (QMT spans the RF+VI stack, muscle_fat_ratio uses
    VI area) but standalone VI features are no longer propagated — the VI
    segmentation is not robust enough to surface.
  * white-paper geometry (full_thickness_q50_mm, full_area_mm2):
    compute_geometry(rf_full, rf_eroded). The q50 in the name refers to
    "full-thickness fascia-to-fascia protocol"; the INPUT for the q50
    computation is the ERODED mask, per rf_quality/features/geometry.py:26.
  * white-paper intensity (int_cv_within and friends): compute_intensity()
    on the ERODED mask, with the image read from the "final" modality
    (which canonically resolves to `{base}_final.png`, NOT the raw .jpg
    — the plan's wording diverges from the code; we follow the code so
    cohort numbers can be reproduced).

Provenance is recorded per-feature in the returned `_provenance` sub-dict.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

# Bridged via env_bootstrap — assumes sys.path already contains the bridge dirs.
from rf_quality.features.geometry import compute_geometry
from rf_quality.features.intensity import compute_intensity
from rf_quality.io.spacing import resolve_spacing

from .segmenter import LABEL_RF

# Pin the `labeling_app` package to THIS repo's copy (Build-Step 2 Phase-3
# prerequisite). feature_compute.py lives at
# muscle_mania/scripts/preflight_helpers/; parents[2] is the muscle_mania repo
# root. Ensuring it is on sys.path makes every `labeling_app.*` import resolve
# to the local copy regardless of how the process was launched. Before this,
# `_measurements_module()` inserted a *legacy* `labeling_app` copy onto
# sys.path; once Build-Step 2 Phase 3 wires in `labeling_app.image_quality`
# (whose QA symbols exist only in the local copy) that shadowing would break
# the import. See `_measurements_module` below.
_MM_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _MM_REPO_ROOT not in sys.path:
    sys.path.insert(0, _MM_REPO_ROOT)

# The RF image-quality QA layer (Build-Step 2). Imported here, after the
# repo-root pin, so they resolve to the local labeling_app copy.
from labeling_app.image_quality import (  # noqa: E402
    calibrated_flags, compute_image_quality_flags,
    detect_segmentation_low_confidence, detect_subq_fat_compressed,
    image_quality_aggregate_tier_from_tiers, image_quality_tier_per_measurement,
    qmt_provenance_capped_tiers,
)
from labeling_app.measurements import (  # noqa: E402
    CONTRACT_VERSION, canonical_image_quality_flag_order,
)


SpacingRow = dict
# Required keys:  pixel_spacing_x, pixel_spacing_y, dcm_rows, dcm_cols,
#                 jpg_height, jpg_width, mask_height, mask_width, bmi.
# IMPORTANT: dcm_cols is required by rf_quality.io.spacing.resolve_spacing for
# the canonical DICOM-derived spacing rescale (used by white-paper geometry).
# It is NOT in decision_log.csv; pull it from transport_metadata.csv. The
# labelling-app side (px_to_mm) doesn't need dcm_cols (only dcm_rows for y).


def _load_u8_gray(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("L"), dtype=np.uint8)


def _resize_to_mask(img: np.ndarray, mask_h: int, mask_w: int) -> np.ndarray:
    """Nearest resize for grayscale image to mask resolution (white-paper convention)."""
    if img.shape == (mask_h, mask_w):
        return img
    pil = Image.fromarray(img).resize((mask_w, mask_h), Image.BILINEAR)
    return np.asarray(pil, dtype=np.uint8)


def _measurements_module():
    """Import the labelling-app measurements module — pinned to THIS repo's
    `muscle_mania/labeling_app` copy (Build-Step 2 Phase-3 prerequisite).

    Until Phase 3 this function `sys.path.insert`-ed a *separate, legacy*
    `labeling_app` copy (`muscle_quality_labeling_exercise_claude_PROMPTED`)
    ahead of this repo and imported `measurements` from it. The two
    `measurements.py` have since diverged: the local copy carries the
    Build-Step 1/2 QA additions (`IMAGE_QUALITY_FLAG_PRIORITY`,
    `CONTRACT_VERSION`, `LABEL_VL/VM/SKIN`, `canonical_image_quality_flag_order`,
    `frame_qmt_validity_flags`) the legacy copy lacks — and the legacy package
    has no `image_quality.py` / `qa_thresholds.py` at all. Pinning the legacy
    path ahead of this repo would shadow `labeling_app`, so Phase 3's
    `labeling_app.image_quality` import would resolve against the legacy
    package and fail. The repo-root sys.path pin at module top guarantees
    `labeling_app` resolves here.

    The pin is safe because the divergence is purely additive — `compute_all_metrics`
    and every measurement function are byte-identical between the two copies;
    `tests/preflight/test_feature_compute.py::test_local_legacy_measurements_compute_all_metrics_identical`
    is the standing gate that proves it and fails loud if they ever diverge.
    """
    from labeling_app import measurements  # noqa: WPS433
    return measurements


@dataclass(frozen=True)
class FrameFeatures:
    """Per-frame feature payload + provenance."""
    frame_base: str
    is_anchor: bool
    # Labelling-app block — raw multi-class mask, no erosion.
    rf_thickness_mm: Optional[float]
    qmt_mm: Optional[float]
    visible_rf_area_mm2: Optional[float]
    muscle_fat_ratio: Optional[float]
    star_ratio: Optional[float]
    rf_echo: Optional[float]
    # QMT measurement provenance. 'standard_qmt' is the real perpendicular
    # fascia-to-femur measurement; 'femur_supported_proxy' / 'no_femur_proxy' /
    # 'no_measurement' are degraded fallbacks (a crude vertical RF+VI span at
    # image centre) that land ~half the standard value — downstream aggregation
    # must NOT pool a proxy QMT with a standard QMT.
    qmt_provenance: Optional[str]
    qmt_guidance_status: Optional[str]
    qmt_validity_flags: tuple[str, ...]
    # White-paper block — eroded mask geometry + intensity on _final.png.
    full_area_mm2: Optional[float]
    full_thickness_q50_mm: Optional[float]
    full_max_thickness_mm: Optional[float]
    area_retained_fraction_after_erosion: Optional[float]
    int_mean: Optional[float]
    int_std: Optional[float]
    int_cv_within: Optional[float]
    int_median: Optional[float]
    int_entropy: Optional[float]
    # QC flags
    touches_edge: bool
    is_empty: bool
    drop_reasons: tuple[str, ...]
    # Per-feature provenance — which mask + which image was used.
    provenance: dict
    # Fat geometry (2026-06-04) — defaulted so any legacy construction site that
    # predates these fields still builds.
    fat_thickness_q50_mm: Optional[float] = None
    fat_thickness_max_mm: Optional[float] = None
    fat_to_depth_q50: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "frame_base": self.frame_base,
            "is_anchor": self.is_anchor,
            "rf_thickness_mm": self.rf_thickness_mm,
            "qmt_mm": self.qmt_mm,
            "visible_rf_area_mm2": self.visible_rf_area_mm2,
            "muscle_fat_ratio": self.muscle_fat_ratio,
            "star_ratio": self.star_ratio,
            "rf_echo": self.rf_echo,
            "qmt_provenance": self.qmt_provenance,
            "qmt_guidance_status": self.qmt_guidance_status,
            "qmt_validity_flags": list(self.qmt_validity_flags),
            "full_area_mm2": self.full_area_mm2,
            "full_thickness_q50_mm": self.full_thickness_q50_mm,
            "full_max_thickness_mm": self.full_max_thickness_mm,
            "area_retained_fraction_after_erosion": self.area_retained_fraction_after_erosion,
            "fat_thickness_q50_mm": self.fat_thickness_q50_mm,
            "fat_thickness_max_mm": self.fat_thickness_max_mm,
            "fat_to_depth_q50": self.fat_to_depth_q50,
            "int_mean": self.int_mean,
            "int_std": self.int_std,
            "int_cv_within": self.int_cv_within,
            "int_median": self.int_median,
            "int_entropy": self.int_entropy,
            "touches_edge": self.touches_edge,
            "is_empty": self.is_empty,
            "drop_reasons": list(self.drop_reasons),
            "_provenance": self.provenance,
        }


@dataclass(frozen=True)
class ImageQualityFeatures:
    """Per-frame image-quality QA grade (RF Image-Quality QA plan § 7.1).

    A *separate* record from `FrameFeatures` — Q3 resolved (plan § 7.1): the QA
    schema versions independently (the `image_quality_contract_version`
    field), churns on a different cadence from the measurement fields, and a
    self-contained QA record is cleaner to audit and to re-grade. It joins to
    the per-frame `FrameFeatures` on `frame_base`.

    Produced by the QA layer — `labeling_app.image_quality` (§ 5.5, § 7.2):
    `compute_image_quality_flags` yields the flags + detector metadata,
    `image_quality_tier_per_measurement` / `image_quality_aggregate_tier` the
    tiers. Build-Step 2 Phase 1 ships this dataclass (the record contract);
    Phase 3 wires the QA compute into the pipeline and persists it into the
    run record.

    Deployed-vs-raw tier semantics (Build-Step 2 item-10a gate H1, ratified
    2026-05-22). `image_quality_aggregate_tier` is the *deployed* verdict —
    § 6.3 applied to the *calibrated* flag subset only (HANDOFF Finding B: an
    un-calibrated Threshold-tier B detector never drives a hard verdict). So a
    frame can read `aggregate_tier = "valid"` while `image_quality_validity_flags`
    visibly carries an un-calibrated flag — `wrong_site`, say, which fires on
    79.8 % of the cohort. `image_quality_pending_calibration_flags` makes that
    explicit: the subset of `image_quality_validity_flags` whose detector is
    still pending calibration — flags that fired and are fully recorded, but are
    deliberately excluded from the verdict. The operator badge renders the
    deployed tier plus, from this field, "+N noticed (pending calibration)";
    Build-Step 2 item 10b turns the field into the operator-reactable
    calibration surface. It is a materialised view — `calibrated_flags()`
    derives the same partition from `validity_flags` + `detector_metadata` —
    persisted so a consumer reads the dual semantic directly, never re-derives.
    """
    frame_base: str                                   # join key to FrameFeatures
    image_quality_validity_flags: tuple[str, ...]      # EVERY flag raised, § 5.1 priority order
    image_quality_pending_calibration_flags: tuple[str, ...]  # H1 — the un-calibrated subset of the above
    image_quality_detector_metadata: dict              # raw per-detector scores — UI rationale + calibration
    image_quality_tier_per_measurement: dict[str, str]  # measurement -> 'valid'|'borderline'|'invalid'
    image_quality_aggregate_tier: str                  # the DEPLOYED verdict — § 6.3 over the calibrated flags
    image_quality_contract_version: str                # the contract version this grade was produced under

    def to_dict(self) -> dict:
        """Plain-dict form for the JSON ledger, joined to the frame record on
        `frame_base` (§ 7.0). Mirrors `FrameFeatures.to_dict`."""
        return {
            "frame_base": self.frame_base,
            "image_quality_validity_flags": list(self.image_quality_validity_flags),
            "image_quality_pending_calibration_flags": list(
                self.image_quality_pending_calibration_flags),
            "image_quality_detector_metadata": self.image_quality_detector_metadata,
            "image_quality_tier_per_measurement": self.image_quality_tier_per_measurement,
            "image_quality_aggregate_tier": self.image_quality_aggregate_tier,
            "image_quality_contract_version": self.image_quality_contract_version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ImageQualityFeatures":
        """Reconstruct from a ledger dict — the inverse of `to_dict`, so a
        persisted QA grade round-trips losslessly."""
        return cls(
            frame_base=d["frame_base"],
            image_quality_validity_flags=tuple(d.get("image_quality_validity_flags") or ()),
            image_quality_pending_calibration_flags=tuple(
                d.get("image_quality_pending_calibration_flags") or ()),
            image_quality_detector_metadata=dict(d.get("image_quality_detector_metadata") or {}),
            image_quality_tier_per_measurement=dict(d.get("image_quality_tier_per_measurement") or {}),
            image_quality_aggregate_tier=d["image_quality_aggregate_tier"],
            image_quality_contract_version=d["image_quality_contract_version"],
        )


def compute_frame_features(
    *,
    frame_base: str,
    is_anchor: bool,
    multiclass_mask: np.ndarray,        # uint8 HxW — annotator or predicted
    all_frame_masks: list[np.ndarray],  # list of multi-class masks (for frame_rf_consistency)
    final_image_path: Optional[Path] = None,
    final_image_u8: Optional[np.ndarray] = None,
    spacing_row: SpacingRow = None,
    rf_mask_pair=None,                  # post_processing.RFMaskPair (full + eroded)
) -> FrameFeatures:
    """Compute all 9+ features for a single frame, returning provenance.

    Image source for intensity features (int_cv_within & friends):
      * If `final_image_u8` is provided, use it directly (live-synthesised
        from the bare .jpg via goruntuler's `_write_final_png` methodology).
      * Else load `final_image_path` (used by stage 1 / annotator-mask path
        which still has transport-side `_final.png` for the cohort 3-frame
        triples).

    Stage 1 (annotator masks): caller passes the annotator multiclass mask.
    Stage 2/3 (predicted): caller passes the segmenter's argmax mask + the
    same live-synthesised `final` the segmenter saw — guarantees the
    intensity input matches the inference input distribution.
    """
    drop = list(rf_mask_pair.drop_reasons)

    # Labelling-app block.
    measurements = _measurements_module()
    # img_gray must be at mask resolution for measurements.echo_intensity to work.
    mh = int(spacing_row.get("mask_height") or multiclass_mask.shape[0])
    mw = int(spacing_row.get("mask_width") or multiclass_mask.shape[1])

    # Resolve the grayscale "final" modality image at mask resolution.
    final_u8: Optional[np.ndarray] = None
    if final_image_u8 is not None:
        final_u8 = final_image_u8
        if final_u8.shape != (mh, mw):
            final_u8 = _resize_to_mask(final_u8, mh, mw)
    elif final_image_path is not None:
        try:
            final_u8 = _load_u8_gray(final_image_path)
            final_u8 = _resize_to_mask(final_u8, mh, mw)
        except FileNotFoundError:
            final_u8 = None
            drop.append(f"final_image_not_found:{final_image_path.name}")
    else:
        drop.append("no_final_image_source_provided")

    lab_metrics = measurements.compute_all_metrics(
        center_mask=multiclass_mask,
        center_img_gray=final_u8,
        all_masks=all_frame_masks,
        spacing_row=spacing_row,
    )

    # White-paper block. The geometry compute expects MASK-SPACE spacing
    # (pixel_spacing rescaled by the DCM-to-mask ratio). Use the canonical
    # resolve_spacing so we agree with the cohort study_features.csv exactly.
    sp = resolve_spacing(
        pixel_spacing_x=spacing_row.get("pixel_spacing_x"),
        pixel_spacing_y=spacing_row.get("pixel_spacing_y"),
        dcm_rows=spacing_row.get("dcm_rows"),
        dcm_cols=spacing_row.get("dcm_cols"),
        jpg_height=spacing_row.get("jpg_height"),
        jpg_width=spacing_row.get("jpg_width"),
        mask_height=spacing_row.get("mask_height"),
        mask_width=spacing_row.get("mask_width"),
    )
    geom = compute_geometry(
        rf_full_mask=rf_mask_pair.rf_full_mask,
        rf_eroded_mask=rf_mask_pair.rf_eroded_mask,
        spacing_y_mm_per_px=sp.y_mm_per_px,
        spacing_x_mm_per_px=sp.x_mm_per_px,
    )
    # Fat geometry — superficial-fat band thickness (q50/max) + the fat-to-(skin-
    # to-femur) per-frame ratio, on the full multiclass mask with mask-space
    # spacing (px_to_mm), the same convention as the RF/QMT measurements.
    from labeling_app.fat_features import compute_fat_geometry
    fat = compute_fat_geometry(multiclass_mask, spacing_row)

    intensity = {"int_mean": None, "int_std": None, "int_cv_within": None,
                 "int_median": None, "int_entropy": None}
    if final_u8 is not None and rf_mask_pair.rf_eroded_mask.sum() > 0:
        # rf_quality expects image at the same resolution as the mask.
        intensity = compute_intensity(final_u8, rf_mask_pair.rf_eroded_mask)

    return FrameFeatures(
        frame_base=frame_base,
        is_anchor=is_anchor,
        # Labelling-app
        rf_thickness_mm=lab_metrics.get("rf_thickness_mm"),
        qmt_mm=lab_metrics.get("qmt_mm"),
        visible_rf_area_mm2=lab_metrics.get("visible_rf_area_mm2"),
        muscle_fat_ratio=lab_metrics.get("muscle_fat_ratio"),
        star_ratio=lab_metrics.get("star_ratio"),
        rf_echo=lab_metrics.get("rf_echo"),
        qmt_provenance=lab_metrics.get("qmt_provenance"),
        qmt_guidance_status=lab_metrics.get("qmt_guidance_status"),
        qmt_validity_flags=tuple(lab_metrics.get("qmt_validity_flags") or ()),
        # White-paper
        full_area_mm2=geom.get("full_area_mm2"),
        full_thickness_q50_mm=geom.get("full_thickness_q50_mm"),
        full_max_thickness_mm=geom.get("full_max_thickness_mm"),
        area_retained_fraction_after_erosion=geom.get("area_retained_fraction_after_erosion"),
        fat_thickness_q50_mm=fat.get("fat_thickness_q50_mm"),
        fat_thickness_max_mm=fat.get("fat_thickness_max_mm"),
        fat_to_depth_q50=fat.get("fat_to_depth_q50"),
        int_mean=intensity.get("int_mean"),
        int_std=intensity.get("int_std"),
        int_cv_within=intensity.get("int_cv_within"),
        int_median=intensity.get("int_median"),
        int_entropy=intensity.get("int_entropy"),
        # QC
        touches_edge=rf_mask_pair.full_touches_edge,
        is_empty=rf_mask_pair.is_empty,
        drop_reasons=tuple(drop),
        # Provenance — per-feature
        provenance={
            "labelling_app_metrics": {
                "source": "labeling_app.measurements.compute_all_metrics",
                "mask_source": "multiclass-as-provided (no erosion)",
                "image_source": str(final_image_path),
                "image_was_resized_to_mask": (mh, mw),
            },
            "white_paper_geometry": {
                "source": "rf_quality.features.geometry.compute_geometry",
                "rf_full_mask_source": "largest_component(multiclass == LABEL_RF)",
                "rf_eroded_mask_source": "cv2.MORPH_ELLIPSE erosion radius=3 of rf_full",
                "q50_input_mask": "rf_eroded_mask  (despite the 'full_' prefix in the name)",
                "spacing_source": sp.source,
                "spacing_y_mm_per_px_used": sp.y_mm_per_px,
                "spacing_x_mm_per_px_used": sp.x_mm_per_px,
            },
            "white_paper_intensity": {
                "source": "rf_quality.features.intensity.compute_intensity",
                "mask_input": "rf_eroded_mask",
                "image_input": str(final_image_path),
                "modality_canonical": "final  (resolves to {base}_final.png — NOT raw .jpg)",
            },
        },
    )


# ---------------------------------------------------------------------------
# RF image-quality QA grade (RF Image-Quality QA plan § 5.5 / § 7.1 / § 7.2).
# Build-Step 2 Phase 3 item 8 — the pipeline wiring of the QA detector layer.
# ---------------------------------------------------------------------------

def _ungraded_image_quality(frame_base: str, error: Optional[str] = None) -> ImageQualityFeatures:
    """The § 8.6 "QA unavailable" record — emitted when the QA layer cannot
    grade a frame: no `_final.png` to run the image detectors on, or a detector
    raised. The frame is never silently treated as `valid`, and a detector
    error never propagates to block the run (§ 8.6) — `ungraded` is its own
    honest state, with the failure reason recorded under `_qa_error` for the
    drilldown."""
    return ImageQualityFeatures(
        frame_base=frame_base,
        image_quality_validity_flags=(),
        image_quality_pending_calibration_flags=(),
        image_quality_detector_metadata=({"_qa_error": error} if error else {}),
        image_quality_tier_per_measurement={},
        image_quality_aggregate_tier="ungraded",
        image_quality_contract_version=CONTRACT_VERSION,
    )


def compute_image_quality_features(
    *,
    frame_base: str,
    multiclass_mask: np.ndarray,
    final_image_path: Optional[Path] = None,
    final_image_u8: Optional[np.ndarray] = None,
    spacing_row: SpacingRow = None,
    boundary_entropy: Optional[float] = None,
    qmt_provenance: Optional[str] = None,
) -> ImageQualityFeatures:
    """Grade one frame with the RF image-quality QA layer — the Build-Step 2
    Phase-3 pipeline wiring of `labeling_app.image_quality` (§ 5.5 / § 7.2).

    Runs all 15 v0.2 detectors and folds them into one `ImageQualityFeatures`
    record, joined to `FrameFeatures` on `frame_base`:

      * the 13 `(img_gray, mask)` registry detectors via
        `compute_image_quality_flags` (femur / edge-clip / saturation /
        dark-clip / wrong_site / motion / TGC / artifact detectors);
      * `subq_fat_compressed` — needs the pixel spacing the registry contract
        omits, so it is run here and merged (HANDOFF Finding A);
      * `segmentation_low_confidence` — needs the segmenter softmax
        `boundary_entropy`; run + merged only when that signal is supplied
        (predicted-mask runs). With an annotator mask there is no segmenter, so
        `boundary_entropy is None` and the detector is honestly skipped.

    `img_gray` is the `_final.png` grayscale resized to the mask resolution
    (decision D3 / § 7.2); `final_image_u8` is preferred (the live-synthesised
    `final` the segmenter saw), else `final_image_path` is loaded. With neither
    available the frame grades `ungraded` (§ 8.6 — QA unavailable); a detector
    that raises is caught and likewise grades `ungraded`, never crashing the
    assessment run (§ 8.6 — detector error), with the reason recorded.

    Finding-B discipline: `image_quality_validity_flags` carries *every* flag
    raised (Transparency Rule 1), but `image_quality_tier_per_measurement` and
    `image_quality_aggregate_tier` — the verdict fields that drive § 7.3
    pooling — are computed over the *calibrated* flag subset only
    (`calibrated_flags`). An un-calibrated Threshold-tier B detector (today 11
    of 15, `wrong_site` among them) never drives a hard verdict; Build-Step 2b's
    calibration flips each in as a data-update, no code change.
    `image_quality_pending_calibration_flags` records that excluded subset
    explicitly (item-10a gate H1) — so the operator read-surface can show the
    deployed verdict *and* what fired-but-was-not-counted, never a bare "valid".

    QMT provenance floor: `qmt_mm` / `star_ratio` are additionally capped by
    `qmt_provenance` (`qmt_provenance_capped_tiers`) — a `femur_supported_proxy`
    QMT is a degraded fallback, never `valid`; a no-femur / absent measurement
    is `invalid`. Without this the QA verdict would contradict the QMT
    provenance gate on the ~30 % of frames whose QMT is a proxy (RF QA § 7.3).
    """
    final_u8: Optional[np.ndarray] = final_image_u8
    if final_u8 is None and final_image_path is not None:
        try:
            final_u8 = _load_u8_gray(final_image_path)
        except FileNotFoundError:
            final_u8 = None
    if final_u8 is None:
        return _ungraded_image_quality(frame_base)

    # § 8.6 — a detector error renders the frame "QA unavailable" (ungraded);
    # it must never propagate and block the assessment run.
    try:
        mh, mw = int(multiclass_mask.shape[0]), int(multiclass_mask.shape[1])
        img_gray = _resize_to_mask(final_u8, mh, mw)

        qa = compute_image_quality_flags(img_gray, multiclass_mask)
        flags: list[str] = list(qa["flags"])
        detector_metadata: dict = dict(qa["detector_metadata"])

        # Finding A — the two standalone detectors the (img_gray, mask) registry
        # contract cannot carry; run here, where the pipeline context exists.
        fat = detect_subq_fat_compressed(multiclass_mask, spacing_row or {})
        detector_metadata["subq_fat_compressed"] = fat["metadata"]
        if fat["flag"]:
            flags.append(fat["flag"])
        if boundary_entropy is not None:
            seg = detect_segmentation_low_confidence(boundary_entropy)
            detector_metadata["segmentation_low_confidence"] = seg["metadata"]
            if seg["flag"]:
                flags.append(seg["flag"])

        all_flags = canonical_image_quality_flag_order(flags)
        # Finding B — only the calibrated detectors drive the hard verdict.
        cal = calibrated_flags(all_flags, detector_metadata)
        # H1 (item-10a deployed-vs-raw schema gate, ratified 2026-05-22) — the
        # un-calibrated subset, materialised onto the record. `all_flags` is
        # canonical-ordered and `cal` a subset of it, so the filter preserves
        # the § 5.1 priority order. The badge renders the deployed tier + "+N
        # pending" from this field; item 10b makes it operator-reactable.
        cal_set = set(cal)
        pending = tuple(f for f in all_flags if f not in cal_set)
        # QMT provenance floor — the QA verdict for qmt_mm / star_ratio may not
        # rate a QMT measurement better than its provenance allows (§ 7.3).
        tiers = qmt_provenance_capped_tiers(
            image_quality_tier_per_measurement(cal), qmt_provenance)
        return ImageQualityFeatures(
            frame_base=frame_base,
            image_quality_validity_flags=all_flags,
            image_quality_pending_calibration_flags=pending,
            image_quality_detector_metadata=detector_metadata,
            image_quality_tier_per_measurement=tiers,
            image_quality_aggregate_tier=image_quality_aggregate_tier_from_tiers(tiers),
            image_quality_contract_version=CONTRACT_VERSION,
        )
    except Exception as exc:                         # noqa: BLE001 — § 8.6
        return _ungraded_image_quality(
            frame_base, error=f"{type(exc).__name__}: {exc}")

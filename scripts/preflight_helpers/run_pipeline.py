"""End-to-end pipeline: study + picked frames + gates + model bundle → run record.

Single entry point for stage3 (CLI) and the Streamlit assessment page. Path:

  load model bundle → for each frame (use_for_analysis=True):
    load + predict (or load annotator mask) → post-process →
    compute features → aggregate (anchor + agg_median) →
    cohort placements → bundle_writer.write_run.

Returns a `RunResult` with the run_id, aggregate, placements, frame payloads
for the UI to render. The full ledger record is persisted side-effect via
bundle_writer.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from . import env
from . import run_lifecycle
from .aggregation import aggregate_features
from .bundle_writer import append_record, build_record
from .cohort_placement import percentile
from .feature_compute import compute_frame_features, compute_image_quality_features
from .model_registry import BundleEntry
from .post_processing import post_process_rf
from .segmenter import (
    BundleMeta, LABEL_RF, build_voi_hist_clahe_stack, load_learner_safe,
    pinned_mask_cfg, predict_mask, stack_modalities, synthesize_final_u8,
)
from .study_metadata import StudyMeta


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass
class GateAnswers:
    usable: bool = True
    segmentation_perfect: bool = False
    use_for_analysis: bool = True
    # Legacy free-text drop note — kept for the pre-QA ledger runs; None for
    # runs created under the v0.2 structured-capture form (Build-Step 1).
    drop_reason: Optional[str] = None
    # Build-Step 1 structured capture (plan § 7.4). `drop_reason_codes` holds
    # the operator's checked IMAGE_QUALITY_FLAG_PRIORITY reason codes, ordered
    # by priority — distinct data from the machine-computed
    # FrameFeatures.drop_reasons (§ 7.1, name-collision resolution).
    # `drop_reason_other` is free text for reasons not yet in the taxonomy
    # (§ 12.2 Channel C). `qa_overrides` is the § 7.6 override schema, empty
    # until Build-Step 2 wires the override UI.
    drop_reason_codes: tuple[str, ...] = ()
    drop_reason_other: str = ""
    qa_overrides: dict = field(default_factory=dict)


@dataclass
class PickedFrame:
    frame_base: str
    is_anchor: bool
    gates: GateAnswers
    # Resolved paths — filled by the orchestrator.
    final_image_path: Optional[Path] = None
    annotator_mask_path: Optional[Path] = None


@dataclass
class RunResult:
    run_id: str
    aggregate: dict
    frame_features: list[dict]
    placements: dict
    record_path: Optional[Path]   # ledger run_dir/record.json, draft file, or None
    record: dict = field(default_factory=dict)  # the full built record (N4: stashable as a draft)


# Headline + secondary features the assessment card renders. Defined in the
# torch-free feature_catalog (single source of truth) and re-exported here so
# existing `from run_pipeline import HEADLINE_FEATURES` callers are unaffected,
# while study_bootstrap can import the same lists from the catalog WITHOUT
# pulling the segmenter/torch this module carries.
from .feature_catalog import (  # noqa: E402,F401
    ALL_DISPLAY_FEATURES, HEADLINE_FEATURES, SECONDARY_FEATURES,
)


def _resolve_transport_dir(study_path: Path) -> Path:
    """Map a study path to the muscle_mania/transport equivalent (cohort-canonical).

    Only the cohort 3-frame triples have transport-side pre-computed PNGs; for
    arbitrary sweep frames the live-synthesis path produces channels directly.
    This function remains for stage-1 (annotator-mask) compatibility.
    """
    suffix = Path(*study_path.parts[-3:])
    return env.TRANSPORT_ROOT / suffix


def _resolve_raw_image_dir(study_path: Path) -> Path:
    """Locate the `RawImagesForResearch_*` subdir under a study path.

    The bare .jpg + _raw.png + _beforeDSC.png live here for every sweep frame
    (109 for AGZ855; one per timestamp). Picks the first matching subdir if
    multiple exist (in practice there is exactly one per study).
    """
    for sub in Path(study_path).iterdir():
        if sub.is_dir() and sub.name.startswith("RawImagesForResearch_"):
            return sub
    raise FileNotFoundError(f"No RawImagesForResearch_ subdir under {study_path}")


def _spacing_row_from_transport(study_path: Path,
                                frame_bases: list[str]) -> Optional[dict]:
    """Spacing for a COHORT study — read transport_metadata.csv.

    Returns None when the study is not in transport_metadata — the expected
    case for new / self-studies dropped straight into studies_root. Those have
    never been through the segmentation-era `transport` pre-processing, so they
    resolve spacing from their own .dcm instead (see _spacing_row_from_dicom).

    Spacing is constant per device per study, so the lookup is scoped at the
    STUDY level (`study_dirname`); transport_metadata has rows for the cohort
    3-frame triples only, so a sweep-frame lookup by frame_base would miss.
    decision_log lacks dcm_cols; transport_metadata has both (needed by
    rf_quality.io.spacing.resolve_spacing for the DICOM-derived mask-space
    rescale). Demographics (bmi) are supplied by the caller from study_meta.
    """
    if not env.TRANSPORT_METADATA.exists():
        return None
    tp = pd.read_csv(env.TRANSPORT_METADATA)
    study_dirname = Path(study_path).name
    rows = tp[tp["study_dirname"] == study_dirname]
    if rows.empty:
        # Fall back: try frame_base match (still useful if the study is named
        # differently in transport_metadata, e.g., truncated).
        rows = tp[tp["frame_base"].astype(str).isin([str(b) for b in frame_bases])]
    if rows.empty:
        return None
    r = rows.iloc[0]
    return {
        "pixel_spacing_x": float(r["pixel_spacing_x"]),
        "pixel_spacing_y": float(r["pixel_spacing_y"]),
        "dcm_rows": float(r["dcm_rows"]) if pd.notna(r["dcm_rows"]) else None,
        "dcm_cols": float(r["dcm_cols"]) if pd.notna(r["dcm_cols"]) else None,
        "jpg_height": float(r["jpg_height"]) if pd.notna(r["jpg_height"]) else None,
        "jpg_width": float(r["jpg_width"]) if pd.notna(r["jpg_width"]) else None,
        "mask_height": int(r["mask_height"]),
        "mask_width": int(r["mask_width"]),
        "bmi": None,  # caller fills from study_metadata.lookup_study
    }


def _spacing_row_from_dicom(study_path: Path,
                            mask_shape: Optional[tuple[int, int]]) -> dict:
    """Spacing for a study NOT in transport_metadata.csv — read its own .dcm.

    The path for new captures / self-studies dropped straight into
    studies_root: no segmentation-era `transport` pre-processing, so spacing
    comes from the DICOM itself. pixel_spacing + Rows/Cols are DICOM tags;
    jpg_height/width from a sample RawImagesForResearch_ frame; mask_height/
    width from the predicted-mask shape (same model → same resolution the
    transport pre-compute would have recorded).
    """
    import pydicom
    from PIL import Image

    dcm_path = next(iter(sorted(Path(study_path).glob("*.dcm"))), None)
    if dcm_path is None:
        raise ValueError(
            f"No transport_metadata row and no .dcm under '{Path(study_path).name}' "
            f"— spacing cannot be resolved."
        )
    ds = pydicom.dcmread(str(dcm_path), stop_before_pixels=True)

    # DICOM PixelSpacing is [row_spacing (y), col_spacing (x)], in mm.
    ps = ds.get("PixelSpacing") or ds.get("ImagerPixelSpacing")
    if ps is None or len(ps) != 2:
        raise ValueError(
            f"{dcm_path.name}: no usable PixelSpacing / ImagerPixelSpacing tag. "
            f"(Ultrasound-region spacing via SequenceOfUltrasoundRegions is not "
            f"yet wired — needs a sample DICOM to implement against.)"
        )
    sy, sx = float(ps[0]), float(ps[1])

    # jpg dimensions — read one bare frame; non-critical, tolerate absence.
    jpg_h = jpg_w = None
    try:
        raw_dir = _resolve_raw_image_dir(study_path)
        jpg = next(iter(sorted(raw_dir.glob("*.jpg"))), None)
        if jpg is not None:
            with Image.open(jpg) as im:
                jpg_w, jpg_h = im.size
    except FileNotFoundError:
        pass

    return {
        "pixel_spacing_x": sx,
        "pixel_spacing_y": sy,
        "dcm_rows": float(ds.Rows),
        "dcm_cols": float(ds.Columns),
        "jpg_height": float(jpg_h) if jpg_h else None,
        "jpg_width": float(jpg_w) if jpg_w else None,
        "mask_height": int(mask_shape[0]) if mask_shape else None,
        "mask_width": int(mask_shape[1]) if mask_shape else None,
        "bmi": None,  # caller fills from study_metadata.lookup_study
    }


def _load_or_predict_masks(
    *,
    picked_frames: list[PickedFrame],
    raw_image_dir: Path,
    bundle: BundleEntry,
    bundle_meta: BundleMeta,
    learn,
    device,
    use_annotator_masks: bool,
    annotator_mask_dir: Optional[Path],
    final_u8_out: Optional[dict[str, np.ndarray]] = None,
    boundary_entropy_out: Optional[dict[str, float]] = None,
) -> tuple[list[np.ndarray], dict]:
    """Return (multi_class_masks_per_frame, mask_provenance).

    For predicted masks: synthesises the `final` modality from the bare .jpg
    (mirrors `goruntuler/scripts/build_transport_dataset.py:_write_final_png`)
    and builds voi/hist/clahe via the canonical `rf_png_channel_utils`
    functions. No dependency on `transport/`-side pre-computed PNGs.

    `final_u8_out` (when supplied) is populated with `{frame_base: ndarray}`
    so callers can reuse the same live-built `final` for downstream
    int_cv_within compute — guarantees the intensity input matches what the
    segmenter saw.

    `boundary_entropy_out` (when supplied) is populated with
    `{frame_base: float}` — the segmenter's mask-boundary-entropy scalar
    (`predict_mask`'s `confidence_out`, RF Image-Quality QA plan § 5.4), the
    `segmentation_low_confidence` detector's input (Build-Step 2 Phase 3).
    Predicted-mask frames only: annotator-mask mode runs no segmenter, so those
    frames are absent from the dict — the detector is then honestly skipped.
    """
    masks: list[np.ndarray] = []
    prov: dict = {"per_frame": []}
    from PIL import Image

    for pf in picked_frames:
        if use_annotator_masks:
            mp = (annotator_mask_dir or env.ANNOTATOR_MASKS_ROOT) / f"{pf.frame_base}_mask.png"
            with Image.open(mp) as im:
                m = np.asarray(im.convert("L"), dtype=np.uint8)
            prov["per_frame"].append({"frame_base": pf.frame_base, "source": "annotator",
                                       "mask_path": str(mp)})
        else:
            final_u8 = synthesize_final_u8(raw_image_dir, pf.frame_base)
            if final_u8_out is not None:
                final_u8_out[pf.frame_base] = final_u8
            stacked = build_voi_hist_clahe_stack(final_u8, modalities=bundle_meta.modalities)
            conf: Optional[dict] = {} if boundary_entropy_out is not None else None
            m = predict_mask(learn, stacked, bundle_meta, device=device,
                             confidence_out=conf)
            if conf is not None:
                boundary_entropy_out[pf.frame_base] = conf.get("boundary_entropy")
            prov["per_frame"].append({"frame_base": pf.frame_base, "source": "predicted_live",
                                       "bundle": str(bundle.pkl_path),
                                       "final_synthesised_from_jpg_at_mask_size": True})
        masks.append(m)
    return masks, prov


_LIFECYCLE_FOR_PERSIST = {
    "ledger": "finalized",
    "draft": "draft",
    "none": "none",
}


def run_pipeline(
    *,
    study_code: str,
    study_path: Path,
    study_meta: StudyMeta,
    picked_frames: list[PickedFrame],
    bundle: BundleEntry,
    selected_by: str = "akadir",
    use_annotator_masks: bool = False,
    annotator_mask_dir: Optional[Path] = None,
    mode: str = "live",
    persist: str = "ledger",
    bundle_sha: Optional[str] = None,
    notes: Optional[str] = None,
    allow_empty: bool = False,
) -> RunResult:
    """Run the full pipeline on the analysis-included frames and produce a run record.

    `picked_frames` carries the human gates (usable / segmentation_perfect /
    use_for_analysis). Only `use_for_analysis=True` frames enter the aggregate;
    `segmentation_perfect=True` frames are flagged for training-candidate
    promotion (caller is responsible for that side-effect — we just persist
    the gate values).

    `persist` (N4 run lifecycle):
      * ``"ledger"`` (default) — append straight to ``runs.jsonl`` + per-run
        artefact dir. This is the pre-N4 behaviour; preflight stage3 CLI keeps
        it, so that path is unaffected.
      * ``"draft"`` — write to ``data/runs_drafts/<run_id>.json`` only. The
        Streamlit assessment view uses this; the run reaches the ledger only
        when the user clicks "Accept run" (`run_lifecycle.finalize_draft`).
      * ``"none"`` — compute only, persist nothing. The full record is still
        on `RunResult.record` for the caller to stash however it likes.

    `bundle_sha` — when the caller has already hashed the bundle pkl (the UI
    caches it once at model-load), pass it here so the ledger record's
    ``model_bundle.sha256`` and the ``frame_set.signature`` use the *same*
    hash. Falls back to ``bundle.pkl_sha256`` then a fresh file hash.

    `allow_empty` — v8 routed studies can score from the femur/RF cines alone
    (no sweep pick). The caller then passes an empty ``picked_frames`` here on
    purpose: the run produces a valid record + ``run_id`` with an empty sweep
    aggregate (``_n_frames_in_aggregate=0``), and ``route_study_aggregate``
    overlays the real femur/RF features afterwards. Defaults False so every
    legacy caller (and the stage-3 CLI) still rejects a genuinely-empty run.
    """
    if not picked_frames and not allow_empty:
        raise ValueError("picked_frames is empty — nothing to run")
    if persist not in _LIFECYCLE_FOR_PERSIST:
        raise ValueError(f"persist must be one of {sorted(_LIFECYCLE_FOR_PERSIST)}, got {persist!r}")

    # Resolve transport dir (used only as a stable identifier for ledger; not
    # required for live-channel construction).
    transport_dir = _resolve_transport_dir(study_path)
    # Raw image dir (jpg + _raw.png live here per goruntuler convention).
    raw_image_dir = _resolve_raw_image_dir(study_path)

    # Load model.
    import torch
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    bundle_meta = BundleMeta.from_pkl(bundle.pkl_path)
    learn = load_learner_safe(bundle.pkl_path) if not use_annotator_masks else None

    # Predict (or load annotator) masks for ALL picked frames; we still need
    # them all for the labelling-app `frame_rf_consistency` computation.
    # Capture each frame's live-synthesised `final` for downstream int_cv_within.
    frame_bases = [pf.frame_base for pf in picked_frames]
    final_u8_cache: dict[str, np.ndarray] = {}
    # Per-frame segmenter mask-boundary entropy — the segmentation_low_confidence
    # QA detector's input (§ 5.4). Populated for predicted-mask frames only.
    boundary_entropy_cache: dict[str, float] = {}
    masks, mask_prov = _load_or_predict_masks(
        picked_frames=picked_frames, raw_image_dir=raw_image_dir,
        bundle=bundle, bundle_meta=bundle_meta, learn=learn, device=device,
        use_annotator_masks=use_annotator_masks,
        annotator_mask_dir=annotator_mask_dir,
        final_u8_out=final_u8_cache,
        boundary_entropy_out=boundary_entropy_cache,
    )

    # Spacing — cohort studies read transport_metadata.csv; new / self-studies
    # dropped straight into studies_root resolve it from their own .dcm.
    spacing_row = (
        _spacing_row_from_transport(study_path, frame_bases)
        or _spacing_row_from_dicom(study_path, masks[0].shape[:2] if masks else None)
    )
    spacing_row["bmi"] = study_meta.bmi

    # Per-frame compute (for ALL picked frames — we surface usable=False ones
    # in the ledger; only use_for_analysis=True go to aggregate).
    per_frame_dicts: list[dict] = []
    analysis_dicts: list[dict] = []
    anchor_base = next((pf.frame_base for pf in picked_frames if pf.is_anchor), None)

    for pf, mask in zip(picked_frames, masks):
        rf_pair = post_process_rf(mask, rf_class_id=LABEL_RF, erosion_px=3, fill_holes=False)
        # Prefer the live-synthesised `final` from the segmenter step (when
        # predicted-mask mode); for annotator-mask mode (stage 1) fall back to
        # transport's `_final.png` which exists for cohort-canonical frames.
        if pf.frame_base in final_u8_cache:
            final_image_path = None
            final_image_u8 = final_u8_cache[pf.frame_base]
        else:
            final_image_path = transport_dir / f"{pf.frame_base}_final.png"
            final_image_u8 = None
        ff = compute_frame_features(
            frame_base=pf.frame_base, is_anchor=pf.is_anchor,
            multiclass_mask=mask, all_frame_masks=masks,
            final_image_path=final_image_path,
            final_image_u8=final_image_u8,
            spacing_row=spacing_row,
            rf_mask_pair=rf_pair,
        ).to_dict()
        # RF image-quality QA grade (§ 5.5 / § 7.1) — Build-Step 2 Phase 3 item
        # 8. Joined onto the frame record; rides into the ledger and feeds the
        # § 7.3 QA-gated aggregation.
        ff["image_quality"] = compute_image_quality_features(
            frame_base=pf.frame_base,
            multiclass_mask=mask,
            final_image_path=final_image_path,
            final_image_u8=final_image_u8,
            spacing_row=spacing_row,
            boundary_entropy=boundary_entropy_cache.get(pf.frame_base),
            qmt_provenance=ff.get("qmt_provenance"),
        ).to_dict()
        ff["gates"] = {
            "usable": pf.gates.usable,
            "segmentation_perfect": pf.gates.segmentation_perfect,
            "use_for_analysis": pf.gates.use_for_analysis,
            "drop_reason": pf.gates.drop_reason,
            "drop_reason_codes": list(pf.gates.drop_reason_codes),
            "drop_reason_other": pf.gates.drop_reason_other,
            "qa_overrides": pf.gates.qa_overrides,
        }
        per_frame_dicts.append(ff)
        if pf.gates.use_for_analysis and pf.gates.usable:
            analysis_dicts.append(ff)

    if not analysis_dicts:
        # Caller can still inspect per_frame_dicts via the ledger — we just
        # produce an empty aggregate.
        agg = {"_n_frames_in_aggregate": 0}
    else:
        agg = aggregate_features(analysis_dicts, anchor_frame_base=anchor_base)

    # Cohort placements for the headline + secondary features.
    placements: dict = {}
    for fname in ALL_DISPLAY_FEATURES:
        val = agg.get(fname)
        p = percentile(fname, val, age=study_meta.age, sex=study_meta.sex)
        placements[fname] = {
            "value": p.value,
            "overall": p.overall,
            "age_matched": p.age_matched,
            "sex_matched": p.sex_matched,
        }

    # ---- Build the run record (no I/O yet — persistence is decided below).
    palette_sha = _sha256_of(env.PALETTE_PATH)
    cls = pinned_mask_cfg()
    selected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    selected_frames_for_ledger = [{
        "frame_base": pf.frame_base,
        "is_anchor": pf.is_anchor,
        "selected_by": selected_by,
        "selected_at_utc": selected_at,
        "final_image_path": str(transport_dir / f"{pf.frame_base}_final.png"),
        "gates": {
            "usable": pf.gates.usable,
            "segmentation_perfect": pf.gates.segmentation_perfect,
            "use_for_analysis": pf.gates.use_for_analysis,
            "drop_reason": pf.gates.drop_reason,
            "drop_reason_codes": list(pf.gates.drop_reason_codes),
            "drop_reason_other": pf.gates.drop_reason_other,
            "qa_overrides": pf.gates.qa_overrides,
        },
    } for pf in picked_frames]

    # One hash for the bundle, reused everywhere: ledger provenance AND the
    # frame_set signature (so the UI's cheap-cached sha and the ledger sha
    # never diverge — that would break N4 cache-invalidation silently).
    pkl_sha = bundle_sha or bundle.pkl_sha256 or _sha256_of(bundle.pkl_path)

    resolved_class_ids = {
        "muscle_class_id": cls.muscle_class_id,
        "fascia_class_id": cls.fascia_class_id,
        "skin_class_id": cls.skin_class_id,
        "fat_class_id": cls.fat_class_id,
        "femur_class_id": cls.femur_class_id,
        "erosion_px": cls.erosion_px,
    }

    # N5 frame_set — the dedup key for study bootstrap. signature is the SHARED
    # input_signature used by N4 cache-invalidation too.
    signature = run_lifecycle.input_signature(picked_frames, pkl_sha, resolved_class_ids)
    frame_set = {
        "signature": signature,
        "frame_bases_sorted": sorted(pf.frame_base for pf in picked_frames),
        "n_analysis_frames": sum(
            1 for pf in picked_frames
            if pf.gates.use_for_analysis and pf.gates.usable
        ),
        "bundle_sha256": pkl_sha,
        "erosion_px": cls.erosion_px,
    }

    run_id, record = build_record(
        study_code=study_code,
        study_path=study_path,
        study_meta={
            **study_meta.to_ui_dict(),
            "_phi": study_meta._phi,
            "raw_path": str(study_meta.raw_path) if study_meta.raw_path else None,
        },
        selected_frames=selected_frames_for_ledger,
        model_bundle={
            "pkl_path": str(bundle.pkl_path),
            "json_path": str(bundle.json_path),
            "source": bundle.source,
            "sha256": pkl_sha,
            "modalities": list(bundle.modalities),
            "fold": bundle.fold,
            "arch": bundle.arch,
            "encoder": bundle.encoder,
            "resize_to": list(bundle.resize_to),
        },
        palette_path=env.PALETTE_PATH,
        palette_sha256=palette_sha,
        resolved_class_ids=resolved_class_ids,
        runtime_bridge=[
            str(Path(__file__).resolve().parents[2] / "private" / "muscle_quality_exploration"),
            str(Path(__file__).resolve().parents[2] / "private" / "muscle_model_training/src"),
        ],
        frame_features=per_frame_dicts,
        aggregate=agg,
        placements=placements,
        mode=mode,
        frame_set=frame_set,
        lifecycle=_LIFECYCLE_FOR_PERSIST[persist],
        notes=notes,
    )

    # ---- Persist per the requested lifecycle.
    record_path: Optional[Path]
    if persist == "ledger":
        run_id, run_dir = append_record(record)
        record_path = run_dir / "record.json"
    elif persist == "draft":
        record_path = run_lifecycle.write_draft(record)
    else:  # "none" — compute-only; the full record is on RunResult.record.
        record_path = None

    return RunResult(
        run_id=run_id,
        aggregate=agg,
        frame_features=per_frame_dicts,
        placements=placements,
        record_path=record_path,
        record=record,
    )

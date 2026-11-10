"""Resolve a PHI-safe (study_code, frame_base) to the frame's pixels.

The calibration UI + fitter address frames by (study_code, frame_base) — never
by the patient-name path (memory feedback_phi_guardrail). This module owns the
ONE place that maps that PHI-safe key back to the transport mask/final paths
and loads the pixels. The index (study_code, frame_base) -> paths is built from
transport_metadata.csv + the study_codes map; it stays server-side and is used
only to load pixels, never to display an identifier.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

_REPO = Path(__file__).resolve().parents[2]
_TRANSPORT = _REPO / "transport"
_METADATA = _TRANSPORT / "transport_metadata.csv"
_STUDY_CODES = _REPO / "configs/study_codes.csv"
# Backup root for the full 5fps sweep pool — the calibration loop must reach the
# ~18,332 sweep frames, but transport indexes only the ~956 annotator-selected
# (~3/study). A sweep frame is resolved via the study_codes map -> backup jpg and
# segmented on-demand. Server-side, PHI-safe (addressed by code, never displayed).
_SWEEP_BACKUP = Path(str(Path(__file__).resolve().parents[2] / "private" / "goruntuler_backup"))


@dataclass(frozen=True)
class FramePaths:
    study_code: str
    frame_base: str
    mask_path: Path
    final_path: Path
    spacing_row: dict      # pixel_spacing_y, dcm_rows, mask_height (filled on load)


@lru_cache(maxsize=1)
def _index() -> dict:
    """{(study_code, frame_base): row_dict} over the transport cohort. Cached —
    built once per process. Uses the PHI-safe study_code map."""
    from preflight_helpers.study_metadata import study_code_for_dirname
    idx: dict = {}
    with open(_METADATA, newline="") as f:
        for row in csv.DictReader(f):
            code = study_code_for_dirname(row.get("study_dirname", "")) or "UNKNOWN"
            idx[(code, row.get("frame_base", ""))] = row
    return idx


def resolve(study_code: str, frame_base: str) -> Optional[FramePaths]:
    """The transport paths for a PHI-safe key, or None if not in the cohort."""
    row = _index().get((study_code, frame_base))
    if row is None:
        return None
    mask_rel, final_rel = row.get("mask_path"), row.get("final_path")
    if not mask_rel or not final_rel:
        return None
    return FramePaths(
        study_code=study_code, frame_base=frame_base,
        mask_path=_TRANSPORT / mask_rel, final_path=_TRANSPORT / final_rel,
        spacing_row={
            "pixel_spacing_y": _safe_float(row.get("pixel_spacing_y")),
            "dcm_rows": _safe_float(row.get("dcm_rows")),
        },
    )


def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_mask(fp: FramePaths) -> np.ndarray:
    """The annotator multi-class label-ID mask (uint8, mode-'L' PNG)."""
    with Image.open(fp.mask_path) as im:
        return np.asarray(im.convert("L"), dtype=np.uint8)


def load_final_u8(fp: FramePaths, mask_h: int, mask_w: int) -> np.ndarray:
    """The `_final.png` grayscale resized to the mask resolution."""
    with Image.open(fp.final_path) as im:
        gray = im.convert("L")
        if gray.size != (mask_w, mask_h):
            gray = gray.resize((mask_w, mask_h), Image.BILINEAR)
        return np.asarray(gray, dtype=np.uint8)


def load_final_native(fp: FramePaths) -> np.ndarray:
    """The `_final.png` grayscale at its NATIVE resolution — the input to the
    live segmenter (p4x grades on predicted masks at this resolution, matching
    production). No resize."""
    with Image.open(fp.final_path) as im:
        return np.asarray(im.convert("L"), dtype=np.uint8)


def load_frame(study_code: str, frame_base: str, *, learn=None, bundle_meta=None,
               device=None):
    """Convenience — returns (mask, final_u8, spacing_row) or None.

    Transport-first: a frame in the 956-frame cohort loads its pre-computed
    annotator mask. A frame NOT in transport (the 5fps sweep pool) falls back to
    `load_frame_sweep`, which segments it on-demand — that branch needs the live
    segmenter (`learn` + `bundle_meta`); without them a sweep frame returns None
    (transport-only behaviour, backward-compatible)."""
    fp = resolve(study_code, frame_base)
    if fp is not None:
        mask = load_mask(fp)
        final_u8 = load_final_u8(fp, mask.shape[0], mask.shape[1])
        spacing = dict(fp.spacing_row)
        spacing["mask_height"] = int(mask.shape[0])
        return mask, final_u8, spacing
    if learn is not None and bundle_meta is not None:
        return load_frame_sweep(study_code, frame_base, learn, bundle_meta, device)
    return None


@lru_cache(maxsize=1)
def _study_dir_map() -> dict:
    """{study_code: relative_frame_dir} from configs/study_codes.csv (PHI-safe)."""
    out: dict = {}
    with open(_STUDY_CODES, newline="") as f:
        for row in csv.DictReader(f):
            out[row.get("study_code", "")] = row.get("relative_frame_dir", "")
    return out


def resolve_sweep_jpg(study_code: str, frame_base: str) -> Optional[Path]:
    """The on-disk RawImages `.jpg` for a sweep frame outside the transport cohort,
    via study_codes -> backup dir. None if unresolvable.

    SERVER-SIDE ONLY: the returned Path embeds patient-name directories (study_codes
    `relative_frame_dir`). Use it to LOAD pixels and discard it — never st.write /
    log / surface the return (memory feedback_phi_guardrail)."""
    rel = _study_dir_map().get(study_code)
    if not rel or rel == "nan":
        return None
    base = _SWEEP_BACKUP / rel
    if not base.is_dir():
        return None
    for rdir in sorted(base.glob("RawImagesForResearch_*")):
        jpg = rdir / f"{frame_base}.jpg"
        if jpg.exists():
            return jpg
    return None


def load_frame_sweep(study_code: str, frame_base: str, learn, bundle_meta,
                     device=None):
    """Segment a sweep frame ON-DEMAND: synthesise the `final` from the jpg, predict
    the mask. The path the calibration loop uses to reach the 5fps sweep pool that
    transport does not index. Returns (mask, final_u8, spacing) or None.

    spacing has no pixel_spacing_y/dcm_rows (sweeps are MAKE-VIDEO-only, no `.dcm`),
    so only the spacing-free detectors grade — exactly as production would here."""
    jpg = resolve_sweep_jpg(study_code, frame_base)
    if jpg is None:
        return None
    import torch
    from preflight_helpers.segmenter import (
        build_voi_hist_clahe_stack, predict_mask, synthesize_final_u8,
    )
    final = synthesize_final_u8(jpg.parent, frame_base)
    dev = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
    mask = predict_mask(
        learn, build_voi_hist_clahe_stack(final, modalities=bundle_meta.modalities),
        bundle_meta, device=dev)
    final_u8 = final
    if final.shape != mask.shape:
        final_u8 = np.asarray(
            Image.fromarray(final).resize((mask.shape[1], mask.shape[0]),
                                          Image.BILINEAR), dtype=np.uint8)
    return mask, final_u8, {"pixel_spacing_y": None, "dcm_rows": None,
                            "mask_height": int(mask.shape[0])}

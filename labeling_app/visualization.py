"""Visualization helpers: mask colorization, overlays, and QMT annotation drawing.

Uses Pillow exclusively (no skimage or matplotlib) to keep the dependency
footprint minimal and keep rendering deterministic.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image, ImageDraw

from labeling_app.measurements import (
    LABEL_RF,
    LABEL_FEMUR,
    LABEL_FASCIA,
    _standard_qmt_bounds,
    _dominant_femur_mask,
    max_span_column,
    _qmt_measurement_details,
    get_qmt_annotation_columns,
)
# qmt_tier (preflight_helpers.aggregation) is the single source of truth for
# the 3-tier QMT verdict. The caliper draw must obey it, or the picture and
# the verdict text contradict each other (bug surfaced 2026-05-22).
from preflight_helpers.aggregation import qmt_tier
# `post_process_rf` produces the same largest-component + (optionally) fill-holes
# pair the measurement code uses. Reused here for the DISPLAY-side cleanup of
# the RF class — the measurement keeps the canonical `fill_holes=False` so
# cohort-matched values are unaffected; the display path opts into `fill_holes=True`.
from preflight_helpers.post_processing import post_process_rf


# ---------------------------------------------------------------------------
# Drawing helpers
# ---------------------------------------------------------------------------

def _draw_label_runs(
    draw: "ImageDraw.ImageDraw",
    col: int,
    col_slice: np.ndarray,
    offset: int,
    label: int,
    color: Tuple[int, int, int],
    width: int = 2,
) -> None:
    """Draw only the contiguous runs of `label` pixels in `col_slice`.

    col_slice is a 1-D array of label IDs (a column or sub-column).
    offset is added to the row indices so coordinates map back to the
    full image (pass 0 when col_slice is the full column).
    """
    rows = np.where(col_slice == label)[0]
    if len(rows) == 0:
        return
    # group into contiguous runs
    runs: List[Tuple[int, int]] = []
    start = prev = int(rows[0])
    for r in rows[1:]:
        ri = int(r)
        if ri == prev + 1:
            prev = ri
        else:
            runs.append((start, prev))
            start = prev = ri
    runs.append((start, prev))
    for rs, re in runs:
        draw.line([(col, offset + rs), (col, offset + re)], fill=color, width=width)


def _draw_dashed_vertical(
    draw: "ImageDraw.ImageDraw",
    col: int,
    top: int,
    bottom: int,
    color: Tuple[int, int, int],
    *,
    dash: int = 6,
    gap: int = 4,
    width: int = 1,
) -> None:
    """Draw a dashed vertical guide line."""
    cursor = top
    while cursor < bottom:
        seg_bottom = min(cursor + dash, bottom)
        draw.line([(col, cursor), (col, seg_bottom)], fill=color, width=width)
        cursor = seg_bottom + gap


# ---------------------------------------------------------------------------
# Palette loader
# ---------------------------------------------------------------------------

def load_label_palette(path: str) -> Dict[int, Tuple[int, int, int, int]]:
    """Parse label_palette.json → {label_id: (R, G, B, A)} as uint8 tuples.

    RGBA values in the JSON are floats in [0, 1]; we scale to [0, 255].
    Background (id=0) keeps alpha=0 so it renders transparent.
    """
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    palette: Dict[int, Tuple[int, int, int, int]] = {}

    # background entry
    bg = raw.get("background", {})
    bg_rgba = bg.get("rgba", [0, 0, 0, 0])
    palette[0] = (
        int(bg_rgba[0] * 255),
        int(bg_rgba[1] * 255),
        int(bg_rgba[2] * 255),
        0,  # always transparent
    )

    for label in raw.get("labels", []):
        lid = int(label["id"])
        rgba = label.get("rgba", [0, 0, 0, 0.75])
        palette[lid] = (
            int(rgba[0] * 255),
            int(rgba[1] * 255),
            int(rgba[2] * 255),
            int(rgba[3] * 255),
        )

    return palette


# ---------------------------------------------------------------------------
# Mask colorization
# ---------------------------------------------------------------------------

def colorize_mask(
    mask: np.ndarray,
    palette: Dict[int, Tuple[int, int, int, int]],
    bg_alpha: int = 0,
) -> Image.Image:
    """Map each pixel's label → RGBA color; background pixels are transparent.

    Args:
        mask:      uint8 H×W numpy array of label IDs
        palette:   {label_id: (R, G, B, A)} from load_label_palette()
        bg_alpha:  alpha override for background pixels (default 0 = transparent)

    Returns RGBA PIL Image.
    """
    h, w = mask.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)

    unique_labels = np.unique(mask)
    for label in unique_labels:
        color = palette.get(int(label), (128, 128, 128, 128))
        region = mask == label
        rgba[region] = color

    # Force background transparency
    rgba[mask == 0, 3] = bg_alpha

    return Image.fromarray(rgba, "RGBA")


# ---------------------------------------------------------------------------
# Overlay compositor
# ---------------------------------------------------------------------------

def overlay_on_image(
    base_pil: Image.Image,
    mask: np.ndarray,
    palette: Dict[int, Tuple[int, int, int, int]],
    alpha: float = 0.5,
) -> Image.Image:
    """Blend colorized mask over base image at the given alpha.

    Args:
        base_pil:  RGB PIL Image (base ultrasound frame)
        mask:      uint8 H×W label array (must match base_pil size)
        palette:   from load_label_palette()
        alpha:     mask opacity [0-1]

    Returns RGB PIL Image.
    """
    base_rgba = base_pil.convert("RGBA")
    mask_rgba = colorize_mask(mask, palette)

    # Scale mask alpha by the blend factor
    r, g, b, a = mask_rgba.split()
    a_scaled = a.point(lambda x: int(x * alpha))
    mask_rgba = Image.merge("RGBA", (r, g, b, a_scaled))

    # Alpha-composite onto base
    composite = Image.alpha_composite(base_rgba, mask_rgba)
    return composite.convert("RGB")


# ---------------------------------------------------------------------------
# Display-side mask post-processing
# (Akadir 2026-05-23 — close the gap between what the measurement code SAW
# and what the operator SEES.)
# ---------------------------------------------------------------------------

def post_process_mask_for_display(
    pred: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray]:
    """Return (cleaned_pred, change_map) — a per-class cleanup of the predicted
    mask intended for the assessment-view overlay only.

    Two classes are cleaned, asymmetrically:

    - **Femur (`LABEL_FEMUR`)** — largest 8-connected component only, via the
      existing `_dominant_femur_mask` (the same helper the QMT measurement code
      uses, so display and measurement see the same femur). No hole-fill — the
      femur cortex is a thin band, not a solid region, and has no enclosed
      interior to fill.
    - **RF (`LABEL_RF`)** — largest 8-connected component **and** `binary_fill_holes`
      on the resulting blob. RF is a solid muscle region, so internal gaps the
      segmenter dropped are real anatomical RF and worth filling for the
      display. (Akadir 2026-05-23 — "actually filled, with uncertainty
      reflected in the heatmap.") This reuses `post_process_rf(pred,
      fill_holes=True).rf_full_mask`; the measurement-canonical
      `post_process_rf(pred)` call elsewhere keeps `fill_holes=False` so the
      cohort-matched values are unchanged.

    Pixels the cleanup re-labels:

    - Stray femur pixels (raw femur outside the dominant blob) → background (0).
    - Stray RF pixels   (raw RF outside the dominant blob) → background (0).
    - Filled-hole RF pixels (in the cleaned RF blob but not raw RF)   → `LABEL_RF`.

    The `change_map` is a bool array, True where the cleanup re-labelled a
    pixel — i.e., the AI said one thing and the post-processing said another.
    These pixels are inherently uncertain (whichever side was wrong) and the
    caller is expected to OR them into the segmentation-confidence heatmap as
    a second uncertainty layer, so the operator can SEE where we overrode the
    AI.

    Pure numpy + cv2 + scipy (all via the reused helpers); no new dependencies.
    """
    pred = np.asarray(pred)
    cleaned = pred.copy()
    change_map = np.zeros(pred.shape, dtype=bool)

    # --- femur: largest-component only --------------------------------------
    raw_femur = (pred == LABEL_FEMUR)
    cleaned_femur = _dominant_femur_mask(pred)
    femur_strays = raw_femur & ~cleaned_femur
    cleaned[femur_strays] = 0
    change_map |= femur_strays

    # --- RF: largest-component + fill-holes ---------------------------------
    raw_rf = (pred == LABEL_RF)
    cleaned_rf = post_process_rf(pred, fill_holes=True).rf_full_mask
    rf_strays = raw_rf & ~cleaned_rf
    rf_filled = cleaned_rf & ~raw_rf
    cleaned[rf_strays] = 0
    cleaned[rf_filled] = LABEL_RF
    change_map |= rf_strays | rf_filled

    return cleaned, change_map


# ---------------------------------------------------------------------------
# Layer-stack composition — the p04 review-page rendering primitive
# (Akadir 2026-05-24 v2 reframe — Layer 2a + 3.)
# ---------------------------------------------------------------------------
# The operator's mental model is *layers*, not one blended image. So instead
# of painting a single red heatmap onto an already-coloured segmentation
# overlay (where the red wash fights with the RF magenta), let the operator
# compose the inspection image themselves: raw + segmentation? + AI-
# confidence? + post-processing-change-map? Each layer toggleable.

def _alpha_blend_rgba(
    base_rgb: np.ndarray, color_rgb: Tuple[int, int, int],
    alpha_field: np.ndarray,
) -> np.ndarray:
    """Alpha-blend a single-colour layer onto an RGB image.

    base_rgb       : (H, W, 3) float32 — the current composition.
    color_rgb      : (R, G, B) — the layer's solid colour, 0–255.
    alpha_field    : (H, W) float32 in [0, 1] — per-pixel alpha for this layer.

    Returns a NEW (H, W, 3) float32 — base * (1 - alpha) + color * alpha.
    """
    a = alpha_field[..., None]
    color = np.zeros_like(base_rgb)
    color[..., 0] = color_rgb[0]
    color[..., 1] = color_rgb[1]
    color[..., 2] = color_rgb[2]
    return base_rgb * (1.0 - a) + color * a


# Layer colour discipline (Akadir 2026-05-24 — red painted on RF magenta in
# v1 was invisible, screenshot 7). Two solid, palette-distinct colours:
#   Layer 2 (AI confidence) — cyan (0, 255, 255). Distinct from RF magenta,
#     VI purple, fat yellow. Reads as "uncertain" — cool, not alarming.
#   Layer 3 (post-processing change) — bright lime (50, 255, 50). Distinct
#     from both cyan AND fat yellow. Reads as "we overrode" — distinct from
#     cool uncertainty.
LAYER_CONFIDENCE_COLOR: Tuple[int, int, int] = (0, 255, 255)
LAYER_CHANGE_COLOR:     Tuple[int, int, int] = (50, 255, 50)


def compose_layer_stack(
    raw_arr: np.ndarray,
    segmentation_arr: Optional[np.ndarray] = None,
    *,
    confidence_map: Optional[np.ndarray] = None,
    change_map: Optional[np.ndarray] = None,
    show_segmentation: bool = True,
    show_confidence: bool = False,
    show_change_map: bool = False,
    confidence_max_alpha: float = 0.6,
    change_alpha: float = 0.7,
) -> np.ndarray:
    """Compose up to 4 layers onto a base RGB image. Each layer independent.

    Layer 0 (always): the raw ultrasound image (`raw_arr`).
    Layer 1 (optional): the segmentation overlay — colours + contour +
        measurement annotations — pre-rendered by the caller as
        `segmentation_arr`. Toggled by `show_segmentation`. The caller owns
        what's in this layer: the colour overlay, the magenta contour, the
        green RF ticks, the QMT caliper. Bundled together because they all
        reference the same AI-output structure; turning off the colour and
        leaving the lines would read as ghost geometry.
    Layer 2 (optional): per-pixel AI confidence wash. Cyan, alpha proportional
        to (1 - confidence). Toggled by `show_confidence`.
    Layer 3 (optional): post-processing change-map wash. Lime green, alpha =
        `change_alpha` where the cleanup re-labelled the AI's call. Toggled
        by `show_change_map`.

    Each layer alpha-blends INDEPENDENTLY onto the running composition (not
    via element-wise max as the single-layer v1 heatmap did). So a pixel that
    is both AI-unsure (layer 2) and post-processing-overridden (layer 3) gets
    BOTH cyan and lime layered on, reading as a mixed wash — operator can
    visually decompose which side flagged it.

    raw_arr           : (H, W, 3) uint8 — the raw image (e.g. grayscale
        broadcast to RGB, or live-synthesised `final` converted to RGB).
    segmentation_arr  : (H, W, 3) uint8 — the pre-rendered segmentation
        overlay (colour + contour + annotations). Caller produces this with
        `overlay_on_image` + the contour-paint + `annotate_overlay`. Same
        size as `raw_arr`. Optional — pass `None` if Layer 1 is conceptually
        absent (no mask yet). Toggled `show_segmentation` controls the blend.
    confidence_map    : (h, w) float in [0, 1] — per-pixel confidence.
        Resized to `raw_arr` size if shape mismatches.
    change_map        : (h, w) bool — True where post-processing changed the
        label. Resized nearest-neighbour if shape mismatches.

    Returns a NEW (H, W, 3) uint8 — does not mutate `raw_arr`.
    """
    if raw_arr.ndim != 3 or raw_arr.shape[2] != 3:
        raise ValueError(f"raw_arr must be (H, W, 3) RGB; got {raw_arr.shape}")
    H, W = raw_arr.shape[:2]
    composed = raw_arr.astype(np.float32)

    # --- Layer 1: segmentation overlay (the caller's bundled rendering) -----
    if show_segmentation and segmentation_arr is not None:
        if segmentation_arr.shape != raw_arr.shape:
            raise ValueError(
                f"segmentation_arr shape {segmentation_arr.shape} must match "
                f"raw_arr {raw_arr.shape}")
        # The segmentation_arr is already the composited overlay+contour+ticks;
        # we adopt it wholesale when Layer 1 is on (alpha = 1.0). No partial
        # blend at this layer — the caller already chose the segmentation
        # palette's alpha.
        composed = segmentation_arr.astype(np.float32)

    # --- Layer 2: AI confidence wash (cyan) ---------------------------------
    if show_confidence and confidence_map is not None:
        cmap = np.asarray(confidence_map, dtype=np.float32)
        if cmap.ndim != 2:
            raise ValueError(f"confidence_map must be 2-D; got {cmap.shape}")
        if cmap.shape != (H, W):
            cmap = np.asarray(
                Image.fromarray(cmap).resize((W, H), resample=Image.BILINEAR),
                dtype=np.float32)
        uncertainty = np.clip(1.0 - cmap, 0.0, 1.0)
        alpha = uncertainty * confidence_max_alpha
        composed = _alpha_blend_rgba(composed, LAYER_CONFIDENCE_COLOR, alpha)

    # --- Layer 3: post-processing change-map (lime) -------------------------
    if show_change_map and change_map is not None:
        cm = np.asarray(change_map, dtype=bool)
        if cm.shape != (H, W):
            cm_resized = Image.fromarray(cm.astype(np.uint8)).resize(
                (W, H), resample=Image.NEAREST)
            cm = np.asarray(cm_resized, dtype=bool)
        alpha = cm.astype(np.float32) * change_alpha
        composed = _alpha_blend_rgba(composed, LAYER_CHANGE_COLOR, alpha)

    return np.clip(composed, 0.0, 255.0).astype(np.uint8)


def confidence_stats(
    confidence_map: Optional[np.ndarray],
    *, roi_mask: Optional[np.ndarray] = None,
) -> Optional[dict]:
    """Return interpretable confidence stats — what % is below the threshold,
    the mean confidence — that the operator can read as a number, not just a
    colour wash. (Akadir 2026-05-24 § 7 — "what does more red mean? maybe a
    percentage.")

    If `roi_mask` is given (bool H×W), the stats are computed over those
    pixels only — e.g. just the muscle region, not the whole image. Else
    over the whole frame.

    Returns `{mean, fraction_below_0_70, p10, n_pixels}` or `None` if the
    confidence map is absent.
    """
    if confidence_map is None:
        return None
    cmap = np.asarray(confidence_map, dtype=np.float32)
    if roi_mask is not None:
        roi = np.asarray(roi_mask, dtype=bool)
        if roi.shape != cmap.shape:
            roi_resized = Image.fromarray(roi.astype(np.uint8)).resize(
                (cmap.shape[1], cmap.shape[0]), resample=Image.NEAREST)
            roi = np.asarray(roi_resized, dtype=bool)
        if not roi.any():
            return None
        values = cmap[roi]
    else:
        values = cmap.ravel()
    if values.size == 0:
        return None
    return {
        "mean": float(np.mean(values)),
        "fraction_below_0_70": float(np.mean(values < 0.70)),
        "p10": float(np.percentile(values, 10)),
        "n_pixels": int(values.size),
    }


# ---------------------------------------------------------------------------
# Segmentation-confidence heatmap (RF Image-Quality QA / chunk 5)
# ---------------------------------------------------------------------------

def confidence_heatmap_overlay(
    arr: np.ndarray,
    confidence_map: np.ndarray,
    *,
    change_map: Optional[np.ndarray] = None,
    max_alpha: float = 0.45,
) -> np.ndarray:
    """Alpha-blend a red uncertainty wash onto an RGB image.

    The segmenter outputs a per-pixel confidence — the peak of its class
    probability distribution; 1.0 = the model is sure of the label here,
    closer to 0 = the model is unsure. The full per-pixel map normally is
    discarded (only `boundary_entropy`, the average along the muscle edge,
    is kept). This helper turns that map into a colour overlay so the
    operator can SEE where the model was uncertain: red wash = uncertain,
    transparent = confident. If a measurement line (the QMT caliper, the
    RF-thickness ticks) runs through a red region, the operator reads it
    a touch softer.

    Pure numpy + Pillow (for the resize); no matplotlib / skimage.

    arr            : (H, W, 3) uint8 — the image to overlay (e.g. the
                     segmentation overlay + contour + measurement ticks
                     that `p05_assessment_view` already builds).
    confidence_map : (h, w) float in [0, 1] — per-pixel confidence. If the
                     shape differs from `arr` the map is bilinearly
                     resized to `arr`'s H×W; in practice they match because
                     `predict_mask` returns `confidence_map_native` at the
                     mask's native H×W.
    change_map     : optional (h, w) bool — pixels where display-side mask
                     post-processing (`post_process_mask_for_display`) re-
                     labelled a pixel. Treated as full uncertainty (1.0) at
                     those pixels, OR'd into the AI-confidence uncertainty
                     via element-wise max. So the heatmap then carries TWO
                     stories: AI confidence + post-processing override. A
                     confident-but-overridden pixel still reads as full
                     uncertainty; an unconfident-and-overridden pixel is
                     unchanged (1.0 already).
    max_alpha      : the heatmap's strength at peak uncertainty (0.45 keeps
                     the underlying segmentation and measurement lines
                     legible while making the uncertain regions obvious).

    Returns a new (H, W, 3) uint8 image — the original with red intensity
    proportional to the combined uncertainty alpha-blended in. Does not
    mutate `arr`.
    """
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError(f"arr must be (H, W, 3) RGB; got {arr.shape}")
    target_h, target_w = arr.shape[:2]
    cmap = np.asarray(confidence_map, dtype=np.float32)
    if cmap.ndim != 2:
        raise ValueError(f"confidence_map must be 2-D; got {cmap.shape}")
    if cmap.shape != (target_h, target_w):
        # Bilinear-resize via Pillow — kept consistent with `predict_mask`'s
        # confidence_map_native resize.
        resized = Image.fromarray(cmap).resize(
            (target_w, target_h), resample=Image.BILINEAR)
        cmap = np.asarray(resized, dtype=np.float32)
    uncertainty = np.clip(1.0 - cmap, 0.0, 1.0)
    if change_map is not None:
        cm = np.asarray(change_map, dtype=bool)
        if cm.shape != (target_h, target_w):
            # Nearest-neighbour resize for a bool map — preserves the binary
            # nature without antialiasing across edges.
            cm_resized = Image.fromarray(cm.astype(np.uint8)).resize(
                (target_w, target_h), resample=Image.NEAREST)
            cm = np.asarray(cm_resized, dtype=bool)
        uncertainty = np.maximum(uncertainty, cm.astype(np.float32))
    alpha = (uncertainty * max_alpha)[..., None]            # (H, W, 1)
    red = np.zeros_like(arr, dtype=np.float32)
    red[..., 0] = 255.0                                     # full red
    blended = arr.astype(np.float32) * (1.0 - alpha) + red * alpha
    return np.clip(blended, 0.0, 255.0).astype(np.uint8)


# ---------------------------------------------------------------------------
# QMT annotation drawing
# ---------------------------------------------------------------------------

def draw_qmt_annotations(
    img_pil: Image.Image,
    mask: np.ndarray,
    metrics_dict: Dict,
    *,
    draw_rf: bool = True,
    draw_qmt: bool = True,
) -> Image.Image:
    """Draw measurement lines onto an image copy.

    Draws:
    - Vertical dashed lines at each valid QMT column (magenta)
    - RF thickness span indicator (green horizontal tick marks)
    - QMT span arrow (white, vertical, double-headed) at primary column
    - Text labels for each measurement

    VI annotations are intentionally omitted — the VI segmentation is not yet
    robust enough to surface as a measurement in the review / assessment.

    `draw_rf` / `draw_qmt` gate the standalone RF-thickness ticks and the QMT
    span + support guides INDEPENDENTLY, so a per-view caller can show QMT-only
    (the femur cine) or RF-only (the center-RF cine). Both default True — the
    legacy all-on overlay (Akadir 2026-06-26: stop drawing RF marks on femur
    frames and the QMT caliper on center-RF frames).

    Returns a new RGB Image (does not modify in-place).
    """
    img = img_pil.convert("RGB").copy()
    draw = ImageDraw.Draw(img)
    h, w = mask.shape[:2]

    qmt_info = {
        "qmt_provenance": metrics_dict.get("qmt_provenance"),
        "qmt_column": metrics_dict.get("qmt_column"),
        "qmt_top_y": metrics_dict.get("qmt_top_y"),
        "qmt_bottom_y": metrics_dict.get("qmt_bottom_y"),
        "qmt_annotation_columns": metrics_dict.get("qmt_annotation_columns"),
        # qmt_validity_flags — passed through so `qmt_tier` (below) can see the
        # `rf_or_vi_missing_in_path` flag that drops a proxy to Tier 3.
        "qmt_validity_flags": metrics_dict.get("qmt_validity_flags") or [],
    }
    if qmt_info["qmt_column"] is None or qmt_info["qmt_top_y"] is None or qmt_info["qmt_bottom_y"] is None:
        qmt_info = _qmt_measurement_details(mask)

    provenance = qmt_info.get("qmt_provenance", "no_measurement")
    qmt_cols = list(qmt_info.get("qmt_annotation_columns") or get_qmt_annotation_columns(mask))
    primary_col = qmt_info.get("qmt_column")
    if primary_col is None:
        primary_col = qmt_cols[0] if qmt_cols else w // 2

    # ---- helper: draw tick marks at top/bot + label ----
    def _tick_ends(col: int, top: int, bot: int,
                   color: Tuple[int, int, int], label: str,
                   tick: int = 6, label_offset: int = 8) -> None:
        draw.line([(col - tick, top), (col + tick, top)], fill=color, width=2)
        draw.line([(col - tick, bot), (col + tick, bot)], fill=color, width=2)
        mid = (top + bot) // 2
        draw.text((col + label_offset, mid - 6), label, fill=color)

    # ---- RF thickness (green) — draw only actual RF pixel runs ----
    if draw_rf:
        rf_col = metrics_dict.get("rf_column")
        if rf_col is None:
            rf_col = max_span_column(mask, LABEL_RF)
        if rf_col is not None:
            rf_rows = np.where(mask[:, rf_col] == LABEL_RF)[0]
            if len(rf_rows) > 0:
                _draw_label_runs(draw, rf_col, mask[:, rf_col], 0, LABEL_RF, (0, 255, 0))
                _tick_ends(rf_col, int(rf_rows.min()), int(rf_rows.max()), (0, 255, 0), "RF")

    # ---- Support guides for additional strict-QMT columns ----
    if draw_qmt and provenance == "standard_qmt":
        for support_col in qmt_cols:
            if support_col == primary_col:
                continue
            bounds = _standard_qmt_bounds(mask, support_col, strict=True)
            if bounds is None:
                continue
            support_top, support_bottom = bounds
            _draw_dashed_vertical(draw, support_col, int(support_top), int(support_bottom), (255, 0, 255))

    # ---- QMT span at the true measurement column ----
    # Gate on `qmt_tier` — the single source of truth for the 3-tier verdict.
    # A Tier-3 frame ("not measurable" — no femur, or a proxy that is
    # single-muscle in the path) must not get a caliper drawn, or the picture
    # contradicts the verdict text. Only Tier 1 (standard) and Tier 2
    # (femur-supported proxy with both RF + VI in the path) get a caliper.
    if draw_qmt and qmt_tier(qmt_info) in (1, 2):
        qmt_top = qmt_info.get("qmt_top_y")
        qmt_bot = qmt_info.get("qmt_bottom_y")
        if qmt_top is not None and qmt_bot is not None and qmt_bot > qmt_top:
            prov_short = "QMT" if provenance == "standard_qmt" else "QMT*"

            # White base line + tick marks at boundaries
            draw.line([(primary_col, qmt_top), (primary_col, qmt_bot)], fill=(255, 255, 255), width=2)
            tick = 6
            draw.line([(primary_col - tick, qmt_top), (primary_col + tick, qmt_top)],
                      fill=(255, 255, 255), width=2)
            draw.line([(primary_col - tick, qmt_bot), (primary_col + tick, qmt_bot)],
                      fill=(255, 255, 255), width=2)

            # Overlay RF portion (green) — only actual RF pixel runs within QMT span
            qmt_col_slice = mask[qmt_top:qmt_bot, primary_col]
            _draw_label_runs(draw, primary_col, qmt_col_slice, qmt_top, LABEL_RF, (0, 255, 0))

            mid_q = (qmt_top + qmt_bot) // 2
            text_x = primary_col + 8 if primary_col <= w - 48 else primary_col - 38
            draw.text((text_x, mid_q - 6), prov_short, fill=(255, 255, 255))

    return img


# ---------------------------------------------------------------------------
# I/O helpers
# ---------------------------------------------------------------------------

def pil_to_bytes(img: Image.Image, fmt: str = "PNG") -> bytes:
    """Serialize a PIL Image to bytes for use with st.image()."""
    buf = io.BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def load_image_gray(path: Optional[str]) -> Optional[np.ndarray]:
    """Load image as uint8 grayscale numpy array; return None if missing/unreadable."""
    if path is None:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        img = Image.open(p).convert("L")
        return np.array(img, dtype=np.uint8)
    except Exception:
        return None


def load_image_pil(path: Optional[str]) -> Optional[Image.Image]:
    """Load image as RGB PIL Image; return None if missing/unreadable."""
    if path is None:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        return Image.open(p).convert("RGB")
    except Exception:
        return None


def load_mask_pil(path: Optional[str]) -> Optional[np.ndarray]:
    """Load a mask PNG as uint8 grayscale numpy array."""
    if path is None:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        img = Image.open(p).convert("L")
        return np.array(img, dtype=np.uint8)
    except Exception:
        return None

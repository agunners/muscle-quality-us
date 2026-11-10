"""F11 — per-frame measurement-location overlay.

Answers Akadir's audit ask: "show *where* each measurement was taken on the
frame." Reuses the inherited drawing stack — `visualization.draw_qmt_annotations`
draws the RF peak-thickness column + the QMT span path; a small wrapper
adds the white-paper `full_thickness_q50_mm` column (where the per-column q50
thickness actually sits on the eroded RF mask).

Used by phase 4 (each review frame) and phase 5 (the anchor overlay).
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from PIL import Image, ImageDraw


def q50_thickness_column(binary_mask: np.ndarray) -> Optional[tuple]:
    """The column whose per-column thickness is closest to the q50.

    Mirrors `rf_quality._vendor.roi.thickness_quantiles`' per-column thickness
    definition (`ys.max() - ys.min() + 1`) but returns the *column* plus its
    top/bottom y — so `full_thickness_q50_mm` can be drawn where it was
    measured. Returns `(col, thickness_px, top_y, bottom_y)` or None.
    """
    if binary_mask is None or getattr(binary_mask, "size", 0) == 0:
        return None
    cols = np.flatnonzero(np.asarray(binary_mask).any(axis=0))
    if cols.size == 0:
        return None
    per_col: list[tuple] = []
    for c in cols:
        ys = np.flatnonzero(binary_mask[:, c])
        if ys.size == 0:
            continue
        per_col.append((int(c), float(ys.max() - ys.min() + 1),
                        int(ys.min()), int(ys.max())))
    if not per_col:
        return None
    q50 = float(np.quantile([t[1] for t in per_col], 0.5))
    # Closest column to the q50 thickness; ties → leftmost.
    return min(per_col, key=lambda t: (abs(t[1] - q50), t[0]))


def _dashed_v(draw: "ImageDraw.ImageDraw", col: int, top: int, bottom: int,
              color: tuple, dash: int = 6, gap: int = 4, width: int = 2) -> None:
    cur = top
    while cur < bottom:
        seg = min(cur + dash, bottom)
        draw.line([(col, cur), (col, seg)], fill=color, width=width)
        cur = seg + gap


def annotate_overlay(overlay_rgb: np.ndarray, multiclass_mask: np.ndarray,
                     rf_eroded_mask: Optional[np.ndarray] = None, *,
                     show_qmt: bool = True, show_rf: bool = True,
                     show_q50: bool = True, show_fat: bool = True) -> np.ndarray:
    """Draw measurement lines onto a segmentation overlay (F11).

    - RF peak-thickness column + the QMT span — via the inherited
      `visualization.draw_qmt_annotations` (it self-derives the measurement
      locations from the mask when handed an empty metrics dict).
    - if `rf_eroded_mask` is given, a dashed **orange** line at the
      `full_thickness_q50` column — where the white-paper q50 was measured.

    The four `show_*` flags gate each measurement independently so a per-view
    caller draws only what that view measures: the femur cine shows QMT only,
    the center-RF cine shows RF + q50 + fat only (Akadir 2026-06-26). All default
    True — the legacy overlay (every mark).

    Defensive: an annotation failure (degenerate mask) never blanks the
    overlay — the un-annotated overlay is returned instead.
    """
    from labeling_app.visualization import draw_qmt_annotations

    img = Image.fromarray(np.asarray(overlay_rgb, dtype=np.uint8)).convert("RGB")
    try:
        img = draw_qmt_annotations(img, multiclass_mask, {},
                                   draw_rf=show_rf, draw_qmt=show_qmt)
    except Exception:
        pass
    if show_q50 and rf_eroded_mask is not None:
        col = q50_thickness_column(rf_eroded_mask)
        if col is not None:
            c, _th, top, bot = col
            draw = ImageDraw.Draw(img)
            _dashed_v(draw, c, top, bot, (255, 165, 0))
            label_y = max(0, (top + bot) // 2 - 6)
            draw.text((min(c + 6, img.width - 28), label_y), "q50",
                      fill=(255, 165, 0))
    # Subcutaneous-fat thickness — a solid cyan caliper (skin-bottom → deep
    # fascia / muscle top) at the column nearest the fat q50, so the fat measure
    # is drawn ON the frame like the RF/QMT spans (Akadir 2026-06-04). Defensive:
    # any failure leaves the overlay untouched.
    if show_fat:
        try:
            from labeling_app.fat_features import fat_q50_column
            fc = fat_q50_column(multiclass_mask)
            if fc is not None:
                c, _t, top, bot = fc
                d = ImageDraw.Draw(img)
                cyan = (0, 200, 255)
                d.line([(c, top), (c, bot)], fill=cyan, width=2)
                d.line([(c - 4, top), (c + 4, top)], fill=cyan, width=2)   # top tick
                d.line([(c - 4, bot), (c + 4, bot)], fill=cyan, width=2)   # bottom tick
                d.text((min(c + 6, img.width - 28), max(0, (top + bot) // 2 - 6)),
                       "fat", fill=cyan)
        except Exception:
            pass
    return np.asarray(img)

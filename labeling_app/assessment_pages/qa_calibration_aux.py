"""Auxiliary widgets for the p4x calibration cells (subsystem D, slot 2).

Detector-specific visualisations that an overlay cannot carry — a 1-D depth
profile, a column marker. Each takes the frame's grayscale + mask + the
detector's metadata and returns PNG bytes (or None when nothing useful can be
drawn). Pure — no Streamlit; the page caches the bytes via st.cache_data.

The registry maps the spec's `auxiliary_widget` name to the callable, so a
typo'd name in `qa_calibration_spec.py` is caught by that module's import-time
self-check (the name must be in `VALID_AUX_WIDGETS`) and a missing renderer
here degrades gracefully (the cell shows the layered image alone).
"""
from __future__ import annotations

import io
from typing import Optional

import numpy as np

# Headless matplotlib — the page renders to PNG bytes, never to a display.
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt        # noqa: E402

from labeling_app.measurements import LABEL_RF   # noqa: E402


def _fig_to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=90, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue()


def _aux_tgc_banding(img_gray: np.ndarray, mask: np.ndarray,
                     metadata: dict) -> Optional[bytes]:
    """The depth-axis row-mean intensity profile + a smooth trend + the residual
    whose σ IS the detector's score. The band signal is invisible against the
    depth-attenuation trend in the raw image; this plot makes it legible
    (spec § 8.2 — score_reveal='visualised')."""
    rows = metadata.get("muscle_band_rows")
    if not rows or len(rows) != 2:
        return None
    r0, r1 = int(rows[0]), int(rows[1])
    if r1 - r0 < 8:
        return None
    rf = (mask == LABEL_RF)
    # Per-row mean intensity over RF columns within the muscle band.
    profile = []
    for r in range(r0, r1):
        cols = rf[r]
        profile.append(float(img_gray[r][cols].mean()) if cols.any()
                       else float(img_gray[r].mean()))
    profile = np.asarray(profile)
    depth = np.arange(r0, r1)
    # 3rd-order polynomial trend (the expected smooth TGC curve) + residual.
    coeffs = np.polyfit(depth, profile, deg=3)
    trend = np.polyval(coeffs, depth)
    residual = profile - trend

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(6.0, 3.0))
    ax1.plot(profile, depth, label="row mean", color="#1f77b4")
    ax1.plot(trend, depth, label="smooth trend", color="#999999", ls="--")
    ax1.invert_yaxis()
    ax1.set_xlabel("intensity"); ax1.set_ylabel("depth (row)")
    ax1.legend(fontsize=7); ax1.set_title("depth profile", fontsize=8)
    ax2.plot(residual, depth, color="#d62728")
    ax2.axvline(0, color="#cccccc", lw=0.8)
    ax2.invert_yaxis()
    ax2.set_xlabel("residual (banding)")
    ax2.set_title(f"residual σ = {residual.std():.2f}", fontsize=8)
    fig.tight_layout()
    return _fig_to_png(fig)


def _aux_vessel_shadow(img_gray: np.ndarray, mask: np.ndarray,
                       metadata: dict) -> Optional[bytes]:
    """The raw frame with a vertical marker at the detector's darkest RF column,
    plus the per-column mean-intensity profile showing the dip relative to the
    median (spec § 8.5). Locates the candidate shadow instantly."""
    col = metadata.get("darkest_column")
    if col is None:
        return None
    col = int(col)
    rf = (mask == LABEL_RF)
    if not rf.any():
        return None
    # Per-column mean over RF rows.
    col_means = []
    for c in range(img_gray.shape[1]):
        rows = rf[:, c]
        col_means.append(float(img_gray[:, c][rows].mean()) if rows.any()
                         else np.nan)
    col_means = np.asarray(col_means)
    median = float(np.nanmedian(col_means))

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(5.0, 4.0),
                                   gridspec_kw={"height_ratios": [3, 1]})
    ax1.imshow(img_gray, cmap="gray", aspect="auto")
    ax1.axvline(col, color="#d62728", lw=1.5)
    ax1.set_title(f"darkest RF column @ x={col}", fontsize=8)
    ax1.set_xticks([]); ax1.set_yticks([])
    ax2.plot(col_means, color="#1f77b4")
    ax2.axhline(median, color="#999999", ls="--", lw=0.8, label=f"median {median:.0f}")
    ax2.axvline(col, color="#d62728", lw=1.0)
    ax2.set_xlabel("column"); ax2.set_ylabel("RF mean")
    ax2.legend(fontsize=7)
    fig.tight_layout()
    return _fig_to_png(fig)


def _aux_motion_blur(img_gray: np.ndarray, mask: np.ndarray,
                     metadata: dict) -> Optional[bytes]:
    """Deferred on the annotator-mask pilot — the R11 segmenter-degradation
    confound (and the segmenter-confidence inline) only exists on predicted-mask
    runs. On annotator masks the fascia band is the clean human segmentation, so
    motion_blur's Tenengrad has no segmenter confound and no aux is needed; the
    cell shows raw + fascia contour. Returns None until the predicted-mask path
    lands."""
    return None


_AUX_REGISTRY = {
    "_aux_tgc_banding": _aux_tgc_banding,
    "_aux_vessel_shadow": _aux_vessel_shadow,
    "_aux_motion_blur": _aux_motion_blur,
    # _aux_saturation_regional is registered in the spec for the deferred
    # saturation work; no renderer until that detector re-enters scope.
}


def render_aux(aux_name: Optional[str], *, img_gray: np.ndarray,
               mask: np.ndarray, metadata: dict) -> Optional[bytes]:
    """Render a detector's auxiliary widget to PNG bytes, or None when the
    detector has no aux, the renderer is not built, or the metadata it needs is
    absent (the cell then shows the layered image alone)."""
    if not aux_name:
        return None
    fn = _AUX_REGISTRY.get(aux_name)
    if fn is None:
        return None
    try:
        return fn(img_gray, mask, metadata)
    except Exception:                       # noqa: BLE001 — an aux must never crash a cell
        return None

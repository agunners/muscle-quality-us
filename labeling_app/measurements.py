"""Pure measurement functions for muscle quality metrics.

All functions accept numpy uint8 masks (H×W, values = label IDs 0-9).
No file I/O; no Streamlit references.

Label constants:
    0  background
    1  RF (Rectus Femoris)
    2  VI (Vastus Intermedius)
    3  VL (Vastus Lateralis) — contaminant in QMT path
    4  VM (Vastus Medialis)  — contaminant
    5  fascia
    6  skin
    7  femur (bone)
    8  fat
    9  Sartorius

Physical spacing formula (original mask space — no resize factor needed):
    mm_per_mask_y = pixel_spacing_y * dcm_rows / mask_height
    mm_per_mask_x = pixel_spacing_x * (jpg_width * dcm_rows / jpg_height) / mask_width
"""
from __future__ import annotations

import math
from typing import Dict, List, Optional, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Label constants
# ---------------------------------------------------------------------------
LABEL_RF = 1
LABEL_VI = 2
# VL / VM — the lateral and medial vasti. Added for Build-Step 2's wrong_site
# class-composition detector (RF Image-Quality QA plan § 5.2): the segmenter
# emits RF / VI / VL / VM as four distinct classes, so an `rf_fraction` over
# the quadriceps classes is computable. IDs match the segmenter's pinned
# contract (scripts/preflight_helpers/segmenter.py — VL=3, VM=4).
LABEL_VL = 3
LABEL_VM = 4
LABEL_FASCIA = 5
# LABEL_SKIN — the skin band. Added for Build-Step 2's near-field QA detectors
# (air_gap_reverberation / near_field_clutter — RF Image-Quality QA plan § 5.2),
# which anchor their FFT / clutter window to the segmented skin + fat layers.
# ID matches the segmenter's pinned contract (preflight_helpers/segmenter.py).
LABEL_SKIN = 6
LABEL_FEMUR = 7
LABEL_FAT = 8

# Labels allowed in a valid QMT path (background, RF, VI, fascia only)
ALLOWED_IN_QMT_PATH = {0, 1, 2, 5}

# Tolerance: a column is valid if contamination ratio ≤ this value (2 %)
QMT_CONTAMINATION_TOL = 0.02

FEMUR_SMOOTHING_WINDOW = 5
QMT_BOUNDARY_SLOPE_HALF_WINDOW = 5
QMT_PLATEAU_SLOPE_PCT = 8.0
QMT_CENTRAL_ZONE_MARGIN_PX = 15
QMT_MUSCLE_OCCUPANCY_MIN = 0.85
QMT_FEMUR_SLOPE_MAX_ABS = 0.15
QMT_FASCIA_SLOPE_MAX_ABS = 0.25
# Minimum femur-class connected-component area (pixels), below which the
# largest component is rejected and the frame is treated as femur-absent. A
# soft noise floor — the load-bearing fix for the AGZ855 phantom-femur bug is
# `_dominant_femur_mask`'s "keep only the largest component" step, which on
# its own drops the stray when a real femur exists alongside it. The
# threshold below catches the residual case of "only a tiny stray, no real
# femur." 5 px is a deliberately low starting point — it lets unit-test
# synthetic femurs (a few pixels) through while still rejecting 1-4 px noise.
# Build-Step 2b will calibrate this against cohort statistics; the real
# threshold is likely much higher.
QMT_FEMUR_MIN_AREA_PX = 5
# Femur cortex-top outlier trim (`_trim_femur_top_outliers`): a dominant-femur
# column whose top sits more than this many px (and a robust-MAD multiple) ABOVE
# the robust cortex line is a CONNECTED over-segmentation "hat" — its above-line
# pixels are removed so the QMT plateau anchors on the true cortex, not the
# protrusion. Conservative: a tilted cortex (slope <= QMT_FEMUR_SLOPE_MAX_ABS) is
# preserved (the line follows the tilt), the cortex is never removed (a passing
# frame cannot be demoted), and a majority protrusion is a no-op.
QMT_FEMUR_TOP_OUTLIER_PX = 8
# Minimum dominant-femur columns needed to fit the cortex line; below this the
# trim is skipped (too few points for a robust fit).
_FEMUR_TRIM_MIN_COLS = 8
QMT_VALIDITY_FLAG_PRIORITY = (
    "femur_absent",
    "outside_central_zone",
    "femur_slope_high",
    "fascia_slope_high",
    "low_muscle_occupancy",
    "rf_or_vi_missing_in_path",
)


# ---------------------------------------------------------------------------
# RF image-quality QA layer — taxonomy + contract version
# (RF Image-Quality QA/QC Canonical Plan v0.2 — docs/rf_qa_canonical_plan_v0_1.md)
# ---------------------------------------------------------------------------

# § 12.3 — the QA contract version stamped onto every grade and override. v0.2
# is the current canonical-plan revision. Build-Step 1 places the constant;
# Build-Step 2+ stamps it onto run records once detectors/overrides produce a
# grade.
CONTRACT_VERSION = "v0.2"

# § 5.1 — the reason-code priority tuple. A frozen tuple; priority order
# controls UI surfacing prominence (the highest-priority flag on a frame is
# the "dominant reason" badge) and flag-precedence in debugging. It does NOT
# control visibility — every flag is always inspectable (Transparency Rule 1).
# This is the operator's structured drop-reason vocabulary at Phase-4 capture
# (§ 7.4 / § 8.3.5) and, from Build-Step 2, the detector flag namespace (§ 5.5).
IMAGE_QUALITY_FLAG_PRIORITY = (
    # orthogonal — taints every measurement
    "wrong_site",
    "segmenter_mislabel_wrong_class",
    # structural — femur
    "femur_out_of_frame",
    "femur_off_central_zone",
    # geometric / framing
    "edge_clip_lateral",   # informational only — Akadir 2026-05-19 C11 / 2026-05-24:
                           # taints visible_rf_area_mm2 / full_area_mm2 only (the C11
                           # coverage-limitation rule); QMT/STAR/thickness stay valid.
    "edge_clip_vertical",
    # gross intensity
    "saturation_high_global",
    "saturation_high_regional",
    "dark_clip_high",
    "tgc_banding",
    # gross sharpness
    "motion_blur",
    # coupling / near-field / artifact
    "air_gap_reverberation",
    "near_field_clutter",
    "vessel_shadow_in_roi",
    "posterior_acoustic_enhancement",
    # anatomical (deterministic — fat is segmented)
    "subq_fat_compressed",
    # ultrasound-specific (Tier-1, Conditional)
    "oblique_plane",
    "anisotropy_dropout_superficial",
    "anisotropy_dropout_deep",
    "deep_attenuation_high",
    # model-derived (Tier-2; [Active] in v0.2 — § 5.4)
    "segmentation_low_confidence",
    # orientation (named-deferred)
    "probe_orientation_flipped",
)


def canonical_image_quality_flag_order(flags) -> Tuple[str, ...]:
    """Return `flags` as a tuple ordered by IMAGE_QUALITY_FLAG_PRIORITY.

    Accepts any iterable (set, list, tuple) or None. Codes outside the taxonomy
    — e.g. a free-text reason not yet promoted to a code — are dropped here;
    those are carried separately in `drop_reason_other`. Used to canonicalise
    the operator's Phase-4 `drop_reason_codes` before they reach the ledger, so
    a frame's reason set is deterministic regardless of checkbox-click order.
    """
    if not flags:
        return ()
    chosen = set(flags)
    return tuple(code for code in IMAGE_QUALITY_FLAG_PRIORITY if code in chosen)


# ---------------------------------------------------------------------------
# Femur plateau helpers
# ---------------------------------------------------------------------------

def _dominant_femur_mask(mask: np.ndarray) -> np.ndarray:
    """Return the femur-class boolean mask with only the dominant blob kept.

    The QMT pipeline anchors its caliper to femur pixels. A real femur reads as
    one large contiguous blob; segmentation errors can produce tiny stray
    clusters of femur-labelled pixels mid-frame, and the plateau detector —
    which anchors on the *highest* femur pixel — gets hijacked by such a stray
    sitting above the real femur. (Bug surfaced on AGZ855 in the 2026-05-22
    operator review; root-caused by subagent A.)

    The guard keeps only the largest 8-connected component, and treats the
    frame as femur-absent (returns an all-zero mask) if that component is
    below `QMT_FEMUR_MIN_AREA_PX`. 8-conn labelling is implemented in pure
    numpy + Python to honour this module's pure-numpy discipline (no
    scipy/cv2). Outer loop iterates only over foreground pixels via
    `np.argwhere`, so cost is O(N_femur_pixels), not O(H*W).
    """
    femur = (mask == LABEL_FEMUR)
    if not femur.any():
        return np.zeros_like(femur)
    h, w = femur.shape
    visited = np.zeros_like(femur)
    foreground = np.argwhere(femur)
    neighbours = ((-1, -1), (-1, 0), (-1, 1),
                  (0, -1),           (0, 1),
                  (1, -1),  (1, 0),  (1, 1))
    best_area = 0
    best_pixels: List[Tuple[int, int]] = []
    for idx in range(len(foreground)):
        sr, sc = int(foreground[idx, 0]), int(foreground[idx, 1])
        if visited[sr, sc]:
            continue
        stack = [(sr, sc)]
        component: List[Tuple[int, int]] = []
        while stack:
            rr, cc = stack.pop()
            if rr < 0 or rr >= h or cc < 0 or cc >= w:
                continue
            if not femur[rr, cc] or visited[rr, cc]:
                continue
            visited[rr, cc] = True
            component.append((rr, cc))
            for dr, dc in neighbours:
                stack.append((rr + dr, cc + dc))
        if len(component) > best_area:
            best_area = len(component)
            best_pixels = component
    if best_area < QMT_FEMUR_MIN_AREA_PX:
        return np.zeros_like(femur)
    out = np.zeros_like(femur)
    rs, cs = zip(*best_pixels)
    out[np.array(rs), np.array(cs)] = True
    return _trim_femur_top_outliers(out)


def _trim_femur_top_outliers(
    femur: np.ndarray,
    outlier_px: int = QMT_FEMUR_TOP_OUTLIER_PX,
) -> np.ndarray:
    """Remove connected over-segmentation that pokes above the femur cortex.

    `_dominant_femur_mask` drops DISCONNECTED strays (largest-component), but a
    "hat" — femur-class pixels CONNECTED to the real cortex yet sitting well above
    it — survives the component filter and then hijacks the plateau anchor (the
    anchor is the highest femur pixel). This trims those top outliers: fit a robust
    Theil-Anonadi line to the per-column femur tops, and in any column whose top sits
    more than `outlier_px` (and a robust-MAD multiple) ABOVE that line, delete the
    pixels above the line, leaving the true cortex.

    Conservative by construction: only TOP pixels above the fitted line are removed
    (nothing below the cortex is touched — no thickening; QMT reads only the top
    edge), and a column's cortex on/below the line is never removed, so a passing
    frame cannot be demoted. A legitimately tilted cortex (slope within
    QMT_FEMUR_SLOPE_MAX_ABS) is preserved because the line follows the tilt; and if
    a protrusion were ever the majority, the line would fit IT and nothing above it
    would be trimmed (a no-op, not a corruption). Pure-numpy (module discipline).
    """
    cols = np.where(femur.any(axis=0))[0]
    if cols.size < _FEMUR_TRIM_MIN_COLS:
        return femur
    tops = np.array([int(np.argmax(femur[:, c])) for c in cols], dtype=np.float64)
    x = cols.astype(np.float64)
    # Theil-Anonadi: median pairwise slope, then median intercept. cols <= image
    # width, so the O(n^2) pair scan is cheap.
    i, j = np.triu_indices(cols.size, k=1)
    dx = x[j] - x[i]
    if not np.any(dx):
        return femur
    slope = float(np.median((tops[j] - tops[i]) / dx))
    intercept = float(np.median(tops - slope * x))
    line = slope * x + intercept
    resid = tops - line                          # < 0 => the top is ABOVE the line (protrusion)
    mad = float(np.median(np.abs(resid - np.median(resid))))
    thresh = max(float(outlier_px), 3.0 * 1.4826 * mad)
    protrusion = resid < -thresh
    if not np.any(protrusion):
        return femur
    out = femur.copy()
    for c, ln, is_p in zip(cols.tolist(), line.tolist(), protrusion.tolist()):
        if is_p:
            cutoff = int(round(ln))              # clip this column's top down to the cortex line
            if cutoff > 0:
                out[:cutoff, c] = False
    return out


def _femur_top_rows(mask: np.ndarray) -> Dict[int, int]:
    """Return {col: topmost_femur_row} for the *dominant* femur blob.

    Stray small clusters of mis-labelled femur pixels are filtered out by
    `_dominant_femur_mask` so they cannot hijack the plateau anchor."""
    femur = _dominant_femur_mask(mask)
    femur_edge: Dict[int, int] = {}
    for col in range(mask.shape[1]):
        rows = np.where(femur[:, col])[0]
        if len(rows):
            femur_edge[col] = int(rows.min())
    return femur_edge


def _median_smooth_boundary_rows(
    edge_col_to_row: Dict[int, int],
    window: int = FEMUR_SMOOTHING_WINDOW,
) -> Dict[int, int]:
    """Median-smooth a sparse boundary defined on image columns."""
    if not edge_col_to_row:
        return {}
    cols = sorted(edge_col_to_row)
    half = max(window // 2, 0)
    smoothed: Dict[int, int] = {}
    for idx, col in enumerate(cols):
        local_cols = cols[max(0, idx - half):min(len(cols), idx + half + 1)]
        smoothed[col] = int(round(float(np.median([edge_col_to_row[c] for c in local_cols]))))
    return smoothed


def _longest_contiguous_run(cols: List[int]) -> List[int]:
    """Return the longest contiguous run from an ascending column list."""
    if not cols:
        return []
    best: List[int] = []
    cur: List[int] = [cols[0]]
    for col in cols[1:]:
        if col == cur[-1] + 1:
            cur.append(col)
        else:
            if len(cur) > len(best):
                best = cur[:]
            cur = [col]
    if len(cur) > len(best):
        best = cur
    return best


def _plateau_columns_from_boundary(
    edge_col_to_row: Dict[int, int],
    slope_pct: float = QMT_PLATEAU_SLOPE_PCT,
) -> List[int]:
    """Return plateau columns from a femur-top boundary map."""
    if not edge_col_to_row:
        return []

    min_row = min(edge_col_to_row.values())
    near_min = [c for c, r in edge_col_to_row.items() if r <= min_row + 2]
    col_ref = int(np.median(near_min))

    slope = slope_pct / 100.0
    flat_cols = sorted(
        c for c, r in edge_col_to_row.items()
        if r <= min_row + abs(c - col_ref) * slope
    )
    if not flat_cols:
        return [col_ref]
    return _longest_contiguous_run(flat_cols)


def find_femur_plateau_columns(
    mask: np.ndarray,
    slope_pct: float = QMT_PLATEAU_SLOPE_PCT,
) -> Optional[Dict[int, int]]:
    """Return {col: actual_femur_top_row} for the longest plateau section.

    A column is accepted into the plateau when:
        femur_top[col] ≤ min_row + |col − col_ref| × (slope_pct / 100)

    i.e. the allowed depth below the minimum row grows linearly with
    distance from the reference column, at the rate of `slope_pct` pixels
    per 100 columns.  This is a slope criterion rather than a fixed pixel
    budget, making the cut-off scale-independent.

    col_ref is the median of all femur columns within 2 px of the minimum
    (robust against single-pixel noise at the minimum).

    Returns None if no femur pixels are present.
    """
    femur_edge = _femur_top_rows(mask)
    if not femur_edge:
        return None
    smoothed = _median_smooth_boundary_rows(femur_edge, window=FEMUR_SMOOTHING_WINDOW)
    plateau_cols = _plateau_columns_from_boundary(smoothed, slope_pct=slope_pct)
    if not plateau_cols:
        return None
    return {c: femur_edge[c] for c in plateau_cols}


def _plateau_ordered_cols(plateau: Dict[int, int]) -> List[int]:
    """Return plateau columns sorted centre-first (by distance from plateau mid)."""
    cols = sorted(plateau.keys())
    if not cols:
        return []
    centre = cols[len(cols) // 2]
    return sorted(cols, key=lambda c: abs(c - centre))


def _central_femur_zone_columns(
    mask: np.ndarray,
    margin_px: int = QMT_CENTRAL_ZONE_MARGIN_PX,
) -> List[int]:
    """Return femur-bearing columns in the central QMT support zone."""
    femur_edge = _femur_top_rows(mask)
    if not femur_edge:
        return []

    femur_cols = sorted(femur_edge)
    plateau = find_femur_plateau_columns(mask, slope_pct=QMT_PLATEAU_SLOPE_PCT)
    plateau_cols = sorted(plateau) if plateau is not None else []

    if len(plateau_cols) >= 3:
        lo = min(plateau_cols) - margin_px
        hi = max(plateau_cols) + margin_px
        zone_cols = [col for col in femur_cols if lo <= col <= hi]
        if zone_cols:
            return zone_cols

    center_col = int(np.median(femur_cols))
    return [col for col in femur_cols if abs(col - center_col) <= margin_px]


# ---------------------------------------------------------------------------
# Basic thickness helpers
# ---------------------------------------------------------------------------

def _max_contiguous_span(col_pixels: np.ndarray, label: int) -> int:
    """Return the maximum contiguous run-length of `label` in a 1-D array."""
    in_run = False
    best = 0
    cur = 0
    for v in col_pixels:
        if v == label:
            cur += 1
            if cur > best:
                best = cur
            in_run = True
        else:
            cur = 0
            in_run = False
    return best


def max_span_column(mask: np.ndarray, label: int) -> Optional[int]:
    """Return the column index where `label` has its maximum contiguous span.

    Returns None if the label is absent from the mask.
    Ties are broken by taking the first (leftmost) maximum column.
    """
    best_span = 0
    best_col = None
    for col in range(mask.shape[1]):
        span = _max_contiguous_span(mask[:, col], label)
        if span > best_span:
            best_span = span
            best_col = col
    return best_col


def rf_thickness_px(mask: np.ndarray) -> Optional[int]:
    """Maximum contiguous RF (label-1) column span across all columns."""
    col = max_span_column(mask, LABEL_RF)
    if col is None:
        return None
    return _max_contiguous_span(mask[:, col], LABEL_RF)


def vi_max_extent_column(mask: np.ndarray) -> Optional[int]:
    """Return the column where VI has the greatest vertical extent.

    When a femur plateau is available, the search is restricted to plateau
    columns and VI's lower boundary is clamped to just above the plateau
    femur row (preventing over-measurement at the curved femur sides).
    Without femur, falls back to the original full-image search.
    """
    plateau = find_femur_plateau_columns(mask)

    search_cols: List[int] = list(plateau.keys()) if plateau is not None else list(range(mask.shape[1]))

    best_extent = -1
    best_col = None
    for col in search_cols:
        vi_rows = np.where(mask[:, col] == LABEL_VI)[0]
        if len(vi_rows) == 0:
            continue
        vi_top = int(vi_rows.min())
        if plateau is not None and col in plateau:
            vi_bot = min(int(vi_rows.max()), plateau[col] - 1)
        else:
            vi_bot = int(vi_rows.max())
        extent = vi_bot - vi_top + 1
        if extent > best_extent and extent > 0:
            best_extent = extent
            best_col = col
    return best_col


def vi_thickness_px(mask: np.ndarray) -> Optional[int]:
    """VI thickness: maximum vertical extent of VI, bounded by the femur plateau.

    When a femur plateau is available, VI's lower boundary is clamped to the
    VI pixel just above the plateau femur row (preventing over-measurement at
    curved femur sides).  Without femur, falls back to the full top-to-bottom
    extent.
    """
    plateau = find_femur_plateau_columns(mask)
    col = vi_max_extent_column(mask)        # already plateau-aware
    if col is None:
        return None
    vi_rows = np.where(mask[:, col] == LABEL_VI)[0]
    if len(vi_rows) == 0:
        return None
    vi_top = int(vi_rows.min())
    if plateau is not None and col in plateau:
        vi_bot = min(int(vi_rows.max()), plateau[col] - 1)
    else:
        vi_bot = int(vi_rows.max())
    if vi_bot <= vi_top:
        return None
    return vi_bot - vi_top + 1


def vi_thickness_perpendicular_px(
    mask: np.ndarray,
    tangent_window: int = 15,
) -> Tuple[Optional[int], Optional[Tuple[int, int, int, int]]]:
    """Measure VI thickness perpendicular to its top (inner) boundary curve.

    VI has a partial-donut/arc shape. A simple column-based measurement
    overestimates thickness because the measurement line cuts diagonally
    across the curved boundaries. This function:

      1. For each column containing VI, estimates the local tangent of the
         VI top edge using neighboring columns within `tangent_window`.
      2. Computes the inward normal (perpendicular to the tangent, directed
         into the muscle body).
      3. Casts a ray along that normal from the top VI boundary pixel.
      4. Walks the ray counting contiguous VI pixels (2 px noise tolerance).
      5. Returns the maximum perpendicular thickness found across all columns
         and the corresponding segment endpoints for visualization.

    Upper boundary: top of VI label (RF-VI interface or fascia below RF).
    Lower boundary: wherever VI pixels end along the perpendicular ray.

    Returns:
        (thickness_px, (start_row, start_col, end_row, end_col))
        or (None, None) if VI is absent or too sparse for tangent estimation.
    """
    h, w = mask.shape

    # Per-column top edge of VI
    top_edge: Dict[int, int] = {}
    for col in range(w):
        vi_rows = np.where(mask[:, col] == LABEL_VI)[0]
        if len(vi_rows) > 0:
            top_edge[col] = int(vi_rows.min())

    cols = sorted(top_edge.keys())
    if len(cols) < max(3, tangent_window // 2):
        return None, None

    half = tangent_window // 2
    max_thickness = 0
    best_segment: Optional[Tuple[int, int, int, int]] = None

    for col in cols:
        # Representative columns for tangent: go half-window left/right
        left_col = max(cols[0], col - half)
        right_col = min(cols[-1], col + half)
        if left_col not in top_edge or right_col not in top_edge or left_col == right_col:
            continue

        # Tangent of top edge: direction vector (Δcol, Δrow)
        dc = float(right_col - left_col)
        dr = float(top_edge[right_col] - top_edge[left_col])

        # Inward normal: rotate tangent 90° toward interior of VI (downward = +row)
        # Tangent (dc, dr) → candidate normals: (-dr, dc) and (dr, -dc)
        norm_c = -dr
        norm_r = dc
        if norm_r < 0:                      # ensure we point downward into muscle
            norm_c, norm_r = -norm_c, -norm_r

        length = math.sqrt(norm_c ** 2 + norm_r ** 2)
        if length < 1e-6:
            continue
        norm_c /= length
        norm_r /= length

        # Ray origin: top VI boundary at this column
        start_r = float(top_edge[col])
        start_c = float(col)

        # Walk the ray, counting how far VI extends
        thickness = 0
        end_ri = int(start_r)
        end_ci = int(start_c)
        non_vi_streak = 0

        for step in range(1, h + w):
            ri = int(round(start_r + step * norm_r))
            ci = int(round(start_c + step * norm_c))

            if not (0 <= ri < h and 0 <= ci < w):
                break

            if mask[ri, ci] == LABEL_VI:
                thickness = step
                end_ri, end_ci = ri, ci
                non_vi_streak = 0
            else:
                non_vi_streak += 1
                if non_vi_streak > 2:       # 2 px noise tolerance
                    break

        if thickness > max_thickness:
            max_thickness = thickness
            best_segment = (int(start_r), int(start_c), end_ri, end_ci)

    return (max_thickness if max_thickness > 0 else None), best_segment


# ---------------------------------------------------------------------------
# Perpendicular QMT (2c)
# ---------------------------------------------------------------------------

def _boundary_tangent(
    edge_col_to_row: Dict[int, int],
    col: int,
    half_win: int,
) -> Optional[Tuple[float, float]]:
    """Return unit tangent vector (dc, dr) of a boundary curve at `col`.

    Uses columns within ±half_win to estimate slope via least-squares.
    Returns None if fewer than 3 neighbours are available.
    """
    cols_near = [c for c in range(col - half_win, col + half_win + 1)
                 if c in edge_col_to_row]
    if len(cols_near) < 3:
        return None
    xs = np.array([float(c) for c in cols_near])
    ys = np.array([float(edge_col_to_row[c]) for c in cols_near])
    # Linear fit: row = a*col + b
    a = float(np.polyfit(xs, ys, 1)[0])
    length = math.sqrt(1.0 + a ** 2)
    return (1.0 / length, a / length)          # (dc, dr), unit vector


def _boundary_slope(
    edge_col_to_row: Dict[int, int],
    col: int,
    half_win: int = QMT_BOUNDARY_SLOPE_HALF_WINDOW,
) -> Optional[float]:
    """Return fitted row-per-column slope at `col`, or None if too sparse."""
    cols_near = [c for c in range(col - half_win, col + half_win + 1) if c in edge_col_to_row]
    if len(cols_near) < 3:
        return None
    xs = np.array([float(c) for c in cols_near])
    ys = np.array([float(edge_col_to_row[c]) for c in cols_near])
    return float(np.polyfit(xs, ys, 1)[0])


def qmt_thickness_perpendicular(
    mask: np.ndarray,
    tangent_window: int = 20,
) -> Tuple[Optional[float], Optional[Tuple[int, int, int, int]], str]:
    """Measure QMT as the optimally-perpendicular segment from upper fascia to femur.

    Approach:
        For each column in the femur extent:
        1. Sample the upper fascia (or top of RF) top edge and femur top edge.
        2. Estimate the local tangent of each boundary curve via least-squares
           over a ±tangent_window neighbourhood.
        3. Inward normal of each boundary = tangent rotated 90° toward muscle.
        4. Average the two unit normals → optimal measurement direction n_avg.
        5. Ray origin = upper fascia point. Walk ray along n_avg.
        6. Record Euclidean segment length (in *pixel* space) when ray exits femur.
        7. Return the segment with maximum length.

    Physical distance:
        Caller must convert using BOTH spacings:
            mm = sqrt( (Δrow·mm_per_y)² + (Δcol·mm_per_x)² )
        because the line is slanted.

    Returns:
        (euclid_px, (start_r, start_c, end_r, end_c), provenance)
        provenance: 'standard_qmt' | 'femur_supported_proxy' | 'no_femur_proxy' | 'no_measurement'
    """
    h, w = mask.shape
    half = tangent_window // 2

    # --- Build upper boundary: top of fascia per column; fallback to top of RF ---
    upper_edge: Dict[int, int] = {}
    for col in range(w):
        fascia = np.where(mask[:, col] == LABEL_FASCIA)[0]
        if len(fascia):
            upper_edge[col] = int(fascia.min())
        else:
            rf = np.where(mask[:, col] == LABEL_RF)[0]
            if len(rf):
                upper_edge[col] = int(rf.min())

    # --- Build lower boundary: top of femur per column ---
    # Reuse the canonical helper so the dominant-femur guard applies here too
    # — without it a stray mis-labelled femur cluster mid-frame can hijack the
    # caliper (the AGZ855 phantom-femur bug).
    femur_edge = _femur_top_rows(mask)

    # Standard path requires both boundaries
    valid_cols = sorted(set(upper_edge) & set(femur_edge))

    if len(valid_cols) < 3:
        # Fallback: straight vertical at image centre
        center_col = w // 2
        rf_rows = np.where(mask[:, center_col] == LABEL_RF)[0]
        vi_rows = np.where(mask[:, center_col] == LABEL_VI)[0]
        tops, bots = [], []
        for rows in (rf_rows, vi_rows):
            if len(rows):
                tops.append(int(rows.min()))
                bots.append(int(rows.max()))
        if not tops:
            return None, None, "no_measurement"
        sr, er = min(tops), max(bots)
        px = float(er - sr)
        provenance = "femur_supported_proxy" if femur_edge else "no_femur_proxy"
        return px, (sr, center_col, er, center_col), provenance

    best_px: float = 0.0
    best_seg: Optional[Tuple[int, int, int, int]] = None

    for col in valid_cols:
        # Tangents
        tang_upper = _boundary_tangent(upper_edge, col, half)
        tang_femur = _boundary_tangent(femur_edge, col, half)

        if tang_upper is None or tang_femur is None:
            continue

        # Inward normals (into muscle = downward, i.e. positive row direction)
        def _inward_normal(dc: float, dr: float) -> Tuple[float, float]:
            # Rotate tangent 90°: two candidates (-dr, dc) and (dr, -dc)
            n1_c, n1_r = -dr, dc
            n2_c, n2_r = dr, -dc
            # Pick the one with positive row component (points downward)
            if n1_r >= 0:
                return n1_c, n1_r
            return n2_c, n2_r

        nu_c, nu_r = _inward_normal(*tang_upper)
        nf_c, nf_r = _inward_normal(*tang_femur)

        # Average normal and re-normalise
        avg_c = nu_c + nf_c
        avg_r = nu_r + nf_r
        avg_len = math.sqrt(avg_c ** 2 + avg_r ** 2)
        if avg_len < 1e-6:
            continue
        avg_c /= avg_len
        avg_r /= avg_len

        # Ensure direction is downward (positive row)
        if avg_r < 0:
            avg_c, avg_r = -avg_c, -avg_r

        # Ray origin at upper fascia
        start_r = float(upper_edge[col])
        start_c = float(col)

        # Walk ray until we exit the femur top or leave image
        # Strategy: walk until we hit femur pixels, then record when we leave
        end_ri, end_ci = int(start_r), int(start_c)
        in_femur = False
        femur_exit_r, femur_exit_c = end_ri, end_ci

        for step in range(1, h + w):
            ri = int(round(start_r + step * avg_r))
            ci = int(round(start_c + step * avg_c))
            if not (0 <= ri < h and 0 <= ci < w):
                break
            label = mask[ri, ci]
            if label == LABEL_FEMUR:
                in_femur = True
                femur_exit_r, femur_exit_c = ri, ci
            elif in_femur:
                break   # exited femur → done

        if not in_femur:
            continue

        # Euclidean pixel distance
        dx = femur_exit_c - int(start_c)
        dy = femur_exit_r - int(start_r)
        px = math.sqrt(float(dx ** 2 + dy ** 2))

        if px > best_px:
            best_px = px
            best_seg = (int(start_r), int(start_c), femur_exit_r, femur_exit_c)

    if best_seg is None:
        return None, None, "no_measurement"

    return best_px, best_seg, "standard_qmt"


# ---------------------------------------------------------------------------
# Compound QMT measurement (column-based, kept for frame-consistency fallback)
# ---------------------------------------------------------------------------

def _column_qmt_measurement(
    mask: np.ndarray, col: int, strict: bool = True
) -> Optional[int]:
    """Try to get a valid QMT measurement at a single column.

    Returns thickness_px (femur_top_y - fascia_top_y) if the column
    has femur, has fascia or RF, and the path between them passes the
    contamination check.  Returns None if the column is invalid.

    Args:
        strict: When True (default), only {background, RF, VI, fascia}
                are allowed in the path (ALLOWED_IN_QMT_PATH).
                When False, VM (label 3) and VL (label 4) are also
                permitted — use this relaxed mode only as a fallback
                when no strict column exists.
    """
    bounds = _standard_qmt_bounds(mask, col, strict=strict)
    if bounds is None:
        return None
    fascia_top_y, femur_top_y = bounds
    return femur_top_y - fascia_top_y


def _upper_muscle_boundary_y(mask: np.ndarray, col: int) -> Optional[int]:
    """Return the first muscle pixel below upper fascia/RF for one column."""
    col_data = mask[:, col]
    rf_rows = np.where(col_data == LABEL_RF)[0]
    fascia_rows = np.where(col_data == LABEL_FASCIA)[0]

    if len(fascia_rows) > 0 and len(rf_rows) > 0:
        upper_fascia = fascia_rows[fascia_rows < int(rf_rows.min())]
        if len(upper_fascia) > 0:
            return int(upper_fascia.max()) + 1
        return int(rf_rows.min())
    if len(rf_rows) > 0:
        return int(rf_rows.min())
    return None


def _upper_muscle_boundary_rows(mask: np.ndarray) -> Dict[int, int]:
    """Return per-column upper muscle boundary rows where RF/fascia exist."""
    result: Dict[int, int] = {}
    for col in range(mask.shape[1]):
        top_y = _upper_muscle_boundary_y(mask, col)
        if top_y is not None:
            result[col] = int(top_y)
    return result


def _standard_qmt_bounds(
    mask: np.ndarray,
    col: int,
    strict: bool = True,
) -> Optional[Tuple[int, int]]:
    """Return (top_y, bottom_y) for a femur-based QMT path at one column.

    top_y is the first muscle pixel immediately below the upper fascia.
    bottom_y is the topmost femur pixel.
    """
    col_data = mask[:, col]

    femur_rows = np.where(col_data == LABEL_FEMUR)[0]
    if len(femur_rows) == 0:
        return None
    femur_top_y = int(femur_rows.min())

    fascia_top_y = _upper_muscle_boundary_y(mask, col)
    if fascia_top_y is None:
        return None

    if fascia_top_y >= femur_top_y:
        return None

    path = col_data[fascia_top_y:femur_top_y]
    if len(path) == 0:
        return None

    allowed = ALLOWED_IN_QMT_PATH if strict else (ALLOWED_IN_QMT_PATH | {3, 4})
    contaminant_mask = ~np.isin(path, list(allowed))
    contamination_ratio = contaminant_mask.sum() / len(path)
    if contamination_ratio > QMT_CONTAMINATION_TOL:
        return None

    return fascia_top_y, femur_top_y


def _zone_center_col(cols: List[int]) -> Optional[int]:
    if not cols:
        return None
    return int(np.median(cols))


def _ordered_qmt_candidate_columns(mask: np.ndarray) -> List[int]:
    """Return femur columns ordered central-zone-first, then centre-first."""
    femur_edge = _femur_top_rows(mask)
    if not femur_edge:
        return []

    femur_cols = sorted(femur_edge)
    central_zone = _central_femur_zone_columns(mask)
    central_set = set(central_zone)
    zone_center = _zone_center_col(central_zone) or int(np.median(femur_cols))
    femur_center = int(np.median(femur_cols))

    central_ordered = sorted(central_zone, key=lambda c: (abs(c - zone_center), c))
    outer_cols = sorted(
        [c for c in femur_cols if c not in central_set],
        key=lambda c: (abs(c - femur_center), c),
    )
    return central_ordered + outer_cols


def _ordered_qmt_flags(flags: List[str]) -> List[str]:
    return [flag for flag in QMT_VALIDITY_FLAG_PRIORITY if flag in set(flags)]


def _path_muscle_stats(
    mask: np.ndarray,
    col: int,
    top_y: int,
    bottom_y: int,
) -> Dict[str, object]:
    path = mask[top_y:bottom_y, col]
    rf_count = int(np.sum(path == LABEL_RF))
    vi_count = int(np.sum(path == LABEL_VI))
    muscle_px = rf_count + vi_count
    occupancy = float(muscle_px) / float(len(path)) if len(path) > 0 else 0.0
    return {
        "rf_px": rf_count if rf_count > 0 else None,
        "vi_px": vi_count if vi_count > 0 else None,
        "has_both": rf_count > 0 and vi_count > 0,
        "occupancy": occupancy,
    }


def _qmt_geometry(mask: np.ndarray) -> Dict[str, object]:
    femur_edge = _femur_top_rows(mask)
    if not femur_edge:
        return {
            "femur_present": False,
            "femur_edge": {},
            "smoothed_femur_edge": {},
            "plateau_core": {},
            "plateau_core_set": set(),
            "central_zone_cols": [],
            "central_zone_set": set(),
            "central_zone_center": None,
            "top_boundary_rows": _upper_muscle_boundary_rows(mask),
        }

    smoothed_femur_edge = _median_smooth_boundary_rows(
        femur_edge,
        window=FEMUR_SMOOTHING_WINDOW,
    )
    plateau_core = find_femur_plateau_columns(mask, slope_pct=QMT_PLATEAU_SLOPE_PCT) or {}
    central_zone_cols = _central_femur_zone_columns(mask, margin_px=QMT_CENTRAL_ZONE_MARGIN_PX)
    return {
        "femur_present": True,
        "femur_edge": femur_edge,
        "smoothed_femur_edge": smoothed_femur_edge,
        "plateau_core": plateau_core,
        "plateau_core_set": set(plateau_core),
        "central_zone_cols": central_zone_cols,
        "central_zone_set": set(central_zone_cols),
        "central_zone_center": _zone_center_col(central_zone_cols),
        "top_boundary_rows": _upper_muscle_boundary_rows(mask),
    }


def _standard_qmt_candidate_details(mask: np.ndarray, col: int, geometry: Dict[str, object]) -> Optional[Dict]:
    if col not in geometry.get("central_zone_set", set()):
        return None

    bounds = _standard_qmt_bounds(mask, col, strict=True)
    if bounds is None:
        return None

    top_y, bottom_y = bounds
    stats = _path_muscle_stats(mask, col, top_y, bottom_y)
    smoothed_femur_edge = geometry.get("smoothed_femur_edge", {})
    top_boundary_rows = geometry.get("top_boundary_rows", {})
    femur_slope = _boundary_slope(smoothed_femur_edge, col, half_win=QMT_BOUNDARY_SLOPE_HALF_WINDOW)
    fascia_slope = _boundary_slope(top_boundary_rows, col, half_win=QMT_BOUNDARY_SLOPE_HALF_WINDOW)

    flags: List[str] = []
    if stats["occupancy"] < QMT_MUSCLE_OCCUPANCY_MIN:
        flags.append("low_muscle_occupancy")
    if femur_slope is not None and abs(femur_slope) > QMT_FEMUR_SLOPE_MAX_ABS:
        flags.append("femur_slope_high")
    if fascia_slope is not None and abs(fascia_slope) > QMT_FASCIA_SLOPE_MAX_ABS:
        flags.append("fascia_slope_high")
    if not stats["has_both"]:
        flags.append("rf_or_vi_missing_in_path")

    plateau_core_set = geometry.get("plateau_core_set", set())
    zone_center = geometry.get("central_zone_center")
    thickness = bottom_y - top_y
    score = (
        int(col in plateau_core_set),
        int(bool(stats["has_both"])),
        float(stats["occupancy"]),
        -(abs(col - zone_center) if zone_center is not None else 0),
        -(abs(femur_slope) if femur_slope is not None else 0.0),
        -(abs(fascia_slope) if fascia_slope is not None else 0.0),
        thickness,
        -col,
    )

    return {
        "col": col,
        "top_y": top_y,
        "bottom_y": bottom_y,
        "rf_px": stats["rf_px"],
        "vi_px": stats["vi_px"],
        "has_both": bool(stats["has_both"]),
        "occupancy": float(stats["occupancy"]),
        "femur_slope_abs": abs(femur_slope) if femur_slope is not None else None,
        "fascia_slope_abs": abs(fascia_slope) if fascia_slope is not None else None,
        "plateau_supported": col in plateau_core_set,
        "central_zone_supported": True,
        "flags": _ordered_qmt_flags(flags),
        "eligible": (
            stats["occupancy"] >= QMT_MUSCLE_OCCUPANCY_MIN
            and (femur_slope is None or abs(femur_slope) <= QMT_FEMUR_SLOPE_MAX_ABS)
            and (fascia_slope is None or abs(fascia_slope) <= QMT_FASCIA_SLOPE_MAX_ABS)
        ),
        "score": score,
    }


def _select_qmt_column(mask: np.ndarray, strict: bool = True) -> Optional[int]:
    """Return the best QMT column for strict production or relaxed debug mode."""
    if strict:
        geometry = _qmt_geometry(mask)
        candidates: List[Dict] = []
        for col in geometry.get("central_zone_cols", []):
            candidate = _standard_qmt_candidate_details(mask, col, geometry)
            if candidate is not None and candidate["eligible"]:
                candidates.append(candidate)
        if not candidates:
            return None
        best = max(candidates, key=lambda item: item["score"])
        return int(best["col"])

    for col in _ordered_qmt_candidate_columns(mask):
        if _column_qmt_measurement(mask, col, strict=False) is not None:
            return col
    return None


def _fallback_qmt_bounds(mask: np.ndarray, col: int) -> Optional[Tuple[int, int]]:
    """Return (top_y, bottom_y) for the RF+VI proxy span at one column."""
    rf_rows = np.where(mask[:, col] == LABEL_RF)[0]
    vi_rows = np.where(mask[:, col] == LABEL_VI)[0]

    if len(rf_rows) == 0 and len(vi_rows) == 0:
        return None

    if len(rf_rows) > 0 and len(vi_rows) > 0:
        top_y = int(rf_rows.min())
        bottom_y = int(vi_rows.max())
        if bottom_y <= top_y:
            top_y = min(int(rf_rows.min()), int(vi_rows.min()))
            bottom_y = max(int(rf_rows.max()), int(vi_rows.max()))
    else:
        rows = rf_rows if len(rf_rows) > 0 else vi_rows
        top_y = int(rows.min())
        bottom_y = int(rows.max())

    if bottom_y <= top_y:
        return None

    return top_y, bottom_y


def _proxy_candidate_details(
    mask: np.ndarray,
    col: int,
    geometry: Dict[str, object],
    *,
    provenance: str,
) -> Optional[Dict]:
    bounds = _fallback_qmt_bounds(mask, col)
    if bounds is None:
        return None

    top_y, bottom_y = bounds
    stats = _path_muscle_stats(mask, col, top_y, bottom_y)
    central_zone_set = geometry.get("central_zone_set", set())
    zone_center = geometry.get("central_zone_center")

    if provenance == "no_femur_proxy":
        rf_vi_cols = sorted(
            c for c in range(mask.shape[1])
            if _fallback_qmt_bounds(mask, c) is not None
        )
        muscle_center = _zone_center_col(rf_vi_cols)
        zone_center = muscle_center

    smoothed_femur_edge = geometry.get("smoothed_femur_edge", {})
    top_boundary_rows = geometry.get("top_boundary_rows", {})
    femur_slope = _boundary_slope(smoothed_femur_edge, col, half_win=QMT_BOUNDARY_SLOPE_HALF_WINDOW)
    fascia_slope = _boundary_slope(top_boundary_rows, col, half_win=QMT_BOUNDARY_SLOPE_HALF_WINDOW)

    flags: List[str] = []
    if provenance == "no_femur_proxy":
        flags.append("femur_absent")
    if geometry.get("femur_present", False) and col not in central_zone_set:
        flags.append("outside_central_zone")
    if stats["occupancy"] < QMT_MUSCLE_OCCUPANCY_MIN:
        flags.append("low_muscle_occupancy")
    if geometry.get("femur_present", False) and femur_slope is not None and abs(femur_slope) > QMT_FEMUR_SLOPE_MAX_ABS:
        flags.append("femur_slope_high")
    if fascia_slope is not None and abs(fascia_slope) > QMT_FASCIA_SLOPE_MAX_ABS:
        flags.append("fascia_slope_high")
    if not stats["has_both"]:
        flags.append("rf_or_vi_missing_in_path")

    thickness = bottom_y - top_y
    score = (
        int(bool(stats["has_both"])),
        float(stats["occupancy"]),
        -(abs(col - zone_center) if zone_center is not None else 0),
        thickness,
        -col,
    )

    plateau_core_set = geometry.get("plateau_core_set", set())
    return {
        "col": col,
        "top_y": top_y,
        "bottom_y": bottom_y,
        "rf_px": None,
        "vi_px": None,
        "has_both": bool(stats["has_both"]),
        "occupancy": float(stats["occupancy"]),
        "femur_slope_abs": abs(femur_slope) if femur_slope is not None else None,
        "fascia_slope_abs": abs(fascia_slope) if fascia_slope is not None else None,
        "plateau_supported": col in plateau_core_set,
        "central_zone_supported": col in central_zone_set,
        "flags": _ordered_qmt_flags(flags),
        "score": score,
        "provenance": provenance,
    }


def _select_proxy_qmt_candidate(mask: np.ndarray, geometry: Dict[str, object]) -> Optional[Dict]:
    femur_present = bool(geometry.get("femur_present", False))
    central_zone_cols = geometry.get("central_zone_cols", [])

    if femur_present:
        central_candidates = [
            _proxy_candidate_details(mask, col, geometry, provenance="femur_supported_proxy")
            for col in central_zone_cols
        ]
        central_candidates = [candidate for candidate in central_candidates if candidate is not None]
        if central_candidates:
            return max(central_candidates, key=lambda item: item["score"])

        all_candidates = [
            _proxy_candidate_details(mask, col, geometry, provenance="femur_supported_proxy")
            for col in range(mask.shape[1])
        ]
        all_candidates = [candidate for candidate in all_candidates if candidate is not None]
        if not all_candidates:
            return None
        return max(all_candidates, key=lambda item: item["score"])

    all_candidates = [
        _proxy_candidate_details(mask, col, geometry, provenance="no_femur_proxy")
        for col in range(mask.shape[1])
    ]
    all_candidates = [candidate for candidate in all_candidates if candidate is not None]
    if not all_candidates:
        return None
    return max(all_candidates, key=lambda item: item["score"])


def _strict_qmt_annotation_columns(mask: np.ndarray, primary: int, geometry: Optional[Dict[str, object]] = None) -> List[int]:
    """Return primary plus nearest eligible left/right standard-QMT columns."""
    geometry = geometry or _qmt_geometry(mask)
    candidates: List[Dict] = []
    for col in geometry.get("central_zone_cols", []):
        candidate = _standard_qmt_candidate_details(mask, col, geometry)
        if candidate is not None and candidate["eligible"]:
            candidates.append(candidate)

    cols = [primary]
    left = [candidate["col"] for candidate in candidates if int(candidate["col"]) < int(primary)]
    right = [candidate["col"] for candidate in candidates if int(candidate["col"]) > int(primary)]
    if left:
        cols.append(int(max(left)))
    if right:
        cols.append(int(min(right)))
    return cols


def _femur_cleaned_mask(mask: np.ndarray) -> np.ndarray:
    """`mask` with the femur reduced to its dominant blob — the SAME cleanup the
    display applies (`visualization.post_process_mask_for_display`): femur pixels
    outside `_dominant_femur_mask` (disconnected islands + the top-outlier trim)
    are set to background.

    Applied at the head of `_qmt_measurement_details` so the measurement anchors on
    the femur the operator actually sees. Before this, `_femur_top_rows` cleaned the
    femur for column selection but the depth read (`_standard_qmt_bounds`) took the
    RAW femur `.min()`, so on a frame whose femur has a shallow over-segmentation the
    QMT anchored ABOVE the displayed (cleaned) cortex — the caliper visibly floating
    over the trimmed-away pixels (Akadir 2026-07-16, AKA019-1E7A / 20260702_143219587)."""
    dominant = _dominant_femur_mask(mask)
    strays = (mask == LABEL_FEMUR) & ~dominant
    if not strays.any():
        return mask
    out = mask.copy()
    out[strays] = 0
    return out


def _qmt_measurement_details(mask: np.ndarray) -> Dict:
    """Return the canonical QMT measurement details for this mask."""
    mask = _femur_cleaned_mask(mask)
    geometry = _qmt_geometry(mask)

    standard_candidates: List[Dict] = []
    for col in geometry.get("central_zone_cols", []):
        candidate = _standard_qmt_candidate_details(mask, col, geometry)
        if candidate is not None and candidate["eligible"]:
            standard_candidates.append(candidate)

    if standard_candidates:
        best = max(standard_candidates, key=lambda item: item["score"])
        qmt_col = int(best["col"])
        return {
            "qmt_px": int(best["bottom_y"]) - int(best["top_y"]),
            "qmt_provenance": "standard_qmt",
            "qmt_column": qmt_col,
            "qmt_top_y": int(best["top_y"]),
            "qmt_bottom_y": int(best["bottom_y"]),
            "qmt_rf_px": best["rf_px"],
            "qmt_vi_px": best["vi_px"],
            "qmt_annotation_columns": _strict_qmt_annotation_columns(mask, qmt_col, geometry=geometry),
            "qmt_muscle_occupancy_pct": round(float(best["occupancy"]) * 100.0, 2),
            "qmt_plateau_supported": bool(best["plateau_supported"]),
            "qmt_central_zone_supported": bool(best["central_zone_supported"]),
            "qmt_femur_slope_abs": round(float(best["femur_slope_abs"]), 3) if best["femur_slope_abs"] is not None else None,
            "qmt_fascia_slope_abs": round(float(best["fascia_slope_abs"]), 3) if best["fascia_slope_abs"] is not None else None,
            "qmt_validity_flags": list(best["flags"]),
            "qmt_guidance_status": "good_standard_window",
        }

    proxy = _select_proxy_qmt_candidate(mask, geometry)
    if proxy is None:
        flags = ["femur_absent"] if not geometry.get("femur_present", False) else []
        return {
            "qmt_px": None,
            "qmt_provenance": "no_measurement",
            "qmt_column": None,
            "qmt_top_y": None,
            "qmt_bottom_y": None,
            "qmt_rf_px": None,
            "qmt_vi_px": None,
            "qmt_annotation_columns": [],
            "qmt_muscle_occupancy_pct": None,
            "qmt_plateau_supported": False,
            "qmt_central_zone_supported": False,
            "qmt_femur_slope_abs": None,
            "qmt_fascia_slope_abs": None,
            "qmt_validity_flags": _ordered_qmt_flags(flags),
            "qmt_guidance_status": "no_measurement",
        }

    qmt_col = int(proxy["col"])
    guidance_status = "proxy_due_to_missing_femur" if proxy["provenance"] == "no_femur_proxy" else "proxy_due_to_geometry"
    return {
        "qmt_px": int(proxy["bottom_y"]) - int(proxy["top_y"]),
        "qmt_provenance": proxy["provenance"],
        "qmt_column": qmt_col,
        "qmt_top_y": int(proxy["top_y"]),
        "qmt_bottom_y": int(proxy["bottom_y"]),
        "qmt_rf_px": None,
        "qmt_vi_px": None,
        "qmt_annotation_columns": [qmt_col],
        "qmt_muscle_occupancy_pct": round(float(proxy["occupancy"]) * 100.0, 2),
        "qmt_plateau_supported": bool(proxy["plateau_supported"]),
        "qmt_central_zone_supported": bool(proxy["central_zone_supported"]),
        "qmt_femur_slope_abs": round(float(proxy["femur_slope_abs"]), 3) if proxy["femur_slope_abs"] is not None else None,
        "qmt_fascia_slope_abs": round(float(proxy["fascia_slope_abs"]), 3) if proxy["fascia_slope_abs"] is not None else None,
        "qmt_validity_flags": list(proxy["flags"]),
        "qmt_guidance_status": guidance_status,
    }


def frame_qmt_validity_flags(mask: np.ndarray) -> Tuple[str, ...]:
    """The frame's QMT validity flags — the column-wise QMT verdict, lifted to
    the frame level.

    Runs the canonical QMT measurement on the multi-class `mask` and returns
    its `qmt_validity_flags` (a subset of QMT_VALIDITY_FLAG_PRIORITY, in
    priority order). The RF image-quality QA layer (RF Image-Quality QA plan
    § 5.2) reuses this rather than re-deriving femur geometry: `femur_absent`
    here is the signal behind the frame-level `femur_out_of_frame` detector,
    and `outside_central_zone` behind `femur_off_central_zone` — "lift the
    column-wise verdict to a frame-level flag" without a second code path.
    """
    return tuple(_qmt_measurement_details(mask)["qmt_validity_flags"])


def compound_thickness_px(
    mask: np.ndarray,
) -> Tuple[Optional[int], str]:
    """Compute quadriceps muscle thickness (QMT) in pixels.

    Returns (thickness_px, provenance) where provenance is one of:
        'standard_qmt'          — central-geometry-valid vertical fascia→femur path
        'femur_supported_proxy' — femur present but only proxy RF+VI span is accepted
        'no_femur_proxy'        — femur absent; central RF+VI proxy span used
        'no_measurement'        — nothing measurable
    """
    details = _qmt_measurement_details(mask)
    return details["qmt_px"], details["qmt_provenance"]


def get_qmt_annotation_columns(mask: np.ndarray) -> List[int]:
    """Return column indices for QMT visualization (up to 3 dashed lines).

    The first column is always the true measurement column used by
    compound_thickness_px. For strict standard QMT, the app returns the
    primary column plus nearest eligible left/right support columns inside
    the central femur zone. For proxy QMT, only the proxy measurement
    column is returned.
    """
    return list(_qmt_measurement_details(mask)["qmt_annotation_columns"])


def qmt_muscle_portions_px(mask: np.ndarray) -> Tuple[Optional[int], Optional[int]]:
    """Return (rf_portion_px, vi_portion_px) within the QMT fascia→femur path.

    Uses _select_qmt_column for column selection (same as compound_thickness_px)
    so that the sub-lengths always correspond to the measurement column.
    Returns (None, None) if no standard QMT column is found.
    """
    details = _qmt_measurement_details(mask)
    return details["qmt_rf_px"], details["qmt_vi_px"]


# ---------------------------------------------------------------------------
# Physical unit conversion
# ---------------------------------------------------------------------------

def px_to_mm(
    px: Optional[float],
    pixel_spacing_y: Optional[float],
    dcm_rows: Optional[float],
    mask_height: Optional[float],
) -> Optional[float]:
    """Convert vertical pixel count → mm using mask-space spacing.

    mm_per_mask_y = pixel_spacing_y * dcm_rows / mask_height
    """
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in
           [px, pixel_spacing_y, dcm_rows, mask_height]):
        return None
    if mask_height == 0:
        return None
    mm_per_y = pixel_spacing_y * dcm_rows / mask_height
    return round(px * mm_per_y, 3)


def area_mm2(
    px_count: int,
    pixel_spacing_x: Optional[float],
    pixel_spacing_y: Optional[float],
    dcm_rows: Optional[float],
    jpg_height: Optional[float],
    jpg_width: Optional[float],
    mask_height: Optional[float],
    mask_width: Optional[float],
) -> Optional[float]:
    """Convert pixel area → mm² using both axis spacings."""
    args = [pixel_spacing_x, pixel_spacing_y, dcm_rows, jpg_height, jpg_width,
            mask_height, mask_width]
    if any(v is None or (isinstance(v, float) and math.isnan(v)) for v in args):
        return None
    if mask_height == 0 or mask_width == 0 or jpg_height == 0:
        return None
    dcm_cols_usg = jpg_width * dcm_rows / jpg_height
    mm_per_x = pixel_spacing_x * dcm_cols_usg / mask_width
    mm_per_y = pixel_spacing_y * dcm_rows / mask_height
    return round(px_count * mm_per_x * mm_per_y, 3)


# ---------------------------------------------------------------------------
# Echo intensity & area
# ---------------------------------------------------------------------------

def echo_intensity(
    img_gray: np.ndarray, mask: np.ndarray, label: int
) -> Optional[float]:
    """Mean grayscale pixel value where mask == label; None if label absent."""
    region = img_gray[mask == label]
    if len(region) == 0:
        return None
    return round(float(region.mean()), 2)


def label_area_px(mask: np.ndarray, label: int) -> int:
    """Count pixels with a given label."""
    return int(np.sum(mask == label))


# ---------------------------------------------------------------------------
# Derived ratios
# ---------------------------------------------------------------------------

def muscle_fat_ratio(mask: np.ndarray) -> Optional[float]:
    """(RF_px + VI_px) / max(fat_px, 1); None if both RF and VI absent."""
    rf_px = label_area_px(mask, LABEL_RF)
    vi_px = label_area_px(mask, LABEL_VI)
    if rf_px == 0 and vi_px == 0:
        return None
    fat_px = label_area_px(mask, LABEL_FAT)
    return round((rf_px + vi_px) / max(fat_px, 1), 3)


def frame_rf_consistency(masks: List[np.ndarray]) -> Optional[float]:
    """Standard deviation of rf_thickness_px across frames; None if < 2 valid."""
    thicknesses = [rf_thickness_px(m) for m in masks]
    valid = [t for t in thicknesses if t is not None]
    if len(valid) < 2:
        return None
    return round(float(np.std(valid, ddof=1)), 3)


def mask_coverage_pct(mask: np.ndarray) -> float:
    """Percentage of pixels with a positive (non-background) label."""
    total = mask.shape[0] * mask.shape[1]
    if total == 0:
        return 0.0
    positive = int(np.sum(mask > 0))
    return round(positive / total * 100, 2)


# ---------------------------------------------------------------------------
# Omnibus compute function
# ---------------------------------------------------------------------------

def compute_all_metrics(
    center_mask: np.ndarray,
    center_img_gray: Optional[np.ndarray],
    all_masks: List[np.ndarray],
    spacing_row: Dict,
) -> Dict:
    """Compute all metrics for the center frame and return a unified dict.

    Args:
        center_mask:      uint8 mask for center frame (H×W)
        center_img_gray:  uint8 grayscale image for center frame; None if unavailable
        all_masks:        list of masks for all frames (for consistency metric)
        spacing_row:      dict/Series with pixel_spacing_x/y, dcm_rows, jpg_height,
                          jpg_width, mask_height, mask_width, bmi (optional)

    Returns dict with keys matching DECISION_LOG_COLUMNS metric fields.
    """
    sx = spacing_row.get("pixel_spacing_x")
    sy = spacing_row.get("pixel_spacing_y")
    dr = spacing_row.get("dcm_rows")
    jh = spacing_row.get("jpg_height")
    jw = spacing_row.get("jpg_width")
    mh = spacing_row.get("mask_height")
    mw = spacing_row.get("mask_width")
    bmi = spacing_row.get("bmi")

    rf_col = max_span_column(center_mask, LABEL_RF)
    vi_col = vi_max_extent_column(center_mask)
    rf_px = rf_thickness_px(center_mask)
    vi_px = vi_thickness_px(center_mask)
    qmt_details = _qmt_measurement_details(center_mask)
    qmt_px_val = qmt_details["qmt_px"]
    qmt_prov = qmt_details["qmt_provenance"]
    qmt_rf_px_val = qmt_details["qmt_rf_px"]
    qmt_vi_px_val = qmt_details["qmt_vi_px"]

    rf_mm = px_to_mm(rf_px, sy, dr, mh)
    vi_mm = px_to_mm(vi_px, sy, dr, mh)
    qmt_mm = px_to_mm(qmt_px_val, sy, dr, mh)
    qmt_rf_mm = px_to_mm(qmt_rf_px_val, sy, dr, mh)
    qmt_vi_mm = px_to_mm(qmt_vi_px_val, sy, dr, mh)

    star = None
    if qmt_mm is not None and bmi:
        try:
            bmi_f = float(bmi)
            if bmi_f > 0:
                star = round(qmt_mm / bmi_f, 4)
        except (TypeError, ValueError):
            pass

    rf_echo = echo_intensity(center_img_gray, center_mask, LABEL_RF) if center_img_gray is not None else None
    vi_echo = echo_intensity(center_img_gray, center_mask, LABEL_VI) if center_img_gray is not None else None

    visible_rf_area_px_val = label_area_px(center_mask, LABEL_RF)
    vi_area_px_val = label_area_px(center_mask, LABEL_VI)
    visible_rf_area_mm2_val = area_mm2(visible_rf_area_px_val, sx, sy, dr, jh, jw, mh, mw)
    vi_area_mm2 = area_mm2(vi_area_px_val, sx, sy, dr, jh, jw, mh, mw)

    return {
        "rf_thickness_px": rf_px,
        "rf_thickness_mm": rf_mm,
        "rf_column": rf_col,
        "vi_thickness_px": vi_px,
        "vi_thickness_mm": vi_mm,
        "vi_column": vi_col,
        "qmt_px": qmt_px_val,
        "qmt_mm": qmt_mm,
        "qmt_provenance": qmt_prov,
        "qmt_column": qmt_details["qmt_column"],
        "qmt_muscle_occupancy_pct": qmt_details["qmt_muscle_occupancy_pct"],
        "qmt_plateau_supported": qmt_details["qmt_plateau_supported"],
        "qmt_central_zone_supported": qmt_details["qmt_central_zone_supported"],
        "qmt_femur_slope_abs": qmt_details["qmt_femur_slope_abs"],
        "qmt_fascia_slope_abs": qmt_details["qmt_fascia_slope_abs"],
        "qmt_validity_flags": qmt_details["qmt_validity_flags"],
        "qmt_guidance_status": qmt_details["qmt_guidance_status"],
        "qmt_rf_px": qmt_rf_px_val,
        "qmt_rf_mm": qmt_rf_mm,
        "qmt_vi_px": qmt_vi_px_val,
        "qmt_vi_mm": qmt_vi_mm,
        "qmt_top_y": qmt_details["qmt_top_y"],
        "qmt_bottom_y": qmt_details["qmt_bottom_y"],
        "qmt_annotation_columns": qmt_details["qmt_annotation_columns"],
        "star_ratio": star,
        "rf_echo": rf_echo,
        "vi_echo": vi_echo,
        "visible_rf_area_px": visible_rf_area_px_val,
        "visible_rf_area_mm2": visible_rf_area_mm2_val,
        "vi_area_px": vi_area_px_val,
        "vi_area_mm2": vi_area_mm2,
        "muscle_fat_ratio": muscle_fat_ratio(center_mask),
        "frame_rf_consistency_px": frame_rf_consistency(all_masks),
        "mask_coverage_pct": mask_coverage_pct(center_mask),
    }

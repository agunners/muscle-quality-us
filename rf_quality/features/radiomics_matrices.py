"""Radiomics matrix-family features — GLRLM, GLSZM, NGTDM, GLDM.

IBSI-aligned hand-rolled implementations (Zwanenburg et al. 2020,
"The Image Biomarker Standardisation Initiative", Radiology 295:328).

This module exists because PyRadiomics 3.x builds fail on Python 3.12
(its bundled `versioneer.py` calls the removed `configparser.SafeConfigParser`),
and the upstream library has not shipped a maintenance release in 2 years.
Hand-rolling against the IBSI spec gives the round-2 pipeline:
  - Direct IBSI alignment by construction (we follow the spec, not a library
    that interprets it).
  - No external-dependency brittleness as Python releases march forward.
  - Tractable, testable Python (≈300 lines vs ≈6000 in PyRadiomics for
    these four families), ready for round-2 review.

Quantization convention: every matrix uses an L-level uniform-binning
quantization of u8 image values onto ``[0, L-1]`` indices via
``q = (img_u8 * L) // 256`` (matches the existing GLCM scheme in
:mod:`rf_quality.features.glcm`). With ``L=32`` (the project default), each
bin spans 8 EI units. For IBSI-aligned binCount semantics, this is the
``binCount`` formulation of the spec (Sec. 3.4 of Zwanenburg 2020); the
binWidth alternative is not used here because the existing round-1 GLCM
pipeline established binCount=32 and consistency across families is more
valuable than absolute binWidth alignment.

Mask convention: every function takes a u8 image array and a boolean mask
of the same shape; pixels where ``mask=False`` are excluded from all
matrix construction. Empty / undersized ROIs return ``None`` for every
emitted feature so downstream Stage A handles them gracefully.

Round-2 (2026-05-02) emits:
  GLRLM:  short_run_emphasis, long_run_emphasis, low_gray_run_emphasis,
          run_length_nonuniformity
  GLSZM:  small_zone_emphasis, zone_size_nonuniformity
  NGTDM:  busyness, coarseness
  GLDM:   gray_level_nonuniformity

All emitted as keys in the returned dict prefixed with the family name
(e.g. ``glrlm_short_run_emphasis``). Plausibility ranges per round-2
plan A5 are wide-first; tighten in a follow-up plausibility_range op
once the cohort distribution is observed.
"""

from __future__ import annotations

import numpy as np
from skimage.measure import label as cc_label

# Module-wide minimum support: matrices computed on fewer pixels are
# unreliable. Mirrors the existing GLCM `min_pairs` convention.
_MIN_PIXELS_DEFAULT = 64


def _quantize(img_u8: np.ndarray, levels: int) -> np.ndarray:
    """Uniform binCount quantization (matches glcm.py)."""
    return np.clip(
        (img_u8.astype(np.int32) * levels) // 256, 0, levels - 1
    ).astype(np.int32)


# ---------------------------------------------------------------------------
# GLRLM — Gray-Level Run-Length Matrix
# ---------------------------------------------------------------------------


def _glrlm_for_direction(q: np.ndarray, mask: np.ndarray, dy: int, dx: int,
                          levels: int) -> np.ndarray:
    """Build a GLRLM matrix P(i, j) by walking 1-D runs in direction (dy, dx).

    P[i, j] = number of contiguous runs of gray-level ``i`` with length
    ``j+1`` (0-indexed run length). The maximum possible run length is the
    longer dimension of the image.
    """
    h, w = q.shape
    max_run = max(h, w)
    P = np.zeros((levels, max_run), dtype=np.int64)

    # Generate scan-line starting points for the (dy, dx) direction.
    # The IBSI 4-direction set: 0° (dx=1, dy=0), 90° (dx=0, dy=-1),
    # 45° (dx=1, dy=-1), 135° (dx=-1, dy=-1). Implemented here by rotating
    # the image 90° as needed and walking horizontally.
    if (dy, dx) == (0, 1):
        # 0° — walk left to right per row
        starts = [(r, 0) for r in range(h)]
    elif (dy, dx) == (-1, 0):
        # 90° — walk top to bottom per column (we walk bottom up so dy=-1)
        starts = [(h - 1, c) for c in range(w)]
    elif (dy, dx) == (-1, 1):
        # 45° — walk diagonally; starting points are the bottom row and the
        # left column (excluding the bottom-left corner already counted).
        starts = [(h - 1, c) for c in range(w)] + [(r, 0) for r in range(h - 1)]
    elif (dy, dx) == (-1, -1):
        # 135° — walk diagonally the other way. Starting points are the
        # bottom row and the right column.
        starts = [(h - 1, c) for c in range(w)] + [(r, w - 1) for r in range(h - 1)]
    else:
        raise ValueError(f"Unsupported direction (dy={dy}, dx={dx})")

    for start_r, start_c in starts:
        r, c = start_r, start_c
        cur_lvl = -1
        cur_len = 0
        while 0 <= r < h and 0 <= c < w:
            if mask[r, c]:
                lvl = int(q[r, c])
                if lvl == cur_lvl:
                    cur_len += 1
                else:
                    if cur_lvl >= 0 and cur_len > 0:
                        P[cur_lvl, cur_len - 1] += 1
                    cur_lvl = lvl
                    cur_len = 1
            else:
                if cur_lvl >= 0 and cur_len > 0:
                    P[cur_lvl, cur_len - 1] += 1
                cur_lvl = -1
                cur_len = 0
            r += dy
            c += dx
        # Flush the final run on the scan line.
        if cur_lvl >= 0 and cur_len > 0:
            P[cur_lvl, cur_len - 1] += 1

    return P


def compute_glrlm(
    img_u8: np.ndarray,
    mask: np.ndarray,
    levels: int = 32,
    min_pixels: int = _MIN_PIXELS_DEFAULT,
) -> dict[str, float | None]:
    """Compute GLRLM features averaged over 4 directions at d=1.

    Returns a dict with keys: ``glrlm_short_run_emphasis``,
    ``glrlm_long_run_emphasis``, ``glrlm_low_gray_run_emphasis``,
    ``glrlm_run_length_nonuniformity``.

    All formulas per IBSI; see Zwanenburg 2020 §3.5.
    """
    out_keys = (
        "glrlm_short_run_emphasis",
        "glrlm_long_run_emphasis",
        "glrlm_low_gray_run_emphasis",
        "glrlm_run_length_nonuniformity",
    )
    out: dict[str, float | None] = {k: None for k in out_keys}

    n_pixels = int(np.count_nonzero(mask))
    if n_pixels < min_pixels:
        return out

    q = _quantize(img_u8, levels)
    directions = [(0, 1), (-1, 0), (-1, 1), (-1, -1)]
    sre = lre = lgre = rlnu = 0.0
    used = 0
    for dy, dx in directions:
        P = _glrlm_for_direction(q, mask, dy, dx, levels)
        N_r = float(P.sum())
        if N_r <= 0:
            continue
        # j is run length (1-indexed in IBSI; here j_idx is 0-indexed → j = j_idx + 1).
        # i is gray level (we use i_idx + 1 to avoid divide-by-zero in LGRE).
        i_idx = np.arange(P.shape[0], dtype=np.float64)[:, None]
        j_idx = np.arange(P.shape[1], dtype=np.float64)[None, :]
        i_g = i_idx + 1.0
        j_l = j_idx + 1.0
        sre += float((P / (j_l ** 2)).sum() / N_r)
        lre += float((P * (j_l ** 2)).sum() / N_r)
        lgre += float((P / (i_g ** 2)).sum() / N_r)
        # RLNU: sum over j of (sum over i)^2 / N_r
        col_sum = P.sum(axis=0).astype(np.float64)
        rlnu += float((col_sum ** 2).sum() / N_r)
        used += 1

    if used == 0:
        return out
    out["glrlm_short_run_emphasis"] = sre / used
    out["glrlm_long_run_emphasis"] = lre / used
    out["glrlm_low_gray_run_emphasis"] = lgre / used
    out["glrlm_run_length_nonuniformity"] = rlnu / used
    return out


# ---------------------------------------------------------------------------
# GLSZM — Gray-Level Size-Zone Matrix
# ---------------------------------------------------------------------------


def compute_glszm(
    img_u8: np.ndarray,
    mask: np.ndarray,
    levels: int = 32,
    min_pixels: int = _MIN_PIXELS_DEFAULT,
) -> dict[str, float | None]:
    """Compute GLSZM features for a single ROI (no direction averaging).

    Returns ``glszm_small_zone_emphasis``, ``glszm_zone_size_nonuniformity``.
    Per IBSI Sec. 3.6; uses 8-connectivity (Moore neighborhood) for
    connected-component labeling — the IBSI-recommended choice for 2-D
    radiomics.
    """
    out_keys = ("glszm_small_zone_emphasis", "glszm_zone_size_nonuniformity")
    out: dict[str, float | None] = {k: None for k in out_keys}

    n_pixels = int(np.count_nonzero(mask))
    if n_pixels < min_pixels:
        return out

    q = _quantize(img_u8, levels)
    # Pixels outside the mask get a sentinel value that never appears in the
    # quantized output (negative); they will not match any gray level when
    # we filter per-level.
    q_masked = np.where(mask, q, -1)

    # Maximum possible zone size = number of mask pixels.
    P = np.zeros((levels, n_pixels), dtype=np.int64)

    for lvl in range(levels):
        binary = (q_masked == lvl)
        if not binary.any():
            continue
        # 8-connectivity per IBSI 2-D recommendation.
        labels = cc_label(binary, connectivity=2, background=0)
        if labels.max() == 0:
            continue
        # Use bincount on the flat labels; index 0 is background.
        sizes = np.bincount(labels.ravel())[1:]  # drop background
        for size in sizes:
            if size > 0:
                P[lvl, size - 1] += 1  # 0-indexed: P[i, j] with j+1 = zone size

    N_z = float(P.sum())
    if N_z <= 0:
        return out

    j_idx = np.arange(P.shape[1], dtype=np.float64)[None, :]
    j_s = j_idx + 1.0  # zone size (1-indexed)
    sze = float((P / (j_s ** 2)).sum() / N_z)

    # ZSN: sum over j of (sum over i)^2 / N_z
    col_sum = P.sum(axis=0).astype(np.float64)
    zsn = float((col_sum ** 2).sum() / N_z)

    out["glszm_small_zone_emphasis"] = sze
    out["glszm_zone_size_nonuniformity"] = zsn
    return out


# ---------------------------------------------------------------------------
# NGTDM — Neighborhood Gray-Tone Difference Matrix
# ---------------------------------------------------------------------------


def compute_ngtdm(
    img_u8: np.ndarray,
    mask: np.ndarray,
    levels: int = 32,
    radius: int = 1,
    min_pixels: int = _MIN_PIXELS_DEFAULT,
) -> dict[str, float | None]:
    """Compute NGTDM features (busyness, coarseness).

    For each pixel within the mask, the neighborhood mean is computed
    over the (2r+1)x(2r+1) window restricted to mask pixels. The NGTDM
    cell s_i sums |i - μ_neighborhood| over all pixels with gray level i.

    Per IBSI Sec. 3.7. r=1 means the 8-neighborhood + center (3x3 window).
    """
    out_keys = ("ngtdm_busyness", "ngtdm_coarseness")
    out: dict[str, float | None] = {k: None for k in out_keys}

    n_pixels = int(np.count_nonzero(mask))
    if n_pixels < min_pixels:
        return out

    q = _quantize(img_u8, levels)
    h, w = q.shape

    # Pre-pad with zeros so window extraction is clean. We use a binary mask
    # to count valid neighbors; the running mean is sum / count.
    pad = radius
    q_pad = np.pad(q.astype(np.float64), pad, mode="constant", constant_values=0.0)
    m_pad = np.pad(mask.astype(np.float64), pad, mode="constant", constant_values=0.0)

    # 2-D box-sum via integral image (cumsum + leading-zero-row/col padding
    # so the inclusive-rectangle formula is uniform regardless of edge).
    #
    # Bug fix 2026-05-02 (red-team review): the previous implementation used
    # the wrong corners of the cumsum array for the "minus" terms, producing
    # an off-by-one shifted (2r)x(2r) window instead of the correct
    # (2r+1)x(2r+1) window. Verified by hand on a 3x3 all-1s padded input:
    # the correct sum at every interior pixel is 9; the buggy formula gave 4.
    # See tests/test_ngtdm_box_sum.py for the synthetic-vs-manual-loop check.
    def _box_sum(arr_pad: np.ndarray) -> np.ndarray:
        # Sum over (2r+1)x(2r+1) windows centered on each (i, j) in the
        # original (unpadded) coordinate frame, using the padded array of
        # shape (h+2r, w+2r). Returns shape (h, w).
        c = arr_pad.cumsum(axis=0).cumsum(axis=1)
        # Prepend a leading zero row + column so the integral-image formula
        # has a well-defined "row -1" and "col -1" without case analysis.
        cp = np.zeros((c.shape[0] + 1, c.shape[1] + 1), dtype=c.dtype)
        cp[1:, 1:] = c
        # Box centered at original (i, j) covers padded coords
        # [i, i+2r] x [j, j+2r] (the box in the padded frame is the original
        # pixel's r-neighborhood translated by the pad). Inclusive-rectangle
        # sum on the leading-zero-padded cumsum:
        #   S(i, j) = cp[i+2r+1, j+2r+1]
        #          - cp[i,        j+2r+1]
        #          - cp[i+2r+1,   j]
        #          + cp[i,        j]
        return (
            cp[2 * radius + 1 : 2 * radius + 1 + h, 2 * radius + 1 : 2 * radius + 1 + w]
            - cp[: h, 2 * radius + 1 : 2 * radius + 1 + w]
            - cp[2 * radius + 1 : 2 * radius + 1 + h, : w]
            + cp[: h, : w]
        )

    sum_q = _box_sum(q_pad * m_pad)
    cnt_q = _box_sum(m_pad)

    # Subtract the center pixel from sum and count to get *neighborhood* mean
    # (excluding the center). IBSI strict definition: μ_neighborhood is the
    # mean over neighbors (not including the center).
    center_q = q.astype(np.float64) * mask.astype(np.float64)
    center_cnt = mask.astype(np.float64)
    nbr_sum = sum_q - center_q
    nbr_cnt = cnt_q - center_cnt

    # Pixels with no valid neighbors are skipped (cnt = 0).
    valid = mask & (nbr_cnt > 0)
    if not valid.any():
        return out
    nbr_mean = np.where(nbr_cnt > 0, nbr_sum / np.maximum(nbr_cnt, 1.0), 0.0)
    diffs = np.abs(q.astype(np.float64) - nbr_mean)

    s = np.zeros(levels, dtype=np.float64)
    n = np.zeros(levels, dtype=np.float64)
    flat_q = q[valid].astype(np.int64)
    flat_d = diffs[valid]
    for i in range(levels):
        sel = flat_q == i
        if sel.any():
            s[i] = flat_d[sel].sum()
            n[i] = sel.sum()

    N_total = n.sum()
    if N_total <= 0:
        return out
    p = n / N_total

    # Coarseness: 1 / (Σ p_i s_i + ε)
    sum_ps = float((p * s).sum())
    eps_c = 1e-6
    out["ngtdm_coarseness"] = 1.0 / (sum_ps + eps_c)

    # Busyness: (Σ p_i s_i) / Σ_{i ≠ j, p_i>0, p_j>0} |i p_i - j p_j|
    nz = p > 0
    i_arr = np.arange(levels, dtype=np.float64)[nz]
    p_arr = p[nz]
    if i_arr.size < 2:
        out["ngtdm_busyness"] = None
    else:
        ip = i_arr * p_arr
        # All pairs |i p_i - j p_j|, i != j
        diff_pair = np.abs(ip[:, None] - ip[None, :])
        # Zero the diagonal (i == j contributes 0 by definition).
        np.fill_diagonal(diff_pair, 0.0)
        denom = float(diff_pair.sum())
        if denom <= 0:
            out["ngtdm_busyness"] = None
        else:
            out["ngtdm_busyness"] = sum_ps / denom

    return out


# ---------------------------------------------------------------------------
# GLDM — Gray-Level Dependence Matrix
# ---------------------------------------------------------------------------


def compute_gldm(
    img_u8: np.ndarray,
    mask: np.ndarray,
    levels: int = 32,
    distance: int = 1,
    eps_alpha: int = 0,
    min_pixels: int = _MIN_PIXELS_DEFAULT,
) -> dict[str, float | None]:
    """Compute GLDM features (gray-level non-uniformity).

    GLDM per IBSI Sec. 3.9: for each pixel at gray level i, count its
    "dependent neighbors" — neighbors within ``distance`` that have a gray
    level differing from i by no more than ``eps_alpha``. The GLDM cell
    P(i, j) = number of pixels with gray level i that have exactly j
    dependent neighbors (j ∈ [0, n_neighbors]).

    Round-2 emits ``gldm_gray_level_nonuniformity`` only; the full GLDM
    family can be added in a follow-up by emitting more reductions of P.
    """
    out_keys = ("gldm_gray_level_nonuniformity",)
    out: dict[str, float | None] = {k: None for k in out_keys}

    n_pixels = int(np.count_nonzero(mask))
    if n_pixels < min_pixels:
        return out

    q = _quantize(img_u8, levels)

    # 8-connectivity at distance 1 ⇒ 8 neighbors per pixel. For distance d,
    # the window is (2d+1)x(2d+1) excluding center ⇒ (2d+1)^2 - 1 neighbors.
    n_neighbors_max = (2 * distance + 1) ** 2 - 1

    # Build dependence count per pixel via shifted comparisons.
    dep_count = np.zeros_like(q, dtype=np.int32)
    h, w = q.shape
    for dr in range(-distance, distance + 1):
        for dc in range(-distance, distance + 1):
            if dr == 0 and dc == 0:
                continue
            # Shift q and mask by (dr, dc).
            r0_src = max(0, -dr); r1_src = min(h, h - dr)
            c0_src = max(0, -dc); c1_src = min(w, w - dc)
            r0_dst = r0_src + dr; r1_dst = r1_src + dr
            c0_dst = c0_src + dc; c1_dst = c1_src + dc
            if r0_src >= r1_src or c0_src >= c1_src:
                continue
            src_q = q[r0_src:r1_src, c0_src:c1_src]
            src_m = mask[r0_src:r1_src, c0_src:c1_src]
            dst_q = q[r0_dst:r1_dst, c0_dst:c1_dst]
            dst_m = mask[r0_dst:r1_dst, c0_dst:c1_dst]
            both_in = src_m & dst_m
            close = np.abs(src_q.astype(np.int64) - dst_q.astype(np.int64)) <= eps_alpha
            inc = (both_in & close).astype(np.int32)
            dep_count[r0_src:r1_src, c0_src:c1_src] += inc

    # P(i, j) where j = dep_count for pixels in mask.
    P = np.zeros((levels, n_neighbors_max + 1), dtype=np.int64)
    flat_q = q[mask].ravel()
    flat_d = dep_count[mask].ravel()
    flat_d = np.clip(flat_d, 0, n_neighbors_max)
    for i in range(levels):
        sel = flat_q == i
        if not sel.any():
            continue
        bins = np.bincount(flat_d[sel], minlength=n_neighbors_max + 1)
        P[i] = bins[: n_neighbors_max + 1]

    N_total = float(P.sum())
    if N_total <= 0:
        return out
    # GLN = Σ_i (Σ_j P(i,j))^2 / N_total
    row_sum = P.sum(axis=1).astype(np.float64)
    out["gldm_gray_level_nonuniformity"] = float((row_sum ** 2).sum() / N_total)
    return out


# ---------------------------------------------------------------------------
# Top-level dispatch
# ---------------------------------------------------------------------------


def compute_radiomics_matrices(
    img_u8: np.ndarray,
    mask: np.ndarray,
    levels: int = 32,
    min_pixels: int = _MIN_PIXELS_DEFAULT,
) -> dict[str, float | None]:
    """One-shot computation of GLRLM + GLSZM + NGTDM + GLDM for one ROI.

    Quantization (binCount=L) is shared across families for cross-family
    comparability. Each individual matrix function may also be called
    directly. Empty / undersized ROIs return None for every feature.
    """
    out: dict[str, float | None] = {}
    out.update(compute_glrlm(img_u8, mask, levels=levels, min_pixels=min_pixels))
    out.update(compute_glszm(img_u8, mask, levels=levels, min_pixels=min_pixels))
    out.update(compute_ngtdm(img_u8, mask, levels=levels, min_pixels=min_pixels))
    out.update(compute_gldm(img_u8, mask, levels=levels, min_pixels=min_pixels))
    return out

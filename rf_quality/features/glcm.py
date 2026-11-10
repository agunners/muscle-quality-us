"""GLCM features — split-distance (d=1 / d=2), 4-angle averaged, L=32 grey levels.

Scale-orientation confounding in a 4×2 mixed summary is avoided by reporting
d=1 and d=2 as separate groups. Each group returns the 4-angle mean of each
Haralick property plus an anisotropy index (|contrast_a0 − contrast_a90|)
for d=1 (the more reliable distance).

The underlying pair-counting primitive (``_shift_pairs`` + GLCM from pair
histograms) is borrowed from upstream ``muscle_quality/metrics.py`` — we
inline it here because the main repo venv does not import upstream.
"""

from __future__ import annotations

import numpy as np


CORE_PROPS = ("contrast", "homogeneity", "entropy", "correlation", "asm")
# Round-2 extensions (2026-05-02): three additional Haralick properties
# the round-2 lit-pass discovery surfaced (cluster_shade, dissimilarity,
# IDM). All emitted at d=1 only — d=2 siblings can be added later if
# Stage F shows they carry independent signal at d=1.
EXT_PROPS = ("cluster_shade", "dissimilarity", "idm")


def _shift_pairs(q: np.ndarray, mask: np.ndarray, dy: int, dx: int):
    h, w = q.shape
    y0 = max(0, -dy); y1 = min(h, h - dy)
    x0 = max(0, -dx); x1 = min(w, w - dx)
    if y0 >= y1 or x0 >= x1:
        return np.asarray([], dtype=np.int32), np.asarray([], dtype=np.int32)
    m_src = mask[y0:y1, x0:x1]
    m_dst = mask[y0 + dy : y1 + dy, x0 + dx : x1 + dx]
    valid = m_src & m_dst
    if not np.any(valid):
        return np.asarray([], dtype=np.int32), np.asarray([], dtype=np.int32)
    i = q[y0:y1, x0:x1][valid]
    j = q[y0 + dy : y1 + dy, x0 + dx : x1 + dx][valid]
    return i, j


def _glcm_from_pairs(i: np.ndarray, j: np.ndarray, levels: int, min_pairs: int) -> dict[str, float | None]:
    n = int(i.size)
    out = {p: None for p in (*CORE_PROPS, *EXT_PROPS)}
    out["n_pairs"] = float(n)
    if n < min_pairs:
        return out
    glcm = np.zeros((levels, levels), dtype=np.float64)
    np.add.at(glcm, (i, j), 1.0)
    glcm = glcm + glcm.T
    s = float(glcm.sum())
    if s <= 0:
        return out
    p = glcm / s
    idx = np.arange(levels, dtype=np.float64)
    ii, jj = np.meshgrid(idx, idx, indexing="ij")
    diff = ii - jj
    abs_diff = np.abs(diff)

    contrast = float(np.sum(p * diff ** 2))
    homogeneity = float(np.sum(p / (1.0 + diff ** 2)))
    asm = float(np.sum(p ** 2))
    nz = p > 0
    entropy = float(-np.sum(p[nz] * np.log2(p[nz]))) if nz.any() else 0.0

    mu_i = float(np.sum(ii * p)); mu_j = float(np.sum(jj * p))
    sig_i = float(np.sqrt(np.sum(((ii - mu_i) ** 2) * p)))
    sig_j = float(np.sqrt(np.sum(((jj - mu_j) ** 2) * p)))
    if sig_i > 1e-12 and sig_j > 1e-12:
        corr = float(np.sum(((ii - mu_i) * (jj - mu_j) * p)) / (sig_i * sig_j))
    else:
        corr = None

    # Round-2 extensions (Haralick 1973; IBSI Sec 3.4):
    # cluster_shade = Σ ((i + j − μ_i − μ_j)^3) p(i, j)
    # dissimilarity = Σ |i − j| p(i, j) (linear analog of contrast)
    # idm           = Σ p(i, j) / (1 + |i − j|) (IBSI's "inverse difference moment",
    #                  distinct from homogeneity which uses 1/(1+(i-j)^2))
    cluster_shade = float(np.sum(p * ((ii + jj - mu_i - mu_j) ** 3)))
    dissimilarity = float(np.sum(p * abs_diff))
    idm = float(np.sum(p / (1.0 + abs_diff)))

    out.update({
        "contrast": contrast, "homogeneity": homogeneity,
        "entropy": entropy, "correlation": corr, "asm": asm,
        "cluster_shade": cluster_shade,
        "dissimilarity": dissimilarity,
        "idm": idm,
    })
    return out


def _angles(distance: int):
    # 0° horizontal, 45°, 90° vertical, 135°
    return [
        (f"a0_d{distance}", 0, distance),
        (f"a45_d{distance}", -distance, distance),
        (f"a90_d{distance}", -distance, 0),
        (f"a135_d{distance}", -distance, -distance),
    ]


def compute_glcm(
    img_u8: np.ndarray,
    mask: np.ndarray,
    levels: int = 32,
    min_pairs: int = 16,
) -> dict[str, float | None]:
    q = np.clip((img_u8.astype(np.int32) * levels) // 256, 0, levels - 1).astype(np.int32)

    out: dict[str, float | None] = {}
    for distance in (1, 2):
        # Round-2: extensions (cluster_shade, dissimilarity, idm) emitted
        # at d=1 only. Per-d=2 siblings can be added in a follow-up
        # plausibility_range pass once the d=1 versions clear Stage F.
        active_props = (*CORE_PROPS, *EXT_PROPS) if distance == 1 else CORE_PROPS
        per_angle: dict[str, list[float]] = {p: [] for p in active_props}
        angle_contrast: dict[str, float] = {}
        for name, dy, dx in _angles(distance):
            i, j = _shift_pairs(q, mask, dy, dx)
            vals = _glcm_from_pairs(i, j, levels=levels, min_pairs=min_pairs)
            for p in active_props:
                v = vals[p]
                if v is not None:
                    per_angle[p].append(float(v))
            if name.startswith("a0_") or name.startswith("a90_"):
                angle_contrast[name] = vals["contrast"] if vals["contrast"] is not None else float("nan")
        for p in active_props:
            out[f"glcm_d{distance}_{p}"] = float(np.mean(per_angle[p])) if per_angle[p] else None
        if distance == 1:
            c0 = angle_contrast.get(f"a0_d1"); c90 = angle_contrast.get(f"a90_d1")
            if c0 is not None and c90 is not None and not (np.isnan(c0) or np.isnan(c90)):
                out["glcm_anisotropy_index"] = float(abs(c0 - c90))
            else:
                out["glcm_anisotropy_index"] = None
    return out

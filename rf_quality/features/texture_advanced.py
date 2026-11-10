"""Advanced texture and intensity features for round-2.

Implementations are pragmatic, IBSI-aligned where the standard speaks, and
mostly thin wrappers around scikit-image / numpy / pywt primitives. Each
function takes a u8 image + boolean mask of the same shape and returns a
dict of named feature values. Empty / undersized ROIs return ``None``.

Round-2 additions (2026-05-02):
  - lbp_entropy_p8r1
  - multi_otsu_fat_fraction, multi_otsu_collagen_fraction
  - bright_speckle_density_per_mm2 (needs pixel_spacing)
  - blob_area_fraction
  - local_entropy_mean
  - wavelet_detail_energy_ratio
  - fractal_dimension_box_count
  - frangi_septae_density_mean_response (Sato sheetness)
  - gabor_energy_mean
  - texture_anisotropy_index_dubois

References inline per feature. All are computed on the masked muscle ROI;
features that need a tight bounding box (LBP, Sato, Gabor, wavelet, fractal
dim) operate on the bbox of the mask with non-mask pixels zeroed.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pywt
from skimage.feature import local_binary_pattern, peak_local_max
from skimage.filters import gabor, sato, threshold_multiotsu
from skimage.filters.rank import entropy as rank_entropy
from skimage.measure import label as cc_label
from skimage.measure import regionprops
from skimage.morphology import disk, erosion, white_tophat
from skimage.transform import rotate as sk_rotate


_MIN_PIXELS_DEFAULT = 64


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _bbox_view(img: np.ndarray, mask: np.ndarray):
    """Return (img_crop, mask_crop) cropped to the mask's bounding box.

    Returns (None, None) if the mask is empty.
    """
    if not mask.any():
        return None, None
    rows = np.any(mask, axis=1)
    cols = np.any(mask, axis=0)
    r0, r1 = np.where(rows)[0][[0, -1]]
    c0, c1 = np.where(cols)[0][[0, -1]]
    return img[r0 : r1 + 1, c0 : c1 + 1], mask[r0 : r1 + 1, c0 : c1 + 1]


# ---------------------------------------------------------------------------
# LBP entropy (P=8, R=1, uniform)
# ---------------------------------------------------------------------------


def compute_lbp_entropy(
    img_u8: np.ndarray,
    mask: np.ndarray,
    P: int = 8,
    R: int = 1,
    min_pixels: int = _MIN_PIXELS_DEFAULT,
) -> dict[str, Optional[float]]:
    """Shannon entropy of the LBP code histogram inside the muscle ROI.

    Uses the rotation-invariant uniform LBP (skimage method='uniform'),
    which produces P+2 = 10 bins for P=8 (the 9 uniform patterns + 1
    non-uniform bucket). Entropy is in bits, computed on the bin-probability
    distribution. Bell 2022 reported LBP entropy correlated with insulin
    resistance in rectus femoris (Texture Analysis MSK Review 2025) — the
    closest direct cohort+anatomy anchor in the round-2 lit pass.
    """
    out = {"lbp_entropy_p8r1": None}
    if int(np.count_nonzero(mask)) < min_pixels:
        return out
    img_crop, mask_crop = _bbox_view(img_u8, mask)
    if img_crop is None:
        return out
    # Erode the mask by R so LBP doesn't pull in zero-padded edge pixels.
    erode_mask = erosion(mask_crop, disk(R))
    if int(np.count_nonzero(erode_mask)) < min_pixels:
        return out
    # `local_binary_pattern` accepts float; produces integer codes.
    lbp = local_binary_pattern(img_crop.astype(np.float64), P, R, method="uniform")
    codes = lbp[erode_mask].astype(np.int64)
    n_bins = P + 2
    counts = np.bincount(codes, minlength=n_bins)[:n_bins].astype(np.float64)
    total = counts.sum()
    if total <= 0:
        return out
    p = counts / total
    nz = p > 0
    if not nz.any():
        return out
    H = float(-np.sum(p[nz] * np.log2(p[nz])))
    out["lbp_entropy_p8r1"] = H
    return out


# ---------------------------------------------------------------------------
# Multi-Otsu trinary (fat / collagen / muscle)
# ---------------------------------------------------------------------------


def compute_multi_otsu_trinary(
    img_u8: np.ndarray,
    mask: np.ndarray,
    min_pixels: int = _MIN_PIXELS_DEFAULT,
    min_threshold_separation_eu: float = 10.0,
) -> dict[str, Optional[float]]:
    """Multi-Otsu (3-class) thresholding on the muscle ROI.

    Returns the fraction of muscle pixels in the upper class
    (``multi_otsu_fat_fraction``) and the middle class
    (``multi_otsu_collagen_fraction``). López-Gómez 2025 anchored this on
    n=337 RF oncology AI-segmented ROIs against CT %IMAT. The lower class
    is implicitly the muscle-tissue fraction (1 − fat − collagen).

    Trinary Otsu requires a tri-modal-ish histogram. On uniformly
    hypoechoic healthy RF (the project's cohort), the upper threshold can
    be unstable: skimage will return two thresholds even when the
    histogram is essentially unimodal, but the resulting fractions are
    arbitrary. The ``min_threshold_separation_eu`` gate (default 10 EI
    units, slightly above the binCount=32 quantization step of 8 u8 units)
    rejects degenerate trinary segmentations: when ``T_2 − T_1`` is below
    that, both fractions are returned as None rather than spurious values
    that would confound Stage A. This is structural-physics gating
    (the input doesn't support the computation), distinct from
    literature-quality gating.
    """
    out = {"multi_otsu_fat_fraction": None, "multi_otsu_collagen_fraction": None}
    if int(np.count_nonzero(mask)) < min_pixels:
        return out
    vals = img_u8[mask]
    if vals.size < min_pixels:
        return out
    # Need at least 3 distinct gray levels for trinary Otsu to be well-defined.
    if np.unique(vals).size < 3:
        return out
    try:
        thresholds = threshold_multiotsu(vals, classes=3)
    except Exception:
        # threshold_multiotsu raises if it can't find 3 distinct classes;
        # treat as missing.
        return out
    if len(thresholds) != 2:
        return out
    t_lo, t_hi = float(thresholds[0]), float(thresholds[1])
    # Threshold-collapse gate: when T_hi − T_lo is below the structural
    # separation floor, the trinary segmentation is degenerate.
    if (t_hi - t_lo) < min_threshold_separation_eu:
        return out
    fat_frac = float((vals >= t_hi).mean())
    coll_frac = float(((vals >= t_lo) & (vals < t_hi)).mean())
    out["multi_otsu_fat_fraction"] = fat_frac
    out["multi_otsu_collagen_fraction"] = coll_frac
    return out


# ---------------------------------------------------------------------------
# Bright-speckle density (Molinger 2024 "starry night" formalization)
# ---------------------------------------------------------------------------


def compute_bright_speckle_density(
    img_u8: np.ndarray,
    mask: np.ndarray,
    pixel_spacing_y_mm_per_px: Optional[float] = None,
    pixel_spacing_x_mm_per_px: Optional[float] = None,
    sigma: float = 1.5,
    threshold_k_sd: float = 1.5,
    min_pixels: int = _MIN_PIXELS_DEFAULT,
) -> dict[str, Optional[float]]:
    """Bright local-maxima density (peaks per mm²) on a top-hat-filtered ROI.

    Counts local maxima above ``mean + threshold_k_sd * std`` of the
    top-hat response, then divides by the muscle ROI area in mm². Works
    on log-compressed B-mode pixels; complementary to envelope-statistics
    features. Sigma controls the top-hat structuring-element radius.
    """
    out = {"bright_speckle_density_per_mm2": None}
    if int(np.count_nonzero(mask)) < min_pixels:
        return out
    if pixel_spacing_y_mm_per_px is None or pixel_spacing_x_mm_per_px is None:
        return out
    if pixel_spacing_y_mm_per_px <= 0 or pixel_spacing_x_mm_per_px <= 0:
        return out
    img_crop, mask_crop = _bbox_view(img_u8, mask)
    if img_crop is None:
        return out
    # Top-hat with a small disk to suppress slow background gradients.
    selem = disk(int(np.ceil(sigma * 2)))
    tophat = white_tophat(img_crop.astype(np.uint8), footprint=selem)
    vals = tophat[mask_crop]
    if vals.size < min_pixels:
        return out
    mu = float(vals.mean())
    sd = float(vals.std()) if vals.std() > 0 else 1.0
    thresh = mu + threshold_k_sd * sd
    # peak_local_max: relative neighborhood threshold disabled, absolute
    # min set on top-hat response.
    peaks = peak_local_max(
        tophat.astype(np.float64),
        min_distance=int(max(1, sigma)),
        threshold_abs=float(thresh),
        labels=mask_crop.astype(np.int64),
    )
    n_peaks = int(peaks.shape[0])
    area_mm2 = float(
        int(np.count_nonzero(mask)) * pixel_spacing_y_mm_per_px * pixel_spacing_x_mm_per_px
    )
    if area_mm2 <= 0:
        return out
    out["bright_speckle_density_per_mm2"] = n_peaks / area_mm2
    return out


# ---------------------------------------------------------------------------
# Blob area fraction (connected components above threshold)
# ---------------------------------------------------------------------------


def compute_blob_area_fraction(
    img_u8: np.ndarray,
    mask: np.ndarray,
    threshold_k_sd: float = 1.0,
    min_blob_size_px: int = 4,
    min_pixels: int = _MIN_PIXELS_DEFAULT,
) -> dict[str, Optional[float]]:
    """Fraction of muscle ROI occupied by bright connected components.

    Threshold pixels at ``mean + k·SD`` of the in-mask intensity, label
    8-connected components, drop those smaller than ``min_blob_size_px``,
    return total retained blob area / ROI area. Pairs with
    bright_speckle_density (count) — same physical mechanism, different
    geometric statistic.
    """
    out = {"blob_area_fraction": None}
    n_pixels = int(np.count_nonzero(mask))
    if n_pixels < min_pixels:
        return out
    vals = img_u8[mask]
    mu = float(vals.mean())
    sd = float(vals.std()) if vals.std() > 0 else 1.0
    thresh = mu + threshold_k_sd * sd
    binary = (img_u8 >= thresh) & mask
    if not binary.any():
        out["blob_area_fraction"] = 0.0
        return out
    labels = cc_label(binary, connectivity=2, background=0)
    if labels.max() == 0:
        out["blob_area_fraction"] = 0.0
        return out
    sizes = np.bincount(labels.ravel())[1:]
    retained = sizes[sizes >= min_blob_size_px].sum()
    out["blob_area_fraction"] = float(retained) / float(n_pixels)
    return out


# ---------------------------------------------------------------------------
# Local entropy (sliding-window Shannon entropy)
# ---------------------------------------------------------------------------


def compute_local_entropy_mean(
    img_u8: np.ndarray,
    mask: np.ndarray,
    radius: int = 5,
    min_pixels: int = _MIN_PIXELS_DEFAULT,
) -> dict[str, Optional[float]]:
    """Mean of sliding-window entropy map inside the ROI.

    Uses skimage.filters.rank.entropy with a disk(r) footprint. Local-vs-
    global of int_entropy: preserves where the disorder occurs before
    summarizing. radius=5 px is a reasonable starting neighborhood for
    32-level histograms on muscle ROIs.
    """
    out = {"local_entropy_mean": None}
    if int(np.count_nonzero(mask)) < min_pixels:
        return out
    img_crop, mask_crop = _bbox_view(img_u8, mask)
    if img_crop is None:
        return out
    selem = disk(radius)
    ent = rank_entropy(img_crop.astype(np.uint8), selem)
    in_mask_vals = ent[mask_crop]
    if in_mask_vals.size == 0:
        return out
    out["local_entropy_mean"] = float(in_mask_vals.mean())
    return out


# ---------------------------------------------------------------------------
# Wavelet detail energy ratio (single-level Daubechies)
# ---------------------------------------------------------------------------


def compute_wavelet_detail_energy_ratio(
    img_u8: np.ndarray,
    mask: np.ndarray,
    wavelet: str = "db4",
    min_pixels: int = _MIN_PIXELS_DEFAULT,
) -> dict[str, Optional[float]]:
    """Fraction of total wavelet energy in the detail subbands (HL+LH+HH).

    Single-level dwt2 (Daubechies db4). The ROI bounding box is used; the
    masked region is zeroed in the input so out-of-mask pixels don't
    contaminate the wavelet response (this is approximate — wavelets see
    the zero band-edge — but acceptable for a first cohort signal test).
    """
    out = {"wavelet_detail_energy_ratio": None}
    if int(np.count_nonzero(mask)) < min_pixels:
        return out
    img_crop, mask_crop = _bbox_view(img_u8, mask)
    if img_crop is None:
        return out
    arr = img_crop.astype(np.float64)
    arr[~mask_crop] = 0.0
    try:
        cA, (cH, cV, cD) = pywt.dwt2(arr, wavelet)
    except Exception:
        return out
    e_detail = float((cH ** 2).sum() + (cV ** 2).sum() + (cD ** 2).sum())
    e_total = float((cA ** 2).sum() + e_detail)
    if e_total <= 0:
        return out
    out["wavelet_detail_energy_ratio"] = e_detail / e_total
    return out


# ---------------------------------------------------------------------------
# Fractal dimension (box-counting on binarized ROI)
# ---------------------------------------------------------------------------


def compute_fractal_dimension(
    img_u8: np.ndarray,
    mask: np.ndarray,
    min_pixels: int = _MIN_PIXELS_DEFAULT,
) -> dict[str, Optional[float]]:
    """Differential box-counting fractal dimension on the muscle ROI.

    Binarises the ROI at its median intensity; counts non-empty boxes of
    sizes ε ∈ {2, 4, 8, 16, 32, 64}; fits log N(ε) vs log ε. Returns the
    negative slope (the standard fractal dimension D ∈ [0, 2] for a 2-D
    binarized image). Mirón-Mombiela 2021 measured FD on transverse RF
    n=102 mixed-frailty and reported negative cohort signal — round-2
    sandbox verdict.
    """
    out = {"fractal_dimension_box_count": None}
    n_pixels = int(np.count_nonzero(mask))
    if n_pixels < min_pixels:
        return out
    img_crop, mask_crop = _bbox_view(img_u8, mask)
    if img_crop is None or img_crop.size == 0:
        return out
    vals = img_crop[mask_crop]
    if vals.size < min_pixels:
        return out
    median_v = float(np.median(vals))
    binary = ((img_crop >= median_v) & mask_crop).astype(np.uint8)
    if binary.sum() == 0:
        return out
    h, w = binary.shape
    sizes = [s for s in (2, 4, 8, 16, 32, 64) if s <= min(h, w)]
    if len(sizes) < 3:
        return out
    counts = []
    for s in sizes:
        # Pad to multiple of s.
        ph = (s - h % s) % s
        pw = (s - w % s) % s
        b_pad = np.pad(binary, ((0, ph), (0, pw)), mode="constant", constant_values=0)
        H, W = b_pad.shape
        # Reshape into (H/s, s, W/s, s) → boxes; box has-any-1 means non-empty.
        boxes = b_pad.reshape(H // s, s, W // s, s).any(axis=(1, 3))
        counts.append(int(boxes.sum()))
    log_eps = np.log([1.0 / s for s in sizes])
    log_n = np.log(counts)
    # Fractal dimension D = slope of log N vs log(1/ε).
    slope, _ = np.polyfit(log_eps, log_n, 1)
    out["fractal_dimension_box_count"] = float(slope)
    return out


# ---------------------------------------------------------------------------
# Sato sheetness (perimysium / septae detection)
# ---------------------------------------------------------------------------


def compute_sato_sheetness_mean(
    img_u8: np.ndarray,
    mask: np.ndarray,
    sigmas: tuple[int, ...] = (1, 2, 4),
    min_pixels: int = _MIN_PIXELS_DEFAULT,
) -> dict[str, Optional[float]]:
    """Mean of multi-scale Sato sheetness response inside the muscle ROI.

    Sato 1998 is the sheetness analog of Frangi 1998 (vesselness) and is
    designed for elongated 2-D structures — muscle septae / perimysium
    fits this geometry. No published muscle-US validation; round-2
    discovery_sandbox verdict.
    """
    out = {"frangi_septae_density_mean_response": None}
    if int(np.count_nonzero(mask)) < min_pixels:
        return out
    img_crop, mask_crop = _bbox_view(img_u8, mask)
    if img_crop is None:
        return out
    arr = img_crop.astype(np.float64)
    arr[~mask_crop] = 0.0
    try:
        # Sato returns max sheetness across the multi-scale stack when given
        # a sigmas iterable.
        s = sato(arr, sigmas=sigmas, black_ridges=False)
    except Exception:
        return out
    in_mask_vals = s[mask_crop]
    if in_mask_vals.size == 0:
        return out
    out["frangi_septae_density_mean_response"] = float(in_mask_vals.mean())
    return out


# ---------------------------------------------------------------------------
# Gabor energy mean (multi-orientation, multi-frequency bank)
# ---------------------------------------------------------------------------


def compute_gabor_energy_mean(
    img_u8: np.ndarray,
    mask: np.ndarray,
    n_orientations: int = 8,
    frequencies: tuple[float, ...] = (0.1, 0.2, 0.3),
    min_pixels: int = _MIN_PIXELS_DEFAULT,
) -> dict[str, Optional[float]]:
    """Mean energy across an 8-orientation × 3-frequency Gabor bank.

    Energy = sqrt(real² + imag²) per filter, averaged across the bank,
    then averaged across in-mask pixels. Works on transverse muscle ROI
    as a band-passed orientation descriptor. Stoyanov 2010 (longitudinal
    fiber orientation) is the closest published anchor; transverse use is
    exploratory — round-2 discovery_sandbox.
    """
    out = {"gabor_energy_mean": None}
    if int(np.count_nonzero(mask)) < min_pixels:
        return out
    img_crop, mask_crop = _bbox_view(img_u8, mask)
    if img_crop is None:
        return out
    arr = img_crop.astype(np.float64)
    arr[~mask_crop] = arr[mask_crop].mean() if mask_crop.any() else 0.0
    energies = []
    for theta_idx in range(n_orientations):
        theta = float(np.pi * theta_idx / n_orientations)
        for f in frequencies:
            real, imag = gabor(arr, frequency=float(f), theta=theta)
            e = np.sqrt(real ** 2 + imag ** 2)
            in_mask = e[mask_crop]
            if in_mask.size > 0:
                energies.append(float(in_mask.mean()))
    if not energies:
        return out
    out["gabor_energy_mean"] = float(np.mean(energies))
    return out


# ---------------------------------------------------------------------------
# Dubois TAI (texture anisotropy index, multi-angle rotation bank)
# ---------------------------------------------------------------------------


def compute_dubois_tai(
    img_u8: np.ndarray,
    mask: np.ndarray,
    n_orientations: int = 18,
    min_pixels: int = _MIN_PIXELS_DEFAULT,
) -> dict[str, Optional[float]]:
    """Dubois 2018 texture anisotropy index: SD across angular rotations
    of the ROI's mean intensity, divided by the mean of those means.

    Distinct from glcm_anisotropy_index (which is the 2-angle simple form).
    Dubois reported lower TAI in muscular dystrophy and gain-robustness
    relative to gray-scale index; round-2 discovery_sandbox.
    """
    out = {"texture_anisotropy_index_dubois": None}
    if int(np.count_nonzero(mask)) < min_pixels:
        return out
    img_crop, mask_crop = _bbox_view(img_u8, mask)
    if img_crop is None or not mask_crop.any():
        return out
    means = []
    for k in range(n_orientations):
        theta_deg = 180.0 * k / n_orientations
        rot_img = sk_rotate(
            img_crop.astype(np.float64), theta_deg, resize=False, order=1, preserve_range=True
        )
        rot_mask = sk_rotate(
            mask_crop.astype(np.float64), theta_deg, resize=False, order=0, preserve_range=True
        ) > 0.5
        if not rot_mask.any():
            continue
        means.append(float(rot_img[rot_mask].mean()))
    if len(means) < 3:
        return out
    means_arr = np.asarray(means, dtype=np.float64)
    mu = float(means_arr.mean())
    if abs(mu) < 1e-12:
        return out
    out["texture_anisotropy_index_dubois"] = float(means_arr.std() / mu)
    return out


# ---------------------------------------------------------------------------
# Top-level dispatch for round-2 advanced texture features
# ---------------------------------------------------------------------------


def compute_advanced_texture(
    img_u8: np.ndarray,
    mask: np.ndarray,
    pixel_spacing_y_mm_per_px: Optional[float] = None,
    pixel_spacing_x_mm_per_px: Optional[float] = None,
    levels: int = 32,  # accepted for API symmetry with radiomics_matrices; unused here
) -> dict[str, Optional[float]]:
    """Run all round-2 advanced texture / intensity features on one ROI."""
    out: dict[str, Optional[float]] = {}
    out.update(compute_lbp_entropy(img_u8, mask))
    out.update(compute_multi_otsu_trinary(img_u8, mask))
    out.update(
        compute_bright_speckle_density(
            img_u8,
            mask,
            pixel_spacing_y_mm_per_px=pixel_spacing_y_mm_per_px,
            pixel_spacing_x_mm_per_px=pixel_spacing_x_mm_per_px,
        )
    )
    out.update(compute_blob_area_fraction(img_u8, mask))
    out.update(compute_local_entropy_mean(img_u8, mask))
    out.update(compute_wavelet_detail_energy_ratio(img_u8, mask))
    out.update(compute_fractal_dimension(img_u8, mask))
    out.update(compute_sato_sheetness_mean(img_u8, mask))
    out.update(compute_gabor_energy_mean(img_u8, mask))
    out.update(compute_dubois_tai(img_u8, mask))
    return out

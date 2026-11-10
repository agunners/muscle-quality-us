"""PNG/JPG-derived channel generation for grayscale ultrasound frames.

This module intentionally avoids heavy imaging dependencies so it can run with
the lightweight environment used in this repo (numpy + Pillow).

The functions here generate "legacy-like" filter channels (voi/log/clahe/etc.)
from an 8-bit grayscale input image (typically the resized "final" channel used
for CVAT annotation).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Mapping, Sequence

from PIL import Image, ImageFilter

try:
    import numpy as np
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "numpy is required; try running with PYTHONPATH=./.local_packages"
    ) from exc

# Optional: OpenCV for fast, correct CLAHE and histogram equalization
try:
    import cv2
    _HAS_CV2 = True
except ImportError:
    _HAS_CV2 = False
    import warnings
    warnings.warn(
        "OpenCV not found. CLAHE and hist channels will use numpy fallback "
        "which may produce visual artifacts (tile boundaries, black dots). "
        "Install with: pip install opencv-python",
        UserWarning,
        stacklevel=2,
    )

# Optional: skimage frangi filter for vesselness
try:
    from skimage.filters import frangi as _skimage_frangi
except ImportError:
    _skimage_frangi = None


DEFAULT_CHANNELS: tuple[str, ...] = (
    "voi",
    "log",
    "clahe",
    "contrast",
    "edge",
    "hist",
    "speckle",
    "depth",
    "frangi",
)


@dataclass(frozen=True)
class ChannelBundle:
    """Generated channels for a single frame."""

    channels_uint8: Dict[str, "np.ndarray"]


def load_grayscale_uint8(path: Path) -> "np.ndarray":
    """Load an image as a uint8 2D numpy array (mode 'L')."""
    with Image.open(path) as img:
        img = img.convert("L")
        return np.asarray(img, dtype=np.uint8)


def save_grayscale_uint8(array: "np.ndarray", path: Path) -> None:
    """Save a uint8 2D array as a PNG."""
    if array.dtype != np.uint8:
        raise ValueError(f"Expected uint8 array; got {array.dtype}")
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(array, mode="L").save(path)


def _float01(arr: "np.ndarray") -> "np.ndarray":
    return (arr.astype(np.float32) / 255.0).clip(0.0, 1.0)


def _to_uint8(arr01: "np.ndarray") -> "np.ndarray":
    arr01 = np.asarray(arr01, dtype=np.float32)
    arr01 = np.clip(arr01, 0.0, 1.0)
    return np.round(arr01 * 255.0).astype(np.uint8)


def voi_channel(img_uint8: "np.ndarray", mask: "np.ndarray | None" = None) -> "np.ndarray":
    """Return the enhanced image as-is (device already applies VOI lookup).

    The ultrasound device applies VOI (Value of Interest) transformation to the
    enhanced CVAT PNG, so this channel simply returns the input unchanged.
    """
    return img_uint8.copy()


def voi_zscore_channel(img_uint8: "np.ndarray", mask: "np.ndarray | None" = None) -> "np.ndarray":
    """Z-score + clipping + min/max normalization (legacy VOI proxy).

    Use this only if you want to apply additional normalization on top of device VOI.
    """
    img01 = _float01(img_uint8)
    mean = float(img01.mean())
    std = float(img01.std())
    std = max(std, 1e-6)
    z = (img01 - mean) / std
    z = np.clip(z, -5.0, 5.0)
    z = (z + 5.0) / 10.0
    return _to_uint8(z)


def contrast_channel(
    img_uint8: "np.ndarray",
    mask: "np.ndarray | None" = None,
    *,
    low_q: float = 2.0,
    high_q: float = 98.0,
) -> "np.ndarray":
    img01 = _float01(img_uint8)
    lo = float(np.percentile(img01, low_q))
    hi = float(np.percentile(img01, high_q))
    spread = max(hi - lo, 1e-6)
    out = (img01 - lo) / spread
    out = np.clip(out, 0.0, 1.0)
    return _to_uint8(out)


def log_channel(
    img_uint8: "np.ndarray",
    mask: "np.ndarray | None" = None,
    *,
    low_q: float = 5.0,
    high_q: float = 95.0,
    strength: float = 9.0,
) -> "np.ndarray":
    img01 = _float01(img_uint8)
    lo = float(np.percentile(img01, low_q))
    hi = float(np.percentile(img01, high_q))
    spread = max(hi - lo, 1e-6)
    norm = np.clip((img01 - lo) / spread, 0.0, None)
    out = np.log1p(strength * norm) / np.log1p(strength)
    out = np.clip(out, 0.0, 1.0)
    return _to_uint8(out)


def hist_channel(img_uint8: "np.ndarray", mask: "np.ndarray | None" = None) -> "np.ndarray":
    """Histogram equalization to normalize intensity distribution.
    
    Uses OpenCV when available (recommended), falls back to numpy.
    """
    if _HAS_CV2:
        # OpenCV: fast, robust histogram equalization
        return cv2.equalizeHist(img_uint8)
    
    # NumPy fallback: CDF-based equalization with improved robustness
    hist, _ = np.histogram(img_uint8.ravel(), bins=256, range=(0, 256))
    cdf = hist.cumsum()
    # Mask zeros and normalize
    cdf_masked = np.ma.masked_equal(cdf, 0)
    cdf_min = cdf_masked.min()
    cdf_max = cdf_masked.max()
    if cdf_max == cdf_min:
        return img_uint8.copy()
    cdf_normalized = ((cdf_masked - cdf_min) * 255 / (cdf_max - cdf_min)).filled(0).astype(np.uint8)
    return cdf_normalized[img_uint8]


def clahe_channel(
    img_uint8: "np.ndarray",
    mask: "np.ndarray | None" = None,
    *,
    clip_limit: float =6.0,
    grid: tuple[int, int] = (8, 8),
) -> "np.ndarray":
    """Contrast Limited Adaptive Histogram Equalization (CLAHE).
    
    Uses OpenCV when available (recommended - proper bilinear interpolation),
    falls back to numpy with Gaussian blur to reduce tile boundary artifacts.
    
    Args:
        img_uint8: Input grayscale image (uint8).
        mask: Unused, kept for API compatibility.
        clip_limit: Contrast limiting threshold. OpenCV uses absolute value,
                   numpy fallback interprets as fraction of tile size.
        grid: Tile grid size (rows, cols).
    """
    if img_uint8.ndim != 2:
        raise ValueError(f"Expected 2D image; got shape {img_uint8.shape}")
    
    if _HAS_CV2:
        # OpenCV: fast CLAHE with proper bilinear interpolation
        clahe = cv2.createCLAHE(clipLimit=clip_limit, tileGridSize=(grid[1], grid[0]))
        return clahe.apply(img_uint8)
    
    # NumPy fallback: tile-wise equalization with blur to reduce seams
    h, w = img_uint8.shape
    tiles_y, tiles_x = max(int(grid[0]), 1), max(int(grid[1]), 1)
    tile_h, tile_w = max(h // tiles_y, 1), max(w // tiles_x, 1)
    
    out = np.zeros_like(img_uint8, dtype=np.uint8)
    for y0 in range(0, h, tile_h):
        y1 = min(h, y0 + tile_h)
        for x0 in range(0, w, tile_w):
            x1 = min(w, x0 + tile_w)
            tile = img_uint8[y0:y1, x0:x1]
            # Simple histogram equalization per tile
            hist, _ = np.histogram(tile.ravel(), bins=256, range=(0, 256))
            # Clip histogram
            clip_val = max(1, int(clip_limit * tile.size / 256))
            excess = np.maximum(hist - clip_val, 0).sum()
            hist = np.minimum(hist, clip_val)
            hist += excess // 256
            # Build CDF
            cdf = hist.cumsum()
            cdf_min, cdf_max = cdf[cdf > 0].min() if (cdf > 0).any() else 0, cdf.max()
            if cdf_max > cdf_min:
                lut = ((cdf - cdf_min) * 255 / (cdf_max - cdf_min)).astype(np.uint8)
            else:
                lut = np.arange(256, dtype=np.uint8)
            out[y0:y1, x0:x1] = lut[tile]
    
    # Apply light Gaussian blur to reduce tile boundary artifacts
    pil_out = Image.fromarray(out, mode="L")
    pil_out = pil_out.filter(ImageFilter.GaussianBlur(radius=1))
    return np.asarray(pil_out, dtype=np.uint8)


def edge_channel(img_uint8: "np.ndarray", mask: "np.ndarray | None" = None) -> "np.ndarray":
    """Sobel gradient magnitude for edge/boundary detection.
    
    Uses OpenCV when available (recommended - 30x faster), falls back to numpy.
    """
    if _HAS_CV2:
        # OpenCV: fast Sobel gradients
        gx = cv2.Sobel(img_uint8, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(img_uint8, cv2.CV_32F, 0, 1, ksize=3)
        mag = cv2.magnitude(gx, gy)
        # Normalize to 0-255
        mag = cv2.normalize(mag, None, 0, 255, cv2.NORM_MINMAX)
        return mag.astype(np.uint8)
    
    # NumPy fallback: manual convolution
    base = _float01(img_uint8).astype(np.float32)
    kx = np.array([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=np.float32)
    ky = np.array([[-1, -2, -1], [0, 0, 0], [1, 2, 1]], dtype=np.float32)
    padded = np.pad(base, 1, mode="edge")
    gx = (
        kx[0, 0] * padded[:-2, :-2] + kx[0, 2] * padded[:-2, 2:]
        + kx[1, 0] * padded[1:-1, :-2] + kx[1, 2] * padded[1:-1, 2:]
        + kx[2, 0] * padded[2:, :-2] + kx[2, 2] * padded[2:, 2:]
    )
    gy = (
        ky[0, 0] * padded[:-2, :-2] + ky[0, 1] * padded[:-2, 1:-1] + ky[0, 2] * padded[:-2, 2:]
        + ky[2, 0] * padded[2:, :-2] + ky[2, 1] * padded[2:, 1:-1] + ky[2, 2] * padded[2:, 2:]
    )
    mag = np.sqrt(gx * gx + gy * gy).astype(np.float32)
    mag = (mag - float(mag.min())) / (float(mag.max() - mag.min()) + 1e-6)
    return _to_uint8(mag)


def speckle_channel(img_uint8: "np.ndarray", mask: "np.ndarray | None" = None) -> "np.ndarray":
    """High-frequency residual (abs(img - median_filter(img))).
    
    Captures texture/speckle noise. Uses OpenCV when available (5x faster).
    """
    if _HAS_CV2:
        # OpenCV: fast median blur
        med = cv2.medianBlur(img_uint8, 5)
    else:
        # Pillow fallback
        pil = Image.fromarray(img_uint8, mode="L")
        med = np.asarray(pil.filter(ImageFilter.MedianFilter(size=5)), dtype=np.uint8)
    
    resid = np.abs(img_uint8.astype(np.int16) - med.astype(np.int16)).astype(np.float32)
    max_val = float(resid.max())
    if max_val <= 0:
        return np.zeros_like(img_uint8, dtype=np.uint8)
    resid = resid / (max_val + 1e-6)
    return _to_uint8(resid)


def depth_channel(img_uint8: "np.ndarray", mask: "np.ndarray | None" = None) -> "np.ndarray":
    """Depth-weighted by local signal strength (ultrasound attenuation proxy).
    
    Combines vertical position with local intensity variance to create a 
    content-aware depth channel. Highlights regions where the signal is 
    strong at depth (good penetration) vs weak (signal loss).
    
    Uses OpenCV when available for Gaussian blur, falls back to Pillow.
    """
    h, w = img_uint8.shape
    if h <= 1:
        return np.zeros_like(img_uint8, dtype=np.uint8)
    
    # Depth gradient (0 at top -> 1 at bottom)
    depth = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None]
    depth = np.broadcast_to(depth, (h, w)).astype(np.float32)
    
    # Local intensity variance as signal strength proxy
    img_f = img_uint8.astype(np.float32)
    
    if _HAS_CV2:
        # OpenCV: fast Gaussian blur
        ksize = (15, 15)
        mean_sq = cv2.GaussianBlur(img_f ** 2, ksize, 0)
        sq_mean = cv2.GaussianBlur(img_f, ksize, 0) ** 2
    else:
        # Pillow fallback
        from PIL import ImageFilter
        pil_img = Image.fromarray(img_uint8, mode="L")
        blur = pil_img.filter(ImageFilter.GaussianBlur(radius=7))
        blur_arr = np.asarray(blur, dtype=np.float32)
        mean_sq = np.asarray(Image.fromarray((img_f**2).astype(np.uint8)).filter(
            ImageFilter.GaussianBlur(radius=7)), dtype=np.float32)
        sq_mean = blur_arr ** 2
    
    local_var = np.clip(mean_sq - sq_mean, 0.0, None)
    
    # Normalize signal strength
    max_var = float(local_var.max())
    if max_var > 1e-6:
        signal_strength = local_var / max_var
    else:
        signal_strength = np.zeros_like(local_var)
    
    # Combine depth with signal strength
    # High value = deep region with strong signal (good penetration)
    out = depth * (0.3 + 0.7 * signal_strength)  # Blend factor to keep some depth info
    out = np.clip(out, 0.0, 1.0)
    
    return _to_uint8(out)


def frangi_channel(
    img_uint8: "np.ndarray",
    mask: "np.ndarray | None" = None,
    *,
    sigmas: tuple[float, ...] = (1.0, 2.0, 3.0),
    alpha: float = 0.5,
    beta: float = 0.5,
    gamma: float = 15.0,
    black_ridges: bool = False,
    _clahe_base: "np.ndarray | None" = None,
) -> "np.ndarray":
    """Multi-scale Hessian/Frangi vesselness to enhance fascia/tendon ridges.

    Falls back to edge_channel if skimage is not available.
    
    Args:
        img_uint8: Input grayscale image.
        mask: Unused, kept for API compatibility.
        sigmas: Scales for multi-scale analysis.
        alpha, beta, gamma: Frangi filter parameters.
        black_ridges: Detect dark ridges on light background.
        _clahe_base: Optional pre-computed CLAHE output to avoid redundant computation.
    """
    # Use pre-computed CLAHE if provided, otherwise compute
    if _clahe_base is not None:
        base = _clahe_base
    else:
        base = clahe_channel(img_uint8)
    base_f = _float01(base)

    if _skimage_frangi is not None:
        response = _skimage_frangi(
            base_f,
            sigmas=sigmas,
            alpha=alpha,
            beta=beta,
            gamma=gamma,
            black_ridges=black_ridges,
            mode="reflect",
        )
        response = np.asarray(response, dtype=np.float32)
    else:
        # Fallback to edge channel if skimage not available
        return edge_channel(img_uint8)

    # Normalize response
    response = np.clip(response, 0.0, None)
    max_val = float(response.max())
    if max_val > 1e-8:
        response = response / max_val
    else:
        response = np.zeros_like(response)

    return _to_uint8(response)


def diff_channel(
    img_a: "np.ndarray",
    img_b: "np.ndarray",
    *,
    contrast_stretch: bool = True,
) -> "np.ndarray":
    """Signed difference channel (img_a - img_b).
    
    Computes the difference between two images. By default, stretches the
    result to full 0-255 range for maximum visibility of removed speckle
    and enhanced edges.
    
    Common usage: diff_channel(before_DSC, raw) reveals what device processing did.
    
    Args:
        img_a: First image (typically 'before_DSC' - the enhanced version).
        img_b: Second image (typically 'raw' - the original version).
        contrast_stretch: If True (default), stretch to full 0-255 range.
                         If False, use 128-centered encoding (128 = no change).
    
    Returns:
        Difference encoded as uint8.
    """
    if img_a.shape != img_b.shape:
        raise ValueError(f"Shape mismatch: {img_a.shape} vs {img_b.shape}")
    
    # Compute signed difference: range [-255, +255]
    diff = img_a.astype(np.int16) - img_b.astype(np.int16)
    
    if contrast_stretch:
        # Stretch to full 0-255 range for maximum visibility
        diff_min, diff_max = float(diff.min()), float(diff.max())
        if diff_max > diff_min:
            diff_stretched = ((diff - diff_min) / (diff_max - diff_min) * 255.0)
        else:
            diff_stretched = np.full_like(diff, 128, dtype=np.float32)
        return diff_stretched.clip(0, 255).astype(np.uint8)
    else:
        # 128-centered encoding: 128 = no change
        diff_encoded = ((diff / 2.0) + 128.0).clip(0, 255).astype(np.uint8)
        return diff_encoded


_CHANNEL_FNS: Mapping[str, callable] = {
    "voi": voi_channel,
    "voi_zscore": voi_zscore_channel,
    "log": log_channel,
    "clahe": clahe_channel,
    "contrast": contrast_channel,
    "edge": edge_channel,
    "hist": hist_channel,
    "speckle": speckle_channel,
    "depth": depth_channel,
    "frangi": frangi_channel,
}


def generate_channels(
    img_uint8: "np.ndarray",
    *,
    channel_names: Sequence[str] = DEFAULT_CHANNELS,
    clahe_clip_limit: float = 6.0,
) -> ChannelBundle:
    """Generate a bundle of channels from a grayscale uint8 image.
    
    Note: No ROI masking is applied. All channels process the full image.
    
    Args:
        img_uint8: Input grayscale image (uint8).
        channel_names: List of channel names to generate.
        clahe_clip_limit: Clip limit for CLAHE (default 2.0, OpenCV-compatible).
    """
    channels: Dict[str, "np.ndarray"] = {}
    
    # Pre-compute CLAHE if needed by multiple channels (clahe, frangi)
    clahe_result: "np.ndarray | None" = None
    needs_clahe = {"clahe", "frangi"} & set(channel_names)
    if needs_clahe:
        clahe_result = clahe_channel(img_uint8, clip_limit=clahe_clip_limit)
    
    for name in channel_names:
        if name not in _CHANNEL_FNS:
            raise ValueError(f"Unknown channel: {name!r}. Options: {sorted(_CHANNEL_FNS)}")
        
        if name == "clahe":
            channels[name] = clahe_result
        elif name == "frangi":
            channels[name] = frangi_channel(img_uint8, _clahe_base=clahe_result)
        else:
            fn = _CHANNEL_FNS[name]
            channels[name] = fn(img_uint8)

    return ChannelBundle(channels_uint8=channels)


def validate_channel_names(names: Iterable[str]) -> tuple[str, ...]:
    out = []
    for name in names:
        if name not in _CHANNEL_FNS:
            raise ValueError(f"Unknown channel: {name!r}. Options: {sorted(_CHANNEL_FNS)}")
        out.append(name)
    return tuple(out)


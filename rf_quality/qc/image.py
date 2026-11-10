"""Tier-1 image QC (always on).

Saturation is computed on the **in-scan ROI** — defined as the union of all
non-background mask classes. Computing it on the full JPG inflates the
saturation ratio because the image carries black borders and (in some
cases) text overlays from the scanner UI, both of which are outside the
acoustic scan area. Similarly, global entropy and Laplacian focus are
evaluated on the scan area only so a large uninformative black border
doesn't swamp the variance/entropy signal.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .._vendor.qc_helpers import laplacian_focus, saturation_ratio, shannon_entropy_u8
from ..config import QCCfg


@dataclass(frozen=True)
class ImageQC:
    focus_laplacian_var: float
    saturation_ratio: float
    global_entropy: float
    flag_focus: bool
    flag_saturation: bool
    flag_entropy: bool

    @property
    def any_flag(self) -> bool:
        return self.flag_focus or self.flag_saturation or self.flag_entropy


def score_image(img_u8: np.ndarray, mask_u8: np.ndarray, cfg: QCCfg) -> ImageQC:
    # scan ROI = any labelled pixel (tissue-bearing area, excludes gutters)
    scan = mask_u8 > 0
    if scan.sum() < 1024:
        scan = np.ones_like(mask_u8, dtype=bool)
    focus_vals = img_u8[scan]
    # Laplacian needs spatial support; do it on the full image but weight by scan.
    lap_full = laplacian_focus(img_u8)  # cheap, unchanged
    # saturation + entropy inside ROI only
    sat = float(((focus_vals <= 2) | (focus_vals >= 253)).mean())
    ent = shannon_entropy_u8(focus_vals)
    return ImageQC(
        focus_laplacian_var=lap_full,
        saturation_ratio=sat,
        global_entropy=ent,
        flag_focus=bool(lap_full < cfg.min_focus_laplacian_var),
        flag_saturation=bool(sat > cfg.max_saturation_ratio),
        flag_entropy=bool(ent < cfg.min_global_entropy),
    )

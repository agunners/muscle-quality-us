"""Leltek 128L depth & artifact deconfliction (E5).

Recovers the depth setting of any captured artifact from its intrinsic properties
— DICOM vertical pixel-spacing `dy` for stills, jpg width for sweeps (which have
no `.dcm`) — so a one-study-per-patient Round-3 acquisition can be allocated to
its measurements (sweep / FEMUR / RF, 5 cm / 7 cm) ORDER-INDEPENDENTLY, with no
device-level label. See docs/leltek_128L_depth_deconfliction.md.

128L ONLY: the FOV constant (1967 = width_px · depth_cm) is probe-specific (128LH
is ~1310). Round-3 is single-probe, so the legacy 128L/128LH texture confound is
gone. The exact device steps (~4.9 / ~6.9 cm for the '5'/'7' buttons) are
backup-derived and locked on the first pilot study.

NOTE (v8, 2026-06-09): this per-artifact helper is SUPERSEDED for grouping by
scripts/preflight_helpers/study_deconfliction.py (pairs mp4+RawImages, classifies a
whole study, tolerates missing/orphan .dcm). Two findings from the real pilot zip:
(1) the sweep now SAVE-IMAGEs (has a .dcm) AND its depth moved 6.9->4.9 cm across
v7->v8, so the has_dcm/depth identity rules in allocate_artifact() no longer hold —
classify clip-length FIRST (long->sweep), then depth. (2) the .dcm PixelSpacing depth
and the RawImages jpg-width depth AGREE on ordering but DISAGREE on absolute cm via
these constants — treat width as a deep/shallow DISCRIMINATOR, not an absolute-cm
source, until FOV_CONST/ROWS are re-derived against more confirmed captures.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

ROWS = 585                  # fixed on this device — depth is encoded in dy
FOV_CONST_128L = 1967       # width_px * depth_cm (128L; 128LH ~1310)
DEVICE_STEPS_CM = (4.0, 4.9, 5.9, 6.9, 7.9, 8.9, 9.9, 10.9)
_DEPTH_5CM = 4.9
_DEPTH_7CM = 6.9


def depth_from_dy(dy_mm: float) -> float:
    """depth_cm = Rows · dy_mm / 10 (Rows = 585). dy = DICOM PixelSpacing[0],
    present on stills (the SAVE-IMAGE `.dcm`) only."""
    return ROWS * dy_mm / 10.0


def depth_from_jpg_width(width_px: float) -> float:
    """depth_cm = FOV_const / jpg_width (128L). The UNIVERSAL signal — it also
    covers sweeps, which are MAKE-VIDEO-only and carry no `.dcm`."""
    if width_px <= 0:
        raise ValueError("width_px must be positive")
    return FOV_CONST_128L / float(width_px)


def snap_to_device_step(depth_cm: float) -> float:
    """Nearest discrete device step — the depth buttons snap to ~.9-ending steps."""
    steps = np.asarray(DEVICE_STEPS_CM)
    return float(steps[int(np.argmin(np.abs(steps - depth_cm)))])


def recover_depth(dy_mm: Optional[float] = None,
                  jpg_width_px: Optional[float] = None) -> float:
    """Snapped device depth (cm) from dy (preferred — stills) or jpg width (sweeps)."""
    if dy_mm is not None:
        raw = depth_from_dy(dy_mm)
    elif jpg_width_px is not None:
        raw = depth_from_jpg_width(jpg_width_px)
    else:
        raise ValueError("need dy_mm or jpg_width_px")
    return snap_to_device_step(raw)


def depth_setting(depth_cm: float) -> str:
    """Round-3 setting label from a snapped depth: '5cm' (~4.9) or '7cm' (~6.9).

    Assumes a PRE-SNAPPED 4.9/6.9 input — the allocation path always snaps first;
    a raw mid-band depth (e.g. 5.9, itself a real step but neither button target)
    ties toward 5cm, which never reaches here in practice."""
    return "5cm" if abs(depth_cm - _DEPTH_5CM) <= abs(depth_cm - _DEPTH_7CM) else "7cm"


def allocate_artifact(*, has_dcm: bool, is_long_clip: bool,
                      dy_mm: Optional[float] = None,
                      jpg_width_px: Optional[float] = None) -> dict:
    """Allocate one artifact to {depth_cm, depth_setting, identity}, order-independently.

    identity: 'sweep' (long clip, no `.dcm`, 7 cm by protocol), 'femur' (short
    clip + `.dcm`, 7 cm), 'rf' (short clip + `.dcm`, 5 cm — the RF-1/RF-2
    lift-replace pair), or 'unknown'. Sweep vs FEMUR (both 7 cm) split on
    clip-length / `.dcm`-presence, NOT depth. Left vs right leg is a SEPARATE
    problem (marker convention + segmenter VL/VM) — not decided here."""
    depth = recover_depth(dy_mm=dy_mm, jpg_width_px=jpg_width_px)
    setting = depth_setting(depth)
    if is_long_clip and not has_dcm:
        identity = "sweep"
    elif has_dcm and setting == "7cm":
        identity = "femur"
    elif has_dcm and setting == "5cm":
        identity = "rf"
    else:
        identity = "unknown"
    return {"depth_cm": depth, "depth_setting": setting, "identity": identity}

"""Pixel-spacing correction.

DICOM spacing is expressed in the DICOM coordinate system; the labeled JPG
and mask are in a resized image space. The correction rescales spacing so
that geometry features computed on the ``512 × W`` mask produce mm² / mm
values that line up with the labeling app. Mirrors Folder A's behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Spacing:
    x_mm_per_px: float | None
    y_mm_per_px: float | None
    complete: bool
    source: str  # "dicom", "jpg_scaled", "missing"


def resolve_spacing(
    pixel_spacing_x: float | None,
    pixel_spacing_y: float | None,
    dcm_rows: float | None,
    dcm_cols: float | None,
    jpg_height: int | None,
    jpg_width: int | None,
    mask_height: int | None,
    mask_width: int | None,
) -> Spacing:
    if pixel_spacing_x is None or pixel_spacing_y is None:
        return Spacing(None, None, complete=False, source="missing")
    # pixel_spacing_{x,y} is DCM-native. Rescale to mask space.
    if dcm_rows and dcm_cols and mask_height and mask_width:
        sx = pixel_spacing_x * (dcm_cols / mask_width)
        sy = pixel_spacing_y * (dcm_rows / mask_height)
        return Spacing(float(sx), float(sy), complete=True, source="dicom")
    if jpg_height and jpg_width and mask_height and mask_width:
        sx = pixel_spacing_x * (jpg_width / mask_width)
        sy = pixel_spacing_y * (jpg_height / mask_height)
        return Spacing(float(sx), float(sy), complete=True, source="jpg_scaled")
    return Spacing(float(pixel_spacing_x), float(pixel_spacing_y), complete=True, source="dicom")

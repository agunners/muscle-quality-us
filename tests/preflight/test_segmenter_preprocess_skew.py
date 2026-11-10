"""Regression guard for the item-4 train/serve preprocessing skew.

The segmenter was TRAINED with an aspect-preserving PAD (fastai
`Resize(method="pad", pad_mode="zeros")`) + the `RandomResizedCropGPU` validation
center-crop, at the bundle's `rrc_size` (the validation OUTPUT resolution). The
pre-fix inference path instead did a single `TF.resize` straight to `resize_to`
with NO pad — which *distorts* aspect whenever the frame's W/H differs from the
box's. For the runtime-default `padded_800x640` bundle (box W/H 0.8) a real tall
~256x512 frame (W/H 0.5) got stretched wide, fabricating a spurious RF blob.

These tests pin the FIXED transform:
  (a) it PADS (aspect preserved, zero bars) rather than distorting a tall input;
  (b) it produces the validation resolution (rrc_size), not resize_to;
  (c) its geometry matches the training repo's own inverse-measurement
      reconstruction (`measurement_utils.compute_sample_geometry`).
The `legacy_distort_resize=True` path (kept for the sensitivity A/B) must still
distort, so the contrast is explicit.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import torch

from preflight_helpers.segmenter import (
    BundleMeta, _validation_geometry, _remap_model_output_to_native,
    preprocess_for_model, predict_mask,
)

# The two real bundles' geometry (from their sibling .json `train_cfg`).
PADDED = BundleMeta(
    pkl_path=Path("padded.pkl"), json_path=Path("padded.json"),
    modalities=("voi", "hist", "clahe"),
    stats_mean=(0.17, 0.55, 0.33), stats_std=(0.18, 0.24, 0.29),
    resize_to=(800, 640), rrc_size=(640, 512), rf_class_id_in_json=1,
    rrc_ratio=(1.0, 1.2), rrc_valid_scale=1.0,
)
NATIVE = BundleMeta(
    pkl_path=Path("native.pkl"), json_path=Path("native.json"),
    modalities=("voi", "hist", "clahe"),
    stats_mean=(0.17, 0.55, 0.33), stats_std=(0.18, 0.24, 0.29),
    resize_to=(512, 256), rrc_size=(384, 192), rf_class_id_in_json=1,
    rrc_ratio=(0.45, 0.55), rrc_valid_scale=1.0,
)

# A real frame's native resolution (tall): H=512, W=256 -> W/H = 0.5.
FRAME_H, FRAME_W = 512, 256


def _denorm(x: torch.Tensor, meta: BundleMeta) -> np.ndarray:
    """Undo the per-channel normalize so pad bars read back as ~0."""
    mean = torch.tensor(meta.stats_mean).view(-1, 1, 1)
    std = torch.tensor(meta.stats_std).view(-1, 1, 1)
    return (x[0] * std + mean).numpy()  # (C, H, W)


def _uniform_frame(val: int = 200) -> np.ndarray:
    return np.full((FRAME_H, FRAME_W, 3), val, dtype=np.uint8)


# --------------------------------------------------------------------------- #
# (b) validation resolution                                                    #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("meta,expect", [(PADDED, (640, 512)), (NATIVE, (384, 192))])
def test_fixed_transform_emits_validation_resolution(meta, expect) -> None:
    """The model must see rrc_size (what it was validated at), not resize_to."""
    x = preprocess_for_model(_uniform_frame(), meta, device="cpu")
    assert x.shape == (1, 3, expect[0], expect[1])


def test_legacy_path_emits_resize_to_not_rrc_size() -> None:
    """The pre-fix path fed resize_to — the resolution gap half of the skew."""
    x = preprocess_for_model(_uniform_frame(), PADDED, device="cpu",
                             legacy_distort_resize=True)
    assert x.shape == (1, 3, 800, 640)  # resize_to, NOT rrc_size (640, 512)


# --------------------------------------------------------------------------- #
# (a) pads, not distorts (aspect preserved + zero padding)                     #
# --------------------------------------------------------------------------- #

def test_fixed_transform_pads_a_tall_frame_with_zeros() -> None:
    """A tall 256x512 frame (W/H 0.5) into the 0.8-aspect padded box must gain
    ZERO side-bars (pad), not fill edge-to-edge (distort)."""
    x = preprocess_for_model(_uniform_frame(200), PADDED, device="cpu")
    dn = _denorm(x, PADDED)                      # (3, 640, 512), ~0 bars / ~0.78 content
    col_max = dn.max(axis=0).max(axis=0)         # (512,) per-column max over rows+chans
    zero_cols = int((col_max < 0.02).sum())
    # ~96px zero bar each side (192px total) survive the crop+resize; allow bleed.
    assert 140 < zero_cols < 240, f"expected ~192 zero cols from padding, got {zero_cols}"
    # The outermost columns are pure pad; the centre is content.
    assert col_max[0] < 0.02 and col_max[-1] < 0.02
    assert col_max[256] > 0.5


def test_legacy_transform_distorts_no_zero_bars() -> None:
    """Contrast: the pre-fix distort resize fills the whole box — NO zero bars —
    which is exactly the aspect distortion the fix removes."""
    x = preprocess_for_model(_uniform_frame(200), PADDED, device="cpu",
                             legacy_distort_resize=True)
    dn = _denorm(x, PADDED)
    col_max = dn.max(axis=0).max(axis=0)
    assert int((col_max < 0.02).sum()) == 0    # distort -> no padding anywhere


def test_fixed_transform_preserves_content_aspect() -> None:
    """The non-zero content block must keep the frame's W/H (0.5), proving the
    pad — not a stretch — carried the geometry."""
    x = preprocess_for_model(_uniform_frame(200), PADDED, device="cpu")
    dn = _denorm(x, PADDED).max(axis=0)         # (640, 512)
    rows = np.where(dn.max(axis=1) > 0.5)[0]
    cols = np.where(dn.max(axis=0) > 0.5)[0]
    content_h = rows.max() - rows.min() + 1
    content_w = cols.max() - cols.min() + 1
    # After the center-crop the content fills full height; W/H tracks the frame's.
    assert abs((content_w / content_h) - (FRAME_W / FRAME_H)) < 0.06


# --------------------------------------------------------------------------- #
# (c) geometry agrees with the training repo's own reconstruction              #
# --------------------------------------------------------------------------- #

def test_padded_letterbox_geometry() -> None:
    g = _validation_geometry(FRAME_H, FRAME_W, PADDED)
    assert (g["out_h"], g["out_w"]) == (640, 512)   # rrc_size, the validation res
    # Tall frame letterboxed into the wide box: full HEIGHT kept (no vertical
    # crop) + real left/right zero bars.
    assert g["content_h"] == 640 and g["content_w"] == 320
    assert g["pad_left"] > 0 and g["pad_top"] == 0


def test_native_letterbox_is_pure_downscale() -> None:
    g = _validation_geometry(FRAME_H, FRAME_W, NATIVE)
    # Frame aspect == box aspect -> no pad; a plain downscale to rrc_size.
    assert (g["out_h"], g["out_w"]) == (384, 192)
    assert (g["pad_left"], g["pad_top"]) == (0, 0)
    assert (g["content_h"], g["content_w"]) == (384, 192)


def test_full_validation_transform_would_crop_but_the_fix_does_not() -> None:
    """Documents the deliberate deviation: the training transform's validation
    center-crop clips content vertically (nulls the skin-anchored fat axis), so
    the fix keeps the full frame instead. Cross-checks the crop claim against
    muscle_model_training's own `compute_sample_geometry`."""
    mu = pytest.importorskip("measurement_utils")
    cfg = {
        "resize_target_hw": tuple(PADDED.resize_to),
        "output_hw": tuple(PADDED.rrc_size),
        "valid_scale": PADDED.rrc_valid_scale,
        "ratio": tuple(PADDED.rrc_ratio),
    }
    ref = mu.compute_sample_geometry(FRAME_H, FRAME_W, cfg, 1.0, 1.0)
    assert ref["crop_clips_content_y"] is True          # training DOES crop vertically
    g = _validation_geometry(FRAME_H, FRAME_W, PADDED)
    assert g["content_h"] == g["out_h"]                 # the fix keeps the full frame


def test_native_validation_transform_does_not_crop_content() -> None:
    """R10 gate for the re-base target: the NATIVE bundle's own training
    validation transform does NOT vertically crop content (frame aspect == box
    aspect), so native is faithful on the WHOLE frame with no residual — the
    reason it is the canonical re-base target. Skips (claim 'unverified') if the
    training repo isn't importable; run once in that env to lock it."""
    mu = pytest.importorskip("measurement_utils")
    cfg = {
        "resize_target_hw": tuple(NATIVE.resize_to),
        "output_hw": tuple(NATIVE.rrc_size),
        "valid_scale": NATIVE.rrc_valid_scale,
        "ratio": tuple(NATIVE.rrc_ratio),
    }
    ref = mu.compute_sample_geometry(FRAME_H, FRAME_W, cfg, 1.0, 1.0)
    assert ref["crop_clips_content_y"] is False         # native keeps the full frame
    g = _validation_geometry(FRAME_H, FRAME_W, NATIVE)
    assert g["content_h"] == g["out_h"] and (g["pad_left"], g["pad_top"]) == (0, 0)


# --------------------------------------------------------------------------- #
# inverse remap round-trips to native + predict_mask stays native-shaped       #
# --------------------------------------------------------------------------- #

def test_inverse_remap_round_trips_the_full_frame() -> None:
    g = _validation_geometry(FRAME_H, FRAME_W, PADDED)
    low = np.ones((g["out_h"], g["out_w"]), dtype=np.uint8)   # all class-1
    native = _remap_model_output_to_native(low, g, (FRAME_H, FRAME_W), 0)  # 0 == NEAREST
    assert native.shape == (FRAME_H, FRAME_W)
    # No crop -> the WHOLE native frame carries the prediction, incl. the top
    # (skin, for fat_to_depth) and the deep edge (femur) — nothing blanked.
    assert native[0, FRAME_W // 2] == 1 and native[-1, FRAME_W // 2] == 1
    assert native[FRAME_H // 2, FRAME_W // 2] == 1


class _ShapeAwareModel:
    def eval(self):
        return self

    def __call__(self, x):
        n, _, h, w = x.shape
        logits = torch.zeros(n, 10, h, w)
        logits[:, 1] = 1.0  # everything -> class 1
        return logits


class _Learn:
    model = _ShapeAwareModel()


@pytest.mark.parametrize("legacy", [False, True])
def test_predict_mask_returns_native_shape_both_paths(legacy) -> None:
    frame = _uniform_frame()
    m = predict_mask(_Learn(), frame, PADDED, device="cpu",
                     legacy_distort_resize=legacy)
    assert m.shape == (FRAME_H, FRAME_W)
    assert m.dtype == np.uint8

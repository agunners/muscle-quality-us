"""Segmenter contract: class IDs pinned; the § 5.4 segmentation-confidence signal.

The class-ID tests guard the pinned semantic labels. The Build-Step 2 item-1
tests guard the new segmentation-confidence signal: the pure
`segmentation_confidence_from_logits` reduction and `predict_mask`'s optional
`confidence_out` out-parameter (byte-identical when omitted).
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import torch

from preflight_helpers.segmenter import (
    BundleMeta, LABEL_BACKGROUND, LABEL_FAT, LABEL_FASCIA, LABEL_FEMUR, LABEL_RF,
    LABEL_SARTORIUS, LABEL_SKIN, LABEL_VI, LABEL_VL, LABEL_VM,
    pinned_mask_cfg, predict_mask, segmentation_confidence_from_logits,
)


def test_class_ids_pinned_correctly() -> None:
    assert LABEL_BACKGROUND == 0
    assert LABEL_RF == 1
    assert LABEL_VI == 2
    assert LABEL_VL == 3
    assert LABEL_VM == 4
    assert LABEL_FASCIA == 5
    assert LABEL_SKIN == 6
    assert LABEL_FEMUR == 7
    assert LABEL_FAT == 8
    assert LABEL_SARTORIUS == 9


def test_pinned_mask_cfg_overrides_femur_default() -> None:
    """rf_quality.config.MaskCfg default is femur_class_id=4 (which is VM).
    Our override must set it to 7 (the actual femur class)."""
    cfg = pinned_mask_cfg()
    assert cfg.femur_class_id == 7
    assert cfg.muscle_class_id == 1
    assert cfg.fascia_class_id == 5
    assert cfg.skin_class_id == 6
    assert cfg.fat_class_id == 8
    assert cfg.erosion_px == 3


# ---------------------------------------------------------------------------
# Build-Step 2 item 1 — the segmentation-confidence signal (plan § 5.4)
# ---------------------------------------------------------------------------

class _FakeModel:
    """A stand-in segmentation model — `.eval()` is a no-op, `__call__` ignores
    its input and returns a fixed logits tensor so predict_mask is fully
    deterministic without a GPU or a real bundle."""

    def __init__(self, logits: torch.Tensor):
        self._logits = logits

    def eval(self):
        return self

    def __call__(self, _x):
        return self._logits


class _FakeLearn:
    def __init__(self, logits: torch.Tensor):
        self.model = _FakeModel(logits)


def _meta(resize_to=(16, 8)) -> BundleMeta:
    """A minimal BundleMeta for a fake-model predict_mask call (3-channel in)."""
    return BundleMeta(
        pkl_path=Path("fake.pkl"), json_path=Path("fake.json"),
        modalities=("voi", "hist", "clahe"),
        stats_mean=(0.5, 0.5, 0.5), stats_std=(0.5, 0.5, 0.5),
        resize_to=resize_to, rrc_size=resize_to, rf_class_id_in_json=1,
    )


def test_predict_mask_byte_identical_when_confidence_out_omitted() -> None:
    """build_step_2_plan.md item 1 done-when: with the out-parameter omitted,
    predict_mask is byte-identical to the pre-Build-Step-2 behaviour."""
    torch.manual_seed(0)
    learn = _FakeLearn(torch.randn(1, 10, 16, 8))
    meta = _meta()
    stacked = np.random.RandomState(0).randint(0, 256, (20, 11, 3), dtype=np.uint8)
    mask_plain = predict_mask(learn, stacked, meta, device="cpu")
    mask_with_out = predict_mask(learn, stacked, meta, device="cpu", confidence_out={})
    assert np.array_equal(mask_plain, mask_with_out)
    assert mask_plain.shape == (20, 11)
    assert mask_plain.dtype == np.uint8


def test_predict_mask_populates_confidence_out() -> None:
    """With a confidence_out dict supplied, predict_mask fills it with the
    § 5.4 signal — the map + scalar — plus the native-resolution map aligned
    with the returned mask."""
    torch.manual_seed(1)
    learn = _FakeLearn(torch.randn(1, 10, 16, 8))
    meta = _meta()
    stacked = np.random.RandomState(1).randint(0, 256, (20, 11, 3), dtype=np.uint8)
    conf: dict = {}
    mask = predict_mask(learn, stacked, meta, device="cpu", confidence_out=conf)
    for key in ("confidence_map", "entropy_map", "boundary_entropy",
                "boundary_band_px", "n_classes", "confidence_map_native"):
        assert key in conf, f"confidence_out missing {key!r}"
    assert conf["n_classes"] == 10
    # The native-resolution map aligns with the returned mask (§ 8.3 heatmap).
    assert conf["confidence_map_native"].shape == mask.shape


def test_segmentation_confidence_structural_contract() -> None:
    logits = torch.randn(1, 7, 10, 6)
    argmax = logits.argmax(dim=1).squeeze(0)
    sig = segmentation_confidence_from_logits(logits, argmax, boundary_band_px=2)
    assert sig["n_classes"] == 7
    assert sig["boundary_band_px"] == 2
    assert sig["confidence_map"].shape == (10, 6)
    assert sig["entropy_map"].shape == (10, 6)
    assert sig["confidence_map"].dtype == np.float32
    assert sig["entropy_map"].dtype == np.float32
    assert isinstance(sig["boundary_entropy"], float)


def test_segmentation_confidence_peaked_logits_are_low_entropy() -> None:
    """A sharply-peaked logits tensor — the model is certain — has near-zero
    entropy and near-one confidence everywhere."""
    logits = torch.zeros(1, 8, 10, 6)
    logits[0, 3] = 30.0                              # class 3 dominates every pixel
    argmax = logits.argmax(dim=1).squeeze(0)
    sig = segmentation_confidence_from_logits(logits, argmax)
    assert float(sig["entropy_map"].max()) < 1e-3
    assert float(sig["confidence_map"].min()) > 0.999


def test_segmentation_confidence_uniform_logits_are_max_entropy() -> None:
    """A uniform logits tensor — the model is maximally unsure — has per-pixel
    entropy log(n_classes) and confidence 1/n_classes."""
    n = 8
    logits = torch.zeros(1, n, 10, 6)                # uniform softmax over n classes
    argmax = logits.argmax(dim=1).squeeze(0)
    sig = segmentation_confidence_from_logits(logits, argmax)
    assert abs(float(sig["entropy_map"].mean()) - math.log(n)) < 1e-4
    assert abs(float(sig["confidence_map"].mean()) - 1.0 / n) < 1e-4


def test_segmentation_confidence_boundary_entropy_tracks_uncertainty() -> None:
    """boundary_entropy is near-zero when the segmenter is confident at the
    class boundary and high when it is ambiguous there (§ 5.4 — 'high boundary
    entropy means the model itself is unsure where the muscle edge is')."""
    n, h, w = 6, 12, 12
    # Confident: class 1 in the left half, class 2 in the right — each sharply
    # peaked. A clean boundary, but the model is sure on both sides of it.
    confident = torch.zeros(1, n, h, w)
    confident[0, 1, :, : w // 2] = 30.0
    confident[0, 2, :, w // 2:] = 30.0
    amap_c = confident.argmax(dim=1).squeeze(0)
    sig_c = segmentation_confidence_from_logits(confident, amap_c, boundary_band_px=1)
    # Ambiguous: peaked interiors, but a uniform (max-entropy) strip at the seam.
    ambiguous = torch.zeros(1, n, h, w)
    ambiguous[0, 1, :, : w // 2 - 2] = 30.0
    ambiguous[0, 2, :, w // 2 + 2:] = 30.0           # the four centre columns stay uniform
    amap_a = ambiguous.argmax(dim=1).squeeze(0)
    sig_a = segmentation_confidence_from_logits(ambiguous, amap_a, boundary_band_px=1)
    assert sig_c["boundary_entropy"] < 1e-3
    assert sig_a["boundary_entropy"] > 0.5
    assert sig_a["boundary_entropy"] > sig_c["boundary_entropy"]


def test_segmentation_confidence_no_boundary_returns_zero() -> None:
    """A degenerate single-class mask has no inter-class boundary, so the
    boundary-entropy scalar is 0.0 — such a frame is caught by the is_empty /
    femur / wrong_site detectors, not by this signal."""
    logits = torch.randn(1, 5, 8, 8)
    single_class = torch.zeros((8, 8), dtype=torch.long)
    sig = segmentation_confidence_from_logits(logits, single_class)
    assert sig["boundary_entropy"] == 0.0

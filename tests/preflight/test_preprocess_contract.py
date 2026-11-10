"""The preprocess-faithfulness contract.

`preprocess_for_model` reproduces each bundle's fastai validation transform as a
letterbox pad straight into `rrc_size`. That shortcut is only equivalent to
training's pad->resize_to->crop-to-rrc_size when `resize_to` and `rrc_size` share
aspect, and the pad method is 'pad'/'zeros'. `assert_faithful_preprocess` turns
those preconditions — a convention across all current fastai bundles — into a
checked contract enforced at load (`BundleMeta.from_pkl`), so a future bundle that
would be silently mis-preprocessed fails loudly instead.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from preflight_helpers import env
from preflight_helpers.segmenter import (
    BundleMeta, PreprocessingContractError, assert_faithful_preprocess,
)


def _meta(resize_to, rrc_size, *, resize_method=None, pad_mode=None) -> BundleMeta:
    return BundleMeta(
        pkl_path=Path("x/fake.pkl"), json_path=Path("x/fake.json"),
        modalities=("voi",), stats_mean=(0.0,), stats_std=(1.0,),
        resize_to=resize_to, rrc_size=rrc_size, rf_class_id_in_json=1,
        resize_method=resize_method, pad_mode=pad_mode,
    )


# ------------------------------------------------------------- passing cases ----

def test_native_config_passes() -> None:
    assert_faithful_preprocess(_meta((512, 256), (384, 192)))   # aspect 0.5 == 0.5


def test_padded_config_passes() -> None:
    assert_faithful_preprocess(_meta((800, 640), (640, 512)))   # aspect 0.8 == 0.8


def test_forward_compat_explicit_pad_zeros_passes() -> None:
    """When a future notebook stamps the method, the happy path still passes."""
    assert_faithful_preprocess(_meta((512, 256), (384, 192),
                                     resize_method="pad", pad_mode="zeros"))


# ------------------------------------------------------------- failing cases ----

def test_aspect_mismatch_raises() -> None:
    """resize_to and rrc_size with different aspect → the letterbox shortcut is
    wrong → the bundle must not load silently."""
    with pytest.raises(PreprocessingContractError):
        assert_faithful_preprocess(_meta((512, 256), (400, 192)))  # 0.5 vs 0.48


def test_explicit_non_pad_method_raises() -> None:
    with pytest.raises(PreprocessingContractError):
        assert_faithful_preprocess(_meta((512, 256), (384, 192), resize_method="squish"))


def test_explicit_non_zeros_pad_mode_raises() -> None:
    with pytest.raises(PreprocessingContractError):
        assert_faithful_preprocess(_meta((512, 256), (384, 192), pad_mode="reflection"))


def test_incomplete_sizes_raise() -> None:
    with pytest.raises(PreprocessingContractError):
        assert_faithful_preprocess(_meta((0, 0), (384, 192)))


# ------------------------------------------------------------- real bundle ----

@pytest.mark.integration
def test_canonical_bundle_loads_through_the_contract() -> None:
    """The real canonical native bundle passes the contract via from_pkl (the load
    path the app + scripts use) — proving the guard is non-destructive today."""
    from preflight_helpers.model_registry import resolve_canonical_pkl
    pkl = resolve_canonical_pkl()
    assert pkl is not None and pkl.exists()
    meta = BundleMeta.from_pkl(pkl)  # raises PreprocessingContractError if violated
    assert meta.resize_to and meta.rrc_size
    # native validates at 384x192, valid_scale 1.0 → exact reproduction
    assert meta.rrc_size == (384, 192)
    assert meta.rrc_valid_scale == 1.0

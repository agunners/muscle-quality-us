"""Phase 0.5 — the model selector shows the HONEST resolution.

The old card showed `resize_to` (the training pad box, e.g. 800×640) as if it
were the resolution the model works at. The model actually validates/infers at
`rrc_size` (e.g. 384×192, letterbox-padded, no crop). The label must show the
latter, and must star the canonical bundle so an override is a visible choice.
"""
from __future__ import annotations

from pathlib import Path

from preflight_helpers.model_registry import BundleEntry
from labeling_app.assessment_pages.p02_model_selector import _bundle_label, _rrc


def _mk(stem: str, resize_to, rrc_size) -> BundleEntry:
    return BundleEntry(
        pkl_path=Path(f"x/{stem}.pkl"), json_path=Path(f"x/{stem}.json"),
        source="local", valid=True, resize_to=resize_to, rrc_size=rrc_size, fold=4,
    )


def test_rrc_is_validation_res_with_resize_to_fallback() -> None:
    assert _rrc(_mk("b", (512, 256), (384, 192))) == (384, 192)   # rrc wins
    assert _rrc(_mk("b", (800, 640), (0, 0))) == (800, 640)       # falls back if absent


def test_label_shows_validation_res_not_pad_box() -> None:
    b = _mk("native_fold4", (512, 256), (384, 192))
    lab = _bundle_label(b, {"stem": "native_fold4"})
    assert "validates 384×192" in lab          # the true inference resolution
    assert "512" not in lab and "256" not in lab  # NOT the resize_to pad box


def test_label_stars_the_canonical_bundle() -> None:
    b = _mk("native_fold4", (512, 256), (384, 192))
    assert "★ canonical" in _bundle_label(b, {"stem": "native_fold4"})
    assert "★ canonical" not in _bundle_label(b, {"stem": "some_other_bundle"})
    assert "★ canonical" not in _bundle_label(b, None)

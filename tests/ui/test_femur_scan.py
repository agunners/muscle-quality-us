"""femur_scan — sweep-wide QMT-tier scan for the picker femur status display.

The GPU seam (synthesize_final_u8 / build_voi_hist_clahe_stack / predict_mask)
and frame_qmt_status are stubbed so each frame's "mask" carries its intended
tier — the test owns the wiring: cache read+fill, per-frame status, the
Tier-1-first sort, and the progress callback.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

import labeling_app.assessment_pages.femur_scan as fs


class _BM:
    modalities = ["voi", "hist", "clahe"]


def _stub_segmenter(monkeypatch, tier_by_fb: dict) -> None:
    """Stub the GPU seam so each frame's mask encodes its intended tier."""
    monkeypatch.setattr(fs, "synthesize_final_u8",
                        lambda raw, fb: np.full((4, 4), tier_by_fb[fb], np.uint8))
    monkeypatch.setattr(fs, "build_voi_hist_clahe_stack",
                        lambda final, modalities: final)
    monkeypatch.setattr(fs, "predict_mask",
                        lambda learn, stacked, bm, device: stacked)
    monkeypatch.setattr(fs, "frame_qmt_status", lambda mask: {
        "tier": int(mask[0, 0]), "label": f"Tier {int(mask[0, 0])}",
        "provenance": "stub", "guidance": None, "flags": [],
        "colour": "x", "meaning": "x"})


def test_scan_ranks_femur_frames_first(monkeypatch) -> None:
    _stub_segmenter(monkeypatch, {"a": 3, "b": 1, "c": 2, "d": 1})
    mask_cache, final_cache = {}, {}
    ranked = fs.scan_sweep_for_femur(
        ["a", "b", "c", "d"], object(), _BM(), Path("/x"),
        mask_cache=mask_cache, final_cache=final_cache)
    # Tier 1 first (b, d in sweep order), then Tier 2 (c), then Tier 3 (a).
    assert [r["frame_base"] for r in ranked] == ["b", "d", "c", "a"]
    assert [r["tier"] for r in ranked] == [1, 1, 2, 3]
    assert set(mask_cache) == {"a", "b", "c", "d"}     # cache filled
    assert set(final_cache) == {"a", "b", "c", "d"}


def test_scan_reuses_cached_masks(monkeypatch) -> None:
    _stub_segmenter(monkeypatch, {"b": 1})             # only "b" gets segmented
    calls: list = []
    stub_predict = fs.predict_mask
    monkeypatch.setattr(fs, "predict_mask",
                        lambda *a, **k: calls.append(1) or stub_predict(*a, **k))
    mask_cache = {"a": np.full((4, 4), 2, np.uint8)}   # "a" pre-segmented
    ranked = fs.scan_sweep_for_femur(
        ["a", "b"], object(), _BM(), Path("/x"),
        mask_cache=mask_cache, final_cache={})
    assert len(calls) == 1                             # only "b" segmented
    assert {r["frame_base"]: r["tier"] for r in ranked} == {"a": 2, "b": 1}


def test_scan_progress_callback(monkeypatch) -> None:
    _stub_segmenter(monkeypatch, {"a": 3, "b": 3})
    seen: list = []
    fs.scan_sweep_for_femur(
        ["a", "b"], object(), _BM(), Path("/x"),
        mask_cache={}, final_cache={},
        progress=lambda frac, fb: seen.append((round(frac, 2), fb)))
    assert seen == [(0.5, "a"), (1.0, "b")]

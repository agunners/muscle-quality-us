"""Tests for study_discovery.study_modalities — the 3-modality loader the 3-phase picker addresses.

A materialized study exposes the SWEEP at the root + the femur/center-RF cines under captures/; a
legacy single-modality study (no captures/) must degrade to just {'sweep'} so the picker shows one
phase exactly as before.
"""
from __future__ import annotations

import json
from pathlib import Path

from preflight_helpers.study_discovery import study_modalities, list_frame_bases


def _cap(d: Path, dcm_name: str, n_frames: int = 1) -> None:
    raw = d / "RawImagesForResearch_20260101_120000000"
    raw.mkdir(parents=True)
    for i in range(n_frames):
        (raw / f"20260101_{120000001 + i:09d}.jpg").write_bytes(b"x")
    (d / dcm_name).write_bytes(b"x")


def _materialized_tree(root: Path, depths=(4.9, 6.9, 4.9)) -> Path:
    """A minimal on-disk materialized study (no real images) for the loader."""
    sp = root / "op" / "sess" / "study"
    _cap(sp, "sweep.dcm", n_frames=3)
    (sp / "s.mp4").write_bytes(b"x")
    _cap(sp / "captures" / "c1_femur_deep", "femur.dcm", n_frames=2)
    _cap(sp / "captures" / "c2_rf_shallow", "rf.dcm", n_frames=2)
    manifest = {"primary": {"identity": "sweep", "dcm_depth_cm": depths[0]},
                "annex": [{"identity": "femur", "dcm_depth_cm": depths[1]},
                          {"identity": "rf", "dcm_depth_cm": depths[2]}]}
    (sp / "materialize_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return sp


def test_three_modalities_with_depths(tmp_path):
    sp = _materialized_tree(tmp_path)
    mods = study_modalities(sp)
    assert set(mods) == {"sweep", "femur", "rf"}
    assert mods["sweep"].depth_cm == 4.9 and len(list_frame_bases(mods["sweep"].raw_image_dir)) == 3
    assert mods["femur"].depth_cm == 6.9 and mods["femur"].role == "femur"
    assert mods["rf"].depth_cm == 4.9
    assert all(m.dicom_path is not None for m in mods.values())
    # sweep is first (the discoverable primary), then the captures/ order
    assert list(mods)[0] == "sweep"


def test_deeper_probe_depths_carry_through(tmp_path):
    # a probe-831 study: sweep+rf at 6.9, femur at 9.9 — the loader surfaces the MEASURED depths
    sp = _materialized_tree(tmp_path, depths=(6.9, 9.9, 6.9))
    mods = study_modalities(sp)
    assert mods["sweep"].depth_cm == 6.9 and mods["femur"].depth_cm == 9.9 and mods["rf"].depth_cm == 6.9


def test_legacy_single_modality_is_sweep_only(tmp_path):
    # a bare sweep triple (no captures/, no manifest) -> just {'sweep'}, depth unknown
    sp = tmp_path / "op" / "sess" / "study"
    _cap(sp, "s.dcm")
    (sp / "s.mp4").write_bytes(b"x")
    mods = study_modalities(sp)
    assert set(mods) == {"sweep"} and mods["sweep"].depth_cm is None

"""`study_is_new_round` — the source classifier behind the p01 'Show studies from' filter
(Akadir 2026-06-25). A new-round study has the 3-modality captures (femur / center-RF cine) and
opens the tabbed picker; a legacy single-sweep study does not. Mirrors study_modalities' role test.
"""
from preflight_helpers.study_discovery import study_is_new_round


def test_legacy_single_sweep_is_not_new(tmp_path):
    # Legacy: a root RawImagesForResearch_* sweep, no captures/.
    (tmp_path / "RawImagesForResearch_20260101_000000").mkdir()
    assert study_is_new_round(tmp_path) is False


def test_femur_capture_is_new(tmp_path):
    (tmp_path / "captures" / "c1_femur_deep_20260101").mkdir(parents=True)
    assert study_is_new_round(tmp_path) is True


def test_rf_capture_is_new(tmp_path):
    (tmp_path / "captures" / "c2_rf_shallow_20260101").mkdir(parents=True)
    assert study_is_new_round(tmp_path) is True


def test_captures_with_only_annex_dirs_is_not_new(tmp_path):
    # captures/ exists but only holds the _dcm_pool annex — no femur/rf modality → still legacy.
    (tmp_path / "captures" / "_dcm_pool").mkdir(parents=True)
    assert study_is_new_round(tmp_path) is False


def test_missing_study_path_is_not_new(tmp_path):
    assert study_is_new_round(tmp_path / "does_not_exist") is False

"""Discovery walks studies_root and validates an addressable capture (DICOM + RawImages).

An .mp4 is deliberately NOT required — see `_scan_study_dir`'s docstring and the synthetic
section at the bottom of this file.
"""
from __future__ import annotations

import pytest

from preflight_helpers.study_discovery import (
    _alive_cohort,
    _scan_study_dir,
    discover_all_studies,
    discover_backup_cohort,
    discover_studies,
    list_frame_bases,
)
from preflight_helpers.study_metadata import study_uid_for_dirname


@pytest.mark.integration
def test_cohort_studies_discover_valid() -> None:
    """The 4 cohort studies must always discover as valid. studies_root is a
    local, freely-populated directory — new captures (self-studies) get dropped
    in directly — so we assert the cohort set is present, not an exact total."""
    entries = discover_studies()
    valid_codes = {e.meta.study_code for e in entries if e.valid}
    cohort = {"ACL762", "AGZ855", "BHW541", "DFN534"}
    assert cohort <= valid_codes, f"missing cohort studies: {cohort - valid_codes}"


@pytest.mark.integration
def test_phi_never_in_ui_dict() -> None:
    """The UI-dict slice must drop raw_path + surname/firstname."""
    entries = discover_studies()
    for e in entries:
        d = e.ui_dict()
        assert "raw_path" not in d
        assert "_phi" not in d
        # Surname / firstname should not appear as keys nor as values.
        flat = " ".join(str(v) for v in d.values())
        # Folder names contain underscores + lowercase Turkish chars; the
        # study_code is uppercase 3-letter + 3-digit so it won't match.
        for forbidden in ("anonadm_anonabu", "anonadq_anonadh", "anonadt", "anonacm"):
            assert forbidden not in flat


@pytest.mark.integration
def test_backup_cohort_surfaces_272_alive() -> None:
    """Slice G — the alive backup cohort is the 272 akadir-completed studies,
    every one valid (a RawImagesForResearch_ dir with frames)."""
    alive = _alive_cohort()
    assert len(alive) == 272, f"expected 272 alive, got {len(alive)}"
    entries = discover_backup_cohort()
    assert len(entries) == 272
    not_valid = [(e.meta.study_code, e.drop_reason) for e in entries if not e.valid]
    assert not not_valid, f"unexpected invalid backup studies: {not_valid[:10]}"
    # study_code-addressed, distinct.
    codes = [e.meta.study_code for e in entries]
    assert len(codes) == len(set(codes))


@pytest.mark.integration
def test_discover_all_merges_root_and_backup() -> None:
    """Slice G — merged discovery carries both the studies_root cohort and the
    backup cohort, deduped by the collision-free study_uid, with studies_root
    winning any overlap. Keyed on study_uid, NOT study_code (Phase 4a): the AKA
    display pseudonym repeats across distinct studies, so keying the merge on it
    silently dropped one member of every collision — the bug this fix closes."""
    def _uid(e) -> str:
        return study_uid_for_dirname(e.study_path.name)

    root = {_uid(e): e for e in discover_studies()}
    merged = discover_all_studies()
    codes = [e.meta.study_code for e in merged]
    # Display-disambiguation guarantees distinct codes even across AKA collisions.
    assert len(codes) == len(set(codes)), "merged discovery has duplicate study_codes"
    code_set = set(codes)
    # The 4 studies_root cohort studies survive the merge.
    assert {"ACL762", "AGZ855", "BHW541", "DFN534"} <= code_set
    # At least the 272 alive are surfaced (plus any studies_root-only entries).
    assert len(merged) >= 272
    # Every distinct studies_root study (by true identity) survives with its own
    # study_path — studies_root wins overlap, and BOTH members of an AKA collision
    # are kept (keying on study_code here would have hidden the drop).
    by_uid = {_uid(e): e for e in merged}
    for uid, root_entry in root.items():
        assert by_uid[uid].study_path == root_entry.study_path


@pytest.mark.integration
def test_backup_cohort_demographics_filled_from_decision_log() -> None:
    """Slice G — backup studies whose folder name doesn't parse still get age /
    sex / BMI from the 3-operator decision log; without this ~39% of the picker
    cards render blank (caught by an actual screenshot, not a unit test)."""
    entries = discover_backup_cohort()
    blank = [e.meta.study_code for e in entries
             if e.meta.age is None and e.meta.sex is None and e.meta.bmi is None]
    assert len(blank) <= 3, f"too many blank-demographic studies: {len(blank)}"
    qqf = next((e for e in entries if e.meta.study_code == "QQF025"), None)
    assert qqf is not None
    assert qqf.meta.age is not None and qqf.meta.bmi is not None  # log-filled


@pytest.mark.integration
def test_backup_cohort_phi_safe() -> None:
    """Slice G — the backup-cohort UI slice drops raw_path + _phi and never
    leaks a folder path (the PHI guardrail)."""
    for e in discover_backup_cohort()[:30]:
        d = e.ui_dict()
        assert "raw_path" not in d and "_phi" not in d
        # No path separators in any rendered value (study_code + demographics).
        flat = " ".join(str(v) for v in d.values())
        assert "/" not in flat
        for forbidden in ("anonacy", "anonadd", "anonaaq/17"):
            assert forbidden not in flat


@pytest.mark.integration
def test_list_frame_bases_picks_only_bare_jpg() -> None:
    entries = discover_studies()
    e = next(x for x in entries if x.meta.study_code == "AGZ855")
    bases = list_frame_bases(e.raw_image_dir)
    # All bases match the YYYYMMDD_HHMMSSmmm pattern (8 + _ + 9).
    assert all(len(b) == 18 and b[8] == "_" for b in bases)
    # AGZ855 has at least the 3 cohort-canonical frames.
    assert "20260127_151031646" in bases
    assert "20260127_151045625" in bases
    assert "20260127_151052505" in bases


# ---------------------------------------------------------------------------
# _scan_study_dir — synthetic layouts, no real data.
#
# The mp4 requirement was vestigial from the era when a study was one flat capture triple.
# On the v8 sweep-primary layout it silently dropped every study whose sweep take had no
# video — 15 real studies, 6 of them from the 2026-07-30 batch — even though nothing under
# labeling_app/ opens an .mp4 and study_modalities() addresses captures via raw dirs + .dcm.
# Absence is now a caveat that rides visibly (feedback_flag_carry_caveats_dont_gate).
# ---------------------------------------------------------------------------
def _capture(d, *, dcm=True, mp4=False, raw=True, frames=("20260728_105826837",)):
    """Write one capture's artifacts into directory `d`."""
    d.mkdir(parents=True, exist_ok=True)
    if dcm:
        (d / f"{d.name}_20260728_105826837_2.dcm").write_bytes(b"\x00")
    if mp4:
        (d / f"{d.name}.mp4").write_bytes(b"\x00")
    if raw:
        r = d / "RawImagesForResearch_20260728_105826837"
        r.mkdir(exist_ok=True)
        for f in frames:
            (r / f"{f}.jpg").write_bytes(b"\x00")
    return d


def _v8_study(tmp_path, *, root_mp4: bool, root_capture: bool = True):
    """A sweep-primary v8 study: sweep at the root, femur + center-RF under captures/."""
    s = tmp_path / "28jul26_126_830_a_b_48k_170_57_46r"
    s.mkdir(parents=True, exist_ok=True)
    if root_capture:
        _capture(s, dcm=True, mp4=root_mp4, raw=True)
    _capture(s / "captures" / "c1_femur_deep", dcm=True, mp4=True, raw=True)
    _capture(s / "captures" / "c2_rf_shallow", dcm=True, mp4=True, raw=True)
    return s


def test_flat_legacy_study_with_everything_is_valid_and_unremarkable(tmp_path) -> None:
    s = _capture(tmp_path / "16jun26_012_831_a_b_67k_163_70_041.5", dcm=True, mp4=True, raw=True)
    valid, reason, raw, dcm, mp4, caveats = _scan_study_dir(s)
    assert (valid, reason, caveats) == (True, None, ())
    assert raw is not None and dcm is not None and mp4 is not None


def test_v8_study_missing_its_sweep_video_is_VALID_with_a_caveat(tmp_path) -> None:
    """THE regression: these were dropped as `missing_mp4` and could not be assessed."""
    s = _v8_study(tmp_path, root_mp4=False)
    valid, reason, raw, dcm, mp4, caveats = _scan_study_dir(s)
    assert valid is True, "a study with frames + DICOM must be assessable without a video"
    assert reason is None
    assert caveats == ("no_video",), "the missing video must still be surfaced, not dropped"
    assert raw is not None and dcm is not None


def test_v8_study_with_its_sweep_video_has_no_caveat(tmp_path) -> None:
    valid, reason, _raw, _dcm, mp4, caveats = _scan_study_dir(_v8_study(tmp_path, root_mp4=True))
    assert (valid, reason, caveats) == (True, None, ())
    assert mp4 is not None


def test_captures_supply_the_capture_when_the_sweep_take_is_absent(tmp_path) -> None:
    """No root triple at all — the femur/RF cines still make the study addressable."""
    s = _v8_study(tmp_path, root_mp4=False, root_capture=False)
    valid, reason, raw, dcm, _mp4, caveats = _scan_study_dir(s)
    assert valid is True and reason is None
    assert "no_sweep_take" in caveats
    assert raw is not None and dcm is not None


def test_a_dicom_is_still_required(tmp_path) -> None:
    """Pixel spacing / depth come from the DICOM — raw pixels are not scale-comparable."""
    s = _capture(tmp_path / "no_dcm_001_831_a_b_50e_170_70_45r", dcm=False, mp4=True, raw=True)
    valid, reason, *_ = _scan_study_dir(s)
    assert (valid, reason) == (False, "missing_dicom")


def test_frames_are_still_required(tmp_path) -> None:
    s = _capture(tmp_path / "no_raw_001_831_a_b_50e_170_70_45r", dcm=True, mp4=True, raw=False)
    valid, reason, *_ = _scan_study_dir(s)
    assert (valid, reason) == (False, "missing_raw_images_dir")


def test_a_nonexistent_path_is_not_a_directory(tmp_path) -> None:
    valid, reason, *_ = _scan_study_dir(tmp_path / "nope")
    assert (valid, reason) == (False, "not_a_directory")

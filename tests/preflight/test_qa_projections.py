"""The three qa projections must not diverge — and the sheet tab must not widen.

`build_qa_sheet` is now one builder feeding three projections: the lean sheet tab (A), the
study-level analysis table (B), and the frame index (C). The failure mode this locks down is
silent widening: an analysis column added to the shared row dict leaking into the glanceable
tab, or the master table drifting off the sheet's column order so a joiner reads the wrong cell.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import build_qa_sheet as b  # noqa: E402


# The sheet tab's width is a DESIGN decision (glanceable), not an accident. Changing it is a
# deliberate act that should have to update this number.
SHEET_COLS = 34


def test_sheet_tab_stays_lean():
    assert len(b.HEADER) == SHEET_COLS
    assert len(set(b.HEADER)) == len(b.HEADER), "duplicate key in the sheet header"


def test_master_extends_the_sheet_without_reordering_it():
    """(B) = (A) + additions, in that order — a positional reader of the sheet columns stays right."""
    assert b.MASTER_COLUMNS[:len(b.HEADER)] == b.HEADER
    extra = b.MASTER_COLUMNS[len(b.HEADER):]
    assert set(extra).isdisjoint(b.HEADER), "an analysis column collides with a sheet column"
    for needed in ("age", "sex", "height_cm", "weight_kg", "bmi",     # the confounder floor
                   "isok_torque_ext_nm", "isok_mean_power_ext_w", "isok_cv_pct",
                   "quality_label", "cohort_round", "rel_path"):
        assert needed in extra


def test_payload_projects_only_the_sheet_columns():
    """Extra keys on a row must be INERT for the sheet — this is what keeps the tab from widening."""
    row = {k: "x" for k in b.MASTER_COLUMNS}
    payload = b.to_payload([row])
    assert payload["header"] == b.HEADER
    assert len(payload["rows"][0]) == SHEET_COLS


def test_demographics_prefers_the_sheet_and_records_the_source():
    class SN:
        matched, age, sex, height, weight, date = True, 40, "e", 170, 70, None

    d = b.demographics({"age": 41, "sex": "F", "bmi": 22.0},
                       {"height_cm": "180", "weight_kg": "80"}, SN())
    assert (d["height_cm"], d["weight_kg"], d["hw_source"]) == (180.0, 80.0, "sheet")
    assert (d["age"], d["sex"], d["bmi"]) == (41.0, "F", 22.0)


def test_demographics_falls_back_to_the_study_name_when_the_sheet_is_absent():
    class SN:
        matched, age, sex, height, weight, date = True, 40, "e", 170, 70, None

    d = b.demographics({}, {}, SN())
    assert (d["height_cm"], d["weight_kg"], d["hw_source"]) == (170.0, 70.0, "study_name")
    assert d["age"] == 40.0 and d["sex"] == "M"
    assert d["bmi"] == pytest.approx(24.2, abs=0.05)   # derived from the two raw numbers


def test_a_partial_sheet_fill_recovers_the_other_field_from_the_study_name():
    """Height and weight resolve INDEPENDENTLY. A height-only sheet row must not blank a weight the
    study name carries (that row would drop out of the complete-case force model) — and must not be
    labelled 'sheet' when half of it came from the grammar."""
    class SN:
        matched, age, sex, height, weight, date = True, 40, "e", 170, 76, None

    d = b.demographics({}, {"height_cm": "170", "weight_kg": ""}, SN())
    assert (d["height_cm"], d["weight_kg"]) == (170.0, 76.0)
    assert d["hw_source"] == "height:sheet+weight:study_name"
    assert d["bmi"] == pytest.approx(26.3, abs=0.05)      # no longer blanked as a knock-on


@pytest.mark.parametrize("variant", [
    "İzokinetik CV (%)", "Izokinetik CV (%)", "izokinetik cv (%)",
    "İzokinetik  CV (%) ", "IZOKINETIK CV (%)", "ızokinetik CV (%)",
])
def test_turkish_force_headers_match_through_casing_spacing_and_dotted_i(variant):
    """An exact-only header match fails SILENTLY and blanks the PRIMARY criterion column for every
    row. Casing, stray whitespace and a plain ASCII 'I' typed for 'İ' must all still resolve."""
    assert b._norm_header(variant) == b._norm_header("İzokinetik CV (%)")


def test_the_normalizer_does_not_collide_the_two_dynamometer_families():
    """Folding must not go so far that İzometrik and İzokinetik become the same column."""
    assert b._norm_header("İzometrik Tepe Tork (Nm)") != b._norm_header("İzokinetik Tepe Tork Ekst (Nm)")


def test_unresolved_sex_is_blank_not_the_word_unknown():
    """The old cohort spells it 'unknown', the grammar spells it '' — one spelling downstream, or a
    model reads 'unknown' as a third level."""
    assert b.demographics({"sex": "unknown"}, {}, None)["sex"] == ""
    assert b.demographics({}, {}, None)["sex"] == ""


def test_a_flaky_sheet_cell_blanks_rather_than_poisons():
    d = b.demographics({}, {"height_cm": "-", "weight_kg": ""}, None)
    assert d["height_cm"] == "" and d["weight_kg"] == "" and d["bmi"] == ""


def test_opsel_picks_of_a_pre_fix_record_is_empty_not_a_guess():
    assert b.opsel_picks({}) == {}
    assert b.opsel_picks({"routed_picks": {"rf": {"frame_bases": ["a"]}}}) == {}   # no ANALYSIS bases
    assert b.opsel_picks({"routed_picks": {"rf": {"analysis_frame_bases": ["a", "b"]}}}) == {
        "rf": {"a", "b"}}


def test_phi_outputs_are_git_ignored():
    """The guard, not just the .gitignore line: both analysis tables bind uid -> raw dirname."""
    from study_registry import _assert_git_ignored
    _assert_git_ignored(b.MASTER_CSV)
    _assert_git_ignored(b.FRAMES_CSV)


def test_the_manifest_bound_dcm_wins_over_an_alphabetical_glob(tmp_path):
    """The ruler is what makes frames physically comparable; a glob is alphabetical, so a second
    .dcm landing in a capture dir must not be able to silently win."""
    cap = tmp_path / "c1_rf_shallow"
    cap.mkdir()
    (cap / "aaa_pooled.dcm").write_bytes(b"")
    (cap / "zzz_bound.dcm").write_bytes(b"")
    assert b._manifest_dcm(cap, tmp_path, "zzz_bound.dcm") == cap / "zzz_bound.dcm"
    assert b._manifest_dcm(cap, tmp_path, None) is None            # no member -> caller globs
    assert b._manifest_dcm(cap, tmp_path, "gone.dcm") is None      # named but absent -> glob


def test_a_disk_walk_keeps_the_bag_when_manifest_enumeration_fails(tmp_path):
    """A study-level enumeration failure used to drop EVERY frame of that study from the index — a
    whole-bag loss for a MIL table, invisible in the csv."""
    raw = tmp_path / "RawImagesForResearch_20260702_151553368"
    raw.mkdir(parents=True)
    (raw / "20260702_151553368.jpg").write_bytes(b"")
    (raw / "thumb.jpg").write_bytes(b"")                            # not a content frame
    cap_raw = tmp_path / "captures" / "c1_femur_deep" / "RawImagesForResearch_x"
    cap_raw.mkdir(parents=True)
    (cap_raw / "20260702_151502111.jpg").write_bytes(b"")

    recs = b._disk_capture_records(tmp_path)
    assert [r[0] for r in recs] == ["sweep(root)", "c1_femur_deep"]
    for _label, _raw, identity, member, depth in recs:
        assert (identity, member, depth) == (None, None, None)      # unknown, never guessed


def test_the_frame_index_columns_are_the_contract():
    assert b.FRAME_COLUMNS[:5] == [
        "study_uid", "role", "capture_dir", "frame_base", "rel_jpg_path"]
    # depth + spacing are load-bearing: RF depth varies 4.9-8.9cm, so pixels are not physically
    # comparable across studies without them.
    assert "depth_cm" in b.FRAME_COLUMNS and "pixel_spacing_row_mm" in b.FRAME_COLUMNS

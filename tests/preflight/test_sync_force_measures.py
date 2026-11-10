"""Tests for the force-datasheet join (scripts/sync_force_measures.py) — pure logic, no live sheet."""
from preflight_helpers import datasheet_schema as ds

import sync_force_measures as sfm


def test_force_headers_match_schema_and_are_text_formatted():
    # the 5 target headers must exist in the schema EXACTLY (guards a rename) + stay text-formatted
    # (so a CoV like '26.8' never date/locale-coerces)
    assert len(sfm.FORCE_VALUE_HEADERS) == 5
    assert sfm.FORCE_VALUE_HEADERS[1] == "İzometrik CV (%)"   # CoV grouped right after its torque (force col C)
    for h in sfm.FORCE_VALUE_HEADERS:
        assert h in ds.HEADERS
        assert h in ds.TEXT_FORMAT_HEADERS
        assert h not in ds.AUTO_HEADERS and h not in ds.CLI_HEADERS


def test_assert_force_header_accepts_match_and_rejects_drift():
    # the join is POSITIONAL; a source rename/reorder/insert must fail LOUD, not silently mis-map.
    import pytest
    assert sfm.EXPECTED_FORCE_HEADER == ("Study Adı",) + sfm.FORCE_VALUE_HEADERS
    sfm.assert_force_header(["Study Adı", *sfm.FORCE_VALUE_HEADERS])                 # exact -> ok
    sfm.assert_force_header(["Study Adı", *sfm.FORCE_VALUE_HEADERS, "notes"])        # extra col past F ignored
    with pytest.raises(ValueError):                                                  # old casual names rejected
        sfm.assert_force_header(["", "izometrik peak tork", "konsantrik peak tork",
                                 "konsantrik average power", "CoV"])
    with pytest.raises(ValueError):                                                  # a reorder rejected
        sfm.assert_force_header(["Study Adı", sfm.FORCE_VALUE_HEADERS[1], sfm.FORCE_VALUE_HEADERS[0],
                                 *sfm.FORCE_VALUE_HEADERS[2:]])


def test_norm_resolves_naming_traps_strip_case_and_turkish_ascii_fold():
    assert sfm._norm("14jun06_071_829_anonace_anonacn_53e_173_88_47r\n") == "14jun06_071_829_anonace_anonacn_53e_173_88_47r"
    # the SAME patient typed with different conventions must collapse to ONE key: case, the Turkish
    # dotless-i (str.casefold mishandles İ/ı), and ASCII-typed accents (operators type 'Anonacq' for 'Anonacq').
    assert sfm._norm("21jun26_060_829_Anonadp_Anonacq_39k_160_61_48r") == sfm._norm("21jun26_060_829_anonadp_anonacq_39k_160_61_48r")
    assert sfm._norm("ANONACG") == sfm._norm("anonacg") == sfm._norm("anonacg")
    assert sfm._norm("ANONACB") == sfm._norm("anonacb")
    assert sfm._norm("Anonabp") == sfm._norm("anonabp") and sfm._norm("Anonadw") == sfm._norm("anonadw")
    # a stray double underscore (an empty field — the real anonabb '__35e' filename typo vs the
    # datasheet '_35e') collapses too, so the zip still links to its row
    assert sfm._norm("03jul26_115_831_anonabb_Anonaco__34e_177_85_42r") == \
        sfm._norm("03jul26_115_831_anonabb_Anonaco_34e_177_85_42r")
    # but two DIFFERENT surnames must NOT merge — the full study name keeps distinct patients apart
    assert sfm._norm("21jun26_076_829_anonabp_anonadv_30k") != sfm._norm("21jun26_076_829_anonack_anonadj_28e")


def test_parse_force_rows_keeps_keyed_rows_and_strips():
    # force-sheet row order (2026-07-01): study, izo tork, izo CoV, kons tork, kons power, iso CoV
    rows = [
        ["14jun06_071_829_anonace_anonacn_53e_173_88_47r\n", "259.8", "7.1", "171.3", "102.7", "1.6"],
        ["18jun26_097_831_anonaaw_anonacp_67k_172_82_043r"],   # matched-but-blank (short row)
        ["", "1", "2", "3", "4", "5"],                           # no key -> dropped
    ]
    out = sfm.parse_force_rows(rows)
    assert len(out) == 2
    anonace = out[sfm._norm("14jun06_071_829_anonace_anonacn_53e_173_88_47r")]
    assert anonace[1] == ["259.8", "7.1", "171.3", "102.7", "1.6"]
    berb = out[sfm._norm("18jun26_097_831_anonaaw_anonacp_67k_172_82_043r")]
    assert berb[1] == ["", "", "", "", ""]          # short row padded to 5 blanks


def test_plan_writes_fill_missing_match_blank_and_orphan():
    force_by_norm = sfm.parse_force_rows([
        ["14jun06_071_829_anonace_anonacn_53e_173_88_47r\n", "259.8", "7.1", "171.3", "102.7", "1.6"],  # full (case+\n)
        ["18jun26_097_831_anonaaw_anonacp_67k_172_82_043r"],                                       # blank force
        ["99zzz_001_829_ghost_x_28e_170_70_40r", "1", "2", "3", "4", "5"],                           # orphan
    ])
    # kayit side carries a DIFFERENT casing for anonace -> the normalised match must still find it
    study_to_row = {
        "14jun06_071_829_ANONACE_anonacn_53e_173_88_47r": 2,
        "18jun26_097_831_anonaaw_anonacp_67k_172_82_043r": 4,
    }
    study_to_vals = {
        "14jun06_071_829_ANONACE_anonacn_53e_173_88_47r": {
            "İzometrik Tepe Tork (Nm)": "300",          # already filled -> must be KEPT, not clobbered
            "İzometrik CV (%)": "",
            "İzokinetik Tepe Tork Ekst (Nm)": "",
            "İzokinetik Ort. Güç Ekst (W)": "",
            "İzokinetik CV (%)": "",
        },
        "18jun26_097_831_anonaaw_anonacp_67k_172_82_043r": {h: "" for h in sfm.FORCE_VALUE_HEADERS},
    }
    writes, skipped_filled, blank_force, orphans = sfm.plan_writes(force_by_norm, study_to_row, study_to_vals)

    written = {(h, v) for _, _, h, v in writes}
    assert ("İzometrik Tepe Tork (Nm)", "259.8") not in written          # filled cell NOT clobbered
    assert ("İzometrik CV (%)", "7.1") in written                        # new blank CV cell -> written
    assert ("İzokinetik Tepe Tork Ekst (Nm)", "171.3") in written        # blank cell -> written
    assert ("İzokinetik Ort. Güç Ekst (W)", "102.7") in written
    assert ("İzokinetik CV (%)", "1.6") in written
    assert all(row == 2 for _, row, _, _ in writes)                       # matched the case-variant row
    assert len(skipped_filled) == 1                                       # the pre-filled isometric cell
    assert [s for s, _ in blank_force] == ["18jun26_097_831_anonaaw_anonacp_67k_172_82_043r"]
    assert orphans == ["99zzz_001_829_ghost_x_28e_170_70_40r"]


def test_plan_writes_skips_blank_value_cells():
    # a force row present in kayit, but some values blank -> those cells are not written (order: izo
    # tork, izo CoV, kons tork, kons power, iso CoV)
    force_by_norm = sfm.parse_force_rows([["a_1_829_x_y_28e_170_70_40r", "100", "", "50", "", "9"]])
    study_to_row = {"a_1_829_x_y_28e_170_70_40r": 5}
    study_to_vals = {"a_1_829_x_y_28e_170_70_40r": {h: "" for h in sfm.FORCE_VALUE_HEADERS}}
    writes, _, blank_force, orphans = sfm.plan_writes(force_by_norm, study_to_row, study_to_vals)
    assert {h for _, _, h, _ in writes} == {"İzometrik Tepe Tork (Nm)", "İzokinetik Tepe Tork Ekst (Nm)", "İzokinetik CV (%)"}
    assert not blank_force and not orphans

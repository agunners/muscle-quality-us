"""Demographics regex handles canonical + edge-case folder names; PHI never leaks to UI dict."""
from __future__ import annotations

from preflight_helpers.study_metadata import StudyMeta, parse_folder_demographics


def test_canonical_male() -> None:
    d = parse_folder_demographics("0000145_anonadm_anonabu_46e_171_110")
    assert d["age"] == 46
    assert d["sex"] == "M"
    assert d["height_cm"] == 171
    assert d["weight_kg"] == 110
    assert d["bmi"] == 37.6
    # PHI stashed in _phi only.
    assert d["_phi"]["surname"] == "anonadm"
    assert d["_phi"]["firstname"] == "anonabu"


def test_canonical_female_with_k_marker() -> None:
    d = parse_folder_demographics("0000246__anonacm_anonaay_44k_161_73")
    # Double underscore = empty surname; firstname is "anonacm_anonaay".
    assert d["age"] == 44
    assert d["sex"] == "F"
    assert d["height_cm"] == 161
    assert d["weight_kg"] == 73
    assert d["_phi"]["surname"] == ""
    assert d["_phi"]["firstname"] == "anonacm_anonaay"


def test_y_marker_means_unknown_sex() -> None:
    # ACL762 + DFN534 use `y` (years only, no sex encoded).
    d = parse_folder_demographics("0000028_anonadt_anonabw_26y_153_48")
    assert d["age"] == 26
    assert d["sex"] is None      # 'y' is years marker, not 'kadın'


def test_id_first_english_m_f_sex_now_parses() -> None:
    # the cohort regex must accept the m/f sex codes its _SEX_MAP + docstring already promise
    # (an English/device export). Before, an id-first m/f name fell through to {} (no demographics,
    # pseudonym) because the regex sex class omitted m/f.
    dm = parse_folder_demographics("0000114_anonadm_anonabu_45m_174_109")
    assert dm["age"] == 45 and dm["sex"] == "M" and dm["height_cm"] == 174
    df = parse_folder_demographics("0000172_anonace_anonaap_43f_163_70")
    assert df["age"] == 43 and df["sex"] == "F" and df["weight_kg"] == 70


def test_ui_dict_drops_phi() -> None:
    sm = StudyMeta(study_code="AGZ855", age=45, sex="M", height_cm=174,
                   weight_kg=109, bmi=36.0, raw_path=None,
                   _phi={"surname": "anonadm", "firstname": "anonabu"})
    ui = sm.to_ui_dict()
    assert "raw_path" not in ui
    assert "_phi" not in ui
    assert "anonadm" not in str(ui)
    assert "anonabu" not in str(ui)


# --- new schema / date-first forward-compat (2026-06-16) --------------------------------

def test_cohort_path_carries_asis_probe_keys_as_none() -> None:
    # legacy id-first cohort names never carry ASIS/probe, but the keys are present (None)
    d = parse_folder_demographics("0000145_anonadm_anonabu_46e_171_110")
    assert d["asis_patella"] is None and d["probe"] is None


def test_date_first_new_schema_is_parsed_not_dropped() -> None:
    # a date-first june-protocol name parses (instead of {} -> hash pseudonym), incl ASIS+probe
    d = parse_folder_demographics("16jun26_012_anonadx_anonabm_67k_163_70_041.5_831")
    assert d["age"] == 67 and d["sex"] == "F"
    assert d["height_cm"] == 163 and d["weight_kg"] == 70
    assert d["asis_patella"] == 41.5 and d["probe"] == 831
    assert d["_phi"]["surname"] == "anonadx" and d["_phi"]["numeric_id"] == "012"


def test_date_seq_capture_folder_still_unparsed() -> None:
    # live on-disk capture folders are <Date>_<seq> with no demographics -> {} (unchanged;
    # demographics come from the datasheet, not the folder name)
    assert parse_folder_demographics("14May26_01") == {}


def test_pseudonym_robust_when_date_first_has_no_id() -> None:
    from pathlib import Path

    from preflight_helpers.study_metadata import lookup_study
    sm = lookup_study(Path("/x/12jun06_anonaam_anonacx_25e_174_70"))   # date-first, no seq
    assert sm.study_code.startswith("AKA") and "None" not in sm.study_code
    assert sm.age == 25 and sm.sex == "M"


def test_dedup_suffix_stripped_in_app_parser() -> None:
    # a trailing " (1)" de-dup suffix must not block the cohort regex / lose the weight
    d = parse_folder_demographics("0000145_anonadm_anonabu_46e_171_110 (1)")
    assert d["height_cm"] == 171 and d["weight_kg"] == 110 and d["sex"] == "M"

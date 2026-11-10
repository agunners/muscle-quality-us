"""Tests for the v7 study-zip filename parser (scripts/preflight_helpers/study_filename.py)."""
import pytest

from preflight_helpers.study_filename import (
    StudyFilenameError,
    compute_bmi,
    normalize_sex,
    parse_study_filename,
)

# The worked example from Görüntü Alma Kılavuzu v7, EK-2.
REF = "07jun06_093_anonabr_anonaab_49e_179_82.zip"


def test_parses_reference_example():
    p = parse_study_filename(REF)
    assert p.study_adi == "07jun06_093_anonabr_anonaab_49e_179_82"
    assert p.tarih == "07jun06"
    assert p.seq == "093"
    assert p.hasta_soyadi == "anonabr"
    assert p.hasta_adi == "anonaab"
    assert p.yas == 49
    assert p.cinsiyet == "E"
    assert p.boy_cm == 179
    assert p.kilo_kg == 82
    assert p.bmi == 25.6
    assert p.warnings == []


@pytest.mark.parametrize(
    "raw,exp",
    [("e", "E"), ("E", "E"), ("m", "E"), ("M", "E"),
     ("k", "K"), ("K", "K"), ("f", "K"), ("F", "K")],
)
def test_sex_normalization(raw, exp):
    assert normalize_sex(raw) == exp


def test_sex_unknown_raises():
    with pytest.raises(StudyFilenameError):
        normalize_sex("x")


def test_bmi_matches_sheet_examples():
    assert compute_bmi(178, 74) == 23.4   # the existing example row in hasta_kayit_v7
    assert compute_bmi(176, 86) == 27.8   # the v7 worked example
    assert compute_bmi(0, 70) is None
    assert compute_bmi(170, None) is None


def test_uppercase_and_missing_extension():
    a = parse_study_filename("07jun06_093_anonabr_anonaab_49e_179_82.ZIP")
    b = parse_study_filename("07jun06_093_anonabr_anonaab_49e_179_82")
    assert a.study_adi == b.study_adi == "07jun06_093_anonabr_anonaab_49e_179_82"


def test_name_order_swap():
    p = parse_study_filename(REF, name_order="ad_soyad")
    assert p.hasta_adi == "anonabr"
    assert p.hasta_soyadi == "anonaab"


def test_too_few_tokens_raises():
    with pytest.raises(StudyFilenameError):
        parse_study_filename("07jun06_093_guner.zip")


def test_missing_sex_letter_raises():
    with pytest.raises(StudyFilenameError):
        parse_study_filename("07jun06_093_anonabr_anonaab_50_176_86.zip")


def test_implausible_age_warns_not_raises():
    p = parse_study_filename("07jun06_093_guner_abdulkadir_150e_176_86.zip")
    assert p.yas == 150
    assert any("yas" in w for w in p.warnings)


def test_three_name_tokens_warns_not_raises():
    p = parse_study_filename("10jul26_003_anonadx_anonaap_anonadg_45k_165_60.zip")
    assert p.cinsiyet == "K"
    assert p.boy_cm == 165
    assert p.kilo_kg == 60
    assert any("name token" in w for w in p.warnings)
    assert p.hasta_soyadi == "anonadx"
    assert p.hasta_adi == "anonaap anonadg"


def test_odd_date_warns():
    p = parse_study_filename("2026-04-19_093_anonabr_anonaab_49e_179_82.zip")
    assert any("tarih" in w for w in p.warnings)
    assert p.cinsiyet == "E"


# --- new schema (ASIS-patella + probe), 2026-06-16 -------------------------------------

def test_new_schema_asis_and_probe():
    p = parse_study_filename("16jun26_012_anonadx_anonabm_67k_163_70_041.5_831.zip")
    assert (p.tarih, p.seq) == ("16jun26", "012")
    assert (p.hasta_soyadi, p.hasta_adi) == ("anonadx", "anonabm")
    assert (p.yas, p.cinsiyet) == (67, "K")
    assert (p.boy_cm, p.kilo_kg) == (163, 70)        # positional — the tail doesn't shift these
    assert p.asis_patella == 41.5 and p.probe == 831
    assert p.warnings == []


def test_new_schema_floors_misplaced_decimal_in_weight():
    # real case 011: weight typed 85.5 must FLOOR to 85 (not be read as 85.5 or as the ASIS);
    # ASIS keeps its value. Without flooring, a misplaced dot corrupts the weight.
    p = parse_study_filename("16jun26_011_anonace_anonacn_50e_175_85.5_045_829.zip")
    assert p.boy_cm == 175 and p.kilo_kg == 85
    assert p.asis_patella == 45.0 and p.probe == 829
    assert any("floored" in w for w in p.warnings)   # the dot typo surfaces, not silently dropped


def test_double_underscore_tolerated():
    p = parse_study_filename("16jun26_009_anonaam_anonacx__25e_174_70_045.5_829.zip")
    assert (p.hasta_soyadi, p.hasta_adi) == ("anonaam", "anonacx")
    assert p.asis_patella == 45.5 and p.probe == 829


def test_missing_seq_is_allowed_not_warned():
    # a name straight from the date to the name (no per-date count) is valid (Akadir 2026-06-16)
    p = parse_study_filename("12jun06_anonaam_anonacx_25e_174_70.zip")
    assert p.seq == ""
    assert (p.hasta_soyadi, p.hasta_adi) == ("anonaam", "anonacx")
    assert (p.boy_cm, p.kilo_kg) == (174, 70)
    assert p.asis_patella is None and p.probe is None
    assert not any("seq" in w.lower() or "count" in w.lower() for w in p.warnings)


def test_old_schema_still_has_no_asis_probe():
    p = parse_study_filename(REF)
    assert p.asis_patella is None and p.probe is None


def test_leg_suffix_maps_to_dominant_bacak():
    # the ASIS leg suffix r/l -> Dominant Bacak (r = SAĞ, l = SOL); absent -> "" (Akadir 2026-06-17)
    assert parse_study_filename("16jun26_012_831_anonadx_anonabm_67k_163_70_041.5r").dominant_bacak == "SAĞ"
    assert parse_study_filename("16jun26_012_831_anonadx_anonabm_67k_163_70_041.5l").dominant_bacak == "SOL"
    p = parse_study_filename("16jun26_012_831_anonadx_anonabm_67k_163_70_041.5")
    assert p.dominant_bacak == "" and p.asis_patella == 41.5   # ASIS intact, no leg


def test_dedup_suffix_does_not_swallow_last_field():
    # a filesystem/Drive de-dup " (1)" suffix must not eat the weight (real on-disk case);
    # without the strip, `115 (1)` is non-numeric and the weight reads as missing
    p = parse_study_filename("16jun26_003_anonace_anonacn_30e_178_115 (1).zip")
    assert p.boy_cm == 178 and p.kilo_kg == 115
    assert not any("missing" in w for w in p.warnings)
    # the real folder (spelled-out date warns separately) still recovers the weight
    q = parse_study_filename("11june26_003_anonabf_anonaah_30e_178_115 (1)")
    assert q.boy_cm == 178 and q.kilo_kg == 115

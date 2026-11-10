"""Tests for the hasta_kayit_v7 schema: column order, helpers, row building, upsert."""
from preflight_helpers.datasheet_schema import (
    AUTO_HEADERS, CHOOSE_OPTION, CLI_HEADERS, COLUMNS, HEADERS, KEY_COLUMN, TEXT_FORMAT_HEADERS,
    array_formulas, auto_column_ranges, bmi_formula, build_row, cli_cells, col_index,
    col_letter, dropdown_values, is_unanswered, merge_fill_missing, row_to_list,
)
from preflight_helpers.study_filename import parse_study_filename

REF = "07jun06_093_anonabr_anonaab_49e_179_82.zip"


def test_schema_has_31_columns_incl_force_and_measurement_check():
    assert len(COLUMNS) == 31          # 24 base + 5 force + Force Operator + Ölçüm Kontrol
    # column order mirrors the LIVE sheet: the force join is POSITIONAL (Y–AC must never
    # shift), Force Operator is the operator-added column discovered at AD on 2026-08-12,
    # and the gate's check column sits past everything at AE
    assert HEADERS[-1] == "Ölçüm Kontrol" and "Ölçüm Kontrol" in TEXT_FORMAT_HEADERS
    assert col_index("İzometrik Tepe Tork (Nm)") == 24   # force block pinned at Y..AC
    assert col_index("İzokinetik CV (%)") == 28
    assert col_index("Force Operator") == 29             # AD — operator-maintained, never script-written
    assert col_index("Ölçüm Kontrol") == 30              # AE — the gate's column
    assert "Alt-kohort mu" in HEADERS
    assert "Artifacts (mp4/RawImages/dcm)" in HEADERS
    assert "Kontrol Sonucu" in HEADERS and "Kontrol Detayı" in HEADERS   # watcher-written
    # the 5 force-criterion columns are grouped (each CV right after its torque), text-formatted, NOT auto/cli.
    for h in ("İzometrik Tepe Tork (Nm)", "İzometrik CV (%)", "İzokinetik Tepe Tork Ekst (Nm)",
              "İzokinetik Ort. Güç Ekst (W)", "İzokinetik CV (%)"):
        assert h in HEADERS and h in TEXT_FORMAT_HEADERS
        assert h not in AUTO_HEADERS and h not in CLI_HEADERS
    # İzometrik CV is grouped immediately after its torque (index 25 = col Z), NOT appended at the tail
    assert HEADERS[col_index("İzometrik Tepe Tork (Nm)") + 1] == "İzometrik CV (%)"
    assert col_index("İzometrik CV (%)") == 25
    assert KEY_COLUMN == "Study Adı"


def test_col_letter():
    assert col_letter(0) == "A"
    assert col_letter(7) == "H"      # Boy (cm)
    assert col_letter(25) == "Z"
    assert col_letter(26) == "AA"


def test_bmi_formula_references_boy_and_kilo():
    # Boy (cm) = column H (index 7), Kilo (kg) = column I (index 8)
    assert col_letter(col_index("Boy (cm)")) == "H"
    assert col_letter(col_index("Kilo (kg)")) == "I"
    assert bmi_formula(5) == '=IFERROR(ROUND(I5/(H5/100)^2,1),"")'
    assert bmi_formula(5, sep=";") == '=IFERROR(ROUND(I5/(H5/100)^2;1);"")'  # tr_TR locale


def test_build_row_seeds_judgment_dropdowns_with_choose_default():
    parsed = parse_study_filename(REF)   # old name: no leg / ASIS / probe
    row = build_row(parsed, "anonacl", "8/8/8", row_1based=2)
    assert row["Tarih"] == "07jun06"
    assert row["Study Adı"] == "07jun06_093_anonabr_anonaab_49e_179_82"
    assert row["Hasta Adı"] == "anonaab"
    assert row["Hasta Soyadı"] == "anonabr"
    assert row["Cinsiyet"] == "E"
    assert row["Yaş"] == "49"
    assert row["Boy (cm)"] == "179"
    assert row["Kilo (kg)"] == "82"
    assert row["Operator"] == "anonacl"
    assert row["Artifacts (mp4/RawImages/dcm)"] == "8/8/8"
    assert row["BMI"] == '=IFERROR(ROUND(I2/(H2/100)^2,1),"")'
    assert row["Sıra"] == "1"
    # operator-judgment dropdowns are SEEDED with the 'Seçiniz' default — the operator must pick
    # (leg / ASIS / probe are no longer here — they're name-derived formula columns now).
    assert row["Yaralanma/Cerrahi (alt ekstremite, son 6 ay)"] == CHOOSE_OPTION
    assert row["Alt-kohort mu"] == CHOOSE_OPTION
    assert row["Software Version"] == CHOOSE_OPTION
    assert row["Presets Ayarlı mı"] == CHOOSE_OPTION
    assert row["Dosyalar Tam mı"] == CHOOSE_OPTION
    # free text stays blank; watcher field blank
    assert row["Notlar"] == ""
    assert row["Kontrol Sonucu"] == ""
    # the force-criterion columns are operator/physio-entered later — blank on a fresh row
    assert row["İzometrik Tepe Tork (Nm)"] == "" and row["İzokinetik CV (%)"] == ""


def test_merge_fill_missing_never_clobbers_operator_entries():
    parsed = parse_study_filename(REF)
    new = build_row(parsed, "anonacl", "8/8/8", row_1based=2)
    existing = {h: "" for h in HEADERS}
    existing["Study Adı"] = "07jun06_093_anonabr_anonaab_49e_179_82"
    existing["Notlar"] = "operator note"               # operator pre-filled free text
    existing["Software Version"] = "1.26.2.1/03jun26"   # operator picked
    merged = merge_fill_missing(existing, new)
    assert merged["Notlar"] == "operator note"                  # kept
    assert merged["Software Version"] == "1.26.2.1/03jun26"     # kept, NOT overwritten with Seçiniz
    assert merged["Artifacts (mp4/RawImages/dcm)"] == "8/8/8"   # filled from new


def test_row_to_list_is_ordered():
    parsed = parse_study_filename(REF)
    row = build_row(parsed, "anonacl", "8/8/8", row_1based=2)
    lst = row_to_list(row)
    assert len(lst) == 31
    assert lst[col_index("Operator")] == "anonacl"


def test_auto_and_cli_partition_is_clean():
    # the formula columns and the CLI-written columns never overlap
    auto, cli, text = set(AUTO_HEADERS), set(CLI_HEADERS), set(TEXT_FORMAT_HEADERS)
    assert auto.isdisjoint(cli)                 # formulas vs CLI writes never collide
    assert auto.isdisjoint(text)                # never text-format a formula column
    assert "Study Adı" in cli and "Operator" in cli and "Artifacts (mp4/RawImages/dcm)" in cli
    assert "Tarih" in auto and "Yaş" in auto and "BMI" in auto
    # the CLI seeds the operator-judgment dropdowns; leg/ASIS/probe are now FORMULA columns (auto)
    assert {"Alt-kohort mu", "Software Version"} <= cli
    assert {"Dominant Bacak", "Probe No", "ASIS-Patella Sol (cm)", "ASIS-Patella Sağ (cm)"} <= auto
    assert cli.isdisjoint({"Kontrol Sonucu", "Kontrol Detayı", "Notlar"})
    assert "Kontrol Sonucu" not in auto


def test_auto_column_ranges_skip_the_editable_study_id():
    # Study Adı (col C, index 2) + the judgment dropdowns sit between the auto columns and must
    # stay editable, so the protected ranges are (Sıra,Tarih), (Hasta Adı … ASIS Sağ), (Probe No).
    ranges = auto_column_ranges()
    assert ranges == [(0, 2), (3, 13), (18, 19)]
    assert col_index("Study Adı") == 2
    assert not any(start <= 2 < end for start, end in ranges)   # Study Adı never protected


def test_array_formulas_parse_study_id():
    f = array_formulas()
    assert set(f) == set(AUTO_HEADERS)
    assert all(v.startswith("=ARRAYFORMULA(") for v in f.values())
    assert "$C$2:$C" in f["Tarih"] and "REGEXEXTRACT" in f["Tarih"]
    assert "ROUND(" in f["BMI"]
    # weight is anchored on the <age><sex> token (NOT end-anchored), so the new ASIS/probe
    # tail can't shift the probe into the weight cell
    assert r"_\d+[A-Za-z]_\d+_(\d+)" in f["Kilo (kg)"]
    # locale separator
    assert ';"")' in array_formulas(sep=";")["Tarih"]


def _regexextract_pattern(formula: str) -> str:
    """Pull the REGEXEXTRACT pattern string out of the formula, so we can run the SAME regex the
    live sheet runs (against a study name). The extract's first argument is no longer a bare
    range but the underscore-folded name (REGEXREPLACE(range,"_+","_") — the 2026-08-24 fix), so
    the pattern is the LAST quoted argument before a closing paren, not the second token."""
    import re
    m = re.search(r'REGEXEXTRACT\(.*[,;]"([^"]+)"\)', formula)
    assert m, f"no REGEXEXTRACT pattern in {formula!r}"
    return m.group(1)


def _fold(formula: str, name: str) -> str:
    """Simulate the formula's input exactly: the live formulas collapse underscore runs before
    extracting (their REGEXREPLACE wrapper), so the simulator must feed them the folded name."""
    import re
    return re.sub(r"_+", "_", name) if "REGEXREPLACE" in formula else name


def test_array_formulas_demographics_correct_on_new_schema():
    # The in-sheet formula fills the demographics, not the Python parser. Run the EXACT regex
    # it uses against a NEW-schema name: the appended ASIS/probe must NOT shift the weight into
    # the probe slot (this end-anchoring bug shipped in e3baf3c and is what this guards).
    import re
    f = array_formulas()

    def cap(header: str, name: str):
        m = re.search(_regexextract_pattern(f[header]), _fold(f[header], name))
        return m.group(1) if m else None

    new = "16jun26_012_anonadx_anonabm_67k_163_70_041.5_831"
    assert cap("Yaş", new) == "67"
    assert cap("Boy (cm)", new) == "163"
    assert cap("Kilo (kg)", new) == "70"          # the weight — NOT 831 (the probe)
    old = "07jun06_093_anonabr_anonaab_49e_179_82"
    assert (cap("Yaş", old), cap("Boy (cm)", old), cap("Kilo (kg)", old)) == ("49", "179", "82")


def test_array_formulas_correct_on_canonical_format_incl_names():
    # CANONICAL format (Akadir 2026-06-17): probe moved to the FRONT, ASIS to the tail. The
    # NAME columns must still resolve (re-anchored backward off <age><sex>) — the probe must NOT
    # land in the surname cell — and the demographics must read the right slots.
    import re
    f = array_formulas()
    name = "16jun26_012_831_anonadx_anonabm_67k_163_70_041.5"

    def cap(header):
        m = re.search(_regexextract_pattern(f[header]), _fold(f[header], name))
        return m.group(1) if m else None

    assert cap("Hasta Soyadı") == "anonadx"      # NOT 831 (the probe sits right before the name)
    assert cap("Hasta Adı") == "anonabm"
    assert cap("Yaş") == "67"
    assert cap("Boy (cm)") == "163"
    assert cap("Kilo (kg)") == "70"


def test_probe_formula_validated_whitelist():
    # the Probe No formula extracts ONLY a real probe value (front or tail), never a stray number
    import re
    formula = array_formulas()["Probe No"]
    pat = _regexextract_pattern(formula)

    def probe(name):
        m = re.search(pat, _fold(formula, name))
        return m.group(1) if m else None

    assert probe("16jun26_012_831_anonadx_anonabm_67k_163_70_041.5") == "831"
    assert probe("16jun26_009_anonaam_anonacx__25e_174_70_045.5_829") == "829"   # at tail, '__' typo folded
    assert probe("16jun26_012_999_anonadx_anonabm_67k_163_70_041.5") is None  # 999 not a probe
    assert probe("07jun06_093_anonabr_anonaab_49e_179_82") is None         # no probe


def test_probe_is_formula_filled_no_dropdown():
    probe = next(c for c in COLUMNS if c.header == "Probe No")
    assert probe.options == ()                                    # dropdown removed (Akadir 2026-06-22)
    assert "Probe No" in AUTO_HEADERS and "Probe No" in array_formulas()


def test_cli_cells_excludes_id_derived_columns():
    parsed = parse_study_filename(REF)
    built = build_row(parsed, "anonacl", "8/8/8", row_1based=2)
    cells = cli_cells(built)
    assert set(cells) == set(CLI_HEADERS)
    assert "Tarih" not in cells and "Yaş" not in cells and "BMI" not in cells
    assert cells["Operator"] == "anonacl"
    assert cells["Artifacts (mp4/RawImages/dcm)"] == "8/8/8"
    assert cells["Study Adı"] == "07jun06_093_anonabr_anonaab_49e_179_82"


# --- name-encoded fields are now in-sheet FORMULA columns (Akadir 2026-06-22) ----------

def test_leg_asis_formulas_extract_from_name():
    import re
    f = array_formulas()

    def first_regex(formula, name):                     # the formula's leg REGEXEXTRACT
        m = re.search(_regexextract_pattern(formula), _fold(formula, name))
        return m.group(1) if m else None

    def asis_value(formula, name):                      # the ASIS number inside VALUE(REGEXEXTRACT())
        pat = re.search(r'VALUE\(REGEXEXTRACT\(.*[,;]"([^"]+)"\)', formula).group(1)
        m = re.search(pat, _fold(formula, name))
        return m.group(1) if m else None

    canon_r = "16jun26_012_831_anonadx_anonabm_67k_163_70_041.5r"      # ASIS + right leg at the tail
    assert first_regex(f["Dominant Bacak"], canon_r) == "r"         # -> Dominant Bacak SAĞ (mapped live)
    assert first_regex(f["Dominant Bacak"], canon_r[:-1] + "l") == "l"
    # the ASIS number is extracted, then LEG-ROUTED: Sol fills only on L, Sağ only on R (verified live)
    assert asis_value(f["ASIS-Patella Sol (cm)"], canon_r) == "041.5"
    assert '="L"' in f["ASIS-Patella Sol (cm)"]                     # Sol routes on a left leg
    assert '="R"' in f["ASIS-Patella Sağ (cm)"]                     # Sağ routes on a right leg
    old = "07jun06_093_anonabr_anonaab_49e_179_82"                 # old name: no ASIS / leg
    assert first_regex(f["Dominant Bacak"], old) is None
    assert asis_value(f["ASIS-Patella Sol (cm)"], old) is None


def test_name_formulas_survive_double_underscore_study_adi():
    """The 2026-08-24 incident: a Study Adı pasted byte-exact from a zip carrying a '__' typo
    blanked the NAME cells while the demographics still rendered — the name patterns required
    exactly one underscore between tokens. The formulas now fold delimiter runs before parsing
    (the sheet-side mirror of `_norm`), so every column extracts. Name tokens here are synthetic."""
    import re
    f = array_formulas()
    name = "23aug26_307_830_anonadk_anonaae__19k_160_59_49r"

    def cap(header):
        m = re.search(_regexextract_pattern(f[header]), _fold(f[header], name))
        return m.group(1) if m else None

    assert cap("Hasta Soyadı") == "anonadk"
    assert cap("Hasta Adı") == "anonaae"
    assert (cap("Yaş"), cap("Boy (cm)"), cap("Kilo (kg)")) == ("19", "160", "59")
    assert cap("Probe No") == "830"


def test_leg_asis_probe_are_formula_columns_not_cli_or_text():
    for h in ("Dominant Bacak", "ASIS-Patella Sol (cm)", "ASIS-Patella Sağ (cm)", "Probe No"):
        assert h in AUTO_HEADERS                                    # in-sheet formula
        assert h not in CLI_HEADERS                                 # the CLI no longer writes them
        assert h not in TEXT_FORMAT_HEADERS                         # text format would freeze the formula
        assert h in array_formulas()
    assert set(CLI_HEADERS).isdisjoint(AUTO_HEADERS)


# --- 'Seçiniz' default + revert: operator dropdowns (Akadir 2026-06-17) ----------------

def test_operator_judgment_dropdown_gets_choose_row_and_is_nonstrict():
    # leg/probe are formula now; a still-manual judgment dropdown keeps the Seçiniz default + revert
    col = next(c for c in COLUMNS if c.header == "Alt-kohort mu")
    values, strict = dropdown_values(col, col.options)
    assert values[0] == CHOOSE_OPTION                   # 'Seçiniz' first: default + revert target
    assert "Evet" in values and "Hayır" in values
    assert strict is False                              # never reject -> can always be re-picked/cleared


def test_cinsiyet_formula_column_keeps_strict_list_no_choose():
    # Cinsiyet is formula-filled + protected; the operator never edits it, so no Seçiniz row.
    cins = next(c for c in COLUMNS if c.header == "Cinsiyet")
    values, strict = dropdown_values(cins, cins.options)
    assert values == ["E", "K"] and strict is True


def test_choose_option_is_treated_as_unanswered():
    # every reader must count 'Seçiniz' (and blank) as not-answered, so it can't leak downstream
    assert is_unanswered(CHOOSE_OPTION) and is_unanswered("") and is_unanswered("  ")
    assert not is_unanswered("SAĞ") and not is_unanswered("Evet")

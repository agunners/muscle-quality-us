"""The shared study-name grammar (study_name_grammar.parse) — the single source of truth for
the date-first schema. Covers every generation that coexists: the OLD style (no probe/ASIS),
the first new-schema cut (probe + ASIS at the TAIL), the CANONICAL format (probe up front next
to the count, ASIS at the tail), plus the degrade/warn cases. The intake parser, the app
parser, and the in-sheet formula all build on this; their own tests assert the adapter layer."""
from preflight_helpers import study_name_grammar as g
from preflight_helpers.study_name_grammar import PROBE_NUMBERS


def test_canonical_format_probe_front_asis_tail():
    p = g.parse("16jun26_012_831_anonadx_anonabm_67k_163_70_041.5")
    assert p.matched
    assert (p.date, p.count, p.probe) == ("16jun26", "012", 831)
    assert p.name_tokens == ["anonadx", "anonabm"]
    assert (p.age, p.sex) == (67, "k")
    assert (p.height, p.weight, p.asis) == (163, 70, 41.5)
    assert p.warnings == []


def test_first_cut_probe_and_asis_at_tail_parses_identically():
    # the earlier new-schema layout put probe + ASIS at the END; it must parse identically,
    # because the probe is found BY VALUE wherever it sits — the reorder is transparent
    p = g.parse("16jun26_012_anonadx_anonabm_67k_163_70_041.5_831")
    assert (p.count, p.probe) == ("012", 831)
    assert (p.height, p.weight, p.asis) == (163, 70, 41.5)


def test_old_style_no_probe_no_asis():
    p = g.parse("10jun26_001_anonaam_anonacu_25e_174_70")
    assert (p.count, p.probe, p.asis) == ("001", None, None)
    assert (p.height, p.weight) == (174, 70)


def test_count_is_optional():
    p = g.parse("12jun06_anonaam_anonacx_25e_174_70")                   # old, no count
    assert p.count is None and p.name_tokens == ["anonaam", "anonacx"]
    q = g.parse("16jun26_831_anonadx_anonabm_67k_163_70_041.5")    # new, count forgotten
    assert q.count is None and q.probe == 831 and q.asis == 41.5


def test_probe_identified_by_value_not_position():
    for n in PROBE_NUMBERS:
        front = g.parse(f"16jun26_012_{n}_anonace_anonacn_50e_175_85_045")
        tail = g.parse(f"16jun26_012_anonace_anonacn_50e_175_85_045_{n}")
        assert front.probe == n and tail.probe == n
        assert front.weight == tail.weight == 85          # the probe is never read as a body field


def test_misplaced_weight_decimal_floors_and_warns():
    p = g.parse("16jun26_011_829_anonace_anonacn_50e_175_85.5_045")
    assert p.weight == 85 and p.asis == 45.0
    assert any("floored" in w for w in p.warnings)


def test_double_underscore_and_unicode_name():
    p = g.parse("16jun26_009_829_anonaam_anonacx__25e_174_70_045.5")
    assert p.name_tokens == ["anonaam", "anonacx"] and p.asis == 45.5


def test_malformed_extra_front_numeric_is_flagged():
    # ASIS placed BEFORE the name (the real 15-june mistake): the extra front numeric is flagged
    # so the study gets renamed rather than silently mis-parsed
    p = g.parse("15jun26_005_050_831_anonadt_anonaax_30e_190_88")
    assert p.probe == 831 and p.count == "005"
    assert any("rename" in w.lower() or "misplaced" in w.lower() for w in p.warnings)


def test_single_name_token():
    p = g.parse("16jun26_012_830_solo_67k_163_70_041.5")
    assert p.name_tokens == ["solo"] and p.probe == 830


def test_imaged_leg_suffix_on_asis():
    # the imaged leg is glued to the ASIS value with no underscore (Akadir 2026-06-17)
    assert g.parse("16jun26_012_831_anonadx_anonabm_67k_163_70_041.5r").leg == "r"
    assert g.parse("16jun26_012_831_anonadx_anonabm_67k_163_70_041.5l").leg == "l"
    assert g.parse("16jun26_012_831_anonadx_anonabm_67k_163_70_041.5").leg is None
    # the leg letter doesn't disturb ASIS / weight / probe (r/l aren't sex letters either)
    p = g.parse("16jun26_009_829_anonaam_anonacx_25e_174_70_045r")
    assert (p.leg, p.asis, p.weight, p.probe) == ("r", 45.0, 70, 829)


def test_malformed_leg_suffix_warns_not_corrupts():
    # a botched leg suffix is IGNORED (no wrong value lands) but WARNED so the operator fixes it
    p = g.parse("16jun26_012_831_anonadx_anonabm_67k_163_70_041.5rl")   # double letter -> ASIS lost
    assert p.asis is None and p.leg is None
    assert any("not a number or number+leg" in w for w in p.warnings)
    q = g.parse("16jun26_012_831_anonadx_anonabm_67k_163_70_041.5_r")   # leg split into its own token
    assert q.asis == 41.5 and q.leg is None                           # ASIS kept; stray 'r' warned
    assert any("not a number or number+leg" in w for w in q.warnings)


def test_trailing_dedup_suffix_stripped_by_the_grammar_itself():
    # a duplicate upload `… (1)` must parse like the original — the grammar strips it now, so a
    # future caller can't forget to (and the bare grammar matches the viewer's parser)
    p = g.parse("16jun26_011_829_anonace_anonacn_50e_175_85_045 (1)")
    assert p.weight == 85 and p.asis == 45.0 and p.probe == 829


def test_no_anchor_is_unmatched_not_a_crash():
    assert g.parse("14May26_01").matched is False      # a <Date>_<seq> capture folder
    assert g.parse("").matched is False


def test_sex_letter_is_raw_lowercase_y_tolerated():
    assert g.parse("16jun26_012_831_a_b_67k_163_70").sex == "k"
    assert g.parse("16jun26_012_831_a_b_50e_175_85").sex == "e"
    assert g.parse("16jun26_001_anonaam_hi_29y_155_52").sex == "y"   # grammar tolerates; adapters decide

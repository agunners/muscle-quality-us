"""The measurement gate — plausibility rules, the flag→kill ledger, and their wiring.

The three REAL cases the gate exists for (force-ladder prereg Amendment 2 + the 2026-08-12 sweep):
  sheet row 87 · NR0D53363E · iso 9.1 Nm, CV 0.0, isok 81.3  -> HARD (kgf·m-as-Nm + CV-zero)
  sheet row 92 · NR245F7FC5 · iso 8.7 Nm, CV 0.0, isok 84.5  -> HARD (same signature)
  sheet row 24 · ratio 0.64, CV 4.6                          -> SOFT (flagged, stays in analysis)
Everything else in the 125 read clean — the golden acceptance run reproduces that end-to-end;
these tests pin the pure logic.
"""
import ast
import json
from pathlib import Path

import pytest

from preflight_helpers import flag_kill_ledger as fkl
from preflight_helpers import measurement_gate as mg
from preflight_helpers import torque_plausibility as tp

REPO = Path(__file__).resolve().parents[2]


def vals(iso="", iso_cv="", isok="", power="", isok_cv="", weight=""):
    return {"izo_torque_nm": iso, "izo_cv_pct": iso_cv, "isok_torque_ext_nm": isok,
            "isok_mean_power_ext_w": power, "isok_cv_pct": isok_cv, "weight_kg": weight}


def codes(findings):
    return sorted(f.code for f in findings)


# ---------------------------------------------------------------- torque_plausibility
def test_real_suspects_fire_kgf_signature_and_cv_zero():
    for iso, isok in (("9.1", "81.3"), ("8.7", "84.5")):
        f = tp.classify(vals(iso=iso, iso_cv="0.0", isok=isok, isok_cv="8.2"))
        assert codes(f) == ["cv_zero", "kgf_m_as_nm"]
        assert all(x.tier == "hard" for x in f)
        # the kgf signature impugns the ISOMETRIC pair, not the isokinetic side
        kgf = next(x for x in f if x.code == "kgf_m_as_nm")
        assert set(kgf.fields) == {"izo_torque_nm", "izo_cv_pct"}


def test_row24_borderline_is_soft_only_and_stays():
    f = tp.classify(vals(iso="80", iso_cv="4.6", isok="125", isok_cv="7.1"))   # ratio 0.64
    assert codes(f) == ["low_ratio"] and f[0].tier == "soft"


def test_clean_median_row_and_blank_row_produce_nothing():
    assert tp.classify(vals(iso="160", iso_cv="5.2", isok="125", isok_cv="9.0")) == []  # ratio 1.28
    assert tp.classify(vals()) == []
    # missing dynamometer session = fact about people; iso alone with a sane CV is clean too
    assert tp.classify(vals(iso="182", iso_cv="4.8")) == []


def test_impossible_ratio_without_kgf_match_holds_all_five():
    f = tp.classify(vals(iso="20", iso_cv="5.0", isok="100", isok_cv="8.0"))   # 20x9.81 far from 100
    assert codes(f) == ["impossible_ratio"]
    assert set(f[0].fields) == set(tp.FORCE_FIELDS)


def test_ratio_boundaries_are_exclusive():
    assert codes(tp.classify(vals(iso="29.9", isok="100"))) == ["impossible_ratio"]  # just under 0.30
    assert codes(tp.classify(vals(iso="30.0", isok="100.0"))) == ["low_ratio"]       # 0.30 exactly -> soft
    assert tp.classify(vals(iso="70.0", isok="100.0")) == []                         # 0.70 exactly -> clean


def test_cv_zero_needs_a_torque_and_is_per_mode():
    assert tp.classify(vals(iso_cv="0.0")) == []                       # CV alone, no torque
    f = tp.classify(vals(isok="150", power="90", isok_cv="0"))
    assert codes(f) == ["cv_zero"]
    assert set(f[0].fields) == {"isok_torque_ext_nm", "isok_mean_power_ext_w", "isok_cv_pct"}


def test_cv_above_15_is_a_hard_hold_on_the_isometric_pair():
    """Akadir's reliability rule (2026-08-13): isometric CV > 15% -> the session is too unstable
    to serve. The two real cases; the 15.0 boundary stays; isokinetic CV is deliberately exempt."""
    for iso, cv in (("138.8", "15.1"), ("65.0", "19.2")):        # AKA035 / AKA108 (the CV>15 pair) as entered
        f = tp.classify(vals(iso=iso, iso_cv=cv))
        assert codes(f) == ["cv_unreliable"] and f[0].tier == "hard"
        assert set(f[0].fields) == {"izo_torque_nm", "izo_cv_pct"}
    assert tp.classify(vals(iso="140", iso_cv="15.0")) == []      # boundary: 15.0 itself stays
    # isokinetic CV runs hot legitimately (a real row reads 26.8) and is never the served criterion
    assert tp.classify(vals(iso="160", iso_cv="5.0", isok="125", isok_cv="26.8")) == []
    assert tp.classify(vals(iso_cv="19.0")) == []                 # CV alone, no torque to hold


def test_change_watch_diffs_only_in_place_edits():
    """The change watch surfaces edits and deletions of EXISTING values; fills (blank->value)
    and brand-new rows are routine data entry, not 'sneaked inputs'."""
    prev = {"NRA": {"izo_torque_nm": "100", "izo_cv_pct": "5"},
            "NRB": {"izo_torque_nm": ""}}
    cur = {"NRA": {"izo_torque_nm": "120", "izo_cv_pct": "5"},   # in-place edit -> surfaces
           "NRB": {"izo_torque_nm": "80"},                        # fill -> silent
           "NRC": {"izo_torque_nm": "50"}}                        # new row -> silent
    assert mg.diff_snapshots(prev, cur) == [("NRA", "izo_torque_nm", "100", "120")]
    assert mg.diff_snapshots({"NRA": {"izo_torque_nm": "100"}},
                             {"NRA": {"izo_torque_nm": ""}}) == \
        [("NRA", "izo_torque_nm", "100", "")]                     # deletion -> surfaces


def test_nm_per_kg_and_nonpositive_and_unparseable_and_turkish_comma():
    f = tp.classify(vals(iso="500", iso_cv="5", weight="70"))          # 7.14 Nm/kg
    assert codes(f) == ["impossible_nm_per_kg"]
    f = tp.classify(vals(iso="0", iso_cv="5"))
    assert codes(f) == ["nonpositive_torque"]
    f = tp.classify(vals(iso="26.8 / 3"))
    assert codes(f) == ["unparseable"] and f[0].tier == "soft"
    assert tp.parse_num("26,8") == pytest.approx(26.8)
    # a genuinely weak subject (the cohort floor is 8 Nm) with no isokinetic pair is NOT flagged
    assert tp.classify(vals(iso="8", iso_cv="6.1", weight="55")) == []


def test_every_code_has_an_operator_message():
    fired = set()
    for v in (vals(iso="9.1", iso_cv="0.0", isok="81.3"), vals(iso="20", isok="100"),
              vals(iso="80", isok="125"), vals(iso="500", weight="70"),
              vals(iso="-1"), vals(iso="x")):
        fired |= {f.code for f in tp.classify(v)}
    assert fired <= set(tp.OPERATOR_TR)


# ---------------------------------------------------------------- flag_kill_ledger
FOUNDING = ('{"key_sha8": "1CC51295", "sheet_row_at_kill": 96, "reason": "broken export", '
            '"decided_by": "Akadir", "date": "2026-08-11"}\n')


@pytest.fixture()
def ledger(tmp_path, monkeypatch):
    p = tmp_path / "kill_list.jsonl"
    p.write_text(FOUNDING, encoding="utf-8")
    monkeypatch.setattr(fkl, "LEDGER", p)
    return p


def test_founding_kill_line_reads_as_study_kill(ledger):
    assert fkl.killed_name_hashes() == {"1CC51295"}
    e = fkl.entries()[0]
    assert e["scope"] == "study" and e["status"] == "killed"


def test_flag_then_dedup_then_clear_then_value_change_reflags(ledger):
    v = vals(iso="9.1", iso_cv="0.0", isok="81.3")
    plan = dict(study_uid="NRTEST01", tier="hard", fields=["izo_torque_nm", "izo_cv_pct"],
                rules=["kgf_m_as_nm", "cv_zero"], values=v, evidence="e", sheet_row=87)
    e1 = fkl.plan_flag(**plan)
    assert e1 is not None
    fkl.append(e1)
    assert fkl.plan_flag(**plan) is None                       # same values -> already recorded
    assert "NRTEST01" in fkl.open_measurement_holds()          # hard flag -> HELD
    # human clears exactly these values -> hold released, and the gate does not re-nag
    fkl.append({**e1, "status": "cleared", "decided_by": "Akadir"})
    assert "NRTEST01" not in fkl.open_measurement_holds()
    assert fkl.plan_flag(**plan) is None
    # the cell is later edited -> a DIFFERENT value re-opens the thread
    v2 = dict(v, izo_torque_nm="91.0")
    assert fkl.plan_flag(**{**plan, "values": v2}) is not None


def test_any_open_flag_makes_the_row_no_force_equivalent(ledger):
    """Akadir's rule: a flagged row serves NO force — a SOFT flag holds all five criterion cells
    exactly like a hard one; tier is audit priority, never hold policy."""
    e = fkl.plan_flag(study_uid="NRTEST02", tier="soft", fields=["izo_torque_nm", "isok_torque_ext_nm"],
                      rules=["low_ratio"], values=vals(iso="80", isok="125"), evidence="e", sheet_row=24)
    fkl.append(e)
    assert set(fkl.open_measurement_holds()["NRTEST02"]) == set(tp.FORCE_FIELDS)
    assert [x["study_uid"] for x in fkl.open_flags()] == ["NRTEST02"]


def test_kill_holds_its_fields_forever_and_releases_the_rest(ledger):
    """The verdict flow on a flagged row: while the flag is OPEN the whole row is no-force;
    a KILL of the bad cells closes the thread — its fields stay dead, the others return
    (the human has ruled; consent for the rest is implicit in the ruling). A bare kill with no
    prior flag holds exactly its fields."""
    flag = fkl.plan_flag(study_uid="NRTEST03", tier="hard", fields=["izo_torque_nm", "izo_cv_pct"],
                         rules=["kgf_m_as_nm"], values=vals(iso="9.1", isok="81.3"), evidence="e")
    fkl.append(flag)
    assert set(fkl.open_measurement_holds()["NRTEST03"]) == set(tp.FORCE_FIELDS)   # pending -> all five
    fkl.append({**flag, "status": "killed", "decided_by": "Akadir"})
    assert set(fkl.open_measurement_holds()["NRTEST03"]) == {"izo_torque_nm", "izo_cv_pct"}
    fkl.append({"scope": "measurement", "status": "killed", "study_uid": "NRTEST04",
                "fields": ["izo_torque_nm"], "values": {}, "decided_by": "Akadir"})
    assert set(fkl.open_measurement_holds()["NRTEST04"]) == {"izo_torque_nm"}
    fkl.append({"scope": "feature", "status": "killed", "study_uid": "NR1C66A990",
                "fields": ["int_cv_within"], "decided_by": "Akadir"})
    assert fkl.feature_kills() == {"NR1C66A990": {"int_cv_within"}}


# ---------------------------------------------------------------- measurement_gate
def test_gate_groups_one_entry_per_tier_and_renders_cells(ledger):
    states = {
        "NRA": (87, vals(iso="9.1", iso_cv="0.0", isok="81.3", isok_cv="8.2")),
        "NRB": (24, vals(iso="80", iso_cv="4.6", isok="125", isok_cv="7.1")),
        "NRC": (30, vals(iso="160", iso_cv="5.2", isok="125", isok_cv="9.0")),
        "NRD": (31, vals()),                                    # no session -> not even evaluated
    }
    ev = mg.evaluate(states)
    assert sorted(sf.study_uid for sf in ev) == ["NRA", "NRB", "NRC"]
    planned = mg.plan_entries(ev)
    assert [(e["study_uid"], e["tier"]) for e in planned] == [("NRA", "hard"), ("NRB", "soft")]
    assert set(planned[0]["rules"]) == {"kgf_m_as_nm", "cv_zero"}
    assert mg.record(planned) == 2
    assert mg.plan_entries(ev) == []                            # idempotent across passes
    # operator column: hard flag warns, clean-with-no-history stays untouched
    assert mg.desired_cell("NRA").startswith("⚠️ ÖLÇÜM:") and "kgf·m" in mg.desired_cell("NRA")
    assert mg.desired_cell("NRC") is None
    fkl.append({"scope": "measurement", "status": "killed", "study_uid": "NRA",
                "fields": planned[0]["fields"], "values": planned[0]["values"],
                "rules": planned[0]["rules"], "decided_by": "Akadir"})
    assert mg.desired_cell("NRA").startswith("❌")
    fkl.append({"scope": "measurement", "status": "cleared", "study_uid": "NRB",
                "fields": planned[1]["fields"], "values": planned[1]["values"],
                "rules": planned[1]["rules"], "decided_by": "Akadir"})
    assert mg.desired_cell("NRB").startswith("✅")


def test_receipt_roundtrip(ledger):
    e = fkl.plan_flag(study_uid="NRX", tier="hard", fields=["izo_torque_nm", "izo_cv_pct"],
                      rules=["kgf_m_as_nm"], values=vals(iso="9.1", isok="81.3"), evidence="e")
    fkl.append(e)
    holds = fkl.open_measurement_holds()
    cell = mg.receipt("NRX", holds)
    assert cell.startswith("HELD ")
    # an open flag holds the whole criterion row — the receipt names all five
    assert mg.parse_held_fields(cell) == sorted(tp.FORCE_FIELDS)
    assert mg.parse_held_fields("") == [] and mg.parse_held_fields("SOFT low_ratio") == []
    assert mg.receipt("NR_UNFLAGGED", holds) == ""


# ---------------------------------------------------------------- drift guards vs the real repo
def test_texture_withhold_constant_matches_ledger_feature_kills():
    """The freeze reads texture withholds from the LEDGER (migrated 2026-08-13): the
    hand-maintained TEXTURE_WITHHOLD constant must stay retired (no silent regression to a code
    list a verdict tool can't reach), the freeze source must call the ledger reader, and the
    founding withhold (AKA017) must still be enforced by the record itself."""
    src = (REPO / "scripts" / "derive_new_round_cohort.py").read_text(encoding="utf-8")
    assigns = [n for n in ast.walk(ast.parse(src))
               if isinstance(n, ast.Assign)
               and any(getattr(t, "id", "") == "TEXTURE_WITHHOLD" for t in n.targets)]
    assert not assigns, "TEXTURE_WITHHOLD constant is back — the freeze must read the ledger"
    assert "feature_kills()" in src and "feature_clears()" in src
    ledger_side = {uid for uid, feats in fkl.feature_kills().items() if "int_cv_within" in feats}
    assert "NR1C66A990" in ledger_side          # AKA017, the founding withhold, still on record


def test_texture_clear_binds_to_its_value_snapshot(monkeypatch):
    """A feature CLEAR un-withholds texture at the freeze ONLY while the per-view thicknesses
    still match the values it was decided on — a re-measured study re-enters the gate. Stale
    clears are reported, never silently honored."""
    import derive_new_round_cohort as dc

    def fake_rows(femur, center):
        vals = {"femur": {"full_max_thickness_mm": femur, "qmt_mm": 40.0, "int_cv_within": None,
                          "qmt_provenance": "standard_qmt"},
                "rf": {"full_max_thickness_mm": center, "int_cv_within": 0.7},
                "sweep": {"full_max_thickness_mm": femur}}
        return [{"study": "teststudy_dir", "vals": vals}]

    uid = dc._study_code("teststudy_dir")
    monkeypatch.setattr(dc.fkl, "killed_study_uids", lambda: set())
    monkeypatch.setattr(dc.fkl, "feature_kills", lambda: {})

    # gate fails (20 vs 25 mm), clear snapshot MATCHES -> texture admitted, listed as applied
    clear = {uid: {"int_cv_within": {"values": {"max_femur_mm": 25.0, "max_center_mm": 20.0,
                                                "max_sweep_mm": 25.0}}}}
    monkeypatch.setattr(dc.fkl, "feature_clears", lambda: clear)
    rows, prov = dc.assemble_cohort(fake_rows(25.0, 20.0))
    assert rows[0]["int_cv_within"] == 0.7
    assert prov["texture_clears_applied"] == [uid] and prov["texture_clears_stale"] == []

    # same clear against MOVED values -> stale: texture stays withheld, stale is reported
    rows, prov = dc.assemble_cohort(fake_rows(27.0, 20.0))
    assert rows[0]["int_cv_within"] is None
    assert prov["texture_clears_stale"] == [uid] and prov["texture_clears_applied"] == []

    # a RUN-BOUND clear serves the RELEASED value (the operator's refined number from the
    # accepted run), never the auto texture — the refine-surface contract (2026-08-14)
    run_clear = {uid: {"int_cv_within": {"values": {"max_femur_mm": 25.0, "max_center_mm": 20.0,
                                                    "max_sweep_mm": 25.0},
                                         "released_value": 0.47,
                                         "source_run_id": "AKAX_123_abc"}}}
    monkeypatch.setattr(dc.fkl, "feature_clears", lambda: run_clear)
    rows, prov = dc.assemble_cohort(fake_rows(25.0, 20.0))
    assert rows[0]["int_cv_within"] == 0.47             # the run's number, not the auto 0.7
    assert prov["per_study"][0]["texture_released_from_run"] == "AKAX_123_abc"


def test_freeze_pools_contributed_frames(monkeypatch, tmp_path):
    """The 5+xth frame (spec §4b): contributed frames join the served median with honest weight
    on both sides; the auto median anchors when the landmark lacks per-frame values; a run-bound
    release outranks int_cv pooling; provenance names the contributing runs."""
    import derive_new_round_cohort as dc

    def rows_with(top_frames=None, rf_center_max=25.2):
        femur = {"full_max_thickness_mm": 25.0, "qmt_mm": 40.0, "int_cv_within": None,
                 "qmt_provenance": "standard_qmt", "rf_thickness_mm": 20.0}
        if top_frames is not None:
            femur["top_frames"] = top_frames
        vals = {"femur": femur,
                "rf": {"full_max_thickness_mm": rf_center_max, "int_cv_within": 0.7},
                "sweep": {"full_max_thickness_mm": 25.0}}
        return [{"study": "teststudy_dir", "vals": vals}]

    uid = dc._study_code("teststudy_dir")
    run_dir = tmp_path / "AKAT_9_zzz"
    run_dir.mkdir()
    (run_dir / "record.json").write_text(json.dumps({
        "study_uid": uid, "run_id": "AKAT_9_zzz",
        "routed_picks": {"femur": {"frame_features": [{"rf_thickness_mm": 30.0}] * 3},
                         "rf": {"frame_features": [{"int_cv_within": 0.5}]}},
        "aggregate": {}}))
    monkeypatch.setattr(dc, "RUNS_DIR", tmp_path)
    monkeypatch.setattr(dc.fkl, "killed_study_uids", lambda: set())
    monkeypatch.setattr(dc.fkl, "feature_kills", lambda: {})
    monkeypatch.setattr(dc.fkl, "feature_clears", lambda: {})
    monkeypatch.setattr(dc.fkl, "contributions", lambda: {uid: [{
        "scope": "contribution", "status": "active", "study_uid": uid,
        "fields": ["AKAT_9_zzz"], "source_run_id": "AKAT_9_zzz"}]})

    # fallback anchor: auto median 20 carries K=5 weight vs three 30s -> the median holds at 20
    out, prov = dc.assemble_cohort(rows_with())
    assert out[0]["rf_thickness_mm"] == pytest.approx(20.0)
    assert prov["contributions_applied"][uid]["runs"] == ["AKAT_9_zzz"]
    assert prov["contributions_applied"][uid]["frames"] == {"femur": 3, "rf": 1}
    # real top-frames on the auto side: [18..22] + [30,30,30] -> median 21.5 (frames vs frames)
    tf = [{"rf_thickness_mm": v} for v in (18.0, 19.0, 20.0, 21.0, 22.0)]
    out, _ = dc.assemble_cohort(rows_with(top_frames=tf))
    assert out[0]["rf_thickness_mm"] == pytest.approx(21.5)
    # validated texture pools too — [0.7]*5 anchor + one 0.5 frame -> stays 0.7
    assert out[0]["int_cv_within"] == pytest.approx(0.7)
    # a run-bound RELEASE outranks pooling: untrusted views + released 0.47 -> served 0.47
    monkeypatch.setattr(dc.fkl, "feature_clears", lambda: {uid: {"int_cv_within": {
        "values": {"max_femur_mm": 25.0, "max_center_mm": 20.0, "max_sweep_mm": 25.0},
        "released_value": 0.47, "source_run_id": "X"}}})
    out, prov = dc.assemble_cohort(rows_with(rf_center_max=20.0))
    assert out[0]["int_cv_within"] == pytest.approx(0.47)
    assert prov["contributions_applied"][uid]["int_cv_pooled"] is False
    # the gate anchor is not poolable: full_max stayed the AUTO value throughout
    assert prov["per_study"][0]["rf_source"] == "femur"


def test_real_qa_master_serves_no_ledger_held_value():
    """The enforcement seam, on the REAL files: every criterion cell the ledger holds must be
    blank in qa_master. Ledger→cells direction on purpose — qa_master's own `measurement_flags`
    receipt is written by the same code path as the blanking, so a substrate that stopped
    consuming the ledger wipes both together (the red-team review's catch, 2026-08-12)."""
    import csv
    qa = REPO / "data" / "qa_master.csv"
    if not qa.exists():
        pytest.skip("qa_master.csv not built on this machine (gitignored substrate)")
    by_uid = {r.get("study_uid"): r for r in csv.DictReader(open(qa, encoding="utf-8"))}
    leaks = {}
    for uid, fields in fkl.open_measurement_holds().items():
        r = by_uid.get(uid)
        if r is None:                      # held study not in the substrate — cannot leak
            continue
        bad = [f for f in fields if (r.get(f) or "").strip()]
        if bad:
            leaks[uid] = bad
    assert not leaks, f"ledger-held cells carry values in qa_master: {leaks}"


def test_real_ledger_parses_and_founding_line_is_intact():
    lines = (REPO / "data" / "kill_list.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0])["key_sha8"] == "1CC51295"      # row 96 stays byte-level first
    for ln in lines:
        e = json.loads(ln)
        assert "key_sha8" in e or "study_uid" in e
        # PHI guard: no entry may carry a study name in any field
        assert "study_name" not in e and "name" not in e

"""force_card serving invariants — the Strength card can never lie.

The contract (docs/force_card_spec.md) in executable form: the frozen artifact is
self-consistent and reproduces its own stored predictions; a ledger-held row can never
surface a number through the real path (the no-force rule end-to-end); the CV display
tiers implement the ratified policy; the envelope refuses instead of extrapolating; and
no payload ever carries a PHI-adjacent key. Real-substrate tests run against the actual
qa_master + kill_list, so they also guard the live data's states.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from preflight_helpers import force_card

REPO = Path(__file__).resolve().parents[2]
QA = REPO / "data" / "qa_master.csv"
ARTIFACT = REPO / "data" / "cohort_stats" / "force_model.json"
LEDGER = REPO / "data" / "kill_list.jsonl"


def _art() -> dict:
    return json.loads(ARTIFACT.read_text())


def _manual_predict(art: dict, feats: dict) -> float:
    z = [(feats[c] - m) / s for c, m, s in
         zip(art["features"], art["scaler_mean"], art["scaler_scale"])]
    return art["intercept"] + sum(zi * ci for zi, ci in zip(z, art["coef"]))


def _numeric_leaves(obj, out=None) -> set:
    out = set() if out is None else out
    if isinstance(obj, dict):
        for v in obj.values():
            _numeric_leaves(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _numeric_leaves(v, out)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out.add(float(obj))
    return out


def _row(**over) -> dict:
    """A synthetic in-envelope new-round row (age 30 M, mid-range everything)."""
    base = {"study_uid": "NRTEST0001", "cohort_round": "new", "izo_torque_nm": "150.0",
            "izo_cv_pct": "5.0", "measurement_flags": "", "age": "30", "sex": "M",
            "height_cm": "175", "weight_kg": "75", "rf_thickness_mm": "22.0",
            "full_thickness_q50_mm": "20.0", "visible_rf_area_mm2": "800.0", "qmt_mm": "45.0"}
    base.update(over)
    return base


# ---------------------------------------------------------------- artifact integrity
def test_artifact_exists_and_is_self_consistent():
    art = _art()
    assert art["kind"] == "force_expected_range"
    assert art["criterion"] == "izo_torque_nm"
    assert len(art["features"]) == len(art["coef"]) == len(art["scaler_mean"]) \
        == len(art["scaler_scale"])
    assert art["n"] == len(art["fit_uids"]) == art["n_by_sex"]["M"] + art["n_by_sex"]["F"]
    assert art["self_test"]["max_abs_diff_vs_sklearn"] < 1e-9
    for sex in ("global", "M", "F"):
        q = art["residual_quantiles"][sex]
        assert q["q2_5"] < q["q10"] < 0 < q["q90"] < q["q97_5"]
    for c, (lo, hi) in art["envelope"].items():
        assert lo < hi, c
    # the recompute reproduced the committed receipt at freeze time (builder aborts otherwise)
    assert abs(art["oof_recomputed"]["r2_mean"] - art["oof_receipt"]["r2_mean"]) <= 0.005


def test_stored_sample_predictions_reproduce():
    art = _art()
    for s in art["sample_predictions"]:
        # stored `predicted` is rounded to 3dp at freeze; the dot product must land on it
        assert abs(_manual_predict(art, s["features"]) - s["predicted"]) < 1e-3, s["uid"]


# ------------------------------------------------------------------- payload states
def test_no_model_and_not_in_substrate_states():
    assert force_card.build_payload(None, None)["state"] == "no_model"
    assert force_card.build_payload(_row(), None)["state"] == "no_model"
    p = force_card.build_payload(None, _art())
    assert p["state"] == "not_in_substrate"
    assert p["model"]["n"] == _art()["n"]


def test_full_state_band_matches_hand_computation():
    art = _art()
    p = force_card.build_payload(_row(), art)
    assert p["state"] == "full"
    feats = {"age": 30.0, "sex_m": 1.0, "height_cm": 175.0, "weight_kg": 75.0,
             "rf_thickness_mm": 22.0, "full_thickness_q50_mm": 20.0,
             "visible_rf_area_mm2": 800.0, "qmt_mm": 45.0}
    pred = _manual_predict(art, feats)
    q = art["residual_quantiles"]["M"]
    assert abs(p["band"]["predicted"] - pred) < 0.06          # payload rounds to 0.1
    assert abs(p["band"]["lo"] - (pred + q["q10"])) < 0.06
    assert abs(p["band"]["hi"] - (pred + q["q90"])) < 0.06


def test_cv_display_tiers_follow_the_ratified_policy():
    art = _art()
    quotable = force_card.build_payload(_row(izo_cv_pct="10.0"), art)["measured"]
    marginal = force_card.build_payload(_row(izo_cv_pct="10.1"), art)["measured"]
    unstable = force_card.build_payload(_row(izo_cv_pct="15.1"), art)["measured"]
    unrecorded = force_card.build_payload(_row(izo_cv_pct=""), art)["measured"]
    assert quotable["tier"] == "quotable" and quotable["display_value"]
    assert marginal["tier"] == "marginal" and marginal["display_value"]
    assert unstable["tier"] == "unstable" and not unstable["display_value"]
    assert unrecorded["tier"] == "marginal" and "not recorded" in unrecorded["note"]
    # an unstable value never gets a concordance verdict
    assert force_card.build_payload(_row(izo_cv_pct="15.1"), art)["concordance"] is None


def test_held_row_serves_no_number_even_if_cells_carried_values():
    # Defense in depth: even if a substrate rebuild ever left values in a flagged row,
    # the flag alone must silence the measured side.
    art = _art()
    p = force_card.build_payload(
        _row(measurement_flags="HELD izo_torque_nm (cv_zero)", izo_torque_nm="9.1"), art)
    assert p["state"] == "expected_only"
    assert p["measured"] is None and p["held"]["receipt"].startswith("HELD")
    assert p["concordance"] is None
    assert "9.1" not in json.dumps(p)


def test_envelope_refusal_names_the_feature_and_bound():
    art = _art()
    p = force_card.build_payload(_row(age="99"), art)
    assert p["band"] is None
    assert any("age 99" in r and "outside the fitted range" in r for r in p["band_refusal"])
    assert p["state"] == "measured_only"        # the measured side still shows
    p2 = force_card.build_payload(_row(qmt_mm=""), art)
    assert p2["band"] is None
    assert any("missing input" in r and "qmt_mm" in r for r in p2["band_refusal"])
    p3 = force_card.build_payload(_row(sex="X"), art)
    assert p3["band"] is None and any("sex" in r for r in p3["band_refusal"])


def test_concordance_verdicts_below_within_above():
    art = _art()
    base = force_card.build_payload(_row(), art)["band"]
    below = force_card.build_payload(_row(izo_torque_nm=str(base["lo"] - 20)), art)
    above = force_card.build_payload(_row(izo_torque_nm=str(base["hi"] + 20)), art)
    within = force_card.build_payload(_row(izo_torque_nm=str((base["lo"] + base["hi"]) / 2)), art)
    assert below["concordance"]["verdict"] == "below"
    assert abs(below["concordance"]["margin_nm"] - 20) < 0.2
    assert above["concordance"]["verdict"] == "above"
    assert within["concordance"]["verdict"] == "within"


# ------------------------------------------------------- real-substrate invariants
@pytest.fixture(scope="module")
def real_rows() -> dict:
    rows = {}
    for r in csv.DictReader(open(QA, encoding="utf-8")):
        if r.get("cohort_round") == "new":
            rows[r["study_uid"]] = r
    return rows


def test_ledger_held_rows_never_surface_a_value(real_rows):
    """The no-force rule through the REAL path: for every measurement-flagged uid in the
    ledger, the served payload has no measured side — and none of the flagged VALUES the
    ledger snapshotted appear anywhere in the payload."""
    art = _art()
    flagged = [json.loads(line) for line in LEDGER.read_text().splitlines()
               if line.strip()]
    flagged = [e for e in flagged if e.get("scope") == "measurement"
               and e.get("status") == "flagged"]
    assert flagged, "expected measurement flags in the ledger (rows 24/87/92)"
    for e in flagged:
        uid = e["study_uid"]
        assert uid in real_rows, f"ledger uid {uid} missing from qa_master new rows"
        p = force_card.build_payload(force_card.qa_row(uid), art)
        assert p["measured"] is None and p["held"] is not None, uid
        assert p["state"] in ("expected_only", "refused"), uid
        # none of the ledger-snapshotted torque/power VALUES may appear as a number anywhere
        # in the payload (weight_kg legitimately feeds the band; a CV of 0.0 collides with
        # ordinary zeros, so the leak check is on the value-bearing cells)
        crit = {k: v for k, v in e.get("values", {}).items()
                if k in ("izo_torque_nm", "isok_torque_ext_nm", "isok_mean_power_ext_w")}
        leaves = _numeric_leaves(p)
        for k, v in crit.items():
            assert float(v) not in leaves, f"held criterion {k}={v} surfaced for {uid}"
        # and never as text either: the panel prints the gate receipt raw, so a future gate
        # that writes VALUES into measurement_flags would leak through it (red-team T3)
        receipt = p["held"]["receipt"]
        for k, v in crit.items():
            assert v not in receipt, f"held criterion {k}={v} embedded in the receipt for {uid}"


def test_every_real_row_yields_a_legal_phi_free_payload(real_rows):
    art = _art()
    legal = {"full", "expected_only", "measured_only", "refused"}
    states = {}
    for uid in real_rows:
        p = force_card.build_payload(force_card.qa_row(uid), art)
        states[p["state"]] = states.get(p["state"], 0) + 1
        assert p["state"] in legal, uid
        blob = json.dumps(p)
        # forbidden qa_master COLUMNS (payload's own keys legitimately contain the word
        # "session" — no_session/session_sd_nm — so check quoted key names, not substrings)
        for phi in ('"study_name"', '"rel_path"', '"operator"', '"surname"'):
            assert phi not in blob, f"PHI-adjacent key {phi} in payload for {uid}"
        if p.get("measured") and p["measured"]["tier"] == "unstable":
            assert p["concordance"] is None, uid
    assert states.get("full", 0) > 0 and states.get("expected_only", 0) > 0


def test_band_coverage_on_served_full_cards_is_sane(real_rows):
    """~80%-nominal band: the share of full cards reading 'within' must sit in a wide
    sanity window — a broken band placement (all-below / all-above) fails loudly."""
    art = _art()
    verdicts = [force_card.build_payload(force_card.qa_row(u), art)["concordance"]["verdict"]
                for u in real_rows
                if force_card.build_payload(force_card.qa_row(u), art)["state"] == "full"]
    share_within = verdicts.count("within") / len(verdicts)
    assert 0.70 <= share_within <= 0.95, share_within


def test_fit_uids_are_a_subset_of_the_substrate(real_rows):
    missing = [u for u in _art()["fit_uids"] if u not in real_rows]
    assert not missing, f"fit uids no longer in qa_master: {missing[:5]}"


def test_should_render_gate_legacy_vs_v8():
    """Regression for the 2026-08-12 browser + red-team catch (B1): a truthiness gate on
    study_modalities() (which reports 'sweep' for EVERY study with raw frames) rendered the
    substrate hint on legacy studies — 273 of them, AGZ855 included. The gate now takes the
    docstring-owned legacy/new predicate (study_is_new_round); only the not_in_substrate
    state consults it."""
    art = _art()
    served = force_card.build_payload(_row(), art)
    absent = force_card.build_payload(None, art)
    # a served payload renders regardless of the round flag
    assert force_card.should_render(served, False)
    assert force_card.should_render(served, True)
    # not-in-substrate: legacy is silent; a new-round study gets the bring-it-in hint
    assert not force_card.should_render(absent, False)
    assert force_card.should_render(absent, True)
    # no payload at all (no uid) never renders
    assert not force_card.should_render(None, True)


def test_should_render_gate_on_the_real_legacy_study():
    """AGZ855 through the real predicate chain: uid derivable, not in the new-round
    substrate, study_is_new_round False -> the card must not render. Skips cleanly if the
    legacy corpus is not on this machine."""
    from preflight_helpers.study_discovery import discover_studies, study_is_new_round
    agz = [e for e in discover_studies() if e.meta.study_code == "AGZ855"]
    if not agz:
        pytest.skip("AGZ855 not discoverable on this machine")
    e = agz[0]
    p = force_card.payload_for_uid(e.meta.study_uid)
    assert p["state"] == "not_in_substrate"
    assert study_is_new_round(e.study_path) is False
    assert not force_card.should_render(p, study_is_new_round(e.study_path))


def test_unstable_tier_is_structurally_value_free():
    """The withheld value must not be IN the payload at all (red-team T2): no consumer can
    leak what the payload does not carry."""
    art = _art()
    m = force_card.build_payload(_row(izo_cv_pct="19.2", izo_torque_nm="65.0"), art)["measured"]
    assert m["tier"] == "unstable" and not m["display_value"]
    assert m["value"] is None and m["session_sd_nm"] is None
    assert m["cv_pct"] == 19.2                       # the reason survives


def test_non_finite_criterion_is_treated_as_absent():
    """A nan criterion compares False against both band edges and would read 'within';
    inf would read 'above by inf' (red-team m1). Non-finite parses as absent."""
    art = _art()
    for bad in ("nan", "inf", "-inf"):
        p = force_card.build_payload(_row(izo_torque_nm=bad), art)
        assert p["measured"] is None and p["concordance"] is None, bad
        assert p["state"] == "expected_only", bad
    p2 = force_card.build_payload(_row(izo_cv_pct="nan"), art)
    assert p2["measured"]["cv_pct"] is None          # nan CV = unrecorded, not quotable-clean
    assert p2["measured"]["tier"] == "marginal"


def test_corrupt_artifact_degrades_to_no_model(monkeypatch, tmp_path):
    """A corrupt force_model.json must surface the visible no_model caption, not vanish
    inside the page's fail-silent wrapper (red-team M3)."""
    bad = tmp_path / "force_model.json"
    bad.write_text("{ this is not json")
    monkeypatch.setattr(force_card, "MODEL_JSON", str(bad))
    monkeypatch.setattr(force_card, "_cache", {})
    assert force_card.artifact() is None
    assert force_card.payload_for_uid("NRTEST0001")["state"] == "no_model"


def test_artifact_and_receipts_share_the_substrate():
    """Coherence tripwire (red-team T4/M2): the served artifact, the ladder receipts, and the
    live qa_master must all be the same bytes — otherwise the panel quotes numbers computed
    on a substrate the model never saw."""
    import hashlib
    art = _art()
    summary = json.loads((REPO / "scripts/analysis/results/force_ladder_summary.json").read_text())
    live = hashlib.sha256(QA.read_bytes()).hexdigest()[:16]
    assert art["substrate"]["sha256_16"] == summary["substrate"]["sha256_16"] == live
    assert art["n"] == summary["n"]["SENS_A_no_texture"]

"""The measurement-trust strip's pure row logic — every state, headless (no streamlit).

The strip absorbs the #17 texture chip, so its texture row must carry the chip text VERBATIM
(one vocabulary), and the strength row must mirror the force-card payload states (the card and
the strip can never tell different stories about the same study).
"""
from labeling_app.assessment_pages import trust_strip
from preflight_helpers.rf_reliability import (resolve_rf_source, resolve_texture_state,
                                              rf_reliability_stamp)


def _agg(femur=20.0, center=20.5, sweep=20.2):
    """Build run_data through the REAL stamp (values included — the strip's this-run numbers)."""
    stamp = rf_reliability_stamp({"full_max_thickness_mm": femur},
                                 {"full_max_thickness_mm": center},
                                 {"full_max_thickness_mm": sweep})
    return {"aggregate": {"_rf_reliability": stamp}}, stamp["decision"]


def _texts(rows):
    return [t for _c, _i, t in rows]


def test_validated_study_reads_center_rf_and_verified():
    run, dec = _agg()
    ts = resolve_texture_state(dec)
    rows = trust_strip.rows_for(run, ts, {"no_session": True, "state": "expected_only"},
                                is_new_round=True, study_uid="NRTEST0001")
    texts = _texts(rows)
    assert "centre-RF validated" in texts[0]
    assert ts["chip"] in texts[1]                       # verbatim chip vocabulary
    assert "no dynamometer session" in texts[2]


def test_disagreement_serves_femur_and_pending_texture():
    run, dec = _agg(femur=24.0, center=19.0, sweep=23.8)   # >10% apart, sweep sides with femur
    ts = resolve_texture_state(dec)
    rows = trust_strip.rows_for(run, ts, None, is_new_round=True, study_uid="NRTEST0001")
    assert "served from the femur view" in _texts(rows)[0]
    assert ts["state"] == "pending_review" and ts["chip"] in _texts(rows)[1]


def test_legacy_single_sweep_says_single_view():
    run = {"aggregate": {"_rf_reliability": {"decision": resolve_rf_source(None, None, 21.0)}}}
    rows = trust_strip.rows_for(run, None, None, is_new_round=False, study_uid="NRTEST0001")
    texts = _texts(rows)
    assert "single-view study" in texts[0]
    assert "no cross-check possible" in texts[1]
    assert len(rows) == 2                               # no strength row outside the new round


def test_strength_states_mirror_the_card_payload():
    run, dec = _agg()
    ts = resolve_texture_state(dec)
    held = {"held": {"receipt": "HELD izo_cv_pct+izo_torque_nm (cv_zero+kgf_m_as_nm)"}}
    r = trust_strip.rows_for(run, ts, held, is_new_round=True, study_uid="NRTEST0001")
    assert "held out of analysis" in _texts(r)[2] and "cv_zero" in _texts(r)[2]
    measured = {"measured": {"value": 239.0, "display_value": True, "tier": "quotable",
                             "cv_pct": 4.0, "note": None},
                "band": {"lo": 180.0, "hi": 300.0},
                "concordance": {"verdict": "within", "margin_nm": 0.0}}   # the card's REAL shape
    r = trust_strip.rows_for(run, ts, measured, is_new_round=True, study_uid="NRTEST0001")
    assert "measured 239 Nm" in _texts(r)[2] and "within the expected range" in _texts(r)[2]
    unstable = {"measured": {"value": None, "display_value": False, "tier": "unstable",
                             "cv_pct": 19.2, "note": "…"}}
    r = trust_strip.rows_for(run, ts, unstable, is_new_round=True, study_uid="NRTEST0001")
    assert "too unstable" in _texts(r)[2]


def test_rows_for_survives_the_real_payload_shape():
    """Pin the strip against force_card's OWN payload builder (never a hand-written imitation) —
    the first render failed precisely because the unit test had invented `concordance`'s shape."""
    from preflight_helpers.force_card import build_payload
    art_row = {"study_uid": "NRTEST0001", "cohort_round": "new", "izo_torque_nm": "200",
               "izo_cv_pct": "5.0", "measurement_flags": "", "age": "30", "sex": "M",
               "height_cm": "180", "weight_kg": "80", "rf_thickness_mm": "20",
               "full_thickness_q50_mm": "17", "visible_rf_area_mm2": "600", "qmt_mm": "40"}
    art = {"n": 101, "n_by_sex": {"M": 50, "F": 51},
           "oof_receipt": {"r2_mean": 0.69, "r2_sd": 0.016, "rmse_mean": 40.1},
           "band": {"label": "8-in-10"}, "target_stats": {"min_nm": 76, "max_nm": 407},
           "within_subject_sd_nm": 10.5, "features": ["age", "sex_m"], "date": "2026-08-13",
           "version": "1.0", "sex_encoding": {"M": 1.0, "F": 0.0},
           "envelope": {"age": [18, 65], "sex_m": [0, 1]},
           "scaler_mean": [30.0, 0.5], "scaler_scale": [10.0, 0.5],
           "intercept": 190.0, "coef": [10.0, 30.0],
           "residual_quantiles": {"M": {"q10": -50, "q90": 60, "q2_5": -80, "q97_5": 90},
                                  "F": {"q10": -35, "q90": 40, "q2_5": -60, "q97_5": 70}}}
    fp = build_payload(art_row, art)
    rows = trust_strip.rows_for({"aggregate": {}}, None, fp,
                                is_new_round=True, study_uid="NRTEST0001")
    assert any("Strength" in t and "measured 200 Nm" in t for t in _texts(rows))


def test_two_level_numbers_on_the_size_and_texture_rows():
    """Akadir 2026-08-14: 'could we add the numbers here' — the Size row carries this run's
    femur-vs-centre pair (from the stamp's values), and the Texture row carries the run's
    int_cv, tagged when it came from a refined region."""
    run, dec = _agg(femur=13.2, center=16.2, sweep=16.0)          # the AKA036 shape
    run["aggregate"]["int_cv_within"] = 0.5203
    run["texture_refine"] = {"erosion_px": 6, "band": (0.1, 0.8)}
    ts = resolve_texture_state(dec)
    rows = trust_strip.rows_for(run, ts, None, is_new_round=True, study_uid="NRTEST0001")
    assert "this run: femur 13.2 vs centre 16.2 mm (19% apart)" in _texts(rows)[0]
    assert "int_cv this run: 0.520 (refined region)" in _texts(rows)[1]
    # stamp values ride the stamp itself — the real shape, not a test invention
    assert run["aggregate"]["_rf_reliability"]["values"]["max_center_mm"] == 16.2


def test_canonical_pair_reads_the_freeze_landmark_when_present():
    """Level 1 (the canonical top-down check) comes from the freeze's own landmark values;
    absent file degrades silently to run-level numbers only."""
    import pytest
    if not trust_strip._LANDMARK.exists():
        pytest.skip("landmark JSON not on this machine (gitignored)")
    trust_strip._canon_cache = None                                # force a fresh read
    pair = trust_strip._canonical_pair("NRE4AEC6CC")               # AKA036
    assert pair and round(pair["femur"], 1) == 13.2 and round(pair["center"], 1) == 16.2
    run, dec = _agg(femur=13.0, center=16.0, sweep=16.0)
    rows = trust_strip.rows_for(run, resolve_texture_state(dec), None,
                                is_new_round=True, study_uid="NRE4AEC6CC")
    assert "canonical check: femur 13.2 vs centre 16.2 mm" in _texts(rows)[0]


def test_ledger_verdict_suffix_uses_the_real_ledger():
    """AKA017's recorded texture kill must surface as a decided withhold — the 'acts' half of
    the surface, on the real ledger file."""
    run, dec = _agg(femur=20.7, center=21.1, sweep=20.8)   # gate passes (the AKA017 shape)
    ts = resolve_texture_state(dec)
    rows = trust_strip.rows_for(run, ts, None, is_new_round=True, study_uid="NR1C66A990")
    assert "withheld by Akadir's recorded verdict" in _texts(rows)[1]

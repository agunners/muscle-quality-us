"""The p05 metric card — the cohort percentile LEADS, and a demographic band too
thin for a precise percentile degrades to a position word (Akadir 2026-06-10: a
single number is the headline). `_one_card` is mounted in isolation; AppTest
re-execs each function in its own namespace, so each is fully self-contained."""
from __future__ import annotations

from streamlit.testing.v1 import AppTest


def _card_normal() -> None:
    from labeling_app.assessment_pages import p05_assessment_view as p05
    run = {"aggregate": {"rf_thickness_mm": 18.0, "_qa_tier": {}},
           "placements": {"rf_thickness_mm": {
               "overall": {"pctile": 72.0, "n": 239},
               "age_matched": {"pctile": 70.0, "n": 94},
               "sex_matched": {"pctile": 56.0, "n": 50}}}}
    p05._one_card(run, "rf_thickness_mm")


def _card_thin_sex() -> None:
    from labeling_app.assessment_pages import p05_assessment_view as p05
    run = {"aggregate": {"rf_thickness_mm": 18.0, "_qa_tier": {}},
           "placements": {"rf_thickness_mm": {
               "overall": {"pctile": 72.0, "n": 239},
               "age_matched": {"pctile": 70.0, "n": 94},
               "sex_matched": {"pctile": 90.0, "n": 8}}}}      # n<20 → degrade
    p05._one_card(run, "rf_thickness_mm")


def _body(at) -> str:
    return (" ".join(m.value for m in at.markdown)
            + " " + " ".join(c.value for c in at.caption))


def test_percentile_leads_the_card() -> None:
    at = AppTest.from_function(_card_normal).run()
    assert not at.exception
    body = _body(at)
    assert "p72" in body                       # the prominent lead number
    assert "p70" in body and "p56" in body     # age + sex refinement (both n≥20)


def test_thin_band_degrades_to_a_word_not_a_false_percentile() -> None:
    at = AppTest.from_function(_card_thin_sex).run()
    assert not at.exception
    body = _body(at)
    assert "p72" in body                        # overall (n=239) stays precise
    assert "above typical" in body              # sex band (n=8) → a word
    assert "p90" not in body                    # NOT a false precise percentile


def _card_landmark_divergent() -> None:
    from labeling_app.assessment_pages import p05_assessment_view as p05
    run = {"aggregate": {"visible_rf_area_mm2": 500.0, "_qa_tier": {}},
           "placements": {"visible_rf_area_mm2": {"overall": {"pctile": 40.0, "n": 239}}},
           "_landmark": {"visible_rf_area_mm2": {"value": 600.0,    # 500 vs 600 = 17% gap
                                                 "overall": {"pctile": 70.0, "n": 239}}}}
    p05._one_card(run, "visible_rf_area_mm2")


def _card_landmark_aligned() -> None:
    from labeling_app.assessment_pages import p05_assessment_view as p05
    run = {"aggregate": {"visible_rf_area_mm2": 595.0, "_qa_tier": {}},
           "placements": {"visible_rf_area_mm2": {"overall": {"pctile": 68.0, "n": 239}}},
           "_landmark": {"visible_rf_area_mm2": {"value": 600.0,    # 595 vs 600 = <1% gap
                                                 "overall": {"pctile": 70.0, "n": 239}}}}
    p05._one_card(run, "visible_rf_area_mm2")


def test_landmark_second_placement_shows_and_nudges_on_divergence() -> None:
    at = AppTest.from_function(_card_landmark_divergent).run()
    assert not at.exception
    body = _body(at)
    assert "p40" in body                        # the SELECTED-frame lead placement
    assert "study landmark" in body and "p70" in body   # the second placement
    assert "median of the 5 fullest clean frames" in body   # accurate (it's a median)
    assert "reads low" in body                          # the BELOW-direction nudge fired
    assert "selection-independent comparison" in body   # the divergence nudge fired


def test_landmark_shown_but_no_nudge_when_aligned() -> None:
    at = AppTest.from_function(_card_landmark_aligned).run()
    assert not at.exception
    body = _body(at)
    assert "study landmark" in body and "p70" in body   # always show both
    assert "selection-independent comparison" not in body  # but no nudge under the gap


def _card_landmark_above() -> None:
    from labeling_app.assessment_pages import p05_assessment_view as p05
    run = {"aggregate": {"visible_rf_area_mm2": 700.0, "_qa_tier": {}},
           "placements": {"visible_rf_area_mm2": {"overall": {"pctile": 80.0, "n": 239}}},
           "_landmark": {"visible_rf_area_mm2": {"value": 600.0,    # 700 vs 600 = +17% ABOVE
                                                 "overall": {"pctile": 70.0, "n": 239}}}}
    p05._one_card(run, "visible_rf_area_mm2")


def test_landmark_above_shows_check_not_backwards_warning() -> None:
    """The YGP554 bug: a picked frame ABOVE the landmark must NOT trip the 'reads
    low' warning — the old nudge used an absolute gap and fired in both directions.
    Now it shows a ✓ instead, and never the below-nudge text."""
    at = AppTest.from_function(_card_landmark_above).run()
    assert not at.exception
    body = _body(at)
    assert "study landmark" in body and "p70" in body          # always show both
    assert "at or above" in body                               # the ✓ good-news line
    assert "reads low" not in body                             # NOT the backwards warning
    assert "selection-independent comparison" not in body      # below-nudge text absent


def _condition_headline_render() -> None:
    from labeling_app.assessment_pages import p05_assessment_view as p05
    result = {"headline": {"available": True, "band": "INTERMEDIATE", "band_idx": 1,
                           "condition": 53.0, "boundaries": [25.5, 75.6]}}
    p05._condition_headline(result)


def test_condition_headline_shows_its_honesty_note_on_live_page() -> None:
    """write-what-we-do: the 3-driver line + the honesty note for whichever recipe is SERVED must be
    on the LIVE headline (paper caveat 7 had said this lived only in the standalone preview).

    The note is asserted against `SERVED_HEADLINE` rather than a literal, because the served recipe
    changed on 2026-08-04 and a hardcoded caption went on describing the old one — the score said
    millimetres while the page said muscle-over-BMI."""
    from preflight_helpers.condition_model import SERVED_HEADLINE
    at = AppTest.from_function(_condition_headline_render).run()
    assert not at.exception
    body = _body(at)
    assert "Muscle condition" in body and "INTERMEDIATE" in body
    assert "Driven by" in body                    # the 3-driver line
    assert "fascicular contrast" in body          # harmonized texture term
    expected = "millimetres" if SERVED_HEADLINE == "A" else "muscular-but-heavy"
    assert expected in body, f"the honesty note must match the served recipe {SERVED_HEADLINE}"


def _ensure_landmark_dict_scan() -> None:
    """Drive the REAL _ensure_landmark gathering with the scan stored the way the
    picker actually stores it — a DICT keyed by frame_base, NOT a list of dicts."""
    import streamlit as st
    import numpy as np
    from types import SimpleNamespace
    from labeling_app.measurements import LABEL_RF
    from labeling_app.assessment_pages import p05_assessment_view as p05
    masks, grades = {}, {}
    for i in range(6):
        fb = f"f{i}"
        m = np.zeros((128, 128), dtype=int)
        m[50:70, 40:90] = LABEL_RF
        masks[fb] = m
        grades[fb] = {"image_quality_validity_flags": [],
                      "image_quality_pending_calibration_flags": [],
                      "image_quality_aggregate_tier": "valid"}
    st.session_state["femur_scan"] = {fb: {"frame_base": fb, "tier": 1} for fb in masks}
    st.session_state["predicted_masks"] = masks
    st.session_state["image_quality_cache"] = grades
    st.session_state["final_u8_cache"] = {}
    entry = SimpleNamespace(study_path="/tmp/_no_such_study_",
                            meta=SimpleNamespace(study_code="TEST001", age=50, sex="M"))
    res = p05._ensure_landmark(entry, 50, "M")
    st.session_state["_t_reason"] = res.get("reason")


def test_ensure_landmark_iterates_a_dict_scan_without_attributeerror() -> None:
    """Regression: the scan is a {frame_base: entry} dict; iterating it as a list of
    dicts called .get() on a str key. The gathering must get PAST that (it may then
    fail on the fake study's spacing — that's fine, just not the 'str' bug)."""
    at = AppTest.from_function(_ensure_landmark_dict_scan).run()
    assert not at.exception
    reason = at.session_state["_t_reason"]
    assert reason is None or "'str' object has no attribute 'get'" not in reason


# ----- The single-score extensions: co-equal breakdown, rater chip, base-800,
#       formula reveal. All ADDITIVE + fail-silent; the breakdown surfaces the
#       three MEASURED axis percentiles (the dimensional read), never the weights.

def test_breakdown_rows_match_card_percentiles_and_axis_direction() -> None:
    """The bug Akadir caught: the breakdown showed a DIFFERENT pXX than the card for
    the same feature (coarse decile-interp + a condition-oriented flip on fat). Now
    each row carries the card's EXACT placement percentile + that axis's own
    better-direction — so fat reads its card p20 ↓-lower-better, never a flipped ~p80."""
    from labeling_app.assessment_pages import p05_assessment_view as p05
    result = {"axes": [
        {"col": "fat_to_depth_q50", "name": "Fat over muscle", "contribution": 1.5},
        {"col": "int_cv_within", "name": "Fascicular contrast", "contribution": -1.2}]}
    run_data = {"placements": {
        "fat_to_depth_q50": {"overall": {"pctile": 20.0}},   # raw — low fat is GOOD
        "int_cv_within": {"overall": {"pctile": 77.0}}}}
    rows = {r["clinical"]: r for r in p05._breakdown_rows(result, run_data)}
    assert rows["Fat"]["pct"] == 20.0                  # SAME as the card, NOT a flipped ~80
    assert rows["Fat"]["direction"] == "↓ lower = better"
    assert rows["Tissue"]["pct"] == 77.0
    assert rows["Tissue"]["direction"] == "↑ higher = better"


def _condition_breakdown_render() -> None:
    from labeling_app.assessment_pages import p05_assessment_view as p05
    result = {"headline": {"available": True, "band": "INTERMEDIATE", "band_idx": 1,
                           "condition": 53.0, "boundaries": [25.5, 75.6]},
              "axes": [
                  {"col": "star_ratio", "name": "Muscle vs body size", "contribution": -2.0},
                  {"col": "fat_to_depth_q50", "name": "Fat over muscle", "contribution": 1.5},
                  {"col": "int_cv_within", "name": "Fascicular contrast", "contribution": -1.2}]}
    run_data = {"placements": {
        "star_ratio": {"overall": {"pctile": 89.0}},
        "fat_to_depth_q50": {"overall": {"pctile": 20.0}},
        "int_cv_within": {"overall": {"pctile": 77.0}}}}
    p05._condition_breakdown(result, run_data)


def test_condition_breakdown_renders_axes_direction_and_contribution() -> None:
    at = AppTest.from_function(_condition_breakdown_render).run()
    assert not at.exception
    body = _body(at)
    assert "What drives the score" in body
    assert "Size" in body and "Fat" in body and "Tissue" in body   # clinical axes
    assert "lower = better" in body                                # fat's own direction (matches its (i))
    assert "pushes the score toward" in body                       # contribution strength
    assert "pushes this score the" in body                         # the driver line (reworded moves->pushes 2026-06-18)


def _condition_headline_base800() -> None:
    from labeling_app.assessment_pages import p05_assessment_view as p05
    result = {"headline": {"available": True, "band": "INTERMEDIATE", "band_idx": 1,
                           "condition": 53.0, "boundaries": [25.5, 75.6]}}
    p05._condition_headline(result, 800)


def test_base800_rescales_the_headline_number_and_scale() -> None:
    at = AppTest.from_function(_condition_headline_base800).run()
    assert not at.exception
    body = _body(at)
    assert "/ 800" in body and "424" in body          # 53 × 8 = 424 on the 0–800 scale
    assert "/ 100" not in body


def _rater_chip_cohort() -> None:
    from labeling_app.assessment_pages import p05_assessment_view as p05
    result = {"headline": {"available": True, "band": "PRESERVED", "band_idx": 0,
                           "condition": 92.0, "boundaries": [25.5, 75.6]}}
    p05._condition_rater_chip("ACL762", result)       # a real cohort study, graded PRESERVED


def test_rater_chip_shows_consensus_grade_and_agreement() -> None:
    at = AppTest.from_function(_rater_chip_cohort).run()
    assert not at.exception
    body = _body(at)
    assert "Doctors' consensus grade" in body and "PRESERVED" in body
    assert "model agrees" in body                      # model band == consensus
    assert "training cohort" in body                   # honest: target, not a check


def _rater_chip_offcohort() -> None:
    from labeling_app.assessment_pages import p05_assessment_view as p05
    result = {"headline": {"available": True, "band": "PRESERVED", "band_idx": 0,
                           "condition": 92.0, "boundaries": [25.5, 75.6]}}
    p05._condition_rater_chip("ZZZ999_NOT_A_STUDY", result)


def test_rater_chip_silent_for_non_cohort_study() -> None:
    at = AppTest.from_function(_rater_chip_offcohort).run()
    assert not at.exception
    assert "consensus grade" not in _body(at)          # fail-silent: no grade, no chip


def _formula_reveal() -> None:
    from labeling_app.assessment_pages import p05_assessment_view as p05
    art = p05._condition_models()
    opts = p05._condition_options()
    result = {"headline": {"available": True, "band": "PRESERVED", "band_idx": 0,
                           "condition": 92.0, "boundaries": [25.5, 75.6]}}
    p05._condition_formula(art, opts, result, 100)


def test_formula_reveal_shows_formula_agreement_and_claim_boundary() -> None:
    at = AppTest.from_function(_formula_reveal).run()
    assert not at.exception
    body = _body(at)
    assert "η" in body and "β" in body                 # the actual formula
    # The OOF agreement for model C of the SERVED version, read from the artifact rather than typed.
    # This line said "0.813" (v1.2) and broke the moment ACTIVE_OLD_VERSION moved to v1.3 (0.811) —
    # which is the test working, but a hardcoded literal only ever catches the change once and then
    # has to be re-typed. Pin the RELATIONSHIP: the panel shows the served model's own metric.
    import json
    from preflight_helpers.cohort_placement import OLD, options_path_for_source
    opts = json.load(open(options_path_for_source(OLD), encoding="utf-8"))
    qwk = next(v["qwk"] for k, v in opts["boss_table"].items() if k.startswith("C"))
    assert "Agreement" in body and f"{qwk:.3f}" in body
    assert "diagnosis" in body                         # the enforced claim boundary


def _condition_block_no_score() -> None:
    from labeling_app.assessment_pages import p05_assessment_view as p05
    result = {"headline": {"available": False},
              "axes": [{"col": "fat_to_depth_q50", "name": "Fat over muscle",
                        "percentile": 54.0}]}
    p05._condition_breakdown(result, {})
    p05._condition_rater_chip("ACL762", result)        # cohort study, but no model score


def test_score_block_all_or_nothing_when_no_model_score() -> None:
    at = AppTest.from_function(_condition_block_no_score).run()
    assert not at.exception
    body = _body(at)
    assert "What drives the score" not in body          # breakdown gated on headline
    assert "consensus grade" not in body               # chip gated on headline too


def test_breakdown_rows_quote_the_same_sex_percentile_the_card_leads_with() -> None:
    """2026-09-02: the card leads with the sex-matched placement (Akadir 2026-08-04) but the
    breakdown quoted the OVERALL rank under a caption promising 'the same percentile as the
    cards'. Now: sex-matched first, overall only as the fallback, and the pool is named."""
    from labeling_app.assessment_pages import p05_assessment_view as p05
    result = {"axes": [
        {"col": "full_thickness_q50_mm", "name": "Muscle size", "contribution": 0.8},
        {"col": "fat_to_depth_q50", "name": "Fat over muscle", "contribution": 1.1},
        {"col": "int_cv_within", "name": "Fascicular contrast", "contribution": 1.1}]}
    run_data = {"placements": {
        "full_thickness_q50_mm": {"overall": {"pctile": 3.4}, "sex_matched": {"pctile": 6.1}},
        "fat_to_depth_q50": {"overall": {"pctile": 84.0}, "sex_matched": {"pctile": 73.5}},
        "int_cv_within": {"overall": {"pctile": 4.8}, "sex_matched": None}}}   # thin sex pool
    rows = {r["clinical"]: r for r in p05._breakdown_rows(result, run_data)}
    assert rows["Size"]["pct"] == 6.1 and rows["Size"]["pool"] == "same-sex peers"
    assert rows["Fat"]["pct"] == 73.5 and rows["Fat"]["pool"] == "same-sex peers"
    assert rows["Tissue"]["pct"] == 4.8 and rows["Tissue"]["pool"] == "all"     # honest fallback
    assert rows["Size"]["name"] == "Muscle size"        # the served recipe's axis, not STAR

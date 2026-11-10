"""The staged RF-thickness triple-screen (Akadir 2026-07-12)."""
from preflight_helpers.rf_reliability import resolve_rf_source


def test_stage1_agreement_uses_finer_center_rf_and_trusts_int_cv():
    r = resolve_rf_source(max_femur=17.0, max_center=17.5, max_sweep=16.0)  # femur≈center within 10%
    assert r["source"] == "center_rf"
    assert r["int_cv_trusted"] is True
    assert r["reliability"] == "high"


def test_disagreement_sweep_corroborates_femur_center_is_outlier():
    r = resolve_rf_source(max_femur=20.0, max_center=15.0, max_sweep=19.5)  # center 25% small, sweep≈femur
    assert r["source"] == "femur"
    assert r["int_cv_trusted"] is False
    assert r["reliability"] == "medium"
    assert "shallow-clip" in r["note"]                 # center smaller => the clip signature
    assert "corroborates femur" in r["note"]


def test_disagreement_sweep_sides_center_is_weaker():
    r = resolve_rf_source(max_femur=20.0, max_center=15.0, max_sweep=15.5)  # both center shots agree, differ from femur
    assert r["source"] == "femur"
    assert r["int_cv_trusted"] is False
    assert r["reliability"] == "low"


def test_all_three_disagree_is_low_confidence():
    r = resolve_rf_source(max_femur=20.0, max_center=15.0, max_sweep=25.0)
    assert r["source"] == "femur" and r["reliability"] == "low" and r["int_cv_trusted"] is False


def test_center_larger_than_femur_is_flagged_atypical():
    r = resolve_rf_source(max_femur=15.0, max_center=20.0, max_sweep=15.2)  # center bigger => not the clip
    assert r["source"] == "femur"
    assert "atypical fault" in r["note"]


def test_no_femur_falls_back_to_sweep_legacy():
    r = resolve_rf_source(max_femur=None, max_center=None, max_sweep=16.0)
    assert r["source"] == "sweep" and r["reliability"] == "legacy-sweep" and r["int_cv_trusted"] is True


def test_spread_reported_when_two_present():
    r = resolve_rf_source(max_femur=20.0, max_center=15.0, max_sweep=None)
    assert r["spread"] == 5.0


# --- the texture audit gate (p04 three-way review) ---
from preflight_helpers.rf_reliability import resolve_texture_state

_AGREE = {"int_cv_trusted": True}
_DISAGREE = {"int_cv_trusted": False}


def test_agreement_texture_is_verified_no_gate():
    t = resolve_texture_state(_AGREE)
    assert t["state"] == "verified" and t["int_cv_usable"] is True and t["needs_review"] is False


def test_unreviewed_discrepancy_fires_the_gate_but_stays_usable_flagged():
    t = resolve_texture_state(_DISAGREE, user_choice=None)
    assert t["state"] == "pending_review" and t["needs_review"] is True
    assert t["int_cv_usable"] is True                      # usable-but-flagged until resolved


def test_keep_and_fix_make_texture_usable_no_further_review():
    for ch in ("keep", "fix"):
        t = resolve_texture_state(_DISAGREE, user_choice=ch)
        assert t["int_cv_usable"] is True and t["needs_review"] is False
        assert t["state"] in ("reviewed_kept", "fixed")


def test_withhold_drops_texture_from_the_score_mode_B():
    t = resolve_texture_state(_DISAGREE, user_choice="withhold")
    assert t["state"] == "withheld"
    assert t["int_cv_usable"] is False                     # score computed texture-withheld, not faked
    assert t["needs_review"] is False


# --- the live stamp (what route_study_aggregate attaches) ---
from preflight_helpers.rf_reliability import rf_reliability_stamp


def test_stamp_agreement_verified_no_gate():
    s = rf_reliability_stamp({"full_max_thickness_mm": 17.0}, {"full_max_thickness_mm": 17.5},
                             {"full_max_thickness_mm": 16.0})
    assert s["decision"]["source"] == "center_rf"
    assert s["texture"]["state"] == "verified" and s["texture"]["needs_review"] is False


def test_stamp_disagreement_fires_gate_femur_anchored():
    s = rf_reliability_stamp({"full_max_thickness_mm": 20.0}, {"full_max_thickness_mm": 15.0},
                             {"full_max_thickness_mm": 19.5})
    assert s["decision"]["source"] == "femur"
    assert s["texture"]["needs_review"] is True


def test_stamp_withhold_choice_drops_texture():
    s = rf_reliability_stamp({"full_max_thickness_mm": 20.0}, {"full_max_thickness_mm": 15.0},
                             {"full_max_thickness_mm": 19.5}, user_choice="withhold")
    assert s["texture"]["int_cv_usable"] is False


def test_stamp_tolerates_missing_modalities():
    s = rf_reliability_stamp(None, {"full_max_thickness_mm": 16.0}, None)  # no femur
    assert s["decision"]["reliability"] == "legacy-sweep" or s["decision"]["source"] == "sweep"


def test_withhold_makes_the_real_headline_unavailable_never_faked():
    """#17 end-to-end contract on the FROZEN v1.1 model: a withheld texture (int_cv_usable=False) means
    the score is computed with int_cv DROPPED, and the headline model (which needs int_cv) reports
    UNAVAILABLE — an honest missing axis, never a faked score. 'keep' leaves int_cv in (score present)."""
    from preflight_helpers import condition_model
    from preflight_helpers.cohort_placement import model_path_for_source, OLD
    art = condition_model.load(model_path_for_source(OLD))
    feats = {"star_ratio": 1.6, "fat_to_depth_q50": 0.12, "int_cv_within": 0.75}
    dec = rf_reliability_stamp({"full_max_thickness_mm": 20.0}, {"full_max_thickness_mm": 15.0},
                               {"full_max_thickness_mm": 19.5})["decision"]        # disagreement
    withheld = resolve_texture_state(dec, user_choice="withhold")
    kept = resolve_texture_state(dec, user_choice="keep")
    assert withheld["int_cv_usable"] is False and kept["int_cv_usable"] is True

    def headline_available(usable):                        # mirrors _score_condition's drop
        f = feats if usable else {k: v for k, v in feats.items() if k != "int_cv_within"}
        return condition_model.score_patient(art, f)["headline"]["available"]
    assert headline_available(kept["int_cv_usable"]) is True         # keep -> score present
    assert headline_available(withheld["int_cv_usable"]) is False    # withhold -> unavailable (not faked)


def test_explicit_withhold_beats_a_trusted_decision():
    """Red-team 2026-07-14: an explicit human 'withhold' must ALWAYS suppress the texture, even if the
    (independently recomputed) decision says int_cv IS trusted. The p04 gate and the p05 stamp can
    aggregate the thickness slightly differently (median vs anchor), so a recomputed 'agree' must NEVER
    resurrect a texture the operator withheld — the honesty guarantee."""
    trusted = {"int_cv_trusted": True, "source": "center_rf", "reliability": "high", "spread": None,
               "note": "views agree"}
    t = resolve_texture_state(trusted, user_choice="withhold")
    assert t["state"] == "withheld" and t["int_cv_usable"] is False   # withhold wins over trusted
    assert resolve_texture_state(trusted)["state"] == "verified"      # sanity: no choice -> verified
    # a legacy single-sweep decision is identifiable (source=='sweep'/'legacy-sweep') so p05 suppresses
    # its chip — only ONE view exists, "three views agree" would be false.
    legacy = resolve_rf_source(None, None, 16.0)
    assert legacy["source"] == "sweep" or legacy["reliability"] == "legacy-sweep"

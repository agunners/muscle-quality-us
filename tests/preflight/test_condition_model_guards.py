"""condition_model guards (Akadir 2026-07-16): (1) the staleness hash — a frozen model
must be able to tell it was fit on a different measurement-code version than what is live
(so a measurement fix can't silently keep serving old-code numbers); (2) the no-femur
headline fallback — a study with no femur (no STAR) still gets a headline via model A."""
from preflight_helpers import condition_model as cm


# -- staleness hash ----------------------------------------------------------
def test_staleness_flags_legacy_artifact():
    assert cm.staleness({"models": {}})["stale"] is True          # no stamp -> stale


def test_staleness_in_sync_when_hash_matches():
    art = {"measurement_code_hash": cm.measurement_code_hash()}
    assert cm.staleness(art)["stale"] is False


def test_staleness_flags_changed_code():
    st = cm.staleness({"measurement_code_hash": "0000deadbeef0000"})
    assert st["stale"] is True and st["current_hash"] == cm.measurement_code_hash()


def test_hash_is_stable():
    assert cm.measurement_code_hash() == cm.measurement_code_hash()


# -- no-femur headline fallback (uses the production artifact's fitted A / C) --
_A_ONLY = {"full_thickness_q50_mm": 20.0, "fat_to_depth_q50": 0.2, "int_cv_within": 0.5, "star_ratio": None}
_WITH_STAR = {**_A_ONLY, "star_ratio": 1.5}


# The SERVED recipe is A (raw millimetres) since 2026-08-04; every artifact is still FIT with C, and
# the artifacts are left untouched as historical records. So the served choice must come from
# `served_headline`, not from the frozen `art["headline"]`, and A must not read as a "fallback".

def test_a_study_with_no_femur_still_scores_on_the_served_recipe():
    """A needs no femur, which is one of the reasons it is served."""
    r = cm.score_patient(cm.load(), _A_ONLY)
    assert r["headline_option"] == "A"
    assert r["headline_is_fallback"] is False, "A is the served headline, not a fallback"
    assert r["headline"] is not None and r["headline"]["available"]


def test_the_served_recipe_is_used_even_though_the_artifact_was_frozen_with_C():
    art = cm.load()
    assert art["headline"] == "C", "the frozen record must stay untouched"
    assert cm.served_headline(art) == "A"
    r = cm.score_patient(art, _WITH_STAR)
    assert r["headline_option"] == "A" and r["headline_is_fallback"] is False


def test_it_falls_back_to_C_when_the_served_recipe_cannot_score():
    """A reads full thickness; a study without it but WITH star_ratio still gets a headline."""
    feats = dict(_WITH_STAR)
    feats["full_thickness_q50_mm"] = None
    r = cm.score_patient(cm.load(), feats)
    assert r["headline_option"] == "C" and r["headline_is_fallback"] is True
    assert r["headline"]["available"]


def test_no_scoreable_data_headline_unavailable():
    # both C and A lose an input -> no fallback lands; the headline stays an honest
    # UNAVAILABLE dict (not a fake, not None -> consumers reading ["available"] don't crash).
    r = cm.score_patient(cm.load(), {"star_ratio": None, "full_thickness_q50_mm": None,
                                     "fat_to_depth_q50": 0.2, "int_cv_within": 0.5})
    assert r["headline_option"] is None and r["headline_is_fallback"] is False
    assert r["headline"] is not None and r["headline"]["available"] is False


# -- freeze stamps the current hash (so a real re-base clears the stale flag) --
def test_freeze_stamps_current_hash(tmp_path):
    out = tmp_path / "m.json"
    cm.freeze_all(csv=cm.CSV, out=str(out))
    art = cm.load(str(out))
    assert art["measurement_code_hash"] == cm.measurement_code_hash()
    assert cm.staleness(art)["stale"] is False


# -- the breakdown axes follow the recipe that PRODUCED the headline (2026-09-02) --
# Every artifact froze `axis_box` on its fit-time headline (C), so the axes used to list STAR
# (muscle ÷ BMI) as "Size" even though the served recipe A scores raw thickness — the page's
# breakdown showed a size percentile that never enters the score, and the real size push was
# invisible (Akadir 2026-09-02).
def test_axes_follow_the_served_recipe_not_the_frozen_axis_box():
    art = cm.load()
    r = cm.score_patient(art, _WITH_STAR)                 # A scores -> A's axes
    assert r["headline_option"] == "A"
    assert [a["col"] for a in r["axes"]] == art["models"]["A"]["cols"]
    assert all(a["contribution"] is not None for a in r["axes"]), "every served axis carries its push"
    names = [a["name"] for a in r["axes"]]
    assert "Muscle size" in names and "Muscle vs body size" not in names


def test_axes_follow_the_fallback_recipe_when_A_cannot_score():
    art = cm.load()
    feats = dict(_WITH_STAR)
    feats["full_thickness_q50_mm"] = None
    r = cm.score_patient(art, feats)
    assert r["headline_option"] == "C"                    # fell back -> C's axes explain C's score
    assert [a["col"] for a in r["axes"]] == art["models"]["C"]["cols"]
    assert all(a["contribution"] is not None for a in r["axes"])


def test_axis_percentile_is_none_outside_the_frozen_axis_box_never_invented():
    art = cm.load()
    r = cm.score_patient(art, _WITH_STAR)
    by_col = {a["col"]: a for a in r["axes"]}
    assert by_col["full_thickness_q50_mm"]["percentile"] is None      # not in axis_box (frozen on C)
    assert by_col["int_cv_within"]["percentile"] is not None          # shared column keeps its deciles

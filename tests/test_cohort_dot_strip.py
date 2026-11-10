"""Small-n honesty: cohort_points yields the actual study values only for a
cohort small enough to need them, and the p05 box-figure overlays them as a
dot-strip so a box resting on a handful is not read as a smooth distribution
(E3, Akadir 2026-06-04)."""
from preflight_helpers.cohort_placement import cohort_points


def test_cohort_points_threshold():
    # the overall cohort (~260) exceeds a small max_n -> None (the box suffices);
    # a large max_n returns the full value list.
    big = cohort_points("rf_thickness_mm", None, None, max_n=10000)
    assert big["overall"] is not None and len(big["overall"]) > 100
    small = cohort_points("rf_thickness_mm", None, None, max_n=5)
    assert small["overall"] is None


def test_cohort_points_missing_feature():
    out = cohort_points("does_not_exist", 29, "M")
    assert out == {"overall": None, "age_matched": None, "sex_matched": None}


def test_dot_strip_overlay_adds_trace():
    from labeling_app.assessment_pages.p05_assessment_view import _cohort_box_figure
    box = {"overall": {"n": 8, "p10": 1, "p25": 2, "p50": 3, "p75": 4, "p90": 5,
                       "min": 1, "max": 5},
           "age_matched": None, "sex_matched": None}
    pl = {"overall": {"pctile": 50}, "age_matched": {}, "sex_matched": {}}
    fig_no = _cohort_box_figure("rf_thickness_mm", 3.0, box, pl, None)
    fig_yes = _cohort_box_figure(
        "rf_thickness_mm", 3.0, box, pl,
        {"overall": [1, 2, 2, 3, 3, 4, 4, 5], "age_matched": None, "sex_matched": None})
    assert len(fig_yes.data) > len(fig_no.data), "dot-strip must add a scatter trace"

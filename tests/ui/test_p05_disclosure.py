"""Slice B — the p05 "pooled from N of M; K set aside" receipt.

Mounts `_pooled_disclosure` in isolation (AppTest.from_function) to assert the
conditional-render contract: QUIET on a fully-clean study (no caption, no
expander), LOUD when a frame was set aside (the headline line + a collapsed
"why" expander carrying the run_what_changed rows, no raw codes / no "§").
"""
from __future__ import annotations

from streamlit.testing.v1 import AppTest


# AppTest.from_function re-execs the function body in an isolated namespace, so
# each script function is fully self-contained (no module-level helpers).

def _disclosure_clean() -> None:
    from labeling_app.assessment_pages import p05_assessment_view as p05
    measures = ("star_ratio", "full_thickness_q50_mm", "int_cv_within",
                "rf_thickness_mm", "qmt_mm", "visible_rf_area_mm2",
                "muscle_fat_ratio")
    valid = {m: "valid" for m in measures}

    def frame(fb):
        return {"frame_base": fb, "is_anchor": False,
                "gates": {"usable": True, "use_for_analysis": True},
                "image_quality": {"image_quality_tier_per_measurement": dict(valid),
                                  "image_quality_aggregate_tier": "valid",
                                  "image_quality_validity_flags": [],
                                  "image_quality_pending_calibration_flags": [],
                                  "image_quality_detector_metadata": {}}}

    run = {"aggregate": {"_qa_tier": dict(valid),
                         "_rf_consistency": {"excluded": []},
                         "qmt_provenance": "standard"},
           "frame_features": [frame(f"S{i}") for i in range(5)]}
    p05._pooled_disclosure(run)


def _disclosure_outlier() -> None:
    from labeling_app.assessment_pages import p05_assessment_view as p05
    pm = {"rf_thickness_mm": "valid"}

    def frame(fb):
        return {"frame_base": fb, "is_anchor": False,
                "gates": {"usable": True, "use_for_analysis": True},
                "image_quality": {"image_quality_tier_per_measurement": dict(pm),
                                  "image_quality_aggregate_tier": "valid",
                                  "image_quality_validity_flags": [],
                                  "image_quality_pending_calibration_flags": [],
                                  "image_quality_detector_metadata": {}}}

    bases = [f"20260101_00004000{i}" for i in range(9)]
    run = {"aggregate": {"_qa_tier": dict(pm),
                         "_rf_consistency": {"active": True,
                                             "excluded": ["20260101_000040002"],
                                             "scores": {"20260101_000040002": 4.1},
                                             "median_mm": 12.4,
                                             "trimmed_median_mm": 12.1},
                         "qmt_provenance": "standard"},
           "frame_features": [frame(b) for b in bases]}
    p05._pooled_disclosure(run)


def test_pooled_disclosure_quiet_on_clean() -> None:
    at = AppTest.from_function(_disclosure_clean).run()
    assert not at.exception
    assert len(at.caption) == 0           # quiet — no headline line
    assert len(at.expander) == 0          # quiet — no expander


def test_pooled_disclosure_loud_when_a_frame_is_set_aside() -> None:
    at = AppTest.from_function(_disclosure_outlier).run()
    assert not at.exception
    assert len(at.expander) == 1                                   # the "why" drilldown
    assert any("set aside" in c.value for c in at.caption)         # the headline line
    # the outlier row reaches the expander with its Z evidence; no raw code / no "§"
    body = (" ".join(m.value for m in at.markdown)
            + " ".join(c.value for c in at.caption))
    assert "Z=4.1" in body
    assert "§" not in body
    assert "rf_thickness_mm" not in body                           # prose, not raw codes

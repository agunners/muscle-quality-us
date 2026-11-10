"""The picker femur badge must never say "no femur" when a femur was segmented.

Tier 3 = "QMT not measurable" splits two ways: no dominant femur at all, or a
femur IS present but the muscle path (RF/VI) is missing. The first is "no femur";
the second must NOT be (the bone is plainly there). See the .757 review,
2026-06-04 — `project_femur_757_is_wrong_site`.
"""
from labeling_app.assessment_pages.p03_frame_picker import _FEMUR_BADGE, _femur_badge


def test_tiers_1_and_2_unchanged():
    assert _femur_badge({"tier": 1}) == _FEMUR_BADGE[1]
    assert _femur_badge({"tier": 2}) == _FEMUR_BADGE[2]


def test_tier3_dominant_empty_reads_no_femur():
    badge = _femur_badge({"tier": 3, "femur_present": False})
    assert badge == _FEMUR_BADGE[3]
    assert "no femur" in badge


def test_tier3_femur_present_is_not_mislabelled_no_femur():
    badge = _femur_badge({"tier": 3, "femur_present": True})
    assert "no femur" not in badge
    assert "not measurable" in badge


def test_missing_femur_present_defaults_to_no_femur():
    # Back-compat: a scan record without the new field falls back to "no femur".
    assert _femur_badge({"tier": 3}) == _FEMUR_BADGE[3]

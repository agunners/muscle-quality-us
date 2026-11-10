"""Guard the 3-phase picker's backward-compat invariant: the SWEEP keeps its legacy session +
widget keys (so session_sig, the scorer, and the existing AppTest helpers are unchanged), while
femur/center-RF get their own per-modality keys.
"""
from labeling_app.assessment_pages import p03_frame_picker as p03


def test_sweep_keeps_legacy_keys():
    assert p03._picked_key("sweep") == "picked_frames"
    assert p03._pick_btn_key("sweep", "20260101_120000001") == "pick_20260101_120000001"


def test_femur_rf_get_their_own_keys():
    assert p03._picked_key("femur") == "picked_frames__femur"
    assert p03._picked_key("rf") == "picked_frames__rf"
    assert p03._pick_btn_key("femur", "X") == "pick_femur_X"
    assert p03._pick_btn_key("rf", "X") == "pick_rf_X"

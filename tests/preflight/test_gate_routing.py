"""The femur/center-RF keep/drop gate (Akadir 2026-06-25): only the frames the operator keeps
usable + for-analysis feed the routed aggregate. This tests the pure filter `_gate_kept` — the
torch-heavy segmentation path (compute_modality_aggregate) is exercised end-to-end by the live app.
"""
from preflight_helpers.feature_routing import _gate_kept

A, B, C = "frame_a", "frame_b", "frame_c"


def test_no_gates_keeps_all():
    assert _gate_kept([A, B, C], None) == [A, B, C]
    assert _gate_kept([A, B, C], {}) == [A, B, C]


def test_usable_no_drops_frame():
    gm = {B: {"usable": False, "use_for_analysis": False}}
    assert _gate_kept([A, B, C], gm) == [A, C]


def test_use_for_analysis_no_drops_frame():
    gm = {C: {"usable": True, "use_for_analysis": False}}
    assert _gate_kept([A, B, C], gm) == [A, B]


def test_undecided_none_keeps_frame():
    # An undecided (None) gate defaults to KEEP — matches the pre-gate behavior.
    gm = {A: {"usable": None, "use_for_analysis": None}}
    assert _gate_kept([A, B], gm) == [A, B]


def test_all_dropped_returns_empty():
    gm = {A: {"usable": False}, B: {"use_for_analysis": False}}
    assert _gate_kept([A, B], gm) == []

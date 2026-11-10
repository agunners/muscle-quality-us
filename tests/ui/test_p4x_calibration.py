"""L3a AppTest — the p4x calibration capture sub-route (subsystem D).

Walks to p04, enters p4x, picks motion_blur, labels a cell through the full
blind-then-reveal flow, and asserts the label lands in the (tmp-redirected)
calibration ledger with the right schema. The cohort data path (score table /
frame loader / detector grade) is mocked so the test is fast + deterministic;
the persistence path (label_store → env.DATA_ROOT) is the REAL code.
"""
from __future__ import annotations

import numpy as np
import pytest

from preflight_helpers import env
from calibration import label_store
from calibration.candidate_selector import Candidate
from labeling_app.assessment_pages import p4x_calibration_capture as p4x


def _widget(at, kind, key):
    pool = list(getattr(at, kind))
    try:
        pool += list(getattr(at.sidebar, kind))
    except Exception:
        pass
    for el in pool:
        if getattr(el, "key", None) == key:
            return el
    raise KeyError(f"{kind}[{key!r}] not found; have "
                   f"{[getattr(e, 'key', None) for e in pool]}")


def _has(at, kind, key) -> bool:
    try:
        _widget(at, kind, key)
        return True
    except KeyError:
        return False


def _walk_to_p04(at, n_pick=2):
    at.run()
    _widget(at, "button", "pick_AGZ855").click(); at.run()
    _widget(at, "button", "load_model_continue").click(); at.run()
    frames = list(at.session_state["available_frame_bases"])
    for fb in frames[:n_pick]:
        _widget(at, "button", f"pick_{fb}").click(); at.run()
    _widget(at, "button", "seg_continue").click(); at.run()
    assert at.session_state["phase"] == 3
    return frames


def _mock_p4x_data(monkeypatch, *, mb_score=420.0):
    """Stub the cohort data path so p4x serves a synthetic motion_blur cell."""
    cands = [
        Candidate("AGZ855", "20260127_151046404", "motion_blur", mb_score, 50.0, "spread"),
        Candidate("AGZ855", "20260127_151047882", "motion_blur", 90.0, 5.0, "spread"),
    ]
    monkeypatch.setattr(p4x.candidate_selector, "load_score_table",
                        lambda *a, **k: object())
    monkeypatch.setattr(p4x.candidate_selector, "select_candidates",
                        lambda *a, **k: cands)

    # p4x now grades on the PREDICTED mask via the live segmenter
    # (`_predicted_grade`) — mock that heavy seam, not the old load_frame +
    # grade_frame path. The shape mirrors what the real function returns.
    def _fake_predicted_grade(study_code, frame_base):
        pred = np.zeros((128, 128), np.uint8)
        pred[30:70, 30:90] = 1          # RF
        pred[20:30, 30:90] = 5          # fascia
        final = np.full((128, 128), 120, np.uint8)
        return {
            "pred": pred,
            "final_u8": final,
            "confidence_map": None,
            "metadata": {"motion_blur": {"fascia_band_tenengrad": 420.0,
                                         "pending_calibration": True,
                                         "n_fascia_px": 400}},
            "fired": set(),
        }
    monkeypatch.setattr(p4x, "_predicted_grade", _fake_predicted_grade)


def test_p4x_label_persists_through_blind_then_reveal(ui_app, monkeypatch) -> None:
    at = ui_app
    _mock_p4x_data(monkeypatch)
    _walk_to_p04(at)

    # Enter the calibration sub-route from p04 → the detector picker shows
    # (motion_blur is one of the 7), which proves p4x is active.
    _widget(at, "button", "p4x_enter").click(); at.run()
    assert _has(at, "button", "p4x_pick_motion_blur")

    # Pick motion_blur → a labelling cell renders.
    _widget(at, "button", "p4x_pick_motion_blur").click(); at.run()
    assert at.session_state["p4x_detector"] == "motion_blur"
    # Blind stage — the three answer buttons are present; no score revealed yet.
    assert _has(at, "button", "p4x_ans_yes")
    assert _has(at, "button", "p4x_ans_no")
    assert _has(at, "button", "p4x_ans_unsure")

    # Answer Yes → the reveal/confirm stage replaces the answer buttons.
    _widget(at, "button", "p4x_ans_yes").click(); at.run()
    assert at.session_state["p4x_answer_pending"]["label"] == "yes"
    assert _has(at, "button", "p4x_confirm_keep")
    assert not _has(at, "button", "p4x_ans_yes")     # blind buttons gone

    # Keep the answer → the label is committed to the ledger and the cell advances.
    _widget(at, "button", "p4x_confirm_keep").click(); at.run()

    rows = label_store.load_labels(env.DATA_ROOT / "calibration_labels.jsonl")
    assert len(rows) == 1
    r = rows[0]
    assert r["detector"] == "motion_blur"
    assert r["label"] == "yes"
    assert r["label_source"] == "p4x"
    assert r["score_revealed_at"] == "after"
    assert r["post_reveal_confirmation"] == "kept"
    assert r["study_code"] == "AGZ855"
    assert r["response_time_ms"] is not None
    # anchor B — the full metadata snapshot, not just the scalar
    assert r["detector_metadata_at_label_time"]["fascia_band_tenengrad"] == 420.0
    # PHI-clean
    assert "study_dirname" not in r and "mask_path" not in r


def test_p4x_change_mind_records_the_flip(ui_app, monkeypatch) -> None:
    at = ui_app
    _mock_p4x_data(monkeypatch)
    _walk_to_p04(at)
    _widget(at, "button", "p4x_enter").click(); at.run()
    _widget(at, "button", "p4x_pick_motion_blur").click(); at.run()

    # Answer No, then change to the opposite (Yes) on the reveal step.
    _widget(at, "button", "p4x_ans_no").click(); at.run()
    _widget(at, "button", "p4x_confirm_opp").click(); at.run()

    r = label_store.load_labels(env.DATA_ROOT / "calibration_labels.jsonl")[0]
    assert r["label"] == "yes"                       # flipped from no
    assert r["post_reveal_confirmation"] == "changed_to_opposite"


def test_p4x_exit_returns_to_review(ui_app, monkeypatch) -> None:
    at = ui_app
    _mock_p4x_data(monkeypatch)
    _walk_to_p04(at)
    _widget(at, "button", "p4x_enter").click(); at.run()
    assert _has(at, "button", "p4x_pick_motion_blur")    # in the picker
    assert "p4x_active" in at.session_state              # sub-route active
    # Exit from the picker → back to p04 (phase still 3). The real signal of
    # exit is the cleared flag; AppTest leaves straggler keyed buttons in the
    # tree after a sub-route transition (memory feedback_apptest_conditional_widgets),
    # so we assert on session_state, not on widget-absence — the real app
    # rebuilds the tree cleanly on st.rerun.
    _widget(at, "button", "p4x_exit_picker").click(); at.run()
    assert "p4x_active" not in at.session_state          # exit cleared the flag
    assert at.session_state["phase"] == 3
    assert _has(at, "button", "p4x_enter")               # p04 surface back


def test_p4x_silence_records_nothing(ui_app, monkeypatch) -> None:
    """Entering a cell and NOT answering writes no label (anchor B — silence is
    not assent)."""
    at = ui_app
    _mock_p4x_data(monkeypatch)
    _walk_to_p04(at)
    _widget(at, "button", "p4x_enter").click(); at.run()
    _widget(at, "button", "p4x_pick_motion_blur").click(); at.run()
    # No answer click. The ledger stays empty.
    rows = label_store.load_labels(env.DATA_ROOT / "calibration_labels.jsonl")
    assert rows == []

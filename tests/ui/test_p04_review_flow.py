"""L3a AppTest — the p04 review page driven through the REAL app flow.

This is the harness I should have used the first time, and it is the project's
own established pattern (see test_p4x_calibration.py::_walk_to_p04): AppTest mounts
the real assessment_app.py, the `ui_app` fixture mocks ONLY the GPU segmenter +
pipeline seams, and the test drives the real widgets with .click()/.set_value()
+ .run() and asserts on behaviour ACROSS RERUNS — which is the only way to catch
an interaction bug like a gate that snaps back on the next refresh.

Guards the 2026-06-09 dark-frame gate regressions on the real page:
  * the review page does not raise StreamlitAPIException ("Phase 4 error") when a
    dark frame's gate radio exists before the bottom-of-page dark check, and
  * the operator can mark a dark frame Usable=Yes and it STICKS across a rerun
    (an earlier coupling forced it back to No every render).
"""
from __future__ import annotations

import numpy as np

from labeling_app.assessment_pages import p04_frame_review


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


def _dark_ensure_predicted_masks(picked):
    """Like conftest._fake_ensure_predicted_masks, but the grayscale is BLACK so
    the RF ROI is near-zero -> dark_clip_high fires -> the dark-image check is
    active on p04 (the conftest default uses mid-grey 120, which is not dark)."""
    import streamlit as st
    pm = st.session_state.setdefault("predicted_masks", {})
    fc = st.session_state.setdefault("final_u8_cache", {})
    m = np.zeros((128, 128), dtype=np.uint8)
    m[30:70, 30:90] = 1                       # LABEL_RF
    for fb in picked:
        pm.setdefault(fb, m.copy())
        fc.setdefault(fb, np.zeros((128, 128), dtype=np.uint8))   # black -> dark


def test_dark_frame_review_drops_the_questionnaire_and_gate_is_free(ui_app, monkeypatch):
    # Make the picked frames dark (dark_clip_high fires).
    monkeypatch.setattr(p04_frame_review, "_ensure_predicted_masks",
                        _dark_ensure_predicted_masks)
    at = ui_app
    frames = _walk_to_p04(at)
    fb = frames[0]
    assert not at.exception

    # The bottom-of-page "Dark-image check" questionnaire was removed 2026-06-09
    # (dark labeling lives in p4x) — no darkov_* radio appears anywhere, even for a
    # dark frame. This guards the consolidation: the detector has ONE labeling home.
    assert not any(str(getattr(r, "key", "")).startswith("darkov_") for r in at.radio), \
        "the removed Dark-image check questionnaire reappeared on the review page"

    # The Usable gate is the operator's and sticks across a rerun (nothing couples
    # a dark frame back to No anymore — that coupling is gone with the questionnaire).
    _widget(at, "radio", f"g1_{fb}").set_value("Yes"); at.run()
    assert not at.exception
    assert at.session_state[f"g1_{fb}"] == "Yes"

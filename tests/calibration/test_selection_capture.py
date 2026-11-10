"""Slice (b) — the in-flow selection capture (label_source=selection)."""
from __future__ import annotations

import json

from calibration.label_store import load_labels, record_selection_label


def test_record_selection_label_round_trips(tmp_path) -> None:
    ledger = tmp_path / "calibration_labels.jsonl"
    iq = {
        "image_quality_validity_flags": ["dark_clip_high"],
        "image_quality_detector_metadata": {"dark_clip_high": {"roi_dark_fraction": 0.08}},
        "image_quality_contract_version": "v0.2",
    }
    lid = record_selection_label(
        study_code="QQF025", frame_base="20260102_105416284",
        detector="dark_clip_high", label="yes", image_quality=iq, path=ledger)
    rows = load_labels(ledger)
    assert len(rows) == 1
    r = rows[0]
    assert r["label_id"] == lid
    assert r["label"] == "yes"
    assert r["label_source"] == "selection"
    assert r["score_revealed_at"] == "before"              # anchored (saw the call)
    assert r["post_reveal_confirmation"] == "not_applicable"
    assert r["detector"] == "dark_clip_high"
    assert r["study_code"] == "QQF025"
    assert r["detector_metadata_at_label_time"] == {"roi_dark_fraction": 0.08}
    # PHI: study_code + frame_base only, never a folder path.
    blob = json.dumps(r)
    assert "goruntuler" not in blob and "RawImages" not in blob


def test_record_selection_label_rejects_bad_label(tmp_path) -> None:
    import pytest
    with pytest.raises(ValueError):
        record_selection_label(
            study_code="X", frame_base="f", detector="d", label="maybe",
            image_quality={}, path=tmp_path / "l.jsonl")


import pytest
from calibration.label_store import selection_label_for


@pytest.mark.parametrize("usable,ufa,cited,expected", [
    (False, False, True,  "yes"),    # set aside AND cited this detector
    (True,  True,  False, "no"),     # kept for analysis despite the flag
    (True,  True,  True,  "no"),     # kept (cited is moot when kept)
    (False, False, False, None),     # set aside for a DIFFERENT reason — skip
    (None,  None,  True,  None),     # undecided — skip
])
def test_selection_label_for_signals(usable, ufa, cited, expected) -> None:
    assert selection_label_for(
        usable=usable, use_for_analysis=ufa, detector_cited=cited) == expected


def test_record_selection_label_carries_notes(tmp_path) -> None:
    ledger = tmp_path / "calibration_labels.jsonl"
    record_selection_label(
        study_code="X", frame_base="f", detector="dark_clip_high", label="yes",
        image_quality={}, notes="dark_override:accept", path=ledger)
    rows = load_labels(ledger)
    assert rows[0]["notes"] == "dark_override:accept"

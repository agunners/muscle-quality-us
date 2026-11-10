"""Subsystem E — the calibration label ledger (append-only, fcntl-locked,
PHI-clean). Tests use a tmp path so the real ledger is never touched."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from calibration.label_store import (
    Label, RELABEL_INTERVAL_DAYS, append_label, due_relabels, labelled_keys,
    load_labels,
)


def _label(**kw) -> Label:
    base = dict(
        study_code="AGZ855", frame_base="20260127_151046404", detector="motion_blur",
        label="yes", score_at_label_time=23.7,
        detector_metadata_at_label_time={"fascia_band_tenengrad": 23.7, "n_fascia_px": 412},
        threshold_at_label_time=None, label_source="p4x",
        score_revealed_at="after", post_reveal_confirmation="kept",
        response_time_ms=4200, visual_aids_shown=("raw", "segmentation_contour"),
        contract_version="v0.2", spec_version_at_label_time="v0.1",
        operator_id_hash="abc123",
    )
    base.update(kw)
    return Label(**base)


def test_label_round_trips_through_jsonl(tmp_path) -> None:
    p = tmp_path / "labels.jsonl"
    lbl = _label()
    append_label(lbl, p)
    rows = load_labels(p)
    assert len(rows) == 1
    r = rows[0]
    assert r["study_code"] == "AGZ855"
    assert r["label"] == "yes"
    assert r["detector_metadata_at_label_time"]["fascia_band_tenengrad"] == 23.7
    assert r["label_id"] == lbl.label_id


def test_full_metadata_snapshot_is_persisted(tmp_path) -> None:
    """Anchor B — the FULL detector metadata dict is recorded, not just the
    headline scalar. A vessel_shadow label keeps darkest_column + median."""
    p = tmp_path / "labels.jsonl"
    append_label(_label(
        detector="vessel_shadow_in_roi",
        detector_metadata_at_label_time={
            "darkest_column_mean": 38.0, "median_column_mean": 92.0,
            "darkest_column": 187, "n_roi_columns": 60},
    ), p)
    md = load_labels(p)[0]["detector_metadata_at_label_time"]
    assert md["darkest_column"] == 187 and md["median_column_mean"] == 92.0


def test_invalid_label_value_rejected() -> None:
    for bad in ("maybe", "Yes", "", "skip"):
        with pytest.raises(ValueError):
            _label(label=bad)


def test_invalid_source_and_reveal_rejected() -> None:
    with pytest.raises(ValueError):
        _label(label_source="campaign")
    with pytest.raises(ValueError):
        _label(score_revealed_at="midway")
    with pytest.raises(ValueError):
        _label(post_reveal_confirmation="flip")


def test_phi_clean_keys_only(tmp_path) -> None:
    p = tmp_path / "labels.jsonl"
    append_label(_label(), p)
    r = load_labels(p)[0]
    assert "study_dirname" not in r and "mask_path" not in r and "_phi" not in r
    # study_code + frame_base are the only frame identifiers
    assert r["study_code"] and r["frame_base"]


def test_relabel_target_gets_a_due_date(tmp_path) -> None:
    """A test-retest target is stamped with relabel_due_utc = +14 days."""
    p = tmp_path / "labels.jsonl"
    lbl = _label(relabel_target=True)
    append_label(lbl, p)
    r = load_labels(p)[0]
    assert r["relabel_due_utc"] is not None
    due = datetime.fromisoformat(r["relabel_due_utc"])
    made = datetime.fromisoformat(r["timestamp_utc"])
    assert abs((due - made).days - RELABEL_INTERVAL_DAYS) <= 1


def test_non_target_has_no_due_date(tmp_path) -> None:
    p = tmp_path / "labels.jsonl"
    append_label(_label(relabel_target=False), p)
    assert load_labels(p)[0]["relabel_due_utc"] is None


def test_labelled_keys_excludes_only_p4x(tmp_path) -> None:
    """labelled_keys (feeds the selector's already_labelled) counts p4x labels;
    a cohort_pass label on the same frame doesn't gate the p4x queue."""
    p = tmp_path / "labels.jsonl"
    append_label(_label(frame_base="f_p4x", label_source="p4x"), p)
    append_label(_label(frame_base="f_cohort", label_source="cohort_pass"), p)
    keys = labelled_keys("motion_blur", p)
    assert ("AGZ855", "f_p4x") in keys
    assert ("AGZ855", "f_cohort") not in keys


def test_due_relabels_surfaces_overdue_unrelabelled(tmp_path) -> None:
    """A relabel_target whose +14d has passed, not yet re-labelled, is due."""
    p = tmp_path / "labels.jsonl"
    old = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat(timespec="seconds")
    lbl = _label(relabel_target=True, timestamp_utc=old)
    # __post_init__ set relabel_due_utc to old+14d, which is in the past
    append_label(lbl, p)
    due = due_relabels(p)
    assert len(due) == 1 and due[0]["frame_base"] == "20260127_151046404"


def test_due_relabels_clears_after_relabel(tmp_path) -> None:
    """Once the relabel is recorded (relabel_of points at the original), it is
    no longer surfaced as due."""
    p = tmp_path / "labels.jsonl"
    old = (datetime.now(timezone.utc) - timedelta(days=20)).isoformat(timespec="seconds")
    orig = _label(relabel_target=True, timestamp_utc=old)
    append_label(orig, p)
    append_label(_label(relabel_of=orig.label_id), p)   # the re-label
    assert due_relabels(p) == []

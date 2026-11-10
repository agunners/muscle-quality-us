"""The release surface's action layer — checkbox eligibility, entry building, ledger append.

Contracts under test (docs/withhold_release_spec.md §3): an action is offered only where it
means something (untrusted texture with NO verdict; views-disagree size with NO marker); a
texture release is always value-bound (run number + binding snapshot, or NO entry); everything
appended is a signed feature-grain entry the freeze and the trust strip already consume.
"""
import json

import pytest

from preflight_helpers import flag_kill_ledger as fkl
from preflight_helpers import release_actions as ra


def _landmark(tmp_path, uid_dirname="teststudy_dir", femur=25.0, center=20.0, sweep=25.0):
    p = tmp_path / "landmark.json"
    p.write_text(json.dumps([{"study": uid_dirname, "vals": {
        "femur": {"full_max_thickness_mm": femur},
        "rf": {"full_max_thickness_mm": center},
        "sweep": {"full_max_thickness_mm": sweep}}}]))
    return p


def _uid(dirname="teststudy_dir"):
    import hashlib
    return "NR" + hashlib.sha256(dirname.encode()).hexdigest()[:8].upper()


UNTRUSTED = {"source": "femur", "int_cv_trusted": False}
VALIDATED = {"source": "center_rf", "int_cv_trusted": True}


def test_pending_actions_eligibility(tmp_path):
    led = tmp_path / "ledger.jsonl"
    uid = _uid()
    a = ra.pending_actions(uid, UNTRUSTED, path=led)
    assert a["texture_release"] and a["size_confirm"] and a["texture_verdict"] is None
    # validated study: nothing to release, nothing to confirm
    a = ra.pending_actions(uid, VALIDATED, path=led)
    assert not a["texture_release"] and not a["size_confirm"]
    # a recorded clear (or kill) removes the checkbox — verdicts supersede via the tool only
    fkl.append({"scope": "feature", "status": "cleared", "study_uid": uid,
                "fields": ["int_cv_within"], "decided_by": "T"}, led)
    a = ra.pending_actions(uid, UNTRUSTED, path=led)
    assert not a["texture_release"] and a["texture_verdict"] == "cleared"
    fkl.append({"scope": "feature", "status": "cleared", "study_uid": uid,
                "fields": [ra.SIZE_CONFIRM_FIELD], "decided_by": "T"}, led)
    a = ra.pending_actions(uid, UNTRUSTED, path=led)
    assert not a["size_confirm"] and a["size_confirmed"]


def test_build_entries_value_bound(tmp_path):
    lm = _landmark(tmp_path)
    rec = {"study_uid": _uid(), "run_id": "AKAT_1_abc",
           "aggregate": {"int_cv_within": 0.471},
           "texture_refine": {"erosion_px": 10, "band": [0.49, 1.0]}}
    es = ra.build_entries(rec, release_texture=True, confirm_size=True, by="T", landmark_path=lm)
    assert len(es) == 2
    tex = next(e for e in es if "int_cv_within" in e["fields"])
    assert tex["released_value"] == pytest.approx(0.471) and tex["source_run_id"] == "AKAT_1_abc"
    assert tex["values"]["max_femur_mm"] == 25.0        # the stale-binding snapshot
    assert "erosion_px" in tex["evidence"]              # the refine params ride the evidence
    size = next(e for e in es if ra.SIZE_CONFIRM_FIELD in e["fields"])
    assert size["status"] == "cleared" and size["source_run_id"] == "AKAT_1_abc"
    # no int_cv in the run -> NO value-less release; missing snapshot -> none either
    assert ra.build_entries({"study_uid": _uid(), "run_id": "r", "aggregate": {}},
                            release_texture=True, confirm_size=False, by="T",
                            landmark_path=lm) == []
    assert ra.build_entries(rec, release_texture=True, confirm_size=False, by="T",
                            landmark_path=tmp_path / "absent.json") == []


def test_record_release_round_trips_through_the_ledger(tmp_path):
    led = tmp_path / "ledger.jsonl"
    lm = _landmark(tmp_path)
    rec = {"study_uid": _uid(), "run_id": "AKAT_2_def", "aggregate": {"int_cv_within": 0.55}}
    n = ra.record_release(rec, release_texture=True, confirm_size=True, by="T",
                          path=led, landmark_path=lm)
    assert n == 2
    clears = fkl.feature_clears(led)[_uid()]
    assert clears["int_cv_within"]["released_value"] == pytest.approx(0.55)
    assert clears["int_cv_within"]["source_run_id"] == "AKAT_2_def"
    assert ra.SIZE_CONFIRM_FIELD in clears


def test_contribution_entry_requires_per_frame_features():
    rec = {"study_uid": _uid(), "run_id": "AKAT_3_ghi",
           "routed_picks": {"femur": {"frame_features": [{"rf_thickness_mm": 20.0}]},
                            "rf": {"frame_features": []}}}
    c = ra.build_contribution(rec, by="T")
    assert c["scope"] == "contribution" and c["status"] == "active"
    assert c["fields"] == ["AKAT_3_ghi"] and c["roles"] == ["femur"]
    # a record from before per-frame persistence offers nothing to pool -> no entry
    assert ra.build_contribution({"study_uid": _uid(), "run_id": "r",
                                  "routed_picks": {"femur": {"frame_features": []}}},
                                 by="T") is None


def test_contributions_reader_coexists_and_revokes(tmp_path):
    led = tmp_path / "ledger.jsonl"
    uid = _uid()
    for rid in ("run_A", "run_B"):
        fkl.append({"scope": "contribution", "status": "active", "study_uid": uid,
                    "fields": [rid], "source_run_id": rid, "decided_by": "T"}, led)
    assert {e["source_run_id"] for e in fkl.contributions(led)[uid]} == {"run_A", "run_B"}
    fkl.append({"scope": "contribution", "status": "revoked", "study_uid": uid,
                "fields": ["run_A"], "source_run_id": "run_A", "decided_by": "T"}, led)
    assert {e["source_run_id"] for e in fkl.contributions(led)[uid]} == {"run_B"}


def test_evidence_rows_compare_canonical_and_run(monkeypatch):
    from labeling_app.assessment_pages import trust_strip
    monkeypatch.setattr(trust_strip, "_canonical_features",
                        lambda uid: {"rf_thickness_mm": "20.0", "int_cv_within": "0.700"})
    rows = trust_strip.evidence_rows(
        {"aggregate": {"rf_thickness_mm": 21.0, "int_cv_within": 0.47}}, "NRX")
    by = {r[0]: r for r in rows}
    assert by["RF thickness (mm)"][1:] == ("20.00", "21.00", "+5.0%")
    assert by["Fascicular contrast (int_cv)"][3] == "-32.9%"
    assert by["QMT (mm)"][1] == "—"                     # absent stays an honest dash

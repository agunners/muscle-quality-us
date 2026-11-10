"""Build-Step 1 — Phase-4 structured drop-reason capture schema.

The QA contract (docs/rf_qa_canonical_plan_v0_1.md § 7.4) restructures the
Phase-4 gate record: the single free-text `drop_reason` becomes structured
`drop_reason_codes` + `drop_reason_other`, plus a `qa_overrides` slot (§ 7.6,
populated from Build-Step 2). These tests guard the *schema* — that
`GateAnswers` carries the new fields with safe defaults, that every existing
construction site still works, and that a persisted gates dict survives a JSON
round-trip with the reason codes intact (the ledger is append-only JSON,
§ 7.0).
"""
from __future__ import annotations

import json

from preflight_helpers.run_pipeline import GateAnswers


def test_gate_answers_defaults_are_empty_and_safe() -> None:
    g = GateAnswers()
    assert g.drop_reason is None            # legacy free-text field still present
    assert g.drop_reason_codes == ()
    assert g.drop_reason_other == ""
    assert g.qa_overrides == {}


def test_qa_overrides_default_is_not_shared() -> None:
    """qa_overrides uses a default_factory — two GateAnswers must not alias the
    same dict (the classic mutable-default bug)."""
    a, b = GateAnswers(), GateAnswers()
    a.qa_overrides["qmt_mm"] = {"human_decision": "use_anyway"}
    assert b.qa_overrides == {}


def test_legacy_construction_still_works() -> None:
    """Every existing GateAnswers(...) call site passes only the four original
    fields — the three new fields must default in cleanly."""
    g = GateAnswers(usable=False, segmentation_perfect=False,
                    use_for_analysis=False, drop_reason="blurry")
    assert g.drop_reason == "blurry"
    assert g.drop_reason_codes == ()
    assert g.qa_overrides == {}


def _persisted_gates(g: GateAnswers) -> dict:
    """The gates dict run_pipeline persists into the ledger record
    (run_pipeline.py:368-373 / :407-412) — kept in step with that shape."""
    return {
        "usable": g.usable,
        "segmentation_perfect": g.segmentation_perfect,
        "use_for_analysis": g.use_for_analysis,
        "drop_reason": g.drop_reason,
        "drop_reason_codes": list(g.drop_reason_codes),
        "drop_reason_other": g.drop_reason_other,
        "qa_overrides": g.qa_overrides,
    }


def test_persisted_gates_json_round_trip() -> None:
    g = GateAnswers(
        usable=False, segmentation_perfect=False, use_for_analysis=False,
        drop_reason_codes=("femur_out_of_frame", "motion_blur"),
        drop_reason_other="probe cable shadow",
    )
    back = json.loads(json.dumps(_persisted_gates(g)))   # the ledger is JSON on disk
    assert back["drop_reason_codes"] == ["femur_out_of_frame", "motion_blur"]
    assert back["drop_reason_other"] == "probe cable shadow"
    assert back["qa_overrides"] == {}
    assert back["drop_reason"] is None                   # legacy field, null for new runs


def test_persisted_gates_accepted_frame_has_empty_capture() -> None:
    """An accepted frame carries no drop reasons — it is a calibration
    negative by virtue of being accepted (§ 11.2 step 2)."""
    g = GateAnswers(usable=True, segmentation_perfect=True, use_for_analysis=True)
    record = _persisted_gates(g)
    assert record["drop_reason_codes"] == []
    assert record["drop_reason_other"] == ""

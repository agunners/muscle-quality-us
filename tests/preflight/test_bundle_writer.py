"""Bundle writer — build_record / append_record split + write_run round-trip.

B2.1 split persistence from construction so the N4 lifecycle can build a
record, stash it as a draft, and only later promote it to the ledger without
re-running the pipeline. The invariants:
  * `build_record` does NO file I/O — it just returns (run_id, record).
  * every record carries `frame_set` + `lifecycle` (N4/N5 fields).
  * `write_run` stays a thin build+append wrapper (preflight CLI unaffected).
"""
from __future__ import annotations

import json
from pathlib import Path

from preflight_helpers import bundle_writer, env


_COMMON = dict(
    study_path=Path("/tmp/fake"),
    selected_frames=[{
        "frame_base": "20260101_120000000", "is_anchor": True,
        "selected_by": "test", "selected_at_utc": "2026-01-01T00:00:00Z",
        "gates": {"usable": True, "segmentation_perfect": True, "use_for_analysis": True},
    }],
    model_bundle={"pkl_path": "/x.pkl", "sha256": "deadbeef", "fold": 4},
    palette_path=Path("/x/palette.json"),
    palette_sha256="cafe",
    resolved_class_ids={"muscle_class_id": 1, "femur_class_id": 7, "erosion_px": 3},
    runtime_bridge=["/p/q"],
    frame_features=[{"frame_base": "20260101_120000000", "rf_thickness_mm": 20.0,
                     "int_cv_within": 0.5}],
    aggregate={"rf_thickness_mm": 20.0, "int_cv_within": 0.5, "_n_frames_in_aggregate": 1},
    placements={"rf_thickness_mm": {"overall": {"n": 262, "pctile": 70.0}}},
)

_FRAME_SET = {
    "signature": "abc123", "frame_bases_sorted": ["20260101_120000000"],
    "n_analysis_frames": 1, "bundle_sha256": "deadbeef", "erosion_px": 3,
}


# ---- build_record: construction, no I/O -----------------------------------

def test_build_record_does_no_io(tmp_env) -> None:
    run_id, record = bundle_writer.build_record(
        study_code="TST001", study_meta={"study_code": "TST001"}, mode="test",
        frame_set=_FRAME_SET, **_COMMON)
    assert run_id == record["run_id"]
    assert record["study_code"] == "TST001"
    # Nothing written anywhere.
    assert not env.RUNS_JSONL.exists()
    assert not env.RUNS_DIR.exists() or not any(env.RUNS_DIR.iterdir())
    assert not env.DRAFTS_DIR.exists() or not any(env.DRAFTS_DIR.iterdir())


def test_build_record_carries_frame_set_and_lifecycle(tmp_env) -> None:
    _id, record = bundle_writer.build_record(
        study_code="TST001", study_meta={"study_code": "TST001"}, mode="test",
        frame_set=_FRAME_SET, lifecycle="draft", **_COMMON)
    assert record["frame_set"] == _FRAME_SET
    assert record["lifecycle"] == "draft"
    # Default lifecycle is "finalized" (the pre-N4, direct-to-ledger shape).
    _id2, record2 = bundle_writer.build_record(
        study_code="TST001", study_meta={"study_code": "TST001"}, mode="test", **_COMMON)
    assert record2["lifecycle"] == "finalized"
    assert record2["frame_set"] is None


def test_new_run_id_is_collision_safe() -> None:
    # Rapid calls within the same wall-clock second must still be unique —
    # the N4 stale-cache regression branches two runs back-to-back.
    ids = {bundle_writer.new_run_id("X") for _ in range(50)}
    assert len(ids) == 50


# ---- append_record: persistence -------------------------------------------

def test_append_record_persists_built_record(tmp_env) -> None:
    _id, record = bundle_writer.build_record(
        study_code="TST001", study_meta={"study_code": "TST001"}, mode="test",
        frame_set=_FRAME_SET, **_COMMON)
    run_id, run_dir = bundle_writer.append_record(record)
    assert (run_dir / "record.json").exists()
    runs = bundle_writer.iter_runs()
    assert len(runs) == 1 and runs[0]["run_id"] == run_id
    assert runs[0]["frame_set"]["signature"] == "abc123"


# ---- write_run: the thin wrapper (preflight CLI path) ---------------------

def test_write_and_read_back_run(tmp_env) -> None:
    run_id, run_dir = bundle_writer.write_run(
        study_code="TST001",
        study_meta={"study_code": "TST001", "age": 50, "sex": "M", "bmi": 25.0},
        mode="test", frame_set=_FRAME_SET, **_COMMON)

    record_path = run_dir / "record.json"
    assert record_path.exists()
    with open(record_path) as f:
        rec = json.load(f)
    assert rec["study_code"] == "TST001"
    assert rec["resolved_class_ids"]["femur_class_id"] == 7   # pinned override
    assert rec["frame_features"][0]["rf_thickness_mm"] == 20.0
    assert rec["lifecycle"] == "finalized"                    # write_run → ledger
    assert rec["frame_set"]["signature"] == "abc123"

    runs = bundle_writer.iter_runs()
    assert len(runs) == 1
    assert runs[0]["run_id"] == run_id


def test_runs_for_study_filter(tmp_env) -> None:
    for code in ("AAA", "BBB"):
        bundle_writer.write_run(
            study_code=code, study_path=Path("/x"), study_meta={"study_code": code},
            selected_frames=[], model_bundle={}, palette_path=Path("/p"),
            palette_sha256="", resolved_class_ids={}, runtime_bridge=[],
            frame_features=[], aggregate={}, placements={}, mode="t")
    assert len(bundle_writer.runs_for_study("AAA")) == 1
    assert len(bundle_writer.runs_for_study("BBB")) == 1
    assert len(bundle_writer.runs_for_study("CCC")) == 0

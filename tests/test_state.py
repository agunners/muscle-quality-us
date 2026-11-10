"""Tests for state.py: SQLite runtime persistence and session helpers."""
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from labeling_app.state import (
    DECISION_LOG_COLUMNS,
    append_row,
    build_row,
    consume_next_anchor,
    export_log,
    get_anchorable_set,
    get_effective_decisions,
    get_labeled_set,
    initialize_runtime_storage,
    load_anchor_queue,
    load_log,
    now_utc_str,
    register_session,
    reset_runtime_storage,
    seed_anchor_queue,
    select_anchor_candidates,
    session_summary,
)


def _minimal_row(**overrides) -> dict:
    row = {col: None for col in DECISION_LOG_COLUMNS}
    row.update(
        {
            "operator_id": "test_op",
            "saved_at_utc": now_utc_str(),
            "session_id": "session_1",
            "review_visit_id": "visit_1",
            "study_code": "AAA001",
            "decision_status": "completed",
            "quality_label": "PRESERVED",
            "quality_gate_pass": True,
            "review_save_index": 1,
            "study_annotation_index": 1,
            "session_study_number": 1,
            "review_context": "fresh",
        }
    )
    row.update(overrides)
    return row


class TestRuntimeInitialization:

    def test_missing_db_returns_empty_df(self, tmp_path):
        db_path = str(tmp_path / "labeling.db")
        log_df = load_log(db_path)
        assert isinstance(log_df, pd.DataFrame)
        assert len(log_df) == 0
        assert list(log_df.columns) == DECISION_LOG_COLUMNS

    def test_invalid_existing_file_is_replaced_with_fresh_db(self, tmp_path):
        db_path = tmp_path / "labeling.db"
        db_path.write_text("not a sqlite database", encoding="utf-8")

        initialize_runtime_storage(str(db_path))
        log_df = load_log(str(db_path))

        assert len(log_df) == 0
        assert list(log_df.columns) == DECISION_LOG_COLUMNS


class TestAppendAndExport:

    def test_append_row_round_trips_from_sqlite(self, tmp_path):
        db_path = str(tmp_path / "labeling.db")
        append_row(db_path, _minimal_row())
        df = load_log(db_path)
        assert len(df) == 1
        assert list(df.columns) == DECISION_LOG_COLUMNS
        assert df.iloc[0]["study_code"] == "AAA001"

    def test_export_log_writes_csv_header_and_rows(self, tmp_path):
        db_path = str(tmp_path / "labeling.db")
        export_path = tmp_path / "decision_log.csv"
        append_row(db_path, _minimal_row(), str(export_path))

        exported = pd.read_csv(export_path)
        assert list(exported.columns) == DECISION_LOG_COLUMNS
        assert exported.iloc[0]["study_code"] == "AAA001"
        assert "study_dirname" not in exported.columns

    def test_reset_runtime_storage_removes_db_and_export(self, tmp_path):
        db_path = tmp_path / "labeling.db"
        export_path = tmp_path / "decision_log.csv"
        append_row(str(db_path), _minimal_row(), str(export_path))

        reset_runtime_storage(str(db_path), str(export_path))

        assert not db_path.exists()
        assert not export_path.exists()

    def test_append_row_requires_study_code(self, tmp_path):
        db_path = str(tmp_path / "labeling.db")
        row = _minimal_row(study_code=None)
        try:
            append_row(db_path, row)
        except ValueError as exc:
            assert "study_code" in str(exc)
        else:
            raise AssertionError("append_row should reject empty study_code values")


class TestBuildRow:

    def test_copies_subject_context_and_serializes_qmt_columns(self):
        study_row = pd.Series(
            {
                "study_code": "AAA001",
                "gender": "F",
                "bmi": 31.2,
                "other_comorbidities": "ra+hipotiroidi",
                "comorbidities": "ht+dm",
                "age": 55,
                "height_cm": 157,
                "weight_kg": 115,
            }
        )
        row = build_row(
            operator_id="test_op",
            session_id="session_1",
            review_visit_id="visit_1",
            study_row=study_row,
            metrics={
                "qmt_annotation_columns": [10, 22, 34],
                "qmt_validity_flags": ["outside_central_zone", "low_muscle_occupancy"],
                "qmt_guidance_status": "proxy_due_to_geometry",
            },
            quality_label="PRESERVED",
            quality_rationale="adequate image quality",
            decision_status="completed",
            quality_gate_pass=True,
            preliminary_label="PRESERVED",
            preliminary_impression="looks preserved",
            center_frame_base="frame_1",
            representative_frame_index=1,
            representative_frame_reason="temporal_center_standard_qmt",
            frame_notes=["lateral", "mid", "medial", "extra"],
            is_anchor=False,
            prior_label=None,
            review_context="fresh",
            review_save_index=1,
            study_annotation_index=1,
            session_study_number=1,
            change_reason=None,
        )
        assert row["study_code"] == "AAA001"
        assert row["sex"] == "F"
        assert row["other_comorbidities"] == "ra+hipotiroidi"
        assert row["qmt_annotation_columns"] == "10|22|34"
        assert row["qmt_validity_flags"] == "outside_central_zone|low_muscle_occupancy"
        assert row["qmt_guidance_status"] == "proxy_due_to_geometry"
        assert row["frame_note_4"] == "extra"


class TestDecisionViews:

    def test_latest_effective_decision_wins(self, tmp_path):
        db_path = str(tmp_path / "labeling.db")
        append_row(
            db_path,
            _minimal_row(
                quality_label="POOR",
                saved_at_utc="2026-01-01T10:00:00+00:00",
            ),
        )
        append_row(
            db_path,
            _minimal_row(
                quality_label="PRESERVED",
                saved_at_utc="2026-01-01T11:00:00+00:00",
            ),
        )
        df = load_log(db_path)
        effective = get_effective_decisions(df)
        assert ("test_op", "AAA001") in effective
        assert str(effective[("test_op", "AAA001")]["quality_label"]) == "PRESERVED"

    def test_labeled_set_includes_quality_failed(self, tmp_path):
        db_path = str(tmp_path / "labeling.db")
        append_row(db_path, _minimal_row(study_code="AAA001", decision_status="completed"))
        append_row(db_path, _minimal_row(study_code="BBB002", decision_status="skipped"))
        append_row(db_path, _minimal_row(study_code="CCC003", decision_status="quality_failed"))
        append_row(db_path, _minimal_row(study_code="DDD004", decision_status="fresh"))
        df = load_log(db_path)
        labeled = get_labeled_set(df, "test_op")
        assert labeled == {"AAA001", "BBB002", "CCC003"}

    def test_anchorable_set_uses_latest_completed_labels_only(self, tmp_path):
        db_path = str(tmp_path / "labeling.db")
        append_row(db_path, _minimal_row(study_code="AAA001", decision_status="completed", quality_label="POOR"))
        append_row(db_path, _minimal_row(study_code="BBB002", decision_status="skipped", quality_label=None))
        append_row(db_path, _minimal_row(study_code="CCC003", decision_status="quality_failed", quality_label=None))
        append_row(
            db_path,
            _minimal_row(
                study_code="DDD004",
                decision_status="completed",
                quality_label="PRESERVED",
                saved_at_utc="2026-01-01T10:00:00+00:00",
            ),
        )
        append_row(
            db_path,
            _minimal_row(
                study_code="DDD004",
                decision_status="skipped",
                quality_label=None,
                saved_at_utc="2026-01-01T11:00:00+00:00",
            ),
        )
        df = load_log(db_path)
        anchorable = get_anchorable_set(df, "test_op")
        assert anchorable == {"AAA001"}


class TestSessionSummary:

    def test_count_uses_first_save_per_review_visit(self, tmp_path):
        db_path = str(tmp_path / "labeling.db")
        session_start = datetime.now(timezone.utc)
        append_row(
            db_path,
            _minimal_row(
                study_code="AAA001",
                review_visit_id="visit_1",
                review_save_index=1,
                quality_label="PRESERVED",
            ),
        )
        append_row(
            db_path,
            _minimal_row(
                study_code="AAA001",
                review_visit_id="visit_1",
                review_save_index=2,
                quality_label="POOR",
            ),
        )
        append_row(
            db_path,
            _minimal_row(
                study_code="BBB002",
                review_visit_id="visit_2",
                review_save_index=1,
                quality_label="INTERMEDIATE",
            ),
        )
        df = load_log(db_path)
        summary = session_summary(df, "test_op", "session_1", session_start)
        assert summary["count"] == 2
        assert summary["label_distribution"]["PRESERVED"] == 1
        assert summary["label_distribution"]["INTERMEDIATE"] == 1
        assert "POOR" not in summary["label_distribution"]

    def test_empty_log_summary(self):
        df = pd.DataFrame(columns=DECISION_LOG_COLUMNS)
        summary = session_summary(df, "test_op", "session_1", datetime.now(timezone.utc))
        assert summary["count"] == 0
        assert summary["label_distribution"] == {}


class TestAnchors:

    def test_select_anchor_candidates_is_deterministic_and_completed_only(self, tmp_path):
        db_path = str(tmp_path / "labeling.db")
        for idx in range(8):
            append_row(
                db_path,
                _minimal_row(
                    study_code=f"AAA{idx:03d}",
                    quality_label="PRESERVED",
                    decision_status="completed",
                ),
            )
        append_row(
            db_path,
            _minimal_row(
                study_code="ZZZ999",
                quality_label=None,
                decision_status="quality_failed",
            ),
        )
        df = load_log(db_path)
        anchors_1 = select_anchor_candidates(df, "test_op", n=5, session_count=2)
        anchors_2 = select_anchor_candidates(df, "test_op", n=5, session_count=2)
        assert anchors_1 == anchors_2
        assert len(anchors_1) == 5
        assert "ZZZ999" not in anchors_1

    def test_seed_and_consume_anchor_queue(self, tmp_path):
        db_path = str(tmp_path / "labeling.db")
        register_session(db_path, "session_1", "test_op", now_utc_str())
        seeded = seed_anchor_queue(
            db_path,
            "session_1",
            "test_op",
            ["AAA001", "BBB002", "CCC003"],
            seed=42,
            limit=2,
        )
        queued = load_anchor_queue(db_path, "session_1", "test_op")
        assert queued == seeded
        assert len(queued) == 2

    def test_reseed_after_consumption_appends_queue_order_without_collision(self, tmp_path):
        db_path = str(tmp_path / "labeling.db")
        register_session(db_path, "session_1", "test_op", now_utc_str())
        seed_anchor_queue(
            db_path,
            "session_1",
            "test_op",
            ["AAA001", "BBB002", "CCC003"],
            seed=42,
            limit=2,
        )
        while consume_next_anchor(db_path, "session_1", "test_op") is not None:
            pass

        reseeded = seed_anchor_queue(
            db_path,
            "session_1",
            "test_op",
            ["AAA001", "BBB002", "CCC003", "DDD004"],
            seed=99,
            limit=2,
        )

        assert reseeded
        assert load_anchor_queue(db_path, "session_1", "test_op") == reseeded

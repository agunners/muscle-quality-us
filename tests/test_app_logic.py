"""Pure helper tests for app.py."""
from types import SimpleNamespace

import pandas as pd

import labeling_app.app as app_module


class TestPhaseValidation:

    def test_phase1_submit_disabled_until_grade_and_text_valid(self):
        assert app_module._phase1_submit_disabled("(not set)", "clear borders") is True
        assert app_module._phase1_submit_disabled("PRESERVED", "short") is True
        assert app_module._phase1_submit_disabled("PRESERVED", "clear borders seen") is False

    def test_phase2_save_disabled_without_rationale(self):
        assert app_module._phase2_save_disabled("PRESERVED", "PRESERVED", "short", None) is True
        assert (
            app_module._phase2_save_disabled(
                "PRESERVED",
                "PRESERVED",
                "adequate rationale text",
                None,
            )
            is False
        )

    def test_phase2_save_disabled_when_change_reason_missing(self):
        assert (
            app_module._phase2_save_disabled(
                "PRESERVED",
                "POOR",
                "adequate rationale text",
                "",
            )
            is True
        )
        assert (
            app_module._phase2_save_disabled(
                "PRESERVED",
                "POOR",
                "adequate rationale text",
                "metrics changed my initial view",
            )
            is False
        )

    def test_can_return_to_phase1_only_after_first_save(self):
        assert app_module._can_return_to_phase1(0) is False
        assert app_module._can_return_to_phase1(1) is True


class TestReviewContext:

    def test_anchor_context_wins(self):
        prior_row = pd.Series({"quality_label": "POOR"})
        assert app_module._derive_review_context(True, prior_row) == "anchor"

    def test_revisit_when_prior_exists(self):
        prior_row = pd.Series({"quality_label": "POOR"})
        assert app_module._derive_review_context(False, prior_row) == "revisit"

    def test_fresh_when_no_prior_exists(self):
        assert app_module._derive_review_context(False, None) == "fresh"


class TestAnnotationIndex:

    def test_next_study_annotation_index_counts_existing_rows(self):
        log_df = pd.DataFrame(
            [
                {"operator_id": "me", "study_code": "AAA001"},
                {"operator_id": "me", "study_code": "AAA001"},
                {"operator_id": "other", "study_code": "AAA001"},
            ]
        )
        assert app_module._next_study_annotation_index(log_df, "me", "AAA001") == 3
        assert app_module._next_study_annotation_index(log_df, "me", "BBB002") == 1


class TestReviewVisitReset:

    def test_set_current_study_clears_review_state(self, monkeypatch):
        dummy_streamlit = SimpleNamespace(
            session_state={
                "review_visit_token": 4,
                "prelim_label": "POOR",
                "prelim_impression": "old text",
                "review_save_count": 2,
            }
        )
        monkeypatch.setattr(app_module, "st", dummy_streamlit)

        app_module._set_current_study("AAA001", is_anchor=True, prior_label="PRESERVED")

        assert dummy_streamlit.session_state["current_study_code"] == "AAA001"
        assert dummy_streamlit.session_state["phase"] == 1
        assert dummy_streamlit.session_state["prelim_label"] is None
        assert dummy_streamlit.session_state["prelim_impression"] == ""
        assert dummy_streamlit.session_state["is_anchor_study"] is True
        assert dummy_streamlit.session_state["prior_label_for_anchor"] == "PRESERVED"
        assert dummy_streamlit.session_state["review_context"] is None
        assert dummy_streamlit.session_state["review_save_count"] == 0
        assert dummy_streamlit.session_state["review_visit_token"] == 5


class TestQueueFiltering:

    def test_filters_use_effective_decision_status(self):
        manifest = pd.DataFrame({"study_code": ["AAA001", "BBB002", "CCC003", "DDD004"]})
        effective = {
            ("me", "AAA001"): pd.Series({"decision_status": "completed"}),
            ("me", "BBB002"): pd.Series({"decision_status": "skipped"}),
            ("me", "CCC003"): pd.Series({"decision_status": "quality_failed"}),
            ("other", "DDD004"): pd.Series({"decision_status": "completed"}),
        }

        assert app_module._select_study_from_manifest(manifest, effective, "me", "Completed") == ["AAA001"]
        assert app_module._select_study_from_manifest(manifest, effective, "me", "Skipped") == ["BBB002"]
        assert app_module._select_study_from_manifest(manifest, effective, "me", "Quality failed") == ["CCC003"]
        assert app_module._select_study_from_manifest(manifest, effective, "me", "Unreviewed") == ["DDD004"]
        assert app_module._select_study_from_manifest(manifest, effective, "me", "All") == [
            "AAA001",
            "BBB002",
            "CCC003",
            "DDD004",
        ]

    def test_empty_filter_result_stays_empty(self):
        manifest = pd.DataFrame({"study_code": ["AAA001"]})
        effective = {("me", "AAA001"): pd.Series({"decision_status": "completed"})}

        assert app_module._select_study_from_manifest(manifest, effective, "me", "Skipped") == []


class TestQmtDisplayHelpers:

    def test_proxy_display_names_use_star_labels(self):
        qmt_name, qmt_mm_name, star_name = app_module._qmt_display_names("femur_supported_proxy")
        assert qmt_name == "QMT* (px)"
        assert qmt_mm_name == "QMT* (mm) [proxy]"
        assert star_name == "STAR* ratio"

    def test_standard_display_names_keep_plain_labels(self):
        qmt_name, qmt_mm_name, star_name = app_module._qmt_display_names("standard_qmt")
        assert qmt_name == "QMT (px)"
        assert qmt_mm_name == "QMT (mm) [standard]"
        assert star_name == "STAR ratio"

    def test_guidance_text_uses_highest_priority_flag(self):
        text = app_module._qmt_guidance_text(
            {
                "qmt_guidance_status": "proxy_due_to_geometry",
                "qmt_validity_flags": ["outside_central_zone", "low_muscle_occupancy"],
            }
        )
        assert "central femur zone" in text

    def test_provenance_badge_contains_human_label(self):
        badge = app_module._qmt_provenance_badge("no_femur_proxy")
        assert "No-Femur Proxy" in badge


class TestConfigPathHelpers:

    def test_get_config_path_uses_env_override(self, monkeypatch, tmp_path):
        cfg_path = tmp_path / "server.yaml"
        cfg_path.write_text("", encoding="utf-8")

        monkeypatch.setenv("MUSCLE_QUALITY_CONFIG_PATH", str(cfg_path))
        assert app_module._get_config_path() == cfg_path.resolve()

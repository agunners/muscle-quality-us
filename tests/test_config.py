"""Tests for config.py."""
from pathlib import Path

from labeling_app.config import CONFIG_ENV_VAR, LabelingConfig, load_config, resolve_config_path


def test_default_anchor_interval_is_15():
    cfg = LabelingConfig(transport_root="transport")
    assert cfg.anchor_interval == 15


def test_default_study_code_map_path_is_set():
    cfg = LabelingConfig(transport_root="transport")
    assert cfg.study_code_map_path.endswith("configs/study_codes.csv")


def test_yaml_loads_clean_slate_runtime_paths():
    cfg_path = Path(__file__).parent.parent / "configs" / "labeling.yaml"
    cfg = load_config(cfg_path)
    assert cfg.anchor_interval == 15
    assert cfg.session_warning_threshold == 60
    assert cfg.runtime_db_path.endswith("runtime/labeling.db")
    assert cfg.decision_export_path.endswith("exports/decision_log.csv")


def test_load_config_resolves_relative_paths_against_yaml_dir(tmp_path):
    transport_root = tmp_path / "transport"
    transport_root.mkdir()
    (transport_root / "transport_metadata.csv").write_text("study_dirname\nstudy_a\n", encoding="utf-8")
    palette_path = tmp_path / "label_palette.json"
    palette_path.write_text('{"background":{"rgba":[0,0,0,0]},"labels":[]}', encoding="utf-8")
    (tmp_path / "study_codes.csv").write_text(
        "study_code,study_dirname,numeric_study_id,annotator,session,relative_frame_dir,created_at_utc\n"
        "ABC123,study_a,0001,ann,sess,ann/sess/study_a,2026-01-01T00:00:00+00:00\n",
        encoding="utf-8",
    )
    cfg_path = tmp_path / "labeling.yaml"
    cfg_path.write_text(
        "transport_root: transport\n"
        "study_code_map_path: study_codes.csv\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)

    assert cfg.transport_root == str(transport_root.resolve())
    assert cfg.study_code_map_path == str((tmp_path / "study_codes.csv").resolve())
    assert cfg.palette_path == str(palette_path.resolve())


def test_load_config_accepts_explicit_palette_path(tmp_path):
    transport_root = tmp_path / "transport"
    transport_root.mkdir()
    (transport_root / "transport_metadata.csv").write_text("study_dirname\nstudy_a\n", encoding="utf-8")

    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    palette_path = assets_dir / "palette.json"
    palette_path.write_text('{"background":{"rgba":[0,0,0,0]},"labels":[]}', encoding="utf-8")

    code_map_path = tmp_path / "study_codes.csv"
    code_map_path.write_text(
        "study_code,study_dirname,numeric_study_id,annotator,session,relative_frame_dir,created_at_utc\n"
        "ABC123,study_a,0001,ann,sess,ann/sess/study_a,2026-01-01T00:00:00+00:00\n",
        encoding="utf-8",
    )

    cfg_path = tmp_path / "labeling.yaml"
    cfg_path.write_text(
        "transport_root: transport\n"
        "study_code_map_path: study_codes.csv\n"
        "palette_path: assets/palette.json\n",
        encoding="utf-8",
    )

    cfg = load_config(cfg_path)

    assert cfg.transport_root == str(transport_root.resolve())
    assert cfg.palette_path == str(palette_path.resolve())


def test_resolve_config_path_uses_env_override(monkeypatch, tmp_path):
    default_path = tmp_path / "default.yaml"
    env_path = tmp_path / "server.yaml"
    default_path.write_text("", encoding="utf-8")
    env_path.write_text("", encoding="utf-8")

    monkeypatch.setenv(CONFIG_ENV_VAR, str(env_path))
    assert resolve_config_path(default_path) == env_path.resolve()


def test_resolve_config_path_uses_default_when_env_missing(monkeypatch, tmp_path):
    cfg_path = tmp_path / "local.yaml"
    cfg_path.write_text("", encoding="utf-8")

    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    assert resolve_config_path(cfg_path) == cfg_path.resolve()

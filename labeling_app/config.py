"""Configuration dataclass and YAML loader for the labeling tool."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

CONFIG_ENV_VAR = "MUSCLE_QUALITY_CONFIG_PATH"


@dataclass
class LabelingConfig:
    # Required
    transport_root: str
    study_code_map_path: str = "configs/study_codes.csv"
    palette_path: str = ""

    # Runtime/output paths
    runtime_dir: str = "runtime"
    export_dir: str = "exports"

    # Session behaviour
    session_warning_threshold: int = 60
    anchor_interval: int = 15
    phase_lock: bool = True
    overlay_alpha: float = 0.5

    # Derived paths
    meta_csv_path: str = field(default="", init=False)
    runtime_db_path: str = field(default="", init=False)
    decision_export_path: str = field(default="", init=False)
    config_dir: str = field(default="", init=False, repr=False)
    _transport_root_input: str = field(default="", init=False, repr=False)
    _study_code_map_input: str = field(default="", init=False, repr=False)
    _palette_path_input: str = field(default="", init=False, repr=False)
    _runtime_dir_input: str = field(default="", init=False, repr=False)
    _export_dir_input: str = field(default="", init=False, repr=False)

    def __post_init__(self) -> None:
        self._transport_root_input = self._transport_root_input or self.transport_root
        self._study_code_map_input = self._study_code_map_input or self.study_code_map_path
        self._palette_path_input = self._palette_path_input or self.palette_path
        self._runtime_dir_input = self._runtime_dir_input or self.runtime_dir
        self._export_dir_input = self._export_dir_input or self.export_dir
        self.resolve_paths()

    def resolve_paths(self, base_dir: Optional[str | Path] = None) -> None:
        """Resolve relative config paths against a base directory."""
        root = Path(base_dir).expanduser().resolve() if base_dir else Path.cwd().resolve()
        transport_root = Path(self._transport_root_input).expanduser()
        study_code_map_path = Path(self._study_code_map_input).expanduser()
        palette_path = Path(self._palette_path_input).expanduser() if self._palette_path_input else None
        runtime_dir = Path(self._runtime_dir_input).expanduser()
        export_dir = Path(self._export_dir_input).expanduser()

        if not transport_root.is_absolute():
            transport_root = (root / transport_root).resolve()
        else:
            transport_root = transport_root.resolve()

        if not study_code_map_path.is_absolute():
            study_code_map_path = (root / study_code_map_path).resolve()
        else:
            study_code_map_path = study_code_map_path.resolve()

        if palette_path is None:
            palette_path = (transport_root.parent / "label_palette.json").resolve()
        elif not palette_path.is_absolute():
            palette_path = (root / palette_path).resolve()
        else:
            palette_path = palette_path.resolve()

        if not runtime_dir.is_absolute():
            runtime_dir = (root / runtime_dir).resolve()
        else:
            runtime_dir = runtime_dir.resolve()

        if not export_dir.is_absolute():
            export_dir = (root / export_dir).resolve()
        else:
            export_dir = export_dir.resolve()

        self.config_dir = str(root)
        self.transport_root = str(transport_root)
        self.study_code_map_path = str(study_code_map_path)
        self.palette_path = str(palette_path)
        self.runtime_dir = str(runtime_dir)
        self.export_dir = str(export_dir)
        self.meta_csv_path = str(transport_root / "transport_metadata.csv")
        self.runtime_db_path = str(runtime_dir / "labeling.db")
        self.decision_export_path = str(export_dir / "decision_log.csv")


def resolve_config_path(path: str | Path) -> Path:
    """Resolve the active config path, honoring the deployment override env var."""
    raw = os.getenv(CONFIG_ENV_VAR) or path
    return Path(raw).expanduser().resolve()


def load_config(
    path: str | Path,
    *,
    allow_missing_code_map: bool = False,
) -> LabelingConfig:
    """Load and validate ``labeling.yaml``."""
    path = resolve_config_path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    missing = [k for k in ("transport_root", "study_code_map_path") if k not in raw]
    if missing:
        raise ValueError(f"Config missing required fields: {missing} in {path}")

    known = {
        k: raw[k]
        for k in (
            "transport_root",
            "study_code_map_path",
            "palette_path",
            "runtime_dir",
            "export_dir",
            "session_warning_threshold",
            "anchor_interval",
            "phase_lock",
            "overlay_alpha",
        )
        if k in raw
    }
    cfg = LabelingConfig(**known)
    cfg.resolve_paths(path.parent)

    if not (0.0 <= cfg.overlay_alpha <= 1.0):
        raise ValueError(f"overlay_alpha must be in [0, 1], got {cfg.overlay_alpha}")
    if cfg.session_warning_threshold < 1:
        raise ValueError(
            f"session_warning_threshold must be >= 1, got {cfg.session_warning_threshold}"
        )
    if cfg.anchor_interval < 1:
        raise ValueError(f"anchor_interval must be >= 1, got {cfg.anchor_interval}")

    meta_path = Path(cfg.meta_csv_path)
    if not meta_path.is_file():
        raise FileNotFoundError(f"transport_metadata.csv not found: {meta_path}")

    palette_path = Path(cfg.palette_path)
    if not palette_path.is_file():
        raise FileNotFoundError(f"label_palette.json not found: {palette_path}")

    code_map_path = Path(cfg.study_code_map_path)
    if not allow_missing_code_map and not code_map_path.is_file():
        raise FileNotFoundError(f"Study-code map not found: {code_map_path}")

    Path(cfg.runtime_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.export_dir).mkdir(parents=True, exist_ok=True)
    return cfg

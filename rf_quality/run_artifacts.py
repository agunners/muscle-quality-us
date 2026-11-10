"""Timestamped run-directory + artifact contract (from Folder A).

Every stage writes under ``runs/<YYYYMMDD>/<HHMMSS>_<tag>_<stage>/``. Required
artifacts: ``config_resolved.yaml``, ``metrics.json``, ``run_summary.txt``. The
pipeline adds the five-artifact output (frame_features, study_features,
decision_log_enriched, analysis_manifest, feature_dictionary) + a
``verification_summary.json``.
"""

from __future__ import annotations

import json
import platform
import socket
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import AppCfg


@dataclass(frozen=True)
class RunHandle:
    root: Path
    stage: str
    tag: str
    started_at: str

    @property
    def metrics_path(self) -> Path:
        return self.root / "metrics.json"

    @property
    def summary_path(self) -> Path:
        return self.root / "run_summary.txt"

    @property
    def config_path(self) -> Path:
        return self.root / "config_resolved.yaml"


def new_run(cfg: AppCfg, stage: str, tag: str = "default") -> RunHandle:
    now = datetime.now(timezone.utc)
    date = now.strftime("%Y%m%d")
    hhmmss = now.strftime("%H%M%S")
    run_dir = cfg.paths.runs_root / date / f"{hhmmss}_{tag}_{stage}"
    run_dir.mkdir(parents=True, exist_ok=True)
    run = RunHandle(root=run_dir, stage=stage, tag=tag, started_at=now.isoformat())
    _snapshot_config(run, cfg)
    _init_summary(run, cfg)
    return run


def _snapshot_config(run: RunHandle, cfg: AppCfg) -> None:
    run.config_path.write_text(cfg.to_yaml())


def _init_summary(run: RunHandle, cfg: AppCfg) -> None:
    lines = [
        f"stage: {run.stage}",
        f"tag: {run.tag}",
        f"started_at_utc: {run.started_at}",
        f"hostname: {socket.gethostname()}",
        f"python: {sys.version.split()[0]}",
        f"platform: {platform.platform()}",
        f"seed: {cfg.seed}",
        f"decision_log: {cfg.paths.decision_log}",
        f"transport_metadata: {cfg.paths.transport_metadata}",
        f"transport_root: {cfg.paths.transport_root}",
        f"seg_qc_cache: {cfg.paths.seg_qc_cache}",
        f"modality_primary: {cfg.features.modality_primary}",
    ]
    run.summary_path.write_text("\n".join(lines) + "\n")


def write_metrics(run: RunHandle, metrics: dict, duration_sec: float) -> None:
    payload = {
        "stage": run.stage,
        "tag": run.tag,
        "timestamp": run.started_at,
        "duration_sec": float(duration_sec),
        **metrics,
    }
    run.metrics_path.write_text(json.dumps(payload, indent=2, default=str))


def append_summary(run: RunHandle, line: str) -> None:
    with open(run.summary_path, "a") as f:
        f.write(line + ("\n" if not line.endswith("\n") else ""))

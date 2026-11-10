"""Canonical CLI — ``python -m rf_quality.cli <subcommand> --config <path>``.

Subcommands:
  - ``doctor``  : print versions, device, resolved paths, verified data counts
  - ``smoke``   : run end-to-end on ``smoke_n_studies`` studies (default 10)
  - ``infer``   : run on every processable completed study
  - ``verify``  : post-hoc verification JSON on an existing run directory
"""

from __future__ import annotations

import argparse
import importlib
import json
import platform
import sys
from pathlib import Path

from .config import AppCfg, load_config
from .io.decision_log import (
    akadir_quality_failed_studies,
    build_analysis_manifest,
    build_study_targets,
    latest_rows,
    load_decision_log,
    raw_counts,
)
from .io.transport import load_transport_index, assert_center_frame_bases_resolve
from .pipeline import run_infer
from .run_artifacts import new_run
from .verification import verify_run


def _version(mod_name: str) -> str:
    try:
        mod = importlib.import_module(mod_name)
        return getattr(mod, "__version__", "unknown")
    except ImportError:
        return "not-installed"


def cmd_doctor(cfg: AppCfg) -> int:
    df = load_decision_log(cfg.paths.decision_log)
    counts = raw_counts(df)
    latest = latest_rows(df)
    excluded = akadir_quality_failed_studies(latest)
    targets = build_study_targets(latest, excluded)
    manifest = build_analysis_manifest(latest, targets, excluded)
    transport = load_transport_index(
        cfg.paths.transport_metadata, cfg.paths.study_codes, cfg.paths.transport_root
    )

    missing_anchor = assert_center_frame_bases_resolve(transport, latest)

    report = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "numpy": _version("numpy"),
        "pandas": _version("pandas"),
        "opencv": _version("cv2"),
        "simpleitk": _version("SimpleITK"),
        "PIL": _version("PIL"),
        "scipy": _version("scipy"),
        "paths": {
            "decision_log": str(cfg.paths.decision_log),
            "transport_metadata": str(cfg.paths.transport_metadata),
            "study_codes": str(cfg.paths.study_codes),
            "transport_root": str(cfg.paths.transport_root),
            "runs_root": str(cfg.paths.runs_root),
            "seg_qc_cache": str(cfg.paths.seg_qc_cache) if cfg.paths.seg_qc_cache else None,
        },
        "counts_raw": counts.__dict__,
        "akadir_quality_failed_studies": len(excluded),
        "latest_rows_total": int(len(latest)),
        "latest_rows_post_exclusion": int(len(latest[~latest["study_code"].isin(excluded)])),
        "track_a_studies": int(manifest.loc[manifest["track_a_use"], "study_code"].nunique()),
        "track_b_studies": int(manifest.loc[manifest["track_b_use"], "study_code"].nunique()),
        "triple_rated_post_exclusion": int(
            targets[~targets["study_code"].isin(excluded)]["triple_rated"].sum()
        ),
        "missing_anchor_frame_bases": missing_anchor[:20],
        "missing_anchor_count": len(missing_anchor),
    }
    print(json.dumps(report, indent=2, default=str))
    return 0


def cmd_smoke(cfg: AppCfg) -> int:
    df = load_decision_log(cfg.paths.decision_log)
    latest = latest_rows(df)
    excluded = akadir_quality_failed_studies(latest)
    completed = latest[(latest["decision_status"] != "quality_failed") & (~latest["study_code"].isin(excluded))]
    candidates = sorted(completed["study_code"].unique())[: cfg.smoke_n_studies]
    run = new_run(cfg, stage="smoke", tag="default")
    metrics = run_infer(cfg, run, study_filter=candidates)
    summary = verify_run(run.root, modality=cfg.features.modality_primary)
    print(json.dumps({"run_dir": str(run.root), "metrics": metrics, "verification": summary}, indent=2, default=str))
    return 0 if summary.get("study_rows", 0) > 0 else 1


def cmd_infer(cfg: AppCfg) -> int:
    run = new_run(cfg, stage="infer", tag="default")
    metrics = run_infer(cfg, run, study_filter=None)
    summary = verify_run(run.root, modality=cfg.features.modality_primary)
    print(json.dumps({"run_dir": str(run.root), "metrics": metrics, "verification": summary}, indent=2, default=str))
    return 0


def cmd_verify(run_dir: str, modality: str) -> int:
    summary = verify_run(Path(run_dir), modality=modality)
    print(json.dumps(summary, indent=2, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rf_quality")
    sub = parser.add_subparsers(dest="cmd", required=True)

    for name in ("doctor", "smoke", "infer"):
        sp = sub.add_parser(name)
        sp.add_argument("--config", required=True, type=str)

    sp = sub.add_parser("verify")
    sp.add_argument("--run-dir", required=True, type=str)
    sp.add_argument("--modality", default="final", type=str)

    args = parser.parse_args(argv)

    if args.cmd == "verify":
        return cmd_verify(args.run_dir, args.modality)

    cfg = load_config(args.config)
    if args.cmd == "doctor":
        return cmd_doctor(cfg)
    if args.cmd == "smoke":
        return cmd_smoke(cfg)
    if args.cmd == "infer":
        return cmd_infer(cfg)
    raise SystemExit(2)


if __name__ == "__main__":
    raise SystemExit(main())

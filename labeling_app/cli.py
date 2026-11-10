"""Administrative CLI for the muscle-quality labeling app.

Usage:
    python -m labeling_app.cli doctor
    python -m labeling_app.cli smoke
    python -m labeling_app.cli seed-study-codes [--force]
    python -m labeling_app.cli reset-runtime
    python -m labeling_app.cli run
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "configs" / "labeling.yaml"


def _config_path() -> Path:
    from labeling_app.config import resolve_config_path

    return resolve_config_path(_DEFAULT_CONFIG_PATH)


def _load_cfg(*, allow_missing_code_map: bool = False):
    from labeling_app.config import load_config

    return load_config(_config_path(), allow_missing_code_map=allow_missing_code_map)


def cmd_doctor() -> int:
    """Print environment and dataset health information."""
    import importlib

    failures = []
    print("=== labeling_app doctor ===")
    print(f"Python: {sys.version}")

    for pkg in ("streamlit", "numpy", "pandas", "PIL", "yaml", "pytest"):
        try:
            mod = importlib.import_module(pkg)
            ver = getattr(mod, "__version__", "?")
            print(f"  {pkg:<12}: {ver}")
        except ImportError:
            print(f"  {pkg:<12}: MISSING")
            failures.append(f"Missing package: {pkg}")

    print()
    try:
        cfg = _load_cfg()
    except Exception as exc:
        print(f"Config FAILED: {exc}")
        return 1

    print(f"Config:             {_config_path()}")
    print(f"transport_root:     {cfg.transport_root}")
    print(f"metadata_csv:       {cfg.meta_csv_path}")
    print(f"palette_path:       {cfg.palette_path}")
    print(f"study_code_map:     {cfg.study_code_map_path}")
    print(f"runtime_db:         {cfg.runtime_db_path}")
    print(f"decision_export:    {cfg.decision_export_path}")

    try:
        import pandas as pd

        from labeling_app.data import build_study_manifest, load_study_code_map
        from labeling_app.state import load_log

        meta_df = pd.read_csv(cfg.meta_csv_path, dtype=str)
        study_count = int(meta_df["study_dirname"].nunique())
        print(f"transport_metadata: OK ({len(meta_df)} rows, {study_count} studies)")
        if study_count != 319:
            failures.append(f"Expected 319 studies, found {study_count}")

        code_map = load_study_code_map(cfg.study_code_map_path)
        print(f"study_code_map:     OK ({len(code_map)} codes)")
        if len(code_map) != study_count:
            failures.append(
                f"Study-code map has {len(code_map)} rows but metadata has {study_count} studies"
            )

        manifest = build_study_manifest(
            cfg.meta_csv_path,
            cfg.transport_root,
            cfg.study_code_map_path,
        )
        if manifest["study_code"].isna().any():
            failures.append("Manifest contains missing study_code values")
        else:
            print(f"manifest:           OK ({len(manifest)} study rows)")

        runtime_df = load_log(cfg.runtime_db_path)
        print(f"runtime_log:        OK ({len(runtime_df)} decision rows)")
    except Exception as exc:
        failures.append(f"Data validation failed: {exc}")
        print(f"Data validation FAILED: {exc}")

    print()
    if failures:
        print("FAILURES:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("All checks passed.")
    return 0


def cmd_seed_study_codes(*, force: bool = False) -> int:
    """Generate the immutable `configs/study_codes.csv` file."""
    from labeling_app.data import generate_study_code_map, load_study_code_map

    try:
        cfg = _load_cfg(allow_missing_code_map=True)
    except Exception as exc:
        print(f"Config load failed: {exc}")
        return 1

    out = Path(cfg.study_code_map_path)
    if out.exists() and not force:
        print(
            f"Refusing to overwrite existing study-code map: {out}\n"
            "Use `python -m labeling_app.cli seed-study-codes --force` only if you intentionally want to replace it."
        )
        return 1

    code_map = generate_study_code_map(cfg.meta_csv_path, str(out))
    validated = load_study_code_map(str(out))
    print(f"Generated {len(code_map)} study codes at {out}")
    if len(validated) != len(code_map):
        print("Validation failed after writing the study-code map.")
        return 1
    return 0


def cmd_reset_runtime() -> int:
    """Delete the active runtime DB and reviewer-facing export."""
    from labeling_app.state import reset_runtime_storage

    try:
        cfg = _load_cfg(allow_missing_code_map=True)
    except Exception as exc:
        print(f"Config load failed: {exc}")
        return 1

    reset_runtime_storage(cfg.runtime_db_path, cfg.decision_export_path)
    print(f"Removed runtime store: {cfg.runtime_db_path}")
    print(f"Removed export file:   {cfg.decision_export_path}")
    return 0


def cmd_smoke() -> int:
    """Run a fast metrics smoke test on the first three studies."""
    print("=== labeling_app smoke ===")
    failures = []

    try:
        cfg = _load_cfg()
    except Exception as exc:
        print(f"Config load failed: {exc}")
        return 1

    try:
        from labeling_app.data import build_study_manifest, select_center_frame_details
        from labeling_app.measurements import compute_all_metrics
        from labeling_app.state import DECISION_LOG_COLUMNS, load_log
        from labeling_app.visualization import load_image_gray, load_mask_pil
    except ImportError as exc:
        print(f"Import error: {exc}")
        return 1

    try:
        manifest = build_study_manifest(
            cfg.meta_csv_path,
            cfg.transport_root,
            cfg.study_code_map_path,
        )
    except Exception as exc:
        print(f"Manifest build FAILED: {exc}")
        return 1

    print(f"Manifest rows: {len(manifest)}")
    if manifest.empty:
        print("Manifest is empty.")
        return 1

    run_id = datetime.now().strftime("%Y-%m-%d")
    run_dir = _PROJECT_ROOT / "runs" / run_id / f"smoke_{datetime.now().strftime('%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for idx, (_, study_row) in enumerate(manifest.head(3).iterrows(), start=1):
        study_code = str(study_row["study_code"])
        print(f"\n  Study {idx}: {study_code}")

        center_idx, center_reason = select_center_frame_details(study_row)
        print(f"    representative_frame_index: {center_idx} ({center_reason})")

        mask_paths = study_row["mask_paths"]
        center_mask_path = mask_paths[center_idx] if center_idx < len(mask_paths) else None
        center_mask = load_mask_pil(center_mask_path)

        if center_mask is None:
            print("    mask: MISSING - skipping metrics")
            results.append({"study_code": study_code, "status": "no_mask"})
            continue

        all_masks = [load_mask_pil(path) for path in mask_paths if path]
        all_masks = [mask for mask in all_masks if mask is not None]

        final_paths = study_row["final_paths"]
        center_img_gray = None
        if center_idx < len(final_paths):
            center_img_gray = load_image_gray(final_paths[center_idx])

        spacing = {
            "pixel_spacing_x": study_row.get("pixel_spacing_x"),
            "pixel_spacing_y": study_row.get("pixel_spacing_y"),
            "dcm_rows": study_row.get("dcm_rows"),
            "jpg_height": study_row.get("jpg_height"),
            "jpg_width": study_row.get("jpg_width"),
            "mask_height": study_row.get("mask_height"),
            "mask_width": study_row.get("mask_width"),
            "bmi": study_row.get("bmi"),
        }

        try:
            metrics = compute_all_metrics(center_mask, center_img_gray, all_masks, spacing)
        except Exception as exc:
            failures.append(f"compute_all_metrics failed for {study_code}: {exc}")
            print(f"    metrics FAILED: {exc}")
            results.append({"study_code": study_code, "status": "metrics_error", "error": str(exc)})
            continue

        print(f"    RF thickness: {metrics.get('rf_thickness_px')} px / {metrics.get('rf_thickness_mm')} mm")
        print(f"    QMT: {metrics.get('qmt_px')} px ({metrics.get('qmt_provenance')})")
        print(f"    STAR: {metrics.get('star_ratio')}")
        results.append(
            {
                "study_code": study_code,
                "status": "ok",
                **{k: str(v) for k, v in metrics.items()},
            }
        )

    log_df = load_log(cfg.runtime_db_path)
    missing_cols = [col for col in DECISION_LOG_COLUMNS if col not in log_df.columns]
    if missing_cols:
        failures.append(f"Runtime log missing columns: {missing_cols}")
    else:
        print("\n  Runtime log schema: OK")

    metrics_path = run_dir / "metrics.json"
    with metrics_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "stage": "smoke",
                "timestamp": datetime.now().isoformat(),
                "duration_sec": 0,
                "studies_tested": len(results),
                "results": results,
            },
            f,
            indent=2,
        )

    (run_dir / "run_summary.txt").write_text(
        f"Command: python -m labeling_app.cli smoke\n"
        f"Timestamp: {datetime.now().isoformat()}\n"
        f"Python: {sys.version}\n"
        f"Config: {_config_path()}\n"
        f"transport_root: {cfg.transport_root}\n"
        f"study_code_map: {cfg.study_code_map_path}\n"
        f"runtime_db: {cfg.runtime_db_path}\n",
        encoding="utf-8",
    )

    print(f"\n  Output: {run_dir}")

    if failures:
        print("\nSMOKE FAILURES:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("\nSmoke test passed.")
    return 0


def cmd_run() -> int:
    """Launch the Streamlit app."""
    app_path = _HERE / "app.py"
    result = subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(app_path)],
        check=False,
    )
    return result.returncode


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m labeling_app.cli [doctor|smoke|seed-study-codes|reset-runtime|run]")
        sys.exit(1)

    cmd = sys.argv[1].lower()
    extra = sys.argv[2:]

    if cmd == "doctor":
        sys.exit(cmd_doctor())
    if cmd == "smoke":
        sys.exit(cmd_smoke())
    if cmd == "seed-study-codes":
        sys.exit(cmd_seed_study_codes(force="--force" in extra))
    if cmd == "reset-runtime":
        sys.exit(cmd_reset_runtime())
    if cmd == "run":
        sys.exit(cmd_run())

    print(f"Unknown command: {cmd}")
    print("Usage: python -m labeling_app.cli [doctor|smoke|seed-study-codes|reset-runtime|run]")
    sys.exit(1)


if __name__ == "__main__":
    main()

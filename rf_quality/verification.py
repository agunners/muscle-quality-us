"""Automated verification checks — reproduces Folder A's verification JSON pattern.

Run after ``run_infer``. Writes ``verification_summary.json`` into the run
directory. Focuses on correctness-critical counts + anchor continuity.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats


def spearman(x: np.ndarray, y: np.ndarray) -> float | None:
    """Spearman ρ on finite pairs only; returns ``None`` if fewer than 5 valid pairs."""
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 5:
        return None
    try:
        r, _ = stats.spearmanr(x[mask], y[mask])
        return float(r)
    except Exception:
        return None


# back-compat alias
_spearman = spearman


def verify_run(run_dir: Path, modality: str = "final") -> dict:
    frame = pd.read_csv(run_dir / "frame_features.csv")
    study = pd.read_csv(run_dir / "study_features.csv")
    enriched = pd.read_csv(run_dir / "decision_log_enriched.csv")
    manifest = pd.read_csv(run_dir / "analysis_manifest.csv")

    # Continuity on anchor_rf_echo vs decision_log rf_echo
    cont = None
    anc_col = f"{modality}_rf_anchor_rf_echo_repr"
    if anc_col in study.columns and "rf_echo" in enriched.columns:
        cmp = enriched.dropna(subset=[anc_col, "rf_echo"])
        if not cmp.empty:
            cont = _spearman(cmp[anc_col].to_numpy(), cmp["rf_echo"].to_numpy())

    summary = {
        "frame_rows": int(len(frame)),
        "frames_passing_qc": int(frame["frame_qc_pass"].astype(bool).sum()) if not frame.empty else 0,
        "study_rows": int(len(study)),
        "decision_log_enriched_rows": int(len(enriched)),
        "analysis_manifest_rows": int(len(manifest)),
        "track_a_latest_rows": int(manifest["track_a_use"].sum()) if "track_a_use" in manifest.columns else 0,
        "track_b_latest_rows": int(manifest["track_b_use"].sum()) if "track_b_use" in manifest.columns else 0,
        "anchor_rf_echo_continuity_spearman": cont,
        "anchor_continuity_pass": bool(cont is not None and cont >= 0.995),
    }
    (run_dir / "verification_summary.json").write_text(json.dumps(summary, indent=2))
    return summary

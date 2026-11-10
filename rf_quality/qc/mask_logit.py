"""Tier-2B logit-gated QC — always null-safe.

Reads ``seg_qc_cache.csv`` (produced by ``rf_quality_segmenter_bridge/``)
if present; emits nulls for every expected column otherwise. The main repo
venv never imports FastAI.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


LOGIT_COLUMNS: tuple[str, ...] = (
    "rf_softmax_margin_mean",
    "rf_softmax_margin_p10",
    "rf_boundary_uncertainty",
)


@dataclass(frozen=True)
class LogitCache:
    df: pd.DataFrame | None  # keyed (study_code, frame_base, modality)

    def lookup(self, study_code: str, frame_base: str, modality: str) -> dict[str, float | None]:
        if self.df is None or self.df.empty:
            return {col: None for col in LOGIT_COLUMNS}
        mask = (
            (self.df["study_code"] == study_code)
            & (self.df["frame_base"] == frame_base)
            & (self.df["modality"] == modality)
        )
        sub = self.df[mask]
        if sub.empty:
            return {col: None for col in LOGIT_COLUMNS}
        r = sub.iloc[0]
        return {col: (float(r[col]) if col in sub.columns and pd.notna(r[col]) else None) for col in LOGIT_COLUMNS}


def load_cache(path: Path | None) -> LogitCache:
    if path is None or not Path(path).exists():
        return LogitCache(df=None)
    df = pd.read_csv(path)
    required = {"study_code", "frame_base", "modality"}
    if not required.issubset(df.columns):
        return LogitCache(df=None)
    return LogitCache(df=df)

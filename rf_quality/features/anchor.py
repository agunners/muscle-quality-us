"""Labeling-app continuity anchors (Folder A's ``legacy_*`` renamed).

These pass through values that the labeling app already wrote to
``decision_log.csv`` for the same study + anchor frame. They are the
cheapest way to verify the new pipeline's erosion / spacing / masking still
match the app that produced the ground-truth labels (``anchor_rf_echo``
Spearman ≥ 0.995 vs ``decision_log.rf_echo`` is the continuity gate).

Registry family=``anchor``, role=``anchor`` — never enters modeling.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


ANCHOR_COLUMN_MAP: dict[str, str] = {
    "anchor_rf_echo": "rf_echo",
    "anchor_rf_thickness_mm": "rf_thickness_mm",
    "anchor_rf_area_mm2": "rf_area_mm2",
}


def take_anchor_values(decision_row: pd.Series) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for stem, source_col in ANCHOR_COLUMN_MAP.items():
        if source_col in decision_row and pd.notna(decision_row[source_col]):
            try:
                out[stem] = float(decision_row[source_col])
            except (TypeError, ValueError):
                out[stem] = None
        else:
            out[stem] = None
    return out

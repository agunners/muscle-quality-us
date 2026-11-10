"""Family-specific aggregation.

For each stem in the registry, emit the study-level columns dictated by the
spec's ``aggregation_policy``:
  - ``anchor_primary``                  → ``*_repr``, ``*_agg_median``, ``*_agg_range``
  - ``agg_primary_with_anchor_passthrough`` → ``*_agg_median``, ``*_agg_iqr``, ``*_agg_n_valid``, ``*_repr``
  - ``per_frame_only``                  → nothing in study_features

Aggregation is computed only over *QC-passing* frames (tier-1 + tier-2A
non-hard-fail). The anchor value is the value at ``center_frame_base`` (may
be None if the anchor itself failed QC — then ``*_repr`` is null but
``*_agg_*`` can still be valid).
"""

from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

from ..registry import FeatureSpec, REGISTRY


def _finite(values: Iterable[float | None]) -> list[float]:
    out: list[float] = []
    for v in values:
        if v is None:
            continue
        try:
            vv = float(v)
        except (TypeError, ValueError):
            continue
        if np.isfinite(vv):
            out.append(vv)
    return out


def _median(v: list[float]) -> float | None:
    return float(np.median(v)) if v else None


def _iqr(v: list[float]) -> float | None:
    if len(v) < 2:
        return None
    q75 = float(np.quantile(v, 0.75))
    q25 = float(np.quantile(v, 0.25))
    return q75 - q25


def _range(v: list[float]) -> float | None:
    if len(v) < 2:
        return None
    return float(max(v) - min(v))


def aggregate_study(
    frame_metrics: pd.DataFrame,
    anchor_frame_base: str,
    modality: str,
    qc_pass_col: str = "frame_qc_pass",
) -> dict[str, float | None]:
    """Emit the wide-table columns for one study x one modality.

    ``frame_metrics`` must carry one row per frame with the per-stem column
    names matching the registry ``stem``. The anchor row is identified by
    ``frame_base == anchor_frame_base``.
    """
    passing = frame_metrics[frame_metrics[qc_pass_col].astype(bool)]
    anchor_row = frame_metrics[frame_metrics["frame_base"] == anchor_frame_base]
    out: dict[str, float | None] = {}
    for spec in REGISTRY:
        stem = spec.stem
        if stem not in frame_metrics.columns:
            continue
        p = f"{modality}_rf_{stem}"
        anchor_val = None
        if not anchor_row.empty and anchor_row[qc_pass_col].iloc[0]:
            val = anchor_row[stem].iloc[0]
            if pd.notna(val):
                try:
                    anchor_val = float(val)
                except (TypeError, ValueError):
                    anchor_val = None

        values = _finite(passing[stem].tolist())
        if spec.aggregation_policy == "anchor_primary":
            out[f"{p}_repr"] = anchor_val
            out[f"{p}_agg_median"] = _median(values)
            out[f"{p}_agg_range"] = _range(values)
        elif spec.aggregation_policy == "agg_primary_with_anchor_passthrough":
            out[f"{p}_agg_median"] = _median(values)
            out[f"{p}_agg_iqr"] = _iqr(values)
            out[f"{p}_agg_n_valid"] = float(len(values))
            out[f"{p}_repr"] = anchor_val
        # per_frame_only → nothing
    return out

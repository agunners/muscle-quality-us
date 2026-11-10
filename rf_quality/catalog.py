"""Mechanical ``feature_dictionary.csv`` generator (derived from the registry)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .registry import REGISTRY


def build_feature_dictionary() -> pd.DataFrame:
    rows = []
    for spec in REGISTRY:
        rows.append({
            "stem": spec.stem,
            "family": spec.family.value,
            "unit": spec.unit,
            "measurement_domain": spec.measurement_domain,
            "formula_summary": spec.formula_summary,
            "role": spec.role,
            "aggregation_policy": spec.aggregation_policy,
            "scanner_risk": spec.scanner_risk,
            "preferred": spec.preferred,
            "grounding_family": spec.grounding_family,
            "min_plausible": spec.min_plausible,
            "max_plausible": spec.max_plausible,
            "null_rules": spec.null_rules,
            "notes": spec.notes,
        })
    return pd.DataFrame(rows)


def write_feature_dictionary(out_path: Path) -> Path:
    df = build_feature_dictionary()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    return out_path

"""Tier-3 feature plausibility gate — registry-bounded."""

from __future__ import annotations

from typing import Iterable

from ..registry import registry_dict


def gate_plausibility(values: dict[str, float | None]) -> tuple[dict[str, float | None], list[str]]:
    """Null out values that fall outside registry-declared plausible ranges.

    Returns (clean_values, violations). Keys not present in the registry are
    passed through untouched (these are internal / provenance columns).
    """
    specs = registry_dict()
    violations: list[str] = []
    clean: dict[str, float | None] = {}
    for k, v in values.items():
        spec = specs.get(k)
        if v is None or spec is None or spec.min_plausible is None or spec.max_plausible is None:
            clean[k] = v
            continue
        try:
            vv = float(v)
        except (TypeError, ValueError):
            clean[k] = v
            continue
        if vv < spec.min_plausible or vv > spec.max_plausible:
            clean[k] = None
            violations.append(f"{k}={vv:.3f} outside [{spec.min_plausible}, {spec.max_plausible}]")
        else:
            clean[k] = vv
    return clean, violations


def violation_audit(values_list: Iterable[dict[str, float | None]]) -> dict[str, int]:
    """Returns stem → count of plausibility violations across a batch."""
    specs = registry_dict()
    counts: dict[str, int] = {k: 0 for k in specs}
    for values in values_list:
        for k, v in values.items():
            spec = specs.get(k)
            if v is None or spec is None or spec.min_plausible is None or spec.max_plausible is None:
                continue
            try:
                vv = float(v)
            except (TypeError, ValueError):
                continue
            if vv < spec.min_plausible or vv > spec.max_plausible:
                counts[k] = counts.get(k, 0) + 1
    return counts

"""Predictor selection — registry → wide-column names.

The rule (plan §(ii) of the analysis plan):

  * role == 'primary' and preferred == True.
  * aggregation_policy != 'per_frame_only'.
  * ``anchor_primary`` stems → use ``<mod>_rf_<stem>_repr``.
  * ``agg_primary_with_anchor_passthrough`` stems → emit both
    ``_agg_median`` and ``_repr``; Stage B's repr-vs-agg A/B picks the
    winner per family.
  * ``family=anchor`` is excluded from modeling (correctness anchor only).
  * ``family=qc_support`` is excluded unless explicitly promoted.

``predictor_columns()`` is the single place that materialises the predictor
column list from the registry. Every analysis script calls it, so a
registry change propagates automatically.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ..registry import REGISTRY, Family, FeatureSpec


EXCLUDED_FAMILIES: set[Family] = {Family.ANCHOR, Family.QC_SUPPORT, Family.MASK_QC}

# Stems that are genuine QC infrastructure (not modeling candidates), even
# though they live in a family we otherwise model. They are ``role=support``
# inside ``Family.GEOMETRY`` and exist only to feed the Tier-2A gate.
EXCLUDED_STEMS_INFRA: set[str] = {
    "full_area_px",
    "area_retained_fraction_after_erosion",
}


@dataclass(frozen=True)
class PredictorCol:
    stem: str
    family: Family
    aggregation_policy: str
    scanner_risk: str
    role: str
    column: str                # exact wide-table column name
    variant: str               # 'repr' or 'agg_median'


def _base_prefix(modality: str) -> str:
    return f"{modality}_rf_"


def predictor_columns(
    modality: str = "final",
    include_stems: Iterable[str] | None = None,
) -> list[PredictorCol]:
    """Materialise the predictor column list.

    If ``include_stems`` is given, restrict to that set (used by Stage B+
    to feed only survivors downstream).
    """
    out: list[PredictorCol] = []
    allow = set(include_stems) if include_stems is not None else None
    for spec in REGISTRY:
        # Plan §4.1 Tier-1 (primary preferred) AND Tier-2 (support preferred)
        # both enter analysis — Tier-2 "earns Core via stability". Only
        # exclude anchor/qc-support/mask-qc families and the two
        # QC-infrastructure geometry support stems listed in
        # ``EXCLUDED_STEMS_INFRA``.
        if not spec.preferred:
            continue
        if spec.family in EXCLUDED_FAMILIES:
            continue
        if spec.stem in EXCLUDED_STEMS_INFRA:
            continue
        if spec.aggregation_policy == "per_frame_only":
            continue
        if allow is not None and spec.stem not in allow:
            continue
        prefix = _base_prefix(modality) + spec.stem
        if spec.aggregation_policy == "anchor_primary":
            out.append(_pc(spec, prefix + "_repr", "repr"))
        elif spec.aggregation_policy == "agg_primary_with_anchor_passthrough":
            out.append(_pc(spec, prefix + "_agg_median", "agg_median"))
            out.append(_pc(spec, prefix + "_repr", "repr"))
    return out


def _pc(spec: FeatureSpec, col: str, variant: str) -> PredictorCol:
    return PredictorCol(
        stem=spec.stem,
        family=spec.family,
        aggregation_policy=spec.aggregation_policy,
        scanner_risk=spec.scanner_risk,
        role=spec.role,
        column=col,
        variant=variant,
    )


def column_names(predictors: Iterable[PredictorCol]) -> list[str]:
    return [p.column for p in predictors]


def by_stem(predictors: Iterable[PredictorCol]) -> dict[str, list[PredictorCol]]:
    """Group variants under a single stem — the A/B step uses this."""
    out: dict[str, list[PredictorCol]] = {}
    for p in predictors:
        out.setdefault(p.stem, []).append(p)
    return out

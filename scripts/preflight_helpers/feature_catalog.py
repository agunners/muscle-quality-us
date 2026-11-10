"""Single source of truth for the assessment's displayed-feature lists + their
display metadata (labels, digits).

WHY THIS EXISTS — the recurrence it prevents. Six surfaces independently hard-
coded "the feature list": run_pipeline (HEADLINE/SECONDARY), study_bootstrap
(BOOTSTRAP_FEATURES, kept local to stay torch-free), the previous-runs sidebar
(short labels), and the p05 card (full labels + digits). When the subcutaneous-
fat features were added to the display lists but NOT to the sidebar label dict
and NOT to BOOTSTRAP_FEATURES, the previous-runs sidebar crashed
(`KeyError('fat_thickness_q50_mm')`) and the cross-run table showed fat as
"— / single run". No test caught it because the broken paths were display glue
with no coverage.

This module is the one place those lists + labels live. It is intentionally
torch-free (pure stdlib), so study_bootstrap can import the feature list from
here without dragging in the segmenter/torch that run_pipeline carries. Adding a
displayed feature is now ONE edit here (append to HEADLINE/SECONDARY + add a
FEATURES entry); the invariant test (tests/preflight/test_feature_catalog.py)
fails loudly if a displayed feature lacks a label/digits or is not pooled by the
study bootstrap.
"""
from __future__ import annotations

from typing import NamedTuple


class FeatureDisplay(NamedTuple):
    label: str    # full label for the assessment card (p05)
    short: str    # compact label for the previous-runs sidebar rows
    digits: int   # decimal places for the assessment card


# One entry per feature the UI can display. Order = a sensible reading order
# (headline, then secondary, then card-only extras). label/digits MUST match what
# the p05 card rendered before this module existed (no visual change intended).
FEATURES: dict[str, FeatureDisplay] = {
    "star_ratio":            FeatureDisplay("STAR (QMT/BMI)", "STAR", 4),
    "full_thickness_q50_mm": FeatureDisplay("RF thickness — median (mm)", "RF thk (med)", 2),
    "int_cv_within":         FeatureDisplay("Fascicular contrast (echo CV)", "echo CV", 3),
    "rf_thickness_mm":       FeatureDisplay("RF thickness — at the landmark cross-section (mm)", "RF thk (lmk)", 2),
    "fat_thickness_q50_mm":  FeatureDisplay("Fat thickness — median (mm)", "Fat thk (med)", 2),
    "qmt_mm":                FeatureDisplay("QMT (mm)", "QMT", 2),
    "visible_rf_area_mm2":   FeatureDisplay("Visible RF area (mm²)", "RF area", 0),
    "fat_to_depth_q50":      FeatureDisplay("Fat fraction (fat / skin-to-femur)", "fat frac", 3),
    # Card-only extra (shown in the p05 detail table, not a headline/secondary tile):
    "fat_thickness_max_mm":  FeatureDisplay("Fat thickness — max (mm)", "Fat thk (max)", 2),
    # DEMOTED from the card 2026-06-10 (red-team): the weakest-built composite
    # (area/area, FOV-coupled denominator, untrusted VI). Computed + kept only as a
    # diagnostic; the clean "Fat fraction" carries the muscle-vs-fat story. Absent
    # from HEADLINE/SECONDARY → not displayed, pooled, or cohort-placed.
    "muscle_fat_ratio":      FeatureDisplay("Muscle / fat ratio (diagnostic)", "M/F ratio", 2),
}

# Headline + secondary features the assessment card renders (tuple order = the
# card's display order). run_pipeline re-exports these so existing
# `from run_pipeline import HEADLINE_FEATURES` callers are unaffected.
HEADLINE_FEATURES: tuple[str, ...] = (
    "star_ratio",
    "full_thickness_q50_mm",
    "int_cv_within",
    "rf_thickness_mm",
    # PROMOTED 2026-06-18: this fat FRACTION is the feature the condition score's
    # fat axis actually uses, so it belongs in the headline — otherwise the score
    # breakdown cites a fat percentile (fraction) the user can't see, while the
    # prominent fat card showed a *different* number (thickness, mm). Now they match.
    "fat_to_depth_q50",
)
SECONDARY_FEATURES: tuple[str, ...] = (
    "qmt_mm",
    "visible_rf_area_mm2",
    # DEMOTED 2026-06-18 (paired with the promote above): fat thickness in mm is a
    # real read but is NOT the score's fat axis — kept as a secondary detail.
    "fat_thickness_q50_mm",
)
ALL_DISPLAY_FEATURES: tuple[str, ...] = HEADLINE_FEATURES + SECONDARY_FEATURES

# The study bootstrap pools exactly the displayed features. It used to keep its
# own copy that silently drifted (it lacked the fat features) — deriving it from
# the displayed set here makes that drift impossible. (VI features dropped
# 2026-05-14 are simply absent from the catalog, so they stay un-pooled.)
BOOTSTRAP_FEATURES: tuple[str, ...] = ALL_DISPLAY_FEATURES

# Convenience views the display surfaces consume.
FEATURE_LABELS: dict[str, str] = {k: v.label for k, v in FEATURES.items()}
SHORT_LABELS: dict[str, str] = {k: v.short for k, v in FEATURES.items()}
DIGITS: dict[str, int] = {k: v.digits for k, v in FEATURES.items()}

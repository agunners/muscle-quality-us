"""UI-dispatch invariants — what the operator would see.

`picker_qa_cue` and the per-measurement attribution panel are pure
functions of an image_quality dict. Build a dict that represents a
contradiction case (tier-3 + edge_clip_lateral fired calibrated) and
assert the cue does NOT surface a misleading dominant.
"""
from __future__ import annotations

import pytest

from labeling_app.assessment_pages.qa_surfacing import picker_qa_cue


def _iq(
    validity: tuple[str, ...] = (),
    pending: tuple[str, ...] = (),
    tier_per_measurement: dict | None = None,
    aggregate_tier: str = "valid",
) -> dict:
    """Construct an image_quality dict shaped like the real one."""
    return {
        "image_quality_validity_flags": list(validity),
        "image_quality_pending_calibration_flags": list(pending),
        "image_quality_detector_metadata": {},
        "image_quality_tier_per_measurement": tier_per_measurement or {},
        "image_quality_aggregate_tier": aggregate_tier,
        "image_quality_contract_version": "v0.2",
    }


# ---------------------------------------------------------------------------
# Picker-cue invariant — no "side of image" prose when the only calibrated
# flag is edge_clip_lateral (reserve state)
# ---------------------------------------------------------------------------
# Akadir 2026-05-24 § 1 — three no-femur thumbnails in screenshot showed
# the cue "muscle runs off the side of the image" because:
#   1. QMT pipeline rejected the frame's femur → tier 3 (dot "no femur").
#   2. femur_out_of_frame did NOT fire (femur_absent not in qmt validity
#      flags — femur pixels exist but aren't QMT-usable).
#   3. The QMT provenance floor capped headlines (qmt_mm, star_ratio) to
#      INV → aggregate INVALID.
#   4. The only calibrated flag fired was edge_clip_lateral → picker cue
#      surfaces its prose.
#
# After the Layer-2d fix (edge_clip_lateral in reserve), edge_clip_lateral
# is still in validity_flags (on the record) but no longer in the
# calibrated SUBSET that drives the cue — because it taints nothing in
# _GATING_TABLE, the picker_qa_cue logic needs to skip it as a dominant.
# This test pins that behaviour.

def test_picker_cue_no_side_of_image_prose_when_edge_clip_is_only_calibrated() -> None:
    """A no-femur frame whose only calibrated flag is edge_clip_lateral
    must not surface "muscle runs off the side of the image" as the
    picker cue. The operator should not see two unrelated signals (dot
    says no femur, cue says off-side) on the same thumbnail."""
    iq = _iq(
        validity=("edge_clip_lateral", "wrong_site"),  # priority-ordered
        pending=("wrong_site",),                        # wrong_site is pending
        tier_per_measurement={
            "rf_thickness_mm": "valid",
            "qmt_mm": "invalid",            # provenance floor (no femur)
            "star_ratio": "invalid",
            "full_thickness_q50_mm": "valid",
            "visible_rf_area_mm2": "invalid",   # currently from edge_clip
            "int_cv_within": "valid",
            "rf_echo": "valid",
            "muscle_fat_ratio": "valid",
        },
        aggregate_tier="invalid",
    )
    cue = picker_qa_cue(iq) or ""
    assert "side of the image" not in cue, (
        f"picker cue surfaces edge_clip_lateral's prose on a no-femur "
        f"frame — that is the screenshot § 1 contradiction. Cue: {cue}")


def test_picker_cue_silent_on_clean_frame() -> None:
    """A clean frame produces no cue. Sanity check that the dispatch is
    silent in the silent case."""
    iq = _iq(validity=(), pending=(),
             tier_per_measurement={m: "valid" for m in (
                 "rf_thickness_mm", "qmt_mm", "star_ratio",
                 "full_thickness_q50_mm", "visible_rf_area_mm2",
                 "int_cv_within", "rf_echo", "muscle_fat_ratio")},
             aggregate_tier="valid")
    assert picker_qa_cue(iq) is None


# ---------------------------------------------------------------------------
# Femur tint ↔ picker dot consistency (Akadir 2026-05-24 § 1)
# ---------------------------------------------------------------------------
# The dot uses QMT-usability via `frame_qmt_status.tier`; the tint must use
# the SAME definition. `_should_paint_femur_tint(tier)` is the single
# predicate; the picker calls it before painting. This test pins it.

def test_femur_tint_does_not_paint_on_no_femur_dot() -> None:
    """Tier 3 (dot = no femur) ⇒ tint must NOT paint, even if the
    segmenter labelled some femur pixels. Akadir 2026-05-24 § 1 — the
    operator was seeing dot=no femur + yellow band on the same thumbnail."""
    from labeling_app.assessment_pages.p03_frame_picker import (
        _should_paint_femur_tint,
    )
    assert _should_paint_femur_tint(3) is False


def test_femur_tint_paints_on_femur_dot() -> None:
    """Tier 1 + Tier 2 (dot says femur visible or proxy) ⇒ tint paints.
    The two signals agree by construction."""
    from labeling_app.assessment_pages.p03_frame_picker import (
        _should_paint_femur_tint,
    )
    assert _should_paint_femur_tint(1) is True
    assert _should_paint_femur_tint(2) is True

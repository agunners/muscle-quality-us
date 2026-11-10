"""Tests for p03 picker helpers — `_ai_suggested_repr`,
`_thumbnail_with_femur_roi` (Akadir 2026-05-24 Layer 2b — picker
simplification).

The helpers are pure functions of session-state inputs (no Streamlit
runtime needed at call time, although streamlit is imported at module load).
"""
from __future__ import annotations

import numpy as np

from labeling_app.assessment_pages.p03_frame_picker import (
    _ai_suggested_repr, _short, _study_under_gain,
)


def _iq(flags: tuple[str, ...] = (),
        pending: tuple[str, ...] = ()) -> dict:
    return {
        "image_quality_validity_flags": list(flags),
        "image_quality_pending_calibration_flags": list(pending),
    }


def test_ai_suggested_repr_prefers_femur_anchored_tier1_over_proxy() -> None:
    """Tier 1 (femur-anchored) beats Tier 2 (proxy) regardless of QA flags —
    a Tier-1 frame with one calibrated invalid flag still wins over a clean
    Tier-2 frame, because QMT provenance is the dominant repr criterion."""
    scan = {
        "f1": {"tier": 1, "image_quality": _iq(("edge_clip_vertical",))},
        "f2": {"tier": 2, "image_quality": _iq(())},
    }
    assert _ai_suggested_repr(["f1", "f2"], scan) == "f1"


def test_ai_suggested_repr_breaks_tie_on_fewer_calibrated_flags() -> None:
    """Within a tier, the frame with fewer calibrated flags wins. Pending
    flags do not count (they do not drive the deployed tier)."""
    scan = {
        "f1": {"tier": 1, "image_quality": _iq(
            ("edge_clip_vertical",))},                       # 1 calibrated
        "f2": {"tier": 1, "image_quality": _iq(
            ("wrong_site",), pending=("wrong_site",))},      # 0 calibrated
    }
    assert _ai_suggested_repr(["f1", "f2"], scan) == "f2"


def test_ai_suggested_repr_returns_none_on_empty_picked() -> None:
    scan = {"f1": {"tier": 1, "image_quality": _iq(())}}
    assert _ai_suggested_repr([], scan) is None


def test_ai_suggested_repr_returns_none_when_scan_empty() -> None:
    """Before the femur-scan button is pressed the scan is empty — the AI
    has no signal to suggest from, so we honestly suggest nothing."""
    assert _ai_suggested_repr(["f1", "f2"], {}) is None
    assert _ai_suggested_repr(["f1", "f2"], None) is None


def test_ai_suggested_repr_returns_none_when_no_tier1_picked() -> None:
    """Akadir 2026-05-25 § 3 — a proxy-femur (Tier-2) or no-femur (Tier-3)
    frame is never a valid repr suggestion. When no picked frame is Tier-1
    the AI suggests nothing; the picker's `selection_repr_status` surface
    then explains the gap (sweep has unpicked Tier-1 → nudge to add it;
    sweep has none anywhere → QMT will be degraded). Designation on p04
    still works — the operator can make any frame the repr; the AI just
    refuses to suggest one when none is truly viable."""
    scan_tier3_only = {
        "f1": {"tier": 3, "image_quality": _iq(("edge_clip_vertical",))},
        "f2": {"tier": 3, "image_quality": _iq(())},
    }
    assert _ai_suggested_repr(["f1", "f2"], scan_tier3_only) is None

    scan_tier2_only = {
        "f1": {"tier": 2, "image_quality": _iq(())},
        "f2": {"tier": 2, "image_quality": _iq(())},
    }
    assert _ai_suggested_repr(["f1", "f2"], scan_tier2_only) is None

    scan_mixed_no_tier1 = {
        "f1": {"tier": 2, "image_quality": _iq(())},  # proxy
        "f2": {"tier": 3, "image_quality": _iq(())},  # no femur
    }
    assert _ai_suggested_repr(["f1", "f2"], scan_mixed_no_tier1) is None


def test_ai_suggested_repr_skips_unscanned_picked_frames() -> None:
    """A picked frame missing from the scan does not score — only scanned
    frames contribute. (Defensive: the picker can be in a state where the
    operator picked a frame before re-running the scan.)"""
    scan = {"f1": {"tier": 1, "image_quality": _iq(())}}
    assert _ai_suggested_repr(["f0", "f1", "f99"], scan) == "f1"


# ---- _short — frame_base disambiguation (Akadir 2026-05-25 § 4a) ----------

def test_short_disambiguates_sweep_siblings_with_subsecond() -> None:
    """5 sweep-sibling frames sharing the same HHMMSS must render with
    distinct sub-seconds (Akadir 2026-05-25 § 4a — the "5 thumbnails all
    labelled 151033" complaint)."""
    siblings = [
        "20260127_151033100",
        "20260127_151033240",
        "20260127_151033380",
        "20260127_151033520",
        "20260127_151033660",
    ]
    out = [_short(fb) for fb in siblings]
    assert out == ["151033.100", "151033.240", "151033.380",
                   "151033.520", "151033.660"]
    # Crucially — they are all DISTINCT (the old behaviour returned five
    # copies of "151033").
    assert len(set(out)) == 5


def test_short_handles_canonical_frame_base_format() -> None:
    """Canonical YYYYMMDD_HHMMSSXXX — date dropped, time with dotted
    sub-second."""
    assert _short("20260127_151045625") == "151045.625"


def test_short_degrades_gracefully_on_short_time_part() -> None:
    """A non-canonical fb (truncated, legacy, or short) returns what it
    can — never crashes."""
    assert _short("abc") == "abc"      # less than 9 chars — return as-is
    assert _short("abc_def") == "def"


def test_study_under_gain_pervasive_when_most_frames_dark() -> None:
    """dark_clip_high firing on >= 80% of the scanned frames is a study-level
    acquisition/gain problem — the picker demotes the per-frame flood to one cue.
    9/10 dark crosses the 0.8 threshold (study_level_qa)."""
    scan = {f"f{i}": {"image_quality": _iq(("dark_clip_high",) if i < 9 else ())}
            for i in range(10)}
    pervasive, frac = _study_under_gain(scan)
    assert pervasive is True
    assert abs(frac - 0.9) < 1e-9


def test_study_under_gain_not_pervasive_when_few_frames_dark() -> None:
    """A handful of dark frames is a per-frame concern (pick around them), NOT a
    study-level one — no demotion. 1/10 is below the 0.8 threshold."""
    scan = {f"f{i}": {"image_quality": _iq(("dark_clip_high",) if i < 1 else ())}
            for i in range(10)}
    pervasive, frac = _study_under_gain(scan)
    assert pervasive is False
    assert abs(frac - 0.1) < 1e-9


def test_study_under_gain_empty_scan_is_not_pervasive() -> None:
    assert _study_under_gain({}) == (False, 0.0)

"""The RF landmark-frame scorer (redesigned 2026-06-09).

Pins the mechanics: area-primary ranking (the fullest CLEAN cross-section wins),
centering as a tiebreaker only, the clean gate (wrong_site / edge_clip_vertical /
invalid tier reject; lateral clip + darkness tolerated for geometry), and the
landmark helpers. The clinical claim (fullest ~= mid-belly, assumption A1) is
flagged-not-resolved in repr_scorer.py — these tests pin code, not anatomy.
"""
from __future__ import annotations

import numpy as np

from labeling_app.measurements import LABEL_RF
from labeling_app.repr_scorer import (
    centering, completeness, fullness, landmark_value, rf_area_px, repr_score,
    score_sweep, top_clean_frames,
)

_H, _W = 20, 40


def _mask(rows: tuple, cols: tuple) -> np.ndarray:
    m = np.zeros((_H, _W), dtype=int)
    (r0, r1), (c0, c1) = rows, cols
    m[r0:r1, c0:c1] = LABEL_RF
    return m


_CENTERED = _mask((8, 12), (16, 24))      # 32 px, away from every edge
_LEFT_CLIP = _mask((8, 12), (0, 8))       # 32 px, touches the left lateral edge
_FULL_WIDTH = _mask((8, 12), (0, 40))     # 160 px, spans the full width (largest)
_SMALL = _mask((9, 11), (18, 22))         # 8 px, centered but tiny
_EMPTY = np.zeros((_H, _W), dtype=int)


def test_completeness_rewards_an_uncut_cross_section() -> None:
    assert completeness(_CENTERED) == 1.0          # neither lateral edge
    assert completeness(_LEFT_CLIP) == 0.5         # one side
    assert completeness(_FULL_WIDTH) == 0.0        # both sides
    assert completeness(_EMPTY) == 0.0             # no RF


def test_centering_peaks_at_image_centre() -> None:
    assert centering(_CENTERED) > 0.9
    assert centering(_LEFT_CLIP) < 0.3
    assert centering(_EMPTY) == 0.0


def test_fullness_is_a_sweep_relative_rank() -> None:
    areas = [rf_area_px(_LEFT_CLIP), rf_area_px(_CENTERED), rf_area_px(_FULL_WIDTH)]
    assert fullness(rf_area_px(_FULL_WIDTH), areas) == 1.0   # the fullest -> 1.0
    assert fullness(0, areas) < 1.0                          # the smallest ranks low
    assert fullness(5.0, []) == 0.0                          # empty sweep -> 0


def test_largest_clean_cross_section_wins_centering_breaks_ties() -> None:
    """Area-primary: the fullest CLEAN cross-section ranks highest — lateral clip
    is tolerated, so the full-width frame wins on area. Among equal-area frames,
    the centered one wins (the tiebreaker)."""
    scores = score_sweep({"A": _CENTERED, "B": _LEFT_CLIP, "C": _FULL_WIDTH})
    assert scores["C"] == max(scores.values())   # largest area (full width) wins
    assert scores["A"] > scores["B"]             # equal area -> centering breaks the tie
    assert scores["B"] > 0.0                      # lateral clip is NOT a reject


def test_gate_excludes_vertical_wrongsite_invalid_tolerates_lateral_and_dark() -> None:
    areas = [rf_area_px(_CENTERED)]

    def s(grade):
        return repr_score(_CENTERED, sweep_rf_areas=areas, grade=grade)

    assert s({"image_quality_validity_flags": ["wrong_site"]}) == 0.0
    assert s({"image_quality_validity_flags": ["edge_clip_vertical"]}) == 0.0
    assert s({"image_quality_aggregate_tier": "invalid"}) == 0.0
    # lateral clip is EXPECTED with the 128L probe -> NOT a reject.
    assert s({"image_quality_validity_flags": ["edge_clip_lateral"]}) > 0.0
    # dark_clip_high does NOT corrupt fascia-based RF geometry (Akadir 2026-06-10)
    # -> tolerated here; it gates the echo features in the § 6.2 table, not this.
    assert s({"image_quality_pending_calibration_flags": ["dark_clip_high"]}) > 0.0
    assert repr_score(_EMPTY, sweep_rf_areas=[0]) == 0.0     # no RF body


def test_top_clean_frames_returns_largest_clean_best_first() -> None:
    masks = {"big": _FULL_WIDTH, "mid": _CENTERED, "small": _SMALL}
    assert top_clean_frames(masks, k=2) == ["big", "mid"]   # by area, best-first
    # a GEOMETRY-gated frame (vertical clip) is excluded even though it is largest.
    grades = {"big": {"image_quality_validity_flags": ["edge_clip_vertical"]}}
    assert top_clean_frames(masks, grades, k=3)[0] == "mid"
    assert "big" not in top_clean_frames(masks, grades, k=3)
    assert top_clean_frames({}, k=3) == []                  # no frames -> empty


def test_landmark_value_is_the_median_over_landmark_frames() -> None:
    values = {"a": 10.0, "b": 20.0, "c": 30.0, "d": None}
    assert landmark_value(values, ["a", "b", "c"]) == 20.0  # median of 10,20,30
    assert landmark_value(values, ["a", "d"]) == 10.0       # None skipped
    assert landmark_value(values, ["d"]) is None            # all None -> None
    assert landmark_value({}, ["a"]) is None                # nothing -> None

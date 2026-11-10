"""Subsystem C — candidate selection. Synthetic score frames so the tests are
independent of the real cohort parquet."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from calibration.candidate_selector import Candidate, select_candidates


def _frame(detector: str, scores, fires=None, threshold=None, start=0):
    """Build a score-table slice for one detector."""
    n = len(scores)
    fires = fires if fires is not None else [False] * n
    return pd.DataFrame({
        "study_code": [f"S{start + i:03d}" for i in range(n)],
        "frame_base": [f"2026010{i % 9}_12000000{i}" for i in range(n)],
        "detector": [detector] * n,
        "raw_score": scores,
        "fires": fires,
        "threshold_at_compute_time": [threshold] * n,
    })


def test_percentile_spread_covers_the_range() -> None:
    """6 candidates span low → high score (not clustered) — the spread that
    pins where a threshold should sit."""
    df = _frame("motion_blur", list(np.linspace(10, 100, 40)))
    cands = select_candidates("motion_blur", df, strategy="percentile_spread", n=6, seed=1)
    assert len(cands) == 6
    pcts = sorted(c.percentile for c in cands)
    assert pcts[0] < 20 and pcts[-1] > 80      # spans the distribution
    assert all(c.band == "spread" for c in cands)


def test_flag_fires_brackets_the_threshold() -> None:
    """flag_fires returns fired + borderline + silent — the threshold-bracketing
    sample. dark_clip_high fires when score > 0.02."""
    scores = [0.001, 0.005, 0.015, 0.019, 0.021, 0.03, 0.1, 0.4]
    fires = [s > 0.02 for s in scores]
    df = _frame("dark_clip_high", scores, fires=fires, threshold=0.02)
    cands = select_candidates("dark_clip_high", df, strategy="flag_fires",
                              n=6, threshold=0.02, seed=1)
    bands = {c.band for c in cands}
    assert "fired" in bands and "silent" in bands
    # at least one borderline (within 0.5σ of 0.02)
    assert any(c.band == "borderline" for c in cands)


def test_already_labelled_frames_are_excluded() -> None:
    """Repeat sessions advance — a frame already labelled is not re-served."""
    df = _frame("motion_blur", list(np.linspace(10, 100, 40)))
    first = select_candidates("motion_blur", df, strategy="percentile_spread", n=6, seed=1)
    done = frozenset((c.study_code, c.frame_base) for c in first)
    second = select_candidates("motion_blur", df, strategy="percentile_spread",
                               n=6, already_labelled=done, seed=1)
    second_keys = {(c.study_code, c.frame_base) for c in second}
    assert not (second_keys & done)            # no overlap with the first session


def test_picked_frame_leads_when_present() -> None:
    """The current-assessment picked frame is included first (operator context)."""
    df = _frame("motion_blur", list(np.linspace(10, 100, 40)))
    pf = (df.iloc[5]["study_code"], df.iloc[5]["frame_base"])
    cands = select_candidates("motion_blur", df, strategy="percentile_spread",
                              n=6, picked_frame=pf, seed=1)
    assert (cands[0].study_code, cands[0].frame_base) == pf


def test_nan_scores_are_dropped() -> None:
    """A frame whose score is NaN (detector couldn't compute) is never a
    candidate — you can't rank on a missing score."""
    scores = [10.0, float("nan"), 50.0, float("nan"), 90.0]
    df = _frame("air_gap_reverberation", scores)
    cands = select_candidates("air_gap_reverberation", df,
                              strategy="percentile_spread", n=6, seed=1)
    assert all(not np.isnan(c.raw_score) for c in cands)
    assert len(cands) == 3                       # only the 3 finite scores


def test_returns_what_is_available_when_thin() -> None:
    """Fewer frames than n → return what exists, no crash, no padding."""
    df = _frame("motion_blur", [10.0, 50.0])
    cands = select_candidates("motion_blur", df, strategy="percentile_spread", n=6, seed=1)
    assert len(cands) == 2


def test_candidates_are_phi_clean() -> None:
    df = _frame("motion_blur", list(np.linspace(10, 100, 10)))
    cands = select_candidates("motion_blur", df, strategy="percentile_spread", n=6, seed=1)
    for c in cands:
        assert isinstance(c, Candidate)
        # only study_code + frame_base identify the frame — no path, no dirname
        assert not hasattr(c, "study_dirname") and not hasattr(c, "mask_path")

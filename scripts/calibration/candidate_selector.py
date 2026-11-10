"""Subsystem C — candidate selection for the calibration labelling UI.

Per detector, pick the frames most worth an operator's label. A cheap,
imperfect detector score routes the expensive, accurate human attention
(qa_data_collection_plan § E). Two strategies, chosen per-detector in the spec:

  * `flag_fires` (dark_clip_high) — the detector fires on a finite threshold,
    so sample around it: clear positives (fired) + the borderline band (within
    ±0.5σ of the threshold, where the cut will land) + clear negatives.
  * `percentile_spread` (the 5 belted detectors; segmentation_low_confidence) —
    no usable threshold, so spread evenly across the cohort score distribution.

Reads the PHI-clean score table (`data/cohort_stats/qa_per_frame_detector_scores.parquet`,
subsystem B). Returns PHI-clean `Candidate`s (study_code + frame_base only).
Excludes already-labelled (study_code, frame_base) so repeat sessions advance
through the cohort rather than re-serving the same frames.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[2]
# PROMOTED to the full sweep pool (272 alive studies / 18,332 frames at 5 fps) —
# the real candidate population. The old 956-frame annotator table
# (`qa_per_frame_detector_scores.parquet`) under-represented the failure modes a
# selector must band around, so the consumer now reads the sweep-pool table
# directly (both are git-tracked; the annotator table stays a regenerable
# artifact). This re-point is the post-gate "promote to canonical" step.
_PARQUET = _REPO / "data" / "cohort_stats" / "qa_per_frame_detector_scores__sweep_pool_5fps.parquet"


@dataclass(frozen=True)
class Candidate:
    """One frame served to the operator for one detector. PHI-clean — carries
    the study_code + frame_base, never the patient-name path."""
    study_code: str
    frame_base: str
    detector: str
    raw_score: float
    percentile: float       # the score's percentile rank within the detector's cohort (0-100)
    band: str               # "fired" | "borderline" | "silent" | "spread"


def load_score_table(path: Path = _PARQUET) -> pd.DataFrame:
    """Load the cohort score table; raises a clear error if it has not been
    built yet (run `python scripts/qa_cohort_failure_histogram.py`)."""
    if not path.exists():
        raise FileNotFoundError(
            f"score table not found at {path} — run "
            f"`python scripts/qa_cohort_failure_histogram.py` to build it (subsystem B)")
    return pd.read_parquet(path)


def _percentile_of(values: np.ndarray, x: float) -> float:
    """The percentile rank (0-100) of x within values."""
    if len(values) == 0:
        return float("nan")
    return float((values < x).mean() * 100.0)


def _key(study_code: str, frame_base: str) -> tuple[str, str]:
    return (study_code, frame_base)


def select_candidates(
    detector: str,
    score_df: pd.DataFrame,
    *,
    strategy: str,
    n: int = 6,
    threshold: Optional[float] = None,
    already_labelled: frozenset = frozenset(),
    picked_frame: Optional[tuple[str, str]] = None,
    seed: Optional[int] = None,
) -> list[Candidate]:
    """Pick up to `n` candidate frames for `detector` (plus `picked_frame` if
    given and not already labelled — preserves the operator's current-assessment
    context).

    `strategy` — "flag_fires" or "percentile_spread" (from the detector spec).
    `threshold` — the detector's current firing threshold (needed for the
        flag_fires borderline band); ignored for percentile_spread.
    `already_labelled` — a set of (study_code, frame_base) to exclude.
    `seed` — tie-break / bucket-pick determinism for reproducible sessions.
    """
    df = score_df[score_df["detector"] == detector].copy()
    df = df[df["raw_score"].notna()]
    # Exclude already-labelled frames so repeat sessions advance.
    if already_labelled:
        mask = ~df.apply(
            lambda r: _key(r["study_code"], r["frame_base"]) in already_labelled,
            axis=1)
        df = df[mask]
    if df.empty:
        return []

    scores = df["raw_score"].to_numpy()
    rng = np.random.default_rng(seed)

    out: list[Candidate] = []
    seen: set[tuple[str, str]] = set()

    def _add(rows: pd.DataFrame, band: str) -> None:
        for _, r in rows.iterrows():
            k = _key(r["study_code"], r["frame_base"])
            if k in seen:
                continue
            seen.add(k)
            out.append(Candidate(
                study_code=r["study_code"], frame_base=r["frame_base"],
                detector=detector, raw_score=float(r["raw_score"]),
                percentile=round(_percentile_of(scores, float(r["raw_score"])), 1),
                band=band))

    # The picked frame from the current assessment always leads, if present
    # in the score table and not already labelled.
    if picked_frame is not None and picked_frame not in already_labelled:
        pf = df[(df["study_code"] == picked_frame[0])
                & (df["frame_base"] == picked_frame[1])]
        _add(pf, "spread")

    if strategy == "flag_fires":
        n_each = max(1, (n - len(out)) // 3)
        fired = df[df["fires"]].sort_values("raw_score", ascending=False)
        silent = df[~df["fires"]]
        _add(fired.head(n_each), "fired")
        if threshold is not None and not np.isnan(threshold):
            sigma = float(np.std(scores)) or 1.0
            df = df.assign(_dist=(df["raw_score"] - threshold).abs())
            borderline = df[df["_dist"] < 0.5 * sigma].sort_values("_dist")
            _add(borderline.head(n_each), "borderline")
        # clear negatives — silent frames furthest below threshold (or lowest score)
        if threshold is not None and not np.isnan(threshold):
            silent = silent.assign(_d=(silent["raw_score"] - threshold))
            silent = silent.sort_values("_d")          # most-below first
        else:
            silent = silent.sort_values("raw_score")
        _add(silent.head(n_each), "silent")
        # top up to n with a percentile spread if any bucket was thin
        if len(out) < n:
            _add(_spread_rows(df, scores, n - len(out), rng), "spread")
    else:   # percentile_spread / score_only
        _add(_spread_rows(df, scores, n - len(out), rng), "spread")

    return out[:n + (1 if picked_frame else 0)]


def _spread_rows(df: pd.DataFrame, scores: np.ndarray, k: int,
                 rng: np.random.Generator) -> pd.DataFrame:
    """k frames spread across the score distribution — one nearest each of k
    evenly-spaced target percentiles."""
    if k <= 0 or df.empty:
        return df.iloc[0:0]
    targets = np.linspace(0, 100, k)
    chosen_idx: list = []
    sorted_df = df.sort_values("raw_score").reset_index(drop=True)
    s = sorted_df["raw_score"].to_numpy()
    for t in targets:
        target_val = np.percentile(scores, t)
        # nearest not-yet-chosen frame to the target score
        order = np.argsort(np.abs(s - target_val))
        for idx in order:
            if idx not in chosen_idx:
                chosen_idx.append(int(idx))
                break
    return sorted_df.iloc[chosen_idx]

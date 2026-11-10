"""Study-level QA aggregation (E4) — demote a per-frame flag that fires across a
whole study to a single STUDY-LEVEL cue.

`dark_clip_high` (under-gain) is bimodal: a handful of studies fire it on ~100% of
their frames, the rest barely at all. A flag pervasive across a study's frames is
a study-level acquisition problem, not a per-frame one — surfacing it on every
frame is noise. These pure helpers compute the per-study firing fraction and pull
out the pervasive studies, so the histogram / UI can present one study-level
under-gain cue instead of painting every frame.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable, Mapping


def per_study_firing_fraction(study_codes: Iterable[str],
                              fired: Iterable[bool]) -> dict:
    """Fraction of each study's frames on which the flag fired. Parallel iterables:
    one study_code and one fired-bool per frame."""
    total: dict = defaultdict(int)
    hits: dict = defaultdict(int)
    for s, f in zip(study_codes, fired):
        total[s] += 1
        hits[s] += int(bool(f))
    return {s: hits[s] / total[s] for s in total}


def pervasive_studies(fractions: Mapping[str, float],
                      threshold: float = 0.8) -> dict:
    """{study: round(fraction, 3)} for studies where the flag fired on >= threshold
    of frames — the study-level (acquisition) cohort, pulled out of per-frame noise."""
    return {s: round(fr, 3) for s, fr in fractions.items() if fr >= threshold}


def study_level_under_gain(study_codes: Iterable[str], fired: Iterable[bool],
                           threshold: float = 0.8) -> dict:
    """Convenience: the pervasive (study-level under-gain) studies from per-frame
    dark_clip_high firings + their firing fraction."""
    return pervasive_studies(per_study_firing_fraction(study_codes, fired), threshold)

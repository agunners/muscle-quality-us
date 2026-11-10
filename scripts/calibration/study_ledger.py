"""Per-study calibration progress ledger (E1).

Akadir's ratified labelling design: do NOT precompute/grade all ~18,332 sweep
frames, and do NOT retreat to the 956 transport triples. Instead hold a ledger of
EXPLORED studies; each session pulls the next unexplored study, segments its 5fps
frames on-demand (frame_loader.load_frame_sweep), selects fit/diverse candidates,
labels, and marks the study explored. PHI-safe — study_code only.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Optional

_REPO = Path(__file__).resolve().parents[2]
_LEDGER = _REPO / "data" / "calibration_explored_studies.json"


def _load(path: Optional[Path] = None) -> dict:
    p = path or _LEDGER
    if p.exists():
        try:
            d = json.loads(p.read_text())
            d.setdefault("explored", {})
            return d
        except (json.JSONDecodeError, OSError):
            return {"explored": {}}
    return {"explored": {}}


def _save(d: dict, path: Optional[Path] = None) -> None:
    p = path or _LEDGER
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, indent=2))


def is_explored(study_code: str, path: Optional[Path] = None) -> bool:
    return study_code in _load(path).get("explored", {})


def mark_explored(study_code: str, n_labeled: int = 0,
                  path: Optional[Path] = None) -> None:
    """Record a study as explored, accumulating its labelled-frame count."""
    d = _load(path)
    entry = d["explored"].get(study_code, {"n_labeled": 0})
    entry["n_labeled"] = int(entry.get("n_labeled", 0)) + int(n_labeled)
    d["explored"][study_code] = entry
    _save(d, path)


def next_unexplored(all_studies: Iterable[str],
                    path: Optional[Path] = None) -> Optional[str]:
    """The first study in `all_studies` not yet explored, or None when all are.

    Deterministic (first-unexplored) for reproducibility; the caller shuffles
    `all_studies` if it wants the random-draw the design calls for."""
    explored = set(_load(path).get("explored", {}))
    for s in all_studies:
        if s not in explored:
            return s
    return None


def explored_count(path: Optional[Path] = None) -> int:
    return len(_load(path).get("explored", {}))

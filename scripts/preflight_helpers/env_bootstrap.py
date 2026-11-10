"""Runtime-bridge bootstrap.

Every Python launcher in muscle_mania imports this first.

Idempotent: if the bridge is already on sys.path / PYTHONPATH (because the caller
sourced scripts/env.sh), this is a no-op. Otherwise it reads scripts/env.sh and
injects the two bridge paths into sys.path so `from rf_quality...` works.

This is the boot fallback the plan calls for: a `bash scripts/env.sh && python ...`
caller would have lost PYTHONPATH to subshell isolation; here we recover.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_BRIDGE_PATHS = (
    str(Path(__file__).resolve().parents[2] / "private" / "muscle_quality_exploration"),
    str(Path(__file__).resolve().parents[2] / "private" / "muscle_model_training/src"),
)


def ensure_bridge() -> list[str]:
    """Make the two bridge paths importable. Returns the paths that were added."""
    added: list[str] = []
    for p in _BRIDGE_PATHS:
        if p not in sys.path:
            sys.path.insert(0, p)
            added.append(p)
    # Mirror into PYTHONPATH so any subprocess we spawn inherits it.
    pp = os.environ.get("PYTHONPATH", "")
    parts = pp.split(":") if pp else []
    changed = False
    for p in _BRIDGE_PATHS:
        if p not in parts:
            parts.insert(0, p)
            changed = True
    if changed:
        os.environ["PYTHONPATH"] = ":".join(p for p in parts if p)
    return added


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


ensure_bridge()

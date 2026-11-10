"""sys.path + env_bootstrap for the semantic-invariants test layer.

The semantic invariants assert SEMANTIC correctness (the QA layer's signals
agree with each other across the dot / tint / cue / detector / gating-table
boundaries), not function-output equality. They live separate from the
unit / preflight / ui test trees because their failure mode is "the
operator sees a contradiction," not "the function returned a different
dict."

Akadir 2026-05-24 audit-deepening: the prior round of fixes passed every
unit + AppTest test and still produced screenshot-level contradictions
(picker dot says no femur + yellow tint paints femur on same thumbnail).
These tests are the layer that catches that.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))
import preflight_helpers.env_bootstrap  # noqa: F401  side-effect

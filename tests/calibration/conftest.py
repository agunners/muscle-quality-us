"""sys.path bridge for the calibration-loop tests — makes `scripts/` importable
so `qa_cohort_failure_histogram` and the `calibration.*` subpackage resolve."""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))
import preflight_helpers.env_bootstrap  # noqa: F401,E402  side-effect (rf_quality bridge)

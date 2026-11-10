"""Unified RF muscle-quality feature pipeline.

See ``upsynergy_plan_02.md`` in the repo root for the architecture and the
locked load-bearing decisions. This package is the MVP implementation of that
plan: registry-driven features + CLI/YAML/run-artifact infrastructure, wiring
to a 3-rater decision log with ``(study_code, operator_id)`` latest-row
keying, and family-specific aggregation (anchor vs agg).
"""

__version__ = "0.1.0"

"""Stage B–F analysis helpers.

These modules are pure compute — the orchestration (reading configs, writing
artifacts, printing audits) lives in ``analysis/03_…08_.py`` scripts at the
repo root. Keeping the helpers here makes them importable from tests and
from the scripts, and keeps the scripts thin.
"""

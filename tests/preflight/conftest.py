"""Shared pytest fixtures + sys.path bridge for preflight_helpers tests."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make scripts/ importable + bridge rf_quality.
_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))
import preflight_helpers.env_bootstrap  # noqa: F401,E402  side-effect


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return _REPO


@pytest.fixture
def tmp_env(monkeypatch, tmp_path) -> Path:
    """Redirect every *writable* env path into a tmp tree.

    Patches only the local-only state dirs (ledger, drafts, runs, preflight,
    training candidates, studies_root, test_artifacts) — NOT the read-only
    corpus paths (transport, cohort_stats, annotator masks, study_codes), so
    a test can still read real data while never touching the real ledger.
    """
    from preflight_helpers import env

    fake_root = tmp_path / "data"
    fake_root.mkdir()
    monkeypatch.setattr(env, "DATA_ROOT", fake_root)
    monkeypatch.setattr(env, "RUNS_JSONL", fake_root / "runs.jsonl")
    monkeypatch.setattr(env, "RUNS_DIR", fake_root / "runs")
    monkeypatch.setattr(env, "DRAFTS_DIR", fake_root / "runs_drafts")
    monkeypatch.setattr(env, "TRAINING_CANDIDATES_DIR", fake_root / "training_candidates")
    monkeypatch.setattr(env, "PREFLIGHT_DIR", fake_root / "preflight")
    monkeypatch.setattr(env, "STUDIES_ROOT", fake_root / "studies_root")
    monkeypatch.setattr(env, "TEST_ARTIFACTS_DIR", fake_root / "test_artifacts")
    return fake_root

"""The v8 study bootstrap — routed runs must produce a bootstrap (the 2026-08-14 gap).

Routed runs persist no per-frame features (`routed_picks` carries bases + gates only), so the
legacy frame pool read 0 frames from every v8 run and the bootstrap rendered blank for the whole
new round (Akadir's AKA016 catch). The fix: with routed runs present, the bootstrap is the
per-feature MEDIAN OVER RUN AGGREGATES — each aggregate already correctly routed, operator
refinements included. These tests pin the mode split synthetically AND on the real AKA016 runs.
"""
from pathlib import Path

import pytest

from preflight_helpers import study_bootstrap as sb

REPO = Path(__file__).resolve().parents[2]


def _routed(run_id, int_cv, rf_mm, n_frames=2):
    return {"run_id": run_id, "lifecycle": "finalized",
            "model_bundle": {"sha256": "cafe" * 16},
            "study_meta": {"age": 30, "sex": "F"},
            "routed_picks": {"femur": {"n_analysis_frames": n_frames},
                             "rf": {"n_analysis_frames": 1}},
            "frame_features": [],                      # exactly the v8 shape that broke it
            "aggregate": {"int_cv_within": int_cv, "rf_thickness_mm": rf_mm,
                          "qmt_provenance": "standard"}}


def _legacy(run_id, rf_mm):
    return {"run_id": run_id, "lifecycle": "finalized",
            "model_bundle": {"sha256": "beef" * 16},
            "study_meta": {"age": 50, "sex": "M"},
            "frame_features": [{"gates": {"use_for_analysis": True, "usable": True},
                                "rf_thickness_mm": rf_mm}],
            "aggregate": {"rf_thickness_mm": rf_mm}}


def test_routed_runs_yield_a_median_bootstrap(monkeypatch):
    runs = [_routed("r1", 0.50, 9.0), _routed("r2", 0.60, 10.0), _routed("r3", 0.52, 9.5)]
    monkeypatch.setattr(sb, "distinct_finalized_runs", lambda *a, **k: runs)
    monkeypatch.setattr(sb, "_finalized_runs_for", lambda *a, **k: runs)
    boot = sb.study_bootstrap("AKATEST", study_uid="NRTESTBOOT")
    assert boot["mode"] == "routed_run_median"
    assert boot["aggregate"]["int_cv_within"] == pytest.approx(0.52)
    assert boot["aggregate"]["rf_thickness_mm"] == pytest.approx(9.5)
    assert boot["n_pooled_frames"] == 9                # 3 runs × (2 femur + 1 rf)
    assert boot["aggregate"]["qmt_provenance"] == "standard"
    assert boot["per_feature"]["rf_thickness_mm"]["per_run"][0]["value"] == 9.0


def test_legacy_runs_keep_the_frame_pool(monkeypatch):
    runs = [_legacy("r1", 8.0), _legacy("r2", 9.0)]
    monkeypatch.setattr(sb, "distinct_finalized_runs", lambda *a, **k: runs)
    monkeypatch.setattr(sb, "_finalized_runs_for", lambda *a, **k: runs)
    boot = sb.study_bootstrap("AGZTEST")
    assert boot["mode"] == "frame_pool"
    assert boot["n_pooled_frames"] == 2


def test_real_aka016_bootstrap_is_no_longer_blank():
    """The exact case from Akadir's review screenshot: 3+ distinct finalized routed runs,
    bootstrap columns blank. Post-fix they must carry values — int_cv near his 0.52 read."""
    if not (REPO / "data" / "runs").exists():
        pytest.skip("run ledger not on this machine")
    boot = sb.study_bootstrap("AKA016", study_uid="NRA3509326")
    if boot["n_distinct_runs"] == 0:
        pytest.skip("no finalized AKA016 runs on this machine")
    assert boot["mode"] == "routed_run_median"
    assert boot["aggregate"].get("int_cv_within") == pytest.approx(0.52, abs=0.1)
    assert boot["n_pooled_frames"] > 0
    assert boot["placements"]["rf_thickness_mm"]["overall"] is not None

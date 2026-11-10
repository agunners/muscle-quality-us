"""qmt_review — phase-4 QMT provenance disclosure helpers.

`frame_qmt_status` wiring is tested here; the underlying `_qmt_measurement_details`
correctness lives in tests/test_qmt_regressions.py and the `qmt_tier` ladder in
tests/preflight/test_aggregation.py. This file owns the new logic:
`selection_qmt_summary` (the verdict aggregation) and the mask -> enriched-dict
wiring.
"""
from __future__ import annotations

import numpy as np

from labeling_app.assessment_pages.qmt_review import (
    frame_qmt_status, selection_qmt_summary, selection_repr_status,
    unpicked_femur_candidates,
)

_RF, _VI = 1, 2          # LABEL ids — mirror labeling_app.measurements


def _no_femur_mask(h: int = 120, w: int = 100) -> np.ndarray:
    """An RF blob stacked over a VI blob, no femur — the no_femur_proxy case."""
    m = np.zeros((h, w), dtype=np.uint8)
    m[30:60, 20:80] = _RF
    m[60:90, 20:80] = _VI
    return m


# ---- frame_qmt_status — the mask -> tier wiring ---------------------------

def test_frame_qmt_status_no_femur_is_tier3() -> None:
    status = frame_qmt_status(_no_femur_mask())
    assert status["tier"] == 3
    assert status["provenance"] in {"no_femur_proxy", "no_measurement"}
    assert status["colour"] == "red"
    assert "not measurable" in status["label"].lower()
    assert isinstance(status["flags"], list)


def test_frame_qmt_status_returns_the_full_shape() -> None:
    status = frame_qmt_status(_no_femur_mask())
    assert set(status) == {"tier", "provenance", "guidance", "flags",
                           "label", "colour", "meaning"}


# ---- selection_qmt_summary — the verdict aggregation ----------------------

def _st(tier: int) -> dict:
    return {"tier": tier}


def test_selection_summary_standard_when_any_tier1() -> None:
    s = selection_qmt_summary([_st(1), _st(2), _st(3)])
    assert s["verdict"] == "standard" and s["colour"] == "green"
    assert (s["n_tier1"], s["n_tier2"], s["n_tier3"]) == (1, 1, 1)


def test_selection_summary_proxy_when_only_tier2() -> None:
    s = selection_qmt_summary([_st(2), _st(2), _st(3)])
    assert s["verdict"] == "proxy" and s["colour"] == "orange"
    assert s["n_tier1"] == 0 and s["n_tier2"] == 2


def test_selection_summary_none_when_all_tier3() -> None:
    s = selection_qmt_summary([_st(3), _st(3)])
    assert s["verdict"] == "none" and s["colour"] == "red"
    assert "NO QMT" in s["message"] and "NO STAR" in s["message"]


def test_selection_summary_empty_is_none() -> None:
    s = selection_qmt_summary([])
    assert s["verdict"] == "none"
    assert (s["n_tier1"], s["n_tier2"], s["n_tier3"]) == (0, 0, 0)


# ---- unpicked_femur_candidates — Tier-1 frames outside the picked set ----

def _scan_entry(tier: int, provenance: str = "standard") -> dict:
    return {"tier": tier, "provenance": provenance}


def test_unpicked_femur_candidates_returns_only_tier1_not_in_picked() -> None:
    """The sweep has a Tier-1 femur frame the operator hasn't picked; the
    picker should surface it as a candidate (Akadir 2026-05-25 § 3)."""
    scan = {
        "f1": _scan_entry(1),
        "f2": _scan_entry(2),  # proxy
        "f3": _scan_entry(1),  # candidate
        "f4": _scan_entry(3),  # no femur
    }
    picked = ["f1", "f2"]
    out = unpicked_femur_candidates(picked, scan)
    assert [r["frame_base"] for r in out] == ["f3"]
    assert out[0]["tier"] == 1


def test_unpicked_femur_candidates_empty_when_all_tier1_already_picked() -> None:
    scan = {"f1": _scan_entry(1), "f2": _scan_entry(1)}
    assert unpicked_femur_candidates(["f1", "f2"], scan) == []


def test_unpicked_femur_candidates_handles_empty_scan() -> None:
    assert unpicked_femur_candidates(["f1"], None) == []
    assert unpicked_femur_candidates(["f1"], {}) == []


def test_unpicked_femur_candidates_sorted_by_sweep_order() -> None:
    """Sorted by frame_base ascending — sweep order is the natural fallback
    when multiple Tier-1 candidates exist outside the picked set."""
    scan = {
        "20260127_151045625": _scan_entry(1),
        "20260127_151030120": _scan_entry(1),
        "20260127_151038400": _scan_entry(1),
    }
    out = unpicked_femur_candidates([], scan)
    assert [r["frame_base"] for r in out] == [
        "20260127_151030120",
        "20260127_151038400",
        "20260127_151045625",
    ]


# ---- selection_repr_status — the four operator-facing cases ---------------

def test_selection_repr_status_unscanned_when_no_scan() -> None:
    """Before pre-scan has run, the picker cannot say — the surface nudges
    the operator to run the scan."""
    s = selection_repr_status(["f1"], None)
    assert s["case"] == "unscanned" and s["level"] == "info"
    assert s["best_unpicked"] is None
    assert "Pre-scan" in s["message"]


def test_selection_repr_status_ok_when_tier1_in_picks() -> None:
    """At least one picked Tier-1 → success; the AI will suggest a repr."""
    scan = {"f1": _scan_entry(1), "f2": _scan_entry(2)}
    s = selection_repr_status(["f1", "f2"], scan)
    assert s["case"] == "ok" and s["level"] == "success"
    assert s["best_unpicked"] is None
    assert s["n_tier1_picked"] == 1


def test_selection_repr_status_unpicked_candidate_when_sweep_has_tier1_picks_dont() -> None:
    """The picked set has no Tier-1 but the sweep does — warn and name the
    best unpicked candidate so the operator can add it."""
    scan = {
        "f1": _scan_entry(2),  # picked, proxy
        "f2": _scan_entry(3),  # picked, no femur
        "f3": _scan_entry(1),  # unpicked Tier-1 candidate
    }
    s = selection_repr_status(["f1", "f2"], scan)
    assert s["case"] == "unpicked_candidate" and s["level"] == "warning"
    assert s["best_unpicked"] == "f3"
    assert s["n_tier1_picked"] == 0
    assert s["n_tier1_sweep"] == 1
    assert "consider picking" in s["message"]


def test_selection_repr_status_no_candidate_when_sweep_has_no_tier1() -> None:
    """No Tier-1 in picks AND none in the sweep — QMT will be degraded; the
    operator should know before proceeding."""
    scan = {
        "f1": _scan_entry(2), "f2": _scan_entry(2), "f3": _scan_entry(3),
    }
    s = selection_repr_status(["f1", "f2"], scan)
    assert s["case"] == "no_candidate" and s["level"] == "error"
    assert s["best_unpicked"] is None
    assert "degraded" in s["message"].lower()

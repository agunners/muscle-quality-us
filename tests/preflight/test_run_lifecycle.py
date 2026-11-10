"""N4 run lifecycle — input_signature stability + draft persistence.

The two load-bearing guarantees:
  * `input_signature` is *stable* (order-independent; drop_reason and the
    QA-capture fields are excluded) and *sensitive* (gate / bundle / class-id
    changes flip it) — it powers
    both N4 cache-invalidation and N5 frame-set dedup.
  * `finalize_draft` promotes-and-deletes: a draft is never in `runs.jsonl`
    before it, and is in `runs.jsonl` (and the draft file gone) after it.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from preflight_helpers import bundle_writer, env, run_lifecycle


# ---- input_signature: stability + sensitivity -----------------------------

def _frames_objs():
    """PickedFrame objects (the run_pipeline path)."""
    from preflight_helpers.run_pipeline import GateAnswers, PickedFrame
    return [
        PickedFrame("20260101_120000000", True,
                    GateAnswers(usable=True, segmentation_perfect=False,
                                use_for_analysis=True, drop_reason=None)),
        PickedFrame("20260101_120000001", False,
                    GateAnswers(usable=True, segmentation_perfect=True,
                                use_for_analysis=True, drop_reason="blurry")),
    ]


def _frames_dicts():
    """Ledger selected_frames dicts (the cached-load / study_bootstrap path)."""
    return [
        {"frame_base": "20260101_120000000", "is_anchor": True,
         "gates": {"usable": True, "segmentation_perfect": False,
                   "use_for_analysis": True, "drop_reason": None}},
        {"frame_base": "20260101_120000001", "is_anchor": False,
         "gates": {"usable": True, "segmentation_perfect": True,
                   "use_for_analysis": True, "drop_reason": "blurry"}},
    ]


_CLASS_IDS = {"muscle_class_id": 1, "femur_class_id": 7, "erosion_px": 3}


def test_input_signature_is_stable() -> None:
    a = run_lifecycle.input_signature(_frames_objs(), "bundlesha", _CLASS_IDS)
    b = run_lifecycle.input_signature(_frames_objs(), "bundlesha", _CLASS_IDS)
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_input_signature_order_independent() -> None:
    fwd = _frames_objs()
    rev = list(reversed(_frames_objs()))
    assert (run_lifecycle.input_signature(fwd, "s", _CLASS_IDS)
            == run_lifecycle.input_signature(rev, "s", _CLASS_IDS))


def test_input_signature_objs_equal_dicts() -> None:
    """A PickedFrame run and its ledger-dict echo hash identically — this is
    what lets study_bootstrap dedup against runs produced by the UI."""
    assert (run_lifecycle.input_signature(_frames_objs(), "s", _CLASS_IDS)
            == run_lifecycle.input_signature(_frames_dicts(), "s", _CLASS_IDS))


def test_input_signature_ignores_drop_reason() -> None:
    base = run_lifecycle.input_signature(_frames_objs(), "s", _CLASS_IDS)
    edited = _frames_objs()
    edited[1].gates.drop_reason = "completely different free text"
    assert run_lifecycle.input_signature(edited, "s", _CLASS_IDS) == base


def test_input_signature_ignores_qa_capture_fields() -> None:
    """drop_reason_codes / drop_reason_other / qa_overrides are operator
    annotations — like drop_reason they must not flip the run signature
    (Build-Step 1; run_lifecycle._norm_frame)."""
    base = run_lifecycle.input_signature(_frames_objs(), "s", _CLASS_IDS)
    edited = _frames_objs()
    edited[1].gates.drop_reason_codes = ("motion_blur", "wrong_site")
    edited[1].gates.drop_reason_other = "a reason not in the taxonomy"
    edited[1].gates.qa_overrides = {"qmt_mm": {"human_decision": "use_anyway"}}
    assert run_lifecycle.input_signature(edited, "s", _CLASS_IDS) == base


@pytest.mark.parametrize("mutate", [
    lambda f: setattr(f[0].gates, "use_for_analysis", False),
    lambda f: setattr(f[0].gates, "usable", False),
    lambda f: setattr(f[0].gates, "segmentation_perfect", True),
    lambda f: setattr(f[1], "is_anchor", True),
])
def test_input_signature_sensitive_to_gate_and_anchor(mutate) -> None:
    base = run_lifecycle.input_signature(_frames_objs(), "s", _CLASS_IDS)
    f = _frames_objs()
    mutate(f)
    assert run_lifecycle.input_signature(f, "s", _CLASS_IDS) != base


def test_input_signature_sensitive_to_bundle_and_class_ids() -> None:
    base = run_lifecycle.input_signature(_frames_objs(), "s", _CLASS_IDS)
    assert run_lifecycle.input_signature(_frames_objs(), "OTHER", _CLASS_IDS) != base
    assert (run_lifecycle.input_signature(_frames_objs(), "s",
                                          {**_CLASS_IDS, "erosion_px": 5}) != base)


# ---- routed picks: honest signature + reload round-trip -------------------
# The v8 routed persistence (Akadir 2026-07-20): a routed record stores its femur/RF frame identities
# so a reload recomputes off the SAME picks. These guard the two load-bearing halves — the signature
# stays byte-identical for sweep-only callers yet folds routed picks in honestly, and a routed draft
# round-trips through disk into the sidebar's rehydrate accessors.

_ROUTED_PICKS = {
    "femur": {"frame_bases": ["f_010", "f_020"],
              "gates": {"f_010": {"usable": True, "use_for_analysis": True},
                        "f_020": {"usable": False, "use_for_analysis": True}},
              "analysis_frame_bases": ["f_010"], "anchor": "f_010", "n_analysis_frames": 1},
    "rf": {"frame_bases": ["f_030"],
           "gates": {"f_030": {"usable": True, "use_for_analysis": True}},
           "analysis_frame_bases": ["f_030"], "anchor": "f_030", "n_analysis_frames": 1},
}


def test_input_signature_default_none_is_backward_compatible() -> None:
    """The new routed_picks param defaults None → the payload is unchanged, so every existing
    sweep-only caller (run_pipeline / session_sig / study_bootstrap) hashes byte-identically."""
    a = run_lifecycle.input_signature(_frames_objs(), "s", _CLASS_IDS)
    b = run_lifecycle.input_signature(_frames_objs(), "s", _CLASS_IDS, routed_picks=None)
    c = run_lifecycle.input_signature(_frames_objs(), "s", _CLASS_IDS, routed_picks={})
    assert a == b == c                                       # falsy routed_picks == absent


def test_input_signature_sensitive_to_routed_picks() -> None:
    base = run_lifecycle.input_signature(_frames_objs(), "s", _CLASS_IDS)
    withrp = run_lifecycle.input_signature(_frames_objs(), "s", _CLASS_IDS,
                                           routed_picks=_ROUTED_PICKS)
    assert withrp != base                                    # routed picks fold into the hash
    # A different femur gate flips it; only the {usable, use_for_analysis} booleans are hashed.
    other = {**_ROUTED_PICKS,
             "femur": {**_ROUTED_PICKS["femur"],
                       "gates": {"f_010": {"usable": True, "use_for_analysis": True},
                                 "f_020": {"usable": True, "use_for_analysis": True}}}}
    assert run_lifecycle.input_signature(_frames_objs(), "s", _CLASS_IDS,
                                         routed_picks=other) != withrp


def test_routed_frame_set_folds_in_identities() -> None:
    record = {"selected_frames": _frames_dicts(), "model_bundle": {"sha256": "s"},
              "resolved_class_ids": _CLASS_IDS,
              "frame_set": {"signature": "sweeponly", "frame_bases_sorted": ["sweep_a"],
                            "n_analysis_frames": 2}}
    fs = run_lifecycle.routed_frame_set(record, _ROUTED_PICKS)
    # honest signature (differs from the sweep-only stub, matches the routed input_signature)
    assert fs["signature"] != "sweeponly"
    assert fs["signature"] == run_lifecycle.input_signature(
        record["selected_frames"], "s", _CLASS_IDS, routed_picks=_ROUTED_PICKS)
    # role:frame_base identities appended; analysis counts summed (2 sweep + 1 femur + 1 rf)
    assert "femur:f_010" in fs["frame_bases_sorted"] and "rf:f_030" in fs["frame_bases_sorted"]
    assert "sweep_a" in fs["frame_bases_sorted"]
    assert fs["n_analysis_frames"] == 4
    # A different femur selection -> a different signature (two selections no longer collide). The
    # signature keys on the per-frame gate identities, so a real re-pick (different gate keys) flips it.
    other = {**_ROUTED_PICKS,
             "femur": {**_ROUTED_PICKS["femur"], "frame_bases": ["f_099"],
                       "gates": {"f_099": {"usable": True, "use_for_analysis": True}}}}
    assert run_lifecycle.routed_frame_set(record, other)["signature"] != fs["signature"]


def test_routed_record_reload_round_trips(tmp_env) -> None:
    """A routed draft persists its routed_picks and, on reload, the sidebar's rehydrate accessors
    restore the femur/RF picks + gates — so a reload-then-refine recomputes off the SAME frames."""
    rec = _build_draft_record()
    rec["routed_picks"] = _ROUTED_PICKS
    rec["frame_set"] = run_lifecycle.routed_frame_set(rec, _ROUTED_PICKS)
    rec["texture_choice"] = "center_rf"
    rec["texture_refine"] = {"erosion_px": 5, "band": [0.1, 0.9]}
    run_lifecycle.write_draft(rec)                            # would crash if a set() leaked
    back = run_lifecycle.read_draft(rec["run_id"])
    assert back["routed_picks"] == _ROUTED_PICKS

    # Reproduce the sidebar rehydrate accessors (previous_runs_sidebar._load_cached_run) verbatim.
    ss: dict = {}
    rp = back.get("routed_picks") or {}
    ss["picked_frames__femur"] = list((rp.get("femur") or {}).get("frame_bases") or [])
    ss["picked_frames__rf"] = list((rp.get("rf") or {}).get("frame_bases") or [])
    ss["gates__femur"] = dict((rp.get("femur") or {}).get("gates") or {})
    ss["gates__rf"] = dict((rp.get("rf") or {}).get("gates") or {})
    ss["texture_choice"] = back.get("texture_choice")
    ss["texture_refine"] = back.get("texture_refine")
    assert ss["picked_frames__femur"] == ["f_010", "f_020"]
    assert ss["picked_frames__rf"] == ["f_030"]
    assert ss["gates__femur"]["f_020"] == {"usable": False, "use_for_analysis": True}
    assert ss["texture_choice"] == "center_rf"
    assert ss["texture_refine"] == {"erosion_px": 5, "band": [0.1, 0.9]}


def test_legacy_record_reload_leaves_routed_empty(tmp_env) -> None:
    """A legacy (sweep-only) record has no routed_picks → the same accessors default empty, so the
    reload behaves exactly as before the v8 change."""
    rec = _build_draft_record()                              # no routed_picks
    run_lifecycle.write_draft(rec)
    back = run_lifecycle.read_draft(rec["run_id"])
    rp = back.get("routed_picks") or {}
    assert list((rp.get("femur") or {}).get("frame_bases") or []) == []
    assert dict((rp.get("rf") or {}).get("gates") or {}) == {}
    assert back.get("texture_choice") is None


# ---- draft persistence ----------------------------------------------------

def _build_draft_record(study_code: str = "TST001") -> dict:
    _run_id, record = bundle_writer.build_record(
        study_code=study_code,
        study_path=Path("/tmp/fake"),
        study_meta={"study_code": study_code},
        selected_frames=[{"frame_base": "20260101_120000000", "is_anchor": True,
                          "gates": {"usable": True, "segmentation_perfect": True,
                                    "use_for_analysis": True, "drop_reason": None}}],
        model_bundle={"sha256": "deadbeef"},
        palette_path=Path("/x/palette.json"), palette_sha256="cafe",
        resolved_class_ids=_CLASS_IDS, runtime_bridge=["/p"],
        frame_features=[{"frame_base": "20260101_120000000", "star_ratio": 1.1}],
        aggregate={"star_ratio": 1.1, "_n_frames_in_aggregate": 1},
        placements={}, mode="live",
        frame_set={"signature": "sig123", "frame_bases_sorted": ["20260101_120000000"],
                   "n_analysis_frames": 1, "bundle_sha256": "deadbeef", "erosion_px": 3},
        lifecycle="draft",
    )
    return record


def test_write_then_read_draft_round_trips(tmp_env) -> None:
    rec = _build_draft_record()
    path = run_lifecycle.write_draft(rec)
    assert path.exists()
    assert path.parent == env.DRAFTS_DIR
    back = run_lifecycle.read_draft(rec["run_id"])
    assert back["run_id"] == rec["run_id"]
    assert back["lifecycle"] == "draft"
    assert back["frame_set"]["signature"] == "sig123"


def test_write_draft_is_idempotent_overwrite(tmp_env) -> None:
    rec = _build_draft_record()
    run_lifecycle.write_draft(rec)
    rec["aggregate"]["star_ratio"] = 9.9            # edit, same run_id
    run_lifecycle.write_draft(rec)
    assert run_lifecycle.read_draft(rec["run_id"])["aggregate"]["star_ratio"] == 9.9
    # Exactly one draft file for this run_id.
    assert len(list(env.DRAFTS_DIR.glob("*.json"))) == 1


def test_draft_not_in_ledger_until_finalized(tmp_env) -> None:
    rec = _build_draft_record()
    run_lifecycle.write_draft(rec)
    # A draft is NOT a ledger run.
    assert bundle_writer.iter_runs() == []
    assert bundle_writer.runs_for_study("TST001") == []

    run_id, run_dir = run_lifecycle.finalize_draft(rec["run_id"])
    # ...now it is, and the draft file is gone.
    assert run_id == rec["run_id"]
    assert not (env.DRAFTS_DIR / f"{rec['run_id']}.json").exists()
    runs = bundle_writer.iter_runs()
    assert len(runs) == 1
    assert runs[0]["run_id"] == rec["run_id"]
    assert runs[0]["lifecycle"] == "finalized"        # promoted
    assert (run_dir / "record.json").exists()


def test_discard_draft(tmp_env) -> None:
    rec = _build_draft_record()
    run_lifecycle.write_draft(rec)
    assert run_lifecycle.discard_draft(rec["run_id"]) is True
    assert run_lifecycle.discard_draft(rec["run_id"]) is False   # already gone
    assert run_lifecycle.discard_draft(None) is False
    assert bundle_writer.iter_runs() == []                        # never hit the ledger


def test_iter_drafts(tmp_env) -> None:
    assert run_lifecycle.iter_drafts() == []
    a = _build_draft_record("AAA")
    b = _build_draft_record("BBB")
    run_lifecycle.write_draft(a)
    run_lifecycle.write_draft(b)
    drafts = run_lifecycle.iter_drafts()
    assert {d["run_id"] for d in drafts} == {a["run_id"], b["run_id"]}


def test_gc_drafts_sweeps_only_old(tmp_env) -> None:
    fresh = _build_draft_record("FRESH")
    stale = _build_draft_record("STALE")
    run_lifecycle.write_draft(fresh)
    stale_path = run_lifecycle.write_draft(stale)
    # Backdate the stale draft 48h.
    old = time.time() - 48 * 3600
    import os
    os.utime(stale_path, (old, old))

    swept = run_lifecycle.gc_drafts(max_age_h=24.0)
    assert swept == [stale["run_id"]]
    assert not stale_path.exists()
    assert (env.DRAFTS_DIR / f"{fresh['run_id']}.json").exists()

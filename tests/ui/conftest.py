"""L3a Streamlit AppTest harness — fixtures + the heavy-seam mocks.

AppTest drives `labeling_app/assessment_app.py` in-process. Two things have to
be faked so the click-through is fast + deterministic + ledger-safe:

  * the model load + segmenter inference (GPU, slow) — phase 2 + phase 4;
  * the assessment pipeline compute — phase 5.

The mocks are *thin*: the fake `run_pipeline` still builds a real ledger
record via `bundle_writer.build_record` and routes it through the REAL
`append_record` / `write_draft`, so the N4 lifecycle (draft vs ledger, accept,
stale-cache) is exercised against production persistence code. Only the
feature *numbers* are synthetic. The ledger + drafts are redirected into
tmp_path — the real `data/runs.jsonl` is never touched.

Study discovery is left REAL: `data/studies_root/` symlinks resolve to the
actual corpus, so the click-through walks a genuine study (AGZ855).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# Make scripts/ + repo root importable + bridge rf_quality.
_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO))
import preflight_helpers.env_bootstrap  # noqa: F401,E402  side-effect

from streamlit.testing.v1 import AppTest  # noqa: E402

from preflight_helpers import bundle_writer, env, run_lifecycle  # noqa: E402
from preflight_helpers.run_pipeline import (  # noqa: E402
    HEADLINE_FEATURES, RunResult, SECONDARY_FEATURES,
)

_APP = _REPO / "labeling_app" / "assessment_app.py"
_ALL_FEATURES = HEADLINE_FEATURES + SECONDARY_FEATURES


# --- the heavy-seam mocks --------------------------------------------------

def _fake_synthetic_mask(h: int = 128, w: int = 128) -> np.ndarray:
    """A multi-class mask with a plausible RF blob + a VI + fat band."""
    m = np.zeros((h, w), dtype=np.uint8)
    m[30:70, 30:90] = 1     # LABEL_RF
    m[70:95, 30:90] = 2     # LABEL_VI
    m[10:20, :] = 8         # LABEL_FAT
    return m


def _fake_image_quality(frame_base: str) -> dict:
    """A synthetic `ImageQualityFeatures` dict — the § 7.1 record shape after
    Build-Step 2 items 8 / 9 / 10a — that `run_pipeline` joins onto every
    frame. The story exercises every Phase-5 QA read surface: rf_thickness_mm
    graded `borderline`, the rest `valid`, and the un-calibrated `wrong_site`
    fired (the H1 `image_quality_pending_calibration_flags` set — recorded,
    excluded from the verdict)."""
    tier_pm = {m: "valid" for m in (
        "rf_thickness_mm", "qmt_mm", "star_ratio", "full_thickness_q50_mm",
        "visible_rf_area_mm2", "int_cv_within", "rf_echo", "muscle_fat_ratio")}
    tier_pm["rf_thickness_mm"] = "borderline"
    return {
        "frame_base": frame_base,
        "image_quality_validity_flags": ["wrong_site"],
        "image_quality_pending_calibration_flags": ["wrong_site"],
        "image_quality_detector_metadata": {
            "wrong_site": {"pending_calibration": True, "rf_fraction": 0.33}},
        "image_quality_tier_per_measurement": tier_pm,
        "image_quality_aggregate_tier": "borderline",
        "image_quality_contract_version": "v0.2",
    }


def _fake_ensure_predicted_masks(picked, raw_dir=None):
    """Drop-in for p04._ensure_predicted_masks — fills the caches with
    synthetic masks + grayscale so the overlay renders without a GPU run.
    Accepts the per-modality `raw_dir` kwarg (ignored — masks are synthetic) that the v8
    per-modality review (p04) and the routed panes (p05) pass for femur/center-RF picks."""
    import streamlit as st
    pm = st.session_state.setdefault("predicted_masks", {})
    fc = st.session_state.setdefault("final_u8_cache", {})
    for fb in picked:
        if fb not in pm:
            pm[fb] = _fake_synthetic_mask()
        if fb not in fc:
            fc[fb] = (np.ones((128, 128), dtype=np.uint8) * 120)


def _fake_run_pipeline(*, study_code, study_path, study_meta, picked_frames,
                       bundle, persist="ledger", bundle_sha=None, mode="live",
                       **_kw) -> RunResult:
    """Drop-in for run_pipeline — synthetic features, but the record build +
    persist routing is the REAL bundle_writer / run_lifecycle code path."""
    n_anal = sum(1 for pf in picked_frames
                 if pf.gates.use_for_analysis and pf.gates.usable)
    agg = {f: float(i + 1) for i, f in enumerate(_ALL_FEATURES)}
    agg["_n_frames_in_aggregate"] = n_anal
    agg["qmt_provenance"] = "standard"   # frame_features are all standard_qmt
    # QA aggregation payloads (Build-Step 2 items 8 / 9 / 10a) — the
    # per-measurement pool tier and the § 14 Step-4 consistency report. Story:
    # rf_thickness_mm pooled `borderline`, every other measurement `valid`.
    agg["_qa_tier"] = {f: "valid" for f in _ALL_FEATURES}
    agg["_qa_tier"]["rf_thickness_mm"] = "borderline"
    agg["_rf_consistency"] = {"active": False, "reason_inert": "too_few_frames",
                              "excluded": [], "n_examined": n_anal}
    frame_features = [{
        "frame_base": pf.frame_base, "is_anchor": pf.is_anchor,
        "gates": {"usable": pf.gates.usable,
                  "segmentation_perfect": pf.gates.segmentation_perfect,
                  "use_for_analysis": pf.gates.use_for_analysis,
                  "drop_reason": pf.gates.drop_reason,
                  # Build-Step 1 structured capture — mirror the real
                  # run_pipeline persist shape (run_pipeline.py:368-373/407-412).
                  "drop_reason_codes": list(pf.gates.drop_reason_codes),
                  "drop_reason_other": pf.gates.drop_reason_other,
                  "qa_overrides": pf.gates.qa_overrides},
        # Synthetic per-frame numbers so study_bootstrap's anchorless
        # pooled-median has real values to aggregate (L3a bootstrap flow).
        # Tier-1 QMT provenance so the qmt_mm / star_ratio gate passes.
        "qmt_provenance": "standard_qmt", "qmt_validity_flags": [],
        # The RF image-quality QA grade run_pipeline joins onto every frame
        # (Build-Step 2 items 8 / 10a) — the § 7.1 ImageQualityFeatures shape.
        "image_quality": _fake_image_quality(pf.frame_base),
        **{f: agg[f] for f in _ALL_FEATURES},
    } for pf in picked_frames]
    placements = {f: {"value": agg[f],
                      "overall": {"n": 100, "pctile": 50.0},
                      "age_matched": {"n": 40, "pctile": 50.0},
                      "sex_matched": None}
                  for f in _ALL_FEATURES}
    resolved = {"muscle_class_id": 1, "fascia_class_id": 5, "skin_class_id": 6,
                "fat_class_id": 8, "femur_class_id": 7, "erosion_px": 3}
    sha = bundle_sha or "fakesha-ui-test"
    sig = run_lifecycle.input_signature(picked_frames, sha, resolved)
    frame_set = {
        "signature": sig,
        "frame_bases_sorted": sorted(pf.frame_base for pf in picked_frames),
        "n_analysis_frames": n_anal, "bundle_sha256": sha, "erosion_px": 3,
    }
    lifecycle = {"ledger": "finalized", "draft": "draft", "none": "none"}[persist]
    ui_meta = (study_meta.to_ui_dict() if hasattr(study_meta, "to_ui_dict")
               else dict(study_meta))
    run_id, record = bundle_writer.build_record(
        study_code=study_code, study_path=study_path, study_meta=ui_meta,
        selected_frames=[{"frame_base": pf.frame_base, "is_anchor": pf.is_anchor,
                          "gates": frame_features[i]["gates"]}
                         for i, pf in enumerate(picked_frames)],
        model_bundle={"sha256": sha, "pkl_path": str(getattr(bundle, "pkl_path", "?"))},
        palette_path=env.PALETTE_PATH, palette_sha256="fakepalette",
        resolved_class_ids=resolved, runtime_bridge=[],
        frame_features=frame_features, aggregate=agg, placements=placements,
        mode=mode, frame_set=frame_set, lifecycle=lifecycle,
    )
    record_path = None
    if persist == "ledger":
        _id, run_dir = bundle_writer.append_record(record)
        record_path = run_dir / "record.json"
    elif persist == "draft":
        record_path = run_lifecycle.write_draft(record)
    return RunResult(run_id=run_id, aggregate=agg, frame_features=frame_features,
                     placements=placements, record_path=record_path, record=record)


def _fake_route_study_aggregate(study_path, sweep_agg, *, picks_femur, picks_rf, bmi,
                                bundle, learn=None, bundle_meta=None, device=None,
                                gates_femur=None, gates_rf=None, texture_choice=None,
                                texture_refine=None):
    """Drop-in for feature_routing.route_study_aggregate — NO torch. route_study_aggregate is the
    only torch seam (compute_modality_aggregate runs only inside it). Honors the REAL torch-free
    keep/drop gate (_gate_kept) and stamps `_routed_modalities` for each role with a usable kept
    pick, carrying the (synthetic) femur/RF feature values already in the sweep aggregate so the
    routed UI branches fire. Mirrors the real contract (a no-pick / all-dropped role is NOT routed)."""
    from preflight_helpers.feature_routing import _gate_kept
    out = dict(sweep_agg or {})
    routed = []
    if _gate_kept(picks_femur, gates_femur):
        routed.append("femur")
        out["qmt_provenance"] = "standard"
    if _gate_kept(picks_rf, gates_rf):
        routed.append("rf")
    out["_routed_modalities"] = routed
    # Mirror the real own-study EI cross-check (sweep int_cv vs center-RF int_cv); the synthetic
    # aggregate makes them identical, so it reports agreement.
    _icv = out.get("int_cv_within")
    if _icv:
        out["_ei_crosscheck"] = {"sweep": _icv, "rf": _icv, "rel_diff": 0.0, "agrees": True}
    # Mirror the real RF-reliability stamp (the synthetic views agree -> verified); honors texture_choice.
    from preflight_helpers.rf_reliability import resolve_texture_state
    _dec = {"source": "center_rf", "int_cv_trusted": True, "reliability": "high", "spread": None,
            "note": "synthetic — three views agree"}
    out["_rf_reliability"] = {"decision": _dec, "texture": resolve_texture_state(_dec, texture_choice)}
    return out


# --- the fixture -----------------------------------------------------------

@pytest.fixture
def ui_app(monkeypatch, tmp_path):
    """A configured AppTest: ledger redirected to tmp, heavy seams mocked."""
    # 1) Redirect the writable ledger paths into tmp (study discovery stays
    #    REAL — STUDIES_ROOT is not patched).
    fake = tmp_path / "data"
    fake.mkdir()
    monkeypatch.setattr(env, "DATA_ROOT", fake)
    monkeypatch.setattr(env, "RUNS_JSONL", fake / "runs.jsonl")
    monkeypatch.setattr(env, "RUNS_DIR", fake / "runs")
    monkeypatch.setattr(env, "DRAFTS_DIR", fake / "runs_drafts")
    monkeypatch.setattr(env, "PREFLIGHT_DIR", fake / "preflight")
    monkeypatch.setattr(env, "TRAINING_CANDIDATES_DIR", fake / "training_candidates")
    monkeypatch.setattr(env, "TEST_ARTIFACTS_DIR", fake / "test_artifacts")

    # 2) Mock the heavy seams in the page modules.
    from labeling_app.assessment_pages import (
        p02_model_selector, p04_frame_review, p05_assessment_view,
    )
    monkeypatch.setattr(p02_model_selector, "load_learner_safe",
                        lambda path: object())          # sentinel learner
    monkeypatch.setattr(p02_model_selector, "_sha256_of",
                        lambda path: "fakesha-ui-test")
    monkeypatch.setattr(p04_frame_review, "_ensure_predicted_masks",
                        _fake_ensure_predicted_masks)
    monkeypatch.setattr(p05_assessment_view, "run_pipeline", _fake_run_pipeline)

    at = AppTest.from_file(str(_APP), default_timeout=30)
    return at


@pytest.fixture
def ui_app_multimodal(ui_app, monkeypatch):
    """`ui_app` + the torch routing seam mocked, for walking a REAL 3-modality study (AKA001/anonaam,
    which has captures/c1_femur_deep + c2_rf_shallow on disk). `study_modalities` stays REAL (reads
    the captures/), so the picker tabs + routed branches are exercised end-to-end; only the
    GPU-bound `route_study_aggregate` is faked. The shared `ui_app` keeps the real route — AGZ855
    has no captures, so it returns `_routed_modalities == []` and the legacy path is unaffected."""
    import preflight_helpers.feature_routing as _fr
    monkeypatch.setattr(_fr, "route_study_aggregate", _fake_route_study_aggregate)
    return ui_app

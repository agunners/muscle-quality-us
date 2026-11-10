"""run_pipeline `persist` routing — ledger vs draft vs none (N4).

Integration test: drives the real annotator-mask pipeline for AGZ855 (no GPU —
`use_annotator_masks=True` skips the learner load) and asserts where the record
lands for each persist mode. The load-bearing N4 guarantee under test:
**persist="draft" never touches runs.jsonl** — a phase-5 result is a draft
until the user accepts it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from preflight_helpers import bundle_writer, env, run_lifecycle
from preflight_helpers.model_registry import default_bundle, list_bundles
from preflight_helpers.run_pipeline import GateAnswers, PickedFrame, run_pipeline
from preflight_helpers.study_metadata import lookup_study


AGZ855_STUDY_PATH = (
    env.GORUNTULER_BACKUP / "anonabn_olcum" / "27Jan26_03"
    / "0000145_anonadm_anonabu_46e_171_110"
)
AGZ855_MASK_DIR = (
    env.ANNOTATOR_MASKS_ROOT / "anonabn_olcum" / "27Jan26_03"
    / "0000145_anonadm_anonabu_46e_171_110"
)
FRAMES = ["20260127_151031646", "20260127_151045625", "20260127_151052505"]
ANCHOR = "20260127_151045625"
_BUNDLE_SHA = "testsha-deterministic"


def _picked() -> list[PickedFrame]:
    return [
        PickedFrame(fb, is_anchor=(fb == ANCHOR),
                    gates=GateAnswers(usable=True, segmentation_perfect=True,
                                      use_for_analysis=True, drop_reason=None))
        for fb in FRAMES
    ]


def _run(persist: str):
    bundle = default_bundle(list_bundles())
    assert bundle is not None, "no valid model bundle in the registry"
    meta = lookup_study(AGZ855_STUDY_PATH, default_code="AGZ855")
    return run_pipeline(
        study_code="AGZ855",
        study_path=AGZ855_STUDY_PATH,
        study_meta=meta,
        picked_frames=_picked(),
        bundle=bundle,
        use_annotator_masks=True,
        annotator_mask_dir=AGZ855_MASK_DIR,
        bundle_sha=_BUNDLE_SHA,
        persist=persist,
        mode="test",
    )


@pytest.mark.integration
def test_persist_draft_never_touches_ledger(tmp_env) -> None:
    res = _run("draft")
    # The draft file exists; runs.jsonl does not.
    draft_path = env.DRAFTS_DIR / f"{res.run_id}.json"
    assert draft_path.exists()
    assert res.record_path == draft_path
    assert not env.RUNS_JSONL.exists()
    assert bundle_writer.iter_runs() == []
    # The record itself is a draft.
    assert res.record["lifecycle"] == "draft"
    # ...and the draft can later be promoted to the ledger.
    run_id, run_dir = run_lifecycle.finalize_draft(res.run_id)
    assert run_id == res.run_id
    assert not draft_path.exists()
    assert len(bundle_writer.iter_runs()) == 1
    assert bundle_writer.iter_runs()[0]["lifecycle"] == "finalized"


@pytest.mark.integration
def test_persist_ledger_appends_finalized(tmp_env) -> None:
    res = _run("ledger")
    runs = bundle_writer.iter_runs()
    assert len(runs) == 1
    assert runs[0]["run_id"] == res.run_id
    assert runs[0]["lifecycle"] == "finalized"
    assert res.record_path == env.RUNS_DIR / res.run_id / "record.json"
    assert not (env.DRAFTS_DIR / f"{res.run_id}.json").exists()


@pytest.mark.integration
def test_persist_none_persists_nothing(tmp_env) -> None:
    res = _run("none")
    assert res.record_path is None
    assert not env.RUNS_JSONL.exists()
    assert not env.DRAFTS_DIR.exists() or not any(env.DRAFTS_DIR.iterdir())
    # The full record is still available on the result for the caller to use.
    assert res.record["run_id"] == res.run_id
    assert res.record["lifecycle"] == "none"
    assert res.record["frame_set"]["signature"]


@pytest.mark.integration
def test_frame_set_signature_matches_input_signature(tmp_env) -> None:
    res = _run("none")
    fs = res.record["frame_set"]
    expected_sig = run_lifecycle.input_signature(
        _picked(), _BUNDLE_SHA, res.record["resolved_class_ids"])
    assert fs["signature"] == expected_sig
    assert fs["frame_bases_sorted"] == sorted(FRAMES)
    assert fs["n_analysis_frames"] == 3
    assert fs["bundle_sha256"] == _BUNDLE_SHA


def test_invalid_persist_raises() -> None:
    # The persist guard fires at the top of run_pipeline, before any file
    # access — so fabricated bundle/meta are enough, no integration data.
    from preflight_helpers.model_registry import BundleEntry
    from preflight_helpers.study_metadata import StudyMeta
    fake_bundle = BundleEntry(pkl_path=Path("/x.pkl"), json_path=Path("/x.json"),
                              source="local", valid=True)
    fake_meta = StudyMeta(study_code="X", age=40, sex="M", height_cm=170,
                          weight_kg=70, bmi=24.2)
    with pytest.raises(ValueError, match="persist must be one of"):
        run_pipeline(study_code="X", study_path=Path("/x"), study_meta=fake_meta,
                     picked_frames=_picked(), bundle=fake_bundle,
                     persist="bogus", mode="test")


@pytest.mark.integration
def test_persist_carries_image_quality_grade(tmp_env) -> None:
    """Build-Step 2 Phase 3 item 8 — every persisted frame record carries its
    RF image-quality QA grade (the § 7.1 ImageQualityFeatures), joined on
    frame_base, and the aggregate carries the per-measurement QA tier the pool
    was drawn from. Annotator-mask mode runs no segmenter, so the grade has the
    14 spacing-free / softmax-free detectors (segmentation_low_confidence is
    honestly skipped)."""
    res = _run("none")
    frames = res.record["frame_features"]
    assert len(frames) == len(FRAMES)
    for ff in frames:
        iq = ff["image_quality"]
        assert set(iq) == {
            "frame_base", "image_quality_validity_flags",
            "image_quality_pending_calibration_flags",   # item-10a gate H1
            "image_quality_detector_metadata",
            "image_quality_tier_per_measurement",
            "image_quality_aggregate_tier", "image_quality_contract_version",
        }
        assert iq["frame_base"] == ff["frame_base"]            # joined on frame_base
        # H1 — the pending set is always a subset of the visible flag set.
        assert set(iq["image_quality_pending_calibration_flags"]) <= set(
            iq["image_quality_validity_flags"])
        assert iq["image_quality_aggregate_tier"] in (
            "valid", "borderline", "invalid", "ungraded")
        assert iq["image_quality_contract_version"] == "v0.2"
        # annotator-mask run -> no segmenter softmax -> seg detector skipped
        assert "segmentation_low_confidence" not in iq["image_quality_detector_metadata"]
    # the aggregate records the per-measurement QA tier the pool was drawn from
    assert "_qa_tier" in res.record["aggregate"]
    assert res.record["aggregate"]["_qa_tier"]["rf_thickness_mm"] in (
        "valid", "borderline", "ungraded", "none")

"""N5 — study bootstrap: distinct-run dedup + anchorless pooled-median.

Load-bearing oracles (from the plan):
  * `distinct_finalized_runs` dedups by `frame_set.signature` (legacy records
    fall back to a signature hashed from `selected_frames`);
  * `study_bootstrap`'s aggregate equals an *independently* recomputed
    `aggregate_features(pooled_distinct_analysis_frames, force_all_median=True)`;
  * a `lifecycle="draft"` record is never pooled (defence in depth — drafts
    don't reach runs.jsonl in the first place).
"""
from __future__ import annotations

from preflight_helpers import bundle_writer
from preflight_helpers.aggregation import aggregate_features
from preflight_helpers.cohort_placement import LEGACY, NEW, OLD, percentile
from preflight_helpers.study_bootstrap import (
    BOOTSTRAP_FEATURES, all_runs_with_dedup_flag, distinct_finalized_runs,
    study_bootstrap, study_bootstrap_summary,
)


def _frame(fb: str, *, use: bool = True, usable: bool = True,
           qmt_provenance: str = "standard_qmt", **feats) -> dict:
    base = {f: 1.0 for f in BOOTSTRAP_FEATURES}
    base.update(feats)
    base.update({
        "frame_base": fb, "is_anchor": False,
        "qmt_provenance": qmt_provenance, "qmt_validity_flags": [],
        "gates": {"usable": usable, "segmentation_perfect": False,
                  "use_for_analysis": use, "drop_reason": None},
    })
    return base


def _record(run_id: str, *, study="STDY", signature: str | None = "sigA",
            lifecycle: str = "finalized", model_sha: str = "modelA",
            created: str = "2026-01-01T00:00:00+00:00",
            frame_features: list | None = None,
            selected_frames: list | None = None,
            aggregate: dict | None = None) -> dict:
    return {
        "run_id": run_id, "study_code": study, "lifecycle": lifecycle,
        "frame_set": ({"signature": signature} if signature else None),
        "selected_frames": selected_frames or [],
        "model_bundle": {"sha256": model_sha},
        "resolved_class_ids": {"erosion_px": 3, "muscle_class_id": 1},
        "study_meta": {"age": 45, "sex": "M"},
        "frame_features": frame_features or [],
        "aggregate": aggregate or {},
        "created_at_utc": created,
    }


def _write(record: dict) -> None:
    bundle_writer.append_record(record)


# ---- dedup ----------------------------------------------------------------

def test_distinct_finalized_runs_dedups_by_signature(tmp_env) -> None:
    # A and B share a signature (B is the later re-run); C is distinct.
    _write(_record("A", signature="sigA", created="2026-01-01T00:00:00+00:00",
                   frame_features=[_frame("f1")]))
    _write(_record("B", signature="sigA", created="2026-01-02T00:00:00+00:00",
                   frame_features=[_frame("f1")]))
    _write(_record("C", signature="sigC", created="2026-01-03T00:00:00+00:00",
                   frame_features=[_frame("f9")]))
    distinct = distinct_finalized_runs("STDY")
    ids = {r["run_id"] for r in distinct}
    assert ids == {"A", "C"}            # earliest of the sigA pair, plus C


def test_all_runs_with_dedup_flag_is_honest_about_duplicates(tmp_env) -> None:
    _write(_record("A", signature="sigA", created="2026-01-01T00:00:00+00:00"))
    _write(_record("B", signature="sigA", created="2026-01-02T00:00:00+00:00"))
    _write(_record("C", signature="sigC", created="2026-01-03T00:00:00+00:00"))
    flagged = {r["run_id"]: r for r in all_runs_with_dedup_flag("STDY")}
    assert len(flagged) == 3                       # every run is still listed
    assert flagged["A"]["_in_bootstrap"] is True and flagged["A"]["_dedup_of"] is None
    assert flagged["B"]["_in_bootstrap"] is False and flagged["B"]["_dedup_of"] == "A"
    assert flagged["C"]["_in_bootstrap"] is True


def test_legacy_record_dedups_against_an_identical_new_record(tmp_env) -> None:
    # A legacy record (no frame_set) and a new record with matching
    # selected_frames / bundle / class-ids must dedup against each other.
    sel = [{"frame_base": "f1", "is_anchor": True,
            "gates": {"usable": True, "segmentation_perfect": False,
                      "use_for_analysis": True, "drop_reason": None}}]
    from preflight_helpers.run_lifecycle import input_signature
    sig = input_signature(sel, "modelA", {"erosion_px": 3, "muscle_class_id": 1})
    _write(_record("LEGACY", signature=None, selected_frames=sel,
                   created="2026-01-01T00:00:00+00:00"))   # no frame_set
    _write(_record("NEW", signature=sig, selected_frames=sel,
                   created="2026-01-02T00:00:00+00:00"))   # frame_set present
    distinct = distinct_finalized_runs("STDY")
    assert {r["run_id"] for r in distinct} == {"LEGACY"}   # earliest kept


# ---- pooling correctness --------------------------------------------------

def test_study_bootstrap_equals_independent_recompute(tmp_env) -> None:
    # Two distinct runs, each with analysis + non-analysis frames.
    r1_frames = [
        _frame("a1", rf_thickness_mm=18.0, star_ratio=1.0, int_cv_within=0.40),
        _frame("a2", rf_thickness_mm=20.0, star_ratio=1.1, int_cv_within=0.50),
        _frame("a3", use=False, rf_thickness_mm=99.0),   # NOT use_for_analysis
    ]
    r2_frames = [
        _frame("b1", rf_thickness_mm=22.0, star_ratio=1.2, int_cv_within=0.60),
        _frame("b2", usable=False, rf_thickness_mm=88.0),  # NOT usable
    ]
    _write(_record("R1", signature="s1", frame_features=r1_frames,
                   created="2026-01-01T00:00:00+00:00"))
    _write(_record("R2", signature="s2", frame_features=r2_frames,
                   created="2026-01-02T00:00:00+00:00"))

    boot = study_bootstrap("STDY")
    assert boot["n_distinct_runs"] == 2
    assert boot["n_total_runs"] == 2
    assert boot["n_pooled_frames"] == 3            # a1, a2, b1 — the others gated out

    # Independent recompute of the pool.
    pooled = [r1_frames[0], r1_frames[1], r2_frames[0]]
    expected = aggregate_features(pooled, force_all_median=True)
    for feat in BOOTSTRAP_FEATURES:
        assert boot["aggregate"][feat] == expected[feat], feat
    # rf_thickness_mm: median(18,20,22) = 20 ; star_ratio: median(1.0,1.1,1.2)=1.1
    assert boot["aggregate"]["rf_thickness_mm"] == 20.0
    assert boot["aggregate"]["star_ratio"] == 1.1
    assert boot["aggregate"]["_force_all_median"] is True


def test_study_bootstrap_qmt_gate_excludes_proxy_frames(tmp_env) -> None:
    # The QMT provenance gate applies to the pooled bootstrap too: a standard
    # frame from one run and a no-femur-proxy frame from another are both
    # pooled, but only the standard frame feeds the bootstrap qmt_mm.
    _write(_record("R1", signature="s1", frame_features=[
        _frame("f1", qmt_mm=40.0, qmt_provenance="standard_qmt")]))
    _write(_record("R2", signature="s2", frame_features=[
        _frame("f2", qmt_mm=18.0, qmt_provenance="no_femur_proxy")]))
    boot = study_bootstrap("STDY")
    assert boot["n_pooled_frames"] == 2
    assert boot["aggregate"]["qmt_mm"] == 40.0          # proxy frame excluded
    assert boot["aggregate"]["qmt_provenance"] == "standard"
    assert boot["aggregate"]["qmt_n_tier3"] == 1


def test_study_bootstrap_excludes_draft_lifecycle(tmp_env) -> None:
    _write(_record("FINAL", signature="s1", lifecycle="finalized",
                   frame_features=[_frame("f1", rf_thickness_mm=15.0)]))
    _write(_record("DRAFT", signature="s2", lifecycle="draft",
                   frame_features=[_frame("f2", rf_thickness_mm=99.0)]))
    boot = study_bootstrap("STDY")
    assert boot["n_distinct_runs"] == 1
    assert boot["contributing_run_ids"] == ["FINAL"]
    assert boot["aggregate"]["rf_thickness_mm"] == 15.0   # the draft never pooled


def test_study_bootstrap_surfaces_mixed_models(tmp_env) -> None:
    _write(_record("R1", signature="s1", model_sha="modelA",
                   frame_features=[_frame("f1")]))
    _write(_record("R2", signature="s2", model_sha="modelB",
                   frame_features=[_frame("f2")]))
    boot = study_bootstrap("STDY")
    assert boot["models_used"] == ["modelA", "modelB"]   # surfaced for the UI warning


def test_per_feature_carries_one_entry_per_distinct_run(tmp_env) -> None:
    _write(_record("R1", signature="s1", frame_features=[_frame("f1")],
                   aggregate={"rf_thickness_mm": 17.0}))
    _write(_record("R2", signature="s2", frame_features=[_frame("f2")],
                   aggregate={"rf_thickness_mm": 19.0}))
    boot = study_bootstrap("STDY")
    per_run = boot["per_feature"]["rf_thickness_mm"]["per_run"]
    assert {pr["run_id"] for pr in per_run} == {"R1", "R2"}
    assert {pr["value"] for pr in per_run} == {17.0, 19.0}


# ---- placement source threading (Phase 4b) --------------------------------
# The pooled running-median must be placed against the SAME live population the
# headline uses (the operator's cohort_source), NOT percentile()'s module-level
# LEGACY default (the retired v1.0 cohort) — else the "Bootstrap pctile" column
# silently ranks against a different cohort than the headline "vs N peers".
# Values seeded below place DIFFERENTLY under OLD vs LEGACY (scratchpad probe_4b:
# rf 15.5 -> p27.5 OLD / p25.7 LEGACY; fat_to_depth 0.14 -> p17.1 / p14.7).

def test_bootstrap_placement_threads_the_cohort_source(tmp_env) -> None:
    _write(_record("R1", signature="s1", frame_features=[
        _frame("f1", rf_thickness_mm=15.5, fat_to_depth_q50=0.14,
               int_cv_within=0.42)]))
    # For every offered population, the bootstrap placement must equal an
    # independent percentile() call with that source — proving the param
    # reaches percentile end-to-end (age/sex come from study_meta: 45 / M).
    for src in (OLD, NEW, LEGACY):
        boot = study_bootstrap("STDY", source=src)
        for feat in BOOTSTRAP_FEATURES:
            val = boot["aggregate"].get(feat)
            exp = percentile(feat, val, age=45, sex="M", source=src)
            assert boot["placements"][feat]["overall"] == exp.overall, (feat, src)


def test_bootstrap_default_source_is_old_not_legacy(tmp_env) -> None:
    _write(_record("R1", signature="s1", frame_features=[
        _frame("f1", rf_thickness_mm=15.5, fat_to_depth_q50=0.14)]))
    default = study_bootstrap("STDY")["placements"]
    old = study_bootstrap("STDY", source=OLD)["placements"]
    legacy = study_bootstrap("STDY", source=LEGACY)["placements"]
    assert default == old                       # default is the live OLD, not LEGACY
    # and OLD genuinely differs from the retired LEGACY cohort (the bug had teeth).
    assert any(default[f]["overall"] != legacy[f]["overall"]
               for f in BOOTSTRAP_FEATURES)


def test_study_bootstrap_empty_when_no_runs(tmp_env) -> None:
    boot = study_bootstrap("NOSUCH")
    assert boot["n_distinct_runs"] == 0
    assert boot["n_total_runs"] == 0
    assert boot["aggregate"] == {}
    summary = study_bootstrap_summary("NOSUCH")
    assert summary["n_distinct_runs"] == 0 and summary["models_used"] == []

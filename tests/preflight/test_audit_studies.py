"""Tests for the audit ORCHESTRATOR/CLI helpers (scripts/audit_studies.py).

The pure reconciliation engine is covered by test_study_audit.py. Here we test the I/O-adjacent glue
the CLI adds — the value coercions, the fact-mapping from a run record / manifest / force row, the
collision dedup, the ledger basename re-index, and the report builders — all HEADLESSLY (monkeypatched
iter_runs, tmp-dir manifests, synthetic populations; no live sheet, no real zip, no real ledger). A
green run here is the floor; the real-data funnel is proven by actually running audit_studies.py.
"""
import json

import audit_studies as A
from preflight_helpers import env
from preflight_helpers import study_audit as sa


# ---------- value coercions ----------
def test_num_coerce():
    assert A._num("26,8") == 26.8        # Turkish decimal comma
    assert A._num("7.1") == 7.1
    assert A._num("3") == 3.0
    assert A._num(0.5) == 0.5
    assert A._num("") is None
    assert A._num(None) is None
    assert A._num("n/a") is None
    assert A._num(True) is None          # a bool is not a measurement


def test_parse_brightness():
    assert A._parse_brightness(["parlaklık 119.0"]) == 119.0
    assert A._parse_brightness(["(deneysel) frekans okuması", "parlaklık 45"]) == 45.0
    assert A._parse_brightness(["(deneysel) overlay henüz doğrulanmadı"]) is None
    assert A._parse_brightness([]) is None
    assert A._parse_brightness(None) is None


# ---------- collision dedup: one occurrence per distinct study, reuse still surfaces ----------
def test_distinct_occurrences_dedups_cross_sheet_keeps_both_patients():
    a = "21jun26_076_829_anonabp_anonadv_30k_165_86_49r"
    b = "21jun26_076_829_anonack_anonadj_28e_179_82_45r"
    # a + b each appear in D and F (4 raw) + a case/space variant -> still exactly 2 distinct studies.
    occ = A._distinct_occurrences([a, b, "  " + a.upper() + " "], [a, b])
    assert sorted(occ, key=str.lower) == sorted([a, b], key=str.lower)
    # fed to the engine, the reused NUMBER fires once, listing BOTH patients (not 4 cross-sheet rows).
    res = sa.audit(sa.Populations(
        datasheet={sa.normalize_key(a): sa.DatasheetFacts(study=a),
                   sa.normalize_key(b): sa.DatasheetFacts(study=b)},
        raw_name_occurrences=occ))
    coll = [f for f in res.global_findings if f.code == sa.NAME_COLLISION]
    assert len(coll) == 1 and len(coll[0].data["occurrences"]) == 2


# ---------- run record -> RunFacts ----------
def test_runfacts_mapping_and_femur_from_routed():
    rec = {"aggregate": {"fat_to_depth_q50": 0.15, "int_cv_within": 0.81, "qmt_mm": 51.4},
           "routed_modalities": ["femur", "rf"],
           "placements": {"int_cv_within": {"overall": {"pctile": 10.0}},
                          "star_ratio": {"overall": {"pctile": 93.2}}},
           "frame_features": [{"drop_reasons": ["rf_mask_empty"], "qmt_validity_flags": []},
                              {"drop_reasons": ["rf_mask_empty", "z"], "qmt_validity_flags": ["q1"]}]}
    rf = A._runfacts(rec, finalized=True)
    assert rf.finalized and rf.has_run
    assert rf.fat_to_depth == 0.15 and rf.int_cv == 0.81 and rf.qmt_mm == 51.4
    assert rf.femur_present is True                       # 'femur' in routed_modalities
    assert rf.placement_pctile == 93.2                    # star_ratio wins the priority order
    assert rf.drop_reasons == ["rf_mask_empty", "z"] and rf.qmt_validity_flags == ["q1"]


def test_runfacts_no_femur_route_is_not_a_qmt_defect():
    rec = {"aggregate": {"qmt_mm": None}, "routed_modalities": ["rf"], "placements": {}}
    rf = A._runfacts(rec, finalized=True)
    assert rf.femur_present is False and rf.qmt_mm is None and rf.placement_pctile is None
    # a sweep/rf-only run with null QMT is EXPECTED (no femur fed) -> no QMT_NULLED; the blank
    # placement is the real B3 finding.
    codes = {f.code for f in sa.audit_row("s", m=sa.MaterializeFacts(materialized=True), r=rf).findings}
    assert sa.QMT_NULLED not in codes and sa.PLACEMENT_BLANK in codes


def test_repr_pctile_priority_and_none():
    assert A._repr_pctile({"star_ratio": {"overall": {"pctile": 5.0}},
                           "qmt_mm": {"overall": {"pctile": 9.0}}}) == 5.0
    assert A._repr_pctile({"qmt_mm": {"overall": {"pctile": 9.0}}}) == 9.0   # star absent -> next
    assert A._repr_pctile({"foo": {"overall": {}}}) is None
    assert A._repr_pctile({}) is None


def test_frame_unions_dedup_sorted():
    drops, flags = A._frame_unions([{"drop_reasons": ["b", "a"], "qmt_validity_flags": ["x"]},
                                    {"drop_reasons": ["a"], "qmt_validity_flags": []},
                                    "not-a-dict"])
    assert drops == ["a", "b"] and flags == ["x"]


# ---------- ledger -> latest finalized, keyed by study_path basename (NOT study_code) ----------
def test_load_runs_latest_finalized_by_basename(monkeypatch):
    base = "17jun06_029_829_anonaam_anonacu_23e_177_66_47.5l"
    records = [
        {"study_path": f"/x/{base}", "study_code": "AKA001", "lifecycle": "finalized",
         "created_at_utc": "2026-06-26T10:00:00+00:00", "aggregate": {"qmt_mm": 40.0},
         "routed_modalities": ["femur", "rf"], "placements": {}},
        {"study_path": f"/y/{base}", "study_code": "AKA001", "lifecycle": "finalized",
         "created_at_utc": "2026-06-27T10:00:00+00:00", "aggregate": {"qmt_mm": 48.7},
         "routed_modalities": ["femur", "rf"], "placements": {}},
        {"study_path": "/z/draftonly_001_829_x_y_30e_1_1_1r", "study_code": "AKA999",
         "lifecycle": "draft", "aggregate": {}, "placements": {}},
    ]
    monkeypatch.setattr("preflight_helpers.bundle_writer.iter_runs", lambda: records)
    runs = A.load_runs()
    rf = runs[sa.normalize_key(base)]
    assert rf.finalized and rf.qmt_mm == 48.7            # the LATEST finalized, by created_at_utc
    draft = runs[sa.normalize_key("draftonly_001_829_x_y_30e_1_1_1r")]
    assert draft.has_run and not draft.finalized          # draft-only -> RUN-NOT-FINALIZED territory


# ---------- manifests + holds -> MaterializeFacts ----------
def test_load_materializes_manifest_hold_and_supersede(tmp_path, monkeypatch):
    study = "17jun06_029_829_anonaam_anonacu_23e_177_66_47.5l"
    sroot = tmp_path / "studies_root"
    mdir = sroot / "op" / "17jun06_029" / study
    mdir.mkdir(parents=True)
    (mdir / "materialize_manifest.json").write_text(json.dumps({
        "study": study,
        "primary": {"identity": "sweep", "caveats": [
            "length-ambiguous (300 frames near sweep/still boundary)",
            "orphan femur capture RawImages_1 (no mp4) is the only femur take — KEPT for review, "
            "not auto-excluded"]},
        "annex": [{"identity": "femur"}, {"identity": "rf"}],
        "excluded_captures": [{"folder": "RawImages_143212", "nframes": 14, "identity": "femur",
                               "reason": "duplicate"}],
    }), encoding="utf-8")

    hold = tmp_path / "hold"
    hold.mkdir()
    held_study = "21jun26_111_829_anonaal_anonadf_51e_174_96_43r"
    (hold / f"{held_study}.hold.json").write_text(
        json.dumps({"study": held_study, "reason": "no sweep capture"}), encoding="utf-8")
    # a STALE hold for the already-materialized study must NOT override the manifest.
    (hold / f"{study}.hold.json").write_text(
        json.dumps({"study": study, "reason": "stale"}), encoding="utf-8")

    monkeypatch.setattr(env, "STUDIES_ROOT", sroot)
    monkeypatch.setattr(A, "HOLD_DIR", hold)

    mats = A.load_materializes({sa.normalize_key(study): "main"})
    anonaam = mats[sa.normalize_key(study)]
    assert anonaam.materialized and anonaam.manifest_captures == 3      # primary + 2 annex
    assert anonaam.expected_captures == 3                            # main profile = 3 captures
    assert len(anonaam.excluded_captures) == 1                       # the AUTO orphan duplicate rides through
    assert anonaam.ambiguous_captures == [                          # ONLY the ASK lone-orphan caveat
        "orphan femur capture RawImages_1 (no mp4) is the only femur take — KEPT for review, "
        "not auto-excluded"]
    anonaal = mats[sa.normalize_key(held_study)]
    assert not anonaal.materialized and anonaal.hold_reason == "no sweep capture"
    # the tmp manifest carries excluded_captures -> it is NOT a pre-Phase-2 manifest.
    assert anonaam.manifest_pre_phase2 is False


def test_load_runs_none_lifecycle_is_not_finalized(monkeypatch):
    # bundle_writer documents lifecycle in {'draft','finalized','none'}. A 'none' (or any non-final)
    # run must NOT bucket as finalized — it would inflate the processed/validatable funnel.
    base = "26jun26_050_829_none_run_30e_170_70_40r"
    none_rec = {"study_path": f"/x/{base}", "study_code": "AKA050", "lifecycle": "none",
                "aggregate": {"qmt_mm": 1.0}, "placements": {}, "routed_modalities": []}
    monkeypatch.setattr("preflight_helpers.bundle_writer.iter_runs", lambda: [none_rec])
    rf = A.load_runs()[sa.normalize_key(base)]
    assert rf.has_run and not rf.finalized
    # a legacy record with the field MISSING is still treated as finalized (reader back-compat).
    legacy = {"study_path": f"/x/{base}", "study_code": "AKA050",
              "aggregate": {"qmt_mm": 1.0}, "placements": {}, "routed_modalities": []}
    monkeypatch.setattr("preflight_helpers.bundle_writer.iter_runs", lambda: [legacy])
    assert A.load_runs()[sa.normalize_key(base)].finalized is True


def test_load_materializes_flags_pre_phase2_manifest(tmp_path, monkeypatch):
    # Phase 2 ALWAYS writes excluded_captures (even []); a manifest WITHOUT the key predates Phase 2,
    # so the ORPHAN-RAWIMAGES auto-correct could not have run on it.
    pre = "14jun06_071_829_anonace_anonacn_53e_173_88_47r"     # no excluded_captures -> pre-Phase-2
    post = "17jun06_029_829_anonaam_anonacu_23e_177_66_47.5l"    # excluded_captures present -> Phase-2
    sroot = tmp_path / "studies_root"
    for study, with_excl in ((pre, False), (post, True)):
        mdir = sroot / "op" / study
        mdir.mkdir(parents=True)
        man = {"study": study, "primary": {"identity": "sweep"}, "annex": []}
        if with_excl:
            man["excluded_captures"] = []
        (mdir / "materialize_manifest.json").write_text(json.dumps(man), encoding="utf-8")
    monkeypatch.setattr(env, "STUDIES_ROOT", sroot)
    monkeypatch.setattr(A, "HOLD_DIR", tmp_path / "nohold")
    mats = A.load_materializes({})
    assert mats[sa.normalize_key(pre)].manifest_pre_phase2 is True
    assert mats[sa.normalize_key(post)].manifest_pre_phase2 is False


def test_coverage_notes_surface_deferred_and_pre_phase2_and_ride_the_report():
    nk = sa.normalize_key
    anonace = "14jun06_071_829_anonace_anonacn_53e_173_88_47r"
    res = sa.audit(sa.Populations(
        datasheet={nk(anonace): sa.DatasheetFacts(study=anonace)},
        zips={nk(anonace): sa.ZipFacts(study=anonace)},
        materializes={nk(anonace): sa.MaterializeFacts(materialized=True, manifest_pre_phase2=True)}))
    notes = A._coverage_notes(res)
    assert any("DEPTH-OFFSPEC" in n for n in notes)                 # the deferred-check note (always)
    assert any("predate Phase 2" in n and "1/1" in n for n in notes)  # the pre-Phase-2 coverage note
    # they ride into the JSON report AND the rendered markdown (the legend stops advertising a dead check).
    report = A.build_report(res, {"M": {"loaded": True, "n": 1, "n_loaded": 1}}, skips=[])
    assert report["coverage_notes"] == notes
    assert "## Coverage notes" in A.render_markdown(report, res)


# ---------- report builders (synthetic populations; no I/O) ----------
def _synthetic_result():
    a = "21jun26_076_829_anonabp_anonadv_30k_165_86_49r"
    b = "21jun26_076_829_anonack_anonadj_28e_179_82_45r"
    anonace = "14jun06_071_829_anonace_anonacn_53e_173_88_47r"
    nk = sa.normalize_key
    pops = sa.Populations(
        datasheet={nk(a): sa.DatasheetFacts(study=a), nk(b): sa.DatasheetFacts(study=b),
                   nk(anonace): sa.DatasheetFacts(study=anonace)},
        zips={nk(anonace): sa.ZipFacts(study=anonace)},
        materializes={nk(anonace): sa.MaterializeFacts(materialized=True)},
        runs={nk(anonace): sa.RunFacts(has_run=True, finalized=True, placement_pctile=50.0,
                                    fat_to_depth=0.3, int_cv=0.2, femur_present=True, qmt_mm=51.0)},
        forces={nk(anonace): sa.ForceFacts(in_force_sheet=True, matched_kayit=True, isometric_present=True,
                                        isokinetic_present=True, isokinetic_cov=1.6),
                nk(a): sa.ForceFacts(in_force_sheet=True, matched_kayit=True, isometric_present=True,
                                     isokinetic_present=True, isokinetic_cov=62.5)},
        raw_name_occurrences=[a, b],
        force_orphans=["ghost_001_829_x_y_30e_1_1_1r"])
    return sa.audit(pops)


def test_build_report_is_json_safe_and_has_funnel():
    res = _synthetic_result()
    loaded = {"D": {"loaded": True, "n": 3, "n_loaded": 3}, "Z": {"loaded": True, "n": 1, "n_loaded": 1},
              "M": {"loaded": True, "n": 1, "n_loaded": 1}, "R": {"loaded": True, "n": 1, "n_loaded": 5},
              "F": {"loaded": True, "n": 2, "n_loaded": 2}}
    report = A.build_report(res, loaded, skips=[])
    json.dumps(report, ensure_ascii=False)                       # must serialize end-to-end
    assert report["funnel"]["recorded"] == 3
    assert report["funnel"]["validatable"] == 1 and report["funnel"]["reliable"] == 1  # only anonace
    assert report["set_diffs"]["counts"]["M"] == 1
    # CoV table carries both force-bearing studies; the 62.5 one is unreliable, anonace is reliable.
    by_study = {r["study"]: r for r in report["cov_table"]}
    assert by_study[next(s for s in by_study if "anonace" in s)]["reliable"] is True
    assert by_study[next(s for s in by_study if "anonabp" in s)]["reliable"] is False


def test_render_markdown_sections_ordered():
    res = _synthetic_result()
    loaded = {"D": {"loaded": True, "n": 3, "n_loaded": 3}, "Z": {"loaded": True, "n": 1, "n_loaded": 1},
              "M": {"loaded": True, "n": 1, "n_loaded": 1}, "R": {"loaded": True, "n": 1, "n_loaded": 5},
              "F": {"loaded": True, "n": 2, "n_loaded": 2}}
    md = A.render_markdown(A.build_report(res, loaded, skips=[]), res)
    for header in ["## Readiness funnel", "## Populations loaded", "## Population set-differences",
                   "## Findings", "## Reused study numbers", "## Force reliability"]:
        assert header in md, header
    # funnel first, then set-diffs, then findings (the required order).
    assert md.index("## Readiness funnel") < md.index("## Population set-differences") \
        < md.index("## Findings")
    # the R ledger note fires when n (1) != n_loaded (5).
    assert "legacy-cohort" in md


# ---------- the audit is READ-ONLY end-to-end (report-only; no sheet write) ----------
def test_audit_is_read_only_no_sheet_writes():
    import inspect
    # The audit only READS the five populations and writes its findings to the console / the report
    # (data/audit/*); it never mutates a sheet. The whole module references no sheets-WRITE method,
    # and main() exposes no write flag (the operator-nudge column + its apply path were cut — the
    # operator's one feedback signal is the check_studies Kontrol watcher).
    modsrc = inspect.getsource(A)
    for forbidden in ("sheets_values_update", "sheets_values_clear", "sheets_batch_update"):
        assert forbidden not in modsrc, f"audit_studies references {forbidden}"
    assert "--apply" not in inspect.getsource(A.main)

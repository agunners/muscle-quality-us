"""Tests for the pure study-consistency audit engine (scripts/preflight_helpers/study_audit.py).

All synthetic — no live sheet, no zip, no runs.jsonl (the engine is I/O-free). Coverage:
  * one PASS + one FAIL per row-level check code (parametrized);
  * the two population-level codes (NAME-COLLISION, FORCE-ORPHAN) via the global path;
  * collisions surfaced, NEVER deduped (the 26jun26_021 reuse);
  * normalize_key parity with sync_force_measures._norm (the pinned join key);
  * the readiness tiers (validatable-N = processed + isometric present);
  * the CoV reliability gate (7.1 passes, 26.8 fails at 15%);
  * fail-open (a raising check leaves the row's other findings intact);
  * a CHECKS-registry contract test (the legend's single source of truth).
"""
import pytest

import sync_force_measures as sfm
from preflight_helpers import study_audit as sa


# ---------- synthetic fact builders ----------
_STUDY = "26jun26_001_829_anonabh_anonaai_30e_170_70_40r"


def _zip(**kw):
    return sa.ZipFacts(**{"study": _STUDY, **kw})


def _ds(**kw):
    return sa.DatasheetFacts(**{"study": _STUDY, **kw})


def _mat(**kw):
    return sa.MaterializeFacts(**{"materialized": True, **kw})


def _run(**kw):
    # a finalized run with all-good numbers; override one field to fire a single B3 code.
    base = dict(has_run=True, finalized=True, fat_to_depth=0.3, int_cv=0.2,
                placement_pctile=50.0, femur_present=True, qmt_mm=12.0)
    return sa.RunFacts(**{**base, **kw})


def _force(**kw):
    return sa.ForceFacts(**{"in_force_sheet": True, "matched_kayit": True, **kw})


def _codes(row):
    return {f.code for f in row.findings}


# ---------- one PASS + one FAIL per row-level check code ----------
# (code, fire_kwargs, clean_kwargs) — only the TARGET code is asserted, so incidental findings from
# other boundaries (e.g. a SET-M-NO-R on a materialized-but-unscored fixture) don't matter.
_CASES = [
    # B0 set-diffs
    (sa.SET_Z_NO_D, dict(z=_zip()), dict(z=_zip(), d=_ds())),
    (sa.SET_D_NO_Z, dict(d=_ds()), dict(z=_zip(), d=_ds())),
    (sa.SET_M_NO_R, dict(m=_mat()), dict(m=_mat(), r=_run())),
    (sa.SET_R_NO_M, dict(r=_run()), dict(m=_mat(), r=_run())),
    # B1 source <-> datasheet
    (sa.ARTIFACT_COUNT, dict(d=_ds(artifacts_mrd="3/3/3"), z=_zip(artifacts_mrd="2/3/3")),
                        dict(d=_ds(artifacts_mrd="3/3/3"), z=_zip(artifacts_mrd="3/3/3"))),
    (sa.MODALITY_MISSING, dict(z=_zip(has_femur=False)), dict(z=_zip())),
    (sa.NAME_UNPARSED, dict(z=_zip(name_parsed=False)), dict(z=_zip())),
    (sa.OPERATOR_FOLDER_MISMATCH, dict(d=_ds(operator="anonacl"), z=_zip(folder_operator="anonabx")),
                                  dict(d=_ds(operator="anonaaf"), z=_zip(folder_operator="anonaad"))),  # alias
    (sa.WASHOUT, dict(z=_zip(brightness=120.0)), dict(z=_zip(brightness=45.0))),
    # B2 datasheet <-> processing
    (sa.MATERIALIZE_HOLD, dict(m=_mat(materialized=False, hold_reason="no sweep capture")), dict(m=_mat())),
    (sa.MANIFEST_MISMATCH, dict(m=_mat(manifest_captures=2, expected_captures=3)),
                           dict(m=_mat(manifest_captures=3, expected_captures=3))),
    (sa.DEPTH_OFFSPEC, dict(m=_mat(depth_in_spec=False)), dict(m=_mat(depth_in_spec=True))),
    (sa.ORPHAN_RAWIMAGES, dict(m=_mat(excluded_captures=[{"folder": "RawImages_143212", "nframes": 14,
                                                          "identity": "femur", "reason": "duplicate"}])),
                          dict(m=_mat())),
    (sa.PAIRING_AMBIGUOUS, dict(m=_mat(ambiguous_captures=["RawImages_143212 (lone orphan femur)"])),
                           dict(m=_mat())),
    # B3 processing <-> numbers
    (sa.RUN_NOT_FINALIZED, dict(m=_mat(), r=sa.RunFacts(has_run=True, finalized=False)),
                           dict(m=_mat(), r=_run())),
    (sa.FAT_DEPTH_HIGH, dict(m=_mat(), r=_run(fat_to_depth=1.5)), dict(m=_mat(), r=_run())),
    (sa.INT_CV_NEG, dict(m=_mat(), r=_run(int_cv=-0.1)), dict(m=_mat(), r=_run())),
    (sa.PLACEMENT_BLANK, dict(m=_mat(), r=_run(placement_pctile=None)), dict(m=_mat(), r=_run())),
    (sa.QMT_NULLED, dict(m=_mat(), r=_run(qmt_mm=None)), dict(m=_mat(), r=_run())),  # femur present
    (sa.FRAME_DROPS, dict(m=_mat(), r=_run(drop_reasons=("rf_mask_empty",))), dict(m=_mat(), r=_run())),
    # B4 force (matched-row cases; FORCE-ORPHAN is global, tested separately)
    (sa.FORCE_BLANK, dict(f=_force(all_blank=True)), dict(f=_force(isometric_present=True))),
    (sa.COV_GATE, dict(f=_force(isokinetic_present=True, isokinetic_cov=26.8)),
                  dict(f=_force(isokinetic_present=True, isokinetic_cov=7.1))),
]


@pytest.mark.parametrize("code,fire,clean", _CASES, ids=[c[0] for c in _CASES])
def test_each_check_fires_and_clears(code, fire, clean):
    assert code in _codes(sa.audit_row(_STUDY, **fire)), f"{code} should fire on its fixture"
    assert code not in _codes(sa.audit_row(_STUDY, **clean)), f"{code} should be clean on its fixture"


def test_every_case_code_is_registered_and_distinct():
    # the param table must exercise every row-level code exactly once (no drift vs CHECKS).
    case_codes = [c[0] for c in _CASES]
    assert len(case_codes) == len(set(case_codes))
    row_level = set(sa.CHECKS) - {sa.NAME_COLLISION, sa.FORCE_ORPHAN, sa.CHECK_ERROR}
    assert set(case_codes) == row_level


# ---------- collisions: surfaced, never deduped ----------
def test_collision_surfaced_not_deduped():
    # two DIFFERENT patients both numbered 26jun26_021 — the number is reused, the full names differ.
    a = "26jun26_021_829_anonaac_anonaai_30e_170_70_40r"
    b = "26jun26_021_831_anonaar_anonads_40k_160_60_44l"
    pops = sa.Populations(
        datasheet={sa.normalize_key(a): sa.DatasheetFacts(study=a),
                   sa.normalize_key(b): sa.DatasheetFacts(study=b)},
        raw_name_occurrences=[a, b],
    )
    res = sa.audit(pops)
    collisions = [f for f in res.global_findings if f.code == sa.NAME_COLLISION]
    assert len(collisions) == 1                                   # ONE finding...
    assert set(collisions[0].data["occurrences"]) == {a, b}       # ...listing BOTH occurrences
    assert collisions[0].data["number"] == "26jun26_021"
    assert len(res.rows) == 2                                     # NEITHER row dropped (distinct keys)
    assert all(r.consistent for r in res.rows)                   # a reused number breaks nothing
    # ratified taxonomy: cosmetic REMIND, not a blocking FAIL.
    assert collisions[0].action == sa.REMIND and collisions[0].severity != sa.FAIL


def test_force_orphan_is_global():
    pops = sa.Populations(force_orphans=["99zzz_001_829_ghost_x_30e_170_70_40r"])
    res = sa.audit(pops)
    orphans = [f for f in res.global_findings if f.code == sa.FORCE_ORPHAN]
    assert len(orphans) == 1 and "ghost" in orphans[0].data["study"]


# ---------- the pinned join key ----------
def test_normalize_key_parity():
    # byte-identical to the force-sync join — and the SAME underlying function (parity by construction).
    assert sa._force_norm is sfm._norm
    for name in ["14jun06_071_829_anonace_anonacn_53e_173_88_47r\n",
                 "21jun26_060_829_Anonadp_Anonacq_39k_160_61_48r",
                 "  21jun26_060_829_ANONABB_x_40k_160_60_44l  "]:
        assert sa.normalize_key(name) == sfm._norm(name)


# ---------- the readiness tiers (validatable-N = processed + isometric present) ----------
def test_readiness_tiers():
    # Tier 0: recorded (datasheet row), no FAIL -> consistent, but not processed.
    t0 = sa.audit_row(_STUDY, d=_ds())
    assert t0.recorded and t0.consistent and not t0.processed

    # A FAIL (missing modality) drops a recorded study out of consistent.
    bad = sa.audit_row(_STUDY, z=_zip(has_femur=False), m=_mat(), r=_run())
    assert bad.recorded and not bad.consistent and not bad.processed

    # Tier 1: + materialized + finalized run -> processed.
    t1 = sa.audit_row(_STUDY, d=_ds(), z=_zip(), m=_mat(), r=_run())
    assert t1.processed and not t1.validatable

    # Tier 2 (the pilot N): processed REQUIRES isometric for validatable.
    no_iso = sa.audit_row(_STUDY, d=_ds(), z=_zip(), m=_mat(), r=_run(), f=_force(isometric_present=False))
    assert no_iso.processed and not no_iso.validatable
    t2 = sa.audit_row(_STUDY, d=_ds(), z=_zip(), m=_mat(), r=_run(), f=_force(isometric_present=True))
    assert t2.validatable and not t2.reliable

    # Tier 3: + reliable isokinetic (CoV <= gate).
    t3 = sa.audit_row(_STUDY, d=_ds(), z=_zip(), m=_mat(), r=_run(),
                      f=_force(isometric_present=True, isokinetic_present=True, isokinetic_cov=8.8))
    assert t3.reliable

    fn = sa.funnel([t0, bad, t1, no_iso, t2, t3])
    assert (fn.recorded, fn.consistent, fn.processed, fn.validatable, fn.reliable) == (6, 5, 4, 2, 1)
    assert fn.validatable_keys.count(_STUDY) == 2 and fn.reliable_keys == [_STUDY]


def test_processed_requires_the_source_zip_reconciled():
    # The finding's exact repro: a datasheet study that is materialized + finalized + carries isometric
    # force but is ABSENT from the zip mirror (z=None) must NOT count as processed/validatable. check_b1
    # returns early on z=None, so the MODALITY-MISSING FAIL that gates a femur/sweep-missing study can
    # never fire — the source was never reconciled, so it must not enter the pilot N on a missing mirror.
    no_zip = sa.audit_row(_STUDY, d=_ds(), m=_mat(), r=_run(), f=_force(isometric_present=True))
    assert no_zip.recorded and no_zip.consistent           # a datasheet row with no FAIL is consistent...
    assert not no_zip.processed and not no_zip.validatable  # ...but NOT processed (source unreconciled)
    assert sa.SET_D_NO_Z in _codes(no_zip)                 # and the WHY is surfaced
    # the SAME study WITH its zip present (modalities reconciled) IS validatable — the gate is exactly Z.
    with_zip = sa.audit_row(_STUDY, d=_ds(), z=_zip(), m=_mat(), r=_run(), f=_force(isometric_present=True))
    assert with_zip.processed and with_zip.validatable


def test_number_prefix_routes_through_the_one_grammar():
    # canonical date_count_probe schema: grammar and the raw split agree.
    assert sa._number_prefix("26jun26_021_829_anonaac_anonaai_30e_170_70_40r") == "26jun26_021"
    # probe BEFORE the count: a raw '_'-split would take the probe (829) as the number; the grammar
    # skips the probe and recovers the real count (021) — the reuse-bypass the finding flags.
    assert sa._number_prefix("26jun26_829_021_anonaac_anonaai_30e_170_70_40r") == "26jun26_021"
    # a count-less legacy name (no count token) -> grammar yields no count -> falls back to the raw split.
    assert sa._number_prefix("10jun_anonace_anonacn_53e_173_88") == "10jun_anonace"


def test_depth_offspec_is_deferred_not_a_dead_check():
    # DEPTH-OFFSPEC is registered but DEFERRED under v8 relative-rank depth (the loader never feeds
    # depth_in_spec). Flagged so the legend cannot advertise an inert check; it is the ONLY deferred one.
    assert sa.CHECKS[sa.DEPTH_OFFSPEC].deferred is True
    assert [c for c, s in sa.CHECKS.items() if s.deferred] == [sa.DEPTH_OFFSPEC]
    leg = {r["code"]: r for r in sa.legend()}              # the legend carries the flag verbatim
    assert leg[sa.DEPTH_OFFSPEC]["deferred"] is True
    assert all(leg[c]["deferred"] is False for c in sa.CHECKS if c != sa.DEPTH_OFFSPEC)
    # the check LOGIC still fires when fed directly, so a future fixed-depth protocol can re-wire it.
    assert sa.DEPTH_OFFSPEC in _codes(sa.audit_row(_STUDY, m=_mat(depth_in_spec=False)))


# ---------- the CoV reliability gate ----------
def test_cov_gate():
    assert sa.isokinetic_reliable(7.1) is True            # under the 15% gate
    assert sa.isokinetic_reliable(26.8) is False          # over it
    assert sa.isokinetic_reliable(15.0) is True           # the boundary is inclusive
    assert sa.isokinetic_reliable(None) is False          # a missing CoV cannot be vouched for
    # the finding fires only over the gate, and only on the isokinetic CoV (isometric is never gated).
    assert sa.COV_GATE in _codes(sa.audit_row(_STUDY, f=_force(isokinetic_present=True, isokinetic_cov=26.8)))
    assert sa.COV_GATE not in _codes(sa.audit_row(_STUDY, f=_force(isokinetic_present=True, isokinetic_cov=7.1)))


# ---------- fail-open: a raising check must not abort the row ----------
def test_fail_open():
    def _boom(_row):
        raise RuntimeError("synthetic check explosion")

    # a real FAIL fixture + a raising check prepended -> the genuine finding + readiness still compute.
    row = sa.audit_row(_STUDY, z=_zip(has_femur=False), checks=[_boom, *sa._ROW_CHECKS])
    codes = _codes(row)
    assert sa.MODALITY_MISSING in codes          # the other checks ran...
    assert sa.CHECK_ERROR in codes               # ...and the raise is surfaced, never silent
    assert row.recorded and not row.consistent   # readiness still computed (FAIL present)
    # the diagnostic carries which check blew up.
    err = next(f for f in row.findings if f.code == sa.CHECK_ERROR)
    assert "_boom" in err.message


# ---------- CHECKS registry contract (the legend's single source of truth) ----------
def test_checks_registry_contract():
    # every registered spec is well-formed.
    for code, spec in sa.CHECKS.items():
        assert spec.severity in sa._SEVERITIES, code
        assert spec.audience in sa._AUDIENCES, code
        assert spec.action in sa._ACTIONS, code
        assert spec.boundary in sa._BOUNDARIES, code
        assert spec.blurb.strip(), code
        # _finding stamps severity/audience/action straight from the registry (no inline drift).
        f = sa._finding(code, "msg")
        assert (f.severity, f.audience, f.action) == (spec.severity, spec.audience, spec.action)

    # every NAMED code constant on the module is registered (and vice versa) — no orphan codes.
    const_codes = {v for k, v in vars(sa).items()
                   if k.isupper() and isinstance(v, str) and v in sa.CHECKS}
    assert const_codes == set(sa.CHECKS)

    # the legend reads the registry verbatim (one row per code, fields intact).
    leg = sa.legend()
    assert {r["code"] for r in leg} == set(sa.CHECKS)
    for r in leg:
        spec = sa.CHECKS[r["code"]]
        assert (r["severity"], r["audience"], r["action"], r["boundary"], r["blurb"]) == \
               (spec.severity, spec.audience, spec.action, spec.boundary, spec.blurb)

    # the ratified action taxonomy anchors (the load-bearing examples).
    assert sa.CHECKS[sa.NAME_COLLISION].action == sa.REMIND      # cosmetic, breaks nothing
    assert sa.CHECKS[sa.ORPHAN_RAWIMAGES].action == sa.AUTO      # auto-corrected upstream
    assert sa.CHECKS[sa.MODALITY_MISSING].action == sa.ASK       # integrity risk + ambiguous
    assert sa.CHECKS[sa.MODALITY_MISSING].severity == sa.FAIL    # ...and it blocks readiness

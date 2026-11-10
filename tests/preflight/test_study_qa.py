"""Unit tests for the unified per-study QA (scripts/study_qa.py).

study_qa builds on plan_zip's relative-rank classification + DICOM pairing; here we mock plan_zip with
synthetic StudyPlans so the QA LOGIC is locked without shipping real zips (the classifier itself is
tested in test_study_deconfliction). These pin the behaviours that motivated the unification:
a tweaked-deeper femur is NOT falsely 'eksik' (the absolute-depth bug), the one RF-shallow anchor, the
two-witness geometry (AKA017 wrong ruler), a sweep-orphan as a note vs a scored-role orphan as a flag,
and the subcohort defer. Widths->depth (1967/w): 399->4.93, 285->6.90, 249->7.9, 222->8.86.
"""
import pytest

from preflight_helpers.study_deconfliction import Capture
from materialize_studies import StudyPlan
import study_qa as sq


def _cap(identity, width, *, mp4=True, raw=None):
    raw = raw or identity
    return Capture(t=1.0, raw_folder=raw, mp4=("m" if mp4 else None), dcm=None, width=width,
                   nframes=300 if identity == "sweep" else 50, depth_setting="",
                   is_sweep=(identity == "sweep"), identity=identity)


def _plan(caps, depths):
    """caps: list[Capture]; depths: {raw_folder: dcm_depth_cm|None}. A None depth = no paired .dcm."""
    primary = next((c for c in caps if c.is_sweep), None)
    dcm_for = {c.raw_folder: f"m_{c.raw_folder}" for c in caps if depths.get(c.raw_folder) is not None}
    dcm_depths = {f"m_{k}": v for k, v in depths.items() if v is not None}
    return StudyPlan(zip_path="x.zip", study="x", operator="op", session="s", protocol="v8",
                     verdict="", materialize=True, primary=primary,
                     primary_dcm=dcm_for.get(primary.raw_folder) if primary else None,
                     dcm_depths=dcm_depths, dcm_for=dcm_for,
                     annex=[c for c in caps if not c.is_sweep])


def _qa(monkeypatch, caps, depths, **kw):
    monkeypatch.setattr(sq, "plan_zip", lambda z, o: _plan(caps, depths))
    return sq.study_qa("x.zip", "op", **kw)


def _clean():
    return [_cap("sweep", 399, raw="sw"), _cap("rf", 399, raw="rf"), _cap("femur", 285, raw="fm")]


def test_ideal_v8_is_ok(monkeypatch):
    q = _qa(monkeypatch, _clean(), {"sw": 4.9, "rf": 4.9, "fm": 6.9})
    assert q.counts == {"femur": 1, "rf": 1, "sweep": 1}
    assert q.verdict == "ok" and sq.render_cell(q) == "✅ femur1 rf1 sweep1 · tam"


def test_tweaked_deep_femur_no_false_eksik(monkeypatch):
    # femur tweaked to 7.9 (width 249) — the WHOLE bug. Relative rank must count it as the femur.
    caps = [_cap("sweep", 399, raw="sw"), _cap("rf", 399, raw="rf"), _cap("femur", 249, raw="fm")]
    q = _qa(monkeypatch, caps, {"sw": 4.9, "rf": 4.9, "fm": 7.9})
    assert q.counts["femur"] == 1 and q.verdict == "ok"
    assert not any("eksik" in r or "protokol" in r for r in q.reasons)


def test_rf_on_femur_plane_fails_no_real_rf(monkeypatch):
    # everything deep (no ~4.9) — the RF role sits on a 6.9 (FEMUR-plane) capture -> no real shallow RF ->
    # FAIL (same scoring consequence as a missing RF; red-team 2026-07-07: it used to only ⚠️).
    caps = [_cap("sweep", 285, raw="sw"), _cap("rf", 285, raw="rf"), _cap("femur", 222, raw="fm")]
    q = _qa(monkeypatch, caps, {"sw": 6.9, "rf": 6.9, "fm": 8.9})
    assert q.verdict == "fail" and any("gerçek RF yok" in r for r in q.reasons)
    assert sq.render_cell(q).startswith("❌")


def test_rf_one_step_off_is_a_soft_warn_not_no_rf(monkeypatch):
    # RF at 5.9 (width 332) — one device step deep. A soft 'doğrulayın', NOT 'gerçek RF yok', NOT a fail: the
    # 128L steps (4.0/4.9/5.9) are ~1cm apart, WIDER than the ±0.6 band, so a symmetric reject was too harsh.
    caps = [_cap("sweep", 399, raw="sw"), _cap("rf", 332, raw="rf"), _cap("femur", 285, raw="fm")]
    q = _qa(monkeypatch, caps, {"sw": 4.9, "rf": 5.9, "fm": 6.9})
    assert q.verdict == "warn"
    assert any("hafif" in r and "doğrulayın" in r for r in q.reasons)
    assert not any("gerçek RF yok" in r for r in q.reasons)


def test_sweep_orphan_is_a_note_not_a_flag(monkeypatch):
    caps = [_cap("sweep", 399, mp4=False, raw="sw"), _cap("rf", 399, raw="rf"), _cap("femur", 285, raw="fm")]
    q = _qa(monkeypatch, caps, {"sw": 4.9, "rf": 4.9, "fm": 6.9})
    assert q.verdict == "ok"                                   # femur+RF are cines -> the study scores
    assert any("sadece sabit" in n for n in q.notes)           # sweep-orphan surfaced as a note
    assert not any("orphan" in r or "cine yok" in r for r in q.reasons)


def test_scored_role_orphan_flags(monkeypatch):
    # the RF has no cine (stills-only) — a SCORED role on an orphan -> the score's RF input is bad.
    caps = [_cap("sweep", 399, raw="sw"), _cap("rf", 399, mp4=False, raw="rf"), _cap("femur", 285, raw="fm")]
    q = _qa(monkeypatch, caps, {"sw": 4.9, "rf": 4.9, "fm": 6.9})
    assert q.verdict == "warn" and any("rf cine yok" in r for r in q.reasons)


def test_ruler_mismatch_flags(monkeypatch):
    # femur frames say 6.9 (width 285) but a 7.9 dcm is attached -> the wrong ruler (AKA017's +14.5%).
    caps = [_cap("sweep", 399, raw="sw"), _cap("rf", 399, raw="rf"), _cap("femur", 285, raw="fm")]
    q = _qa(monkeypatch, caps, {"sw": 4.9, "rf": 4.9, "fm": 7.9})
    assert q.verdict == "warn" and any("RULER_MISMATCH" in r for r in q.reasons)


def test_geometry_two_witnesses_surfaced(monkeypatch):
    q = _qa(monkeypatch, _clean(), {"sw": 4.9, "rf": 4.9, "fm": 6.9})
    assert any("genişlik→" in n and "dcm→" in n for n in q.notes)          # width + dcm both shown
    # a capture with no paired .dcm shows 'dcm→yok' rather than a bogus number
    q2 = _qa(monkeypatch, _clean(), {"sw": 4.9, "rf": 4.9, "fm": None})
    assert any("dcm→yok" in n for n in q2.notes)


def test_femur_absent_is_a_fail(monkeypatch):
    # no deep capture at all -> genuinely no femur (both shorts at 4.9, sweep at 4.9)
    caps = [_cap("sweep", 399, raw="sw"), _cap("rf", 399, raw="rf")]
    q = _qa(monkeypatch, caps, {"sw": 4.9, "rf": 4.9})
    assert q.verdict == "fail" and any("femur eksik" in r for r in q.reasons)


def test_subcohort_defers_not_false_eksik(monkeypatch):
    q = _qa(monkeypatch, _clean(), {"sw": 4.9, "rf": 4.9, "fm": 6.9}, declared_profile="subcohort")
    assert q.verdict == "warn" and any("ertelendi" in r for r in q.reasons)
    assert not any("eksik" in r for r in q.reasons)


def test_empty_plan_defers_not_false_eksik(monkeypatch):
    q = _qa(monkeypatch, [], {})
    assert q.verdict == "warn" and any("okunamadı" in r for r in q.reasons)
    assert not any("eksik" in r for r in q.reasons)


# ---------- the check_studies wiring: verdict_for routes v8 -> study_qa, else -> legacy check_study ----------
def test_verdict_for_routes_v8_to_study_qa(monkeypatch):
    import check_studies as cs
    fake = sq.StudyQA(study="x", counts={"femur": 1, "rf": 1, "sweep": 1}, captures=[],
                      verdict="ok", reasons=[], notes=[])
    monkeypatch.setattr(cs, "resolve_zip", lambda cfg, op, st: ("x.zip", None))
    monkeypatch.setattr(cs, "study_protocol", lambda z: "v8")
    monkeypatch.setattr(cs, "_mean_brightness", lambda z: None)
    monkeypatch.setattr(cs, "study_qa", lambda *a, **k: fake)
    cell, _d = cs.verdict_for({}, {"Study Adı": "x", "Operator": "op", "Alt-kohort mu": "Hayır"})
    assert cell == "✅ femur1 rf1 sweep1 · tam"                 # rendered from study_qa, not check_study


def test_verdict_for_routes_v7_to_legacy_check_study(monkeypatch):
    import check_studies as cs
    called = {}

    class _Rpt:
        depth_notes, param_notes, details = [], [], []

        def as_cell(self):
            return "LEGACY-v7-headline"

    monkeypatch.setattr(cs, "resolve_zip", lambda cfg, op, st: ("x.zip", None))
    monkeypatch.setattr(cs, "study_protocol", lambda z: "v7")
    monkeypatch.setattr(cs, "check_study", lambda *a, **k: (called.setdefault("hit", True), _Rpt())[1])
    cell, _d = cs.verdict_for({}, {"Study Adı": "x", "Operator": "op", "Alt-kohort mu": "Hayır"})
    assert cell == "LEGACY-v7-headline" and called.get("hit")

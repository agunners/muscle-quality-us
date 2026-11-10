"""Tests for the operator-facing study completeness/accuracy check (check_study).

The verdict is driven by the .dcm DEPTH MULTISET + capture/sweep counts, so tests inject a
DeconflictResult (captures, with is_sweep) AND a list of .dcm depths — the same inputs the real
zip yields, without shipping the gitignored zips. Two real-zip tests run only where the cached
studies exist (the DGX): one pins that the deep (6.9cm) femur IS detected (the regression for
the false "femur eksik" when SAVE-IMAGE precedes the video); one pins a study's depths are read.
The cached zips are resolved by de-identified date+sequence prefix — no patient name is committed.
"""
import glob
import os

import pytest

from preflight_helpers.check_study import check_study, infer_profile
from preflight_helpers.study_deconfliction import Capture, DeconflictResult


def _cached_zip(prefix: str) -> str:
    """Resolve a cached study zip by its de-identified date+sequence prefix (the full filename
    encodes patient name + demographics — PHI — and must never enter the repo). On the DGX this
    finds the real gitignored zip; elsewhere it returns a missing path so the real-data tests skip."""
    hits = sorted(glob.glob(f"data/intake_cache/{prefix}_*.zip"))
    return hits[0] if hits else f"data/intake_cache/{prefix}_unavailable.zip"


SUBCOHORT_ZIP = _cached_zip("08jun06_001")   # the v7 8-capture example
MAIN_ZIP = _cached_zip("10jun26_001")        # the v8 3-capture study (femur-before-video case)


def _cap(is_sweep=False, **kw):
    base = dict(t=1.0, raw_folder="r", mp4="m", dcm="d", width=399,
                nframes=300 if is_sweep else 50, depth_setting="shallow",
                is_sweep=is_sweep, identity="sweep" if is_sweep else "rf", flags=[])
    base.update(kw)
    return Capture(**base)


def _study(n_stills, n_sweeps, depths, *, orphans=0, warnings=None, **check_kw):
    caps = [_cap(is_sweep=False) for _ in range(n_stills)] + \
           [_cap(is_sweep=True) for _ in range(n_sweeps)]
    res = DeconflictResult(captures=caps, orphan_dcms=["o"] * orphans, warnings=warnings or [])
    return check_study("x.zip", result=res, dcm_depths=depths, **check_kw)


# ---------- profile inference ----------
def test_infer_exact_match_wins():
    assert infer_profile({"femur": 1, "rf": 1, "sweep": 1}, 3) == "main"
    assert infer_profile({"femur": 2, "rf": 4, "sweep": 2}, 8) == "subcohort"


# ---------- the depth multiset drives the modality counts ----------
def test_perfect_main_is_ok():
    r = _study(2, 1, [6.9, 4.9, 4.9], declared_profile="main")
    assert r.modality_counts == {"femur": 1, "rf": 1, "sweep": 1}
    assert r.verdict == "ok" and r.headline.startswith("✅")


def test_perfect_subcohort_is_ok():
    r = _study(6, 2, [6.9, 6.9, 4.9, 4.9, 4.9, 4.9, 4.9, 4.9], declared_profile="subcohort")
    assert r.modality_counts == {"femur": 2, "rf": 4, "sweep": 2}
    assert r.verdict == "ok"


def test_absent_femur_is_a_hard_fail():
    # 3 captures, NO 6.9cm depth -> femur genuinely missing
    r = _study(2, 1, [4.9, 4.9, 4.9], declared_profile="main")
    assert r.modality_counts["femur"] == 0
    assert r.verdict == "fail" and "femur" in r.headline and "eksik" in r.headline


def test_absent_sweep_is_a_hard_fail():
    r = _study(2, 0, [6.9, 4.9], declared_profile="main")
    assert r.verdict == "fail" and "sweep eksik" in r.headline


def test_femur_present_even_when_dcm_order_is_weird():
    # the anonaaq shape: depths {6.9,4.9,4.9} in any order -> femur detected, NOT eksik
    r = _study(2, 1, [4.9, 6.9, 4.9], declared_profile="main")
    assert r.modality_counts["femur"] == 1
    assert not any("femur" in d and "eksik" in d for d in r.details)


def test_depth_multiset_is_surfaced():
    r = _study(2, 1, [6.9, 4.9, 4.9], declared_profile="main")
    assert any("6.9cm" in d and "4.9cm" in d for d in r.depth_notes)


def test_offspec_depth_warns():
    r = _study(2, 1, [6.9, 4.9, 5.8], declared_profile="main")  # 5.8 = neither 4.9 nor 6.9
    assert r.verdict == "warn" and "protokol dışı" in r.headline


def test_dcm_count_mismatch_is_flagged_approximate():
    r = _study(2, 1, [6.9, 4.9], declared_profile="main")  # 2 dcm for 3 captures
    assert any("yaklaşık" in d for d in r.details)


# ---------- declaration cross-checks ----------
def test_mislabel_main_but_recorded_subcohort_warns_and_leads_with_it():
    r = _study(6, 2, [6.9, 6.9, 4.9, 4.9, 4.9, 4.9, 4.9, 4.9], declared_profile="main")
    assert r.verdict == "warn" and "subcohort gibi" in r.headline


def test_blank_cohort_nudges_active_selection():
    r = _study(2, 1, [6.9, 4.9, 4.9], declared_profile=None)
    assert r.verdict == "warn" and "lütfen işaretleyin" in r.headline


def test_cell_string_is_the_headline():
    r = _study(2, 1, [6.9, 4.9, 4.9], declared_profile="main")
    assert r.as_cell() == r.headline


# ---------- real zips (DGX only) ----------
@pytest.mark.skipif(not os.path.exists(MAIN_ZIP), reason="v8 main zip not on this machine")
def test_main_femur_is_detected_not_falsely_eksik():
    # Regression: the femur SAVE-IMAGE precedes its video, so per-capture attachment orphaned it
    # and the check wrongly said "femur eksik". The depth multiset must detect the 6.9cm femur.
    r = check_study(MAIN_ZIP, declared_profile="main", protocol="v8")
    assert r.modality_counts["femur"] == 1, "the 6.9cm femur must be detected"
    assert not any("femur" in d and "eksik" in d for d in r.details)
    assert any("6.9cm" in d for d in r.depth_notes)
    assert any("parlak" in w for w in r.details), "this study is washed out (~119) — flag it"


@pytest.mark.skipif(not os.path.exists(SUBCOHORT_ZIP), reason="v7 subcohort zip not on this machine")
def test_subcohort_depths_read_and_not_washed():
    r = check_study(SUBCOHORT_ZIP, declared_profile="subcohort", protocol="v7")
    assert any("6.9cm" in d and "4.9cm" in d for d in r.depth_notes)
    assert not any("parlak" in w for w in r.details), "this study (~48) is not washed out"

"""Build-Step 2 Phase 3 item 8 — the QA layer wired into aggregation.

docs/build_step_2_plan.md item 8 / RF Image-Quality QA plan § 5.5 / § 7.1 / § 7.3:

  * compute_image_quality_features — the pipeline QA grade: runs all 15 v0.2
    detectors and folds them into one ImageQualityFeatures record (HANDOFF
    Finding A — the 2 standalone detectors merged in), with the verdict fields
    computed over the *calibrated* flag subset only (HANDOFF Finding B).
  * _aggregate_with_image_quality — the § 7.3 per-measurement QA-tier pooling
    gate; aggregate_features applies it (an invalid frame is excluded and the
    aggregate re-pools; an all-valid / pre-QA run is unchanged).
"""
from __future__ import annotations

import numpy as np

from labeling_app.image_quality import (
    BORDERLINE, GATING_MEASUREMENTS, INVALID, VALID, calibrated_flags,
    image_quality_aggregate_tier, image_quality_aggregate_tier_from_tiers,
    image_quality_tier_per_measurement, qmt_provenance_capped_tiers,
)
from preflight_helpers.aggregation import (
    _aggregate_with_image_quality, _rf_consistency_report, aggregate_features,
)
from preflight_helpers.feature_compute import (
    ImageQualityFeatures, compute_image_quality_features,
)


# ===========================================================================
# A — compute_image_quality_features: the per-frame QA grade
# ===========================================================================

def _clean_frame():
    """A good RF frame (§ 15.1 Example A) — every detector silent."""
    h, w = 140, 120
    mask = np.zeros((h, w), np.uint8)
    img = np.full((h, w), 110, np.uint8)
    mask[3:19, 12:108] = 6                       # skin
    mask[19:34, 12:108] = 8                      # fat
    mask[34:38, 24:96] = 5                       # superficial fascia
    mask[38:86, 24:96] = 1                       # RF body
    mask[86:90, 24:96] = 5                       # deep fascia
    mask[92:108, 42:78] = 7                      # femur — central, plateau
    return img, mask


def _no_femur_frame():
    """RF present, no femur — the calibrated `femur_out_of_frame` detector
    fires (it reuses the QMT pipeline's femur-presence test)."""
    mask = np.zeros((60, 60), np.uint8)
    mask[20:40, 20:40] = 1                       # RF, interior, no femur
    return np.full((60, 60), 110, np.uint8), mask


def _wrong_site_frame():
    """A frame with RF in only ~30 % of columns — wrong_site fires under
    the column-coverage rule (Akadir 2026-05-24 redesign). RF blob spans
    cols 60-120 of a 200-wide image → 60/200 = 30 % < 50 % threshold."""
    h, w = 200, 200
    mask = np.zeros((h, w), np.uint8)
    img = np.full((h, w), 110, np.uint8)
    mask[60:100, 60:120] = 1                     # RF — narrow band, 30% cols
    mask[60:140, 130:190] = 3                    # VL — at the edge
    mask[150:170, 75:105] = 7                    # femur — present, central
    return img, mask


def _pending_dark_clip_frame():
    """A frame designed to fire `dark_clip_high` (un-calibrated Tier-B) —
    used to exercise the pending-flag-does-not-condemn property after
    `wrong_site` moved to calibrated (Akadir 2026-05-24). The frame's RF
    spans > 50 % of columns (so wrong_site is silent) and the image is
    mostly very dark (so dark_clip_high fires)."""
    h, w = 200, 200
    mask = np.zeros((h, w), np.uint8)
    mask[60:120, 20:180] = 1                     # RF — wide enough
    mask[120:160, 20:180] = 2                    # VI deep
    mask[170:175, 80:120] = 7                    # femur central
    img = np.zeros((h, w), np.uint8)             # mostly black → dark_clip fires
    return img, mask


def test_compute_image_quality_features_clean_frame_is_valid() -> None:
    """A clean frame: no flag fires, aggregate tier `valid` (§ 15.1)."""
    img, mask = _clean_frame()
    iqf = compute_image_quality_features(
        frame_base="clean", multiclass_mask=mask, final_image_u8=img,
        spacing_row={})
    assert isinstance(iqf, ImageQualityFeatures)
    assert iqf.image_quality_validity_flags == ()
    assert iqf.image_quality_aggregate_tier == VALID
    assert iqf.image_quality_contract_version == "v0.2"


def test_compute_image_quality_features_calibrated_detector_drives_verdict() -> None:
    """A calibrated detector DOES drive the hard verdict: no femur ->
    `femur_out_of_frame` (Threshold-tier A by reuse) -> star_ratio invalid (a
    headline measurement) -> aggregate tier `invalid`."""
    img, mask = _no_femur_frame()
    iqf = compute_image_quality_features(
        frame_base="nofemur", multiclass_mask=mask, final_image_u8=img,
        spacing_row={})
    assert "femur_out_of_frame" in iqf.image_quality_validity_flags
    md = iqf.image_quality_detector_metadata["femur_out_of_frame"]
    assert md["pending_calibration"] is False        # calibrated
    assert iqf.image_quality_aggregate_tier == INVALID
    assert iqf.image_quality_tier_per_measurement["star_ratio"] == INVALID


def test_compute_image_quality_features_wrong_site_is_now_calibrated() -> None:
    """Akadir 2026-05-24 redesign of `wrong_site` — the rf_fraction-based
    Finding B problem (un-calibrated detector firing on 79.8 % of cohort)
    is dissolved at the root by switching to the column-coverage rule.
    The new detector is geometric (0.5 half-columns design constant), so
    it ships as CALIBRATED — when it fires, it accurately says the frame
    has too little RF, and it drives the verdict like any calibrated
    flag would. The Finding-B premise no longer applies to wrong_site;
    the pending-flag-does-not-condemn property still holds for other
    un-calibrated detectors (see motion_blur test below)."""
    img, mask = _wrong_site_frame()
    iqf = compute_image_quality_features(
        frame_base="wrongsite", multiclass_mask=mask, final_image_u8=img,
        spacing_row={})
    flags = iqf.image_quality_validity_flags
    md = iqf.image_quality_detector_metadata
    assert "wrong_site" in flags
    # Wrong_site is now CALIBRATED.
    assert md["wrong_site"]["pending_calibration"] is False
    assert "wrong_site" in calibrated_flags(flags, md)
    # It drives the verdict to invalid because it taints all gating measurements.
    assert iqf.image_quality_aggregate_tier == INVALID


def test_compute_image_quality_features_merges_all_sixteen_detectors() -> None:
    """HANDOFF Finding A — the 2 standalone detectors are merged into the
    record. With a `boundary_entropy` and a `spacing_row` supplied, all 16
    detectors (14 registry incl. E4 anisotropy_dropout_superficial +
    subq_fat_compressed + segmentation_low_confidence) report metadata; without
    `boundary_entropy` the segmenter-only detector is honestly skipped
    (annotator-mask runs have no softmax)."""
    img, mask = _clean_frame()
    spacing = {"pixel_spacing_y": 0.1, "dcm_rows": 600, "mask_height": 140}
    full = compute_image_quality_features(
        frame_base="full15", multiclass_mask=mask, final_image_u8=img,
        spacing_row=spacing, boundary_entropy=0.1)
    md = full.image_quality_detector_metadata
    assert len(md) == 16
    assert "subq_fat_compressed" in md and "segmentation_low_confidence" in md

    no_seg = compute_image_quality_features(
        frame_base="noseg", multiclass_mask=mask, final_image_u8=img,
        spacing_row=spacing, boundary_entropy=None)
    nmd = no_seg.image_quality_detector_metadata
    assert len(nmd) == 15
    assert "subq_fat_compressed" in nmd
    assert "segmentation_low_confidence" not in nmd


def test_compute_image_quality_features_ungraded_when_no_image() -> None:
    """No `_final.png` to run the image detectors on -> the § 8.6 'QA
    unavailable' state. The frame is never silently treated as `valid`."""
    _img, mask = _no_femur_frame()
    iqf = compute_image_quality_features(frame_base="noimg", multiclass_mask=mask)
    assert iqf.image_quality_aggregate_tier == "ungraded"
    assert iqf.image_quality_validity_flags == ()
    assert iqf.image_quality_tier_per_measurement == {}


def test_compute_image_quality_features_record_round_trips() -> None:
    """The produced record serialises to the JSON ledger and back losslessly."""
    img, mask = _clean_frame()
    iqf = compute_image_quality_features(
        frame_base="rt", multiclass_mask=mask, final_image_u8=img, spacing_row={})
    import json
    back = ImageQualityFeatures.from_dict(json.loads(json.dumps(iqf.to_dict())))
    assert back == iqf


def test_compute_image_quality_features_pending_flags_are_the_uncalibrated_subset() -> None:
    """H1 (item-10a schema gate) — `image_quality_pending_calibration_flags`
    is exactly the un-calibrated subset of fired flags. A no-femur frame
    fires `femur_out_of_frame` (calibrated) → pending set empty. A
    motion-blur frame fires `motion_blur` (un-calibrated Tier-B) → pending
    set is exactly that. The partition is a disjoint cover of
    `image_quality_validity_flags`."""
    img_c, mask_c = _no_femur_frame()
    cal_only = compute_image_quality_features(
        frame_base="cal", multiclass_mask=mask_c, final_image_u8=img_c, spacing_row={})
    assert "femur_out_of_frame" in cal_only.image_quality_validity_flags
    # femur_out_of_frame is calibrated → not in pending set.
    assert "femur_out_of_frame" not in cal_only.image_quality_pending_calibration_flags

    img_w, mask_w = _pending_dark_clip_frame()
    pend = compute_image_quality_features(
        frame_base="pend", multiclass_mask=mask_w, final_image_u8=img_w,
        spacing_row={})
    # dark_clip_high is un-calibrated Tier-B and SHOULD be in pending.
    assert "dark_clip_high" in pend.image_quality_validity_flags
    assert "dark_clip_high" in pend.image_quality_pending_calibration_flags

    # the pending set always cleanly partitions the visible flag set.
    for iqf in (cal_only, pend):
        cal = set(calibrated_flags(iqf.image_quality_validity_flags,
                                   iqf.image_quality_detector_metadata))
        pend_set = set(iqf.image_quality_pending_calibration_flags)
        assert cal.isdisjoint(pend_set)
        assert cal | pend_set == set(iqf.image_quality_validity_flags)


def test_image_quality_features_from_dict_back_compat_pending_flags() -> None:
    """H1 — `from_dict` defaults `image_quality_pending_calibration_flags` to ()
    for a pre-H1 ledger record with no such key, so the finalized runs already
    in `data/runs.jsonl` keep loading after the schema gains the field."""
    legacy = {
        "frame_base": "pre_h1",
        "image_quality_validity_flags": ["wrong_site"],
        "image_quality_detector_metadata": {},
        "image_quality_tier_per_measurement": {},
        "image_quality_aggregate_tier": "valid",
        "image_quality_contract_version": "v0.2",
    }
    iqf = ImageQualityFeatures.from_dict(legacy)
    assert iqf.image_quality_pending_calibration_flags == ()
    assert iqf.image_quality_validity_flags == ("wrong_site",)


# ===========================================================================
# B — _aggregate_with_image_quality: the per-measurement QA-tier selector
# ===========================================================================

def _tiers(**overrides) -> dict:
    """An image_quality_tier_per_measurement map — every gating measurement
    `valid` unless overridden."""
    t = {m: VALID for m in GATING_MEASUREMENTS}
    t.update(overrides)
    return t


def _frame(fb: str, *, anchor: bool = False, tiers: dict | None = None,
           **values) -> dict:
    """A frame dict for aggregation — measurement values + an optional QA
    grade. `tiers=None` means a pre-QA / ungraded frame (no `image_quality`)."""
    f: dict = {"frame_base": fb, "is_anchor": anchor, **values}
    if tiers is not None:
        f["image_quality"] = {
            "image_quality_validity_flags": [],
            "image_quality_detector_metadata": {},
            "image_quality_tier_per_measurement": tiers,
            "image_quality_aggregate_tier": "valid",
            "image_quality_contract_version": "v0.2",
        }
    return f


def test_aggregate_with_image_quality_inert_without_grades() -> None:
    """No frame carries a QA grade -> the gate is inert: every frame is
    selected (a pre-QA / legacy run aggregates exactly as before)."""
    frames = [_frame("a"), _frame("b"), _frame("c")]
    selected, tier = _aggregate_with_image_quality(frames, "rf_thickness_mm")
    assert selected == frames
    assert tier == "ungraded"


def test_aggregate_with_image_quality_excludes_invalid_prefers_valid() -> None:
    """Mixed tiers -> the `invalid` frame is dropped and only `valid` frames
    are pooled (§ 6.4 / § 7.3 prefer-valid)."""
    frames = [
        _frame("a", tiers=_tiers()),
        _frame("b", tiers=_tiers(rf_thickness_mm=INVALID)),
        _frame("c", tiers=_tiers()),
    ]
    selected, tier = _aggregate_with_image_quality(frames, "rf_thickness_mm")
    assert [f["frame_base"] for f in selected] == ["a", "c"]
    assert tier == "valid"


def test_aggregate_with_image_quality_falls_back_to_borderline() -> None:
    """No `valid` frame -> `borderline` frames are the fallback pool."""
    frames = [
        _frame("a", tiers=_tiers(rf_thickness_mm=BORDERLINE)),
        _frame("b", tiers=_tiers(rf_thickness_mm=BORDERLINE)),
    ]
    selected, tier = _aggregate_with_image_quality(frames, "rf_thickness_mm")
    assert len(selected) == 2
    assert tier == "borderline"


def test_aggregate_with_image_quality_none_when_all_invalid() -> None:
    """Every frame `invalid` for the measurement -> no admissible pool."""
    frames = [
        _frame("a", tiers=_tiers(int_cv_within=INVALID)),
        _frame("b", tiers=_tiers(int_cv_within=INVALID)),
    ]
    selected, tier = _aggregate_with_image_quality(frames, "int_cv_within")
    assert selected == []
    assert tier == "none"


def test_aggregate_with_image_quality_resolves_measurement_aliases() -> None:
    """`full_area_mm2` gates as `visible_rf_area_mm2`; the `int_*` family as
    `rf_echo` (§ 6.2) — the QA grade carries only the 8 gating-table keys."""
    frames = [_frame("a", tiers=_tiers(visible_rf_area_mm2=INVALID, rf_echo=INVALID))]
    assert _aggregate_with_image_quality(frames, "full_area_mm2")[1] == "none"
    assert _aggregate_with_image_quality(frames, "int_mean")[1] == "none"
    # a measurement outside the § 6.2 table has no verdict — gate transparent
    assert _aggregate_with_image_quality(frames, "full_max_thickness_mm")[1] == "ungraded"


# ===========================================================================
# C — aggregate_features: QA-gated pooling end to end
# ===========================================================================

def _trio(tiers_a=None, tiers_b=None, tiers_c=None) -> list[dict]:
    """Three frames; B is the anchor. int_cv_within 0.30/0.40/0.90 and
    rf_thickness_mm 10/20/40 are chosen so a re-pool is numerically visible."""
    return [
        _frame("A", int_cv_within=0.30, rf_thickness_mm=10.0, tiers=tiers_a),
        _frame("B", anchor=True, int_cv_within=0.40, rf_thickness_mm=20.0,
               tiers=tiers_b),
        _frame("C", int_cv_within=0.90, rf_thickness_mm=40.0, tiers=tiers_c),
    ]


def test_aggregate_features_all_valid_run_is_unchanged() -> None:
    """build_step_2_plan item 8 done-when: an all-valid graded run produces the
    identical aggregate to the same run with no QA grades at all."""
    graded = aggregate_features(
        _trio(_tiers(), _tiers(), _tiers()), anchor_frame_base="B")
    ungraded = aggregate_features(_trio(), anchor_frame_base="B")
    for m in ("int_cv_within", "rf_thickness_mm"):
        assert graded[m] == ungraded[m]
        assert graded[f"{m}_repr"] == ungraded[f"{m}_repr"]
        assert graded[f"{m}_agg_median"] == ungraded[f"{m}_agg_median"]
    # the intensity median over all three; geometry primary = anchor value
    assert graded["int_cv_within"] == 0.40            # median(0.30, 0.40, 0.90)
    assert graded["rf_thickness_mm"] == 20.0          # anchor B


def test_aggregate_features_excludes_invalid_frame_and_repools() -> None:
    """build_step_2_plan item 8 done-when: a frame flagged `invalid` for a
    measurement is excluded from that measurement's pool and the aggregate
    re-pools. C is invalid for int_cv_within -> median over A+B only."""
    frames = _trio(_tiers(), _tiers(), _tiers(int_cv_within=INVALID))
    out = aggregate_features(frames, anchor_frame_base="B")
    assert out["int_cv_within"] == 0.35               # median(0.30, 0.40) — C dropped
    assert out["_qa_tier"]["int_cv_within"] == "valid"
    # the raw all-frame support column still sees every frame
    assert out["int_cv_within_agg_median"] == 0.40    # median(0.30, 0.40, 0.90)


def test_aggregate_features_invalid_anchor_repools_geometry() -> None:
    """When the anchor itself is QA-`invalid` for a geometry measurement, the
    anchor_primary policy cannot use the anchor value — it re-pools to the
    eligible-subset median."""
    frames = _trio(_tiers(), _tiers(rf_thickness_mm=INVALID), _tiers())
    out = aggregate_features(frames, anchor_frame_base="B")
    assert out["rf_thickness_mm"] == 25.0             # median(A=10, C=40) — anchor B dropped
    assert out["_qa_tier"]["rf_thickness_mm"] == "valid"


def test_aggregate_features_all_invalid_measurement_is_none() -> None:
    """Every feeding frame `invalid` for a measurement -> it has no admissible
    pool; the primary is None (§ 6.4 — invalid is never pooled)."""
    inv = _tiers(int_cv_within=INVALID)
    out = aggregate_features(_trio(inv, inv, inv), anchor_frame_base="B")
    assert out["int_cv_within"] is None
    assert out["_qa_tier"]["int_cv_within"] == "none"
    assert out["_policies"]["int_cv_within"] == "image_quality_all_invalid"
    # a measurement with no invalid verdict is unaffected
    assert out["rf_thickness_mm"] == 20.0


# ===========================================================================
# D — review fixes: QMT/STAR QA-gating (P0/P1) + detector-error handling (P2)
# ===========================================================================

def _qmt_frame(fb: str, qmt: float, star: float, prov: str, *,
               tiers: dict | None = None, anchor: bool = False) -> dict:
    """A frame carrying the QMT-relevant fields + an optional QA grade."""
    return _frame(fb, anchor=anchor, qmt_mm=qmt, star_ratio=star,
                  qmt_provenance=prov, qmt_validity_flags=[], tiers=tiers)


def test_aggregate_features_qmt_excludes_qa_invalid_frame() -> None:
    """Review fix P0/P1 — QMT/STAR are QA-gated (§ 6.2 / § 7.3). A standard-QMT
    frame QA-invalid for qmt_mm is dropped from the QMT median; the QA tier
    filters within the provenance tier, and star_ratio rides on that selection."""
    frames = [
        _qmt_frame("A", 36.0, 1.00, "standard_qmt", tiers=_tiers()),
        _qmt_frame("B", 40.0, 1.10, "standard_qmt", tiers=_tiers(), anchor=True),
        _qmt_frame("C", 80.0, 2.00, "standard_qmt", tiers=_tiers(qmt_mm=INVALID)),
    ]
    out = aggregate_features(frames, anchor_frame_base="B")
    assert out["qmt_mm"] == 38.0              # median(36, 40) — C dropped
    assert out["star_ratio"] == 1.05          # median(1.00, 1.10) — rides on qmt
    assert out["qmt_provenance"] == "standard"
    assert out["_qa_tier"]["qmt_mm"] == "valid"
    assert out["qmt_mm_agg_median"] == 40.0   # raw support col still sees all 3


def test_aggregate_features_qmt_falls_through_qa_invalid_tier() -> None:
    """When every Tier-1 (standard) frame is QA-invalid for qmt_mm, the QMT
    aggregate falls through to the QA-valid Tier-2 (proxy) frames — provenance
    is the outer gate, the QA tier filters within it."""
    frames = [
        _qmt_frame("A", 40.0, 1.1, "standard_qmt", tiers=_tiers(qmt_mm=INVALID)),
        _qmt_frame("B", 42.0, 1.2, "standard_qmt", tiers=_tiers(qmt_mm=INVALID)),
        _qmt_frame("C", 20.0, 0.6, "femur_supported_proxy", tiers=_tiers()),
    ]
    out = aggregate_features(frames)
    assert out["qmt_mm"] == 20.0              # Tier-1 wiped by QA -> the proxy
    assert out["qmt_provenance"] == "proxy"
    assert out["_qa_tier"]["qmt_mm"] == "valid"


def test_aggregate_features_qmt_inert_without_qa_grades() -> None:
    """The QMT QA gate is inert on a pre-QA run — frames with no image_quality
    grade aggregate exactly as the provenance gate alone dictates."""
    frames = [
        _qmt_frame("A", 36.0, 1.0, "standard_qmt"),
        _qmt_frame("B", 44.0, 1.2, "standard_qmt"),
    ]
    out = aggregate_features(frames)
    assert out["qmt_mm"] == 40.0              # median(36, 44) — no QA exclusion
    assert out["qmt_provenance"] == "standard"
    assert out["_qa_tier"]["qmt_mm"] == "ungraded"


def test_compute_image_quality_features_detector_error_is_ungraded(monkeypatch) -> None:
    """Review fix P2 / § 8.6 — a detector that raises renders the frame
    `ungraded` ('QA unavailable'); the error never propagates to block the run,
    and its reason is recorded for the drilldown."""
    def _boom(img_gray, mask):
        raise RuntimeError("synthetic detector failure")
    monkeypatch.setattr(
        "preflight_helpers.feature_compute.compute_image_quality_flags", _boom)
    img, mask = _clean_frame()
    iqf = compute_image_quality_features(
        frame_base="boom", multiclass_mask=mask, final_image_u8=img, spacing_row={})
    assert iqf.image_quality_aggregate_tier == "ungraded"
    err = iqf.image_quality_detector_metadata.get("_qa_error", "")
    assert "RuntimeError" in err and "synthetic detector failure" in err


# ===========================================================================
# E — the QMT provenance floor (the QA qmt verdict capped by qmt_provenance)
# ===========================================================================

def test_qmt_provenance_capped_tiers_caps_proxy_to_borderline() -> None:
    """A `femur_supported_proxy` QMT is a degraded fallback — its QA qmt_mm /
    star_ratio tier is capped at borderline; other measurements are untouched."""
    capped = qmt_provenance_capped_tiers(_tiers(), "femur_supported_proxy")
    assert capped["qmt_mm"] == BORDERLINE
    assert capped["star_ratio"] == BORDERLINE
    assert capped["rf_thickness_mm"] == VALID            # untouched


def test_qmt_provenance_capped_tiers_caps_no_femur_to_invalid() -> None:
    for prov in ("no_femur_proxy", "no_measurement"):
        capped = qmt_provenance_capped_tiers(_tiers(), prov)
        assert capped["qmt_mm"] == INVALID, prov
        assert capped["star_ratio"] == INVALID, prov


def test_qmt_provenance_capped_tiers_standard_and_none_impose_no_cap() -> None:
    for prov in ("standard_qmt", None, "something_unrecognised"):
        capped = qmt_provenance_capped_tiers(_tiers(), prov)
        assert capped["qmt_mm"] == VALID, prov
        assert capped["star_ratio"] == VALID, prov


def test_qmt_provenance_capped_tiers_is_a_ceiling_not_a_floor() -> None:
    """The cap never *raises* a tier — a qmt_mm already `invalid` stays invalid
    under a `borderline` cap — and the input dict is not mutated."""
    src = _tiers(qmt_mm=INVALID)
    capped = qmt_provenance_capped_tiers(src, "femur_supported_proxy")
    assert capped["qmt_mm"] == INVALID                   # invalid is worse than the cap
    assert src["qmt_mm"] == INVALID                      # input not mutated


def test_compute_image_quality_features_floors_proxy_qmt() -> None:
    """A clean frame whose QMT measurement is a `femur_supported_proxy`: the QA
    verdict no longer calls qmt_mm `valid` — it is capped at borderline, and
    star_ratio (a headline measurement) with it, so the aggregate badge reads
    borderline rather than the misleading `valid`. A `standard_qmt` frame is
    unaffected."""
    img, mask = _clean_frame()
    proxy = compute_image_quality_features(
        frame_base="proxy", multiclass_mask=mask, final_image_u8=img,
        spacing_row={}, qmt_provenance="femur_supported_proxy")
    assert proxy.image_quality_tier_per_measurement["qmt_mm"] == BORDERLINE
    assert proxy.image_quality_tier_per_measurement["star_ratio"] == BORDERLINE
    assert proxy.image_quality_tier_per_measurement["rf_thickness_mm"] == VALID
    assert proxy.image_quality_aggregate_tier == BORDERLINE

    std = compute_image_quality_features(
        frame_base="std", multiclass_mask=mask, final_image_u8=img,
        spacing_row={}, qmt_provenance="standard_qmt")
    assert std.image_quality_tier_per_measurement["qmt_mm"] == VALID
    assert std.image_quality_aggregate_tier == VALID


def test_image_quality_aggregate_tier_from_tiers_matches_the_flag_path() -> None:
    """The `_from_tiers` split is behaviour-preserving: for any flag set the
    aggregate from the tier map equals the aggregate from the flags."""
    for flags in ((), ("femur_off_central_zone", "edge_clip_lateral"),
                  ("motion_blur",), ("edge_clip_lateral",)):
        assert image_quality_aggregate_tier_from_tiers(
            image_quality_tier_per_measurement(flags)
        ) == image_quality_aggregate_tier(flags)


# ===========================================================================
# F — the frame_rf_consistency gate (item 9a — RF QA plan § 3.4 / § 14 Step 4)
# ===========================================================================
#
# `frame_rf_consistency` is promoted from a passive sweep-σ metric to an active
# per-frame gate: a frame whose rf_thickness_mm is a robust outlier among its
# sweep siblings (Iglewicz-Hoaglin modified Z-score past the registry cut) is
# excluded from the rf_thickness_mm aggregate pool. `_rf_consistency_report` is
# the per-sweep selector; aggregate_features applies it (item 9 done-when:
# `frame_rf_consistency` actually gates a frame in aggregation).

def _sweep(thicknesses, *, int_cvs=None, tiers_list=None, anchor_idx=None):
    """N frames for the consistency gate. `thicknesses` sets rf_thickness_mm
    per frame; `int_cvs` (optional) sets int_cv_within per frame; `tiers_list`
    (optional) sets a per-frame QA grade; `anchor_idx` marks the anchor."""
    frames = []
    for i, t in enumerate(thicknesses):
        kw = {"rf_thickness_mm": t}
        if int_cvs is not None:
            kw["int_cv_within"] = int_cvs[i]
        frames.append(_frame(
            f"S{i}", anchor=(i == anchor_idx),
            tiers=(tiers_list[i] if tiers_list is not None else None),
            **kw))
    return frames


def test_rf_consistency_report_inert_below_min_frames() -> None:
    """A 3-frame sweep (the cohort triple) is too few for a robust outlier
    statement — the gate is inert by design (the registry minimum is 4)."""
    r = _rf_consistency_report(_sweep([10.0, 12.0, 40.0]))
    assert r["active"] is False
    assert r["reason_inert"] == "too_few_frames"
    assert r["n_examined"] == 3
    assert r["excluded"] == []


def test_rf_consistency_report_inert_on_zero_mad() -> None:
    """No spread — four identical frames give MAD 0, so a robust z-score is
    undefined and the gate stays inert rather than condemning the odd frame."""
    r = _rf_consistency_report(_sweep([20.0, 20.0, 20.0, 20.0, 40.0]))
    assert r["active"] is False
    assert r["reason_inert"] == "zero_mad"
    assert r["mad_mm"] == 0.0
    assert r["excluded"] == []


def test_rf_consistency_report_flags_gross_outlier() -> None:
    """A gross rf-thickness outlier among a tight sweep is caught — its
    modified Z-score is far past the 3.5 cut, every inlier well within it."""
    r = _rf_consistency_report(_sweep([10.0, 12.0, 40.0, 11.0, 13.0]))
    assert r["active"] is True
    assert r["excluded"] == ["S2"]                       # the 40 mm frame
    assert r["scores"]["S2"] > 3.5
    assert all(abs(r["scores"][f"S{i}"]) < 3.5 for i in (0, 1, 3, 4))
    assert r["median_mm"] == 12.0 and r["mad_mm"] == 1.0


def test_rf_consistency_report_silent_on_consistent_sweep() -> None:
    """A genuine smooth sweep — RF thickness drifting gently along the muscle
    — fires no flag: every frame's modified Z-score is within the cut."""
    r = _rf_consistency_report(_sweep([19.0, 20.0, 21.0, 20.0, 19.0, 21.0]))
    assert r["active"] is True
    assert r["excluded"] == []


def test_aggregate_features_rf_consistency_gates_a_frame() -> None:
    """build_step_2_plan item 9 done-when — `frame_rf_consistency` actually
    gates a frame in aggregation. Per-run path, the gross rf-thickness outlier
    is the anchor: anchor_primary cannot pass its value through, so the headline
    re-pools to the four inliers' median; the raw support column still sees all 5."""
    frames = _sweep([10.0, 12.0, 40.0, 11.0, 13.0], anchor_idx=2)
    out = aggregate_features(frames, anchor_frame_base="S2")
    assert out["_rf_consistency"]["active"] is True
    assert out["_rf_consistency"]["excluded"] == ["S2"]
    assert out["rf_thickness_mm"] == 11.5                # median(10,12,11,13) — S2 dropped
    assert out["rf_thickness_mm_repr"] == 40.0           # _repr still the raw anchor value
    assert out["rf_thickness_mm_agg_median"] == 12.0     # raw support col — all 5


def test_aggregate_features_rf_consistency_non_anchor_outlier_advisory_only() -> None:
    """Per-run path, the outlier is NOT the anchor: the headline rf_thickness_mm
    is the anchor's value and is unchanged — a non-anchor outlier never
    threatened an anchor-based headline (the gate's per-run numeric reach is
    exactly the bad-anchor case). The gate still records the outlier and the
    clean trimmed median — the advisory signal item 10's UI must surface."""
    frames = _sweep([10.0, 12.0, 40.0, 11.0, 13.0], anchor_idx=0)
    out = aggregate_features(frames, anchor_frame_base="S0")
    assert out["rf_thickness_mm"] == 10.0                # the anchor's value — unchanged
    rc = out["_rf_consistency"]
    assert rc["active"] is True
    assert rc["excluded"] == ["S2"]                      # recorded for the operator
    assert rc["trimmed_median_mm"] == 11.5               # the clean sensitivity figure
    assert rc["median_mm"] == 12.0                       # the raw (outlier-in) median


def test_aggregate_features_rf_consistency_reference_excludes_qa_invalid() -> None:
    """The consistency gate runs *within* the image-quality-eligible pool: a
    QA-`invalid` frame is dropped by `_aggregate_with_image_quality` first, so
    its (here wildly off) thickness never enters the median/MAD reference."""
    frames = _sweep(
        [20.0, 21.0, 19.0, 20.0, 200.0],
        tiers_list=[_tiers(), _tiers(), _tiers(), _tiers(),
                    _tiers(rf_thickness_mm=INVALID)])
    out = aggregate_features(frames, anchor_frame_base="S0")
    rc = out["_rf_consistency"]
    assert rc["n_examined"] == 4                          # only the QA-eligible frames
    assert rc["excluded"] == []                           # 200 mm never entered the reference
    assert out["_qa_tier"]["rf_thickness_mm"] == "valid"


def test_aggregate_features_rf_consistency_only_touches_rf_thickness() -> None:
    """The gate narrows the rf_thickness_mm pool only — every other measurement
    still pools over all frames, the outlier included."""
    frames = _sweep([10.0, 12.0, 40.0, 11.0, 13.0],
                    int_cvs=[0.30, 0.40, 0.95, 0.50, 0.60], anchor_idx=2)
    out = aggregate_features(frames, anchor_frame_base="S2")
    assert out["rf_thickness_mm"] == 11.5                # S2 (anchor outlier) re-pooled out
    assert out["int_cv_within"] == 0.50                  # median of ALL 5 — S2 still pooled


def test_aggregate_features_rf_consistency_inert_on_cohort_triple() -> None:
    """A 3-frame run (the cohort triple) — the gate is inert and rf_thickness_mm
    is exactly the pre-gate result; the report is still recorded, marked
    inactive, so the decision stays auditable downstream."""
    out = aggregate_features(_trio(), anchor_frame_base="B")
    assert out["rf_thickness_mm"] == 20.0                # anchor B — unchanged
    rc = out["_rf_consistency"]
    assert rc["active"] is False
    assert rc["reason_inert"] == "too_few_frames"


def test_rf_consistency_thresholds_are_registered_tier_a() -> None:
    """The gate's two parameters live in the QA threshold registry (§ 12.4
    single-source rule). Both are Threshold-tier A: the modified Z-score is
    scale-invariant and 3.5 is the documented Iglewicz-Hoaglin standard cut — a
    universal statistical convention, not a cohort fit — and the 4-frame
    minimum is a structural floor. Neither is a Build-Step 2b calibration
    target; both ship trustworthy on day one."""
    from labeling_app.qa_thresholds import THRESHOLDS
    z = THRESHOLDS["frame_rf_consistency__modified_z_max"]
    n = THRESHOLDS["frame_rf_consistency__min_frames"]
    assert z.value == 3.5 and z.tier == "A" and z.is_calibrated
    assert n.value == 4 and n.tier == "A"


def test_rf_consistency_cut_is_sourced_from_the_registry(monkeypatch) -> None:
    """§ 12.4 single-source rule — the gate resolves its parameters live from
    `qa_thresholds.THRESHOLDS`, never a literal. Monkeypatching the registered
    min-frames up to 99 makes a 5-frame sweep go inert, which is only possible
    if the value is read from the registry at call time."""
    from dataclasses import replace
    from labeling_app.qa_thresholds import THRESHOLDS
    sweep = _sweep([10.0, 12.0, 40.0, 11.0, 13.0])
    assert _rf_consistency_report(sweep)["excluded"] == ["S2"]   # fires by default
    monkeypatch.setitem(
        THRESHOLDS, "frame_rf_consistency__min_frames",
        replace(THRESHOLDS["frame_rf_consistency__min_frames"], value=99))
    r = _rf_consistency_report(sweep)
    assert r["active"] is False and r["reason_inert"] == "too_few_frames"


def test_aggregate_features_rf_consistency_skipped_in_bootstrap() -> None:
    """The consistency gate is a per-sweep guard — it does NOT run on the N5
    study-bootstrap pool (`force_all_median`), a union across distinct runs
    that is not a sweep. The report is recorded inert (`bootstrap_pool`); the
    outlier is not excluded — the bootstrap's pooled median is itself robust."""
    out = aggregate_features(_sweep([10.0, 12.0, 40.0, 11.0, 13.0]),
                             force_all_median=True)
    rc = out["_rf_consistency"]
    assert rc["active"] is False
    assert rc["reason_inert"] == "bootstrap_pool"
    assert rc["excluded"] == []
    assert out["rf_thickness_mm"] == 12.0                # plain median of all 5 — S2 kept


def test_aggregate_features_rf_consistency_bootstrap_duplicate_frame_base() -> None:
    """item-9 implementation audit (P1): the N5 bootstrap pool can carry the
    same `frame_base` twice — one frame assessed in two distinct runs. The gate
    is skipped there, so the frame_base-keyed exclusion can never double-drop a
    duplicate; the pool aggregates cleanly."""
    pool = _sweep([10.0, 12.0, 40.0, 11.0, 13.0])
    pool.append(_frame("S2", rf_thickness_mm=41.0))      # a duplicate frame_base
    out = aggregate_features(pool, force_all_median=True)
    assert out["_rf_consistency"]["reason_inert"] == "bootstrap_pool"
    assert out["rf_thickness_mm"] == 12.5                # median of all 6, no double-drop


def test_rf_consistency_report_records_range_and_trimmed_median() -> None:
    """The audit payload carries `range_mm` (the full span — recorded even on a
    zero-MAD sweep, so a zero-MAD false-negative stays visible) and
    `trimmed_median_mm` (the rf_thickness_mm median over the kept frames — the
    clean sensitivity figure once outliers are dropped)."""
    flagged = _rf_consistency_report(_sweep([10.0, 12.0, 40.0, 11.0, 13.0]))
    assert flagged["range_mm"] == 30.0                   # 40 - 10
    assert flagged["median_mm"] == 12.0                  # raw median, S2 in
    assert flagged["trimmed_median_mm"] == 11.5          # median(10,12,11,13), S2 dropped
    zero = _rf_consistency_report(_sweep([20.0, 20.0, 20.0, 20.0, 40.0]))
    assert zero["reason_inert"] == "zero_mad"
    assert zero["range_mm"] == 20.0                      # recorded — a visible missed outlier
    assert zero["trimmed_median_mm"] is None             # gate inert — nothing trimmed

"""Build-Step 2 Phase 1 (items 2 + 3) — the QA detector entry point, the
gating spine, and the ImageQualityFeatures record contract.

docs/rf_qa_canonical_plan_v0_1.md / build_step_2_plan.md items 2 and 3:
  * compute_image_quality_flags — the § 5.5 / § 7.2 entry point. Phase 1 ships
    the locked contract; the detector registry is empty until Phase 2.
  * image_quality_tier — the § 6.1 rule encoding the § 6.2 gating table.
  * image_quality_aggregate_tier — the § 6.3 per-frame badge.
  * ImageQualityFeatures — the § 7.1 per-frame QA record.
"""
from __future__ import annotations

import dataclasses
import json

import numpy as np
import pytest
import scipy.ndimage as ndi

from labeling_app.image_quality import (
    BORDERLINE, GATING_MEASUREMENTS, HEADLINE_MEASUREMENTS, INVALID, VALID,
    _DETECTOR_REGISTRY, _detect_air_gap_reverberation, _detect_dark_clip_high,
    _detect_edge_clip_lateral, _detect_edge_clip_vertical,
    _detect_femur_off_central_zone, _detect_femur_out_of_frame,
    _detect_motion_blur, _detect_near_field_clutter,
    _detect_saturation_high_global, _detect_saturation_high_regional,
    _detect_tgc_banding, _detect_vessel_shadow_in_roi, _detect_wrong_site,
    compute_image_quality_flags, detect_segmentation_low_confidence,
    detect_subq_fat_compressed, dominant_reason, image_quality_aggregate_tier,
    image_quality_tier, image_quality_tier_per_measurement,
)
from labeling_app.measurements import (
    IMAGE_QUALITY_FLAG_PRIORITY, LABEL_FASCIA, LABEL_FAT, LABEL_FEMUR, LABEL_RF,
    LABEL_SKIN, LABEL_VI, LABEL_VL, LABEL_VM, frame_qmt_validity_flags,
    label_area_px,
)
from labeling_app.qa_thresholds import THRESHOLDS
from preflight_helpers.feature_compute import ImageQualityFeatures


# --- item 2: LABEL_VL / LABEL_VM (the wrong_site prerequisite, § 5.2) -------

def test_label_vl_vm_constants() -> None:
    assert LABEL_VL == 3
    assert LABEL_VM == 4


# --- item 2: the detector entry point (§ 5.5 / § 7.2) ----------------------

def test_compute_image_quality_flags_returns_documented_shape() -> None:
    """The § 5.5 entry-point contract: {'flags': a canonically-ordered tuple,
    'detector_metadata': one entry per registered detector}. Phase 2 fills the
    registry, so the shape is exercised against a real (clean) frame — every
    registry detector runs and records metadata, and a clean frame raises no
    flag (§ 15.1 Example A — the silent path)."""
    img, mask = _clean_frame()
    out = compute_image_quality_flags(img, mask)
    assert set(out) == {"flags", "detector_metadata"}
    assert isinstance(out["flags"], tuple)
    assert out["flags"] == ()
    assert set(out["detector_metadata"]) == {n for n, _ in _DETECTOR_REGISTRY}


def test_compute_image_quality_flags_rejects_mismatched_shapes() -> None:
    """img_gray must be the _final.png at mask resolution (§ 7.2) — a resize
    mismatch fails loud, never produces a misaligned per-pixel verdict."""
    with pytest.raises(ValueError, match="share a shape"):
        compute_image_quality_flags(np.zeros((8, 8), np.uint8),
                                    np.zeros((9, 9), np.uint8))


def test_compute_image_quality_flags_rejects_out_of_taxonomy_flag(monkeypatch) -> None:
    """A detector that returns a flag outside the 22-code taxonomy fails loud.
    A typo'd code (motion_blr) must never be silently dropped into a clean
    verdict — review hardening of the Phase-1 detector entry point."""
    def _buggy_detector(img_gray, mask):
        return {"flag": "motion_blr", "metadata": {}}      # typo of motion_blur
    monkeypatch.setattr("labeling_app.image_quality._DETECTOR_REGISTRY",
                        [("motion_blur", _buggy_detector)])
    with pytest.raises(ValueError, match="taxonomy"):
        compute_image_quality_flags(np.zeros((8, 8), np.uint8),
                                    np.zeros((8, 8), np.uint8))


def test_compute_image_quality_flags_runs_valid_detectors(monkeypatch) -> None:
    """A detector emitting a canonical code, and one emitting None (did not
    fire), both flow through cleanly — the entry point collects the flag, keeps
    every detector's metadata, and canonicalises the flag order."""
    def _fires(img_gray, mask):
        return {"flag": "motion_blur", "metadata": {"tenengrad": 9.9}}

    def _silent(img_gray, mask):
        return {"flag": None, "metadata": {"tenengrad": 88.0}}

    monkeypatch.setattr("labeling_app.image_quality._DETECTOR_REGISTRY",
                        [("motion_blur", _fires), ("tgc_banding", _silent)])
    out = compute_image_quality_flags(np.zeros((8, 8), np.uint8),
                                      np.zeros((8, 8), np.uint8))
    assert out["flags"] == ("motion_blur",)
    assert out["detector_metadata"]["motion_blur"] == {"tenengrad": 9.9}
    assert out["detector_metadata"]["tgc_banding"] == {"tenengrad": 88.0}


# --- item 2: the ImageQualityFeatures record contract (§ 7.1) --------------

def test_image_quality_features_constructs_and_round_trips() -> None:
    flags = ("motion_blur", "edge_clip_lateral")
    iqf = ImageQualityFeatures(
        frame_base="20260514_181856256",
        image_quality_validity_flags=flags,
        image_quality_pending_calibration_flags=("motion_blur",),
        image_quality_detector_metadata={"motion_blur": {"tenengrad": 12.4}},
        image_quality_tier_per_measurement=image_quality_tier_per_measurement(flags),
        image_quality_aggregate_tier=image_quality_aggregate_tier(flags),
        image_quality_contract_version="v0.2",
    )
    # Round-trip through the JSON ledger (§ 7.0 — the ledger is JSON on disk).
    back = ImageQualityFeatures.from_dict(json.loads(json.dumps(iqf.to_dict())))
    assert back == iqf


# --- item 3: the § 6.2 gating table, cell by cell --------------------------

# An INDEPENDENT transcription of the § 6.2 table — a visual grid, deliberately
# a different form from image_quality._GATING_TABLE so a copy error would have
# to be made identically in two formats. Columns are GATING_MEASUREMENTS order;
# tokens: '.' transparent (blank cell), 'I' INV, 'B' BDR.
#                                    rf_th qmt star full area  cv  echo fat
_SIX_TWO_GRID = """
wrong_site                            I   I   I   I   I   I   I   I
segmenter_mislabel_wrong_class         I   I   I   I   I   I   I   I
femur_out_of_frame                     .   I   I   .   .   .   .   .
femur_off_central_zone                 .   B   B   .   .   .   .   .
edge_clip_lateral                      .   .   .   .   .   .   .   .
edge_clip_vertical                     I   B   B   I   .   .   .   .
saturation_high_global                 .   .   .   .   .   I   I   .
saturation_high_regional               B   .   .   .   .   B   .   .
dark_clip_high                         .   B   B   .   .   I   I   .
tgc_banding                            .   .   .   .   .   B   B   .
motion_blur                            I   I   I   I   I   B   .   .
air_gap_reverberation                  I   I   I   I   I   B   B   I
near_field_clutter                     B   .   .   B   B   .   .   .
vessel_shadow_in_roi                   I   I   I   I   I   B   B   .
posterior_acoustic_enhancement         .   .   .   .   .   B   B   .
subq_fat_compressed                    .   B   B   .   .   .   .   I
oblique_plane                          I   I   I   I   I   B   B   .
anisotropy_dropout_superficial         B   .   .   B   B   .   .   .
anisotropy_dropout_deep                B   I   I   B   B   .   .   .
deep_attenuation_high                  .   B   B   B   .   .   .   .
segmentation_low_confidence            B   B   B   B   B   .   .   B
probe_orientation_flipped              .   .   .   .   .   .   .   .
"""

_TOKEN = {"I": INVALID, "B": BORDERLINE, ".": VALID}


def _parse_six_two() -> dict:
    parsed: dict = {}
    for line in _SIX_TWO_GRID.strip().splitlines():
        cells = line.split()
        flag, tokens = cells[0], cells[1:]
        assert len(tokens) == len(GATING_MEASUREMENTS), f"{flag}: {len(tokens)} cells"
        parsed[flag] = {m: _TOKEN[t] for m, t in zip(GATING_MEASUREMENTS, tokens)}
    return parsed


def test_six_two_grid_covers_the_whole_taxonomy() -> None:
    """The independent grid lists exactly the 22-code taxonomy — so the
    cell-by-cell test below is complete, not partial."""
    assert set(_parse_six_two()) == set(IMAGE_QUALITY_FLAG_PRIORITY)
    assert len(IMAGE_QUALITY_FLAG_PRIORITY) == 22


def test_image_quality_tier_reproduces_every_six_two_cell() -> None:
    """build_step_2_plan.md item 3 done-when: a table-driven test reproduces
    every § 6.2 cell (22 flags x 8 measurements = 176 cells)."""
    parsed = _parse_six_two()
    for flag, row in parsed.items():
        for measurement, expected in row.items():
            got = image_quality_tier(measurement, (flag,))
            assert got == expected, f"{flag} / {measurement}: {got!r} != {expected!r}"


def test_image_quality_tier_inv_beats_bdr() -> None:
    """A frame carrying both a BDR flag and an INV flag for one measurement
    grades invalid (§ 6.1 — INV beats BDR beats valid)."""
    # qmt_mm: femur_off_central_zone is BDR, femur_out_of_frame is INV.
    assert image_quality_tier(
        "qmt_mm", ("femur_off_central_zone", "femur_out_of_frame")) == INVALID


def test_image_quality_tier_measurement_aliases() -> None:
    """Measurement aliases route to canonical entries (§ 6.2). The int_*
    family gates as `rf_echo`. NB: full_area_mm2 / visible_rf_area_mm2
    no longer taints under edge_clip_lateral (Akadir 2026-05-24 — reserve
    state); a different flag (saturation_high_global) is used here to
    exercise the int_* alias chain."""
    for m in ("int_mean", "int_std", "int_median", "int_entropy"):
        assert image_quality_tier(m, ("saturation_high_global",)) == INVALID
    # full_area_mm2 / visible_rf_area_mm2 stay valid under edge_clip_lateral
    # — the C11 coverage-limitation rule was the LETTER of the spec; reserve
    # state is the SPIRIT (visible measurement = visible portion, by def).
    assert image_quality_tier("full_area_mm2", ("edge_clip_lateral",)) == VALID
    assert image_quality_tier("visible_rf_area_mm2", ("edge_clip_lateral",)) == VALID


def test_image_quality_tier_unknown_flag_is_transparent() -> None:
    """A code outside the taxonomy taints nothing (§ 6.1 — the blank cell)."""
    assert image_quality_tier("qmt_mm", ("not_a_real_flag",)) == VALID


def test_image_quality_tier_unknown_measurement_raises() -> None:
    with pytest.raises(ValueError):
        image_quality_tier("not_a_measurement", ())


# --- item 3: the § 6.3 aggregate tier + the § 15 worked examples -----------

def test_aggregate_example_a_clean_frame_is_valid() -> None:
    """§ 15.1 Example A — a clean frame: no flags, every measurement valid,
    aggregate Valid, no dominant reason."""
    tiers = image_quality_tier_per_measurement(())
    assert all(t == VALID for t in tiers.values())
    assert image_quality_aggregate_tier(()) == VALID
    assert dominant_reason(()) is None


def test_aggregate_example_b_borderline_frame() -> None:
    """§ 15.2 Example B — femur_off_central_zone + edge_clip_lateral: QMT/STAR
    borderline (femur_off), visible_rf_area NOT invalid any more
    (edge_clip_lateral is reserve — Akadir 2026-05-24). Aggregate
    Borderline because qmt+star are BDR; no headline INV."""
    flags = ("femur_off_central_zone", "edge_clip_lateral")
    assert image_quality_tier_per_measurement(flags) == {
        "rf_thickness_mm": VALID, "qmt_mm": BORDERLINE, "star_ratio": BORDERLINE,
        "full_thickness_q50_mm": VALID, "visible_rf_area_mm2": VALID,
        "int_cv_within": VALID, "rf_echo": VALID, "muscle_fat_ratio": VALID,
    }
    assert image_quality_aggregate_tier(flags) == BORDERLINE
    # § 5.1 priority: femur_off_central_zone outranks edge_clip_lateral.
    assert dominant_reason(flags) == "femur_off_central_zone"


def test_aggregate_headline_invalid_forces_invalid() -> None:
    """Any headline-4 measurement invalid -> aggregate Invalid (§ 6.3)."""
    # motion_blur -> rf_thickness_mm INV (a headline measurement).
    assert image_quality_tier("rf_thickness_mm", ("motion_blur",)) == INVALID
    assert image_quality_aggregate_tier(("motion_blur",)) == INVALID


def test_aggregate_edge_clip_lateral_alone_stays_valid() -> None:
    """edge_clip_lateral is RESERVE STATE (Akadir 2026-05-24): fires +
    goes on the record, taints nothing. Every measurement valid, aggregate
    Valid. The earlier C11 coverage-limitation rule (INV on the area
    feature) was the LETTER of the spec; reserve state is the SPIRIT —
    visible measurement = visible portion by definition; FOV cannot
    invalidate it."""
    flags = ("edge_clip_lateral",)
    assert image_quality_tier("visible_rf_area_mm2", flags) == VALID
    assert image_quality_aggregate_tier(flags) == VALID


def test_headline_and_gating_measurement_sets() -> None:
    """§ 6.3 — the headline-4 are a subset of the eight gating-table columns."""
    assert set(HEADLINE_MEASUREMENTS) <= set(GATING_MEASUREMENTS)
    assert len(HEADLINE_MEASUREMENTS) == 4
    assert len(GATING_MEASUREMENTS) == 8


# ===========================================================================
# Build-Step 2 Phase 2 — the detectors (build_step_2_plan.md items 5 + 6)
# ===========================================================================
# Each detector's done-when (build_step_2_plan.md): fires on a synthetic
# positive, stays silent on a synthetic negative. For a Tier-B detector keyed
# on a NaN-shipped threshold the firing logic is exercised against a calibrated
# value injected by `_calibrate`; a separate assertion confirms that, with the
# *shipped* registry, the detector never fires — the § 8.6 safety belt, item
# 6's "no guessed verdict ships as fact".


def _calibrate(monkeypatch, name, value) -> None:
    """Inject a concrete value for a Tier-B threshold so a NaN-shipped
    detector's firing logic can be exercised — the shipped value is NaN."""
    monkeypatch.setitem(THRESHOLDS, name,
                        dataclasses.replace(THRESHOLDS[name], value=value))


def _clean_frame():
    """A good RF frame (§ 15.1 Example A) — every registry detector silent.
    Anatomically layered skin / fat / superficial fascia / RF body / deep
    fascia / femur, all interior to the frame, mid-gray with no rail clipping."""
    h, w = 140, 120
    mask = np.zeros((h, w), np.uint8)
    img = np.full((h, w), 110, np.uint8)
    mask[3:19, 12:108] = LABEL_SKIN
    mask[19:34, 12:108] = LABEL_FAT
    mask[34:38, 24:96] = LABEL_FASCIA            # superficial fascia
    mask[38:86, 24:96] = LABEL_RF                # RF muscle body
    mask[86:90, 24:96] = LABEL_FASCIA            # deep fascia
    mask[92:108, 42:78] = LABEL_FEMUR            # femur — central, plateau
    return img, mask


# --- item 5: the clean frame — every registry detector silent ---------------

def test_clean_frame_no_registry_detector_fires() -> None:
    """build_step_2_plan.md items 5–6 "stays silent on a synthetic negative",
    aggregated: a good frame trips none of the 13 registry detectors."""
    img, mask = _clean_frame()
    out = compute_image_quality_flags(img, mask)
    assert out["flags"] == ()


# --- item 5: femur_out_of_frame --------------------------------------------

def test_femur_out_of_frame_fires_when_no_femur() -> None:
    mask = np.zeros((60, 60), np.uint8)
    mask[20:40, 20:40] = LABEL_RF                # RF present, no femur
    assert _detect_femur_out_of_frame(np.zeros((60, 60), np.uint8), mask)["flag"] \
        == "femur_out_of_frame"


def test_femur_out_of_frame_silent_when_femur_present() -> None:
    img, mask = _clean_frame()
    assert _detect_femur_out_of_frame(img, mask)["flag"] is None


def test_femur_out_of_frame_agrees_with_qmt_femur_absent() -> None:
    """The done-when "agree with QMT_VALIDITY_FLAG_PRIORITY": no femur pixels
    ⇔ the QMT path's `femur_absent` ⇔ the detector fires (§ 5.2 reuse)."""
    no_femur = np.zeros((60, 60), np.uint8)
    no_femur[20:40, 20:40] = LABEL_RF
    assert "femur_absent" in frame_qmt_validity_flags(no_femur)
    assert _detect_femur_out_of_frame(np.zeros((60, 60), np.uint8), no_femur)["flag"]
    clean_img, with_femur = _clean_frame()
    assert "femur_absent" not in frame_qmt_validity_flags(with_femur)
    assert _detect_femur_out_of_frame(clean_img, with_femur)["flag"] is None


def test_femur_out_of_frame_fires_on_subthreshold_stray_pixels() -> None:
    """Akadir 2026-05-25 v2 § 1 — a frame with stray femur-class pixels
    *below* `QMT_FEMUR_MIN_AREA_PX` (the sub-threshold-stray case) must
    fire `femur_out_of_frame`. The earlier cut used raw `label_area_px`
    and stayed silent here, while the picker tier dot + the QMT
    `femur_absent` flag both used the dominant-component definition and
    correctly reported "no femur". The asymmetry surfaced as two
    no-femur thumbnails showing different cue/no-cue states on the
    picker — same femur state, different flag sets. The detector is now
    aligned with `_dominant_femur_mask`, so all functionally-femur-less
    frames fire `femur_out_of_frame` uniformly. The raw count survives
    in metadata for traceability."""
    from labeling_app.measurements import QMT_FEMUR_MIN_AREA_PX
    mask = np.zeros((60, 60), np.uint8)
    mask[20:40, 20:40] = LABEL_RF
    # Add a 2-pixel stray femur — well below the 5-px threshold.
    mask[5, 5] = LABEL_FEMUR
    mask[6, 5] = LABEL_FEMUR
    assert label_area_px(mask, LABEL_FEMUR) < QMT_FEMUR_MIN_AREA_PX
    out = _detect_femur_out_of_frame(np.zeros((60, 60), np.uint8), mask)
    assert out["flag"] == "femur_out_of_frame", (
        "Sub-threshold stray femur should still fire femur_out_of_frame — "
        "matches the picker tier-3 dot ('no femur') and the QMT "
        "`femur_absent` flag. The earlier raw-pixel-count path stayed "
        "silent here and broke the picker/IQ symmetry.")
    # Metadata still distinguishes the case (raw count > 0) for trace_frame.
    assert out["metadata"]["raw_femur_pixel_count"] == 2
    assert out["metadata"]["dominant_femur_pixel_count"] == 0


# --- item 5: femur_off_central_zone ----------------------------------------

def test_femur_off_central_zone_fires_femur_one_side_rf_other() -> None:
    """A femur on the left with the RF body on the right: no QMT measurement
    column lands in the femur-anchored central zone, so the QMT path emits
    `outside_central_zone` — lifted to the frame-level flag (§ 5.2)."""
    h, w = 120, 150
    mask = np.zeros((h, w), np.uint8)
    mask[80:96, 8:32] = LABEL_FEMUR              # femur — far left
    mask[30:66, 100:138] = LABEL_RF              # RF body — far right
    assert "outside_central_zone" in frame_qmt_validity_flags(mask)
    assert _detect_femur_off_central_zone(
        np.zeros((h, w), np.uint8), mask)["flag"] == "femur_off_central_zone"


def test_femur_off_central_zone_silent_on_central_femur() -> None:
    img, mask = _clean_frame()
    assert _detect_femur_off_central_zone(img, mask)["flag"] is None


# --- item 5: edge_clip_lateral / _vertical ---------------------------------

def test_edge_clip_lateral_fires_on_rf_at_side_edge() -> None:
    mask = np.zeros((60, 60), np.uint8)
    mask[20:40, 0:30] = LABEL_RF                 # RF runs off the left edge
    res = _detect_edge_clip_lateral(np.zeros((60, 60), np.uint8), mask)
    assert res["flag"] == "edge_clip_lateral"
    assert "left" in res["metadata"]["edges"]


def test_edge_clip_lateral_silent_when_rf_interior() -> None:
    img, mask = _clean_frame()
    assert _detect_edge_clip_lateral(img, mask)["flag"] is None


def test_edge_clip_vertical_fires_on_clipped_superficial_fascia() -> None:
    """F6 / Comment 10 — the superficial fascia runs off the top edge while the
    RF body itself is interior: edge_clip_vertical fires on the fascia."""
    mask = np.zeros((60, 60), np.uint8)
    mask[0:4, 20:40] = LABEL_FASCIA              # fascia clipped at the top
    mask[6:34, 20:40] = LABEL_RF                 # RF body interior
    res = _detect_edge_clip_vertical(np.zeros((60, 60), np.uint8), mask)
    assert res["flag"] == "edge_clip_vertical"
    assert "fascia" in res["metadata"]["clipped"]["top"]


def test_edge_clip_vertical_silent_when_structures_interior() -> None:
    img, mask = _clean_frame()
    assert _detect_edge_clip_vertical(img, mask)["flag"] is None


def test_edge_clip_vertical_ignores_femur_at_bottom_edge() -> None:
    """The femur runs off the BOTTOM edge by anatomy — the bone extends past the
    imaging window and QMT reads only its near surface, so a femur edge-touch is
    NOT a clip and must not fire (Akadir 2026-06-26). RF + fascia interior here,
    only the femur hits the bottom row."""
    H = 60
    mask = np.zeros((H, 60), np.uint8)
    mask[6:10, 20:40] = LABEL_FASCIA             # superficial fascia, interior (not row 0)
    mask[12:34, 20:40] = LABEL_RF                # RF body interior
    mask[H - 1, 20:40] = LABEL_FEMUR            # femur touching the bottom row
    res = _detect_edge_clip_vertical(np.zeros((H, 60), np.uint8), mask)
    assert res["flag"] is None, res["metadata"]


def test_edge_clip_vertical_still_fires_on_rf_at_bottom_edge() -> None:
    """A real RF / deep-fascia clip at the bottom still truncates the thickness
    and must still fire — only the femur is exempt."""
    H = 60
    mask = np.zeros((H, 60), np.uint8)
    mask[6:10, 20:40] = LABEL_FASCIA
    mask[12:H, 20:40] = LABEL_RF                 # RF runs off the bottom edge
    res = _detect_edge_clip_vertical(np.zeros((H, 60), np.uint8), mask)
    assert res["flag"] == "edge_clip_vertical"
    assert "rf" in res["metadata"]["clipped"]["bottom"]


# --- item 5: saturation / dark-clip ----------------------------------------

def _roi_frame(h=80, w=80):
    mask = np.zeros((h, w), np.uint8)
    mask[20:60, 20:60] = LABEL_RF                # a 1600-px RF ROI
    return np.full((h, w), 110, np.uint8), mask


def test_saturation_high_global_fires_and_silent() -> None:
    img, mask = _roi_frame()
    img[20:50, 20:60] = 255                      # 1200 / 1600 ROI px railed
    assert _detect_saturation_high_global(img, mask)["flag"] == "saturation_high_global"
    clean_img, clean_mask = _roi_frame()
    assert _detect_saturation_high_global(clean_img, clean_mask)["flag"] is None


def test_dark_clip_high_fires_and_silent() -> None:
    img, mask = _roi_frame()
    img[20:50, 20:60] = 0                        # 1200 / 1600 ROI px at the 0 rail
    assert _detect_dark_clip_high(img, mask)["flag"] == "dark_clip_high"
    clean_img, clean_mask = _roi_frame()
    assert _detect_dark_clip_high(clean_img, clean_mask)["flag"] is None


def test_saturation_high_regional_fires_on_concentrated_patch() -> None:
    """A bright patch concentrated in one 32x32 block trips the regional
    detector while staying well under the global ROI fraction."""
    h, w = 200, 200
    mask = np.zeros((h, w), np.uint8)
    mask[10:190, 10:190] = LABEL_RF              # a large RF ROI
    img = np.full((h, w), 110, np.uint8)
    img[40:52, 40:50] = 255                      # 120 railed px inside one block
    res = _detect_saturation_high_regional(img, mask)
    assert res["flag"] == "saturation_high_regional"
    # the same patch is far below the 2% global cut-off — the detectors differ
    assert _detect_saturation_high_global(img, mask)["flag"] is None


def test_saturation_high_regional_silent_on_clean_frame() -> None:
    img, mask = _clean_frame()
    assert _detect_saturation_high_regional(img, mask)["flag"] is None


# --- item 6: wrong_site -----------------------------------------------------

def test_wrong_site_fires_when_rf_column_coverage_is_low() -> None:
    """Akadir 2026-05-24 redesign — wrong_site fires on geometry, not
    composition: a thin RF blob covering only a few columns means too
    little RF to assess, regardless of VL/VM around it."""
    mask = np.zeros((70, 70), np.uint8)
    mask[10:20, 10:20] = LABEL_RF                # 10 cols (~14%)
    mask[10:50, 25:55] = LABEL_VL                # VL alongside — irrelevant now
    res = _detect_wrong_site(np.zeros((70, 70), np.uint8), mask)
    assert res["flag"] == "wrong_site"
    assert res["metadata"]["rf_column_coverage"] < 0.5


def test_wrong_site_silent_when_rf_spans_majority_of_columns() -> None:
    """A wide RF band — even with VL/VM around — is meaningful coverage;
    wrong_site is silent. This was the cohort case (Build-Step 2a Finding
    B): rf_fraction-only fired on 80% of frames because side-shots of RF
    scored low rf_fraction; column-coverage resolves that."""
    mask = np.zeros((70, 70), np.uint8)
    # RF spans 60 of 70 cols at rows 10-30 (~86%).
    mask[10:30, 5:65] = LABEL_RF
    mask[30:50, 5:65] = LABEL_VI
    mask[10:50, 0:5] = LABEL_VL                  # left-edge VL
    mask[10:50, 65:70] = LABEL_VM                # right-edge VM
    res = _detect_wrong_site(np.zeros((70, 70), np.uint8), mask)
    assert res["flag"] is None
    assert res["metadata"]["rf_column_coverage"] >= 0.5


def test_wrong_site_fires_on_no_rf_pixels() -> None:
    """A frame with zero RF (VL/VM only, or no muscle at all) fires
    wrong_site — this is the genuine 'wrong muscle / off-thigh' case the
    detector exists for."""
    mask = np.zeros((50, 50), np.uint8)
    mask[10:30, 10:40] = LABEL_VL                # only VL — no RF anywhere
    res = _detect_wrong_site(np.zeros((50, 50), np.uint8), mask)
    assert res["flag"] == "wrong_site"
    assert res["metadata"]["rf_column_coverage"] == 0.0


def test_wrong_site_no_muscle_pixels_fires() -> None:
    """An empty mask fires wrong_site — zero RF column coverage, the
    genuine off-thigh case. (Previous 0/0 belt was an artefact of the
    composition-ratio detector; geometry is well-defined at 0/N = 0.)"""
    res = _detect_wrong_site(np.zeros((40, 40), np.uint8), np.zeros((40, 40), np.uint8))
    assert res["flag"] == "wrong_site"
    assert res["metadata"]["rf_column_coverage"] == 0.0


# --- item 6: motion_blur ----------------------------------------------------

def _fascia_frame(sharp: bool):
    """A fascia band carrying a bright specular line — crisp when `sharp`,
    Gaussian-blurred otherwise."""
    h, w = 60, 90
    mask = np.zeros((h, w), np.uint8)
    mask[24:32, 12:78] = LABEL_FASCIA
    img = np.full((h, w), 60, np.uint8)
    img[27:29, 12:78] = 240                      # the specular fascia line
    if not sharp:
        img = ndi.gaussian_filter(img.astype(np.float64), sigma=3.0)
        img = img.astype(np.uint8)
    return img, mask


def test_motion_blur_score_separates_blur_from_sharp_and_belt_holds() -> None:
    sharp_img, mask = _fascia_frame(sharp=True)
    blur_img, _ = _fascia_frame(sharp=False)
    sharp = _detect_motion_blur(sharp_img, mask)["metadata"]["fascia_band_tenengrad"]
    blur = _detect_motion_blur(blur_img, mask)["metadata"]["fascia_band_tenengrad"]
    assert blur < sharp                          # motion lowers fascia sharpness
    # safety belt — the shipped Tenengrad threshold is NaN, so a blurred frame
    # raises no flag until Build-Step 2b calibrates it.
    assert _detect_motion_blur(blur_img, mask)["flag"] is None


def test_motion_blur_fires_with_a_calibrated_threshold(monkeypatch) -> None:
    sharp_img, mask = _fascia_frame(sharp=True)
    blur_img, _ = _fascia_frame(sharp=False)
    sharp = _detect_motion_blur(sharp_img, mask)["metadata"]["fascia_band_tenengrad"]
    blur = _detect_motion_blur(blur_img, mask)["metadata"]["fascia_band_tenengrad"]
    _calibrate(monkeypatch, "motion_blur__fascia_band_tenengrad_min", (sharp + blur) / 2)
    assert _detect_motion_blur(blur_img, mask)["flag"] == "motion_blur"
    assert _detect_motion_blur(sharp_img, mask)["flag"] is None


# --- item 6: tgc_banding ----------------------------------------------------

def _tgc_frame(banded: bool):
    h, w = 90, 70
    mask = np.zeros((h, w), np.uint8)
    mask[20:64, 15:55] = LABEL_RF                # a 44-row muscle band
    img = np.full((h, w), 120, np.uint8)
    if banded:
        for r in range(h):
            img[r, :] = np.clip(120 + 40 * np.sin(2 * np.pi * r / 10), 0, 255)
    return img.astype(np.uint8), mask


def test_tgc_banding_score_separates_banded_from_flat_and_belt_holds() -> None:
    banded = _detect_tgc_banding(*_tgc_frame(banded=True))["metadata"]["detrended_residual_sigma"]
    flat = _detect_tgc_banding(*_tgc_frame(banded=False))["metadata"]["detrended_residual_sigma"]
    assert banded > flat
    assert _detect_tgc_banding(*_tgc_frame(banded=True))["flag"] is None     # NaN belt


def test_tgc_banding_fires_with_a_calibrated_threshold(monkeypatch) -> None:
    banded = _detect_tgc_banding(*_tgc_frame(banded=True))["metadata"]["detrended_residual_sigma"]
    flat = _detect_tgc_banding(*_tgc_frame(banded=False))["metadata"]["detrended_residual_sigma"]
    _calibrate(monkeypatch, "tgc_banding__detrended_residual_sigma_max", (banded + flat) / 2)
    assert _detect_tgc_banding(*_tgc_frame(banded=True))["flag"] == "tgc_banding"
    assert _detect_tgc_banding(*_tgc_frame(banded=False))["flag"] is None


# --- item 6: air_gap_reverberation -----------------------------------------

def _air_gap_frame(comb: bool):
    h, w = 90, 70
    mask = np.zeros((h, w), np.uint8)
    mask[2:30, 12:58] = LABEL_SKIN               # near-surface band, depth 30
    img = np.full((h, w), 100, np.uint8)
    if comb:
        for r in range(h):
            img[r, :] = np.clip(100 + 50 * np.sin(2 * np.pi * r / 5), 0, 255)
    return img.astype(np.uint8), mask


def test_air_gap_reverberation_score_separates_comb_and_belt_holds() -> None:
    comb = _detect_air_gap_reverberation(*_air_gap_frame(comb=True))["metadata"]["comb_peak_magnitude"]
    flat = _detect_air_gap_reverberation(*_air_gap_frame(comb=False))["metadata"]["comb_peak_magnitude"]
    assert comb > flat
    assert _detect_air_gap_reverberation(*_air_gap_frame(comb=True))["flag"] is None  # NaN belt


def test_air_gap_reverberation_fires_with_a_calibrated_threshold(monkeypatch) -> None:
    comb = _detect_air_gap_reverberation(*_air_gap_frame(comb=True))["metadata"]["comb_peak_magnitude"]
    flat = _detect_air_gap_reverberation(*_air_gap_frame(comb=False))["metadata"]["comb_peak_magnitude"]
    _calibrate(monkeypatch, "air_gap_reverberation__comb_peak_magnitude_min", (comb + flat) / 2)
    assert _detect_air_gap_reverberation(*_air_gap_frame(comb=True))["flag"] == "air_gap_reverberation"
    assert _detect_air_gap_reverberation(*_air_gap_frame(comb=False))["flag"] is None


# --- item 6: near_field_clutter --------------------------------------------

def _near_field_frame(noisy: bool):
    h, w = 80, 70
    mask = np.zeros((h, w), np.uint8)
    mask[2:18, 12:58] = LABEL_SKIN
    mask[18:34, 12:58] = LABEL_FAT
    img = np.full((h, w), 100, np.uint8)
    if noisy:
        rng = np.random.default_rng(0)
        near = (mask == LABEL_SKIN) | (mask == LABEL_FAT)
        noise = rng.integers(-70, 70, size=(h, w))
        img = np.clip(img.astype(np.int64) + noise * near, 0, 255).astype(np.uint8)
    return img, mask


def test_near_field_clutter_score_separates_noisy_and_belt_holds() -> None:
    noisy = _detect_near_field_clutter(*_near_field_frame(noisy=True))["metadata"]["clutter_energy"]
    smooth = _detect_near_field_clutter(*_near_field_frame(noisy=False))["metadata"]["clutter_energy"]
    assert noisy > smooth
    assert _detect_near_field_clutter(*_near_field_frame(noisy=True))["flag"] is None  # NaN belt


def test_near_field_clutter_fires_with_a_calibrated_threshold(monkeypatch) -> None:
    noisy = _detect_near_field_clutter(*_near_field_frame(noisy=True))["metadata"]["clutter_energy"]
    smooth = _detect_near_field_clutter(*_near_field_frame(noisy=False))["metadata"]["clutter_energy"]
    _calibrate(monkeypatch, "near_field_clutter__clutter_energy_max", (noisy + smooth) / 2)
    assert _detect_near_field_clutter(*_near_field_frame(noisy=True))["flag"] == "near_field_clutter"
    assert _detect_near_field_clutter(*_near_field_frame(noisy=False))["flag"] is None


# --- item 6: vessel_shadow_in_roi ------------------------------------------

def _vessel_frame(shadow: bool):
    h, w = 60, 90
    mask = np.zeros((h, w), np.uint8)
    mask[15:45, 12:78] = LABEL_RF
    img = np.full((h, w), 120, np.uint8)
    if shadow:
        img[:, 42:46] = 4                        # a near-zero vertical shadow column
    return img, mask


def test_vessel_shadow_score_separates_shadow_and_belt_holds() -> None:
    shadow = _detect_vessel_shadow_in_roi(*_vessel_frame(shadow=True))["metadata"]["darkest_column_mean"]
    clear = _detect_vessel_shadow_in_roi(*_vessel_frame(shadow=False))["metadata"]["darkest_column_mean"]
    assert shadow < clear
    assert _detect_vessel_shadow_in_roi(*_vessel_frame(shadow=True))["flag"] is None  # NaN belt


def test_vessel_shadow_fires_with_a_calibrated_threshold(monkeypatch) -> None:
    _calibrate(monkeypatch, "vessel_shadow_in_roi__column_darkness_max", 40.0)
    assert _detect_vessel_shadow_in_roi(*_vessel_frame(shadow=True))["flag"] == "vessel_shadow_in_roi"
    assert _detect_vessel_shadow_in_roi(*_vessel_frame(shadow=False))["flag"] is None


# --- item 6: the two standalone detectors ----------------------------------

def test_segmentation_low_confidence_belt_and_calibrated(monkeypatch) -> None:
    """The boundary-entropy reduction (§ 5.4) — un-calibrated (NaN) so silent
    today; with a calibrated entropy cut-off it separates high from low."""
    assert detect_segmentation_low_confidence(9.9)["flag"] is None          # NaN belt
    _calibrate(monkeypatch, "segmentation_low_confidence__boundary_entropy_max", 0.5)
    assert detect_segmentation_low_confidence(1.2)["flag"] == "segmentation_low_confidence"
    assert detect_segmentation_low_confidence(0.05)["flag"] is None


def _fat_frame(fat_rows: int):
    """A frame whose subcutaneous-fat band is `fat_rows` pixels thick across
    the RF muscle's column span."""
    h, w = 100, 70
    mask = np.zeros((h, w), np.uint8)
    mask[6:6 + fat_rows, 15:55] = LABEL_FAT
    mask[45:85, 15:55] = LABEL_RF
    return mask


# pixel_spacing_y * dcm_rows / mask_height = 0.1 * 600 / 120 = 0.5 mm per px
_FAT_SPACING = {"pixel_spacing_y": 0.1, "dcm_rows": 600, "mask_height": 120}


def test_subq_fat_compressed_fires_on_a_thin_fat_layer() -> None:
    """A 4-px fat band -> 2.0 mm < the 3 mm starting point -> fires. The 3 mm
    threshold is a Tier-B starting point (real, not NaN), so this Tier-B
    detector does produce a (provisional) verdict on the shipped registry."""
    res = detect_subq_fat_compressed(_fat_frame(4), _FAT_SPACING)
    assert res["flag"] == "subq_fat_compressed"
    assert res["metadata"]["central_fat_thickness_mm"] == 2.0


def test_subq_fat_compressed_silent_on_a_thick_fat_layer() -> None:
    res = detect_subq_fat_compressed(_fat_frame(12), _FAT_SPACING)   # 6.0 mm
    assert res["flag"] is None


def test_subq_fat_compressed_no_spacing_yields_no_verdict() -> None:
    """Without pixel spacing the mm threshold cannot be applied — the detector
    reports the px measurement and withholds a verdict (honest, not guessed)."""
    res = detect_subq_fat_compressed(_fat_frame(4), {})
    assert res["flag"] is None
    assert res["metadata"]["spacing_unavailable"] is True


# --- the registry itself ----------------------------------------------------

def test_detector_registry_is_fourteen_canonical_image_mask_detectors() -> None:
    assert len(_DETECTOR_REGISTRY) == 14   # +anisotropy_dropout_superficial (E4)
    for name, fn in _DETECTOR_REGISTRY:
        assert name in IMAGE_QUALITY_FLAG_PRIORITY, name
        assert callable(fn), name
    assert len({n for n, _ in _DETECTOR_REGISTRY}) == 14           # no duplicates
    assert "anisotropy_dropout_superficial" in {n for n, _ in _DETECTOR_REGISTRY}


def test_standalone_detectors_are_not_registry_members() -> None:
    """`segmentation_low_confidence` / `subq_fat_compressed` need pipeline
    context the (img_gray, mask) registry contract cannot carry — they are
    standalone, Phase-3-plumbed (see the image_quality module docstring)."""
    names = {n for n, _ in _DETECTOR_REGISTRY}
    assert "segmentation_low_confidence" not in names
    assert "subq_fat_compressed" not in names


def test_compute_image_quality_flags_surfaces_a_registry_detector() -> None:
    """End to end through the § 5.5 entry point: a frame with no femur grades
    `femur_out_of_frame`, canonically ordered, with its metadata recorded."""
    mask = np.zeros((60, 60), np.uint8)
    mask[20:40, 20:40] = LABEL_RF                # RF present, no femur
    out = compute_image_quality_flags(np.full((60, 60), 110, np.uint8), mask)
    assert "femur_out_of_frame" in out["flags"]
    assert out["detector_metadata"]["femur_out_of_frame"]["raw_femur_pixel_count"] == 0
    assert out["detector_metadata"]["femur_out_of_frame"]["dominant_femur_pixel_count"] == 0


# --- calibration provenance (the audit P0 #2 fix) --------------------------

def test_detector_metadata_carries_calibration_provenance() -> None:
    """Every detector stamps its metadata with `thresholds_used` and
    `pending_calibration` (§ 11.1 / § 8.6) — the signal Phase 3 gates an
    un-calibrated verdict on. The 4 pure Tier-A detectors are calibrated; every
    Tier-B detector — whether NaN-belted or on a finite starting point — is
    pending. The boundary is is_starting_point, not "is the threshold NaN"."""
    img, mask = _clean_frame()
    md = compute_image_quality_flags(img, mask)["detector_metadata"]
    # Calibrated set — Akadir 2026-05-24 moved wrong_site into this set
    # after the column-coverage redesign: the 0.5 half-columns rule is a
    # geometric design constant, not a tunable Tier-B threshold.
    for name in ("femur_out_of_frame", "femur_off_central_zone",
                 "edge_clip_lateral", "edge_clip_vertical", "wrong_site"):
        assert md[name]["pending_calibration"] is False, name
    for name in ("saturation_high_global", "saturation_high_regional",
                 "dark_clip_high", "motion_blur", "tgc_banding",
                 "air_gap_reverberation", "near_field_clutter",
                 "vessel_shadow_in_roi"):
        assert md[name]["pending_calibration"] is True, name
    # the recorded names resolve in the registry — no typo, no drift
    used = md["saturation_high_global"]["thresholds_used"]
    assert "saturation_high__pixel_value_min" in used
    assert all(name in THRESHOLDS for name in used)


def test_standalone_detectors_carry_calibration_provenance() -> None:
    """The two standalone detectors stamp the same provenance — both are
    un-calibrated Tier-B, so Phase 3 reads pending_calibration=True."""
    seg = detect_segmentation_low_confidence(0.1)
    fat = detect_subq_fat_compressed(np.zeros((20, 20), np.uint8), {})
    assert seg["metadata"]["pending_calibration"] is True
    assert fat["metadata"]["pending_calibration"] is True

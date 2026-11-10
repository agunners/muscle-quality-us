"""center_rf_scorer: shadow-aware texture-frame ranking (Akadir 2026-07-16).

The physics under test: a dark patch with measurable echo DEEPER in its column is
real hypoechoic tissue (NOT shadow); a dark column with nothing deeper is a true
acoustic shadow. int_cv is corrupted only by the latter, so the least-shadow clean
frame ranks highest and a shadow-free study is a no-op. Pure numpy — no model."""
import numpy as np

from labeling_app import center_rf_scorer as crs
from labeling_app.measurements import LABEL_RF

H, W = 60, 40


def _mask():
    m = np.zeros((H, W), np.uint8)
    m[10:36, 5:35] = LABEL_RF          # an RF block that survives the 3px erosion
    return m


def _img(kind):
    im = np.full((H, W), 100, np.uint8)   # bright echo everywhere by default
    if kind == "tissue":                  # dark patch INSIDE the ROI, echo (100) below it
        im[13:21, 8:32] = 3
    elif kind == "shadow":                # dark from mid-ROI to the frame bottom — nothing deeper
        im[13:60, 8:32] = 3
    return im


def test_shadow_fraction_separates_tissue_from_shadow():
    m = _mask()
    assert crs.shadow_fraction(m, _img("tissue")) < 0.02   # echo beneath -> not shadow
    assert crs.shadow_fraction(m, _img("shadow")) > 0.5     # dark to depth -> shadow


def test_bright_frame_has_no_shadow():
    assert crs.shadow_fraction(_mask(), _img("bright")) == 0.0


def test_clean_frame_outranks_shadow():
    m = _mask()
    assert (crs.center_rf_frame_score(m, _img("tissue"), sweep_rf_areas=[500])
            > crs.center_rf_frame_score(m, _img("shadow"), sweep_rf_areas=[500]))


def test_top_texture_frames_prefers_least_shadow():
    m = _mask()
    masks = {"tissue": m, "shadow": m}
    finals = {"tissue": _img("tissue"), "shadow": _img("shadow")}
    assert crs.top_texture_frames(masks, finals, k=2)[0] == "tissue"


def test_real_dark_tissue_is_not_penalised():
    # a fully-dark-but-recovering ROI must score like a clean frame, not a shadow
    m = _mask()
    assert crs.shadow_fraction(m, _img("tissue")) == crs.shadow_fraction(m, _img("bright"))


def test_no_rf_body_scores_zero():
    empty = np.zeros((H, W), np.uint8)
    assert crs.center_rf_frame_score(empty, _img("bright"), sweep_rf_areas=[0]) == 0.0
    assert crs.top_texture_frames({"e": empty}, {"e": _img("bright")}) == []


def test_missing_image_scores_zero():
    assert crs.score_center_rf_cine({"a": _mask()}, {})["a"] == 0.0

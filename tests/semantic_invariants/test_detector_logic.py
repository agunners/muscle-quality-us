"""Detector-level invariants on synthetic masks.

No model load. Build a mask that exercises the geometry / coverage the
detector reads, run the detector directly, assert it does (or doesn't)
fire. The synthetic frames here are the smallest cases that pin down the
semantic correctness of the wrong_site / edge_clip_lateral logic.
"""
from __future__ import annotations

import numpy as np
import pytest

from labeling_app.measurements import LABEL_RF, LABEL_VI, LABEL_VL, LABEL_VM
from labeling_app.image_quality import compute_image_quality_flags


def _frame_with_rf_column_coverage(coverage_fraction: float,
                                    *, h: int = 128, w: int = 256) -> np.ndarray:
    """Mask reproducing the screenshot § 4 / § 5 cohort case: a wide RF band
    (`coverage_fraction` of columns) sitting above a thick VI layer, with
    VL + VM filling the lateral edges (the muscles the AI sees in a
    sweep frame that is NOT centred on RF). The composition makes
    rf_fraction = RF/(RF+VI+VL+VM) small (~0.3) even though RF spans
    most of the image — exactly the case the rf_fraction-only detector
    misfires on and the half-of-columns rule resolves."""
    m = np.zeros((h, w), dtype=np.uint8)
    n_cols = int(round(w * coverage_fraction))
    start = (w - n_cols) // 2
    # RF: a narrow band spanning most columns (the cohort frame shape).
    m[30:50, start:start + n_cols] = LABEL_RF
    # VI: deeper, thicker than RF (realistic vasti depth proportion).
    m[50:90, start:start + n_cols] = LABEL_VI
    # VL / VM: at the lateral edges, filling the OTHER 1 - coverage_fraction.
    if start > 0:
        m[30:90, :start] = LABEL_VL
    if start + n_cols < w:
        m[30:90, start + n_cols:] = LABEL_VM
    return m


# ---------------------------------------------------------------------------
# wrong_site — column-coverage rule (Akadir 2026-05-24 § 4)
# ---------------------------------------------------------------------------
# The half-of-columns rule the user converged on:
#   "as long as we have RF pixels (for the largest component) in half of
#    all the columns (as mentioned after post-process for largest component
#    + fillpoly), this is a valid frame"
#
# So wrong_site should NOT fire when rf_column_coverage >= 0.5, regardless
# of whether it's a side-shot or a centred frame. The rf_fraction-only
# detector misses this — it fires on 80 % of the cohort because side-shots
# of RF without VL/VM neighbours score low rf_fraction.

def test_wrong_site_silent_on_wide_rf_coverage() -> None:
    """A frame with RF spanning 85 % of columns (≥ 50 % rule) must not
    fire wrong_site — that frame has meaningful RF, whether centred or
    side-shot."""
    img = np.full((128, 256), 120, dtype=np.uint8)
    mask = _frame_with_rf_column_coverage(0.85)
    out = compute_image_quality_flags(img, mask)
    flags = set(out.get("flags") or ())
    assert "wrong_site" not in flags, (
        f"wrong_site fired despite ~85% RF column coverage — the half-of-"
        f"columns rule says this frame is valid. flags={flags}")


def test_wrong_site_fires_on_no_rf() -> None:
    """A frame with NO RF at all must fire wrong_site — this is the real
    'wrong muscle' case the detector is supposed to catch."""
    img = np.full((128, 256), 120, dtype=np.uint8)
    mask = np.zeros((128, 256), dtype=np.uint8)
    mask[30:70, 30:200] = LABEL_VL    # only VL — no RF in sight
    mask[70:90, 30:200] = LABEL_VM
    out = compute_image_quality_flags(img, mask)
    flags = set(out.get("flags") or ())
    assert "wrong_site" in flags, (
        f"wrong_site silent on a no-RF frame — that's the case the "
        f"detector exists for. flags={flags}")


# ---------------------------------------------------------------------------
# femur detection — sweep-sibling consistency (Akadir 2026-05-25 § 4b)
# ---------------------------------------------------------------------------
# Akadir's click-through reported 5 thumbnails all labelled `151033` in
# AGZ855 where the femur dot/tint fired on 3 frames and not on the other 2,
# even though the visible femur structure was the same across all 5. The
# dot/tint are now unified to a single source of truth (commit 246e2ec —
# both drive from `_dominant_femur_mask`), so the local helper cannot be
# the source of the divergence. The inconsistency lives in the SEGMENTER,
# which produces different femur-class assignments on visually-similar
# sweep-sibling frames.
#
# Two invariants below:
#   - LOCAL: `_dominant_femur_mask` is robust to small label-noise
#     perturbations far from the dominant component (PASSES — this is a
#     regression guard, not a forward-looking xfail).
#   - UPSTREAM: the SEGMENTER itself must yield consistent femur masks
#     across sweep-sibling frames (XFAIL — surfaces the Build-Step 2b
#     calibration target in the test runner output until the cohort-data
#     load infrastructure for semantic_invariants exists; the fix lives in
#     the femur classifier, not in any post-processing helper).

def test_dominant_femur_mask_robust_to_subthreshold_label_noise() -> None:
    """Given a femur component well above QMT_FEMUR_MIN_AREA_PX, the helper
    must not flip its detection outcome on label noise added FAR from the
    dominant component. The dot/tint unify (commit 246e2ec) made this
    helper the single source of truth for both signals — the helper itself
    must therefore be deterministic with respect to noise outside the
    dominant region.
    """
    from labeling_app.measurements import _dominant_femur_mask, LABEL_FEMUR

    base = np.zeros((100, 200), dtype=np.uint8)
    base[80:90, 50:150] = LABEL_FEMUR     # 10 × 100 = 1000 px, well above 5
    out_base = _dominant_femur_mask(base)
    assert out_base.any(), "baseline femur not detected"

    # Add a stray VL pixel far from the femur — must not perturb femur det.
    perturbed = base.copy()
    perturbed[10, 10] = LABEL_VL
    out_perturbed = _dominant_femur_mask(perturbed)
    assert np.array_equal(out_base, out_perturbed), (
        "_dominant_femur_mask flipped on a label-noise perturbation far "
        "from the dominant femur component — the dot/tint unify (commit "
        "246e2ec) relies on this helper being noise-stable")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Build-Step 2b calibration target — Akadir 2026-05-25 § 4b. The "
        "AGZ855 sweep contains 5 frames with frame_base prefix `151033` "
        "(canonical exemplar — _short rendered as 151033.100 .. 151033.660 "
        "after Item 4a). The visible femur structure is the same in all 5; "
        "the segmenter detects femur on 3 and misses it on 2. The fix is "
        "in the segmenter's femur classifier, not in any downstream helper. "
        "This xfail surfaces the finding in pytest output until the cohort-"
        "data-loading test infrastructure exists; when 2b lands, the "
        "synthetic placeholder below should be replaced with a real test "
        "that loads the 5 canonical masks and asserts _dominant_femur_mask "
        "produces a consistent outcome across them."
    ),
)
def test_segmenter_femur_consistent_on_sweep_siblings() -> None:
    """xfail-marked placeholder — see module-level comment + xfail reason."""
    pytest.fail(
        "Cohort-data-load infrastructure for tests/semantic_invariants/ "
        "is not yet wired. The finding is documented in the xfail reason "
        "and will be implemented as a real test alongside the Build-Step "
        "2b femur-classifier calibration."
    )

"""RF-texture region refine (Akadir 2026-07-14) — the no-canvas depth-band + erosion refine.
Deterministic: the region is a pure function of (mask, erosion_px, band), so a session's int_cv can be
re-derived exactly from the captured numbers."""
import numpy as np

from preflight_helpers.texture_refine import (DEFAULT_EROSION_PX, is_refined, refined_int_cv,
                                              vetted_rf_region)


def _rf_mask(h=100, w=120):
    m = np.zeros((h, w), dtype=np.uint8)
    m[20:80, 20:100] = 1                       # LABEL_RF rectangle (60 tall × 80 wide)
    return m


def test_more_erosion_shrinks_the_region():
    m = _rf_mask()
    base = int(vetted_rf_region(m, erosion_px=3).sum())
    more = int(vetted_rf_region(m, erosion_px=9).sum())
    assert 0 < more < base                     # more erosion -> strictly smaller clean interior


def test_depth_band_keeps_only_a_superficial_section():
    m = _rf_mask()
    full = int(vetted_rf_region(m, band=(0.0, 1.0)).sum())
    reg = vetted_rf_region(m, band=(0.0, 0.5))  # keep the top (superficial) half, trim the deep edge
    assert 0 < int(reg.sum()) < full
    rows = np.where(reg.any(axis=1))[0]
    assert rows.max() < 60                      # nothing kept in the deep half (rows >= 60)


def test_default_is_the_unchanged_3px_full_band():
    assert not is_refined(DEFAULT_EROSION_PX, (0.0, 1.0))     # 3px full = the kept default, NOT a refine
    assert is_refined(9, (0.0, 1.0)) and is_refined(3, (0.0, 0.7))


def test_refined_int_cv_changes_with_the_region_never_faked():
    m = _rf_mask()
    grad = np.tile(np.linspace(0, 255, 100).astype(np.uint8)[:, None], (1, 120))  # vertical gradient
    masks, finals = {"f": m}, {"f": grad}
    full = refined_int_cv(masks, finals, band=(0.0, 1.0))
    top = refined_int_cv(masks, finals, band=(0.0, 0.4))
    assert full is not None and top is not None
    assert abs(full - top) > 1e-6              # a different section reads different texture — real, not copied


def test_no_usable_region_returns_none_never_invented():
    z = np.zeros((100, 120), dtype=np.uint8)
    assert refined_int_cv({"f": z}, {"f": z}) is None     # nothing to read -> None, never a faked value

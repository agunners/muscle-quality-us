"""Tests for measurements.py — all synthetic masks, no file I/O."""
import math

import pytest
import numpy as np

from labeling_app.measurements import (
    LABEL_RF, LABEL_VI, LABEL_FASCIA, LABEL_FEMUR, LABEL_FAT,
    rf_thickness_px,
    vi_thickness_px,
    compound_thickness_px,
    get_qmt_annotation_columns,
    find_femur_plateau_columns,
    qmt_muscle_portions_px,
    _select_qmt_column,
    px_to_mm,
    area_mm2,
    echo_intensity,
    label_area_px,
    muscle_fat_ratio,
    frame_rf_consistency,
    mask_coverage_pct,
    compute_all_metrics,
    _boundary_slope,
    _central_femur_zone_columns,
    _median_smooth_boundary_rows,
    IMAGE_QUALITY_FLAG_PRIORITY,
    CONTRACT_VERSION,
    canonical_image_quality_flag_order,
)


# ---------------------------------------------------------------------------
# Mask factory helper
# ---------------------------------------------------------------------------

def make_mask(h: int = 20, w: int = 20, blocks=None) -> np.ndarray:
    """Create a synthetic uint8 mask.

    blocks: list of (label, row_start, row_end, col_start, col_end)
            row_end and col_end are exclusive (like numpy slicing).
    """
    mask = np.zeros((h, w), dtype=np.uint8)
    if blocks:
        for label, r0, r1, c0, c1 in blocks:
            mask[r0:r1, c0:c1] = label
    return mask


# ---------------------------------------------------------------------------
# RF image-quality QA taxonomy (Build-Step 1 — plan § 5.1 / § 12.3)
# ---------------------------------------------------------------------------

class TestImageQualityTaxonomy:

    def test_priority_tuple_shape(self):
        # 22 failure modes (plan § 4.2 / § 5.1), all distinct, all non-empty.
        assert isinstance(IMAGE_QUALITY_FLAG_PRIORITY, tuple)
        assert len(IMAGE_QUALITY_FLAG_PRIORITY) == 22
        assert len(set(IMAGE_QUALITY_FLAG_PRIORITY)) == 22
        assert all(isinstance(c, str) and c for c in IMAGE_QUALITY_FLAG_PRIORITY)

    def test_priority_order_endpoints(self):
        # wrong_site is the orthogonal top-priority flag; probe_orientation_
        # flipped is the named-deferred tail (plan § 5.1).
        assert IMAGE_QUALITY_FLAG_PRIORITY[0] == "wrong_site"
        assert IMAGE_QUALITY_FLAG_PRIORITY[-1] == "probe_orientation_flipped"

    def test_contract_version(self):
        assert CONTRACT_VERSION == "v0.2"

    def test_flag_order_canonicalises_regardless_of_input_order(self):
        # The operator may click checkboxes in any order; the persisted set is
        # always priority-ordered.
        out = canonical_image_quality_flag_order(
            {"motion_blur", "wrong_site", "edge_clip_lateral"})
        assert out == ("wrong_site", "edge_clip_lateral", "motion_blur")
        assert canonical_image_quality_flag_order(out) == out  # idempotent

    def test_flag_order_accepts_list_set_none(self):
        assert canonical_image_quality_flag_order(None) == ()
        assert canonical_image_quality_flag_order([]) == ()
        assert canonical_image_quality_flag_order(set()) == ()
        assert canonical_image_quality_flag_order(["motion_blur"]) == ("motion_blur",)

    def test_flag_order_drops_unknown_codes(self):
        # A free-text reason not yet in the taxonomy is carried in
        # drop_reason_other — the order helper drops it, never invents a slot.
        out = canonical_image_quality_flag_order(
            {"motion_blur", "not_a_real_code", "wrong_site"})
        assert out == ("wrong_site", "motion_blur")


# ---------------------------------------------------------------------------
# RF / VI thickness
# ---------------------------------------------------------------------------

class TestRfThickness:

    def test_simple(self):
        m = make_mask(20, 20, [(LABEL_RF, 5, 10, 3, 4)])  # span = 5 (rows 5-9)
        assert rf_thickness_px(m) == 5

    def test_max_across_columns(self):
        m = make_mask(20, 20, [
            (LABEL_RF, 0, 3, 0, 1),   # col 0: span = 3
            (LABEL_RF, 0, 7, 5, 6),   # col 5: span = 7
        ])
        assert rf_thickness_px(m) == 7

    def test_absent_returns_none(self):
        m = make_mask(20, 20)  # all zeros
        assert rf_thickness_px(m) is None


class TestViThickness:

    def test_simple(self):
        m = make_mask(20, 20, [(LABEL_VI, 12, 17, 3, 4)])  # span = 5
        assert vi_thickness_px(m) == 5

    def test_absent_returns_none(self):
        m = make_mask(20, 20)
        assert vi_thickness_px(m) is None


# ---------------------------------------------------------------------------
# Compound QMT
# ---------------------------------------------------------------------------

class TestCompoundThickness:

    def _standard_mask(self, w=20):
        """
        Row layout (col 10, center femur col):
          3  fascia
          4-7  RF (span 4)
          8-12  VI (span 5)
          14-16  femur
        Path from fascia(3) to femur_top(14) contains only 0,1,2,5 → valid
        Expected thickness = 14 - (3+1) = 10  (excludes fascia row, starts at first muscle pixel)
        """
        m = make_mask(20, w, [
            (LABEL_FASCIA, 3, 4, 8, 12),   # fascia row 3, cols 8-11
            (LABEL_RF, 4, 8, 8, 12),        # RF rows 4-7
            (LABEL_VI, 8, 13, 8, 12),       # VI rows 8-12
            (LABEL_FEMUR, 14, 16, 8, 12),   # femur rows 14-15
        ])
        return m

    def test_standard_qmt(self):
        m = self._standard_mask()
        thickness, prov = compound_thickness_px(m)
        assert prov == "standard_qmt"
        assert thickness == 10  # femur_top(14) - (fascia_bottom(3)+1) = muscle only

    def test_femur_center_column_selection(self):
        """Femur in cols 8-12 → center_col = 10; measure succeeds at col 10."""
        m = self._standard_mask()
        thickness, prov = compound_thickness_px(m)
        assert prov == "standard_qmt"
        assert thickness is not None

    def test_invalid_path_triggers_fallback(self):
        """
        Place a contaminating label (3 = VM) in the QMT path at center column.
        With contamination > 2%, column is invalid; should still find valid column
        nearby or fall back to one of the proxy tiers.
        """
        m = make_mask(20, 20, [
            (LABEL_FASCIA, 3, 4, 10, 11),
            (LABEL_RF, 4, 8, 10, 11),
            (3, 8, 12, 10, 11),             # VM: contaminant across most of path
            (LABEL_VI, 12, 14, 10, 11),
            (LABEL_FEMUR, 15, 17, 10, 11),
        ])
        thickness, prov = compound_thickness_px(m)
        assert prov in ("standard_qmt", "femur_supported_proxy", "no_femur_proxy", "no_measurement")

    def test_no_femur_fallback(self):
        """No femur label → no_femur_proxy."""
        m = make_mask(20, 20, [
            (LABEL_RF, 3, 8, 10, 11),
            (LABEL_VI, 8, 13, 10, 11),
        ])
        thickness, prov = compound_thickness_px(m)
        assert prov == "no_femur_proxy"
        assert thickness is not None
        assert thickness > 0

    def test_no_femur_no_rf_vi(self):
        m = make_mask(20, 20)  # all zeros
        thickness, prov = compound_thickness_px(m)
        assert prov == "no_measurement"
        assert thickness is None

    def test_qmt_annotation_columns_returns_list(self):
        m = self._standard_mask()
        cols = get_qmt_annotation_columns(m)
        assert isinstance(cols, list)
        assert len(cols) >= 1

    def test_qmt_annotation_fallback_center(self):
        """No measurable RF/VI or femur path → no annotation columns."""
        m = make_mask(20, 20)
        cols = get_qmt_annotation_columns(m)
        assert cols == []


# ---------------------------------------------------------------------------
# Physical unit conversions
# ---------------------------------------------------------------------------

class TestPxToMm:

    def test_standard(self):
        # 50px, spacing_y=0.13486072, dcm_rows=585, mask_height=512
        result = px_to_mm(50, 0.13486072, 585, 512)
        expected = 50 * (0.13486072 * 585 / 512)
        assert result == pytest.approx(expected, rel=1e-4)

    def test_none_spacing(self):
        assert px_to_mm(50, None, 585, 512) is None

    def test_none_px(self):
        assert px_to_mm(None, 0.135, 585, 512) is None

    def test_zero_mask_height(self):
        assert px_to_mm(50, 0.135, 585, 0) is None


class TestAreaMm2:

    def test_formula(self):
        # Real example from methodology doc:
        # spacing=0.13486072, dcm_rows=585, jpg=512x249, mask=512x256
        # dcm_cols_usg = 249 * 585 / 512 = 284.50
        # mm_per_x = 0.13486072 * 284.50 / 256 = 0.1498
        # mm_per_y = 0.13486072 * 585 / 512 = 0.1541
        # area_mm2(1000) ≈ 1000 * 0.1498 * 0.1541
        result = area_mm2(
            1000,
            pixel_spacing_x=0.13486072,
            pixel_spacing_y=0.13486072,
            dcm_rows=585,
            jpg_height=512,
            jpg_width=249,
            mask_height=512,
            mask_width=256,
        )
        assert result is not None
        # Loose check: should be roughly 23 mm² for 1000 pixels
        assert 15.0 < result < 35.0

    def test_none_spacing(self):
        result = area_mm2(100, None, 0.135, 585, 512, 249, 512, 256)
        assert result is None


# ---------------------------------------------------------------------------
# Echo intensity
# ---------------------------------------------------------------------------

class TestEchoIntensity:

    def test_rf_uniform_image(self):
        img = np.full((10, 10), 100, dtype=np.uint8)
        mask = make_mask(10, 10, [(LABEL_RF, 2, 7, 2, 7)])
        result = echo_intensity(img, mask, LABEL_RF)
        assert result == pytest.approx(100.0)

    def test_label_absent_returns_none(self):
        img = np.full((10, 10), 50, dtype=np.uint8)
        mask = make_mask(10, 10)  # all zeros
        assert echo_intensity(img, mask, LABEL_RF) is None


# ---------------------------------------------------------------------------
# Muscle/fat ratio
# ---------------------------------------------------------------------------

class TestMuscleFatRatio:

    def test_standard(self):
        # RF: 20px, VI: 15px, fat: 35px → (20+15) / 35 = 1.0
        m = make_mask(20, 20, [
            (LABEL_RF, 0, 4, 0, 5),    # 4*5 = 20px
            (LABEL_VI, 4, 7, 0, 5),    # 3*5 = 15px
            (LABEL_FAT, 10, 17, 0, 5), # 7*5 = 35px
        ])
        ratio = muscle_fat_ratio(m)
        assert ratio == pytest.approx(1.0, abs=0.01)

    def test_no_fat_no_div_by_zero(self):
        m = make_mask(20, 20, [(LABEL_RF, 0, 5, 0, 5)])
        # fat = 0 → denominator = max(0, 1) = 1 → no ZeroDivisionError
        ratio = muscle_fat_ratio(m)
        assert ratio is not None
        assert ratio == 25.0  # 25 / 1

    def test_no_muscle_returns_none(self):
        m = make_mask(20, 20)
        assert muscle_fat_ratio(m) is None


# ---------------------------------------------------------------------------
# Frame RF consistency
# ---------------------------------------------------------------------------

class TestFrameRfConsistency:

    def test_three_frames(self):
        # RF thickness: 10, 12, 14 → std dev (ddof=1) ≈ 2.0
        masks = []
        for span in [10, 12, 14]:
            m = np.zeros((20, 20), dtype=np.uint8)
            m[0:span, 10] = LABEL_RF
            masks.append(m)
        result = frame_rf_consistency(masks)
        assert result == pytest.approx(2.0, abs=0.01)

    def test_one_valid_returns_none(self):
        m_rf = np.zeros((20, 20), dtype=np.uint8)
        m_rf[0:5, 10] = LABEL_RF
        m_empty = np.zeros((20, 20), dtype=np.uint8)
        result = frame_rf_consistency([m_rf, m_empty])
        # Only one valid → None
        assert result is None


# ---------------------------------------------------------------------------
# Mask coverage
# ---------------------------------------------------------------------------

class TestMaskCoverage:

    def test_half_coverage(self):
        m = np.zeros((10, 10), dtype=np.uint8)
        m[0:5, :] = 1  # 50 of 100 pixels positive
        assert mask_coverage_pct(m) == pytest.approx(50.0)

    def test_all_background(self):
        m = np.zeros((10, 10), dtype=np.uint8)
        assert mask_coverage_pct(m) == 0.0


# ---------------------------------------------------------------------------
# compute_all_metrics omnibus
# ---------------------------------------------------------------------------

class TestComputeAllMetrics:

    def _spacing(self):
        return {
            "pixel_spacing_x": 0.13486072,
            "pixel_spacing_y": 0.13486072,
            "dcm_rows": 585.0,
            "jpg_height": 512.0,
            "jpg_width": 249.0,
            "mask_height": 512.0,
            "mask_width": 256.0,
            "bmi": 25.6,
        }

    def test_complete_keys(self):
        center_mask = make_mask(20, 20, [
            (LABEL_FASCIA, 2, 3, 8, 12),
            (LABEL_RF, 3, 7, 8, 12),
            (LABEL_VI, 7, 11, 8, 12),
            (LABEL_FEMUR, 13, 15, 8, 12),
        ])
        center_img = np.full((20, 20), 80, dtype=np.uint8)
        result = compute_all_metrics(center_mask, center_img, [center_mask], self._spacing())

        required_keys = [
            "rf_thickness_px", "rf_thickness_mm",
            "vi_thickness_px", "vi_thickness_mm",
            "qmt_px", "qmt_mm", "qmt_provenance",
            "qmt_rf_px", "qmt_rf_mm", "qmt_vi_px", "qmt_vi_mm",
            "qmt_muscle_occupancy_pct", "qmt_plateau_supported",
            "qmt_central_zone_supported", "qmt_femur_slope_abs",
            "qmt_fascia_slope_abs", "qmt_validity_flags",
            "qmt_guidance_status",
            "star_ratio", "rf_echo", "vi_echo",
            "visible_rf_area_px", "visible_rf_area_mm2", "vi_area_px", "vi_area_mm2",
            "muscle_fat_ratio", "frame_rf_consistency_px", "mask_coverage_pct",
        ]
        for key in required_keys:
            assert key in result, f"Missing key: {key}"

    def test_no_spacing_mm_fields_none(self):
        center_mask = make_mask(20, 20, [(LABEL_RF, 3, 8, 8, 12)])
        spacing = {k: None for k in self._spacing()}
        spacing["bmi"] = None
        result = compute_all_metrics(center_mask, None, [center_mask], spacing)
        # mm fields should be None when spacing is absent
        assert result["rf_thickness_mm"] is None
        assert result["qmt_mm"] is None

    def test_proxy_star_for_fallback(self):
        """Fallback/proxy QMT still gets a proxy STAR when BMI is available."""
        # Mask with RF and VI but no femur → fallback
        center_mask = make_mask(20, 20, [
            (LABEL_RF, 3, 8, 10, 11),
            (LABEL_VI, 8, 13, 10, 11),
        ])
        result = compute_all_metrics(center_mask, None, [center_mask], self._spacing())
        assert result["qmt_provenance"] == "no_femur_proxy"
        assert result["star_ratio"] is not None
        assert result["star_ratio"] > 0
        assert result["qmt_guidance_status"] == "proxy_due_to_missing_femur"

    def test_star_for_standard_qmt(self):
        """STAR = qmt_mm / bmi must be computed for standard QMT."""
        center_mask = make_mask(20, 20, [
            (LABEL_FASCIA, 2, 3, 8, 12),
            (LABEL_RF, 3, 7, 8, 12),
            (LABEL_VI, 7, 11, 8, 12),
            (LABEL_FEMUR, 13, 15, 8, 12),
        ])
        spacing = self._spacing()
        spacing["bmi"] = 25.6
        result = compute_all_metrics(center_mask, None, [center_mask], spacing)
        if result["qmt_provenance"] == "standard_qmt" and result["qmt_mm"] is not None:
            expected_star = result["qmt_mm"] / 25.6
            assert result["star_ratio"] == pytest.approx(expected_star, rel=1e-3)

    def test_measurement_columns_are_recorded(self):
        center_mask = make_mask(20, 20, [
            (LABEL_FASCIA, 2, 3, 8, 12),
            (LABEL_RF, 3, 7, 8, 12),
            (LABEL_VI, 7, 11, 8, 12),
            (LABEL_FEMUR, 13, 15, 8, 12),
        ])
        result = compute_all_metrics(center_mask, None, [center_mask], self._spacing())
        assert result["rf_column"] is not None
        assert result["vi_column"] is not None
        assert result["qmt_column"] is not None
        assert result["qmt_annotation_columns"][0] == result["qmt_column"]


# ---------------------------------------------------------------------------
# Femur plateau
# ---------------------------------------------------------------------------

class TestFemurPlateau:

    def test_flat_femur_returns_all_cols(self):
        """Flat femur (all same row) → all femur columns in plateau."""
        m = make_mask(20, 20, [(LABEL_FEMUR, 15, 17, 5, 15)])  # cols 5-14
        plateau = find_femur_plateau_columns(m, slope_pct=5.0)
        assert plateau is not None
        assert set(plateau.keys()) == set(range(5, 15))

    def test_curved_femur_restricts_plateau(self):
        """Femur that curves deep on sides → plateau excludes steep columns."""
        m = np.zeros((30, 20), dtype=np.uint8)
        # Flat section cols 5-14 at row 15; curved col 4 at row 22; col 3 at row 25
        for col in range(5, 15):
            m[15, col] = LABEL_FEMUR
        m[22, 4] = LABEL_FEMUR
        m[25, 3] = LABEL_FEMUR
        # col_ref ≈ 9, min_row = 15
        # col 4 (5 from ref): allowed = 15 + 5*0.05 = 15.25 → row 22 excluded ✓
        # col 3 (6 from ref): allowed = 15 + 6*0.05 = 15.30 → row 25 excluded ✓
        plateau = find_femur_plateau_columns(m, slope_pct=5.0)
        assert plateau is not None
        assert 3 not in plateau
        assert 4 not in plateau
        assert all(col in plateau for col in range(5, 15))

    def test_no_femur_returns_none(self):
        m = make_mask(20, 20)
        assert find_femur_plateau_columns(m) is None

    def test_plateau_row_values(self):
        """Plateau dict values must equal topmost femur row for each column."""
        m = make_mask(20, 20, [(LABEL_FEMUR, 14, 16, 8, 12)])
        plateau = find_femur_plateau_columns(m)
        assert plateau is not None
        for col, row in plateau.items():
            assert row == 14

    def test_slope_excludes_steep_sides(self):
        """Columns where local rise-per-column exceeds 5% are excluded."""
        m = np.zeros((40, 30), dtype=np.uint8)
        # Flat centre cols 10-19 at row 10
        for col in range(10, 20):
            m[10, col] = LABEL_FEMUR
        # Multi-column side dive should stay excluded even with smoothing.
        m[14, 7] = LABEL_FEMUR
        m[13, 8] = LABEL_FEMUR
        m[12, 9] = LABEL_FEMUR
        plateau = find_femur_plateau_columns(m, slope_pct=5.0)
        assert plateau is not None
        assert 9 not in plateau
        assert 8 not in plateau
        assert 7 not in plateau
        assert all(col in plateau for col in range(10, 20))

    def test_smoothing_ignores_single_jagged_minimum_pixel(self):
        # The jag at col 14 used to be at row 8 (two rows above the row-10
        # band); that put it 8-disconnected from the band, which is now
        # correctly filtered as noise by `_dominant_femur_mask`. Keep the
        # test's intent — smoothing handles a single jagged pixel — by
        # placing the jag at row 9 instead (one row above the band, so the
        # diagonal 8-neighbour to (10, 13) and (10, 15) keeps everything as
        # one connected femur).
        m = np.zeros((40, 30), dtype=np.uint8)
        for col in range(10, 20):
            row = 10 if col != 14 else 9
            m[row, col] = LABEL_FEMUR
        plateau = find_femur_plateau_columns(m)
        assert plateau is not None
        assert set(plateau.keys()) == set(range(10, 20))


class TestCentralQmtGeometry:

    def test_central_femur_zone_expands_plateau_by_margin(self):
        # Wings were at row 21 (6 rows below the plateau at row 15); that
        # 6-row gap makes wings 8-disconnected from the plateau, and
        # `_dominant_femur_mask` correctly treats them as separate
        # components. Keep the test's intent — zone expands plateau by
        # margin even into cols where femur does exist — by placing wings
        # at row 16 (one row below the plateau, so the diagonal 8-neighbour
        # at (15, 20)↔(16, 19) keeps the femur one connected component).
        m = np.zeros((30, 60), dtype=np.uint8)
        for col in range(15, 36):
            row = 15 if 20 <= col <= 30 else 16
            m[row, col] = LABEL_FEMUR
        zone = _central_femur_zone_columns(m, margin_px=5)
        assert zone[0] == 15
        assert zone[-1] == 35

    def test_boundary_slope_returns_fitted_row_per_column(self):
        edge = {10: 10, 11: 11, 12: 12, 13: 13, 14: 14}
        slope = _boundary_slope(edge, 12, half_win=2)
        assert slope == pytest.approx(1.0, abs=1e-6)

    def test_median_smoothing_reduces_single_spike(self):
        smoothed = _median_smooth_boundary_rows({10: 10, 11: 10, 12: 6, 13: 10, 14: 10}, window=5)
        assert smoothed[12] == 10


# ---------------------------------------------------------------------------
# VI thickness with femur plateau
# ---------------------------------------------------------------------------

class TestViThicknessPlateau:

    def test_plateau_clamps_lower_border(self):
        """VI extending below femur top should be clamped to femur_top - 1."""
        m = make_mask(30, 20, [
            (LABEL_VI, 5, 20, 8, 12),      # VI rows 5-19 at cols 8-11
            (LABEL_FEMUR, 15, 18, 8, 12),  # femur rows 15-17
        ])
        # Without plateau clamping, extent would be 19-5+1=15
        # With plateau (femur_top=15), vi_bot = min(19, 14) = 14, extent = 14-5+1 = 10
        result = vi_thickness_px(m)
        assert result == 10

    def test_no_femur_uses_full_extent(self):
        """Without femur, falls back to full VI extent."""
        m = make_mask(20, 20, [(LABEL_VI, 5, 15, 8, 12)])
        result = vi_thickness_px(m)
        assert result == 10  # 14-5+1 = 10


# ---------------------------------------------------------------------------
# QMT muscle portions
# ---------------------------------------------------------------------------

class TestQmtMusclePotions:

    def _qmt_mask(self):
        """Standard mask with 4 RF pixels and 5 VI pixels in QMT path."""
        return make_mask(20, 20, [
            (LABEL_FASCIA, 3, 4, 8, 12),
            (LABEL_RF, 4, 8, 8, 12),     # RF rows 4-7 → 4 px in path
            (LABEL_VI, 8, 13, 8, 12),    # VI rows 8-12 → 5 px in path
            (LABEL_FEMUR, 14, 16, 8, 12),
        ])

    def test_portions_present(self):
        m = self._qmt_mask()
        rf_px, vi_px = qmt_muscle_portions_px(m)
        assert rf_px == 4
        assert vi_px == 5

    def test_no_femur_returns_none(self):
        m = make_mask(20, 20, [(LABEL_RF, 3, 8, 10, 11)])
        rf_px, vi_px = qmt_muscle_portions_px(m)
        assert rf_px is None
        assert vi_px is None

    def test_portions_in_compute_all_metrics(self):
        """compute_all_metrics must include qmt_rf_px / qmt_vi_px keys."""
        m = self._qmt_mask()
        spacing = {
            "pixel_spacing_x": 0.135, "pixel_spacing_y": 0.135,
            "dcm_rows": 585.0, "jpg_height": 512.0, "jpg_width": 249.0,
            "mask_height": 512.0, "mask_width": 256.0, "bmi": 25.0,
        }
        result = compute_all_metrics(m, None, [m], spacing)
        assert result["qmt_rf_px"] == 4
        assert result["qmt_vi_px"] == 5
        assert result["qmt_rf_mm"] is not None
        assert result["qmt_vi_mm"] is not None


# ---------------------------------------------------------------------------
# _select_qmt_column: strict production selection, relaxed debug-only selection
# ---------------------------------------------------------------------------

class TestSelectQmtColumn:

    def _clean_mask(self):
        """Plateau columns have a clean QMT path (RF+VI+fascia only)."""
        return make_mask(20, 20, [
            (LABEL_FASCIA, 3, 4, 8, 12),
            (LABEL_RF, 4, 8, 8, 12),
            (LABEL_VI, 8, 13, 8, 12),
            (LABEL_FEMUR, 14, 16, 8, 12),
        ])

    def test_clean_path_strict_pass(self):
        """A clean mask is resolved on the strict pass."""
        col = _select_qmt_column(self._clean_mask())
        assert col is not None

    def test_no_femur_returns_none(self):
        m = make_mask(20, 20, [(LABEL_RF, 3, 8, 10, 11)])
        assert _select_qmt_column(m) is None

    def test_vm_contaminated_plateau_uses_non_plateau_strict(self):
        """Plateau columns are VM-contaminated (>2%) but adjacent femur cols
        outside the plateau are clean.  Strict pass should find those cols."""
        # Plateau: cols 6-10, femur at row 14, flat section
        # VM (label 3) fills rows 5-13 in cols 6-10 → contaminates plateau path
        # Col 12-14: clean femur+RF+VI path (no VM)
        m = make_mask(20, 20, [
            (LABEL_FASCIA, 3, 4, 5, 15),   # fascia row 3
            (LABEL_RF, 4, 7, 5, 15),        # RF rows 4-6
            (3, 7, 13, 6, 11),              # VM in plateau cols (>2% of path)
            (LABEL_VI, 7, 13, 11, 15),      # VI clean cols 11-14
            (LABEL_FEMUR, 14, 16, 6, 15),   # femur in cols 6-14 (plateau at 6-10)
        ])
        col = _select_qmt_column(m)
        # Should pick a non-plateau column (11-14) where path is clean
        assert col is not None
        assert col >= 11

    def test_all_cols_vm_relaxed_debug_only(self):
        """VM/VL-tolerant columns remain available only via strict=False."""
        # Only VM contaminates the path; all other labels are allowed
        m = make_mask(20, 20, [
            (LABEL_FASCIA, 3, 4, 8, 12),
            (LABEL_RF, 4, 6, 8, 12),
            (3, 6, 13, 8, 12),             # VM fills most of path (>2%)
            (LABEL_VI, 13, 14, 8, 12),
            (LABEL_FEMUR, 14, 16, 8, 12),
        ])
        assert _select_qmt_column(m) is None
        assert _select_qmt_column(m, strict=False) is not None

    def test_relaxed_only_paths_do_not_become_standard_qmt(self):
        """Production QMT provenance stays fallback when only relaxed columns exist."""
        m = make_mask(20, 20, [
            (LABEL_FASCIA, 3, 4, 8, 12),
            (LABEL_RF, 4, 6, 8, 12),
            (3, 6, 13, 8, 12),
            (LABEL_VI, 13, 14, 8, 12),
            (LABEL_FEMUR, 14, 16, 8, 12),
        ])
        thickness, prov = compound_thickness_px(m)
        assert prov == "femur_supported_proxy"
        assert thickness is not None

    def test_femur_present_fallback_stays_femur_anchored(self):
        """When femur exists, fallback stays on the central femur-supported proxy."""
        m = make_mask(60, 50, [
            (LABEL_FASCIA, 5, 6, 18, 23),
            (LABEL_RF, 6, 16, 18, 23),
            (3, 16, 26, 18, 23),
            (LABEL_VI, 26, 36, 18, 23),
            (LABEL_FEMUR, 37, 40, 18, 23),
            (LABEL_RF, 8, 22, 35, 36),
            (LABEL_VI, 22, 48, 35, 36),
        ])
        metrics = compute_all_metrics(m, None, [m], {
            "pixel_spacing_x": None,
            "pixel_spacing_y": None,
            "dcm_rows": None,
            "jpg_height": None,
            "jpg_width": None,
            "mask_height": None,
            "mask_width": None,
            "bmi": None,
        })
        assert metrics["qmt_provenance"] == "femur_supported_proxy"
        assert metrics["qmt_column"] == 20
        assert metrics["qmt_guidance_status"] == "proxy_due_to_geometry"

    def test_select_and_compound_agree_on_column(self):
        """compound_thickness_px must use the same column as _select_qmt_column."""
        m = self._clean_mask()
        selected = _select_qmt_column(m)
        thickness, prov = compound_thickness_px(m)
        assert prov == "standard_qmt"
        assert thickness is not None

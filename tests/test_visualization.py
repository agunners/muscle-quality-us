"""Tests for visualization.py."""
import json
import pytest
import numpy as np
from pathlib import Path
from PIL import Image

from labeling_app.visualization import (
    LAYER_CHANGE_COLOR,
    LAYER_CONFIDENCE_COLOR,
    load_label_palette,
    colorize_mask,
    compose_layer_stack,
    confidence_heatmap_overlay,
    confidence_stats,
    draw_qmt_annotations,
    load_image_gray,
    load_image_pil,
    overlay_on_image,
    pil_to_bytes,
    post_process_mask_for_display,
)
from labeling_app.measurements import (
    LABEL_RF, LABEL_VI, LABEL_FEMUR, compute_all_metrics,
)

_PALETTE_PATH = Path(__file__).parent.parent / "label_palette.json"


# ---------------------------------------------------------------------------
# Bug 2 — `draw_qmt_annotations` must obey the QMT-tier verdict
# ---------------------------------------------------------------------------
# Operator review (Akadir, 2026-05-22 Google Doc, point 2) reported: the
# verdict text says "QMT not measurable" (a Tier-3 verdict — e.g. a proxy
# that is single-muscle in the path), but a "QMT+" caliper is drawn over the
# image. Verdict and picture contradict. Subagent A root-caused: the draw
# used a looser provenance gate than `qmt_tier`. Fix: gate on `qmt_tier`.

def _bug2_tier3_metrics() -> dict:
    """A QMT metrics dict resolving to Tier 3 — proxy + rf_or_vi_missing."""
    return {
        "qmt_provenance": "femur_supported_proxy",
        "qmt_validity_flags": ["rf_or_vi_missing_in_path"],
        "qmt_column": 50, "qmt_top_y": 20, "qmt_bottom_y": 80,
        "qmt_annotation_columns": [50],
    }


def _bug2_tier1_metrics() -> dict:
    """A QMT metrics dict resolving to Tier 1 — standard, no flags."""
    return {
        "qmt_provenance": "standard_qmt", "qmt_validity_flags": [],
        "qmt_column": 50, "qmt_top_y": 20, "qmt_bottom_y": 80,
        "qmt_annotation_columns": [50],
    }


def _bug2_frame() -> tuple[np.ndarray, Image.Image]:
    """A 100×100 mask + flat-gray RGB image — substrate for the QMT draw."""
    mask = np.zeros((100, 100), dtype=np.uint8)
    mask[20:30, 30:70] = LABEL_RF
    mask[30:60, 30:70] = LABEL_VI
    mask[80:90, 40:60] = LABEL_FEMUR
    return mask, Image.new("RGB", (100, 100), (50, 50, 50))


def test_bug2_draw_qmt_annotations_skips_caliper_for_tier3() -> None:
    """A Tier-3 frame must NOT get a QMT caliper drawn. The caliper is white
    (255,255,255); a Tier-3 output has no near-white pixels along the QMT
    column inside the would-be span."""
    mask, img = _bug2_frame()
    out = draw_qmt_annotations(img, mask, _bug2_tier3_metrics())
    arr = np.array(out)
    near_white = np.all(arr[20:80, 50] >= 250, axis=-1)
    assert not near_white.any(), (
        "Tier-3 frame got a QMT caliper drawn — verdict-vs-picture mismatch")


def test_bug2_draw_qmt_annotations_draws_caliper_for_tier1() -> None:
    """Sanity — a Tier-1 (standard QMT) frame still gets the caliper. The fix
    must not over-suppress."""
    mask, img = _bug2_frame()
    out = draw_qmt_annotations(img, mask, _bug2_tier1_metrics())
    arr = np.array(out)
    near_white = np.all(arr[20:80, 50] >= 250, axis=-1)
    assert near_white.any(), (
        "Tier-1 frame must have a QMT caliper drawn (white pixels along the column)")


# ---------------------------------------------------------------------------
# Chunk 5 — `confidence_heatmap_overlay` (the segmentation-confidence heatmap)
# ---------------------------------------------------------------------------

def test_confidence_heatmap_overlay_high_confidence_leaves_image_unchanged() -> None:
    """A region the model was sure about (confidence ≈ 1) gets no red wash —
    the original pixels come through cleanly so the underlying overlay /
    measurement lines stay legible."""
    arr = np.full((50, 50, 3), 100, dtype=np.uint8)
    confidence = np.ones((50, 50), dtype=np.float32)
    out = confidence_heatmap_overlay(arr, confidence)
    # Output equals input — alpha = (1 - 1) * max_alpha = 0 → no blend.
    np.testing.assert_array_equal(out, arr)


def test_confidence_heatmap_overlay_low_confidence_adds_red() -> None:
    """A region the model was unsure about (confidence ≈ 0) gets a red tint.
    The red channel should rise above the original; the green / blue
    channels should fall (red is blended in, the original is faded)."""
    arr = np.full((50, 50, 3), 100, dtype=np.uint8)
    confidence = np.zeros((50, 50), dtype=np.float32)        # very unsure
    out = confidence_heatmap_overlay(arr, confidence, max_alpha=0.45)
    # Red channel: 100 * (1 - 0.45) + 255 * 0.45 ≈ 55 + 115 = 170
    assert int(out[0, 0, 0]) > 150, "red channel must rise on uncertain pixels"
    # Green / blue: 100 * (1 - 0.45) + 0 * 0.45 ≈ 55
    assert int(out[0, 0, 1]) < 70
    assert int(out[0, 0, 2]) < 70


def test_confidence_heatmap_overlay_is_per_pixel() -> None:
    """The overlay is per-pixel: half the image confident, half uncertain →
    the confident half is unchanged, the uncertain half is red-tinted. The
    measurement line / contour visibility is preserved where the model was
    sure."""
    arr = np.full((20, 20, 3), 100, dtype=np.uint8)
    confidence = np.ones((20, 20), dtype=np.float32)
    confidence[:, 10:] = 0.0                                 # right half unsure
    out = confidence_heatmap_overlay(arr, confidence)
    # Left half (confident): unchanged.
    np.testing.assert_array_equal(out[:, :10], arr[:, :10])
    # Right half (unsure): red channel above original.
    assert (out[:, 10:, 0] > arr[:, 10:, 0]).all()


def test_confidence_heatmap_overlay_resizes_mismatched_confidence() -> None:
    """When the confidence map's shape differs from the image (an uncommon
    path, but defensive), it is bilinearly resized to match — no shape
    error."""
    arr = np.full((50, 50, 3), 100, dtype=np.uint8)
    confidence = np.zeros((25, 25), dtype=np.float32)        # half resolution
    out = confidence_heatmap_overlay(arr, confidence)
    assert out.shape == arr.shape
    # The (resized, all-zero) confidence -> red tint everywhere.
    assert (out[..., 0] > 150).all()


def test_confidence_heatmap_overlay_does_not_mutate_input() -> None:
    """The helper returns a new array — the caller's `arr` stays intact so
    code that draws additional decorations afterwards (e.g. the QMT
    caliper) still works against the original pixels."""
    arr = np.full((10, 10, 3), 100, dtype=np.uint8)
    snapshot = arr.copy()
    confidence_heatmap_overlay(arr, np.zeros((10, 10), dtype=np.float32))
    np.testing.assert_array_equal(arr, snapshot)


# ---------------------------------------------------------------------------
# `confidence_heatmap_overlay` — change_map second-layer uncertainty (2026-05-23)
# ---------------------------------------------------------------------------
# The display-side mask cleanup (`post_process_mask_for_display`) re-labels
# stray femur / RF pixels and fills RF holes. Those overrides are inherently
# uncertain — the AI said one thing, our rule said another — so they ride the
# heatmap as a second layer, OR'd into the AI-confidence uncertainty via
# element-wise max. A confident-but-overridden pixel still reads red.

def test_confidence_heatmap_overlay_change_map_only_paints_red() -> None:
    """All pixels fully confident; a single pixel flagged in change_map gets a
    red tint (full uncertainty at that pixel). Everywhere else is unchanged."""
    arr = np.full((10, 10, 3), 100, dtype=np.uint8)
    confidence = np.ones((10, 10), dtype=np.float32)
    change_map = np.zeros((10, 10), dtype=bool)
    change_map[5, 5] = True
    out = confidence_heatmap_overlay(arr, confidence, change_map=change_map)
    # The flagged pixel: red channel > original, green/blue < original.
    assert int(out[5, 5, 0]) > 150
    assert int(out[5, 5, 1]) < 70
    # An adjacent un-flagged pixel: unchanged.
    np.testing.assert_array_equal(out[0, 0], arr[0, 0])


def test_confidence_heatmap_overlay_change_map_combined_with_confidence() -> None:
    """The two uncertainty signals combine via element-wise max: an unsure
    pixel that was ALSO overridden stays red (1.0 already); a confident pixel
    that was overridden becomes red (was 0.0, max-up'd to 1.0); a confident
    pixel that was NOT overridden stays clean."""
    arr = np.full((10, 10, 3), 100, dtype=np.uint8)
    confidence = np.ones((10, 10), dtype=np.float32)
    confidence[0, :] = 0.0                          # row 0: AI unsure
    change_map = np.zeros((10, 10), dtype=bool)
    change_map[1, :] = True                         # row 1: post-proc override
    out = confidence_heatmap_overlay(arr, confidence, change_map=change_map)
    # Row 0 (AI unsure): red.
    assert (out[0, :, 0] > 150).all()
    # Row 1 (post-proc override on a confident pixel): also red.
    assert (out[1, :, 0] > 150).all()
    # Row 2+ (confident, not overridden): unchanged.
    np.testing.assert_array_equal(out[2:, :], arr[2:, :])


def test_confidence_heatmap_overlay_change_map_none_is_identity() -> None:
    """Passing change_map=None reproduces the previous behaviour exactly."""
    arr = np.full((20, 20, 3), 100, dtype=np.uint8)
    confidence = np.full((20, 20), 0.5, dtype=np.float32)
    no_change = confidence_heatmap_overlay(arr, confidence, change_map=None)
    default = confidence_heatmap_overlay(arr, confidence)
    np.testing.assert_array_equal(no_change, default)


# ---------------------------------------------------------------------------
# `post_process_mask_for_display` — Akadir 2026-05-23, the display-side
# mask cleanup that closes the gap between "what the measurement code saw"
# and "what the operator sees on screen." Femur: largest-component only. RF:
# largest-component AND fill internal holes. Both contribute to the change_map
# the heatmap can OR into its red wash.
# ---------------------------------------------------------------------------

def test_post_process_mask_for_display_femur_strays_dropped_to_background() -> None:
    """A stray femur pixel outside the dominant blob becomes background; the
    change_map flags it; the dominant blob is preserved."""
    mask = np.zeros((40, 40), dtype=np.uint8)
    mask[5:8, 5:25] = LABEL_FEMUR              # dominant blob (60 px)
    mask[20, 20] = LABEL_FEMUR                 # stray single pixel
    cleaned, change_map = post_process_mask_for_display(mask)
    # Dominant blob preserved.
    assert (cleaned[5:8, 5:25] == LABEL_FEMUR).all()
    # Stray dropped to background.
    assert cleaned[20, 20] == 0
    # change_map flags the stray, not the dominant blob.
    assert bool(change_map[20, 20]) is True
    assert not change_map[5:8, 5:25].any()


def test_post_process_mask_for_display_rf_strays_dropped() -> None:
    """A stray RF pixel outside the dominant RF blob becomes background; the
    dominant RF blob is preserved (no fill needed if no holes)."""
    mask = np.zeros((40, 40), dtype=np.uint8)
    mask[10:20, 10:30] = LABEL_RF              # dominant RF blob (200 px)
    mask[35, 5] = LABEL_RF                     # stray
    cleaned, change_map = post_process_mask_for_display(mask)
    assert (cleaned[10:20, 10:30] == LABEL_RF).all()
    assert cleaned[35, 5] == 0
    assert bool(change_map[35, 5]) is True


def test_post_process_mask_for_display_rf_holes_filled_as_rf() -> None:
    """A hole inside the dominant RF blob (a pixel labelled something else,
    fully surrounded by RF) gets re-labelled to RF; the change_map flags the
    filled pixel."""
    mask = np.zeros((40, 40), dtype=np.uint8)
    mask[10:20, 10:30] = LABEL_RF              # dominant RF
    # A hole at (15, 20) — currently RF; flip it to VI so it becomes a hole.
    mask[15, 20] = LABEL_VI
    cleaned, change_map = post_process_mask_for_display(mask)
    # The hole is now RF.
    assert cleaned[15, 20] == LABEL_RF
    # And flagged.
    assert bool(change_map[15, 20]) is True


def test_post_process_mask_for_display_already_clean_is_idempotent() -> None:
    """An already-clean mask (no strays, no holes, no femur) yields itself
    and an all-False change_map."""
    mask = np.zeros((40, 40), dtype=np.uint8)
    mask[10:20, 10:30] = LABEL_RF              # solid RF blob
    mask[20:25, 10:30] = LABEL_VI              # solid VI blob (untouched)
    cleaned, change_map = post_process_mask_for_display(mask)
    np.testing.assert_array_equal(cleaned, mask)
    assert not change_map.any()


def test_post_process_mask_for_display_does_not_mutate_input() -> None:
    """The helper returns a new array; the caller's `pred` stays intact for
    measurement-canonical drawing (eroded contour, ticks) on the raw mask."""
    mask = np.zeros((30, 30), dtype=np.uint8)
    mask[5:8, 5:25] = LABEL_FEMUR
    mask[20, 20] = LABEL_FEMUR                 # stray
    snapshot = mask.copy()
    post_process_mask_for_display(mask)
    np.testing.assert_array_equal(mask, snapshot)


def test_post_process_mask_for_display_femur_no_filling() -> None:
    """The femur cleanup does NOT fill holes — the cortex is a thin band, not
    a solid region. A "hole" inside femur stays whatever it was; only strays
    are dropped."""
    mask = np.zeros((40, 40), dtype=np.uint8)
    mask[10:14, 10:30] = LABEL_FEMUR           # femur band
    mask[12, 20] = LABEL_VI                    # "hole" inside femur band
    cleaned, change_map = post_process_mask_for_display(mask)
    # The "hole" stays VI — no fill.
    assert cleaned[12, 20] == LABEL_VI
    # change_map does not flag the hole (femur path doesn't fill).
    assert bool(change_map[12, 20]) is False


# ---------------------------------------------------------------------------
# `compose_layer_stack` — Akadir 2026-05-24 v2 reframe (Layer 2a + 3)
# ---------------------------------------------------------------------------
# Four toggleable layers: raw (always), segmentation overlay, AI-confidence
# wash (cyan), post-processing change-map wash (lime). Each layer is
# independent — a pixel that is both AI-unsure AND post-processing-overridden
# reads as a mix of cyan + lime (not max-blended into a single colour).

def test_compose_layer_stack_only_raw_returns_raw() -> None:
    """No segmentation, no confidence, no change-map → composed is the raw."""
    raw = np.full((20, 20, 3), 100, dtype=np.uint8)
    out = compose_layer_stack(raw, segmentation_arr=None,
                              show_segmentation=False)
    np.testing.assert_array_equal(out, raw)


def test_compose_layer_stack_segmentation_layer_replaces_raw() -> None:
    """Layer 1 ON adopts the segmentation overlay (the caller's bundled
    colour + contour + ticks) at alpha=1.0 — the caller already chose the
    palette's alpha when building it."""
    raw = np.full((20, 20, 3), 100, dtype=np.uint8)
    seg = np.full((20, 20, 3), 200, dtype=np.uint8)
    out = compose_layer_stack(raw, segmentation_arr=seg, show_segmentation=True)
    np.testing.assert_array_equal(out, seg)


def test_compose_layer_stack_confidence_paints_cyan_on_low_confidence() -> None:
    """Layer 2 with low confidence (≈ 0) paints a cyan wash. The cyan channel
    pair (G, B) rises; the red channel falls."""
    raw = np.full((10, 10, 3), 128, dtype=np.uint8)
    conf = np.zeros((10, 10), dtype=np.float32)
    out = compose_layer_stack(raw, segmentation_arr=None,
                              confidence_map=conf,
                              show_segmentation=False,
                              show_confidence=True,
                              confidence_max_alpha=0.6)
    # Red below original (no red in cyan); green + blue above original.
    assert int(out[0, 0, 0]) < int(raw[0, 0, 0])
    assert int(out[0, 0, 1]) > int(raw[0, 0, 1])
    assert int(out[0, 0, 2]) > int(raw[0, 0, 2])


def test_compose_layer_stack_change_map_paints_lime_on_changed_pixels() -> None:
    """Layer 3 paints a lime wash on changed pixels and leaves unchanged
    pixels alone."""
    raw = np.full((10, 10, 3), 128, dtype=np.uint8)
    cm = np.zeros((10, 10), dtype=bool)
    cm[5, 5] = True
    out = compose_layer_stack(raw, segmentation_arr=None,
                              change_map=cm,
                              show_segmentation=False,
                              show_change_map=True,
                              change_alpha=0.7)
    # Flagged pixel: green channel rises (lime ≈ (50, 255, 50)).
    assert int(out[5, 5, 1]) > 150
    # Adjacent pixel: unchanged.
    np.testing.assert_array_equal(out[0, 0], raw[0, 0])


def test_compose_layer_stack_layers_compose_independently_not_via_max() -> None:
    """A pixel that is both AI-unsure AND post-processing-overridden gets
    BOTH cyan AND lime layered on — not element-wise max into one colour.
    The composed pixel's green channel rises from both contributions."""
    raw = np.full((10, 10, 3), 100, dtype=np.uint8)
    conf = np.zeros((10, 10), dtype=np.float32)   # unsure everywhere
    cm = np.ones((10, 10), dtype=bool)            # changed everywhere
    only_conf = compose_layer_stack(raw, None,
                                    confidence_map=conf,
                                    show_segmentation=False,
                                    show_confidence=True,
                                    confidence_max_alpha=0.6)
    only_change = compose_layer_stack(raw, None,
                                      change_map=cm,
                                      show_segmentation=False,
                                      show_change_map=True,
                                      change_alpha=0.7)
    both = compose_layer_stack(raw, None,
                               confidence_map=conf, change_map=cm,
                               show_segmentation=False,
                               show_confidence=True,
                               show_change_map=True,
                               confidence_max_alpha=0.6,
                               change_alpha=0.7)
    # Independence proof: `both` is distinct from both single-layer
    # compositions — neither layer cancels nor dominates. Element-wise max
    # would have made `both` equal to whichever single layer wins per channel.
    assert not np.array_equal(both, only_conf)
    assert not np.array_equal(both, only_change)
    # Per-channel: the blue channel in `both` is between only_change (low B,
    # lime has B=50) and only_conf (high B, cyan has B=255) — the lime layer
    # pulled the cyan-blue down. If layers used element-wise max, only_conf's
    # blue would have stayed.
    assert int(both[0, 0, 2]) < int(only_conf[0, 0, 2])
    assert int(both[0, 0, 2]) > int(only_change[0, 0, 2])


def test_compose_layer_stack_does_not_mutate_raw() -> None:
    raw = np.full((10, 10, 3), 128, dtype=np.uint8)
    snapshot = raw.copy()
    compose_layer_stack(raw, None, confidence_map=np.zeros((10, 10), dtype=np.float32),
                        show_segmentation=False, show_confidence=True)
    np.testing.assert_array_equal(raw, snapshot)


def test_compose_layer_stack_colors_are_distinct_from_each_other() -> None:
    """LAYER_CONFIDENCE_COLOR (cyan) and LAYER_CHANGE_COLOR (lime) must be
    perceptually distinct — operator must be able to read which override
    is which when both layers are on. Quick sanity: cyan has high B, low
    R; lime has high G, low R, low B."""
    assert LAYER_CONFIDENCE_COLOR != LAYER_CHANGE_COLOR
    # Cyan: G + B high.
    assert LAYER_CONFIDENCE_COLOR[1] > 200 and LAYER_CONFIDENCE_COLOR[2] > 200
    # Lime: G high, B low.
    assert LAYER_CHANGE_COLOR[1] > 200 and LAYER_CHANGE_COLOR[2] < 100


# ---------------------------------------------------------------------------
# `confidence_stats` — interpretable metric for the heatmap layer
# ---------------------------------------------------------------------------

def test_confidence_stats_none_when_no_map() -> None:
    assert confidence_stats(None) is None


def test_confidence_stats_summarises_all_pixels_when_no_roi() -> None:
    cmap = np.full((10, 10), 0.85, dtype=np.float32)
    out = confidence_stats(cmap)
    assert abs(out["mean"] - 0.85) < 1e-5
    assert out["fraction_below_0_70"] == 0.0
    assert out["n_pixels"] == 100


def test_confidence_stats_fraction_below_threshold_is_meaningful() -> None:
    cmap = np.full((10, 10), 0.85, dtype=np.float32)
    cmap[:5] = 0.50      # half the rows below 0.70
    out = confidence_stats(cmap)
    assert abs(out["fraction_below_0_70"] - 0.5) < 1e-5


def test_confidence_stats_roi_restricts_to_masked_pixels() -> None:
    cmap = np.zeros((10, 10), dtype=np.float32)
    cmap[:5] = 1.0
    roi = np.zeros((10, 10), dtype=bool)
    roi[:5] = True       # only the confident half
    out = confidence_stats(cmap, roi_mask=roi)
    assert out["mean"] == 1.0
    assert out["n_pixels"] == 50


@pytest.fixture
def palette():
    if not _PALETTE_PATH.exists():
        pytest.skip("label_palette.json not available")
    return load_label_palette(str(_PALETTE_PATH))


@pytest.fixture
def simple_mask():
    """10×10 mask with label 1 in top half, label 2 in bottom half."""
    m = np.zeros((10, 10), dtype=np.uint8)
    m[0:5, :] = 1
    m[5:10, :] = 2
    return m


@pytest.fixture
def base_img():
    """10×10 RGB gray image."""
    return Image.fromarray(np.full((10, 10, 3), 128, dtype=np.uint8), "RGB")


class TestLoadPalette:

    def test_keys_are_ints(self, palette):
        for k in palette:
            assert isinstance(k, int)

    def test_background_transparent(self, palette):
        assert palette[0][3] == 0  # alpha = 0

    def test_all_labels_present(self, palette):
        # Must have at least 0-8
        for lid in range(9):
            assert lid in palette

    def test_rgba_values_uint8(self, palette):
        for lid, rgba in palette.items():
            for ch in rgba:
                assert 0 <= ch <= 255


class TestColorizeMask:

    def test_output_shape(self, simple_mask, palette):
        img = colorize_mask(simple_mask, palette)
        assert img.mode == "RGBA"
        assert img.size == (10, 10)  # (width, height) in PIL

    def test_background_transparent(self, palette):
        m = np.zeros((10, 10), dtype=np.uint8)  # all background
        img = colorize_mask(m, palette, bg_alpha=0)
        arr = np.array(img)
        assert (arr[:, :, 3] == 0).all()

    def test_non_background_has_color(self, simple_mask, palette):
        img = colorize_mask(simple_mask, palette)
        arr = np.array(img)
        # Label-1 region (top 5 rows) should have non-zero RGB
        top_region = arr[0:5, :, :3]
        assert top_region.sum() > 0


class TestOverlayOnImage:

    def test_output_type(self, base_img, simple_mask, palette):
        result = overlay_on_image(base_img, simple_mask, palette, alpha=0.5)
        assert isinstance(result, Image.Image)
        assert result.mode == "RGB"

    def test_output_size_matches_base(self, base_img, simple_mask, palette):
        result = overlay_on_image(base_img, simple_mask, palette, alpha=0.5)
        assert result.size == base_img.size

    def test_zero_alpha_equals_base(self, base_img, simple_mask, palette):
        """At alpha=0 the mask is invisible; output should equal base."""
        result = overlay_on_image(base_img, simple_mask, palette, alpha=0.0)
        # Allow small floating-point diff
        base_arr = np.array(base_img)
        res_arr = np.array(result)
        assert np.abs(base_arr.astype(int) - res_arr.astype(int)).max() <= 2


class TestDrawQmtAnnotations:

    def _make_annotated_mask(self):
        """Mask with fascia, RF, VI, femur for standard QMT path."""
        from labeling_app.measurements import LABEL_FASCIA, LABEL_RF, LABEL_VI, LABEL_FEMUR
        m = np.zeros((50, 50), dtype=np.uint8)
        m[5, 20:30] = LABEL_FASCIA
        m[6:15, 20:30] = LABEL_RF
        m[15:25, 20:30] = LABEL_VI
        m[28:32, 20:30] = LABEL_FEMUR
        return m

    def test_runs_without_crash(self):
        m = self._make_annotated_mask()
        base = Image.fromarray(np.full((50, 50, 3), 100, dtype=np.uint8), "RGB")
        metrics = {
            "qmt_provenance": "standard_qmt",
            "qmt_px": 23,
            "qmt_mm": 4.7,
        }
        result = draw_qmt_annotations(base, m, metrics)
        assert isinstance(result, Image.Image)
        assert result.size == base.size

    def test_no_femur_no_crash(self):
        """Proxy provenance without femur must not crash."""
        from labeling_app.measurements import LABEL_RF, LABEL_VI
        m = np.zeros((50, 50), dtype=np.uint8)
        m[10:20, 20:30] = LABEL_RF
        m[20:30, 20:30] = LABEL_VI
        base = Image.fromarray(np.full((50, 50, 3), 100, dtype=np.uint8), "RGB")
        metrics = {"qmt_provenance": "no_femur_proxy", "qmt_px": 10}
        result = draw_qmt_annotations(base, m, metrics)
        assert isinstance(result, Image.Image)

    def test_no_measurement_no_crash(self):
        m = np.zeros((50, 50), dtype=np.uint8)
        base = Image.fromarray(np.full((50, 50, 3), 100, dtype=np.uint8), "RGB")
        metrics = {"qmt_provenance": "no_measurement"}
        result = draw_qmt_annotations(base, m, metrics)
        assert isinstance(result, Image.Image)

    def test_qmt_draws_at_true_column_not_offset(self):
        m = self._make_annotated_mask()
        base = Image.fromarray(np.zeros((50, 50, 3), dtype=np.uint8), "RGB")
        spacing = {
            "pixel_spacing_x": None,
            "pixel_spacing_y": None,
            "dcm_rows": None,
            "jpg_height": None,
            "jpg_width": None,
            "mask_height": None,
            "mask_width": None,
            "bmi": None,
        }
        metrics = compute_all_metrics(m, None, [m], spacing)
        result = draw_qmt_annotations(base, m, metrics)
        arr = np.array(result)
        qmt_col = metrics["qmt_column"]
        qmt_top = metrics["qmt_top_y"]

        assert arr[qmt_top, qmt_col].sum() > 0
        offset_col = qmt_col - 14
        if offset_col >= 0:
            assert arr[qmt_top, offset_col].sum() == 0

    def test_secondary_qmt_support_columns_are_drawn_as_guides(self):
        m = np.zeros((50, 60), dtype=np.uint8)
        m[4:18, 10:11] = 1
        m[20:38, 12:13] = 2
        m[5, 20:40] = 5
        m[6:15, 20:40] = 1
        m[15:25, 20:40] = 2
        m[28:32, 20:40] = 7
        base = Image.fromarray(np.zeros((50, 60, 3), dtype=np.uint8), "RGB")
        spacing = {
            "pixel_spacing_x": None,
            "pixel_spacing_y": None,
            "dcm_rows": None,
            "jpg_height": None,
            "jpg_width": None,
            "mask_height": None,
            "mask_width": None,
            "bmi": None,
        }
        metrics = compute_all_metrics(m, None, [m], spacing)
        support_cols = metrics["qmt_annotation_columns"]
        assert len(support_cols) >= 2

        metrics = dict(metrics)
        metrics["rf_column"] = 0
        result = draw_qmt_annotations(base, m, metrics)
        arr = np.array(result)
        qmt_top = metrics["qmt_top_y"]
        assert arr[qmt_top + 2, support_cols[1]].sum() > 0


class TestIoHelpers:

    def test_pil_to_bytes_returns_bytes(self, base_img):
        data = pil_to_bytes(base_img)
        assert isinstance(data, bytes)
        assert len(data) > 0

    def test_load_image_gray_missing(self, tmp_path):
        result = load_image_gray(str(tmp_path / "nonexistent.png"))
        assert result is None

    def test_load_image_gray_valid(self, tmp_path):
        img_path = tmp_path / "test.png"
        Image.fromarray(np.full((10, 10), 128, dtype=np.uint8)).save(img_path)
        result = load_image_gray(str(img_path))
        assert result is not None
        assert result.shape == (10, 10)
        assert result.dtype == np.uint8

    def test_load_image_pil_missing(self):
        result = load_image_pil("/does/not/exist.png")
        assert result is None

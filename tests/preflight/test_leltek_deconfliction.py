"""E5 deconfliction — the depth recovery + artifact allocation must reproduce the
backup-derived table in docs/leltek_128L_depth_deconfliction.md, order-independently.
The MOP ships now; full validation rides the first Round-3 pilot study."""
import pytest

from preflight_helpers.leltek_deconfliction import (
    allocate_artifact, depth_from_dy, depth_from_jpg_width, depth_setting,
    recover_depth, snap_to_device_step,
)

# depth_cm -> dy_mm -> jpg_width_px, verbatim from the doc's full 128L band table.
_BANDS = [(4.0, 0.068, 499), (4.9, 0.084, 399), (5.9, 0.101, 332), (6.9, 0.118, 285),
          (7.9, 0.135, 249), (8.9, 0.152, 222), (9.9, 0.169, 199), (10.9, 0.186, 181)]


@pytest.mark.parametrize("depth,dy,w", _BANDS)
def test_full_band_table(depth, dy, w):
    assert abs(depth_from_dy(dy) - depth) < 0.10, f"dy {dy} -> {depth_from_dy(dy)}"
    assert abs(depth_from_jpg_width(w) - depth) < 0.15, f"w {w} -> {depth_from_jpg_width(w)}"


def test_the_two_round3_settings():
    # RF "5 cm": dy ~0.084 / width ~399; sweep+FEMUR "7 cm": dy ~0.118 / width ~285
    assert recover_depth(dy_mm=0.084) == 4.9
    assert recover_depth(jpg_width_px=399) == 4.9
    assert recover_depth(dy_mm=0.118) == 6.9
    assert recover_depth(jpg_width_px=285) == 6.9


def test_snap_and_setting():
    assert snap_to_device_step(4.93) == 4.9
    assert snap_to_device_step(6.90) == 6.9
    assert depth_setting(4.9) == "5cm"
    assert depth_setting(6.9) == "7cm"


def test_separation_margin_no_overlap():
    # 5 vs 7 cm must not collide on either signal (doc § Separation margin)
    assert recover_depth(dy_mm=0.084) != recover_depth(dy_mm=0.118)
    assert recover_depth(jpg_width_px=399) != recover_depth(jpg_width_px=285)


def test_artifact_allocation_order_independent():
    sweep = allocate_artifact(has_dcm=False, is_long_clip=True, jpg_width_px=285)
    assert sweep["identity"] == "sweep" and sweep["depth_setting"] == "7cm"
    femur = allocate_artifact(has_dcm=True, is_long_clip=False, dy_mm=0.118)
    assert femur["identity"] == "femur" and femur["depth_setting"] == "7cm"
    rf1 = allocate_artifact(has_dcm=True, is_long_clip=False, dy_mm=0.084)
    rf2 = allocate_artifact(has_dcm=True, is_long_clip=False, jpg_width_px=399)
    assert rf1["identity"] == "rf" and rf2["identity"] == "rf"


def test_guards():
    with pytest.raises(ValueError):
        depth_from_jpg_width(0)
    with pytest.raises(ValueError):
        recover_depth()

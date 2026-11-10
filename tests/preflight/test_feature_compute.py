"""Feature compute reproduces decision_log values exactly on AGZ855 annotator masks.

This is the load-bearing regression test for the spacing-rescale fix:
without `resolve_spacing` + `dcm_cols`, `full_thickness_q50_mm` drifts
from 18.028 to 15.78 mm (a 14% error). Test guards against that.
"""
from __future__ import annotations

import importlib.util
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PIL import Image

from preflight_helpers import env
from preflight_helpers.feature_compute import _measurements_module, compute_frame_features
from preflight_helpers.post_processing import post_process_rf


# ---------------------------------------------------------------------------
# Build-Step 2 Phase-3 prerequisite — `_measurements_module()` pinned to the
# local labeling_app, gated by a legacy/local `compute_all_metrics` equivalence
# check (HANDOFF "Before any Phase 3 wiring").
# ---------------------------------------------------------------------------

_MM_ROOT = Path(__file__).resolve().parents[2]
_LEGACY_MEASUREMENTS = Path(
    str(Path(__file__).resolve().parents[2] / "private" / "muscle_quality_labeling_exercise_claude_PROMPTED") +
    "/labeling_app/measurements.py")


def _gate_mask() -> np.ndarray:
    """A layered RF / VI / fascia / femur / fat mask that exercises
    `compute_all_metrics` end to end (thickness, QMT, area, ratios, echo)."""
    m = np.zeros((160, 140), np.uint8)
    m[4:20,    15:125] = 6      # skin
    m[20:40,   15:125] = 8      # subcutaneous fat
    m[40:44,   30:110] = 5      # superficial fascia
    m[44:92,   30:110] = 1      # RF
    m[92:96,   30:110] = 5      # deep fascia
    m[96:120,  30:110] = 2      # VI
    m[124:140, 55:85] = 7       # femur — central, plateau
    return m


_GATE_SPACING = {
    "pixel_spacing_x": 0.13486, "pixel_spacing_y": 0.13486,
    "dcm_rows": 585.0, "dcm_cols": 760.0, "jpg_height": 512.0,
    "jpg_width": 249.0, "mask_height": 160, "mask_width": 140, "bmi": 30.0,
}


def _deep_equal(a, b) -> bool:
    """Structural equality that treats NaN == NaN — so a float-NaN field does
    not spuriously fail the byte-identical-output gate."""
    if isinstance(a, dict):
        return (isinstance(b, dict) and a.keys() == b.keys()
                and all(_deep_equal(a[k], b[k]) for k in a))
    if isinstance(a, (list, tuple)):
        return (type(a) is type(b) and len(a) == len(b)
                and all(_deep_equal(x, y) for x, y in zip(a, b)))
    if isinstance(a, float) and isinstance(b, float):
        return a == b or (math.isnan(a) and math.isnan(b))
    return bool(a == b)


def _load_legacy_measurements():
    """Load the legacy labeling_app/measurements.py as a standalone module — via
    importlib, so it never enters sys.modules as `labeling_app.measurements`
    and cannot shadow the local package."""
    spec = importlib.util.spec_from_file_location(
        "_legacy_measurements_gate", _LEGACY_MEASUREMENTS)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_measurements_module_pinned_to_local() -> None:
    """`_measurements_module()` resolves to THIS repo's labeling_app copy, not
    the legacy muscle_quality_labeling_exercise_claude_PROMPTED one — the
    Phase-3 import-shadowing fix. The local-only QA symbols confirm the copy."""
    mod = _measurements_module()
    assert Path(mod.__file__).resolve() == _MM_ROOT / "labeling_app" / "measurements.py"
    assert hasattr(mod, "IMAGE_QUALITY_FLAG_PRIORITY")   # local-only (Build-Step 1)
    assert hasattr(mod, "CONTRACT_VERSION")


def test_local_legacy_measurements_compute_all_metrics_identical() -> None:
    """The HANDOFF Phase-3 prerequisite gate: the pin to the local labeling_app
    is safe because local and legacy `compute_all_metrics` produce byte-identical
    output. This standing check fails loud the day the two copies diverge in a
    way that would move a measurement number. Skips when the legacy repo is
    absent — the pin itself does not depend on it."""
    if not _LEGACY_MEASUREMENTS.exists():
        pytest.skip(f"legacy labeling_app absent at {_LEGACY_MEASUREMENTS}")
    local = _measurements_module()
    legacy = _load_legacy_measurements()
    mask = _gate_mask()
    img = np.full(mask.shape, 110, np.uint8)
    kw = dict(center_mask=mask, center_img_gray=img,
              all_masks=[mask, mask], spacing_row=_GATE_SPACING)
    local_out = local.compute_all_metrics(**kw)
    legacy_out = legacy.compute_all_metrics(**kw)
    # Akadir 2026-05-24 — `rf_area_*` renamed to `visible_rf_area_*` in LOCAL
    # to surface the FOV-limitation truth in the field name itself. The LEGACY
    # repo (<repo>/private/muscle_quality_exploration) is intentionally NOT
    # renamed (it is the rf_quality external bridge, not a build target). Map
    # the rename before the byte-identical compare so the gate still detects a
    # number divergence — the whole point — without false-failing on the
    # intentional key-name divergence.
    _RENAME = {"rf_area_mm2": "visible_rf_area_mm2", "rf_area_px": "visible_rf_area_px"}
    legacy_out = {_RENAME.get(k, k): v for k, v in legacy_out.items()}
    assert _deep_equal(local_out, legacy_out), (
        "local vs legacy compute_all_metrics diverged — the _measurements_module "
        "pin is no longer a safe equivalence; reconcile the two labeling_app "
        f"copies.\nlocal={local_out}\nlegacy={legacy_out}")


AGZ855_ANCHOR = "20260127_151045625"
AGZ855_FRAMES = ["20260127_151031646", AGZ855_ANCHOR, "20260127_151052505"]
AGZ855_DIR = (
    Path(str(Path(__file__).resolve().parents[2] / "private" / "goruntuler_backup/annotations/masks"))
    / "anonabn_olcum" / "27Jan26_03"
    / "0000145_anonadm_anonabu_46e_171_110"
)
AGZ855_TRANSPORT_DIR = (
    env.TRANSPORT_ROOT / "anonabn_olcum" / "27Jan26_03"
    / "0000145_anonadm_anonabu_46e_171_110"
)


@pytest.mark.integration
def test_agz855_anchor_features_reproduce_decision_log() -> None:
    masks = [
        np.asarray(Image.open(AGZ855_DIR / f"{fb}_mask.png").convert("L"), dtype=np.uint8)
        for fb in AGZ855_FRAMES
    ]
    anchor_mask = masks[1]
    pair = post_process_rf(anchor_mask)

    tp = pd.read_csv(env.TRANSPORT_METADATA)
    tp_rows = tp[tp["frame_base"].astype(str).isin(AGZ855_FRAMES)]
    dcm_cols = float(tp_rows.iloc[0]["dcm_cols"])
    spacing_row = {
        "pixel_spacing_x": 0.13486072,
        "pixel_spacing_y": 0.13486072,
        "dcm_rows": 585.0,
        "dcm_cols": dcm_cols,
        "jpg_height": 512.0,
        "jpg_width": 249.0,
        "mask_height": 512,
        "mask_width": 256,
        "bmi": 36.0,
    }
    ff = compute_frame_features(
        frame_base=AGZ855_ANCHOR, is_anchor=True,
        multiclass_mask=anchor_mask,
        all_frame_masks=masks,
        final_image_path=AGZ855_TRANSPORT_DIR / f"{AGZ855_ANCHOR}_final.png",
        spacing_row=spacing_row,
        rf_mask_pair=pair,
    ).to_dict()

    # Labelling-app numbers — should reproduce decision_log EXACTLY.
    # VI features (vi_thickness_mm / vi_area_mm2) are no longer propagated —
    # the VI segmentation is not robust enough to surface (2026-05-14).
    assert abs(ff["rf_thickness_mm"]   - 20.186)  < 0.005
    assert abs(ff["qmt_mm"]            - 34.054)  < 0.005
    assert abs(ff["visible_rf_area_mm2"]       - 713.656) < 0.05
    assert abs(ff["muscle_fat_ratio"]  - 2.188)   < 0.005
    assert abs(ff["star_ratio"]        - 0.9459)  < 0.001
    assert "vi_thickness_mm" not in ff and "vi_area_mm2" not in ff

    # White-paper features — q50 reproduces cohort _repr (anchor frame value).
    # This is the test guarding the spacing-rescale fix.
    assert abs(ff["full_thickness_q50_mm"] - 18.028) < 0.05
    # int_cv_within reproduces cohort _repr (anchor frame value).
    assert abs(ff["int_cv_within"] - 0.425244) < 0.001

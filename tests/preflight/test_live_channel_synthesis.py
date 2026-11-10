"""Live channel synthesis works for arbitrary sweep frames, not just cohort triples.

Regression guard against the 2026-05-12 audit-surfaced bug: the frame picker
exposed all 109 AGZ855 sweep frames, but the segmenter path loaded
voi/hist/clahe from `transport/` which only has the cohort 3-frame triples.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from preflight_helpers import env
from preflight_helpers.segmenter import (
    build_voi_hist_clahe_stack, synthesize_final_u8, target_size_from_raw_png,
)


# Both roots come from `env`, never a hardcoded absolute path. The raw frames used to be addressed
# at <repo>/private/goruntuler/..., which does not exist on this machine — the study is materialized
# under studies_root and the channel PNGs live in the in-repo transport/. That stale path, not absent
# data, is why these four tests failed; the frames were on disk the whole time.
_AGZ855_REL = Path("anonabn_olcum") / "27Jan26_03" / "0000145_anonadm_anonabu_46e_171_110"
AGZ855_RAW_DIR = env.STUDIES_ROOT / _AGZ855_REL / "RawImagesForResearch_20260127_151031136"
AGZ855_TRANSPORT_DIR = env.TRANSPORT_ROOT / _AGZ855_REL
CANONICAL_ANCHOR = "20260127_151045625"
NON_CANONICAL_SWEEP_FRAME = "20260127_151047882"  # in sweep, NOT in cohort triple


@pytest.mark.integration
def test_target_size_from_raw_png_returns_512x256_for_agz855() -> None:
    w, h = target_size_from_raw_png(AGZ855_RAW_DIR, CANONICAL_ANCHOR)
    # Device-canonical mask space for this hardware.
    assert (w, h) == (256, 512)


@pytest.mark.integration
def test_live_final_for_canonical_anchor_matches_transport() -> None:
    """For a cohort-canonical frame, live `_final` must equal transport `_final.png`."""
    live = synthesize_final_u8(AGZ855_RAW_DIR, CANONICAL_ANCHOR)
    with Image.open(AGZ855_TRANSPORT_DIR / f"{CANONICAL_ANCHOR}_final.png") as im:
        canonical = np.asarray(im.convert("L"), dtype=np.uint8)
    assert live.shape == canonical.shape == (512, 256)
    assert np.array_equal(live, canonical), (
        "Live `_final` synthesis must reproduce the canonical transport "
        "_final.png byte-exact for cohort-canonical frames."
    )


@pytest.mark.integration
def test_live_channels_for_canonical_anchor_match_transport() -> None:
    """For a cohort-canonical frame, live voi/hist/clahe must equal transport PNGs."""
    final_u8 = synthesize_final_u8(AGZ855_RAW_DIR, CANONICAL_ANCHOR)
    stack = build_voi_hist_clahe_stack(final_u8, modalities=("voi", "hist", "clahe"))
    for i, m in enumerate(("voi", "hist", "clahe")):
        with Image.open(AGZ855_TRANSPORT_DIR / f"{CANONICAL_ANCHOR}_{m}.png") as im:
            canonical = np.asarray(im.convert("L"), dtype=np.uint8)
        assert np.array_equal(stack[..., i], canonical), (
            f"Live {m} channel must be byte-exact vs canonical transport for "
            f"the AGZ855 anchor frame."
        )


@pytest.mark.integration
def test_live_pipeline_works_on_non_canonical_sweep_frame() -> None:
    """Non-canonical sweep frame: synthesis runs cleanly and produces sane channels.

    No transport-side _voi/_hist/_clahe.png exists for this frame; this test
    guards against the audit-surfaced bug where the picker exposed sweep frames
    that the segmenter path could not actually process.
    """
    # Sanity: the canonical PNGs do NOT exist for this frame.
    assert not (AGZ855_TRANSPORT_DIR / f"{NON_CANONICAL_SWEEP_FRAME}_voi.png").exists()

    final_u8 = synthesize_final_u8(AGZ855_RAW_DIR, NON_CANONICAL_SWEEP_FRAME)
    assert final_u8.shape == (512, 256)
    assert final_u8.dtype == np.uint8
    assert 0 < final_u8.mean() < 255  # non-degenerate

    stack = build_voi_hist_clahe_stack(final_u8, modalities=("voi", "hist", "clahe"))
    assert stack.shape == (512, 256, 3)
    assert stack.dtype == np.uint8
    # Each channel should be non-degenerate (live synthesis didn't collapse).
    for i, m in enumerate(("voi", "hist", "clahe")):
        ch = stack[..., i]
        assert 0 < ch.mean() < 255, f"{m} channel degenerate"

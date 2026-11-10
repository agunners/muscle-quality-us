"""Probe classification from FOV geometry — the basis for setting the 128LH studies aside.

The legacy corpus was shot on two Leltek probes and the probe is NOT in the DICOM. It is recoverable
only as `width_px x depth_cm`, a probe constant: ~1967 for 128L, ~1310 for 128LH. On the real 3-rater
log the clusters are DISJOINT (1956-1973 vs 1300-1319), which is what makes the call unambiguous.
"""
from __future__ import annotations

import pytest

import classify_probe as cp


@pytest.mark.parametrize("fov,expected", [
    (1967, "128L"), (1956, "128L"), (1973, "128L"), (1964, "128L"),
    (1310, "128LH"), (1300, "128LH"), (1319, "128LH"), (1312, "128LH"),
])
def test_each_real_cluster_lands_on_its_own_probe(fov, expected) -> None:
    assert cp.classify(fov) == expected


def test_the_two_clusters_do_not_touch() -> None:
    """A gap of ~640 between 1319 and 1956 is why this is a call and not a guess."""
    assert cp.classify(1319) == "128LH" and cp.classify(1956) == "128L"
    midpoint = (cp.FOV_128L + cp.FOV_128LH) / 2
    assert 1319 < midpoint < 1956


def test_fov_is_width_times_depth_in_cm() -> None:
    """depth_cm = dcm_rows * pixel_spacing_y / 10 — spacing is mm/px, so the /10 is load-bearing."""
    row = {"jpg_width": "249", "pixel_spacing_y": "0.13486072", "dcm_rows": "585"}
    got = cp.fov_of(row)
    assert got == pytest.approx(249 * (585 * 0.13486072 / 10), rel=1e-9)
    assert cp.classify(got) == "128L"


def test_a_128lh_geometry_classifies_as_128lh() -> None:
    row = {"jpg_width": "185", "pixel_spacing_y": "0.121203534", "dcm_rows": "585"}
    assert cp.classify(cp.fov_of(row)) == "128LH"


@pytest.mark.parametrize("row", [
    {},
    {"jpg_width": "", "pixel_spacing_y": "0.13", "dcm_rows": "585"},
    {"jpg_width": "249", "pixel_spacing_y": "0", "dcm_rows": "585"},
    {"jpg_width": "n/a", "pixel_spacing_y": "0.13", "dcm_rows": "585"},
])
def test_missing_or_unusable_geometry_yields_None_never_a_guess(row) -> None:
    """19 studies in the 282 lack these fields. They must read 'unknown', never be assigned a probe."""
    assert cp.fov_of(row) is None


# --- the geometry refill -----------------------------------------------------------------
# 19 studies (all inside the frozen 282) have jpg_width but blank pixel_spacing_y/dcm_rows, and their
# DICOMs are not in the backup. They are refillable because each width comes with exactly one
# (spacing, rows) pair on this corpus — so the cells are recovered, never guessed.

def test_a_width_seen_with_one_pair_is_refillable() -> None:
    log = [{"jpg_width": "249", "pixel_spacing_y": "0.13486072", "dcm_rows": "585"},
           {"jpg_width": "249", "pixel_spacing_y": "0.13486072", "dcm_rows": "585"},
           {"jpg_width": "249", "pixel_spacing_y": "", "dcm_rows": ""}]
    assert cp.geometry_map(log)[249.0] == ("0.13486072", "585")


def test_a_width_seen_with_TWO_pairs_is_dropped_not_averaged() -> None:
    """Ambiguity must produce 'unknown', never a plausible-looking blend."""
    log = [{"jpg_width": "249", "pixel_spacing_y": "0.13486072", "dcm_rows": "585"},
           {"jpg_width": "249", "pixel_spacing_y": "0.11778986", "dcm_rows": "585"}]
    assert 249.0 not in cp.geometry_map(log)


def test_refilled_geometry_reproduces_the_probe_it_should() -> None:
    """The real refill case: width 249 -> 128L, width 185 -> 128LH."""
    pair_l, pair_lh = ("0.13486072", "585"), ("0.121203534", "585")
    fov_l = 249 * (float(pair_l[1]) * float(pair_l[0]) / 10)
    fov_lh = 185 * (float(pair_lh[1]) * float(pair_lh[0]) / 10)
    assert cp.classify(fov_l) == "128L" and 1956 <= fov_l <= 1973
    assert cp.classify(fov_lh) == "128LH" and 1300 <= fov_lh <= 1319


def test_blank_geometry_rows_never_enter_the_map() -> None:
    log = [{"jpg_width": "300", "pixel_spacing_y": "", "dcm_rows": "585"},
           {"jpg_width": "300", "pixel_spacing_y": "0.1", "dcm_rows": ""}]
    assert cp.geometry_map(log) == {}

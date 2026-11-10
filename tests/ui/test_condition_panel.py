"""condition_panel — the breakdown shows, per axis, WHERE the patient sits (the SAME
cohort percentile the feature card shows + that axis's own better-direction) and HOW
MUCH it moves the score (its β·z contribution, exact for the near-orthogonal 3-axis
model). The display scale (0–100 vs 0–800) rescales the figures WITHOUT touching the
model. AppTest re-execs each render fn in its own namespace, so each is self-contained."""
from __future__ import annotations

from streamlit.testing.v1 import AppTest

from labeling_app.assessment_pages import condition_panel as cp


def test_driver_read_names_the_dominant_contributor() -> None:
    rows = [{"clinical": "Size", "name": "Muscle vs body size", "contribution": -2.57},
            {"clinical": "Fat", "name": "Fat over muscle", "contribution": -0.46},
            {"clinical": "Tissue", "name": "Fascicular contrast", "contribution": -1.41}]
    line = cp.driver_read(rows)
    assert "Size" in line                       # the largest |β·z|
    assert "PRESERVED" in line                  # negative β·z -> toward PRESERVED


def test_driver_read_toward_poor_when_contribution_positive() -> None:
    line = cp.driver_read(
        [{"clinical": "Fat", "name": "Fat over muscle", "contribution": 2.1},
         {"clinical": "Tissue", "name": "Fascicular contrast", "contribution": -0.5}])
    assert "Fat" in line and "POOR" in line


def test_driver_read_empty_when_no_contribution() -> None:
    assert cp.driver_read([{"clinical": "Fat", "contribution": None}]) == ""


def _breakdown_render() -> None:
    import streamlit as st
    from labeling_app.assessment_pages import condition_panel as cp
    rows = [
        {"clinical": "Size", "name": "Muscle vs body size", "help_name": "Muscle vs body size",
         "pct": 89.0, "direction": "↑ higher = better", "contribution": -2.57},
        {"clinical": "Fat", "name": "Fat over muscle", "help_name": "Fat over muscle",
         "pct": 20.0, "direction": "↓ lower = better", "contribution": -0.20},
        {"clinical": "Tissue", "name": "Fascicular contrast", "help_name": "Fascicular contrast",
         "pct": 77.0, "direction": "↑ higher = better", "contribution": -1.41}]
    cp.breakdown_block(rows, st)


def test_breakdown_block_shows_position_and_contribution() -> None:
    at = AppTest.from_function(_breakdown_render).run()
    assert not at.exception
    body = (" ".join(m.value for m in at.markdown)
            + " " + " ".join(c.value for c in at.caption))
    assert "What drives the score" in body
    assert "Size" in body and "Fat" in body and "Tissue" in body   # the clinical axes
    assert "lower = better" in body                                # fat's own direction
    assert "pushes the score toward" in body                       # contribution strength
    assert "pushes this score the" in body                         # the driver line (reworded moves->pushes 2026-06-18)
    # (the pXX numbers live in st.progress, which AppTest's markdown scrape can't
    #  read; the card-percentile match is locked by the pure _breakdown_rows test.)


def test_display_scale_rescales_figures_not_the_model() -> None:
    f100 = cp.band_strip_figure(53.0, [25.5, 75.6], "INTERMEDIATE", 100)
    f800 = cp.band_strip_figure(53.0, [25.5, 75.6], "INTERMEDIATE", 800)
    assert tuple(f100.layout.xaxis.range) == (0, 100)
    assert tuple(f800.layout.xaxis.range) == (0, 800)
    # the score marker (and only the display) moves 8x — the model's value is native
    assert abs(f100.data[0].x[0] - 53.0) < 1e-6
    assert abs(f800.data[0].x[0] - 53.0 * 8) < 1e-6


def test_triplet_scale_rescales_axis() -> None:
    opts = {"C": {"available": True, "condition": 91.0, "band": "PRESERVED",
                  "boundaries": [25.5, 75.6],
                  "box": {"p10": 20, "p25": 40, "p50": 60, "p75": 80, "p90": 95},
                  "label": "STAR / Fat / Texture"},
            "A": {"available": True, "condition": 70.0, "band": "INTERMEDIATE",
                  "boundaries": [25.0, 74.1],
                  "box": {"p10": 15, "p25": 35, "p50": 55, "p75": 78, "p90": 92},
                  "label": "RF / Fat / Texture"}}
    assert tuple(cp.triplet_figure(opts, scale_max=100).layout.xaxis.range) == (0, 100)
    assert tuple(cp.triplet_figure(opts, scale_max=800).layout.xaxis.range) == (0, 800)

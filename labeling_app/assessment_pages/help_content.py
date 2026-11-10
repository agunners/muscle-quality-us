"""Per-stage help + methodology content (F6 / F12 / N6).

Each phase's sidebar carries an `st.expander("ℹ Help & methodology")`. The
first four phases get a short "what this step is" note; phase 5 carries the
deepest content — how every one of the 9 features is calculated, which
aggregation policy it follows, how the anchor frame is chosen, and the two
caveats that change how a number should be read (the side-frame caveat and
the model VI over-prediction caveat).

Akadir's audit ask, verbatim: "We/the user should know how we calculate each
param ... is p50 thickness calculated for each frame or what happens if it a
single frame and that is a side view? Which feature(s) is aggregate and which
is per representative frame, how is the representative frame selected?"
"""
from __future__ import annotations

import streamlit as st


# Phase index (0-based) → (title, markdown body). Phases 1-4.
PHASE_HELP: dict[int, tuple[str, str]] = {
    0: ("Step 1 — Study selector", """
**What this is.** Pick the study (one ultrasound sweep) to assess. The
selector walks `data/studies_root/` for folders that contain a DICOM + an
MP4 + a `RawImagesForResearch_` subdirectory.

**PHI guardrail.** Folder names carry patient identifiers — they never reach
the screen. You only ever see the `study_code` (e.g. AGZ855) + parsed
demographics. Raw paths live in the dev-mode expander + the local ledger only.
"""),
    1: ("Step 2 — Model selector", """
**What this is.** Pick the segmentation bundle. Every bundle is validated
(load smoke + output-channel count from the loaded model, not the JSON);
invalid bundles are listed but disabled. The default is the most-recent valid
bundle.

The bundle's class IDs are *pinned in code* (RF=1 … FEMUR=7 … FAT=8), not read
from the palette — the palette is for colour only.
"""),
    2: ("Step 3 — Frame picker", """
**What this is.** Pick up to 9 frames from the sweep; 3 is the suggested
target. The picked set, the counter, and the **Segment + continue** button
are in this sidebar so they stay put while you scroll the grid.

**The anchor (★).** Mark one picked frame as the *anchor* — the representative
frame the *anchor_primary* aggregation reads for geometry features. If you
don't mark one, the middle of the pick order is used.
"""),
    3: ("Step 4 — Review + 3-stage gates", """
**What this is.** Each picked frame is segmented; you judge it through three
sequential gates:

- **Usable?** — frame + segmentation good enough to use at all. No → dropped.
- **Segmentation perfect?** — only if usable. Yes → flagged as a training
  candidate (a curator promotes it later; this gate never auto-promotes).
- **Use for analysis?** — only if usable. Yes → the frame enters the aggregate.

A frame can be *usable but not perfect* and still feed the analysis. Only
`use_for_analysis = Yes` frames are aggregated.
"""),
}


# Per-feature calculation explanation — all 9 (N6), not just the headline 4.
FEATURE_METHODOLOGY: dict[str, tuple[str, str]] = {
    "star_ratio": ("STAR (QMT / BMI)",
        "QMT thickness in mm divided by BMI — a quality ratio that scales total "
        "quadriceps thickness by body size. Higher = more muscle relative to "
        "body habitus. **anchor_primary** policy."),
    "full_thickness_q50_mm": ("RF thickness — median (mm)",
        "Per-column fascia-to-fascia RF thickness, computed on the *eroded* RF "
        "mask, then the **median** (50th percentile) across columns — the typical "
        "thickness across the muscle profile, robust to a few thin or thick "
        "columns. **anchor_primary** policy."),
    "int_cv_within": ("Fascicular contrast (echo CV)",
        "Within-frame coefficient of variation of echo intensity inside the "
        "eroded RF ROI, on the `_final.png` modality — gain-invariant. Higher = "
        "preserved internal contrast (fascicular structure visible); lower = "
        "washed-out, less internal contrast. **agg_primary** policy (multi-frame median)."),
    "rf_thickness_mm": ("RF thickness — at the landmark cross-section (mm)",
        "RF thickness read at the landmark — the study's fullest clean "
        "cross-section. Per frame it is the maximum contiguous RF span (the "
        "tallest column); the study number is taken at the landmark because the "
        "single thickest sweep frame is usually an oblique (foreshortened) cut, "
        "not thicker muscle. **anchor_primary** policy."),
    "muscle_fat_ratio": ("Muscle / fat ratio",
        "(visible RF area + visible VI area) ÷ subcutaneous fat area — area-based, not "
        "intensity-derived. Higher = more muscle per unit fat. "
        "**anchor_primary** policy."),
    "qmt_mm": ("QMT (mm)",
        "Quadriceps muscle thickness — the fascia-to-femur (or femur-proxy) "
        "span at the standard QMT column. **anchor_primary** policy."),
    "visible_rf_area_mm2": ("Visible RF area (mm²)",
        "The RF cross-section visible in the frame, in mask-space mm² on the "
        "full (non-eroded) mask. The probe surface limits the field of view, "
        "so this is always a partial measurement of the anatomical RF — the "
        "name acknowledges that honestly (Akadir 2026-05-24). "
        "**anchor_primary** policy."),
}


_POLICY_NOTE = """
**Aggregate vs anchor — which is which.**

- **Geometry** (STAR, RF thickness — at landmark, RF thickness — median, QMT,
  Visible RF area, muscle/fat ratio) follows **anchor_primary**: the headline
  number is the *anchor frame's* value (`{feature}_repr`). The multi-frame
  median is kept alongside as a sensitivity check.
- **Intensity** (fascicular contrast / int_cv_within) follows
  **agg_primary**: the headline number is the *multi-frame median* over the
  analysis frames; the anchor value is the sensitivity check.
- A **single** analysis frame collapses both policies to that one frame.
- The **anchor frame** is the one you marked ★ in the picker; unmarked → the
  middle of the pick order.
"""

_CAVEATS = """
**Two caveats that change how a number reads.**

- **Side-frame caveat.** Per-column q50 thickness computed over *lateral-only*
  picks represents the lateral RF extent, not centre anatomy. For
  cohort-comparable thickness, include a mid-sweep frame and mark it the
  anchor.
- **Model VI over-prediction.** The segmenter tends to over-predict VI on some
  studies. VI is no longer surfaced as its own number, but QMT spans the RF+VI
  muscle stack — so on those studies QMT and STAR can still drift up ~10–15% vs
  the annotator-mask cohort values (headline RF / full-thickness / fascicular-
  contrast stay close). When you see VI bleeding past where your eye expects,
  gate that frame **segmentation perfect = No**.
"""


def render_help_expander(phase_idx: int) -> None:
    """Render the per-stage help expander into the current sidebar."""
    with st.expander("ℹ Help & methodology", expanded=False):
        if phase_idx in PHASE_HELP:
            title, body = PHASE_HELP[phase_idx]
            st.markdown(f"**{title}**")
            st.markdown(body)
        if phase_idx == 4:  # Assessment view — the deep methodology.
            st.markdown("**Step 5 — Assessment view**")
            st.markdown(
                "How each of the 9 features is calculated, which aggregation "
                "policy it follows, and the caveats that change how to read it."
            )
            st.markdown("**Per-feature calculation**")
            for _feat, (label, body) in FEATURE_METHODOLOGY.items():
                st.markdown(f"- **{label}** — {body}")
            st.markdown(_POLICY_NOTE)
            st.markdown(_CAVEATS)

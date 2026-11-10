"""Streamlit entry for the Friday MVP — Assessment workflow.

Five phases (stepped through in-app via session state, not via Streamlit's
auto-discovered pages/ — that would clutter the sidebar):

    Phase 1  Study selector
    Phase 2  Model selector
    Phase 3  Frame picker (manual; auto-suggest is a stub)
    Phase 4  Frame review + 3-stage human gates
    Phase 5  Assessment view (the boss-facing screen)

Plus a persistent sidebar (collapsed by default during demo):
    Previous runs against this study (cached-mode loading)

PHI guardrail enforced throughout: study_code + parsed demographics are the
only patient identifiers rendered. Raw paths live inside a "Developer info"
expander (collapsed by default) and in the gitignored run-ledger JSONL.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Launcher preamble — make repo root + scripts/ importable, ensure runtime bridge.
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))
import preflight_helpers.env_bootstrap  # noqa: F401  side-effect

from preflight_helpers import run_lifecycle  # noqa: E402

from labeling_app.assessment_pages import (  # noqa: E402
    p01_study_selector,
    p02_model_selector,
    p03_frame_picker,
    p04_frame_review,
    p05_assessment_view,
)

# Each phase module exposes render() (main area) + render_sidebar()
# (stage-modular sidebar — F3). assessment_app dispatches both.
_PHASES = [
    ("1. Study", p01_study_selector),
    ("2. Model", p02_model_selector),
    ("3. Frames", p03_frame_picker),
    ("4. Review", p04_frame_review),
    ("5. Assessment", p05_assessment_view),
]


def _init_session() -> None:
    st.session_state.setdefault("phase", 0)
    st.session_state.setdefault("study_entry", None)
    st.session_state.setdefault("bundle", None)
    st.session_state.setdefault("learn", None)             # cached learner
    st.session_state.setdefault("bundle_meta", None)
    st.session_state.setdefault("picked_frames", [])       # list[str] frame_base
    st.session_state.setdefault("predicted_masks", {})     # {frame_base: ndarray}
    st.session_state.setdefault("final_u8_cache", {})      # {frame_base: ndarray}
    st.session_state.setdefault("gates", {})               # {frame_base: dict}
    st.session_state.setdefault("anchor_frame", None)      # explicit user-marked anchor (B2.3)
    st.session_state.setdefault("run_result", None)        # RunResult-shaped dict or cached record
    st.session_state.setdefault("run_mode", "live")        # 'live' | 'cached'
    # N4 run lifecycle: 'none' (no run yet) → 'draft' (computed, not in ledger)
    # → 'finalized' (accepted into runs.jsonl, or loaded from it).
    st.session_state.setdefault("run_lifecycle", "none")
    st.session_state.setdefault("run_input_sig", None)     # input_signature of the cached run_result
    st.session_state.setdefault("masks_input_sig", None)   # signature of the cached predicted_masks
    st.session_state.setdefault("dev_mode", False)
    # Sweep orphaned drafts once per app session (a draft is orphaned when the
    # user closed the app or hit "Start afresh" without accepting it).
    if not st.session_state.get("_gc_drafts_done"):
        run_lifecycle.gc_drafts()
        st.session_state["_gc_drafts_done"] = True


def reset_run_state() -> None:
    """N4 "Start afresh" — discard the open draft, reset picks / masks / gates /
    run / femur-scan, and return to the study selector. Study + model selection
    are preserved (and so are bundle_meta + learner — no GPU re-warmup); the
    operator lands back at step 1 and can re-confirm or switch the study.
    """
    ss = st.session_state
    if ss.get("run_lifecycle") == "draft":
        rr = ss.get("run_result") or {}
        run_lifecycle.discard_draft(rr.get("run_id"))
    ss.picked_frames = []
    ss.predicted_masks = {}
    ss.final_u8_cache = {}
    ss.gates = {}
    ss.anchor_frame = None
    ss.femur_scan = None
    # v8 per-modality state — clear so a prior study's femur-cine scan/masks + femur/RF picks/gates
    # don't bleed into the next study (a stale femur tier-summary or femur landmark). Akadir 2026-06-26.
    ss.femur_cine_scan = None
    ss.femur_cine_masks = None
    ss["picked_frames__femur"] = []
    ss["picked_frames__rf"] = []
    ss["gates__femur"] = {}
    ss["gates__rf"] = {}
    ss["_landmark_cache"] = None
    ss.run_result = None
    ss.run_input_sig = None
    ss.masks_input_sig = None
    ss.run_lifecycle = "none"
    ss.run_mode = "live"
    ss.phase = 0  # the study selector — study + model stay loaded (no GPU re-warmup)


def _stepper() -> None:
    """Horizontal stepper at the top — clickable to revisit earlier phases."""
    cols = st.columns(len(_PHASES))
    for i, (label, _module) in enumerate(_PHASES):
        with cols[i]:
            cur = st.session_state.phase
            if i < cur:
                if st.button(f"✓ {label}", key=f"step_{i}", help="Revisit this step"):
                    st.session_state.phase = i
                    st.rerun()
            elif i == cur:
                st.markdown(f"**▸ {label}**")
            else:
                st.markdown(f":gray[{label}]")


def _inject_global_css() -> None:
    """One-time CSS injection.

    F1 (audit-v2): the image fullscreen affordance only re-renders the same
    thumbnail at native source resolution — no extra information, just a
    misleading "click me" cue for the boss. Streamlit exposes no API to
    disable it, so we hide the button via CSS.

    Selector note: the plan named `[data-testid='StyledFullScreenButton']`,
    which is a stale testid — Streamlit 1.52.2 renders the fullscreen control
    as `stElementToolbarButton` with `aria-label="Fullscreen"` inside
    `stElementToolbar`. We target the current selector and keep the legacy
    one in the list (harmless — it simply matches nothing).
    """
    st.markdown(
        "<style>"
        "[data-testid='StyledFullScreenButton'],"
        "[data-testid='stElementToolbarButton'][aria-label='Fullscreen'],"
        "button[aria-label='Fullscreen']{display:none !important;}"
        # Akadir 2026-05-25 § 2: the picker's pre-scan button needs visual
        # prominence so the operator notices it as an info-gathering action
        # (distinct from the red "Segment + continue" primary CTA). Green
        # outline + green text via Streamlit's key-scoped class
        # (`.st-key-<key>`). Type stays "secondary" so the outlined-button
        # shape carries the colour cleanly.
        ".st-key-femur_scan_btn button{"
        "border-color:#16a34a !important;color:#16a34a !important;}"
        ".st-key-femur_scan_btn button:hover{"
        "border-color:#15803d !important;color:#15803d !important;"
        "background-color:rgba(22,163,74,0.08) !important;}"
        "</style>",
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(
        page_title="Muscle assessment (v3 MVP)",
        layout="wide",
        # B2.2 moved load-bearing stage controls into the sidebar (the picker's
        # Segment+continue button + live selection panel — F2's sticky fix), so
        # the sidebar must start expanded or those controls are hidden on land.
        initial_sidebar_state="expanded",
    )
    _init_session()
    _inject_global_css()

    st.title("Rectus femoris muscle assessment")
    st.caption(
        "MVP v3 — pipeline-driven assessment + cohort placement. "
        "Internal terms only: assessment, analysis, quality signals. "
        "Not a clinical decision tool."
    )
    _stepper()
    st.divider()

    label, module = _PHASES[st.session_state.phase]

    # Stage-modular sidebar (F3): each phase owns its sidebar content. The
    # native left sidebar is sticky while the main area scrolls — that is also
    # F2's fix for the phase-3 picker (selection panel + Segment button live
    # here, so they never scroll out of reach).
    with st.sidebar:
        st.session_state.dev_mode = st.toggle(
            "Developer info", value=st.session_state.dev_mode,
            key="dev_mode_toggle",
            help="Surface raw paths and per-feature provenance — Akadir-side only; "
                 "do NOT open during demo."
        )
        # Layer-stack toggles — Akadir 2026-05-25 § 1: the Display-layers panel
        # is only meaningful on p04 (review page), where the layer-stack image
        # actually renders. It used to be rendered unconditionally in the
        # global sidebar — Akadir's click-through flagged that the panel
        # appears (and is non-functional) on p03 + p05. The setdefault keeps
        # the session-state keys defined on every phase so any downstream
        # reader's lookup is safe; only the widget RENDER is phase-gated.
        # AppTest note: scoping a widget to a phase is fine when the widget
        # is CONSISTENTLY absent off-phase; the conditional-keyed-widget
        # gotcha (memory `feedback_apptest_conditional_widgets`) only fires
        # when the same key flickers in/out within a single test flow, which
        # is not the case here.
        st.session_state.setdefault("p04_show_segmentation", True)
        st.session_state.setdefault("p04_show_confidence", False)
        st.session_state.setdefault("p04_show_change_map", False)
        if st.session_state.phase == 3:
            with st.expander("Display layers (review-page image)", expanded=False):
                st.session_state.p04_show_segmentation = st.toggle(
                    "Layer 1 — segmentation overlay",
                    value=st.session_state.p04_show_segmentation,
                    key="p04_show_segmentation_toggle",
                    help="Coloured class overlay + magenta eroded contour + green "
                         "RF ticks + white QMT caliper.")
                st.session_state.p04_show_confidence = st.toggle(
                    "Layer 2 — AI confidence (cyan)",
                    value=st.session_state.p04_show_confidence,
                    key="p04_show_confidence_toggle",
                    help="Cyan wash where the segmenter was unsure. Paints "
                         "BOTH the raw image (left) and the overlay (right) — "
                         "Akadir 2026-05-25 § 5.")
                st.session_state.p04_show_change_map = st.toggle(
                    "Layer 3 — post-proc override (lime)",
                    value=st.session_state.p04_show_change_map,
                    key="p04_show_change_map_toggle",
                    help="Lime wash where the display-side cleanup re-labelled "
                         "the AI's call — stray pixels dropped, RF holes filled.")
        st.divider()
        try:
            module.render_sidebar()
        except Exception as e:  # never let a sidebar error crash the page
            st.caption(f":gray[Sidebar unavailable: {e!r}]")

    # Dispatch the current phase's main area.
    try:
        module.render()
    except Exception as e:  # surface errors without crashing the page
        st.error(f"Phase {st.session_state.phase + 1} error: {e!r}")
        if st.session_state.dev_mode:
            import traceback
            st.code(traceback.format_exc(), language="text")


if __name__ == "__main__":
    main()

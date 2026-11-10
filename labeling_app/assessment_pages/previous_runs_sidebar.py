"""Sidebar: previous runs against the currently-selected study.

Renders a small table of prior *finalized* run timestamps + which frames were
used; click a row to load that run into the assessment view in cached mode
(banner explicitly says "Loaded cached preflight run from <timestamp>").

N4: only finalized runs reach `runs.jsonl`, so this never shows drafts. Loading
a row hydrates picks + gates from the ledger record — the user can then refine
the loaded run, and editing branches a fresh draft (handled in p05._ensure_run).

Persist richly, display simply — this is the secondary surface; the main view
is always one current assessment.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from preflight_helpers import env
from preflight_helpers.bundle_writer import runs_for_study
from preflight_helpers.feature_catalog import HEADLINE_FEATURES, SHORT_LABELS

from labeling_app.assessment_pages.session_sig import current_input_sig


# Each previous-run row shows the headline features. Short labels come from the
# feature_catalog (single source) so a newly-added headline feature is labelled
# automatically — the old hand-maintained dict here lacked the fat feature, and
# hard-indexing it (`_SHORT_LABELS[f]`) crashed the WHOLE sidebar with
# KeyError('fat_thickness_q50_mm'). The per-row DIGITS stay local on purpose:
# these rows are a deliberately COARSER, compact readout than the p05 card (RF
# thickness to 1 dp here vs 2 on the card).
_DIGITS = {"star_ratio": 2, "full_thickness_q50_mm": 1,
           "int_cv_within": 2, "rf_thickness_mm": 1}


def _fmt(feature: str, value) -> str:
    if not isinstance(value, (int, float)):
        return "—"
    return f"{value:.{_DIGITS.get(feature, 2)}f}"


def _load_cached_run(run: dict) -> None:
    """Hydrate session state with a finalized ledger run (N4 cached-mode)."""
    st.session_state.run_result = {
        "run_id": run["run_id"],
        "aggregate": run.get("aggregate", {}),
        "frame_features": run.get("frame_features", []),
        "placements": run.get("placements", {}),
        "routed_modalities": run.get("routed_modalities", []),  # v8 — so the caveat banner reloads too
        "record": run,
        "record_path": str(env.RUNS_DIR / run["run_id"] / "record.json"),
        "created_at_utc": run.get("created_at_utc", "—"),
    }
    # Hydrate picks + gates from the ledger record so the user can refine the
    # loaded run — editing then branches a fresh draft (see p05._ensure_run).
    sel = run.get("selected_frames", []) or []
    st.session_state.picked_frames = [s["frame_base"] for s in sel]
    st.session_state.gates = {
        s["frame_base"]: dict(s.get("gates", {}) or {}) for s in sel
    }
    st.session_state.anchor_frame = next(
        (s["frame_base"] for s in sel if s.get("is_anchor")), None
    )
    # v8 (Akadir 2026-07-20): rehydrate the routed femur/RF picks + gates so a reload-then-refine
    # recomputes off the SAME femur/RF frames the run stood on — not empty picks (which silently
    # recomputed a WRONG routed number). All default empty for legacy records with no `routed_picks`,
    # so a sweep-only run behaves exactly as before. Keys mirror the app's own session stores.
    rp = run.get("routed_picks") or {}
    st.session_state["picked_frames__femur"] = list((rp.get("femur") or {}).get("frame_bases") or [])
    st.session_state["picked_frames__rf"] = list((rp.get("rf") or {}).get("frame_bases") or [])
    st.session_state["gates__femur"] = dict((rp.get("femur") or {}).get("gates") or {})
    st.session_state["gates__rf"] = dict((rp.get("rf") or {}).get("gates") or {})
    st.session_state["texture_choice"] = run.get("texture_choice")
    st.session_state["texture_refine"] = run.get("texture_refine")
    st.session_state.run_mode = "cached"
    st.session_state.run_lifecycle = "finalized"
    # run_input_sig must match the *hydrated* state so the first _ensure_run is
    # a cache hit, not an immediate recompute. (The plan's text said `None`
    # here, but None can never equal a real signature → it would recompute the
    # freshly-loaded cached run. Setting it to the signature of the hydrated
    # picks/gates/bundle is the correct, working equivalent.)
    st.session_state.run_input_sig = current_input_sig()
    # Leave predicted_masks / final_u8_cache alone: if they happen to hold this
    # run's frames the anchor overlay renders; otherwise it falls back blank,
    # and p04 self-heals via its masks-signature guard on next visit.
    st.session_state.phase = 4  # straight to the assessment view


def render() -> None:
    entry = st.session_state.get("study_entry")
    if entry is None:
        st.caption("No study selected.")
        return
    runs = runs_for_study(entry.meta.study_code, study_uid=entry.meta.study_uid)
    if not runs:
        st.caption(f"No previous runs against {entry.meta.study_code}.")
        return

    st.markdown(f"**Previous runs ({len(runs)})**")
    # F16 — provenance label: answers "why do STAR values differ across runs?"
    st.caption(
        ":gray[Why values shift run-to-run: geometry (STAR, thicknesses) is "
        "the **anchor-frame representative** — it moves with which frame is "
        "the anchor and which model ran; echo CV is the multi-frame median.]"
    )
    for run in reversed(runs[-10:]):  # most recent 10
        ts = run.get("created_at_utc", "—")
        ag = run.get("aggregate", {}) or {}
        n_frames = ag.get("_n_frames_in_aggregate", "?")
        mb = run.get("model_bundle", {}) or {}
        model_short = Path(mb["pkl_path"]).stem[:24] if mb.get("pkl_path") else "?"
        with st.container(border=True):
            st.caption(f"`{ts}` · n={n_frames} · model `{model_short}`")
            # N1 — headline-4 values per row, not STAR alone.
            st.markdown(" · ".join(
                f"{SHORT_LABELS.get(f, f)} **{_fmt(f, ag.get(f))}**"
                for f in HEADLINE_FEATURES
            ))
            if st.button("Load this run", key=f"load_{run['run_id']}",
                         type="secondary", use_container_width=True):
                _load_cached_run(run)
                st.rerun()

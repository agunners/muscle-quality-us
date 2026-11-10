"""Phase 2 — Model selector.

Lists every bundle from both registry sources (`transport/models/` and
`model_/`) with validation status + key metadata. Default selection = the
most-recent-mtime valid bundle (which is usually the canonical fold4 of the
latest 5-fold ensemble). User can override.

Once selected, the learner is loaded once and cached in session state — the
~1.5 s GPU warmup is paid once, not per page interaction.
"""
from __future__ import annotations

import dataclasses

import streamlit as st

from preflight_helpers.model_registry import (
    BundleEntry, _sha256_of, default_bundle, is_canonical, list_bundles,
    load_canonical_pointer,
)
from preflight_helpers.segmenter import BundleMeta, load_learner_safe


def _rrc(b: BundleEntry) -> tuple[int, int]:
    """The resolution the model actually validates/infers at (rrc_size), not
    resize_to (the training pad box). Falls back to resize_to if rrc is absent."""
    return b.rrc_size if b.rrc_size and b.rrc_size != (0, 0) else b.resize_to


def _bundle_label(b: BundleEntry, pointer: dict | None = None) -> str:
    stem = b.display_name()
    tag = "🟢 valid" if b.valid else f"🔴 {b.validation_error}"
    star = "★ canonical · " if is_canonical(b, pointer) else ""
    rrc = _rrc(b)
    return f"[{b.source}] {tag} · {star}fold {b.fold} · validates {rrc[0]}×{rrc[1]} · {stem[:58]}"


def _load_into_session(b: BundleEntry) -> None:
    # The registry pass runs with compute_hash=False (cheap). Hash the chosen
    # pkl exactly once here, inside the load spinner — so the bundle's sha is
    # populated for every downstream consumer (run_pipeline ledger provenance
    # + the N4/N5 input signatures via session_sig.session_bundle_sha).
    with st.spinner(f"Loading {b.display_name()[:40]} …"):
        learn = load_learner_safe(b.pkl_path)
        meta = BundleMeta.from_pkl(b.pkl_path)
        b = dataclasses.replace(b, pkl_sha256=_sha256_of(b.pkl_path))
    st.session_state.bundle = b
    st.session_state.learn = learn
    st.session_state.bundle_meta = meta


def render_sidebar() -> None:
    """Stage-modular sidebar (F3) — phase 2: carries the study context forward
    so the operator sees who they are about to model."""
    st.markdown("### ② Model")
    entry = st.session_state.get("study_entry")
    if entry is not None:
        d = entry.meta.to_ui_dict()
        sub = f"{d['age']}y {d['sex'] or '—'}"
        if d["bmi"] is not None:
            sub += f" · BMI {d['bmi']:.1f}"
        st.caption(f"Study **{d['study_code']}** · {sub}")
    else:
        st.caption("No study selected yet.")
    bundle = st.session_state.get("bundle")
    if bundle is not None:
        st.caption(f"Loaded: `{bundle.display_name()[:34]}…`")
    else:
        st.caption("Pick a segmentation bundle, then **Load + continue**.")
    from labeling_app.assessment_pages import help_content
    help_content.render_help_expander(1)


def render() -> None:
    if st.session_state.study_entry is None:
        st.info("Pick a study first (phase 1).")
        return

    sm = st.session_state.study_entry.meta.to_ui_dict()
    st.subheader(f"Pick a segmentation model — for {sm['study_code']}")

    bundles = list_bundles(compute_hash=False)
    if not bundles:
        st.error("No model bundles found in either `model_/` or `transport/models/`.")
        return

    # Default: the pinned canonical bundle (sha-guarded), not newest-mtime.
    ptr = load_canonical_pointer()
    default = default_bundle(bundles)
    default_idx = bundles.index(default) if default else 0
    selected_idx = st.radio(
        "Bundles", range(len(bundles)),
        index=default_idx,
        format_func=lambda i: _bundle_label(bundles[i], ptr),
        key="bundle_choice",
    )
    chosen = bundles[selected_idx]

    cols = st.columns([3, 2, 2])
    with cols[0]:
        if st.button("Load model + continue →", type="primary",
                     disabled=not chosen.valid, key="load_model_continue"):
            _load_into_session(chosen)
            st.session_state.phase = 2
            st.rerun()
    with cols[1]:
        if st.button("← Back", key="back_to_study"):
            st.session_state.phase = 0
            st.rerun()

    # Bundle detail card.
    st.divider()
    st.markdown(f"**Selected:** `{chosen.display_name()}`")
    cols = st.columns(4)
    cols[0].metric("Modalities", " + ".join(chosen.modalities))
    rrc = _rrc(chosen)
    cols[1].metric("Validates at (H×W)", f"{rrc[0]} × {rrc[1]}")
    cols[2].metric("Arch", chosen.arch or "?")
    cols[3].metric("Encoder", chosen.encoder or "?")

    # Honest preprocessing note + canonical audit line. The model sees rrc_size
    # (letterbox-padded, no crop); resize_to is the training pad box, NOT the
    # inference resolution. If the operator overrides off the canonical bundle,
    # warn that measurements will not match the v1.1 cohort ruler.
    if is_canonical(chosen, ptr):
        st.caption(
            f"★ **Canonical bundle** — the segmenter the v1.1 cohort ruler and the "
            f"condition score were calibrated on. Serving preprocessing: letterbox-pad "
            f"to {rrc[0]}×{rrc[1]}, no crop. (`resize_to` {chosen.resize_to[0]}×"
            f"{chosen.resize_to[1]} is the training pad box, not the inference size.)"
        )
    else:
        st.warning(
            f"⚠️ **Not the canonical bundle.** Measurements from this segmenter are placed "
            f"against the v1.1 cohort, which was cut on a *different* bundle — the comparison "
            f"is off-ruler. Prefer the ★ canonical bundle unless you know why you are "
            f"overriding. (Serving: letterbox-pad to {rrc[0]}×{rrc[1]}, no crop.)"
        )

    if st.session_state.dev_mode:
        with st.expander("Developer info — bundle JSON + path", expanded=False):
            st.code(f"pkl : {chosen.pkl_path}\njson: {chosen.json_path}", language="text")
            st.json({
                "modalities": list(chosen.modalities),
                "stats_mean": list(chosen.stats_mean),
                "stats_std": list(chosen.stats_std),
                "resize_to": list(chosen.resize_to),
                "rrc_size": list(chosen.rrc_size),
                "source": chosen.source,
                "fold": chosen.fold,
                "arch": chosen.arch,
                "encoder": chosen.encoder,
            })

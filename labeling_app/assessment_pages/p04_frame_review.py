"""Phase 4 — Frame review + 3-stage human gates.

For each picked frame:
  1. Run segmenter (cached after first pass).
  2. Show side-by-side: raw frame + overlay (with eroded contour drawn).
  3. Three sequential gates (Yes/No):
        a. Usable?
        b. Segmentation perfect?  (only shown if Usable=Yes)
        c. Use for analysis?       (only shown if Usable=Yes)

Once user clicks "Lock gates and continue", the picked-frame manifest with
gates persists into st.session_state.gates and we advance to assessment.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import streamlit as st
import torch
from PIL import Image

from preflight_helpers import env
from preflight_helpers.post_processing import post_process_rf
from preflight_helpers.segmenter import (
    LABEL_RF, build_voi_hist_clahe_stack, predict_mask, synthesize_final_u8,
)

from labeling_app.assessment_pages.session_sig import current_masks_sig
from labeling_app.assessment_pages.measurement_overlay import annotate_overlay
from labeling_app.assessment_pages import qa_surfacing, qmt_review
from labeling_app.assessment_pages import p4x_calibration_capture
from labeling_app.measurements import IMAGE_QUALITY_FLAG_PRIORITY
from labeling_app import repr_scorer
from labeling_app.visualization import (
    compose_layer_stack, confidence_heatmap_overlay, confidence_stats,
    load_label_palette, overlay_on_image, post_process_mask_for_display,
)
from preflight_helpers.feature_compute import compute_image_quality_features


def _transport_dir_for_entry(entry) -> Path:
    suffix = Path(*entry.study_path.parts[-3:])
    return env.TRANSPORT_ROOT / suffix


def _ensure_predicted_masks(picked: list[str], raw_dir: Optional[Path] = None) -> None:
    """Run segmenter for any picked frame not yet in session cache.

    `raw_dir` defaults to the sweep (the discoverable primary); the per-modality review passes the
    femur / center-RF dir so those picks segment from their own captures/ folder. Masks + finals
    are cached by frame_base, and the three modalities have distinct timestamps, so they coexist.

    Uses goruntuler's canonical methodology live per frame: synthesise the
    "final" modality from the bare .jpg + `_raw.png` size (mirrors
    `goruntuler/scripts/build_transport_dataset.py:_write_final_png`), then
    build voi/hist/clahe via the same functions in
    `goruntuler/rf_png_channel_utils.py` that produced the training set.
    No dependency on transport-side pre-computed PNGs — works for any of the
    sweep's 100+ frames, not just the cohort 3-frame triples.
    """
    if st.session_state.learn is None or st.session_state.bundle_meta is None:
        st.error("Model bundle not loaded; revisit phase 2.")
        return
    entry = st.session_state.study_entry
    if raw_dir is None:
        raw_dir = entry.raw_image_dir if entry else None
    if raw_dir is None:
        st.error("No RawImagesForResearch_ directory on this study.")
        return

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    # Stale-cache guard (N4): the predicted-mask cache is keyed on the picked
    # frame bases + the model bundle. If either changed since the cache was
    # filled (the user went back to phase 3 and re-picked, or swapped the
    # model), the cached masks are for a different input set — drop them and
    # the live-`final` cache before recomputing. Gates are NOT in this
    # signature: a gate change never changes a predicted mask. The
    # confidence-map cache (chunk 5 — segmentation-confidence heatmap) rides
    # in the same staleness regime: same model + same frames → same maps.
    masks_sig = current_masks_sig()
    if st.session_state.get("masks_input_sig") != masks_sig:
        st.session_state.predicted_masks = {}
        st.session_state.final_u8_cache = {}
        st.session_state.confidence_map_cache = {}
        st.session_state.masks_input_sig = masks_sig

    cache: dict[str, np.ndarray] = st.session_state.predicted_masks
    final_cache: dict[str, np.ndarray] = st.session_state.setdefault("final_u8_cache", {})
    confidence_cache: dict[str, np.ndarray] = st.session_state.setdefault(
        "confidence_map_cache", {})
    needed = [fb for fb in picked if fb not in cache]
    if not needed:
        return
    bm = st.session_state.bundle_meta
    progress = st.progress(0.0, text=f"Running segmenter on {len(needed)} frame(s)…")
    for i, fb in enumerate(needed):
        if fb not in final_cache:
            final_cache[fb] = synthesize_final_u8(raw_dir, fb)
        stacked = build_voi_hist_clahe_stack(final_cache[fb], modalities=bm.modalities)
        # `predict_mask`'s `confidence_out` populates a dict in-place with the
        # per-pixel confidence map (`confidence_map_native`) at mask
        # resolution. We capture it once per frame so the p05 heatmap (chunk
        # 5) can read it; segmentation cost is unchanged because the model
        # already runs once.
        conf_buf: dict = {}
        pred = predict_mask(st.session_state.learn, stacked, bm,
                             device=device, confidence_out=conf_buf)
        cache[fb] = pred
        if "confidence_map_native" in conf_buf:
            confidence_cache[fb] = conf_buf["confidence_map_native"]
        progress.progress((i + 1) / len(needed), text=f"Segmented {fb}")
    progress.empty()
    st.session_state.predicted_masks = cache


def _render_segmentation_arr(fb: str, pred_mask: np.ndarray,
                             with_measurements: bool = True,
                             modality: Optional[str] = None) -> np.ndarray:
    """Build Layer 1 — the bundled segmentation overlay (colour + magenta eroded
    RF contour, plus the geometry annotations when `with_measurements`) as a
    single (H, W, 3) uint8 array. Caller decides whether to display it via
    `compose_layer_stack`.

    `with_measurements` gates the whole geometry layer. `modality` then selects
    WHICH measurements draw, so the overlay matches what that view measures
    (Akadir 2026-07-14 — all measurements from the femur, texture from center-RF):
      * ``"femur"`` → ALL calipers (QMT + RF thickness + q50 + fat) — the femur
                      cine supplies every measurement;
      * ``"rf"``    → NO calipers — the center-RF cine supplies only echo texture
                      (int_cv), which has no caliper; the magenta RF contour shows
                      where the echo is read;
      * ``None``    → all four (the legacy single-sweep overlay).
    This mirrors the feature router (FEMUR_FEATURES vs CENTER_RF_FEATURES): the drawn
    measurement equals what the score actually reads from that view.

    The mask cleanup (`post_process_mask_for_display`) runs here so the
    coloured overlay matches the QMT measurement code's view (Akadir
    2026-05-23 — see `8fc0094`). The change_map is returned separately so
    Layer 3 can render it.
    """
    from PIL import Image as _Image
    final_u8 = st.session_state.final_u8_cache[fb]
    img = _Image.fromarray(final_u8).convert("RGB")
    palette = load_label_palette(env.PALETTE_PATH)
    cleaned_pred, _change_map = post_process_mask_for_display(pred_mask)
    overlay = overlay_on_image(img, cleaned_pred, palette, alpha=0.5)
    rf_pair = post_process_rf(pred_mask, rf_class_id=LABEL_RF, erosion_px=3)
    contour = rf_pair.rf_full_mask ^ rf_pair.rf_eroded_mask
    arr = np.asarray(overlay).copy()
    arr[contour] = [255, 0, 255]                            # magenta contour
    if with_measurements:
        # Per-VIEW measurement display (Akadir 2026-07-14): the femur pick draws ALL calipers (it
        # supplies every measurement); the center-RF pick draws NONE (it feeds only echo texture, which
        # has no caliper). A legacy sweep pick (modality=None) keeps all four. So the drawn marks match
        # what each view feeds the routed score (feature_routing FEMUR_FEATURES / CENTER_RF_FEATURES).
        if modality == "femur":
            # Femur cine supplies ALL measurements (Akadir 2026-07-14) → draw every caliper.
            arr = annotate_overlay(arr, pred_mask, rf_pair.rf_eroded_mask,
                                   show_qmt=True, show_rf=True, show_q50=True, show_fat=True)
        elif modality == "rf":
            # Center-RF cine supplies ONLY texture (int_cv) → no calipers; the magenta RF contour
            # (drawn above) shows the boundary the echo is read inside.
            arr = annotate_overlay(arr, pred_mask, rf_pair.rf_eroded_mask,
                                   show_qmt=False, show_rf=False, show_q50=False, show_fat=False)
        else:
            arr = annotate_overlay(arr, pred_mask, rf_pair.rf_eroded_mask)
    return arr


def _raw_arr_for(fb: str) -> Optional[np.ndarray]:
    """The Layer-0 raw image as (H, W, 3) uint8. Falls back to None when the
    live-`final` cache is somehow not populated (Layer-stack composition
    skips Layer 0 then)."""
    final_u8 = st.session_state.get("final_u8_cache", {}).get(fb)
    if final_u8 is None:
        return None
    rgb = np.stack([final_u8, final_u8, final_u8], axis=-1).astype(np.uint8)
    return rgb


def _repr_gate_status(qmt_statuses: dict[str, dict]) -> tuple[bool, str]:
    """Akadir 2026-05-24 § 1g + § 3a — the representative-frame constraint:

      - There must BE a repr designated.
      - The repr must be a picked frame (else stale state).
      - If any picked frame has femur (QMT tier 1 or 2), the repr MUST be one
        of those — a no-femur frame is structurally a worse repr because the
        anchor-frame overlay on p05 is what the operator publishes as the
        canonical picture, and a no-femur frame loses QMT + STAR.
      - Fallback: if NO picked frame has femur, allow any picked frame as
        repr — the run becomes a no-QMT/no-STAR run, which is still salvageable
        but the operator should know.

    Returns (ok, message). `ok=False` disables the p04→p05 lock button and
    `message` is shown next to it.
    """
    picked = list(st.session_state.get("picked_frames") or [])
    anchor = st.session_state.get("anchor_frame")
    if not picked:
        return False, "No frames picked — go back to phase 3."
    if anchor is None:
        return False, ("No representative frame designated. "
                       "Use the **★ Make this the repr** button on a frame.")
    if anchor not in picked:
        return False, (
            f"Representative frame `{anchor}` is not in the picked set — "
            "designate a repr from the picked frames.")
    # Femur-valid set (Tier 1 or 2 — femur present, even if proxy).
    femur_picked = [fb for fb in picked
                    if qmt_statuses.get(fb, {}).get("tier") in (1, 2)]
    if femur_picked and anchor not in femur_picked:
        return False, (
            f"Representative frame `{anchor}` has no femur, but a femur-valid "
            f"picked frame exists. Designate one of the femur-valid frames as "
            f"the repr: {', '.join(femur_picked)}.")
    # Either anchor is femur-valid (preferred) or no femur-valid picked exists
    # (fallback — no-QMT/no-STAR run, but allowed).
    if not femur_picked:
        return True, ("No femur visible in any picked frame — the run will "
                      "produce **no QMT and no STAR**. Anchor is allowed for "
                      "any picked frame.")
    return True, ""


def _per_measurement_attribution_panel(
    fb: str, pred_mask: Optional[np.ndarray], qmt_status: Optional[dict],
) -> None:
    """Akadir 2026-05-24 § 5b — per-measurement attribution panel.

    For each gating measurement, one row naming the calibrated flag(s) that
    drove ITS tier — not the dominant flag attributed to the union. So
    "Visible RF area: muscle off side" sits in its own row; "STAR + QMT: no
    femur" in another; "RF thickness: clipped vertically" in another. No
    more chained misattribution.

    Runs the image-quality detectors per frame (mask-only — no GPU): same
    code path the assessment uses, but the frame's `image_quality` payload
    is computed live here so the panel can show the per-measurement story
    BEFORE the run is locked. Pending (un-calibrated) flags appear in a
    separate sub-section per the Akadir 2026-05-24 reframe — they do not
    drive tier and they generate noise on the assessment page.
    """
    if pred_mask is None:
        return
    final_u8 = st.session_state.get("final_u8_cache", {}).get(fb)
    if final_u8 is None:
        return
    # Compute the frame's QA grade. `spacing_row={}` and `boundary_entropy=None`
    # match how femur_scan computes the picker grade — same data the assessment
    # joins onto frame_features.
    iq = compute_image_quality_features(
        frame_base=fb,
        multiclass_mask=pred_mask,
        final_image_u8=final_u8,
        spacing_row={},
        boundary_entropy=None,
    ).to_dict()
    rows = qa_surfacing.per_measurement_attribution(iq)
    # Stats — n_invalid, n_borderline, n_pending — at a glance.
    n_invalid = sum(1 for r in rows if r["tier"] == "invalid")
    n_borderline = sum(1 for r in rows if r["tier"] == "borderline")
    pending = iq.get("image_quality_pending_calibration_flags") or ()
    label = f"Image-quality detail — {n_invalid} invalid, {n_borderline} borderline"
    if pending:
        label += f", {len(pending)} pending"
    with st.expander(label, expanded=(n_invalid + n_borderline) > 0):
        # One row per measurement. Silent on valid (no noise on clean frames).
        any_shown = False
        for r in rows:
            if r["tier"] == "valid" or not r["drivers"]:
                continue
            any_shown = True
            tier_label = {"invalid": "❌ invalid",
                          "borderline": "⚠ borderline",
                          "ungraded": "— ungraded"}[r["tier"]]
            drivers_text = "; ".join(d["prose"] for d in r["drivers"])
            st.markdown(f"- **{r['measurement_label']}** — {tier_label}: {drivers_text}")
        if not any_shown:
            st.caption(":green[All gating measurements valid for this frame.]")
        # Pending (un-calibrated) sub-section — surfaced, not gating. The
        # operator's keep / set-aside gate below IS the reaction: at lock time
        # `_capture_selection_labels` records it as a calibration label
        # (label_source=selection), so these "still tuning" checks finally
        # accumulate the agree/disagree evidence they need (Akadir 2026-05-31).
        if pending:
            st.markdown(
                ":gray[**Possible issues — extra checks the app is still tuning:**]")
            for f in pending:
                st.markdown(f"  - {qa_surfacing.flag_prose(f)}")
            st.caption(":gray[Your keep / set-aside decision on this frame "
                       "trains these checks — it never blocks your choice.]")


def _capture_selection_labels() -> None:
    """At lock time, turn the operator's keep / set-aside decisions into
    calibration labels (`label_source=selection`) for the surfaceable detectors
    that fired — the weak-supervision channel the per-detector set was starved
    of. Clean signals only (`label_store.selection_label_for`): set-aside + the
    detector cited -> 'yes'; kept-for-analysis -> 'no'; the ambiguous middle is
    skipped. Idempotent within a session via `_selection_captured`. Writes to
    the ledger `label_store` resolves from env.DATA_ROOT (tmp-redirected under
    test), PHI-clean (study_code + frame_base only).
    """
    from calibration.label_store import record_selection_label, selection_label_for
    picked = list(st.session_state.get("picked_frames") or [])
    masks = st.session_state.get("predicted_masks") or {}
    finals = st.session_state.get("final_u8_cache") or {}
    gates = st.session_state.get("gates") or {}
    entry = st.session_state.get("study_entry")
    study_code = entry.meta.study_code if entry is not None else "?"
    captured = st.session_state.setdefault("_selection_captured", set())
    for fb in picked:
        pred = masks.get(fb)
        final_u8 = finals.get(fb)
        if pred is None or final_u8 is None:
            continue
        iq = compute_image_quality_features(
            frame_base=fb, multiclass_mask=pred, final_image_u8=final_u8,
            spacing_row={}, boundary_entropy=None).to_dict()
        fired = [c["detector"] for c in qa_surfacing.frame_checks(iq)["fired"]]
        if not fired:
            continue
        g = gates.get(fb, {})
        usable = g.get("usable")
        use_for_analysis = g.get("use_for_analysis")
        cited = set(g.get("drop_reason_codes") or set())
        for det in fired:
            if (fb, det) in captured:
                continue
            label = selection_label_for(
                usable=usable, use_for_analysis=use_for_analysis,
                detector_cited=det in cited)
            if label is None:
                continue
            record_selection_label(study_code=study_code, frame_base=fb,
                                   detector=det, label=label, image_quality=iq)
            captured.add((fb, det))


# Dark-image labeling lives in p4x (the per-detector calibration surface), NOT on
# this review page. The old bottom-of-page "Dark-image check" questionnaire was
# removed 2026-06-09: it was stranded at the page end, identified frames by bare
# codes the operator couldn't match to an image, and duplicated p4x — and the
# same coupling caused the StreamlitAPIException crash + the gate-revert. The
# per-frame under-gain cue still shows inline (qa_surfacing attribution); the
# dark_clip_high calibration label is captured from the operator's keep/drop gate
# decision at lock (_capture_selection_labels), the same as every other detector.


def _make_repr_button(fb: str, status: Optional[dict],
                       qmt_statuses: Optional[dict] = None,
                       ai_suggested: Optional[str] = None) -> None:
    """Akadir 2026-05-24 § 1g + § 3a — the repr designation moves to p04.

    Renders a "★ Make this the repr" button per frame. The repr is the frame
    whose anchor-frame overlay p05 publishes as the canonical picture, so this
    is exactly where the operator should designate it — on the review page,
    with the big image + the QMT status + the per-measurement attribution
    already on screen. The state is the existing `st.session_state.anchor_frame`
    (same key the picker used to set); the gate at the bottom of this page
    blocks navigation to p05 if it is not set or not valid.

    Three visual states, label only — no key swap, AppTest sees one stable
    widget per frame:
      - already the repr  → button disabled, label "★ this is the repr"
      - not the repr      → button active,   label "★ Make this the repr"
      - QMT tier 3 + a femur-valid picked frame exists → button disabled,
        label "★ this frame has no femur — pick a femur-valid one"

    The QMT-tier-3 lockout enforces the femur-valid constraint at the point
    of action, not just at the lock-button level — the operator sees WHY this
    frame is not eligible.
    """
    # AI suggestion + WHY (Slice F advisory) — surfaced on the matching frame so
    # the operator's repr designation is informed by the score's reasoning. C/K
    # recompute from the cached mask (per-frame, cheap); R is the sweep-relative
    # score carried from the p03 scan.
    if ai_suggested is not None and fb == ai_suggested:
        pred = (st.session_state.get("predicted_masks") or {}).get(fb)
        why = ""
        if pred is not None:
            c = repr_scorer.completeness(pred)
            k = repr_scorer.centering(pred)
            r = float(((st.session_state.get("femur_scan") or {}).get(fb) or {})
                      .get("repr_score") or 0.0)
            comp = {1.0: "complete cross-section", 0.5: "one lateral edge clipped",
                    0.0: "both lateral edges clipped"}.get(c, f"completeness {c:.2f}")
            why = (f"  \n:gray[Why: most representative of the picked frames — "
                   f"centred (K={k:.2f}), {comp} (C={c:.2f}); score R={r:.2f}. Advisory.]")
        st.markdown(f":blue[**✦ AI suggests this frame as the representative**]{why}")
    current = st.session_state.get("anchor_frame")
    is_repr = (current == fb)
    picked = list(st.session_state.get("picked_frames") or [])
    # Compute the femur-eligible picked set from the latest qmt_statuses
    # already stashed on session state by the render loop. We approximate from
    # `status` for this frame + a forward reference to the cached statuses for
    # peer frames; the lock-time gate is the load-bearing check.
    eligible_peers_exist = bool(picked)  # cheap: render-time peer info not yet
                                          # computed here, see the lock check.
    no_femur = (status is not None and status.get("tier") == 3)
    # The no-femur lockout only applies when at least ONE peer frame DOES
    # have femur (tier 1 or 2). If every picked frame is tier 3, the run
    # fallback kicks in (no-QMT/no-STAR run) and any picked frame is a
    # legitimate repr — so the button must remain clickable.
    femur_valid_peer_exists = False
    if qmt_statuses:
        femur_valid_peer_exists = any(
            (qmt_statuses.get(peer) or {}).get("tier") in (1, 2)
            for peer in (st.session_state.get("picked_frames") or [])
        )
    # Akadir 2026-05-24 § 5 — the confirmed-repr state was rendering as a
    # disabled grey button that read like FAILURE ("I think I selected it
    # but, it shouldn't just grey out if selected and approved"). The
    # success signal needs to be unambiguous. Render the confirmed state
    # as a green markdown badge + an inactive button below; the
    # not-yet-repr state stays as a primary clickable button.
    if is_repr:
        st.markdown(":green[**★ This frame IS the representative frame ✓**]")
        st.button("✓ confirmed as repr (click another frame's button to change)",
                  key=f"make_repr_{fb}", disabled=True,
                  use_container_width=True)
        return
    if no_femur and femur_valid_peer_exists:
        st.button(
            "★ this frame has no femur — designate a femur-valid one",
            key=f"make_repr_{fb}", disabled=True,
            use_container_width=True,
        )
        return
    if st.button("★ Make this the repr", key=f"make_repr_{fb}",
                 type="primary"):
        st.session_state.anchor_frame = fb
        # Akadir 2026-05-25 § 6 — repr designation auto-couples to
        # usable=Yes + use_for_analysis=Yes. A frame designated as repr
        # but excluded from analysis is incoherent; the coupling makes
        # that state impossible to enter accidentally. The operator can
        # still flip the radios manually after — _gate_block surfaces a
        # non-blocking warning if they do. "Segmentation perfect?" stays
        # independent — that's a quality judgment unrelated to repr-ness.
        gates = st.session_state.setdefault("gates", {})
        g = gates.setdefault(fb, {
            "usable": None,
            "segmentation_perfect": None,
            "use_for_analysis": None,
            "drop_reason_codes": set(),
            "drop_reason_other": "",
            "qa_overrides": {},
        })
        g["usable"] = True
        g["use_for_analysis"] = True
        # Akadir 2026-05-25 v2 § 4 — segmentation_perfect ALSO auto-sets, to
        # the realistic default `No`. The earlier v4 cut cleared this to
        # `None` (the unanswered "—" state), which then BLOCKED lock-gates
        # because gate 2 was unanswered. Setting `No` here matches Akadir's
        # explicit guidance ("useable → yes, segmentation perfect → no, use
        # for analysis → yes, should come autoselected as default"). Perfect
        # segmentation is rare; the operator flips to Yes only when they
        # actually judge the segmentation pixel-perfect.
        g["segmentation_perfect"] = False
        # Designating a previously-dropped frame as repr clears the drop —
        # the new repr is by definition not dropped.
        g["drop_reason_codes"] = set()
        g["drop_reason_other"] = ""
        # Sync the widget-state keys so the radios reflect the new gates on
        # rerun. A radio whose session_state key already exists (the operator
        # touched it before make-repr) would otherwise override the canonical
        # dict on re-render.
        st.session_state[f"g1_{fb}"] = "Yes"
        st.session_state[f"g2_{fb}"] = "No"
        st.session_state[f"g3_{fb}"] = "Yes"
        st.rerun()


def _structured_drop_reasons(
    fb: str, prefill_flags: tuple[str, ...] = (),
) -> tuple[set[str], str]:
    """Structured drop-reason capture for a dropped frame (plan § 8.3.5).

    Renders the IMAGE_QUALITY_FLAG_PRIORITY taxonomy as a checkbox group plus a
    free-text "Other" field; returns (checked reason codes, other text). This
    is the Build-Step-1 label-capture instrument — the structured "why" that
    replaces the old free-text drop-reason box and feeds the § 11 calibration
    substrate.

    `prefill_flags` is the algorithmic-flag pre-fill seam (§ 7.4(b)). It is
    empty in Build-Step 1 — no detectors exist yet — so every box starts
    unchecked and the operator labels by hand, which is exactly the
    pre-fill-free label the § 11.2 calibration wants. Build-Step 2 passes the
    real detector flags through this parameter.
    """
    g = st.session_state.gates.get(fb, {})
    existing = g.get("drop_reason_codes", set())
    if not isinstance(existing, set):
        existing = set(existing or ())

    st.markdown(
        ":red[**Why is this frame being dropped?**] — check every "
        "image-quality problem you can see."
    )
    checked: set[str] = set()

    # Akadir 2026-06-01 — plain-language labels (flag_prose, never raw codes),
    # grouped under the taxonomy's headers; the 7 judgment-only reasons (no auto
    # detector) tucked behind an expander. Keys unchanged (`drc_{fb}__{code}`),
    # so the captured `drop_reason_codes` are still the raw codes.
    def _box(code: str) -> None:
        if st.checkbox(
            qa_surfacing.flag_prose(code),
            value=(code in existing) or (code in prefill_flags),
            key=f"drc_{fb}__{code}",
        ):
            checked.add(code)

    for header, codes in qa_surfacing.DROP_REASON_GROUPS:
        st.caption(f"**{header}**")
        for code in codes:
            _box(code)
    with st.expander("More — judgment-only reasons (no automatic check)",
                     expanded=False):
        for code in qa_surfacing.OPERATOR_ONLY_DROP_REASONS:
            _box(code)

    other = st.text_input(
        "Other — a reason not listed (grows the taxonomy)",
        value=g.get("drop_reason_other", ""),
        key=f"dro_{fb}",
    )
    return checked, other


def _gate_block(fb: str, idx: int, store: str = "gates") -> dict:
    """Render the 3-gate Yes/No sequence + structured drop-reason capture; returns the gates dict.
    `store` is the session_state dict the gates live in: the sweep uses "gates" (unchanged); the v8
    per-modality review passes "gates__femur"/"gates__rf" so each modality's keep/drop is kept
    separate and read by feature_routing.route_study_aggregate. Widget keys stay keyed by the
    (globally-unique, timestamped) frame_base, so the sweep keys are byte-identical."""
    gates = st.session_state.setdefault(store, {}).setdefault(fb, {
        "usable": None, "segmentation_perfect": None, "use_for_analysis": None,
        "drop_reason_codes": set(),   # Build-Step 1 — structured reason codes
        "drop_reason_other": "",      # Build-Step 1 — free text, taxonomy growth
        "qa_overrides": {},           # § 7.6 — populated from Build-Step 2
    })
    # Forward-compat: a gates dict hydrated from a pre-QA ledger run (via
    # previous_runs_sidebar) lacks the structured-capture keys, and JSON has no
    # sets — backfill + normalise so the rest of this function sees v0.2 shape.
    gates.setdefault("drop_reason_codes", set())
    gates.setdefault("drop_reason_other", "")
    gates.setdefault("qa_overrides", {})
    if not isinstance(gates["drop_reason_codes"], set):
        gates["drop_reason_codes"] = set(gates["drop_reason_codes"] or ())
    # Akadir 2026-05-25 v3 § 1 — the gate radios are seeded via `setdefault`
    # on session_state and rendered WITHOUT `index=`. This dodges the
    # Streamlit warning "widget created with default value but also had its
    # value set via Session State API" that fires when the make-repr handler
    # programmatically sets g1/g2/g3 while the radios pass `index=`. The
    # setdefault seeds session_state[key] from the gates dict on first
    # render; subsequent reruns read session_state directly; programmatic
    # setters (make-repr) update session_state freely.
    def _seed(value) -> str:
        if value is True: return "Yes"
        if value is False: return "No"
        return "—"

    cols = st.columns([1, 1, 1, 2])
    with cols[0]:
        st.session_state.setdefault(f"g1_{fb}", _seed(gates["usable"]))
        usable = st.radio(
            "Usable?", ["—", "Yes", "No"],
            key=f"g1_{fb}", horizontal=False,
        )
        gates["usable"] = True if usable == "Yes" else (False if usable == "No" else None)

    if gates["usable"] is True:
        with cols[1]:
            st.session_state.setdefault(
                f"g2_{fb}", _seed(gates["segmentation_perfect"]))
            perf = st.radio(
                "Segmentation perfect?", ["—", "Yes", "No"],
                key=f"g2_{fb}",
            )
            gates["segmentation_perfect"] = (
                True if perf == "Yes" else False if perf == "No" else None
            )
        with cols[2]:
            st.session_state.setdefault(
                f"g3_{fb}", _seed(gates["use_for_analysis"]))
            use = st.radio(
                "Use for analysis?", ["—", "Yes", "No"],
                key=f"g3_{fb}",
            )
            gates["use_for_analysis"] = (
                True if use == "Yes" else False if use == "No" else None
            )
    elif gates["usable"] is False:
        # Explicitly dropped → neither downstream gate applies.
        gates["segmentation_perfect"] = False
        gates["use_for_analysis"] = False
    else:
        # Akadir 2026-05-25 v3 § 1 (side issue) — cascade defaults when
        # usable is still unanswered: segmentation_perfect default No,
        # use_for_analysis default Yes ("making a frame useful inherently
        # assumes its usage within analysis, therefore this default, which
        # is still overrideable by the user later"). When the operator
        # flips usable to Yes, the g2/g3 widgets render with these defaults
        # already in the gates dict, so the radios show No / Yes from the
        # first frame — no extra clicks needed to unblock lock-gates.
        gates["segmentation_perfect"] = False
        gates["use_for_analysis"] = True

    # Akadir 2026-05-25 § 6 — surface the incoherent state when a repr frame
    # is excluded from analysis. Make-repr auto-sets use_for_analysis=Yes,
    # but the operator can still flip it manually; if they do, this warning
    # tells them the state is usually inconsistent and offers the two fixes
    # (re-include this frame, or designate a different repr).
    if (gates["use_for_analysis"] is False
            and fb == st.session_state.get("anchor_frame")):
        st.warning(
            ":warning: This frame is the **designated representative** but "
            "excluded from analysis — usually inconsistent. Either re-include "
            "it (Usable = Yes and Use-for-analysis = Yes above) or designate "
            "a different frame as repr."
        )

    # A dropped frame (usable = No) gets the structured "why" — the
    # Build-Step-1 capture instrument (§ 7.4 / § 8.3.5), rendered full-width
    # below the gate row. A still-pending frame (usable = —) shows nothing yet.
    if gates["usable"] is False:
        codes, other = _structured_drop_reasons(fb)
        gates["drop_reason_codes"] = codes
        gates["drop_reason_other"] = other

    return gates


# The per-modality gate stores. A legacy single-sweep study only ever fills
# `picked_frames`/`gates`; a v8 3-modality study also fills the femur + center-RF
# stores (`_modality_review_section`). The summary + completeness check aggregate
# across all three, so the lock gate sees every modality's kept frames — not just
# the sweep's (Akadir 2026-06-26 audit, #6: the legacy sweep was the mandatory
# spine). The empty femur/RF loops make this a no-op on a legacy study.
_GATE_STORES = (("picked_frames", "gates"),
                ("picked_frames__femur", "gates__femur"),
                ("picked_frames__rf", "gates__rf"))


def _gate_summary() -> dict:
    """Count gate states across the picked frames of every modality (sweep +
    femur + center-RF)."""
    n = {"usable": 0, "perfect": 0, "for_analysis": 0, "dropped": 0, "pending": 0}
    for picks_key, store in _GATE_STORES:
        picked = st.session_state.get(picks_key, []) or []
        gates = st.session_state.get(store, {}) or {}
        for fb in picked:
            g = gates.get(fb, {})
            u = g.get("usable")
            if u is True:
                n["usable"] += 1
                n["perfect"] += int(g.get("segmentation_perfect") is True)
                n["for_analysis"] += int(g.get("use_for_analysis") is True)
            elif u is False:
                n["dropped"] += 1
            else:
                n["pending"] += 1
    return n


def _gate_completeness_issues() -> list[str]:
    """Reasons the run is not yet ready to lock — an empty list means ready.

    Build-Step 1 enforces two completeness rules before "Lock gates":
      * every picked frame is decided Yes/No on every gate it was shown — no
        gate left on the "—" pending state (no stale, un-made decisions);
      * every frame gated Usable=No carries a drop reason — at least one
        structured code, or free text in "Other". A frame dropped with no
        recorded "why" is the exact gap Build-Step 1 closes (plan § 15.2);
        leaving the capture optional would silently re-open it.
    """
    issues: list[str] = []
    # Akadir 2026-05-25 v2 § 3a — render the frame id using the same short
    # form the picker uses (HHMMSS.XXX). The full frame_base
    # (20260127_151045432) is the storage key, not an operator-facing label;
    # showing it in the finish-gating bullet breaks the visual link to the
    # thumbnails the operator was just working with. Aggregated across modalities
    # so an undecided femur/RF pick also blocks the lock (Akadir 2026-06-26, #6).
    for picks_key, store in _GATE_STORES:
        picked = st.session_state.get(picks_key, []) or []
        gates = st.session_state.get(store, {}) or {}
        for fb in picked:
            label = f"`{qa_surfacing._short(fb)}`"
            g = gates.get(fb, {})
            usable = g.get("usable")
            if usable is None:
                issues.append(f"{label} — gate 1 (usable?) is unanswered")
                continue
            if usable is True:
                if g.get("segmentation_perfect") is None:
                    issues.append(f"{label} — gate 2 (segmentation perfect?) is unanswered")
                if g.get("use_for_analysis") is None:
                    issues.append(f"{label} — gate 3 (use for analysis?) is unanswered")
            else:  # usable is False — a dropped frame must record why
                codes = g.get("drop_reason_codes") or ()
                other = (g.get("drop_reason_other") or "").strip()
                if not codes and not other:
                    issues.append(
                        f'{label} — dropped with no reason: check a reason code '
                        f'or fill in "Other"'
                    )
    return issues


def render_sidebar() -> None:
    """Stage-modular sidebar (F3) — phase 4: the live gate summary, so the
    operator sees usable / dropped / analysis-included counts without
    scrolling the (potentially long) per-frame review list."""
    from labeling_app.assessment_pages import help_content
    st.markdown("### ④ Review + gate")
    entry = st.session_state.get("study_entry")
    if entry is None or st.session_state.get("learn") is None:
        st.caption("Earlier phases not complete.")
        help_content.render_help_expander(3)
        return
    picked = st.session_state.get("picked_frames", []) or []
    st.caption(f"{len(picked)} frame(s) picked for review.")
    n = _gate_summary()
    st.markdown(
        f"- usable **{n['usable']}**\n"
        f"- dropped **{n['dropped']}**\n"
        f"- training-candidate (perfect) **{n['perfect']}**\n"
        f"- analysis-included **{n['for_analysis']}**\n"
        f"- gates pending **{n['pending']}**"
    )
    if n["for_analysis"] == 0:
        st.caption(":gray[Mark ≥1 frame use-for-analysis=Yes to unlock the assessment.]")
    help_content.render_help_expander(3)


def _modality_review_section() -> None:
    """v8 per-modality review (Akadir 2026-06-25): segment + show the FEMUR and CENTER-RF picks so
    the operator validates THEIR segmentation before scoring — geometry reads the femur, echo-
    intensity the center-RF. These picks feed the score (the router merges them over the sweep base),
    and each frame has a keep/drop gate — only frames kept usable + for-analysis are medianed into the
    routed aggregate. A legacy single-modality study has no femur/RF captures, so this renders nothing."""
    entry = st.session_state.study_entry
    from preflight_helpers.study_discovery import study_modalities
    mods = study_modalities(entry.study_path)
    roles = [r for r in ("femur", "rf") if r in mods]
    if not roles:
        return
    _label = {"femur": "Femur (measurements)", "rf": "Center-RF (echo intensity)"}
    st.markdown("##### Per-modality review — validate the segmentation")
    st.caption("Validate each femur (thickness) and center-RF (echo texture) pick, then keep or drop "
               "it below — only the frames you keep usable feed the score (geometry from the femur, "
               "echo texture from the center-RF).")
    for role in roles:
        picks = st.session_state.get(f"picked_frames__{role}") or []
        with st.expander(f"{_label[role]} — {len(picks)} pick(s)", expanded=True):
            if not picks:
                st.caption("No frames picked in this tab (phase 3).")
                continue
            _ensure_predicted_masks(picks, raw_dir=mods[role].raw_image_dir)
            for i, fb in enumerate(picks):
                cols = st.columns([1, 1])
                with cols[0]:
                    final_u8 = st.session_state.get("final_u8_cache", {}).get(fb)
                    if final_u8 is not None:
                        st.image(final_u8, caption=f"Raw — {fb}", width="stretch")
                    else:
                        st.warning(f"No image for {fb}")
                with cols[1]:
                    pred = st.session_state.predicted_masks.get(fb)
                    if pred is None:
                        st.warning("No predicted mask")
                    else:
                        # Per-VIEW measurements (Akadir 2026-07-14): the femur pick shows ALL calipers
                        # (it supplies every measurement); the center-RF pick shows NONE (it feeds only
                        # echo texture, which has no caliper). Driven by `modality=role` so the drawn marks
                        # match what the router reads from each view.
                        st.image(_render_segmentation_arr(fb, pred, with_measurements=True, modality=role),
                                 caption=f"Segmentation — {fb}", width="stretch")
                        if role == "femur":
                            # The femur cine is the QMT source — surface its provenance tier
                            # HERE (where the caliper-to-bone QMT is actually read), not on the
                            # shallow sweep where it is vestigial (Akadir 2026-06-26 audit, #12).
                            _qs = qmt_review.frame_qmt_status(pred)
                            st.markdown(f":{_qs['colour']}[**QMT: {_qs['label']}**]  \n"
                                        f":gray[{_qs['meaning']}]")
                        else:
                            st.caption(":gray[Only the echo TEXTURE (int_cv / fascicular contrast) is "
                                       "read here — confirm the magenta outline tracks the RF muscle it "
                                       "is measured inside. Geometry + fat come from the femur.]")
                # Keep/drop gate — only frames kept usable + for-analysis feed the routed score
                # (Akadir 2026-06-25); stored per modality so route_study_aggregate reads them.
                _gate_block(fb, i, store=f"gates__{role}")
                st.divider()
    st.divider()


def _provisional_rf_decision() -> Optional[dict]:
    """#16: measure the femur + center-RF picks' MAX thickness from the already-predicted masks and
    stage `resolve_rf_source` — so the discrepancy gate can fire HERE at p04 (p05 is too late to react).
    Returns the decision dict, or None when either measurement view has no kept pick (then no gate; the
    definitive stamp is still recomputed in route_study_aggregate at lock). The sweep is omitted — it
    only refines the medium/low tier, not the femur-vs-center int_cv-trust decision the gate rides on.

    CRITICAL (red-team 2026-07-14): each view's thickness is the ANCHOR (middle kept pick) frame's value,
    NOT the median — because the definitive stamp goes through `aggregate_features`, where
    `full_max_thickness_mm` (an anchor_primary geometry feature) is the anchor frame's value. Measuring
    the same frame keeps this gate in lockstep with the stamp, so the operator never acts on a
    discrepancy the stamp then silently disagrees with (which could resurrect a withheld texture)."""
    from preflight_helpers.study_discovery import study_modalities
    from preflight_helpers.run_pipeline import _spacing_row_from_dicom
    from preflight_helpers.landmark import measure_frame_geometry
    from preflight_helpers.rf_reliability import resolve_rf_source
    from preflight_helpers.feature_routing import _gate_kept
    entry = st.session_state.study_entry
    mods = study_modalities(entry.study_path)
    masks = st.session_state.get("predicted_masks") or {}
    finals = st.session_state.get("final_u8_cache") or {}

    def _mod_max(role: str) -> Optional[float]:
        m = mods.get(role)
        kept = _gate_kept(st.session_state.get(f"picked_frames__{role}") or [],
                          st.session_state.get(f"gates__{role}") or {})
        if not m or not m.raw_image_dir or not kept:
            return None
        anchor_fb = kept[len(kept) // 2]                  # the anchor = middle kept pick (matches the stamp)
        mask = masks.get(anchor_fb)
        if mask is None:
            return None
        try:
            spacing = _spacing_row_from_dicom(m.raw_image_dir.parent, None)
        except Exception:
            spacing = {}
        spacing = dict(spacing)
        spacing["mask_height"], spacing["mask_width"] = mask.shape[:2]
        return measure_frame_geometry(mask, finals.get(anchor_fb), spacing).get("full_max_thickness_mm")

    mf, mc = _mod_max("femur"), _mod_max("rf")
    if mf is None or mc is None:
        return None
    return resolve_rf_source(mf, mc, None)


def _render_texture_region_overlay(fb: str, erosion_px: int, band: tuple) -> None:
    """The VISIBILITY that makes the gate mean something: draw the exact RF pixels int_cv reads (green)
    over a representative center-RF frame, so the operator SEES fascia bleeding into the region and can
    judge whether to refine. Fail-safe — a missing/mismatched mask just skips the overlay."""
    import numpy as np
    from PIL import Image
    from preflight_helpers.texture_refine import vetted_rf_region
    mask = (st.session_state.get("predicted_masks") or {}).get(fb)
    final = (st.session_state.get("final_u8_cache") or {}).get(fb)
    if mask is None or final is None:
        return
    try:
        region = vetted_rf_region(mask, erosion_px, band)
        img = np.asarray(Image.fromarray(final).convert("RGB")).copy()
        if region.shape == img.shape[:2] and region.any():
            img[region] = (0.45 * img[region] + 0.55 * np.array([0, 230, 0])).astype(np.uint8)
    except Exception:
        return
    st.image(img, caption=f"green = the RF pixels int_cv reads · erosion {erosion_px}px · "
                          f"depth {int(band[0] * 100)}–{int(band[1] * 100)}%", width="stretch")


def _preview_refined_int_cv(rf_kept: list, erosion_px: int, band: tuple):
    """Live int_cv over the kept center-RF picks' vetted regions (same helper the score uses at lock)."""
    from preflight_helpers.texture_refine import refined_int_cv
    masks = st.session_state.get("predicted_masks") or {}
    finals = st.session_state.get("final_u8_cache") or {}
    try:
        return refined_int_cv({fb: masks[fb] for fb in rf_kept if fb in masks}, finals, erosion_px, band)
    except Exception:
        return None


def _texture_review_panel() -> None:
    """Phase 2 (Akadir 2026-07-14): the RF-texture (int_cv) review — available on ANY routed study, not
    just the ~17% that disagree. int_cv reads the RF's clean interior (3px-eroded by default). Here the
    operator SEES that region over a center-RF frame and can REFINE it — erode more and/or keep a depth
    band (trim the contaminated deep edge) — with the refined int_cv previewed live, or WITHHOLD if no
    clean RF section is resolvable. On a femur-vs-center DISAGREEMENT the panel opens with an amber prompt
    (the '#16' triage signal); the 83% that agree see a collapsed panel they can still open. The refine
    is captured as {erosion_px, band} numbers (session `texture_refine`) — reproducible, no hand-drawn
    shape. RF geometry is unaffected (it comes from the femur)."""
    from preflight_helpers.study_discovery import study_modalities
    from preflight_helpers.feature_routing import _gate_kept
    from preflight_helpers.texture_refine import is_refined
    entry = st.session_state.study_entry
    if entry is None or "rf" not in study_modalities(entry.study_path):
        return
    rf_kept = _gate_kept(st.session_state.get("picked_frames__rf") or [],
                         st.session_state.get("gates__rf") or {})
    if not rf_kept:
        return
    try:
        dec = _provisional_rf_decision()
    except Exception:
        dec = None
    disagree = bool(dec and not dec.get("int_cv_trusted"))
    with st.expander("🔬 RF texture (int_cv) — the region read for fascicular contrast"
                     + ("  ⚠ views disagree — review" if disagree else ""), expanded=disagree):
        if disagree:
            st.caption(f":orange[{dec['note']} — the center-RF boundary is suspect, so is the texture "
                       "read inside it.]")
        st.caption(":gray[int_cv reads the RF's clean interior (3px-eroded by default). Refine to a "
                   "cleaner RF section if fascia is bleeding in, or withhold if none is resolvable. RF "
                   "geometry is unaffected — it comes from the femur.]")
        disp = st.radio(
            "Center-RF texture", options=["as-is", "refine", "withhold"],
            format_func=lambda c: {"as-is": "Use as-is (3px interior)",
                                   "refine": "Refine a clean section",
                                   "withhold": "Withhold — no clean section resolvable"}[c],
            index=None, key="tex_disposition", horizontal=True)
        erosion_px, band = 3, (0.0, 1.0)
        if disp == "refine":
            erosion_px = st.slider("Erosion — trim the boundary inward (px)", 3, 15,
                                   int((st.session_state.get("texture_refine") or {}).get("erosion_px", 3)),
                                   key="tex_ero")
            _b = st.slider("Keep RF depth band (0 = superficial/top · 100 = deep/bottom)", 0, 100,
                           (0, 100), key="tex_band")
            band = (_b[0] / 100.0, _b[1] / 100.0)
        _render_texture_region_overlay(rf_kept[len(rf_kept) // 2], erosion_px, band)   # VISIBILITY
        if disp != "withhold":
            _icv = _preview_refined_int_cv(rf_kept, erosion_px, band)
            if _icv is not None:
                _tag = " · refined from the 3px default" if is_refined(erosion_px, band) else ""
                st.caption(f":gray[**int_cv from this region: {_icv:.3f}**{_tag}]")
        # Derive the audit choice + store the (reproducible) refine params.
        st.session_state["texture_choice"] = {"as-is": "keep", "refine": "fix",
                                              "withhold": "withhold"}.get(disp)
        st.session_state["texture_refine"] = ({"erosion_px": erosion_px, "band": band}
                                              if (disp == "refine" and is_refined(erosion_px, band))
                                              else None)
        if disp is None and disagree:
            st.caption(":gray[Un-reviewed → the score still uses the 3px texture but FLAGS it. "
                       "Choose above to resolve.]")


def render() -> None:
    # p4x calibration capture is a sub-route of p04 (Akadir 2026-05-28) — when
    # active, the operator is in the per-detector labelling surface; the phase
    # stays 3 so the Display-layers sidebar keeps working. Exit returns here.
    if p4x_calibration_capture.is_active():
        p4x_calibration_capture.render()
        return

    if not (st.session_state.get("picked_frames")
            or st.session_state.get("picked_frames__femur")
            or st.session_state.get("picked_frames__rf")):
        st.info("Pick frames first (phase 3).")
        return
    if st.session_state.study_entry is None or st.session_state.learn is None:
        st.info("Earlier phases not complete.")
        return

    sm = st.session_state.study_entry.meta.to_ui_dict()
    st.subheader(f"Review + gate frames — {sm['study_code']}")

    _modality_review_section()
    _texture_review_panel()   # Phase 2: RF-texture review — refine (depth-band/erosion) / withhold, any study

    # Is this the new 3-modality protocol (femur/RF captures present)? On a
    # multimodal study the sweep is the texture-robustness / legacy-comparability
    # view — NOT a geometry source — so its review renders segmentation-only, the
    # QMT-tier / attribution / representative-frame machinery is suppressed (those
    # route from the femur cine now), and the lock gates on per-modality kept
    # frames, not a sweep repr (Akadir 2026-06-26 audit, #3/#4/#5/#11). A legacy
    # single-sweep study keeps the full geometry review path, unchanged.
    from preflight_helpers.study_discovery import study_modalities as _study_modalities
    _mods = _study_modalities(st.session_state.study_entry.study_path)
    is_multimodal = any(r in _mods for r in ("femur", "rf"))

    picked = list(st.session_state.picked_frames)
    _ensure_predicted_masks(picked)
    transport = _transport_dir_for_entry(st.session_state.study_entry)

    # AI-suggested representative (LEGACY single-sweep only): the Tier-1 picked
    # frame with the highest representativeness score, the DEFAULT anchor. The
    # representative-frame concept is retired on multimodal studies — per-modality
    # medians replace the single anchor (docs/implementation.md A6).
    if not is_multimodal:
        from labeling_app.assessment_pages.p03_frame_picker import _ai_suggested_repr
        ai_suggested = _ai_suggested_repr(picked, st.session_state.get("femur_scan"))
        _cur_anchor = st.session_state.get("anchor_frame")
        if ai_suggested and (_cur_anchor is None or _cur_anchor not in picked):
            st.session_state.anchor_frame = ai_suggested
    else:
        ai_suggested = None

    # ----- Layer-stack toggles (Akadir 2026-05-24 v2 reframe — § 7) ---------
    # The operator composes the inspection image themselves. Four layers:
    # raw (Layer 0, always on) + segmentation overlay (Layer 1) + AI-
    # confidence wash (Layer 2, cyan) + post-processing change-map (Layer 3,
    # lime). Each toggle is global (one set for all frames on this page) —
    # toggle once, the whole walk-through changes consistently. The toggles
    # live in session state with stable keys so AppTest can find them.
    # Layer-stack state — read from session_state. The toggles live in
    # assessment_app.py's global sidebar (always-rendered, like
    # dev_mode_toggle) to dodge the AppTest conditional-keyed-widget gotcha
    # (memory `feedback_apptest_conditional_widgets`). Operator toggles
    # them on the global sidebar; this page reads the current state.
    show_seg = bool(st.session_state.get("p04_show_segmentation", True))
    show_conf = bool(st.session_state.get("p04_show_confidence", False))
    show_change = bool(st.session_state.get("p04_show_change_map", False))

    if is_multimodal and picked:
        st.markdown("##### Sweep (lateral→medial) — texture / brightness robustness")
        st.caption(
            "The sweep is the legacy-comparable view + a texture/brightness robustness "
            "cross-check — **not** a geometry source (size, fat, QMT come from the femur cine). "
            "Validate the segmentation; multiple frames are fine. Keep or drop each below."
        )
    qmt_statuses: dict[str, dict] = {}
    for i, fb in enumerate(picked):
        st.divider()
        cols = st.columns([1, 1])
        with cols[0]:
            # Layer 0 — raw image. Display the live-synthesised `final` (uint8
            # grayscale at mask space). Akadir 2026-05-25 § 5: when Layer 2
            # (AI confidence) is ON, blend the confidence wash onto the raw
            # too — both panels share the toggle, so the operator can read
            # confidence against an otherwise-unmodified raw image (the
            # right-side overlay carries too many layers at once). Falls back
            # to the bare jpg path if the live cache is somehow not populated.
            final_u8 = st.session_state.get("final_u8_cache", {}).get(fb)
            if final_u8 is not None:
                confidence_map = (
                    st.session_state.get("confidence_map_cache") or {}
                ).get(fb)
                if show_conf and confidence_map is not None:
                    raw_rgb = np.stack(
                        [final_u8, final_u8, final_u8], axis=-1
                    ).astype(np.uint8)
                    raw_display = confidence_heatmap_overlay(
                        raw_rgb, confidence_map)
                    caption = f"Raw + AI confidence — {fb}"
                else:
                    raw_display = final_u8
                    caption = f"Raw (live final) — {fb}"
                st.image(raw_display, caption=caption, width="stretch")
            else:
                jpg_path = st.session_state.study_entry.raw_image_dir / f"{fb}.jpg"
                if jpg_path.exists():
                    st.image(str(jpg_path), caption=f"Raw (jpg) — {fb}", width="stretch")
                else:
                    st.warning(f"Missing source for {fb}")
        with cols[1]:
            pred = st.session_state.predicted_masks.get(fb)
            if pred is None:
                st.warning("No predicted mask")
            else:
                # Layer-stack composition.
                raw_arr = _raw_arr_for(fb)
                if raw_arr is None:
                    st.warning("No raw image cached")
                else:
                    seg_arr = _render_segmentation_arr(
                        fb, pred, with_measurements=not is_multimodal)
                    _, change_map = post_process_mask_for_display(pred)
                    confidence_map = (
                        st.session_state.get("confidence_map_cache") or {}
                    ).get(fb)
                    composed = compose_layer_stack(
                        raw_arr, seg_arr,
                        confidence_map=confidence_map,
                        change_map=change_map,
                        show_segmentation=show_seg,
                        show_confidence=show_conf,
                        show_change_map=show_change,
                    )
                    caption_parts = ["Layers:"]
                    if show_seg: caption_parts.append("seg")
                    if show_conf: caption_parts.append("conf")
                    if show_change: caption_parts.append("change")
                    _layers_cap = (" + ".join(caption_parts) if len(caption_parts) > 1
                                   else "raw only — toggle layers above")
                    st.image(composed, caption=f"{fb} · {_layers_cap}", width="stretch")
                # QMT provenance tier — LEGACY single-sweep only. On a multimodal
                # study the sweep is not a geometry source, so its QMT tier is
                # vestigial; QMT routes from the femur cine and the tier is shown
                # in the femur review instead (Akadir 2026-06-26 audit, #5).
                if not is_multimodal:
                    status = qmt_review.frame_qmt_status(pred)
                    qmt_statuses[fb] = status
                    st.markdown(
                        f":{status['colour']}[**QMT: {status['label']}**]  \n"
                        f":gray[{status['meaning']}]"
                    )
                # Layer 2 interpretability — per-frame confidence stat. Pixels
                # over the WHOLE frame; we report the mean confidence + how
                # much of the frame is below the 0.70 threshold. Concrete
                # number the operator can react to instead of just a colour
                # (Akadir 2026-05-24 § 7).
                cs = confidence_stats(confidence_map) if confidence_map is not None else None
                if cs is not None:
                    st.caption(
                        f"AI confidence over the frame: mean **{cs['mean']:.2f}**, "
                        f"**{cs['fraction_below_0_70']*100:.0f}%** of pixels below 0.70.")

        # --- Per-measurement QA attribution panel (v2 § 5b) — LEGACY only; the
        # attributed measurements are geometry, which routes from the femur on a
        # multimodal study (Akadir 2026-06-26 audit, #5).
        if not is_multimodal:
            _per_measurement_attribution_panel(fb, pred, qmt_statuses[fb] if fb in qmt_statuses else None)

        # --- Make-repr button — LEGACY only. The single representative frame is
        # replaced by per-modality medians on a multimodal study (A6), so there is
        # no sweep repr to designate (Akadir 2026-06-26 audit, #4).
        if not is_multimodal:
            _make_repr_button(fb, status=qmt_statuses.get(fb),
                              qmt_statuses=qmt_statuses, ai_suggested=ai_suggested)

        _gate_block(fb, i)

    st.divider()
    summary_n = _gate_summary()
    st.markdown(
        f"**Summary:** usable {summary_n['usable']} · dropped {summary_n['dropped']}"
        f" · training-candidates (perfect) {summary_n['perfect']}"
        f" · analysis-included {summary_n['for_analysis']}"
        f" · gates pending {summary_n['pending']}"
    )

    # QMT selection panel — surface the femur / provenance status NOW, while
    # reselection is cheap, not after the assessment when it is too late.
    gates_now = st.session_state.get("gates", {}) or {}
    feeding = [qmt_statuses[fb] for fb in picked
               if fb in qmt_statuses
               and gates_now.get(fb, {}).get("use_for_analysis") is not False]
    if feeding:
        qsum = qmt_review.selection_qmt_summary(feeding)
        box = {"standard": st.success, "proxy": st.warning,
               "none": st.error}[qsum["verdict"]]
        # Akadir 2026-05-25 v2 § 3b — the Tier-1/Tier-2/Tier-3 numbers are
        # encrypted on their own (the operator has not necessarily met the
        # Tier ladder). Lead with the plain-English label and carry the
        # canonical Tier-N in parentheses so the two vocabularies converge.
        box(f"**QMT status — {qsum['message']}**  \n"
            f":gray[femur-anchored (Tier-1) {qsum['n_tier1']} · "
            f"proxy (Tier-2) {qsum['n_tier2']} · "
            f"no femur (Tier-3) {qsum['n_tier3']} across the frames that "
            f"will feed the run.]")
        if qsum["verdict"] != "standard":
            if st.button("↻ Reselect frames (clears picks + gates, fresh start)",
                         key="qmt_reselect"):
                # Genuinely fresh: drop the picks, the gates dict, AND the
                # per-frame gate widget state — Streamlit persists widgets by
                # key, so without this the old Yes/No survives a re-pick.
                for old_fb in picked:
                    keys = [f"g1_{old_fb}", f"g2_{old_fb}", f"g3_{old_fb}",
                            f"dro_{old_fb}"]
                    keys += [f"drc_{old_fb}__{code}"
                             for code in IMAGE_QUALITY_FLAG_PRIORITY]
                    for wk in keys:
                        st.session_state.pop(wk, None)
                st.session_state.picked_frames = []
                st.session_state.gates = {}
                st.session_state.anchor_frame = None
                st.session_state.phase = 2
                st.rerun()

    # Build-Step 1 — review-completeness gate. The run cannot be locked until
    # every frame is decided Yes/No on every gate and every dropped frame has
    # a recorded reason (§ 7.4(c) / § 8.3.5): the structured capture is the
    # point of the Phase-4 refactor, so an optional capture would re-open the
    # very gap it closes.
    issues = _gate_completeness_issues()
    if issues:
        st.warning(
            "**Finish gating before this run can be locked:**\n\n"
            + "\n".join(f"- {msg}" for msg in issues)
        )

    # p04→p05 gate. LEGACY single-sweep: the representative-frame constraint (a
    # valid repr must be designated). MULTIMODAL: the representative frame is
    # retired (per-modality medians, A6) — gate only on "≥1 frame kept for
    # analysis across the modalities" + gating complete (Akadir 2026-06-26, #4).
    if not is_multimodal:
        repr_ok, repr_msg = _repr_gate_status(qmt_statuses)
        if not repr_ok:
            st.error(repr_msg)
        elif repr_msg:
            # OK but with a warning (fallback case — no femur in any picked frame).
            st.warning(repr_msg)
    else:
        repr_ok = True

    cols = st.columns([1, 3, 1])
    with cols[0]:
        if st.button("← Back", key="review_back"):
            st.session_state.phase = 2
            st.rerun()
    with cols[2]:
        if st.button("Lock gates + run assessment →",
                     type="primary",
                     disabled=(summary_n["for_analysis"] == 0
                              or bool(issues)
                              or not repr_ok),
                     key="lock_gates"):
            _capture_selection_labels()
            st.session_state.phase = 4
            st.rerun()

    # Optional detour into the calibration capture surface (subsystem D),
    # rendered LAST so it appends to the element tree after every gate widget —
    # placing it earlier shifts the conditional g3 gate radio's AppTest tree
    # position and trips the conditional-keyed-widget gotcha (memory
    # `feedback_apptest_conditional_widgets`). It collects the operator-label
    # substrate that un-freezes the pending detectors; it never changes the
    # deployed verdict. Entered at will, exits back here.
    st.divider()
    if st.button("→ Help calibrate detectors (optional)", key="p4x_enter"):
        p4x_calibration_capture.enter()
        st.rerun()

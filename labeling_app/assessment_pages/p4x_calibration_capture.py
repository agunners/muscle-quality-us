"""Phase 4x — the per-detector calibration capture surface (subsystem D).

A sub-route of p04 (entered via "→ Help calibrate", exited at will back to p04),
so the phase stays 3 and the Display-layers sidebar keeps working. The operator
picks a pending detector, then labels a candidate frame per cell:

    see image (spec default layers) + optional aux  →  read the per-detector
    question  →  answer Yes / No / Unsure (score hidden — anchor B)  →  the
    score + threshold are revealed with a "change your mind?" confirm  →  the
    label is persisted to data/calibration_labels.jsonl.

Everything here is PHI-clean — frames are addressed by (study_code, frame_base)
and loaded through `calibration.frame_loader`. No detector verdict is changed;
this only collects the calibration substrate (the deployed thresholds stay
pending until Build-Step 2b's fitter promotes them).
"""
from __future__ import annotations

import time
from typing import Optional

import numpy as np
import streamlit as st
import torch
from PIL import Image
from scipy import ndimage as ndi

from labeling_app.measurements import (
    CONTRACT_VERSION, LABEL_FASCIA, LABEL_FAT, LABEL_FEMUR, LABEL_RF,
    LABEL_SKIN, LABEL_VI, LABEL_VL, LABEL_VM,
)
from labeling_app import qa_calibration_spec as spec
from labeling_app.assessment_pages import qa_calibration_aux
from labeling_app.visualization import (
    confidence_heatmap_overlay, load_label_palette, overlay_on_image,
)
from preflight_helpers import env

# scripts/ is on path (assessment_app launcher) — these resolve.
from calibration import candidate_selector, frame_loader, label_store
from qa_cohort_failure_histogram import _SCORE_KEY
from labeling_app.qa_thresholds import THRESHOLDS
# The segmenter machinery — p4x grades on PREDICTED masks (deployment-matched),
# the same masks the rest of the UI shows (Akadir 2026-05-28 mask-source fix).
from preflight_helpers.segmenter import (
    build_voi_hist_clahe_stack, predict_mask, synthesize_final_u8,
)
from preflight_helpers.feature_compute import compute_image_quality_features

# contour-class token -> (label id, RGB line colour)
_CONTOUR = {
    "rf": (LABEL_RF, (0, 220, 120)), "vi": (LABEL_VI, (120, 180, 255)),
    "vl": (LABEL_VL, (255, 180, 80)), "vm": (LABEL_VM, (255, 120, 200)),
    "femur": (LABEL_FEMUR, (255, 230, 0)), "fat": (LABEL_FAT, (200, 160, 255)),
    "skin": (LABEL_SKIN, (0, 200, 200)), "fascia": (LABEL_FASCIA, (255, 0, 255)),
}

# detector -> the qa_thresholds registry key whose value is its firing cut.
_THRESHOLD_KEY = {
    "dark_clip_high": "dark_clip_high__roi_fraction_max",
    "motion_blur": "motion_blur__fascia_band_tenengrad_min",
    "tgc_banding": "tgc_banding__detrended_residual_sigma_max",
    "air_gap_reverberation": "air_gap_reverberation__comb_peak_magnitude_min",
    "near_field_clutter": "near_field_clutter__clutter_energy_max",
    "vessel_shadow_in_roi": "vessel_shadow_in_roi__column_darkness_max",
}

_N_PER_SESSION = 6


# ---------------------------------------------------------------------------
# session-state helpers
# ---------------------------------------------------------------------------

def is_active() -> bool:
    return bool(st.session_state.get("p4x_active"))


def enter() -> None:
    st.session_state["p4x_active"] = True


def _exit() -> None:
    for k in ("p4x_active", "p4x_detector", "p4x_candidates", "p4x_cell_idx",
              "p4x_answer_pending", "p4x_cell_start"):
        st.session_state.pop(k, None)


def _operator_hash() -> str:
    """A stable per-operator id hash. Single-operator project today — uses the
    dev session; a real multi-operator deployment would key off auth."""
    import hashlib
    who = st.session_state.get("operator_id", "akadir")
    return hashlib.sha256(who.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# image composition
# ---------------------------------------------------------------------------

def _contour_pixels(binary: np.ndarray) -> np.ndarray:
    """Boundary pixels of a binary region (region minus its erosion)."""
    if not binary.any():
        return binary
    return binary & ~ndi.binary_erosion(binary, iterations=1, border_value=0)


def _compose(final_u8: np.ndarray, mask: np.ndarray, s: spec.CalibrationSpec,
             active_layers: tuple[str, ...],
             confidence_map: np.ndarray | None = None) -> np.ndarray:
    """Build the cell image: raw grayscale, plus the spec's contour and/or the
    full colour overlay and/or the AI-confidence wash, per `active_layers`. The
    `mask` is the PREDICTED mask (so the overlay matches the rest of the UI);
    `confidence_map` is the segmenter's per-pixel confidence (for the
    'confidence' layer — segmentation_low_confidence's signal). Returns
    (H, W, 3) uint8."""
    rgb = np.stack([final_u8] * 3, axis=-1).astype(np.uint8)
    if "segmentation_full" in active_layers:
        img = Image.fromarray(final_u8).convert("RGB")
        palette = load_label_palette(env.PALETTE_PATH)
        rgb = np.asarray(overlay_on_image(img, mask, palette, alpha=0.5)).copy()
    if "segmentation_contour" in active_layers:
        for cls in s.contour_classes:
            label_id, colour = _CONTOUR[cls]
            edge = _contour_pixels(mask == label_id)
            rgb[edge] = colour
    if "confidence" in active_layers and confidence_map is not None:
        rgb = confidence_heatmap_overlay(rgb, confidence_map, max_alpha=0.5)
    return rgb


# ---------------------------------------------------------------------------
# live predicted-mask grade (the mask-source fix — Akadir 2026-05-28)
# ---------------------------------------------------------------------------

def _predicted_grade(study_code: str, frame_base: str) -> dict | None:
    """Run the segmenter on a cohort frame and grade all 15 detectors on the
    PREDICTED mask — the same mask source the rest of the UI shows and that
    production grades on. Returns a dict with the predicted mask, the final
    grayscale, the per-pixel confidence map, and the per-detector metadata; or
    None if the frame can't be resolved or the model isn't loaded.

    Cached per (study_code, frame_base) in session_state — the segmenter is a
    GPU run and p4x reruns on every click. Defined module-level so the AppTest
    can monkeypatch it (the heavy seam)."""
    cache = st.session_state.setdefault("p4x_pred_cache", {})
    key = (study_code, frame_base)
    if key in cache:
        return cache[key]
    learn = st.session_state.get("learn")
    bm = st.session_state.get("bundle_meta")
    if learn is None or bm is None:
        return None                              # model not loaded (deep-link)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    conf: dict = {}
    fp = frame_loader.resolve(study_code, frame_base)
    if fp is not None:
        # Transport cohort frame (956-frame index) — pre-computed native final.
        final_u8 = frame_loader.load_final_native(fp)
        stacked = build_voi_hist_clahe_stack(final_u8, modalities=bm.modalities)
        pred = predict_mask(learn, stacked, bm, device=device, confidence_out=conf)
        spacing = dict(fp.spacing_row)
    else:
        # 5fps sweep-pool frame — NOT in the transport index. The candidate
        # selector ranks the 18,332-frame sweep pool, so most p4x candidates land
        # here; resolving only via transport returned None and surfaced "could
        # not be graded" for every one of them. Synthesize the `final` from the
        # RawImages jpg and segment on-demand (mirrors frame_loader.load_frame_
        # sweep, but keeps the confidence map this surface reveals). PHI:
        # resolve_sweep_jpg returns a patient-name path — load pixels, never show.
        jpg = frame_loader.resolve_sweep_jpg(study_code, frame_base)
        if jpg is None:
            return None
        final_u8 = synthesize_final_u8(jpg.parent, frame_base)
        stacked = build_voi_hist_clahe_stack(final_u8, modalities=bm.modalities)
        pred = predict_mask(learn, stacked, bm, device=device, confidence_out=conf)
        if final_u8.shape != pred.shape:
            final_u8 = np.asarray(
                Image.fromarray(final_u8).resize(
                    (pred.shape[1], pred.shape[0]), Image.BILINEAR), dtype=np.uint8)
        spacing = {"pixel_spacing_y": None, "dcm_rows": None}
    spacing["mask_height"] = int(pred.shape[0])
    iqf = compute_image_quality_features(
        frame_base=frame_base, multiclass_mask=pred, final_image_u8=final_u8,
        spacing_row=spacing, boundary_entropy=conf.get("boundary_entropy"))
    result = {
        "pred": pred, "final_u8": final_u8,
        "confidence_map": conf.get("confidence_map_native"),
        "metadata": iqf.image_quality_detector_metadata,
        "fired": set(iqf.image_quality_validity_flags),
    }
    cache[key] = result
    return result


# ---------------------------------------------------------------------------
# the detector picker
# ---------------------------------------------------------------------------

def _progress_by_detector() -> dict:
    """{detector: {'yes':n,'no':n,'unsure':n,'total':n}} from the p4x ledger."""
    out = {d: {"yes": 0, "no": 0, "unsure": 0, "total": 0}
           for d in spec.round1_detectors()}
    for r in label_store.load_labels():
        if r.get("label_source") != "p4x":
            continue
        d = r.get("detector")
        if d in out:
            out[d][r.get("label", "unsure")] = out[d].get(r.get("label"), 0) + 1
            out[d]["total"] += 1
    return out


def _render_picker() -> None:
    st.subheader("Help calibrate the image-quality detectors")
    st.caption("Optional. Each answer you give trains a detector's threshold. "
               "The headline assessment numbers are unaffected — this is the "
               "calibration substrate, not the deployed verdict.")

    due = label_store.due_relabels()
    if due:
        st.warning(f"**{len(due)} frame(s) are due for a test-retest re-label** "
                   f"(the 2-week consistency check). Re-labelling them blind "
                   f"measures your self-consistency (κ).")

    prog = _progress_by_detector()
    st.markdown("**Pick a detector to work on:**")
    for det in spec.round1_detectors():
        s = spec.get(det)
        p = prog[det]
        target = s.cohort_n_target
        pos = p["yes"]
        cols = st.columns([4, 2, 1])
        with cols[0]:
            st.markdown(f"**{det.replace('_', ' ')}** — "
                        f":gray[{s.operator_question}]")
        with cols[1]:
            st.progress(min(1.0, pos / target),
                        text=f"{pos}/{target} positives · {p['total']} labelled")
        with cols[2]:
            if st.button("Label", key=f"p4x_pick_{det}"):
                _start_detector(det)
                st.rerun()
    st.divider()
    if st.button("← Back to review (phase 4)", key="p4x_exit_picker"):
        _exit()
        st.rerun()


def _start_detector(det: str) -> None:
    s = spec.get(det)
    df = candidate_selector.load_score_table()
    threshold = None
    tk = _THRESHOLD_KEY.get(det)
    if tk and tk in THRESHOLDS:
        v = THRESHOLDS[tk].value
        threshold = None if (isinstance(v, float) and np.isnan(v)) else v
    already = label_store.labelled_keys(det)
    cands = candidate_selector.select_candidates(
        det, df, strategy=s.selector_strategy, n=_N_PER_SESSION,
        threshold=threshold, already_labelled=already, seed=None)
    st.session_state["p4x_detector"] = det
    st.session_state["p4x_candidates"] = [
        (c.study_code, c.frame_base, c.raw_score, c.percentile, c.band) for c in cands]
    st.session_state["p4x_cell_idx"] = 0
    st.session_state["p4x_answer_pending"] = None
    st.session_state["p4x_cell_start"] = time.time()


# ---------------------------------------------------------------------------
# the labelling cell
# ---------------------------------------------------------------------------

def _render_cell() -> None:
    det = st.session_state["p4x_detector"]
    s = spec.get(det)
    cands = st.session_state["p4x_candidates"]
    idx = st.session_state["p4x_cell_idx"]

    top = st.columns([3, 1])
    with top[0]:
        st.subheader(f"Calibrating: {det.replace('_', ' ')}")
    with top[1]:
        if st.button("Done for now →", key="p4x_done"):
            st.session_state["p4x_detector"] = None
            st.rerun()
    st.progress(idx / max(1, len(cands)), text=f"frame {idx + 1} of {len(cands)}")

    if idx >= len(cands):
        st.success("All candidates for this detector are labelled this session. "
                   "Pick another detector, or come back later for more.")
        if st.button("← Detector list", key="p4x_back_to_list"):
            st.session_state["p4x_detector"] = None
            st.rerun()
        return

    study_code, frame_base, raw_score, percentile, band = cands[idx]
    # Grade on the PREDICTED mask (the segmenter runs live — same mask source
    # the rest of the UI shows + production grades on). The score the operator
    # sees on reveal is THIS predicted score, not the annotator-table proxy
    # that ranked the candidate.
    graded = _predicted_grade(study_code, frame_base)
    if graded is None:
        st.error(f"Frame {study_code}/{frame_base} could not be graded "
                 f"(unresolved, or the model is not loaded — revisit phase 2).")
        if st.button("Skip", key="p4x_skip_unloadable"):
            _advance()
            st.rerun()
        return
    pred, final_u8 = graded["pred"], graded["final_u8"]
    confidence_map = graded["confidence_map"]
    det_meta = graded["metadata"].get(det, {})
    score_key = _SCORE_KEY.get(det)
    pred_score = det_meta.get(score_key) if score_key else None
    fired = det in graded["fired"]

    pending = st.session_state.get("p4x_answer_pending")

    # --- the image + optional aux ---
    img_col, aux_col = st.columns([3, 2])
    with img_col:
        active = s.default_layers          # toggles are a later refinement
        rgb = _compose(final_u8, pred, s, active, confidence_map=confidence_map)
        st.image(rgb, width="stretch",
                 caption=f"{study_code} · {frame_base}  (predicted mask · "
                         f"layers: {', '.join(active)})")
    with aux_col:
        aux_png = qa_calibration_aux.render_aux(
            s.auxiliary_widget, img_gray=final_u8, mask=pred, metadata=det_meta)
        if aux_png is not None:
            st.image(aux_png, width="stretch", caption="what the detector sees")

    st.markdown(f"### {s.operator_question}")

    if pending is None:
        _render_answer_buttons()
    else:
        _render_reveal(det, s, pred_score, det_meta)

    # persistence target preview (dev only)
    if st.session_state.get("dev_mode"):
        sc = f"{pred_score:.4g}" if isinstance(pred_score, (int, float)) else "n/a"
        st.caption(f":gray[predicted score={sc} · selection pctile={percentile} · "
                   f"band={band} · fires={fired}]")


def _render_answer_buttons() -> None:
    st.caption("Answer from the image — the detector's score is hidden until "
               "after you choose (so your eye is the source, not the detector).")
    c = st.columns(3)
    if c[0].button("✓ Yes — problem present", key="p4x_ans_yes", use_container_width=True):
        _set_pending("yes")
    if c[1].button("✗ No — frame is fine", key="p4x_ans_no", use_container_width=True):
        _set_pending("no")
    if c[2].button("? Unsure", key="p4x_ans_unsure", use_container_width=True):
        _set_pending("unsure")


def _set_pending(label: str) -> None:
    start = st.session_state.get("p4x_cell_start", time.time())
    st.session_state["p4x_answer_pending"] = {
        "label": label,
        "response_time_ms": int((time.time() - start) * 1000),
    }
    st.rerun()


def _threshold_for(det: str):
    tk = _THRESHOLD_KEY.get(det)
    if not tk or tk not in THRESHOLDS:
        return None
    v = THRESHOLDS[tk].value
    return None if (isinstance(v, float) and np.isnan(v)) else v


def _render_reveal(det: str, s: spec.CalibrationSpec, raw_score: float,
                   det_meta: dict) -> None:
    pending = st.session_state["p4x_answer_pending"]
    answer = pending["label"]
    thr = _threshold_for(det)
    have_score = isinstance(raw_score, (int, float)) and raw_score == raw_score
    if not have_score:
        detail = "The detector could not compute a reading on this frame."
    elif thr is not None:
        # Name which number is which — a bare "measured 1 — threshold 0.02" read
        # like a binary 1/0 (Akadir 2026-06-09). And say plainly that the
        # threshold is provisional, because the operator's labels here calibrate it.
        detail = (f"On this frame the detector's reading was **{raw_score:.4g}**, and "
                  f"its current trigger threshold is **{thr:.4g}** — a provisional "
                  f"starting point; your labels here are what calibrate it.")
    else:
        detail = (f"On this frame the detector's reading was **{raw_score:.4g}** — "
                  f"no threshold set yet (this detector is un-calibrated).")
    st.info(f"You answered **{answer.upper()}**.  \n{detail}  \n"
            f"Does that change your mind?")
    # context-aware confirm options
    others = {"yes": ("no", "Change to No"), "no": ("yes", "Change to Yes"),
              "unsure": ("yes", "Change to Yes")}
    opp_label, opp_text = others[answer]
    c = st.columns(3)
    if c[0].button("Keep my answer", key="p4x_confirm_keep", type="primary",
                   use_container_width=True):
        _commit(det, s, answer, "kept", raw_score, det_meta)
    if c[1].button(opp_text, key="p4x_confirm_opp", use_container_width=True):
        _commit(det, s, opp_label, "changed_to_opposite", raw_score, det_meta)
    third = "unsure" if answer != "unsure" else "no"
    third_text = "Change to Unsure" if answer != "unsure" else "Change to No"
    if c[2].button(third_text, key="p4x_confirm_third", use_container_width=True):
        conf = "changed_to_unsure" if third == "unsure" else "changed_to_opposite"
        _commit(det, s, third, conf, raw_score, det_meta)


def _commit(det: str, s: spec.CalibrationSpec, final_label: str,
            confirmation: str, raw_score: float, det_meta: dict) -> None:
    pending = st.session_state["p4x_answer_pending"]
    study_code, frame_base, _score, _pct, _band = \
        st.session_state["p4x_candidates"][st.session_state["p4x_cell_idx"]]
    has_score = isinstance(raw_score, (int, float)) and raw_score == raw_score
    lbl = label_store.Label(
        study_code=study_code, frame_base=frame_base, detector=det,
        label=final_label,
        score_at_label_time=float(raw_score) if has_score else None,
        detector_metadata_at_label_time={
            k: v for k, v in det_meta.items() if k != "thresholds_used"},
        threshold_at_label_time=_threshold_for(det),
        label_source="p4x",
        score_revealed_at="after" if s.score_reveal != "never" else "never",
        post_reveal_confirmation=confirmation,
        response_time_ms=pending["response_time_ms"],
        visual_aids_shown=tuple(s.default_layers),
        contract_version=CONTRACT_VERSION,
        spec_version_at_label_time=s.spec_version,
        operator_id_hash=_operator_hash(),
        relabel_target=False,
    )
    label_store.append_label(lbl)
    _advance()
    st.rerun()


def _advance() -> None:
    st.session_state["p4x_cell_idx"] = st.session_state.get("p4x_cell_idx", 0) + 1
    st.session_state["p4x_answer_pending"] = None
    st.session_state["p4x_cell_start"] = time.time()


# ---------------------------------------------------------------------------
# entry point (called by p04.render when p4x_active)
# ---------------------------------------------------------------------------

def render() -> None:
    if not st.session_state.get("p4x_detector"):
        _render_picker()
    else:
        _render_cell()

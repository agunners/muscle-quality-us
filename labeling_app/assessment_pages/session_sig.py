"""Shared session-state helpers for the N4 run lifecycle.

Kept in its own module (not in assessment_app.py) to avoid an import cycle:
assessment_app imports every page module at load time, so a page importing
back from assessment_app would deadlock the import graph.

Two signatures live here:
  * `current_masks_sig()` — over picked frame bases + bundle. Gates are
    excluded: changing a gate does not change a predicted mask. p04 uses this
    to know when to drop the predicted-mask cache.
  * `current_input_sig()` — over picked frames *with* gates + bundle + class
    ids. p05 uses this to know when the cached assessment run is stale.
Both must stay consistent with what `run_pipeline` writes into the ledger —
hence the shared `session_bundle_sha()` (one hash, computed once at model load).
"""
from __future__ import annotations

import hashlib
import json
from typing import Optional

import streamlit as st

from preflight_helpers.run_lifecycle import input_signature
from preflight_helpers.run_pipeline import GateAnswers, PickedFrame
from preflight_helpers.segmenter import pinned_mask_cfg

from labeling_app.measurements import canonical_image_quality_flag_order


def session_bundle_sha() -> str:
    """The selected bundle's sha256, as cached on the BundleEntry at model load.

    p02 hashes the pkl once (inside the load spinner) and stores the hashed
    BundleEntry, so this is a cheap attribute read on every rerun. Falls back
    to a path-as-proxy string if the sha was never computed (test paths that
    bypass p02) — still stable within a session.
    """
    bundle = st.session_state.get("bundle")
    if bundle is None:
        return ""
    sha = getattr(bundle, "pkl_sha256", None)
    if sha:
        return sha
    return f"path:{getattr(bundle, 'pkl_path', '')}"


def resolved_class_ids() -> dict:
    """The pinned class-id config (femur=7 override etc.) as a plain dict."""
    cls = pinned_mask_cfg()
    return {
        "muscle_class_id": cls.muscle_class_id,
        "fascia_class_id": cls.fascia_class_id,
        "skin_class_id": cls.skin_class_id,
        "fat_class_id": cls.fat_class_id,
        "femur_class_id": cls.femur_class_id,
        "erosion_px": cls.erosion_px,
    }


def build_picked_frames() -> list[PickedFrame]:
    """Build `PickedFrame` objects from session state (picks + gates + anchor).

    Anchor resolution: if the user explicitly marked one (B2.3 — session key
    `anchor_frame`), honour it; otherwise fall back to the middle of the pick
    order. Either way exactly one frame ends up flagged `is_anchor` so the
    anchor_primary aggregation policy has a frame to read.
    """
    picked: list[str] = list(st.session_state.get("picked_frames", []) or [])
    gates_map: dict = st.session_state.get("gates", {}) or {}
    anchor_fb: Optional[str] = st.session_state.get("anchor_frame")
    n = len(picked)
    out: list[PickedFrame] = []
    for i, fb in enumerate(picked):
        g = gates_map.get(fb, {})
        if anchor_fb is not None:
            is_anchor = (fb == anchor_fb)
        else:
            is_anchor = (i == n // 2)
        out.append(PickedFrame(
            frame_base=fb,
            is_anchor=is_anchor,
            gates=GateAnswers(
                usable=bool(g.get("usable", True)),
                segmentation_perfect=bool(g.get("segmentation_perfect", False)),
                use_for_analysis=bool(g.get("use_for_analysis", True)),
                drop_reason=g.get("drop_reason") or None,
                # Build-Step 1 structured capture — the session gates dict
                # holds a set; canonicalise to a priority-ordered tuple here.
                drop_reason_codes=canonical_image_quality_flag_order(
                    g.get("drop_reason_codes")),
                drop_reason_other=(g.get("drop_reason_other") or "").strip(),
                qa_overrides=dict(g.get("qa_overrides") or {}),
            ),
        ))
    # Guard: explicit anchor not among the picks (or none set) → fall back to
    # the middle so there is always exactly one anchor.
    if out and not any(pf.is_anchor for pf in out):
        out[n // 2].is_anchor = True
    return out


def current_input_sig() -> Optional[str]:
    """input_signature of the current picks/gates/bundle/class-ids.

    Returns None when the inputs are not all present yet (no bundle, or no
    picked frames) — callers treat None as "cannot be a cache hit".
    """
    if st.session_state.get("bundle") is None:
        return None
    # v8: a routed study can be scored from femur/RF picks ALONE (no sweep pick). Only bail when
    # NO modality has picks — else the femur/RF-only run never gets a cacheable signature (every
    # rerun would miss the cache + re-segment, and a femur/RF re-pick couldn't invalidate it).
    if (not st.session_state.get("picked_frames")
            and not (st.session_state.get("picked_frames__femur") or [])
            and not (st.session_state.get("picked_frames__rf") or [])):
        return None
    base = input_signature(
        build_picked_frames(), session_bundle_sha(), resolved_class_ids()
    )
    # v8: the femur/RF picks drive the routed score (geometry/STAR/QMT from the femur, int_cv from
    # the center-RF), so a re-pick in those tabs MUST invalidate the run cache. A legacy
    # single-modality study has no such picks -> base unchanged (no regression).
    fem = tuple(sorted(st.session_state.get("picked_frames__femur") or []))
    rf = tuple(sorted(st.session_state.get("picked_frames__rf") or []))
    if not fem and not rf:
        return base
    # v8 keep/drop gate (Akadir 2026-06-25): the femur/RF gates change which frames feed the routed
    # aggregate, so they must invalidate the cache too — else dropping a frame in review won't re-score.
    def _gate_fp(picks: tuple, store: str) -> tuple:
        gm = st.session_state.get(store, {}) or {}
        return tuple((fb, gm.get(fb, {}).get("usable"), gm.get(fb, {}).get("use_for_analysis"))
                     for fb in picks)
    fem_g, rf_g = _gate_fp(fem, "gates__femur"), _gate_fp(rf, "gates__rf")
    return hashlib.sha256(
        f"{base}|femur:{fem}|rf:{rf}|fg:{fem_g}|rg:{rf_g}".encode("utf-8")).hexdigest()


def current_masks_sig() -> str:
    """Signature of the predicted-mask inputs: sorted picked frame bases +
    bundle sha. Gates are deliberately excluded — they do not affect a mask."""
    picked = sorted(st.session_state.get("picked_frames", []) or [])
    blob = json.dumps(
        {"frames": picked, "bundle_sha": session_bundle_sha()},
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()

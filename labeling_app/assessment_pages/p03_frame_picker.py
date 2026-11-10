"""Phase 3 — Frame picker.

User scans the sweep's jpg thumbnails (from RawImagesForResearch_) and clicks
up to 9 to include in the analysis. Three suggested slots is the default
visual cue; user is not pressured to fill 9. Auto-suggest button is present
but dehighlighted (disabled v3 stub — real algorithm post-Friday).

UX (audit-v2 — F2 / F3):

- The selection panel + counter + Segment-and-continue button live in the
  Streamlit-native LEFT SIDEBAR (`render_sidebar`). The sidebar is sticky
  while the main area scrolls — so the operator never has to scroll back up
  to see their selection, the running count, or the advance button. This is
  F2's fix; the old in-page top/bottom button bars are gone.
- The main area (`render`) is now a full-width thumbnail grid. Each thumbnail
  sits in a bordered container that lights up when picked.
- Explicit repr marking (B2.3): the operator marks ONE picked frame as the
  "repr" — the representative frame the app reads the geometry headline
  values from (internal anchor_primary policy; the session-state key is
  still `anchor_frame` for backwards compatibility). The auto "middle of
  pick order" is only the fallback when none is marked.

Fullscreen note: the image fullscreen affordance is hidden app-wide (F1 — see
assessment_app._inject_global_css). The source `.jpg` is the device's native
per-frame output; fullscreen would only show the same content scaled up.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import streamlit as st
from PIL import Image

from labeling_app.assessment_pages import qa_surfacing


MAX_PICKED = 9
SUGGESTED_PICKS = 3
THUMB_WIDTH_PX = 130
GRID_COLS = 8           # thumbnails per row — main area is now full-width

# v8 3-phase selection (Akadir 2026-06-25): a materialized study has three modalities, picked in
# tabs. Order = femur (measurements) · center-RF (echo intensity) · sweep (legacy view). A LEGACY
# single-modality study (no captures/) shows just the sweep grid, exactly as before.
_TAB_ORDER = ("femur", "rf", "sweep")
_MODALITY_TAB = {
    "femur": ("Femur (all measurements)",
              "Pick the frame(s) where the femur is the deep boundary — ALL measurements read here: RF "
              "thickness / area, fat, QMT (muscle to bone) → STAR, and the fat fraction. Aggregated across your picks."),
    "rf": ("Center-RF (echo texture)",
           "Pick the frame(s) the echo TEXTURE reads — int_cv / fascicular contrast inside the RF mask. "
           "The shallow cine; geometry + fat now come from the femur. Aggregated across your picks."),
    "sweep": ("Sweep (echo cross-check)",
              "The long lateral→medial sweep — its ONLY job is an own-study echo cross-check (does its "
              "contrast agree with the center-RF). NOT a geometry source, and NO cohort value. "
              "Multiple frames are fine; no femur pre-scan here."),
}


def _picked_key(role: str) -> str:
    """Session-state key for a modality's picks. The SWEEP keeps the legacy `picked_frames`
    (session_sig + the scorer + the existing tests read it unchanged); femur/RF get their own keys,
    filled by the 3-phase tabs and consumed by BOTH the feature router and the score (geometry
    reads the femur, echo-intensity the center-RF)."""
    return "picked_frames" if role == "sweep" else f"picked_frames__{role}"


def _pick_btn_key(role: str, fb: str) -> str:
    """Widget key for a thumbnail's add/remove button. The SWEEP keeps the legacy `pick_<fb>` (the
    existing AppTest helpers click it); femur/RF prefix the role so the three tabs never collide."""
    return f"pick_{fb}" if role == "sweep" else f"pick_{role}_{fb}"

# Femur ROI overlay (Akadir 2026-05-24 § 1f + § 2b — "I don't see a non-trivial
# femur in the thumbnail"). The picker's femur dot is an AI assertion the
# operator cannot visually verify on a 130-px grayscale thumbnail. The ROI
# overlay tints the dominant femur band so the operator can SEE where the AI
# thinks the femur is. Colour: a warm yellow-orange (255, 220, 0) — distinct
# from RF magenta (segmentation palette) and from cyan/lime (the p04 layer-
# stack colours). The picker thumbnail does not show full segmentation so a
# fat-yellow collision does not arise here.
FEMUR_ROI_TINT = (255, 220, 0)
FEMUR_ROI_ALPHA = 0.50

# QMT femur-scan tier badges for the thumbnail grid. The picker only reports
# status — it never recommends or auto-picks; the operator decides.
_FEMUR_BADGE = {
    1: ":green[● femur]",
    2: ":orange[● femur · proxy]",
    3: ":red[● no femur]",
}
# Tier 3 ("QMT not measurable") splits two ways: no femur was segmented at all
# (the dominant-femur mask is empty), or a femur IS present but the muscle path
# (RF/VI) is missing so QMT can't be measured. Calling the second case "no femur"
# is false — the bone is plainly there — so split on `femur_present` (from the
# scan, = `femur_absent` did not fire). See the .757 review, 2026-06-04.
_FEMUR_BADGE_FEMUR_PRESENT_UNMEASURABLE = ":orange[● femur present · not measurable]"


def _femur_badge(fs: dict) -> str:
    """The thumbnail femur status badge, with tier-3 split so a frame whose femur
    was segmented but whose QMT is unmeasurable is never mislabelled 'no femur'."""
    tier = fs.get("tier", 3)
    if tier == 3 and fs.get("femur_present"):
        return _FEMUR_BADGE_FEMUR_PRESENT_UNMEASURABLE
    return _FEMUR_BADGE.get(tier, "")


def _run_femur_scan() -> None:
    """Scan every sweep frame for a usable femur — STATUS only, no auto-pick.

    Segments the whole sweep and stores each frame's QMT tier; the grid then
    shows the status so the operator picks informed. Per Akadir: the picker
    reports the status and lets the user go back / proceed — it does not
    recommend or auto-select frames.
    """
    entry = st.session_state.get("study_entry")
    learn = st.session_state.get("learn")
    bm = st.session_state.get("bundle_meta")
    if entry is None or learn is None or bm is None or entry.raw_image_dir is None:
        st.warning("Pick a study + model first.")
        return
    frame_bases = st.session_state.get("available_frame_bases") or []
    if not frame_bases:
        st.warning("No sweep frames discovered.")
        return
    from labeling_app.assessment_pages.femur_scan import scan_sweep_for_femur
    mask_cache = st.session_state.setdefault("predicted_masks", {})
    final_cache = st.session_state.setdefault("final_u8_cache", {})
    iq_cache = st.session_state.setdefault("image_quality_cache", {})
    bar = st.progress(
        0.0, text="Pre-scanning sweep (femur + image quality)…")
    ranked = scan_sweep_for_femur(
        frame_bases, learn, bm, entry.raw_image_dir,
        mask_cache=mask_cache, final_cache=final_cache,
        image_quality_cache=iq_cache,
        progress=lambda frac, fb: bar.progress(frac, text=f"Scanned {fb}"),
    )
    bar.empty()
    scan = {r["frame_base"]: r for r in ranked}
    # Slice F — attach the advisory RF-representative-frame score to each frame
    # (computed from the masks + grades the scan just produced; no new GPU
    # pass). Scored over THIS sweep only so fullness is sweep-relative;
    # _ai_suggested_repr ranks the Tier-1 picks by it.
    from labeling_app import repr_scorer
    sweep_masks = {fb: mask_cache[fb] for fb in scan if fb in mask_cache}
    scores = repr_scorer.score_sweep(sweep_masks, iq_cache)
    for fb, r in scan.items():
        r["repr_score"] = scores.get(fb, 0.0)
    st.session_state.femur_scan = scan
    st.rerun()


def _run_femur_cine_scan() -> None:
    """Pre-scan the FEMUR CINE — the deep-plane analogue of the sweep pre-scan (Akadir 2026-06-26).

    Segments every femur-cine frame, tags each with its QMT tier + image-quality grade (the same
    `scan_sweep_for_femur` the sweep uses — it is generic), then ranks them with `femur_cine_scorer`:
    a femur-anchored Tier-1 frame (caliper reaches the bone) first, then the fullest/least-squeezed
    within a tier. The ranking rides on each frame's `repr_score` so the grid's existing 'recommended'
    path works unchanged. Stored under `femur_cine_scan` (separate from the sweep's `femur_scan`).
    Status only — the operator still picks.
    """
    entry = st.session_state.get("study_entry")
    learn = st.session_state.get("learn")
    bm = st.session_state.get("bundle_meta")
    if entry is None or learn is None or bm is None:
        st.warning("Pick a study + model first.")
        return
    from preflight_helpers.study_discovery import study_modalities, list_frame_bases
    mods = study_modalities(entry.study_path)
    femur = mods.get("femur")
    if femur is None or femur.raw_image_dir is None:
        st.warning("This study has no femur cine.")
        return
    frame_bases = list_frame_bases(femur.raw_image_dir) or []
    if not frame_bases:
        st.warning("No femur-cine frames discovered.")
        return
    from labeling_app.assessment_pages.femur_scan import scan_sweep_for_femur
    from labeling_app import femur_cine_scorer
    mask_cache = st.session_state.setdefault("predicted_masks", {})
    final_cache = st.session_state.setdefault("final_u8_cache", {})
    iq_cache = st.session_state.setdefault("image_quality_cache", {})
    bar = st.progress(0.0, text="Pre-scanning femur cine (QMT + image quality)…")
    ranked = scan_sweep_for_femur(
        frame_bases, learn, bm, femur.raw_image_dir,
        mask_cache=mask_cache, final_cache=final_cache, image_quality_cache=iq_cache,
        progress=lambda frac, fb: bar.progress(frac, text=f"Scanned {fb}"))
    bar.empty()
    scan = {r["frame_base"]: r for r in ranked}
    cine_masks = {fb: mask_cache[fb] for fb in scan if fb in mask_cache}
    # Persist the femur-cine masks in a dedicated session key so the p05 femur-cine size landmark can
    # read them after the operator walks p04 (the per-modality review may re-segment / overwrite the
    # shared predicted_masks). Overwritten wholesale on a re-scan, so it cannot go stale vs femur_cine_scan.
    st.session_state.femur_cine_masks = dict(cine_masks)
    scores = femur_cine_scorer.score_femur_cine(cine_masks)
    for fb, r in scan.items():
        r["repr_score"] = scores.get(fb, 0.0)   # femur ranking: Tier-1 > Tier-2 (> Tier-3 = 0), fullest first
    st.session_state.femur_cine_scan = scan
    st.rerun()


def _femur_cine_prescan_controls() -> None:
    """The femur-cine tab's pre-scan button + post-scan tier summary — the in-tab analogue of the
    sweep's sidebar pre-scan (Akadir 2026-06-26). The femur-detection vestigial on the shallow sweep
    is load-bearing here, on the deep cine where the QMT is actually measured."""
    if st.button("🔍 Pre-scan femur cine (QMT + image quality)",
                 type="secondary", use_container_width=True, key="femur_cine_scan_btn",
                 help="Segments every femur-cine frame and tags each thumbnail with its QMT tier "
                      "(Tier 1 = femur-anchored fascia-to-bone · Tier 2 = proxy · Tier 3 = no measurable "
                      "femur), the yellow femur tint, any image-quality issue, and a 'recommended' badge "
                      "on the best femur-anchored + fullest frames. Status only — you still pick."):
        _run_femur_cine_scan()
    cine_scan = st.session_state.get("femur_cine_scan") or {}
    if cine_scan:
        n = {t: sum(1 for r in cine_scan.values() if r.get("tier") == t) for t in (1, 2, 3)}
        st.caption(f":gray[Femur QMT — Tier-1 (femur-anchored) {n[1]} · Tier-2 (proxy) {n[2]} · "
                   f"Tier-3 (no femur) {n[3]}. ★ marks the recommended femur-anchored + fullest frames.]")


def _run_center_rf_scan() -> None:
    """Pre-scan the CENTER-RF cine — the texture analogue of the femur-cine QMT pre-scan
    (Akadir 2026-07-16). That cine feeds ONE thing (int_cv / echo texture), and int_cv is
    corrupted by acoustic SHADOW (dropout), so `center_rf_scorer` ranks frames by shadow-freeness
    and each thumbnail gets a shadow badge. Shadow != darkness — a true shadow blacks out
    everything DEEPER in the column (no echo beneath); real dark muscle has echo below and is NOT
    penalised (the depth test). Status only — the operator still picks, and nothing here changes an
    int_cv VALUE (the aggregation gate + the withhold fallback are a separate, later step)."""
    entry = st.session_state.get("study_entry")
    learn = st.session_state.get("learn")
    bm = st.session_state.get("bundle_meta")
    if entry is None or learn is None or bm is None:
        st.warning("Pick a study + model first.")
        return
    from preflight_helpers.study_discovery import study_modalities, list_frame_bases
    mods = study_modalities(entry.study_path)
    rf = mods.get("rf")
    if rf is None or rf.raw_image_dir is None:
        st.warning("This study has no center-RF cine.")
        return
    frame_bases = list_frame_bases(rf.raw_image_dir) or []
    if not frame_bases:
        st.warning("No center-RF frames discovered.")
        return
    from labeling_app.assessment_pages.femur_scan import scan_sweep_for_femur
    from labeling_app import center_rf_scorer
    mask_cache = st.session_state.setdefault("predicted_masks", {})
    final_cache = st.session_state.setdefault("final_u8_cache", {})
    iq_cache = st.session_state.setdefault("image_quality_cache", {})
    bar = st.progress(0.0, text="Pre-scanning center-RF (shadow + image quality)…")
    ranked = scan_sweep_for_femur(
        frame_bases, learn, bm, rf.raw_image_dir,
        mask_cache=mask_cache, final_cache=final_cache, image_quality_cache=iq_cache,
        progress=lambda frac, fb: bar.progress(frac, text=f"Scanned {fb}"))
    bar.empty()
    scan = {r["frame_base"]: r for r in ranked}
    cine_masks = {fb: mask_cache[fb] for fb in scan if fb in mask_cache}
    cine_finals = {fb: final_cache[fb] for fb in scan if fb in final_cache}
    scores = center_rf_scorer.score_center_rf_cine(cine_masks, cine_finals, iq_cache)
    for fb, r in scan.items():
        r["repr_score"] = scores.get(fb, 0.0)      # texture ranking: least shadow, fullest RF
        img = cine_finals.get(fb)
        r["shadow_frac"] = (center_rf_scorer.shadow_fraction(cine_masks[fb], img)
                            if (fb in cine_masks and img is not None) else None)
    st.session_state.center_rf_scan = scan
    st.rerun()


def _center_rf_prescan_controls() -> None:
    """The center-RF tab's pre-scan button + shadow summary — the texture analogue of the femur-cine
    pre-scan. Ranks by shadow-freeness and badges shadowed frames so the operator sees WHY a clean
    frame is recommended (auditable) and still picks. RECORDED for refinement: the 3% material-shadow
    line + the SHADOW_DARK_RAIL/ECHO_FLOOR are first cuts, calibratable once we watch it on real cines."""
    if st.button("🔍 Pre-scan center-RF (shadow + image quality)",
                 type="secondary", use_container_width=True, key="center_rf_scan_btn",
                 help="Segments every center-RF frame and ranks them by how SHADOW-FREE the RF ROI is — "
                      "acoustic shadow (dropout) is the one artifact that corrupts int_cv / fascicular "
                      "contrast. Real dark muscle is NOT penalised (a shadow blacks out everything deeper; "
                      "real tissue has echo beneath). ★ marks the least-shadow frames; a shadow badge flags "
                      "the rest. Status only — you still pick."):
        _run_center_rf_scan()
    rf_scan = st.session_state.get("center_rf_scan") or {}
    sf = [r.get("shadow_frac") for r in rf_scan.values() if r.get("shadow_frac") is not None]
    if sf:
        n_clean = sum(1 for s in sf if s < 0.03)
        n_shadow = len(sf) - n_clean
        st.caption(f":gray[Center-RF shadow — {n_clean} shadow-free · {n_shadow} with material shadow "
                   f"(≥3% of the RF ROI). ★ marks the shadow-free frames int_cv should read.]")


def _should_paint_femur_tint(qmt_tier: int) -> bool:
    """The single load-bearing predicate for "should the yellow femur tint
    paint?" — Akadir 2026-05-24 audit. The picker showed three contradictory
    signals on the same thumbnail (dot=no femur, tint=paints labels,
    cue=side-of-image). The unify move: tint paints ONLY when the femur is
    QMT-usable (tier 1 or 2). Tier 3 = no tint, even if
    `_dominant_femur_mask` returns labels — those labels exist but are not
    measurable; surfacing them via tint contradicts the dot.

    This way the dot and the tint use ONE definition of "femur" (QMT-
    usability via qmt_tier), and they cannot disagree on a thumbnail."""
    return qmt_tier in (1, 2)


def _thumbnail_with_femur_roi(
    fb: str, raw_image_dir: Path, scan: Optional[dict] = None,
) -> "Image.Image | str":
    """Return a PIL image of the live-synthesised final with the dominant
    femur band tinted, or the raw jpg path if no mask is cached for this
    frame yet (st.image accepts either type). The picker calls this on
    every thumbnail; before the femur-scan button is pressed, no mask is
    cached and the raw jpg is returned unchanged (no overlay, the old
    behaviour).

    Akadir 2026-05-24 § 1f + § 2b — the tint makes the AI's femur claim
    visually verifiable. AND § 1 — the tint must agree with the dot:
    `_should_paint_femur_tint(qmt_tier)` gates the paint. Tier-3 (no QMT-
    usable femur) = no tint, even if labels exist.
    """
    pred = (st.session_state.get("predicted_masks") or {}).get(fb)
    final_u8 = (st.session_state.get("final_u8_cache") or {}).get(fb)
    if pred is None or final_u8 is None:
        return str(raw_image_dir / f"{fb}.jpg")
    # Gate the tint on QMT-usability via the femur-scan tier. The scan dict
    # carries each frame's tier; if it isn't in the scan (defensive — should
    # not happen after _ensure_predicted_masks), don't paint.
    scan = scan if scan is not None else (st.session_state.get("femur_scan") or {})
    tier = (scan.get(fb) or {}).get("tier", 3)
    if not _should_paint_femur_tint(tier):
        return Image.fromarray(final_u8)
    from labeling_app.measurements import _dominant_femur_mask
    femur = _dominant_femur_mask(pred)
    if not femur.any():
        return Image.fromarray(final_u8)
    rgb = np.stack([final_u8, final_u8, final_u8], axis=-1).astype(np.float32)
    tint = np.zeros_like(rgb)
    tint[..., 0] = FEMUR_ROI_TINT[0]
    tint[..., 1] = FEMUR_ROI_TINT[1]
    tint[..., 2] = FEMUR_ROI_TINT[2]
    alpha = femur.astype(np.float32)[..., None] * FEMUR_ROI_ALPHA
    blended = rgb * (1.0 - alpha) + tint * alpha
    return Image.fromarray(np.clip(blended, 0, 255).astype(np.uint8))


def _ai_suggested_repr(
    picked: list[str], scan: Optional[dict],
) -> Optional[str]:
    """The AI's suggested representative frame — Tier-1 ONLY.

    Akadir 2026-05-25 § 3 — a proxy-femur (Tier-2) or no-femur (Tier-3)
    frame is never a valid repr suggestion, because the headline QMT for
    the run would be degraded or absent. When no picked frame is Tier-1
    we return None; the picker's `selection_repr_status` then surfaces the
    gap explicitly (sweep has an unpicked Tier-1 → "consider picking it";
    sweep has no Tier-1 anywhere → "QMT will be degraded for this study").

    Within Tier-1, the highest RF-representative-frame score wins (Slice F —
    most central, fullest, most-complete cross-section); fewer calibrated
    flags then frame order break ties. The picker still renders the suggestion
    as `:green[✦ AI suggests as repr]` on the matching thumbnail — designation
    itself happens on p04 (Akadir 2026-05-24 § 3a).
    """
    if not picked or not scan:
        return None
    candidates: list[tuple[float, int, str]] = []
    for fb in picked:
        s = scan.get(fb)
        if s is None or s.get("tier") != 1:
            continue
        iq = s.get("image_quality") or {}
        flags = iq.get("image_quality_validity_flags") or ()
        pending = set(
            iq.get("image_quality_pending_calibration_flags") or ())
        n_cal = sum(1 for f in flags if f not in pending)
        # Slice F — among the Tier-1 picks, the highest representative-frame
        # score (R) wins; fewer calibrated flags then frame order break ties.
        # repr_score is absent for an un-scored frame / a legacy scan -> 0.0
        # (the prior flag-only ordering, so old callers/tests are unchanged).
        repr_r = float(s.get("repr_score") or 0.0)
        candidates.append((-repr_r, n_cal, fb))
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]


def _counter_label(n_picked: int) -> str:
    """N-of-3 (max 9) display; the 3 is the soft target, 9 is the hard ceiling."""
    if n_picked == 0:
        return "0 / 3 picked  (target 3, max 9)"
    if n_picked < SUGGESTED_PICKS:
        return f"{n_picked} / 3 picked  ({SUGGESTED_PICKS - n_picked} to suggested target)"
    if n_picked == SUGGESTED_PICKS:
        return f"{n_picked} / 3 picked  (target met; add more for a richer aggregate)"
    if n_picked <= MAX_PICKED:
        return f"{n_picked} / 3 picked  (above target; max {MAX_PICKED})"
    return f"{n_picked} / {MAX_PICKED} picked  (at max)"


def _toggle_pick(fb: str, role: str = "sweep") -> None:
    key = _picked_key(role)
    picked = set(st.session_state.get(key, []))
    if fb in picked:
        picked.discard(fb)
    elif len(picked) < MAX_PICKED:
        picked.add(fb)
    new_picked = sorted(picked)
    st.session_state[key] = new_picked
    # The explicit repr anchor is SWEEP-only (femur/RF median their picks, no single repr); keep
    # it valid by dropping it if its frame was removed.
    if role == "sweep" and st.session_state.get("anchor_frame") not in new_picked:
        st.session_state.anchor_frame = None


def _effective_anchor(picked: list[str]) -> str | None:
    """The frame the anchor_primary policy will actually use: the explicit
    user-marked anchor when valid, else the middle of the pick order."""
    explicit = st.session_state.get("anchor_frame")
    if explicit in picked:
        return explicit
    return picked[len(picked) // 2] if picked else None


def _short(fb: str) -> str:
    """Display label for a frame_base — HHMMSS.XXX where XXX is the
    sub-second counter that disambiguates sweep-sibling frames.

    Akadir 2026-05-25 § 4a: the picker grid showed 5 thumbnails all labelled
    `151033`, because the original `_short` truncated the 9-digit time part
    to its first 6 chars (HHMMSS), dropping the sub-second / camera index
    that distinguishes adjacent sweep captures. The frame_base format is
    `YYYYMMDD_HHMMSSXXX`; the date is dropped, the sub-second is rendered
    with a dot so the operator can read the disambiguator at a glance.
    """
    time_part = fb.split("_")[-1]
    if len(time_part) >= 9:
        return f"{time_part[:6]}.{time_part[6:9]}"
    return time_part[:6]


# ---------------------------------------------------------------------------
# Sidebar — counter + actions + live selection panel + anchor marking (F2/F3)
# ---------------------------------------------------------------------------

def render_sidebar() -> None:
    from labeling_app.assessment_pages import help_content
    st.markdown("### ③ Frame picker")
    if st.session_state.get("study_entry") is None or st.session_state.get("learn") is None:
        st.caption("Pick a study + model first.")
        help_content.render_help_expander(2)
        return

    # Is this the new 3-modality protocol? On a multimodal study the score reads
    # geometry from the femur + echo-intensity from the center-RF, so the sidebar
    # gives the three modalities co-equal billing (not the sweep as the headline
    # count) and the sweep-only femur pre-scan / repr mining is dropped — that
    # belongs to the legacy single-sweep flow (Akadir 2026-06-26 audit, #3/#13).
    from preflight_helpers.study_discovery import study_modalities as _study_modalities
    _entry = st.session_state.study_entry
    _mods = _study_modalities(_entry.study_path) if _entry is not None else {}
    is_multimodal = any(r in _mods for r in ("femur", "rf"))

    picked: list[str] = list(st.session_state.get("picked_frames", []) or [])
    n = len(picked)
    _fem = st.session_state.get("picked_frames__femur") or []
    _rf = st.session_state.get("picked_frames__rf") or []

    if is_multimodal:
        st.markdown(f"**Picks** — femur **{len(_fem)}** · center-RF **{len(_rf)}** · sweep **{n}**")
        st.caption(":gray[Size + fat ← femur · echo texture ← center-RF · sweep = legacy-comparable base.]")
    else:
        st.caption(_counter_label(n))

    # Repr status — Akadir 2026-05-25 § 3. Surfaces the picked-set verdict
    # BEFORE the operator clicks Segment + continue: whether a Tier-1
    # femur-anchored frame is in the picks (success), whether the sweep has
    # an unpicked Tier-1 candidate they should consider (warning + "Add to
    # picks" button), or whether the sweep has no Tier-1 anywhere (error,
    # QMT will be degraded). When no pre-scan has been run yet, an info box
    # nudges them to run it. The Segment + continue button stays enabled in
    # every case — the surface warns, it does not block.
    if n > 0 and not is_multimodal:
        from labeling_app.assessment_pages.qmt_review import (
            selection_repr_status,
        )
        scan_for_status = st.session_state.get("femur_scan")
        repr_status = selection_repr_status(picked, scan_for_status)
        _render = {
            "info": st.info, "success": st.success,
            "warning": st.warning, "error": st.error,
        }[repr_status["level"]]
        _render(repr_status["message"])
        if (repr_status["case"] == "unpicked_candidate"
                and repr_status["best_unpicked"]):
            best = repr_status["best_unpicked"]
            if st.button(
                f"+ Add {_short(best)} to picks",
                key="add_femur_candidate", type="secondary",
                use_container_width=True,
            ):
                _toggle_pick(best)
                st.rerun()

    # Primary action — Segment + continue. On a multimodal study the score reads ALL measurements
    # (geometry + fat + STAR + fat-fraction) from the femur cine AND the echo texture from the center-RF
    # cine, so BOTH measurement views must have at least one pick before continuing. The sweep is only an
    # own-study echo cross-check, so it is NOT required. (Akadir 2026-06-26: a femur-only selection
    # must NOT let you segment + continue.) A legacy single-sweep study keeps the old rule: any pick.
    _seg_n = (n + len(_fem) + len(_rf)) if is_multimodal else n
    if is_multimodal:
        _missing = []
        if "femur" in _mods and not _fem:
            _missing.append("femur")
        if "rf" in _mods and not _rf:
            _missing.append("center-RF")
        seg_blocked = bool(_missing)
        if seg_blocked:
            st.caption(":red[Pick at least one frame from each measurement view before continuing — "
                       f"still need: {', '.join(_missing)}.]")
    else:
        seg_blocked = (n == 0)
    if st.button(f"Segment {_seg_n} frame(s) + continue →", type="primary",
                 use_container_width=True,
                 disabled=seg_blocked, key="seg_continue"):
        st.session_state.phase = 3
        st.rerun()
    # Femur scan (QMT fast-follow) — LEGACY single-sweep only. Segments the sweep
    # and tags each frame's QMT tier. On a multimodal study the femur is measured
    # on the femur cine (its own in-tab pre-scan), and the shallow sweep usually
    # has no femur to find — so this is dropped here (Akadir 2026-06-26 audit, #3;
    # this also clears the bogus femur tint / QMT badge / geometry-ranked
    # "recommended" from the sweep grid, since they ride this scan).
    if not is_multimodal and st.button(
                 "🔍 Pre-scan sweep (femur + image quality)",
                 type="secondary", use_container_width=True,
                 key="femur_scan_btn",
                 help="Segments every sweep frame and tags each thumbnail "
                      "with three cues: (1) a femur tier (Tier 1 = femur-"
                      "anchored, Tier 2 = proxy, Tier 3 = no femur); (2) any "
                      "image-quality issue the trusted detectors find; (3) "
                      "a yellow band on the thumbnail itself — the AI "
                      "segmenter's actual femur prediction on the full-size "
                      "image, cleaned to the largest connected component, "
                      "so the operator can SEE where the AI thinks the "
                      "thigh bone is. Same femur mask the QMT measurement "
                      "reads from. Status only — you still pick the frames."):
        _run_femur_scan()
    scan = st.session_state.get("femur_scan")
    if scan and not is_multimodal:
        # Akadir 2026-05-25 v2 § 2 — make the yellow tint's provenance
        # explicit so the operator doesn't have to ask. The tint is real
        # segmenter output (not a heuristic), filtered to the dominant
        # connected femur component, painted only on tier-1 / tier-2
        # thumbnails (`_should_paint_femur_tint`) so the dot and the tint
        # tell one consistent story.
        st.caption(
            ":gray[The :orange[yellow band] on each thumbnail is the AI "
            "segmenter's actual femur prediction (largest connected "
            "component); same mask the QMT reads from.]"
        )
        n1 = sum(1 for s in scan.values() if s["tier"] == 1)
        n2 = sum(1 for s in scan.values() if s["tier"] == 2)
        n3 = sum(1 for s in scan.values() if s["tier"] == 3)
        # Image-quality counts — "issue" means borderline or invalid per the
        # trusted (calibrated) detectors. Subagent B's discipline at the
        # picker: pending detectors (e.g. `wrong_site` at 80%) would paint
        # nearly every thumbnail orange, so only calibrated drives the cue.
        iq_issue = sum(
            1 for s in scan.values()
            if (s.get("image_quality") or {}).get(
                "image_quality_aggregate_tier") in ("borderline", "invalid"))
        st.caption(
            f"Femur — :green[{n1} standard] · :orange[{n2} proxy] · "
            f":red[{n3} no femur]  of {len(scan)}. A valid QMT needs a "
            f"femur frame."
        )
        if iq_issue:
            st.caption(
                f"Image quality — :orange[{iq_issue} frame(s) with a "
                f"trusted-detector concern]; see the cues under each thumbnail.")
        under_gain, ug_frac = _study_under_gain(scan)
        if under_gain:
            st.warning(
                f"⚠ This whole study reads **under-gained** — the dark-image check "
                f"fires on {ug_frac * 100:.0f}% of frames. That is an acquisition / "
                f"gain problem, not per-frame, so the per-frame dark flags are "
                f"demoted to this one cue (not painted on every thumbnail). You can "
                f"still pick frames; label the dark detector in p4x.")
    cols = st.columns(2)
    with cols[0]:
        if st.button("Clear picks", type="secondary", use_container_width=True,
                     disabled=(n == 0), key="clear_picks"):
            st.session_state.picked_frames = []
            st.session_state.anchor_frame = None
            st.rerun()
    with cols[1]:
        if st.button("← Model", type="secondary", use_container_width=True,
                     key="back_to_model"):
            st.session_state.phase = 1
            st.rerun()

    st.divider()
    st.markdown(f"**Selection · {n}/{SUGGESTED_PICKS}**")
    if not picked:
        st.caption("No frames picked yet. Pick at least 1 to enable Segment + continue.")
        help_content.render_help_expander(2)
        return

    entry = st.session_state.study_entry
    raw_image_dir = entry.raw_image_dir if entry else None

    st.caption(
        "Designate the **representative frame** on the review page (phase 4) — "
        "it carries the big image + per-frame quality detail you need to make "
        "the call. The picker shows the AI's suggestion (✦) on the grid."
    )
    for fb in picked:
        with st.container(border=False):
            cols = st.columns([1, 2])
            with cols[0]:
                if raw_image_dir is not None:
                    st.image(str(raw_image_dir / f"{fb}.jpg"), width=58)
            with cols[1]:
                st.markdown(f"**{_short(fb)}**")
                if st.button("× remove", key=f"sel_x_{fb}",
                             type="secondary", use_container_width=True):
                    _toggle_pick(fb)
                    st.rerun()

    help_content.render_help_expander(2)


# ---------------------------------------------------------------------------
# Main area — full-width thumbnail grid
# ---------------------------------------------------------------------------

_UNDER_GAIN_PERVASIVE_FRAC = 0.8  # matches study_level_qa.pervasive_studies default


def _study_under_gain(scan: dict) -> tuple[bool, float]:
    """E4 study-level demotion: is `dark_clip_high` pervasive across the scanned
    frames? Returns (is_pervasive, firing_fraction). A flag firing on ~every frame
    is an acquisition/gain problem, not a per-frame one — so the picker shows ONE
    study-level cue and suppresses the per-frame flood (the policy in
    scripts/preflight_helpers/study_level_qa.py; threshold 0.8)."""
    if not scan:
        return False, 0.0
    fired = ["dark_clip_high" in ((s.get("image_quality") or {}).get(
                 "image_quality_validity_flags") or [])
             for s in scan.values()]
    frac = (sum(fired) / len(fired)) if fired else 0.0
    return frac >= _UNDER_GAIN_PERVASIVE_FRAC, frac


def _thumbnail_grid(raw_image_dir: Path, frame_bases: list[str], role: str = "sweep") -> None:
    picked = set(st.session_state.get(_picked_key(role), []))
    at_max = len(picked) >= MAX_PICKED
    # Pre-scan results per modality: the sweep's femur+QMT scan, the femur cine's own QMT pre-scan
    # (femur_cine_scorer ranks it femur-anchoring-then-fullness), and (2026-07-16) the center-RF cine's
    # SHADOW pre-scan (center_rf_scorer ranks it least-shadow-first — the one artifact that corrupts int_cv).
    _scan_key = {"sweep": "femur_scan", "femur": "femur_cine_scan", "rf": "center_rf_scan"}.get(role)
    scan = (st.session_state.get(_scan_key) or {}) if _scan_key else {}
    explicit_anchor = st.session_state.get("anchor_frame") if role == "sweep" else None
    # Study-level under-gain: when dark fires across ~every frame, the one banner in
    # the summary carries it; suppress the per-frame dark cue so the grid is not a
    # wall of red (the per-frame flood Akadir flagged on AKA9001).
    study_under_gain, _ = _study_under_gain(scan)
    # Surface the best frames to pick BEFORE selection (Akadir 2026-06-09): the
    # top-3 by the landmark score (largest clean RF, centering tiebreaker) get a
    # "recommended" badge, so the operator sees which frames are good without
    # having to add them first. repr_score is the redesigned area-primary score.
    _ranked = sorted(scan.items(), key=lambda kv: kv[1].get("repr_score", 0.0),
                     reverse=True)
    recommended = [fb for fb, r in _ranked if r.get("repr_score", 0.0) > 0][:3]

    for row_start in range(0, len(frame_bases), GRID_COLS):
        row_bases = frame_bases[row_start:row_start + GRID_COLS]
        cols = st.columns(GRID_COLS)
        for col, fb in zip(cols, row_bases):
            with col:
                is_picked = fb in picked
                is_repr = is_picked and (fb == explicit_anchor)
                # Two visual states: unselected (no border), selected
                # (bordered, ✓). Repr designation lives on p04 (Akadir 2026-05-24
                # § 1g + § 3a). The picker no longer suggests a repr — the old
                # "AI suggests as repr" hint flipped a recommended frame's badge on
                # selection and could land on a NON-recommended picked frame,
                # contradicting the "recommended" guidance (Akadir 2026-06-11). The
                # picker GUIDES selection via "recommended"; the repr is confirmed
                # on the review page.
                with st.container(border=is_picked):
                    # Layer-1 femur ROI tint (Akadir 2026-05-24 § 1f + § 2b)
                    # — only kicks in when the pre-scan has run and a mask
                    # is cached. Before the scan, the raw jpg is shown.
                    st.image(_thumbnail_with_femur_roi(fb, raw_image_dir, scan),
                             width=THUMB_WIDTH_PX)
                    caption_bits = [_short(fb)]
                    if is_picked:
                        caption_bits.append("✓")
                    st.caption(" ".join(caption_bits))
                    if is_repr:
                        st.markdown(":green[★ repr (confirmed on review)]")
                    elif fb in recommended:
                        _why = {"femur": "femur-anchored · fullest",
                                "sweep": "largest clean RF",
                                "rf": "shadow-free · fullest RF"}.get(role, "recommended")
                        st.markdown(
                            f":green[★ recommended #{recommended.index(fb) + 1} · {_why}]")
                    fs = scan.get(fb)
                    if fs:
                        # center-RF is a TEXTURE frame — its status is shadow, not the femur QMT tier.
                        if role == "rf":
                            _sf = fs.get("shadow_frac")
                            if _sf is not None and _sf >= 0.03:
                                st.markdown(f":orange[◐ shadow {int(round(_sf * 100))}% of RF ROI]")
                        else:
                            st.markdown(_femur_badge(fs))
                        # Picker image-quality cue — calibrated detectors
                        # only; silent on a clean frame. `edge_clip_lateral`
                        # never surfaces here in practice (it does not taint
                        # a headline-4 measurement, so it never raises the
                        # aggregate tier above valid on its own — Akadir
                        # 2026-05-24 § 1c). Akadir 2026-05-25 v2 § 1 — when
                        # the tier dot already says "no femur" (tier 3),
                        # silence the `femur_out_of_frame` prose so the
                        # thumbnail doesn't double-surface the same fact.
                        suppress = (("femur_out_of_frame",)
                                    if fs.get("tier") == 3 else ())
                        if study_under_gain:
                            suppress = suppress + ("dark_clip_high",)
                        cue = qa_surfacing.picker_qa_cue(
                            fs.get("image_quality"),
                            suppress_flags=suppress,
                            allow_pending=qa_surfacing._PICKER_PENDING_ALLOW)
                        if cue:
                            st.markdown(cue)
                    if is_picked:
                        if st.button("× remove", key=_pick_btn_key(role, fb),
                                     type="secondary", use_container_width=True):
                            _toggle_pick(fb, role)
                            st.rerun()
                    else:
                        if st.button("+ add", key=_pick_btn_key(role, fb), type="primary",
                                     disabled=at_max, use_container_width=True):
                            _toggle_pick(fb, role)
                            st.rerun()


def render() -> None:
    if st.session_state.study_entry is None:
        st.info("Pick a study first (phase 1).")
        return
    if st.session_state.learn is None:
        st.info("Pick a model first (phase 2).")
        return

    entry = st.session_state.study_entry
    sm = entry.meta.to_ui_dict()
    header_demos = (f"({sm['age']}y {sm['sex'] or '—'}, BMI {sm['bmi']:.1f})"
                    if sm["bmi"] is not None else "")
    st.subheader(f"Pick frames — {sm['study_code']} {header_demos}")

    if entry.raw_image_dir is None:
        st.error("No RawImagesForResearch_ directory on this study.")
        return

    # The SWEEP frames feed the existing femur pre-scan path (which reads `available_frame_bases`),
    # so cache them here exactly as before.
    from preflight_helpers.study_discovery import list_frame_bases, study_modalities
    sweep_bases = st.session_state.get("available_frame_bases") or []
    if not sweep_bases:
        sweep_bases = list_frame_bases(entry.raw_image_dir)
        st.session_state.available_frame_bases = sweep_bases
    if not sweep_bases:
        st.error("No frames discovered in RawImagesForResearch_.")
        return

    # v8 3-phase: one tab per available modality (femur · center-RF · sweep). A LEGACY
    # single-modality study (no captures/) has only the sweep -> render its grid as before.
    mods = study_modalities(entry.study_path)
    ordered = [r for r in _TAB_ORDER if r in mods]
    if len(ordered) <= 1:
        st.caption(
            f"Up to {MAX_PICKED} frames; {SUGGESTED_PICKS} is the suggested target. Click a "
            f"thumbnail's button to toggle it. The count + Segment-and-continue are in the sidebar."
        )
        _thumbnail_grid(entry.raw_image_dir, sweep_bases, role="sweep")
        return

    st.caption(
        "Three captures — pick per tab. The score reads **all measurements** (RF thickness / area, fat, "
        "QMT → STAR, fat fraction) from the **femur cine**, and **only the echo texture** from the "
        "**center-RF cine**. The **sweep** is only an own-study echo cross-check — no cohort value. "
        "Each tab aggregates your kept picks."
    )
    # The radio labels MUST be STABLE across reruns (the modality name only — NO live pick count).
    # A keyed st.radio whose option LABELS change between reruns is reset to its default by Streamlit;
    # embedding the count here made an RF pick (which reruns) change the RF label and bounce the
    # operator back to the Femur tab — the exact bug. The per-modality counts live in the sidebar
    # ("Picks — femur N · center-RF N · sweep N"), so they don't belong on the selector. Verified:
    # stable labels persist the selection across the pick rerun in both the browser and AppTest.
    labels = {r: _MODALITY_TAB[r][0] for r in ordered}
    # A keyed st.radio — NOT st.tabs — selects the active capture. st.tabs keeps its active tab only
    # on the client, so it reset to the first tab (femur) on every server rerun; and a frame pick
    # reruns, so picking a center-RF frame bounced the operator back to the femur tab (Akadir
    # 2026-06-26). A session-state-keyed radio with STABLE labels persists the choice across the pick
    # rerun. Only the chosen modality's grid renders (this also removes the cross-modality pick-button
    # overlap that st.tabs was hiding by mounting every grid at once).
    if st.session_state.get("picker_modality") not in ordered:
        st.session_state["picker_modality"] = ordered[0]
    role = st.radio("Capture", options=ordered, key="picker_modality",
                    format_func=lambda r: labels[r], horizontal=True,
                    label_visibility="collapsed")
    st.caption(_MODALITY_TAB[role][1])
    raw_dir = entry.raw_image_dir if role == "sweep" else mods[role].raw_image_dir
    bases = sweep_bases if role == "sweep" else list_frame_bases(raw_dir)
    if not bases:
        st.warning("No frames in this capture.")
        return
    depth = mods[role].depth_cm
    if depth is not None:
        st.caption(f":gray[{len(bases)} frames · depth {depth} cm]")
    if role == "femur":
        _femur_cine_prescan_controls()
    elif role == "rf":
        _center_rf_prescan_controls()
    _thumbnail_grid(raw_dir, bases, role=role)

"""Phase 5 — Assessment view (the boss-facing screen).

Renders the v2.2 Q5 layout:
  - Study header (study_code + parsed demographics; PHI-clean)
  - Segmentation overlay on the anchor frame + eroded contour
  - Headline 4 features (STAR, full_thickness_q50_mm, fascicular contrast / int_cv_within, RF thickness)
  - Collapsible secondary features (muscle/fat, QMT, RF area)
  - Cohort placement panel: density strip + marker, per measurement, three rows
    (overall + age-matched ±10y + sex-matched)
  - Greyed "Earlier scan" + "Heckmatt-axis features" trajectory affordances
  - Raters' read footer (cohort cases only)
  - Run-status banner: live or "Loaded cached preflight run from <timestamp>"
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import plotly.graph_objects as go
import streamlit as st

from preflight_helpers import condition_model, env, run_lifecycle
from preflight_helpers.cohort_placement import (
    HIGHER_IS_BETTER, LEGACY, MIXED, NEW, OLD, LOWER_IS_BETTER, cohort_aware_phrase, cohort_box,
    cohort_points, consensus_grade, is_intensity_feature, percentile,
    percentile_phrase, rater_votes,
)
from preflight_helpers.landmark import LANDMARK_FEATURES, compute_study_landmark
from preflight_helpers.run_pipeline import (
    HEADLINE_FEATURES, SECONDARY_FEATURES, _spacing_row_from_dicom,
    _spacing_row_from_transport, run_pipeline,
)
from preflight_helpers.study_bootstrap import study_bootstrap

from labeling_app.assessment_pages import previous_runs_sidebar, qa_surfacing
from labeling_app.assessment_pages.measurement_overlay import annotate_overlay
from labeling_app.assessment_pages.session_sig import (
    build_picked_frames, current_input_sig, session_bundle_sha,
)
from labeling_app.assessment_pages.condition_panel import (
    CLINICAL_AXIS, band_strip_figure, breakdown_block, triplet_figure,
)


# F7 — direction-of-better visual scheme, driven by percentile_phrase sentiment.
_SENTIMENT_ICON = {"concern": "⚠", "strength": "✓", "neutral": "•"}
_SENTIMENT_COLOR = {"concern": "red", "strength": "green", "neutral": "gray"}

# Below this many studies, a demographic band is too thin to support a precise
# percentile — show a position word instead of a false pXX (Akadir 2026-06-10:
# the single number must not pretend to precision the comparison group can't give).
_THIN_BAND_N = 20


def _band_bit(label: str, block: Optional[dict]) -> str:
    """A demographic band rendered as 'label: pXX (n=NN)', or a position word when
    the band is too thin to support a precise percentile."""
    if not block or block.get("pctile") is None:
        return ""
    n = block.get("n", 0)
    p = block["pctile"]
    if n < _THIN_BAND_N:
        word = "above typical" if p >= 75 else "below typical" if p <= 25 else "typical"
        return f"{label}: {word} (n={n})"
    return f"{label}: p{p:.0f} (n={n})"


# ---------- N4 run lifecycle: one-time draft compute + cache-invalidation ----

def _ensure_run() -> dict:
    """Compute the assessment run once, cache it, and keep it fresh (N4).

    The result is shaped like RunResult fields plus `record`/`created_at_utc`.

    Lifecycle behaviour:
      * **Cache hit** — the input signature (picks + gates + bundle + class
        ids) is unchanged since the last compute/load → return the cached run.
      * **Cached-mode** run loaded from the previous-runs sidebar → return it
        read-only; never recomputed while its inputs are untouched.
      * **Inputs changed** — the user edited picks/gates/model after a
        finalized or cached run → branch a *fresh draft* (`persist="draft"`,
        writes to data/runs_drafts/, NOT runs.jsonl). The draft reaches the
        ledger only when the user clicks "Accept run".
    """
    sig = current_input_sig()
    cached = st.session_state.get("run_result")

    # Cache hit — inputs identical to the last compute / cached-load.
    if (cached is not None and sig is not None
            and st.session_state.get("run_input_sig") == sig):
        return cached
    # Cached-mode run whose signature can't be recomputed (legacy ledger
    # record, or bundle not loaded) — show it as-is rather than recomputing.
    if cached is not None and st.session_state.get("run_mode") == "cached" and sig is None:
        return cached

    entry = st.session_state.study_entry
    bundle = st.session_state.bundle
    if entry is None or bundle is None:
        raise RuntimeError("Study or bundle missing in session state")

    picked = build_picked_frames()
    # v8: the routed score can come from femur/RF picks ALONE (no sweep pick) — run_pipeline runs with
    # an empty sweep aggregate and route_study_aggregate overlays the real femur/RF features. So the
    # analysis-included gate must span ALL THREE pick stores (using the same _gate_kept helper that
    # actually feeds the routed aggregate), not just the legacy sweep picks. Pre-fix this raised
    # "No analysis-included frames" the instant an operator picked femur+RF and no sweep — the v8
    # happy path — crashing the assessment page.
    from preflight_helpers.feature_routing import _gate_kept
    sweep_ok = any(pf.gates.use_for_analysis and pf.gates.usable for pf in picked)
    fem_ok = bool(_gate_kept(st.session_state.get("picked_frames__femur") or [],
                             st.session_state.get("gates__femur") or {}))
    rf_ok = bool(_gate_kept(st.session_state.get("picked_frames__rf") or [],
                            st.session_state.get("gates__rf") or {}))
    if not (sweep_ok or fem_ok or rf_ok):
        raise RuntimeError("No analysis-included frames; revisit phase 4")

    # Inputs changed. A finalized or cached run already in state means the user
    # edited inputs after accepting/loading it → branch a fresh draft.
    if (st.session_state.get("run_lifecycle") == "finalized"
            or st.session_state.get("run_mode") == "cached"):
        st.session_state.run_lifecycle = "none"
        st.session_state.run_mode = "live"

    with st.spinner("Computing 9-feature assessment + cohort placement …"):
        res = run_pipeline(
            study_code=entry.meta.study_code,
            study_path=entry.study_path,
            study_meta=entry.meta,
            picked_frames=picked,
            bundle=bundle,
            bundle_sha=session_bundle_sha(),
            mode="live",
            persist="draft",   # N4 — phase-5 results are drafts, not ledger entries
            allow_empty=(len(picked) == 0),   # v8: femur/RF-only selection → empty sweep run, routed below
        )
        # v8 feature routing — read each feature from the modality that measures it best: geometry +
        # STAR/QMT + fat from the femur cine, int_cv from the center-RF cine, the rest from the sweep.
        # These are the SAME anatomical quantities the legacy model already uses (legacy QMT/STAR came
        # from femur-bearing frames), so the model applies — a recorded measurement-view shift, not
        # OOD (docs/implementation.md A5). A legacy single-modality study routes all to the sweep.
        from preflight_helpers.feature_routing import route_study_aggregate, placements_for
        agg = route_study_aggregate(
            entry.study_path, res.aggregate,
            picks_femur=st.session_state.get("picked_frames__femur") or [],
            picks_rf=st.session_state.get("picked_frames__rf") or [],
            bmi=entry.meta.bmi, bundle=bundle,
            learn=st.session_state.get("learn"), bundle_meta=st.session_state.get("bundle_meta"),
            gates_femur=st.session_state.get("gates__femur") or {},
            gates_rf=st.session_state.get("gates__rf") or {},
            texture_choice=st.session_state.get("texture_choice"),   # #16/#17 operator p04 gate choice
            texture_refine=st.session_state.get("texture_refine"),    # Phase 2 depth-band/erosion refine
        )
        routed_roles = agg.get("_routed_modalities") or []
        placements = placements_for(agg, entry.meta.age, entry.meta.sex) if routed_roles else res.placements
        # Persist the ROUTED aggregate into the draft so Accept/reload + the ledger match the
        # displayed score (run_pipeline persisted the SWEEP aggregate before routing ran).
        if routed_roles and res.record:
            res.record["aggregate"] = agg
            res.record["placements"] = placements
            res.record["routed_modalities"] = routed_roles
            # Phase 3 (Akadir 2026-07-14): capture the RF-texture audit PROVENANCE — the operator's
            # choice (None=auto / keep / fix / withhold) + the derived state — so a reloaded run knows how
            # it vetted the texture, and the per-study cross-run view can surface how each session did.
            # Recorded on EVERY routed study (auto by default); int_cv itself already rides in `aggregate`.
            res.record["texture_choice"] = st.session_state.get("texture_choice")
            res.record["texture_state"] = (_texture_state({"aggregate": agg}) or {}).get("state")
            res.record["texture_refine"] = st.session_state.get("texture_refine")   # {erosion_px, band} or None
            # Persist the routed femur/RF frame identities so a reloaded run recomputes off the SAME
            # picks (not empty sweep-only picks) and frame_set.signature is honest (Akadir 2026-07-20).
            # pop so the private `_routed_picks` key never persists into the stored aggregate; agg is
            # the same ref as res.record["aggregate"], so the pop cleans that too.
            _rp = agg.pop("_routed_picks", None) or {}
            if _rp:
                res.record["routed_picks"] = _rp
                res.record["frame_set"] = run_lifecycle.routed_frame_set(res.record, _rp)
            run_lifecycle.write_draft(res.record)
    cached = {
        "run_id": res.run_id,
        "aggregate": agg,
        "frame_features": res.frame_features,
        "placements": placements,
        # the refine params ride the LIVE run_data too, not only the stored record — the trust
        # strip's "(refined region)" tag and the RF band visual read them here (2026-08-14; the
        # first render proved the record had them while the page dict didn't).
        "texture_refine": st.session_state.get("texture_refine"),
        "routed_modalities": routed_roles,
        "record": res.record,
        "record_path": str(res.record_path) if res.record_path else None,
        "created_at_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    st.session_state.run_result = cached
    st.session_state.run_input_sig = sig
    st.session_state.run_lifecycle = "draft"
    st.session_state.run_mode = "live"
    return cached


# ---------- Study landmark: the selection-independent second placement ----------

# Relative gap between the selected-frame value and the study landmark beyond which
# we nudge — but ONLY when the chosen frame falls BELOW the landmark (it then
# under-represents the study's fullest cross-sections, so its percentile reads low).
# A pick at/above the landmark is fine; within the gap, the two agree closely.
_LANDMARK_DIVERGENCE = 0.12


def _ensure_landmark(entry, age, sex, routed=()) -> dict:
    """Compute (cached) the study's LANDMARK value + cohort placement for each size feature — the
    median over the top-5 fullest CLEAN frames, the SELECTION-INDEPENDENT number.

    The landmark exists for the LEGACY long sweep — a lateral→medial pass where the fullest clean
    cross-section is selection-independent. A v8 routed study has NO landmark: its size features come
    from the center SHORT-sweeps, which are CENTER shots in sweep form (every frame is the same center
    cross-section), so the operator's picks ARE the value — there is no separate 'fullest' to find
    (Akadir 2026-06-26). Returns empty (no landmark, no reason) for a routed study; the sweep landmark
    for a legacy single-sweep study, unchanged.

    Returns {"features": {feature: {value, overall, age_matched, sex_matched, frames}}, "reason": str|None}."""
    if routed:
        return {"features": {}, "reason": None}   # center short-sweeps: the picks are the value
    src = st.session_state.get("cohort_source") or _default_cohort_source()
    cache_key = (entry.meta.study_code, session_bundle_sha(), src,
                 len(st.session_state.get("femur_scan") or {}))
    cached = st.session_state.get("_landmark_cache")
    if cached and cached.get("key") == cache_key:
        return cached["result"]
    features: dict = {}
    reason: Optional[str] = None
    try:
        # LEGACY single-sweep: the sweep IS the size source -> sweep landmark.
        scan = st.session_state.get("femur_scan") or {}   # {frame_base: entry} dict
        masks_all = st.session_state.get("predicted_masks") or {}
        grades = st.session_state.get("image_quality_cache") or {}
        finals = st.session_state.get("final_u8_cache") or {}
        # scan is keyed by frame_base — the keys ARE the frame_bases; do NOT iterate as a list of dicts.
        sweep_masks = {fb: masks_all[fb] for fb in scan if fb in masks_all}
        if not scan:
            reason = "the sweep pre-scan has not run this session (run it in frame selection)"
        elif not sweep_masks:
            reason = "the sweep scan ran but no frame masks are cached"
        else:
            spacing = (_spacing_row_from_transport(entry.study_path, list(sweep_masks))
                       or _spacing_row_from_dicom(
                           entry.study_path,
                           next(iter(sweep_masks.values())).shape[:2]))
            lm = compute_study_landmark(sweep_masks, grades, finals, spacing, k=5)
            for feat, val in lm["values"].items():
                pl = percentile(feat, val, age=age, sex=sex, source=src)
                features[feat] = {"value": val, "overall": pl.overall,
                                  "age_matched": pl.age_matched,
                                  "sex_matched": pl.sex_matched, "frames": lm["frames"]}
            if not features:
                reason = f"no clean landmark frame among {len(sweep_masks)} scanned"
    except Exception as e:                       # never let the landmark crash p05
        reason = f"{type(e).__name__}: {e}"[:140]
    result = {"features": features, "reason": reason}
    st.session_state["_landmark_cache"] = {"key": cache_key, "result": result}
    return result


# ---------- N4 run-status panel: Draft → Accept → Finalized ----------

def _accept_run() -> None:
    """Promote the open draft to the study's ledger (N4 "Accept run")."""
    rr = st.session_state.get("run_result") or {}
    run_id = rr.get("run_id")
    if not run_id:
        st.error("No draft run to accept.")
        return
    try:
        _rid, run_dir = run_lifecycle.finalize_draft(run_id)
        rr["record_path"] = str(run_dir / "record.json")
        st.session_state.run_result = rr
        # The release surface's teeth (spec §3.2): checked boxes bind their verdict to THIS
        # accepted run. A failure here must be VISIBLE — a silently dropped human verdict is
        # worse than an error banner — so no blanket except around the append itself.
        _rel = bool(st.session_state.pop("release_texture_ck", False))
        _cfm = bool(st.session_state.pop("confirm_size_ck", False))
        _ctb = bool(st.session_state.pop("contribute_frames_ck", False))
        if _rel or _cfm or _ctb:
            import json as _json
            from preflight_helpers import release_actions as _ra
            try:
                _rec = _json.load(open(rr["record_path"], encoding="utf-8"))
                _n = _ra.record_release(_rec, release_texture=_rel, confirm_size=_cfm,
                                        contribute=_ctb)
                if _n:
                    st.toast(f"{_n} action(s) recorded against {run_id}")
                else:
                    st.warning("A checked action could not be recorded (missing run number, "
                               "binding snapshot, or per-frame features) — record it with "
                               "measurement_verdict.py instead.")
            except Exception as _e:                      # noqa: BLE001 — surfaced, not swallowed
                st.warning(f"Release NOT recorded ({type(_e).__name__}) — use "
                           "measurement_verdict.py; the accepted run itself is safe.")
    except FileNotFoundError:
        # Draft file already gone (double-click, or gc swept it) — the run is
        # effectively accepted-or-lost; mark finalized so the UI is consistent.
        pass
    st.session_state.run_lifecycle = "finalized"
    st.rerun()


def _start_afresh() -> None:
    """N4 "Start afresh" — discard the open draft, reset picks/gates, keep
    study + model, return to the frame picker."""
    from labeling_app.assessment_app import reset_run_state  # lazy — import cycle
    reset_run_state()
    st.rerun()


def _picked_gate_counts(run_data) -> tuple:
    """(analysis-included, total picked) across the THREE pick stores — the same reckoning the
    analysis-included guard uses (`_gate_kept` + the sweep's own PickedFrame gates), so what the
    draft banner reports matches what actually feeds the routed aggregate. Falls back to the
    sweep-shaped `frame_features` record only when session state is gone (a reloaded run).
    Akadir 2026-09-05."""
    from preflight_helpers.feature_routing import _gate_kept
    kept = total = 0
    try:
        picked = build_picked_frames()
    except Exception:
        picked = []
    total += len(picked)
    kept += sum(1 for pf in picked if pf.gates.use_for_analysis and pf.gates.usable)
    for role in ("femur", "rf"):
        bases = st.session_state.get(f"picked_frames__{role}") or []
        total += len(bases)
        kept += len(_gate_kept(bases, st.session_state.get(f"gates__{role}") or {}))
    if total == 0:                      # no session state (e.g. a run reloaded cold)
        ffs = run_data.get("frame_features", []) or []
        total = len(ffs)
        kept = sum(1 for v in ffs
                   if (v.get("gates") or {}).get("use_for_analysis")
                   and (v.get("gates") or {}).get("usable"))
    return kept, total


def _run_status_panel(run_data: dict) -> None:
    """The Draft → Accept → Finalized banner + actions (N4).

    Replaces the old live/cached banner. Three states:
      * cached   — read-only, loaded from the ledger via the sidebar.
      * draft    — computed, not yet saved; Accept promotes it to the ledger.
      * finalized — accepted (or loaded then re-accepted); in the ledger.
    """
    lifecycle = st.session_state.get("run_lifecycle", "none")
    run_mode = st.session_state.get("run_mode", "live")
    ts = run_data.get("created_at_utc", "—")

    if run_mode == "cached":
        st.info(f"Loaded cached preflight run from {ts}")
        if st.button("Start afresh", key="afresh_cached", type="secondary"):
            _start_afresh()
        st.caption(
            ":gray[Read-only — this run came from the study's ledger. Edit "
            "picks or gates upstream to branch a fresh draft from it.]"
        )
        return

    if lifecycle == "draft":
        # The count must span ALL THREE pick stores, exactly like the analysis-included guard
        # above (:104) — `run_data["frame_features"]` is the SWEEP-shaped record, so on the v8
        # happy path (femur + centre-RF, no sweep pick) it carries no gates and the banner read
        # "0 of 3 frame(s) in the aggregate" while the sidebar correctly said 3. Display-only,
        # but it contradicted the panel it sits on top of. Same bug class as the guard's own
        # pre-fix, missed here — 2026-09-05.
        n_anal, n_total = _picked_gate_counts(run_data)
        bundle = st.session_state.get("bundle")
        model_name = bundle.display_name()[:46] if bundle is not None else "?"
        st.warning(
            f"⚠ **Draft run — computed, not yet saved.** "
            f"model `{model_name}…` · {n_anal} of {n_total} frame(s) in the "
            f"aggregate. Review it below, then **Accept run** to add it to "
            f"this study's ledger."
        )
        cols = st.columns([1, 1, 4])
        with cols[0]:
            if st.button("✓ Accept run", key="accept_run", type="primary"):
                _accept_run()
        with cols[1]:
            if st.button("Start afresh", key="afresh_draft", type="secondary"):
                _start_afresh()
        return

    if lifecycle == "finalized":
        st.success(f"✓ Run accepted — saved to this study's ledger at {ts}.")
        if st.button("Start afresh", key="afresh_final", type="secondary"):
            _start_afresh()
        st.caption(
            ":gray[Editing picks or gates upstream branches a new draft run — "
            "the accepted run stays in the ledger unchanged.]"
        )
        return

    # "none" — _ensure_run always sets draft/finalized, so this is a safety net.
    st.info("Run computed.")


# ---------- Renderers ----------

# Display labels + per-feature digits for the assessment card — sourced from the
# single feature_catalog so they can never drift from the bootstrap / sidebar
# lists. (The fat features were added to THIS card dict but NOT to the sidebar
# labels or BOOTSTRAP_FEATURES, which crashed the previous-runs sidebar and
# blanked fat across runs.) Catalog values match what this card rendered before.
from preflight_helpers.feature_catalog import (  # noqa: E402
    FEATURE_LABELS as _FEATURE_LABELS, DIGITS as _FEATURE_UNIT_DIGITS,
)


def _fmt_value(feature: str, value: Optional[float]) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "—"
    digits = _FEATURE_UNIT_DIGITS.get(feature, 2)
    return f"{value:.{digits}f}"


def _qmt_provenance_note(run_data: dict) -> Optional[dict]:
    """Display state for qmt_mm / star_ratio when their provenance is not the
    femur-anchored standard — None when standard (normal display).

    The headline cards, the cohort rows and the F5 panel all key off this so
    QMT and STAR are never shown as cohort-comparable when they are not. Keys:
    `colour` (streamlit), `badge` (short), `caption` (the why)."""
    agg = run_data.get("aggregate", {}) or {}
    prov = agg.get("qmt_provenance")
    if prov == "standard":
        return None
    if prov == "proxy":
        return {
            "colour": "orange", "badge": "proxy QMT — not femur-anchored",
            "caption": ("QMT and STAR here are a proxy — an RF+VI muscle-stack "
                        "span, not a femur-anchored measurement. Not comparable "
                        "to the standard-QMT cohort, so cohort placement is "
                        "suppressed for these two."),
        }
    if agg.get("qmt_mm") is None:
        return {
            "colour": "red", "badge": "not measurable — no femur",
            "caption": ("No femur in any analysis frame — QMT and STAR "
                        "(STAR = QMT / BMI) are not measurable for this run."),
        }
    return {
        "colour": "orange", "badge": "provenance unverified",
        "caption": ("This run predates the QMT provenance gate — its QMT and "
                    "STAR cannot be vouched for. Re-run for a checked value."),
    }


# F8 — cohort-placement viz redesign. The old long-tailed density strip read
# as "uninterpretable in the first view" (audit). Replaced with a box-plot +
# a prominent case marker: box = cohort p25–p75, whiskers p10–p90, line p50,
# a bold diamond + pXX label for this case, and a faint direction-of-better
# half-band. The x-range is clamped to the p10–p90 span (+ the case value) so
# the long cohort tails no longer swamp the chart.

_ROW_LABELS = ("Overall", "Age ±10y", "Sex-matched")
_ROW_KEYS = ("overall", "age_matched", "sex_matched")


def _cohort_box_figure(feature: str, value: Optional[float],
                       box_data: dict, placement: dict,
                       points: Optional[dict] = None) -> go.Figure:
    """Box-plot + case marker, three rows (overall / age ±10y / sex-matched).

    `points` (from cohort_points) overlays the actual study values as a faint
    dot-strip on any cohort small enough to need it — so a box resting on a
    handful is not read as a smooth distribution."""
    fig = go.Figure()
    higher_better = feature in HIGHER_IS_BETTER

    # x-range from every box edge + the case value, padded — never the full
    # min/max cohort tail.
    xs: list[float] = []
    for key in _ROW_KEYS:
        box = box_data.get(key)
        if box:
            xs += [box["p10"], box["p90"]]
    if value is not None and not (isinstance(value, float) and np.isnan(value)):
        xs.append(float(value))
    if not xs:
        fig.update_layout(height=60, margin=dict(l=8, r=8, t=4, b=4))
        return fig
    lo, hi = min(xs), max(xs)
    pad = (hi - lo) * 0.10 or (abs(hi) * 0.1) or 1.0
    lo, hi = lo - pad, hi + pad

    yticktext: list[str] = []
    for i, (label, key) in enumerate(zip(_ROW_LABELS, _ROW_KEYS)):
        y = i
        box = box_data.get(key)
        pl = placement.get(key) or {}
        if box is None:
            yticktext.append(f"{label} (n/a)")
            fig.add_annotation(x=(lo + hi) / 2, y=y, text="not available",
                               showarrow=False,
                               font=dict(size=10, color="gray"))
            continue
        yticktext.append(f"{label} (n={box['n']})")
        # direction-of-better half-band (only for higher-is-better features).
        if higher_better:
            fig.add_shape(type="rect", x0=lo, x1=box["p50"], y0=y - 0.34,
                          y1=y + 0.34, fillcolor="rgba(214,39,40,0.06)",
                          line_width=0, layer="below")
            fig.add_shape(type="rect", x0=box["p50"], x1=hi, y0=y - 0.34,
                          y1=y + 0.34, fillcolor="rgba(44,160,44,0.07)",
                          line_width=0, layer="below")
        # whiskers p10–p90.
        fig.add_shape(type="line", x0=box["p10"], x1=box["p90"], y0=y, y1=y,
                      line=dict(color="rgba(90,90,120,0.7)", width=1.5))
        # box p25–p75.
        fig.add_shape(type="rect", x0=box["p25"], x1=box["p75"], y0=y - 0.18,
                      y1=y + 0.18, fillcolor="rgba(120,120,180,0.35)",
                      line=dict(color="rgba(90,90,120,0.9)", width=1))
        # median p50.
        fig.add_shape(type="line", x0=box["p50"], x1=box["p50"], y0=y - 0.24,
                      y1=y + 0.24, line=dict(color="rgba(50,50,80,1)", width=2))
        # small-n honesty: overlay the actual study values as a faint dot-strip so
        # a box resting on a handful is not read as a smooth distribution.
        pts = (points or {}).get(key)
        if pts:
            yj = [y + 0.20 * (((j % 5) - 2) / 2.0) for j in range(len(pts))]
            fig.add_trace(go.Scatter(
                x=pts, y=yj, mode="markers",
                marker=dict(symbol="circle", size=5,
                            color="rgba(90,90,120,0.45)"),
                hovertemplate="study %{x:.4g}<extra></extra>", showlegend=False,
            ))
        # the case marker — bold diamond + pXX label.
        if value is not None and not (isinstance(value, float) and np.isnan(value)):
            pct = pl.get("pctile")
            fig.add_trace(go.Scatter(
                x=[float(value)], y=[y], mode="markers+text",
                marker=dict(symbol="diamond", size=15, color="#d62728",
                            line=dict(color="white", width=1.5)),
                text=[f"  p{pct:.0f}" if pct is not None else ""],
                textposition="middle right",
                textfont=dict(size=12, color="#d62728"),
                hovertemplate=(f"{label}: {float(value):.4g} "
                               f"(cohort n={box['n']})<extra></extra>"),
                showlegend=False,
            ))

    fig.update_layout(
        xaxis=dict(range=[lo, hi], showgrid=True,
                   gridcolor="rgba(0,0,0,0.06)", zeroline=False),
        yaxis=dict(tickvals=[0, 1, 2], ticktext=yticktext,
                   range=[2.6, -0.6], showgrid=False, zeroline=False),
        margin=dict(l=110, r=24, t=6, b=24), height=190, plot_bgcolor="white",
    )
    return fig


def _qa_pool_badge(run_data: dict, fname: str) -> None:
    """The Tier-0 per-card QA badge — quiet-or-loud per the 2026-05-22
    operator review (point 9: the green-text badges were not noticeable).

    A clean card (`_qa_tier` is `valid`) gets NO badge — silence is the
    "everything is fine" signal, and removing the green-on-clean noise stops
    training the operator's eye to skip the badge. A degraded card gets a
    boxed warning / error that the eye cannot skip; the per-frame detail
    lives on the **review page** (phase 4) — Akadir 2026-05-24 v2 reframe
    Layer 2c stripped p05's per-frame Image-quality panel so the summary
    page stays calm. Silent on a feature the QA gate never scores
    (`tier is None`) and on the legacy `ungraded` state."""
    # v8 routed study: the displayed value is femur/RF-sourced, but the aggregate's _qa_tier is the
    # SWEEP's (route_aggregate overlays feature values, not QA) — a sweep-QA badge here would
    # mis-describe the femur/RF number. Suppress on routed; per-frame QA lives in the p04 per-modality
    # review (Akadir 2026-06-26 — merging per-modality QA into the routed aggregate is a follow-up).
    if run_data.get("routed_modalities"):
        return
    tier = (run_data.get("aggregate", {}).get("_qa_tier") or {}).get(fname)
    if tier in (None, "valid", "ungraded"):
        return                                 # quiet — no badge on clean
    if tier == "borderline":
        st.warning("Image quality — based on lower-quality frames. "
                   "See review (phase 4) for per-frame detail.", icon="⚠️")
    else:                                       # "none" / "invalid"
        st.error("Image quality — no clean value here. "
                 "See review (phase 4) for per-frame detail.", icon="🚫")


def _pooled_disclosure(run_data: dict) -> None:
    """Slice B — the p05 "pooled from N of M; K set aside" receipt.

    QUIET on a fully-clean study: `run_what_changed` returns no rows exactly
    when the run pooled cleanly, so we render nothing — the cardinal
    default-quiet rule (a provenance line that fires on every clean study is
    badge-fatigue). When the QA layer set aside or softened a number, a single
    caption states how many frames stand behind the study number, with a
    collapsed "why" expander over the already-computed `run_what_changed` rows
    (the ratified title / detail / evidence triple, severity-coloured). NOT
    the per-frame Image-quality table — that was deliberately stripped from
    p05 (Akadir 2026-05-24 Layer-2c); this is a compact run-level receipt that
    sits with the headline numbers it qualifies.
    """
    agg = run_data.get("aggregate", {})
    ffs = run_data.get("frame_features", [])
    rows = qa_surfacing.run_what_changed(agg, ffs)
    if not rows:
        return                                   # fully clean — stay quiet
    n, m, k = qa_surfacing.pooled_disclosure_counts(agg, ffs)
    if k > 0:
        st.caption(f"Pooled from **{n}** of **{m}** sweep frames · "
                   f"**{k}** set aside.")
    else:
        st.caption(f"Pooled from **{m}** sweep frames · see quality notes.")
    with st.expander("Why these numbers — what the app set aside or softened",
                     expanded=False):
        for row in rows:
            drop = row.get("severity") == "drop"
            colour, icon = ("red", "✗") if drop else ("orange", "⚠")
            st.markdown(f":{colour}[{icon} **{row['title']}**]")
            if row.get("detail"):
                st.markdown(row["detail"])
            if row.get("evidence"):
                st.caption(row["evidence"])


def _display_label(fname: str, run_data: dict) -> str:
    """The card / cohort-row label, routing-aware: a v8 routed study has NO landmark (the center
    short-sweeps ARE the value), so the rf_thickness label reads 'center cross-section' rather than the
    legacy long-sweep's 'at the landmark cross-section' — the static catalog title would otherwise name a
    landmark that does not exist for this view (Akadir 2026-06-26; the verifier caught the title + the
    cohort-row label, which the body-caption fix had missed)."""
    if fname == "rf_thickness_mm" and "rf" in (run_data.get("routed_modalities") or []):
        return "RF thickness — center cross-section (mm)"
    return _FEATURE_LABELS.get(fname, fname)


def _one_card(run_data: dict, fname: str) -> None:
    """A single metric card: value + percentile sub-caption + F7 direction line."""
    value = run_data["aggregate"].get(fname)
    # QMT + STAR ride on the provenance gate — a proxy / not-measurable value
    # is shown honestly, without a cohort percentile it cannot stand behind.
    if fname in ("qmt_mm", "star_ratio"):
        note = _qmt_provenance_note(run_data)
        if note is not None:
            st.metric(_display_label(fname, run_data), _fmt_value(fname, value))
            _qa_pool_badge(run_data, fname)
            st.markdown(f":{note['colour']}[⚠ {note['badge']}]")
            st.caption(note["caption"])
            return
    p = run_data["placements"].get(fname) or {}
    ov = p.get("overall") or {}
    age = p.get("age_matched") or {}
    sex = p.get("sex_matched") or {}
    # SEX-MATCHED IS THE HEADLINE PLACEMENT (Akadir 2026-08-04). Sex moves these measurements more
    # than most things we correct for: males read ~19 grey levels darker in the RF ROI after age and
    # BMI are removed, which is ~1 SD of `int_cv_within` — the axis carrying 43% of the score. And
    # the served recipe now reads raw millimetres instead of dividing by BMI, so it is honest about
    # size differences instead of hiding them in a divisor. Comparing a patient to their own sex is
    # what makes that honesty readable; overall and age-matched stay one hover away.
    _sex_p = sex.get("pctile")
    _sex_n = sex.get("n")
    st.metric(
        _display_label(fname, run_data),
        _fmt_value(fname, value),
        delta=(f"p{_sex_p:.0f} vs {_sex_n} same-sex peers" if _sex_p is not None else None),
        delta_color="off",
        # Sex is unknown for some studies, so the sex block can be absent while overall is present —
        # each percentile is formatted only once its own value exists, never off a sibling's guard.
        help=(" · ".join(
            ([f"Sex-matched p{_sex_p:.0f} (shown)"] if _sex_p is not None else [])
            + [f"Overall p{ov['pctile']:.0f}"]
            + ([f"Age ±10y p{age['pctile']:.0f}"] if age.get("pctile") is not None else []))
            if (value is not None and ov.get("pctile") is not None) else None),
    )
    _qa_pool_badge(run_data, fname)
    # The study LANDMARK — the selection-independent second placement for the SIZE features, from the
    # purpose-owning modality (the femur cine on a routed study). Rendered BEFORE the early-return so a
    # REAL value still surfaces when the per-pick aggregate primary is QA-nulled (e.g. the
    # sweep-calibrated image-quality gate sets a femur thickness aside even though the femur measured it
    # fine) — never a silent blank, and the "Read at the landmark" caption below must match this exact
    # predicate (Akadir 2026-06-26 #11 — a verifier caught the caption claiming a landmark line that did
    # not draw). The femur landmark is computed from femur_cine_masks, NOT through the QA-gated aggregate.
    lm = (run_data.get("_landmark") or {}).get(fname)
    _lm_draws = bool(lm and (lm.get("overall") or {}).get("pctile") is not None)
    if fname == "rf_thickness_mm":
        _src_modality = ("center-RF" if "rf" in (run_data.get("routed_modalities") or [])
                         else "sweep")
        _lm_clause = "Read at the landmark — the fullest clean cross-section. " if _lm_draws else ""
        st.caption(f":gray[{_lm_clause}The single thickest {_src_modality} frame usually runs higher "
                   f"but is an oblique (foreshortened) cut, not thicker muscle, so it is not the "
                   f"number shown.]")
    if _lm_draws:
        lm_p = lm["overall"]["pctile"]
        lm_v = lm["value"]
        st.markdown(f":blue[◆ study landmark **p{lm_p:.0f}** · "
                    f"{_fmt_value(fname, lm_v)} (median of the 5 fullest clean frames)]")
        # One-directional nudge — warn ONLY when the picked frame is materially BELOW the study's
        # fullest cross-sections. Self-guards on the picked value (silent when the primary is QA-nulled).
        if lm_v and value is not None:
            rel = (value - lm_v) / abs(lm_v)
            if rel <= -_LANDMARK_DIVERGENCE:
                st.caption(
                    f":orange[⚠ the frame you picked ({_fmt_value(fname, value)}) is "
                    f"{abs(rel) * 100:.0f}% below this study's fullest clean cross-sections "
                    f"(median {_fmt_value(fname, lm_v)}); its percentile reads low — the "
                    f"landmark is the selection-independent comparison.]")
            elif rel >= _LANDMARK_DIVERGENCE:
                st.caption(
                    f":green[✓ the frame you picked ({_fmt_value(fname, value)}) is at or "
                    f"above this study's fullest clean cross-sections "
                    f"(median {_fmt_value(fname, lm_v)}).]")
    if value is None or ov.get("pctile") is None:
        return
    # The cohort placement, led as ONE prominent number — the overall percentile,
    # sentiment-coloured, with the direction hint (Akadir 2026-06-10: a single
    # number is the headline; the box-plot distribution stays below as detail).
    phrase, sentiment = cohort_aware_phrase(
        ov.get("pctile"), age.get("pctile"), sex.get("pctile"), fname)
    icon = _SENTIMENT_ICON[sentiment]
    color = _SENTIMENT_COLOR[sentiment]
    if fname in HIGHER_IS_BETTER:
        dir_hint = "higher = better muscle signal"
    elif fname in LOWER_IS_BETTER:
        dir_hint = "lower = better (less fat)"
    else:
        dir_hint = "bigger ≠ better — geometry; read alongside the quality signals"
    st.markdown(
        f":{color}[{icon} **p{ov['pctile']:.0f}** · {phrase}]  ·  :gray[{dir_hint}]")
    # Demographic refinement — age ±10y + sex-matched, each degrading to a position
    # word when its band is too thin for a precise percentile.
    refine = [b for b in (_band_bit("age ±10y", age), _band_bit("sex", sex)) if b]
    st.caption(":gray[vs. {} peers overall{}]".format(
        ov.get("n", "?"), (" · " + " · ".join(refine)) if refine else ""))
    # RF echo-intensity is gain/probe-sensitive (A7): when int_cv was routed from the center-RF cine,
    # its cohort placement (read from a different view) is approximate — surface it on the card itself,
    # INDEPENDENT of the cohort selector (the gain shift exists vs the legacy cohort too). Akadir 2026-06-26 #18.
    if is_intensity_feature(fname) and "rf" in (run_data.get("routed_modalities") or []):
        st.caption(":orange[⚠ Echo intensity, measured on the center-RF cine — gain- and "
                   "probe-sensitive, so this cohort placement is approximate. Geometry "
                   "(size, fat, STAR) is not affected.]")
    # (the ◆ study-landmark line + its nudge now render ABOVE the early-return — relocated so they
    #  surface a real value even when the per-pick aggregate primary is QA-nulled; see earlier in fn.)


def _measurement_card(run_data: dict, features: tuple[str, ...],
                      *, layout: str = "row") -> None:
    """Render a set of metric cards.

    layout="stack" — one card per row (F9: the headline-4 stack vertically in
    the right column so each gets a full caption + direction line instead of
    being squeezed into a narrow sub-column).
    layout="row"   — side-by-side columns (used for the secondary expander).
    """
    if layout == "stack":
        for fname in features:
            with st.container(border=True):
                _one_card(run_data, fname)
    else:
        cols = st.columns(len(features))
        for col, fname in zip(cols, features):
            with col:
                _one_card(run_data, fname)


# The per-frame Image-quality panel (`_image_quality_panel` + the row
# helper `_image_quality_frame_row` + the bottom-close button) has been
# removed from p05 by the Akadir 2026-05-24 v2 reframe Layer 2c. The
# summary page stays calm; per-frame detail lives on **review (phase 4)**
# (commit 87eb83d added per-measurement attribution + the layer-stack
# heatmap there). The per-card `_qa_pool_badge` above is the surviving
# Tier-0 surface on p05 — it points the operator to the review page for
# the per-frame detail.


def _story_caption(study_code: str, run_data: dict) -> str:
    """Per-case story caption.

    Uses the ratified AGZ855 caption verbatim when study_code matches. For
    other cases, generates a structured caption from the placement payload.
    """
    # The ratified AGZ855 verbatim caption asserts "STAR ratio in the bottom
    # decile" — true only when AGZ855 actually has a standard QMT. Under the
    # provenance gate AGZ855 is a no_femur study, so the verbatim text would
    # contradict the cards below; fall through to the generic builder (which
    # omits STAR gracefully) unless the run genuinely has a standard QMT.
    if (study_code == "AGZ855"
            and run_data.get("aggregate", {}).get("qmt_provenance") == "standard"):
        return (
            "Thick muscle (above-average for age), but STAR ratio in the bottom "
            "decile age- and sex-matched, plus fascicular contrast in the bottom "
            "decile among males. Quantity is not the same as quality — and the "
            "assessment makes that gap visible."
        )
    # Otherwise — concise pattern from the placement payload.
    star = run_data["placements"].get("star_ratio") or {}
    rf = run_data["placements"].get("rf_thickness_mm") or {}
    icv = run_data["placements"].get("int_cv_within") or {}
    parts: list[str] = []
    if (rf.get("overall") or {}).get("pctile") is not None:
        p = rf["overall"]["pctile"]
        kind = "above-average" if p >= 60 else "around average" if p >= 40 else "below-average"
        parts.append(f"RF thickness {kind} for the cohort (p{p:.0f}).")
    if (star.get("overall") or {}).get("pctile") is not None:
        p = star["overall"]["pctile"]
        kind = "preserved" if p >= 60 else "intermediate" if p >= 30 else "low"
        parts.append(f"STAR placement is {kind} (p{p:.0f}).")
    if (icv.get("overall") or {}).get("pctile") is not None:
        p = icv["overall"]["pctile"]
        kind = "higher fascicular contrast on this image" if p >= 60 else "mid-range fascicular contrast" if p >= 30 else "lower fascicular contrast on this image"
        parts.append(f"Echo texture suggests {kind} (p{p:.0f}).")
    return " ".join(parts) if parts else "Assessment computed; cohort placement available below."


def _trajectory_panel() -> None:
    """Two greyed trajectory affordances — explicit slots showing where this is going."""
    cols = st.columns(2)
    with cols[0]:
        st.markdown(":gray[**◷ Earlier scan**]")
        # N2 — longitudinal preview copy. The feature itself is parked (needs
        # re-imaging data that doesn't exist yet); only this tooltip-copy ships.
        st.caption(
            ":gray[Longitudinal axis — the same patient compared against their "
            "own earlier scan: percentile *progress* vs peers over time, e.g. "
            "pXX(later) − pXX(early). Lights up once re-imaging data exists.]"
        )
    with cols[1]:
        st.markdown(":gray[**◷ Heckmatt-axis features beyond fascicular contrast**]")
        st.caption(":gray[Quality-axis features — in development (white paper).]")


# ---------- F5 + N1 — rich previous-run comparison + study bootstrap --------

def _convergence(per_run_values: list) -> str:
    """Relative spread of a feature's per-run values → a convergence label.

    Heuristic ±15% band: agree within it → consistent, else → varies. With
    fewer than two distinct runs there is nothing to converge on yet.
    """
    vals = [float(v) for v in per_run_values
            if isinstance(v, (int, float))
            and not (isinstance(v, float) and np.isnan(v))]
    if len(vals) < 2:
        return "— (single run)"
    lo, hi = min(vals), max(vals)
    mid = abs(float(np.median(vals)))
    if mid < 1e-9:                       # zero-centred feature — the median is
        mid = (abs(lo) + abs(hi)) / 2    # no scale; fall back to the spread
    rel = (hi - lo) / mid if mid else 0.0
    if rel <= 0.15:
        return f"✓ consistent (±{rel * 100:.0f}%)"
    return f"⚠ varies (±{rel * 100:.0f}%)"


def _run_comparison_panel(study_code: str, run_data: dict) -> None:
    """F5 + N1 — this run vs the study bootstrap vs the prior runs.

    The study bootstrap (N5) pools every analysis frame across the *distinct
    finalized* runs and re-aggregates anchorless-median; this panel surfaces
    it next to the current run, with a per-feature convergence signal and a
    warning when the pool mixes model bundles.
    """
    with st.expander("📊 Run comparison + study bootstrap", expanded=False):
        # Key the pool on the collision-free study_uid (the AKA study_code repeats
        # across studies; keying on it would pool different patients' frames).
        _entry = st.session_state.get("study_entry")
        _uid = _entry.meta.study_uid if _entry is not None else None
        # Place the pooled running-median against the SAME live population the
        # headline uses (old/new/mixed) — not percentile()'s LEGACY v1.0 default.
        _src = st.session_state.get("cohort_source") or _default_cohort_source()
        boot = study_bootstrap(study_code, study_uid=_uid, source=_src)
        n_d, n_t = boot["n_distinct_runs"], boot["n_total_runs"]
        if n_d == 0:
            st.caption(
                "No finalized runs in this study's ledger yet — accept a run "
                "to start compounding the study bootstrap."
            )
            return
        if boot.get("mode") == "routed_run_median":
            st.markdown(
                f"**Study bootstrap** — {n_d} distinct finalized run(s) "
                f"({n_t} finalized for this study) · per-feature **median over the runs' "
                f"routed measurements** ({boot['n_pooled_frames']} analysis frames behind them). "
                f"Routed runs don't persist per-frame features, so this is a median of complete "
                f"measurements, not a frame pool."
            )
        else:
            st.markdown(
                f"**Study bootstrap** — {n_d} distinct finalized run(s) "
                f"({n_t} finalized for this study) · {boot['n_pooled_frames']} "
                f"analysis frame(s) pooled · anchorless pooled-median over all of them."
            )
        models = boot["models_used"]
        if len(models) > 1:
            st.warning(
                f"⚠ The pool mixes {len(models)} model bundles — bootstrap "
                f"values blend across models. Strict same-model-only pooling "
                f"is a post-MVP refinement."
            )
        elif models:
            st.caption(f":gray[Pooled model bundle: `{models[0][:16]}…`]")

        cur_agg = run_data.get("aggregate", {}) or {}
        cur_qmt_prov = cur_agg.get("qmt_provenance")
        boot_qmt_prov = (boot.get("aggregate") or {}).get("qmt_provenance")
        rows = []
        qmt_flagged = False
        for feat in HEADLINE_FEATURES + SECONDARY_FEATURES:
            pf = boot["per_feature"].get(feat) or {}
            bp = ((boot["placements"].get(feat) or {}).get("overall") or {}).get("pctile")
            label = _FEATURE_LABELS.get(feat, feat)
            if feat in ("qmt_mm", "star_ratio"):
                # QMT/STAR are only cohort-comparable when standard on both
                # sides — withhold the bootstrap pctile otherwise, and flag.
                if boot_qmt_prov != "standard":
                    bp = None
                if cur_qmt_prov != "standard" or boot_qmt_prov != "standard":
                    label += " ⚠"
                    qmt_flagged = True
            rows.append({
                "Feature": label,
                "This run": _fmt_value(feat, cur_agg.get(feat)),
                "Study bootstrap": _fmt_value(feat, pf.get("bootstrap_value")),
                "Bootstrap pctile": f"p{bp:.0f}" if bp is not None else "—",
                "Across runs": _convergence(
                    [pr.get("value") for pr in pf.get("per_run", [])]),
            })
        st.dataframe(rows, width="stretch", hide_index=True)
        if qmt_flagged:
            st.caption(
                ":orange[⚠ QMT / STAR — this run and/or the bootstrap pooled "
                "non-femur-anchored frames; those rows are a proxy or not "
                "measurable, and the bootstrap percentile is withheld where the "
                "pool was not standard. See the headline cards for detail.]"
            )
        st.caption(
            ":gray[\"This run\" is the run on screen, aggregated by its per-run "
            "policy (anchor frame for geometry). \"Study bootstrap\" pools every "
            "distinct finalized run and re-aggregates anchorless median — so part "
            "of any gap is the policy difference, not run-to-run drift. \"Across "
            "runs\" is the relative spread of the per-run values — a heuristic "
            "±15% consistency band.]"
        )
        contributing = boot.get("contributing_run_ids", [])
        if contributing:
            st.caption(f":gray[Contributing runs: {', '.join(contributing)}]")

        # Phase 3b (Akadir 2026-07-14): surface HOW each session vetted the RF texture (auto / keep /
        # fix / withhold) next to its int_cv — so the operator sees not just THAT the texture varied
        # across sessions (the spread above) but how each was judged. This is the manual-intervention
        # audit trail per study; loud only when a session was not plain 'auto'.
        from preflight_helpers.bundle_writer import runs_for_study
        _sessions = [(r.get("run_id", "")[-6:], r.get("texture_state") or "auto",
                      (r.get("aggregate") or {}).get("int_cv_within"))
                     for r in runs_for_study(study_code, study_uid=_uid)]
        if any(state not in (None, "auto", "verified") for _, state, _ in _sessions):
            _bits = [f"`{rid}` {state}" + (f" · int_cv {v:.3f}" if v is not None else "")
                     for rid, state, v in _sessions if state not in (None, "auto", "verified")]
            st.caption(":orange[**RF texture — manual review across sessions:** " + "  |  ".join(_bits) + "]")


def render_sidebar() -> None:
    """Stage-modular sidebar (F3) — phase 5 hosts the previous-runs panel (F4:
    it is no longer always-on; it belongs to the assessment step) plus the
    trajectory preview copy."""
    from labeling_app.assessment_pages import help_content
    st.markdown("### ⑤ Assessment")
    if st.session_state.get("study_entry") is None:
        st.caption("Earlier phases not complete.")
        help_content.render_help_expander(4)
        return
    # Muscle-condition score display scale — /100 default, /800 offered for the SME.
    # Display-only: the model + bands + which band a patient falls in are unchanged.
    st.radio(
        "Muscle-condition score scale", options=[100, 800],
        format_func=lambda s: f"0–{s}", horizontal=True, key="cond_scale_max",
        help="Display only — rescales the headline number and its band lines. The "
             "model, the bands, and which band a patient falls in do not change; "
             "0–800 is offered because some readers prefer a wider scale.")
    # The reference-population lens sits BESIDE the score-scale lens — both are display-time choices
    # that belong to step 5, and the sidebar renders before the page body, so a flip is reflected in
    # the same run. It is NOT in the page body: a keyed radio there made AppTest's get_widget_states
    # KeyError on the conditionally-rendered phase-3 frame gates ([[feedback_apptest_conditional_widgets]]).
    _cohort_reference_control()
    # F4 — previous-runs panel renders here, not as an always-on sidebar.
    previous_runs_sidebar.render()
    st.divider()
    help_content.render_help_expander(4)


# --- Muscle-condition score. ADDITIVE and FAIL-SILENT: no model file, or a study
#     it can't score, renders nothing and never disturbs the assessment page. The
#     measurements + their cohort placement already live on this page (the cards +
#     box plots); this only adds the synthesised score on top. Reuses the frozen
#     3-option model (preflight_helpers.condition_model) + the panel's figures. ---
_COND_BAND_COLOR = ("#2ca02c", "#e8a000", "#d62728")  # idx 0/1/2 = PRESERVED/INTERMEDIATE/POOR


@st.cache_resource
def _condition_models(source: str = OLD):
    """The frozen scorer, FIXED to cohort_placement.ACTIVE_OLD_VERSION for every population source
    (old/new/mixed) — the population toggle changes the percentiles, not the score (new/mixed have no
    grades to refit). Only the explicit rollback sources load an earlier re-base. `cache_resource`
    keys by `source`. The version is NOT named here on purpose: this line read "v1.1" for three weeks
    after v1.2 froze, and prose is where the two pointers drifted apart the first time."""
    from preflight_helpers.cohort_placement import model_path_for_source
    try:
        return condition_model.load(model_path_for_source(source))
    except Exception:
        return None


@st.cache_resource
def _condition_options(source: str = OLD):
    """Bake-off metrics (QWK / exact / off-by-two / VIF per model) for the formula
    reveal — version-matched to the selected reference (condition_options[_v1.1].json)."""
    from preflight_helpers.cohort_placement import options_path_for_source
    try:
        import json
        with open(options_path_for_source(source)) as f:
            return json.load(f)
    except Exception:
        return None


def _texture_state(run_data: dict) -> Optional[dict]:
    """Re-derive the center-RF texture (int_cv) audit state from the stamped RF-reliability DECISION +
    the operator's p04 choice (session `texture_choice`). Pure + cheap — the decision is fixed by the
    aggregate's thicknesses, so the choice can change without re-segmenting. Returns None on a legacy
    single-sweep study (no `_rf_reliability` stamp) — there is no discrepancy gate there."""
    stamp = (run_data.get("aggregate") or {}).get("_rf_reliability")
    dec = stamp.get("decision") if stamp else None
    # No discrepancy gate on a LEGACY single-sweep study — only one view exists, so there is nothing to
    # cross-check, and a "three views agree" chip there would be a false statement (red-team 2026-07-14).
    # (route_study_aggregate stamps `_rf_reliability` even for a legacy study, so we must filter by the
    # decision, not just the stamp's presence.)
    if not dec or dec.get("source") == "sweep" or dec.get("reliability") == "legacy-sweep":
        return None
    from preflight_helpers.rf_reliability import resolve_texture_state
    return resolve_texture_state(dec, st.session_state.get("texture_choice"))


def _score_condition(run_data: dict, source: str = OLD) -> Optional[dict]:
    art = _condition_models(source)
    if art is None:
        return None
    feats = run_data.get("aggregate", {})
    # #17 texture-WITHHOLD (Mode B): if the operator judged the center-RF boundary NOT resolvable at the
    # shallow FOV, DROP int_cv so the score is computed texture-withheld (a missing axis -> headline
    # unavailable), never faked. All 3 recipes need texture, so the whole score withholds — the honest
    # handling. 'keep'/'fix'/verified/pending all leave int_cv in (int_cv_usable=True).
    _ts = _texture_state(run_data)
    if _ts and not _ts["int_cv_usable"]:
        feats = {k: v for k, v in feats.items() if k != "int_cv_within"}
    try:
        return condition_model.score_patient(art, feats)
    except Exception:
        return None


def _texture_reliability_chip(ts: Optional[dict]) -> None:
    """The subtle RF-reliability audit chip near the condition score (Akadir 2026-07-13). Reflects the
    center-RF texture (int_cv) state from the 3-way RF check + the operator's p04 choice:
    verified (three views agree) · reviewed/fixed (operator accepted/corrected) · pending (un-reviewed
    discrepancy, amber) · withheld (Mode B). On WITHHELD the headline is unavailable BY DESIGN, so we
    say it plainly here — the absence of a score is a deliberate honest withhold, not a glitch."""
    if not ts:
        return                                   # legacy single-sweep study — no discrepancy gate
    state = ts["state"]
    color = {"withheld": "red", "pending_review": "orange"}.get(state, "gray")
    icon = {"verified": "✓", "reviewed_kept": "✓", "fixed": "✎",
            "pending_review": "⚠", "withheld": "🚫"}.get(state, "•")
    st.caption(f":{color}[{icon} {ts['chip']}]")
    if state == "withheld":
        st.warning("Muscle condition **withheld** — the center-RF texture (fascicular contrast) could "
                   "not be resolved at the shallow FOV, and every recipe needs it. The honest read is "
                   "no score, not an invented one. Change this in the phase-4 review.", icon="🚫")


def _condition_headline(result: Optional[dict], scale_max: int = 100) -> None:
    h = (result or {}).get("headline")
    if not h or not h.get("available"):
        return
    disp = h["condition"] * scale_max / 100.0
    c = _COND_BAND_COLOR[h["band_idx"]]
    st.markdown(
        f"#### Muscle condition: <span style='color:{c}'>{h['band']}</span>"
        f"&nbsp; · &nbsp;<span style='color:{c};font-weight:600'>{disp:.0f}</span>"
        f"<span style='color:#888;font-size:.75em'> / {scale_max:g}</span>",
        unsafe_allow_html=True)
    try:
        st.plotly_chart(
            band_strip_figure(h["condition"], h["boundaries"], h["band"], scale_max),
            width="stretch", config={"displayModeBar": False})
    except Exception:
        pass
    # Derived from the SERVED recipe, never hardcoded: the served option changed on 2026-08-04 and
    # this caption still described the old one for as long as it was a literal — the score said
    # millimetres while the page said muscle-over-BMI.
    # lower-cased: these are axis LABELS ("Fascicular contrast") but they read mid-sentence here,
    # and the page's harmonised term is lowercase everywhere else.
    _axis_names = " · ".join(
        lbl[:1].lower() + lbl[1:] for _, _, lbl in
        (condition_model.OPTIONS.get(condition_model.SERVED_HEADLINE) or (None, None, []))[2])
    if condition_model.SERVED_HEADLINE == "A":
        st.caption(f":gray[**Driven by** {_axis_names}.  Muscle size is read in **millimetres**, "
                   "not divided by BMI — dividing by body size removed much of the signal it was "
                   "measuring, so a bigger person reads bigger. Compare against same-sex peers.]")
    else:
        st.caption(f":gray[**Driven by** {_axis_names}.  STAR is muscle thickness ÷ BMI, so a "
                   "muscular-but-heavy person can read low — intended (the relative-muscle view "
                   "the doctors use), not an error.]")
    st.caption(":gray[**Agreement with our doctors' eye** — a fast, consistent second "
               "read, not a measurement of something new. Provisional: *not* validated "
               "against strength or MRI, not a diagnosis, and not able to track change "
               "over time yet. The band is what we stand behind (0% off by two bands, on the "
               "legacy sweep cohort); "
               "the exact number is finer than the model truly resolves.]")


def _condition_rater_chip(study_code: str, result: Optional[dict]) -> None:
    """The human target the score reproduces: this study's 3-rater consensus grade
    (+ the individual votes when the operator log is present). Shown only for studies
    in the reference cohort — a fresh pilot capture has no grade, so it stays silent.
    ADDITIVE + fail-silent."""
    h = (result or {}).get("headline")
    if not h or not h.get("available"):     # only alongside an actual model score
        return
    try:
        grade = consensus_grade(study_code)
    except Exception:
        grade = None
    if not grade:
        return
    c = _COND_BAND_COLOR[{"PRESERVED": 0, "INTERMEDIATE": 1, "POOR": 2}[grade]]
    model_band = ((result or {}).get("headline") or {}).get("band")
    match = ""
    if model_band:
        match = (" <span style='color:#2ca02c'>✓ model agrees</span>"
                 if model_band == grade
                 else f" <span style='color:#e8a000'>model reads {model_band.title()}</span>")
    st.markdown(
        f"<span style='color:#888'>Doctors' consensus grade:</span> "
        f"<span style='color:{c};font-weight:600'>{grade}</span>{match}",
        unsafe_allow_html=True)
    try:
        votes = rater_votes(study_code)
    except Exception:
        votes = None
    if votes:
        st.caption(
            f":gray[The {len(votes)} doctors graded: "
            f"{', '.join(g.title() for _, g in votes)} — the model reproduces their "
            f"consensus. This study is in the training cohort, so the grade is the "
            f"target the model learned, not an independent check.]")
    else:
        st.caption(":gray[The consensus grade the model is trained to reproduce "
                   "(this study is in the training cohort).]")


def _breakdown_rows(result: Optional[dict], run_data: dict) -> list:
    """Assemble the breakdown rows: each axis's CARD percentile (the SAME number the
    feature card shows — not a second, differently-computed pXX), its own better-
    direction, and its β·z contribution. Pure (no Streamlit) so it is unit-testable."""
    axes = (result or {}).get("axes") or []
    placements = (run_data or {}).get("placements", {})
    rows = []
    for ax in axes:
        col = ax.get("col")
        # The card LEADS with the same-sex placement (Akadir 2026-08-04: sex moves these
        # measurements more than most things we adjust for), so the breakdown quotes that same
        # number and says so; it falls back to the overall rank only when the sex block is
        # absent (sex not recorded, or a thin same-sex pool). Until 2026-09-02 this row read the
        # OVERALL rank while the card led with the sex-matched one — two different pXX for one
        # feature, under a caption promising they were the same.
        pl = placements.get(col) or {}
        pct = (pl.get("sex_matched") or {}).get("pctile")
        pool = "same-sex peers"
        if pct is None:
            pct = (pl.get("overall") or {}).get("pctile")
            pool = "all"
        direction = ("↑ higher = better" if col in HIGHER_IS_BETTER
                     else "↓ lower = better" if col in LOWER_IS_BETTER else "")
        if not direction and ax.get("better_high") is not None:
            # A geometry axis the cards call "bigger ≠ better" still has a sign INSIDE the score
            # (thickness: β < 0, so higher pushes toward PRESERVED). Say so, scoped to the score,
            # so the push phrase beneath the bar does not imply a direction the row never stated.
            direction = ("↑ higher = better in this score" if ax["better_high"]
                         else "↓ lower = better in this score")
        rows.append({"clinical": CLINICAL_AXIS.get(col, ""), "name": ax.get("name", ""),
                     "help_name": ax.get("name", ""), "pct": pct, "pool": pool,
                     "direction": direction, "contribution": ax.get("contribution")})
    return rows


def _condition_breakdown(result: Optional[dict], run_data: dict) -> None:
    """The co-equal size/fat/tissue read under the headline — WHERE the patient sits
    per axis (the card's exact percentile + that axis's better-direction) and HOW
    MUCH each axis moves THIS patient's score (β·z). ADDITIVE + fail-silent; gated on
    a scoreable headline. Row assembly is in _breakdown_rows (unit-tested)."""
    h = (result or {}).get("headline")
    if not h or not h.get("available"):     # no score -> no "what drives the score"
        return
    rows = _breakdown_rows(result, run_data)
    if not any(r.get("pct") is not None for r in rows):
        return
    try:
        breakdown_block(rows, st)
        # Name the ruler the bars are read on. The percentiles follow the sidebar's reference
        # selector; the score does not (it is fixed to the old-cohort model). Said here, next to
        # the bars, so a radio flip is legible on this side of the page too (Akadir 2026-09-02:
        # "shouldn't reference selection on the left also change the placements on the right?").
        st.caption(_breakdown_reference_caption())
    except Exception:
        pass


def _breakdown_reference_caption() -> str:
    """'placed against New round · n=175 …' for the breakdown — fail-safe (empty on any error)."""
    try:
        from preflight_helpers.cohort_placement import cohort_n, OLD as _O, NEW as _N, MIXED as _M
        src = st.session_state.get("cohort_source") or _default_cohort_source()
        name = {_O: "Old reference", _N: "New round", _M: "Mixed"}.get(src, src)
        return (f":gray[Percentiles above are read against **{name} · n={cohort_n(src)}** "
                f"(the sidebar's reference selector; same-sex peers where the pool allows). "
                f"The score itself is fixed to the old-cohort model and does not follow the "
                f"selector.]")
    except Exception:                       # noqa: BLE001 - a caption must never break the page
        return ""


def _condition_formula(art: Optional[dict], opts_meta: Optional[dict],
                       result: Optional[dict], scale_max: int = 100, *,
                       routed: bool = False) -> None:
    """The (i): the exact formula + the real fitted coefficients + the model's
    out-of-fold agreement + the claim boundary. Reads the frozen artifact, so what
    it prints IS the model that scored this patient. ADDITIVE + fail-silent."""
    h = (result or {}).get("headline")
    if not art or not h or not h.get("available"):
        return
    key = art.get("headline", "C")
    m = (art.get("models") or {}).get(key)
    if not m:
        return
    with st.expander("ⓘ How this score is computed — formula, coefficients, "
                     "and what it can and can't claim"):
        st.markdown(
            "A **proportional-odds ordinal-logistic model** — the right form for an "
            "*ordered* grade (PRESERVED ▸ INTERMEDIATE ▸ POOR). One measurement per "
            "clinical axis (size, fat, tissue), fit once on the cohort and frozen; "
            "the app then does pure arithmetic, identical for every patient.")
        st.markdown(
            "1. **Standardize** each feature:  z = (x − μ) / σ\n"
            "2. **Combine** into one total:  η = Σ βᵢ·zᵢ  "
            "(a *negative* β means more is healthier)\n"
            "3. **Probabilities** from two cutpoints c₁, c₂:  "
            "P(PRESERVED) = σ(c₁−η),  P(POOR) = 1 − σ(c₂−η)\n"
            f"4. **Score** = (1 − E[grade] / 2) × {scale_max:g}  (higher = better)")
        babs = [abs(b) for b in m["beta"]]
        tot = sum(babs) or 1.0
        raw = [b / tot * 100.0 for b in babs]
        shares = [int(x) for x in raw]                       # floor each
        # largest-remainder (Hamilton): hand the leftover from flooring to the axes
        # with the biggest fractional parts, so the displayed ints sum to EXACTLY 100
        # (else 33+30+36 = 99 reads as a bug). Texture's 36.4 rounds up to 37 here.
        for i in sorted(range(len(raw)), key=lambda j: raw[j] - shares[j],
                        reverse=True)[:max(0, 100 - sum(shares))]:
            shares[i] += 1
        rows = [{"axis": CLINICAL_AXIS.get(col, ""), "feature": col,
                 "β (weight)": f"{b:+.3f}", "share": f"{sh}%",
                 "cohort μ": f"{mu:.4g}", "cohort σ": f"{sd:.4g}"}
                for col, b, sh, mu, sd in zip(m["cols"], m["beta"], shares,
                                              m["mu"], m["sd"])]
        st.dataframe(rows, hide_index=True, width="stretch")
        # 'share' answers "what weight is each axis?": |β| as a fraction of the total.
        # It is a faithful stand-in for average influence ONLY because all three inputs
        # vary by a similar amount (mean|z| ≈ 0.8 each), so |β|-share ≈ mean|β·z|-share.
        # This caption also now carries the per-patient-push explanation that used to
        # sit inline in the breakdown above (moved here on Akadir's 2026-06-18 note).
        st.caption(
            ":gray[**share** = |β| as a fraction of the total — roughly how much each "
            "axis pushes the score on average; the three come out near-even thirds "
            "(no single axis dominates). The per-patient *push* shown in the breakdown "
            "above is that axis's β·z: it is *exact* because the three axes barely "
            "overlap (VIF < 3), and the size axis is BMI-adjusted, so part of its push "
            "reflects body size.]")
        k = scale_max / 100.0
        st.caption(
            f"cutpoints c₁ = {m['cuts'][0]:.3f}, c₂ = {m['cuts'][1]:.3f}  ·  bands "
            f"(this cohort's own decision points, not clinical thresholds): "
            f"POOR < {h['boundaries'][0] * k:.0f} · "
            f"PRESERVED ≥ {h['boundaries'][1] * k:.0f}")
        bt = (opts_meta or {}).get("boss_table") or {}
        row = next((v for kk, v in bt.items() if kk.startswith(key + ":")), None)
        if row:
            st.markdown(
                f"**Agreement with the 3-rater consensus** — 10-fold out-of-fold, the "
                f"metric that answers *“how often does it land on my grade?”*: "
                f"weighted κ **{row['qwk']:.3f}**, exact grade **{row['exact_pct']:.0f}%**, "
                f"off by two bands **{row['off2_pct']:.0f}%**, worst feature-overlap "
                f"VIF **{row['worst_vif']:.1f}**, covers **{row['coverage_pct']:.0f}%** "
                f"of scans. Rater-vs-rater agreement is only κ 0.63–0.73, so this sits "
                f"at the reliability ceiling — more features won't help; a better "
                f"criterion (the pilot) will.")
        if routed:
            st.caption(
                ":orange[⚠ These agreement figures were measured on the **legacy single-sweep "
                "view** — the cohort the model was fit and cross-validated on. This study is scored "
                "from the femur / center-RF cines (v8 routing), the same quantities from a cleaner "
                "view, but the figures have not yet been re-validated on that view.]")
        st.caption(
            ":gray[What this is: agreement with our own doctors' eye, read from the "
            "same image — it reproduces an expert read, *not* an outside truth. It is "
            "NOT validated against strength or MRI, NOT a diagnosis, NOT a population "
            "norm (the bands are this cohort's percentiles), and it can NOT yet tell a "
            "real change from scanner noise. Those need the pilot and an external "
            "yardstick.]")


def _condition_triplet(result: Optional[dict], scale_max: int = 100) -> None:
    opts = (result or {}).get("options", {})
    if sum(1 for o in opts.values() if o.get("available")) < 2:
        return
    k = scale_max / 100.0
    with st.expander("📊 Compare the three score models", expanded=False):
        st.caption(
            "Three ways to build the score; the diamond is this patient, the number "
            "is the exact score. They land in the same band ~85% of the time — the "
            "answer barely depends on the model. The headline serves "
            f"{condition_model.SERVED_HEADLINE} ({(condition_model.OPTIONS.get(condition_model.SERVED_HEADLINE) or ('?',))[0]}).")
        cols = st.columns(3)
        for col, key in zip(cols, ("C", "A", "B")):
            o = opts.get(key, {})
            col.metric(key,
                       f"{o['condition'] * k:.0f} / {scale_max:g}" if o.get("available") else "—",
                       o.get("band", "n/a"), delta_color="off")
        try:
            st.plotly_chart(triplet_figure(opts, scale_max=scale_max), width="stretch",
                            config={"displayModeBar": False})
        except Exception:
            pass


def _render_routed_panes(entry, run_data: dict) -> None:
    """v8 two-pane assessment view (Akadir 2026-06-26 audit, #1/#7): the FEMUR
    pick (the geometry source — shown WITH the QMT / thickness / fat overlay) and
    the CENTER-RF pick (the echo-intensity source — segmentation ONLY, so the
    operator confirms the model found the right RF muscle pixels). The displayed
    frames now match where the score reads each feature, instead of one sweep
    frame the score no longer uses. Masks come from the shared session cache
    (populated in review); the anchor is the positional-median pick, matching the
    router's per-modality choice (`feature_routing.compute_modality_aggregate`)."""
    from preflight_helpers.study_discovery import study_modalities
    from labeling_app.assessment_pages.p04_frame_review import (
        _ensure_predicted_masks, _render_segmentation_arr)
    mods = study_modalities(entry.study_path)
    panes = [
        ("femur", "Femur cine — all measurements", True,
         "RF thickness / area, fat, QMT (muscle thickness to the bone) → STAR, and the fat fraction are "
         "all measured here — the overlay shows the calipers down to the femur."),
        ("rf", "Center-RF cine — echo texture", True,
         "Only the echo texture (int_cv / fascicular contrast) is read here — confirm the magenta "
         "outline tracks the RF muscle it is measured inside. Geometry + fat come from the femur."),
    ]
    shown = 0
    for role, title, with_meas, cap in panes:
        if role not in mods:
            continue
        picks = st.session_state.get(f"picked_frames__{role}") or []
        gates = st.session_state.get(f"gates__{role}") or {}
        kept = [fb for fb in picks
                if gates.get(fb, {}).get("usable", True) is not False
                and gates.get(fb, {}).get("use_for_analysis", True) is not False]
        if not kept:
            continue
        _ensure_predicted_masks(kept, raw_dir=mods[role].raw_image_dir)
        anchor = kept[len(kept) // 2]   # the positional-median pick the router measures
        pred = st.session_state.predicted_masks.get(anchor)
        final_u8 = st.session_state.get("final_u8_cache", {}).get(anchor)
        st.markdown(f"**{title}**")
        if pred is not None and final_u8 is not None:
            _extra = f"  ·  +{len(kept) - 1} more kept" if len(kept) > 1 else ""
            st.image(_render_segmentation_arr(anchor, pred, with_measurements=with_meas, modality=role),
                     caption=f"{anchor}{_extra}", width="stretch")
        elif final_u8 is not None:
            st.image(final_u8, caption=f"{anchor}", width="stretch")
        else:
            st.caption(":gray[Segmentation not cached — revisit review (phase 4).]")
        st.caption(f":gray[{cap}]")
        # When the texture was refined on the review page, the served int_cv reads a BOUNDED
        # region — show that region here too, or the summary page displays an RF extent that is
        # not the actually-effective texture space (Akadir's AKA036 ask, 2026-08-14 review doc).
        if role == "rf" and run_data.get("texture_refine"):
            try:
                from labeling_app.assessment_pages.p04_frame_review import _render_texture_region_overlay
                _tr = run_data["texture_refine"]
                _render_texture_region_overlay(anchor, int(_tr.get("erosion_px", 3)),
                                               tuple(_tr.get("band", (0.0, 1.0))))
                st.caption(":gray[Texture is read from the green banded region above — the "
                           "refined section this run's int_cv actually measured, not the full "
                           "RF outline.]")
            except Exception:
                pass
        if role == "femur" and with_meas and len(kept) > 1:
            # QA readout (Akadir 2026-07-17): is the QMT a stable read across the femur frames,
            # or does the segmentation wobble? A reliability signal, independent of the value.
            from labeling_app.femur_cine_scorer import femur_qmt_consistency
            _fm = {fb: st.session_state.predicted_masks[fb]
                   for fb in kept if fb in st.session_state.get("predicted_masks", {})}
            _cons = femur_qmt_consistency(_fm)
            if _cons["verdict"] != "insufficient":
                _ok = _cons["verdict"] == "reliable"
                _c, _icon = ("green", "✓") if _ok else ("orange", "⚠")
                st.caption(
                    f":{_c}[{_icon} QMT read **{_cons['verdict']}** across {_cons['n']} femur frames "
                    f"· frame-to-frame spread {100 * _cons['cv']:.0f}%]"
                    + ("" if _ok else "  :gray[— a wobble flags an unreliable femur segmentation; "
                       "re-pick or re-image.]"))
        shown += 1
    if shown:
        # Both panes paint the eroded RF contour (p04 _render_segmentation_arr draws it outside the
        # with_measurements gate), so explain the erosion here (Akadir 2026-06-26 #17).
        st.caption(":gray[Measurements compute on the muscle interior — we drop a 3-pixel boundary "
                   "(magenta contour) to avoid edge artefacts.]")
    if shown == 0:
        st.caption(":gray[No usable femur / center-RF picks to display — the score used the sweep.]")


def _render_legacy_anchor(run_data: dict) -> None:
    """LEGACY single-sweep studies: the single representative (anchor) sweep frame
    with the operator's seg / measurements / confidence layer toggles. A v8
    3-modality study uses `_render_routed_panes` instead — the score reads geometry
    from the femur and intensity from the center-RF, not from one sweep frame."""
    anchor_ff = next((ff for ff in run_data["frame_features"] if ff.get("is_anchor")),
                     run_data["frame_features"][0] if run_data["frame_features"] else None)
    if anchor_ff is not None:
        fb = anchor_ff["frame_base"]
        # Display source: the live-synthesised `final` cached in session state by
        # phase 4 (`final_u8_cache`). Mirrors goruntuler's canonical methodology.
        final_u8 = st.session_state.get("final_u8_cache", {}).get(fb)
        pred = st.session_state.predicted_masks.get(fb)
        if pred is not None and final_u8 is not None:
            from labeling_app.visualization import (
                confidence_heatmap_overlay, load_label_palette,
                overlay_on_image, post_process_mask_for_display,
            )
            from preflight_helpers.post_processing import post_process_rf
            from PIL import Image as _Image
            # Three local layer toggles — Akadir 2026-05-25 § 8. Defaults:
            # segmentation + measurements ON (the working view); confidence OFF.
            st.markdown("**Display layers**")
            tcols = st.columns(3)
            with tcols[0]:
                show_seg = st.toggle(
                    "Segmentation overlay", value=True,
                    key="p05_show_segmentation",
                    help="Coloured class overlay on the cleaned masks "
                         "(RF magenta, VI, VL, VM tints; the eroded RF "
                         "contour rides with the measurements layer).")
            with tcols[1]:
                show_heatmap = st.toggle(
                    "Confidence heatmap", value=False,
                    key="show_confidence_heatmap",
                    help="Red wash where the AI was uncertain about the "
                         "pixels, OR our display-side post-processing "
                         "overrode the AI (stray dropped, RF hole filled). "
                         "Measurement lines that cross red regions read "
                         "a touch softer.")
            with tcols[2]:
                show_meas = st.toggle(
                    "Measurements", value=True,
                    key="p05_show_measurements",
                    help="Magenta eroded contour + RF/VI peak-thickness "
                         "ticks + QMT caliper + q50 column. The numbers "
                         "in the headline cards are read off these.")
            cleaned_pred, change_map = post_process_mask_for_display(pred)
            img = _Image.fromarray(final_u8).convert("RGB")

            if show_seg:
                palette = load_label_palette(env.PALETTE_PATH)
                overlay = overlay_on_image(img, cleaned_pred, palette, alpha=0.5)
                arr = np.asarray(overlay).copy()
            else:
                arr = np.asarray(img).copy()

            if show_meas:
                rf_pair = post_process_rf(pred)
                contour = rf_pair.rf_full_mask ^ rf_pair.rf_eroded_mask
                arr[contour] = [255, 0, 255]
                # F11 — RF/VI peak-thickness columns + QMT span + q50 col.
                arr = annotate_overlay(arr, pred, rf_pair.rf_eroded_mask)

            if show_heatmap:
                conf_native = (
                    st.session_state.get("confidence_map_cache") or {}
                ).get(fb)
                if conf_native is not None:
                    arr = confidence_heatmap_overlay(
                        arr, conf_native, change_map=change_map,
                    )

            cap_layers = []
            if show_seg: cap_layers.append("segmentation")
            if show_meas: cap_layers.append("measurements")
            if show_heatmap: cap_layers.append("confidence")
            layer_label = " + ".join(cap_layers) if cap_layers else "raw only"
            st.image(arr, caption=f"Anchor = {fb} · {layer_label}",
                     width="stretch")
        elif final_u8 is not None:
            st.image(final_u8, caption=f"Anchor frame · {fb}", width="stretch")
    st.caption(
        "Measurements compute on the muscle interior — we drop a 3-pixel "
        "boundary (magenta contour) to avoid edge artefacts."
    )


def _fmt(d: dict | None, unit: str = "", nd: int = 2) -> str:
    """median (p25–p75) for the reference strip; '—' when the population lacks the feature."""
    if not d:
        return "—"
    return f"{d['median']:.{nd}f} ({d['p25']:.{nd}f}–{d['p75']:.{nd}f}){unit}"


def _cohort_reference_control() -> None:
    """The placement REFERENCE POPULATION selector, and WHAT EACH POPULATION IS.

    Moved here from p01 (Akadir 2026-08-03) for two reasons that turned out to be one. Relevance: the
    control belongs on the page where its effect is visible, not on the study picker two clicks back.
    Magnitude: flipping it is not a nuance — the same old-round study reads p90.2 for star_ratio against
    OLD and p49.5 against NEW. A 40-point swing whose control lived on another page, with its caveat in
    a caption you had already left behind.

    The medians below are the anchor that makes the swing legible instead of alarming: OLD has median
    age 49 and star_ratio 1.24, NEW has age 30 and star_ratio 1.72. Same millimetres, different peers.
    Without the strip the user has to guess what they just switched to."""
    from preflight_helpers.cohort_placement import (
        ACTIVE_OLD_VERSION, COHORT_SOURCES, NEW_COHORT_MIN_N, OLD as _O, NEW as _N, MIXED as _M,
        cohort_n, cohort_reference_summary)
    # Seed from the STUDY's own round, not a fixed constant — this is the widget that actually sets
    # `cohort_source`, so seeding it OLD made every reader's default academic. A v8 patient placed
    # against the legacy reference reads far too well (p82 texture / p91 size for a perfectly average
    # one, against p50 on its own round), because the older probe and software imaged brighter and
    # flatter. The user can still pick any of the three; this only chooses where it opens.
    if st.session_state.get("cohort_source") not in COHORT_SOURCES:
        st.session_state.cohort_source = _default_cohort_source()
    ns = {s: cohort_n(s) for s in COHORT_SOURCES}
    labels = {_O: f"Old · {ns[_O]}", _N: f"New round · {ns[_N]}", _M: f"Mixed · {ns[_M]}"}
    st.radio(
        "Reference cohort — who this patient is placed against", options=list(COHORT_SOURCES),
        format_func=lambda s: labels[s], horizontal=True, key="cohort_source",
        help=f"Changes the PERCENTILES on this page, live. The condition SCORE is fixed to the "
             f"{ACTIVE_OLD_VERSION} model and does not move — new/mixed have no doctors' grades to "
             f"refit against.")

    s = cohort_reference_summary(st.session_state.cohort_source)
    if not s["n"]:
        st.caption(":orange[This population has no studies yet — percentiles are unavailable.]")
        return
    f = s["features"]
    sex = " / ".join(f"{k} {v}" for k, v in sorted(s["sex"].items())) or "sex not recorded"
    st.caption(
        f"**n = {s['n']}**  ·  age {_fmt(f.get('age'), nd=0)}  ·  BMI {_fmt(f.get('bmi'), nd=1)}  ·  {sex}")
    st.caption(
        f"median (IQR) — RF {_fmt(f.get('rf_thickness_mm'), ' mm')}  ·  "
        f"fat {_fmt(f.get('fat_thickness_q50_mm'), ' mm')}  ·  "
        f"QMT {_fmt(f.get('qmt_mm'), ' mm')}  ·  "
        f"STAR {_fmt(f.get('star_ratio'))}  ·  "
        f"echo int_cv {_fmt(f.get('int_cv_within'))}")
    # The one caveat that is not merely informational: geometry compares cleanly across rounds (a
    # millimetre is a millimetre), echo texture does not — the probe/software footprint differs, and
    # int_cv_within carries 43% of the score. Surfaced HERE, at the moment of choosing, rather than in
    # a caption on the page the user has already left.
    if st.session_state.cohort_source in (_N, _M):
        st.caption(":orange[⚠ Echo texture (int_cv) is **not** directly comparable across the "
                   "old↔new boundary — different probe/software footprint. Geometry (thicknesses, "
                   "fat, STAR) is comparable.]")
    if ns[_N] < NEW_COHORT_MIN_N:      # ported from p01 with the selector — a thin reference is
        st.caption(f":orange[New/Mixed are indicative — only {ns[_N]} new-round studies "
                   f"(< {NEW_COHORT_MIN_N}), not a stable baseline yet.]")


def _default_cohort_source() -> str:
    """Which reference a study is placed against when the user has not chosen one.

    A patient must be compared to people imaged on the same equipment. The older probe and software
    produced brighter, flatter images, so a v8 study placed against the legacy reference reads far
    too well — a perfectly average new patient lands at p82 on texture and p91 on size against OLD,
    against p50 on its own round (2026-08-04). So the default follows the STUDY's round rather than
    being a fixed constant: v8 goes to the new reference, legacy stays on the old one. The selector
    still offers all three; this only sets where it starts."""
    try:
        from preflight_helpers.study_discovery import study_is_new_round
        entry = st.session_state.get("study_entry")
        if entry is not None and study_is_new_round(entry.study_path):
            return NEW
    except Exception:                       # noqa: BLE001 - a default must never break the page
        pass
    return OLD


def render() -> None:
    if st.session_state.study_entry is None or st.session_state.learn is None:
        st.info("Earlier phases not complete.")
        return
    entry = st.session_state.study_entry
    sm = entry.meta.to_ui_dict()
    run_data = _ensure_run()
    # cohort_source is a DISPLAY-time lens (old/new/mixed population). _ensure_run caches placements
    # against the canonical LEGACY (v1.0) cohort for a stable run signature; the display re-derives them
    # LIVE for the selected population so the headline percentile, "vs N peers", story caption, breakdown
    # AND box plots all place against the SAME selected cohort (the cached aggregate/features are
    # source-independent; the SCORE is fixed to ACTIVE_OLD_VERSION below — so cohort_source stays OUT of
    # the run signature and never branches a draft on a radio flip). Every UI source (old/new/mixed)
    # differs from the LEGACY cache, so it always re-derives; only the vestigial LEGACY rollback source
    # reuses the cache. Akadir 2026-07-14 (default is 'old' = the active re-based cohort).
    _src = st.session_state.get("cohort_source") or _default_cohort_source()
    if _src != LEGACY:
        from preflight_helpers.feature_routing import placements_for as _placements_for
        run_data["placements"] = _placements_for(
            run_data["aggregate"], entry.meta.age, entry.meta.sex, source=_src)
    # The study landmark (size features' selection-independent second placement) — from the
    # PURPOSE-OWNING modality: the femur cine on a routed study, the sweep on a legacy study.
    _lm = _ensure_landmark(entry, entry.meta.age, entry.meta.sex,
                           routed=run_data.get("routed_modalities") or [])
    run_data["_landmark"] = _lm["features"]
    run_data["_landmark_reason"] = _lm["reason"]
    cond = _score_condition(run_data, _src)   # version-matched headline score (fails silent)

    # Staleness guard: the served model is fit on a FROZEN cohort that does NOT re-measure when
    # the measurement code changes. If a measurement fix has landed since the freeze (e.g. the
    # 2026-07-16 QMT cleaned-femur), the cohort is a code-version behind and a re-base is owed —
    # surface it, never serve stale silently. Advisory (`condition_model.staleness`).
    _art_for_stale = _condition_models(_src)
    if _art_for_stale is not None and condition_model.staleness(_art_for_stale)["stale"]:
        st.caption(":orange[⚙ Score model predates a measurement-code change — a cohort re-base "
                   "is pending, so values are indicative until it re-measures.]")

    # Run-status panel (N4): Draft → Accept → Finalized, or read-only Cached.
    _run_status_panel(run_data)

    # What this build currently believes, on the page rather than in a doc — so the direction is
    # legible by running the app, and a provisional choice cannot quietly read as a settled one.
    from labeling_app.assessment_pages import direction_notes
    direction_notes.render(st)

    # Study header.
    header = f"**{sm['study_code']}**"
    sub = (f"{sm['age']}y {sm['sex'] or '—'}"
           + (f" · BMI {sm['bmi']:.1f}" if sm["bmi"] is not None else "")
           + (f" · {sm['height_cm']} cm" if sm["height_cm"] else "")
           + (f" · {sm['weight_kg']} kg" if sm["weight_kg"] else ""))
    st.markdown(f"## {header}")
    st.caption(sub)

    # v8 routing caveat — surface the shared semantics + caveat the cleaner-view difference; the
    # score uses the modality that measures each quantity best. No gating (docs/implementation.md A5).
    _routed = run_data.get("routed_modalities") or []
    if _routed:
        _bits = []
        if "femur" in _routed:
            _bits.append("geometry, STAR/QMT and fat on the **femur cine**")
        if "rf" in _routed:
            _bits.append("echo-intensity on the **center-RF cine**")
        st.caption(
            f":gray[**v8 protocol** — {', '.join(_bits)} (the dedicated views for them), not the "
            f"sweep. The same quantities as the legacy cohort, from a cleaner view; refined as the "
            f"new round grows — pick the new/mixed cohort to place against the matching round.]")

    # Capture present but no usable pick -> that modality's features silently fell back to the sweep.
    # Surface it (do NOT gate — the score still renders from the sweep value). Akadir 2026-06-26 #19.
    from preflight_helpers.study_discovery import study_modalities as _sm_fallback
    _fellback = [r for r in ("femur", "rf")
                 if r in _sm_fallback(entry.study_path) and r not in _routed]
    if _fellback:
        _what = {"femur": "size, fat, STAR and QMT", "rf": "echo intensity (fascicular contrast)"}
        st.warning(
            "⚠ This study has a " + " and ".join(_fellback) + " capture, but no usable pick from it "
            "— so " + " · ".join(f"**{r}** ({_what[r]})" for r in _fellback) + " fell back to the "
            "**sweep**. Pick and keep a frame from that view in review (phase 4) for the cleaner-view "
            "measurement.")

    # Own-study EI cross-check — the long sweep's only role (Akadir 2026-06-26): does its echo contrast
    # agree with the center-RF's? The sweep has no cohort value, so this is the only place its number shows.
    _eix = (run_data.get("aggregate") or {}).get("_ei_crosscheck")
    if _eix and _eix.get("agrees"):
        st.caption(f":gray[Echo cross-check ✓ — the long sweep's contrast agrees with the center-RF "
                   f"(within {_eix['rel_diff'] * 100:.0f}%); the texture read is robust.]")
    elif _eix:
        st.warning(f"⚠ Echo cross-check — the long sweep's contrast differs from the center-RF by "
                   f"{_eix['rel_diff'] * 100:.0f}%; the texture read may be acquisition-sensitive "
                   f"(the score uses the center-RF value).")

    # Story caption — directly above the measurement card.
    st.info(_story_caption(sm["study_code"], run_data))
    # F10a (audit-v2) — transparency micro-caption. Akadir asked on the audit:
    # "who actually writes the assessment in blue? ... I need to be able to
    # replicate it. Can I? How?" Answer: it is a deterministic template, no LLM.
    st.caption(
        ":gray[How this caption is written: a deterministic template over the "
        "cohort-placement percentiles — no language model, no hand-editing. "
        "Same study + same frames + same gates → the exact same sentence, every "
        "run. AGZ855 uses the ratified verbatim text from the design doc.]"
    )

    # Segmentation overlay on the anchor frame.
    cols = st.columns([3, 2])
    with cols[0]:
        if run_data.get("routed_modalities"):
            # v8: show the frames the score is built FROM — the femur pick
            # (geometry) + the center-RF pick (echo-intensity) — not the legacy
            # single sweep anchor (Akadir 2026-06-26 audit, #1/#7).
            _render_routed_panes(entry, run_data)
        else:
            _render_legacy_anchor(run_data)
    with cols[1]:
        scale_max = int(st.session_state.get("cond_scale_max", 100))
        # The single score, top-down: the headline, the doctors' grade it reproduces,
        # the co-equal size/fat/tissue breakdown (the dimensional read that makes one
        # number safe), then the (i) formula. All additive + fail-silent.
        # The measurement-trust strip (QA surface step 1, Akadir 2026-08-13) — one glance at what
        # Size / Texture / Strength each rest on. It ABSORBS the #17 RF-reliability chip + its
        # withhold notice (same vocabulary, same trigger); on any failure it degrades to the
        # original chip so #17 is never silently absent.
        try:
            from labeling_app.assessment_pages import trust_strip
            from preflight_helpers import force_card as _fc
            from preflight_helpers.study_discovery import study_is_new_round as _inr
            trust_strip.render(st, run_data, _texture_state(run_data),
                               _fc.payload_for_uid(entry.meta.study_uid),
                               is_new_round=_inr(entry.study_path),
                               study_uid=entry.meta.study_uid)
            # The evidence panel (spec §4b): canonical vs this-run numbers + the release/confirm
            # actions, armed by Accept run. Additive + fail-silent like every panel block.
            try:
                from preflight_helpers import release_actions as _ra
                _dec = ((run_data.get("aggregate") or {}).get("_rf_reliability") or {}).get("decision")
                trust_strip.render_evidence(
                    st, run_data, entry.meta.study_uid,
                    actions=_ra.pending_actions(entry.meta.study_uid, _dec))
            except Exception:
                pass
        except Exception:
            _texture_reliability_chip(_texture_state(run_data))   # #17 chip / withhold notice
        _condition_headline(cond, scale_max)
        _condition_rater_chip(sm["study_code"], cond)
        _condition_breakdown(cond, run_data)
        _condition_formula(_condition_models(_src), _condition_options(_src), cond, scale_max,
                            routed=bool(run_data.get("routed_modalities")))
        st.markdown("**Key measurements**")
        # F9 — stack the headline-4 vertically so each card gets a full
        # caption + F7 direction line instead of being squeezed 4-across.
        _measurement_card(run_data, HEADLINE_FEATURES, layout="stack")
        # The run-level pooled-disclosure receipt counts SWEEP frames + sweep QA, so it does NOT
        # describe a femur/RF-routed study's displayed numbers — suppress it there (per-frame QA
        # lives in the p04 per-modality review). Legacy single-sweep keeps it. Akadir 2026-06-26 #16.
        if not run_data.get("routed_modalities"):
            _pooled_disclosure(run_data)
        # Landmark visibility — when the size features carry no landmark line, say
        # WHY here instead of leaving it silently absent (so "where is the landmark"
        # always has an answer on screen).
        if not run_data.get("_landmark") and run_data.get("_landmark_reason"):
            st.caption(f":gray[ℹ Study landmark not shown — {run_data['_landmark_reason']}]")

    # Strength — the force axis on the page: measured torque next to the range this profile
    # usually produces (the frozen demographics+geometry model). A context surface; force stays
    # OUT of the condition score (Akadir 2026-08-11 "force into the app, not into the equation").
    # New-round only: legacy studies have no dynamometer data, and a cross-protocol expected-band
    # would reopen the old↔new comparability question for no reader benefit
    # (docs/force_card_spec.md). ADDITIVE + fail-silent, like every panel block.
    try:
        from labeling_app.assessment_pages import force_panel
        from preflight_helpers import force_card
        from preflight_helpers.study_discovery import study_is_new_round as _is_new_round
        _fp = force_card.payload_for_uid(entry.meta.study_uid)
        # legacy studies render nothing (v1 scope); the substrate hint is for new-round
        # studies only — force_card.should_render on the docstring-owned legacy/new split.
        if force_card.should_render(_fp, _is_new_round(entry.study_path)):
            force_panel.render(_fp, st)
    except Exception:
        pass

    # Per-frame Image-quality panel REMOVED on p05 (Akadir 2026-05-24 v2
    # reframe Layer 2c). The summary page stays calm — per-card badges
    # surface degradation at a glance and point to **review (phase 4)** for
    # per-frame detail. The detail panel + per-measurement attribution +
    # layer-stack heatmap all live on p04 now (commits 87eb83d + 0b65039).

    # Secondary features in a collapsible.
    with st.expander("Secondary features", expanded=False):
        _measurement_card(run_data, SECONDARY_FEATURES, layout="row")

    # Cohort distributions — the percentile leads on each card above; the box
    # plots are opt-in detail below (Akadir 2026-06-10: a single number, not a
    # wall of box plots). The header + caption stay at TOP level (moving them into
    # the expander destabilised AppTest widget-state carry-forward).
    st.divider()
    st.markdown("### Cohort distributions")
    from preflight_helpers.cohort_placement import cohort_n as _cohort_n
    _coh_src = st.session_state.get("cohort_source") or _default_cohort_source()
    _coh_label = {OLD: "old-round", NEW: "new-round", MIXED: "mixed old+new"}.get(_coh_src, "old-round")
    st.caption(
        "The percentile on each card above is the headline; these box plots are "
        "the full distribution behind it. Box = cohort p25–p75, whiskers p10–p90, "
        "the line is the median; the "
        "red diamond is this case (with its percentile). For higher-is-better "
        f"features the green half marks the better direction. Reference cohort: the "
        f"**{_coh_label}** cohort (n={_cohort_n(_coh_src)}); age-matched is ±10 years; sex is M/F "
        "when known (recorded for ~58% of the cohort, so the sex-matched box rests on fewer "
        "studies). The green line is conservative — it claims a strength only when every "
        "available cohort agrees."
    )
    # Same as the per-card QA badge: the cohort caveat reads the aggregate's _qa_tier = the SWEEP's on
    # a routed study, so it would mis-describe the femur/RF numbers — suppress on routed (Akadir 2026-06-26).
    _qa_caveat = (None if run_data.get("routed_modalities")
                  else qa_surfacing.cohort_caveat(run_data.get("aggregate", {})))
    if _qa_caveat:
        st.caption(f":orange[⚠ {_qa_caveat}]")
    age = entry.meta.age
    sex = entry.meta.sex

    def _cohort_row(fname: str) -> None:
        st.markdown(f"**{_display_label(fname, run_data)}**")
        # QMT + STAR: a proxy / not-measurable value is not comparable to the
        # Tier-1 (standard-QMT) cohort — suppress the box-plot and say why.
        if fname in ("qmt_mm", "star_ratio"):
            note = _qmt_provenance_note(run_data)
            if note is not None:
                st.markdown(f":{note['colour']}[⚠ {note['badge']}]")
                st.caption(f":gray[{note['caption']}]")
                return
        v = run_data["aggregate"].get(fname)
        placement = run_data["placements"].get(fname) or {}
        src = st.session_state.get("cohort_source") or _default_cohort_source()
        box = cohort_box(fname, age, sex, source=src)
        # F7-style direction caption above the chart.
        phrase, sentiment = cohort_aware_phrase(
            (placement.get("overall") or {}).get("pctile"),
            (placement.get("age_matched") or {}).get("pctile"),
            (placement.get("sex_matched") or {}).get("pctile"), fname)
        color = _SENTIMENT_COLOR[sentiment]
        icon = _SENTIMENT_ICON[sentiment]
        st.markdown(f":{color}[{icon} {phrase}]")
        _rf_routed = "rf" in (run_data.get("routed_modalities") or [])
        if is_intensity_feature(fname) and (src != OLD or _rf_routed):
            _why = ("measured on the center-RF cine (a different view) and gain/probe-sensitive"
                    if _rf_routed else "not directly comparable across probe-software versions")
            st.caption(f":orange[⚠ echo-intensity feature — {_why}; geometry (thicknesses, fat) is not]")
        if not any((box or {}).values()):
            # An empty distribution rendered as a blank plot reads like a bug; say WHY instead
            # (Akadir 2026-08-14, "all end-points for the new data/cohort really work"). The one
            # standing case: the legacy reference stores the FULL RF area, the app measures the
            # VISIBLE RF area — different quantities, no comparable box.
            _msg = ("the legacy reference stores the FULL RF area, not the visible-RF area "
                    "measured here — switch the reference to New round or Mixed for this box"
                    if fname == "visible_rf_area_mm2" and src == OLD
                    else "no distribution for this feature in the selected reference")
            st.caption(f":gray[box plot unavailable — {_msg}.]")
            return
        fig = _cohort_box_figure(fname, v, box, placement,
                                 cohort_points(fname, age, sex, source=src))
        st.plotly_chart(fig, width="stretch", config={"displayModeBar": False})

    # The percentile leads on each card above; the box plots are opt-in detail
    # (Akadir 2026-06-10: a single number, not a wall of box plots).
    with st.expander("📊 Cohort box plots — optional distribution detail",
                     expanded=False):
        for fname in HEADLINE_FEATURES + SECONDARY_FEATURES:
            _cohort_row(fname)

    # The 3-model comparison sits with the cohort detail (opt-in, below the score).
    _condition_triplet(cond, int(st.session_state.get("cond_scale_max", 100)))

    # F5 + N1 — this run vs the compounding study bootstrap.
    st.divider()
    st.markdown("### Run history")
    _run_comparison_panel(sm["study_code"], run_data)

    # Trajectory affordances + dev info.
    st.divider()
    st.markdown("### Trajectory (next, in development)")
    _trajectory_panel()

    if st.session_state.dev_mode:
        with st.expander("Developer info — full run record", expanded=False):
            st.code(f"record: {run_data['record_path']}\nrun_id: {run_data['run_id']}",
                    language="text")
            st.json(run_data["aggregate"], expanded=False)
            st.json(run_data["placements"], expanded=False)

"""v8 feature routing — the assessment reads each feature from the modality that measures it best:
geometry + fat + STAR/QMT from the FEMUR cine, echo-intensity (int_cv / int_*) from the CENTER-RF
cine, everything else from the SWEEP. These are the SAME anatomical quantities the legacy cohort
measured (legacy QMT/STAR already came from femur-bearing frames via the qmt_provenance gate), so the
frozen model applies — a modest, recorded measurement-view shift, not an out-of-distribution break
(docs/implementation.md A5). A legacy single-modality study (no captures/) routes everything to the
sweep, unchanged. The compute path is identical to the sweep's, so a modality's features are computed
the same way; only the source frames + that modality's pixelspacing differ.
"""
from __future__ import annotations

from pathlib import Path

from preflight_helpers.aggregation import (_GEOMETRY_FEATURES, _INTENSITY_FEATURES,
                                           _MEDIAN_PRIMARY_FEATURES, aggregate_features)
from preflight_helpers.study_discovery import study_modalities

# Per-VIEW routing (Akadir 2026-07-14 — ALL measurements from the femur cine, TEXTURE only from center-RF).
# This REVERSES the 2026-06-26 "center-RF for RF geometry" split: the shallow-clip discovery showed the
# center-RF cine (shot ~4.9 cm) systematically clips the RF's lower fascia, so its geometry is unreliable
# even though its pixels are finer. The femur cine, at the deep FOV, ALWAYS resolves the complete lower
# fascia — so it is the ONE consistent source for every geometry + fat + QMT/STAR measurement across all
# studies (a mixed-source cohort — some studies' thickness from center-RF, some from femur — isn't
# comparable). The center-RF cine contributes ONLY the echo/TEXTURE family (int_cv & the int_* intensities
# + rf_echo), which reads pixels inside the RF mask and wants the finer near-field. The femur-vs-center
# thickness comparison NO LONGER routes geometry (always femur now) — it only gates whether that center-RF
# texture is TRUSTWORTHY (`rf_reliability.resolve_rf_source(...).int_cv_trusted`). The tradeoff accepted:
# femur pixels are ~1.4-2x coarser, but a complete boundary beats a finer-but-clipped one. A legacy
# single-modality study (no femur/RF aggregate) routes everything from the sweep, unchanged.
_TEXTURE_FEATURES = frozenset(_INTENSITY_FEATURES | {"rf_echo"})   # the ONLY family read from the center-RF cine
FEMUR_FEATURES = frozenset((_GEOMETRY_FEATURES - {"rf_echo"})
                           | {"qmt_mm", "star_ratio", "fat_to_depth_q50",
                              "fat_thickness_q50_mm", "fat_thickness_max_mm", "muscle_fat_ratio"})
CENTER_RF_FEATURES = _TEXTURE_FEATURES                             # exported name for the center-RF (texture) set
_QMT_PROVENANCE_KEYS = ("qmt_provenance", "qmt_n_tier1", "qmt_n_tier2", "qmt_n_tier3")
# Own-study EI cross-check tolerance — the long sweep's ONLY job is to confirm its echo intensity agrees
# with the center-RF's (a within-study robustness check; the sweep has NO cross-study/cohort value).
_EI_CROSSCHECK_TOL = 0.20


def _variants(base: str):
    return (base, f"{base}_repr", f"{base}_agg_median")


def route_aggregate(sweep_agg: dict, femur_agg: dict, rf_agg: dict) -> dict:
    """Merge per-modality aggregates: ALL measurements (geometry + fat + QMT/STAR) <- femur, the
    echo/TEXTURE family <- center-RF, everything else <- sweep. Each feature carries its bare key +
    `_repr` + `_agg_median`; the QMT provenance follows the femur. A missing femur/RF aggregate ({})
    leaves the sweep value in place (graceful — a legacy sweep-only study routes entirely from the sweep).

    Akadir 2026-07-14: the femur cine is the single consistent source for every measurement (its deep FOV
    always resolves the RF's complete lower fascia); the center-RF cine, clipped at its shallow FOV,
    contributes ONLY texture. The femur-vs-center thickness agreement no longer routes geometry — it is
    used in `rf_reliability` purely to decide whether the center-RF texture is trustworthy."""
    out = dict(sweep_agg or {})
    if femur_agg:
        for base in FEMUR_FEATURES:
            for k in _variants(base):
                if k in femur_agg:
                    out[k] = femur_agg[k]
        for k in _QMT_PROVENANCE_KEYS:
            if k in femur_agg:
                out[k] = femur_agg[k]
    if rf_agg:
        for base in CENTER_RF_FEATURES:
            for k in _variants(base):
                if k in rf_agg:
                    out[k] = rf_agg[k]
    return out


def _gate_kept(picked_bases, gates_map=None) -> list:
    """Frames surviving the operator's keep/drop gate (the p04 per-modality review): a frame feeds the
    aggregate unless it was marked usable=No or use-for-analysis=No. An absent/undecided frame defaults
    to KEEP (matches the pre-gate behavior). Pure + torch-free so the routing gate is unit-testable.
    Akadir 2026-06-25."""
    gm = gates_map or {}
    return [fb for fb in (picked_bases or [])
            if (gm.get(fb, {}).get("usable", True) is not False)
            and (gm.get(fb, {}).get("use_for_analysis", True) is not False)]


def compute_modality_aggregate(raw_image_dir, dcm_dir, picked_bases, *, bmi, bundle,
                               learn=None, bundle_meta=None, device=None, gates_map=None,
                               texture_refine=None, frames_out=None) -> dict:
    """Segment a modality's picks from its own folder, measure (with its DICOM's pixelspacing), and
    median — the SAME compute path the sweep uses (run_pipeline internals), so the features are
    computed identically. Returns {} for no picks. Pass `learn`/`bundle_meta` from the loaded session
    to avoid a model reload. `texture_refine` ({erosion_px, band}) — only meaningful for the center-RF
    call: when the operator refined the RF texture region, re-read int_cv from the vetted section.

    `frames_out` (optional out-param, PURE ADDITIVE — the return value is unchanged): when a dict is
    passed, it is populated with the frame IDENTITIES this aggregate actually stood on —
    `kept_bases` (the gated/analysis frames that fed the median, [] on an early return) and `anchor`
    (the anchor frame_base, or None). This lets a routed run persist its femur/RF frame identities so
    the record is reproducible + reloadable (previously the picks were discarded after the median)."""
    if not picked_bases:
        if frames_out is not None:
            frames_out['kept_bases'] = []
            frames_out['anchor'] = None
        return {}
    import torch
    from preflight_helpers.run_pipeline import (_load_or_predict_masks, _spacing_row_from_dicom,
                                                PickedFrame, GateAnswers)
    from preflight_helpers.feature_compute import (compute_frame_features,
                                                   compute_image_quality_features)
    from preflight_helpers.post_processing import post_process_rf
    from preflight_helpers.segmenter import BundleMeta, load_learner_safe, LABEL_RF
    device = device or torch.device("cuda" if torch.cuda.is_available() else "cpu")
    bundle_meta = bundle_meta or BundleMeta.from_pkl(bundle.pkl_path)
    learn = learn if learn is not None else load_learner_safe(bundle.pkl_path)
    # Honor the operator's keep/drop from the p04 per-modality review (mirrors the sweep). Akadir 2026-06-25.
    kept = _gate_kept(picked_bases, gates_map)
    if not kept:
        if frames_out is not None:
            frames_out['kept_bases'] = []
            frames_out['anchor'] = None
        return {}
    pfs = [PickedFrame(frame_base=fb, is_anchor=(i == len(kept) // 2),
                       gates=GateAnswers(usable=True, use_for_analysis=True))
           for i, fb in enumerate(kept)]
    final_cache, bentropy = {}, {}
    masks, _ = _load_or_predict_masks(picked_frames=pfs, raw_image_dir=raw_image_dir, bundle=bundle,
                                      bundle_meta=bundle_meta, learn=learn, device=device,
                                      use_annotator_masks=False, annotator_mask_dir=None,
                                      final_u8_out=final_cache, boundary_entropy_out=bentropy)
    spacing = _spacing_row_from_dicom(dcm_dir, masks[0].shape[:2] if masks else None)
    spacing["bmi"] = bmi
    anchor = next((pf.frame_base for pf in pfs if pf.is_anchor), None)
    rows = []
    for pf, m in zip(pfs, masks):
        rfp = post_process_rf(m, rf_class_id=LABEL_RF, erosion_px=3, fill_holes=False)
        ff = compute_frame_features(frame_base=pf.frame_base, is_anchor=pf.is_anchor, multiclass_mask=m,
                                    all_frame_masks=masks, final_image_path=None,
                                    final_image_u8=final_cache[pf.frame_base], spacing_row=spacing,
                                    rf_mask_pair=rfp).to_dict()
        ff["image_quality"] = compute_image_quality_features(
            frame_base=pf.frame_base, multiclass_mask=m, final_image_path=None,
            final_image_u8=final_cache[pf.frame_base], spacing_row=spacing,
            boundary_entropy=bentropy.get(pf.frame_base), qmt_provenance=ff.get("qmt_provenance")).to_dict()
        rows.append(ff)
    agg = aggregate_features(rows, anchor_frame_base=anchor)
    # Phase 2 (Akadir 2026-07-14): the operator's texture refine — re-read int_cv from a VETTED RF
    # section (more erosion and/or a depth band trimming the contaminated deep edge). Recompute the
    # median int_cv over the vetted regions and OVERRIDE it; only when actually refined (else the default
    # 3px aggregate stands), and only if it yields a usable region (else leave int_cv untouched — never
    # fake one). The refine is captured as {erosion_px, band} numbers upstream, so this is reproducible.
    if texture_refine:
        from preflight_helpers.texture_refine import refined_int_cv, is_refined
        _ero = texture_refine.get("erosion_px", 3)
        _band = tuple(texture_refine.get("band", (0.0, 1.0)))
        if is_refined(_ero, _band):
            _ricv = refined_int_cv({pf.frame_base: m for pf, m in zip(pfs, masks)},
                                   final_cache, _ero, _band)
            if _ricv is not None:
                for _k in ("int_cv_within", "int_cv_within_repr", "int_cv_within_agg_median"):
                    if _k in agg:
                        agg[_k] = _ricv
    if frames_out is not None:
        frames_out['kept_bases'] = list(kept)
        frames_out['anchor'] = anchor
        # Per-frame features, for the record (refine-surface spec §4b): routed runs previously
        # persisted only frame BASES, which left the study bootstrap and any future pool-median
        # unable to reach frame level. JSON-safe by construction — the same to_dict shape legacy
        # sweep records have always stored in `frame_features`.
        frames_out['frame_features'] = rows
    return agg


def route_study_aggregate(study_path, sweep_agg: dict, *, picks_femur, picks_rf, bmi, bundle,
                          learn=None, bundle_meta=None, device=None, gates_femur=None,
                          gates_rf=None, texture_choice=None, texture_refine=None) -> dict:
    """Full routing for a study: if it has femur/RF captures + picks, compute their aggregates from
    those picks and merge into the sweep aggregate. A legacy single-modality study (no captures/) or
    no femur/RF picks returns the sweep aggregate unchanged. Stamps `_routed_modalities` (the roles
    actually merged) so the UI can surface the caveat only when it applies. The DICOM dir for each
    modality's spacing is its capture folder (the RawImages dir's parent)."""
    mods = study_modalities(Path(study_path))
    femur_agg = rf_agg = {}
    fem_out: dict = {}
    rf_out: dict = {}
    # A modality with no .dcm has no resolvable spacing — skip it (its features stay on the sweep)
    # rather than crash the page; in practice every materialized study carries each modality's .dcm.
    if "femur" in mods and picks_femur and mods["femur"].dicom_path is not None:
        m = mods["femur"]
        femur_agg = compute_modality_aggregate(m.raw_image_dir, m.raw_image_dir.parent,
                                               list(picks_femur), bmi=bmi, bundle=bundle,
                                               learn=learn, bundle_meta=bundle_meta, device=device,
                                               gates_map=gates_femur, frames_out=fem_out)
    if "rf" in mods and picks_rf and mods["rf"].dicom_path is not None:
        m = mods["rf"]
        rf_agg = compute_modality_aggregate(m.raw_image_dir, m.raw_image_dir.parent,
                                            list(picks_rf), bmi=bmi, bundle=bundle,
                                            learn=learn, bundle_meta=bundle_meta, device=device,
                                            gates_map=gates_rf, texture_refine=texture_refine,
                                            frames_out=rf_out)
    routed = route_aggregate(sweep_agg, femur_agg, rf_agg)
    routed["_routed_modalities"] = [r for r, a in (("femur", femur_agg), ("rf", rf_agg)) if a]
    # Persist the femur/RF FRAME IDENTITIES the routed aggregate stood on (Akadir 2026-07-20). A routed
    # run's number comes from these femur/RF picks — but the sweep-only run_pipeline record never stored
    # them, so a reload recomputed off EMPTY femur/RF picks → a wrong number. We stamp a slim, JSON-safe
    # per-role block so p05 can write it into the record + honestly seed frame_set. CRITICAL: store ONLY
    # the {usable, use_for_analysis} booleans per frame — NEVER the raw gate dict, whose drop_reason_codes
    # is a Python set() that would crash write_draft's json.dump.
    routed_picks: dict = {}
    for role, agg_role, picks, gm, out in (
            ("femur", femur_agg, list(picks_femur or []), gates_femur, fem_out),
            ("rf", rf_agg, list(picks_rf or []), gates_rf, rf_out)):
        if not agg_role:
            continue
        routed_picks[role] = {
            "frame_bases": sorted(picks),
            "gates": {fb: {
                "usable": (gm or {}).get(fb, {}).get("usable", True) is not False,
                "use_for_analysis": (gm or {}).get(fb, {}).get("use_for_analysis", True) is not False,
            } for fb in picks},
            "analysis_frame_bases": sorted(out.get("kept_bases") or []),
            "anchor": out.get("anchor"),
            "n_analysis_frames": len(out.get("kept_bases") or []),
            # per-frame features (2026-08-14): lets the bootstrap / future evidence pooling go
            # frame-level for runs recorded from today on (older records stay aggregate-level).
            "frame_features": out.get("frame_features") or [],
        }
    routed["_routed_picks"] = routed_picks
    # Own-study EI cross-check (Akadir 2026-06-26): the long sweep's ONLY role — does its echo intensity
    # agree with the center-RF's? A within-study robustness check; the sweep has NO cross-cohort value, so
    # its numbers never enter a placement. Surfaced in the UI; flagged when they diverge beyond the tol.
    _scv = (sweep_agg or {}).get("int_cv_within")
    _rcv = (rf_agg or {}).get("int_cv_within")
    if _scv is not None and _rcv:
        _rel = abs(_scv - _rcv) / abs(_rcv)
        routed["_ei_crosscheck"] = {"sweep": _scv, "rf": _rcv, "rel_diff": _rel,
                                    "agrees": _rel <= _EI_CROSSCHECK_TOL}
    # RF triple-screen reliability (Akadir 2026-07-13): the 3-way RF max-thickness -> the staged
    # decision (femur-anchored) + the texture (int_cv) audit state. ADDITIVE — the p04 gate reads
    # `.texture.needs_review` to fire, the p05 chip reads `.texture.chip`; no routing changes here.
    from preflight_helpers.rf_reliability import rf_reliability_stamp
    routed["_rf_reliability"] = rf_reliability_stamp(femur_agg, rf_agg, sweep_agg, texture_choice)
    return routed


def placements_for(aggregate: dict, age, sex, source: str = "legacy") -> dict:
    """Recompute cohort placements for a (routed) aggregate against a cohort `source`
    (legacy/new/mix — cohort_placement.LEGACY/NEW/MIX). Defaults to "legacy" so the _ensure_run
    persistence call stays canonical and reproducible; p05 re-derives placements LIVE in render() from
    the operator's cohort selector — a DISPLAY-time lens, since the aggregate + the condition score are
    source-independent (the score z-scores against the frozen model's mu/sd, not the cohort). Torch-free."""
    from preflight_helpers.cohort_placement import percentile
    from preflight_helpers.feature_catalog import ALL_DISPLAY_FEATURES
    out = {}
    for fname in ALL_DISPLAY_FEATURES:
        p = percentile(fname, aggregate.get(fname), age=age, sex=sex, source=source)
        out[fname] = {"value": p.value, "overall": p.overall,
                      "age_matched": p.age_matched, "sex_matched": p.sex_matched}
    return out

"""Aggregation policy for per-frame features into a per-study row.

Two policies, per the white-paper canonical:

  * `anchor_primary` — primary = anchor-frame value (`_repr`); the multi-frame
    median (`_agg_median`) is a sensitivity-check support column.
    Used by: GEOMETRY (full_thickness_q50_mm, full_area_mm2, full_max_thickness_mm,
    rf_thickness_mm, qmt_mm, visible_rf_area_mm2, ratios).

  * `agg_primary_with_anchor_passthrough` — primary = multi-frame median over
    QC-passing frames (`_agg_median`); `_repr` (anchor value) is the
    sensitivity check.
    Used by: INTENSITY family (int_cv_within and friends).

  * QMT 3-tier provenance gate — `qmt_mm` (and `star_ratio`, which rides on it)
    are NOT aggregated by either policy above. A frame's QMT is usable only if
    it is femur-anchored: Tier 1 = `standard_qmt`, Tier 2 = `femur_supported_proxy`
    with both RF+VI in the path, Tier 3 = everything else. The aggregate prefers
    Tier-1 frames, falls back to Tier-2 only when no Tier-1 frame exists, and is
    None when neither does — it never pools across tiers. This overrides
    `force_all_median` too, so the N5 study bootstrap gets the same gate.

  * Image-quality tier gate (RF Image-Quality QA plan § 6.2 / § 6.4 / § 7.3 —
    Build-Step 2 Phase 3 item 8) — every measurement above is, in addition,
    pooled only over the frames whose stored image-quality grade rates the
    measurement `valid` (falling back to `borderline` when no `valid` frame
    exists, never pooling an `invalid`-tier frame). `_aggregate_with_image_quality`
    is the per-measurement selector. It is inert on a run whose frames carry no
    QA grade, so a pre-QA / legacy run aggregates exactly as before. QMT/STAR
    are gated too — provenance stays the outer gate and the QA tier filters
    within the selected provenance tier (see `_aggregate_qmt`).

  * frame_rf_consistency gate (RF Image-Quality QA plan § 3.4 / § 14 Step 4 —
    Build-Step 2 Phase 3 item 9a) — `rf_thickness_mm` is, in addition, pooled
    only over the frames that are not robust rf-thickness outliers among their
    sweep siblings. `_rf_consistency_report` is the per-sweep selector: a frame
    whose `rf_thickness_mm` modified Z-score exceeds the registry cut is
    dropped from the pool. The reference median/MAD are taken over the
    image-quality-eligible subset, so a QA-`invalid` frame's thickness never
    skews them. It is a per-run guard — inert on the N5 study-bootstrap pool
    (`force_all_median`), and on a sweep below the registry minimum size or
    with no spread, so a cohort 3-frame run aggregates exactly as before.

When the user has gated frames in the UI, the input is the subset they
flagged `use_for_analysis=True`. A single-frame subset collapses both
policies to passthrough of that one frame.
"""
from __future__ import annotations

from statistics import median
from typing import Iterable, Optional


# Which features use which policy.
_GEOMETRY_FEATURES = {
    "rf_thickness_mm",
    "visible_rf_area_mm2",
    "rf_echo",
    "full_area_mm2", "full_thickness_q50_mm", "full_max_thickness_mm",
    "area_retained_fraction_after_erosion",
}
# Aggregate-median primary: the per-(valid-)frame value medianed (NOT anchor/repr).
# The fat family + muscle_fat_ratio live here — subcutaneous fat is ~flat along the
# sweep (no anatomical landmark; red-team 2026-06-09), so the across-frame median
# is the robust basis; and any RATIO must be formed PER FRAME then medianed
# (ratio-of-medians would mix frames). `fat_thickness_q50_mm` + `muscle_fat_ratio`
# moved here from anchor_primary 2026-06-10 (muscle_fat_ratio is also demoted from
# the card — see feature_catalog — and kept only as a diagnostic).
_MEDIAN_PRIMARY_FEATURES = {
    "fat_thickness_max_mm", "fat_to_depth_q50",
    "fat_thickness_q50_mm", "muscle_fat_ratio",
}
_INTENSITY_FEATURES = {
    "int_mean", "int_std", "int_cv_within", "int_median", "int_entropy",
}
# QMT + STAR are geometry-family by nature but aggregate under the provenance
# 3-tier gate (see module docstring), not anchor_primary. STAR = QMT/BMI rides
# on QMT's tier selection.
_QMT_FEATURES = {"qmt_mm", "star_ratio"}

# frame_rf_consistency gate — the MAD→σ consistency constant. 0.6745 = Φ⁻¹(0.75):
# scaling the median absolute deviation by it makes MAD a consistent estimator
# of σ for normal data, so the Iglewicz-Hoaglin modified Z-score reads in
# familiar σ-like units. This is a fixed property of the modified-Z method, not
# a tunable threshold — the tunable cut is `frame_rf_consistency__modified_z_max`
# in the qa_thresholds registry (§ 12.4 single-source rule).
_MAD_TO_SIGMA = 0.6745


def _nullsafe_median(xs: Iterable) -> Optional[float]:
    vals = [float(x) for x in xs if x is not None]
    if not vals:
        return None
    return float(median(vals))


def qmt_tier(frame: dict) -> int:
    """QMT quality tier for one frame — the locked 3-tier ladder.

      1 — `standard_qmt`: the real perpendicular fascia-to-femur measurement.
      2 — `femur_supported_proxy` with both RF+VI in the path: an RF+VI-stack
          proxy span that is at least femur-zone-anchored. Fallback-only.
      3 — no femur (`no_femur_proxy` / `no_measurement`), OR a *proxy* frame
          that is single-muscle in the path (`rf_or_vi_missing_in_path`), OR
          provenance missing (legacy records pre-date the field). Not usable.

    Single-muscle demotes a *proxy* to Tier 3 — it never demotes a standard
    measurement, which spans fascia-to-femur regardless of path content.
    """
    prov = frame.get("qmt_provenance")
    flags = frame.get("qmt_validity_flags") or []
    if prov == "standard_qmt":
        return 1
    if prov == "femur_supported_proxy" and "rf_or_vi_missing_in_path" not in flags:
        return 2
    return 3


def _aggregate_qmt(frame_dicts: list[dict], anchor_row: dict) -> dict:
    """QMT + STAR aggregate under the provenance-gated 3-tier policy, then the
    image-quality tier gate within the selected tier.

    Prefers Tier-1 frames; falls back to Tier-2 only when there is no Tier-1
    frame; the aggregate is None when neither tier has a frame. Never pools
    across provenance tiers. Applies in both the per-run path and the N5
    `force_all_median` path — QMT is exempt from anchor_primary and from the
    blind pooled-median.

    The QA image-quality tier (RF QA plan § 6.2 / § 7.3) then gates *within*
    the selected provenance tier: provenance is the outer gate — a standard
    fascia-to-femur measurement outranks a proxy regardless of image quality,
    so a QA-`valid` proxy never beats a QA-`borderline` standard — and the QA
    tier prefers QA-`valid` frames over QA-`borderline` ones and never pools a
    QA-`invalid` frame. If every frame of a provenance tier is QA-`invalid` for
    `qmt_mm`, that tier yields nothing poolable and the next tier is tried.
    `star_ratio` rides on `qmt_mm`'s selection (STAR = QMT / BMI). The gate is
    inert on a pre-QA run — a frame with no QA grade is never excluded.
    """
    tiers: dict[int, list] = {1: [], 2: [], 3: []}
    for f in frame_dicts:
        tiers[qmt_tier(f)].append(f)

    # Provenance is the outer gate; the QA image-quality tier filters within
    # the chosen provenance tier (see docstring). A tier emptied by
    # QA-invalidity falls through to the next.
    selected: list = []
    stamp, policy, qa_tier = None, "qmt_none_no_femur_valid", "none"
    for tier_frames, tier_stamp, tier_policy in (
        (tiers[1], "standard", "qmt_tier1_standard"),
        (tiers[2], "proxy", "qmt_tier2_proxy"),
    ):
        if not tier_frames:
            continue
        qa_selected, qa_t = _aggregate_with_image_quality(tier_frames, "qmt_mm")
        if qa_selected:
            selected, stamp, policy, qa_tier = qa_selected, tier_stamp, tier_policy, qa_t
            break

    out: dict = {
        "qmt_provenance": stamp,
        "qmt_n_tier1": len(tiers[1]),
        "qmt_n_tier2": len(tiers[2]),
        "qmt_n_tier3": len(tiers[3]),
        "_policies": {},
        "_qa_tier": {},
    }
    for fname in _QMT_FEATURES:
        # Support columns stay raw (anchor passthrough + median of all) for
        # transparency; the primary is the provenance- + QA-tier-selected median.
        out[f"{fname}_repr"] = anchor_row.get(fname)
        out[f"{fname}_agg_median"] = _nullsafe_median(f.get(fname) for f in frame_dicts)
        out[fname] = _nullsafe_median(f.get(fname) for f in selected)
        out["_policies"][fname] = policy
        out["_qa_tier"][fname] = qa_tier
    return out


# Feature -> § 6.2 gating-table key. `image_quality_tier_per_measurement` is
# keyed by the 8 gating-table measurements; `full_area_mm2` gates as
# `visible_rf_area_mm2` and the `int_*` family gates as `rf_echo` (RF QA plan § 6.2). A
# feature absent from this map and not itself a gating key
# (`full_max_thickness_mm`, `area_retained_fraction_after_erosion`) has no § 6.2
# verdict — the QA gate is then transparent to it.
_QA_MEASUREMENT_ALIAS = {
    "full_area_mm2": "visible_rf_area_mm2",
    "int_mean": "rf_echo", "int_std": "rf_echo",
    "int_median": "rf_echo", "int_entropy": "rf_echo",
}


def _aggregate_with_image_quality(
    frame_dicts: list[dict], measurement: str
) -> tuple[list[dict], str]:
    """The § 7.3 image-quality aggregation hook — the QA-tier sibling of
    `_aggregate_qmt`, applied per measurement.

    Partitions the feeding frames by their image-quality tier for `measurement`
    and applies the § 6.4 three-tier rule: prefer `valid` frames; fall back to
    `borderline` only when no `valid` frame exists; never pool an `invalid`-tier
    frame. Returns `(selected_frames, qa_tier)` — the frame subset that may feed
    `measurement`'s aggregate, and which tier it was drawn from
    (`valid` | `borderline` | `ungraded` | `none`).

    Each frame's tier is read from the QA grade the pipeline already computed
    and stored on it (`image_quality.image_quality_tier_per_measurement`); that
    tier is the § 6.2 mapping over the *calibrated* flag subset only (HANDOFF
    Finding B — see `feature_compute.compute_image_quality_features`). A frame
    with no QA grade — a legacy / pre-QA frame, an `ungraded` record, or a
    measurement outside the § 6.2 table — carries no `invalid` verdict and is
    never excluded; when *no* frame is QA-graded for the measurement the gate
    is inert and every frame is selected, so a pre-QA run aggregates exactly as
    before. Same prefer-better / fall-back-to-worse / never-pool-across-tiers
    discipline as `_aggregate_qmt`, with the QA tier as the tier source.
    """
    key = _QA_MEASUREMENT_ALIAS.get(measurement, measurement)
    buckets: dict[str, list[dict]] = {
        "valid": [], "borderline": [], "invalid": [], "ungraded": []}
    for f in frame_dicts:
        iq = f.get("image_quality") or {}
        tiers = iq.get("image_quality_tier_per_measurement") or {}
        tier = tiers.get(key)
        buckets[tier if tier in buckets else "ungraded"].append(f)
    if buckets["valid"]:
        return buckets["valid"], "valid"
    if buckets["borderline"]:
        return buckets["borderline"], "borderline"
    if buckets["ungraded"]:
        # No valid/borderline frame, but un-graded frames carry no `invalid`
        # verdict — they are pooled (a pre-QA run lands here in full).
        return buckets["ungraded"], "ungraded"
    # Every feeding frame is QA-`invalid` for this measurement.
    return [], "none"


def _rf_consistency_report(frame_dicts: list[dict], *, applies: bool = True) -> dict:
    """The § 14 Step-4 frame-consistency gate — promotes `frame_rf_consistency`
    from a passive sweep-σ metric to an active per-frame outlier gate
    (Build-Step 2 Phase 3 item 9a).

    Within one sweep RF thickness varies smoothly along the muscle belly; a
    frame whose `rf_thickness_mm` is a *robust* outlier among its siblings is
    almost always a mis-segmentation or an off-axis slice, and pooling it skews
    the `rf_thickness_mm` aggregate. This identifies such a frame with the
    Iglewicz-Hoaglin modified Z-score — `0.6745·(x − median)/MAD`. MAD (median
    absolute deviation), not σ, is the spread estimate on purpose: σ is itself
    inflated by the very outlier it would be used to detect, MAD is not. The
    gate makes a *pooling* decision, not a frame-quality verdict — a flagged
    frame is a "potential sweep outlier" (it may be a genuine anatomical
    extreme), never asserted to be defective.

    `applies` is the per-run scope gate (Build-Step 2 Phase-3 item-9 audit):
    the consistency gate is a *per-sweep* guard, so it runs only on a per-run
    aggregate. `applies=False` — the N5 study-bootstrap pool, a union across
    distinct finalized runs that is not a sweep and may carry duplicate
    `frame_base` — returns the inert report (`reason_inert='bootstrap_pool'`)
    and changes nothing; the bootstrap's pooled median is already outlier-robust
    on its own.

    Returns an audit dict — the same shape whether or not the gate fires, so
    the decision is fully inspectable downstream (the item-10 UI drilldown, the
    Build-Step 2b calibration substrate):

        measurement     — always 'rf_thickness_mm'
        active          — True iff a robust test actually ran
        reason_inert    — why not, when inactive:
                          'bootstrap_pool'  (the N5 pool — gate out of scope) |
                          'too_few_frames'  (< the registry minimum sweep size) |
                          'zero_mad'        (no spread — ≥ half the frames equal)
        n_examined      — frames carrying an `rf_thickness_mm` value
        median_mm, mad_mm, range_mm — the robust centre, the robust spread, and
                          the full min–max span (`range_mm` is recorded even on
                          a zero-MAD sweep, so a zero-MAD false-negative stays
                          visible — the signal Build-Step 2b calibrates against)
        modified_z_max  — the registry cut applied
        scores          — {frame_base: modified_z} for every examined frame
        excluded        — [frame_base, …] — the subset whose |modified_z| > cut
        trimmed_median_mm — the `rf_thickness_mm` median over the *kept* frames:
                          the clean sensitivity figure (None when inert)

    `frame_dicts` is the caller's already-image-quality-eligible pool, so a
    QA-`invalid` frame's (possibly garbage) thickness never enters the
    median/MAD reference. The gate is structurally pool-preserving: the
    modified Z-score centres on the median, so the median frame scores 0 and
    ~half the sweep scores within 1 MAD — it can never empty the pool.

    Scope is `rf_thickness_mm` only — the measurement `frame_rf_consistency` is
    defined on. Widening it to other geometry measurements, or surfacing the
    outlier as a first-class § 5.1 reason code, is a taxonomy decision for
    Akadir, not an execution-sequencing item (see HANDOFF).
    """
    # Lazy import — keeps this module free of any `labeling_app` load-order
    # coupling (mirrors feature_compute._measurements_module). By the time
    # aggregate_features runs, feature_compute has pinned the repo root.
    from labeling_app.qa_thresholds import get as _qa_get

    z_max = _qa_get("frame_rf_consistency__modified_z_max").value
    min_n = _qa_get("frame_rf_consistency__min_frames").value

    measured = (
        [(f.get("frame_base"), float(f["rf_thickness_mm"]))
         for f in frame_dicts if f.get("rf_thickness_mm") is not None]
        if applies else []
    )
    report: dict = {
        "measurement": "rf_thickness_mm",
        "active": False,
        "reason_inert": None,
        "n_examined": len(measured),
        "median_mm": None,
        "mad_mm": None,
        "range_mm": None,
        "modified_z_max": z_max,
        "scores": {},
        "excluded": [],
        "trimmed_median_mm": None,
    }
    if not applies:
        # The N5 study-bootstrap pool is not a sweep — the gate is out of scope.
        report["reason_inert"] = "bootstrap_pool"
        return report
    if len(measured) < min_n:
        report["reason_inert"] = "too_few_frames"
        return report
    xs = [x for _, x in measured]
    med = median(xs)
    mad = median([abs(x - med) for x in xs])
    report["median_mm"] = round(med, 3)
    report["mad_mm"] = round(mad, 3)
    report["range_mm"] = round(max(xs) - min(xs), 3)
    if mad == 0:
        # No spread to measure an outlier against (≥ half the frames identical)
        # — a robust z-score is undefined, so the gate stays inert (§ 14 Step 4).
        report["reason_inert"] = "zero_mad"
        return report
    report["active"] = True
    kept: list[float] = []
    for fb, x in measured:
        mod_z = _MAD_TO_SIGMA * (x - med) / mad
        report["scores"][fb] = round(mod_z, 3)
        if abs(mod_z) > z_max:
            report["excluded"].append(fb)
        else:
            kept.append(x)
    report["trimmed_median_mm"] = round(median(kept), 3) if kept else None
    return report


def aggregate_features(
    frame_dicts: list[dict],
    *,
    anchor_frame_base: Optional[str] = None,
    force_all_median: bool = False,
) -> dict:
    """Aggregate per-frame feature dicts into a per-study row.

    Returns a dict containing:
      - {feature}_repr    — anchor-frame value if known, else first non-null
      - {feature}_agg_median — multi-frame median (over non-null values)
      - {feature}          — the *primary* per its policy family
      - qmt_provenance     — 'standard' | 'proxy' | None: the tier the qmt_mm /
        star_ratio primaries were drawn from (None = no femur-valid frame)
      - qmt_n_tier1/2/3    — frame counts per QMT tier

    `force_all_median` (N5 — the anchorless pooled-median policy): when True,
    EVERY feature's primary is the multi-frame median (`_agg_median`),
    regardless of the geometry/intensity family split — *except* qmt_mm /
    star_ratio, which always use the provenance 3-tier gate. This is what the
    study bootstrap needs — pooling analysis frames across distinct finalized
    runs has no single meaningful anchor, so every feature is re-aggregated by
    median. The default (`False`) keeps the per-family anchor_primary /
    agg_primary policies, so the per-run UI path is unchanged.

    Caller is responsible for filtering by `use_for_analysis` upstream.
    """
    if not frame_dicts:
        return {}

    # Identify anchor row.
    anchor_row = None
    if anchor_frame_base is not None:
        for f in frame_dicts:
            if f.get("frame_base") == anchor_frame_base:
                anchor_row = f
                break
    if anchor_row is None:
        # Fall back to is_anchor=True flag or first row.
        for f in frame_dicts:
            if f.get("is_anchor"):
                anchor_row = f
                break
        if anchor_row is None:
            anchor_row = frame_dicts[0]

    out: dict = {
        "_n_frames_in_aggregate": len(frame_dicts),
        "_anchor_frame_base": anchor_row.get("frame_base"),
        "_force_all_median": force_all_median,
        "_policies": {},
        "_qa_tier": {},     # per-measurement image-quality tier the pool came from
        "_rf_consistency": {},  # § 14 Step-4 rf-thickness sweep-outlier gate report
    }

    feature_names = _GEOMETRY_FEATURES | _INTENSITY_FEATURES | _MEDIAN_PRIMARY_FEATURES
    anchor_base = anchor_row.get("frame_base")
    for fname in feature_names:
        vals = [f.get(fname) for f in frame_dicts]
        repr_val = anchor_row.get(fname)
        agg_med = _nullsafe_median(vals)
        out[f"{fname}_repr"] = repr_val          # raw support col — anchor passthrough
        out[f"{fname}_agg_median"] = agg_med     # raw support col — all-frame median

        # Image-quality tier gate (§ 6.4 / § 7.3): pool only the QA-eligible
        # frames for this measurement. `_aggregate_with_image_quality` is inert
        # — selects every frame — on a run whose frames carry no QA grade, so a
        # pre-QA / legacy run is unchanged.
        selected, qa_tier = _aggregate_with_image_quality(frame_dicts, fname)
        out["_qa_tier"][fname] = qa_tier

        # frame_rf_consistency gate (§ 14 Step 4 — Build-Step 2 Phase 3 item
        # 9a). For `rf_thickness_mm` only: drop the robust rf-thickness sweep
        # outliers from the pool, *within* the QA-eligible `selected` set so
        # the reference median/MAD are not skewed by a QA-`invalid` frame's
        # thickness. It is a *per-sweep* guard — `applies=not force_all_median`
        # scopes it out of the N5 study-bootstrap pool, which is a union across
        # distinct runs (not a sweep, and may carry duplicate frame_base) whose
        # pooled median is already outlier-robust. The report is recorded
        # either way — inert states included — and the narrowed `selected`
        # flows through the unchanged anchor_primary / re-pool logic below.
        if fname == "rf_thickness_mm":
            rf_consistency = _rf_consistency_report(
                selected, applies=not force_all_median)
            out["_rf_consistency"] = rf_consistency
            if rf_consistency["excluded"]:
                dropped = set(rf_consistency["excluded"])
                selected = [f for f in selected
                            if f.get("frame_base") not in dropped]

        sel_med = _nullsafe_median(f.get(fname) for f in selected)
        anchor_selected = any(f.get("frame_base") == anchor_base for f in selected)

        if not selected:
            # Every feeding frame is QA-`invalid` for this measurement — no
            # admissible pool (§ 6.4: an invalid measurement is never pooled).
            primary = None
            policy = "image_quality_all_invalid"
        elif force_all_median:
            # N5 anchorless pooled-median — every feature uses the median.
            primary = sel_med
            policy = "force_all_median"
        elif fname in _INTENSITY_FEATURES or fname in _MEDIAN_PRIMARY_FEATURES:
            # Median of the QA-eligible per-frame values. For the fat RATIO this
            # is the per-valid-frame ratio then median (Akadir 2026-06-04).
            primary = sel_med
            policy = "agg_primary_with_anchor_passthrough"
        else:
            # GEOMETRY — anchor_primary: the anchor's value when the anchor is
            # QA-eligible, else the eligible-subset median (the QA re-pool).
            primary = repr_val if (anchor_selected and repr_val is not None) else sel_med
            policy = "anchor_primary"
        out[fname] = primary
        out["_policies"][fname] = policy

    # QMT + STAR — provenance-gated 3-tier policy. Overrides both anchor_primary
    # and force_all_median; STAR rides on QMT's tier selection.
    qmt_block = _aggregate_qmt(frame_dicts, anchor_row)
    out["_policies"].update(qmt_block.pop("_policies"))
    out["_qa_tier"].update(qmt_block.pop("_qa_tier"))
    out.update(qmt_block)

    # Single-frame collapses both policies to the anchor passthrough — surface
    # that for UI labelling.
    if len(frame_dicts) == 1:
        out["_single_frame_collapse"] = True
    else:
        out["_single_frame_collapse"] = False

    return out

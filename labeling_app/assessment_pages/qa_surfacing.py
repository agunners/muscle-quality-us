"""RF image-quality QA — operator read-surface logic (Build-Step 2 item 10a).

Pure presentation logic for the QA layer's Tier-0 / Tier-1 / Tier-3 read
surfaces (`docs/qa_ui_surfacing_plan.md` § C / § D). Every function here is
pure — it maps a stored QA record / run aggregate to display strings or row
structures, with **no Streamlit import** — so the surfacing decisions are
unit-testable and the Phase-5 render (`p05_assessment_view.py`) stays thin.

The three surfaces this module feeds (the 10a half of the 10a/10b split):

  * Tier 0 — `qa_badge` + `aria_label`: the verdict glyph, on each headline
    card. The badge renders the *deployed* tier (§ 6.3 over the calibrated
    flags); a "+N pending" suffix carries the H1 signal — detectors that fired
    but, un-calibrated, do not drive the verdict (Build-Step 2 Finding B).
  * Tier 1 — `run_what_changed`: every aggregation decision that altered a
    number or dropped a frame, one row each ("X was done, because Y, evidence
    Z" — the § 8.0 explainability primitive).
  * Tier 3 — `frame_qa_summary`: the per-frame raw-record view.

Plus `cohort_caveat` — the § 8.2 caveat line for the cohort-placement panel.

§ 8.0 accessibility: a tier is never carried by colour alone — every badge has
a colour AND a glyph AND a word, and `aria_label` is the screen-reader text.
"""
from __future__ import annotations

from typing import Optional


# tier -> (Streamlit colour, glyph, word). The four `ImageQualityFeatures`
# aggregate-tier values plus `none` — the `_aggregate_with_image_quality`
# outcome when every feeding frame was QA-`invalid` for a measurement, so it
# could not be pooled at all. Glyphs follow the § 8.3.2 drilldown mockup
# (✓ / ⚠ / ✗); `○` is the hollow un-graded / no-pool state.
_TIER_STYLE: dict[str, tuple[str, str, str]] = {
    "valid":      ("green",  "✓", "valid"),
    "borderline": ("orange", "⚠", "borderline"),
    "invalid":    ("red",    "✗", "invalid"),
    "ungraded":   ("gray",   "○", "not graded"),
    "none":       ("red",    "○", "no usable frame"),
}

# The measurements p05 renders a card for — headline 4 then secondary 3, card
# order. All are direct § 6.2 gating-table keys, so a per-measurement tier is a
# plain `image_quality_tier_per_measurement[m]` lookup, no § 6.2 aliasing.
_DISPLAY_MEASUREMENTS = (
    "star_ratio", "full_thickness_q50_mm", "int_cv_within", "rf_thickness_mm",
    "qmt_mm", "visible_rf_area_mm2",
)

# Short, operator-facing measurement labels. Kept here (not imported from p05)
# so this module is standalone-testable. "Fascicular contrast" is the ratified
# label for int_cv_within — do not relitigate.
_LABELS = {
    "star_ratio": "STAR", "full_thickness_q50_mm": "RF thickness — median",
    "int_cv_within": "Fascicular contrast",
    "rf_thickness_mm": "RF thickness — at the landmark cross-section",
    "qmt_mm": "QMT",
    "visible_rf_area_mm2": "Visible RF area",
}


def _label(measurement: str) -> str:
    return _LABELS.get(measurement, measurement)


# Detector-code translation table — every IMAGE_QUALITY_FLAG_PRIORITY code
# rendered as one plain operator sentence. Operator-facing surfaces call
# `flag_prose(code)` — never embed a raw code or "_"-joined identifier in
# any string the operator sees. (The 2026-05-22 operator review surfaced raw
# codes like `edge_clip_lateral` leaking through; subagent B called it a
# category failure, not isolated typos — every operator-facing surface must
# go through this table.) Unknown codes fall through to a space-stripped
# form rather than a raw identifier.
_FLAG_PROSE: dict[str, str] = {
    "wrong_site":                "the frame has too little RF visible to assess",
    "segmenter_mislabel_wrong_class": "the muscle labels look wrong on this frame",
    "femur_out_of_frame":        "the thigh bone is not visible in this frame",
    "femur_off_central_zone":    "the thigh bone is off to one side, not centred",
    "edge_clip_lateral":         "the muscle runs off the side of the image",
    "edge_clip_vertical":        "the muscle runs off the top or bottom of the image",
    "saturation_high_global":    "bright washed-out areas — the image may be over-gained",
    "saturation_high_regional":  "a bright washed-out patch — likely a strong reflection",
    "dark_clip_high":            "large dark areas — the image may be under-gained",
    "tgc_banding":               "horizontal brightness bands — gain mis-set across depth",
    "motion_blur":               "the image is blurred, likely from probe or patient movement",
    "air_gap_reverberation":     "poor probe contact — an air gap is likely",
    "near_field_clutter":        "high-frequency clutter near the surface — probe artefact",
    "vessel_shadow_in_roi":      "a blood vessel is casting a shadow over the muscle",
    "posterior_acoustic_enhancement": "a bright region behind a fluid pocket — may bias intensity",
    "subq_fat_compressed":       "the probe may be pressing too hard — the fat layer looks compressed",
    "oblique_plane":             "the probe is angled off-perpendicular",
    "anisotropy_dropout_superficial": "a darkening of the superficial muscle from the probe angle",
    "anisotropy_dropout_deep":   "a darkening of the deep muscle from the probe angle",
    "deep_attenuation_high":     "the deep part of the image is too dark to read",
    "segmentation_low_confidence": "the app was unsure where the muscle edges are",
    "probe_orientation_flipped": "the image looks flipped — probe orientation may be wrong",
}


def flag_prose(code: str) -> str:
    """Translate a detector code (e.g. `edge_clip_lateral`) to a plain operator
    sentence (e.g. "the muscle runs off the side of the image"). Unknown codes
    fall through to a space-stripped readable form. Operator-facing surfaces
    use this; raw codes never reach the screen."""
    if code in _FLAG_PROSE:
        return _FLAG_PROSE[code]
    return (code or "").replace("_", " ")


# Drop-reason grouping for the p04 capture (Akadir 2026-06-01 — "group under the
# taxonomy's own headers"). The 15 codes that HAVE a live detector, grouped for
# scanability; rendered with flag_prose labels (never raw codes).
DROP_REASON_GROUPS = (
    ("Wrong muscle / no femur",
     ("wrong_site", "femur_out_of_frame", "femur_off_central_zone")),
    ("Framing / edges",
     ("edge_clip_lateral", "edge_clip_vertical")),
    ("Brightness / gain",
     ("saturation_high_global", "saturation_high_regional",
      "dark_clip_high", "tgc_banding")),
    ("Sharpness",
     ("motion_blur",)),
    ("Coupling / artifact",
     ("air_gap_reverberation", "near_field_clutter", "vessel_shadow_in_roi")),
    ("Tissue / model",
     ("subq_fat_compressed", "segmentation_low_confidence")),
)

# The 7 taxonomy codes with NO automatic detector — operator judgment only.
# Tucked behind an expander so the common (detector-backed) reasons stay
# scannable. (A drift-guard test asserts GROUPS + this == the full taxonomy.)
OPERATOR_ONLY_DROP_REASONS = (
    "segmenter_mislabel_wrong_class",
    "posterior_acoustic_enhancement",
    "oblique_plane",
    "anisotropy_dropout_superficial",
    "anisotropy_dropout_deep",
    "deep_attenuation_high",
    "probe_orientation_flipped",
)


def _short(frame_base: Optional[str]) -> str:
    """Short frame id — HHMMSS.XXX (mirror of `p03_frame_picker._short`).
    The sub-second .XXX disambiguates sweep-sibling frames (Akadir 2026-05-25
    § 4a)."""
    if not frame_base:
        return "?"
    time_part = frame_base.split("_")[-1]
    if len(time_part) >= 9:
        return f"{time_part[:6]}.{time_part[6:9]}"
    return time_part[:6]


def _style(tier: str) -> tuple[str, str, str]:
    """Resolve a tier to its (colour, glyph, word). An unknown tier degrades to
    the gray `not graded` style rather than raising — a read surface must never
    crash the assessment screen on an unexpected value."""
    return _TIER_STYLE.get(tier, _TIER_STYLE["ungraded"])


# ---------------------------------------------------------------------------
# Tier 0 — the verdict badge
# ---------------------------------------------------------------------------

def qa_badge(tier: str, *, dominant_reason: Optional[str] = None,
             n_pending: int = 0, prefix: str = "QA") -> str:
    """The § 8.3.1 verdict badge, as a Streamlit-markdown string.

    `tier` — one of `_TIER_STYLE`'s keys (the `ImageQualityFeatures`
    aggregate-tier values, plus the `none` no-pool outcome).
    `dominant_reason` — the highest-priority *calibrated* flag driving the
    verdict; appended when present (the verdict-reason half of the § 8.0
    explainability triple). A pending flag is never the dominant reason — it
    did not drive the verdict; it is carried by `n_pending` instead.
    `n_pending` — the count of `image_quality_pending_calibration_flags`; a
    non-zero count appends a gray "+N pending" note, the H1 deployed-vs-raw
    signal that detectors fired but are not (yet) counted.

    Colour AND glyph AND word, always — never colour alone (§ 8.0 principle 2).
    """
    colour, glyph, word = _style(tier)
    text = f"{glyph} {prefix}: {word}"
    if dominant_reason:
        text += f" · {flag_prose(dominant_reason)}"
    badge = f":{colour}[{text}]"
    if n_pending > 0:
        badge += f"  :gray[+{n_pending} pending]"
    return badge


def aria_label(tier: str, *, dominant_reason: Optional[str] = None,
               n_pending: int = 0) -> str:
    """The screen-reader text alternative for a `qa_badge` (§ 8.0 principle 2 /
    § 8.3.9) — the same information in plain words, no glyph, no colour."""
    _colour, _glyph, word = _style(tier)
    parts = [f"image quality: {word}"]
    if dominant_reason:
        parts.append(f"reason — {flag_prose(dominant_reason)}")
    if n_pending > 0:
        parts.append(f"{n_pending} additional check(s) still being tuned, not counted")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# Tier 3 — the per-frame summary
# ---------------------------------------------------------------------------

def frame_qa_summary(image_quality: Optional[dict]) -> dict:
    """A structured per-frame QA summary from a stored `image_quality` record
    (the dict form of `ImageQualityFeatures`, joined onto a frame). Pure.

    Returns:
      tier             — `image_quality_aggregate_tier`, the deployed verdict
      dominant_reason  — the highest-priority calibrated flag (the one that
                         actually drove `tier`), else None
      calibrated_flags — fired flags whose detector is calibrated: they drove
                         the verdict
      pending_flags    — `image_quality_pending_calibration_flags` (H1): fired
                         and recorded, but excluded from the verdict
      per_measurement  — `image_quality_tier_per_measurement`
      contract_version — the contract the grade was produced under
      qa_error         — the § 8.6 detector-error reason, when present

    A missing / empty `image_quality` (a pre-QA frame) returns the honest
    `ungraded` summary — never silently a `valid` one (§ 8.6).
    """
    iq = image_quality or {}
    validity = tuple(iq.get("image_quality_validity_flags") or ())
    pending = tuple(iq.get("image_quality_pending_calibration_flags") or ())
    pending_set = set(pending)
    # The calibrated subset = the fired flags that are NOT pending. `validity`
    # is § 5.1-priority-ordered, so the filtered tuple keeps that order and its
    # first element is the dominant (highest-priority) calibrated reason.
    calibrated = tuple(f for f in validity if f not in pending_set)
    md = iq.get("image_quality_detector_metadata") or {}
    return {
        "tier": iq.get("image_quality_aggregate_tier", "ungraded"),
        "dominant_reason": calibrated[0] if calibrated else None,
        "calibrated_flags": calibrated,
        "pending_flags": pending,
        "per_measurement": dict(iq.get("image_quality_tier_per_measurement") or {}),
        "contract_version": iq.get("image_quality_contract_version"),
        "qa_error": md.get("_qa_error"),
    }


# ---------------------------------------------------------------------------
# Per-measurement attribution (Akadir 2026-05-24 v2 reframe — § 5b)
# ---------------------------------------------------------------------------
# v1 showed one "dominant flag" per frame paired with the union of all
# measurements pooled out. The wording chained as "muscle off side → didn't
# count for STAR/QMT/RF area/RF thickness", reading as if off-side caused all
# four. That was structurally misleading: edge_clip_lateral only invalidates
# visible RF area (§ 6.2 / C11). The fix is per-measurement attribution —
# one row per measurement, naming the flag(s) that actually drove ITS tier.

def per_measurement_attribution(
    image_quality: Optional[dict],
) -> list[dict]:
    """Per-measurement attribution for a frame's QA grade. Each measurement in
    `GATING_MEASUREMENTS` gets one row naming the calibrated flag(s) that
    drove its tier — not the union. Pure.

    Returns one row per gating measurement, in `GATING_MEASUREMENTS` order:

        {
          "measurement":        canonical key (e.g. "visible_rf_area_mm2"),
          "measurement_label":  display label ("Visible RF area"),
          "tier":               "valid" | "borderline" | "invalid" | "ungraded",
          "drivers":            list of {flag, tier_contribution, prose}
        }

    `drivers` lists only the CALIBRATED flags that taint THIS measurement (via
    `_GATING_TABLE`). Pending flags are excluded — they do not drive the
    deployed tier (Finding B). A `valid` measurement carries an empty list.

    A missing / empty `image_quality` (pre-QA frame) returns one row per
    measurement with tier `ungraded` and empty drivers — never silently
    `valid`.
    """
    from labeling_app.image_quality import _GATING_TABLE  # local-import to avoid cycle

    iq = image_quality or {}
    validity = tuple(iq.get("image_quality_validity_flags") or ())
    pending = set(iq.get("image_quality_pending_calibration_flags") or ())
    tiers = iq.get("image_quality_tier_per_measurement") or {}
    # The calibrated fired flags — § 5.1-priority-ordered.
    calibrated = tuple(f for f in validity if f not in pending)

    rows: list[dict] = []
    for m in _DISPLAY_MEASUREMENTS:
        tier = tiers.get(m, "ungraded" if not iq else "valid")
        drivers: list[dict] = []
        for f in calibrated:
            row = _GATING_TABLE.get(f, {})
            if m in row:
                drivers.append({
                    "flag": f,
                    "tier_contribution": row[m],     # "invalid" or "borderline"
                    "prose": flag_prose(f),
                })
        rows.append({
            "measurement": m,
            "measurement_label": _label(m),
            "tier": tier,
            "drivers": drivers,
        })
    return rows


# ---------------------------------------------------------------------------
# Tier 1 — "what the QA layer changed"
# ---------------------------------------------------------------------------

def _analysis_frames(frame_features: Optional[list[dict]]) -> list[dict]:
    """The frames that fed the aggregate — gated `use_for_analysis` AND
    `usable` (`run_pipeline` aggregates exactly this subset)."""
    out: list[dict] = []
    for ff in frame_features or []:
        g = ff.get("gates") or {}
        if g.get("use_for_analysis") and g.get("usable"):
            out.append(ff)
    return out


def _bucket(frame: dict, measurement: str) -> str:
    """A frame's per-measurement QA tier — its pooling bucket. Mirrors
    `_aggregate_with_image_quality`: a value outside valid/borderline/invalid
    (a pre-QA frame, an `ungraded` record) buckets as `ungraded`."""
    iq = frame.get("image_quality") or {}
    tiers = iq.get("image_quality_tier_per_measurement") or {}
    tier = tiers.get(measurement)
    return tier if tier in ("valid", "borderline", "invalid") else "ungraded"


def _dropped_from_pool(measurement: str, pool_tier: str,
                       analysis: list[dict]) -> list[tuple[str, str]]:
    """Analysis frames that did NOT feed `measurement`'s pool — `(frame_base,
    tier)` each. A frame fed the pool iff its per-measurement tier equals the
    tier the pool was drawn from (`_aggregate_with_image_quality`'s bucket
    rule); every other frame was dropped."""
    return [(ff.get("frame_base"), _bucket(ff, measurement))
            for ff in analysis if _bucket(ff, measurement) != pool_tier]


def _frames_evidence(dropped: list[tuple[str, str]]) -> str:
    return ", ".join(f"{_short(fb)} ({tier})" for fb, tier in dropped)


def _measurement_row(measurement: str, pool_tier: Optional[str],
                     analysis: list[dict], *,
                     label_override: Optional[str] = None) -> Optional[dict]:
    """A "what changed" row for one measurement's QA-tier pooling (item 8), or
    None when it pooled cleanly (a full set of QA-`valid` frames) — a clean
    pool belongs in the Tier-3 raw record, not the Tier-1 drilldown.

    `label_override` lets the caller display a joint label such as "QMT and
    STAR" when two measurements move together (run_what_changed handles the
    grouping)."""
    if pool_tier in (None, "ungraded"):
        return None                       # pre-QA run / no per-measurement verdict
    dropped = _dropped_from_pool(measurement, pool_tier, analysis)
    label = label_override or _label(measurement)
    label_lower = label.lower()
    if pool_tier == "valid":
        if not dropped:
            return None                   # clean valid pool — nothing changed
        return {
            "kind": "pool", "measurement": measurement, "severity": "drop",
            "title": f"{label} — {len(dropped)} frame(s) did not count toward this number",
            "detail": (f"The app set those frames aside because of image-quality "
                       f"problems on them; the {label_lower} value uses only "
                       f"the frames the app trusts here."),
            "evidence": _frames_evidence(dropped),
        }
    if pool_tier == "borderline":
        in_pool = len(analysis) - len(dropped)
        return {
            "kind": "pool", "measurement": measurement, "severity": "caveat",
            "title": f"{label} — uses lower-quality frames",
            "detail": (f"None of your frames met the app's top quality bar for "
                       f"{label_lower}, so the app used the frames it could "
                       f"(the next-best tier). Read this number a little softer "
                       f"than usual."),
            "evidence": (f"{in_pool} lower-quality frame(s) used"
                         + (f"; set aside {_frames_evidence(dropped)}"
                            if dropped else "")),
        }
    if pool_tier == "none":
        return {
            "kind": "pool", "measurement": measurement, "severity": "drop",
            "title": f"{label} — no usable frame",
            "detail": (f"Every frame had an image-quality problem that "
                       f"prevented {label_lower} from being measured, so no "
                       f"value is shown for it."),
            "evidence": f"{len(analysis)} frame(s), all with image problems",
        }
    return None


def run_what_changed(aggregate: Optional[dict],
                     frame_features: Optional[list[dict]]) -> list[dict]:
    """The Tier-1 "what changed" rows (`qa_ui_surfacing_plan.md` § C) — every
    QA / aggregation decision that altered a number or dropped a frame, each a
    row of the § 8.0 "X done, because Y, evidence Z" shape:

        {kind, measurement, title, detail, evidence, severity}

    `severity` is `caveat` (a number is softer than it looks) or `drop` (a
    frame, or a whole measurement, left the pool) — for the render's colour.
    A decision that changed nothing (a clean valid pool, an inert consistency
    gate) produces no row: it belongs in the Tier-3 raw record, and surfacing
    it here is the overwhelm § C warns against. An empty list means the run
    pooled cleanly — nothing to drill into.

    Sources, all already recorded on the aggregate (Build-Step 2 items 8 + 9):
    `_rf_consistency` (the § 14 Step-4 sweep-outlier report), `_qa_tier` (the
    per-measurement pool tier), `qmt_provenance` (the QMT 3-tier gate).
    """
    rows: list[dict] = []
    agg = aggregate or {}
    analysis = _analysis_frames(frame_features)

    # (1) frame_rf_consistency (item 9) — a sweep outlier dropped from the
    # rf_thickness_mm pool. The report is self-describing; surface it only
    # when it actually excluded a frame (an inert / clean gate changed nothing).
    rc = agg.get("_rf_consistency") or {}
    excluded = list(rc.get("excluded") or [])
    if excluded:
        scores = rc.get("scores") or {}
        zs = ", ".join(f"{_short(fb)} Z={scores.get(fb)}" for fb in excluded)
        rows.append({
            "kind": "rf_consistency", "measurement": "rf_thickness_mm",
            "severity": "drop",
            "title": f"RF thickness — {len(excluded)} outlier frame(s) set aside",
            "detail": ("One or more frames had an RF-thickness value far from "
                       "the rest of the sweep — usually a mis-segmentation or "
                       "an off-axis slice. They were set aside; the RF "
                       "thickness above uses the remaining frames."),
            "evidence": (f"sweep median {rc.get('median_mm')} mm; "
                         f"set aside {zs}; clean median "
                         f"{rc.get('trimmed_median_mm')} mm"),
        })

    # (2) Per-measurement QA-tier pooling (item 8) — one row per display
    # measurement whose pool was not a clean set of QA-`valid` frames. QMT
    # and STAR move together (STAR = QMT / BMI), so when their pool tiers
    # match we emit one row labelled "QMT and STAR" rather than two
    # duplicate rows (subagent B's redesign, addressing point 4a).
    qa_tier = agg.get("_qa_tier") or {}
    processed: set[str] = set()
    for measurement in _DISPLAY_MEASUREMENTS:
        if measurement in processed:
            continue
        pool_tier = qa_tier.get(measurement)
        label = _label(measurement)
        if measurement in ("qmt_mm", "star_ratio"):
            partner = "star_ratio" if measurement == "qmt_mm" else "qmt_mm"
            if qa_tier.get(partner) == pool_tier:
                label = "QMT and STAR"
                processed.add(partner)
        row = _measurement_row(measurement, pool_tier, analysis,
                               label_override=label)
        processed.add(measurement)
        if row is not None:
            rows.append(row)

    # (3) QMT provenance (item 8) — QMT/STAR drawn from a non-standard tier.
    prov = agg.get("qmt_provenance")
    if prov == "proxy":
        rows.append({
            "kind": "qmt_provenance", "measurement": "qmt_mm",
            "severity": "caveat",
            "title": "QMT and STAR — measured without the thigh-bone anchor",
            "detail": ("No frame had a usable thigh bone, so the app could "
                       "only estimate QMT (and STAR, which uses QMT) from a "
                       "fallback method. The number is roughly half what a "
                       "proper measurement would give, and cannot be compared "
                       "to the reference cohort."),
            "evidence": (f"{agg.get('qmt_n_tier1', 0)} frame(s) with the "
                         f"thigh bone properly anchored, "
                         f"{agg.get('qmt_n_tier2', 0)} fallback, "
                         f"{agg.get('qmt_n_tier3', 0)} no thigh bone"),
        })
    elif prov is None and ("qmt_mm" in qa_tier):
        # The QMT block ran but no femur frame existed in any tier.
        rows.append({
            "kind": "qmt_provenance", "measurement": "qmt_mm",
            "severity": "drop",
            "title": "QMT and STAR — not measurable",
            "detail": ("No frame had the thigh bone visible, so QMT could "
                       "not be measured. STAR (QMT divided by BMI) inherits "
                       "that — not measurable either."),
            "evidence": (f"{agg.get('qmt_n_tier1', 0)} frame(s) with the "
                         f"thigh bone properly anchored, "
                         f"{agg.get('qmt_n_tier2', 0)} fallback, "
                         f"{agg.get('qmt_n_tier3', 0)} no thigh bone"),
        })

    return rows


# ---------------------------------------------------------------------------
# The merged Image-quality panel data model (redesign chunk 2)
# ---------------------------------------------------------------------------

def frames_with_changes(aggregate: Optional[dict],
                        frame_features: Optional[list[dict]]) -> dict:
    """The merged Image-quality panel data — one entry per analysis frame
    with changes pinned to it, plus run-level callouts that do not pin to a
    single frame.

    (Build-Step 2 redesign chunk 2 — subagent B's merge of the "what
    changed" and "per-frame detail" widgets into one panel organised by
    frame. The operator complaint that motivated this: two widgets where
    the operator's mental model has one question; cross-referencing
    forced; thumbnails missing; raw codes leaked. This data shape lets the
    Streamlit render show one row per frame with the per-frame verdict and
    any result-changes pinned to it, plus a small run-level section above
    for things that apply to the whole run.)

    Returns:
      n_total       — analysis frame count
      n_set_aside   — frames that did NOT contribute to at least one display
                       measurement
      frames        — list of per-frame dicts (see below)
      run_level     — list of run-wide callout dicts (see below)

    Each per-frame dict:
      frame_base, is_anchor, tier (the deployed per-frame aggregate verdict)
      dominant_reason_prose      — operator sentence; None on a clean frame
      calibrated_flags_prose     — list of operator sentences
      pending_flags_prose        — list of operator sentences (H1 pending set)
      affected_measurements      — labels of measurements this frame did
                                    NOT contribute to (sweep outlier or
                                    excluded from a pool); STAR + QMT
                                    folded together as "STAR and QMT" when
                                    both are affected
      is_outlier                 — True iff the rf_consistency gate excluded
                                    this frame from the RF-thickness pool
      qa_error                   — § 8.6 detector-error reason when present

    Each run_level dict mirrors the `run_what_changed` row shape
    (kind, severity, title, detail) — only run-wide kinds are kept here:
    `qmt_provenance`, and pool rows where the pool tier is `borderline`/
    `none` (those are run-wide statements; valid-pool-with-drops rows pin
    to the per-frame `affected_measurements`, not here).
    """
    agg = aggregate or {}
    analysis = _analysis_frames(frame_features)
    qa_tier = agg.get("_qa_tier") or {}
    rc = agg.get("_rf_consistency") or {}
    rc_excluded = set(rc.get("excluded") or [])

    # ---- per-frame entries ----
    n_set_aside = 0
    frames_out: list[dict] = []
    for ff in analysis:
        fb = ff.get("frame_base")
        iq = ff.get("image_quality") or {}
        summary = frame_qa_summary(iq)
        # Which display measurements did this frame NOT contribute to?
        # A frame is in the pool for M iff its bucket for M == the pool's
        # tier (`_aggregate_with_image_quality`'s bucket rule).
        affected_others: list[str] = []
        affected_qmt = affected_star = False
        for m in _DISPLAY_MEASUREMENTS:
            pool_tier = qa_tier.get(m)
            if pool_tier in (None, "ungraded"):
                continue                       # gate inert — frame "contributed"
            if _bucket(ff, m) != pool_tier:
                if m == "qmt_mm":
                    affected_qmt = True
                elif m == "star_ratio":
                    affected_star = True
                else:
                    affected_others.append(_label(m))
        is_outlier = fb in rc_excluded
        if is_outlier:                          # sweep outlier → out of RF-thickness pool
            rf_label = _label("rf_thickness_mm")
            if rf_label not in affected_others:
                affected_others.append(rf_label)
        affected: list[str] = []
        if affected_qmt and affected_star:
            affected.append("STAR and QMT")
        elif affected_qmt:
            affected.append(_label("qmt_mm"))
        elif affected_star:
            affected.append(_label("star_ratio"))
        affected.extend(affected_others)
        if affected:
            n_set_aside += 1
        dom_raw = summary["dominant_reason"]
        frames_out.append({
            "frame_base": fb,
            "is_anchor": bool(ff.get("is_anchor")),
            "tier": summary["tier"],
            "dominant_reason_prose": flag_prose(dom_raw) if dom_raw else None,
            "calibrated_flags_prose": [flag_prose(f) for f in summary["calibrated_flags"]],
            "pending_flags_prose": [flag_prose(f) for f in summary["pending_flags"]],
            "affected_measurements": affected,
            "is_outlier": is_outlier,
            "qa_error": summary["qa_error"],
        })

    # ---- run-level callouts ----
    # Reuse `run_what_changed` and keep only the rows that apply run-wide
    # (the per-frame drops are already captured above via
    # `affected_measurements`).
    all_rows = run_what_changed(aggregate, frame_features)
    run_level = [
        r for r in all_rows
        if r["kind"] == "qmt_provenance"
        or (r["kind"] == "pool"
            and qa_tier.get(r.get("measurement")) in ("borderline", "none"))
    ]

    return {
        "n_total": len(analysis),
        "n_set_aside": n_set_aside,
        "frames": frames_out,
        "run_level": run_level,
    }


def pooled_disclosure_counts(aggregate: Optional[dict],
                             frame_features: Optional[list[dict]]
                             ) -> tuple[int, int, int]:
    """The compact `(N, M, K)` for the p05 pooled-disclosure line (Slice B).

    M = analysis frames that fed the run (`use_for_analysis AND usable`);
    K = frames set aside from at least one displayed measurement
        (`frames_with_changes.n_set_aside`);
    N = M - K = frames that fed EVERY displayed measurement — the
        conservative, never-overstates count, so the line "N of M ... K set
        aside" always reconciles (N + K = M).

    The denominator convention (this universally-clean floor vs a
    per-measurement min-pool) is the §6.4 open call; this ships the floor.
    Cheap pure function — this is the ONLY use of `frames_with_changes` the
    disclosure makes (a count source, never the render — its per-frame render
    was deliberately stripped from p05).
    """
    fwc = frames_with_changes(aggregate, frame_features)
    m = int(fwc["n_total"])
    k = int(fwc["n_set_aside"])
    return m - k, m, k


# The detectors we are willing to SURFACE for operator reaction at p04 (the
# "what the app checked / is this right?" capture, Akadir 2026-05-31): the
# calibrated verdict-drivers (femur x2, edge_clip_vertical, wrong_site) + the
# allow-set pending steer (dark_clip_high). edge_clip_lateral (reserve, taints
# nothing) and the rest of the pending/NaN-belted set stay hidden — surfacing
# them would be the badge-fatigue the friction budget forbids.
_SURFACEABLE_CHECKS = (
    "wrong_site", "femur_out_of_frame", "femur_off_central_zone",
    "edge_clip_vertical", "dark_clip_high",
)


def frame_checks(image_quality: Optional[dict]) -> dict:
    """Per-frame check inventory for the p04 'what the app checked' surface.

    Returns `{fired: [{detector, prose}], passed: [prose, ...]}` over the
    surfaceable detector set only. `fired` = the detector raised on this frame;
    `passed` = it ran and stayed silent. Quiet by construction: a frame where
    nothing surfaceable fired has `fired == []`, so the caller renders nothing
    ambient. `passed` exists for the on-demand "we checked these, they passed"
    confidence view (Akadir's (2)) — shown only inside an opened expander.
    """
    iq = image_quality or {}
    validity = set(iq.get("image_quality_validity_flags") or ())
    fired: list[dict] = []
    passed: list[str] = []
    for det in _SURFACEABLE_CHECKS:
        if det in validity:
            fired.append({"detector": det, "prose": flag_prose(det)})
        else:
            passed.append(flag_prose(det))
    return {"fired": fired, "passed": passed}


# Pending detectors allowed to surface at the picker as an INFORMATIONAL
# pick-time steer (never a verdict). dark_clip_high is the one cleanly-firing
# acquisition detector (~18% of frames, vs wrong_site's ~80%), so it steers
# without painting the grid. Akadir 2026-05-31 — the recorded allow-set.
_PICKER_PENDING_ALLOW = ("dark_clip_high",)


def picker_qa_cue(
    image_quality: Optional[dict],
    *,
    suppress_flags: tuple[str, ...] = (),
    allow_pending: tuple[str, ...] = (),
) -> Optional[str]:
    """A small operator cue for a picker thumbnail — one short markdown line
    summarising whether the frame is likely usable for the assessment.

    Returns None when the frame is image-quality-valid (silent — quiet-or-loud
    discipline, consistent with the result-card badge in chunk 3). Returns a
    coloured one-liner — colour + glyph + plain operator sentence — when the
    deployed verdict is borderline or invalid.

    Only the calibrated four detectors drive the cue. The H1 pending set is
    deliberately ignored at the picker because `wrong_site` (pending) fires
    on ~80 % of cohort frames and would paint nearly every thumbnail orange
    — exactly the noise the 2026-05-22 operator review warned against.

    `suppress_flags` — Akadir 2026-05-25 v2 § 1. A tuple of flag codes whose
    prose the caller wants silenced because some OTHER thumbnail surface
    already conveys the same information. Concrete instance: when the picker
    shows the tier-3 ":red[● no femur]" dot, `femur_out_of_frame`'s prose
    ("the thigh bone is not visible in this frame") would double-surface
    the same fact alongside the dot — the call site passes
    `suppress_flags=("femur_out_of_frame",)` when `tier == 3` to silence the
    redundant prose. The cue still fires when a DIFFERENT calibrated flag
    drives the verdict on a tier-3 frame; only the redundant prose is gated.
    """
    from labeling_app.image_quality import _GATING_TABLE
    if not image_quality:
        return None
    validity = image_quality.get("image_quality_validity_flags") or ()
    pending_set = set(
        image_quality.get("image_quality_pending_calibration_flags") or ())
    suppress_set = set(suppress_flags)
    tier = image_quality.get("image_quality_aggregate_tier")

    # Primary — the calibrated-driven VERDICT cue. Only the calibrated,
    # verdict-tainting flags speak, and only when the deployed tier is below
    # valid. Akadir 2026-05-24 audit — reserve-state flags (those whose
    # _GATING_TABLE row is empty, i.e. they taint nothing) MUST NOT be the
    # picker's dominant; `suppress_flags` dedupes a flag the tier dot conveys.
    primary = None
    if tier not in (None, "valid", "ungraded"):
        calibrated = [f for f in validity if f not in pending_set]
        actionable = [f for f in calibrated
                      if _GATING_TABLE.get(f) and f not in suppress_set]
        if actionable:
            dom = actionable[0]                    # § 5.1 priority-ordered
            primary = (f":red[✗ {flag_prose(dom)}]" if tier == "invalid"
                       else f":orange[⚠ {flag_prose(dom)}]")

    # Opt-in pending steer (Akadir 2026-05-31, revised 2026-06-01) — an ALLOWED
    # pending detector that fired, surfaced as an INFORMATIONAL pick-time steer
    # (never a verdict). If the operator OPTED IT IN, it is NOT suppressed by a
    # louder calibrated cue: both surface (the calibrated verdict leads, the
    # opted-in steer follows on its own line). Akadir's rule — "if the user opts
    # in, don't let it be quiet on frames it fires." `allow_pending` is empty by
    # default, so this is a no-op for every existing caller; the picker passes
    # `_PICKER_PENDING_ALLOW` to opt dark_clip_high in.
    pending_cue = None
    if allow_pending:
        allow = set(allow_pending)
        hits = [f for f in validity
                if f in pending_set and f in allow and f not in suppress_set]
        if hits:
            pending_cue = f":orange[⚠ {flag_prose(hits[0])}]"

    parts = [c for c in (primary, pending_cue) if c]
    return "\n\n".join(parts) if parts else None


def cohort_caveat(aggregate: Optional[dict]) -> Optional[str]:
    """The cohort-placement caveat — a one-liner when the run's pooled numbers
    did not all come from clean top-quality frames, so a reader knows a
    percentile sits on a partially-degraded measurement. None when every
    display measurement pooled cleanly."""
    qa_tier = (aggregate or {}).get("_qa_tier") or {}
    softened = [m for m in _DISPLAY_MEASUREMENTS
                if qa_tier.get(m) in ("borderline", "none")]
    if not softened:
        return None
    labels = ", ".join(_label(m) for m in softened)
    return (f"Image-quality note — for {labels}, the value above was not built "
            f"from a clean set of top-quality frames. Read the percentile here "
            f"with that softness in mind.")

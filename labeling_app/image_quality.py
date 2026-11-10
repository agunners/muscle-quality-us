"""RF image-quality QA layer — detector entry point + the gating spine.

RF Image-Quality QA/QC Canonical Plan v0.2 (docs/rf_qa_canonical_plan_v0_1.md).
This module is the QA contract every detector and every consumer is coded
against — Build-Step 2 Phase 1 (build_step_2_plan.md items 2 + 3):

  * `compute_image_quality_flags(img_gray, mask)` — the § 5.5 / § 7.2 detector
    entry point. It runs every registered [Active] detector and returns the
    documented flag tuple + raw detector metadata.
  * `image_quality_tier(measurement, flags)` — the § 6.1 per-measurement
    validity rule, encoding the § 6.2 gating table.
  * `image_quality_aggregate_tier(flags)` — the § 6.3 per-frame aggregate
    badge derived from the per-measurement tiers.
  * `dominant_reason(flags)` — the highest-priority flag, the § 8.0 badge text.

Phase 1 locks these *interfaces*; the detectors themselves are Phase 2 (plan
items 5–6). Until then `_DETECTOR_REGISTRY` is empty, so a Phase-1 call
produces no flags — the contract is what Phase 1 ships.

Naming discipline (plan § 7.2): this module is deliberately NOT placed in, and
does not share a name with, the `rf_quality` package — `rf_quality` is a
measurement / geometry library that does not do quality assessment despite its
name. QA code lives here, in the assessment app.
"""
from __future__ import annotations

import contextvars
import functools
import math
from typing import Callable, Optional

import numpy as np
import scipy.ndimage as ndi

try:  # skimage is optional — the fascia detector degrades to the segmented
    from skimage.filters import frangi as _frangi  # fascia alone when absent.
except Exception:  # pragma: no cover - exercised only where skimage is missing
    _frangi = None

from labeling_app.measurements import (
    IMAGE_QUALITY_FLAG_PRIORITY,
    LABEL_FASCIA,
    LABEL_FAT,
    LABEL_FEMUR,
    LABEL_RF,
    LABEL_SKIN,
    LABEL_VI,
    LABEL_VL,
    LABEL_VM,
    canonical_image_quality_flag_order,
    frame_qmt_validity_flags,
    label_area_px,
    px_to_mm,
)
from labeling_app.qa_thresholds import THRESHOLDS, get as _registry_get

# Verdict literals — the three values image_quality_tier() can return for one
# measurement, and the per-cell verdicts of the § 6.2 table.
VALID = "valid"
BORDERLINE = "borderline"
INVALID = "invalid"

# § 3.2 / § 6.3 — the four headline measurements shown on the operator's result
# card. A failure in one drives the aggregate tier hard (§ 6.3).
HEADLINE_MEASUREMENTS = (
    "star_ratio",
    "full_thickness_q50_mm",
    "int_cv_within",
    "rf_thickness_mm",
)

# § 6.2 — the eight gating-table columns: the seven card measurements plus
# `rf_echo` (which stands in for the int_* family). The aggregate-tier 60%
# rule (§ 6.3) counts `valid` over exactly these eight.
GATING_MEASUREMENTS = (
    "rf_thickness_mm",
    "qmt_mm",
    "star_ratio",
    "full_thickness_q50_mm",
    "visible_rf_area_mm2",
    "int_cv_within",
    "rf_echo",
    "muscle_fat_ratio",
)

# § 3.2 / § 6.2 — measurements that gate identically to a table column.
# `full_area_mm2` gates as `visible_rf_area_mm2` (both are FOV-limited cross-sections);
# the int_* family gates as `rf_echo`. A caller may pass any of these names.
_MEASUREMENT_ALIASES = {
    "full_area_mm2": "visible_rf_area_mm2",
    "int_mean": "rf_echo",
    "int_std": "rf_echo",
    "int_median": "rf_echo",
    "int_entropy": "rf_echo",
}


# ---------------------------------------------------------------------------
# § 5.5 / § 7.2 — the detector entry point
# ---------------------------------------------------------------------------

# The detector registry — (flag_name, detector_fn) per [Active] image+mask
# detector. Each detector_fn has the signature
#   fn(img_gray, mask) -> {'flag': str | None, 'metadata': dict}
# — it returns its flag code when it fires (None when it does not) and always
# returns its raw score(s) under 'metadata' (for the § 8.3.2 UI rationale and
# the § 11.2 calibration). The returned flag MUST be a canonical
# IMAGE_QUALITY_FLAG_PRIORITY code or None — compute_image_quality_flags raises
# on anything else.
#
# Build-Step 2 Phase 2 (build_step_2_plan.md items 5–6) populates this with the
# 14 image+mask detectors: the `.extend(...)` and the registration self-check
# are at the END of this module, after the detector functions are defined.
# Two further [Active] detectors are deliberately NOT in this registry —
# `segmentation_low_confidence` and `subq_fat_compressed` need pipeline context
# (the segmenter's softmax entropy / pixel spacing) the `(img_gray, mask)`
# contract cannot carry. They ship as standalone, fully-tested functions
# (`detect_segmentation_low_confidence` / `detect_subq_fat_compressed`); the
# Phase-3 pipeline wiring — which has that context — runs and merges them. See
# the "Build-Step 2 Phase 2 — the detectors" section at the end of the module.
_DETECTOR_REGISTRY: list[tuple[str, Callable[[np.ndarray, np.ndarray], dict]]] = []


def compute_image_quality_flags(img_gray: np.ndarray, mask: np.ndarray) -> dict:
    """Run every registered [Active] image-quality detector on one frame.

    img_gray : the `_final.png` grayscale — the modality the measurements are
               computed on (decision D3) — at mask resolution.
    mask     : the multi-class segmentation mask (uint8 label IDs).

    Returns ``{'flags': tuple[str, ...] ordered by IMAGE_QUALITY_FLAG_PRIORITY,
    'detector_metadata': dict}`` — the § 5.5 contract. `detector_metadata` maps
    each detector's flag name to its raw score dict.

    A detector must return its `flag` as either None (did not fire) or a
    canonical § 5.1 IMAGE_QUALITY_FLAG_PRIORITY code. A flag outside the
    taxonomy raises ValueError — a typo'd or unknown flag is a detector bug
    and must fail loud, never be silently swallowed into a "no problem found"
    verdict. The guard sits here, at the detector boundary, deliberately *not*
    in `canonical_image_quality_flag_order`: that helper is shared with the
    Build-Step 1 operator path (Phase-4 `drop_reason_codes`), where a free-text
    non-code is *expected* to drop out (carried in `drop_reason_other`).

    Build-Step 2 Phase 2 (build_step_2_plan.md items 5–6) fills the registry
    with the 14 image+mask detectors, so this entry point now returns their
    live flags. Two further [Active] detectors — `segmentation_low_confidence`
    (needs the segmenter softmax entropy, § 5.4) and `subq_fat_compressed`
    (needs pixel spacing for its mm threshold, § 5.2) — take inputs the
    `(img_gray, mask)` contract cannot carry; they are standalone functions
    (`detect_segmentation_low_confidence` / `detect_subq_fat_compressed`) the
    Phase-3 pipeline wiring runs and merges. See the detector section below.
    """
    if img_gray.shape != mask.shape:
        raise ValueError(
            f"img_gray {img_gray.shape} and mask {mask.shape} must share a "
            f"shape — img_gray is the _final.png grayscale at mask resolution "
            f"(§ 7.2). A resize mismatch must fail loud here, never produce a "
            f"misaligned per-pixel detector verdict."
        )
    flags: list[str] = []
    detector_metadata: dict = {}
    for name, detector in _DETECTOR_REGISTRY:
        result = detector(img_gray, mask)
        detector_metadata[name] = result.get("metadata", {})
        flag = result.get("flag")
        if flag is not None and flag not in IMAGE_QUALITY_FLAG_PRIORITY:
            raise ValueError(
                f"detector {name!r} returned flag {flag!r}, which is not a "
                f"canonical § 5.1 IMAGE_QUALITY_FLAG_PRIORITY reason code — a "
                f"detector must emit a taxonomy code or None. A typo'd or "
                f"unknown flag is a detector bug and must fail loud, not be "
                f"silently dropped into a 'no problem found' verdict."
            )
        if flag:
            flags.append(flag)
    return {
        "flags": canonical_image_quality_flag_order(flags),
        "detector_metadata": detector_metadata,
    }


# ---------------------------------------------------------------------------
# § 6.2 — the per-measurement gating table
# ---------------------------------------------------------------------------

# flag -> {measurement: INVALID | BORDERLINE}. A measurement absent from a
# flag's row is *transparent* to that flag (§ 6.1 — the "blank cell"). Every
# one of the 22 IMAGE_QUALITY_FLAG_PRIORITY codes has a row; an empty row means
# the flag taints nothing (probe_orientation_flipped — § 6.2 ◇: no current
# measurement is directional). Transcribed cell-for-cell from the § 6.2 table;
# the four technical-audit flips and the C11 coverage rule are carried in the
# § 6.2 footnotes and are already baked into the cells below.
_GATING_TABLE: dict[str, dict[str, str]] = {
    # orthogonal — taints every measurement (§ 6.2 rows 1–2)
    "wrong_site": {m: INVALID for m in GATING_MEASUREMENTS},
    "segmenter_mislabel_wrong_class": {m: INVALID for m in GATING_MEASUREMENTS},
    # structural — femur
    "femur_out_of_frame": {"qmt_mm": INVALID, "star_ratio": INVALID},
    "femur_off_central_zone": {"qmt_mm": BORDERLINE, "star_ratio": BORDERLINE},
    # geometric / framing
    # ‡ edge_clip_lateral — RESERVE STATE (Akadir 2026-05-24 § 1 + § 4 + § 5):
    #   the detector still fires + goes on the record + carries its prose in
    #   the validity_flags tuple, but it taints NO measurement. The rationale
    #   ratified in the v2 reframe: "visible RF area" is the RF cross-section
    #   visible in the frame, by definition — it cannot be 'invalid' due to
    #   FOV; the FOV IS what defines it. The earlier C11 rule (INV for the
    #   visible-area features only) was the LETTER of the rename; this empty
    #   row is the SPIRIT. If anyone reinstates an invalidation here,
    #   tests/semantic_invariants/test_data_invariants.py goes red.
    "edge_clip_lateral": {},
    "edge_clip_vertical": {
        "rf_thickness_mm": INVALID, "qmt_mm": BORDERLINE,
        "star_ratio": BORDERLINE, "full_thickness_q50_mm": INVALID,
    },
    # gross intensity
    "saturation_high_global": {"int_cv_within": INVALID, "rf_echo": INVALID},
    "saturation_high_regional": {"rf_thickness_mm": BORDERLINE, "int_cv_within": BORDERLINE},
    "dark_clip_high": {
        "qmt_mm": BORDERLINE, "star_ratio": BORDERLINE,
        "int_cv_within": INVALID, "rf_echo": INVALID,
    },
    "tgc_banding": {"int_cv_within": BORDERLINE, "rf_echo": BORDERLINE},
    # gross sharpness — † motion_blur → visible_rf_area_mm2 = INV (technical-audit flip #2)
    "motion_blur": {
        "rf_thickness_mm": INVALID, "qmt_mm": INVALID, "star_ratio": INVALID,
        "full_thickness_q50_mm": INVALID, "visible_rf_area_mm2": INVALID,
        "int_cv_within": BORDERLINE,
    },
    # coupling / near-field / artifact — § column-set detectors
    "air_gap_reverberation": {
        "rf_thickness_mm": INVALID, "qmt_mm": INVALID, "star_ratio": INVALID,
        "full_thickness_q50_mm": INVALID, "visible_rf_area_mm2": INVALID,
        "int_cv_within": BORDERLINE, "rf_echo": BORDERLINE, "muscle_fat_ratio": INVALID,
    },
    "near_field_clutter": {
        "rf_thickness_mm": BORDERLINE, "full_thickness_q50_mm": BORDERLINE,
        "visible_rf_area_mm2": BORDERLINE,
    },
    "vessel_shadow_in_roi": {
        "rf_thickness_mm": INVALID, "qmt_mm": INVALID, "star_ratio": INVALID,
        "full_thickness_q50_mm": INVALID, "visible_rf_area_mm2": INVALID,
        "int_cv_within": BORDERLINE, "rf_echo": BORDERLINE,
    },
    "posterior_acoustic_enhancement": {"int_cv_within": BORDERLINE, "rf_echo": BORDERLINE},
    # anatomical — ¶ subq_fat_compressed → qmt_mm/star_ratio = BDR (provisional)
    "subq_fat_compressed": {
        "qmt_mm": BORDERLINE, "star_ratio": BORDERLINE, "muscle_fat_ratio": INVALID,
    },
    # ultrasound-specific (Tier-1, [Conditional]) — ◊ oblique_plane → rf_echo = BDR
    "oblique_plane": {
        "rf_thickness_mm": INVALID, "qmt_mm": INVALID, "star_ratio": INVALID,
        "full_thickness_q50_mm": INVALID, "visible_rf_area_mm2": INVALID,
        "int_cv_within": BORDERLINE, "rf_echo": BORDERLINE,
    },
    "anisotropy_dropout_superficial": {
        "rf_thickness_mm": BORDERLINE, "full_thickness_q50_mm": BORDERLINE,
        "visible_rf_area_mm2": BORDERLINE,
    },
    "anisotropy_dropout_deep": {
        "rf_thickness_mm": BORDERLINE, "qmt_mm": INVALID, "star_ratio": INVALID,
        "full_thickness_q50_mm": BORDERLINE, "visible_rf_area_mm2": BORDERLINE,
    },
    "deep_attenuation_high": {
        "qmt_mm": BORDERLINE, "star_ratio": BORDERLINE,
        "full_thickness_q50_mm": BORDERLINE,
    },
    # model-derived (Tier-2, [Active] in v0.2) — all geometry BDR
    "segmentation_low_confidence": {
        "rf_thickness_mm": BORDERLINE, "qmt_mm": BORDERLINE, "star_ratio": BORDERLINE,
        "full_thickness_q50_mm": BORDERLINE, "visible_rf_area_mm2": BORDERLINE,
        "muscle_fat_ratio": BORDERLINE,
    },
    # orientation (named-deferred) — § 6.2 ◇: empty row, no directional measurement
    "probe_orientation_flipped": {},
}


def image_quality_tier(measurement: str, flags) -> str:
    """The § 6.1 per-measurement validity rule.

    Return ``'valid' | 'borderline' | 'invalid'`` for one measurement, given
    the frame's set of image-quality flags. INV beats BDR beats valid: if any
    flag is INV for this measurement -> invalid; else if any is BDR ->
    borderline; else valid. A flag in neither set is transparent (§ 6.1 —
    the "blank cell").

    This is the *raw* per-measurement map. The § 11.5 compound-false-positive
    discipline (two-detector concurrence) acts one layer up, on the aggregate
    badge — never here (§ 6.1 note): several flags are legitimate single-
    detector killers, and this verdict also feeds the § 7.3 pooling exclusion.
    """
    canonical = _MEASUREMENT_ALIASES.get(measurement, measurement)
    if canonical not in GATING_MEASUREMENTS:
        raise ValueError(
            f"unknown measurement {measurement!r}; the § 6.2 gating table is "
            f"defined for {sorted(GATING_MEASUREMENTS)} "
            f"(plus aliases {sorted(_MEASUREMENT_ALIASES)})"
        )
    verdicts = {_GATING_TABLE.get(flag, {}).get(canonical) for flag in flags}
    if INVALID in verdicts:
        return INVALID
    if BORDERLINE in verdicts:
        return BORDERLINE
    return VALID


def image_quality_tier_per_measurement(flags) -> dict[str, str]:
    """The per-measurement tier for every § 6.2 gating-table measurement —
    the `image_quality_tier_per_measurement` field of `ImageQualityFeatures`
    (§ 7.1)."""
    return {m: image_quality_tier(m, flags) for m in GATING_MEASUREMENTS}


def image_quality_aggregate_tier_from_tiers(tiers: dict) -> str:
    """The § 6.3 aggregate badge computed from a per-measurement tier map.

    Split out of `image_quality_aggregate_tier` so a caller that has *adjusted*
    the per-measurement tiers derives the aggregate from the adjusted map
    rather than re-deriving it from the raw flags. Build-Step 2 Phase 3 uses it
    after `qmt_provenance_capped_tiers` has capped qmt_mm / star_ratio by the
    QMT provenance (a `femur_supported_proxy` measurement is never `valid`).
    """
    headline = [tiers[m] for m in HEADLINE_MEASUREMENTS]
    if INVALID in headline:
        return INVALID
    n_valid = sum(1 for m in GATING_MEASUREMENTS if tiers[m] == VALID)
    floor = THRESHOLDS["aggregate_tier__headline_secondary_valid_fraction_min"].value
    min_valid = math.ceil(floor * len(GATING_MEASUREMENTS))
    if all(t == VALID for t in headline) and n_valid >= min_valid:
        return VALID
    return BORDERLINE


def image_quality_aggregate_tier(flags) -> str:
    """The § 6.3 per-frame aggregate badge, derived from the per-measurement tiers.

    * ``invalid``    — any headline-4 measurement is invalid.
    * ``valid``      — every headline-4 measurement is valid AND at least
      ``ceil(f * 8)`` of the eight gating-table measurements are valid, where
      ``f`` is the registry's aggregate-Valid fraction floor (§ 6.3 — default
      0.60, i.e. 5-of-8). The floor is read live from `qa_thresholds.THRESHOLDS`
      so a Build-Step-2 calibration of it propagates without a code change.
    * ``borderline`` — everything else: a headline-4 measurement borderline
      with none invalid, or enough secondary measurements degraded to miss the
      valid-fraction floor.

    The headline-4 gate hard because they are the operator's result-card
    numbers; a degraded *secondary* measurement is real information but does
    not by itself condemn the frame — the 60% floor exists precisely so a
    single secondary failure does not demote the whole frame (§ 6.3).
    """
    return image_quality_aggregate_tier_from_tiers(
        image_quality_tier_per_measurement(flags))


def dominant_reason(flags) -> Optional[str]:
    """The highest-priority flag — the "dominant reason" badge text (§ 8.0).

    None when there are no flags. Order is IMAGE_QUALITY_FLAG_PRIORITY (§ 5.1);
    the priority tuple controls UI surfacing prominence, not visibility — every
    flag stays inspectable (Transparency Rule 1).
    """
    ordered = canonical_image_quality_flag_order(flags)
    return ordered[0] if ordered else None


def calibrated_flags(flags, detector_metadata: dict) -> tuple:
    """The subset of `flags` whose detector ships a *calibrated* threshold —
    the flags eligible to drive a hard validity verdict (§ 8.6 / § 11.1).

    Each detector self-reports calibration status in its metadata as
    `pending_calibration` (the `@_detector` provenance stamp): False for the
    Threshold-tier A / reuse detectors trustworthy on day one, True for every
    un-calibrated Threshold-tier B detector. Build-Step 2's cohort histogram
    surfaced why this gate is load-bearing — `wrong_site` (un-calibrated) fires
    on 79.8 % of cohort frames; feeding it into the aggregate verdict would
    condemn most of the cohort on a guessed threshold (HANDOFF Finding B). So
    the per-measurement and aggregate tiers that drive pooling are computed
    over THIS subset only; the full `flags` set stays fully visible for the UI
    (Transparency Rule 1 — every flag inspectable, just labelled "pending
    calibration").

    A flag whose detector metadata is missing, or does not stamp
    `pending_calibration is False`, is treated as un-calibrated — default-deny,
    so an un-stamped verdict is never silently trusted.

    As Build-Step 2b calibrates each Tier-B detector, its registry entry gains
    `calibrated_on`, `pending_calibration` flips to False, and its flag begins
    counting here automatically — no code change (§ 12.4: calibration is a
    data-update, never a detector rebuild).
    """
    return canonical_image_quality_flag_order(
        flag for flag in (flags or ())
        if detector_metadata.get(flag, {}).get("pending_calibration") is False
    )


# QMT provenance -> the *best* image-quality tier qmt_mm / star_ratio may carry.
# A `femur_supported_proxy` QMT is a degraded fallback measurement (~half the
# standard value, fallback-only), so the QA verdict caps it at `borderline`; a
# no-femur / absent measurement is `invalid`. `standard_qmt` — and a missing
# provenance (a legacy frame) — impose no cap. This keeps the QA qmt_mm verdict
# from contradicting the QMT provenance gate (RF QA plan § 7.3): the QA layer
# must not rate a degraded proxy measurement `valid`.
_QMT_PROVENANCE_TIER_CAP = {
    "standard_qmt": VALID,
    "femur_supported_proxy": BORDERLINE,
    "no_femur_proxy": INVALID,
    "no_measurement": INVALID,
}
_TIER_RANK = {INVALID: 0, BORDERLINE: 1, VALID: 2}


def qmt_provenance_capped_tiers(tiers: dict, qmt_provenance) -> dict:
    """Return `tiers` with `qmt_mm` / `star_ratio` capped by the QMT provenance.

    The per-measurement QA tier is the § 6.2 flag mapping — it is blind to
    whether the QMT measurement is a standard fascia-to-femur reading or a
    degraded `femur_supported_proxy` fallback. This caps the qmt_mm /
    star_ratio tiers so the QA verdict can never rate a QMT measurement better
    than its provenance warrants (a proxy is never `valid`). `standard_qmt`, or
    a missing / None / unrecognised provenance, impose no cap. The cap is a
    ceiling, not a floor — a tier already worse than the cap is left as-is.
    Returns a new dict; `tiers` is not mutated.
    """
    cap = _QMT_PROVENANCE_TIER_CAP.get(qmt_provenance, VALID)
    out = dict(tiers)
    if cap == VALID:
        return out
    for m in ("qmt_mm", "star_ratio"):
        if m in out and _TIER_RANK[out[m]] > _TIER_RANK[cap]:
            out[m] = cap
    return out


def _self_check() -> None:
    """Import-time invariant: the gating table covers exactly the 22-code
    taxonomy — no flag missing, none invented. A divergence here is a
    transcription error against § 6.2."""
    table = set(_GATING_TABLE)
    taxonomy = set(IMAGE_QUALITY_FLAG_PRIORITY)
    assert table == taxonomy, (
        f"_GATING_TABLE vs IMAGE_QUALITY_FLAG_PRIORITY mismatch — "
        f"missing {sorted(taxonomy - table)}, extra {sorted(table - taxonomy)}"
    )
    for flag, row in _GATING_TABLE.items():
        for meas, verdict in row.items():
            assert meas in GATING_MEASUREMENTS, f"{flag}: unknown column {meas!r}"
            assert verdict in (INVALID, BORDERLINE), f"{flag}/{meas}: bad verdict {verdict!r}"


_self_check()


# ===========================================================================
# Build-Step 2 Phase 2 — the detectors (build_step_2_plan.md items 5 + 6)
# ===========================================================================
# The 15 detectors of the v0.2 working set (RF Image-Quality QA plan § 5.2 /
# § 5.4). Each returns the registry contract — {'flag': <§ 5.1 code> | None,
# 'metadata': <raw scores>} — so the entry point and the Phase-3 merge treat
# every detector uniformly.
#
# THRESHOLD DISCIPLINE (§ 12.4 single-source rule): every detection number is
# resolved by name from qa_thresholds.THRESHOLDS via get() — no detector
# hard-codes one. Threshold-tier B values ship un-calibrated; the NaN-valued
# ones can never fire (a NaN comparison is always False — the § 8.6 safety
# belt), and Build-Step 2b sets the real numbers as a data-update, no code
# change. The _TGC_* constants below are algorithm-form choices (a polynomial
# degree, a guard floor) — not detection thresholds, so not registry entries.

# Algorithm-form constants for tgc_banding's detrending (NOT § 12.4 thresholds).
_TGC_DETREND_POLY_DEG = 3   # smooth attenuation trend; bands stay in the residual
_TGC_MIN_MUSCLE_ROWS = 8    # below this the muscle band is too short to fit/measure


def _meta_threshold(value):
    """A threshold value for detector metadata — None when NaN (un-calibrated
    Tier-B), so a JSON ledger never carries a bare NaN and a UI reads None as
    "pending calibration" (§ 8.6). The raw detector *score* is always real."""
    return None if isinstance(value, float) and math.isnan(value) else value


# Calibration-provenance plumbing. The calibration boundary is
# ThresholdEntry.is_starting_point, NOT "is the value NaN" — several
# un-calibrated Tier-B detectors (wrong_site, dark_clip_high, the saturation
# fractions, subq_fat_compressed) carry a finite starting value and DO fire.
# Every detector resolves its thresholds through the recording `get` below; the
# `@_detector` rebind then stamps the detector's metadata with `thresholds_used`
# and `pending_calibration`. Phase 3 gates an un-calibrated verdict on
# `pending_calibration` — the NaN safety belt is only the subset of those that
# additionally cannot fire (§ 8.6 / § 11.1).
_thresholds_used: contextvars.ContextVar = contextvars.ContextVar("qa_thresholds_used")


def get(name: str):
    """Resolve a QA threshold via the registry and, when called inside a
    detector, record the name so `@_detector` can report it. Outside a detector
    it is a plain pass-through to qa_thresholds.get (loud on a typo'd name)."""
    recorder = _thresholds_used.get(None)
    if recorder is not None:
        recorder.append(name)
    return _registry_get(name)


def _detector(fn):
    """Wrap a detector so its returned metadata is auto-stamped with calibration
    provenance: `thresholds_used` (the registry thresholds it resolved) and
    `pending_calibration` (True when any is an un-calibrated Threshold-tier B
    starting point — § 11.1 / § 8.6). Applied by a post-definition rebind so the
    module name, the registry entry, and a direct test import are one object."""
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        recorder: list[str] = []
        token = _thresholds_used.set(recorder)
        try:
            result = fn(*args, **kwargs)
        finally:
            _thresholds_used.reset(token)
        meta = result.setdefault("metadata", {})
        meta["thresholds_used"] = list(recorder)
        meta["pending_calibration"] = any(
            THRESHOLDS[n].is_starting_point for n in recorder)
        return result
    return wrapped


def _largest_component(binary: np.ndarray) -> np.ndarray:
    """The largest 8-connected component of a 2-D bool mask.

    Mirrors post_processing._largest_component so the edge-clip detectors key
    off the same RF body `touches_edge` does — an island in a frame corner does
    not by itself trip an edge-clip flag (§ 5.2)."""
    if binary.size == 0 or not binary.any():
        return binary
    labelled, n = ndi.label(binary, structure=np.ones((3, 3), dtype=int))
    if n <= 1:
        return binary
    counts = np.bincount(labelled.ravel())
    counts[0] = 0                                    # drop the background label
    return labelled == int(counts.argmax())


def _ring_touch(binary: np.ndarray, n: int) -> tuple[bool, bool, bool, bool]:
    """(top, bottom, left, right) — whether `binary` has a pixel in the
    outermost n-pixel ring on each side of the frame."""
    if binary.size == 0 or not binary.any():
        return (False, False, False, False)
    return (
        bool(binary[:n, :].any()),
        bool(binary[-n:, :].any()),
        bool(binary[:, :n].any()),
        bool(binary[:, -n:].any()),
    )


def _max_run(col: np.ndarray) -> int:
    """Longest contiguous run of True in a 1-D bool array — a layer thickness
    in pixels, robust to a stray gap-pixel a bare count would miscount."""
    best = run = 0
    for v in col:
        run = run + 1 if v else 0
        if run > best:
            best = run
    return best


# ---------------------------------------------------------------------------
# Item 5 — Threshold-tier A detectors (live day one — physics rail or reuse)
# ---------------------------------------------------------------------------

def _detect_femur_out_of_frame(img_gray: np.ndarray, mask: np.ndarray) -> dict:
    """femur_out_of_frame (§ 5.2, F3) — no usable femur cortex anywhere in the
    frame, so qmt_mm / star_ratio cannot be measured.

    Threshold-tier A by reuse of the QMT pipeline's femur-presence test:
    fires precisely when the QMT path's `femur_absent` fires — i.e., when
    `_dominant_femur_mask` returns no above-threshold component
    (largest 8-conn blob ≥ `QMT_FEMUR_MIN_AREA_PX`). Sub-threshold stray
    femur pixels DO NOT count: they cannot anchor a QMT measurement, so a
    frame with only strays is functionally femur-less.

    Akadir 2026-05-25 v2 § 1 — fixed a definition asymmetry. The earlier
    cut used raw `label_area_px(mask, LABEL_FEMUR)`, firing only when the
    pixel count was literally 0; the picker's tier-3 dot and the QMT
    `femur_absent` flag both used the dominant-component definition. Two
    no-femur frames produced different IQ flag sets when one had stray
    sub-threshold pixels — the operator saw the same red "no femur" dot
    on both but a "thigh bone not visible" prose on only one. Aligning
    the IQ detector to the dominant-component definition collapses that
    asymmetry; the display-layer suppress (qa_surfacing.picker_qa_cue
    `suppress_flags`) then applies uniformly to all no-femur thumbnails.

    The raw femur pixel count survives in metadata for traceability — a
    truly-zero frame and a sub-threshold-stray frame are still
    distinguishable in `trace_frame.py` output and in any future
    calibration substrate, even though the flag firing is now identical."""
    from labeling_app.measurements import _dominant_femur_mask
    dominant = _dominant_femur_mask(mask)
    fired = not bool(dominant.any())
    return {
        "flag": "femur_out_of_frame" if fired else None,
        "metadata": {
            "dominant_femur_pixel_count": int(dominant.sum()),
            "raw_femur_pixel_count": int(label_area_px(mask, LABEL_FEMUR)),
        },
    }


def _detect_femur_off_central_zone(img_gray: np.ndarray, mask: np.ndarray) -> dict:
    """femur_off_central_zone (§ 5.2, F4) — a femur is present but the QMT
    measurement falls outside the central-zone pixel margin.

    Threshold-tier A by reuse: lifts the QMT pipeline's column-wise
    `outside_central_zone` verdict (central zone = QMT_CENTRAL_ZONE_MARGIN_PX,
    already calibrated) to a frame-level flag. When the femur is absent the QMT
    path emits `femur_absent`, not `outside_central_zone`, so this detector is
    correctly silent there — `femur_out_of_frame` owns that case."""
    flags = frame_qmt_validity_flags(mask)
    fired = "outside_central_zone" in flags
    return {
        "flag": "femur_off_central_zone" if fired else None,
        "metadata": {"qmt_validity_flags": list(flags)},
    }


def _detect_edge_clip_lateral(img_gray: np.ndarray, mask: np.ndarray) -> dict:
    """edge_clip_lateral (§ 5.2, F5) — the RF cross-section runs off the left
    or right frame edge.

    Directional extension of `touches_edge`: the RF body (largest component) in
    the outermost N-pixel ring of a left/right column. Threshold-tier A,
    N = edge_clip__boundary_ring_px (= 1, the existing `touches_edge`
    behaviour). Per the C11 coverage-limitation rule it taints visible_rf_area_mm2 /
    full_area_mm2 only — the § 6.2 gating table already encodes that. The
    "visible" rename (Akadir 2026-05-24) acknowledges the FOV truth in the
    field name itself; the rule stays the same."""
    n = int(get("edge_clip__boundary_ring_px").value)
    rf = _largest_component(mask == LABEL_RF)
    _t, _b, left, right = _ring_touch(rf, n)
    edges = [name for name, hit in (("left", left), ("right", right)) if hit]
    return {
        "flag": "edge_clip_lateral" if edges else None,
        "metadata": {"edges": edges, "ring_px": n},
    }


def _detect_edge_clip_vertical(img_gray: np.ndarray, mask: np.ndarray) -> dict:
    """edge_clip_vertical (§ 5.2, F6) — a measurement-bearing structure is
    truncated at the top or bottom frame edge.

    Fires when the RF body (largest component) or the fascia has a pixel in the
    outermost N-pixel ring of a top/bottom row — the superficial fascia clipped
    off the top, or the RF / deep fascia off the bottom, each costing a thickness
    caliper an endpoint (§ 5.2 Comment 10).

    Skin and fat are excluded — they sit at the top by anatomy, not by clipping.
    The FEMUR is excluded for the SAME reason at the other edge: the bone sits at
    the *deep* edge by anatomy — it extends past the imaging window, and QMT reads
    only its NEAR surface (the muscle–bone interface, which is captured), so a
    femur edge-touch is not a clipped measurement (QMT/STAR stay calculable). The
    bottom of the femur running off-frame is expected, not a defect. Whether the
    femur is adequately captured for QMT is owned by the dedicated
    `femur_out_of_frame` detector, not here. (Akadir 2026-06-26: including the
    femur made a normal deep femur null the RF thickness on the center-RF cine and
    spuriously BORDERLINE the QMT on the femur cine.)
    Threshold-tier A, N = edge_clip__boundary_ring_px."""
    n = int(get("edge_clip__boundary_ring_px").value)
    structures = {
        "rf": _largest_component(mask == LABEL_RF),
        "fascia": mask == LABEL_FASCIA,
    }
    clipped: dict[str, list[str]] = {}
    for name, struct in structures.items():
        top, bottom, _l, _r = _ring_touch(struct, n)
        if top:
            clipped.setdefault("top", []).append(name)
        if bottom:
            clipped.setdefault("bottom", []).append(name)
    return {
        "flag": "edge_clip_vertical" if clipped else None,
        "metadata": {"clipped": clipped, "ring_px": n},
    }


def _detect_saturation_high_global(img_gray: np.ndarray, mask: np.ndarray) -> dict:
    """saturation_high_global (§ 5.2, F7) — pixels clipped at the 255 rail
    across the RF ROI, killing the intensity statistics.

    Threshold-tier A rail (>= 250 grayscale — the 8-bit ceiling, cohort-
    independent); Threshold-tier B ROI-fraction cut-off (the 2 % is borrowed
    from the QMT occupancy tolerance — § 11.1 honesty caveat — so it ships as a
    starting point, pending calibration)."""
    rail = get("saturation_high__pixel_value_min").value
    frac_max = get("saturation_high_global__roi_fraction_max").value
    roi = mask == LABEL_RF
    n_roi = int(roi.sum())
    if n_roi == 0:
        return {"flag": None, "metadata": {"roi_empty": True}}
    frac = float(np.count_nonzero(img_gray[roi] >= rail)) / n_roi
    return {
        "flag": "saturation_high_global" if frac > frac_max else None,
        "metadata": {"roi_saturated_fraction": frac, "pixel_rail": rail,
                     "roi_fraction_max": frac_max, "n_roi_px": n_roi},
    }


def _detect_dark_clip_high(img_gray: np.ndarray, mask: np.ndarray) -> dict:
    """dark_clip_high (§ 5.2, F9) — pixels clipped at the 0 rail across the RF
    ROI (gain too low, or deep attenuation).

    Threshold-tier A rail (<= 5 grayscale); Threshold-tier B ROI-fraction
    cut-off — the mirror of saturation_high_global at the opposite rail."""
    rail = get("dark_clip_high__pixel_value_max").value
    frac_max = get("dark_clip_high__roi_fraction_max").value
    roi = mask == LABEL_RF
    n_roi = int(roi.sum())
    if n_roi == 0:
        return {"flag": None, "metadata": {"roi_empty": True}}
    frac = float(np.count_nonzero(img_gray[roi] <= rail)) / n_roi
    return {
        "flag": "dark_clip_high" if frac > frac_max else None,
        "metadata": {"roi_dark_fraction": frac, "pixel_rail": rail,
                     "roi_fraction_max": frac_max, "n_roi_px": n_roi},
    }


def _detect_saturation_high_regional(img_gray: np.ndarray, mask: np.ndarray) -> dict:
    """saturation_high_regional (§ 5.2, F8) — a concentrated patch of 255-rail
    clipping (a strong local specular reflector).

    Tiles the frame into non-overlapping square blocks and, over every block
    that overlaps the RF ROI, measures the fraction of block pixels at the rail
    — fires when any such block exceeds the block-fraction cut-off. Rail is
    Threshold-tier A; the block size and fraction are Threshold-tier B."""
    rail = get("saturation_high__pixel_value_min").value
    frac_max = get("saturation_high_regional__block_fraction_max").value
    block = int(get("saturation_high_regional__block_size_px").value)
    roi = mask == LABEL_RF
    if not roi.any():
        return {"flag": None, "metadata": {"roi_empty": True}}
    h, w = img_gray.shape
    worst_frac = 0.0
    worst_block = None
    n_blocks = 0
    for r0 in range(0, h, block):
        for c0 in range(0, w, block):
            r1, c1 = min(r0 + block, h), min(c0 + block, w)
            if not roi[r0:r1, c0:c1].any():
                continue                             # block does not reach the RF ROI
            n_blocks += 1
            tile = img_gray[r0:r1, c0:c1]
            frac = float(np.count_nonzero(tile >= rail)) / tile.size
            if frac > worst_frac:
                worst_frac, worst_block = frac, [r0, c0, r1, c1]
    return {
        "flag": "saturation_high_regional" if worst_frac > frac_max else None,
        "metadata": {"worst_block_saturated_fraction": worst_frac,
                     "worst_block": worst_block, "pixel_rail": rail,
                     "block_size_px": block, "block_fraction_max": frac_max,
                     "n_roi_blocks": n_blocks},
    }


# ---------------------------------------------------------------------------
# Item 6 — Tier-0 algorithm detectors (ship + run; Tier-B, pending calibration)
# ---------------------------------------------------------------------------

def _detect_wrong_site(img_gray: np.ndarray, mask: np.ndarray) -> dict:
    """wrong_site (§ 5.2, F1) — the frame does not contain enough RF to
    assess. Akadir 2026-05-24 redesign (the half-of-columns rule):

      The detector is now a geometric coverage check, not a composition
      ratio. We compute `rf_column_coverage` over the *cleaned* RF
      (largest 8-conn component + binary_fill_holes, the same canonical
      cleanup p04 displays):

          rf_column_coverage = (# columns with ≥ 1 cleaned-RF pixel) / total_columns

      The detector FIRES when `rf_column_coverage < 0.5` (the half-of-
      columns threshold, ratified by Akadir 2026-05-24 § 4). At ≥ 50 %
      column coverage, the frame is judged as having meaningful RF
      whether centred or side-shot — both are valid scan poses for the
      assessment.

      Earlier rf_fraction-on-quadriceps logic fired on 79.8 % of cohort
      (Build-Step 2a histogram, Finding B) because side-shots of RF
      (high column coverage, no VL/VM neighbours) scored low rf_fraction
      and got mislabelled as wrong-site. The column-coverage rule
      dissolves that misfire at the root — composition is irrelevant
      when geometry says RF is meaningfully present.

      Threshold-tier A (geometric, not statistical): the 0.5 cut is the
      design constant, resolved from the registry as
      `wrong_site__rf_column_coverage_min` (§ 12.4 — not hardcoded).
      Detector also stays as the gate for true off-thigh frames (zero RF
      pixels), which is what wrong_site was always supposed to catch.
    """
    # Local imports to avoid widening the module's import surface.
    from preflight_helpers.post_processing import post_process_rf

    # Threshold-tier A geometric constant, resolved from the registry (§ 12.4 —
    # no hardcoded literal). The get() call also records the name, so the
    # @_detector wrapper stamps pending_calibration=False legitimately (Tier-A),
    # not via an empty-recorder accident.
    threshold = float(get("wrong_site__rf_column_coverage_min").value)

    rf_full = post_process_rf(mask, fill_holes=True).rf_full_mask
    if rf_full.size == 0 or not rf_full.any():
        # No RF in the cleaned blob — genuine off-RF frame.
        return {
            "flag": "wrong_site",
            "metadata": {"reason": "no_rf_pixels",
                          "rf_column_coverage": 0.0,
                          "threshold": threshold},
        }
    cols_with_rf = int(rf_full.any(axis=0).sum())
    total_cols = int(rf_full.shape[1])
    rf_column_coverage = cols_with_rf / total_cols
    fires = rf_column_coverage < threshold
    return {
        "flag": "wrong_site" if fires else None,
        "metadata": {
            "rf_column_coverage": rf_column_coverage,
            "cols_with_rf": cols_with_rf,
            "total_cols": total_cols,
            "threshold": threshold,
        },
    }


def _detect_motion_blur(img_gray: np.ndarray, mask: np.ndarray) -> dict:
    """motion_blur (§ 5.2, F11) — probe/patient motion smears the fascia.

    Tenengrad (mean squared gradient) over the segmented fascia band — fascia
    is a specular structure whose sharpness is genuinely motion-degraded and
    not speckle-confounded (the v0 variance-of-Laplacian is rejected, § 5.2 /
    Loizou 2006). A LOW Tenengrad fires the flag. Threshold-tier B — unit-free,
    probe/gain-dependent; un-calibrated (NaN) so the comparison never fires."""
    threshold = get("motion_blur__fascia_band_tenengrad_min").value
    fascia = mask == LABEL_FASCIA
    if not fascia.any():
        return {"flag": None, "metadata": {"fascia_absent": True}}
    gy, gx = np.gradient(img_gray.astype(np.float64))
    tenengrad = float((gx * gx + gy * gy)[fascia].mean())
    return {
        "flag": "motion_blur" if tenengrad < threshold else None,    # NaN -> belt
        "metadata": {"fascia_band_tenengrad": tenengrad,
                     "threshold": _meta_threshold(threshold),
                     "n_fascia_px": int(fascia.sum())},
    }


def _detect_tgc_banding(img_gray: np.ndarray, mask: np.ndarray) -> dict:
    """tgc_banding (§ 5.2, F10) — a mis-set time-gain compensation paints
    horizontal intensity bands.

    Restricts to the muscle rows (the RF row span — femur, fat and skin
    excluded), detrends the row-mean depth profile with a smooth low-order
    polynomial (the expected attenuation curve; the femur disappears into the
    trend), and measures the standard deviation of the residual — bands survive
    as residual oscillation. Threshold-tier B (scale-dependent), un-calibrated."""
    threshold = get("tgc_banding__detrended_residual_sigma_max").value
    rf = mask == LABEL_RF
    rf_rows = np.where(rf.any(axis=1))[0]
    rf_cols = np.where(rf.any(axis=0))[0]
    if rf_rows.size < _TGC_MIN_MUSCLE_ROWS or rf_cols.size == 0:
        return {"flag": None, "metadata": {"muscle_band_row_count": int(rf_rows.size),
                                           "muscle_band_too_short": True}}
    r0, r1 = int(rf_rows.min()), int(rf_rows.max())
    c0, c1 = int(rf_cols.min()), int(rf_cols.max())
    profile = img_gray[r0:r1 + 1, c0:c1 + 1].astype(np.float64).mean(axis=1)
    y = np.arange(profile.size, dtype=np.float64)
    trend = np.polyval(np.polyfit(y, profile, _TGC_DETREND_POLY_DEG), y)
    residual_sigma = float(np.std(profile - trend))
    return {
        "flag": "tgc_banding" if residual_sigma > threshold else None,   # NaN -> belt
        "metadata": {"detrended_residual_sigma": residual_sigma,
                     "threshold": _meta_threshold(threshold),
                     "muscle_band_rows": [r0, r1]},
    }


def _detect_air_gap_reverberation(img_gray: np.ndarray, mask: np.ndarray) -> dict:
    """air_gap_reverberation (§ 5.2, F12) — poor probe-skin coupling paints a
    periodic reverberation comb in the near-surface zone.

    FFT of each column's intensity profile over the segmented near-surface band
    (rows down to the skin layer); the comb peak is the largest magnitude in
    the 3–8 px reverberation-period band, normalised by the column's signal RMS
    and averaged over columns. Threshold-tier B (probe-footprint-dependent),
    un-calibrated."""
    mag_min = get("air_gap_reverberation__comb_peak_magnitude_min").value
    p_min = int(get("air_gap_reverberation__comb_period_px_min").value)
    p_max = int(get("air_gap_reverberation__comb_period_px_max").value)
    skin = mask == LABEL_SKIN
    if not skin.any():
        return {"flag": None, "metadata": {"skin_absent": True}}
    band_bottom = int(np.where(skin.any(axis=1))[0].max()) + 1
    if band_bottom < 2 * p_max:                      # too shallow to resolve the comb
        return {"flag": None, "metadata": {"near_surface_band_px": band_bottom,
                                           "band_too_short": True}}
    peaks = []
    for c in np.where(skin.any(axis=0))[0]:
        prof = img_gray[:band_bottom, c].astype(np.float64)
        prof = prof - prof.mean()
        rms = float(np.sqrt(np.mean(prof * prof)))
        if rms < 1e-9:
            continue
        spec = np.abs(np.fft.rfft(prof))
        freqs = np.fft.rfftfreq(prof.size)           # cycles per pixel
        sel = (freqs >= 1.0 / p_max) & (freqs <= 1.0 / p_min)
        if sel.any():
            peaks.append(float(spec[sel].max()) / rms)
    comb = float(np.mean(peaks)) if peaks else 0.0
    return {
        "flag": "air_gap_reverberation" if comb > mag_min else None,   # NaN -> belt
        "metadata": {"comb_peak_magnitude": comb, "threshold": _meta_threshold(mag_min),
                     "period_band_px": [p_min, p_max], "n_columns": len(peaks)},
    }


def _detect_near_field_clutter(img_gray: np.ndarray, mask: np.ndarray) -> dict:
    """near_field_clutter (§ 5.2, F13) — probe-element artifacts paint
    high-frequency, non-periodic clutter in the near field.

    Mean squared Laplacian (a broadband high-frequency energy) over the
    segmented near-field layers — the skin and subcutaneous-fat classes
    (anchored to segmentation per Comment 4, not a fixed pixel window).
    Distinct from air_gap_reverberation, whose signal is periodic.
    Threshold-tier B, un-calibrated."""
    energy_max = get("near_field_clutter__clutter_energy_max").value
    near_field = (mask == LABEL_SKIN) | (mask == LABEL_FAT)
    n = int(near_field.sum())
    if n == 0:
        return {"flag": None, "metadata": {"near_field_layers_absent": True}}
    lap = ndi.laplace(img_gray.astype(np.float64))
    clutter_energy = float((lap[near_field] ** 2).mean())
    return {
        "flag": "near_field_clutter" if clutter_energy > energy_max else None,  # NaN -> belt
        "metadata": {"clutter_energy": clutter_energy,
                     "threshold": _meta_threshold(energy_max), "n_near_field_px": n},
    }


def _detect_vessel_shadow_in_roi(img_gray: np.ndarray, mask: np.ndarray) -> dict:
    """vessel_shadow_in_roi (§ 5.2, F14) — a vessel or calcification casts a
    near-zero-intensity vertical shadow column through the RF ROI.

    Per RF-ROI column, the mean intensity over the ROI pixels in that column;
    the darkest column carries the shadow. Threshold-tier B (column-darkness
    cut-off), un-calibrated. The darkest-vs-median contrast is surfaced in
    metadata for calibration to separate a true shadow from uniform dimming
    (dark_clip_high) or horizontal banding (tgc_banding)."""
    dark_max = get("vessel_shadow_in_roi__column_darkness_max").value
    roi = mask == LABEL_RF
    cols = np.where(roi.any(axis=0))[0]
    if cols.size == 0:
        return {"flag": None, "metadata": {"roi_empty": True}}
    col_means = np.array([float(img_gray[roi[:, c], c].mean()) for c in cols])
    darkest = float(col_means.min())
    return {
        "flag": "vessel_shadow_in_roi" if darkest < dark_max else None,   # NaN -> belt
        "metadata": {"darkest_column_mean": darkest,
                     "median_column_mean": float(np.median(col_means)),
                     "darkest_column": int(cols[int(col_means.argmin())]),
                     "threshold": _meta_threshold(dark_max),
                     "n_roi_columns": int(cols.size)},
    }


# ---------------------------------------------------------------------------
# Fascia continuity / anisotropy dropout (E4, Akadir 2026-06-04 note 9)
# ---------------------------------------------------------------------------
_FASCIA_BAND_ABOVE_PX = 4   # rows above the muscle top to look for the fascia ridge
_FASCIA_BAND_BELOW_PX = 2   # ...and just into the muscle top
_FASCIA_CLOSE_COLS = 2      # close fascia gaps this short (probe noise, not dropout)
_FASCIA_MIN_COLS = 16       # too few muscle columns -> not assessable
_FRANGI_REL_MIN = 0.15      # normalized Frangi ridge response counting as "present"


def _close_gaps_1d(arr: np.ndarray, k: int) -> np.ndarray:
    """Binary closing of a 1-D bool array: fill interior False gaps of length <= k
    (a 1-2 column fascia gap is probe noise, not a real plane dropout)."""
    if k <= 0 or not arr.any():
        return arr
    out = arr.copy()
    n = len(arr)
    i = 0
    while i < n:
        if out[i]:
            i += 1
            continue
        j = i
        while j < n and not out[j]:
            j += 1
        if i > 0 and j < n and (j - i) <= k:
            out[i:j] = True
        i = j
    return out


def _fascia_ridge_present(img_gray: np.ndarray, cols: np.ndarray,
                          rf: np.ndarray) -> Optional[np.ndarray]:
    """Per-column (aligned to `cols`): is there a Frangi bright-ridge response in
    the fascia band, normalized over the frame? None when skimage.frangi is absent
    (the detector then degrades to the segmented fascia alone)."""
    if _frangi is None:
        return None
    resp = _frangi(img_gray.astype(float), black_ridges=False)
    m = float(resp.max())
    if not np.isfinite(m) or m <= 0:
        return None
    resp = resp / m
    h = resp.shape[0]
    out = np.zeros(cols.size, dtype=bool)
    for i, c in enumerate(cols):
        mt = int(np.flatnonzero(rf[:, c]).min())
        lo = max(0, mt - _FASCIA_BAND_ABOVE_PX)
        hi = min(h, mt + _FASCIA_BAND_BELOW_PX + 1)
        out[i] = bool(resp[lo:hi, c].max() > _FRANGI_REL_MIN)
    return out


def _detect_fascia_dropout(img_gray: np.ndarray, mask: np.ndarray) -> dict:
    """anisotropy_dropout_superficial (E4, Akadir 2026-06-04 note 9) — the
    superficial fascia (the bright line on top of the RF) drops out in columns
    where the beam is off-perpendicular to it (anisotropy). A Tier-A GEOMETRIC
    detector that lives independent of the (dormant) calibration loop.

    continuity_fraction = the fraction of muscle-top columns carrying a fascia
    ridge — LABEL_FASCIA segmented in the band just above the muscle top, OR a
    Frangi bright-ridge there (to RECOVER a faint, real fascia the segmenter
    missed, so a segmentation gap is not mistaken for a beam dropout) — after
    closing 1-2 column gaps. Fires below the cut (0.70 default, a defensible
    geometric line pending Se/Sp validation on Round-3 labelled dropout)."""
    cut = get("anisotropy_dropout_superficial__continuity_fraction_min").value
    rf = _largest_component(mask == LABEL_RF)
    cols = np.where(rf.any(axis=0))[0]
    if cols.size < _FASCIA_MIN_COLS:
        return {"flag": None,
                "metadata": {"continuity_fraction": None, "reason": "rf_too_small",
                             "n_cols": int(cols.size)}}
    fascia = mask == LABEL_FASCIA
    ridge = _fascia_ridge_present(img_gray, cols, rf)
    present = np.zeros(cols.size, dtype=bool)
    for i, c in enumerate(cols):
        mt = int(np.flatnonzero(rf[:, c]).min())
        lo = max(0, mt - _FASCIA_BAND_ABOVE_PX)
        hi = mt + _FASCIA_BAND_BELOW_PX + 1
        has_fascia = bool(fascia[lo:hi, c].any())
        has_ridge = bool(ridge[i]) if ridge is not None else False
        present[i] = has_fascia or has_ridge
    continuity = float(_close_gaps_1d(present, _FASCIA_CLOSE_COLS).mean())
    return {
        "flag": "anisotropy_dropout_superficial" if continuity < cut else None,
        "metadata": {"continuity_fraction": continuity, "continuity_cut": cut,
                     "n_cols": int(cols.size), "frangi_used": ridge is not None},
    }


# ---------------------------------------------------------------------------
# Item 6 — the two standalone detectors (not _DETECTOR_REGISTRY members)
# ---------------------------------------------------------------------------
# These need pipeline context the (img_gray, mask) registry contract cannot
# carry. Phase 2 ships them implemented + tested; the Phase-3 pipeline wiring —
# which has the segmenter softmax entropy and the spacing row — runs and merges
# them into ImageQualityFeatures. This is consistent with Phase 1 leaving
# predict_mask's confidence_out pipeline-wiring to Phase 3 (HANDOFF item 1).

def detect_segmentation_low_confidence(boundary_entropy: float) -> dict:
    """segmentation_low_confidence (§ 5.4, F21) — the mask-boundary-entropy
    reduction: high boundary entropy means the segmenter is itself unsure where
    the muscle edge is.

    Consumes `boundary_entropy` — the scalar predict_mask's confidence
    out-parameter produces (Build-Step 2 Phase 1, segmenter.py). Not a
    _DETECTOR_REGISTRY member: that registry holds (img_gray, mask) detectors,
    and the softmax entropy is a model-internal signal not derivable from an
    argmax mask. Threshold-tier B, un-calibrated (NaN) — no entropy number
    ships as a verdict (§ 5.4); Build-Step 2b sets it."""
    threshold = get("segmentation_low_confidence__boundary_entropy_max").value
    fired = boundary_entropy > threshold             # NaN -> False -> belt
    return {
        "flag": "segmentation_low_confidence" if fired else None,
        "metadata": {"boundary_entropy": float(boundary_entropy),
                     "threshold": _meta_threshold(threshold)},
    }


def detect_subq_fat_compressed(mask: np.ndarray, spacing_row: dict) -> dict:
    """subq_fat_compressed (§ 5.2, F16, decision D8) — probe compression has
    flattened the subcutaneous fat layer.

    Reads the median subcutaneous-fat-layer thickness across the RF muscle's
    column span straight from the segmentation (fat IS a class, LABEL_FAT) and
    converts to mm with the mask-space pixel spacing. Not a _DETECTOR_REGISTRY
    member: the (img_gray, mask) contract carries no pixel spacing, and the
    threshold is in mm. Threshold-tier B (3 mm starting point — "plausible but
    not physics-grounded", § 5.2). It is the weakest detector in the working
    set: single-frame compressed-vs-lean discrimination is intrinsically weak
    (plan limitation L8) — its verdict is provisional, pending calibration."""
    thr_mm = get("subq_fat_compressed__central_fat_thickness_mm_min").value
    rf_cols = np.where((mask == LABEL_RF).any(axis=0))[0]
    if rf_cols.size == 0:
        return {"flag": None, "metadata": {"roi_empty": True}}
    fat = mask == LABEL_FAT
    thicknesses = [_max_run(fat[:, c]) for c in rf_cols]
    median_px = float(np.median(thicknesses))
    median_mm = px_to_mm(median_px, spacing_row.get("pixel_spacing_y"),
                         spacing_row.get("dcm_rows"), spacing_row.get("mask_height"))
    if median_mm is None:
        return {"flag": None,
                "metadata": {"central_fat_thickness_px": median_px,
                             "spacing_unavailable": True}}
    return {
        "flag": "subq_fat_compressed" if median_mm < thr_mm else None,
        "metadata": {"central_fat_thickness_mm": median_mm,
                     "central_fat_thickness_px": median_px,
                     "threshold_mm": thr_mm, "n_columns": int(rf_cols.size)},
    }


# ---------------------------------------------------------------------------
# Calibration provenance — wrap every detector
# ---------------------------------------------------------------------------
# Rebind each detector to its @_detector-wrapped form, so its metadata carries
# `thresholds_used` + `pending_calibration`. A post-definition rebind (rather
# than a decorator line per def) keeps the module name, the _DETECTOR_REGISTRY
# entry, and a direct test import all pointing at the one wrapped object.
_detect_femur_out_of_frame = _detector(_detect_femur_out_of_frame)
_detect_femur_off_central_zone = _detector(_detect_femur_off_central_zone)
_detect_edge_clip_lateral = _detector(_detect_edge_clip_lateral)
_detect_edge_clip_vertical = _detector(_detect_edge_clip_vertical)
_detect_saturation_high_global = _detector(_detect_saturation_high_global)
_detect_saturation_high_regional = _detector(_detect_saturation_high_regional)
_detect_dark_clip_high = _detector(_detect_dark_clip_high)
_detect_wrong_site = _detector(_detect_wrong_site)
_detect_motion_blur = _detector(_detect_motion_blur)
_detect_tgc_banding = _detector(_detect_tgc_banding)
_detect_air_gap_reverberation = _detector(_detect_air_gap_reverberation)
_detect_near_field_clutter = _detector(_detect_near_field_clutter)
_detect_vessel_shadow_in_roi = _detector(_detect_vessel_shadow_in_roi)
_detect_fascia_dropout = _detector(_detect_fascia_dropout)
detect_segmentation_low_confidence = _detector(detect_segmentation_low_confidence)
detect_subq_fat_compressed = _detector(detect_subq_fat_compressed)


# ---------------------------------------------------------------------------
# The detector registry — populated now that the detectors are defined
# ---------------------------------------------------------------------------

_DETECTOR_REGISTRY.extend([
    ("wrong_site", _detect_wrong_site),
    ("femur_out_of_frame", _detect_femur_out_of_frame),
    ("femur_off_central_zone", _detect_femur_off_central_zone),
    ("edge_clip_lateral", _detect_edge_clip_lateral),
    ("edge_clip_vertical", _detect_edge_clip_vertical),
    ("saturation_high_global", _detect_saturation_high_global),
    ("saturation_high_regional", _detect_saturation_high_regional),
    ("dark_clip_high", _detect_dark_clip_high),
    ("tgc_banding", _detect_tgc_banding),
    ("motion_blur", _detect_motion_blur),
    ("air_gap_reverberation", _detect_air_gap_reverberation),
    ("near_field_clutter", _detect_near_field_clutter),
    ("vessel_shadow_in_roi", _detect_vessel_shadow_in_roi),
    ("anisotropy_dropout_superficial", _detect_fascia_dropout),
])


def _detector_registry_self_check() -> None:
    """Import-time invariant: every registered detector keys a canonical § 5.1
    taxonomy code and is registered exactly once. A typo in a registration
    must fail loud at import, not at the first frame that exercises it."""
    seen: set[str] = set()
    for name, fn in _DETECTOR_REGISTRY:
        assert name in IMAGE_QUALITY_FLAG_PRIORITY, \
            f"detector {name!r} is not a canonical § 5.1 reason code"
        assert name not in seen, f"detector {name!r} registered twice"
        assert callable(fn), f"detector {name!r} is not callable"
        seen.add(name)


_detector_registry_self_check()

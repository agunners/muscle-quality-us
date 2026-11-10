"""RF QA — per-detector calibration spec (machine-readable mirror).

Source of truth for the per-detector calibration UI: what the operator is
asked, what they see while answering, how the score is revealed, and how the
label is fit. The human-readable contract is `docs/rf_qa_detector_calibration_spec.md`
(internal v0.2); this module mirrors its § 8 per-detector table machine-readably
and **loud-fails at import** on any malformed entry — the same discipline
`qa_thresholds._self_check()` enforces for the threshold registry.

Scope (Round 1, ratified via AskUserQuestion 2026-05-26): 7 detectors —
motion_blur, tgc_banding, air_gap_reverberation, near_field_clutter,
vessel_shadow_in_roi, dark_clip_high, segmentation_low_confidence. Excluded:
subq_fat_compressed (§ 5.2 L8 single-frame weakness) and saturation_high_*
(modality-inert per FW-25).

Three semantic anchors this spec encodes (Akadir 2026-05-28):
  (A) Different detectors need different visual context AND different questions —
      a wrong question poisons the label even when the operator answers honestly.
  (B) Surface + ask explicitly + record the right scales/metrics — the label
      snapshots the detector's FULL metadata dict, not just the scalar.
  (C) No detector is pre-judged into mechanism review — the data decides.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


SPEC_VERSION = "v0.1"   # the machine-spec content version; per-detector below


# --- the controlled vocabularies the self-check validates against ----------

# Layer tokens for the p4x cell's "slot 1" (the layered image). `raw` is
# always present (compose_layer_stack Layer 0). `segmentation_contour` renders
# ONLY the outline of `contour_classes` (no colour fill) — the caller builds
# that segmentation_arr; the fill would hide the very pixels several detectors
# need (motion_blur edge sharpness, dark_clip darkness). `segmentation_full`
# is the bundled colour+contour+annotations overlay (compose_layer_stack
# Layer 1 as-is). `confidence` / `change_map` are Layers 2 / 3.
VALID_LAYERS = frozenset({
    "raw", "segmentation_contour", "segmentation_full", "confidence", "change_map",
})

# Mask classes a `segmentation_contour` layer may outline. Lowercase tokens;
# the p4x renderer maps each to the LABEL_* constant in measurements.py.
VALID_CONTOUR_CLASSES = frozenset({
    "rf", "vi", "vl", "vm", "femur", "fat", "skin", "fascia",
})

# Auxiliary-widget callable names (slot 2). A typo here fails at import rather
# than silently rendering nothing at label time. `_aux_saturation_regional`
# is registered for the deferred saturation work even though no Round-1
# detector references it.
VALID_AUX_WIDGETS = frozenset({
    "_aux_motion_blur", "_aux_tgc_banding", "_aux_vessel_shadow",
    "_aux_saturation_regional",
})

VALID_SCORE_REVEAL = frozenset({"hide_before", "visualised", "never"})
VALID_FIT_OBJECTIVE = frozenset({"sens_target_at_spec", "youden_j"})
VALID_SELECTOR_STRATEGY = frozenset({"flag_fires", "percentile_spread", "score_only"})
VALID_TIER_B_STATUS = frozenset({"belted", "starting_point"})
VALID_DETECTOR_STATUS = frozenset({
    "pending_calibration", "calibrated_provisional", "calibrated_published",
    "under_mechanism_review", "retired",
})


@dataclass(frozen=True)
class CalibrationSpec:
    """The per-detector calibration contract (one § 8 card, machine-readable).

    `operator_question` is load-bearing (anchor A): it must point the operator
    at the real thing the detector measures, in plain language, with no raw
    detector code. `default_layers` + `contour_classes` encode the visual
    context (anchor A's visual half). `score_reveal` encodes anchor B's blind-
    by-default discipline. `fit_objective` + `sens_target_at_spec_floor` drive
    the fitter (subsystem F).
    """
    detector: str
    operator_question: str
    default_layers: tuple[str, ...]
    toggleable_layers: tuple[str, ...]
    contour_classes: tuple[str, ...]        # outlined when a contour layer is on
    auxiliary_widget: Optional[str]
    score_reveal: str
    selector_strategy: str
    fit_objective: str
    # (sensitivity_target, specificity_floor) when fit_objective is
    # 'sens_target_at_spec'; None for 'youden_j'.
    sens_target_at_spec_floor: Optional[tuple[float, float]]
    cohort_n_target: int
    tier_b_status: str
    detector_status: str
    spec_version: str


def _spec(**kw) -> CalibrationSpec:
    kw.setdefault("spec_version", SPEC_VERSION)
    kw.setdefault("cohort_n_target", 50)
    kw.setdefault("detector_status", "pending_calibration")
    return CalibrationSpec(**kw)


# ---------------------------------------------------------------------------
# The 7 Round-1 detectors (docs/rf_qa_detector_calibration_spec.md § 8).
# Operator-question wording is kept as drafted and validated live on the UI
# (O1 resolved 2026-05-28).
# ---------------------------------------------------------------------------

CALIBRATION_SPEC: dict[str, CalibrationSpec] = {
    # § 8.1
    "motion_blur": _spec(
        detector="motion_blur",
        operator_question="Does the fascia layer look crisp, or blurred / smeared?",
        default_layers=("raw", "segmentation_contour"),
        toggleable_layers=("segmentation_full", "confidence"),
        contour_classes=("fascia",),
        auxiliary_widget="_aux_motion_blur",
        score_reveal="hide_before",
        selector_strategy="percentile_spread",
        fit_objective="sens_target_at_spec",
        sens_target_at_spec_floor=(0.80, 0.85),
        tier_b_status="belted",
    ),
    # § 8.2
    "tgc_banding": _spec(
        detector="tgc_banding",
        operator_question=("Are there horizontal brightness bands that look "
                           "like gain mis-set across depth?"),
        default_layers=("raw", "segmentation_contour"),
        toggleable_layers=("segmentation_full",),
        contour_classes=("rf",),
        auxiliary_widget="_aux_tgc_banding",
        score_reveal="visualised",
        selector_strategy="percentile_spread",
        fit_objective="sens_target_at_spec",
        sens_target_at_spec_floor=(0.80, 0.85),
        tier_b_status="belted",
    ),
    # § 8.3
    "air_gap_reverberation": _spec(
        detector="air_gap_reverberation",
        operator_question=("Are there bright periodic horizontal fringes near "
                           "the skin surface?"),
        default_layers=("raw", "segmentation_contour"),
        toggleable_layers=("segmentation_full", "confidence"),
        contour_classes=("skin",),
        auxiliary_widget=None,
        score_reveal="hide_before",
        selector_strategy="percentile_spread",
        fit_objective="youden_j",
        sens_target_at_spec_floor=None,
        tier_b_status="belted",
    ),
    # § 8.4
    "near_field_clutter": _spec(
        detector="near_field_clutter",
        operator_question=("Is the layer above the muscle full of high-frequency "
                           "artefacts (not normal speckle)?"),
        default_layers=("raw", "segmentation_contour"),
        toggleable_layers=("segmentation_full",),
        contour_classes=("skin", "fat"),
        auxiliary_widget=None,
        score_reveal="hide_before",
        selector_strategy="percentile_spread",
        fit_objective="youden_j",
        sens_target_at_spec_floor=None,
        tier_b_status="belted",
    ),
    # § 8.5
    "vessel_shadow_in_roi": _spec(
        detector="vessel_shadow_in_roi",
        operator_question=("Is there a vertical dark column running through the "
                           "muscle that looks like a vessel shadow?"),
        default_layers=("raw", "segmentation_contour"),
        toggleable_layers=("segmentation_full",),
        contour_classes=("rf",),
        auxiliary_widget="_aux_vessel_shadow",
        score_reveal="hide_before",
        selector_strategy="percentile_spread",
        fit_objective="sens_target_at_spec",
        sens_target_at_spec_floor=(0.80, 0.85),
        tier_b_status="belted",
    ),
    # § 8.6
    "dark_clip_high": _spec(
        detector="dark_clip_high",
        operator_question=("Is a large part of the RF muscle region too dark to "
                           "read (clipped to black)?"),
        default_layers=("raw", "segmentation_contour"),
        toggleable_layers=("segmentation_full",),
        contour_classes=("rf",),
        auxiliary_widget=None,
        score_reveal="hide_before",
        selector_strategy="flag_fires",
        fit_objective="sens_target_at_spec",
        sens_target_at_spec_floor=(0.80, 0.85),
        tier_b_status="starting_point",
    ),
    # § 8.7
    "segmentation_low_confidence": _spec(
        detector="segmentation_low_confidence",
        operator_question=("Does the AI look unsure where the muscle edges are? "
                           "(Look at the confidence wash — red = unsure.)"),
        default_layers=("raw", "confidence"),
        toggleable_layers=("segmentation_contour",),
        contour_classes=("rf",),
        auxiliary_widget=None,
        score_reveal="visualised",
        selector_strategy="percentile_spread",
        fit_objective="youden_j",
        sens_target_at_spec_floor=None,
        tier_b_status="belted",
    ),
}


# Detectors deliberately out of Round-1 scope (§ 3) — recorded so a lookup of
# an excluded detector gives a clear reason rather than a bare KeyError.
OUT_OF_SCOPE: dict[str, str] = {
    "subq_fat_compressed": ("§ 5.2 L8 — single-frame compress-vs-anatomy "
                            "discrimination is structurally weak; real "
                            "disambiguator is sweep-level. Re-enter later."),
    "saturation_high_global": ("FW-25 — modality-inert on _final.png (caps at "
                               "244, below the 250 rail). Re-enter after D3."),
    "saturation_high_regional": ("FW-25 — modality-inert on _final.png. "
                                 "Re-enter after D3."),
}


def get(detector: str) -> CalibrationSpec:
    """Resolve a detector's calibration spec. Raises KeyError with a reason if
    the detector is out of Round-1 scope, or with the closest matches if it is
    an unknown name — a typo should fail loud, not silently."""
    if detector in CALIBRATION_SPEC:
        return CALIBRATION_SPEC[detector]
    if detector in OUT_OF_SCOPE:
        raise KeyError(
            f"{detector!r} is out of Round-1 scope: {OUT_OF_SCOPE[detector]}")
    near = sorted(k for k in CALIBRATION_SPEC if detector.split("_")[0] in k)
    raise KeyError(
        f"unknown detector {detector!r}; "
        f"{'did you mean ' + repr(near) + '?' if near else 'no near match'}")


def round1_detectors() -> tuple[str, ...]:
    """The 7 in-scope detector names, in spec order."""
    return tuple(CALIBRATION_SPEC.keys())


def _self_check() -> None:
    """Import-time invariants — a malformed spec must fail loudly (mirrors
    qa_thresholds._self_check)."""
    for name, s in CALIBRATION_SPEC.items():
        assert s.detector == name, f"{name}: detector field {s.detector!r} != key"
        # Operator question — anchor A: non-empty, plain, no raw detector code.
        assert s.operator_question.strip(), f"{name}: empty operator_question"
        assert name not in s.operator_question, (
            f"{name}: raw detector code leaked into operator_question — "
            f"the operator must never see the code name")
        assert s.operator_question.endswith("?") or s.operator_question.rstrip().endswith(")"), \
            f"{name}: operator_question should read as a question"
        # Layer tokens.
        assert "raw" in s.default_layers, f"{name}: default_layers must include 'raw'"
        for layer in (*s.default_layers, *s.toggleable_layers):
            assert layer in VALID_LAYERS, f"{name}: unknown layer {layer!r}"
        assert not (set(s.default_layers) & set(s.toggleable_layers)), \
            f"{name}: a layer is in both default and toggleable"
        # A contour layer requires contour_classes; the classes must be known.
        uses_contour = ("segmentation_contour" in s.default_layers
                        or "segmentation_contour" in s.toggleable_layers)
        if uses_contour:
            assert s.contour_classes, f"{name}: segmentation_contour needs contour_classes"
        for cls in s.contour_classes:
            assert cls in VALID_CONTOUR_CLASSES, f"{name}: unknown contour class {cls!r}"
        # Auxiliary widget — None or a registered name.
        assert s.auxiliary_widget is None or s.auxiliary_widget in VALID_AUX_WIDGETS, \
            f"{name}: unknown auxiliary_widget {s.auxiliary_widget!r}"
        # Score reveal / selector / fit / status vocabularies.
        assert s.score_reveal in VALID_SCORE_REVEAL, f"{name}: bad score_reveal {s.score_reveal!r}"
        assert s.selector_strategy in VALID_SELECTOR_STRATEGY, \
            f"{name}: bad selector_strategy {s.selector_strategy!r}"
        assert s.fit_objective in VALID_FIT_OBJECTIVE, f"{name}: bad fit_objective {s.fit_objective!r}"
        assert s.tier_b_status in VALID_TIER_B_STATUS, f"{name}: bad tier_b_status {s.tier_b_status!r}"
        assert s.detector_status in VALID_DETECTOR_STATUS, \
            f"{name}: bad detector_status {s.detector_status!r}"
        # Fit-objective ↔ sens-target coupling.
        if s.fit_objective == "sens_target_at_spec":
            assert s.sens_target_at_spec_floor is not None, \
                f"{name}: sens_target_at_spec needs (sens, spec) floor"
            sens, spec = s.sens_target_at_spec_floor
            assert 0.0 < sens <= 1.0 and 0.0 < spec <= 1.0, \
                f"{name}: sens/spec floor out of (0,1]: {s.sens_target_at_spec_floor}"
        else:
            assert s.sens_target_at_spec_floor is None, \
                f"{name}: youden_j must not carry a sens-target floor"
        assert s.cohort_n_target >= 1, f"{name}: cohort_n_target must be ≥ 1"
        # score_reveal='visualised' is reserved for detectors whose visual aid
        # IS the score (the heatmap / the residual plot). Those carry either a
        # confidence layer or an auxiliary widget.
        if s.score_reveal == "visualised":
            assert ("confidence" in s.default_layers) or (s.auxiliary_widget is not None), \
                (f"{name}: score_reveal='visualised' needs the score shown via a "
                 f"confidence layer or an auxiliary widget")


_self_check()

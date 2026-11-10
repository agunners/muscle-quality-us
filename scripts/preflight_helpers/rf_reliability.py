"""Staged RF-thickness triple-screen (Akadir 2026-07-12).

Which of the three views do we TRUST for RF thickness, and can we trust the RF
echo (int_cv / fascicular contrast) that rides on it?

  Anchor of trust = the FEMUR cine. At the deep FOV it sees the RF's full lower
  fascia, so the boundary is COMPLETE; its only caveat is coarser resolution
  (~1.4-2x). The CENTER-RF cine is FINER but, shot shallow (~4.9 cm), clips the
  lower fascia -> under-measures. The long SWEEP is a third, more-independent leg.

  Stage 1 (femur vs center-RF, on MAX thickness — most sensitive to a clip):
    agree within TOL -> center-RF is femur-VALIDATED -> USE the finer center-RF,
                        and TRUST its int_cv (the EI sits inside a good RF mask).
    disagree         -> Stage 2.
  Stage 2 (disagreement -> femur wins; int_cv NOT trusted; the sweep DIAGNOSES):
    sweep corroborates femur -> center-RF is the outlier (the shallow-clip case).
    sweep sides with center-RF -> both center shots differ from femur (weaker
                        evidence; sweep+center-RF share a center-shot bias).
    all three disagree -> lowest confidence.

int_cv (fascicular contrast) ALWAYS comes from the center-RF, but is only
trustworthy when the RF segmentation there is validated (Stage-1 agreement) —
otherwise we can't even say the echo was measured inside the real RF.

The AGREE_TOL is provisional: it should be set from the RF-thickness reproducibility
(its test-retest MDC), not a round number. Named here so it is one line to retune.
"""
from __future__ import annotations
from typing import Optional

AGREE_TOL = 0.10   # provisional; retune from the RF max-thickness MDC (reproducibility harness)


def _rel(a: float, b: float) -> float:
    m = max(abs(a), abs(b))
    return abs(a - b) / m if m > 0 else 0.0


def _agree(a: Optional[float], b: Optional[float], tol: float) -> bool:
    return a is not None and b is not None and _rel(a, b) <= tol


def resolve_rf_source(max_femur: Optional[float], max_center: Optional[float],
                      max_sweep: Optional[float], *, tol: float = AGREE_TOL) -> dict:
    """Stage the three RF MAX-thickness readings (mm) into ONE trusted source + int_cv trust.

    Returns {source, int_cv_trusted, reliability, spread, note}:
      source          'center_rf' | 'femur' | 'sweep'  — the caller reads THIS modality's
                      thickness feature (q50 for the score, max for display) for the value.
      int_cv_trusted  bool — is the center-RF echo (fascicular contrast) trustworthy?
      reliability     'high' | 'medium' | 'low' | 'legacy-sweep'
      spread          max-min across the present readings (mm), or None if <2 present.
      note            a human one-liner (drives the widget caption).
    """
    present = [v for v in (max_femur, max_center, max_sweep) if v is not None]
    spread = (max(present) - min(present)) if len(present) >= 2 else None

    if max_femur is None:                        # old-round / sweep-only: no femur to anchor on
        return {"source": "sweep", "int_cv_trusted": max_sweep is not None,
                "reliability": "legacy-sweep", "spread": spread,
                "note": "no femur cine — the sweep is the only anchor (legacy cohort)"}

    if _agree(max_femur, max_center, tol):       # Stage 1: center-RF validated by femur
        return {"source": "center_rf", "int_cv_trusted": True, "reliability": "high",
                "spread": spread,
                "note": (f"femur {max_femur:.1f} ≈ center-RF {max_center:.1f} mm (within "
                         f"{tol:.0%}) — center-RF validated, use the finer view; int_cv trusted")}

    # Stage 2: disagreement -> femur wins (complete boundary), int_cv suspect; sweep diagnoses.
    parts = ["center-RF diverges from femur"]
    if max_center is not None:
        parts.append("center-RF SMALLER — shallow-clip signature"
                     if max_center < max_femur else "center-RF larger — atypical fault")
    if _agree(max_sweep, max_femur, tol):
        parts.append("sweep corroborates femur (center-RF is the outlier)")
        reliability = "medium"
    elif _agree(max_sweep, max_center, tol):
        parts.append("sweep sides with center-RF (both center shots differ from femur)")
        reliability = "low"
    else:
        parts.append("all three disagree" if max_sweep is not None else "no sweep to arbitrate")
        reliability = "low"
    return {"source": "femur", "int_cv_trusted": False, "reliability": reliability,
            "spread": spread, "note": "; ".join(parts) + " — using femur (resolution caveat); int_cv NOT trusted"}


# --------------------------------------------------------------------------- #
# Texture (int_cv) audit gate — the p04 human-in-the-loop decision.
# RF THICKNESS is already settled by resolve_rf_source (femur on disagreement).
# This governs only the CENTER-RF TEXTURE (int_cv / fascicular contrast), which
# is measured INSIDE the center-RF boundary — so when the views disagree (the
# boundary is suspect) the texture is suspect too. The operator resolves it at the
# frame-review step (p04) with one of three choices, which map to two failure
# modes: 'keep'/'fix' = Mode A (boundary is real, model was right / redraw it),
# 'withhold' = Mode B (boundary not resolvable in the shallow view — don't invent).
# --------------------------------------------------------------------------- #
TEXTURE_CHOICES = ("keep", "fix", "withhold")            # operator's p04 options on a discrepancy
TEXTURE_STATES = ("verified", "pending_review", "reviewed_kept", "fixed", "withheld")


def resolve_texture_state(rf_decision: dict, user_choice: Optional[str] = None) -> dict:
    """How to treat the center-RF texture (int_cv), given the staged RF decision + the
    operator's p04 choice. Returns {state, int_cv_usable, needs_review, chip}:

      int_cv_usable  the SCORE feeds int_cv only when True; on 'withhold' it is dropped
                     and the score is computed texture-withheld (a missing axis, NOT a
                     faked one — the accurate handling of Mode B).
      needs_review   True only for an un-reviewed discrepancy -> the p04 gate fires.
      chip           the p05 audit-chip text reflecting the final state.

    user_choice in {None, 'keep', 'fix', 'withhold'} (None = un-reviewed).
    """
    # An EXPLICIT human 'withhold' ALWAYS suppresses — it is checked BEFORE int_cv_trusted so it can
    # never be resurrected by a recomputed agreement (the p04 gate and the p05 stamp can aggregate the
    # thickness slightly differently; a human's Mode-B judgement must win either way — red-team 2026-07-14).
    if user_choice == "withhold":                        # Mode B: not resolvable -> don't invent
        return {"state": "withheld", "int_cv_usable": False, "needs_review": False,
                "chip": "texture withheld · center-RF boundary not resolvable"}
    if rf_decision.get("int_cv_trusted"):                # femur validated center-RF -> sound
        return {"state": "verified", "int_cv_usable": True, "needs_review": False,
                "chip": "RF verified · three views agree"}
    if user_choice == "keep":                            # operator: center-RF boundary is fine
        return {"state": "reviewed_kept", "int_cv_usable": True, "needs_review": False,
                "chip": "RF reviewed · center-RF boundary accepted"}
    if user_choice == "fix":                             # Mode A: boundary redrawn -> recomputed
        return {"state": "fixed", "int_cv_usable": True, "needs_review": False,
                "chip": "RF boundary corrected (manual)"}
    return {"state": "pending_review", "int_cv_usable": True, "needs_review": True,
            "chip": "RF reduced confidence · views disagree — review"}


def rf_reliability_stamp(femur_agg: Optional[dict], rf_agg: Optional[dict],
                         sweep_agg: Optional[dict], user_choice: Optional[str] = None) -> dict:
    """The live `_rf_reliability` stamp: from the three per-modality aggregates' RF MAX thickness,
    the staged decision + the texture-audit state. Attached by route_study_aggregate; read by the
    p04 gate (`.texture.needs_review`) and the p05 chip (`.texture.chip`)."""
    mx = lambda a: (a or {}).get("full_max_thickness_mm")  # noqa: E731
    dec = resolve_rf_source(mx(femur_agg), mx(rf_agg), mx(sweep_agg))
    return {"decision": dec, "texture": resolve_texture_state(dec, user_choice=user_choice),
            # the raw inputs, for surfaces that show the NUMBERS behind the decision (the
            # trust strip's this-run level — Akadir 2026-08-14: "could we add the numbers here")
            "values": {"max_femur_mm": mx(femur_agg), "max_center_mm": mx(rf_agg),
                       "max_sweep_mm": mx(sweep_agg)}}

"""QMT provenance review for the frame-review stage (phase 4).

Computes each picked frame's QMT tier from its predicted mask so the operator
sees — *before* the assessment, while reselection is still cheap — whether the
selection can yield a femur-anchored QMT at all. Mask-only, no GPU.

The per-mask provenance comes from the labelling-app measurement code
(`labeling_app.measurements._qmt_measurement_details`); the tier ladder is the
shared run-side gate (`preflight_helpers.aggregation.qmt_tier`), so the review
stage and the assessment agree on what counts as a femur-anchored QMT.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from labeling_app.measurements import _qmt_measurement_details
from preflight_helpers.aggregation import qmt_tier


# tier -> (label, streamlit colour, one-line meaning)
_TIER = {
    1: ("Tier 1 · standard QMT", "green",
        "Femur-anchored fascia-to-femur measurement — fully valid QMT."),
    2: ("Tier 2 · proxy QMT", "orange",
        "Femur present but the geometry is too poor to anchor it — an RF+VI "
        "muscle-stack span, not a femur-anchored measurement. Fallback only."),
    3: ("Tier 3 · QMT not measurable", "red",
        "No usable femur (or a single-muscle path) — QMT cannot be measured "
        "on this frame."),
}


def frame_qmt_status(mask: np.ndarray) -> dict:
    """QMT tier + provenance for one predicted mask. Pure numpy, no GPU."""
    details = _qmt_measurement_details(mask)
    tier = qmt_tier(details)        # reads qmt_provenance + qmt_validity_flags
    label, colour, meaning = _TIER[tier]
    return {
        "tier": tier,
        "provenance": details.get("qmt_provenance"),
        "guidance": details.get("qmt_guidance_status"),
        "flags": list(details.get("qmt_validity_flags") or []),
        "label": label,
        "colour": colour,
        "meaning": meaning,
    }


def unpicked_femur_candidates(
    picked, scan: Optional[dict],
) -> list[dict]:
    """Tier-1 femur-anchored frames that exist in the sweep but are NOT in
    the picked set. Sorted by sweep order (sorted frame_base). The picker
    surfaces these as the "best candidate" recommendation when the operator
    has no femur-anchored frame in their picks but the sweep does.

    `picked` is the picked-frames container (frame_base strings).
    `scan` is the femur-scan dict (`{frame_base: result_dict}`), as stored
    in `st.session_state.femur_scan`; `None` or empty returns `[]`.
    """
    if not scan:
        return []
    picked_set = set(picked or [])
    out: list[dict] = []
    for fb, status in scan.items():
        if status.get("tier") != 1 or fb in picked_set:
            continue
        out.append({
            "frame_base": fb, "tier": 1,
            "provenance": status.get("provenance"),
        })
    out.sort(key=lambda r: r["frame_base"])
    return out


def selection_repr_status(
    picked, scan: Optional[dict],
) -> dict:
    """The repr-status verdict for the picker (Akadir 2026-05-25 § 3).

    Combines `selection_qmt_summary` (picked-set verdict) with the
    unpicked-candidates view to tell the operator — before they advance to
    review — whether a viable repr frame exists, and whether it is in the
    picked set.

    Four cases:
      `unscanned`          — no pre-scan has been run; we cannot say. The
                             surface nudges the operator to scan.
      `ok`                 — at least one picked frame is Tier-1
                             (femur-anchored); the AI will suggest one as
                             repr.
      `unpicked_candidate` — no Tier-1 in the picked set, but the sweep has
                             one or more Tier-1 candidates. The surface names
                             the best unpicked candidate and lets the operator
                             add it.
      `no_candidate`       — no Tier-1 in the picked set and none in the
                             sweep either. QMT for this run will be a
                             degraded (proxy) measurement; the operator
                             should know this before proceeding.

    Returns a rendering payload:
      `{case, message, level, best_unpicked, n_tier1_picked, n_tier1_sweep}`
    where `level` is one of `info|success|warning|error` (the Streamlit
    semantic, not a strict severity).
    """
    if not scan:
        return {
            "case": "unscanned",
            "message": (
                "Run **Pre-scan sweep** to check whether your picks include "
                "a femur-anchored repr candidate. Without the scan we can't "
                "tell whether the run will yield a valid QMT."),
            "level": "info",
            "best_unpicked": None,
            "n_tier1_picked": 0,
            "n_tier1_sweep": 0,
        }
    picked_set = set(picked or [])
    picked_statuses = [scan[fb] for fb in picked_set if fb in scan]
    summary = selection_qmt_summary(picked_statuses)
    n_tier1_picked = summary["n_tier1"]
    unpicked = unpicked_femur_candidates(picked_set, scan)
    n_tier1_sweep = n_tier1_picked + len(unpicked)

    if n_tier1_picked >= 1:
        msg = (f"Your picks include {n_tier1_picked} femur-anchored "
               f"frame(s) — the AI suggests one as repr (✦ on the grid).")
        return {
            "case": "ok", "message": msg, "level": "success",
            "best_unpicked": None,
            "n_tier1_picked": n_tier1_picked,
            "n_tier1_sweep": n_tier1_sweep,
        }
    if unpicked:
        best = unpicked[0]["frame_base"]
        msg = (f"**No femur-anchored frame in your picks.** The sweep has "
               f"{len(unpicked)} femur-anchored frame(s) — consider picking "
               f"one. Best candidate: `{best}`.")
        return {
            "case": "unpicked_candidate", "message": msg, "level": "warning",
            "best_unpicked": best,
            "n_tier1_picked": 0,
            "n_tier1_sweep": n_tier1_sweep,
        }
    return {
        "case": "no_candidate",
        "message": (
            "**No femur-anchored frame anywhere in this sweep.** QMT and "
            "STAR will be degraded (proxy) or unavailable for this study."),
        "level": "error",
        "best_unpicked": None,
        "n_tier1_picked": 0,
        "n_tier1_sweep": 0,
    }


def selection_qmt_summary(statuses: list[dict]) -> dict:
    """Aggregate per-frame QMT statuses into a selection-level verdict.

    Mirrors the run-side aggregation gate: the run's QMT needs >=1 Tier-1
    frame (-> standard), else >=1 Tier-2 frame (-> caveated proxy), else it is
    not measurable. `statuses` should be the frames that will (or might still)
    feed the analysis — i.e. not the ones already gated use_for_analysis=No.
    """
    n1 = sum(1 for s in statuses if s.get("tier") == 1)
    n2 = sum(1 for s in statuses if s.get("tier") == 2)
    n3 = sum(1 for s in statuses if s.get("tier") == 3)
    if n1:
        verdict, colour = "standard", "green"
        message = (f"{n1} femur-anchored (Tier-1) frame(s) selected — the run "
                   f"will have a standard QMT.")
    elif n2:
        verdict, colour = "proxy", "orange"
        message = (f"No Tier-1 frame selected. {n2} Tier-2 proxy frame(s) — the "
                   f"run's QMT will be a caveated proxy (an RF+VI span, not a "
                   f"femur-anchored measurement). If a femur-bearing frame "
                   f"exists in the sweep, reselect.")
    else:
        verdict, colour = "none", "red"
        message = ("No femur in any selected frame — the run will have NO QMT "
                   "and NO STAR (STAR = QMT / BMI). Reselect frames that show "
                   "the femur, or proceed knowing QMT and STAR will read "
                   "'not measurable'.")
    return {
        "verdict": verdict, "colour": colour, "message": message,
        "n_tier1": n1, "n_tier2": n2, "n_tier3": n3,
    }

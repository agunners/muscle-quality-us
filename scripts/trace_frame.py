#!/usr/bin/env python
"""Frame-trace CLI — Akadir 2026-05-24 v2 audit-deepening.

Run the full QA pipeline on one frame and dump every signal the operator
sees + every signal that feeds it. Surfaces semantic contradictions
BEFORE the operator does.

The discipline this enforces: I cannot click through the rendered Streamlit
UI, but I can run all the LOGIC the UI dispatches on a real frame and
inspect the result. If any semantic invariant fails (the picker dot
contradicts the femur tint; a Tier-1 high-confidence frame has an invalid
headline measurement; etc.) the trace surfaces it as `✗ FAIL`. The
embedded-testing ritual: build a feature → trace canonical frames → fix any
✗ → ship.

Usage:
    python scripts/trace_frame.py STUDY_CODE FRAME_BASE
    python scripts/trace_frame.py AGZ855 20260127_151034123
    python scripts/trace_frame.py AGZ855 20260127_151046404
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO))

import preflight_helpers.env_bootstrap  # noqa: F401  side-effect: rf_quality bridge

import numpy as np
import torch

from labeling_app.measurements import (
    LABEL_FAT, LABEL_FASCIA, LABEL_FEMUR,
    LABEL_RF, LABEL_SKIN, LABEL_VI, LABEL_VL, LABEL_VM,
    IMAGE_QUALITY_FLAG_PRIORITY, QMT_FEMUR_MIN_AREA_PX,
    _dominant_femur_mask, _qmt_measurement_details,
)
LABEL_BACKGROUND = 0
LABEL_SARTORIUS = 9
from labeling_app.image_quality import (
    _GATING_TABLE, GATING_MEASUREMENTS, INVALID,
    image_quality_aggregate_tier, image_quality_tier_per_measurement,
)
from labeling_app.visualization import confidence_stats
from labeling_app.assessment_pages.qmt_review import frame_qmt_status
from labeling_app.assessment_pages.qa_surfacing import (
    flag_prose, per_measurement_attribution, picker_qa_cue,
)
from labeling_app.assessment_pages.p03_frame_picker import _should_paint_femur_tint
from preflight_helpers import env
from preflight_helpers.feature_compute import compute_image_quality_features
from preflight_helpers.post_processing import _largest_component, post_process_rf
from preflight_helpers.segmenter import (
    BundleMeta, build_voi_hist_clahe_stack, load_learner_safe, predict_mask,
    synthesize_final_u8,
)
from preflight_helpers.study_discovery import discover_studies


_LABEL_NAMES = {
    LABEL_BACKGROUND: "background",
    LABEL_RF: "rf",
    LABEL_VI: "vi",
    LABEL_VL: "vl",
    LABEL_VM: "vm",
    LABEL_FASCIA: "fascia",
    LABEL_SKIN: "skin",
    LABEL_FEMUR: "femur",
    LABEL_FAT: "fat",
    LABEL_SARTORIUS: "sartorius",
}


# ---------------------------------------------------------------------------
# Trace functions — one section of the output each
# ---------------------------------------------------------------------------

def _header(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def _section(title: str) -> None:
    print()
    print(f"--- {title} " + "-" * (74 - len(title)))


def _row(label: str, value) -> None:
    print(f"  {label:<32} {value}")


def _trace_labels(pred: np.ndarray) -> dict:
    h, w = pred.shape
    total = h * w
    counts = {}
    _section(f"Label counts ({h}×{w} = {total} px)")
    for lid, name in _LABEL_NAMES.items():
        n = int((pred == lid).sum())
        if n > 0:
            counts[name] = n
            pct = 100.0 * n / total
            print(f"  {name:<12} {n:>8} ({pct:5.2f}%)")
    return counts


def _column_coverage(boolmask: np.ndarray) -> tuple[int, int, float]:
    """(#columns containing ≥1 True pixel, total columns, fraction)."""
    if boolmask.size == 0:
        return 0, 0, 0.0
    has_pix = boolmask.any(axis=0)
    n_cols = int(has_pix.sum())
    total = int(has_pix.size)
    return n_cols, total, n_cols / total if total else 0.0


def _bbox(boolmask: np.ndarray) -> tuple[int, int, int, int] | None:
    if not boolmask.any():
        return None
    rows = np.where(boolmask.any(axis=1))[0]
    cols = np.where(boolmask.any(axis=0))[0]
    return int(rows.min()), int(rows.max()), int(cols.min()), int(cols.max())


def _trace_rf_dominant(pred: np.ndarray) -> dict:
    _section("RF dominant blob (post_process_rf, largest-component + fill-holes)")
    rf_pair = post_process_rf(pred, fill_holes=True)
    cleaned_rf = rf_pair.rf_full_mask
    raw_rf = (pred == LABEL_RF)
    n_cols, total_cols, frac = _column_coverage(cleaned_rf)
    _row("size (cleaned)", f"{int(cleaned_rf.sum())} px "
                            f"(raw RF labelled: {int(raw_rf.sum())} px)")
    bbox = _bbox(cleaned_rf)
    if bbox:
        r0, r1, c0, c1 = bbox
        _row("bbox", f"rows {r0}-{r1}, cols {c0}-{c1}")
    _row("column coverage", f"{n_cols} / {total_cols} = {frac*100:5.1f}%")
    _row("touches_edge", str(rf_pair.full_touches_edge))
    return {
        "cleaned_rf_mask": cleaned_rf,
        "raw_rf_mask": raw_rf,
        "column_coverage": frac,
        "n_cols_with_rf": n_cols,
        "total_cols": total_cols,
        "rf_pair": rf_pair,
    }


def _trace_femur(pred: np.ndarray, qmt_tier: int) -> dict:
    _section("Femur dominant blob (_dominant_femur_mask — 8-conn + 5 px min)")
    raw_femur = (pred == LABEL_FEMUR)
    dom = _dominant_femur_mask(pred)
    _row("raw femur labels", f"{int(raw_femur.sum())} px")
    _row("dominant blob", f"{int(dom.sum())} px "
                          f"(threshold = {QMT_FEMUR_MIN_AREA_PX} px)")
    bbox = _bbox(dom)
    if bbox:
        r0, r1, c0, c1 = bbox
        _row("bbox", f"rows {r0}-{r1}, cols {c0}-{c1}")
    else:
        _row("bbox", "(empty — no femur or below threshold)")
    # The tint-paint decision unifies dot + tint via the QMT tier
    # (Akadir 2026-05-24 § 1). Dominant labels alone are not enough —
    # the operator can only verify QMT-usable femur via the tint.
    tint_gated_by_tier = _should_paint_femur_tint(qmt_tier)
    paints = bool(dom.any()) and tint_gated_by_tier
    _row("tier-gated paint", f"tier {qmt_tier} → "
                              f"{'paint allowed' if tint_gated_by_tier else 'NO paint'}")
    _row("would paint tint", "YES" if paints else "no")
    return {"dominant_femur_mask": dom, "raw_femur_mask": raw_femur,
            "would_paint_tint": paints}


def _trace_qmt(pred: np.ndarray) -> dict:
    _section("QMT pipeline (mask-only — _qmt_measurement_details + frame_qmt_status)")
    details = _qmt_measurement_details(pred)
    status = frame_qmt_status(pred)
    _row("qmt_provenance", str(details.get("qmt_provenance")))
    _row("qmt_validity_flags", str(details.get("qmt_validity_flags") or []))
    _row("qmt_tier", f"{status['tier']} ({status['label']})")
    _row("qmt_column", str(details.get("qmt_column")))
    _row("qmt_top_y", str(details.get("qmt_top_y")))
    _row("qmt_bottom_y", str(details.get("qmt_bottom_y")))
    return {"details": details, "status": status}


def _trace_detectors(iq: dict) -> dict:
    _section("Detectors (image_quality_validity_flags + per-detector metadata)")
    validity = iq.get("image_quality_validity_flags") or []
    pending = set(iq.get("image_quality_pending_calibration_flags") or [])
    metadata = iq.get("image_quality_detector_metadata") or {}
    calibrated = [f for f in validity if f not in pending]
    silent = [code for code in IMAGE_QUALITY_FLAG_PRIORITY if code not in validity]

    print("  Fired:")
    if not validity:
        print("    (none)")
    for code in IMAGE_QUALITY_FLAG_PRIORITY:
        if code not in validity:
            continue
        cal = "calibrated" if code not in pending else "pending"
        md = metadata.get(code) or {}
        # Pull a useful score if any.
        score_bits = []
        for k, v in md.items():
            if k in ("pending_calibration", "thresholds_used"):
                continue
            score_bits.append(f"{k}={v}")
        score = "; ".join(score_bits) if score_bits else "(no metadata)"
        print(f"    {code:<32} {cal:<10} {score}")

    print("  Silent:")
    if not silent:
        print("    (every detector fired)")
    else:
        # Show silent detectors compactly.
        for i in range(0, len(silent), 3):
            row = "  ".join(f"{c:<28}" for c in silent[i:i+3])
            print(f"    {row}")

    return {"validity": validity, "pending": pending,
            "calibrated": calibrated, "metadata": metadata}


def _trace_gating_table(iq: dict) -> dict:
    _section("Gating table consequences (per fired flag, per measurement)")
    validity = iq.get("image_quality_validity_flags") or []
    pending = set(iq.get("image_quality_pending_calibration_flags") or [])
    fired_calibrated = [f for f in validity if f not in pending]

    if not fired_calibrated:
        print("  (no calibrated flag fired)")
    for flag in fired_calibrated:
        row = _GATING_TABLE.get(flag, {})
        if not row:
            print(f"  {flag:<32} (reserve — taints nothing)")
            continue
        consequences = ", ".join(f"{m}={t}" for m, t in row.items())
        print(f"  {flag:<32} {consequences}")
    return {}


def _trace_tier_per_measurement(iq: dict) -> dict:
    _section("Per-measurement tier (image_quality_tier_per_measurement)")
    tiers = iq.get("image_quality_tier_per_measurement") or {}
    glyph = {"valid": "✓", "borderline": "⚠", "invalid": "✗",
             "ungraded": "—", "none": "·"}
    for m in GATING_MEASUREMENTS:
        t = tiers.get(m, "ungraded")
        _row(m, f"{glyph.get(t, '?')} {t}")
    return {"tiers": tiers}


def _trace_aggregate(iq: dict) -> dict:
    _section("Aggregate (image_quality_aggregate_tier + dominant reason)")
    tier = iq.get("image_quality_aggregate_tier")
    validity = iq.get("image_quality_validity_flags") or []
    pending = set(iq.get("image_quality_pending_calibration_flags") or [])
    calibrated = [f for f in validity if f not in pending]
    dom = calibrated[0] if calibrated else None
    _row("aggregate_tier", str(tier))
    _row("dominant calibrated", str(dom))
    _row("dominant prose", flag_prose(dom) if dom else "(none)")
    _row("contract_version", str(iq.get("image_quality_contract_version")))
    return {"tier": tier, "dominant": dom}


def _trace_ui_renders(iq: dict, qmt_status: dict, femur: dict,
                      rf: dict, confidence_map) -> dict:
    _section("What the operator would see — UI render dispatch")

    # Picker dot (from frame_qmt_status tier)
    dot_map = {1: "● femur",
               2: "● femur · proxy",
               3: "● no femur"}
    print("  Picker thumbnail:")
    _row("  dot", f"{dot_map.get(qmt_status['tier'], '?')} (tier {qmt_status['tier']})")
    _row("  yellow tint paints", "YES" if femur["would_paint_tint"] else "no")
    cue = picker_qa_cue(iq)
    _row("  cue (picker_qa_cue)", cue if cue else "(silent)")

    # p04 per-measurement attribution
    print("  Review (p04) per-measurement attribution:")
    rows = per_measurement_attribution(iq)
    any_shown = False
    for r in rows:
        if r["tier"] == "valid" or not r["drivers"]:
            continue
        any_shown = True
        drivers = "; ".join(d["prose"] for d in r["drivers"])
        print(f"    {r['measurement_label']:<24} {r['tier']:<11} ← {drivers}")
    if not any_shown:
        print("    (all valid)")

    # p04 confidence stat
    cs = confidence_stats(confidence_map) if confidence_map is not None else None
    if cs:
        print("  Review (p04) confidence stat:")
        _row("  mean confidence", f"{cs['mean']:.2f}")
        _row("  fraction below 0.70", f"{cs['fraction_below_0_70']*100:.0f}%")
    return {"picker_cue": cue, "would_paint_tint": femur["would_paint_tint"]}


# ---------------------------------------------------------------------------
# Semantic invariants — assert properties that no unit test catches
# ---------------------------------------------------------------------------

def _check_invariants(
    qmt_status: dict, femur: dict, rf: dict, iq: dict, ui: dict,
) -> list[tuple[str, bool, str]]:
    """Each invariant: (name, passed, message)."""
    out: list[tuple[str, bool, str]] = []

    # 1. Femur dot and yellow tint must agree.
    dot_femur = qmt_status["tier"] in (1, 2)
    tint = femur["would_paint_tint"]
    ok = (dot_femur == tint)
    msg = (f"dot says femur={dot_femur} (tier {qmt_status['tier']}), "
           f"tint paints={tint}")
    if not ok:
        msg += " — CONTRADICTION on the femur signal"
    out.append(("femur dot ↔ yellow tint agree", ok, msg))

    # 2. edge_clip_lateral is reserve state — taints nothing.
    row = _GATING_TABLE.get("edge_clip_lateral", {})
    ok = (row == {})
    msg = (f"_GATING_TABLE['edge_clip_lateral'] = {row} — "
           f"{'reserve (taints nothing) ✓' if ok else 'still invalidates — should be reserve'}")
    out.append(("edge_clip_lateral is reserve", ok, msg))

    # 3. If column coverage ≥ 0.5, wrong_site must be silent.
    coverage = rf["column_coverage"]
    wrong_site_fired = "wrong_site" in (iq.get("image_quality_validity_flags") or [])
    if coverage >= 0.5:
        ok = not wrong_site_fired
        msg = (f"RF column coverage = {coverage*100:.1f}% (≥50%); "
               f"wrong_site fired = {wrong_site_fired}")
        if not ok:
            msg += " — should be silent on side-shot / centred RF"
        out.append(("wrong_site silent on column_coverage ≥ 0.5", ok, msg))

    # 4. Tier-1 + high confidence + central femur → no headline INV.
    tier = qmt_status["tier"]
    qmt_flags = qmt_status.get("flags") or []
    central = "outside_central_zone" not in qmt_flags
    if tier == 1 and central:
        tiers = iq.get("image_quality_tier_per_measurement") or {}
        headlines = ("rf_thickness_mm", "qmt_mm", "star_ratio",
                     "full_thickness_q50_mm")
        bad = [m for m in headlines if tiers.get(m) == "invalid"]
        ok = not bad
        msg = (f"QMT tier 1 + central; invalid headlines = {bad or 'none'}")
        if not ok:
            msg += " — a clean Tier-1 frame should not have invalid headlines"
        out.append(("Tier-1 + central → no INV headline", ok, msg))

    # 5. Picker cue ↔ no-femur dot consistency.
    cue = ui.get("picker_cue")
    if qmt_status["tier"] == 3 and cue and "side of the image" in cue:
        # Edge_clip_lateral cue surfacing on a tier-3 frame — that's the
        # exact failure mode of screenshot § 1.
        out.append(("picker cue not 'side of image' on no-femur frame",
                    False,
                    "tier 3 + cue surfaces edge_clip_lateral — operator gets "
                    "two unrelated signals on the same thumbnail"))
    elif qmt_status["tier"] == 3:
        out.append(("picker cue not 'side of image' on no-femur frame",
                    True, f"tier 3, cue = {cue or '(silent)'}"))

    return out


def _print_invariants(checks: list[tuple[str, bool, str]]) -> int:
    _section("SEMANTIC INVARIANTS")
    n_fail = 0
    for name, ok, msg in checks:
        glyph = "✓" if ok else "✗ FAIL"
        print(f"  [{glyph}] {name}")
        print(f"        {msg}")
        if not ok:
            n_fail += 1
    print()
    if n_fail:
        print(f"  → {n_fail} invariant(s) FAILED. Fix before claiming done.")
    else:
        print(f"  → all {len(checks)} invariants pass.")
    return n_fail


# ---------------------------------------------------------------------------
# Pipeline glue — find study, load model, synthesize, predict
# ---------------------------------------------------------------------------

def _resolve_study(study_code: str):
    entries = discover_studies()
    matches = [e for e in entries if e.meta and e.meta.study_code == study_code]
    if not matches:
        raise SystemExit(f"No study found with study_code = {study_code!r}")
    if len(matches) > 1:
        print(f"WARN  {len(matches)} matches for {study_code!r}; using the first.")
    e = matches[0]
    if e.raw_image_dir is None:
        raise SystemExit(f"Study {study_code!r} has no RawImagesForResearch_ dir")
    return e


def _find_bundle() -> Path:
    # The pinned canonical bundle (configs/canonical_bundle.json), not newest-mtime.
    from preflight_helpers.model_registry import resolve_canonical_pkl
    p = resolve_canonical_pkl()
    if p is None:
        raise SystemExit(f"No canonical bundle resolvable (pointer + {env.MODEL_DIR_LOCAL})")
    return p


def trace(study_code: str, frame_base: str) -> int:
    entry = _resolve_study(study_code)
    bundle_pkl = _find_bundle()
    bm = BundleMeta.from_pkl(bundle_pkl)

    _header(f"Frame trace — {study_code} / {frame_base}")
    _row("study path", str(entry.study_path))
    _row("raw image dir", str(entry.raw_image_dir))
    _row("model bundle", bundle_pkl.name)
    _row("modalities", ", ".join(bm.modalities))

    # Synthesize + segment.
    final_u8 = synthesize_final_u8(entry.raw_image_dir, frame_base)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    learn = load_learner_safe(bundle_pkl)
    stacked = build_voi_hist_clahe_stack(final_u8, modalities=bm.modalities)
    conf_buf: dict = {}
    pred = predict_mask(learn, stacked, bm, device=device, confidence_out=conf_buf)
    confidence_map = conf_buf.get("confidence_map_native")

    _row("mask shape", f"{pred.shape[0]} × {pred.shape[1]}")
    _row("final_u8 (mean, p10, p99)",
         f"{final_u8.mean():.1f}, "
         f"{np.percentile(final_u8, 10):.0f}, "
         f"{np.percentile(final_u8, 99):.0f}")

    # Analyses (each prints its section).
    _trace_labels(pred)
    rf = _trace_rf_dominant(pred)
    qmt = _trace_qmt(pred)
    femur = _trace_femur(pred, qmt["status"]["tier"])

    iq = compute_image_quality_features(
        frame_base=frame_base, multiclass_mask=pred,
        final_image_u8=final_u8, spacing_row={},
        qmt_provenance=qmt["status"].get("provenance"),
        boundary_entropy=None,
    ).to_dict()

    _trace_detectors(iq)
    _trace_gating_table(iq)
    _trace_tier_per_measurement(iq)
    _trace_aggregate(iq)
    ui = _trace_ui_renders(iq, qmt["status"], femur, rf, confidence_map)

    # The killer feature: semantic invariants.
    n_fail = _print_invariants(
        _check_invariants(qmt["status"], femur, rf, iq, ui))
    print()
    return 1 if n_fail else 0


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("study_code", help="e.g. AGZ855")
    ap.add_argument("frame_base", help="e.g. 20260127_151034123")
    args = ap.parse_args()
    return trace(args.study_code, args.frame_base)


if __name__ == "__main__":
    sys.exit(main())

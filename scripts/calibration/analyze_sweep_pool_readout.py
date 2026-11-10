"""PROBE readout — per-detector candidate-availability over the sweep pool.

`docs/rf_qa_next_phase_plan.md` Step 1 deliverable. Consumes the probe score
table + frame manifest (written by `build_sweep_pool_score_table.py`) and emits,
per detector, the three things the plan asks for:

  * the score DISTRIBUTION over the pool, on SEGMENTABLE frames;
  * the estimated CANDIDATE-TAIL size — frames scoring where the failure mode
    is plausibly present;
  * the SEGMENTER-USABLE fraction — how many frames the detector could score
    at all (the chicken-and-egg bound, plan risk #2).

...plus a provisional GREEN / AMBER / RED go/no-go per Round-1 detector.

WHY candidate-tail estimation is detector-dependent (this is the load-bearing
honesty of the readout): 6 of the 7 Round-1 detectors are NaN-belted — no
calibrated firing threshold exists — so a "# fires" count is meaningless for
them (a NaN comparison never fires). For those, the candidate tail is estimated
by a ROBUST-OUTLIER rule in the detector's failure direction: the Iglewicz-
Hoaglin modified Z-score |0.6745·(x−median)/MAD| > 3.5 (the same scale-free
standard `frame_rf_consistency` already uses). That counts frames whose score
is a far outlier toward the failure mode — a principled "plausible failure
population" proxy WITHOUT inventing a threshold. For the one finite-threshold
Round-1 detector (`dark_clip_high`) the candidate tail is the actual fire count
at its starting cut. Every belted call is marked `provisional` — the true
positive count is unknowable until the operator campaign hypothesises a cut;
the probe can only show whether a separable tail plausibly exists.

GREEN/AMBER/RED follow the plan's heuristics, stated explicitly in the output.

Run:  .venv/bin/python scripts/calibration/analyze_sweep_pool_readout.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))
import preflight_helpers.env_bootstrap  # noqa: E402,F401  side-effect: rf_quality bridge
from labeling_app.qa_thresholds import THRESHOLDS  # noqa: E402

_OUTDIR = _REPO / "data" / "cohort_stats"
_PARQUET = _OUTDIR / "qa_per_frame_detector_scores__sweep_pool_5fps.parquet"
_MANIFEST = _OUTDIR / "qa_sweep_pool_frame_manifest.parquet"
_OUT_JSON = _OUTDIR / "qa_sweep_pool_readout.json"
_OUT_MD = _OUTDIR / "qa_sweep_pool_readout.md"

# (score_key, fire_when, threshold_registry_name) — re-derived from
# labeling_app/image_quality.py (the fire comparison) + qa_thresholds.py.
_DET = {
    "motion_blur":                 ("fascia_band_tenengrad",        "low",  "motion_blur__fascia_band_tenengrad_min"),
    "tgc_banding":                 ("detrended_residual_sigma",     "high", "tgc_banding__detrended_residual_sigma_max"),
    "air_gap_reverberation":       ("comb_peak_magnitude",          "high", "air_gap_reverberation__comb_peak_magnitude_min"),
    "near_field_clutter":          ("clutter_energy",               "high", "near_field_clutter__clutter_energy_max"),
    "vessel_shadow_in_roi":        ("darkest_column_mean",          "low",  "vessel_shadow_in_roi__column_darkness_max"),
    "dark_clip_high":              ("roi_dark_fraction",            "high", "dark_clip_high__roi_fraction_max"),
    "segmentation_low_confidence": ("boundary_entropy",             "high", "segmentation_low_confidence__boundary_entropy_max"),
    "saturation_high_global":      ("roi_saturated_fraction",       "high", "saturation_high_global__roi_fraction_max"),
    "saturation_high_regional":    ("worst_block_saturated_fraction", "high", "saturation_high_regional__block_fraction_max"),
    "subq_fat_compressed":         ("central_fat_thickness_mm",     "low",  "subq_fat_compressed__central_fat_thickness_mm_min"),
    "wrong_site":                  ("rf_column_coverage",           "low",  "wrong_site__rf_column_coverage_min"),
}
_ROUND1 = ("motion_blur", "tgc_banding", "air_gap_reverberation", "near_field_clutter",
           "vessel_shadow_in_roi", "dark_clip_high", "segmentation_low_confidence")

# Plan heuristic constants (docs/rf_qa_next_phase_plan.md Step-1 go/no-go).
_GREEN_TAIL = 150     # "a clear candidate tail (>=150-200 gradeable frames)"
_AMBER_TAIL = 20      # below this the tail is too thin / clustered clean -> RED
_MIN_USABLE = 50      # campaign needs >=50 positives; below this even a perfect tail can't reach it
_MOD_Z = 3.5          # Iglewicz-Hoaglin modified-Z outlier cut (matches frame_rf_consistency)
_GREEN_STUDIES = 50   # >=50 INDEPENDENT positives ~= studies at 5 fps; a tail in fewer can't reach it
_AMBER_STUDIES = 10   # below this the separable tail is too study-concentrated to calibrate


def _pcts(x: np.ndarray) -> dict:
    q = [0, 1, 5, 10, 25, 50, 75, 90, 95, 99, 100]
    v = np.percentile(x, q)
    return {f"p{p}": round(float(val), 5) for p, val in zip(q, v)}


def _tail(x: np.ndarray, direction: str) -> dict:
    """Tail counts at increasing modified-Z cuts + a SEPARABILITY verdict.

    The crux for a belted detector: a `modified-Z>3.5` count alone cannot tell a
    SEPARABLE failure population (a distinct cluster far from the bulk — real
    failures) from the HEAVY TAIL of a unimodal clean distribution (the score
    just has fat tails; no failure sub-population). 18k frames throw dozens of
    z>3.5 "outliers" from distribution shape alone. The discriminator is whether
    the tail PERSISTS far out: a separable population still has frames at z>8/12;
    a heavy tail has decayed to ~0 by then. Verdict:
      * no_tail                  — nothing even at z>3.5 (maxZ<3.5): starved.
      * heavy_tail_not_separable — z>3.5 outliers exist but decay by z>8: the
                                   "candidates" are distribution shape, NOT a
                                   failure population — effectively starved.
      * separable_population     — the tail persists (>=20 frames at z>8): a real
                                   far cluster worth labeling (still PROVISIONAL —
                                   separable != confirmed the named failure mode).
    """
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med)))
    _, cnts = np.unique(np.round(x, 6), return_counts=True)
    mode_frac = float(cnts.max() / len(x)) if len(x) else 0.0
    spike = bool(mode_frac > 0.25)          # a value-spike shrinks MAD -> false separability
    if mad <= 1e-12:
        return {"z3_5": 0, "z5": 0, "z8": 0, "z12": 0, "max_z": 0.0,
                "verdict": "no_tail", "mode_frac": round(mode_frac, 3), "spike": spike}
    zz = 0.6745 * (x - med) / mad
    zz = zz if direction == "high" else -zz
    c = lambda t: int((zz > t).sum())
    z8 = c(8.0)
    verdict = ("no_tail" if c(_MOD_Z) == 0
               else "separable_population" if z8 >= 20
               else "heavy_tail_not_separable")
    return {"z3_5": c(_MOD_Z), "z5": c(5.0), "z8": z8, "z12": c(12.0),
            "max_z": round(float(zz.max()), 1), "verdict": verdict,
            "mode_frac": round(mode_frac, 3), "spike": spike}


def _call(usable: int, *, finite: bool, fires: int = 0, n_studies: int = 0,
          tail: dict | None = None, flat: bool = False) -> str:
    """GREEN/AMBER/RED, STUDY-CLUSTERING aware. At 5 fps the independent unit is
    the study, so the campaign's >=50-independent-positive target needs the tail
    spread across enough STUDIES, not just frames. Belted detectors must also
    clear separability and not be mask-confounded (collapsed-mask segmenter
    failure) or spike-driven (tiny-MAD artifact)."""
    if usable < _MIN_USABLE:
        return "RED"
    if finite:
        if flat or fires < _AMBER_TAIL:
            return "RED"
        if fires >= _GREEN_TAIL and n_studies >= _GREEN_STUDIES and usable >= 200:
            return "GREEN"
        return "AMBER" if n_studies >= _AMBER_STUDIES else "RED"
    v = (tail or {}).get("verdict")
    if v in (None, "no_tail"):
        return "RED"
    if (tail or {}).get("confounded"):
        return "RED"                         # tail is collapsed-mask segmenter failure, not a failure mode
    if v == "heavy_tail_not_separable":
        return "AMBER" if n_studies >= _AMBER_STUDIES else "RED"
    if (tail or {}).get("spike"):
        return "AMBER"                       # separability may be a tiny-MAD artifact
    if n_studies >= _GREEN_STUDIES and usable >= 200:
        return "GREEN"
    return "AMBER" if n_studies >= _AMBER_STUDIES else "RED"   # separable but study-starved


def analyze(parquet: Path, manifest: Path) -> dict:
    df = pd.read_parquet(parquet)
    man = pd.read_parquet(manifest) if manifest.exists() else pd.DataFrame()
    graded = man[man.get("status") == "graded"] if "status" in man.columns else man
    n_graded = int(len(graded))
    has_mask = bool(n_graded) and "fg_frac" in graded.columns and "rf_px" in graded.columns
    cohort_fg_median = float(graded["fg_frac"].median()) if has_mask else None
    fg_by_key = (graded.set_index(["study_code", "frame_base"])["fg_frac"].to_dict()
                 if has_mask else {})
    cohort_label = ("quality_failed (47 rejected)" if "_qf" in Path(parquet).name
                    else "completed (272 alive)")

    skip_rows = (man[man.get("status").isin(["skipped", "skipped_study"])]
                 if "status" in getattr(man, "columns", []) else pd.DataFrame())
    out = {
        "cohort_label": cohort_label,
        "score_parquet": str(parquet),
        "manifest_parquet": str(manifest),
        "n_studies": int(df["study_code"].nunique()) if not df.empty else 0,
        "n_frames_graded": n_graded,
        "n_frames_skipped": int(len(skip_rows)),
        "n_frames_degenerate_mask": int(graded["degenerate"].sum()) if n_graded and "degenerate" in graded else 0,
        "mask_quality": {
            "cohort_fg_frac_median": round(cohort_fg_median, 3) if cohort_fg_median is not None else None,
            "n_rf_px_zero": int((graded["rf_px"] == 0).sum()) if has_mask else None,
            "n_femur_px_zero": int((graded["femur_px"] == 0).sum()) if (n_graded and "femur_px" in graded) else None,
            "note": "degenerate=fg_frac<0.01 is a near-empty-mask floor; it does NOT catch a confidently-"
                    "WRONG full mask. rf_px==0 / femur_px==0 frames grade as non-degenerate but the RF-ROI "
                    "detectors return NaN on them — the segmenter chicken-and-egg (risk R2) surfaces here, "
                    "not in the degenerate count.",
        },
        "code_sig": str(df["code_sig"].iloc[0]) if not df.empty else None,
        "modality": str(df["modality"].iloc[0]) if not df.empty else None,
        "mask_source": str(df["mask_source"].iloc[0]) if "mask_source" in df.columns and not df.empty else None,
        "heuristic": {
            "min_usable": _MIN_USABLE, "modified_z_cut": _MOD_Z,
            "green_studies_min": _GREEN_STUDIES, "amber_studies_min": _AMBER_STUDIES,
            "note": "Belted (NaN-threshold) detectors: candidate_tail is a modified-Z>3.5 robust-outlier "
                    "count, but the CALL uses tail SEPARABILITY (persistence to z>8) + the distinct-STUDY "
                    "count (>=50 independent positives ~= studies at 5 fps) + a mask-CONFOUND check (a tail "
                    "on collapsed masks is segmenter failure, not a failure mode) + a SPIKE guard. "
                    "dark_clip_high uses its finite 0.02 fire count + study spread. Every belted call is "
                    "PROVISIONAL until operator labels confirm the named mode.",
        },
        "detectors": {},
    }

    def _studies(frame_df):
        return int(frame_df["study_code"].nunique()) if len(frame_df) else 0

    def _top(frame_df):
        vc = frame_df["study_code"].value_counts()
        return int(vc.iloc[0]) if len(vc) else 0

    for det, (key, direction, thr_name) in _DET.items():
        sub = df[df.detector == det]
        valid_mask = sub["raw_score"].notna()
        valid = sub.loc[valid_mask, "raw_score"].to_numpy(dtype=float)
        n_valid = int(valid.size)
        entry = THRESHOLDS.get(thr_name)
        thr_val = entry.value if entry else None
        thr_belted = bool(entry and isinstance(thr_val, float) and np.isnan(thr_val))
        rec = {
            "score_key": key, "fire_direction": direction,
            "threshold_name": thr_name,
            "threshold_value": None if thr_belted else (None if thr_val is None else float(thr_val)),
            "threshold_tier": entry.tier if entry else None,
            "belted": thr_belted,
            "round1": det in _ROUND1,
            "n_valid_scores": n_valid,
            "n_graded_frames": n_graded,
            "detector_scoreable_fraction": round(n_valid / n_graded, 4) if n_graded else 0.0,
            "segmenter_usable_fraction": round(n_valid / n_graded, 4) if n_graded else 0.0,
            "n_fires_at_starting_threshold": int(sub["fires"].sum()),
        }
        if n_valid:
            rec["distribution"] = _pcts(valid)
            rec["mean"] = round(float(valid.mean()), 5)
            rec["std"] = round(float(valid.std()), 5)
            spread = float(np.percentile(valid, 95) - np.percentile(valid, 5))
            rec["p95_minus_p5"] = round(spread, 5)
            flat = spread <= 1e-9
            rec["flat_no_dynamic_range"] = bool(flat)
            if not thr_belted and thr_val is not None:
                fired_df = sub[sub["fires"] == True]   # noqa: E712
                fires = int(len(fired_df))
                n_studies = _studies(fired_df)
                rec["candidate_tail"] = fires
                rec["n_studies_in_tail"] = n_studies
                rec["top_study_frame_count"] = _top(fired_df)
                rec["candidate_tail_basis"] = (f"fires at the finite starting cut {thr_val} ({direction}); "
                                               f"{fires} frames across {n_studies} studies")
                rec["go_no_go"] = _call(n_valid, finite=True, fires=fires, n_studies=n_studies, flat=flat)
            else:
                ti = _tail(valid, direction)
                med = float(np.median(valid)); mad = float(np.median(np.abs(valid - med)))
                vdf = sub.loc[valid_mask, ["study_code", "frame_base", "raw_score"]].copy()
                if mad > 1e-12:
                    z = 0.6745 * (vdf["raw_score"] - med) / mad
                    vdf["_z"] = z if direction == "high" else -z
                else:
                    vdf["_z"] = 0.0
                tail8 = vdf[vdf["_z"] > 8.0]
                n_studies = _studies(tail8)
                tfg = ([fg_by_key.get((sc, fb)) for sc, fb in zip(tail8["study_code"], tail8["frame_base"])]
                       if has_mask else [])
                tfg = [v for v in tfg if v is not None]
                tail_fg = float(np.median(tfg)) if tfg else None
                ti["n_studies_in_tail"] = n_studies
                ti["top_study_frame_count"] = _top(tail8)
                ti["tail_fg_frac_median"] = round(tail_fg, 3) if tail_fg is not None else None
                ti["confounded"] = bool(tail_fg is not None and cohort_fg_median
                                        and tail_fg < 0.5 * cohort_fg_median)
                rec["tail_persistence"] = ti
                rec["separability_verdict"] = ti["verdict"]
                rec["candidate_tail"] = ti["z3_5"]
                rec["n_studies_in_tail"] = n_studies
                rec["candidate_tail_basis"] = (
                    f"modified-Z robust-outlier tail ({direction}); separability={ti['verdict']}, z>8={ti['z8']} "
                    f"across {n_studies} studies (top study {ti['top_study_frame_count']} frames), tail fg_frac="
                    f"{ti['tail_fg_frac_median']} vs cohort {out['mask_quality']['cohort_fg_frac_median']}"
                    f"{' — MASK-CONFOUNDED' if ti['confounded'] else ''}{' — SPIKE' if ti.get('spike') else ''}. "
                    f"PROVISIONAL — separable tail is necessary, not sufficient.")
                rec["go_no_go"] = _call(n_valid, finite=False, n_studies=n_studies, tail=ti, flat=flat)
            rec["go_no_go_provisional"] = bool(thr_belted)
        else:
            rec["distribution"] = None
            rec["candidate_tail"] = 0
            rec["n_studies_in_tail"] = 0
            rec["go_no_go"] = "RED"
            rec["go_no_go_basis"] = "no segmentable/scoreable frames"
        out["detectors"][det] = rec

    if not skip_rows.empty and "skip_error" in skip_rows.columns:
        reasons = skip_rows["skip_error"].fillna("?").apply(lambda s: str(s).split(":")[0]).value_counts()
        out["skip_reasons"] = {str(k): int(v) for k, v in reasons.items()}
    return out


def _markdown(out: dict) -> str:
    mq = out.get("mask_quality", {})
    L = []
    L.append(f"# RF QA probe — sweep-pool candidate-availability readout ({out.get('cohort_label','')})\n")
    L.append(f"- studies: **{out['n_studies']}** | frames graded: **{out['n_frames_graded']}** "
             f"| skipped: **{out['n_frames_skipped']}** | degenerate masks: **{out['n_frames_degenerate_mask']}**")
    L.append(f"- mask quality: cohort fg_frac median **{mq.get('cohort_fg_frac_median')}** | "
             f"rf_px==0 on **{mq.get('n_rf_px_zero')}** frames | femur_px==0 on **{mq.get('n_femur_px_zero')}** — "
             f"these grade as non-degenerate but RF-ROI detectors NaN on them (the real R2 chicken-and-egg surface)")
    L.append(f"- modality: `{out['modality']}` | mask_source: `{out['mask_source']}` | code_sig: `{out['code_sig']}`")
    L.append(f"- CALL logic: belted → separability (persist to z>8) + distinct-study count "
             f"(GREEN ≥{_GREEN_STUDIES} studies, AMBER ≥{_AMBER_STUDIES}, else RED) + mask-confound + spike guards; "
             f"finite (dark_clip) → fire count + study count. ≥50 independent positives ≈ studies at 5 fps.\n")
    L.append("| detector | R1 | belted | scoreable | cand.tail | studies | call | median | p5–p95 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    order = list(_ROUND1) + [d for d in _DET if d not in _ROUND1]
    for det in order:
        r = out["detectors"][det]
        dist = r.get("distribution") or {}
        med = dist.get("p50", "—")
        rng = f"{dist.get('p5','—')}–{dist.get('p95','—')}" if dist else "—"
        call = r["go_no_go"] + ("*" if r.get("go_no_go_provisional") else "")
        L.append(f"| {det} | {'✓' if r['round1'] else ''} | {'NaN' if r['belted'] else 'fin'} | "
                 f"{r.get('detector_scoreable_fraction', r.get('segmenter_usable_fraction'))} | {r['candidate_tail']} | "
                 f"{r.get('n_studies_in_tail','—')} | **{call}** | {med} | {rng} |")
    L.append("\n`*` = provisional (NaN-belted — no calibrated cut). 'cand.tail' is the modified-Z>3.5 frame count; "
             "the CALL uses separability + study spread + mask-health below.\n")
    L.append("## Belted-detector tail separability + study clustering + mask health\n")
    L.append("Does the z>3.5 tail PERSIST far out (separable cluster) or DECAY by z>8 (distribution shape)? Is it "
             "spread across enough STUDIES to yield ≥50 independent positives? Does it sit on HEALTHY masks or "
             "collapsed (segmenter-failure) ones?\n")
    L.append("| detector | maxZ | z>3.5 | z>8 | z>12 | studies (top) | tail fg_frac | verdict | flags |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for det in _ROUND1:
        tp = out["detectors"][det].get("tail_persistence")
        if tp:
            flags = []
            if tp.get("confounded"):
                flags.append("MASK-CONFOUNDED")
            if tp.get("spike"):
                flags.append("SPIKE")
            L.append(f"| {det} | {tp['max_z']} | {tp['z3_5']} | {tp['z8']} | {tp['z12']} | "
                     f"{tp.get('n_studies_in_tail','—')} ({tp.get('top_study_frame_count','—')}) | "
                     f"{tp.get('tail_fg_frac_median','—')} | **{tp['verdict']}** | {', '.join(flags) or '—'} |")
    L.append("")
    if out.get("skip_reasons"):
        L.append(f"- skip reasons: {out['skip_reasons']}")
    return "\n".join(L)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--parquet", type=Path, default=_PARQUET)
    ap.add_argument("--manifest", type=Path, default=_MANIFEST)
    ap.add_argument("--out-tag", default="", help="suffix the readout json/md (e.g. 'qf')")
    args = ap.parse_args()
    if not args.parquet.exists():
        raise SystemExit(f"probe score table not found: {args.parquet} — run build_sweep_pool_score_table.py first")
    out = analyze(args.parquet, args.manifest)
    sfx = f"_{args.out_tag}" if args.out_tag else ""
    out_json = _OUTDIR / f"qa_sweep_pool_readout{sfx}.json"
    out_md = _OUTDIR / f"qa_sweep_pool_readout{sfx}.md"
    out_json.write_text(json.dumps(out, indent=2))
    md = _markdown(out)
    out_md.write_text(md)
    print(md)
    print(f"\n-> {out_json}\n-> {out_md}")


if __name__ == "__main__":
    main()

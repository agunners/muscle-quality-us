"""PROBE substrate — predicted-mask QA score table over the FULL SWEEP POOL.

`docs/rf_qa_next_phase_plan.md` Step 1 (the go/no-go probe). Where
`build_predicted_score_table.py` reads the 956 transport frames (the clean
~3/study annotator-selected pool), this enumerates the FULL sweeps of the 272
ALIVE studies, subsamples to 5 fps, synthesizes the deployed `_final` modality
per frame, segments, and grades all 15 QA detectors on the PREDICTED masks
(writing score rows for the 11 detectors with a continuous scalar in _SCORE_KEY;
the 4 structural femur/edge detectors fire booleans with no calibratable scalar,
so they are graded but do not appear in the score table — 11 rows/frame, not 15).

The skip rate AND the per-frame mask-quality manifest are first-class results:
they bound gradeability — the segmenter-on-bad-frames chicken-and-egg (plan
risk #2). A failure-mode frame the segmenter cannot mask produces no usable
score, shrinking the calibratable pool below the campaign's positive target.

COHORT   operator_id=='akadir' AND decision_status=='completed' (272 distinct
         study_code; 274 rows − 2 re-saves) from
         `exports/decision_log_full_3operators.csv`.
FRAMES   goruntuler_backup/<relative_frame_dir>/RawImagesForResearch_*/<fb>.jpg
         (`configs/study_codes.csv` gives relative_frame_dir). NOTE: the
         `data/studies_root` symlinks point at the cleaned-out `goruntuler`
         and reach only 4/272 studies — the full corpus is in
         `goruntuler_backup` (probe finding, 2026-05-29). 5 fps = >=200 ms
         spacing on the HHMMSSmmm frame_base token.
MODALITY `synthesize_final_u8` (jpg -> L -> resize to the `_raw.png` size,
         BILINEAR) = the deployed `_final`, NOT raw/before_DSC (D6/D7). The
         jpg and `_raw.png` differ in size, so the resize is load-bearing; a
         wrong size/modality yields scores INCOMPARABLE to the deployed cohort.

WRITES (all PROBE artifacts in data/cohort_stats/ — does NOT overwrite the
        canonical `qa_per_frame_detector_scores.parquet`; promoting the
        sweep-pool table to canonical + re-pointing `candidate_selector._PARQUET`
        is a POST-GATE action, deliberately not done here):
  * qa_per_frame_detector_scores__sweep_pool_5fps.parquet   (the score table)
  * qa_sweep_pool_frame_manifest.parquet                    (per-frame mask qual)
  * qa_sweep_pool_run_summary.json                          (counts/timing/skip)
  Crash-safe: per-study append to JSONL; `--resume` skips done studies;
  `--finalize-only` consolidates JSONL -> parquet without re-running.

PHI-clean: every row carries study_code + frame_base only (never the
patient-name path) — `study_code` comes straight from study_codes.csv.

Run:
  .venv/bin/python scripts/calibration/build_sweep_pool_score_table.py [--limit-studies N] [--fps 5] [--resume] [--finalize-only]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "scripts"))

import preflight_helpers.env_bootstrap  # noqa: E402,F401  side-effect: rf_quality + segmenter bridge

from labeling_app.measurements import CONTRACT_VERSION                       # noqa: E402
from preflight_helpers.segmenter import (                                    # noqa: E402
    BundleMeta, LABEL_FEMUR, LABEL_RF, build_voi_hist_clahe_stack,
    load_learner_safe, predict_mask, synthesize_final_u8,
)
from preflight_helpers.feature_compute import compute_image_quality_features  # noqa: E402
from preflight_helpers import env                                            # noqa: E402
# Reuse the canonical score-key map, code-sig, and parquet writer (+ PHI guard).
from qa_cohort_failure_histogram import _SCORE_KEY, _code_sig, write_score_table  # noqa: E402

_BK = env.GORUNTULER_BACKUP
_CODES = _REPO / "configs" / "study_codes.csv"
_DLOG = _REPO / "exports" / "decision_log_full_3operators.csv"
_OUTDIR = _REPO / "data" / "cohort_stats"
_OUT_PARQUET = _OUTDIR / "qa_per_frame_detector_scores__sweep_pool_5fps.parquet"
_ROWS_JSONL = _OUTDIR / "_sweep_pool_rows.jsonl"
_MANIFEST_JSONL = _OUTDIR / "_sweep_pool_manifest.jsonl"
_MANIFEST_PARQUET = _OUTDIR / "qa_sweep_pool_frame_manifest.parquet"
_SUMMARY_JSON = _OUTDIR / "qa_sweep_pool_run_summary.json"

_FB_RE = re.compile(r"^\d{8}_\d{9}$")
_MODALITY = "_final(synth)"
_MUSCLE_CLASSES = (1, 2, 3, 4)   # RF, VI, VL, VM


def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ms_of_day(fb: str) -> int:
    """frame_base = '20260127_151046404' -> ms since midnight (HHMMSSmmm)."""
    t = fb.split("_")[1]
    return ((int(t[0:2]) * 60 + int(t[2:4])) * 60 + int(t[4:6])) * 1000 + int(t[6:9])


def _subsample_fps(bases: list[str], fps: float) -> list[str]:
    """Greedy >= (1000/fps) ms spacing on the millisecond-timestamp names."""
    ordered = sorted(bases, key=_ms_of_day)
    if fps <= 0:
        return ordered
    gap = 1000.0 / fps
    kept: list[str] = []
    last = None
    for fb in ordered:
        t = _ms_of_day(fb)
        if last is None or (t - last) >= gap:
            kept.append(fb)
            last = t
    return kept


def _cohort(decision_status: str = "completed") -> pd.DataFrame:
    """The akadir cohort at `decision_status` + relative_frame_dir + per-study spacing.
    'completed' -> the 272 alive studies; 'quality_failed' -> the 47 rejected (128LH
    wrong-probe) studies, a diagnostic pool to test whether the acquisition-failure
    starvation is real or a clean-cohort selection artifact."""
    dl = pd.read_csv(_DLOG)
    sub = dl[(dl.operator_id == "akadir") & (dl.decision_status == decision_status)].copy()
    # Dedup re-saves on study_code; keep the most-recently-saved row.
    sub = sub.sort_values("saved_at_utc").drop_duplicates("study_code", keep="last")
    codes = pd.read_csv(_CODES)[["study_code", "relative_frame_dir"]]
    merged = sub.merge(codes, on="study_code", how="left")
    return merged[["study_code", "relative_frame_dir", "pixel_spacing_y", "dcm_rows"]]


def _raw_dir_for(relative_frame_dir) -> Path | None:
    if not isinstance(relative_frame_dir, str) or not relative_frame_dir:
        return None
    sd = _BK / relative_frame_dir
    if not sd.is_dir():
        return None
    raws = sorted(sd.glob("RawImagesForResearch_*"))
    return raws[0] if raws else None


def _mask_stats(pred: np.ndarray) -> dict:
    h, w = pred.shape
    tot = h * w
    fg = int((pred > 0).sum())
    return {
        "mask_h": int(h), "mask_w": int(w),
        "fg_frac": round(fg / tot, 4) if tot else 0.0,
        "rf_px": int((pred == LABEL_RF).sum()),
        "femur_px": int((pred == LABEL_FEMUR).sum()),
        "n_muscle_classes": int(sum(bool((pred == c).any()) for c in _MUSCLE_CLASSES)),
        # A "degenerate" mask is the chicken-and-egg signal: the segmenter
        # produced almost no foreground, so RF-ROI detectors cannot score it.
        "degenerate": bool((fg / tot) < 0.01) if tot else True,
    }


def _load_done_studies() -> set[str]:
    done: set[str] = set()
    if _ROWS_JSONL.exists():
        for line in _ROWS_JSONL.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                done.add(json.loads(line)["study_code"])
            except Exception:  # noqa: BLE001
                pass
    return done


def finalize(meta_info: dict) -> None:
    """Consolidate the per-study JSONL into the probe parquet + manifest + summary."""
    rows = [json.loads(l) for l in _ROWS_JSONL.read_text().splitlines() if l.strip()]
    write_score_table(rows, _OUT_PARQUET)   # reuses the PHI-guard assertion

    man = [json.loads(l) for l in _MANIFEST_JSONL.read_text().splitlines() if l.strip()]
    man_df = pd.DataFrame(man)
    man_df.to_parquet(_MANIFEST_PARQUET, index=False)

    df = pd.DataFrame(rows)
    graded = man_df[man_df.get("status") == "graded"] if not man_df.empty else man_df
    n_graded = int(len(graded))
    summary = {
        **meta_info,
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "score_parquet": str(_OUT_PARQUET),
        "manifest_parquet": str(_MANIFEST_PARQUET),
        "n_score_rows": int(len(df)),
        "n_studies_in_table": int(df["study_code"].nunique()) if not df.empty else 0,
        "n_frames_graded": n_graded,
        "n_frames_degenerate_mask": int(graded["degenerate"].sum()) if n_graded else 0,
        "per_detector": {},
    }
    for det in _SCORE_KEY:
        s = df[df.detector == det]["raw_score"] if not df.empty else pd.Series(dtype=float)
        nv = int(s.notna().sum())
        summary["per_detector"][det] = {
            "n_valid_scores": nv,
            "n_fires": int(df[(df.detector == det)].fires.sum()) if not df.empty else 0,
            "min": float(s.min()) if nv else None,
            "median": float(s.median()) if nv else None,
            "max": float(s.max()) if nv else None,
        }
    _SUMMARY_JSON.write_text(json.dumps(summary, indent=2))

    print(f"\n[finalize] {len(df)} score rows | {n_graded} frames graded | "
          f"{summary['n_frames_degenerate_mask']} degenerate masks")
    print(f"[finalize] -> {_OUT_PARQUET.name} / {_MANIFEST_PARQUET.name} / {_SUMMARY_JSON.name}")
    print("\nPer-detector predicted-score spread (sweep pool):")
    for det, d in summary["per_detector"].items():
        if d["n_valid_scores"]:
            print(f"  {det:26s} n_valid={d['n_valid_scores']:5d}  "
                  f"min={d['min']:.3g} med={d['median']:.3g} max={d['max']:.3g}  "
                  f"fires={d['n_fires']}")
        else:
            print(f"  {det:26s} no valid scores")


def run(*, limit_studies: int | None, fps: float, resume: bool,
        decision_status: str = "completed") -> None:
    from preflight_helpers.model_registry import resolve_canonical_pkl
    bundle = resolve_canonical_pkl()  # pinned canonical bundle, not newest-mtime
    if bundle is None:
        raise SystemExit(f"No canonical bundle resolvable (pointer + {env.MODEL_DIR_LOCAL})")
    meta = BundleMeta.from_pkl(bundle)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    learn = load_learner_safe(bundle)
    code_sig = _code_sig()
    computed_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    print(f"[bundle] {bundle.name}  modalities={list(meta.modalities)}  "
          f"resize_to={meta.resize_to}  device={device}", flush=True)

    cohort = _cohort(decision_status)
    if limit_studies:
        cohort = cohort.head(limit_studies)
    n_total_studies = len(cohort)

    if resume:
        done = _load_done_studies()
        print(f"[resume] {len(done)} studies already complete; appending", flush=True)
    else:
        _ROWS_JSONL.write_text("")
        _MANIFEST_JSONL.write_text("")
        done = set()

    rows_f = open(_ROWS_JSONL, "a")
    man_f = open(_MANIFEST_JSONL, "a")

    n_studies = n_frames = n_graded = n_skip = 0
    skip_reasons: dict[str, int] = {}
    t0 = time.monotonic()

    for _, s in cohort.iterrows():
        sc = str(s.study_code)
        if sc in done:
            continue
        n_studies += 1
        rdir = _raw_dir_for(s.relative_frame_dir)
        if rdir is None:
            skip_reasons["study_no_raw_dir"] = skip_reasons.get("study_no_raw_dir", 0) + 1
            man_f.write(json.dumps({"study_code": sc, "status": "skipped_study",
                                    "skip_error": "no_raw_dir"}) + "\n")
            man_f.flush()
            continue
        bases = _subsample_fps(
            [p.stem for p in rdir.iterdir() if p.suffix == ".jpg" and _FB_RE.match(p.stem)],
            fps)
        psy, dcr = _safe_float(s.pixel_spacing_y), _safe_float(s.dcm_rows)

        study_rows: list[dict] = []
        for fb in bases:
            n_frames += 1
            try:
                final_u8 = synthesize_final_u8(rdir, fb)
                stacked = build_voi_hist_clahe_stack(final_u8, modalities=meta.modalities)
                conf: dict = {}
                pred = predict_mask(learn, stacked, meta, device=device, confidence_out=conf)
                be = conf.get("boundary_entropy")
                spacing = {"pixel_spacing_y": psy, "dcm_rows": dcr,
                           "mask_height": int(pred.shape[0])}
                iqf = compute_image_quality_features(
                    frame_base=fb, multiclass_mask=pred, final_image_u8=final_u8,
                    spacing_row=spacing, boundary_entropy=be, qmt_provenance=None)
            except Exception as exc:                    # noqa: BLE001 — report, never crash the batch
                n_skip += 1
                k = f"error:{type(exc).__name__}"
                skip_reasons[k] = skip_reasons.get(k, 0) + 1
                man_f.write(json.dumps({"study_code": sc, "frame_base": fb,
                                        "status": "skipped",
                                        "skip_error": f"{type(exc).__name__}: {exc}"}) + "\n")
                continue

            n_graded += 1
            man_f.write(json.dumps({"study_code": sc, "frame_base": fb, "status": "graded",
                                    "boundary_entropy": be, **_mask_stats(pred)}) + "\n")
            metadata = iqf.image_quality_detector_metadata
            fired = set(iqf.image_quality_validity_flags)
            for det, score_key in _SCORE_KEY.items():
                dm = metadata.get(det)
                if not isinstance(dm, dict):
                    continue
                raw = dm.get(score_key)
                study_rows.append({
                    "study_code": sc, "frame_base": fb, "detector": det,
                    "raw_score": float(raw) if isinstance(raw, (int, float)) else float("nan"),
                    "fires": det in fired,
                    "threshold_at_compute_time": dm.get("threshold"),
                    "pending_calibration": dm.get("pending_calibration"),
                    "metadata_json": json.dumps(
                        {k: v for k, v in dm.items() if k != "thresholds_used"}, default=str),
                    "modality": _MODALITY, "mask_source": "predicted",
                    "contract_version": CONTRACT_VERSION, "code_sig": code_sig,
                    "computed_at_utc": computed_at,
                })

        for r in study_rows:
            rows_f.write(json.dumps(r) + "\n")
        rows_f.flush()
        man_f.flush()
        el = time.monotonic() - t0
        rate = n_frames / max(el, 1e-6)
        print(f"[{n_studies}/{n_total_studies}] {sc}: {len(bases)} frames | "
              f"graded={n_graded} skipped={n_skip} | {el:.0f}s @ {rate:.1f} fps", flush=True)

    rows_f.close()
    man_f.close()

    elapsed = time.monotonic() - t0
    finalize({
        "cohort": f"akadir ∧ {decision_status}",
        "fps": fps, "limit_studies": limit_studies,
        "frame_source": "goruntuler_backup/<relative_frame_dir>/RawImagesForResearch_*",
        "modality": _MODALITY, "mask_source": "predicted",
        "code_sig": code_sig, "contract_version": CONTRACT_VERSION,
        "model_bundle": bundle.name, "device": str(device),
        "n_studies_processed_this_run": n_studies,
        "n_frames_enumerated_this_run": n_frames,
        "n_frames_skipped_this_run": n_skip,
        "skip_reasons_this_run": skip_reasons,
        "elapsed_seconds_this_run": round(elapsed, 1),
    })


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-studies", type=int, default=None,
                    help="process only the first N alive studies (smoke test)")
    ap.add_argument("--fps", type=float, default=5.0, help="subsample rate (frames/s)")
    ap.add_argument("--resume", action="store_true",
                    help="skip studies already present in the rows JSONL and append")
    ap.add_argument("--finalize-only", action="store_true",
                    help="rebuild parquet/summary from the existing JSONL, no inference")
    ap.add_argument("--decision-status", default="completed",
                    help="akadir cohort filter: 'completed' (272 alive) or 'quality_failed' (47 rejected)")
    ap.add_argument("--tag", default="",
                    help="suffix all output paths (e.g. 'qf') so a 2nd cohort run keeps the alive artifacts")
    args = ap.parse_args()

    if args.tag:
        # Rebind output paths so this cohort's artifacts sit beside the alive ones.
        global _OUT_PARQUET, _ROWS_JSONL, _MANIFEST_JSONL, _MANIFEST_PARQUET, _SUMMARY_JSON
        s = f"_{args.tag}"
        _OUT_PARQUET = _OUTDIR / f"qa_per_frame_detector_scores__sweep_pool_5fps{s}.parquet"
        _ROWS_JSONL = _OUTDIR / f"_sweep_pool_rows{s}.jsonl"
        _MANIFEST_JSONL = _OUTDIR / f"_sweep_pool_manifest{s}.jsonl"
        _MANIFEST_PARQUET = _OUTDIR / f"qa_sweep_pool_frame_manifest{s}.parquet"
        _SUMMARY_JSON = _OUTDIR / f"qa_sweep_pool_run_summary{s}.json"

    if args.finalize_only:
        finalize({"cohort": f"akadir ∧ {args.decision_status}", "fps": args.fps,
                  "note": "finalize-only — consolidated from JSONL"})
        return
    run(limit_studies=args.limit_studies, fps=args.fps, resume=args.resume,
        decision_status=args.decision_status)


if __name__ == "__main__":
    main()

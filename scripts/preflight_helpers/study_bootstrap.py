"""N5 — study bootstrap: compound distinct finalized runs into one canonical
study assessment.

The run ledger is a *compounding asset*: each accepted run adds frames; the
study's canonical assessment is the anchorless pooled-median over every
analysis frame across the *distinct* finalized runs.

  * **distinct** — runs are deduped by `frame_set.signature` (identical
    re-runs stay in the history but are excluded from the pool). Legacy ledger
    records that predate the `frame_set` field get a fallback signature hashed
    the same way, from their `selected_frames` — so a legacy run and an
    identical new run still dedup against each other.
  * **anchorless** — pooling frames across runs has no single meaningful
    anchor, so every one of the 9 features is re-aggregated by multi-frame
    median: `aggregate_features(pooled, force_all_median=True)`.

Pure functions over `bundle_writer.iter_runs()` — no Streamlit, no torch.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from .aggregation import aggregate_features
from .bundle_writer import iter_runs
from .cohort_placement import OLD, percentile
from .run_lifecycle import input_signature
from .study_metadata import run_study_uid


# The assessment features pooled across runs — imported from the torch-free
# feature_catalog (single source of truth) so this list can never again drift
# from the displayed HEADLINE+SECONDARY set. It silently lacked the fat features
# when they were added, so the run-history table showed them as "— / single run".
# The catalog is torch-free, preserving this module's no-Streamlit/no-torch
# property. (VI features dropped 2026-05-14 are simply absent from the catalog.)
from .feature_catalog import BOOTSTRAP_FEATURES


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _is_finalized(record: dict) -> bool:
    """B2.1+ records carry `lifecycle`; legacy records (no field) are finalized
    by convention — drafts never reach runs.jsonl in the first place."""
    return record.get("lifecycle", "finalized") == "finalized"


def _signature_of(record: dict) -> str:
    """The dedup key for a ledger record — `frame_set.signature` when present,
    else a fallback hashed identically from `selected_frames` so a legacy run
    and an identical new run dedup against each other."""
    fs = record.get("frame_set")
    if isinstance(fs, dict) and fs.get("signature"):
        return str(fs["signature"])
    sel = record.get("selected_frames") or []
    bundle_sha = (record.get("model_bundle") or {}).get("sha256", "")
    class_ids = record.get("resolved_class_ids") or {}
    return input_signature(sel, bundle_sha, class_ids)


def _finalized_runs_for(study_code: str,
                        study_uid: Optional[str] = None) -> list[dict]:
    """Finalized runs for a study. Keyed on the collision-free `study_uid` when
    given (the correct path — the AKA `study_code` repeats across studies, so
    keying on it would pool DIFFERENT patients' frames into one bootstrap); falls
    back to study_code only for a legacy caller with no uid, or a legacy record
    that carries no study_path."""
    def _match(r: dict) -> bool:
        if not _is_finalized(r):
            return False
        if study_uid is not None:
            ru = run_study_uid(r)
            return ru == study_uid if ru is not None else r.get("study_code") == study_code
        return r.get("study_code") == study_code
    runs = [r for r in iter_runs() if _match(r)]
    # Earliest first — so dedup keeps the *first* run per signature.
    runs.sort(key=lambda r: str(r.get("created_at_utc", "")))
    return runs


def distinct_finalized_runs(study_code: str,
                            study_uid: Optional[str] = None) -> list[dict]:
    """Finalized runs for a study, deduped by signature (earliest kept)."""
    seen: dict[str, dict] = {}
    for r in _finalized_runs_for(study_code, study_uid):
        sig = _signature_of(r)
        if sig not in seen:
            seen[sig] = r
    return list(seen.values())


def all_runs_with_dedup_flag(study_code: str,
                             study_uid: Optional[str] = None) -> list[dict]:
    """Every finalized run, annotated for an honest history view:
      `_in_bootstrap` — True iff this run is the distinct representative;
      `_dedup_of`     — the run_id it duplicates (None if it is the original).
    """
    seen: dict[str, str] = {}   # signature -> earliest run_id
    out: list[dict] = []
    for r in _finalized_runs_for(study_code, study_uid):
        sig = _signature_of(r)
        if sig in seen:
            out.append({**r, "_in_bootstrap": False, "_dedup_of": seen[sig],
                        "_signature": sig})
        else:
            seen[sig] = r["run_id"]
            out.append({**r, "_in_bootstrap": True, "_dedup_of": None,
                        "_signature": sig})
    return out


def _analysis_frames(record: dict) -> list[dict]:
    """The frame_features of a run that actually fed its aggregate —
    `gates.use_for_analysis AND gates.usable`."""
    out: list[dict] = []
    for ff in record.get("frame_features", []) or []:
        g = ff.get("gates") or {}
        if g.get("use_for_analysis") and g.get("usable"):
            out.append(ff)
    return out


def study_bootstrap(study_code: str, study_uid: Optional[str] = None,
                    source: str = OLD) -> dict:
    """The canonical study assessment — anchorless pooled-median over the
    analysis frames of every *distinct finalized* run.

    Pass `study_uid` (the collision-free `StudyMeta.study_uid`) so runs are pooled
    by true study identity: the AKA `study_code` repeats across studies, and keying
    on it alone would pool DIFFERENT patients' frames into one assessment. Without
    a uid it falls back to study_code (legacy/best-effort).

    `source` is the placement REFERENCE population (old/new/mixed) — it must be the
    SAME live `cohort_source` the headline places against, or the bootstrap pctile
    silently ranks the pooled median against a DIFFERENT cohort than the headline.
    Defaults to OLD (the live default), NOT the module-level LEGACY of `percentile`
    (the retired v1.0 cohort). PLACE-ONLY: moves the percentile, never the aggregate.

    Returns:
      study_code, n_distinct_runs, n_total_runs, n_pooled_frames,
      contributing_run_ids, models_used (sorted sha list — warn if >1),
      aggregate (force_all_median), placements (9 features vs the cohort),
      per_feature {feat: {bootstrap_value, per_run:[{run_id, value}]}},
      generated_at_utc.
    """
    distinct = distinct_finalized_runs(study_code, study_uid)
    n_total = len(_finalized_runs_for(study_code, study_uid))
    base = {
        "study_code": study_code,
        "n_distinct_runs": len(distinct),
        "n_total_runs": n_total,
        "n_pooled_frames": 0,
        "contributing_run_ids": [],
        "models_used": [],
        "aggregate": {},
        "placements": {},
        "per_feature": {},
        "generated_at_utc": _utc_iso(),
    }
    if not distinct:
        return base

    pooled: list[dict] = []
    routed_aggs: list[dict] = []
    n_routed_frames = 0
    contributing: list[str] = []
    models: set[str] = set()
    for r in distinct:
        contributing.append(r["run_id"])
        sha = (r.get("model_bundle") or {}).get("sha256")
        if sha:
            models.add(str(sha))
        rp = r.get("routed_picks") or {}
        if rp:
            # v8 routed run: per-frame features are NOT persisted (routed_picks stores frame
            # bases + gates only), so the frame pool below would read 0 frames from every
            # routed run and the bootstrap rendered blank for the whole new round (found via
            # AKA016, 2026-08-14). The run's AGGREGATE is itself the correctly-routed
            # measurement (geometry <- femur, texture <- centre-RF, any operator refinement
            # applied) — so the v8 bootstrap is the per-feature MEDIAN OVER RUN AGGREGATES,
            # a median of complete measurements rather than a pool of frames.
            routed_aggs.append(r.get("aggregate") or {})
            n_routed_frames += sum(int((v or {}).get("n_analysis_frames") or 0)
                                   for v in rp.values())
        else:
            pooled.extend(_analysis_frames(r))

    if routed_aggs:
        from statistics import median as _median
        agg = {}
        for feat in BOOTSTRAP_FEATURES:
            xs = [a.get(feat) for a in routed_aggs
                  if isinstance(a.get(feat), (int, float))]
            if xs:
                agg[feat] = float(_median(xs))
        # the QMT-comparability guard reads the provenance string — carry the modal one
        provs = [a.get("qmt_provenance") for a in routed_aggs if a.get("qmt_provenance")]
        if provs:
            from collections import Counter
            agg["qmt_provenance"] = Counter(provs).most_common(1)[0][0]
        mode = "routed_run_median"
    else:
        agg = aggregate_features(pooled, force_all_median=True)
        mode = "frame_pool"

    # Demographics — constant per study; take the first distinct run's.
    sm = distinct[0].get("study_meta") or {}
    age = sm.get("age")
    sex = sm.get("sex")

    placements: dict = {}
    per_feature: dict = {}
    for feat in BOOTSTRAP_FEATURES:
        val = agg.get(feat)
        p = percentile(feat, val, age=age, sex=sex, source=source)
        placements[feat] = {
            "value": p.value, "overall": p.overall,
            "age_matched": p.age_matched, "sex_matched": p.sex_matched,
        }
        per_feature[feat] = {
            "bootstrap_value": val,
            "per_run": [{"run_id": r["run_id"],
                         "value": (r.get("aggregate") or {}).get(feat)}
                        for r in distinct],
        }

    base.update({
        "n_pooled_frames": len(pooled) if mode == "frame_pool" else n_routed_frames,
        "mode": mode,
        "contributing_run_ids": contributing,
        "models_used": sorted(models),
        "aggregate": agg,
        "placements": placements,
        "per_feature": per_feature,
    })
    return base


def study_bootstrap_summary(study_code: str,
                            study_uid: Optional[str] = None) -> dict:
    """Lightweight header stats — no pooling / aggregation / placement."""
    distinct = distinct_finalized_runs(study_code, study_uid)
    models = sorted({
        str((r.get("model_bundle") or {}).get("sha256"))
        for r in distinct
        if (r.get("model_bundle") or {}).get("sha256")
    })
    return {
        "study_code": study_code,
        "n_distinct_runs": len(distinct),
        "n_total_runs": len(_finalized_runs_for(study_code, study_uid)),
        "models_used": models,
        "generated_at_utc": _utc_iso(),
    }

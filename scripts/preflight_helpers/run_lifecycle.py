"""N4 run lifecycle — input signatures + draft persistence.

States: ``none`` -> ``draft`` -> ``finalized``.

  * A phase-5 assessment result is a **draft**: it lives in
    ``data/runs_drafts/<run_id>.json`` (gitignored scratch), never in
    ``runs.jsonl``. Back-nav + edits refine the *same* draft (same run_id,
    overwritten).
  * "Accept run" **finalizes** the draft — promotes it to the ledger via
    ``bundle_writer.append_record`` and deletes the draft file. After
    finalization the ledger entry is immutable; editing inputs branches a
    *fresh* draft.
  * "Start afresh" discards the open draft and resets picks/gates.

This module is pure logic + draft file I/O. **No Streamlit import** — it is
unit-testable on its own, and it is the shared home of `input_signature`,
which serves *both* N4 cache-invalidation and N5 frame-set dedup.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from . import env
from . import bundle_writer


# ---------------------------------------------------------------------------
# input_signature — the shared dedup / cache-invalidation key
# ---------------------------------------------------------------------------

def _get(obj: Any, name: str) -> Any:
    """Attribute-or-key access — tolerates dicts (ledger records) and dataclass
    objects (run_pipeline.PickedFrame / GateAnswers) interchangeably."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _norm_frame(pf: Any) -> tuple:
    """Normalise one picked-frame spec to a stable, hashable tuple.

    Accepts a ``run_pipeline.PickedFrame`` object *or* a ledger
    ``selected_frames`` dict. ``drop_reason`` and the Build-Step-1 QA-capture
    fields (``drop_reason_codes``, ``drop_reason_other``, ``qa_overrides``) are
    intentionally excluded — operator annotations with no effect on the
    computed features. Only the five compute-determining fields below are
    hashed; any other gate field is excluded by construction.
    """
    gates = _get(pf, "gates")
    return (
        str(_get(pf, "frame_base")),
        bool(_get(pf, "is_anchor")),
        bool(_get(gates, "usable")),
        bool(_get(gates, "segmentation_perfect")),
        bool(_get(gates, "use_for_analysis")),
    )


def input_signature(
    picked_frames: Iterable[Any],
    bundle_sha: Optional[str],
    resolved_class_ids: Optional[dict],
    routed_picks: Optional[dict] = None,
) -> str:
    """SHA256 over the inputs that determine a run's computed features.

    Inputs hashed:
      * sorted ``[(frame_base, is_anchor, usable, segmentation_perfect,
        use_for_analysis)]`` over the picked frames (drop_reason and the
        QA-capture fields excluded — see _norm_frame);
      * the model bundle's sha256;
      * the resolved class-id config — every ``*_class_id`` plus ``erosion_px``.
      * (v8 routed runs only) the femur/RF picks + their keep/drop gates, added
        ONLY when ``routed_picks`` is truthy so the hash stays BYTE-IDENTICAL for
        every existing sweep-only caller (default None → the payload is unchanged).

    Two runs with the same signature would compute the same features; this is
    why it powers N4 cache-invalidation (recompute when the signature changes)
    and N5 frame-set dedup (identical re-runs excluded from the study pool).
    """
    frames = sorted(_norm_frame(pf) for pf in picked_frames)
    class_ids = resolved_class_ids or {}
    payload = {
        "frames": frames,
        "bundle_sha": str(bundle_sha or ""),
        "resolved_class_ids": {k: class_ids[k] for k in sorted(class_ids)},
    }
    if routed_picks:
        payload["routed_picks"] = {
            role: sorted([
                [fb, bool(g.get("usable", True)), bool(g.get("use_for_analysis", True))]
                for fb, g in (routed_picks[role].get("gates") or {}).items()
            ]) for role in sorted(routed_picks)
        }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def routed_frame_set(record: dict, routed_picks: dict) -> dict:
    """Fold the v8 femur/RF frame identities into a sweep-derived ``frame_set`` so a routed run's
    dedup key + analysis-frame count are HONEST (torch-free, Akadir 2026-07-20).

    run_pipeline builds ``frame_set`` from the SWEEP picks alone; on a routed study that signature
    collides across different femur/RF selections and its ``n_analysis_frames`` omits the femur/RF
    frames that actually produced the number. This re-hashes the signature with ``routed_picks``
    threaded in, appends the ``role:frame_base`` identities, and adds the routed analysis-frame counts.
    Every field is read defensively so a sweep-only or legacy record is untouched by construction."""
    fs = dict(record.get("frame_set") or {})
    fs["signature"] = input_signature(
        record.get("selected_frames") or [],
        (record.get("model_bundle") or {}).get("sha256", ""),
        record.get("resolved_class_ids") or {},
        routed_picks=routed_picks,
    )
    fs["frame_bases_sorted"] = list(fs.get("frame_bases_sorted") or []) + [
        role + ":" + fb
        for role in sorted(routed_picks)
        for fb in sorted(routed_picks[role].get("frame_bases") or [])
    ]
    fs["n_analysis_frames"] = (fs.get("n_analysis_frames") or 0) + sum(
        (routed_picks[r].get("n_analysis_frames") or 0) for r in routed_picks
    )
    return fs


# ---------------------------------------------------------------------------
# Draft persistence — runs_drafts/<run_id>.json
# ---------------------------------------------------------------------------

def _draft_path(run_id: str) -> Path:
    return env.DRAFTS_DIR / f"{run_id}.json"


def write_draft(record: dict) -> Path:
    """Write (idempotent overwrite) a draft record to its scratch file.

    The record must already carry ``run_id`` and ``lifecycle == "draft"`` —
    ``bundle_writer.build_record`` produces it; the caller (run_pipeline) just
    hands it through. Returns the draft file path.
    """
    env.ensure_local_dirs()
    run_id = record["run_id"]
    path = _draft_path(run_id)
    with open(path, "w") as f:
        json.dump(record, f, indent=2)
    return path


def read_draft(run_id: str) -> dict:
    """Read a draft record back. Raises FileNotFoundError if the draft is gone."""
    with open(_draft_path(run_id)) as f:
        return json.load(f)


def finalize_draft(run_id: str) -> tuple[str, Path]:
    """Promote a draft to the ledger and delete the draft file.

    Reads ``runs_drafts/<run_id>.json``, flips ``lifecycle`` to ``finalized``,
    appends it to ``runs.jsonl`` via ``bundle_writer.append_record`` (which also
    writes the per-run artefact dir), then removes the draft file.

    Returns (run_id, run_dir). After this the ledger entry is immutable.
    """
    record = read_draft(run_id)
    record["lifecycle"] = "finalized"
    run_id_out, run_dir = bundle_writer.append_record(record)
    discard_draft(run_id)
    return run_id_out, run_dir


def discard_draft(run_id: Optional[str]) -> bool:
    """Delete a draft file if it exists. Returns True iff a file was removed."""
    if not run_id:
        return False
    path = _draft_path(run_id)
    if path.exists():
        path.unlink()
        return True
    return False


def iter_drafts() -> list[dict]:
    """All draft records currently on disk, newest mtime first."""
    if not env.DRAFTS_DIR.exists():
        return []
    out: list[tuple[float, dict]] = []
    for p in env.DRAFTS_DIR.glob("*.json"):
        try:
            with open(p) as f:
                out.append((p.stat().st_mtime, json.load(f)))
        except (json.JSONDecodeError, OSError):
            continue
    out.sort(key=lambda t: t[0], reverse=True)
    return [rec for _mtime, rec in out]


def gc_drafts(max_age_h: float = 24.0) -> list[str]:
    """Sweep orphaned drafts older than ``max_age_h`` hours.

    A draft is orphaned when the user closed the app (or hit "Start afresh")
    without accepting it. Called once on app start. Returns the run_ids swept.
    """
    if not env.DRAFTS_DIR.exists():
        return []
    cutoff = time.time() - max_age_h * 3600.0
    swept: list[str] = []
    for p in env.DRAFTS_DIR.glob("*.json"):
        try:
            if p.stat().st_mtime < cutoff:
                run_id = p.stem
                p.unlink()
                swept.append(run_id)
        except OSError:
            continue
    return swept

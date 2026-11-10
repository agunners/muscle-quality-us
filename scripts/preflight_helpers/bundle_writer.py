"""Write a run-bundle: append to runs.jsonl + per-run artifacts under data/runs/<run_id>/.

Run-ledger record carries the full provenance set required by Akadir's
guardrail #1:
  - sweep / study id (study_code + raw_path)
  - per-frame paths + selected_by/selected_at
  - 3 human gates per frame: usable / segmentation_perfect / use_for_analysis
  - model bundle path + sha256 hash + palette path/hash
  - resolved class mapping
  - runtime/PYTHONPATH bridge used
  - mask + overlay paths
  - per-frame features (full provenance from feature_compute._provenance)
  - aggregate features by policy
  - timestamps

The ledger JSONL is gitignored, local-only. Raw study paths and the PHI
`_phi` block live here — never in the UI.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from . import env


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _study_uid(study_path: Any) -> Optional[str]:
    """The canonical study identity for a record. Lazy import — study_metadata
    pulls pandas, and this module stays import-light. Defensive: no path (or an
    unparseable one) yields None, and readers fall back to `run_study_uid`."""
    if not study_path:
        return None
    try:
        from . import study_metadata
        return study_metadata.study_uid_for_dirname(Path(study_path).name)
    except Exception:
        return None


def _coerce(o: Any) -> Any:
    """JSON-safe coerce for dataclasses, Paths, numpy scalars."""
    if is_dataclass(o):
        return _coerce(asdict(o))
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, dict):
        return {k: _coerce(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_coerce(x) for x in o]
    # numpy scalar?
    if hasattr(o, "item") and callable(o.item):
        try:
            return o.item()
        except Exception:
            return str(o)
    return o


def new_run_id(study_code: str) -> str:
    """A collision-safe run id.

    Format: `<study_code>_<unix_seconds>_<6 hex>`. The hex suffix matters: the
    N4 stale-cache regression (and any rapid AppTest click-through) can branch
    two runs inside the same wall-clock second — without the suffix their run
    ids would collide and the draft files would overwrite each other.
    """
    return f"{study_code}_{int(time.time())}_{uuid.uuid4().hex[:6]}"


def build_record(
    *,
    study_code: str,
    study_path: Path,
    study_meta: dict,           # already a dict (use StudyMeta.to_ui_dict + extras)
    selected_frames: list[dict],  # [{frame_base, selected_at, selected_by, paths, gates: {...}}]
    model_bundle: dict,         # {pkl_path, json_path, sha256, modalities, ...}
    palette_path: Path,
    palette_sha256: str,
    resolved_class_ids: dict,   # the pinned class IDs that were used
    runtime_bridge: list[str],  # the PYTHONPATH entries that were active
    frame_features: list[dict], # per-frame feature dicts from FrameFeatures.to_dict()
    aggregate: dict,            # output of aggregation.aggregate_features
    placements: dict,           # {feature: Placement (as dict)}
    mode: str,                  # 'live' or 'cached' or 'preflight_stage1' etc.
    frame_set: Optional[dict] = None,  # N5 dedup key — {signature, frame_bases_sorted, ...}
    lifecycle: str = "finalized",      # N4 — 'draft' | 'finalized' | 'none'
    notes: Optional[str] = None,
) -> tuple[str, dict]:
    """Construct a JSON-safe run-ledger record. No file I/O.

    Split out of the old `write_run` so the N4 lifecycle can build a record,
    stash it as a draft, and only later promote it to the ledger via
    `append_record` — without re-running the (expensive) pipeline.

    Returns (run_id, record). The record carries two N4/N5 fields the older
    ledger entries lack:
      - `frame_set`  — the dedup key for N5 study bootstrap (signature +
        sorted frame bases + n analysis frames + bundle sha + erosion px).
      - `lifecycle`  — 'draft' while in the scratch dir, 'finalized' once in
        the ledger. (Legacy records without this field are treated as
        finalized by readers.)
    """
    run_id = new_run_id(study_code)
    record = {
        "run_id": run_id,
        "study_code": study_code,
        # Canonical identity, stamped as a STORED FACT (Akadir 2026-07-21) rather than
        # re-derived from a PHI path on every read. `study_code` above is a LABEL and means
        # two different things across our files; `study_uid` is the one key. Readers keep
        # their `run_study_uid(record)` fallback for legacy records that lack this field.
        "study_uid": _study_uid(study_path),
        # PHI guardrail: ledger-only. Keep a missing path as None — stringifying it
        # would store the literal "None", from which the legacy `run_study_uid`
        # fallback would mint a phantom uid.
        "study_path": str(study_path) if study_path else None,
        "study_meta": study_meta,
        "selected_frames": selected_frames,
        "model_bundle": model_bundle,
        "palette_path": str(palette_path),
        "palette_sha256": palette_sha256,
        "resolved_class_ids": resolved_class_ids,
        "runtime": {
            "pythonpath_bridge": runtime_bridge,
            "python_version": sys.version.split()[0],
            "env_pythonpath": os.environ.get("PYTHONPATH", ""),
        },
        "frame_features": frame_features,
        "aggregate": aggregate,
        "placements": placements,
        "frame_set": frame_set,
        "lifecycle": lifecycle,
        "mode": mode,
        "created_at_utc": _utc_iso(),
        "notes": notes,
    }
    # Freshness stamp (Akadir 2026-07-20): the measurement-code digest, so a future run self-declares
    # staleness — `condition_model.staleness()` compares this against the frozen model's stamped hash.
    # Set here (the single record-assembly point) so it covers BOTH sweep-only and v8 routed runs.
    # Lazy import: condition_model pulls pandas, and this keeps bundle_writer torch/pandas-free at import.
    try:
        from . import condition_model
        record["measurement_code_hash"] = condition_model.measurement_code_hash()
    except Exception:
        record["measurement_code_hash"] = None
    return run_id, _coerce(record)


def append_record(record: dict) -> tuple[str, Path]:
    """Persist a built record: per-run artefact dir + append to runs.jsonl.

    Idempotent on the run_id's directory (mkdir exist_ok); the JSONL append is
    not idempotent, so callers must not append the same record twice. The
    record is assumed already JSON-safe (it came from `build_record`).

    Returns (run_id, run_dir).
    """
    env.ensure_local_dirs()
    run_id = record["run_id"]
    run_dir = env.RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    # Per-run artifact (a copy of the record alone, easier to find).
    with open(run_dir / "record.json", "w") as f:
        json.dump(record, f, indent=2)

    # Append to the canonical JSONL ledger.
    with open(env.RUNS_JSONL, "a") as f:
        f.write(json.dumps(record) + "\n")

    return run_id, run_dir


def write_run(
    *,
    study_code: str,
    study_path: Path,
    study_meta: dict,
    selected_frames: list[dict],
    model_bundle: dict,
    palette_path: Path,
    palette_sha256: str,
    resolved_class_ids: dict,
    runtime_bridge: list[str],
    frame_features: list[dict],
    aggregate: dict,
    placements: dict,
    mode: str,
    frame_set: Optional[dict] = None,
    lifecycle: str = "finalized",
    notes: Optional[str] = None,
) -> tuple[str, Path]:
    """Build + persist a ledger record in one call (the pre-N4 behaviour).

    Thin wrapper over `build_record` + `append_record` so the preflight
    stage1/2/3 CLI path and any direct-to-ledger caller stays untouched.

    Returns (run_id, run_dir).
    """
    _run_id, record = build_record(
        study_code=study_code,
        study_path=study_path,
        study_meta=study_meta,
        selected_frames=selected_frames,
        model_bundle=model_bundle,
        palette_path=palette_path,
        palette_sha256=palette_sha256,
        resolved_class_ids=resolved_class_ids,
        runtime_bridge=runtime_bridge,
        frame_features=frame_features,
        aggregate=aggregate,
        placements=placements,
        mode=mode,
        frame_set=frame_set,
        lifecycle=lifecycle,
        notes=notes,
    )
    return append_record(record)


def iter_runs() -> list[dict]:
    """Yield all records from runs.jsonl in append order."""
    if not env.RUNS_JSONL.exists():
        return []
    out: list[dict] = []
    with open(env.RUNS_JSONL) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                # Skip malformed; ledger is append-only so this should be rare.
                pass
    return out


def runs_for_study(study_code: str, study_uid: Optional[str] = None) -> list[dict]:
    """Runs for a study. Keyed on the collision-free `study_uid` when given (the
    AKA study_code repeats across studies, so keying on it alone would mix DIFFERENT
    patients' runs); falls back to study_code for a legacy caller / a legacy record
    with no study_path."""
    from .study_metadata import run_study_uid
    def _match(r: dict) -> bool:
        if study_uid is not None:
            ru = run_study_uid(r)
            return ru == study_uid if ru is not None else r.get("study_code") == study_code
        return r.get("study_code") == study_code
    return [r for r in iter_runs() if _match(r)]

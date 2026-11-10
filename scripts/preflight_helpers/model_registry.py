"""Model registry — walks both model dirs, validates each bundle, returns metadata.

Validation = load smoke + JSON parses + output-channel count probed from
loaded model (not from JSON). The Streamlit UI shows invalid bundles but
disables them (with the validation error visible in the dev expander).

Bundles are listed from two sources per Akadir 2026-05-12:
  - `~/dev/muscle_model_training/transport/models/` (5-fold ensemble + others)
  - `~/dev/muscle_mania/model_/` (manually-staged bundle shipping with the app)

Default selection at boot = the most-recent-mtime bundle from
transport/models/ that successfully validates.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import functools
import hashlib
import json

from . import env

# The explicit, version-controlled "which bundle is canonical" pointer. Serving
# resolves this first (sha-guarded) instead of picking the newest mtime — so the
# live segmenter is provably the one the cohort ruler + condition model were
# calibrated on. See configs/canonical_bundle.json for the full rationale.
CANONICAL_POINTER_PATH = env.REPO_ROOT / "configs" / "canonical_bundle.json"


@dataclass(frozen=True)
class BundleEntry:
    pkl_path: Path
    json_path: Path
    source: str  # 'local' or 'transport'
    valid: bool
    validation_error: Optional[str] = None
    # Metadata (when loadable)
    modalities: tuple[str, ...] = ()
    stats_mean: tuple[float, ...] = ()
    stats_std: tuple[float, ...] = ()
    resize_to: tuple[int, int] = (0, 0)
    rrc_size: tuple[int, int] = (0, 0)
    fold: Optional[int] = None
    arch: Optional[str] = None
    encoder: Optional[str] = None
    pkl_sha256: Optional[str] = None
    mtime: float = 0.0

    def display_name(self) -> str:
        return self.pkl_path.stem


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_json_meta(json_path: Path) -> dict:
    with open(json_path) as f:
        return json.load(f)


def _try_validate(pkl_path: Path, source: str, *, compute_hash: bool = True) -> BundleEntry:
    """Static validation only — JSON parse + sibling pkl exists.

    Does NOT load the model (too expensive for a registry pass; load smoke is
    deferred to the actual selection step).
    """
    json_path = pkl_path.with_suffix(".json")
    if not json_path.exists():
        return BundleEntry(
            pkl_path=pkl_path,
            json_path=json_path,
            source=source,
            valid=False,
            validation_error="sibling_json_missing",
            mtime=pkl_path.stat().st_mtime,
        )
    try:
        meta = _load_json_meta(json_path)
        modalities = tuple(meta.get("modalities", ()))
        stats = meta.get("stats", [[], []])
        means = tuple(stats[0]) if len(stats) > 0 else ()
        stds = tuple(stats[1]) if len(stats) > 1 else ()
        train_cfg = meta.get("train_cfg", {})
        resize_to = tuple(train_cfg.get("resize_to", (0, 0))) or (0, 0)
        rrc = tuple(train_cfg.get("rrc_size", (0, 0))) or (0, 0)
    except Exception as e:
        return BundleEntry(
            pkl_path=pkl_path,
            json_path=json_path,
            source=source,
            valid=False,
            validation_error=f"json_parse_error: {e}",
            mtime=pkl_path.stat().st_mtime,
        )

    return BundleEntry(
        pkl_path=pkl_path,
        json_path=json_path,
        source=source,
        valid=True,
        modalities=modalities,
        stats_mean=means,
        stats_std=stds,
        resize_to=resize_to,
        rrc_size=rrc,
        fold=train_cfg.get("fold"),
        arch=train_cfg.get("arch"),
        encoder=train_cfg.get("encoder"),
        pkl_sha256=_sha256_of(pkl_path) if compute_hash else None,
        mtime=pkl_path.stat().st_mtime,
    )


def list_bundles(*, compute_hash: bool = False) -> list[BundleEntry]:
    """Walk both model dirs and return validated bundle entries.

    `compute_hash` is False by default to keep the registry pass cheap; the
    actual run-time selection step recomputes the hash for ledger provenance.
    """
    out: list[BundleEntry] = []
    for src, root in (("local", env.MODEL_DIR_LOCAL), ("transport", env.MODEL_DIR_TRANSPORT)):
        if not root.exists():
            continue
        for pkl in sorted(root.glob("*.pkl")):
            out.append(_try_validate(pkl, source=src, compute_hash=compute_hash))
    return out


class BundleIntegrityError(RuntimeError):
    """The on-disk canonical bundle does not match its pinned sha256 — i.e. the
    serving segmenter is not the one the cohort/score were calibrated on. Raised
    loudly on purpose: a silent mismatch is exactly the train/serve skew we fixed."""


def load_canonical_pointer() -> Optional[dict]:
    """Parse configs/canonical_bundle.json, or None if absent/unreadable.

    None → callers fall back to the legacy most-recent-mtime pick, so a machine
    without the pointer still boots (just without the canonical guarantee)."""
    p = CANONICAL_POINTER_PATH
    if not p.exists():
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


@functools.lru_cache(maxsize=64)
def _sha256_cached(path_str: str, mtime: float, size: int) -> str:
    # Keyed on (path, mtime, size) so an edited/replaced pkl re-hashes; keeps the
    # per-rerun serving path from re-reading 34 MB on every Streamlit interaction.
    return _sha256_of(Path(path_str))


def _sha256_memo(path: Path) -> str:
    stt = path.stat()
    return _sha256_cached(str(path), stt.st_mtime, stt.st_size)


def _mtime_default(bundles: list[BundleEntry]) -> Optional[BundleEntry]:
    """Legacy fallback: most-recent-mtime valid bundle (transport wins ties)."""
    valid = [b for b in bundles if b.valid]
    if not valid:
        return None
    valid.sort(key=lambda b: (b.mtime, 0 if b.source == "transport" else 1), reverse=True)
    return valid[0]


def is_canonical(b: BundleEntry, pointer: Optional[dict] = None) -> bool:
    """True if this bundle's stem matches the canonical pointer (stem only —
    the sha is enforced at selection time in default_bundle)."""
    ptr = pointer if pointer is not None else load_canonical_pointer()
    return bool(ptr) and b.pkl_path.stem == ptr.get("stem")


def default_bundle(bundles: list[BundleEntry], *, enforce_sha: bool = True) -> Optional[BundleEntry]:
    """The serving default: the explicitly-pinned canonical bundle
    (configs/canonical_bundle.json), sha-guarded — NOT the newest mtime.

    Why not mtime: the live measurement must be segmented on the same bundle the
    cohort ruler + condition model were calibrated on (v1.1 = native 512×256
    fold4). A newer-mtime bundle (e.g. a padded retrain) silently reintroduces
    the train/serve skew the v1.1 re-base removed.

    Resolution:
      1. Pointer names a stem present + valid among `bundles` → return it,
         sha-checked against the pointer (mismatch → BundleIntegrityError).
         Tie-break: prefer the app-local copy (the ship-with-the-app bundle),
         then transport — both are byte-identical when present.
      2. Pointer absent, or its stem not on this machine → legacy
         most-recent-mtime fallback (keeps a pointer-less machine working)."""
    ptr = load_canonical_pointer()
    if ptr and ptr.get("stem"):
        matches = [b for b in bundles if b.valid and b.pkl_path.stem == ptr["stem"]]
        if matches:
            matches.sort(key=lambda b: 0 if b.source == "local" else 1)
            chosen = matches[0]
            want = ptr.get("sha256")
            if want and enforce_sha:
                got = _sha256_memo(chosen.pkl_path)
                if got != want:
                    raise BundleIntegrityError(
                        f"Canonical bundle sha256 mismatch for {chosen.pkl_path.name}: "
                        f"pointer expects {want[:12]}…, disk has {got[:12]}…. The serving "
                        f"segmenter is not the one the v1.1 cohort was calibrated on."
                    )
            return chosen
        # Pointer present but that stem is not on this machine → fall through.
    return _mtime_default(bundles)


def resolve_canonical_pkl() -> Optional[Path]:
    """The canonical bundle's pkl Path via the same pointer logic as the app
    default — for scripts that would otherwise glob `*.pkl` by mtime
    (calibration / trace / verify), so every entry point is provably one
    segmenter rather than accidentally-native because model_/ holds one file."""
    b = default_bundle(list_bundles(compute_hash=False))
    return b.pkl_path if b else None

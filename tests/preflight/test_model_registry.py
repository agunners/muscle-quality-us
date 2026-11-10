"""Model registry finds bundles in both registries, validates JSON, and picks
the *canonical* default — the sha-pinned bundle the v1.1 cohort was cut on,
NOT the newest-mtime bundle (which reintroduces the train/serve skew)."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from preflight_helpers import model_registry as mr
from preflight_helpers.model_registry import (
    BundleEntry, BundleIntegrityError, default_bundle, is_canonical,
    list_bundles,
)


# ---------------------------------------------------------------- helpers ----

def _mk_bundle(tmp_path: Path, name: str, content: bytes, *,
               source: str = "local", mtime: float = 1000.0,
               valid: bool = True) -> BundleEntry:
    pkl = tmp_path / f"{name}.pkl"
    pkl.write_bytes(content)
    return BundleEntry(
        pkl_path=pkl, json_path=pkl.with_suffix(".json"),
        source=source, valid=valid, mtime=mtime,
    )


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


# ------------------------------------------------------------ integration ----

@pytest.mark.integration
def test_finds_bundles_in_both_dirs() -> None:
    bundles = list_bundles(compute_hash=False)
    sources = {b.source for b in bundles}
    assert "local" in sources, "no bundles found in muscle_mania/model_/"
    assert "transport" in sources, "no bundles found in muscle_model_training/transport/models/"


@pytest.mark.integration
def test_default_is_the_canonical_pinned_bundle() -> None:
    """On this machine the serving default MUST be the pinned canonical bundle
    (the one the v1.1 cohort + score were calibrated on), not newest-mtime.
    default_bundle also sha-verifies it, so this passing == serving matches the
    ruler (the train/serve skew is closed)."""
    ptr = mr.load_canonical_pointer()
    assert ptr and ptr.get("stem"), "configs/canonical_bundle.json missing or malformed"
    bundles = list_bundles(compute_hash=False)
    db = default_bundle(bundles)  # raises BundleIntegrityError on sha mismatch
    assert db is not None and db.valid
    assert db.pkl_path.stem == ptr["stem"], "serving default is not the canonical bundle"
    assert is_canonical(db)


# -------------------------------------------------------------------- unit ----

def test_default_prefers_canonical_pointer_over_newer_mtime(tmp_path, monkeypatch) -> None:
    """The pinned canonical bundle wins even when a *newer*-mtime bundle exists
    — the exact case that was serving padded_0329 over native_fold4."""
    canon = _mk_bundle(tmp_path, "native_fold4", b"CANON-BYTES", source="local", mtime=100.0)
    newer = _mk_bundle(tmp_path, "padded_newer", b"PADDED", source="transport", mtime=999.0)
    monkeypatch.setattr(mr, "load_canonical_pointer",
                        lambda: {"stem": "native_fold4", "sha256": _sha(b"CANON-BYTES")})
    chosen = default_bundle([newer, canon])
    assert chosen.pkl_path.stem == "native_fold4"


def test_default_raises_on_sha_mismatch(tmp_path, monkeypatch) -> None:
    """A bundle whose bytes don't match the pinned sha is a wrong/corrupt
    serving model — loud failure, never a silent substitution."""
    canon = _mk_bundle(tmp_path, "native_fold4", b"CANON-BYTES")
    monkeypatch.setattr(mr, "load_canonical_pointer",
                        lambda: {"stem": "native_fold4", "sha256": "0" * 64})
    with pytest.raises(BundleIntegrityError):
        default_bundle([canon])


def test_default_falls_back_to_mtime_without_pointer(tmp_path, monkeypatch) -> None:
    """No pointer → legacy most-recent-mtime pick (a pointer-less machine boots)."""
    older = _mk_bundle(tmp_path, "a", b"A", mtime=1.0, source="local")
    newer = _mk_bundle(tmp_path, "b", b"B", mtime=2.0, source="transport")
    monkeypatch.setattr(mr, "load_canonical_pointer", lambda: None)
    chosen = default_bundle([older, newer])
    assert chosen.pkl_path.stem == "b"


def test_default_falls_back_when_canonical_absent_on_machine(tmp_path, monkeypatch) -> None:
    """Pointer names a bundle not present here → mtime fallback, no crash."""
    present = _mk_bundle(tmp_path, "present", b"X", mtime=5.0)
    monkeypatch.setattr(mr, "load_canonical_pointer",
                        lambda: {"stem": "missing_bundle", "sha256": "abc"})
    chosen = default_bundle([present])
    assert chosen.pkl_path.stem == "present"


def test_sha_guard_can_be_disabled(tmp_path, monkeypatch) -> None:
    """enforce_sha=False resolves by stem without hashing (for a fast display path)."""
    canon = _mk_bundle(tmp_path, "native_fold4", b"CANON-BYTES")
    monkeypatch.setattr(mr, "load_canonical_pointer",
                        lambda: {"stem": "native_fold4", "sha256": "0" * 64})
    chosen = default_bundle([canon], enforce_sha=False)
    assert chosen.pkl_path.stem == "native_fold4"

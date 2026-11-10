#!/usr/bin/env python3
"""check_parity.py — fingerprint this machine's data + code + env, for cross-machine parity.

The migration taught us the hard way: "the files copied" is NOT "the app agrees". This reproduces
the study picker's own counts (studies_root + the 272 backup cohort), checks that every env.py data
root actually resolves, and checksums the frozen artifacts — so two machines can be *proven*
identical, or the exact gap named, with one command. No screenshots.

Run from the repo root (needs the venv + the PYTHONPATH bridge):
    source scripts/env.sh && .venv/bin/python scripts/check_parity.py
        default          print the fingerprint AND compare data to the committed baseline
        --save-baseline  (re)write scripts/parity_baseline.json from THIS machine, then commit it
                         (do this on the source-of-truth machine after the data is complete)
        --json           raw JSON fingerprint only (diff two machines' outputs directly)

Exit code is non-zero on a data mismatch, so it doubles as a gate.
PHI-safe: counts + short checksums only — never a study name or path.
"""
from __future__ import annotations
import argparse, hashlib, json, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent            # .../muscle_mania/scripts
sys.path.insert(0, str(HERE))                     # make `preflight_helpers` importable headlessly
REPO = HERE.parent
DEV = Path.home() / "dev"
BASELINE = HERE / "parity_baseline.json"

# Fields that MUST match across machines = data parity. git head advances via commits and
# `uncommitted` is machine-local; `env` is per-machine (python/torch) — so those are reported
# but NOT compared.
PARITY_KEYS = ("studies", "roots", "checksums")


def _sh(args, cwd):
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=30).stdout
    except Exception:
        return ""


def _git(repo: Path) -> dict:
    if not (repo / ".git").exists():
        return {"head": None, "uncommitted": None}
    head = _sh(["git", "rev-parse", "--short", "HEAD"], repo).strip() or None
    porc = _sh(["git", "status", "--porcelain"], repo)
    return {"head": head, "uncommitted": sum(1 for ln in porc.splitlines() if ln.strip())}


def _sha16(p: Path):
    p = Path(p)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _n(p, glob: str):
    """Count items under p matching glob; None if p is missing (missing != present-but-empty)."""
    p = Path(p)
    return None if not p.exists() else sum(1 for _ in p.glob(glob))


def fingerprint() -> dict:
    from preflight_helpers import env, study_discovery as sd
    local = sd.discover_studies()
    cohort = sd.discover_backup_cohort()

    def counts(entries):
        return {"discovered": len(entries),
                "valid": sum(1 for e in entries if e.valid),
                "new_round": sum(1 for e in entries if sd.study_is_new_round(e.study_path))}

    fp = {
        "git": {r: _git(DEV / r) for r in
                ("muscle_mania", "muscle_quality_exploration", "muscle_model_training")},
        "studies": {"local": counts(local), "cohort": counts(cohort)},
        "roots": {
            "goruntuler_backup_frames": _n(env.GORUNTULER_BACKUP, "**/RawImagesForResearch_*"),
            "transport_metadata": env.TRANSPORT_METADATA.is_file(),
            "decision_log": env.DECISION_LOG.is_file(),
            "decision_log_3op": env.DECISION_LOG_3OP.is_file(),
            "study_codes": env.STUDY_CODES_CSV.is_file(),
            "model_transport_bundles": _n(env.MODEL_DIR_TRANSPORT, "*.pkl"),
            "model_local_bundles": _n(env.MODEL_DIR_LOCAL, "*.pkl"),
        },
        "checksums": {
            "per_study_cohort.csv": _sha16(env.COHORT_STATS_DIR / "per_study_cohort.csv"),
            "condition_models.json": _sha16(env.COHORT_STATS_DIR / "condition_models.json"),
            "study_codes.csv": _sha16(env.STUDY_CODES_CSV),
        },
        "env": {"python": ".".join(map(str, sys.version_info[:3]))},
    }
    try:
        import torch
        fp["env"]["torch"] = torch.__version__
        fp["env"]["cuda"] = torch.version.cuda
    except Exception:
        fp["env"]["torch"] = None
    return fp


def _walk(path, a, b, out):
    if isinstance(a, dict) or isinstance(b, dict):
        for k in sorted(set(a or {}) | set(b or {})):
            _walk(f"{path}.{k}", (a or {}).get(k), (b or {}).get(k), out)
    elif a != b:
        out.append(f"{path}:  this={a!r}   baseline={b!r}")


def compare(fp, base) -> list:
    out = []
    for k in PARITY_KEYS:
        _walk(k, fp.get(k), base.get(k), out)
    return out


def main():
    ap = argparse.ArgumentParser(description="cross-machine parity fingerprint")
    ap.add_argument("--save-baseline", action="store_true", help="write parity_baseline.json from this machine")
    ap.add_argument("--json", action="store_true", help="raw JSON only")
    args = ap.parse_args()

    fp = fingerprint()

    if args.json:
        print(json.dumps(fp, indent=2, sort_keys=True))
        return
    if args.save_baseline:
        base = {k: fp[k] for k in PARITY_KEYS}
        BASELINE.write_text(json.dumps(base, indent=2, sort_keys=True) + "\n")
        print(f"baseline written -> {BASELINE}\n(commit it; the OTHER machine compares against it)")
        return

    g = fp["git"]; s = fp["studies"]
    print("=== parity fingerprint ===")
    print("  git:    " + "   ".join(f"{r.split('_')[-1]}={g[r]['head']}(+{g[r]['uncommitted']} unc)" for r in g))
    print(f"  local : {s['local']}")
    print(f"  cohort: {s['cohort']}")
    print(f"  roots : {fp['roots']}")
    print(f"  env   : {fp['env']}")

    if not BASELINE.is_file():
        print("\n  no baseline yet — on the source-of-truth machine run:  ...check_parity.py --save-baseline  (then commit)")
        return
    diffs = compare(fp, json.loads(BASELINE.read_text()))
    if not diffs:
        print("\n  DATA PARITY: MATCH ✓   (studies + roots + checksums identical to the baseline)")
    else:
        print(f"\n  DATA PARITY: {len(diffs)} MISMATCH(es) vs baseline:")
        for d in diffs:
            print("    ✗ " + d)
        sys.exit(1)


if __name__ == "__main__":
    main()

"""Check TRANSITION.yaml against the machine — the gate before packing a departure, and the first
thing to run after an arrival.

A manifest is a promise; this script is what turns it into a claim with evidence. It verifies, for the
muscle work as a whole: every side repo exists at its canonical path with the right remote, clean and
pushed; every heavy tree is present with the counts the manifest states; the Claude memory slug is in
place; the pack and arrival scripts exist; the machine-transition skill resolves; and the per-machine
logins (GitHub, Google Workspace, the Hetzner host) answer.

    .venv/bin/python scripts/transition_check.py            # before packing, and after arriving
    .venv/bin/python scripts/transition_check.py --offline  # skip the three network/login checks

Exit 0 = every check passed. Exit 1 = at least one FAIL (a WARN never fails the run: it marks something
the manifest itself records as expected-absent, e.g. the fetch-on-demand run artifact).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
MANIFEST = REPO / "TRANSITION.yaml"
ROWS: list[tuple[str, str, str]] = []          # (verdict, what, evidence)


def check(what: str, ok: bool | None, evidence: str) -> None:
    ROWS.append(("PASS" if ok else ("WARN" if ok is None else "FAIL"), what, evidence))


def sh(cmd: str, cwd: Path | None = None, timeout: int = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def expand(p: str) -> Path:
    return Path(os.path.expanduser(p))


def check_repo(path: str, remote: str | None, label: str, allowed_dirty: list[str] | None = None) -> None:
    d = expand(path)
    if not d.is_dir():
        check(f"{label}: present", False, f"{d} does not exist")
        return
    if not (d / ".git").exists():
        check(f"{label}: present", None, f"{d} (data folder, no git — travels on the drive)")
        return
    rc, url = sh("git remote get-url origin", cwd=d)
    remote_ok = (remote in (None, "none") ) or (rc == 0 and remote.strip() == url.strip())
    # sh() strips, which eats porcelain's leading space, so parse code+path by splitting, not by column
    rc, dirty = sh("git status --porcelain --no-renames", cwd=d)
    entries = [ln.strip().split(maxsplit=1) for ln in dirty.splitlines() if ln.strip()]
    entries = [(e[0], e[1]) for e in entries if len(e) == 2]
    untracked = [p for code, p in entries if code == "??"]
    tracked_dirty = [p for code, p in entries if code != "??" and p not in (allowed_dirty or [])]
    rc, ahead = sh("git rev-list --count @{u}..HEAD", cwd=d)
    ahead_n = ahead.strip() if rc == 0 else "no upstream"
    rc, head = sh("git rev-parse --short HEAD", cwd=d)
    ok = remote_ok and ahead_n == "0" and not tracked_dirty
    check(f"{label}: clean + pushed", ok,
          f"{head} · remote {'ok' if remote_ok else 'MISMATCH: ' + url} · ahead {ahead_n} · "
          f"{len(tracked_dirty)} tracked modified{' (' + ', '.join(tracked_dirty[:2]) + ')' if tracked_dirty else ''}"
          f" · {len(untracked)} untracked")


def count(expr: str) -> str:
    rc, out = sh(expr, cwd=REPO, timeout=180)
    return out.strip().splitlines()[-1] if out.strip() else "?"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="skip the GitHub / Workspace / Hetzner checks")
    a = ap.parse_args()
    if not MANIFEST.is_file():
        print(f"no manifest at {MANIFEST}")
        return 1
    m = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))

    # --- the repos -----------------------------------------------------------------
    check_repo(m["canonical_path"], m.get("remote"), "muscle_mania", m.get("deliberately_uncommitted", []))
    for r in m.get("side_repos", []):
        check_repo(r["path"], r.get("remote"), Path(os.path.expanduser(r["path"])).name)

    # --- heavy trees ----------------------------------------------------------------
    ops = count("ls data/studies_root 2>/dev/null | wc -l")
    sessions = count("find data/studies_root -mindepth 2 -maxdepth 2 -type d 2>/dev/null | wc -l")
    studies = count("find data/studies_root -mindepth 3 -maxdepth 3 -type d 2>/dev/null | wc -l")
    check("studies_root", ops == "9" and int(studies or 0) >= 175,
          f"{ops} operators · {sessions} sessions · {studies} studies (ingest_all --status is the authority)")

    packs = sorted((REPO / "data/grading_pack").glob("pack_*"))
    if packs:
        rc, out = sh("sha256sum -c SHA256SUMS --quiet", cwd=packs[-1], timeout=900)
        nfiles = count(f"find {packs[-1]}/items -type f | wc -l")
        check("grading pack checksums", rc == 0, f"{packs[-1].name} · {nfiles} item files · "
                                                 f"{'all match' if rc == 0 else out.splitlines()[-1][:80]}")
    else:
        check("grading pack", False, "no data/grading_pack/pack_* on this machine")

    tm = REPO / "transport/transport_metadata.csv"
    check("transport", tm.is_file(), f"{'real folder' if (REPO / 'transport').is_dir() and not (REPO / 'transport').is_symlink() else 'symlink'} · "
                                     f"{count('wc -l < transport/transport_metadata.csv') if tm.is_file() else 'missing'} rows")

    gb = expand("~/dev/goruntuler_backup")
    check("goruntuler_backup frames", gb.is_dir(),
          f"{count('find ~/dev/goruntuler_backup -maxdepth 4 -name RawImagesForResearch_* -type d 2>/dev/null | wc -l')} frame dirs")

    bundles = count("ls ~/dev/muscle_model_training/transport/models 2>/dev/null | grep -c native_512x256_v5___300_samples____fold4")
    check("frozen segmenter bundle", bundles != "0", f"{bundles} matching entries in muscle_model_training/transport/models")

    # --- caches the manifest marks fetch-on-demand ----------------------------------
    for c in m.get("caches", []):
        if c.get("policy", "").startswith("fetch-on-demand"):
            p = expand(str(c["path"]).split(",")[0])
            check(f"cache {p.name}", True if p.is_file() else None,
                  f"{'present' if p.is_file() else 'ABSENT — manifest records the fetch: ' + str(c.get('resolution', ''))[:70]}")

    # --- Claude memory, the skill, the scripts ---------------------------------------
    slug = expand(f"~/.claude/projects/{m['claude']['memory_slug']}")
    n_mem = len(list((slug / "memory").glob("*.md"))) if (slug / "memory").is_dir() else 0
    check("claude memory", n_mem > 0, f"{n_mem} notes at {slug}")
    skill = expand("~/.claude/skills/machine-transition/SKILL.md")
    check("machine-transition skill", skill.is_file(),
          f"{'resolves -> ' + str(skill.resolve().parent.parent.parent) if skill.is_file() else 'NOT installed (clone the brain repo, then symlink)'}")
    for key in ("pack_script", "arrival_script", "runbook"):
        if key == "arrival_script":
            continue                               # lives on the drive, not in the repo
        p = REPO / m[key]
        check(key, p.is_file(), str(p.relative_to(REPO)))

    # --- per-machine logins -----------------------------------------------------------
    if not a.offline:
        rc, out = sh("gh auth status", timeout=25)
        check("github login", rc == 0, out.splitlines()[1].strip() if rc == 0 and len(out.splitlines()) > 1 else out[:80])
        if shutil.which("gws"):
            rc, out = sh("gws auth status", timeout=40)
            check("workspace login", "token_valid: true" in out or rc == 0, out.splitlines()[-1][:80] if out else "?")
        else:
            check("workspace login", None, "gws CLI not on PATH (per-machine; brain's gws-add-machine skill)")
        rc, out = sh("ssh -o BatchMode=yes -o ConnectTimeout=8 root@203.0.113.10 hostname", timeout=25)
        check("hetzner host (live grading table)", rc == 0,
              out.strip()[:60] if rc == 0 else "no key on the host yet — add ~/.ssh/id_*.pub to its authorized_keys")

    # --- report --------------------------------------------------------------------
    w = max(len(r[1]) for r in ROWS) + 2
    print(f"\nTRANSITION CHECK — {m['project']}\n{'-' * (w + 62)}")
    for verdict, what, ev in ROWS:
        print(f"  {verdict:<5} {what:<{w}} {ev}")
    fails = [r for r in ROWS if r[0] == "FAIL"]
    warns = [r for r in ROWS if r[0] == "WARN"]
    print(f"{'-' * (w + 62)}\n  {len(ROWS) - len(fails) - len(warns)} pass · {len(warns)} warn · {len(fails)} FAIL\n")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

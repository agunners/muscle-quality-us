#!/usr/bin/env python
"""Re-stamp `source_zip_verdict` in every materialize_manifest.json from the UNIFIED classifier.

Why this exists. Until 2026-07-30 `plan_zip` stamped `cs.check_study(...).headline`, which tests
the femur at an ABSOLUTE 6.9 cm, so a femur imaged at 7.9 cm read "femur (6.9cm) eksik". Every
manifest written before the fix carries that false negative as a PERMANENT record — the 2026-07-30
batch read ⚠️80 / ❌13 with zero ✅ while the operator sheet, which uses the unified relative-rank
`study_qa`, correctly read ✅. A manifest is provenance; a wrong verdict in it misleads whoever
reads it next.

Only that one advisory field is touched. Roles, depths, captures and every other key are left
exactly as written — those were always computed correctly and are independent of the verdict.

Idempotent: the verdict is recomputed from the cached source zip each run, so re-running is a
no-op once clean. Needs the zip in `cache_dir` (data/intake_cache); a study whose zip is gone is
reported and skipped rather than guessed at.

    python scripts/repair_manifest_verdicts.py            # dry-run: show what would change
    python scripts/repair_manifest_verdicts.py --apply    # write
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from preflight_helpers import env                      # noqa: E402
from study_qa import study_qa, render_cell             # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = REPO_ROOT / "data" / "intake_cache"


def _tag(verdict: str) -> str:
    v = (verdict or "").strip()
    for mark in ("✅", "⚠️", "❌"):
        if v.startswith(mark):
            return mark
    return "—"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write (default: dry-run)")
    ap.add_argument("--root", default=None, help="studies_root (default: env.STUDIES_ROOT)")
    args = ap.parse_args(argv)

    root = Path(args.root) if args.root else Path(env.STUDIES_ROOT)
    manifests = sorted(root.glob("*/*/*/materialize_manifest.json"))
    print(f"=== MANIFEST VERDICT REPAIR ({'APPLY' if args.apply else 'DRY-RUN'}) === "
          f"{len(manifests)} manifest(s) under {root}")

    before, after = collections.Counter(), collections.Counter()
    changed = unchanged = no_zip = failed = 0

    for mf in manifests:
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
        except Exception as e:                                   # noqa: BLE001
            print(f"  [error] unreadable {mf.parent.name}: {e}")
            failed += 1
            continue

        study, operator = m.get("study", ""), m.get("operator", "")
        old = m.get("source_zip_verdict", "")
        before[_tag(old)] += 1

        zip_path = CACHE_DIR / f"{study}.zip"
        if not zip_path.is_file():
            print(f"  [skip] no cached zip for {m.get('session', '?')} — verdict left as-is")
            after[_tag(old)] += 1
            no_zip += 1
            continue

        try:
            new = render_cell(study_qa(str(zip_path), operator, study=study))
        except Exception as e:                                   # noqa: BLE001
            print(f"  [error] {m.get('session', '?')}: {e}")
            after[_tag(old)] += 1
            failed += 1
            continue

        after[_tag(new)] += 1
        if new.strip() == (old or "").strip():
            unchanged += 1
            continue

        changed += 1
        print(f"  {'Δ' if args.apply else '~'} {m.get('session', '?'):<16} "
              f"{_tag(old)} -> {_tag(new)}   {new[:64]}")
        if args.apply:
            m["source_zip_verdict"] = new
            m["source_zip_verdict_classifier"] = "study_qa (unified relative-rank)"
            mf.write_text(json.dumps(m, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"\nbefore: {dict(before)}\nafter : {dict(after)}")
    print(f"changed {changed} · already-correct {unchanged} · no-zip {no_zip} · errors {failed}")
    if not args.apply and changed:
        print("dry-run — re-run with --apply to write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

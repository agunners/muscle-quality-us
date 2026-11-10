#!/usr/bin/env python
"""The HUMAN half of the flag→kill ledger — record a kill or clear verdict on a flagged measurement.

The gate flags automatically; KILL and CLEAR are human-only transitions (Akadir or delegate,
[[project_flag_kill_ledger]]). This appends the verdict to `data/kill_list.jsonl` — it never edits
the operators' sheet; the next `sync_force_measures` pass propagates the verdict into the
'Ölçüm Kontrol' column, and the next substrate build applies it to `qa_master.csv` (a KILL blanks
the cells permanently for these values; a CLEAR releases them back into analysis).

  python scripts/measurement_verdict.py                       # list current measurement verdicts
  python scripts/measurement_verdict.py --kill  --uid NR0D53363E --by Akadir --evidence "source audit: kgf·m confirmed"
  python scripts/measurement_verdict.py --clear --uid NR245F7FC5 --by Akadir --evidence "source audit: value is genuine"

A verdict copies the flagged thread's fields and value snapshot, so it applies to THOSE values: if
the sheet cell later changes, the gate re-flags the new value and the thread re-opens.

FEATURE scope (2026-08-13, the texture un-withhold path) — the same instrument for the 3-way
thickness gate's texture withholds. A CLEAR is Akadir's recorded consent to USE the centre-RF
texture although the views disagree (he reviewed the boundary and accepted it); a KILL makes the
withhold permanent (the AKA017 pattern). A clear snapshots the three per-view max thicknesses and
is honored by the next freeze only while those values still match — a re-measured study re-enters
the gate. Applies at the NEXT freeze (cohort membership is frozen, never live-edited):

  python scripts/measurement_verdict.py --scope feature --clear --uid NR9A3342EF --by Akadir --evidence "reviewed in viewer: boundary real"
  python scripts/measurement_verdict.py --scope feature --kill  --uid NR1FB80768 --by Akadir --evidence "boundary not resolvable; re-take"
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from preflight_helpers import flag_kill_ledger as fkl


def _texture_snapshot(uid: str) -> dict:
    """The three per-view RF max thicknesses a texture CLEAR binds to (release_actions owns the
    read — one implementation for CLI and app). Aborts loudly when unavailable, because a clear
    without a snapshot could never go stale and would override the gate forever."""
    from preflight_helpers.release_actions import texture_snapshot
    snap = texture_snapshot(uid)
    if snap is None:
        sys.exit(f"cannot snapshot the clear's binding values for {uid}: landmark JSON absent "
                 "or the uid is not in it — run derive_new_round_cohort.py (or copy its "
                 "landmark JSON) on this machine first, and check the uid.")
    return snap


def list_state() -> int:
    ent = [e for e in fkl.current().values() if e["scope"] == "measurement"]
    feat = [e for e in fkl.current().values() if e["scope"] == "feature"]
    if not ent and not feat:
        print("no measurement- or feature-scope entries in the ledger.")
        return 0
    if feat:
        print("feature scope (texture withholds/clears; applies at the next freeze):")
        for e in sorted(feat, key=lambda x: x.get("study_uid") or ""):
            print(f"  {e.get('study_uid', '?'):12} {e['status']:8} "
                  f"{'+'.join(e.get('fields') or ())}  ({e.get('decided_by', '?')})")
        print()
    if not ent:
        return 0
    print(f"{'uid':12} {'row':>4} {'status':8} {'tier':4}  fields / rules / values")
    for e in sorted(ent, key=lambda x: (x["status"], x.get("sheet_row_at_flag") or 0)):
        vals = e.get("values") or {}
        short = {"izo_torque_nm": "izo", "izo_cv_pct": "izo_cv", "isok_torque_ext_nm": "isok"}
        vtxt = " ".join(f"{s}={vals.get(k)}" for k, s in short.items() if vals.get(k))
        print(f"{e.get('study_uid', '?'):12} {e.get('sheet_row_at_flag') or '—':>4} "
              f"{e['status']:8} {e.get('tier') or '—':4}  "
              f"{'+'.join(e.get('fields') or ())} / {'+'.join(e.get('rules') or ())} / {vtxt}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--kill", action="store_true", help="pronounce the flagged values invalid (permanent for these values)")
    g.add_argument("--clear", action="store_true", help="accept the flagged values back into analysis")
    ap.add_argument("--uid", help="study_uid of the flagged thread (see the list output)")
    ap.add_argument("--fields", help="comma-separated field subset; default = the single open thread's fields")
    ap.add_argument("--by", help="who decided — a person, never a script (required for kill/clear)")
    ap.add_argument("--evidence", default="", help="why — the audit finding behind the verdict")
    ap.add_argument("--scope", choices=("measurement", "feature", "study"), default="measurement",
                    help="measurement = dynamometer cells (default); feature = a per-study feature "
                         "withhold/clear (texture un-withhold path); study = kill a materialized "
                         "study outright (duplicate ingest / dead recording — permanent)")
    a = ap.parse_args(argv)

    if not (a.kill or a.clear):
        return list_state()
    if not a.uid or not a.by:
        ap.error("--kill/--clear need --uid and --by (verdicts are a person's, on the record)")

    if a.scope == "study":
        if a.clear:
            ap.error("a study kill is permanent by design (a re-recorded patient arrives as a NEW "
                     "study) — there is no study-scope clear")
        fkl.append({"scope": "study", "status": "killed", "study_uid": a.uid,
                    "evidence": a.evidence, "decided_by": a.by})
        print(f"KILLED study {a.uid} — recorded by {a.by}. The folder stays on disk; the study "
              f"leaves every cohort at the next freeze and the census reports it decided.")
        return 0

    if a.scope == "feature":
        fields = sorted(f.strip() for f in (a.fields or "int_cv_within").split(",") if f.strip())
        entry = {"scope": "feature", "status": "killed" if a.kill else "cleared",
                 "study_uid": a.uid, "fields": fields,
                 "evidence": a.evidence, "decided_by": a.by}
        if a.clear and fields == ["int_cv_within"]:
            entry["values"] = _texture_snapshot(a.uid)
        fkl.append(entry)
        word = "KILLED (withheld permanently)" if a.kill else "CLEARED (consent to use)"
        print(f"{word} {a.uid} {'+'.join(fields)} — recorded by {a.by}.")
        print("propagation: applies at the NEXT freeze (derive_new_round_cohort); a clear is "
              "honored only while the per-view thicknesses still match its snapshot.")
        return 0

    threads = [e for e in fkl.current().values()
               if e["scope"] == "measurement" and e.get("study_uid") == a.uid]
    if a.fields:
        want = set(f.strip() for f in a.fields.split(","))
        threads = [e for e in threads if set(e.get("fields") or ()) == want]
    if not threads:
        print(f"no measurement thread for uid {a.uid}"
              + (f" with fields {a.fields}" if a.fields else "") + " — nothing to verdict.")
        return 1
    if len(threads) > 1:
        print(f"{len(threads)} threads for uid {a.uid} — name one with --fields:")
        for e in threads:
            print(f"  --fields {','.join(e.get('fields') or ())}   ({e['status']}, "
                  f"rules {'+'.join(e.get('rules') or ())})")
        return 1

    t = threads[0]
    entry = {"scope": "measurement", "status": "killed" if a.kill else "cleared",
             "study_uid": a.uid, "fields": t.get("fields") or [],
             "rules": t.get("rules") or [], "values": t.get("values") or {},
             "evidence": a.evidence, "decided_by": a.by}
    if t.get("sheet_row_at_flag") is not None:
        entry["sheet_row_at_flag"] = t["sheet_row_at_flag"]
    fkl.append(entry)
    word = "KILLED" if a.kill else "CLEARED"
    print(f"{word} {a.uid} {'+'.join(entry['fields'])} — recorded by {a.by}.")
    print("propagation: next `sync_force_measures --apply` updates the sheet column; "
          "next `build_qa_sheet --analysis` applies it to qa_master.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

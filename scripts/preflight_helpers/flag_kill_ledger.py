"""The flag→kill ledger — one append-only record of graded data verdicts, at three grains.

`data/kill_list.jsonl` IS this ledger (the name predates the flag grain and is kept for continuity
of Akadir's 2026-08-11 kill record; the founding row-96 line stays byte-identical). The design is
Akadir's (2026-08-12, [[project_flag_kill_ledger]]):

  four grains, four instruments — do not conflate:
    study        a dead recording or duplicate ingest, permanent (row 96; the AKA158 twin);
    measurement  a suspect CELL (the kgf·m torque rows) — study and scan stay;
    feature      one feature untrustworthy for one study (AKA017 texture) — lengths serve.
                 A feature CLEAR is the inverse instrument (2026-08-13): Akadir's recorded
                 consent to USE a feature the automatic gate distrusts (the texture
                 un-withhold). It binds to the per-view thickness values it was decided on;
                 if those move, the clear is stale and the gate's distrust stands.
    contribution an ACCEPTED run's vetted frames joining the study's canonical evidence pool
                 (the refine surface's "5+xth frame", 2026-08-14) — evidence addition, never a
                 trust verdict. Keyed per run (`fields` = [source_run_id], so multiple runs
                 coexist and a later entry for the SAME run supersedes it — status `revoked`
                 withdraws one). The freeze pools these frames into the served medians; the
                 3-way trust gate keeps reading the automatic values, so a contribution can
                 refine a number but never flip a trust decision or stale a recorded clear.

  the flow — any check may FLAG (automatic, cheap, recorded at once); a row carrying ANY open
  flag — hard or soft — is NO-FORCE EQUIVALENT: every criterion cell is held out of new analyses
  pending an explicit human verdict (Akadir's rule, 2026-08-12 — tier is audit priority, never
  hold policy). KILL / CLEAR are human-only transitions (Akadir or delegate), via
  scripts/measurement_verdict.py, recorded with evidence + date: CLEAR is the recorded consent
  that puts values back into analysis; KILL holds its fields forever and releases the rest.
  Nothing is ever deleted; the CURRENT state of a cell is the LAST entry for its identity
  (append-only, latest wins).

Two keyspaces, explicit on every entry (the NR-vs-legacy mis-join lesson): a STUDY kill of a row
that never materialized is keyed `key_sha8` = sha256(normalized study name)[:8] upper (what the
ingest census matches sheet-side); a STUDY kill of a materialized study (a duplicate ingest) and
all measurement/feature entries are keyed `study_uid` = study_metadata.study_uid_for_dirname
(what qa_master and the cohort join by). Both are PHI-free; entries never carry a study name.

Entry schema (v2; a legacy line without `scope` reads as scope=study, status=killed):
  {scope, status: flagged|killed|cleared, tier: hard|soft (flags only), study_uid | key_sha8,
   fields: [substrate column names] (measurement) | [feature names] (feature),
   rules: [torque_plausibility codes], values: {field: raw cell string} snapshot,
   sheet_row_at_flag, evidence, decided_by, date}
"""
from __future__ import annotations

import json
from datetime import date as _date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

REPO = Path(__file__).resolve().parents[2]
LEDGER = REPO / "data" / "kill_list.jsonl"

# the five criterion fields a value-comparison considers "the measurement" — weight_kg and other
# context keys in a snapshot are informative, never part of the same-value test (a name edit that
# reparses weight must not resurrect a cleared flag).
_VALUE_FIELDS = ("izo_torque_nm", "izo_cv_pct", "isok_torque_ext_nm",
                 "isok_mean_power_ext_w", "isok_cv_pct")


def _normalize(e: dict) -> dict:
    """A raw ledger line -> the v2 view. The founding kill line predates `scope`/`status`."""
    if "scope" not in e:
        e = {**e, "scope": "study", "status": "killed"}
    return e


def entries(path: Optional[Path] = None) -> List[dict]:
    path = path or LEDGER
    if not path.exists():
        return []
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            out.append(_normalize(json.loads(ln)))
    return out


def append(entry: dict, path: Optional[Path] = None) -> None:
    """One entry, one line, append-only. The caller supplies a complete entry; `date` defaults to
    today. Never rewrites existing lines."""
    path = path or LEDGER
    entry = dict(entry)
    entry.setdefault("date", _date.today().isoformat())
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- current state (latest wins)
def _identity(e: dict) -> Tuple:
    """What an entry is ABOUT — a later entry with the same identity supersedes an earlier one.
    Fields are a set: the same cell-set under a different rule mix is still one verdict thread."""
    return (e["scope"], e.get("study_uid") or e.get("key_sha8"),
            frozenset(e.get("fields") or ()))


def current(path: Optional[Path] = None) -> Dict[Tuple, dict]:
    state: Dict[Tuple, dict] = {}
    for e in entries(path):
        state[_identity(e)] = e
    return state


def killed_name_hashes(path: Optional[Path] = None) -> set:
    """Sheet-side study kills (the census's exclusion set) — exactly what ingest_all.killed_keys
    returned before this module existed."""
    return {e["key_sha8"] for e in current(path).values()
            if e["scope"] == "study" and e["status"] == "killed" and e.get("key_sha8")}


def killed_study_uids(path: Optional[Path] = None) -> set:
    """Disk-side study kills (uid-keyed — a MATERIALIZED study pronounced dead, e.g. the AKA158
    duplicate ingest). The freeze's membership filter and the census's disk-side exclusion set —
    the uid twin of killed_name_hashes(). The folder stays on disk (nothing deletes data); the
    kill removes the study from every cohort and substrate built after it."""
    return {e["study_uid"] for e in current(path).values()
            if e["scope"] == "study" and e["status"] == "killed" and e.get("study_uid")}


def contributions(path: Optional[Path] = None) -> Dict[str, List[dict]]:
    """{study_uid: [active contribution entries]} — the accepted runs whose vetted frames join
    the study's canonical evidence pool at the next freeze. Identity is per run (`fields` =
    [source_run_id]), so several runs contribute side by side and a `revoked` entry for the
    same run withdraws exactly that one."""
    out: Dict[str, List[dict]] = {}
    for e in current(path).values():
        if e["scope"] == "contribution" and e["status"] == "active" and e.get("study_uid"):
            out.setdefault(e["study_uid"], []).append(e)
    return out


def feature_clears(path: Optional[Path] = None) -> Dict[str, Dict[str, dict]]:
    """{study_uid: {feature: entry}} for feature-grain CLEAR verdicts — Akadir's recorded consent
    to USE a feature the automatic trust gate distrusts (the texture un-withhold path,
    2026-08-13). Latest-wins applies: a later kill for the same (uid, feature) supersedes a
    clear and vice versa. The consumer (derive_new_round_cohort) must validate the entry's
    `values` snapshot against the currently measured per-view thicknesses before honoring it —
    a clear made on values that have since moved is STALE and must not resurrect the feature."""
    out: Dict[str, Dict[str, dict]] = {}
    for e in current(path).values():
        if e["scope"] == "feature" and e["status"] == "cleared" and e.get("study_uid"):
            for f in e.get("fields") or ():
                out.setdefault(e["study_uid"], {})[f] = e
    return out


def open_measurement_holds(path: Optional[Path] = None) -> Dict[str, Dict[str, dict]]:
    """{study_uid: {field: entry}} for every criterion cell HELD out of new analyses.

    Akadir's rule (2026-08-12): a row carrying ANY open measurement flag — hard or soft — is
    **no-force equivalent**: all five criterion cells are held, pending an explicit human verdict.
    Tier is signature strength (audit priority), never hold policy. The verdicts
    (measurement_verdict.py) are the consent instrument: CLEAR releases a thread's hold — using a
    flagged value is always an explicit, recorded human decision, never a default — and KILL holds
    exactly its fields forever (so killing the bad cells releases the rest of the row: the human
    has ruled, the pending uncertainty is gone)."""
    from .torque_plausibility import FORCE_FIELDS
    holds: Dict[str, Dict[str, dict]] = {}
    for e in current(path).values():
        if e["scope"] != "measurement" or not e.get("study_uid"):
            continue
        if e["status"] == "flagged":
            for f in FORCE_FIELDS:
                holds.setdefault(e["study_uid"], {})[f] = e
        elif e["status"] == "killed":
            for f in e.get("fields") or ():
                holds.setdefault(e["study_uid"], {})[f] = e
    return holds


def open_flags(path: Optional[Path] = None) -> List[dict]:
    """Every measurement-scope entry whose current status is `flagged` (both tiers) — what the
    census prints and the operator column renders."""
    return [e for e in current(path).values()
            if e["scope"] == "measurement" and e["status"] == "flagged"]


def feature_kills(path: Optional[Path] = None) -> Dict[str, set]:
    """{study_uid: {feature, ...}} for feature-grain kills (withholds). The freeze reads THIS
    (derive_new_round_cohort, migrated 2026-08-13 off the hand-maintained TEXTURE_WITHHOLD
    constant) — a verdict recorded via measurement_verdict.py --scope feature enforces at the
    next freeze without any code edit. Latest-wins: a later CLEAR for the same (study, feature)
    supersedes a kill, and vice versa."""
    out: Dict[str, set] = {}
    for e in current(path).values():
        if e["scope"] == "feature" and e["status"] == "killed" and e.get("study_uid"):
            out.setdefault(e["study_uid"], set()).update(e.get("fields") or ())
    return out


# ---------------------------------------------------------------- the gate's append decision
def _same_values(a: Optional[dict], b: Optional[dict]) -> bool:
    ga = {f: ((a or {}).get(f) or "").strip() for f in _VALUE_FIELDS}
    gb = {f: ((b or {}).get(f) or "").strip() for f in _VALUE_FIELDS}
    return ga == gb


def plan_flag(*, study_uid: str, tier: str, fields: Iterable[str], rules: Iterable[str],
              values: dict, evidence: str, sheet_row: Optional[int] = None,
              path: Optional[Path] = None) -> Optional[dict]:
    """The entry a fresh gate finding WOULD append, or None when the ledger already answers it.

    None when the latest entry for this cell-set carries the SAME five criterion values and is
      - flagged  (already recorded — the gate must not re-append every run),
      - cleared  (a human looked at exactly these values and accepted them — no re-nag), or
      - killed   (terminal for these values).
    A CHANGED value re-opens any of those as a fresh flag: the human verdict applied to the old
    value, not to the cell forever.
    """
    entry = {"scope": "measurement", "status": "flagged", "tier": tier, "study_uid": study_uid,
             "fields": sorted(set(fields)), "rules": sorted(set(rules)), "values": dict(values),
             "evidence": evidence, "decided_by": "gate:torque_plausibility"}
    if sheet_row is not None:
        entry["sheet_row_at_flag"] = sheet_row
    prev = current(path).get(_identity(entry))
    if prev is not None and _same_values(prev.get("values"), entry["values"]):
        return None
    return entry

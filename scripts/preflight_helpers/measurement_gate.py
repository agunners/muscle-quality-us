"""The measurement gate — plausibility findings applied to ledger, substrate, and operator sheet.

The one runner both entry points share, so a dynamometer value is checked wherever it flows and the
two can never disagree:

  * `sync_force_measures.py` (values ENTER the kayit sheet) evaluates the post-sync state, records
    flags, and writes the operator-facing 'Ölçüm Kontrol' column — measurement checks, deliberately
    a SEPARATE column from the recording verdict 'Kontrol Sonucu' (the recording-vs-measurement
    dedup lesson);
  * `build_qa_sheet.py` (values REACH analysis) re-evaluates whatever is on the sheet — including a
    value someone typed directly, never synced — and enforces the holds: a flagged row is
    NO-FORCE EQUIVALENT (every criterion cell blanked, any tier — Akadir's rule 2026-08-12), a
    killed thread's fields stay blank forever, and the receipt lands in `measurement_flags`.
    Using a flagged value is always an explicit recorded consent (a CLEAR verdict), never a
    default.

Pure logic + local-file ledger I/O only; no sheet access here (callers own their I/O and their
dry-run semantics).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from . import flag_kill_ledger as fkl
from . import torque_plausibility as tp

# operator column (kayit sheet) — header + the cell renderings
MEASUREMENT_CHECK_COL = "Ölçüm Kontrol"

# ledger field name (substrate vocabulary) -> load_sheet_rows / sheet-state dict key
FIELD_TO_SHEET_KEY = {
    "izo_torque_nm": "izo_torque",
    "izo_cv_pct": "izo_cv",
    "isok_torque_ext_nm": "isok_torque_ext_nm",
    "isok_mean_power_ext_w": "isok_mean_power_ext_w",
    "isok_cv_pct": "isok_cv_pct",
}


@dataclass
class StudyFindings:
    study_uid: str
    sheet_row: Optional[int]
    values: Dict[str, str]                      # raw criterion cells + weight_kg, ledger snapshot
    findings: List[tp.Finding] = field(default_factory=list)

    def by_tier(self, tier: str) -> List[tp.Finding]:
        return [f for f in self.findings if f.tier == tier]


def values_from_sheet_state(state: Dict[str, str]) -> Dict[str, str]:
    """A load_sheet_rows-style dict -> the classify()/ledger value snapshot."""
    vals = {f: (state.get(k) or "").strip() for f, k in FIELD_TO_SHEET_KEY.items()}
    vals["weight_kg"] = (state.get("weight_kg") or "").strip()
    return vals


def evaluate(states: Dict[str, Tuple[Optional[int], Dict[str, str]]]) -> List[StudyFindings]:
    """{study_uid: (sheet_row, value snapshot)} -> findings for every study with any criterion
    value. Studies with all-blank cells are facts about people, silently clean."""
    out: List[StudyFindings] = []
    for uid, (row, vals) in states.items():
        if not any((vals.get(f) or "").strip() for f in tp.FORCE_FIELDS):
            continue
        out.append(StudyFindings(uid, row, vals, tp.classify(vals)))
    return out


def plan_entries(evaluated: List[StudyFindings]) -> List[dict]:
    """The ledger entries this pass would append — one per (study, tier), fields/rules unioned,
    deduplicated against the ledger by plan_flag (same-value flagged/cleared/killed -> nothing)."""
    planned = []
    for sf in evaluated:
        for tier in ("hard", "soft"):
            fs = sf.by_tier(tier)
            if not fs:
                continue
            entry = fkl.plan_flag(
                study_uid=sf.study_uid, tier=tier,
                fields=[f for x in fs for f in x.fields],
                rules=[x.code for x in fs],
                values=sf.values,
                evidence=" · ".join(x.evidence for x in fs),
                sheet_row=sf.sheet_row)
            if entry is not None:
                planned.append(entry)
    return planned


def record(planned: List[dict]) -> int:
    for e in planned:
        fkl.append(e)
    return len(planned)


# ---------------------------------------------------------------- the change watch
def diff_snapshots(prev: Dict[str, Dict[str, str]],
                   cur: Dict[str, Dict[str, str]]) -> List[Tuple[str, str, str, str]]:
    """Criterion cells whose EXISTING value changed between two gate_states snapshots —
    (study_uid, field, old, new), sorted. Akadir's ask (2026-08-13): operators sometimes edit
    values in place ("sneak some inputs") and nothing said so — the gate re-flags a changed
    value only when the NEW value is implausible; this surfaces every in-place edit, plausible
    or not, including a deletion (value -> blank). A blank -> value transition is routine data
    entry (the sync's own fills, a new dynamometer session) and is deliberately not a change.
    A study absent from the previous snapshot is a new row, not a change."""
    out: List[Tuple[str, str, str, str]] = []
    for uid, cvals in cur.items():
        p = prev.get(uid)
        if not p:
            continue
        for f in tp.FORCE_FIELDS:
            old = (p.get(f) or "").strip()
            new = (cvals.get(f) or "").strip()
            if old and old != new:
                out.append((uid, f, old, new))
    return sorted(out)


# ---------------------------------------------------------------- the operator column
def desired_cell(study_uid: str) -> Optional[str]:
    """What 'Ölçüm Kontrol' should read for this study, from CURRENT ledger state — so a human
    kill/clear (appended via measurement_verdict.py) propagates to the sheet on the next sync
    pass. None = no measurement-scope history at all -> leave the cell untouched."""
    ent = [e for e in fkl.current().values()
           if e["scope"] == "measurement" and e.get("study_uid") == study_uid]
    if not ent:
        return None
    killed = [e for e in ent if e["status"] == "killed"]
    flagged = [e for e in ent if e["status"] == "flagged"]
    if killed:
        by = killed[0].get("decided_by", "karar")
        return f"❌ ölçüm geçersiz (karar: {by})"
    if flagged:
        codes: List[str] = []
        for e in flagged:
            codes += [c for c in e.get("rules") or () if c not in codes]
        msgs = "; ".join(tp.OPERATOR_TR.get(c, c) for c in codes)
        return f"⚠️ ÖLÇÜM: {msgs} — kaynaktan doğrulayın"
    return "✅ ölçüm doğrulandı"


# ---------------------------------------------------------------- the substrate receipt
def receipt(study_uid: str, holds: Dict[str, Dict[str, dict]]) -> str:
    """The qa_master `measurement_flags` cell: what is HELD (blanked) and why. Under the no-force
    rule every open flag holds the whole criterion row, so HELD is the only live state a flagged
    study shows here; the tier rides in the rule names. parse_held_fields() is the inverse —
    formats change together."""
    held = holds.get(study_uid) or {}
    if not held:
        return ""
    fields = "+".join(sorted(held))
    rules = sorted({r for e in held.values() for r in e.get("rules") or ("killed",)})
    return f"HELD {fields} ({'+'.join(rules)})"


def parse_held_fields(receipt_cell: str) -> List[str]:
    """Inverse of receipt() for the HELD part — the invariant's reader."""
    cell = (receipt_cell or "").strip()
    if not cell.startswith("HELD "):
        return []
    return cell[len("HELD "):].split(" (")[0].split("+")

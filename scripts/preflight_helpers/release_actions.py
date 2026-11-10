"""The release surface's action layer — what a study's checkboxes may offer, and what an
accepted run records (docs/withhold_release_spec.md §3, Akadir's design 2026-08-14).

Torch-free, streamlit-free, ledger-I/O only. The p05 page renders checkboxes from
`pending_actions`, and `_accept_run` calls `record_release` with the finalized record — so a
release is always bound to a REAL accepted run (its number, its id), never to a draft.

Three actions, three meanings (his decisions, same day):
  * release texture — the study's texture is gate-withheld with no verdict yet; the operator
    vouches for THIS run's texture (possibly refined on the review page). The freeze then
    serves the run's number (`released_value`), never the auto value.
  * confirm size — the views disagreed, thickness serves from the femur view (it always does —
    "we never switch to RF for measurements"); the operator records that the femur numbers were
    reviewed and stand. A marker entry; no value changes anywhere.
  * contribute frames — the accepted run's vetted frames JOIN the study's canonical evidence
    pool at the next freeze (the "5+xth frame"): femur frames refine the geometry medians, RF
    frames refine the texture median. Evidence addition, never a trust verdict — the 3-way gate
    keeps reading the automatic values, and an explicit release verdict always outranks pooling.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from . import flag_kill_ledger as fkl

REPO = Path(__file__).resolve().parents[2]
LANDMARK = REPO / "scratchpad" / "new_round_landmark_full.json"

# the size-confirm marker "feature" — read by the trust strip, served by nothing.
SIZE_CONFIRM_FIELD = "size_femur_confirmed"


def texture_snapshot(study_uid: str, landmark_path: Optional[Path] = None) -> Optional[dict]:
    """The three per-view RF max thicknesses a texture clear binds to (stale detection),
    from the freeze's landmark JSON. None when the file or the study is absent — the caller
    decides whether that aborts (CLI) or disables the action (UI)."""
    p = landmark_path or LANDMARK
    if not p.exists():
        return None
    for r in json.load(open(p)):
        if "NR" + hashlib.sha256(r["study"].encode("utf-8")).hexdigest()[:8].upper() == study_uid:
            def g(role):
                v = ((r.get("vals") or {}).get(role) or {}).get("full_max_thickness_mm")
                return None if v is None else round(float(v), 3)
            return {"max_femur_mm": g("femur"), "max_center_mm": g("rf"),
                    "max_sweep_mm": g("sweep")}
    return None


def pending_actions(study_uid: str, rf_decision: Optional[dict],
                    path: Optional[Path] = None) -> dict:
    """Which checkboxes this study may offer, from the gate decision + the ledger's current
    state. A feature with ANY recorded verdict (cleared or killed) offers nothing — verdicts
    are superseded through the verdict tool, not silently re-clicked."""
    dec = rf_decision or {}
    cur = fkl.current(path)
    tex_state = next((e["status"] for e in cur.values()
                      if e["scope"] == "feature" and e.get("study_uid") == study_uid
                      and "int_cv_within" in (e.get("fields") or ())), None)
    size_state = next((e["status"] for e in cur.values()
                       if e["scope"] == "feature" and e.get("study_uid") == study_uid
                       and SIZE_CONFIRM_FIELD in (e.get("fields") or ())), None)
    untrusted = bool(dec) and dec.get("int_cv_trusted") is False
    disagreed = dec.get("source") == "femur"
    n_contrib = len(fkl.contributions(path).get(study_uid) or [])
    return {
        "texture_release": untrusted and tex_state is None,
        "size_confirm": disagreed and size_state is None,
        "texture_verdict": tex_state,          # 'cleared' | 'killed' | None — for captions
        "size_confirmed": size_state == "cleared",
        # contributing is open to every routed study — it refines, it never gates
        "contribute": True,
        "n_contributed_runs": n_contrib,
    }


def build_entries(record: dict, *, release_texture: bool, confirm_size: bool,
                  by: str, landmark_path: Optional[Path] = None) -> list[dict]:
    """The ledger entries an accepted run's checked boxes produce. Pure — no I/O beyond the
    landmark read for the binding snapshot. A texture release REQUIRES the run's int_cv and the
    binding snapshot; missing either yields no entry (never a value-less release)."""
    uid = record.get("study_uid") or ""
    run_id = record.get("run_id") or ""
    entries: list[dict] = []
    if release_texture:
        icv = (record.get("aggregate") or {}).get("int_cv_within")
        snap = texture_snapshot(uid, landmark_path)
        if uid and run_id and icv is not None and snap is not None:
            refine = record.get("texture_refine")
            entries.append({
                "scope": "feature", "status": "cleared", "study_uid": uid,
                "fields": ["int_cv_within"], "values": snap,
                "released_value": round(float(icv), 6), "source_run_id": run_id,
                "evidence": (f"released via the app at Accept run (refine={refine})"
                             if refine else "released via the app at Accept run (standard region)"),
                "decided_by": by,
            })
    if confirm_size and uid and run_id:
        entries.append({
            "scope": "feature", "status": "cleared", "study_uid": uid,
            "fields": [SIZE_CONFIRM_FIELD], "source_run_id": run_id,
            "evidence": "femur numbers reviewed and confirmed via the app at Accept run",
            "decided_by": by,
        })
    return entries


def build_contribution(record: dict, *, by: str) -> Optional[dict]:
    """The contribution entry an accepted run's checked box produces. Requires the record to
    actually CARRY per-frame features for at least one role (records from before 2026-08-14
    don't — a frame-less contribution would silently degrade the pool to nothing)."""
    uid = record.get("study_uid") or ""
    run_id = record.get("run_id") or ""
    roles = [role for role, v in (record.get("routed_picks") or {}).items()
             if (v or {}).get("frame_features")]
    if not (uid and run_id and roles):
        return None
    n = sum(len((record["routed_picks"][r] or {}).get("frame_features") or []) for r in roles)
    return {"scope": "contribution", "status": "active", "study_uid": uid,
            "fields": [run_id], "source_run_id": run_id, "roles": sorted(roles),
            "evidence": f"{n} vetted frame(s) ({'+'.join(sorted(roles))}) contributed to the "
                        f"canonical pool via the app at Accept run",
            "decided_by": by}


def record_release(record: dict, *, release_texture: bool, confirm_size: bool,
                   contribute: bool = False, by: str = "Akadir", path: Optional[Path] = None,
                   landmark_path: Optional[Path] = None) -> int:
    """Append the checked actions to the ledger; returns how many entries landed."""
    entries = build_entries(record, release_texture=release_texture,
                            confirm_size=confirm_size, by=by, landmark_path=landmark_path)
    if contribute:
        c = build_contribution(record, by=by)
        if c is not None:
            entries.append(c)
    for e in entries:
        fkl.append(e, path)
    return len(entries)

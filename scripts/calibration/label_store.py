"""Subsystem E — the calibration label ledger.

Append-only, fcntl-locked JSON-lines at `data/calibration_labels.jsonl`. PHI-clean
(study_code + frame_base only). One line per operator answer. The schema carries
everything the fitter (F) and the metrics/audit CLIs (J/I) need, plus anchor B's
"right scales/metrics" — the FULL detector metadata snapshot, not just the
headline scalar.

Drift policy (spec § 7, P1): `score_at_label_time` is an audit snapshot; the
fitter joins labels against the CURRENT cohort score table, not this value.
`spec_version_at_label_time` lets the fitter filter to the current spec.
"""
from __future__ import annotations

import fcntl
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_REPO = Path(__file__).resolve().parents[2]


def _default_path() -> Path:
    """The label ledger path, resolved from env.DATA_ROOT at call time so a test
    fixture's tmp-redirect (monkeypatch env.DATA_ROOT) captures the writes
    instead of touching the real ledger."""
    try:
        from preflight_helpers import env
        return Path(env.DATA_ROOT) / "calibration_labels.jsonl"
    except Exception:
        return _REPO / "data" / "calibration_labels.jsonl"


_VALID_LABEL = ("yes", "no", "unsure")
# p4x = blind-then-reveal (anchor-free); cohort_pass = the 3-operator overall-
# quality log; selection = in-flow agree/disagree captured at p04 review
# (anchored — the operator saw the app's call). The fitter reports sens/spec
# per source, so an anchored `selection` label is weighted as different evidence
# from a blind `p4x` one (spec § 5; plan Part 2.3.2).
_VALID_SOURCE = ("p4x", "cohort_pass", "selection")
_VALID_REVEAL = ("before", "after", "never")
_VALID_CONFIRM = ("kept", "changed_to_opposite", "changed_to_unsure", "not_applicable")
RELABEL_INTERVAL_DAYS = 14


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Label:
    """One calibration label. `to_dict` is what lands in the ledger."""
    study_code: str
    frame_base: str
    detector: str
    label: str                              # yes | no | unsure
    score_at_label_time: Optional[float]
    detector_metadata_at_label_time: dict
    threshold_at_label_time: Optional[float]
    label_source: str                       # p4x | cohort_pass
    score_revealed_at: str                  # before | after | never
    post_reveal_confirmation: str           # kept | changed_* | not_applicable
    response_time_ms: Optional[int]
    visual_aids_shown: tuple[str, ...]
    contract_version: str
    spec_version_at_label_time: str
    operator_id_hash: str
    notes: str = ""
    relabel_target: bool = False
    relabel_of: Optional[str] = None
    label_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp_utc: str = field(default_factory=_now_utc)
    relabel_due_utc: Optional[str] = None

    def __post_init__(self) -> None:
        if self.label not in _VALID_LABEL:
            raise ValueError(f"label must be one of {_VALID_LABEL}; got {self.label!r}")
        if self.label_source not in _VALID_SOURCE:
            raise ValueError(f"label_source must be one of {_VALID_SOURCE}; got {self.label_source!r}")
        if self.score_revealed_at not in _VALID_REVEAL:
            raise ValueError(f"score_revealed_at must be one of {_VALID_REVEAL}; got {self.score_revealed_at!r}")
        if self.post_reveal_confirmation not in _VALID_CONFIRM:
            raise ValueError(
                f"post_reveal_confirmation must be one of {_VALID_CONFIRM}; "
                f"got {self.post_reveal_confirmation!r}")
        # A relabel_target gets a due date 14 days out (the test-retest revisit).
        if self.relabel_target and self.relabel_due_utc is None:
            due = datetime.fromisoformat(self.timestamp_utc) + timedelta(
                days=RELABEL_INTERVAL_DAYS)
            self.relabel_due_utc = due.isoformat(timespec="seconds")

    def to_dict(self) -> dict:
        return {
            "label_id": self.label_id,
            "operator_id_hash": self.operator_id_hash,
            "timestamp_utc": self.timestamp_utc,
            "study_code": self.study_code,
            "frame_base": self.frame_base,
            "detector": self.detector,
            "score_at_label_time": self.score_at_label_time,
            "detector_metadata_at_label_time": self.detector_metadata_at_label_time,
            "threshold_at_label_time": self.threshold_at_label_time,
            "label": self.label,
            "label_source": self.label_source,
            "score_revealed_at": self.score_revealed_at,
            "post_reveal_confirmation": self.post_reveal_confirmation,
            "response_time_ms": self.response_time_ms,
            "visual_aids_shown": list(self.visual_aids_shown),
            "contract_version": self.contract_version,
            "spec_version_at_label_time": self.spec_version_at_label_time,
            "notes": self.notes,
            "relabel_target": self.relabel_target,
            "relabel_of": self.relabel_of,
            "relabel_due_utc": self.relabel_due_utc,
        }


def append_label(label: Label, path: Optional[Path] = None) -> None:
    """Append one label as a JSON line, with an exclusive file lock so two
    browser tabs / processes can't interleave a write (R10)."""
    path = path or _default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(label.to_dict(), default=str)
    with open(path, "a", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            f.write(line + "\n")
            f.flush()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def selection_label_for(
    *, usable: Optional[bool], use_for_analysis: Optional[bool],
    detector_cited: bool,
) -> Optional[str]:
    """The clean weak-supervision label from an operator's keep/set-aside
    decision for ONE fired surfaceable detector — or None when the signal is
    ambiguous and should not be recorded:

      * set aside (usable=False) AND the detector cited in drop_reason_codes
        -> "yes"  (the operator confirmed this exact problem)
      * kept for analysis (use_for_analysis AND usable) -> "no"  (kept despite
        the flag — the false-fire signal the optional validation pass wanted)
      * set aside for a DIFFERENT reason (usable=False, not cited) -> None
        (we cannot infer this detector's truth; skip rather than mislabel)
    """
    if usable is False and detector_cited:
        return "yes"
    if use_for_analysis and usable:
        return "no"
    return None


def record_selection_label(
    *, study_code: str, frame_base: str, detector: str, label: str,
    image_quality: Optional[dict], operator_id: str = "akadir",
    notes: str = "", path: Optional[Path] = None,
) -> str:
    """Capture an in-flow calibration label from the p04 review "is this call
    right?" surface (Akadir 2026-05-31). `label_source='selection'`,
    `score_revealed_at='before'` (anchored — the operator saw the app's call).

    The FULL detector metadata is snapshotted; the headline score/threshold are
    audit-only and left None, because the fitter re-reads the CURRENT cohort
    score table (drift policy P1) rather than trusting a label-time snapshot.
    PHI-clean: study_code + frame_base only. Returns the label_id.
    """
    md_all = (image_quality or {}).get("image_quality_detector_metadata") or {}
    det_md = md_all.get(detector) or {}
    oid = _hashlib().sha256(
        ("muscle_mania_selection::" + str(operator_id)).encode()).hexdigest()[:16]
    contract = (image_quality or {}).get("image_quality_contract_version") or "v0.2"
    lab = Label(
        study_code=study_code, frame_base=frame_base, detector=detector,
        label=label, score_at_label_time=None,
        detector_metadata_at_label_time=det_md, threshold_at_label_time=None,
        label_source="selection", score_revealed_at="before",
        post_reveal_confirmation="not_applicable", response_time_ms=None,
        visual_aids_shown=(), contract_version=contract,
        spec_version_at_label_time=contract, operator_id_hash=oid, notes=notes)
    append_label(lab, path)
    return lab.label_id


def _hashlib():
    import hashlib
    return hashlib


def load_labels(path: Optional[Path] = None) -> list[dict]:
    """All labels (each a dict). Empty list when the ledger does not exist."""
    path = path or _default_path()
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def labelled_keys(detector: str, path: Optional[Path] = None) -> frozenset:
    """The (study_code, frame_base) set already labelled for `detector` — feeds
    the candidate selector's `already_labelled` so sessions advance. Counts only
    real answers (yes/no/unsure all count as 'seen this frame for this detector')."""
    return frozenset(
        (r["study_code"], r["frame_base"])
        for r in load_labels(path)
        if r.get("detector") == detector and r.get("label_source") == "p4x")


def due_relabels(path: Optional[Path] = None, *, now: Optional[datetime] = None) -> list[dict]:
    """Labels whose test-retest revisit is due (relabel_due_utc has passed and
    no relabel has been recorded for them yet). Drives the p4x entry-page banner."""
    now = now or datetime.now(timezone.utc)
    rows = load_labels(path)
    relabelled_of = {r.get("relabel_of") for r in rows if r.get("relabel_of")}
    due: list[dict] = []
    for r in rows:
        if not r.get("relabel_target") or not r.get("relabel_due_utc"):
            continue
        if r["label_id"] in relabelled_of:
            continue                                    # already re-labelled
        if datetime.fromisoformat(r["relabel_due_utc"]) <= now:
            due.append(r)
    return due

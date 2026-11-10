"""Pure study-consistency audit engine (Phase 3a): reconcile the five pilot populations
— source zips (Z), the hasta_kayit datasheet (D), the materialized view (M), the scored
runs (R), and the external force/strength criterion (F) — into per-study findings + the
validatable-N readiness funnel.

This module does ZERO I/O. It receives ALREADY-LOADED facts (the CLI `audit_studies.py` is the
separate next step that opens the sheet, the zips, runs.jsonl and the force sheet, and packs each
into the *Facts dataclasses below) and returns findings + the funnel. Keeping it pure mirrors the
`check_study` / `sync_force_measures` style — every rule lives in one headlessly-unit-testable place,
and a green test over synthetic populations is the floor for trusting the reconciliation.

Two contracts make the engine the single source of truth:

* `normalize_key` is the PINNED join key — it reuses `sync_force_measures._norm` byte-for-byte, so
  D / Z / M / R / F all key identically (strip + casefold — Latin-case only; NOT Turkish dotted/
  dotless-İ aware, so two spellings differing only in Turkish I-case would not join). A mismatch here would
  silently split a study across populations (the live `study_path`-basename vs `study_code` bug the
  plan calls out), so the parity is structural, not re-implemented.
* `CHECKS` is a DATA registry (code -> CheckSpec) read by BOTH the report legend and a contract
  test, so a finding's severity / audience / action can never drift from its documented meaning —
  `_finding` stamps every finding straight from the registry.

The action taxonomy is Akadir-ratified (the safety rule: never mutate the operator's source):
  * REMIND — cosmetic, zero integrity risk; a batched per-operator nudge "for next time", never
    blocks. e.g. NAME-COLLISION (`26jun26_021` reused) — the join keys on the FULL name, so a reused
    number breaks nothing; both rows audit independently.
  * AUTO   — an unambiguous surplus already auto-corrected upstream (materialize EXCLUDED it, source
    untouched). e.g. ORPHAN-RAWIMAGES (a re-recorded take's leftover folder).
  * ASK    — an integrity risk AND ambiguous; hold/triage with the exact numbers + the remedy.
    e.g. MODALITY-MISSING, PAIRING-AMBIGUOUS, the impossible-number sanity checks.

Collisions are SURFACED, never deduped — `parse_force_rows`/`read_sheet` would keep only the last
occurrence and hide a reused number; the raw-occurrence scan here runs BEFORE any dedup.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Mapping, Optional, Sequence

# The pinned join key MUST be byte-identical to the force-sync join. Import the real function so
# parity is by construction, not a re-implementation that could drift. study_audit.py lives in
# scripts/preflight_helpers/, sync_force_measures.py in scripts/ — self-bootstrap the scripts/ dir
# onto the path when imported outside the CLI/tests (mirrors env_bootstrap's fallback).
try:
    from sync_force_measures import _norm as _force_norm
except ModuleNotFoundError:  # pragma: no cover - exercised only outside the conftest/CLI path
    import sys as _sys
    from pathlib import Path as _Path
    _sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
    from sync_force_measures import _norm as _force_norm

# The ONE study-name parser (CLAUDE.md: `study_name_grammar` is the single source of truth for
# study-name parsing). It is pure (stdlib only), so it stays importable from this I/O-free engine, and
# scripts/ is on sys.path by here (the block above guarantees it). Used by `_number_prefix` so the
# collision unit routes through the grammar instead of a hand-rolled split that drifts.
from preflight_helpers import study_name_grammar as _sng

__all__ = [
    "normalize_key", "scan_collisions", "isokinetic_reliable",
    "AuditFinding", "StudyAuditRow", "ReadinessFunnel", "AuditResult", "Populations",
    "DatasheetFacts", "ZipFacts", "MaterializeFacts", "RunFacts", "ForceFacts",
    "CHECKS", "CheckSpec", "legend", "audit_row", "audit", "funnel",
    "COV_GATE_PCT", "WASHOUT_MEAN_L",
]

# ---- enumerations (plain strings, matching the repo's "ok"/"warn"/"fail" idiom) ----
FAIL, WARN, INFO = "FAIL", "WARN", "INFO"                  # severity
OPERATOR, ANALYST = "OPERATOR", "ANALYST"                  # audience
REMIND, AUTO, ASK = "REMIND", "AUTO", "ASK"                # action
B0, B1, B2, B3, B4, ENGINE = "B0", "B1", "B2", "B3", "B4", "ENGINE"  # boundary

_SEVERITIES = frozenset({FAIL, WARN, INFO})
_AUDIENCES = frozenset({OPERATOR, ANALYST})
_ACTIONS = frozenset({REMIND, AUTO, ASK})
_BOUNDARIES = frozenset({B0, B1, B2, B3, B4, ENGINE})

# ---- tunables ----
COV_GATE_PCT = 15.0     # isokinetic CoV above this (%) = unreliable (schema note "≤10–15%"; flags
                        # 62.5/26.8/20.9, keeps 10.5/8.8/1.6). Isokinetic-only — isometric is the
                        # PRIMARY criterion and its CoV is never imported, so it is never gated.
WASHOUT_MEAN_L = 90.0   # mean frame luminance above this = likely washed out (gain/preset/software)
_FAT_DEPTH_MAX = 1.0    # fat thicker than the imaged depth is impossible (a ratio > 1 is a defect)

# ---- check codes (constants so the contract test can prove every emitted code is registered) ----
NAME_COLLISION = "NAME-COLLISION"
SET_Z_NO_D, SET_D_NO_Z, SET_M_NO_R, SET_R_NO_M = "SET-Z-NO-D", "SET-D-NO-Z", "SET-M-NO-R", "SET-R-NO-M"
ARTIFACT_COUNT, MODALITY_MISSING = "ARTIFACT-COUNT", "MODALITY-MISSING"
NAME_UNPARSED, OPERATOR_FOLDER_MISMATCH, WASHOUT = "NAME-UNPARSED", "OPERATOR-FOLDER-MISMATCH", "WASHOUT"
MATERIALIZE_HOLD, MANIFEST_MISMATCH, DEPTH_OFFSPEC = "MATERIALIZE-HOLD", "MANIFEST-MISMATCH", "DEPTH-OFFSPEC"
ORPHAN_RAWIMAGES, PAIRING_AMBIGUOUS = "ORPHAN-RAWIMAGES", "PAIRING-AMBIGUOUS"
RUN_NOT_FINALIZED = "RUN-NOT-FINALIZED"
FAT_DEPTH_HIGH, INT_CV_NEG = "FAT-DEPTH>1", "INT-CV<0"
PLACEMENT_BLANK, QMT_NULLED, FRAME_DROPS = "PLACEMENT-BLANK", "QMT-NULLED", "FRAME-DROPS"
FORCE_ORPHAN, FORCE_BLANK, COV_GATE = "FORCE-ORPHAN", "FORCE-BLANK", "COV-GATE"
CHECK_ERROR = "CHECK-ERROR"   # fail-open diagnostic (a check raised — never silent)


# ============================ the CHECKS registry (DATA) ============================
@dataclass(frozen=True)
class CheckSpec:
    """One registered check's fixed metadata — read by the report legend AND the contract test, so a
    finding's severity/audience/action are defined ONCE here, never inline at the emit site."""
    severity: str       # FAIL | WARN | INFO
    audience: str       # OPERATOR | ANALYST
    action: str         # REMIND | AUTO | ASK
    boundary: str       # B0..B4 (which population seam) | ENGINE
    blurb: str          # one-line legend description
    deferred: bool = False   # registered but intentionally INERT this phase (the loader never feeds it);
                             # surfaced as a coverage note so the legend can't advertise a dead check


# code -> CheckSpec. Boundaries: B0 collisions+set-diffs · B1 source<->datasheet ·
# B2 datasheet<->processing · B3 processing<->numbers · B4 force.
CHECKS: Dict[str, CheckSpec] = {
    # --- B0: collisions + population set-diffs ---
    NAME_COLLISION: CheckSpec(WARN, OPERATOR, REMIND, B0,
        "a study NUMBER (date_count, e.g. 26jun26_021) is reused; full-name join keys differ so "
        "nothing breaks — a nudge for next time, both rows kept"),
    SET_Z_NO_D: CheckSpec(WARN, ANALYST, ASK, B0, "a zip exists with no datasheet row (log it?)"),
    SET_D_NO_Z: CheckSpec(WARN, ANALYST, ASK, B0, "a datasheet row with no uploaded zip (upload pending?)"),
    SET_M_NO_R: CheckSpec(WARN, ANALYST, ASK, B0, "materialized but never scored (no run record)"),
    SET_R_NO_M: CheckSpec(WARN, ANALYST, ASK, B0,
        "a finalized run with no current materialized tree (ledger drift — e.g. a since-deleted study)"),
    # --- B1: source <-> datasheet ---
    ARTIFACT_COUNT: CheckSpec(WARN, OPERATOR, ASK, B1,
        "the datasheet Artifacts M/R/D claim disagrees with the real zip count"),
    MODALITY_MISSING: CheckSpec(FAIL, OPERATOR, ASK, B1, "a required modality (femur / sweep) is absent"),
    NAME_UNPARSED: CheckSpec(WARN, OPERATOR, ASK, B1,
        "the study name does not parse via the one study-name grammar (joins/derived cols at risk)"),
    OPERATOR_FOLDER_MISMATCH: CheckSpec(WARN, OPERATOR, REMIND, B1,
        "the datasheet Operator disagrees with the zip's Drive folder (after alias-normalize)"),
    WASHOUT: CheckSpec(WARN, OPERATOR, REMIND, B1,
        "frames are washed out (mean luminance high) — echo-intensity features unusable"),
    # --- B2: datasheet <-> processing ---
    MATERIALIZE_HOLD: CheckSpec(WARN, ANALYST, ASK, B2, "held at materialize (not processed) with a reason"),
    MANIFEST_MISMATCH: CheckSpec(WARN, ANALYST, ASK, B2,
        "the materialize manifest capture count disagrees with what was expected"),
    DEPTH_OFFSPEC: CheckSpec(WARN, OPERATOR, REMIND, B2,
        "a materialized capture's depth is off-spec (wrong dial) — DEFERRED: v8 assigns each capture's "
        "depth by RELATIVE rank (deepest=femur), not a fixed protocol depth, so a deep-imaging probe "
        "(831 ~9cm) is in-spec; the loader leaves depth_in_spec=None until a fixed-depth protocol returns",
        deferred=True),
    ORPHAN_RAWIMAGES: CheckSpec(INFO, OPERATOR, AUTO, B2,
        "a re-recorded take's orphan RawImages was auto-excluded at materialize (source untouched)"),
    PAIRING_AMBIGUOUS: CheckSpec(WARN, ANALYST, ASK, B2,
        "a capture's mp4<->RawImages pairing is ambiguous — kept for review, not auto-acted"),
    # --- B3: processing <-> numbers ---
    RUN_NOT_FINALIZED: CheckSpec(WARN, ANALYST, ASK, B3, "run(s) exist but none are finalized (draft-stuck)"),
    FAT_DEPTH_HIGH: CheckSpec(FAIL, ANALYST, ASK, B3, "fat_to_depth > 1.0 — fat thicker than the imaged depth (impossible)"),
    INT_CV_NEG: CheckSpec(FAIL, ANALYST, ASK, B3, "int_cv_within < 0 — a coefficient of variation cannot be negative"),
    PLACEMENT_BLANK: CheckSpec(WARN, ANALYST, ASK, B3, "cohort placement is blank on a finalized run (data-lineage gap)"),
    QMT_NULLED: CheckSpec(WARN, ANALYST, ASK, B3,
        "QMT is null though a femur capture is present (the edge_clip_femur regression class)"),
    FRAME_DROPS: CheckSpec(INFO, ANALYST, REMIND, B3,
        "surfaced: the union of per-frame drop_reasons + qmt_validity_flags behind the aggregate"),
    # --- B4: force / strength criterion ---
    FORCE_ORPHAN: CheckSpec(WARN, ANALYST, REMIND, B4, "a force-sheet study has no datasheet (kayit) row"),
    FORCE_BLANK: CheckSpec(WARN, ANALYST, REMIND, B4, "a matched force row carried no values"),
    COV_GATE: CheckSpec(WARN, ANALYST, ASK, B4,
        "isokinetic CoV exceeds the reliability gate — isokinetic torque/power unreliable "
        "(isometric is the primary criterion and is always kept)"),
    # --- engine meta ---
    CHECK_ERROR: CheckSpec(INFO, ANALYST, ASK, ENGINE, "an audit check raised — fail-open diagnostic"),
}


def legend() -> List[dict]:
    """The CHECKS registry as report rows (code + spec fields), most-actionable-first by boundary —
    the report .md legend and the contract test both read this, never a hand-kept copy."""
    order = {b: i for i, b in enumerate([B0, B1, B2, B3, B4, ENGINE])}
    rows = [{"code": code, "severity": s.severity, "audience": s.audience, "action": s.action,
             "boundary": s.boundary, "blurb": s.blurb, "deferred": s.deferred}
            for code, s in CHECKS.items()]
    return sorted(rows, key=lambda r: (order.get(r["boundary"], 99), r["code"]))


# ============================ findings ============================
@dataclass
class AuditFinding:
    """One reconciliation result. severity/audience/action are stamped from CHECKS[code] (see
    `_finding`) so they cannot drift from the registry; `data` carries the load-bearing numbers
    (the actual counts/values, JSON-safe) so the report shows evidence, not just a conclusion."""
    code: str
    severity: str
    audience: str
    action: str
    message: str
    data: dict = field(default_factory=dict)


def _finding(code: str, message: str, data: Optional[dict] = None) -> AuditFinding:
    """Build a finding, stamping severity/audience/action/(boundary in data) from the registry."""
    spec = CHECKS[code]   # KeyError here = an unregistered code (the contract test forbids it)
    return AuditFinding(code=code, severity=spec.severity, audience=spec.audience,
                        action=spec.action, message=message, data=dict(data or {}))


# ============================ input fact shapes (the CLI populates these) ============================
@dataclass(frozen=True)
class DatasheetFacts:
    """A hasta_kayit row's audit-relevant fields. The CLI maps `check_studies.read_sheet`'s raw
    {header: value} dict here. `study` (the full 'Study Adı') is the join-key source; `operator`
    and `artifacts_mrd` are the operator's CLAIMS to reconcile against the real zip (Z)."""
    study: str                                  # 'Study Adı' — the full name (join key source)
    operator: Optional[str] = None              # 'Operator' login claimed on the sheet
    artifacts_mrd: Optional[str] = None         # 'Artifacts' M/R/D claim, e.g. '3/3/3'
    declared_profile: Optional[str] = None      # 'main' | 'subcohort' from 'Alt-kohort mu'
    row: Optional[int] = None                   # 1-based sheet row (provenance)


@dataclass(frozen=True)
class ZipFacts:
    """What the CLI derived from ONE study zip, via `count_zip_artifacts` + `deconflict_zip` +
    `check_study`. The engine does no zip I/O — every field is pre-computed:

      study            the zip stem (full study name; join-key source when there is no D row)
      artifacts_mrd    `count_zip_artifacts(zip).as_str()` — the REAL 'M/R/D' (trash excluded)
      modalities       `check_study(...).modality_counts`, e.g. {'femur':1,'rf':1,'sweep':1}
      has_femur        a femur capture is present (geometry available)
      has_sweep        a sweep capture is present (cohort-comparable)
      name_parsed      `study_name_grammar.parse` accepted the name (no hard parse warning)
      folder_operator  the Drive subfolder the zip was filed under (operator login), or None
      verdict          `check_study(...).verdict` — 'ok' | 'warn' | 'fail'
      depth_offspec    off-spec depths (cm) the source check flagged (advisory)
      brightness       mean frame luminance (washout flag); None if unread
    """
    study: str
    artifacts_mrd: Optional[str] = None
    modalities: Mapping[str, int] = field(default_factory=dict)
    has_femur: bool = True
    has_sweep: bool = True
    name_parsed: bool = True
    folder_operator: Optional[str] = None
    verdict: Optional[str] = None
    depth_offspec: Sequence[float] = ()
    brightness: Optional[float] = None


@dataclass(frozen=True)
class MaterializeFacts:
    """What the materialize layer DID for this study (from its `StudyPlan` / manifest). Present only
    when the study reached materialize at all; `materialized=False` == HELD (with a reason).

      materialized        a materialized tree was written (StudyPlan.materialize)
      hold_reason         why HELD (StudyPlan.reason) when not materialized
      manifest_captures   capture count the manifest recorded (None if unknown)
      expected_captures   captures the declared/inferred profile expected (None if unknown)
      depth_in_spec       all materialized captures depth-matched in spec (None if unknown; feeds the
                          DEFERRED DEPTH-OFFSPEC check — the loader leaves it None under v8 relative-rank)
      excluded_captures   AUTO-corrected orphan duplicates [{folder,nframes,identity,reason}]
      ambiguous_captures  captures held for ambiguous pairing (ASK), as human-readable strings
      manifest_pre_phase2 the manifest predates Phase 2 (no excluded_captures field) — the ORPHAN-
                          RAWIMAGES auto-correct could not have run on it; a live-coverage note, not a check
    """
    materialized: bool = False
    hold_reason: Optional[str] = None
    manifest_captures: Optional[int] = None
    expected_captures: Optional[int] = None
    depth_in_spec: Optional[bool] = None
    excluded_captures: Sequence[Mapping] = ()
    ambiguous_captures: Sequence[str] = ()
    manifest_pre_phase2: bool = False


@dataclass(frozen=True)
class RunFacts:
    """The scored-run facts for this study, from `bundle_writer.iter_runs()` re-indexed by
    `Path(study_path).name` (NOT `study_code` — the live join-key bug) and reduced to the LATEST
    finalized run's aggregate + per-frame provenance:

      has_run            any run record exists for this study
      finalized          a lifecycle=='finalized' run exists (a draft-only study is NOT finalized)
      fat_to_depth       aggregate['fat_to_depth_q50'] — must be <= 1.0
      int_cv             aggregate['int_cv_within'] — must be >= 0
      qmt_mm             aggregate['qmt_mm'] (provenance-gated primary); None == no femur-valid tier
      placement_pctile   a representative cohort placement percentile; None == placement blank
      femur_present      a femur capture fed the run (so QMT SHOULD resolve — null QMT is then a defect)
      drop_reasons       union of per-frame `drop_reasons`
      qmt_validity_flags union of per-frame `qmt_validity_flags`
    """
    has_run: bool = False
    finalized: bool = False
    fat_to_depth: Optional[float] = None
    int_cv: Optional[float] = None
    qmt_mm: Optional[float] = None
    placement_pctile: Optional[float] = None
    femur_present: bool = False
    drop_reasons: Sequence[str] = ()
    qmt_validity_flags: Sequence[str] = ()


@dataclass(frozen=True)
class ForceFacts:
    """Per-study force/strength facts from `sync_force_measures.plan_writes` + the raw force row.
    Isometric peak torque is the PRIMARY criterion. Its CoV is now imported to the sheet (2026-07-01,
    at Dr. M's request) but is NOT yet wired into a readiness gate — isometric is the most reproducible
    mode, so its CoV rarely gates; the isokinetic torque/power are secondary and gated on the isokinetic CoV.

      in_force_sheet     the study has a row in the force sheet
      matched_kayit      that row joined to a kayit row (else a FORCE-ORPHAN, surfaced globally)
      isometric_present  İzometrik Tepe Tork present — the validatable-N gate
      isokinetic_present İzokinetik torque/power present
      isokinetic_cov     İzokinetik CV (%) — the reliability-gate input (None if absent)
      all_blank          the matched row carried no values (FORCE-BLANK)
    """
    in_force_sheet: bool = False
    matched_kayit: bool = True
    isometric_present: bool = False
    isokinetic_present: bool = False
    isokinetic_cov: Optional[float] = None
    all_blank: bool = False


# ============================ the per-study row ============================
@dataclass
class StudyAuditRow:
    """One pinned-key row joining all five populations + its findings + readiness tier flags.
    Readiness (set by `audit_row` after the checks run, since `consistent` depends on no FAIL):
      recorded     — present in Z or D
      consistent   — recorded AND no FAIL finding                         (Tier 0)
      processed    — consistent AND source reconciled (Z present) AND
                     materialized AND a finalized run                     (Tier 1)
      validatable  — processed AND isometric peak torque present          (Tier 2 — the pilot N)
      reliable     — validatable AND reliable isokinetic (CoV <= gate)    (Tier 3)
    """
    study: str
    norm_key: str
    operator: Optional[str] = None
    d: Optional[DatasheetFacts] = None
    z: Optional[ZipFacts] = None
    m: Optional[MaterializeFacts] = None
    r: Optional[RunFacts] = None
    f: Optional[ForceFacts] = None
    findings: List[AuditFinding] = field(default_factory=list)
    recorded: bool = False
    consistent: bool = False
    processed: bool = False
    validatable: bool = False
    reliable: bool = False


# ============================ join-key + collision scan ============================
def normalize_key(study_name: str) -> str:
    """The PINNED cross-population join key — byte-identical to the force-sync join
    (`sync_force_measures._norm`: strip + casefold; Latin-case only, NOT Turkish-İ-aware — a pair of
    spellings differing only in dotted/dotless-I case would not join), so D/Z/M/R/F all key the same."""
    return _force_norm(study_name)


def _number_prefix(study_name: str) -> str:
    """The operator-facing study NUMBER = the `date_count` token pair ('26jun26_021'), parsed via the
    ONE study-name grammar so a probe/count reorder can't fool a raw split (on the canonical
    `date_count_probe_...` schema both agree; the grammar wins when the probe sits where the count
    would). The reused number is the collision unit; the full name (with patient soyad/ad) still
    differs, so the normalized full-name join key stays unique and nothing breaks. Falls back to the
    raw `date_count` split only for a count-less legacy name the grammar yields no count for."""
    p = _sng.parse(study_name)
    if p.matched and p.date and p.count:
        return f"{p.date}_{p.count}".casefold()
    toks = (study_name or "").strip().split("_")
    return "_".join(toks[:2]).casefold() if len(toks) >= 2 else (study_name or "").strip().casefold()


def scan_collisions(raw_study_names: Sequence[str]) -> Dict[str, List[str]]:
    """Group RAW (pre-dedup) study-name occurrences by their study NUMBER and return the numbers
    used by >1 DISTINCT study (a reused number across two patients). Run on the raw occurrence list
    — `read_sheet`/`parse_force_rows` already dedup and would hide the reuse. Surfaced, never
    deduped: every occurrence is kept in the returned lists, and both rows still audit."""
    by: Dict[str, List[str]] = {}
    for nm in raw_study_names:
        if not (nm or "").strip():
            continue
        by.setdefault(_number_prefix(nm), []).append(nm.strip())
    return {num: occ for num, occ in by.items()
            if len({normalize_key(o) for o in occ}) > 1}


def isokinetic_reliable(cov_pct: Optional[float], gate_pct: float = COV_GATE_PCT) -> bool:
    """True iff an isokinetic CoV is present and at/under the reliability gate (default 15%).
    7.1 -> True, 26.8 -> False at the 15% gate. A missing CoV is NOT reliable (cannot vouch)."""
    return cov_pct is not None and cov_pct <= gate_pct


# ============================ per-row checks (one fn per boundary; fail-open isolates them) ============================
def check_b0_setdiffs(row: StudyAuditRow) -> List[AuditFinding]:
    """Population membership gaps for this key. Membership is read from which facts are present:
    in M == materialized; in R == a finalized run exists (a draft does not count)."""
    out: List[AuditFinding] = []
    in_d, in_z = row.d is not None, row.z is not None
    in_m = bool(row.m and row.m.materialized)
    in_r = bool(row.r and row.r.finalized)
    has_any_run = bool(row.r and row.r.has_run)
    if in_z and not in_d:
        out.append(_finding(SET_Z_NO_D, f"{row.study}: zip present, no datasheet row"))
    if in_d and not in_z:
        out.append(_finding(SET_D_NO_Z, f"{row.study}: datasheet row, no uploaded zip"))
    if in_m and not has_any_run:
        out.append(_finding(SET_M_NO_R, f"{row.study}: materialized but never scored (no run)"))
    if in_r and not in_m:
        out.append(_finding(SET_R_NO_M,
                            f"{row.study}: finalized run with no materialized tree (ledger drift)"))
    return out


def check_b1_source_datasheet(row: StudyAuditRow) -> List[AuditFinding]:
    """Source zip (Z) <-> the operator's datasheet claim (D)."""
    out: List[AuditFinding] = []
    z, d = row.z, row.d
    if z is None:
        return out
    # modality presence — an absent femur/sweep is a hard FAIL (cannot fully score the study).
    missing = [name for name, have in (("femur", z.has_femur), ("sweep", z.has_sweep)) if not have]
    if missing:
        out.append(_finding(MODALITY_MISSING, f"{row.study}: missing {', '.join(missing)}",
                            {"missing": missing, "modalities": dict(z.modalities)}))
    # Artifacts M/R/D claim vs the real zip count.
    if d is not None and d.artifacts_mrd and z.artifacts_mrd and d.artifacts_mrd != z.artifacts_mrd:
        out.append(_finding(ARTIFACT_COUNT,
                            f"{row.study}: datasheet Artifacts {d.artifacts_mrd} != zip {z.artifacts_mrd}",
                            {"datasheet": d.artifacts_mrd, "zip": z.artifacts_mrd}))
    # name parses via the one grammar.
    if not z.name_parsed:
        out.append(_finding(NAME_UNPARSED, f"{row.study}: study name did not parse"))
    # operator-folder match, after alias-normalize (e.g. anonaad/anonaaf).
    if d is not None and d.operator and z.folder_operator:
        if normalize_operator(d.operator) != normalize_operator(z.folder_operator):
            out.append(_finding(OPERATOR_FOLDER_MISMATCH,
                                f"{row.study}: datasheet operator '{d.operator}' != zip folder "
                                f"'{z.folder_operator}'",
                                {"datasheet": d.operator, "folder": z.folder_operator}))
    # washout (image-quality advisory).
    if z.brightness is not None and z.brightness > WASHOUT_MEAN_L:
        out.append(_finding(WASHOUT, f"{row.study}: mean luminance {z.brightness} > {WASHOUT_MEAN_L}",
                            {"brightness": z.brightness}))
    return out


def check_b2_processing(row: StudyAuditRow) -> List[AuditFinding]:
    """Datasheet (D) <-> the materialize/processing layer (M)."""
    out: List[AuditFinding] = []
    m = row.m
    if m is None:
        return out
    if not m.materialized:
        out.append(_finding(MATERIALIZE_HOLD, f"{row.study}: held — {m.hold_reason or 'no reason given'}",
                            {"reason": m.hold_reason}))
    if (m.manifest_captures is not None and m.expected_captures is not None
            and m.manifest_captures != m.expected_captures):
        out.append(_finding(MANIFEST_MISMATCH,
                            f"{row.study}: manifest {m.manifest_captures} captures != expected "
                            f"{m.expected_captures}",
                            {"manifest": m.manifest_captures, "expected": m.expected_captures}))
    if m.depth_in_spec is False:
        out.append(_finding(DEPTH_OFFSPEC, f"{row.study}: a materialized capture is off-spec depth"))
    if m.excluded_captures:
        out.append(_finding(ORPHAN_RAWIMAGES,
                            f"{row.study}: {len(m.excluded_captures)} orphan capture(s) auto-excluded",
                            {"excluded": [dict(e) for e in m.excluded_captures]}))
    if m.ambiguous_captures:
        out.append(_finding(PAIRING_AMBIGUOUS,
                            f"{row.study}: {len(m.ambiguous_captures)} ambiguous pairing(s) held for review",
                            {"ambiguous": list(m.ambiguous_captures)}))
    return out


def check_b3_numbers(row: StudyAuditRow) -> List[AuditFinding]:
    """Processing (M) <-> the numbers (R). The sanity checks gate on a finalized run existing — a
    draft/absent run yields RUN-NOT-FINALIZED instead of false number findings."""
    out: List[AuditFinding] = []
    r = row.r
    if r is None or not r.has_run:
        return out
    if not r.finalized:
        out.append(_finding(RUN_NOT_FINALIZED, f"{row.study}: run(s) exist but none finalized"))
        return out
    if r.fat_to_depth is not None and r.fat_to_depth > _FAT_DEPTH_MAX:
        out.append(_finding(FAT_DEPTH_HIGH, f"{row.study}: fat_to_depth {r.fat_to_depth} > {_FAT_DEPTH_MAX}",
                            {"fat_to_depth": r.fat_to_depth}))
    if r.int_cv is not None and r.int_cv < 0:
        out.append(_finding(INT_CV_NEG, f"{row.study}: int_cv_within {r.int_cv} < 0",
                            {"int_cv_within": r.int_cv}))
    if r.placement_pctile is None:
        out.append(_finding(PLACEMENT_BLANK, f"{row.study}: cohort placement blank on a finalized run"))
    if r.femur_present and r.qmt_mm is None:
        out.append(_finding(QMT_NULLED, f"{row.study}: QMT null though a femur capture is present"))
    # surface the union of per-frame drop_reasons + qmt_validity_flags behind the aggregate.
    drops, flags = sorted(set(r.drop_reasons)), sorted(set(r.qmt_validity_flags))
    if drops or flags:
        out.append(_finding(FRAME_DROPS,
                            f"{row.study}: {len(drops)} drop reason(s), {len(flags)} qmt flag(s)",
                            {"drop_reasons": drops, "qmt_validity_flags": flags}))
    return out


def check_b4_force(row: StudyAuditRow) -> List[AuditFinding]:
    """The force/strength criterion (F). FORCE-ORPHAN (no kayit row) is population-level (the orphan
    key is not in D∪Z) and surfaced globally in `audit`; here we catch the matched-row cases."""
    out: List[AuditFinding] = []
    f = row.f
    if f is None or not f.in_force_sheet:
        return out
    if f.matched_kayit and f.all_blank:
        out.append(_finding(FORCE_BLANK, f"{row.study}: matched force row carried no values"))
    if f.isokinetic_present and f.isokinetic_cov is not None and not isokinetic_reliable(f.isokinetic_cov):
        out.append(_finding(COV_GATE,
                            f"{row.study}: isokinetic CoV {f.isokinetic_cov}% > {COV_GATE_PCT}% — "
                            f"isokinetic torque/power unreliable (isometric kept)",
                            {"isokinetic_cov": f.isokinetic_cov, "gate": COV_GATE_PCT}))
    return out


# The per-row check registry. Each is isolated by the fail-open runner, so a raise in one boundary
# never aborts the row's audit (the others + readiness still compute).
_ROW_CHECKS: List[Callable[[StudyAuditRow], List[AuditFinding]]] = [
    check_b0_setdiffs, check_b1_source_datasheet, check_b2_processing,
    check_b3_numbers, check_b4_force,
]

# Known operator-login aliases -> canonical (the example: 'anonaaf' is a variant of 'anonaad'). A
# small map keeps the engine pure; the CLI can extend it as more aliases surface.
_OPERATOR_ALIASES = {"anonaaf": "anonaad"}


def normalize_operator(op: Optional[str]) -> str:
    """Casefold + strip an operator login, then map known aliases to canonical, so a benign login
    variant (anonaad/anonaaf) does not read as a real operator-folder mismatch."""
    base = (op or "").strip().casefold()
    return _OPERATOR_ALIASES.get(base, base)


# ============================ row + funnel + top-level audit ============================
def _run_row_checks(row: StudyAuditRow,
                    checks: Sequence[Callable[[StudyAuditRow], List[AuditFinding]]]) -> List[AuditFinding]:
    """Run every check fail-open: a raising check yields a CHECK-ERROR diagnostic (never silent) and
    the remaining checks + readiness still run, so one buggy check can't blind the whole row."""
    findings: List[AuditFinding] = []
    for chk in checks:
        try:
            findings.extend(chk(row) or [])
        except Exception as exc:   # noqa: BLE001 - fail-open is the point
            findings.append(_finding(CHECK_ERROR,
                                     f"{getattr(chk, '__name__', repr(chk))} raised: {exc!r}",
                                     {"check": getattr(chk, "__name__", repr(chk)), "error": repr(exc)}))
    return findings


def _compute_readiness(row: StudyAuditRow) -> None:
    """Set the tier flags from the facts + findings. `consistent` excludes a row with any FAIL
    finding; the higher tiers stack on it."""
    has_fail = any(fd.severity == FAIL for fd in row.findings)
    row.recorded = (row.z is not None) or (row.d is not None)
    row.consistent = row.recorded and not has_fail
    # processed REQUIRES the source zip (Z) to have been reconciled. check_b1 only evaluates the
    # MODALITY-MISSING FAIL when z is present, so a materialized + finalized study with NO zip would
    # otherwise sail through to validatable with its femur/sweep never checked — and the pilot N must be
    # clean END-TO-END, source included. The WHY is already surfaced as SET-D-NO-Z (a datasheet row with
    # no uploaded zip). On the current pilot all 3 materialized studies are also in the mirror, so this
    # changes no live number; it closes the hole where validatability flipped on a missing mirror zip.
    row.processed = (row.consistent and row.z is not None
                     and bool(row.m and row.m.materialized) and bool(row.r and row.r.finalized))
    row.validatable = row.processed and bool(row.f and row.f.isometric_present)
    row.reliable = row.validatable and bool(
        row.f and row.f.isokinetic_present and isokinetic_reliable(row.f.isokinetic_cov))


def audit_row(study: str, norm_key: Optional[str] = None, operator: Optional[str] = None, *,
              d: Optional[DatasheetFacts] = None, z: Optional[ZipFacts] = None,
              m: Optional[MaterializeFacts] = None, r: Optional[RunFacts] = None,
              f: Optional[ForceFacts] = None,
              checks: Optional[Sequence[Callable[[StudyAuditRow], List[AuditFinding]]]] = None
              ) -> StudyAuditRow:
    """Build one audited row: join the facts, run the checks fail-open, compute the readiness tiers.
    `checks` defaults to `_ROW_CHECKS`; a test can pass its own list (e.g. with a raising check)."""
    row = StudyAuditRow(study=study, norm_key=norm_key or normalize_key(study),
                        operator=operator, d=d, z=z, m=m, r=r, f=f)
    row.findings = _run_row_checks(row, checks if checks is not None else _ROW_CHECKS)
    _compute_readiness(row)
    return row


@dataclass
class ReadinessFunnel:
    """The headline funnel — how many studies survive each tier (validatable = the pilot N)."""
    recorded: int = 0
    consistent: int = 0       # Tier 0
    processed: int = 0        # Tier 1
    validatable: int = 0      # Tier 2 — the pilot N
    reliable: int = 0         # Tier 3
    validatable_keys: List[str] = field(default_factory=list)
    reliable_keys: List[str] = field(default_factory=list)

    def headline(self) -> str:
        return (f"recorded {self.recorded} -> consistent {self.consistent} -> processed {self.processed} "
                f"-> VALIDATABLE {self.validatable} -> reliable {self.reliable}")


def funnel(rows: Sequence[StudyAuditRow]) -> ReadinessFunnel:
    """Tally the readiness tiers across rows."""
    fn = ReadinessFunnel()
    for row in rows:
        fn.recorded += row.recorded
        fn.consistent += row.consistent
        fn.processed += row.processed
        fn.validatable += row.validatable
        fn.reliable += row.reliable
        if row.validatable:
            fn.validatable_keys.append(row.study)
        if row.reliable:
            fn.reliable_keys.append(row.study)
    return fn


@dataclass
class Populations:
    """All five already-loaded populations, keyed by the PINNED normalized key (the CLI builds this;
    the engine never does I/O). The row universe is D ∪ Z — never the 282-study legacy cohort in
    runs.jsonl — so M/R/F only attach to a study already recorded in the datasheet or as a zip."""
    datasheet: Mapping[str, DatasheetFacts] = field(default_factory=dict)
    zips: Mapping[str, ZipFacts] = field(default_factory=dict)
    materializes: Mapping[str, MaterializeFacts] = field(default_factory=dict)
    runs: Mapping[str, RunFacts] = field(default_factory=dict)
    forces: Mapping[str, ForceFacts] = field(default_factory=dict)
    raw_name_occurrences: Sequence[str] = ()   # ALL raw Study Adı strings (D + F), pre-dedup
    force_orphans: Sequence[str] = ()          # force studies with no kayit row (plan_writes orphans)


@dataclass
class AuditResult:
    """The full reconciliation: per-study rows + the funnel + population-level (global) findings
    (collisions, force orphans) that belong to no single row."""
    rows: List[StudyAuditRow]
    funnel: ReadinessFunnel
    global_findings: List[AuditFinding]


def audit(pops: Populations,
          checks: Optional[Sequence[Callable[[StudyAuditRow], List[AuditFinding]]]] = None) -> AuditResult:
    """Reconcile the populations into rows + funnel + global findings. The row universe is D ∪ Z;
    M/R/F attach by the pinned key. Collisions and force-orphans are surfaced globally (they belong
    to no single row), never deduped."""
    keys = sorted(set(pops.datasheet) | set(pops.zips))
    rows: List[StudyAuditRow] = []
    for key in keys:
        d = pops.datasheet.get(key)
        z = pops.zips.get(key)
        study = (d.study if d else None) or (z.study if z else None) or key
        operator = (d.operator if d else None) or (z.folder_operator if z else None)
        rows.append(audit_row(study, norm_key=key, operator=operator, d=d, z=z,
                              m=pops.materializes.get(key), r=pops.runs.get(key),
                              f=pops.forces.get(key), checks=checks))

    global_findings: List[AuditFinding] = []
    for num, occ in scan_collisions(pops.raw_name_occurrences).items():
        global_findings.append(_finding(NAME_COLLISION,
                                         f"study number '{num}' reused by {len(occ)} studies",
                                         {"number": num, "occurrences": occ}))
    for orphan in pops.force_orphans:
        global_findings.append(_finding(FORCE_ORPHAN, f"force study '{orphan}' has no datasheet row",
                                         {"study": orphan}))

    return AuditResult(rows=rows, funnel=funnel(rows), global_findings=global_findings)

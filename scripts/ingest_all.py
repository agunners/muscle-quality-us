#!/usr/bin/env python
"""The ONE full-ingestion pipeline — Drive to analysis-ready, in a single command.

Why this exists (Akadir, 2026-07-30). Ingestion was seven separate entry points whose correct ORDER
lived only in a memory file and in whoever was driving. On 2026-07-30 the first five ran, every one
reported success, and the last two were silently owed: `qa_master.csv` stayed at 348 rows from 07-21
while 98 studies sat on disk, so an analysis run that afternoon would have used n=66 believing it had
everything. Nobody was told. **Ingestion is not finished when files land on disk — it is finished when
the data is usable by analysis**, and a pipeline that cannot say which of those two states it is in is
not a pipeline.

Three properties this has that running the scripts by hand does not:

  * it REPORTS the population census after every stage, so a number never changes unannounced;
  * it STOPS AND ASKS at the four points a human genuinely decides — the three that write to the
    shared operator sheet (new rows, recording verdicts, force values + measurement flags; other
    people read it), and re-deriving the new-round cohort, which is a FREEZE because it moves the
    placement reference the app serves. NOT yet gated, and listed as open in
    `docs/DATA_LIFECYCLE.md`: a study that is ingest-ready but carries a ❌ verdict, and a probe
    or operator the protocol does not know;
  * it refuses to claim success until a COMPLETION CONTRACT holds. Declining a stage is always
    allowed; being unaware that you declined it is not.

PHI: each stage's own output is echoed verbatim, and the intake scripts print study names, which
carry patient identifiers by design (operators need to recognise a study). Terminal use is fine; do
not paste a run log into a shared doc, a ticket or a commit message without scrubbing it.

    python scripts/ingest_all.py --status     # where does the pipeline stand? runs nothing
    python scripts/ingest_all.py              # dry-run every stage that supports one
    python scripts/ingest_all.py --apply      # run for real, asking at each gate
    python scripts/ingest_all.py --apply --yes  # non-interactive; gates auto-declined, then reported
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

PY = str(REPO / ".venv" / "bin" / "python")

# Child stages import `labeling_app` (repo root) and the two sibling repos (scripts/env.sh's bridge).
# None of that is guaranteed by the caller's shell — the 2026-08-11 freeze died on
# `ModuleNotFoundError: labeling_app` when run with the venv active but env.sh unsourced — so the
# launcher supplies the full path itself: the pipeline must work from any directory, any shell.
# The bridge paths mirror scripts/env.sh (the single place they are otherwise defined).
_BRIDGE = [str(REPO), str(SCRIPTS), str(Path(__file__).resolve().parents[1] / "private" / "muscle_quality_exploration"),
           str(Path(__file__).resolve().parents[1] / "private" / "muscle_model_training/src")]
CHILD_ENV = {**os.environ,
             "PYTHONPATH": ":".join(_BRIDGE + ([os.environ["PYTHONPATH"]]
                                               if os.environ.get("PYTHONPATH") else []))}
STUDIES_ROOT = REPO / "data" / "studies_root"
REGISTRY = REPO / "data" / "study_registry.csv"
QA_MASTER = REPO / "data" / "qa_master.csv"
NEW_COHORT = REPO / "data" / "cohort_stats" / "per_study_cohort_new.csv"
PROBE_CLASS = REPO / "data" / "cohort_stats" / "probe_classification.csv"
PARITY_BASELINE = SCRIPTS / "parity_baseline.json"
FREEZE_LOG = REPO / "data" / "cohort_stats" / "new_round_freeze_log.jsonl"
# The flag→kill LEDGER (grown from the kill list, 2026-08-12 — same file, same founding entry).
# Study grain: rows Akadir has pronounced DEAD (2026-08-11, sheet row 96: a broken export with zero
# RawImages — "kill it and just move… there is no way to save it now or later"). A killed row stays
# visible in the operators' sheet but stops counting anywhere here: the ready-rows invariant
# excludes it, the census names the exclusion so it is ignored-but-never-invisible. Measurement
# grain: suspect dynamometer CELLS the sync-time gate flags (hard tier = held out of qa_master
# pending human verdict). Feature grain: per-study withholds (AKA017 texture). Parsing lives in
# preflight_helpers/flag_kill_ledger.py — ONE reader, never ad-hoc json loops.
KILL_LIST = REPO / "data" / "kill_list.jsonl"


def killed_keys() -> set:
    from preflight_helpers import flag_kill_ledger as fkl
    return fkl.killed_name_hashes()

# Substrate columns that NO amount of re-measuring can produce — they exist only in the operator
# datasheet, so they arrive through a live sheet read or not at all. The operator's recording verdict
# and all five dynamometer columns, the criterion the force work is validated against.
SHEET_ONLY_COLUMNS = ("kontrol_verdict", "izo_torque_nm", "izo_cv_pct",
                      "isok_torque_ext_nm", "isok_mean_power_ext_w", "isok_cv_pct")


# --------------------------------------------------------------------------- census
@dataclass
class Census:
    """Every population number the project quotes, from one place, with its source."""
    studies_on_disk: int = 0
    v8_on_disk: int = 0
    flat_links: int = 0
    flat_dangling: int = 0   # symlinks that point nowhere — the viewer sees these as absent
    registry_rows: int = 0
    registry_present: int = 0
    cohort: dict = field(default_factory=dict)          # cohort -> {present, missing}
    unknown_present: int = 0
    substrate_rows: int = 0
    substrate_by_round: dict = field(default_factory=dict)
    substrate_built: str = ""
    substrate_new_rows: int = 0
    substrate_sheet_cover: dict = field(default_factory=dict)  # datasheet-only col -> non-empty count
    present_dirnames: set = field(default_factory=set)   # folder names actually materialized on disk
    filled_not_on_disk: Optional[int] = None            # operator says ready, nothing was materialized
    filled_missing_rows: list = field(default_factory=list)  # their SHEET ROW NUMBERS (never names: PHI)
    # Parallel Sıra values for the rows above. Every row number this pipeline prints or records
    # (census, contract, sheet_row_at_flag) is the PHYSICAL sheet row — header = row 1 — while the
    # sheet's own Sıra column runs one behind. Conflating the two cost a mis-diagnosis on
    # 2026-08-24 (row 168 read as Sıra 168), so the contract now prints both.
    filled_missing_sira: list = field(default_factory=list)
    killed_rows: list = field(default_factory=list)     # rows on the KILL_LIST — excluded, shown, dead
    meas_flags_hard: Optional[int] = None    # ledger: open hard measurement flags (HELD from analysis)
    meas_flags_soft: Optional[int] = None    # ledger: open soft flags (in analysis, visible)
    meas_killed: Optional[int] = None        # ledger: killed measurement cells
    meas_flag_rows: list = field(default_factory=list)   # sheet rows of open flags (PHI-free)
    held_violations: Optional[int] = None    # qa_master rows carrying a value the ledger holds
    new_cohort_rows: int = 0
    new_cohort_frozen: str = ""
    newest_study: str = ""
    sheet_rows: Optional[int] = None
    sheet_filled: Optional[int] = None
    sheet_not_ready: Optional[int] = None
    legacy_128l: Optional[int] = None
    legacy_128lh: Optional[int] = None
    legacy_unknown_probe: Optional[int] = None
    set_aside_in_cohort: Optional[int] = None   # 128LH studies inside the ACTIVE placement cohort
    drive_zips: Optional[int] = None
    unlogged: Optional[int] = None       # in Drive, no datasheet row => invisible to every stage
    parity_studies: Optional[int] = None  # study count the committed parity baseline claims
    unknown_placeable: int = 0   # present + cohort=unknown + COULD be placed => a real failure
    unknown_exempt: list = field(default_factory=list)  # present + structurally unplaceable
    disk_dup_groups: list = field(default_factory=list)  # UNDECIDED same-recording twins on disk (uids)
    disk_dups_decided: int = 0   # duplicate groups already resolved by a study kill
    code_collisions: dict = field(default_factory=dict)  # display code -> living studies sharing it (>1)
    disk_killed: int = 0         # ledger-killed studies still on disk (out of cohort + substrate)
    freezelog_n: Optional[int] = None   # n_studies of the newest recorded re-freeze
    freezelog_r: Optional[int] = None   # its refreeze number
    landmark_ok: bool = False           # r<N>'s frame evidence exists and lists exactly the served uids
    landmark_state: str = ""            # the one-line reason either way
    notes: list = field(default_factory=list)


def _mtime(p: Path) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(p.stat().st_mtime)) if p.exists() else "—"


def census(*, remote: bool = False) -> Census:
    """Local facts always; sheet facts only when asked, degrading with a note rather than dying."""
    from preflight_helpers.study_discovery import study_is_new_round

    c = Census()
    studies = [p for p in STUDIES_ROOT.glob("*/*/*") if p.is_dir()] if STUDIES_ROOT.exists() else []
    c.studies_on_disk = len(studies)
    c.v8_on_disk = sum(1 for p in studies if study_is_new_round(p))
    # Count links that RESOLVE, not entries that exist. Counting entries reported `flat links 116` on
    # a machine where all 116 were dangling and the viewer listed nothing — a number that was right
    # about the filesystem and wrong about the world (2026-08-03, DGX).
    flat = REPO / "data" / "studies_root_flat"
    if flat.is_dir():
        entries = list(flat.iterdir())
        c.flat_links = sum(1 for p in entries if p.exists())      # exists() follows the link
        c.flat_dangling = sum(1 for p in entries if p.is_symlink() and not p.exists())
    else:
        c.flat_links = c.flat_dangling = 0
    if studies:
        c.newest_study = time.strftime("%Y-%m-%d",
                                       time.localtime(max(p.stat().st_mtime for p in studies)))

    if REGISTRY.exists():
        rows = list(csv.DictReader(open(REGISTRY, encoding="utf-8")))
        c.registry_rows = len(rows)
        c.registry_present = sum(1 for r in rows if r.get("status") == "present")
        c.present_dirnames = {r.get("dirname", "") for r in rows if r.get("status") == "present"}
        for r in rows:
            slot = c.cohort.setdefault(r.get("cohort", "?"), {"present": 0, "missing": 0})
            slot[r.get("status", "missing")] = slot.get(r.get("status", "missing"), 0) + 1
        c.unknown_present = c.cohort.get("unknown", {}).get("present", 0)
        # A study is PLACEABLE when it could join a cohort at all: v8 studies go to the new round,
        # legacy-coded ones to the old 282. A study that is neither — an operator self-scan, say —
        # can never be placed, so counting it as an incomplete ingestion makes the contract
        # permanently red, and a gate that can never go green is one people stop reading. That is
        # how the 07-30 substrate gap survived a run in which every stage reported success. So:
        # exempt the unplaceable, NAME it so it stays visible, and keep failing on the case the
        # invariant exists for — a v8 study that did not reach the cohort.
        v8_rel = {str(p.relative_to(STUDIES_ROOT)) for p in studies if study_is_new_round(p)}
        from preflight_helpers.flag_kill_ledger import killed_study_uids
        dead_uids = killed_study_uids()
        for r in rows:
            if r.get("status") != "present" or r.get("cohort") != "unknown":
                continue
            if (r.get("study_uid") or "") in dead_uids:
                # Ledger-killed at study grain: out of every cohort BY DECISION, and the registry
                # rebind resets its cohort to unknown the moment the freeze drops it (the AKA158
                # twin, first seen at r5). It can never be placed again, so it is exempt — named,
                # or the contract goes permanently red on a case a human already decided.
                c.unknown_exempt.append((r.get("display_code") or r.get("study_uid") or "?")
                                        + " (ledger-killed)")
            elif r.get("rel_path") in v8_rel or (r.get("legacy_study_code") or "").strip():
                c.unknown_placeable += 1
            else:                       # display_code / study_uid only — dirname carries PHI
                c.unknown_exempt.append(r.get("display_code") or r.get("study_uid") or "?")
        # Duplicate-ingest + display-code collision watch (2026-08-13, the AKA158 lesson). Two
        # dirs whose names NORMALIZE to the same study (Turkish-fold + '__' collapse — the
        # pipeline's own join key) are the SAME recording materialized twice: one person counting
        # twice in every percentile until a human kills one twin. Display-code collisions are the
        # opposite case — DIFFERENT patients sharing an intake number (44 studies at discovery):
        # legitimate data, a display hazard, disambiguated in the picker and counted here so a
        # fresh numbering slip is visible at entry. uids/codes only — dirnames carry PHI.
        from sync_force_measures import _norm as _nrm
        from preflight_helpers import flag_kill_ledger as _fkl
        dead_uids = _fkl.killed_study_uids()
        c.disk_killed = sum(1 for r in rows if r.get("status") == "present"
                            and (r.get("study_uid") or "?") in dead_uids)
        by_key: dict = {}
        for r in rows:
            if r.get("status") == "present" and r.get("dirname"):
                by_key.setdefault(_nrm(r["dirname"]), []).append(r.get("study_uid") or "?")
        for group in by_key.values():
            if len(group) < 2:
                continue
            living = sorted(u for u in group if u not in dead_uids)
            if len(living) > 1:
                c.disk_dup_groups.append(living)
            else:
                c.disk_dups_decided += 1
        codes: dict = {}
        for r in rows:
            code = (r.get("display_code") or "").strip()
            if (r.get("status") == "present" and code
                    and (r.get("study_uid") or "?") not in dead_uids):
                codes[code] = codes.get(code, 0) + 1
        c.code_collisions = {k: n for k, n in sorted(codes.items()) if n > 1}

    if QA_MASTER.exists():
        qm = list(csv.DictReader(open(QA_MASTER, encoding="utf-8")))
        c.substrate_rows = len(qm)
        for r in qm:
            k = r.get("cohort_round", "") or "?"
            c.substrate_by_round[k] = c.substrate_by_round.get(k, 0) + 1
        c.substrate_built = _mtime(QA_MASTER)
        # Columns ONLY the datasheet can supply. When the live sheet read fails,
        # build_qa_sheet.load_sheet_rows returns {} — a documented graceful degrade that warns to
        # stderr and nowhere else — and EVERY one of these blanks for EVERY study while every
        # image-derived column (score, QMT provenance, texture trust) stays perfect. That asymmetry
        # is the signature. Counted per column so the contract can tell a FAILED READ (0 of N) from
        # a fact about people (96 of 116 had a dynamometer session; 20 did not, and that is fine).
        new_rows = [r for r in qm if (r.get("cohort_round") or "") != "old"]
        c.substrate_new_rows = len(new_rows)
        c.substrate_sheet_cover = {k: sum(1 for r in new_rows if (r.get(k) or "").strip())
                                   for k in SHEET_ONLY_COLUMNS}
    # The flag→kill ledger's measurement grain — open flags are printed every run so a suspect
    # criterion value is never invisible (the same ignored-but-never-invisible rule as the kills).
    try:
        from preflight_helpers import flag_kill_ledger as fkl
        flags = fkl.open_flags()
        c.meas_flags_hard = sum(1 for e in flags if e.get("tier") == "hard")
        c.meas_flags_soft = sum(1 for e in flags if e.get("tier") == "soft")
        c.meas_killed = sum(1 for e in fkl.current().values()
                            if e["scope"] == "measurement" and e["status"] == "killed")
        c.meas_flag_rows = sorted(e.get("sheet_row_at_flag") for e in flags
                                  if e.get("sheet_row_at_flag") is not None)
        # Enforcement: every LEDGER-held criterion cell must be blank in qa_master. Derived from
        # the ledger, the source of truth — never from qa_master's own `measurement_flags`
        # receipt: the receipt and the blanking are written by the same build_rows consumption,
        # so a substrate that stopped consuming the ledger wipes both TOGETHER and a
        # receipt-based check sees nothing (the red-team review's reproduction, 2026-08-12).
        # A held study absent from the substrate cannot leak and does not count. A substrate
        # built BEFORE the gate genuinely serves suspect values, so failing on it is correct —
        # the printed fix is one rebuild.
        if QA_MASTER.exists():
            by_uid = {r.get("study_uid"): r for r in qm}
            c.held_violations = sum(
                1 for uid, fields in fkl.open_measurement_holds().items()
                if by_uid.get(uid) is not None
                and any((by_uid[uid].get(f) or "").strip() for f in fields))
    except Exception as e:                                   # noqa: BLE001 - advisory
        c.notes.append(f"flag→kill ledger unreadable ({type(e).__name__})")

    # The legacy corpus was shot on TWO probes and 128LH is set aside — a texture confound on the
    # axis carrying 43% of the score. This is checked HERE, every run, because a classification file
    # nobody reads is how the fact got lost for two months the first time.
    if PROBE_CLASS.exists():
        pcls = list(csv.DictReader(open(PROBE_CLASS, encoding="utf-8")))
        c.legacy_128l = sum(1 for r in pcls if r["probe"] == "128L")
        c.legacy_128lh = sum(1 for r in pcls if r["probe"] == "128LH")
        c.legacy_unknown_probe = sum(1 for r in pcls if r["probe"] == "unknown")
        # Read the ACTIVE reference through cohort_placement's own pointer, never a copy of the
        # filename: a re-freeze repoints that dict, and a hardcoded name here would keep auditing
        # the superseded file and report a contamination that is no longer served.
        active = set()
        try:
            from preflight_helpers import cohort_placement as cp
            f = REPO / "data" / "cohort_stats" / cp._COHORT_FILE[cp.OLD]
            if f.exists():
                active = {r["study_code"] for r in csv.DictReader(open(f, encoding="utf-8"))}
        except Exception as e:                                   # noqa: BLE001 - advisory
            c.notes.append(f"could not resolve the active cohort ({type(e).__name__})")
        c.set_aside_in_cohort = sum(1 for r in pcls
                                    if r["set_aside"] == "True" and r["study_code"] in active)

    if NEW_COHORT.exists():
        c.new_cohort_rows = len(list(csv.DictReader(open(NEW_COHORT, encoding="utf-8"))))
        c.new_cohort_frozen = _mtime(NEW_COHORT)

    # A re-freeze moves the placement reference every patient is scored against. The log is the only
    # record of BY HOW MUCH — on the 66->93 freeze, int_cv_within percentiles moved a median 6.67
    # points on the axis carrying 43% of the score. If the log's newest entry does not describe the
    # cohort actually being served, that movement happened unrecorded.
    if FREEZE_LOG.exists():
        entries = [json.loads(ln) for ln in
                   FREEZE_LOG.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if entries:
            c.freezelog_n, c.freezelog_r = entries[-1]["n_studies"], entries[-1]["refreeze"]

    # Since 2026-09-03 each freeze also keeps WHICH frames its numbers came from
    # (new_round_landmark_r<N>.json, uid-keyed, name-free). The freezelog stage writes it after the
    # log row and says so in one line of a nine-stage run — so the contract asks for it explicitly:
    # the doctors' grading pack shows raters the frames the served numbers came from, and without this
    # file that claim rests on a gitignored scratch file the next apply overwrites.
    if c.freezelog_r is not None:
        snap = REPO / "data" / "cohort_stats" / f"new_round_landmark_r{c.freezelog_r}.json"
        if not snap.exists():
            c.landmark_state = (f"no {snap.name} — nothing durable says which frames the served "
                                f"numbers came from")
        else:
            try:
                d = json.loads(snap.read_text(encoding="utf-8"))
                uids = {s.get("study_uid") for s in d.get("studies", [])}
                served = ({r["study_code"] for r in csv.DictReader(open(NEW_COHORT, encoding="utf-8"))}
                          if NEW_COHORT.exists() else set())
                if uids == served:
                    c.landmark_ok = True
                    c.landmark_state = (f"{snap.name}: {len(uids)} studies = the served cohort, "
                                        f"frame_identity {d.get('frame_identity')}")
                else:
                    c.landmark_state = (f"{snap.name} lists {len(uids)} uids vs {len(served)} served "
                                        f"({len(served - uids)} served studies missing, "
                                        f"{len(uids - served)} extra)")
            except Exception as e:      # a truncated or foreign file is a FAIL with its reason, not a crash
                c.landmark_state = f"{snap.name} unreadable: {e}"

    # Read the baseline's own CONTENT, never its mtime: `git checkout` stamps a pulled file with the
    # pull time, so an mtime test would call a freshly-pulled stale baseline fresh — which is exactly
    # the machine (a just-pulled second machine) the check exists to warn.
    if PARITY_BASELINE.exists():
        try:
            c.parity_studies = json.load(
                open(PARITY_BASELINE, encoding="utf-8"))["studies"]["local"]["discovered"]
        except Exception as e:                                   # noqa: BLE001 - advisory
            c.notes.append(f"parity baseline unreadable ({type(e).__name__})")

    if remote:
        try:
            import sync_records as sr
            from preflight_helpers import gws_io as io, datasheet_schema as ds
            cfg = json.load(open(REPO / "configs" / "round3_intake.json", encoding="utf-8"))
            hk = cfg["sheets"]["hasta_kayit"]
            title = io.first_sheet(io.sheets_meta(hk))["title"]
            vals = io.sheets_values_get(hk, f"{title}!A1:{sr.LAST_COL}1000") or []
            hdr = vals[0] if vals else []
            body = [r for r in vals[1:] if any((x or "").strip() for x in r)]
            c.sheet_rows = len(body)
            if "Dosyalar Tam mı" in hdr and "Study Adı" in hdr and "Operator" in hdr:
                di, si, oi = (hdr.index("Dosyalar Tam mı"), hdr.index("Study Adı"),
                              hdr.index("Operator"))
                def cell(r, i): return (r[i] if len(r) > i else "").strip()
                real = [r for r in body if cell(r, si) and cell(r, oi)]
                c.sheet_filled = sum(1 for r in real if cell(r, di) == "Evet")
                c.sheet_not_ready = len(real) - c.sheet_filled
                # A row the operator marked READY that never became a study on disk. ingest.py
                # prints `HOLD <study> (<reason>)` and moves on with exit 0, writes no hold record
                # (data/intake_hold/ is empty), and the dry-run view kept only the last 3 lines — so
                # the two studies held on 2026-08-03 vanished with no durable trace. Every other
                # invariant here is disk-relative, so nothing compared the datasheet to the disk.
                # Row NUMBERS, never study names: those carry patient identifiers.
                from sync_force_measures import _norm as _n
                import hashlib
                on_disk = {_n(d) for d in c.present_dirnames}
                dead = killed_keys()
                def _k8(name):
                    return hashlib.sha256(_n(name).encode("utf-8")).hexdigest()[:8].upper()
                sira_i = hdr.index("Sıra") if "Sıra" in hdr else None
                cand = [(i, cell(r, si), cell(r, sira_i) if sira_i is not None else "")
                        for i, r in enumerate(vals[1:], start=2)
                        if cell(r, si) and cell(r, oi) and cell(r, di) == "Evet"
                        and _n(cell(r, si)) not in on_disk]
                c.killed_rows = [i for i, nm, _s in cand if _k8(nm) in dead]
                miss = [(i, s) for i, nm, s in cand if _k8(nm) not in dead]
                c.filled_missing_rows = [i for i, _s in miss]
                c.filled_missing_sira = [s for _i, s in miss]
                c.filled_not_on_disk = len(c.filled_missing_rows)
                # A zip an operator uploaded but nobody logged is invisible to check_studies AND
                # ingest — both are row-gated — so it never appears in any other count. On
                # 2026-07-30 eight arrived mid-afternoon and nothing would have said so.
                # Match the way the pipeline itself matches: the SHARED normalized key, which
                # folds Turkish letters and collapses the '__' typo. An exact stem compare
                # over-reports — it counted the 4 known name-fixup studies as unlogged.
                from sync_force_measures import _norm
                names = {_norm(cell(r, si)) for r in real}
                zips = 0
                for fol in io.drive_list_children(cfg["drive"]["goruntuler_folder"],
                                                  folders_only=True):
                    kids = io.drive_list_children(fol["id"], zips_only=True)
                    zips += len(kids)
                    for z in kids:
                        if _norm(Path(z["name"]).stem) not in names:
                            c.unlogged = (c.unlogged or 0) + 1
                c.drive_zips = zips
                c.unlogged = c.unlogged or 0
        except Exception as e:                                   # noqa: BLE001 - advisory
            c.notes.append(f"sheet/Drive unreadable ({type(e).__name__}); local facts only")
    return c


def print_census(c: Census, *, title: str = "CENSUS") -> None:
    print(f"\n  ── {title} " + "─" * max(0, 62 - len(title)))
    if c.drive_zips is not None:
        flag = "  <-- INVISIBLE to every stage" if c.unlogged else ""
        print(f"     Drive zips         {c.drive_zips:>4}        "
              f"({c.unlogged} with no datasheet row){flag}")
    if c.sheet_rows is not None:
        print(f"     datasheet          {c.sheet_rows:>4} rows   "
              f"({c.sheet_filled} filled · {c.sheet_not_ready} awaiting an operator)")
    _dang = f"  <-- {c.flat_dangling} DANGLING (viewer sees them as absent)" if c.flat_dangling else ""
    print(f"     on disk            {c.studies_on_disk:>4} studies ({c.v8_on_disk} on protocol v8)"
          f"   flat links {c.flat_links}{_dang}")
    coh = " · ".join(f"{k} {v['present']}/{v['present'] + v['missing']}"
                     for k, v in sorted(c.cohort.items()))
    print(f"     registry           {c.registry_rows:>4} rows   present {c.registry_present}"
          + (f"   [{coh}]" if coh else ""))
    if c.disk_dup_groups or c.disk_dups_decided:
        grp = "; ".join(" + ".join(g) for g in c.disk_dup_groups)
        open_txt = (f"{len(c.disk_dup_groups)} UNDECIDED ({grp})" if c.disk_dup_groups
                    else "0 undecided")
        print(f"     └─ duplicate watch: {open_txt} · {c.disk_dups_decided} decided "
              f"(killed twin stays on disk, out of every cohort)")
    if c.code_collisions:
        print(f"     └─ display-code collisions: {len(c.code_collisions)} code(s) shared by "
              f"{sum(c.code_collisions.values())} studies — distinct patients, disambiguated "
              f"in the picker")
    _fl = (f"   [freeze log r{c.freezelog_r}: {c.freezelog_n}]"
           + ("  <-- DOES NOT DESCRIBE THIS COHORT" if c.freezelog_n != c.new_cohort_rows else "")
           ) if c.freezelog_n is not None else "   [freeze log: none]"
    print(f"     new-round cohort   {c.new_cohort_rows:>4} rows   frozen {c.new_cohort_frozen}{_fl}")
    print(f"     substrate          {c.substrate_rows:>4} rows   built {c.substrate_built}"
          f"   {c.substrate_by_round or ''}")
    if c.substrate_sheet_cover:
        force = c.substrate_sheet_cover.get("izo_torque_nm", 0)
        verdict = c.substrate_sheet_cover.get("kontrol_verdict", 0)
        flag = ("   <-- SHEET READ FAILED (see contract)"
                if max(c.substrate_sheet_cover.values()) == 0 else "")
        print(f"     └─ from the datasheet: isometric force {force}/{c.substrate_new_rows}"
              f" · operator verdict {verdict}/{c.substrate_new_rows}{flag}")
    if c.meas_flags_hard is not None and (c.meas_flags_hard or c.meas_flags_soft or c.meas_killed):
        rows_txt = (f"   [sheet row(s) {', '.join(str(r) for r in c.meas_flag_rows)}]"
                    if c.meas_flag_rows else "")
        n_open = (c.meas_flags_hard or 0) + (c.meas_flags_soft or 0)
        print(f"     └─ measurement gate: {n_open} flagged rows serve NO force "
              f"({c.meas_flags_hard} hard · {c.meas_flags_soft} soft) pending human verdict · "
              f"{c.meas_killed} killed{rows_txt}")
    if c.legacy_128lh is not None:
        print(f"     legacy probes      128L {c.legacy_128l} · 128LH {c.legacy_128lh} (set aside)"
              f" · unknown {c.legacy_unknown_probe}")
        flag = "   <-- WRONG PROBE in the placement reference" if c.set_aside_in_cohort else ""
        print(f"     └─ 128LH still in the active cohort: {c.set_aside_in_cohort}{flag}")
    for n in c.notes:
        print(f"     note: {n}")


# --------------------------------------------------------------------------- contract
def contract(c: Census) -> list[tuple[bool, str, str]]:
    """What must hold for ingestion to be FINISHED — i.e. the data usable by analysis.

    Each is an invariant that would have caught the 2026-07-30 gap on its own.
    """
    out: list[tuple[bool, str, str]] = []
    if c.unlogged is not None:
        out.append((c.unlogged == 0,
                    "every Drive zip has a datasheet row",
                    f"{c.unlogged} uploaded zip(s) nobody logged — invisible to check and ingest"))
    if c.filled_not_on_disk is not None:
        out.append((c.filled_not_on_disk == 0,
                    "every ready datasheet row became a study on disk",
                    (f"{c.filled_not_on_disk} row(s) the operator marked ready were never "
                     f"materialized — held by ingest with no durable record; sheet row(s) "
                     + ", ".join((f"{i} (Sıra {s})" if s else str(i)) for i, s in zip(
                         c.filled_missing_rows,
                         c.filled_missing_sira or [""] * len(c.filled_missing_rows)))
                     if c.filled_not_on_disk else
                     "0 ready rows are missing from disk")
                    + (f" · {len(c.killed_rows)} KILLED row(s) excluded, dead by decision "
                       f"(row {', '.join(str(i) for i in c.killed_rows)}; data/kill_list.jsonl)"
                       if c.killed_rows else "")))
    if c.set_aside_in_cohort is not None:
        out.append((c.set_aside_in_cohort == 0,
                    "no set-aside (128LH) study is in the active cohort",
                    f"{c.set_aside_in_cohort} wrong-probe studies are inside the placement reference "
                    f"every patient is scored against"))
    if c.legacy_unknown_probe is not None:
        out.append((c.legacy_unknown_probe == 0,
                    "every legacy study has a determined probe",
                    f"{c.legacy_unknown_probe} legacy studies have no probe assignment"))
    out.append((c.registry_present == c.studies_on_disk,
                "every study on disk is registered",
                f"registry present {c.registry_present} vs {c.studies_on_disk} on disk"))
    # Two on-disk studies that normalize to the same name are one recording counted twice —
    # the AKA158 duplicate sat inside the frozen 125 undetected (2026-08-13). A killed twin
    # resolves its group; only an UNDECIDED group fails.
    out.append((not c.disk_dup_groups,
                "no undecided duplicate ingest on disk",
                (f"{len(c.disk_dup_groups)} same-recording group(s) on disk: "
                 f"{'; '.join(' + '.join(g) for g in c.disk_dup_groups)} — one person is counting "
                 f"twice; kill the twin: measurement_verdict.py --scope study --kill --uid <NR…>"
                 if c.disk_dup_groups else
                 f"{c.disk_dups_decided} duplicate group(s), all decided")))
    out.append((c.unknown_placeable == 0,
                "no study on disk is left cohort=unknown",
                f"{c.unknown_placeable} placeable studies have no cohort"
                + (f"; {len(c.unknown_exempt)} exempt (unplaceable or ledger-killed): "
                   f"{', '.join(sorted(c.unknown_exempt))}" if c.unknown_exempt else "")))
    non_old = sum(v for k, v in c.substrate_by_round.items() if k != "old")
    out.append((non_old == c.v8_on_disk - c.disk_killed,
                "the substrate covers every living v8 study on disk",
                f"substrate non-old rows {non_old} vs {c.v8_on_disk} v8 studies on disk"
                + (f" ({c.disk_killed} ledger-killed, rightly excluded)" if c.disk_killed else "")))
    out.append((bool(c.newest_study) and c.new_cohort_frozen >= c.newest_study,
                "the new-round cohort is not older than the newest study",
                f"cohort frozen {c.new_cohort_frozen} vs newest study {c.newest_study}"))
    out.append((bool(c.newest_study) and c.substrate_built >= c.newest_study,
                "the substrate is not older than the newest study",
                f"substrate built {c.substrate_built} vs newest study {c.newest_study}"))
    out.append((c.freezelog_n == c.new_cohort_rows,
                "the freeze log describes the cohort being served",
                (f"log r{c.freezelog_r} describes {c.freezelog_n} studies vs {c.new_cohort_rows} "
                 f"in the served cohort" if c.freezelog_n is not None
                 else f"no freeze recorded for the {c.new_cohort_rows}-study reference")))
    out.append((c.landmark_ok,
                "the freeze's frame evidence is on disk and describes the served cohort",
                c.landmark_state or "no freeze recorded, so no evidence to check"))
    # The substrate was rebuilt on 2026-08-03 while gws was expired, and arrived with the operator
    # verdict and ALL FIVE dynamometer columns empty on all 116 new-round studies — while this
    # contract read COMPLETE 8/8, because every other item asks about COVERAGE (is each study
    # present, registered, placed, fresh?) and none asks whether the values inside are there.
    # Analysis would have read force n=0 and concluded the criterion did not exist.
    #
    # The test is deliberately "not ALL empty", not "every study has force". 20 of 116 genuinely had
    # no dynamometer session, which is a fact about people; demanding full coverage would make the
    # gate permanently red, and a gate that cannot go green is one people stop reading (the same
    # lesson as the cohort=unknown self-scan). A failed read is CATEGORICAL — it blanks every one of
    # these columns for every study at once — so zero coverage across all of them is the signature.
    if c.substrate_new_rows and c.substrate_sheet_cover:
        cover = ", ".join(f"{k} {v}/{c.substrate_new_rows}"
                          for k, v in c.substrate_sheet_cover.items())
        empty = max(c.substrate_sheet_cover.values()) == 0
        out.append((not empty,
                    "the substrate carries what only the datasheet can supply",
                    (f"EVERY datasheet-only column is empty across all {c.substrate_new_rows} "
                     f"new-round studies — the live sheet read failed at build time and degraded "
                     f"silently. Re-auth gws, then: ingest_all.py --apply --only substrate"
                     if empty else cover)))
    # A cell the LEDGER holds (hard-flagged or killed) must actually be blank in qa_master —
    # otherwise the substrate stopped consuming the ledger and analysis is reading a value a
    # human was told is suspect. Checked ledger→cells (never via qa_master's own receipt, which
    # the same regression wipes). Omitted only when qa_master does not exist; an open flag itself
    # is a normal pending state and never fails the contract.
    if c.held_violations is not None:
        out.append((c.held_violations == 0,
                    "held measurements are actually held",
                    (f"{c.held_violations} qa_master row(s) carry a value in a criterion cell the "
                     f"flag→kill ledger holds — rebuild: ingest_all.py --apply --only substrate"
                     if c.held_violations else
                     "every ledger-held criterion cell is blank in qa_master")))
    return out


def print_contract(c: Census) -> bool:
    rows = contract(c)
    print("\n  ── COMPLETION CONTRACT " + "─" * 45)
    for ok, name, detail in rows:
        print(f"     {'PASS' if ok else 'FAIL'}  {name}\n           {detail}")
    done = all(ok for ok, _, _ in rows)
    print(f"\n     => ingestion is {'COMPLETE' if done else 'NOT COMPLETE'}"
          f" — data {'is' if done else 'is NOT'} usable by analysis")
    return done


def print_handover(c: Census) -> None:
    """Is this machine's cross-machine receipt current? Reported, never gated.

    Deliberately NOT a contract item. The contract answers "is the data usable by analysis" and a
    stale baseline does not make data unusable; folding it in would also deadlock, since the baseline
    is only safe to save once the contract already passes. But nothing else ever mentions it —
    `check_parity.py --save-baseline` has no caller anywhere in the repo, so between 2026-07-08 and
    08-02 the baseline sat 27 studies behind while `check_parity.py` reported MATCH. A receipt whose
    staleness nothing announces is how that happens; announcing it here is the cheapest fix.
    """
    print("\n  ── HANDOVER (cross-machine) " + "─" * 41)
    if c.parity_studies is None:
        print("     parity baseline    ABSENT — no cross-machine receipt exists")
        print("        On the machine that holds the complete data, once the contract reads")
        print("        COMPLETE:  .venv/bin/python scripts/check_parity.py --save-baseline")
        return

    n, disk = c.parity_studies, c.studies_on_disk
    state = "CURRENT" if n == disk else ("STALE" if n < disk else "THIS MACHINE IS BEHIND")
    print(f"     parity baseline    describes {n} studies · this machine has {disk}    {state}")

    # The two directions need OPPOSITE advice, and conflating them is actively dangerous: telling a
    # machine that is behind to --save-baseline would overwrite the golden state with the poorer one.
    if n < disk:            # the baseline lags this machine => this machine is the source of truth
        print("        <-- check_parity.py compares against this file, so it will report MATCH")
        print(f"            on any machine holding {n} studies — including one that never")
        print("            received the newer ones.")
        print("        Refresh it AFTER the contract above reads COMPLETE, then commit:")
        print("          .venv/bin/python scripts/check_parity.py --save-baseline")
    elif n > disk:          # this machine lags the baseline => it has data still to receive
        print(f"        <-- the golden state has {n} studies and this machine holds {disk}.")
        print("            The contract above is DISK-RELATIVE — it can read COMPLETE here while")
        print("            studies recorded elsewhere are still missing. Ingest them:")
        print("              .venv/bin/python scripts/ingest_all.py --apply")
        print("        Do NOT run --save-baseline here: it would overwrite the golden state")
        print("        with this machine's poorer one.")


# --------------------------------------------------------------------------- stages
@dataclass
class Stage:
    key: str
    title: str
    why: str
    dry: Optional[list]          # argv for a dry run; None = cannot dry-run
    apply: list                  # argv for the real run
    gate: str = ""               # non-empty => stop and ask before applying


STAGES = [
    Stage("sync", "Drive → datasheet rows", "an uploaded zip with no row is invisible to everything",
          [PY, "scripts/sync_records.py"], [PY, "scripts/sync_records.py", "--apply"],
          gate="writes NEW ROWS to the shared operator datasheet"),
    Stage("check", "recording verdicts", "the operator's only feedback signal",
          [PY, "scripts/check_studies.py"], [PY, "scripts/check_studies.py", "--apply"],
          gate="writes ✅/⚠️/❌ into the shared operator datasheet"),
    # AFTER check (rows exist to receive values), BEFORE substrate (which reads them). Until
    # 2026-08-12 this join ran only when someone remembered the script by hand — the exact
    # "order lives in someone's head" failure this pipeline abolishes — and the dynamometer
    # numbers it carries passed NO check at entry (two kgf·m-as-Nm entries sat in the criterion
    # for weeks; DATA_LIFECYCLE §7 item 8).
    Stage("forcesync", "force datasheet → kayit + measurement gate",
          "dynamometer entries are the criterion; the gate is the only check they pass at entry",
          [PY, "scripts/sync_force_measures.py"],
          [PY, "scripts/sync_force_measures.py", "--apply"],
          gate="writes force values + 'Ölçüm Kontrol' plausibility flags into the shared "
               "operator datasheet"),
    Stage("ingest", "pull + materialize", "no filled row, no pull — consistency is structural",
          [PY, "scripts/ingest.py"], [PY, "scripts/ingest.py", "--apply"]),
    Stage("audit", "capture-consistency report", "flags role/DICOM errors so review is targeted",
          None, [PY, "scripts/audit_capture_consistency.py"]),
    Stage("cohort", "re-derive the new-round cohort",
          "until this runs new studies stay cohort=unknown and analysis cannot see them",
          None, [PY, "scripts/derive_new_round_cohort.py"],
          gate="FREEZE — rewrites per_study_cohort_new.csv, the placement reference the APP serves, "
               "so percentiles shown against 'New round' will move. It also re-segments (slow)"),
    Stage("freezelog", "record what the freeze did to the reference",
          "a reference that moves unrecorded is one nobody can question afterwards",
          [PY, "scripts/cohort_freeze_log.py", "--report"],
          [PY, "scripts/cohort_freeze_log.py", "--record"]),
    # AFTER cohort, not before it. study_registry.py:69/191 derives each study's `cohort` column from
    # MEMBERSHIP in per_study_cohort_new.csv — so running it first assigns against the PREVIOUS
    # reference and every study the freeze just added is stamped `unknown`. That is not cosmetic: those
    # studies are placeable, so the completion contract fails and `--apply` cannot reach COMPLETE in one
    # pass. It happened on 2026-07-30 (28 unknown) and again on 08-03 (23), and both times the fix was a
    # human noticing and re-running the stage by hand — the exact class of "the order lives in someone's
    # head" this pipeline exists to abolish. Nothing between ingest and cohort reads the registry
    # (verified: sync/check/audit have zero references, and derive_new_round_cohort's only `registry`
    # is model_registry, the segmenter bundle), so moving it costs nothing.
    Stage("registry", "assign study ids + bind cohort membership",
          "one id, one table that resolves it — and it can only bind studies the cohort already knows",
          [PY, "scripts/study_registry.py", "--report-only"], [PY, "scripts/study_registry.py"]),
    Stage("substrate", "rebuild the analysis substrate", "qa_master.csv is what analysis reads",
          [PY, "scripts/build_qa_sheet.py", "--no-sheet"],
          [PY, "scripts/build_qa_sheet.py", "--analysis"]),
]


def ask(question: str, detail: str, *, assume_no: bool) -> bool:
    print(f"\n  ⚠ {question}")
    print(f"     {detail}")
    if assume_no:
        print("     [--yes/non-interactive] DECLINED — re-run interactively to approve.")
        return False
    try:
        return input("     proceed? [y/N] ").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print("\n     no answer — declined.")
        return False


_TRANSIENT = ("currently unavailable", "rateLimitExceeded", "backendError", "timed out",
              "Internal error", "503", "429")


def run(argv: list, *, echo: bool = True, attempts: int = 3,
        stream: bool = False) -> tuple[int, str]:
    """Run a stage, retrying a TRANSIENT remote failure. A Google 503 on stage 1 should not end a
    run — but a real failure must still stop it, so only known-transient text is retried.

    `stream` echoes the stage's output as it arrives instead of only the last 3 lines afterwards.
    Capturing was silently costing two things: the cohort re-freeze runs over an HOUR and printed
    nothing for its whole duration, so the only way to tell a working run from a hung one was to go
    read /proc; and per-study lines that matter — `HOLD <study> (<reason>)` — were captured and then
    thrown away, so two held studies vanished without a trace. It also makes this match the module
    docstring, which already promised each stage's output is echoed verbatim."""
    if echo:
        print(f"     $ {' '.join(a.replace(str(REPO) + '/', '') for a in argv)}")
    rc, out = 1, ""
    for i in range(1, attempts + 1):
        if stream:
            proc = subprocess.Popen(argv, cwd=REPO, env=CHILD_ENV, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, bufsize=1)
            chunks = []
            for ln in proc.stdout:                       # PHI: stage output names studies, by design
                chunks.append(ln)
                print(f"     | {ln.rstrip()[:150]}", flush=True)
            rc, out = proc.wait(), "".join(chunks)
        else:
            p = subprocess.run(argv, cwd=REPO, env=CHILD_ENV, capture_output=True, text=True)
            rc, out = p.returncode, p.stdout + p.stderr
        if rc == 0 or not any(t.lower() in out.lower() for t in _TRANSIENT):
            return rc, out
        if i < attempts:
            print(f"     … transient remote error, retry {i}/{attempts - 1}")
            time.sleep(3 * i)
    return rc, out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status", action="store_true",
                    help="report where the pipeline stands and exit — runs no stage")
    ap.add_argument("--apply", action="store_true", help="run for real (default: dry-run)")
    ap.add_argument("--yes", action="store_true",
                    help="non-interactive: gated stages are DECLINED and reported, never guessed")
    ap.add_argument("--only", help="comma-separated stage keys to run")
    ap.add_argument("--offline", action="store_true", help="skip the datasheet read in the census")
    a = ap.parse_args(argv)

    print("=" * 70)
    print(f"  FULL INGESTION  ({'STATUS' if a.status else 'APPLY' if a.apply else 'DRY-RUN'})")
    print("=" * 70)

    before = census(remote=not a.offline)
    print_census(before, title="BEFORE" if not a.status else "NOW")
    if a.status:
        done = print_contract(before)
        print_handover(before)
        if not done:
            print("\n  To finish, run:  python scripts/ingest_all.py --apply")
        return 0 if done else 1

    only = {s.strip() for s in a.only.split(",")} if a.only else None
    declined: list[str] = []

    for st in STAGES:
        if only and st.key not in only:
            continue
        print(f"\n  ── [{st.key}] {st.title} " + "─" * max(0, 50 - len(st.title)))
        print(f"     why: {st.why}")

        if not a.apply:
            if st.dry is None:
                print("     (no dry-run available — skipped; it would run under --apply)")
                continue
            rc, out = run(st.dry)
            tail = [ln for ln in out.strip().splitlines() if ln.strip()][-3:]
            for ln in tail:
                print(f"     | {ln[:120]}")
            if rc != 0:
                print(f"     ! exit {rc} — stopping")
                return rc
            continue

        if st.gate and not ask(f"[{st.key}] {st.gate.split(' —')[0]}?", st.gate, assume_no=a.yes):
            declined.append(st.key)
            print(f"     DECLINED — {st.key} not run.")
            continue

        rc, out = run(st.apply, stream=True)     # live: a silent hour is indistinguishable from a hang
        if rc != 0:
            print(f"     ! exit {rc} — stopping so the failure is not buried")
            return rc

    after = census(remote=not a.offline)
    print_census(after, title="AFTER")
    done = print_contract(after)
    print_handover(after)
    if declined:
        print(f"\n  ⚠ DECLINED this run: {', '.join(declined)} — the contract above shows the cost.")
    if not a.apply:
        print("\n  dry-run — nothing was written. Re-run with --apply.")
    return 0 if (done or not a.apply) else 1


if __name__ == "__main__":
    raise SystemExit(main())

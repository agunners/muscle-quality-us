"""The completion contract — the thing that decides whether ingestion is FINISHED.

This exists because on 2026-07-30 an ingest reported success at every stage while the analysis
substrate stayed 9 days stale and 27 studies sat at cohort=unknown, so an analysis run that day
would have used n=66 believing it had everything. Each invariant below would have caught it alone.
All pure logic on a Census — no disk, no network.
"""
from __future__ import annotations

import pytest

import ingest_all as ia


def _complete(**over) -> ia.Census:
    """A census in the state ingestion is supposed to end in."""
    c = ia.Census(
        studies_on_disk=98, v8_on_disk=93, flat_links=93,
        registry_rows=413, registry_present=98,
        cohort={"new": {"present": 93, "missing": 0}, "old": {"present": 5, "missing": 278}},
        # The real finished state of this machine: one operator self-scan sits on disk that belongs
        # to neither cohort and never can, so "finished" must tolerate it or never be reachable.
        unknown_present=1, unknown_placeable=0, unknown_exempt=["AKA9001"],
        freezelog_n=93, freezelog_r=2,
        # Since 2026-09-03 the freeze also keeps WHICH frames its numbers came from; a finished
        # ingest has that file beside the r<N> CSV and it lists exactly the served uids.
        landmark_ok=True, landmark_state="new_round_landmark_r2.json: 93 studies = the served cohort",
        substrate_rows=375, substrate_by_round={"new": 93, "old": 282},
        substrate_built="2026-07-31",
        # Partial by nature, not by defect: not every patient gets a dynamometer session, and some
        # studies are still awaiting an operator's recording verdict. A finished ingest looks like
        # THIS, not like full coverage — see the read-failure test below.
        substrate_new_rows=93,
        substrate_sheet_cover={"kontrol_verdict": 74, "izo_torque_nm": 78, "izo_cv_pct": 78,
                               "isok_torque_ext_nm": 78, "isok_mean_power_ext_w": 77,
                               "isok_cv_pct": 76},
        new_cohort_rows=93, new_cohort_frozen="2026-07-31",
        newest_study="2026-07-30",
        drive_zips=107, unlogged=0, filled_not_on_disk=0,
        legacy_128l=271, legacy_128lh=48,
        legacy_unknown_probe=0, set_aside_in_cohort=0,
    )
    for k, v in over.items():
        setattr(c, k, v)
    return c


def _fails(c: ia.Census) -> set[str]:
    return {name for ok, name, _ in ia.contract(c) if not ok}


def test_a_finished_ingest_satisfies_every_invariant() -> None:
    assert _fails(_complete()) == set()


def test_the_2026_07_30_state_is_reported_as_not_complete() -> None:
    """The real state after that day's ingest — four invariants must fire, not zero."""
    broken = _complete(
        unknown_present=28, unknown_placeable=27,   # 27 stranded v8 studies + the exempt self-scan
        substrate_by_round={"new": 66, "old": 282}, substrate_rows=348,
        substrate_built="2026-07-21",
        new_cohort_rows=66, new_cohort_frozen="2026-07-14", freezelog_n=66, freezelog_r=1,
        unlogged=8,
    )
    f = _fails(broken)
    assert "no study on disk is left cohort=unknown" in f
    assert "the substrate covers every living v8 study on disk" in f
    assert "the new-round cohort is not older than the newest study" in f
    assert "the substrate is not older than the newest study" in f
    assert "every Drive zip has a datasheet row" in f


_SHEET_ITEM = "the substrate carries what only the datasheet can supply"
_ONDISK_ITEM = "every ready datasheet row became a study on disk"


def test_a_ready_row_that_never_became_a_study_is_caught() -> None:
    """The real 2026-08-03 state: 118 ready datasheet rows, 116 v8 studies on disk. ingest held two
    (one zip exported with no RawImages, one whose shallow plane was recorded a device step deep at
    5.9 cm so no .dcm matched it), printed a HOLD line, wrote no durable record and exited 0. Every
    other invariant is disk-relative, so the contract read COMPLETE while two studies were lost."""
    held = _complete(filled_not_on_disk=2, filled_missing_rows=[96, 113])
    assert _fails(held) == {_ONDISK_ITEM}
    detail = next(d for _, n, d in ia.contract(held) if n == _ONDISK_ITEM)
    assert "96" in detail and "113" in detail, "name the rows, or nobody can act on it"


def test_the_missing_row_report_carries_no_patient_names() -> None:
    """Study folder names carry patient identifiers, so the contract reports ROW NUMBERS only."""
    detail = next(d for _, n, d in ia.contract(_complete(filled_not_on_disk=1,
                                                         filled_missing_rows=[96]))
                  if n == _ONDISK_ITEM)
    assert "_" not in detail.split("row(s)")[-1], "a study name would leak PHI into the console"


def test_the_on_disk_item_is_omitted_offline() -> None:
    """It needs the datasheet, so an offline run must not invent a verdict about it."""
    names = {n for _, n, _ in ia.contract(_complete(filled_not_on_disk=None))}
    assert _ONDISK_ITEM not in names


def test_the_2026_08_03_silent_force_loss_is_caught() -> None:
    """The real DGX state: the substrate was rebuilt while gws was expired, so the operator verdict
    and all five dynamometer columns arrived empty on all 116 studies — and every OTHER invariant
    passed, because they all ask about coverage and none asks whether the values are there."""
    broken = _complete(
        substrate_new_rows=116,
        substrate_sheet_cover={k: 0 for k in ia.SHEET_ONLY_COLUMNS},
    )
    assert _fails(broken) == {_SHEET_ITEM}, "it must fire, and nothing else must"


def test_a_partly_measured_cohort_still_passes() -> None:
    """The distinction the item exists to make. 96 of 116 had a dynamometer session and 20 did not —
    a fact about people, not a defect. Demanding full coverage would make the gate permanently red,
    and a gate that cannot go green is one people stop reading."""
    partial = _complete(
        substrate_new_rows=116,
        substrate_sheet_cover={"kontrol_verdict": 93, "izo_torque_nm": 96, "izo_cv_pct": 96,
                               "isok_torque_ext_nm": 96, "isok_mean_power_ext_w": 96,
                               "isok_cv_pct": 94},
    )
    assert _fails(partial) == set()


def test_one_surviving_datasheet_column_is_enough_to_clear_it() -> None:
    """A read failure is CATEGORICAL — it blanks every one of these at once. So any single column
    carrying a value proves the sheet was reached, which is all this item claims."""
    assert _SHEET_ITEM not in _fails(_complete(
        substrate_new_rows=116,
        substrate_sheet_cover={**{k: 0 for k in ia.SHEET_ONLY_COLUMNS}, "kontrol_verdict": 1}))


def test_the_datasheet_item_is_omitted_when_there_is_no_substrate() -> None:
    names = {n for _, n, _ in ia.contract(_complete(substrate_new_rows=0,
                                                    substrate_sheet_cover={}))}
    assert _SHEET_ITEM not in names


def test_an_unlogged_drive_zip_is_caught() -> None:
    """A zip with no row is invisible to check_studies AND ingest — both are row-gated — so no
    other count reveals it."""
    assert "every Drive zip has a datasheet row" in _fails(_complete(unlogged=1))
    assert "every Drive zip has a datasheet row" not in _fails(_complete(unlogged=0))


def test_a_study_on_disk_but_unregistered_is_caught() -> None:
    assert "every study on disk is registered" in _fails(_complete(registry_present=97))


def test_a_stale_substrate_is_caught_even_when_row_counts_look_right() -> None:
    """The subtle one: counts can agree while the file predates the newest study."""
    c = _complete(substrate_built="2026-07-01")
    f = _fails(c)
    assert "the substrate is not older than the newest study" in f
    assert "the substrate covers every living v8 study on disk" not in f


def test_offline_census_does_not_invent_a_drive_verdict() -> None:
    """With no Drive read, `unlogged` is None and the invariant is omitted rather than passed —
    an unknown must never be scored as a success."""
    c = _complete(unlogged=None, drive_zips=None)
    names = {name for _, name, _ in ia.contract(c)}
    assert "every Drive zip has a datasheet row" not in names


@pytest.mark.parametrize("stage", ["cohort", "audit"])
def test_stages_that_cannot_dry_run_are_declared_not_silently_skipped(stage) -> None:
    """derive_new_round_cohort has no dry-run and re-segments; audit always writes its report.
    Both must be visible as dry=None so the runner announces them instead of pretending."""
    st = next(s for s in ia.STAGES if s.key == stage)
    assert st.dry is None


def test_the_freeze_stage_is_gated() -> None:
    """Re-deriving the new-round cohort moves the placement reference the app serves."""
    st = next(s for s in ia.STAGES if s.key == "cohort")
    assert st.gate and "FREEZE" in st.gate


def test_sheet_writing_stages_are_gated() -> None:
    for key in ("sync", "check"):
        assert next(s for s in ia.STAGES if s.key == key).gate


# --- the probe invariant ------------------------------------------------------------------
# The legacy corpus is two probes and 128LH is set aside (texture confound on the axis carrying 43%
# of the score). This is a CONTRACT item, not a report, because the fact was worked out on
# 2026-05-29 and then lost for two months inside a frozen archive nobody reads.

def test_a_wrong_probe_study_in_the_active_cohort_fails_the_contract() -> None:
    c = _complete(set_aside_in_cohort=11, legacy_unknown_probe=0)
    assert "no set-aside (128LH) study is in the active cohort" in _fails(c)


def test_a_clean_cohort_passes_the_probe_invariant() -> None:
    c = _complete(set_aside_in_cohort=0, legacy_unknown_probe=0)
    assert "no set-aside (128LH) study is in the active cohort" not in _fails(c)


def test_an_undetermined_probe_fails_too() -> None:
    """A study with no probe assignment is an open question, never a silent pass."""
    assert "every legacy study has a determined probe" in _fails(
        _complete(set_aside_in_cohort=0, legacy_unknown_probe=19))


def test_probe_invariants_are_omitted_when_the_classification_is_absent() -> None:
    """No classification file => the questions are not asked, rather than answered 'fine'."""
    names = {n for _, n, _ in ia.contract(_complete(set_aside_in_cohort=None,
                                                    legacy_unknown_probe=None))}
    assert "no set-aside (128LH) study is in the active cohort" not in names
    assert "every legacy study has a determined probe" not in names


# --- placeable vs exempt ------------------------------------------------------------------
# A study that is neither v8 nor legacy-coded can never join a cohort, so counting it as an
# incomplete ingestion made the contract permanently red — it had never once read COMPLETE on the
# Acer. A gate that cannot go green is a gate people skim, which is how the 07-30 gap survived a
# run where every stage reported success. Exempt the unplaceable; never exempt the placeable.

def test_an_unplaceable_self_scan_does_not_block_completion() -> None:
    assert _fails(_complete(unknown_present=1, unknown_placeable=0,
                            unknown_exempt=["AKA9001"])) == set()


def test_a_ledger_killed_study_does_not_block_completion() -> None:
    """The r5 lesson: the freeze drops a killed twin, the registry rebind resets its cohort to
    unknown, and the contract must read that as a decided exemption, not a stranded study."""
    assert _fails(_complete(unknown_present=1, unknown_placeable=0,
                            unknown_exempt=["AKA158 (ledger-killed)"])) == set()


def test_a_placeable_study_left_uncohorted_still_fails() -> None:
    """The case the invariant exists for: a v8 study that never reached the cohort."""
    assert "no study on disk is left cohort=unknown" in _fails(
        _complete(unknown_present=1, unknown_placeable=1, unknown_exempt=[]))


def test_an_exemption_is_named_not_silently_dropped(capsys) -> None:
    """An exempt study must stay visible, or the exemption becomes a place to hide a real gap."""
    ia.print_contract(_complete(unknown_present=1, unknown_placeable=0,
                                unknown_exempt=["AKA9001"]))
    out = capsys.readouterr().out
    assert "AKA9001" in out and "exempt" in out


def test_exemption_does_not_mask_a_simultaneous_real_failure() -> None:
    c = _complete(unknown_present=28, unknown_placeable=27, unknown_exempt=["AKA9001"])
    assert "no study on disk is left cohort=unknown" in _fails(c)


# --- the freeze log -----------------------------------------------------------------------
# A re-freeze moves the placement reference every patient is scored against. On the 66->93 freeze,
# int_cv_within percentiles moved a median 6.67 points on the axis carrying 43% of the score. That
# is only visible if every freeze is recorded, so recording it is a CONTRACT item, not a habit.

def test_a_freeze_the_log_does_not_describe_fails() -> None:
    """The reference was re-frozen and the log still describes the previous one."""
    assert "the freeze log describes the cohort being served" in _fails(
        _complete(new_cohort_rows=116, freezelog_n=93, freezelog_r=2))


def test_no_freeze_log_at_all_fails_rather_than_passing_by_absence() -> None:
    """An unrecorded reference must not score as a success — the same rule the Drive invariant
    follows, except here absence is a real failure rather than an unknown: the cohort file exists."""
    assert "the freeze log describes the cohort being served" in _fails(_complete(freezelog_n=None))


def test_a_recorded_freeze_passes() -> None:
    assert "the freeze log describes the cohort being served" not in _fails(
        _complete(new_cohort_rows=116, freezelog_n=116, freezelog_r=3))


def test_a_freeze_without_its_frame_evidence_fails() -> None:
    """The freezelog stage says "landmark snapshot -> ..." in one line of a nine-stage run; the
    contract asks for the file explicitly, so a freeze whose evidence never landed (or lists other
    uids) cannot read COMPLETE — the doctors' grading pack shows raters the frames THIS file names."""
    item = "the freeze's frame evidence is on disk and describes the served cohort"
    assert item in _fails(_complete(landmark_ok=False,
                                    landmark_state="no new_round_landmark_r2.json"))
    assert item not in _fails(_complete())


def test_the_freezelog_stage_runs_before_the_substrate() -> None:
    """Order matters: the log must capture the reference the substrate is then built against."""
    keys = [s.key for s in ia.STAGES]
    assert keys.index("cohort") < keys.index("freezelog") < keys.index("substrate")


def test_registry_runs_after_the_cohort_freeze() -> None:
    """study_registry.py derives each study's `cohort` column from MEMBERSHIP in
    per_study_cohort_new.csv. Run it before the freeze and it assigns against the PREVIOUS reference,
    so every study the freeze just added is stamped `unknown` — placeable, therefore failing the
    contract, therefore un-COMPLETE-able in one pass. That happened on 2026-07-30 (28 unknown) and
    again on 08-03 (23); both times a human had to notice and re-run the stage by hand."""
    keys = [s.key for s in ia.STAGES]
    assert keys.index("cohort") < keys.index("registry"), (
        "registry must follow cohort — it binds membership the freeze has just changed")


def test_substrate_runs_after_the_registry() -> None:
    """build_qa_sheet.py reads study_registry.csv, so the substrate can only be as current as the
    registry it is built from. This is the other real edge in the graph; everything else is free."""
    keys = [s.key for s in ia.STAGES]
    assert keys.index("registry") < keys.index("substrate")


def test_nothing_before_the_cohort_stage_depends_on_the_registry() -> None:
    """The licence for moving it. sync/check/audit hold zero references to study_registry, and
    derive_new_round_cohort's only `registry` import is model_registry (the segmenter bundle). If a
    future stage before `cohort` starts reading the registry, this ordering becomes unsafe and this
    test is where that shows up."""
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    keys = [s.key for s in ia.STAGES]
    before_cohort = {s.key for s in ia.STAGES[:keys.index("cohort")]}
    sources = {"sync": "sync_records.py", "check": "check_studies.py", "ingest": "ingest.py",
               "audit": "audit_capture_consistency.py"}
    for key, fname in sources.items():
        if key not in before_cohort:
            continue
        text = (repo / "scripts" / fname).read_text(encoding="utf-8")
        assert "study_registry" not in text, (
            f"{fname} now reads the registry, but its stage runs BEFORE cohort/registry")


def test_the_freezelog_stage_is_not_gated() -> None:
    """It only appends a log row and an archive copy — nothing a human needs to approve, and a gate
    would let the one recording step be the one people decline."""
    # falsy, not `is None` — the dataclass defaults gate to "" and the runner tests `if st.gate`
    assert not next(s for s in ia.STAGES if s.key == "freezelog").gate


# --- the handover receipt -----------------------------------------------------------------
# `check_parity.py` answers "do two machines match?" against parity_baseline.json — and nothing in
# the repo regenerates that file. Between 2026-07-08 and 08-02 it sat 27 studies behind while
# check_parity reported MATCH throughout, because stale data and a stale baseline agree with each
# other. Reported here, never gated: a stale receipt does not make data unusable.

def test_a_stale_parity_baseline_is_reported(capsys) -> None:
    ia.print_handover(_complete(parity_studies=71))          # baseline 71 vs 98 on disk
    out = capsys.readouterr().out
    assert "STALE" in out and "71" in out and "98" in out
    assert "--save-baseline" in out


def test_a_machine_behind_the_baseline_is_told_to_ingest_not_to_save(capsys) -> None:
    """The opposite direction, and the dangerous one: --save-baseline on a machine that is BEHIND
    would overwrite the golden state with the poorer one. Caught by rehearsing a DGX arrival."""
    ia.print_handover(_complete(studies_on_disk=71, parity_studies=98))
    out = capsys.readouterr().out
    assert "BEHIND" in out
    assert "ingest_all.py --apply" in out
    assert "Do NOT run --save-baseline" in out


def test_a_machine_behind_is_warned_the_contract_cannot_see_it(capsys) -> None:
    """The contract is disk-relative, so it can read COMPLETE on an under-populated machine. The
    handover block is the only thing that says so — it must say it out loud."""
    ia.print_handover(_complete(studies_on_disk=71, parity_studies=98))
    assert "DISK-RELATIVE" in capsys.readouterr().out


def test_a_current_parity_baseline_is_reported_without_the_fix_instruction(capsys) -> None:
    ia.print_handover(_complete(parity_studies=98))
    out = capsys.readouterr().out
    assert "CURRENT" in out
    assert "--save-baseline" not in out


def test_an_absent_baseline_says_so_rather_than_claiming_parity(capsys) -> None:
    ia.print_handover(_complete(parity_studies=None))
    out = capsys.readouterr().out
    assert "ABSENT" in out
    assert "CURRENT" not in out


def test_the_handover_receipt_is_never_a_contract_item() -> None:
    """Staleness must not gate the exit code: the baseline is only safe to save once the contract
    already passes, so gating on it would deadlock."""
    assert _fails(_complete(parity_studies=71)) == set()
    names = {n for _, n, _ in ia.contract(_complete(parity_studies=71))}
    assert not any("parity" in n or "baseline" in n for n in names)

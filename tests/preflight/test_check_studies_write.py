"""The `run_once` write predicate — the one thing that decides whether an operator ever
sees a verdict.

This had zero coverage, and that is how a regression shipped: commit 0e9779e made
`--force --apply` surgical with `changed = bool(cur) and cell.strip() != cur`, which is
False for a BLANK cell — so the watcher could refresh an existing verdict but never write
a first one, i.e. it could not do the job it exists for. Nothing surfaced it because no new
data arrived for a week and the watcher was never installed.

Two properties must hold together, and they are what these tests pin:
  * a blank cell gets the first verdict written (the watcher's primary job), and
  * a cell that already agrees is left alone (the surgical property 0e9779e wanted).

All Sheets I/O is faked: no network, no gws, no real sheet.
"""
from __future__ import annotations

import pytest

import check_studies as cs


CFG = {"sheets": {"hasta_kayit": "SHEET_ID"}}
HEADER = ["Study Adı", "Operator", cs.RESULT_COL, cs.DETAIL_COL]


def _row(n, study="07jun06_093_x_y_49e_179_82", operator="anonacl", verdict=""):
    return {"_row": n, cs.KEY_COL: study, "Operator": operator,
            cs.RESULT_COL: verdict, cs.DETAIL_COL: ""}


@pytest.fixture
def harness(monkeypatch):
    """Patch out sheet reads/writes/backup; return the recorded write list."""
    writes: list[tuple[str, str]] = []

    def fake_update(sid, rng, values):
        writes.append((rng, values[0][0]))
        return {}

    monkeypatch.setattr(cs.io, "sheets_values_update", fake_update)
    monkeypatch.setattr(cs, "_backup_sheet", lambda *a, **k: None)

    def install(rows, cell, detail="depth detail"):
        monkeypatch.setattr(cs, "read_sheet", lambda cfg: ("kayit", HEADER, rows))
        monkeypatch.setattr(cs, "verdict_for", lambda cfg, row, **kw: (cell, detail))
    return install, writes


def test_apply_writes_a_first_verdict_into_a_blank_cell(harness):
    """THE regression. A filled row with an empty verdict is the watcher's whole purpose."""
    install, writes = harness
    rows = [_row(7, verdict="")]
    install(rows, "✅ femur1 rf1 sweep1 · tam")

    cs.run_once(CFG, apply=True)

    written = [v for _, v in writes]
    assert "✅ femur1 rf1 sweep1 · tam" in written, (
        "a blank verdict cell must receive the computed verdict")
    assert any(rng.startswith("kayit!C7") for rng, _ in writes)   # RESULT_COL == C
    assert any(rng.startswith("kayit!D7") for rng, _ in writes)   # DETAIL_COL == D


def test_apply_leaves_a_row_whose_verdict_already_agrees(harness):
    """The surgical property 0e9779e wanted: an already-correct row is never rewritten —
    including a manual edit the classifier agrees with."""
    install, writes = harness
    same = "✅ femur1 rf1 sweep1 · tam"
    install([_row(7, verdict=same)], same)

    cs.run_once(CFG, apply=True, force=True)

    assert writes == [], f"an unchanged verdict must not be rewritten, got {writes}"


def test_force_apply_rewrites_a_stale_verdict(harness):
    """The refresh case: an older classifier's wrong verdict is corrected."""
    install, writes = harness
    install([_row(7, verdict="❌ femur0 rf2 sweep1 · femur (6.9cm) eksik")],
            "✅ femur1 rf1 sweep1 · tam")

    cs.run_once(CFG, apply=True, force=True)

    assert [v for _, v in writes][0] == "✅ femur1 rf1 sweep1 · tam"


def test_apply_never_writes_the_dry_run_marker(harness):
    """--no-download yields a '⏳ (dry...)' placeholder. It differs from a blank cell, so the
    predicate alone would write it — the marker guard must still suppress it."""
    install, writes = harness
    install([_row(7, verdict="")], f"{cs.PENDING_PREFIX} (dry, indirme yok)")

    cs.run_once(CFG, apply=True, do_download=False)

    assert writes == [], f"the dry-run placeholder must never reach the sheet, got {writes}"


def test_dry_run_writes_nothing_even_when_the_verdict_changed(harness):
    """Without --apply nothing is written, however different the verdict is."""
    install, writes = harness
    install([_row(7, verdict="")], "❌ femur0 rf2 sweep1 · femur (6.9cm) eksik")

    cs.run_once(CFG, apply=False)

    assert writes == []


def test_pending_selection_covers_blank_and_the_pending_marker(harness):
    """A row is 'pending' when its verdict is empty OR still a ⏳ marker; a row with a real
    verdict is only revisited under --force."""
    install, writes = harness
    rows = [_row(2, verdict=""),
            _row(3, verdict=f"{cs.PENDING_PREFIX} kuyrukta"),
            _row(4, verdict="✅ femur1 rf1 sweep1 · tam")]
    install(rows, "✅ femur1 rf1 sweep1 · tam")

    assert cs.run_once(CFG, apply=False) == 2          # rows 2 and 3
    assert cs.run_once(CFG, apply=False, force=True) == 3


def test_a_row_without_study_or_operator_is_not_pending():
    """The gate is structural: no study name or no operator means nothing to check."""
    assert cs.is_pending(_row(2)) is True
    assert cs.is_pending(_row(2, study="")) is False
    assert cs.is_pending(_row(2, operator="")) is False

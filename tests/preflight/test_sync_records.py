"""Orchestration tests for the intake CLI (scripts/sync_records.py).

The leaf helpers (filename parse, artifact count, schema) have their own tests; this covers
the orchestration that lives ONLY in sync_records and was previously untested: ledger dedup
(a re-run skips an already-processed study), fill-missing upsert (an operator's entry is
never clobbered), the artifacts-string write, and locale_sep (';' vs ',' — a wrong pick
corrupts every in-sheet ARRAYFORMULA). All Drive/Sheets I/O is faked in-memory: no network,
no gws, no real sheet.
"""
from __future__ import annotations

import re
import zipfile
from pathlib import Path

import pytest

import sync_records as sr
from preflight_helpers import datasheet_schema as ds

REF = "07jun06_093_anonabr_anonaab_49e_179_82"


# ---------- in-memory fakes for gws_io ----------
def _col0(letters: str) -> int:
    c = 0
    for ch in letters:
        c = c * 26 + (ord(ch) - ord("A") + 1)
    return c - 1


def _parse_a1(rng: str):
    cell = rng.split("!", 1)[1].split(":")[0]
    m = re.match(r"([A-Z]+)(\d+)", cell)
    return int(m.group(2)) - 1, _col0(m.group(1))


class _FakeBackend:
    """A tiny in-memory stand-in for preflight_helpers.gws_io (sheets + drive)."""

    def __init__(self, locale: str = "en_US"):
        self.locale = locale
        self.grids: dict = {}     # spreadsheet_id -> list[list[str]]
        self.titles: dict = {}    # spreadsheet_id -> first-sheet title
        self.folders: dict = {}   # parent_id -> [ {name,id} ]
        self.zips: dict = {}      # parent_id -> [ {name,id,size} ]
        self.zip_bytes: bytes = b""

    # --- sheets ---
    def sheets_meta(self, sid):
        return {"properties": {"locale": self.locale},
                "sheets": [{"properties": {"sheetId": 0, "title": self.titles[sid],
                                           "gridProperties": {}}}]}

    def sheets_values_get(self, sid, rng):
        return [list(r) for r in self.grids[sid]]

    def sheets_values_update(self, sid, rng, values):
        grid = self.grids[sid]
        r0, c0 = _parse_a1(rng)
        for i, row in enumerate(values):
            rr = r0 + i
            while len(grid) <= rr:
                grid.append([])
            for j, v in enumerate(row):
                cc = c0 + j
                while len(grid[rr]) <= cc:
                    grid[rr].append("")
                grid[rr][cc] = v
        return {}

    def sheets_batch_update(self, sid, requests):
        return {}

    def sheets_protected_range_ids(self, sid, sheet_id):
        return []

    # --- drive ---
    def drive_list_children(self, parent, *, folders_only=False, zips_only=False):
        if folders_only:
            return list(self.folders.get(parent, []))
        if zips_only:
            return list(self.zips.get(parent, []))
        return []

    def drive_download(self, file_id, rel):
        dest = sr.REPO_ROOT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.zip_bytes)
        return str(dest)


def _make_zip_bytes(tmp_path: Path) -> bytes:
    """A minimal study zip: one capture's worth of mp4 / RawImages / dcm -> counts '1/1/1'."""
    zp = tmp_path / "sample.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("study/cap1.mp4", b"x")
        zf.writestr("study/RawImagesForResearch_1/000000000_000000000.jpg", b"x")
        zf.writestr("study/cap1.dcm", b"x")
    return zp.read_bytes()


def _patch(monkeypatch, be: _FakeBackend):
    for fn in ("sheets_meta", "sheets_values_get", "sheets_values_update",
               "sheets_batch_update", "sheets_protected_range_ids",
               "drive_list_children", "drive_download"):
        monkeypatch.setattr(sr.io, fn, getattr(be, fn))


def _cfg(tmp_path: Path) -> dict:
    return {
        "sheets": {"hasta_kayit": "HK", "records_list": "RL"},
        "drive": {"goruntuler_folder": "GORUNTULER"},
        "operators": ["anonacl"],
        "owner_email": "x@y.z",
        "name_order": "soyad_ad",
        "cache_dir": str(tmp_path / "cache"),
    }


def _fresh_backend(tmp_path, *, hk_rows=(), rl_rows=(), zips=()) -> _FakeBackend:
    be = _FakeBackend()
    be.titles = {"HK": "kayit", "RL": "Sheet1"}
    be.grids = {
        "HK": [list(ds.HEADERS)] + [list(r) for r in hk_rows],
        "RL": [list(sr.RECORDS_LIST_HEADERS)] + [list(r) for r in rl_rows],
    }
    be.folders = {"GORUNTULER": [{"name": "anonacl", "id": "OP_ANONACL"}]}
    be.zips = {"OP_ANONACL": list(zips)}
    be.zip_bytes = _make_zip_bytes(tmp_path)
    return be


def _hk_cell(be: _FakeBackend, study: str, header: str):
    grid = be.grids["HK"]
    hdr = grid[0]
    kcol, ccol = hdr.index(ds.KEY_COLUMN), hdr.index(header)
    for row in grid[1:]:
        if len(row) > kcol and row[kcol] == study:
            return row[ccol] if len(row) > ccol else ""
    return None


def test_new_zip_creates_one_row_with_artifacts(tmp_path, monkeypatch):
    be = _fresh_backend(tmp_path, zips=[{"name": REF + ".zip", "id": "Z1", "size": "10"}])
    _patch(monkeypatch, be)
    n = sr.sync(_cfg(tmp_path), apply=True)
    assert n == 1
    assert _hk_cell(be, REF, "Study Adı") == REF
    assert _hk_cell(be, REF, "Operator") == "anonacl"
    assert _hk_cell(be, REF, "Artifacts (mp4/RawImages/dcm)") == "1/1/1"
    # demographics are in-sheet formula columns — the CLI must NOT write them
    assert _hk_cell(be, REF, "Yaş") in ("", None)
    # the ledger registered the study
    assert any(row and row[0] == REF for row in be.grids["RL"][1:])


def test_rerun_skips_already_processed(tmp_path, monkeypatch):
    # REF already in the records_list ledger -> a second pass must skip it (no double-write)
    rl_row = [REF, "anonacl", "Z1", "10", "1/1/1", "2026-06-08T00:00:00", "Evet"]
    be = _fresh_backend(tmp_path, rl_rows=[rl_row],
                        zips=[{"name": REF + ".zip", "id": "Z1", "size": "10"}])
    _patch(monkeypatch, be)
    assert sr.sync(_cfg(tmp_path), apply=True) == 0


def test_fill_missing_never_clobbers_operator_entry(tmp_path, monkeypatch):
    # an existing HK row whose Dominant Bacak the operator set to SOL; the zip name has no leg
    # suffix (so the built value would be the 'Seçiniz' default) -> the operator's SOL survives,
    # while a genuinely blank cell (Operator) still gets filled.
    study = "12jun06_anonace_anonaci_40e_180_90"
    hk_row = [""] * len(ds.HEADERS)
    hk_row[ds.col_index(ds.KEY_COLUMN)] = study
    hk_row[ds.col_index("Dominant Bacak")] = "SOL"
    be = _fresh_backend(tmp_path, hk_rows=[hk_row],
                        zips=[{"name": study + ".zip", "id": "Z2", "size": "10"}])
    _patch(monkeypatch, be)
    sr.sync(_cfg(tmp_path), apply=True)
    assert _hk_cell(be, study, "Dominant Bacak") == "SOL"               # preserved, not 'Seçiniz'
    assert _hk_cell(be, study, "Operator") == "anonacl"                   # blank cell filled
    assert _hk_cell(be, study, "Artifacts (mp4/RawImages/dcm)") == "1/1/1"


def test_double_underscore_filename_updates_not_duplicates(tmp_path, monkeypatch):
    # A Drive zip whose filename carries the stray '__' typo must resolve to the EXISTING
    # single-'_' datasheet row (UPDATE), never a duplicate NEW row — the SAME normalized key
    # ingest + check_studies resolve by. Regression: sync_records used to exact-match the raw
    # name, so 'Anonaco__35e' looked new next to the sheet's 'Anonaco_35e' and duplicated it.
    existing = "07jun06_093_anonabr_anonaab_49e_179_82"                 # clean, on the sheet
    hk_row = [""] * len(ds.HEADERS)
    hk_row[ds.col_index(ds.KEY_COLUMN)] = existing
    typo_zip = "07jun06_093_anonabr_anonaab__49e_179_82.zip"           # '__' typo, in Drive
    be = _fresh_backend(tmp_path, hk_rows=[hk_row],
                        zips=[{"name": typo_zip, "id": "Z9", "size": "10"}])
    _patch(monkeypatch, be)
    n = sr.sync(_cfg(tmp_path), apply=True)
    assert n == 1                                                        # one plan — an UPDATE
    assert len(be.grids["HK"]) == 2                                      # header + the one row; NO duplicate appended
    assert _hk_cell(be, existing, "Study Adı") == existing              # clean name untouched (fill-missing)
    assert _hk_cell(be, existing, "Operator") == "anonacl"                # blank cell on that same row filled


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    be = _fresh_backend(tmp_path, zips=[{"name": REF + ".zip", "id": "Z1", "size": "10"}])
    _patch(monkeypatch, be)
    n = sr.sync(_cfg(tmp_path), apply=False)
    assert n == 1                                   # it PLANS the study
    assert len(be.grids["HK"]) == 1                 # but writes no HK row (header only)
    assert len(be.grids["RL"]) == 1                 # and no ledger row


@pytest.mark.parametrize("locale,sep", [("tr_TR", ";"), ("en_US", ","), ("", ",")])
def test_locale_sep(locale, sep):
    assert sr.locale_sep({"properties": {"locale": locale}}) == sep


def test_protection_requests_hard_lock_plus_anchor_warning():
    # Two guards per auto-column range: an owner-only hard-lock over the whole column, and a
    # warning-only seatbelt over JUST the row-2 anchor (the one the owner — exempt from the
    # hard-lock — can otherwise clear silently, blanking the spilled column).
    reqs = sr._protection_requests(sheet_id=7, owner_email="x@y.z", existing_ids=[11, 12])
    assert {r["deleteProtectedRange"]["protectedRangeId"]
            for r in reqs if "deleteProtectedRange" in r} == {11, 12}
    adds = [r["addProtectedRange"]["protectedRange"] for r in reqs if "addProtectedRange" in r]
    hard = [p for p in adds if not p.get("warningOnly")]
    warn = [p for p in adds if p.get("warningOnly")]
    n = len(ds.auto_column_ranges())
    assert len(hard) == n and len(warn) == n
    for p in hard:                                   # owner-only, full data height
        assert p["editors"]["users"] == ["x@y.z"]
        assert (p["range"]["startRowIndex"], p["range"]["endRowIndex"]) == (0, 1000)
    for p in warn:                                   # row 2 only, prompts EVERYONE (no editors)
        assert (p["range"]["startRowIndex"], p["range"]["endRowIndex"]) == (1, 2)
        assert "editors" not in p
    cols = lambda ps: sorted((p["range"]["startColumnIndex"], p["range"]["endColumnIndex"])
                             for p in ps)
    assert cols(hard) == cols(warn) == sorted(ds.auto_column_ranges())


def test_protection_requests_noop_without_owner():
    # No owner_email -> emit NOTHING (deleting without re-adding would strip protection entirely).
    assert sr._protection_requests(sheet_id=7, owner_email="", existing_ids=[11]) == []


# ---------------------------------------------------------------------------
# resolve_operators — the Operator dropdown is DERIVED, not a hard-coded whitelist.
#
# `anonaav` shipped 13 studies while missing from configs/round3_intake.json: the scan still
# found the zips, but the operator got no dropdown entry and the run emitted only a warning.
# Meanwhile two EMPTY Drive subfolders exist whose names match patient surnames, not staff —
# admitting those would pollute the dropdown. So: a folder with studies self-registers, an
# empty one is ignored, and the config list is a seed that is never dropped.
# ---------------------------------------------------------------------------
def _ops_backend(tmp_path, folders: dict) -> _FakeBackend:
    """folders: {name: n_zips}."""
    be = _fresh_backend(tmp_path)
    be.folders = {"GORUNTULER": [{"name": n, "id": f"OP_{n}"} for n in folders]}
    be.zips = {f"OP_{n}": [{"name": f"{REF}_{i}.zip", "id": f"Z{i}", "size": "10"}
                           for i in range(k)] for n, k in folders.items()}
    return be


def test_a_drive_folder_with_studies_self_registers_as_an_operator(tmp_path, monkeypatch):
    be = _ops_backend(tmp_path, {"anonacl": 1, "anonaav": 13})
    _patch(monkeypatch, be)
    ops, notes = sr.resolve_operators(_cfg(tmp_path))          # config seed = ["anonacl"]
    assert "anonaav" in ops, "an operator shipping studies must reach the dropdown"
    assert any("anonaav" in n and "self-registered" in n for n in notes)


def test_an_empty_drive_folder_IS_an_operator(tmp_path, monkeypatch):
    """Akadir 2026-07-30: goruntuler/<operator>/ is where each operator deposits studies, so the
    folder itself is the roster entry — `anonacr` and `anonade` are operators who simply have not
    uploaded yet. A has-studies test would silently omit them from the dropdown."""
    be = _ops_backend(tmp_path, {"anonacl": 1, "anonacr": 0, "anonade": 0})
    _patch(monkeypatch, be)
    ops, notes = sr.resolve_operators(_cfg(tmp_path))
    assert ops == ["anonacl", "anonacr", "anonade"]
    assert any("anonacr" in n and "self-registered" in n for n in notes)


def test_a_seed_name_with_no_folder_and_no_rows_is_dropped(tmp_path, monkeypatch):
    """Drive is the roster. `anonaad`/`anonaby` sat in the config with no folder and no rows, and
    the live sheet's own dropdown already excluded them — honouring the seed would re-add two
    departed operators."""
    be = _ops_backend(tmp_path, {"anonaav": 2})
    _patch(monkeypatch, be)
    ops, notes = sr.resolve_operators(_cfg(tmp_path))          # seed anonacl has no folder, no rows
    assert ops == ["anonaav"]
    assert any("anonacl" in n and "no rows" in n for n in notes)


def test_an_operator_used_by_existing_rows_is_kept_even_with_no_folder(tmp_path, monkeypatch):
    """The safety catch: a row already on the sheet must never be invalidated."""
    be = _ops_backend(tmp_path, {"anonaav": 2})
    # a historical row whose operator has no Drive folder any more
    hdr = be.grids["HK"][0]
    row = [""] * len(hdr)
    row[hdr.index(ds.KEY_COLUMN)] = REF
    row[hdr.index("Operator")] = "departed_person"
    be.grids["HK"].append(row)
    _patch(monkeypatch, be)
    ops, notes = sr.resolve_operators(_cfg(tmp_path))
    assert "departed_person" in ops
    assert any("departed_person" in n and "existing rows use it" in n for n in notes)


def test_drive_failure_falls_back_to_the_config_seed(tmp_path, monkeypatch):
    """A structure push must not die because Drive is unreachable."""
    be = _ops_backend(tmp_path, {"anonacl": 1})
    _patch(monkeypatch, be)

    def boom(*a, **k):
        raise RuntimeError("drive down")
    monkeypatch.setattr(sr.io, "drive_list_children", boom)
    ops, notes = sr.resolve_operators(_cfg(tmp_path))
    assert ops == ["anonacl"]
    assert any("unreachable" in n for n in notes)

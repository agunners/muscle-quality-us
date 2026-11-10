"""Layout tests for the operator tablet view (scripts/sync_records.py column-group fold).

The whole non-key middle — Hasta Adı … Dosyalar Tam mı, i.e. the auto-derived demographics AND the
judgment + acquisition block — is collapsed into ONE column group on --sync-structure, so the tablet
defaults to the minimal Study Adı + Kontrol + force and the operator expands the block to fill it. It
is ONE group (not a demographics + judgment pair) because Google Sheets merges two adjacent same-depth
column groups into one — separate adjacent toggles need a column reorder. These assert the fold spans
that block (never Study Adı, Kontrol, or the force columns) and that the request set is add + collapse,
delete-first so a re-sync replaces rather than stacks. Pure request-builder: no network/gws.
"""
from __future__ import annotations

import sync_records as sr
from preflight_helpers import datasheet_schema as ds


def _fold_indices() -> set:
    """All column indices covered by COLLAPSE_FOLDS (inclusive)."""
    idx: set = set()
    for s_h, e_h in sr.COLLAPSE_FOLDS:
        idx |= set(range(ds.col_index(s_h), ds.col_index(e_h) + 1))
    return idx


def test_fold_covers_the_whole_middle_block():
    folds = _fold_indices()
    # the fold spans Hasta Adı … Dosyalar Tam mı — the demographics AND the judgment/acquisition block
    assert ds.col_index("Hasta Adı") in folds and ds.col_index("Dosyalar Tam mı") in folds
    assert ds.col_index("Yaralanma/Cerrahi (alt ekstremite, son 6 ay)") in folds
    # the demographics sub-span is all auto (formula) columns the operator never types
    for i in range(ds.col_index("Hasta Adı"), ds.col_index("ASIS-Patella Sağ (cm)") + 1):
        assert ds.HEADERS[i] in ds.AUTO_HEADERS
    # Study Adı (the key) + the Kontrol verdict stay OUTSIDE the fold — always visible on the tablet
    for h in ("Study Adı", "Kontrol Sonucu", "Kontrol Detayı"):
        assert ds.col_index(h) not in folds


def test_column_group_requests_add_and_collapse_per_fold():
    reqs = sr._column_group_requests(sheet_id=7, existing=[])
    adds = [r["addDimensionGroup"]["range"] for r in reqs if "addDimensionGroup" in r]
    ups = [r["updateDimensionGroup"] for r in reqs if "updateDimensionGroup" in r]
    assert len(adds) == len(sr.COLLAPSE_FOLDS) and len(ups) == len(sr.COLLAPSE_FOLDS)
    expected = {(ds.col_index(s), ds.col_index(e) + 1) for s, e in sr.COLLAPSE_FOLDS}  # half-open
    assert {(a["startIndex"], a["endIndex"]) for a in adds} == expected
    assert all(a["sheetId"] == 7 and a["dimension"] == "COLUMNS" for a in adds)
    assert all(u["dimensionGroup"]["collapsed"] is True and u["fields"] == "collapsed" for u in ups)


def test_column_group_requests_is_delete_first_when_a_group_exists():
    existing = [{"startIndex": 3, "endIndex": 13}]            # a prior fold from sheets_column_groups()
    reqs = sr._column_group_requests(sheet_id=7, existing=existing)
    kinds = [next(iter(r)) for r in reqs]
    # the existing group is deleted BEFORE any add — a re-sync replaces it (never stacks/deepens)
    assert kinds[0] == "deleteDimensionGroup"
    assert "addDimensionGroup" in kinds and "deleteDimensionGroup" not in kinds[kinds.index("addDimensionGroup"):]
    dele = reqs[0]["deleteDimensionGroup"]["range"]
    assert (dele["startIndex"], dele["endIndex"]) == (3, 13)

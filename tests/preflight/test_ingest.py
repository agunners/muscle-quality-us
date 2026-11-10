"""Tests for the gated ingest spine (scripts/ingest.py) — the pure gate + the flat view.
The pull/materialize path is integration (Drive + a real zip) and is exercised separately."""
import shutil

import ingest


def _row(study="14jun06_071_829_anonace_anonacn_53e_173_88_47r", operator="anonaad", ready="Evet"):
    return {"Study Adı": study, "Operator": operator, "Dosyalar Tam mı": ready}


def test_is_filled_gate():
    assert ingest.is_filled(_row()) is True
    assert ingest.is_filled(_row(ready="evet")) is True            # case-insensitive ready signal
    assert ingest.is_filled(_row(ready="Seçiniz")) is False         # the default dropdown = not ready
    assert ingest.is_filled(_row(ready="Hayır")) is False           # files NOT complete -> not ready
    assert ingest.is_filled(_row(ready="")) is False                # blank -> not ready
    assert ingest.is_filled(_row(operator="")) is False             # no operator -> can't pull/route
    assert ingest.is_filled(_row(study="")) is False                # no study name -> no key


def _materialized(root, op, sess, st):
    d = root / op / sess / st
    d.mkdir(parents=True)
    (d / "materialize_manifest.json").write_text("{}", encoding="utf-8")
    return d


def test_rebuild_flat_view_links_and_is_idempotent(tmp_path):
    sr, flat = tmp_path / "studies_root", tmp_path / "flat"
    anonace = _materialized(sr, "anonaad", "14jun06_071", "anonace")
    _materialized(sr, "anonabx", "12jun06_001", "anonaam")

    n = ingest.rebuild_flat_view(sr, flat)
    assert n == 2
    assert sorted(p.name for p in flat.iterdir()) == ["anonaam", "anonace"]
    assert (flat / "anonace").is_symlink() and (flat / "anonace").resolve() == anonace.resolve()

    # a study is removed from studies_root -> a second pass clears its stale symlink, no dup, no error
    shutil.rmtree(sr / "anonaad")
    n2 = ingest.rebuild_flat_view(sr, flat)
    assert n2 == 1
    assert sorted(p.name for p in flat.iterdir()) == ["anonaam"]

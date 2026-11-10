"""The freeze keeps WHICH frames its numbers came from, and each freeze keeps a durable, name-free
copy of that evidence (2026-09-03; grading-round audit risk #1: "no persisted frame identity")."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import cohort_freeze_log as cfl
import derive_new_round_cohort as dnc


# ----------------------------------------------------------------------------- fakes
class _FakeSeg:
    """Stands in for derive_new_round_cohort._Segmenter: RF area per frame is scripted, geometry
    returns one distinguishable value per frame, so the ranking and the alignment are checkable."""

    def __init__(self, areas: dict[str, int]):
        self.areas = areas
        self.learn = object()
        self.meta = SimpleNamespace(modalities=None)
        self.label_rf = 1
        self.pkl_path = Path("fake_bundle.pkl")

    def spacing_row(self, _dir, _x):
        return {"sy": 0.1}

    def synth(self, _dir, fb):
        return fb                                     # the "image" is just its frame base

    def stack(self, final, modalities=None):
        return final

    def predict_mask(self, _learn, stacked, _meta):
        m = np.zeros((4, 4), dtype=np.uint8)
        m.flat[: self.areas[stacked]] = 1              # RF area = scripted pixel count
        return m

    def geometry(self, mask, final, spacing):
        return {"rf_thickness_mm": float(self.areas[final]), "qmt_provenance": "standard"}


def _study(root: Path, name: str, bases: list[str]) -> Path:
    sp = root / name
    raw = sp / "captures" / "c1_femur_deep" / "RawImagesForResearch_x"
    raw.mkdir(parents=True)
    for b in bases:
        (raw / f"{b}.jpg").write_bytes(b"")
    return sp


def _raw(sp: Path) -> Path:
    return sp / "captures" / "c1_femur_deep" / "RawImagesForResearch_x"


# a name the real study-name grammar parses: date, count, name tokens, the <age><sex> anchor, tail
NAME = "01jan26_999_Testsurname_Testname_45e_170_70_41.5r"
BASES = ["20260101_000000001", "20260101_000000002", "20260101_000000003"]
UID = dnc._study_code(NAME)


# ----------------------------------------------------------------------------- measure_study
def test_measure_study_records_which_frames_made_the_top_k(tmp_path, monkeypatch):
    sp = _study(tmp_path, NAME, BASES)
    monkeypatch.setattr(dnc, "study_modalities",
                        lambda p: {"femur": SimpleNamespace(raw_image_dir=_raw(sp))})
    seg = _FakeSeg({BASES[0]: 3, BASES[1]: 9, BASES[2]: 5})

    row = dnc.measure_study(sp, seg)

    assert row["study"] == NAME and row["study_uid"] == UID
    fem = row["vals"]["femur"]
    # best-first by RF area, and index-aligned with the per-frame values
    assert fem["top_frame_bases"] == [BASES[1], BASES[2], BASES[0]]
    assert [tf["rf_thickness_mm"] for tf in fem["top_frames"]] == [9.0, 5.0, 3.0]
    assert fem["rf_thickness_mm"] == 5.0 and fem["n_frames"] == 3
    assert "rf" not in row["vals"] and "sweep" not in row["vals"]


def test_measure_study_ties_keep_frame_base_order(tmp_path, monkeypatch):
    """Equal RF areas rank in frame-base (capture) order — the stable sort the freeze always used.
    A key of (area, base) reversed would give [B2, B1, B0] and silently move the served frames."""
    sp = _study(tmp_path, NAME, BASES)
    monkeypatch.setattr(dnc, "study_modalities",
                        lambda p: {"femur": SimpleNamespace(raw_image_dir=_raw(sp))})
    fem = dnc.measure_study(sp, _FakeSeg({BASES[0]: 5, BASES[1]: 5, BASES[2]: 9}))["vals"]["femur"]
    assert fem["top_frame_bases"] == [BASES[2], BASES[0], BASES[1]]


def test_measure_study_a_frame_with_no_rf_is_not_a_candidate(tmp_path, monkeypatch):
    sp = _study(tmp_path, NAME, BASES)
    monkeypatch.setattr(dnc, "study_modalities",
                        lambda p: {"femur": SimpleNamespace(raw_image_dir=_raw(sp))})
    seg = _FakeSeg({BASES[0]: 0, BASES[1]: 4, BASES[2]: 0})   # two frames segment no RF at all
    fem = dnc.measure_study(sp, seg)["vals"]["femur"]
    assert fem["top_frame_bases"] == [BASES[1]] and fem["n_frames"] == 1


def test_measure_all_returns_the_rows_it_wrote_and_they_assemble(tmp_path, monkeypatch):
    """The entry point: main() hands measure_all()'s return to assemble_cohort. The 2026-09-03
    refactor once dropped that return (caught in review) — an apply would have run the whole GPU
    pass and then crashed on None."""
    sp = _study(tmp_path / "root", NAME, BASES)
    mods = {r: SimpleNamespace(raw_image_dir=_raw(sp)) for r in ("femur", "rf", "sweep")}
    monkeypatch.setattr(dnc, "STUDIES_ROOT", tmp_path / "root")
    monkeypatch.setattr(dnc, "LANDMARK_JSON", tmp_path / "landmark.json")
    monkeypatch.setattr(dnc, "study_modalities", lambda p: mods)
    monkeypatch.setattr(dnc, "_Segmenter", lambda: _FakeSeg({BASES[0]: 3, BASES[1]: 9, BASES[2]: 5}))
    monkeypatch.setattr(dnc.fkl, "killed_study_uids", lambda: set())

    rows = dnc.measure_all()

    assert rows is not None and len(rows) == 1 and rows[0]["study_uid"] == UID
    assert rows == json.loads((tmp_path / "landmark.json").read_text(encoding="utf-8"))
    out_rows, prov = dnc.assemble_cohort(rows)          # the call that crashed on None
    assert len(out_rows) == 1 and out_rows[0]["study_code"] == UID and prov["n_studies"] == 1


# ----------------------------------------------------------------------------- snapshot
def _rows(with_bases: bool, name: str = NAME, note: str = "femur ≈ center-RF") -> list[dict]:
    lm = {"rf_thickness_mm": 20.0, "n_frames": 15,
          "top_frames": [{"rf_thickness_mm": 20.0}] * 5}
    if with_bases:
        lm["top_frame_bases"] = BASES[:1] * 5
    return [{"study": name, "vals": {"femur": dict(lm), "rf": dict(lm)},
             "decision": {"source": "center_rf", "int_cv_trusted": True, "note": note}}]


def test_snapshot_is_uid_keyed_and_carries_no_folder_name(tmp_path):
    out = dnc.snapshot_landmark(_rows(with_bases=True), refreeze=9, date="2026-09-03", out_dir=tmp_path)
    assert out == tmp_path / "new_round_landmark_r9.json"
    text = out.read_text(encoding="utf-8")
    assert NAME not in text and "Testsurname" not in text and '"study"' not in text
    doc = json.loads(text)
    assert doc["refreeze"] == 9 and doc["n_studies"] == 1 and doc["dropped_not_in_cohort"] == 0
    assert doc["studies"][0]["study_uid"] == UID
    assert doc["frame_identity"] == "top_frame_bases" and doc["studies_with_frame_bases"] == 1
    assert doc["studies"][0]["vals"]["femur"]["top_frame_bases"] == BASES[:1] * 5


def test_snapshot_of_a_values_only_freeze_says_so(tmp_path):
    """r1–r5 were measured before frame identity existed: the snapshot must not pretend otherwise."""
    doc = json.loads(dnc.snapshot_landmark(_rows(with_bases=False), refreeze=5, date="2026-08-23",
                                           out_dir=tmp_path).read_text(encoding="utf-8"))
    assert doc["frame_identity"] == "values_only" and doc["studies_with_frame_bases"] == 0
    # a row that predates `study_uid` still keys by the same recipe
    assert doc["studies"][0]["study_uid"] == UID


def test_snapshot_identity_ignores_a_study_with_nothing_measurable():
    rows = _rows(with_bases=True) + [{"study": "02jan26_998_Other_Person_50k", "vals": {},
                                      "decision": {"source": "none"}}]
    doc = dnc.build_landmark_snapshot(rows, refreeze=9, date="2026-09-03")
    assert doc["n_studies"] == 2 and doc["frame_identity"] == "top_frame_bases"


def test_snapshot_refuses_to_write_a_leaking_full_name(tmp_path):
    rows = _rows(with_bases=True, note=f"checked {NAME} by hand")
    with pytest.raises(ValueError, match="folder name"):
        dnc.snapshot_landmark(rows, refreeze=9, date="2026-09-03", out_dir=tmp_path)
    assert not (tmp_path / "new_round_landmark_r9.json").exists()


def test_snapshot_refuses_a_bare_name_token_but_not_a_substring(tmp_path):
    """The surname alone, as a word, is a leak. A name that merely sits inside a template word
    ("anonaaz" in "corroborates") is not — whole-word matching keeps the check loud AND usable."""
    with pytest.raises(ValueError, match="name token"):
        dnc.build_landmark_snapshot(_rows(True, note="checked Testsurname by hand"),
                                    refreeze=9, date="2026-09-03")
    anonaaz = "01jan26_999_Anonaaz_Testname_45e_170_70_41.5r"
    doc = dnc.build_landmark_snapshot(_rows(True, name=anonaaz, note="sweep corroborates femur"),
                                      refreeze=9, date="2026-09-03")
    assert doc["studies"][0]["study_uid"] == dnc._study_code(anonaaz)


def test_snapshot_is_bound_to_the_cohort_it_sits_beside():
    rows = _rows(True) + _rows(True, name="02jan26_998_Other_Person_50k_160_60_40.0l")
    other = dnc._study_code("02jan26_998_Other_Person_50k_160_60_40.0l")
    # a row outside the cohort (a ledger kill applied at assembly) is dropped and counted
    doc = dnc.build_landmark_snapshot(rows, refreeze=9, date="2026-09-03", expect_uids={UID})
    assert [s["study_uid"] for s in doc["studies"]] == [UID] and doc["dropped_not_in_cohort"] == 1
    # a cohort uid the rows cannot describe = the working JSON is not this freeze's source
    with pytest.raises(ValueError, match="absent"):
        dnc.build_landmark_snapshot(rows, refreeze=9, date="2026-09-03", expect_uids={UID, other, "NRZZZZZZZZ"})


def test_snapshot_is_written_once_unless_forced(tmp_path):
    doc = dnc.build_landmark_snapshot(_rows(True), refreeze=9, date="2026-09-03")
    dnc.write_landmark_snapshot(doc, out_dir=tmp_path)
    with pytest.raises(FileExistsError):
        dnc.write_landmark_snapshot(doc, out_dir=tmp_path)
    dnc.write_landmark_snapshot(dict(doc, date="2026-09-04"), out_dir=tmp_path, force=True)
    assert json.loads((tmp_path / "new_round_landmark_r9.json").read_text())["date"] == "2026-09-04"


# ----------------------------------------------------------------------------- the freeze log writes it
def _stats(tmp_path, monkeypatch, uids=(UID,), landmark_rows=None):
    stats = tmp_path / "cohort_stats"
    stats.mkdir()
    live = stats / "per_study_cohort_new.csv"
    with open(live, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["study_code", "rf_thickness_mm", "sex"])
        for u in uids:
            w.writerow([u, "20.0", "M"])
    monkeypatch.setattr(cfl, "STATS", stats)
    monkeypatch.setattr(cfl, "LIVE", live)
    monkeypatch.setattr(cfl, "LOG", stats / "new_round_freeze_log.jsonl")
    monkeypatch.setattr(cfl, "PROV_LIVE", stats / "new_round_cohort_provenance.json")
    landmark = tmp_path / "landmark_full.json"
    landmark.write_text(json.dumps(landmark_rows if landmark_rows is not None else _rows(True)),
                        encoding="utf-8")
    monkeypatch.setattr(dnc, "LANDMARK_JSON", landmark)
    return stats, landmark


def test_freeze_log_record_writes_the_landmark_snapshot_beside_the_csv(tmp_path, monkeypatch):
    stats, _ = _stats(tmp_path, monkeypatch)
    (stats / "new_round_cohort_provenance.json").write_text('{"n_studies": 1}', encoding="utf-8")

    e = cfl.record(cfl._read_live(), date="2026-09-03", note="t")

    assert e["refreeze"] == 1
    assert (stats / "per_study_cohort_new_r1.csv").exists()
    snap = json.loads((stats / "new_round_landmark_r1.json").read_text(encoding="utf-8"))
    assert snap["refreeze"] == 1 and snap["date"] == "2026-09-03"
    assert snap["studies"][0]["study_uid"] == UID
    assert NAME not in (stats / "new_round_landmark_r1.json").read_text(encoding="utf-8")
    # the provenance file (overwritten by every apply) travels with the freeze too
    assert (stats / "new_round_cohort_provenance_r1.json").read_text(encoding="utf-8") == '{"n_studies": 1}'


def test_freeze_log_record_validates_before_writing_and_the_retry_is_not_locked_out(tmp_path, monkeypatch):
    """A refusal must leave NO archive and NO log row — else the idempotency guard ("unchanged since
    rN") would refuse the retry forever and the freeze would stand without its evidence."""
    stats, landmark = _stats(tmp_path, monkeypatch,
                             landmark_rows=_rows(True, note="checked Testsurname by hand"))
    with pytest.raises(ValueError, match="name token"):
        cfl.record(cfl._read_live(), date="2026-09-03", note="t")
    assert not (stats / "per_study_cohort_new_r1.csv").exists()
    assert not (stats / "new_round_freeze_log.jsonl").exists()
    assert not (stats / "new_round_landmark_r1.json").exists()

    landmark.write_text(json.dumps(_rows(True)), encoding="utf-8")          # fixed source
    e = cfl.record(cfl._read_live(), date="2026-09-03", note="t")
    assert e["refreeze"] == 1 and (stats / "new_round_landmark_r1.json").exists()


def test_freeze_log_record_refuses_a_json_that_does_not_describe_the_cohort(tmp_path, monkeypatch):
    stats, _ = _stats(tmp_path, monkeypatch, uids=(UID, "NRZZZZZZZZ"))
    with pytest.raises(ValueError, match="absent"):
        cfl.record(cfl._read_live(), date="2026-09-03", note="t")
    assert not (stats / "new_round_freeze_log.jsonl").exists()


def test_freeze_log_record_refuses_when_a_stale_snapshot_already_exists(tmp_path, monkeypatch):
    stats, _ = _stats(tmp_path, monkeypatch)
    (stats / "new_round_landmark_r1.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FileExistsError):
        cfl.record(cfl._read_live(), date="2026-09-03", note="t")
    assert not (stats / "per_study_cohort_new_r1.csv").exists()


def test_freeze_log_record_drops_a_killed_study_from_the_snapshot(tmp_path, monkeypatch):
    killed = "02jan26_998_Other_Person_50k_160_60_40.0l"
    stats, _ = _stats(tmp_path, monkeypatch, landmark_rows=_rows(True) + _rows(True, name=killed))
    cfl.record(cfl._read_live(), date="2026-09-03", note="t")
    snap = json.loads((stats / "new_round_landmark_r1.json").read_text(encoding="utf-8"))
    assert [s["study_uid"] for s in snap["studies"]] == [UID] and snap["dropped_not_in_cohort"] == 1


def test_freeze_log_backfill_from_git_does_not_snapshot(tmp_path, monkeypatch):
    """A --from-git backfill's cohort is not what the working landmark JSON describes — through the
    CLI, which is where `snapshot=not a.from_git` lives."""
    stats, _ = _stats(tmp_path, monkeypatch)
    monkeypatch.setattr(cfl, "_read_git", lambda ref: cfl._read_live())
    assert cfl.main(["--record", "--from-git", "deadbeef", "--date", "2026-09-03"]) == 0
    assert (stats / "per_study_cohort_new_r1.csv").exists()
    assert not (stats / "new_round_landmark_r1.json").exists()

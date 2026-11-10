"""E1 — the per-study calibration progress ledger (pull next unexplored, mark
explored, accumulate labelled counts; resilient to a corrupt file)."""
from calibration import study_ledger as sl


def test_ledger_roundtrip(tmp_path):
    p = tmp_path / "ledger.json"
    assert sl.explored_count(p) == 0
    assert not sl.is_explored("ABC", p)
    sl.mark_explored("ABC", n_labeled=3, path=p)
    assert sl.is_explored("ABC", p)
    assert sl.explored_count(p) == 1
    sl.mark_explored("ABC", n_labeled=2, path=p)   # accumulates
    assert sl._load(p)["explored"]["ABC"]["n_labeled"] == 5


def test_next_unexplored(tmp_path):
    p = tmp_path / "ledger.json"
    studies = ["S1", "S2", "S3"]
    assert sl.next_unexplored(studies, p) == "S1"
    sl.mark_explored("S1", path=p)
    assert sl.next_unexplored(studies, p) == "S2"
    sl.mark_explored("S2", path=p)
    sl.mark_explored("S3", path=p)
    assert sl.next_unexplored(studies, p) is None


def test_corrupt_ledger_recovers(tmp_path):
    p = tmp_path / "ledger.json"
    p.write_text("{not valid json")
    assert sl.explored_count(p) == 0          # falls back, no crash
    sl.mark_explored("ABC", path=p)           # and is writable again
    assert sl.is_explored("ABC", p)

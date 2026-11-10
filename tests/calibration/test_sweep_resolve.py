"""E1 — the frame_loader sweep-resolve branch. A frame outside the 956-frame
transport cohort resolves to its on-disk RawImages jpg via the study_codes map,
and load_frame stays transport-only (returns None) when no segmenter is supplied."""
from calibration import frame_loader as fl


def test_resolve_sweep_jpg(tmp_path, monkeypatch):
    rdir = tmp_path / "rel" / "dir" / "RawImagesForResearch_20260101"
    rdir.mkdir(parents=True)
    (rdir / "FRAME123.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    monkeypatch.setattr(fl, "_SWEEP_BACKUP", tmp_path)
    monkeypatch.setattr(fl, "_study_dir_map", lambda: {"ABC999": "rel/dir"})
    assert fl.resolve_sweep_jpg("ABC999", "FRAME123") == rdir / "FRAME123.jpg"
    assert fl.resolve_sweep_jpg("ABC999", "MISSING") is None   # study known, frame absent
    assert fl.resolve_sweep_jpg("NOPE", "FRAME123") is None    # study unknown


def test_resolve_sweep_jpg_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(fl, "_SWEEP_BACKUP", tmp_path)
    monkeypatch.setattr(fl, "_study_dir_map", lambda: {"ABC999": "does/not/exist"})
    assert fl.resolve_sweep_jpg("ABC999", "FRAME123") is None


def test_load_frame_transport_first_then_sweep_needs_segmenter(monkeypatch):
    # not in transport + no segmenter -> None (backward-compatible transport-only)
    monkeypatch.setattr(fl, "resolve", lambda s, f: None)
    assert fl.load_frame("X", "Y") is None
    # ...and it would dispatch to the sweep loader only with learn + bundle_meta
    called = {}
    monkeypatch.setattr(fl, "load_frame_sweep",
                        lambda s, f, learn, bm, dev=None: called.setdefault("hit", (s, f)))
    fl.load_frame("X", "Y", learn=object(), bundle_meta=object())
    assert called["hit"] == ("X", "Y")

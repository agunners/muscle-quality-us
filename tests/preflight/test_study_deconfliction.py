"""Tests for study-level deconfliction — encodes the edge cases found in the real anonacl zip."""
from preflight_helpers.study_deconfliction import _Rec, deconflict


def test_pilot_shape_pairs_classifies_and_flags_edge_cases():
    # Mirrors the anonacl pilot: a long deep sweep, short shallow RFs, short deep FEMUR,
    # an ORPHAN .dcm before any capture, and a final retake with NO .dcm.
    # nframes are CONTENT frames (.jpg): the sweep is the long clip, the stills are short (~15).
    recs = [
        _Rec("dcm", "d_orphan", 100.0, dcm_index="1"),         # no capture near -> orphan
        _Rec("raw", "sweepA", 600.0, width=285, nframes=100),  # deep + long -> sweep
        _Rec("mp4", "sweepA.mp4", 615.0),
        _Rec("dcm", "d_sweepA", 660.0, dcm_index="2"),         # follows the sweep
        _Rec("raw", "rf1", 700.0, width=399, nframes=15),
        _Rec("mp4", "rf1.mp4", 703.0),
        _Rec("dcm", "d_rf1", 712.0, dcm_index="3"),
        _Rec("raw", "femur", 800.0, width=285, nframes=15),
        _Rec("mp4", "femur.mp4", 804.0),
        _Rec("dcm", "d_femur", 815.0, dcm_index="4"),
        _Rec("raw", "retake", 2000.0, width=399, nframes=15),  # shallow short, NO .dcm
        _Rec("mp4", "retake.mp4", 2005.0),
    ]
    r = deconflict(recs)
    assert [c.identity for c in r.captures] == ["sweep", "rf", "femur", "rf"]
    assert len(r.orphan_dcms) == 1 and "d_orphan" in r.orphan_dcms[0]
    # the sweep keeps ITS dcm (preceding-rule), not stolen by the nearer rf1
    assert r.captures[0].dcm == "d_sweepA"
    retake = r.captures[-1]
    assert retake.dcm is None and any("no .dcm" in f for f in retake.flags)


def test_missing_dcm_does_not_block_depth_classification():
    r = deconflict([_Rec("raw", "r", 10.0, width=399, nframes=15), _Rec("mp4", "m", 12.0)])
    assert r.captures[0].identity == "rf" and r.captures[0].depth_setting == "shallow"


def test_orphan_take_does_not_steal_the_sweep_mp4():
    # 26jun26_020 (anonaal): a leftover take (its mp4 deleted, the RawImages forgotten) must NOT grab a
    # real capture's video and cascade the real sweep into an orphan. Mutual-nearest pairs each real
    # capture and orphans ONLY the leftover. (Old time-order-nearest paired the leftover at a 42s gap,
    # shifted every cine onto the wrong mp4, and left the real 90-frame sweep with mp4=None.)
    recs = [
        _Rec("raw", "leftover", 12.0, width=300, nframes=15),   # mp4 deleted -> no pair
        _Rec("raw", "rfA", 50.0, width=300, nframes=15),
        _Rec("mp4", "rfA.mp4", 54.0),
        _Rec("raw", "rfB", 121.0, width=300, nframes=15),
        _Rec("mp4", "rfB.mp4", 125.0),
        _Rec("raw", "sweep", 170.0, width=300, nframes=90),     # long clip -> sweep
        _Rec("mp4", "sweep.mp4", 193.0),                        # 23s after start (a 90-frame sweep)
    ]
    by = {c.raw_folder: c for c in deconflict(recs).captures}
    assert by["sweep"].is_sweep and by["sweep"].mp4 == "sweep.mp4" and by["sweep"].pairing == "paired"
    assert by["leftover"].mp4 is None and by["leftover"].pairing == "orphan"
    assert by["rfA"].mp4 == "rfA.mp4" and by["rfB"].mp4 == "rfB.mp4"


def test_long_clip_is_sweep_regardless_of_depth():
    # v8: the sweep sits at the SHALLOW (4.9) setting, so clip length must win over depth.
    r = deconflict([_Rec("raw", "s", 10.0, width=399, nframes=500), _Rec("mp4", "m", 12.0)])
    assert r.captures[0].identity == "sweep"


def test_unpaired_raw_is_flagged():
    r = deconflict([_Rec("raw", "lonely", 10.0, width=285, nframes=60)])  # no mp4
    assert r.captures[0].mp4 is None
    assert any("no mp4" in w for w in r.warnings)

"""The femur-cine representative-frame ranking (Akadir 2026-06-26): a femur-anchored Tier-1 frame
(caliper reaches the bone) outranks a Tier-2 proxy, which outranks a Tier-3 (no measurable femur,
excluded); within a tier the fullest (higher muscle occupancy) wins. We monkeypatch the canonical QMT
detail so the ranking logic is tested without a model / real masks."""
from labeling_app import femur_cine_scorer as fcs

# mask sentinel -> (tier, occupancy%)
_TABLE = {
    "f_t1_full": (1, 80.0),
    "f_t1_lean": (1, 55.0),
    "f_t2_full": (2, 99.0),
    "f_t3": (3, 0.0),
}


def _patch(monkeypatch):
    monkeypatch.setattr(fcs, "_qmt_measurement_details",
                        lambda m: {"qmt_muscle_occupancy_pct": _TABLE[m][1], "_t": _TABLE[m][0]})
    monkeypatch.setattr(fcs, "qmt_tier", lambda d: d["_t"])


def test_tier3_is_not_a_candidate(monkeypatch):
    _patch(monkeypatch)
    assert fcs.femur_frame_score("f_t3") == 0.0


def test_tier_dominates_occupancy(monkeypatch):
    _patch(monkeypatch)
    # A nearly-full Tier-2 must NOT outrank even a lean Tier-1 (caliper-to-bone wins).
    assert fcs.femur_frame_score("f_t1_lean") > fcs.femur_frame_score("f_t2_full")


def test_occupancy_breaks_ties_within_tier(monkeypatch):
    _patch(monkeypatch)
    assert fcs.femur_frame_score("f_t1_full") > fcs.femur_frame_score("f_t1_lean")


def test_top_frames_orders_and_excludes_tier3(monkeypatch):
    _patch(monkeypatch)
    masks = {k: k for k in _TABLE}            # mask sentinel == its key
    assert fcs.top_femur_frames(masks, k=3) == ["f_t1_full", "f_t1_lean", "f_t2_full"]
    assert fcs.top_femur_frames(masks, k=2) == ["f_t1_full", "f_t1_lean"]
    assert "f_t3" not in fcs.top_femur_frames(masks, k=10)


def test_missing_occupancy_defaults_to_zero(monkeypatch):
    monkeypatch.setattr(fcs, "_qmt_measurement_details", lambda m: {"qmt_muscle_occupancy_pct": None})
    monkeypatch.setattr(fcs, "qmt_tier", lambda d: 1)
    assert fcs.femur_frame_score("x") == 2000.0   # Tier-1 base, occupancy treated as 0


# ---- QMT consistency QA (Akadir 2026-07-17) ---------------------------------

def test_qmt_consistency_reliable_when_tight():
    s = fcs._qmt_consistency_stats([30.0, 30.5, 30.2, 30.4, 30.1])
    assert s["verdict"] == "reliable" and s["cv"] < 0.08 and s["n"] == 5


def test_qmt_consistency_variable_when_erratic():
    s = fcs._qmt_consistency_stats([30.0, 12.0, 28.0, 14.0, 30.0])
    assert s["verdict"] == "variable" and s["cv"] >= 0.08
    assert s["roughness"] > 0.1                     # erratic -> high curvature


def test_qmt_consistency_insufficient_below_two():
    assert fcs._qmt_consistency_stats([30.0])["verdict"] == "insufficient"
    assert fcs._qmt_consistency_stats([])["cv"] is None
    assert fcs._qmt_consistency_stats([30.0, 31.0])["roughness"] is None   # n<3 -> no curvature


def test_qmt_consistency_is_scale_free():
    # px vs mm cannot change the verdict: both measures normalise by the median.
    a = fcs._qmt_consistency_stats([30.0, 31.0, 29.0, 30.5])
    b = fcs._qmt_consistency_stats([300.0, 310.0, 290.0, 305.0])
    assert a["cv"] == b["cv"] and a["roughness"] == b["roughness"]


def test_femur_qmt_consistency_uses_only_standard(monkeypatch):
    # only Tier-1 (femur-anchored) frames feed the QMT reproducibility; Tier-2/3 excluded.
    tbl = {"a": (1, 100.0), "b": (1, 102.0), "c": (2, 500.0), "d": (3, None)}
    monkeypatch.setattr(fcs, "_qmt_measurement_details",
                        lambda m: {"qmt_px": tbl[m][1], "_t": tbl[m][0]})
    monkeypatch.setattr(fcs, "qmt_tier", lambda d: d["_t"])
    s = fcs.femur_qmt_consistency({k: k for k in tbl})
    assert s["n"] == 2 and s["verdict"] == "reliable"   # a,b only; tight -> reliable

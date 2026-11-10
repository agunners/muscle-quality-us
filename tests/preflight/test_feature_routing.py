"""v8 feature routing — ALL measurements from the femur cine, TEXTURE only from center-RF (Akadir
2026-07-14; reverses the 2026-06-26 center-RF-geometry split). Every geometry + fat + QMT/STAR value
comes from the femur (deep FOV → complete lower fascia, one consistent source across studies); only the
echo/texture family (int_cv, int_*, rf_echo) comes from the center-RF cine. The long sweep contributes
nothing to a 3-modality study; a legacy single-modality study (no femur/RF aggregate) leaves the sweep
unchanged."""
import json

import preflight_helpers.feature_routing as fr
from preflight_helpers.feature_routing import route_aggregate, FEMUR_FEATURES, CENTER_RF_FEATURES
from preflight_helpers.study_discovery import Modality


def test_all_measurements_from_femur_texture_from_center_rf():
    sweep = {"full_thickness_q50_mm": 22.3, "rf_thickness_mm": 20.0, "int_cv_within": 0.80,
             "fat_thickness_q50_mm": 4.0, "qmt_mm": 39.5, "some_other": 1}
    femur = {"full_thickness_q50_mm": 27.4, "rf_thickness_mm": 25.5, "visible_rf_area_mm2": 1000.0,
             "qmt_mm": 51.1, "star_ratio": 1.8, "fat_to_depth_q50": 0.13,
             "fat_thickness_q50_mm": 5.0, "fat_thickness_max_mm": 6.2, "muscle_fat_ratio": 3.1,
             "qmt_provenance": "standard",
             # adversarial: the femur ALSO carries texture — it must NOT win (texture is center-RF's).
             "int_cv_within": 0.50, "rf_echo": 0.30}
    rf = {"int_cv_within": 0.82, "rf_echo": 0.71,
          # adversarial: center-RF ALSO carries geometry/fat — it must NOT win (those are the femur's).
          "full_thickness_q50_mm": 18.0, "rf_thickness_mm": 16.0, "fat_thickness_q50_mm": 9.0}
    out = route_aggregate(sweep, femur, rf)
    # ALL measurements come from the femur (complete deep boundary, consistent across studies)
    assert out["full_thickness_q50_mm"] == 27.4
    assert out["rf_thickness_mm"] == 25.5
    assert out["visible_rf_area_mm2"] == 1000.0
    assert out["qmt_mm"] == 51.1 and out["star_ratio"] == 1.8
    assert out["fat_to_depth_q50"] == 0.13
    assert out["fat_thickness_q50_mm"] == 5.0 and out["fat_thickness_max_mm"] == 6.2
    assert out["muscle_fat_ratio"] == 3.1
    assert out["qmt_provenance"] == "standard"          # provenance follows the femur
    # ONLY texture (the echo family) comes from the center-RF
    assert out["int_cv_within"] == 0.82
    assert out["rf_echo"] == 0.71
    assert out["some_other"] == 1                        # sweep untouched


def test_missing_modality_keeps_sweep():
    sweep = {"rf_thickness_mm": 20.0, "int_cv_within": 0.80}
    assert route_aggregate(sweep, {}, {}) == sweep       # legacy sweep-only: nothing to route


def test_femur_geometry_wins_even_when_center_rf_disagrees():
    # There is no longer a disagreement-conditional swap — geometry is ALWAYS the femur's, regardless of
    # whether the center-RF thickness agrees. (The agreement only gates texture trust, tested elsewhere.)
    sweep = {}
    femur = {"full_thickness_q50_mm": 28.0, "rf_thickness_mm": 24.0}
    rf = {"full_thickness_q50_mm": 12.0, "rf_thickness_mm": 10.0, "int_cv_within": 0.7}  # wildly clipped
    out = route_aggregate(sweep, femur, rf)
    assert out["full_thickness_q50_mm"] == 28.0 and out["rf_thickness_mm"] == 24.0   # femur, always
    assert out["int_cv_within"] == 0.7                                               # texture, center-RF


def test_routing_sets_are_disjoint_and_correctly_split():
    # femur owns every measurement; center-RF owns only the echo/texture family; the two are disjoint.
    assert {"rf_thickness_mm", "full_thickness_q50_mm", "visible_rf_area_mm2", "qmt_mm", "star_ratio",
            "fat_to_depth_q50", "fat_thickness_q50_mm", "fat_thickness_max_mm",
            "muscle_fat_ratio"} <= FEMUR_FEATURES
    assert {"int_cv_within", "rf_echo"} <= CENTER_RF_FEATURES
    assert "int_cv_within" not in FEMUR_FEATURES and "rf_echo" not in FEMUR_FEATURES
    assert "rf_thickness_mm" not in CENTER_RF_FEATURES and "qmt_mm" not in CENTER_RF_FEATURES
    assert not (FEMUR_FEATURES & CENTER_RF_FEATURES)


# --- routed picks persistence (torch-free) ---------------------------------
# route_study_aggregate is the only torch seam (compute_modality_aggregate reloads the segmenter). We
# stub BOTH the seam and study_modalities so this exercises the pure `_routed_picks` slim projection
# (Akadir 2026-07-20) without a GPU — the CI guard that a routed record is JSON-safe + reproducible.

def _fake_mods(tmp_path):
    return {
        "femur": Modality(role="femur", raw_image_dir=tmp_path / "femur" / "RawImages",
                          dicom_path=tmp_path / "femur" / "x.dcm"),
        "rf": Modality(role="rf", raw_image_dir=tmp_path / "rf" / "RawImages",
                       dicom_path=tmp_path / "rf" / "y.dcm"),
    }


def _stub_compute(monkeypatch):
    """Stub compute_modality_aggregate: honor the REAL keep/drop gate, populate frames_out with the
    kept (analysis) frames + a middle anchor, and return a synthetic non-empty aggregate."""
    def fake(raw_image_dir, dcm_dir, picked_bases, *, bmi, bundle, gates_map=None,
             frames_out=None, **kw):
        kept = fr._gate_kept(picked_bases, gates_map)
        if frames_out is not None:
            frames_out["kept_bases"] = list(kept)
            frames_out["anchor"] = kept[len(kept) // 2] if kept else None
        if not kept:
            return {}
        return {"full_thickness_q50_mm": 25.0, "int_cv_within": 0.8, "rf_echo": 0.5}
    monkeypatch.setattr(fr, "compute_modality_aggregate", fake)


def test_routed_picks_shape_is_slim_and_json_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(fr, "study_modalities", lambda p: _fake_mods(tmp_path))
    _stub_compute(monkeypatch)
    # A RAW gate dict carrying drop_reason_codes as a Python set() — exactly the shape that would crash
    # write_draft's json.dump if it leaked into the record. f_020 is marked NOT usable (a dropped pick).
    gates_femur = {
        "f_020": {"usable": False, "use_for_analysis": True,
                  "drop_reason_codes": {"motion_blur", "wrong_site"}},
    }
    routed = fr.route_study_aggregate(
        tmp_path, {"some_sweep": 1}, picks_femur=["f_010", "f_020"], picks_rf=["f_030"],
        bmi=24.0, bundle=object(), gates_femur=gates_femur)
    rp = routed["_routed_picks"]
    assert set(rp) == {"femur", "rf"}
    # frame_bases keeps every pick (sorted); analysis excludes the gated-out f_020; anchor is a kept base.
    assert rp["femur"]["frame_bases"] == ["f_010", "f_020"]
    assert rp["femur"]["analysis_frame_bases"] == ["f_010"]
    assert rp["femur"]["n_analysis_frames"] == 1
    assert rp["femur"]["anchor"] == "f_010"
    # CRITICAL: only the two slim booleans per frame — never the raw gate (no drop_reason_codes/set()).
    assert rp["femur"]["gates"]["f_020"] == {"usable": False, "use_for_analysis": True}
    assert rp["femur"]["gates"]["f_010"] == {"usable": True, "use_for_analysis": True}
    for role in rp:
        for g in rp[role]["gates"].values():
            assert set(g) == {"usable", "use_for_analysis"}
    # The whole record-bound block round-trips through JSON (no set leaked).
    assert json.loads(json.dumps(rp)) == rp


def test_routed_picks_skips_role_with_no_kept_frame(tmp_path, monkeypatch):
    """A role whose picks are all gated out (empty aggregate) is NOT recorded — mirrors the routing
    contract (route_aggregate leaves the sweep untouched for that role)."""
    monkeypatch.setattr(fr, "study_modalities", lambda p: _fake_mods(tmp_path))
    _stub_compute(monkeypatch)
    routed = fr.route_study_aggregate(
        tmp_path, {}, picks_femur=["f_010"], picks_rf=["f_030"], bmi=24.0, bundle=object(),
        gates_rf={"f_030": {"usable": False}})   # RF fully dropped -> no rf aggregate
    assert set(routed["_routed_picks"]) == {"femur"}
    assert routed["_routed_modalities"] == ["femur"]

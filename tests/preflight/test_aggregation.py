"""Aggregation: anchor_primary (geometry) vs agg_primary_with_anchor_passthrough
(intensity) vs the QMT provenance 3-tier gate (qmt_mm + star_ratio)."""
from __future__ import annotations

from preflight_helpers.aggregation import aggregate_features


def _frames() -> list[dict]:
    """Three frames, all Tier-1 standard QMT. The anchor (F2) is deliberately
    *not* the QMT median, so a test can tell tier-median from anchor-passthrough."""
    return [
        {"frame_base": "F1", "is_anchor": False, "rf_thickness_mm": 18.0,
         "int_cv_within": 0.40, "star_ratio": 1.1, "qmt_mm": 36.0,
         "qmt_provenance": "standard_qmt", "qmt_validity_flags": []},
        {"frame_base": "F2", "is_anchor": True,   # ANCHOR
         "rf_thickness_mm": 20.0, "int_cv_within": 0.50, "star_ratio": 1.3,
         "qmt_mm": 44.0, "qmt_provenance": "standard_qmt", "qmt_validity_flags": []},
        {"frame_base": "F3", "is_anchor": False, "rf_thickness_mm": 22.0,
         "int_cv_within": 0.60, "star_ratio": 1.2, "qmt_mm": 40.0,
         "qmt_provenance": "standard_qmt", "qmt_validity_flags": []},
    ]


def _qframe(fb: str, qmt: float, prov: str, *, flags: list | None = None,
            star: float | None = None) -> dict:
    """A frame carrying just the QMT-relevant fields."""
    return {"frame_base": fb, "is_anchor": False, "qmt_mm": qmt,
            "star_ratio": star if star is not None else qmt / 36.0,
            "qmt_provenance": prov, "qmt_validity_flags": flags or []}


# ---- the two generic policies (geometry / intensity) ----------------------

def test_anchor_primary_for_geometry() -> None:
    out = aggregate_features(_frames(), anchor_frame_base="F2")
    # Geometry: primary = anchor value (_repr); _agg_median is sensitivity check.
    assert out["rf_thickness_mm"] == 20.0          # primary (anchor)
    assert out["rf_thickness_mm_repr"] == 20.0
    assert out["rf_thickness_mm_agg_median"] == 20.0
    assert out["_policies"]["rf_thickness_mm"] == "anchor_primary"


def test_agg_primary_for_intensity() -> None:
    out = aggregate_features(_frames(), anchor_frame_base="F2")
    # Intensity: primary = multi-frame median (_agg_median); _repr is the anchor.
    assert out["int_cv_within"] == 0.50            # median of 0.40, 0.50, 0.60
    assert out["int_cv_within_repr"] == 0.50       # anchor
    assert out["int_cv_within_agg_median"] == 0.50
    assert out["_policies"]["int_cv_within"] == "agg_primary_with_anchor_passthrough"


def test_single_frame_collapse() -> None:
    out = aggregate_features([_frames()[1]], anchor_frame_base="F2")
    assert out["_single_frame_collapse"] is True
    assert out["rf_thickness_mm"] == 20.0
    assert out["int_cv_within"] == 0.50
    assert out["qmt_mm"] == 44.0                   # the one Tier-1 frame


def test_force_all_median_overrides_every_family() -> None:
    # N5 anchorless pooled-median: the generic families all use _agg_median.
    out = aggregate_features(_frames(), anchor_frame_base="F2",
                             force_all_median=True)
    assert out["_force_all_median"] is True
    assert out["rf_thickness_mm"] == 20.0    # median of 18/20/22, not anchor
    assert out["int_cv_within"] == 0.50      # median of 0.40/0.50/0.60
    assert out["_policies"]["rf_thickness_mm"] == "force_all_median"
    assert out["_policies"]["int_cv_within"] == "force_all_median"
    # _repr / _agg_median support columns are still emitted.
    assert out["rf_thickness_mm_repr"] == 20.0
    assert out["rf_thickness_mm_agg_median"] == 20.0


def test_default_keeps_per_family_policy() -> None:
    # The default (force_all_median=False) is unchanged — the per-run UI path.
    out = aggregate_features(_frames(), anchor_frame_base="F2")
    assert out["_force_all_median"] is False
    assert out["_policies"]["rf_thickness_mm"] == "anchor_primary"
    assert out["_policies"]["int_cv_within"] == "agg_primary_with_anchor_passthrough"


# ---- QMT 3-tier provenance gate -------------------------------------------

def test_qmt_tier1_standard_is_median_not_anchor() -> None:
    # All Tier-1; QMT/STAR primary = median of the tier, NOT the anchor value.
    out = aggregate_features(_frames(), anchor_frame_base="F2")
    assert out["qmt_mm"] == 40.0                   # median of 36/44/40
    assert out["star_ratio"] == 1.2                # median of 1.1/1.3/1.2
    assert out["qmt_provenance"] == "standard"
    assert (out["qmt_n_tier1"], out["qmt_n_tier2"], out["qmt_n_tier3"]) == (3, 0, 0)
    assert out["_policies"]["qmt_mm"] == "qmt_tier1_standard"
    assert out["_policies"]["star_ratio"] == "qmt_tier1_standard"
    # Support columns stay raw — _repr is still the anchor passthrough.
    assert out["qmt_mm_repr"] == 44.0
    assert out["qmt_mm_agg_median"] == 40.0


def test_qmt_force_all_median_does_not_override_the_gate() -> None:
    # force_all_median flips the generic families, but QMT/STAR still go
    # through the provenance gate — never the blind pooled-median.
    out = aggregate_features(_frames(), anchor_frame_base="F2",
                             force_all_median=True)
    assert out["_policies"]["qmt_mm"] == "qmt_tier1_standard"
    assert out["_policies"]["star_ratio"] == "qmt_tier1_standard"
    assert out["qmt_mm"] == 40.0


def test_qmt_never_pools_standard_with_proxy() -> None:
    # Two Tier-1 standard frames + one Tier-2 proxy. The proxy is excluded.
    frames = [
        _qframe("a", 38.0, "standard_qmt"),
        _qframe("b", 42.0, "standard_qmt"),
        _qframe("c", 18.0, "femur_supported_proxy"),   # would drag the median
    ]
    out = aggregate_features(frames)
    assert out["qmt_mm"] == 40.0                   # median(38, 42) — proxy excluded
    assert out["qmt_provenance"] == "standard"
    assert (out["qmt_n_tier1"], out["qmt_n_tier2"]) == (2, 1)


def test_qmt_falls_back_to_proxy_when_no_standard() -> None:
    frames = [
        _qframe("a", 34.0, "femur_supported_proxy"),
        _qframe("b", 38.0, "femur_supported_proxy"),
        _qframe("c", 5.0, "no_femur_proxy"),           # Tier 3 — excluded
    ]
    out = aggregate_features(frames)
    assert out["qmt_mm"] == 36.0                   # median(34, 38) — Tier-2 only
    assert out["qmt_provenance"] == "proxy"
    assert (out["qmt_n_tier2"], out["qmt_n_tier3"]) == (2, 1)
    assert out["_policies"]["qmt_mm"] == "qmt_tier2_proxy"


def test_qmt_none_when_no_femur_valid_frame() -> None:
    frames = [
        _qframe("a", 17.0, "no_femur_proxy"),
        _qframe("b", 60.0, "no_measurement"),
    ]
    out = aggregate_features(frames)
    assert out["qmt_mm"] is None
    assert out["star_ratio"] is None               # STAR rides on QMT
    assert out["qmt_provenance"] is None
    assert out["_policies"]["qmt_mm"] == "qmt_none_no_femur_valid"
    assert out["qmt_n_tier3"] == 2


def test_qmt_single_muscle_kills_proxy_not_standard() -> None:
    # rf_or_vi_missing_in_path demotes a *proxy* to Tier 3, but a standard
    # measurement keeps Tier 1 — it spans fascia-to-femur regardless of path.
    frames = [
        _qframe("std", 41.0, "standard_qmt", flags=["rf_or_vi_missing_in_path"]),
        _qframe("prox", 18.0, "femur_supported_proxy",
                flags=["rf_or_vi_missing_in_path"]),
    ]
    out = aggregate_features(frames)
    assert out["qmt_mm"] == 41.0                   # the standard frame survives
    assert out["qmt_provenance"] == "standard"
    assert (out["qmt_n_tier1"], out["qmt_n_tier3"]) == (1, 1)


def test_qmt_missing_provenance_is_tier3() -> None:
    # Legacy frames (no qmt_provenance field) must not be trusted.
    frames = [{"frame_base": "legacy", "qmt_mm": 33.0, "star_ratio": 0.9}]
    out = aggregate_features(frames)
    assert out["qmt_mm"] is None
    assert out["qmt_provenance"] is None
    assert out["qmt_n_tier3"] == 1

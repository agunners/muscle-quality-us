"""Build-Step 2 Phase 3 item 10a — the QA read-surface logic.

`labeling_app/assessment_pages/qa_surfacing.py` (docs/qa_ui_surfacing_plan.md
§ C / § D): the pure presentation logic behind the Phase-5 QA surfaces —

  * Tier 0 — `qa_badge` / `aria_label`: the verdict glyph + screen-reader text.
  * Tier 1 — `run_what_changed`: the "what the QA layer changed" drilldown.
  * Tier 3 — `frame_qa_summary`: the per-frame raw-record view.
  * `cohort_caveat`: the cohort-placement panel caveat.

Every function is pure (no Streamlit), so this is an L2 component test.
"""
from __future__ import annotations

from labeling_app.assessment_pages.qa_surfacing import (
    aria_label, cohort_caveat, flag_prose, frame_qa_summary,
    frames_with_changes, per_measurement_attribution, picker_qa_cue,
    pooled_disclosure_counts, qa_badge, run_what_changed,
)
from labeling_app.measurements import IMAGE_QUALITY_FLAG_PRIORITY


# ---------------------------------------------------------------------------
# flag_prose — the operator-prose translation table for detector codes
# ---------------------------------------------------------------------------

def test_flag_prose_translates_known_codes_to_plain_sentences() -> None:
    """Every IMAGE_QUALITY_FLAG_PRIORITY code resolves to a plain operator
    sentence — no raw code, no underscores. (Post 2026-05-22 operator-review
    redesign: the operator sees prose, never raw identifiers.)"""
    for code in IMAGE_QUALITY_FLAG_PRIORITY:
        prose = flag_prose(code)
        assert prose, f"{code} has no prose translation"
        assert "_" not in prose, f"{code} prose still has underscores: {prose!r}"
        assert prose != code, f"{code} fell through to raw — translation missing"


def test_flag_prose_unknown_code_falls_through_to_space_stripped() -> None:
    """An unknown code returns a space-stripped readable form rather than a
    raw identifier — a safety belt so a future detector code does not show
    raw on screen if its translation is forgotten."""
    assert flag_prose("a_new_unknown_code") == "a new unknown code"
    assert flag_prose("") == ""


# ===========================================================================
# A — qa_badge: the Tier-0 verdict badge
# ===========================================================================

def test_qa_badge_renders_each_tier_with_colour_glyph_and_word() -> None:
    """§ 8.0 principle 2 — a tier is never carried by colour alone: every badge
    string carries a Streamlit colour, a glyph, and a word."""
    cases = {
        "valid": ("green", "✓", "valid"),
        "borderline": ("orange", "⚠", "borderline"),
        "invalid": ("red", "✗", "invalid"),
        "ungraded": ("gray", "○", "not graded"),
        "none": ("red", "○", "no usable frame"),
    }
    for tier, (colour, glyph, word) in cases.items():
        badge = qa_badge(tier)
        assert badge.startswith(f":{colour}["), tier
        assert glyph in badge, tier
        assert word in badge, tier


def test_qa_badge_appends_dominant_reason_and_pending_count() -> None:
    """The verdict-reason half of the explainability triple is appended — and
    the dominant reason is rendered as a plain operator sentence, not a raw
    detector code (post 2026-05-22 operator-review redesign — see
    `flag_prose`). A non-zero pending count adds the H1 "+N pending"
    deployed-vs-raw signal."""
    assert qa_badge("invalid", dominant_reason="femur_out_of_frame") == (
        ":red[✗ QA: invalid · the thigh bone is not visible in this frame]")
    assert qa_badge("valid", n_pending=3) == (
        ":green[✓ QA: valid]  :gray[+3 pending]")
    # n_pending = 0 adds nothing; a clean valid badge stays terse.
    assert qa_badge("valid", n_pending=0) == ":green[✓ QA: valid]"


def test_qa_badge_unknown_tier_degrades_to_not_graded() -> None:
    """A read surface never crashes the assessment screen on an unexpected
    tier value — it degrades to the gray `not graded` style."""
    assert qa_badge("nonsense_tier") == ":gray[○ QA: not graded]"


def test_aria_label_is_plain_text_with_no_colour_or_glyph() -> None:
    """The screen-reader alternative carries the same information in plain
    words — no colour markup, no glyph, and the dominant reason is the
    operator-prose translation, not the raw code."""
    label = aria_label("invalid", dominant_reason="femur_out_of_frame", n_pending=2)
    assert "image quality: invalid" in label
    # Prose translation, not raw underscore-stripped code.
    assert "the thigh bone is not visible" in label
    assert "femur_out_of_frame" not in label             # raw code never speaks
    assert "2 additional check(s) still being tuned" in label
    assert ":" not in label.split("image quality")[0]    # no `:red[` markup
    for glyph in ("✓", "⚠", "✗", "○"):
        assert glyph not in label


# ===========================================================================
# B — frame_qa_summary: the Tier-3 per-frame view
# ===========================================================================

def _iq(*, tier="valid", validity=(), pending=(), per_measurement=None,
        error=None) -> dict:
    """A stored `image_quality` dict (the dict form of ImageQualityFeatures)."""
    md: dict = {"_qa_error": error} if error else {}
    return {
        "image_quality_validity_flags": list(validity),
        "image_quality_pending_calibration_flags": list(pending),
        "image_quality_detector_metadata": md,
        "image_quality_tier_per_measurement": dict(per_measurement or {}),
        "image_quality_aggregate_tier": tier,
        "image_quality_contract_version": "v0.2",
    }


def test_frame_qa_summary_partitions_calibrated_and_pending_flags() -> None:
    """The dominant reason is the highest-priority *calibrated* flag — a
    pending flag (`wrong_site` here) never becomes the dominant reason, it is
    carried in `pending_flags`. `image_quality_validity_flags` is § 5.1-ordered
    (`femur_out_of_frame` outranks `wrong_site`)."""
    summary = frame_qa_summary(_iq(
        tier="invalid",
        validity=("femur_out_of_frame", "wrong_site"),
        pending=("wrong_site",)))
    assert summary["tier"] == "invalid"
    assert summary["calibrated_flags"] == ("femur_out_of_frame",)
    assert summary["pending_flags"] == ("wrong_site",)
    assert summary["dominant_reason"] == "femur_out_of_frame"


def test_frame_qa_summary_clean_frame_has_no_dominant_reason() -> None:
    summary = frame_qa_summary(_iq(tier="valid"))
    assert summary["tier"] == "valid"
    assert summary["dominant_reason"] is None
    assert summary["calibrated_flags"] == ()
    assert summary["pending_flags"] == ()


def test_frame_qa_summary_missing_record_is_ungraded_not_valid() -> None:
    """A pre-QA frame (no `image_quality`) reads `ungraded` — never silently
    `valid` (§ 8.6)."""
    for missing in (None, {}):
        summary = frame_qa_summary(missing)
        assert summary["tier"] == "ungraded"
        assert summary["dominant_reason"] is None


def test_frame_qa_summary_surfaces_a_detector_error() -> None:
    """A § 8.6 detector error is carried through for the drilldown."""
    summary = frame_qa_summary(_iq(
        tier="ungraded", error="RuntimeError: synthetic detector failure"))
    assert summary["qa_error"] == "RuntimeError: synthetic detector failure"


# ===========================================================================
# C — run_what_changed: the Tier-1 "what the QA layer changed" drilldown
# ===========================================================================

def _frame(fb: str, per_measurement: dict, *, analysis: bool = True) -> dict:
    """A frame dict as `run_data['frame_features']` carries it."""
    return {
        "frame_base": fb,
        "gates": {"usable": analysis, "use_for_analysis": analysis},
        "image_quality": _iq(per_measurement=per_measurement),
    }


def test_run_what_changed_clean_run_yields_no_rows() -> None:
    """A run that pooled cleanly — every measurement from QA-valid frames, the
    consistency gate inert — has nothing to drill into."""
    agg = {
        "_qa_tier": {m: "valid" for m in
                     ("star_ratio", "full_thickness_q50_mm", "int_cv_within",
                      "rf_thickness_mm", "qmt_mm", "visible_rf_area_mm2",
                      "muscle_fat_ratio")},
        "_rf_consistency": {"active": False, "reason_inert": "too_few_frames",
                            "excluded": []},
        "qmt_provenance": "standard",
    }
    valid_pm = {m: "valid" for m in
                ("star_ratio", "full_thickness_q50_mm", "int_cv_within",
                 "rf_thickness_mm", "qmt_mm", "visible_rf_area_mm2", "muscle_fat_ratio")}
    frames = [_frame(f"S{i}", valid_pm) for i in range(3)]
    assert run_what_changed(agg, frames) == []


def test_run_what_changed_surfaces_an_rf_consistency_exclusion() -> None:
    """The item-9 sweep-outlier gate — when it dropped a frame, a `drop` row
    names the frame, the cut, and the clean pooled median."""
    agg = {
        "_qa_tier": {"rf_thickness_mm": "valid"},
        "_rf_consistency": {
            "active": True, "excluded": ["20260101_000040000"],
            "scores": {"20260101_000040000": 7.4}, "median_mm": 12.0,
            "modified_z_max": 3.5, "trimmed_median_mm": 11.5},
        "qmt_provenance": "standard",
    }
    rows = run_what_changed(agg, [])
    rc = [r for r in rows if r["kind"] == "rf_consistency"]
    assert len(rc) == 1
    assert rc[0]["severity"] == "drop"
    assert "000040" in rc[0]["evidence"] and "Z=7.4" in rc[0]["evidence"]
    assert "11.5" in rc[0]["evidence"]                   # the clean pooled median


def test_run_what_changed_names_a_frame_dropped_from_a_valid_pool() -> None:
    """item-8 QA-tier pooling — a frame graded `invalid` for a measurement is
    dropped from that measurement's pool; the row names it and its tier."""
    agg = {"_qa_tier": {"int_cv_within": "valid"}, "_rf_consistency": {},
           "qmt_provenance": "standard"}
    frames = [
        _frame("S0", {"int_cv_within": "valid"}),
        _frame("S1", {"int_cv_within": "valid"}),
        _frame("S2", {"int_cv_within": "invalid"}),       # dropped
    ]
    rows = [r for r in run_what_changed(agg, frames) if r["kind"] == "pool"]
    assert len(rows) == 1
    assert rows[0]["measurement"] == "int_cv_within"
    assert rows[0]["severity"] == "drop"
    assert "1 frame" in rows[0]["title"]
    assert "S2" in rows[0]["evidence"] or "invalid" in rows[0]["evidence"]


def test_run_what_changed_flags_a_borderline_fallback_pool() -> None:
    """A measurement with no top-quality frame — the pool fell back to
    lower-quality frames — yields a `caveat` row, not a `drop` row."""
    agg = {"_qa_tier": {"rf_thickness_mm": "borderline"}, "_rf_consistency": {},
           "qmt_provenance": "standard"}
    frames = [_frame(f"S{i}", {"rf_thickness_mm": "borderline"}) for i in range(2)]
    rows = [r for r in run_what_changed(agg, frames) if r["kind"] == "pool"]
    assert len(rows) == 1
    assert rows[0]["severity"] == "caveat"
    assert "RF thickness" in rows[0]["title"]
    assert "lower-quality" in rows[0]["title"]


def test_run_what_changed_flags_an_unpoolable_measurement() -> None:
    """Every feeding frame had an image problem for a measurement -> `_qa_tier`
    is `none`, the aggregate value is blank, and a `drop` row says so in
    plain language ("no usable frame")."""
    agg = {"_qa_tier": {"visible_rf_area_mm2": "none"}, "_rf_consistency": {},
           "qmt_provenance": "standard"}
    frames = [_frame(f"S{i}", {"visible_rf_area_mm2": "invalid"}) for i in range(2)]
    rows = [r for r in run_what_changed(agg, frames) if r["kind"] == "pool"]
    assert len(rows) == 1
    assert rows[0]["measurement"] == "visible_rf_area_mm2"
    assert rows[0]["severity"] == "drop"
    assert "no usable frame" in rows[0]["title"]


def test_run_what_changed_surfaces_proxy_qmt_provenance() -> None:
    """A proxy-provenance run — QMT/STAR from a fallback method without the
    thigh-bone anchor — gets a caveat row carrying the per-tier frame counts
    in plain language."""
    agg = {"_qa_tier": {"qmt_mm": "valid"}, "_rf_consistency": {},
           "qmt_provenance": "proxy", "qmt_n_tier1": 0, "qmt_n_tier2": 2,
           "qmt_n_tier3": 1}
    rows = [r for r in run_what_changed(agg, []) if r["kind"] == "qmt_provenance"]
    assert len(rows) == 1
    assert rows[0]["severity"] == "caveat"
    assert "QMT and STAR" in rows[0]["title"]
    assert "thigh-bone" in rows[0]["title"] or "thigh bone" in rows[0]["title"]
    assert "2 fallback" in rows[0]["evidence"]


def test_run_what_changed_surfaces_not_measurable_qmt() -> None:
    """No femur frame in any tier -> `qmt_provenance` is None and a `drop` row
    says QMT/STAR is not measurable."""
    agg = {"_qa_tier": {"qmt_mm": "none"}, "_rf_consistency": {},
           "qmt_provenance": None, "qmt_n_tier1": 0, "qmt_n_tier2": 0,
           "qmt_n_tier3": 3}
    rows = [r for r in run_what_changed(agg, []) if r["kind"] == "qmt_provenance"]
    assert len(rows) == 1
    assert rows[0]["severity"] == "drop"
    assert "not measurable" in rows[0]["title"]


def test_run_what_changed_groups_qmt_and_star_when_they_move_together() -> None:
    """QMT and STAR move together (STAR = QMT/BMI). When their pool tiers
    match, run_what_changed emits ONE row labelled "QMT and STAR" rather
    than two duplicate rows (operator review point 4a)."""
    agg = {"_qa_tier": {"qmt_mm": "none", "star_ratio": "none"},
           "_rf_consistency": {}, "qmt_provenance": "standard"}
    frames = [_frame(f"S{i}", {"qmt_mm": "invalid", "star_ratio": "invalid"})
              for i in range(2)]
    rows = [r for r in run_what_changed(agg, frames) if r["kind"] == "pool"]
    assert len(rows) == 1, "must be one combined row, not two"
    assert "QMT and STAR" in rows[0]["title"]
    assert "no usable frame" in rows[0]["title"]


def test_run_what_changed_separates_qmt_and_star_when_tiers_differ() -> None:
    """If QMT and STAR somehow have different pool tiers (unusual but
    possible), they get separate rows — the grouping is conditional on a
    match, not blind."""
    agg = {"_qa_tier": {"qmt_mm": "borderline", "star_ratio": "valid"},
           "_rf_consistency": {}, "qmt_provenance": "standard"}
    frames = [_frame(f"S{i}", {"qmt_mm": "borderline", "star_ratio": "valid"})
              for i in range(2)]
    rows = [r for r in run_what_changed(agg, frames) if r["kind"] == "pool"]
    # only qmt_mm yields a row (star_ratio is valid with no drops -> clean)
    assert len(rows) == 1
    assert rows[0]["measurement"] == "qmt_mm"
    assert "QMT and STAR" not in rows[0]["title"]   # solo, not grouped


def test_run_what_changed_ignores_non_analysis_frames() -> None:
    """A frame gated `use_for_analysis = False` never fed the aggregate, so it
    is not counted as a pool exclusion."""
    agg = {"_qa_tier": {"int_cv_within": "valid"}, "_rf_consistency": {},
           "qmt_provenance": "standard"}
    frames = [
        _frame("S0", {"int_cv_within": "valid"}),
        _frame("S1", {"int_cv_within": "invalid"}, analysis=False),  # not fed
    ]
    rows = [r for r in run_what_changed(agg, frames) if r["kind"] == "pool"]
    assert rows == []                                    # S1 was never in the pool


# ===========================================================================
# D — cohort_caveat
# ===========================================================================

def test_cohort_caveat_fires_on_a_softened_measurement() -> None:
    caveat = cohort_caveat({"_qa_tier": {"star_ratio": "valid",
                                         "rf_thickness_mm": "borderline"}})
    assert caveat is not None
    assert "RF thickness" in caveat


def test_cohort_caveat_silent_on_a_clean_run() -> None:
    assert cohort_caveat({"_qa_tier": {"star_ratio": "valid",
                                       "rf_thickness_mm": "valid"}}) is None
    assert cohort_caveat({}) is None


# ===========================================================================
# E — frames_with_changes: the merged Image-quality panel data model
# ===========================================================================

def test_frames_with_changes_clean_run_no_setasides_no_run_level() -> None:
    """A clean run — every frame valid for every measurement — produces no
    set-asides, no run-level callouts. The panel will render "all frames
    used" silently."""
    agg = {"_qa_tier": {m: "valid" for m in
                        ("star_ratio", "full_thickness_q50_mm", "int_cv_within",
                         "rf_thickness_mm", "qmt_mm", "visible_rf_area_mm2",
                         "muscle_fat_ratio")},
           "_rf_consistency": {"active": False, "reason_inert": "too_few_frames",
                               "excluded": []},
           "qmt_provenance": "standard"}
    pm = {m: "valid" for m in
          ("rf_thickness_mm", "qmt_mm", "star_ratio", "full_thickness_q50_mm",
           "visible_rf_area_mm2", "int_cv_within", "rf_echo", "muscle_fat_ratio")}
    frames = [_frame(f"S{i}", pm) for i in range(3)]
    out = frames_with_changes(agg, frames)
    assert out["n_total"] == 3
    assert out["n_set_aside"] == 0
    assert out["run_level"] == []
    assert all(f["affected_measurements"] == [] for f in out["frames"])
    assert all(not f["is_outlier"] for f in out["frames"])


def test_frames_with_changes_pins_pool_exclusions_to_the_responsible_frame() -> None:
    """A frame invalid for a measurement is pinned with that measurement's
    label in `affected_measurements` — it is the row that should carry the
    "Did not count toward..." line."""
    agg = {"_qa_tier": {"int_cv_within": "valid", "visible_rf_area_mm2": "valid"},
           "_rf_consistency": {}, "qmt_provenance": "standard"}
    frames = [
        _frame("S0", {"int_cv_within": "valid", "visible_rf_area_mm2": "valid"}),
        _frame("S1", {"int_cv_within": "invalid", "visible_rf_area_mm2": "valid"}),
    ]
    out = frames_with_changes(agg, frames)
    s1 = next(f for f in out["frames"] if f["frame_base"] == "S1")
    s0 = next(f for f in out["frames"] if f["frame_base"] == "S0")
    assert "Fascicular contrast" in s1["affected_measurements"]   # int_cv_within label
    assert s0["affected_measurements"] == []                      # clean
    assert out["n_set_aside"] == 1


def test_frames_with_changes_groups_star_and_qmt_in_affected_list() -> None:
    """A frame excluded from both QMT and STAR pools shows one combined
    "STAR and QMT" label, not two duplicate labels (subagent B point 4a)."""
    agg = {"_qa_tier": {"qmt_mm": "valid", "star_ratio": "valid"},
           "_rf_consistency": {}, "qmt_provenance": "standard"}
    frames = [_frame("F", {"qmt_mm": "invalid", "star_ratio": "invalid"})]
    out = frames_with_changes(agg, frames)
    assert out["frames"][0]["affected_measurements"] == ["STAR and QMT"]


def test_frames_with_changes_marks_sweep_outlier_and_pins_rf_thickness() -> None:
    """A frame that the rf_consistency gate excluded carries `is_outlier=True`
    and "RF thickness" in `affected_measurements` (it did not count toward
    RF thickness)."""
    agg = {"_qa_tier": {"rf_thickness_mm": "valid"},
           "_rf_consistency": {"active": True, "excluded": ["S2"],
                               "scores": {"S2": 7.4}, "median_mm": 12.0,
                               "trimmed_median_mm": 11.5, "modified_z_max": 3.5},
           "qmt_provenance": "standard"}
    frames = [_frame(f"S{i}", {"rf_thickness_mm": "valid"}) for i in range(5)]
    out = frames_with_changes(agg, frames)
    s2 = next(f for f in out["frames"] if f["frame_base"] == "S2")
    assert s2["is_outlier"] is True
    assert "RF thickness — at the landmark cross-section" in s2["affected_measurements"]


def test_frames_with_changes_run_level_keeps_only_run_wide_callouts() -> None:
    """Pool-borderline / pool-none / qmt-provenance rows are run-wide and go
    into `run_level`. Per-frame drops from a `valid` pool are pinned to the
    per-frame entries (NOT in `run_level`)."""
    agg = {"_qa_tier": {"rf_thickness_mm": "borderline",   # run-wide
                        "int_cv_within": "valid"},          # per-frame drop
           "_rf_consistency": {"active": True, "excluded": ["S0"],
                               "scores": {"S0": 7.0}, "median_mm": 12.0,
                               "trimmed_median_mm": 11.5, "modified_z_max": 3.5},
           "qmt_provenance": "proxy",
           "qmt_n_tier1": 0, "qmt_n_tier2": 2, "qmt_n_tier3": 1}
    frames = [
        _frame("S0", {"rf_thickness_mm": "borderline", "int_cv_within": "invalid"}),
        _frame("S1", {"rf_thickness_mm": "borderline", "int_cv_within": "valid"}),
    ]
    out = frames_with_changes(agg, frames)
    kinds = {r["kind"] for r in out["run_level"]}
    # qmt_provenance + the rf_thickness_mm borderline pool row are run-wide.
    assert "qmt_provenance" in kinds
    assert any(r["kind"] == "pool" and r.get("measurement") == "rf_thickness_mm"
               for r in out["run_level"])
    # The S0 frame's int_cv_within drop pins to S0, NOT into run_level.
    assert not any(r.get("measurement") == "int_cv_within"
                   for r in out["run_level"])
    s0 = next(f for f in out["frames"] if f["frame_base"] == "S0")
    assert "Fascicular contrast" in s0["affected_measurements"]


# ===========================================================================
# F — picker_qa_cue: the per-thumbnail picker cue (chunk 4 — Q1)
# ===========================================================================

def test_picker_qa_cue_silent_on_valid_frame() -> None:
    """A clean frame produces NO cue — silence is the "frame looks fine"
    signal (consistent with the chunk-3 quiet-or-loud principle for the
    per-card badge)."""
    assert picker_qa_cue(None) is None
    assert picker_qa_cue({}) is None
    assert picker_qa_cue({
        "image_quality_aggregate_tier": "valid",
        "image_quality_validity_flags": [],
        "image_quality_pending_calibration_flags": [],
    }) is None


def test_picker_qa_cue_orange_borderline_with_prose() -> None:
    cue = picker_qa_cue({
        "image_quality_aggregate_tier": "borderline",
        "image_quality_validity_flags": ["femur_off_central_zone"],
        "image_quality_pending_calibration_flags": [],
    })
    assert cue is not None
    assert cue.startswith(":orange[")
    # plain operator sentence — no raw code
    assert "off to one side" in cue
    assert "femur_off_central_zone" not in cue


def test_picker_qa_cue_red_invalid_with_prose() -> None:
    cue = picker_qa_cue({
        "image_quality_aggregate_tier": "invalid",
        "image_quality_validity_flags": ["femur_out_of_frame"],
        "image_quality_pending_calibration_flags": [],
    })
    assert cue is not None
    assert cue.startswith(":red[")
    assert "thigh bone is not visible" in cue


def test_picker_qa_cue_ignores_pending_only_flags() -> None:
    """Subagent B's discipline: at the picker we use calibrated detectors
    only. A frame whose only fired flag is pending (wrong_site is the
    canonical case — 80% of cohort) gets NO cue."""
    cue = picker_qa_cue({
        "image_quality_aggregate_tier": "valid",  # pending didn't drive verdict
        "image_quality_validity_flags": ["wrong_site"],
        "image_quality_pending_calibration_flags": ["wrong_site"],
    })
    assert cue is None


def test_picker_qa_cue_silent_when_no_calibrated_flag_drove_a_non_valid_verdict() -> None:
    """If the verdict is non-valid but no calibrated flag is to blame
    (e.g. QMT provenance floor capping qmt_mm via a `proxy` provenance
    rather than a calibrated detector), the cue stays silent — an honest
    cue needs a named calibrated reason."""
    cue = picker_qa_cue({
        "image_quality_aggregate_tier": "borderline",
        "image_quality_validity_flags": [],
        "image_quality_pending_calibration_flags": [],
    })
    assert cue is None


def test_picker_qa_cue_suppresses_femur_out_of_frame_when_caller_asks() -> None:
    """Akadir 2026-05-25 v2 § 1 — the picker's tier-3 dot already says "no
    femur"; surfacing `femur_out_of_frame`'s prose ("the thigh bone is not
    visible in this frame") via the cue duplicates the same fact on one
    thumbnail. When the call site asks to suppress that flag, the cue stays
    silent IF femur_out_of_frame was the only actionable driver."""
    cue = picker_qa_cue(
        {
            "image_quality_aggregate_tier": "invalid",
            "image_quality_validity_flags": ["femur_out_of_frame"],
            "image_quality_pending_calibration_flags": [],
        },
        suppress_flags=("femur_out_of_frame",),
    )
    assert cue is None


def test_picker_qa_cue_still_fires_on_other_flag_when_femur_absent_suppressed() -> None:
    """The suppress is per-flag, not blanket. A tier-3 frame with a SECOND
    calibrated flag (e.g. `femur_off_central_zone` — different prose, useful
    info) keeps the cue alive on that other flag even with femur_out_of_frame
    suppressed."""
    cue = picker_qa_cue(
        {
            "image_quality_aggregate_tier": "borderline",
            "image_quality_validity_flags": [
                "femur_out_of_frame", "femur_off_central_zone",
            ],
            "image_quality_pending_calibration_flags": [],
        },
        suppress_flags=("femur_out_of_frame",),
    )
    assert cue is not None
    assert "off to one side" in cue


def test_frames_with_changes_translates_dominant_reason_to_prose() -> None:
    """Frame entries carry operator-prose strings, never raw detector codes."""
    iq_with_flag = {
        "image_quality_validity_flags": ["femur_out_of_frame"],
        "image_quality_pending_calibration_flags": [],
        "image_quality_detector_metadata": {
            "femur_out_of_frame": {"pending_calibration": False}},
        "image_quality_tier_per_measurement": {"qmt_mm": "invalid",
                                                "star_ratio": "invalid"},
        "image_quality_aggregate_tier": "invalid",
        "image_quality_contract_version": "v0.2",
    }
    frames = [{
        "frame_base": "F", "is_anchor": True,
        "gates": {"usable": True, "use_for_analysis": True},
        "image_quality": iq_with_flag,
    }]
    agg = {"_qa_tier": {"qmt_mm": "valid", "star_ratio": "valid"},
           "_rf_consistency": {}, "qmt_provenance": "standard"}
    out = frames_with_changes(agg, frames)
    f = out["frames"][0]
    assert f["dominant_reason_prose"] == "the thigh bone is not visible in this frame"
    assert "the thigh bone is not visible in this frame" in f["calibrated_flags_prose"]
    assert "femur_out_of_frame" not in (f["dominant_reason_prose"] or "")


# ===========================================================================
# F — per_measurement_attribution: the v2 reframe fix for misattribution
# ===========================================================================
# v1 paired one dominant-flag prose with the union of pooled-out measurements,
# reading as "off-side caused all four exclusions". v2 attributes per-
# measurement — each measurement names the calibrated flag(s) that drove ITS
# tier, not the union (Akadir 2026-05-24 § 5b).

def _iq_with_flags(flags: tuple[str, ...], *, pending: tuple[str, ...] = ()) -> dict:
    """A minimal image_quality dict built from a flag set. Builds the tier
    map from the actual gating table so the test stays in sync with the
    canonical _GATING_TABLE."""
    from labeling_app.image_quality import image_quality_tier_per_measurement
    return {
        "image_quality_validity_flags": list(flags),
        "image_quality_pending_calibration_flags": list(pending),
        "image_quality_detector_metadata": {},
        "image_quality_tier_per_measurement":
            image_quality_tier_per_measurement(
                tuple(f for f in flags if f not in set(pending))),
        "image_quality_aggregate_tier": "borderline",
        "image_quality_contract_version": "v0.2",
    }


def test_per_measurement_attribution_edge_clip_lateral_is_reserve() -> None:
    """Akadir 2026-05-24 — edge_clip_lateral is RESERVE STATE: fires +
    goes on the record, but taints NO measurement. The per-measurement
    attribution carries no driver for edge_clip_lateral; every measurement
    stays valid when edge_clip_lateral is the only flag."""
    rows = per_measurement_attribution(_iq_with_flags(("edge_clip_lateral",)))
    by_m = {r["measurement"]: r for r in rows}
    for m, row in by_m.items():
        assert row["tier"] == "valid", f"{m} should be valid (edge_clip is reserve)"
        assert row["drivers"] == [], f"{m} should have no drivers"


def test_per_measurement_attribution_multiple_flags_attribute_per_measurement() -> None:
    """A frame with TWO flags: femur_out_of_frame (taints qmt_mm + star_ratio)
    + motion_blur (taints visible_rf_area_mm2 INV and the headlines INV).
    Each measurement gets only its OWN drivers — no leakage. The v1→v2 fix:
    per-measurement attribution, not union-of-everything misattribution.
    (Pre-Akadir-2026-05-24 this test used edge_clip_lateral; that flag is
    reserve now, so motion_blur is used instead to exercise the multi-flag
    path.)"""
    rows = per_measurement_attribution(
        _iq_with_flags(("femur_out_of_frame", "motion_blur")))
    by_m = {r["measurement"]: r for r in rows}
    # qmt + star carry both femur_out_of_frame AND motion_blur (both taint).
    for m in ("qmt_mm", "star_ratio"):
        flags = [d["flag"] for d in by_m[m]["drivers"]]
        assert "femur_out_of_frame" in flags, f"{m} drivers: {flags}"
        assert "motion_blur" in flags, f"{m} drivers: {flags}"
    # rf_thickness carries motion_blur only (femur_out_of_frame does not
    # taint rf_thickness — it's femur-anchored only).
    flags = [d["flag"] for d in by_m["rf_thickness_mm"]["drivers"]]
    assert flags == ["motion_blur"], f"rf_thickness drivers: {flags}"


def test_per_measurement_attribution_pending_flags_do_not_drive_tiers() -> None:
    """A pending (un-calibrated) flag — Akadir 2026-05-24 Finding B —
    does NOT drive tier and does NOT appear as a driver. The verdict is
    computed over the calibrated subset only; pending lives in its own
    surface."""
    # wrong_site fires + is in the pending set. motion_blur fires +
    # calibrated. visible_rf_area_mm2 should be invalid via motion_blur
    # only (not via wrong_site).
    iq = _iq_with_flags(("wrong_site", "motion_blur"), pending=("wrong_site",))
    rows = per_measurement_attribution(iq)
    by_m = {r["measurement"]: r for r in rows}
    # No measurement names pending wrong_site as a driver.
    for m in ("qmt_mm", "star_ratio", "rf_thickness_mm", "visible_rf_area_mm2"):
        flags = [d["flag"] for d in by_m[m]["drivers"]]
        assert "wrong_site" not in flags, f"{m} should not name pending wrong_site"
    # visible_rf_area_mm2 carries motion_blur as the calibrated driver.
    flags = [d["flag"] for d in by_m["visible_rf_area_mm2"]["drivers"]]
    assert "motion_blur" in flags


def test_per_measurement_attribution_ungraded_returns_rows_with_no_drivers() -> None:
    """A pre-QA / missing image_quality returns one row per measurement, all
    tier `ungraded`, all drivers empty."""
    rows = per_measurement_attribution(None)
    assert len(rows) >= 6   # the 6 _DISPLAY_MEASUREMENTS (muscle_fat_ratio demoted)
    assert all(r["tier"] == "ungraded" for r in rows)
    assert all(r["drivers"] == [] for r in rows)


def test_per_measurement_attribution_clean_frame_all_valid_no_drivers() -> None:
    """No flags fired → every measurement valid, no drivers."""
    rows = per_measurement_attribution(_iq_with_flags(()))
    assert all(r["tier"] == "valid" for r in rows)
    assert all(r["drivers"] == [] for r in rows)


def test_per_measurement_attribution_prose_is_plain_not_a_code() -> None:
    """Drivers carry the operator-prose (`flag_prose`) sentence, not the raw
    flag code. The panel renders that — never the raw underscore name.
    (Edge_clip_lateral was the example pre-Akadir-2026-05-24; now reserve
    state, so the example switches to motion_blur — a flag that genuinely
    taints headlines.)"""
    rows = per_measurement_attribution(_iq_with_flags(("motion_blur",)))
    by_m = {r["measurement"]: r for r in rows}
    drv = by_m["rf_thickness_mm"]["drivers"][0]
    assert "_" not in drv["prose"]                            # no snake_case
    assert "motion_blur" not in drv["prose"].lower()          # no raw code
    assert "blur" in drv["prose"].lower() or "motion" in drv["prose"].lower()


# ===========================================================================
# Slice B — pooled_disclosure_counts: the (N, M, K) for the p05 receipt
# ===========================================================================

_ALL_M = ("star_ratio", "full_thickness_q50_mm", "int_cv_within",
          "rf_thickness_mm", "qmt_mm", "visible_rf_area_mm2", "muscle_fat_ratio")
_ALL_VALID = {m: "valid" for m in _ALL_M}


def test_pooled_counts_clean_run_is_M_of_M_zero_set_aside() -> None:
    """A fully-clean run: every analysis frame fed every measurement, so
    N == M and K == 0 (and the disclosure renders nothing — no run_what_changed
    rows)."""
    agg = {"_qa_tier": dict(_ALL_VALID), "_rf_consistency": {"excluded": []},
           "qmt_provenance": "standard"}
    frames = [_frame(f"S{i}", dict(_ALL_VALID)) for i in range(9)]
    n, m, k = pooled_disclosure_counts(agg, frames)
    assert (n, m, k) == (9, 9, 0)
    assert n + k == m
    assert run_what_changed(agg, frames) == []          # quiet-on-clean


def test_pooled_counts_one_rf_outlier_is_M_minus_1() -> None:
    """One frame_rf_consistency outlier -> "M-1 of M ... 1 set aside" (the
    plan's Slice-B acceptance example)."""
    agg = {"_qa_tier": {"rf_thickness_mm": "valid"},
           "_rf_consistency": {"active": True, "excluded": ["S2"],
                               "scores": {"S2": 4.1}, "median_mm": 12.4,
                               "trimmed_median_mm": 12.1},
           "qmt_provenance": "standard"}
    frames = [_frame(f"S{i}", {"rf_thickness_mm": "valid"}) for i in range(9)]
    n, m, k = pooled_disclosure_counts(agg, frames)
    assert (n, m, k) == (8, 9, 1)
    assert n + k == m


def test_pooled_counts_invalid_frame_dropped_from_one_measurement() -> None:
    """A frame graded invalid for one measurement is set aside from it -> K=1."""
    agg = {"_qa_tier": {"int_cv_within": "valid"}, "_rf_consistency": {"excluded": []},
           "qmt_provenance": "standard"}
    frames = [_frame("S0", {"int_cv_within": "valid"}),
              _frame("S1", {"int_cv_within": "valid"}),
              _frame("S2", {"int_cv_within": "invalid"})]
    n, m, k = pooled_disclosure_counts(agg, frames)
    assert (n, m, k) == (2, 3, 1)


def test_pooled_counts_borderline_pool_sets_nothing_aside() -> None:
    """A borderline pool softens the number but sets aside no frame (every
    frame is in the borderline bucket) -> K=0, N=M; the disclosure still
    renders (run_what_changed emits the caveat row), exercising the k==0 path."""
    agg = {"_qa_tier": {"rf_thickness_mm": "borderline"},
           "_rf_consistency": {"excluded": []}, "qmt_provenance": "standard"}
    frames = [_frame(f"S{i}", {"rf_thickness_mm": "borderline"}) for i in range(4)]
    n, m, k = pooled_disclosure_counts(agg, frames)
    assert (n, m, k) == (4, 4, 0)
    assert run_what_changed(agg, frames)                # not quiet — a caveat exists


# ===========================================================================
# Slice (a) — picker dark_clip allow-set (informational pick-time steer)
# ===========================================================================

def test_picker_qa_cue_allow_pending_surfaces_dark_clip_informationally() -> None:
    """A dark frame: dark_clip_high is PENDING so it doesn't move the aggregate
    tier (stays 'valid'). Default allow_pending=() -> silent (calibrated-only,
    back-compat). Opting it in -> an informational orange steer in plain prose."""
    iq = {
        "image_quality_aggregate_tier": "valid",
        "image_quality_validity_flags": ["dark_clip_high"],
        "image_quality_pending_calibration_flags": ["dark_clip_high"],
    }
    assert picker_qa_cue(iq) is None                          # default = silent
    cue = picker_qa_cue(iq, allow_pending=("dark_clip_high",))
    assert cue is not None and cue.startswith(":orange[⚠")
    assert "dark_clip_high" not in cue and "_" not in cue     # prose, not raw code


def test_picker_qa_cue_opted_in_pending_surfaces_alongside_calibrated() -> None:
    """Akadir 2026-06-01 — if dark_clip is opted in and fires, it is NOT
    suppressed by a louder calibrated verdict: both surface (the calibrated
    verdict leads, the opted-in steer follows on its own line)."""
    iq = {
        "image_quality_aggregate_tier": "invalid",
        "image_quality_validity_flags": ["wrong_site", "dark_clip_high"],
        "image_quality_pending_calibration_flags": ["dark_clip_high"],
    }
    cue = picker_qa_cue(iq, allow_pending=("dark_clip_high",))
    assert cue.startswith(":red[✗")                  # calibrated verdict leads
    assert ":orange[⚠" in cue                        # opted-in dark_clip ALSO shows
    # Without the opt-in, only the calibrated cue (back-compat preserved).
    assert picker_qa_cue(iq) == cue.split("\n\n")[0]


# ===========================================================================
# Slice (b) — frame_checks: the p04 "what the app checked" inventory
# ===========================================================================

def test_frame_checks_partitions_fired_and_passed() -> None:
    """Only the surfaceable detectors appear; fired = raised, passed = ran
    silent. Reserve/NaN-belted detectors (edge_clip_lateral, etc.) never show."""
    from labeling_app.assessment_pages.qa_surfacing import frame_checks
    out = frame_checks({"image_quality_validity_flags":
                        ["dark_clip_high", "wrong_site", "edge_clip_lateral"]})
    fired = {c["detector"] for c in out["fired"]}
    assert fired == {"dark_clip_high", "wrong_site"}        # surfaceable + fired
    assert "edge_clip_lateral" not in fired                 # reserve — never surfaced
    assert len(out["passed"]) == 3                          # femur x2 + edge_clip_vertical
    assert all("_" not in p for p in out["passed"])         # prose, not raw codes
    assert all("_" not in c["prose"] for c in out["fired"])


def test_frame_checks_quiet_on_clean_frame() -> None:
    from labeling_app.assessment_pages.qa_surfacing import frame_checks
    out = frame_checks({"image_quality_validity_flags": []})
    assert out["fired"] == []
    assert len(out["passed"]) == 5                          # all surfaceable, all silent


# ===========================================================================
# (b) drop-reason grouping — partition guard
# ===========================================================================

def test_drop_reason_groups_partition_the_taxonomy() -> None:
    """The grouped (detector-backed) codes + the operator-only codes partition
    IMAGE_QUALITY_FLAG_PRIORITY exactly — no code dropped, none double-listed,
    every label is plain prose (no raw code reaches the operator)."""
    from labeling_app.assessment_pages.qa_surfacing import (
        DROP_REASON_GROUPS, OPERATOR_ONLY_DROP_REASONS, flag_prose)
    grouped = [c for _, codes in DROP_REASON_GROUPS for c in codes]
    op_only = list(OPERATOR_ONLY_DROP_REASONS)
    assert len(grouped) == len(set(grouped))                       # no dup
    assert set(grouped).isdisjoint(op_only)                        # disjoint
    assert set(grouped) | set(op_only) == set(IMAGE_QUALITY_FLAG_PRIORITY)  # partition
    assert len(grouped) == 15 and len(op_only) == 7
    for code in grouped + op_only:
        assert "_" not in flag_prose(code), f"{code} label not prose"

"""L3a — Streamlit AppTest click-through of the 5-phase assessment workflow.

Drives the app programmatically (no browser) and asserts on session_state +
the element tree. The load-bearing job is the **N4 regression suite**:

  (a) first phase-5 render → run_lifecycle == "draft" AND runs.jsonl has 0 rows
  (b) "Accept run"        → run_lifecycle == "finalized" + exactly 1 ledger row
  (c) stale-cache         → back to phase 3, toggle a pick, return → a *new*
                            run_id and a fresh "draft" (the accepted run stays
                            the only ledger row)
  (d) "Start afresh"      → picks cleared, study + model preserved, phase == 0

The heavy seams (model load, segmenter, pipeline compute) are mocked in
conftest; the persistence path (build_record / append_record / write_draft /
finalize_draft) is the REAL code.
"""
from __future__ import annotations

import json

from preflight_helpers import env


# --- widget helpers --------------------------------------------------------

def _widget(at, kind: str, key: str):
    """Find a widget by key anywhere in the tree (main area + sidebar)."""
    pool = list(getattr(at, kind))
    try:
        pool += list(getattr(at.sidebar, kind))
    except Exception:
        pass
    for el in pool:
        if getattr(el, "key", None) == key:
            return el
    raise KeyError(f"{kind}[{key!r}] not found; keys present: "
                   f"{[getattr(e, 'key', None) for e in pool]}")


def _has_widget(at, kind: str, key: str) -> bool:
    try:
        _widget(at, kind, key)
        return True
    except KeyError:
        return False


def _ledger_rows() -> list[dict]:
    if not env.RUNS_JSONL.exists():
        return []
    return [json.loads(l) for l in env.RUNS_JSONL.read_text().splitlines() if l.strip()]


def _n_drafts() -> int:
    if not env.DRAFTS_DIR.exists():
        return 0
    return len(list(env.DRAFTS_DIR.glob("*.json")))


def _gate_frame(at, fb: str) -> None:
    """Set the 3-gate cascade to usable / perfect / use-for-analysis = Yes."""
    _widget(at, "radio", f"g1_{fb}").set_value("Yes")
    at.run()
    _widget(at, "radio", f"g2_{fb}").set_value("Yes")
    _widget(at, "radio", f"g3_{fb}").set_value("Yes")
    at.run()


def _make_repr(at, fb: str) -> None:
    """Designate `fb` as the representative frame (p04→p05 gate dependency —
    Akadir 2026-05-24 § 1g + § 3a). Click the `make_repr_<fb>` button — the
    canonical operator path. The button click + at.run() rerun cycle is the
    pattern AppTest tracks cleanly."""
    _widget(at, "button", f"make_repr_{fb}").click()
    at.run()


def _walk_to_assessment(at, n_pick: int = 2):
    """Drive phases 0→4: pick a study, load a model, pick + gate frames, land
    on the assessment view. Returns (available_frame_bases, picked_list)."""
    at.run()
    assert at.session_state["phase"] == 0
    assert not at.exception, at.exception

    # Phase 0 → 1: study selector (AGZ855 is the canonical demo study).
    _widget(at, "button", "pick_AGZ855").click()
    at.run()
    assert at.session_state["phase"] == 1

    # Phase 1 → 2: model selector (default bundle; load is mocked).
    _widget(at, "button", "load_model_continue").click()
    at.run()
    assert at.session_state["phase"] == 2
    assert at.session_state["learn"] is not None
    assert at.session_state["bundle"] is not None

    # Phase 2 → 3: pick n_pick frames, then Segment + continue (sidebar).
    frames = list(at.session_state["available_frame_bases"])
    assert len(frames) >= n_pick + 1, "need a spare frame for the stale-cache test"
    picks = frames[:n_pick]
    for fb in picks:
        _widget(at, "button", f"pick_{fb}").click()
        at.run()
    assert sorted(at.session_state["picked_frames"]) == sorted(picks)
    _widget(at, "button", "seg_continue").click()
    at.run()
    assert at.session_state["phase"] == 3

    # Phase 3 → 4: gate every picked frame, designate a repr, then Lock gates.
    # The repr designation moved from p03 to p04 in the v2 reframe (Akadir
    # 2026-05-24 § 1g + § 3a) — p04→p05 navigation requires a valid repr.
    for fb in picks:
        _gate_frame(at, fb)
    _make_repr(at, picks[0])
    _widget(at, "button", "lock_gates").click()
    at.run()
    assert at.session_state["phase"] == 4
    return frames, picks


# --- tests -----------------------------------------------------------------

def test_app_boots_to_study_selector(ui_app) -> None:
    at = ui_app
    at.run()
    assert not at.exception, at.exception
    assert at.session_state["phase"] == 0
    assert len(at.error) == 0
    # The study selector lists the canonical demo studies.
    assert _has_widget(at, "button", "pick_AGZ855")


def test_n4_lifecycle_draft_accept_stale_cache_afresh(ui_app) -> None:
    at = ui_app
    frames, picks = _walk_to_assessment(at, n_pick=2)

    # ---- (a) first phase-5 render is a DRAFT — nothing in the ledger.
    assert at.session_state["run_lifecycle"] == "draft"
    assert at.session_state["run_mode"] == "live"
    assert len(at.error) == 0
    assert _ledger_rows() == []                       # runs.jsonl untouched
    assert _n_drafts() == 1                           # ...the draft is on disk
    draft_run_id = at.session_state["run_result"]["run_id"]
    # The element tree shows the amber draft banner + Accept affordance.
    assert _has_widget(at, "button", "accept_run")
    assert any("Draft run" in w.value for w in at.warning)

    # ---- (b) Accept run → FINALIZED + exactly one ledger row.
    _widget(at, "button", "accept_run").click()
    at.run()
    assert at.session_state["run_lifecycle"] == "finalized"
    rows = _ledger_rows()
    assert len(rows) == 1
    assert rows[0]["run_id"] == draft_run_id
    assert rows[0]["lifecycle"] == "finalized"
    assert _n_drafts() == 0                           # draft promoted + deleted
    assert any("accepted" in w.value.lower() for w in at.success)

    # ---- (c) stale-cache regression: go back, toggle a pick, return.
    _widget(at, "button", "step_2").click()           # stepper → frame picker
    at.run()
    assert at.session_state["phase"] == 2
    spare = frames[2]
    _widget(at, "button", f"pick_{spare}").click()     # add a 3rd frame
    at.run()
    _widget(at, "button", "seg_continue").click()
    at.run()
    assert at.session_state["phase"] == 3
    _gate_frame(at, spare)                            # the new frame needs gates
    _make_repr(at, picks[0])                          # repr cleared on re-pick
    _widget(at, "button", "lock_gates").click()
    at.run()
    assert at.session_state["phase"] == 4
    new_run_id = at.session_state["run_result"]["run_id"]
    assert new_run_id != draft_run_id                 # cache invalidated
    assert at.session_state["run_lifecycle"] == "draft"  # branched a fresh draft
    assert len(_ledger_rows()) == 1                   # accepted run still alone
    assert _n_drafts() == 1                           # ...the new draft

    # ---- (d) Start afresh → picks cleared, study + model kept, phase == 0.
    study_before = at.session_state["study_entry"]
    bundle_before = at.session_state["bundle"]
    _widget(at, "button", "afresh_draft").click()
    at.run()
    assert at.session_state["phase"] == 0      # back to the study selector
    assert at.session_state["picked_frames"] == []
    assert at.session_state["gates"] == {}
    assert at.session_state["run_result"] is None
    assert at.session_state["run_lifecycle"] == "none"
    assert at.session_state["study_entry"] is study_before    # study preserved
    assert at.session_state["bundle"] is bundle_before        # model preserved
    assert _n_drafts() == 0                           # the open draft discarded
    assert len(_ledger_rows()) == 1                   # ledger still intact


def test_n5_study_bootstrap_flow_after_accept(ui_app) -> None:
    """L3a — the bootstrap half (B4): accepting a run makes it the study's
    sole distinct finalized run, the N5 pool re-aggregates it anchorless-
    median, and the F5 'Run comparison + study bootstrap' panel renders it."""
    from preflight_helpers.run_pipeline import (
        HEADLINE_FEATURES, SECONDARY_FEATURES,
    )
    from preflight_helpers.study_bootstrap import study_bootstrap

    at = ui_app
    _walk_to_assessment(at, n_pick=2)

    # Before Accept the run is a draft — drafts never reach the bootstrap pool.
    assert study_bootstrap("AGZ855")["n_distinct_runs"] == 0

    _widget(at, "button", "accept_run").click()
    at.run()
    assert at.session_state["run_lifecycle"] == "finalized"
    run_id = at.session_state["run_result"]["run_id"]

    # The bootstrap now compounds exactly this one distinct finalized run.
    boot = study_bootstrap("AGZ855")
    assert boot["n_distinct_runs"] == 1
    assert boot["n_total_runs"] == 1
    assert boot["contributing_run_ids"] == [run_id]
    assert boot["n_pooled_frames"] == 2                   # 2 analysis frames
    # force_all_median over identical synthetic per-frame values → that value.
    for i, feat in enumerate(HEADLINE_FEATURES + SECONDARY_FEATURES):
        assert boot["aggregate"][feat] == float(i + 1), feat
    assert boot["aggregate"]["_force_all_median"] is True

    # The F5 panel renders the bootstrap (AppTest executes the expander body).
    assert any("Study bootstrap" in m.value for m in at.markdown)
    assert not at.exception, at.exception


def test_qa_read_surfaces_render_on_assessment(ui_app) -> None:
    """Akadir 2026-05-24 v2 reframe Layer 2c — p05 is the trusted summary;
    per-frame QA detail has moved to **review (phase 4)** (commit 87eb83d
    added per-measurement attribution + layer-stack heatmap there). The
    only QA surfaces remaining on p05 are:
      - the per-card Tier-0 badge (quiet-or-loud);
      - the cohort-placement caveat (a one-liner under the box plot);
      - the per-card "see review" attribution embedded in the degraded badge.
    The synthetic run grades rf_thickness_mm `borderline` and fires the
    un-calibrated `wrong_site` (the H1 pending set), so the rf_thickness
    card carries the warning. The per-frame Image-quality panel + the
    "Possible issues — pending detectors" block are NOT rendered on p05
    any more (the layer-2c strip)."""
    at = ui_app
    _walk_to_assessment(at, n_pick=2)
    assert not at.exception, at.exception
    assert len(at.error) == 0

    # All rendered text — markdown + caption + warning + error (different
    # Streamlit element types land in different `at.*` buckets).
    texts = [m.value for m in at.markdown]
    for kind in ("caption", "warning", "error", "info"):
        try:
            texts += [e.value for e in getattr(at, kind)]
        except Exception:
            pass
    blob = "\n".join(texts)

    # Per-card QA badge — quiet-or-loud: silent on a clean card, a boxed
    # st.warning on a degraded one. In the mock rf_thickness_mm is
    # borderline, so its card carries the warning, which now points the
    # operator to review (phase 4) — not to a panel below this page.
    assert "based on lower-quality frames" in blob
    assert "review (phase 4)" in blob.lower() or "phase 4" in blob.lower()

    # The cohort-placement caveat fires for the softened measurement.
    assert "Image-quality note" in blob and "RF thickness" in blob

    # The per-frame Image-quality panel is GONE on p05 — its content moved
    # to p04. Assert the layer-2c removal: the panel's signature run-level
    # callout text + the per-frame "Used in all measurements" caption no
    # longer appear on the summary page.
    assert "RF thickness — uses lower-quality frames" not in blob
    assert "Used in all measurements" not in blob

    # The "Possible issues — pending detectors" block is also GONE on p05.
    # Pending detectors now live on p04 with operator agree/disagree
    # affordance (Akadir 2026-05-24 § 6 — they belong with the data, not
    # on the result page).
    assert "this may not be the right muscle" not in blob
    assert "Possible issues" not in blob
    # The raw detector identifier is still never rendered (Akadir-ratified
    # operator-prose discipline).
    assert "wrong_site" not in blob


def test_drop_frame_captures_structured_reason_codes(ui_app) -> None:
    """Build-Step 1 — dropping a frame renders the structured reason-code
    checkbox group (replacing the old free-text box), the checked code lands
    in `st.session_state.gates`, and it is carried through into the persisted
    run record. Verifies the *purpose* of the structured-capture refactor —
    capture -> gates -> record — not just the click."""
    at = ui_app
    at.run()
    _widget(at, "button", "pick_AGZ855").click()
    at.run()
    _widget(at, "button", "load_model_continue").click()
    at.run()
    frames = list(at.session_state["available_frame_bases"])
    keep, drop = frames[0], frames[1]
    for fb in (keep, drop):
        _widget(at, "button", f"pick_{fb}").click()
        at.run()
    _widget(at, "button", "seg_continue").click()
    at.run()
    assert at.session_state["phase"] == 3                  # frame review

    # Keep one frame (all gates Yes) so "Lock gates" stays enabled.
    _widget(at, "radio", f"g1_{keep}").set_value("Yes")
    at.run()
    _widget(at, "radio", f"g2_{keep}").set_value("Yes")
    _widget(at, "radio", f"g3_{keep}").set_value("Yes")
    at.run()

    # Drop the other frame: usable = No → the structured checkbox group
    # appears, and the old free-text "dr_" box is gone.
    _widget(at, "radio", f"g1_{drop}").set_value("No")
    at.run()
    assert not _has_widget(at, "text_input", f"dr_{drop}")
    assert _has_widget(at, "checkbox", f"drc_{drop}__motion_blur")
    # Build-Step 1 enforcement: dropped with no reason yet → the lock is
    # blocked (warning shown, "Lock gates" disabled).
    assert any("Finish gating" in w.value for w in at.warning)
    assert _widget(at, "button", "lock_gates").proto.disabled
    _widget(at, "checkbox", f"drc_{drop}__motion_blur").set_value(True)
    at.run()

    # The checked code is captured into the session gates dict (the UI half),
    # and recording a reason clears the lock block. Designate a repr (the
    # kept frame) — Akadir 2026-05-24 v2 reframe added the repr gate.
    assert "motion_blur" in at.session_state["gates"][drop]["drop_reason_codes"]
    assert not any("Finish gating" in w.value for w in at.warning)
    _make_repr(at, keep)
    assert not _widget(at, "button", "lock_gates").proto.disabled

    # Lock gates → the assessment view builds a draft run record via the real
    # bundle_writer path. Reading that draft proves the structured codes
    # survive capture -> GateAnswers -> persisted record (the data half).
    _widget(at, "button", "lock_gates").click()
    at.run()
    assert at.session_state["phase"] == 4
    drafts = list(env.DRAFTS_DIR.glob("*.json"))
    assert len(drafts) == 1
    record = json.loads(drafts[0].read_text())
    gates_by_frame = {sf["frame_base"]: sf["gates"]
                      for sf in record["selected_frames"]}
    # The dropped frame carries the structured code; the kept frame is clean.
    assert gates_by_frame[drop]["usable"] is False
    assert "motion_blur" in gates_by_frame[drop]["drop_reason_codes"]
    assert gates_by_frame[keep]["usable"] is True
    assert gates_by_frame[keep]["drop_reason_codes"] == []


def test_lock_blocked_until_every_frame_decided(ui_app) -> None:
    """Build-Step 1 — a picked frame left on the "—" pending state blocks
    "Lock gates" (no stale, un-made decisions); deciding it clears the block."""
    at = ui_app
    at.run()
    _widget(at, "button", "pick_AGZ855").click()
    at.run()
    _widget(at, "button", "load_model_continue").click()
    at.run()
    frames = list(at.session_state["available_frame_bases"])
    a, b = frames[0], frames[1]
    for fb in (a, b):
        _widget(at, "button", f"pick_{fb}").click()
        at.run()
    _widget(at, "button", "seg_continue").click()
    at.run()
    assert at.session_state["phase"] == 3

    # Gate frame A fully (all three gates Yes); leave frame B untouched —
    # its gate 1 is still on the "—" pending state.
    _widget(at, "radio", f"g1_{a}").set_value("Yes")
    at.run()
    _widget(at, "radio", f"g2_{a}").set_value("Yes")
    _widget(at, "radio", f"g3_{a}").set_value("Yes")
    at.run()
    # B is undecided → the run cannot be locked.
    assert any("Finish gating" in w.value for w in at.warning)
    assert _widget(at, "button", "lock_gates").proto.disabled

    # Decide B too → the gate-completeness block clears. Designate a repr
    # (Akadir 2026-05-24 — p04→p05 gate added by the v2 reframe).
    _widget(at, "radio", f"g1_{b}").set_value("Yes")
    at.run()
    _widget(at, "radio", f"g2_{b}").set_value("Yes")
    _widget(at, "radio", f"g3_{b}").set_value("Yes")
    at.run()
    _make_repr(at, a)
    assert not any("Finish gating" in w.value for w in at.warning)
    assert not _widget(at, "button", "lock_gates").proto.disabled


def test_make_repr_auto_couples_usable_and_use_for_analysis(ui_app) -> None:
    """Akadir 2026-05-25 § 6 — designating a frame as repr should auto-set
    usable=True AND use_for_analysis=True without the operator having to
    interact with the gate radios. Without this, a frame can be marked as
    repr but flagged use_for_analysis=No, which is incoherent.
    Segmentation perfect stays independent — it's a quality judgment
    unrelated to repr-ness."""
    at = ui_app
    at.run()
    _widget(at, "button", "pick_AGZ855").click()
    at.run()
    _widget(at, "button", "load_model_continue").click()
    at.run()
    frames = list(at.session_state["available_frame_bases"])
    a, b = frames[0], frames[1]
    for fb in (a, b):
        _widget(at, "button", f"pick_{fb}").click()
        at.run()
    _widget(at, "button", "seg_continue").click()
    at.run()

    # SKIP the gate radio interaction entirely. Click make-repr cold — the
    # auto-coupling should set usable + use_for_analysis without the
    # operator touching any radio.
    _make_repr(at, a)

    gates_a = at.session_state["gates"][a]
    assert gates_a["usable"] is True
    assert gates_a["use_for_analysis"] is True
    # Akadir 2026-05-25 v2 § 4 — segmentation_perfect auto-defaults to False
    # ("No") so the realistic default doesn't block lock-gates. The earlier
    # v4 cut left it at None; Akadir clarified all three should auto-select.
    assert gates_a["segmentation_perfect"] is False
    # The other picked frame is not affected.
    assert at.session_state["gates"].get(b, {}).get("usable") is None


def test_usable_yes_cascades_use_for_analysis_default_to_yes(ui_app) -> None:
    """Akadir 2026-05-25 v3 § 1 (side issue) — when the operator clicks
    usable=Yes on a frame (without going through make-repr), the cascade
    should default use_for_analysis=Yes (not No). 'Making a frame useful
    inherently assumes its usage within analysis, therefore this default,
    which is still overrideable by the user later.' Segmentation perfect
    stays cascade-defaulted to No (the realistic default for that
    judgment). Both defaults are overrideable."""
    at = ui_app
    at.run()
    _widget(at, "button", "pick_AGZ855").click()
    at.run()
    _widget(at, "button", "load_model_continue").click()
    at.run()
    frames = list(at.session_state["available_frame_bases"])
    a = frames[0]
    _widget(at, "button", f"pick_{a}").click()
    at.run()
    _widget(at, "button", "seg_continue").click()
    at.run()

    # Click usable=Yes only — DO NOT touch g2/g3.
    _widget(at, "radio", f"g1_{a}").set_value("Yes")
    at.run()

    gates_a = at.session_state["gates"][a]
    assert gates_a["usable"] is True
    # Cascade defaults — both reachable without the operator clicking g2/g3.
    assert gates_a["segmentation_perfect"] is False, (
        "segmentation_perfect should default to No (realistic — perfect "
        "segmentation is rare)")
    assert gates_a["use_for_analysis"] is True, (
        "use_for_analysis should default to Yes — making a frame usable "
        "inherently implies usage within analysis (Akadir 2026-05-25 v3)")


def test_repr_excluded_from_analysis_surfaces_warning(ui_app) -> None:
    """Akadir 2026-05-25 § 6 — if the operator marks a frame as repr AND
    THEN manually flips Use-for-analysis to No, the incoherent state is
    permitted (the operator may have a reason) but surfaces a non-blocking
    warning that names the two paths out of the inconsistency."""
    at = ui_app
    at.run()
    _widget(at, "button", "pick_AGZ855").click()
    at.run()
    _widget(at, "button", "load_model_continue").click()
    at.run()
    frames = list(at.session_state["available_frame_bases"])
    a = frames[0]
    _widget(at, "button", f"pick_{a}").click()
    at.run()
    _widget(at, "button", "seg_continue").click()
    at.run()

    _make_repr(at, a)
    # Now manually flip Use-for-analysis back to No on the repr frame.
    _widget(at, "radio", f"g3_{a}").set_value("No")
    at.run()

    # The incoherent-state warning surfaces, naming the path out.
    assert any("designated representative" in w.value
               and "excluded from analysis" in w.value
               for w in at.warning), [w.value for w in at.warning]

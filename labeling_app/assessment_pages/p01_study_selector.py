"""Phase 1 — Study selector.

Lists every valid study under `data/studies_root/` with study_code + parsed
demographics + last-run timestamp (when one exists in runs.jsonl). User picks
one; the choice persists into `st.session_state.study_entry`.

PHI: never displays the raw folder name except in the dev-mode expander.
"""
from __future__ import annotations

from datetime import datetime

import streamlit as st

from preflight_helpers.bundle_writer import iter_runs
from preflight_helpers.study_discovery import (
    StudyEntry, discover_all_studies, list_frame_bases,
)


def _last_run_map() -> dict[str, str]:
    """`{study_code: latest created_at_utc}` in one pass over runs.jsonl.

    Built once per render instead of per-card — at 272 studies the old
    per-card `runs_for_study` would re-scan the ledger 272 times.
    """
    m: dict[str, str] = {}
    for r in iter_runs():  # oldest -> newest, so the last write wins = latest
        code = r.get("study_code")
        ts = r.get("created_at_utc")
        if code and ts:
            m[code] = ts
    return m


def _entry_card(entry: StudyEntry, last_run: str, is_new: bool = False) -> None:
    d = entry.meta.to_ui_dict()
    sex = d["sex"] or "—"
    age = d["age"] if d["age"] is not None else "—"
    bmi = f"{d['bmi']:.1f}" if d["bmi"] is not None else "—"
    height = f"{d['height_cm']} cm" if d["height_cm"] else ""
    weight = f"{d['weight_kg']} kg" if d["weight_kg"] else ""

    cols = st.columns([3, 2, 2, 2, 2, 2])
    # Tag the new 3-modality-round captures (femur/RF/sweep) so the operator can tell them from the
    # legacy single-sweep backup cohort at a glance (Akadir 2026-06-26). Pure markdown — NOT a widget,
    # so it does not re-trip the p01 AppTest harness limit (feedback_apptest_conditional_widgets).
    badge = " :green[● new round]" if is_new else ""
    # Usable-but-deviant conditions ride visibly next to the code and never disable Select
    # (feedback_flag_carry_caveats_dont_gate). Also pure markdown, for the same AppTest reason.
    # NBSP inside the label so a caveat wraps as one unit instead of splitting mid-phrase.
    badge += "".join(f" :orange[⚠\u00a0{c.replace('_', '\u00a0')}]" for c in entry.caveats)
    cols[0].markdown(f"**{d['study_code']}**{badge}")
    cols[1].markdown(f"{age}y {sex}")
    cols[2].markdown(f"BMI {bmi}")
    cols[3].markdown(f"{height}, {weight}".strip(", "))
    cols[4].markdown(f"Last run: `{last_run}`")
    pick = cols[5].button("Select", key=f"pick_{d['study_code']}",
                          disabled=not entry.valid,
                          type="primary" if entry.valid else "secondary")
    if pick:
        st.session_state.study_entry = entry
        for _k in ("texture_choice", "texture_refine", "tex_disposition", "tex_ero", "tex_band",
                   "release_texture_ck", "confirm_size_ck", "contribute_frames_ck"):
            st.session_state.pop(_k, None)             # a new study starts un-reviewed (Phase-2 texture panel)
        # Pre-cache frame bases so phase 3 starts fast.
        st.session_state.available_frame_bases = list_frame_bases(entry.raw_image_dir)
        st.session_state.phase = 1
        st.rerun()


def _discovered_studies() -> list[StudyEntry]:
    """Merged studies_root + alive-backup-cohort discovery, cached in session.

    Discovery globs 272 backup dirs, so it is cached per session and only
    re-walked when the operator hits Refresh (or a new session starts).
    """
    if "discovered_studies" not in st.session_state:
        st.session_state.discovered_studies = discover_all_studies()
    return st.session_state.discovered_studies


def render_sidebar() -> None:
    """Stage-modular sidebar (F3) — phase 1 is tight: what this step does +
    the current selection once one is made."""
    st.markdown("### ① Study")
    st.caption(
        "Pick a study to assess. Lists the full alive cohort (272) plus any "
        "local `data/studies_root/` captures."
    )
    entry = st.session_state.get("study_entry")
    if entry is not None:
        d = entry.meta.to_ui_dict()
        st.caption(f"Selected: **{d['study_code']}**")
    from labeling_app.assessment_pages import help_content
    help_content.render_help_expander(0)




def render() -> None:
    st.subheader("Pick a study")
    st.caption(
        "Lists the full alive cohort (272 studies) plus any local "
        "`data/studies_root/` captures. New studies dropped under "
        "`studies_root/` appear after a Refresh."
    )
    # The reference-cohort selector used to live here. It moved to the assessment page (p05) on
    # 2026-08-03: it changes percentiles by up to 40 points and belongs beside the numbers it changes,
    # with the reference's own medians visible while you choose. Nothing sets cohort_source here now —
    # p05's control seeds the default, and every reader already falls back to OLD.
    st.caption(":gray[The **reference cohort** (old / new round / mixed) is chosen on the "
               "**assessment page (step 5)**, beside the percentiles it moves.]")

    head_l, head_r = st.columns([6, 1])
    if head_r.button("🔄 Refresh", key="rediscover_studies",
                     help="Re-walk the study sources (picks up newly dropped captures)."):
        st.session_state.pop("discovered_studies", None)
        st.rerun()

    entries = _discovered_studies()
    if not entries:
        st.warning(
            "No studies discovered. Expected the alive cohort under "
            "`goruntuler_backup/` (via `configs/study_codes.csv`) or local "
            "captures under `data/studies_root/`."
        )
        return

    last_runs = _last_run_map()
    # Which studies are the new 3-modality round (a femur/RF captures dir present)? One cheap pass so
    # the cards can badge them and the header can count them (Akadir 2026-06-26).
    from preflight_helpers.study_discovery import study_is_new_round
    new_codes = {e.meta.study_code for e in entries if study_is_new_round(e.study_path)}
    valid_count = sum(1 for e in entries if e.valid)
    caveat_count = sum(1 for e in entries if e.valid and e.caveats)
    _new_txt = f" · :green[{len(new_codes)} new round]" if new_codes else ""
    _cav_txt = f" · :orange[{caveat_count} with caveats]" if caveat_count else ""
    head_l.markdown(f"**{valid_count} valid** of {len(entries)} discovered{_new_txt}{_cav_txt}")

    # 272 cards is a lot — a study_code filter keeps the picker usable.
    query = st.text_input(
        "Filter by study code", key="study_filter", value="",
        placeholder="e.g. AGZ855 — leave blank to show all",
    ).strip().upper()
    shown = [e for e in entries if not query or query in e.meta.study_code.upper()]
    if query:
        st.caption(f"Showing {len(shown)} of {len(entries)} (filtered).")

    for entry in shown:
        _entry_card(entry, last_runs.get(entry.meta.study_code, "—"),
                    is_new=entry.meta.study_code in new_codes)
        if not entry.valid:
            with st.expander(f"⚠ {entry.meta.study_code} not valid — {entry.drop_reason}",
                             expanded=False):
                st.json(entry.ui_dict())

    if st.session_state.dev_mode:
        with st.expander("Developer info — raw paths (PHI-bearing, do not screenshot)",
                         expanded=False):
            for e in shown:
                st.code(str(e.study_path), language="text")

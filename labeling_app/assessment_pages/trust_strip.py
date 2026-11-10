"""The measurement-trust strip — one glance at what this study's numbers rest on.

Step one of the QA/reproducibility surface (Akadir 2026-08-13: *"I want to let the users know our
assessment app also has a reproducibility surface and it makes sense and acts, and show the
acts"*). Three rows — Size, Texture, Strength — each stating WHAT is served, FROM WHERE, and WHY,
built entirely from stamps that already exist:

  Size      the 3-way RF-reliability decision (`_rf_reliability` on the aggregate): which view's
            thickness is served — centre-RF when the femur validates it, femur when they disagree.
  Texture   the #17 audit state (verbatim chip text — one vocabulary, never two) plus any
            recorded human verdict from the flag→kill ledger (a kill = withheld by decision;
            a clear = consent recorded, applies at the next freeze).
  Strength  the force-card payload state: measured (with its tier), held (with the receipt),
            no session, or band refusal. New-round studies only — the card's own scope.

No new computation, no widgets (pure markdown — the p01/p05 AppTest keyed-widget limit), additive
and fail-silent like every panel block. Headless-importable: `st` is passed in, never imported.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import List, Optional, Tuple

Row = Tuple[str, str, str]          # (color, icon, text) — rendered as one caption line each

_REPO = Path(__file__).resolve().parents[2]
_LANDMARK = _REPO / "scratchpad" / "new_round_landmark_full.json"
_canon_cache: Optional[dict] = None


def _canonical_pair(study_uid: str) -> Optional[dict]:
    """The CANONICAL consistency numbers — the per-view max thicknesses the freeze's own
    top-down check compared (Akadir's two-level ask, 2026-08-14: level 1 = the check that runs
    canonically for every study; level 2 = this run's selected frames). Read once from the
    freeze's landmark JSON; fail-silent when it is absent on this machine (gitignored)."""
    global _canon_cache
    if _canon_cache is None:
        try:
            _canon_cache = {}
            for r in json.load(open(_LANDMARK)):
                uid = "NR" + hashlib.sha256(r["study"].encode("utf-8")).hexdigest()[:8].upper()
                g = lambda role: ((r.get("vals") or {}).get(role) or {}).get("full_max_thickness_mm")  # noqa: E731
                _canon_cache[uid] = {"femur": g("femur"), "center": g("rf")}
        except Exception:
            _canon_cache = {}
    return _canon_cache.get(study_uid)


def _pair_txt(femur, center) -> Optional[str]:
    """'femur 13.2 vs centre 16.2 mm (18% apart)' — the gate's own relative-difference units."""
    if femur is None or center is None:
        return None
    m = max(abs(femur), abs(center))
    d = abs(femur - center) / m if m > 0 else 0.0
    return f"femur {femur:.1f} vs centre {center:.1f} mm ({d:.0%} apart)"


# --------------------------------------------------------------- the evidence panel (§4b)
_COHORT_CSV = _REPO / "data" / "cohort_stats" / "per_study_cohort_new.csv"
_cohort_cache: Optional[dict] = None

# (label, feature key) — the measured features the canonical spine and a run both produce.
EVIDENCE_FEATURES = [
    ("RF thickness (mm)", "rf_thickness_mm"),
    ("Full thickness q50 (mm)", "full_thickness_q50_mm"),
    ("QMT (mm)", "qmt_mm"),
    ("STAR (QMT/BMI)", "star_ratio"),
    ("Fat thickness q50 (mm)", "fat_thickness_q50_mm"),
    ("Muscle/fat ratio", "muscle_fat_ratio"),
    ("Visible RF area (mm²)", "visible_rf_area_mm2"),
    ("Fascicular contrast (int_cv)", "int_cv_within"),
]


def _canonical_features(study_uid: str) -> Optional[dict]:
    """The study's row in the FROZEN cohort CSV — the exact numbers placement uses (Akadir
    2026-08-14: 'the auto-spine is a black box … this new surface helps these numbers to be
    seen, compared, assessed'). Cached once; fail-silent when absent."""
    global _cohort_cache
    if _cohort_cache is None:
        try:
            import csv
            _cohort_cache = {r["study_code"]: r
                             for r in csv.DictReader(open(_COHORT_CSV, encoding="utf-8"))}
        except Exception:
            _cohort_cache = {}
    return _cohort_cache.get(study_uid)


def _canonical_frame_counts(study_uid: str) -> Optional[str]:
    """'femur ×N · RF ×M frames' — how much automatic evidence stands behind the canonical
    numbers (the landmark JSON records the per-view frame counts)."""
    global _canon_cache
    _canonical_pair(study_uid)                       # ensure the cache attempt ran
    try:
        import hashlib as _h
        for r in json.load(open(_LANDMARK)):
            if "NR" + _h.sha256(r["study"].encode("utf-8")).hexdigest()[:8].upper() == study_uid:
                g = lambda role: ((r.get("vals") or {}).get(role) or {}).get("n_frames")  # noqa: E731
                f, c = g("femur"), g("rf")
                if f or c:
                    return f"femur ×{f or '?'} · centre-RF ×{c or '?'} frames"
    except Exception:
        pass
    return None


def _fmt(v) -> str:
    if v is None or v == "":
        return "—"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{x:.3f}" if abs(x) < 10 else f"{x:.2f}"


def evidence_rows(run_data: dict, study_uid: str) -> List[Tuple[str, str, str, str]]:
    """(feature label, canonical, this run, delta) per measured feature — the comparison table
    Akadir asked for. Canonical = the frozen cohort row (what placement uses); this run = the
    page's own aggregate. Delta is signed percent when both sides are numeric."""
    canon = _canonical_features(study_uid) or {}
    agg = run_data.get("aggregate") or {}
    out: List[Tuple[str, str, str, str]] = []
    for label, key in EVIDENCE_FEATURES:
        cv, rv = (canon.get(key) or "").strip() if canon else "", agg.get(key)
        delta = "—"
        try:
            c, r = float(cv), float(rv)
            if c != 0:
                delta = f"{(r - c) / abs(c):+.1%}"
        except (TypeError, ValueError):
            pass
        out.append((label, _fmt(cv), _fmt(rv), delta))
    return out


def render_evidence(st, run_data: dict, study_uid: str, *, actions: Optional[dict] = None) -> None:
    """The 'Measurement evidence' expander under the strip: the canonical numbers vs this run,
    plus the release/confirm checkboxes when the study offers them (armed by Accept run).
    Widget keys are stable; checkboxes only appear for a study that has an action to offer, so
    the page stays calm everywhere else."""
    rows = evidence_rows(run_data, study_uid)
    if not any(r[1] != "—" or r[2] != "—" for r in rows):
        return
    with st.expander("Measurement evidence — canonical numbers vs this run", expanded=False):
        counts = _canonical_frame_counts(study_uid)
        st.caption(":gray[**Canonical** = the automatic top-5-frame pipeline the cohort and "
                   "placement use" + (f" ({counts})" if counts else "") +
                   " — **this run** = your selected frames. Same code measures both.]")
        st.markdown("\n".join(
            ["| Feature | Canonical | This run | Δ |", "|---|---|---|---|"]
            + [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows]))
        acts = actions or {}
        if acts.get("texture_release"):
            st.checkbox("Release texture from THIS run at the next freeze — I reviewed the "
                        "centre-RF region and vouch for it", key="release_texture_ck",
                        help="Records a run-bound clear: the flag stays visible, this run's "
                             "int_cv becomes the served value at the next freeze. Armed by "
                             "Accept run below.")
        elif acts.get("texture_verdict") == "cleared":
            st.caption(":gray[Texture already released by recorded verdict — supersede via "
                       "`measurement_verdict.py --scope feature` if needed.]")
        if acts.get("size_confirm"):
            st.checkbox("Confirm size — femur numbers reviewed and stand", key="confirm_size_ck",
                        help="Records the review; nothing changes value (thickness always "
                             "serves from the femur view). Armed by Accept run below.")
        if acts.get("contribute"):
            _n = acts.get("n_contributed_runs") or 0
            _prev = f" — {_n} run(s) already contributed" if _n else ""
            st.checkbox(f"Contribute this run's frames to the canonical pool (the 5+xth "
                        f"frame){_prev}", key="contribute_frames_ck",
                        help="At the next freeze, this run's vetted frames join the automatic "
                             "top-5 in the study's evidence pool: femur frames refine the "
                             "geometry medians, RF frames the texture median. Evidence "
                             "addition only — trust decisions keep reading the automatic "
                             "values, and an explicit release verdict always outranks the "
                             "pool. Armed by Accept run below.")
        if acts.get("texture_release") or acts.get("size_confirm") or acts.get("contribute"):
            st.caption(":gray[Checked boxes take effect when you **Accept run** — every action "
                       "is bound to that accepted run's numbers, as a signed ledger entry.]")


def _ledger_texture_verdict(study_uid: str) -> Optional[str]:
    """A recorded human verdict on this study's texture, if any — the suffix that shows the
    surface ACTS. Run-bound clears name their run. Fail-silent: an unreadable ledger yields no
    suffix, never an error."""
    try:
        from preflight_helpers import flag_kill_ledger as fkl
        for e in fkl.current().values():
            if (e["scope"] == "feature" and e.get("study_uid") == study_uid
                    and "int_cv_within" in (e.get("fields") or ())):
                who = e.get("decided_by", "recorded verdict")
                if e["status"] == "killed":
                    return f"withheld by {who}'s recorded verdict"
                if e["status"] == "cleared":
                    run = e.get("source_run_id")
                    src = f" from run {run[:22]}…" if run else ""
                    return f"cleared by {who}{src} — applies at the next freeze"
    except Exception:
        pass
    return None


def _ledger_size_confirm(study_uid: str) -> Optional[str]:
    """The size-confirm marker (femur numbers reviewed) — turns the Size caveat into a
    recorded consent. Fail-silent like the texture suffix."""
    try:
        from preflight_helpers import flag_kill_ledger as fkl
        from preflight_helpers.release_actions import SIZE_CONFIRM_FIELD
        for e in fkl.current().values():
            if (e["scope"] == "feature" and e.get("study_uid") == study_uid
                    and e["status"] == "cleared"
                    and SIZE_CONFIRM_FIELD in (e.get("fields") or ())):
                return f"femur numbers confirmed by {e.get('decided_by', '?')}"
    except Exception:
        pass
    return None


def rows_for(run_data: dict, texture_state: Optional[dict], force_payload: Optional[dict],
             *, is_new_round: bool, study_uid: str) -> List[Row]:
    """The strip's rows, pure. Every branch states a fact already stamped elsewhere."""
    rows: List[Row] = []

    # ---- Size: which view the thickness numbers come from, and why — with the numbers at both
    # levels: this run's selected frames, and the canonical top-down check (Akadir 2026-08-14).
    stamp = (run_data.get("aggregate") or {}).get("_rf_reliability") or {}
    dec = stamp.get("decision") or {}
    src, rel = dec.get("source"), dec.get("reliability")
    sv = stamp.get("values") or {}
    run_pair = _pair_txt(sv.get("max_femur_mm"), sv.get("max_center_mm"))
    canon = _canonical_pair(study_uid) or {}
    canon_pair = _pair_txt(canon.get("femur"), canon.get("center"))
    detail = ""
    if run_pair:
        detail += f" — this run: {run_pair}"
    if canon_pair:
        detail += f" · canonical check: {canon_pair}"
    if not dec or rel == "legacy-sweep" or src == "sweep":
        rows.append(("gray", "✓", "**Size** — single-view study: the sweep is the only source "
                                  "(legacy round, no cross-check possible)"))
    elif src == "center_rf":
        rows.append(("gray", "✓", "**Size** — centre-RF validated by the femur view (max "
                                  f"thickness agrees within 10%): the finer view is served{detail}"))
    else:                                   # femur won a disagreement
        confirm = _ledger_size_confirm(study_uid)
        if confirm:
            rows.append(("gray", "✓", "**Size** — views disagreed on max thickness: served from "
                                      f"the femur view · {confirm}{detail}"))
        else:
            rows.append(("orange", "✓", "**Size** — views disagreed on max thickness: served from "
                                        f"the femur view (complete boundary; coarser resolution){detail}"))

    # ---- Texture: the #17 state verbatim, plus any recorded verdict.
    if texture_state is None:
        rows.append(("gray", "•", "**Texture** — no cross-check possible (single view)"))
    else:
        state = texture_state["state"]
        color = {"withheld": "red", "pending_review": "orange"}.get(state, "gray")
        icon = {"verified": "✓", "reviewed_kept": "✓", "fixed": "✎",
                "pending_review": "⚠", "withheld": "🚫"}.get(state, "•")
        txt = f"**Texture** — {texture_state['chip']}"
        icv = (run_data.get("aggregate") or {}).get("int_cv_within")
        if icv is not None:
            refined = " (refined region)" if run_data.get("texture_refine") else ""
            txt += f" · int_cv this run: {icv:.3f}{refined}"
        verdict = _ledger_texture_verdict(study_uid)
        if verdict:
            txt += f" · {verdict}"
        rows.append((color, icon, txt))

    # ---- Strength: the card's own payload state (new-round scope, like the card).
    if is_new_round and force_payload:
        fp = force_payload
        if fp.get("held"):
            receipt = (fp["held"].get("receipt") or "").strip()
            rows.append(("orange", "⚠", f"**Strength** — measurement held out of analysis "
                                        f"pending a human verdict ({receipt})"))
        elif fp.get("measured") and fp["measured"].get("display_value"):
            m = fp["measured"]
            # concordance is the card's own dict: {"verdict": within|below|above, "margin_nm": …}
            verdict = (fp.get("concordance") or {}).get("verdict")
            txt = f"**Strength** — measured {m['value']:.0f} Nm"
            if m.get("tier") == "marginal" and m.get("note"):
                txt += f" ({m['note']})"
            if verdict in ("within", "below", "above") and fp.get("band"):
                txt += f" · {verdict} the expected range for this profile"
            rows.append(("gray", "✓", txt))
        elif fp.get("measured"):            # unstable tier — value structurally absent
            cv = fp["measured"].get("cv_pct")
            rows.append(("orange", "⚠", f"**Strength** — measured, but session repeats varied "
                                        f"±{cv:.0f}% — too unstable to quote or analyse"))
        elif fp.get("no_session"):
            # a refused band rides along here: no_session is defined as "no held, no measured",
            # so a standalone refusal branch after it could never fire (red-team 2026-08-13)
            txt = "**Strength** — no dynamometer session recorded"
            if fp.get("band_refusal"):
                txt += f" · expected range refused: {'; '.join(fp['band_refusal'])}"
            rows.append(("gray", "•", txt))
    return rows


def render(st, run_data: dict, texture_state: Optional[dict], force_payload: Optional[dict],
           *, is_new_round: bool, study_uid: str) -> None:
    """The strip + the #17 withhold notice it absorbed (same text, same trigger — the standalone
    chip call this replaces used to emit it). Deliberately NO try/except here: a failure must
    propagate to the caller, whose fallback restores the original chip — swallowing it here
    rendered NEITHER surface (the 2026-08-13 first-render bug)."""
    rows = rows_for(run_data, texture_state, force_payload,
                    is_new_round=is_new_round, study_uid=study_uid)
    if not rows:
        return
    st.caption(":gray[**Measurement trust** — what each number rests on]")
    for color, icon, txt in rows:
        st.caption(f":{color}[{icon} {txt}]")
    if texture_state and texture_state["state"] == "withheld":
        st.warning("Muscle condition **withheld** — the center-RF texture (fascicular contrast) "
                   "could not be resolved at the shallow FOV, and every recipe needs it. The "
                   "honest read is no score, not an invented one. Change this in the phase-4 "
                   "review.", icon="🚫")

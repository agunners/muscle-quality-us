"""Standalone demo of the muscle-condition panel -- SEE it before it touches p05.

    Launch:   .venv/bin/streamlit run scripts/condition_panel_demo.py
    Headless figure check (no browser):  .venv/bin/python scripts/condition_panel_demo.py --check

Loads the frozen model, picks a few illustrative real patients, and renders the
panel exactly as the assessment page will. Nothing here writes or refits.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))            # scripts/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "labeling_app", "assessment_pages"))

import pandas as pd  # noqa: E402
from preflight_helpers import condition_model as cm  # noqa: E402
import condition_panel as cp  # noqa: E402


def _examples():
    art = cm.load()
    df = pd.read_csv(cm.CSV)
    feat = cm.EIGHT
    sub = df[feat + ["quality_label", "study_code"]].dropna().reset_index(drop=True)
    scored = [cm.score_patient(art, dict(sub.loc[i, feat])) for i in range(len(sub))]
    conds = [s["headline"]["condition"] for s in scored]
    # The "split" example = the patient whose size axis and tissue axis pull hardest in opposite
    # directions. Picked by the axes' exact pushes (beta*z), not the coarse decile percentile: since
    # 2026-09-02 the axes follow the SERVED recipe and the decile box is honestly None for a column
    # the frozen artifact never boxed (raw thickness), so the percentile is no longer a safe key.
    size_push = [s["axes"][0]["contribution"] or 0.0 for s in scored]
    tex_push = [s["axes"][2]["contribution"] or 0.0 for s in scored]
    import numpy as np
    picks = {
        "Best (clearly preserved)": int(np.argmax(conds)),
        "Worst (clearly poor)": int(np.argmin(conds)),
        "Split (big body-size vs tissue gap)":
            int(np.argmax([abs(a - b) for a, b in zip(size_push, tex_push)])),
        "Median": int(np.argsort(conds)[len(conds) // 2]),
    }
    return art, df, feat, sub, picks


def _headless_check():
    """Build every figure for the example patients; assert structure, no browser."""
    art, df, feat, sub, picks = _examples()
    print("HEADLESS FIGURE CHECK\n")
    ok = True
    for label, i in picks.items():
        res = cm.score_patient(art, dict(sub.loc[i, feat]))
        h = res["headline"]
        strip = cp.band_strip_figure(h["condition"], h["boundaries"], h["band"])
        trip = cp.triplet_figure(res["options"])
        n_rows = sum(1 for k in res["options"].values() if k.get("available"))
        strip_marker = any(getattr(t, "mode", "") and "markers" in t.mode for t in strip.data)
        trip_markers = sum(1 for t in trip.data if getattr(t, "mode", "") and "markers" in t.mode)
        good = strip_marker and trip_markers == n_rows and len(strip.layout.shapes) == 5
        ok &= good
        print(f"  {label:38s} score {h['condition']:>3.0f} {h['band']:<13s} "
              f"strip-marker={strip_marker} triplet-rows={trip_markers}/{n_rows} "
              f"{'OK' if good else 'CHECK'}")
    print(f"\n{'figures construct + carry the patient marker on every row' if ok else 'STRUCTURE PROBLEM'}")


def _streamlit_app():
    import streamlit as st
    art, df, feat, sub, picks = _examples()
    st.set_page_config(page_title="Muscle-condition panel — demo", layout="centered")
    st.title("Muscle-condition panel — demo")
    st.caption("Standalone preview. Pick a patient; the panel renders as it will on "
               "the assessment page (headline band + what-drives-it + the 3-option "
               "comparison).")
    label = st.selectbox("Patient", list(picks) + ["— pick by study —"])
    if label == "— pick by study —":
        sc = st.selectbox("study_code", sub["study_code"].tolist())
        i = int(sub.index[sub["study_code"] == sc][0])
    else:
        i = picks[label]
    hk = st.radio("Headline model", ["C", "A", "B"], horizontal=True,
                  format_func=lambda k: f"{k}: {cm.OPTIONS[k][0]}",
                  help="Which option drives the headline number. Default C (STAR). "
                       "In a deployed tool you would LOCK one — a free switch lets a "
                       "user pick the answer they like (cherry-picking).")
    st.divider()
    res = cm.score_patient(art, dict(sub.loc[i, feat]))
    cp.render(res, st, headline_key=hk)
    st.divider()
    st.caption(f"study {sub.loc[i,'study_code']} · doctors graded: "
               f"**{sub.loc[i,'quality_label']}**")


if "--check" in sys.argv:
    _headless_check()
else:
    _streamlit_app()

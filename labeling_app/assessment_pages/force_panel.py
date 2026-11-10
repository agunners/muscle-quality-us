"""Assessment-page Strength card: measured torque vs the range this profile usually produces.

Renders what scripts/preflight_helpers/force_card.py serves — nothing is computed here. Three
elements, per the contract (docs/force_card_spec.md):

  1. measured  -- the dynamometer value with its OWN session repeatability (never a bare point),
                  tiered by the ratified CV display policy; held / unstable / absent states say
                  plainly why there is no number.
  2. expected  -- the frozen model's sex-stratified 8-in-10 range (intervals, never points: the
                  ridge prediction itself is deliberately not marked).
  3. concordance -- measured below / within / above the range, with the honest context numbers
                  (band meaning, day-to-day wobble) alongside.

The figure builder takes plain data and returns go.Figure — importable and checkable without
Streamlit. render() takes `st` injected so the module stays headless-importable.
"""
from __future__ import annotations

from typing import Optional

import plotly.graph_objects as go

_BAND80 = "rgba(31,119,180,0.20)"     # the served 8-in-10 band
_BAND95 = "rgba(31,119,180,0.08)"     # the wider 95% context band (i-detail)
_EDGE = "rgba(31,119,180,0.65)"


def force_strip_figure(band: Optional[dict], measured: Optional[float],
                       axis_hi: float) -> go.Figure:
    """Horizontal Nm strip: shaded expected band (80% solid over 95% faint) + the measured
    diamond. Either side may be absent; the axis always starts at 0 Nm."""
    fig = go.Figure()
    if band:
        lo, hi = max(0.0, band["lo"]), band["hi"]
        lo95, hi95 = max(0.0, band["lo95"]), band["hi95"]
        fig.add_shape(type="rect", x0=lo95, x1=hi95, y0=0.12, y1=0.88,
                      fillcolor=_BAND95, line_width=0, layer="below")
        fig.add_shape(type="rect", x0=lo, x1=hi, y0=0, y1=1,
                      fillcolor=_BAND80, line=dict(color=_EDGE, width=1), layer="below")
        for x in (lo, hi):
            fig.add_annotation(x=x, y=-0.25, text=f"{x:.0f}", showarrow=False,
                               font=dict(size=10, color="gray"))
    if measured is not None:
        tpos = ("top left" if axis_hi and measured / axis_hi >= 0.92 else
                "top right" if axis_hi and measured / axis_hi <= 0.08 else "top center")
        fig.add_trace(go.Scatter(
            x=[measured], y=[0.5], mode="markers+text",
            marker=dict(symbol="diamond", size=20, color="#111",
                        line=dict(color="white", width=2)),
            text=[f"{measured:.0f}"], textposition=tpos,
            textfont=dict(size=13, color="#111"), hoverinfo="skip", showlegend=False))
    fig.update_xaxes(range=[0, axis_hi], showgrid=False, zeroline=False,
                     title_text="isometric knee-extension torque (Nm)",
                     title_font=dict(size=11, color="gray"))
    fig.update_yaxes(range=[-0.5, 1.4], visible=False)
    fig.update_layout(height=110, margin=dict(l=8, r=8, t=8, b=34), plot_bgcolor="white")
    return fig


def _axis_hi(payload: dict) -> float:
    xs = [float(payload["model"]["target_max_nm"])]
    if payload.get("band"):
        xs.append(payload["band"]["hi95"])
    if payload.get("measured") and payload["measured"]["display_value"]:
        xs.append(payload["measured"]["value"])
    return max(xs) * 1.06


def _measured_lines(payload: dict, st) -> None:
    m, held = payload.get("measured"), payload.get("held")
    if held:
        st.markdown(":orange[🚫 A recorded strength measurement is **held by the measurement "
                    "gate** (entry-plausibility flag) pending a human verdict — not served.]")
        st.caption(f":gray[Gate receipt: `{held['receipt']}` · verdicts via "
                   "`scripts/measurement_verdict.py` (CLEAR restores the value, KILL retires it).]")
        return
    if payload.get("no_session"):
        st.markdown(":gray[No dynamometer session recorded for this study.]")
        return
    if m is None:
        return
    if not m["display_value"]:
        st.markdown(f":orange[A dynamometer session exists, but {m['note']}.]")
        if m["cv_pct"] is not None:
            st.caption(":gray[The measurement stays in every analysis (\"drop nothing\") — the "
                       "limit governs display only.]")
        return
    sd = f" · session repeats ±{m['session_sd_nm']:.0f} Nm (CV {m['cv_pct']:.1f}%)" \
        if m["session_sd_nm"] is not None else ""
    st.markdown(f"**Measured: {m['value']:.0f} Nm**{sd}")
    if m["tier"] == "marginal":
        st.markdown(f":orange[⚠ {m['note']}]")


def _expected_lines(payload: dict, st) -> None:
    band, refusal, model = payload.get("band"), payload.get("band_refusal"), payload["model"]
    if band:
        st.markdown(f"**Expected for this profile: {max(0, band['lo']):.0f}–{band['hi']:.0f} Nm** "
                    f"— an {model['band_label']} range "
                    f"({'male' if band['sex'] == 'M' else 'female'}-stratified)")
        with st.popover("ⓘ how this range is made"):
            st.write(
                f"The frozen force model's prediction interval for this profile.\n\n"
                f"- **Model**: ridge on age, sex, height, weight + RF thickness, full thickness, "
                f"visible RF area, QMT — the pre-registered rung-2 feature set — fit on "
                f"**{model['n']}** new-round studies ({model['n_by_sex']['M']} M / "
                f"{model['n_by_sex']['F']} F) with a dynamometer session.\n"
                f"- **Honest error, measured out-of-fold** (25-seed nested CV): "
                f"R² **{model['r2']:.2f} ± {model['r2_sd']:.2f}**, RMSE **{model['rmse_nm']:.0f} Nm**. "
                f"Ranking is strong; individual prediction is honest at about ±{model['rmse_nm']:.0f} Nm "
                f"— read the range, not a point.\n"
                f"- **The band** is the central 8-in-10 range of the model's out-of-fold errors, "
                f"sex-stratified (the male band is wider); 2 in 10 measurements like these fall "
                f"outside it by construction. For studies the model was fit on — most of today's "
                f"cohort — coverage runs slightly higher (86% in-sample); 8-in-10 is the honest "
                f"expectation for a NEW study. The band edges themselves carry sampling "
                f"uncertainty of roughly ±10 Nm at n={model['n']}.\n"
                f"- **Demographics carry most of the prediction** (block-Shapley .35 vs geometry .18 "
                f"on the r5 ladder, n=136) — this is *expected for this body and this muscle*, never "
                f"\"your muscle alone\". Imaging's own out-of-fold contribution: +2.1 R² points on "
                f"the pre-registered ladder, +2.5 on this fit set.\n"
                f"- Fit torque range {model['target_min_nm']:.0f}–{model['target_max_nm']:.0f} Nm; "
                f"the model refuses outside its fitted feature ranges.\n"
                f"- Artifact: `force_model.json` v{model['version']}, frozen {model['date']} · "
                f"contract: `docs/force_card_spec.md` · prereg: `docs/force_ladder_prereg.md`.")
    elif refusal:
        st.markdown(":orange[Expected range **refused** — " + "; ".join(refusal) + ". "
                    "The model does not speak outside where it was fit.]")


def _concordance_line(payload: dict, st) -> None:
    c, model = payload.get("concordance"), payload["model"]
    if not c:
        return
    if c["verdict"] == "within":
        st.markdown(":green[✓ Measured strength is **consistent** with what this profile "
                    "usually produces.]")
    elif c["verdict"] == "below":
        by = f"{c['margin_nm']:.0f}" if c["margin_nm"] >= 1 else "<1"
        st.markdown(f":orange[↓ Measured strength sits **below** the {model['band_label']} range "
                    f"for this profile (by {by} Nm). Effort, pain, a neuromuscular "
                    f"limit — or an entry error — can all do this; a flag for a look, not a verdict.]")
    else:
        by = f"{c['margin_nm']:.0f}" if c["margin_nm"] >= 1 else "<1"
        st.markdown(f":blue[↑ Measured strength is **above** the {model['band_label']} range "
                    f"for this profile (by {by} Nm).]")


def render(payload: dict, st) -> None:
    """The Streamlit entry point. `st` injected so the module stays importable headlessly."""
    state = payload.get("state")
    if state == "no_model":
        st.caption(":gray[Strength card unavailable — the force model artifact is missing "
                   "or unreadable (rebuild it: `scripts/preflight_helpers/force_model.py`).]")
        return
    st.divider()
    st.markdown("### Strength — isometric knee extension")
    st.caption(":gray[The dynamometer measurement next to the range the frozen "
               "demographics + geometry model expects for this profile. A context surface — "
               "force stays **out** of the muscle-condition score.]")
    if state == "not_in_substrate":
        st.caption(":gray[This study is not in the analysis substrate yet — bring it in with the "
                   "ingest pipeline (`scripts/ingest_all.py --status` shows what is pending).]")
        return
    band = payload.get("band")
    measured = payload.get("measured")
    show_val = (measured["value"] if (measured and measured["display_value"]) else None)
    if band or show_val is not None:
        st.plotly_chart(force_strip_figure(band, show_val, _axis_hi(payload)),
                        width="stretch", config={"displayModeBar": False})
    _measured_lines(payload, st)
    _expected_lines(payload, st)
    _concordance_line(payload, st)
    m = payload["model"]
    # "within a session" is the receipt's definition (izo_cv x torque, same visit) — between-day
    # repeatability is NORTHSTAR R3 and does not exist yet; do not imply it here (red-team M1).
    st.caption(f":gray[A context read, not a diagnosis — and not validated for tracking change "
               f"over time. Repeat-measurement wobble *within one session* is about "
               f"±{m['wobble_nm']:.1f} Nm — differences smaller than that are noise, and between-day "
               f"variation (not yet measured) comes on top. The criterion "
               f"itself is effort-dependent (best of a maximal-effort protocol).]")

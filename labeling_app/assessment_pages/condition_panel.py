"""Assessment-page muscle-condition panel: banded headline + axis breakdown +
the three-option box-plot triplet.

Renders what scripts/preflight_helpers/condition_model.py scores. Three parts:
  1. headline  -- the BAND (big), the score as supporting detail, on a strip that
                  shows the model's own POOR/INTERMEDIATE/PRESERVED boundaries.
  2. axes      -- compact size / fat / texture percentile bars, each with an (i).
  3. triplet   -- the three model options as box plots (cohort condition-score
                  spread + this patient marked), so the choice of model is visible.
                  Lives inside the page's existing optional "Cohort box plots"
                  expander.

The figure builders (band_strip_figure, triplet_figure) take plain data and
return go.Figure -- importable and checkable WITHOUT Streamlit/a browser (see the
headless self-test). render() is the Streamlit entry point.
"""
from __future__ import annotations

from typing import Optional

import plotly.graph_objects as go

# zone colours (POOR / INTERMEDIATE / PRESERVED), faint for backgrounds
_ZONE = ["rgba(214,39,40,0.13)", "rgba(255,193,7,0.13)", "rgba(44,160,44,0.13)"]
_ZONE_SOLID = ["#d62728", "#e8a000", "#2ca02c"]

# (i) copy -- how each number is collected + what it means. BMI-honest on size.
AXIS_HELP = {
    "Muscle vs body size": "Muscle thickness divided by body-mass index — muscle "
        "relative to your body size (higher is better). Because it is BMI-adjusted, "
        "a heavier body lowers this at the SAME muscle thickness — intended (it is "
        "the sarcopenia view: relative muscle), but it means a muscular-but-heavy "
        "person can read low.",
    "Muscle size": "Thickness of the rectus femoris muscle, in mm (higher is better).",
    "Fat over muscle": "The fat layer above the muscle as a share of total tissue "
        "depth (lower is better).",
    "Fascicular contrast": "How crisp the striations inside the muscle look — organized "
        "vs washed-out (less internal contrast); higher contrast is better. Partly "
        "affected by overlying fat; being refined.",
}
OPTION_HELP = {
    "C": "Headline model. Muscle (size÷BMI) + fat + texture. Agreement with the "
         "doctors: 0.81 (weighted kappa), exact 82%, never off by two bands; covers "
         "~70% of patients. The size input is BMI-adjusted.",
    "A": "Same as the headline but size = raw RF thickness (not BMI-adjusted): 0.77, "
         "covers ~76% of patients.",
    "B": "Uses all eight features. Slightly higher raw accuracy, but the weights are "
         "unstable (can't say which feature drives it) and it covers the fewest "
         "patients. Shown for comparison — not the headline.",
}

# Each headline-axis column -> its clinical axis (the size / fat / tissue split the
# breakdown reports). Keyed by COLUMN so it is robust to the display-label wording.
CLINICAL_AXIS = {
    "star_ratio": "Size", "full_thickness_q50_mm": "Size",
    "fat_to_depth_q50": "Fat", "fat_thickness_q50_mm": "Fat",
    "int_cv_within": "Tissue",
}


def _contrib_phrase(contribution) -> tuple:
    """A contribution (β·z) -> (phrase, streamlit colour). β·z is the axis's EXACT
    additive push on the model's linear predictor η (η = Σ β·z), so >0 pushes the
    patient toward POOR and <0 toward PRESERVED; |β·z| is the strength. Honest for
    the 3-axis headline because its axes barely overlap (VIF<3) — unlike the
    8-feature model, whose weights flip sign and could not be attributed this way."""
    if contribution is None:
        return ("", "gray")
    mag = abs(contribution)
    if mag < 0.25:
        return ("near-neutral for this patient", "gray")
    strength = "strong" if mag >= 1.5 else "moderate" if mag >= 0.7 else "slight"
    toward = "PRESERVED" if contribution < 0 else "POOR"
    colour = "green" if contribution < 0 else "red"
    return (f"pushes the score toward {toward} ({strength})", colour)


def driver_read(rows: list) -> str:
    """One line naming the axis that pushes THIS patient's score the most (largest
    |β·z|) and which way. Contribution-based — it answers "which axis is the lever
    here", which the per-axis position bars cannot show on their own."""
    rated = [r for r in (rows or []) if r.get("contribution") is not None]
    if not rated:
        return ""
    dom = max(rated, key=lambda r: abs(r["contribution"]))
    toward = "PRESERVED" if dom["contribution"] < 0 else "POOR"
    return (f"**{dom.get('clinical') or dom.get('name', '')}** pushes this score the "
            f"most here — toward {toward}.")


def breakdown_block(rows: list, st) -> None:
    """The co-equal size / fat / tissue read — the dimensional analysis that makes a
    single number safe to act on. Per axis it shows WHERE the patient sits (the SAME
    cohort percentile the feature card shows, with that axis's own better-direction —
    never a second, differently-computed pXX) and HOW MUCH the axis moves THIS
    patient's score (its β·z contribution), ordered most-influential first. Each row
    dict: clinical, name, help_name, pct, direction, contribution. `st` is passed in
    so the module stays importable headlessly."""
    if not any(r.get("pct") is not None for r in (rows or [])):
        return
    st.markdown("**What drives the score** — :gray[where this patient sits on each "
                "axis (the same percentile the card leads with — same-sex peers) and "
                "how much each axis pushes the score]")
    ordered = sorted(
        rows, key=lambda r: abs(r["contribution"]) if r.get("contribution") is not None
        else -1.0, reverse=True)
    for r in ordered:
        clinical = r.get("clinical") or ""
        name = r.get("name", "")
        label = f"{clinical} · {name}" if clinical else name
        direction = r.get("direction") or ""
        # Each axis is ONE bordered card: name + ⓘ on top, its percentile bar and push
        # directly beneath, all inside the border — so a bar unambiguously belongs to the
        # name above it. The old flat layout let the tall ⓘ-button inflate the name row,
        # so a name read as part of the block ABOVE it instead of owning the bar below
        # (Akadir 2026-06-18, confirmed by a browser render + fixed by browser render).
        with st.container(border=True):
            nc, ic = st.columns([0.88, 0.12], vertical_alignment="center")
            nc.markdown(f"**{label}**  ·  :gray[{direction}]" if direction
                        else f"**{label}**")
            with ic.popover("ⓘ"):
                st.write(AXIS_HELP.get(r.get("help_name", name), ""))
            pct = r.get("pct")
            if pct is None:
                st.caption(":gray[— not measured on this scan]")
            else:
                pool = r.get("pool")
                st.progress(int(round(pct)) / 100.0,
                            text=f"p{pct:.0f}" + (f" · vs {pool}" if pool else ""))
            phrase, colour = _contrib_phrase(r.get("contribution"))
            if phrase:
                st.markdown(f":{colour}[→ {phrase}]")
    # the "what 'pushes' means" math (additive β·z, axes barely overlap, BMI-adjusted
    # size) moved OUT of this inline caption into the (i) formula box below, where the
    # reader who wants the mechanism already goes (Akadir 2026-06-18).
    line = driver_read(rows)
    if line:
        st.markdown(f":gray[{line}]")


def band_strip_figure(condition: float, boundaries, band: str,
                      scale_max: float = 100) -> go.Figure:
    """Horizontal 0–scale_max strip: three coloured zones, the patient's score
    marked. Score + boundaries arrive on the model's native 0–100 scale;
    `scale_max` rescales the DISPLAY only (e.g. 800 for the SME) — the model is
    untouched, so the band a score falls in never moves."""
    k = scale_max / 100.0
    pos = condition                          # native 0–100 — for label-anchor logic
    cond = condition * k
    b_ip, b_pi = boundaries[0] * k, boundaries[1] * k   # POOR<b_ip , >=b_pi PRESERVED
    edges = [0, b_ip, b_pi, scale_max]
    fig = go.Figure()
    for j in range(3):
        fig.add_shape(type="rect", x0=edges[j], x1=edges[j + 1], y0=0, y1=1,
                      fillcolor=_ZONE[j], line_width=0, layer="below")
    for x in (b_ip, b_pi):
        fig.add_shape(type="line", x0=x, x1=x, y0=0, y1=1,
                      line=dict(color="rgba(90,90,90,0.5)", width=1, dash="dot"))
        fig.add_annotation(x=x, y=-0.25, text=(f"{x:.1f}" if k == 1 else f"{x:.0f}"),
                           showarrow=False, font=dict(size=10, color="gray"))
    # keep the score label inside the plot at the extremes (top clips on the right,
    # 0 on the left) — anchor it inward there, centred everywhere else.
    tpos = "top left" if pos >= 92 else "top right" if pos <= 8 else "top center"
    fig.add_trace(go.Scatter(
        x=[cond], y=[0.5], mode="markers+text",
        marker=dict(symbol="diamond", size=22, color="#111",
                    line=dict(color="white", width=2)),
        text=[f"{cond:.0f}"], textposition=tpos,
        textfont=dict(size=13, color="#111"), hoverinfo="skip", showlegend=False))
    fig.update_xaxes(range=[0, scale_max], showgrid=False, zeroline=False,
                     tickvals=[0, scale_max / 2, scale_max])
    fig.update_yaxes(range=[-0.5, 1.4], visible=False)
    fig.update_layout(height=90, margin=dict(l=8, r=8, t=8, b=20),
                      plot_bgcolor="white")
    return fig


def triplet_figure(options: dict, order=("C", "A", "B"),
                   scale_max: float = 100) -> go.Figure:
    """Three rows (the model options): cohort condition box + this patient's mark.
    `scale_max` rescales the display (native 0–100 → 0–scale_max), model untouched."""
    k = scale_max / 100.0
    fig = go.Figure()
    rows = [key for key in order if options.get(key, {}).get("available")]
    yticktext = []
    for i, key in enumerate(rows):
        o = options[key]
        box, y = o["box"], i
        b_ip, b_pi = o["boundaries"][0] * k, o["boundaries"][1] * k
        # faint band zones behind every row (drawn once, full height handled by range)
        for j, (x0, x1) in enumerate([(0, b_ip), (b_ip, b_pi), (b_pi, scale_max)]):
            fig.add_shape(type="rect", x0=x0, x1=x1, y0=y - 0.4, y1=y + 0.4,
                          fillcolor=_ZONE[j], line_width=0, layer="below")
        # exact boundary numbers per row (each option carries its own cut-points)
        for bx in (b_ip, b_pi):
            fig.add_shape(type="line", x0=bx, x1=bx, y0=y - 0.4, y1=y + 0.4,
                          line=dict(color="rgba(90,90,90,0.4)", width=1, dash="dot"))
            fig.add_annotation(x=bx, y=y + 0.33,
                               text=(f"{bx:.1f}" if k == 1 else f"{bx:.0f}"),
                               showarrow=False, font=dict(size=8, color="gray"))
        fig.add_shape(type="line", x0=box["p10"] * k, x1=box["p90"] * k, y0=y, y1=y,
                      line=dict(color="rgba(70,70,90,0.8)", width=1.5))
        fig.add_shape(type="rect", x0=box["p25"] * k, x1=box["p75"] * k, y0=y - 0.16,
                      y1=y + 0.16, fillcolor="rgba(120,120,170,0.4)",
                      line=dict(color="rgba(80,80,110,0.9)", width=1))
        fig.add_shape(type="line", x0=box["p50"] * k, x1=box["p50"] * k, y0=y - 0.22,
                      y1=y + 0.22, line=dict(color="rgba(40,40,70,1)", width=2))
        fig.add_trace(go.Scatter(
            x=[o["condition"] * k], y=[y], mode="markers+text",
            marker=dict(symbol="diamond", size=15, color="#111",
                        line=dict(color="white", width=1.5)),
            text=[f"{o['condition'] * k:.0f} · {o['band'][:4]}"],
            textposition="middle right",
            textfont=dict(size=11), hoverinfo="skip", showlegend=False))
        yticktext.append(f"{key}: {o['label']}")
    fig.update_xaxes(range=[0, scale_max],
                     title_text=f"muscle-condition score (0–{scale_max:g}, higher = better)",
                     showgrid=False, zeroline=False)
    fig.update_yaxes(tickvals=list(range(len(rows))), ticktext=yticktext,
                     range=[-0.6, len(rows) - 0.4], autorange="reversed")
    fig.update_layout(height=70 * max(1, len(rows)) + 50,
                      margin=dict(l=8, r=8, t=8, b=40), plot_bgcolor="white")
    return fig


# --------------------------------------------------------- Streamlit entry ----
def render(result: dict, st, headline_key: str = "C") -> None:
    """result = condition_model.score_patient(...). `st` passed in so this module
    stays importable headlessly. `headline_key` picks which option drives the
    headline (C default; selectable -- a demo/advanced control, not a patient knob)."""
    # Default headline (C) uses the score's own C→A fallback (no measurable femur → no STAR →
    # model A, which needs no femur); an explicit demo/advanced selection is honoured as-is.
    if headline_key == "C":
        h = result.get("headline")
        is_fallback = bool(result.get("headline_is_fallback"))
    else:
        h = result["options"].get(headline_key)
        is_fallback = False
        if not h or not h.get("available"):        # chosen option can't score -> fall back
            h = next((o for o in result["options"].values() if o.get("available")), None)
    if not h or not h.get("available"):            # None or an unavailable option -> no score
        st.info("Muscle-condition score unavailable — this scan is missing the "
                "inputs (muscle, fat, or texture). The breakdown below shows "
                "whatever was measured.")
    else:
        c = _ZONE_SOLID[h["band_idx"]]
        st.markdown(
            f"#### Muscle condition: <span style='color:{c}'>{h['band']}</span>"
            f"&nbsp; · &nbsp;<span style='color:{c};font-weight:600'>{h['condition']:.0f}</span>"
            f"<span style='color:#888;font-size:.75em'> / 100</span>",
            unsafe_allow_html=True)
        if is_fallback:
            st.caption(":orange[No measurable femur — headline uses model A "
                       "(RF-thickness / Fat / Texture), not STAR = QMT ÷ BMI.]")
        st.plotly_chart(band_strip_figure(h["condition"], h["boundaries"], h["band"]),
                        width="stretch", config={"displayModeBar": False})
        st.caption("Reproduces an experienced reader's overall grade · the band is "
                   "what we stand behind; the exact number is finer than the model "
                   "truly resolves · provisional, not a diagnosis.")

    # compact axis breakdown
    st.markdown("**What drives it**")
    cols = st.columns(len(result["axes"]))
    for col, ax in zip(cols, result["axes"]):
        with col:
            label = ax["name"]
            row = st.columns([0.8, 0.2])
            row[0].caption(label)
            with row[1].popover("ⓘ"):
                st.write(AXIS_HELP.get(label, ""))
            pct = ax["percentile"]
            if pct is None:
                st.caption("— (not measured)")
            else:
                st.progress(int(pct) / 100.0, text=f"{pct:.0f}/100")

    # the three-option triplet, opt-in
    with st.expander("📊 Compare the three model options", expanded=False):
        st.caption("Each row is one way to build the score; the diamond is this "
                   "patient and the number is the exact score. They agree on the "
                   "band ~85% of the time — the answer barely depends on the choice, "
                   "so we ship the clean, interpretable one (C).")
        cols = st.columns(3)
        for col, key in zip(cols, ("C", "A", "B")):
            o = result["options"].get(key, {})
            if o.get("available"):
                col.metric(key, f"{o['condition']:.0f} / 100", o["band"], delta_color="off")
            else:
                col.metric(key, "—", "n/a", delta_color="off")
        st.plotly_chart(triplet_figure(result["options"]),
                        width="stretch", config={"displayModeBar": False})
        for key in ("C", "A", "B"):
            o = result["options"].get(key, {})
            with st.popover(f"ⓘ  {key}: {o.get('label','')}"):
                st.write(OPTION_HELP.get(key, ""))

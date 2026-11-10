"""force_panel — every card state renders, with the honest copy the spec pins.

AppTest re-execs each render fn in its own namespace, so each is self-contained: it
builds its payload through the REAL force_card logic (synthetic rows + the frozen
artifact), then renders. A green AppTest is the floor, never the proof — the browser
render+LOOK in the PROVE-IT loop is the proof (docs/force_card_spec.md §verification).
"""
from __future__ import annotations

from streamlit.testing.v1 import AppTest

from labeling_app.assessment_pages import force_panel as fp


def _payload(**row_over):
    from preflight_helpers import force_card
    base = {"study_uid": "NRTEST0001", "cohort_round": "new", "izo_torque_nm": "150.0",
            "izo_cv_pct": "5.0", "measurement_flags": "", "age": "30", "sex": "M",
            "height_cm": "175", "weight_kg": "75", "rf_thickness_mm": "22.0",
            "full_thickness_q50_mm": "20.0", "visible_rf_area_mm2": "800.0", "qmt_mm": "45.0"}
    base.update(row_over)
    return force_card.build_payload(base, force_card.artifact())


def _body(at: AppTest) -> str:
    return (" ".join(m.value for m in at.markdown)
            + " " + " ".join(c.value for c in at.caption))


def _mid_band_izo() -> float:
    """The synthetic profile's own band midpoint — a guaranteed 'within' measured value."""
    b = _payload()["band"]
    return (b["lo"] + b["hi"]) / 2


def _render_full() -> None:
    import streamlit as st
    from labeling_app.assessment_pages import force_panel
    from tests.ui.test_force_panel import _mid_band_izo, _payload
    force_panel.render(_payload(izo_torque_nm=str(_mid_band_izo())), st)


def test_full_state_shows_measured_expected_and_concordance() -> None:
    at = AppTest.from_function(_render_full).run()
    assert not at.exception
    body = _body(at)
    assert "Strength — isometric knee extension" in body
    assert f"Measured: {_mid_band_izo():.0f} Nm" in body
    assert "session repeats ±" in body
    assert "Expected for this profile:" in body and "8-in-10 range" in body
    assert "consistent" in body                        # within verdict
    art_wobble = __import__("json").load(open("data/cohort_stats/force_model.json"))["within_subject_sd_nm"]
    assert "not a diagnosis" in body and f"±{art_wobble} Nm" in body


def _render_marginal_above() -> None:
    import streamlit as st
    from labeling_app.assessment_pages import force_panel
    from tests.ui.test_force_panel import _payload
    force_panel.render(_payload(izo_cv_pct="12.5", izo_torque_nm="400.0"), st)


def test_marginal_cv_flag_and_above_verdict() -> None:
    at = AppTest.from_function(_render_marginal_above).run()
    assert not at.exception
    body = _body(at)
    assert "above the ≤10% repeatability target" in body
    assert "above" in body.lower() and "Nm)" in body   # above-band verdict with margin


def _render_unstable() -> None:
    import streamlit as st
    from labeling_app.assessment_pages import force_panel
    from tests.ui.test_force_panel import _payload
    force_panel.render(_payload(izo_cv_pct="19.2"), st)


def test_unstable_cv_withholds_the_value_but_keeps_the_band() -> None:
    at = AppTest.from_function(_render_unstable).run()
    assert not at.exception
    body = _body(at)
    assert "beyond the 15% display limit" in body
    assert "Measured: " not in body                    # the value is NOT quoted
    assert "Expected for this profile:" in body        # the band still serves
    assert "drop nothing" in body                      # analysis untouched, said out loud


def _render_held() -> None:
    import streamlit as st
    from labeling_app.assessment_pages import force_panel
    from tests.ui.test_force_panel import _payload
    force_panel.render(_payload(measurement_flags="HELD izo_torque_nm (cv_zero+kgf_m_as_nm)",
                                izo_torque_nm="", izo_cv_pct=""), st)


def test_held_state_names_the_gate_and_shows_no_number() -> None:
    at = AppTest.from_function(_render_held).run()
    assert not at.exception
    body = _body(at)
    assert "held by the measurement gate" in body
    assert "measurement_verdict.py" in body            # the human path is on screen
    assert "Measured: " not in body


def _render_no_session() -> None:
    import streamlit as st
    from labeling_app.assessment_pages import force_panel
    from tests.ui.test_force_panel import _payload
    force_panel.render(_payload(izo_torque_nm="", izo_cv_pct=""), st)


def test_no_session_state() -> None:
    at = AppTest.from_function(_render_no_session).run()
    assert not at.exception
    assert "No dynamometer session recorded" in _body(at)


def _render_envelope_refusal() -> None:
    import streamlit as st
    from labeling_app.assessment_pages import force_panel
    from tests.ui.test_force_panel import _payload
    force_panel.render(_payload(age="99"), st)


def test_envelope_refusal_says_why_and_keeps_measured() -> None:
    at = AppTest.from_function(_render_envelope_refusal).run()
    assert not at.exception
    body = _body(at)
    assert "refused" in body and "age 99 outside the fitted range" in body
    assert "does not speak outside where it was fit" in body
    assert "Measured: 150 Nm" in body                  # measured side survives


def _render_not_in_substrate() -> None:
    import streamlit as st
    from labeling_app.assessment_pages import force_panel
    from preflight_helpers import force_card
    force_panel.render(force_card.build_payload(None, force_card.artifact()), st)


def test_not_in_substrate_points_at_ingest() -> None:
    at = AppTest.from_function(_render_not_in_substrate).run()
    assert not at.exception
    assert "not in the analysis substrate yet" in _body(at)


def _render_no_model() -> None:
    import streamlit as st
    from labeling_app.assessment_pages import force_panel
    force_panel.render({"state": "no_model"}, st)


def test_no_model_degrades_to_one_caption() -> None:
    at = AppTest.from_function(_render_no_model).run()
    assert not at.exception
    assert "force model artifact is missing" in _body(at)


# ------------------------------------------------------------------ figure geometry
def test_strip_figure_band_and_marker_geometry() -> None:
    band = {"lo": 100.0, "hi": 200.0, "lo95": 80.0, "hi95": 220.0,
            "predicted": 150.0, "sex": "M"}
    fig = fp.force_strip_figure(band, 170.0, 430.0)
    assert tuple(fig.layout.xaxis.range) == (0, 430.0)
    rects = [s for s in fig.layout.shapes if s.type == "rect"]
    assert {(r.x0, r.x1) for r in rects} == {(80.0, 220.0), (100.0, 200.0)}
    assert abs(fig.data[0].x[0] - 170.0) < 1e-6        # the measured diamond
    # band-only (no measured): no scatter trace, band rects still there
    fig2 = fp.force_strip_figure(band, None, 430.0)
    assert len(fig2.data) == 0 and len([s for s in fig2.layout.shapes if s.type == "rect"]) == 2
    # negative band edge is clamped to the 0 Nm axis floor
    neg = dict(band, lo=-10.0, lo95=-30.0)
    fig3 = fp.force_strip_figure(neg, None, 430.0)
    assert min(s.x0 for s in fig3.layout.shapes if s.type == "rect") == 0.0

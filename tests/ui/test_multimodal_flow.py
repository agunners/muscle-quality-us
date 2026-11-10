"""L3a — AppTest click-through of the v8 3-MODALITY assessment flow (the first automated coverage
of the routed branches; the rest of the suite walks AGZ855, a legacy single-sweep study).

Walks the REAL 3-modality study `anonaam` (study_code AKA001 — captures/c1_femur_deep + c2_rf_shallow on
disk) through picker -> per-modality review -> assessment, and asserts the Phase-A shape (3 picker
tabs, modality-aware sidebar, no sweep femur pre-scan, segmentation-only RF, no representative-frame
gate, the femur+RF two-pane, the sweep anchor GONE) and the Phase-B truth fixes (story caption
softened, RF echo-intensity caveat, cohort-source-honoring headline percentile).

Heavy seams mocked in conftest (model / _ensure_predicted_masks / run_pipeline); the GPU routing seam
(route_study_aggregate) is mocked by the `ui_app_multimodal` fixture. study_modalities stays REAL.
Skipped if the AKA001 study is not on disk."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))
sys.path.insert(0, str(_REPO))
import preflight_helpers.env_bootstrap  # noqa: F401,E402

from preflight_helpers import env  # noqa: E402
from preflight_helpers.study_discovery import study_modalities, list_frame_bases  # noqa: E402
from preflight_helpers.cohort_placement import percentile  # noqa: E402

# Resolve the AKA001 study folder on disk (skip the module if absent — e.g. a clean checkout).
_ANONAAM = sorted((env.STUDIES_ROOT).glob("anonabx/*/*anonaam_anonacu*")) if env.STUDIES_ROOT.exists() else []
pytestmark = pytest.mark.skipif(not _ANONAAM, reason="AKA001 (anonaam) 3-modality study not in studies_root")


# --- widget helpers (mirror tests/ui/test_appflow.py) ----------------------

def _widget(at, kind: str, key: str):
    pool = list(getattr(at, kind))
    try:
        pool += list(getattr(at.sidebar, kind))
    except Exception:
        pass
    for el in pool:
        if getattr(el, "key", None) == key:
            return el
    raise KeyError(f"{kind}[{key!r}] not found; present: {[getattr(e, 'key', None) for e in pool]}")


def _has(at, kind: str, key: str) -> bool:
    try:
        _widget(at, kind, key)
        return True
    except KeyError:
        return False


def _click(at, key: str) -> None:
    _widget(at, "button", key).click()
    at.run()


def _select_modality(at, role: str) -> None:
    """Drive the keyed picker selector (issue 3: st.radio, not st.tabs). set_value takes the RAW option
    (the role), NOT the format_func display label. The selection PERSISTS across a pick rerun because the
    radio's option labels are STABLE (the modality name, no live count) — a keyed radio whose labels
    change is reset by Streamlit, which was the original bounce-back-to-Femur bug."""
    _widget(at, "radio", "picker_modality").set_value(role)
    at.run()


def _gate_frame(at, fb: str) -> None:
    """3-gate cascade usable / perfect / use-for-analysis = Yes. NEVER 'No' — a dropped frame
    renders the structured drop-reason group, tripping the AppTest conditional-keyed-widget gotcha."""
    _widget(at, "radio", f"g1_{fb}").set_value("Yes")
    at.run()
    _widget(at, "radio", f"g2_{fb}").set_value("Yes")
    _widget(at, "radio", f"g3_{fb}").set_value("Yes")
    at.run()


def _all_text(at) -> str:
    out: list[str] = []
    for kind in ("markdown", "caption", "info", "warning", "success", "error",
                 "metric", "subheader", "title", "header"):
        try:
            for el in getattr(at, kind):
                v = (getattr(el, "value", None) or getattr(el, "body", None)
                     or getattr(el, "label", None))
                if v:
                    out.append(str(v))
        except Exception:
            pass
    return "\n".join(out)


def _pick_key_for(at, study_path) -> str:
    """The picker button key for a study ON DISK, resolved rather than typed.

    `pick_AKA001` was hardcoded until 2026-08-03, when the 23 newly ingested studies made display
    codes ambiguous: three studies now claim AKA001, so the picker renders `AKA001-B752` etc. and the
    literal key stopped existing. The display code is documented in the registry as "a LABEL, never a
    key", so a test must not treat it as one — match on the study FOLDER, which is unique by
    construction, and take whatever code the app assigned it."""
    from preflight_helpers.study_discovery import discover_all_studies
    for e in discover_all_studies():
        if e.study_path.name == study_path.name:
            return f"pick_{e.meta.study_code}"          # post-_disambiguate_display_codes
    raise AssertionError(f"the picker does not list {study_path.name}")


def _to_picker(at):
    """Phases 0->2: pick the anonaam 3-modality study, load the model.
    Returns (entry, femur_fbs, rf_fbs, sweep_fbs)."""
    at.run()
    assert at.session_state["phase"] == 0
    assert not at.exception, at.exception
    _click(at, _pick_key_for(at, _ANONAAM[0]))
    assert at.session_state["phase"] == 1
    _click(at, "load_model_continue")
    assert at.session_state["phase"] == 2
    entry = at.session_state["study_entry"]
    mods = study_modalities(entry.study_path)
    assert "femur" in mods and "rf" in mods, f"AKA001 should be 3-modality; got {sorted(mods)}"
    fem = list_frame_bases(mods["femur"].raw_image_dir)[:2]
    rf = list_frame_bases(mods["rf"].raw_image_dir)[:2]
    swp = list(at.session_state["available_frame_bases"])[:2]
    return entry, fem, rf, swp


def _pick_all(at, fem, rf, swp) -> None:
    # Only the ACTIVE modality's grid renders (issue 3). Select each modality ONCE — the keyed radio
    # with stable labels keeps that modality selected across the pick reruns (the issue-3 fix), so the
    # remaining picks in a group land without re-selecting.
    _select_modality(at, "femur")
    for fb in fem:
        _click(at, f"pick_femur_{fb}")
    _select_modality(at, "rf")
    for fb in rf:
        _click(at, f"pick_rf_{fb}")
    _select_modality(at, "sweep")
    for fb in swp:
        _click(at, f"pick_{fb}")
    _click(at, "seg_continue")
    assert at.session_state["phase"] == 3


def _gate_and_lock(at, fbs) -> None:
    for fb in fbs:
        _gate_frame(at, fb)
    _click(at, "lock_gates")


# --- tests -----------------------------------------------------------------

def test_multimodal_routed_shape(ui_app_multimodal) -> None:
    at = ui_app_multimodal
    entry, fem, rf, swp = _to_picker(at)

    # Picker (phase 2): a keyed modality SELECTOR (radio, not tabs — it persists across the pick
    # rerun so a center-RF pick no longer bounces back to Femur, Akadir issue 3), the femur cine's
    # OWN pre-scan, and NO sweep femur pre-scan (gated off on multimodal — Akadir issue 1).
    sel = _widget(at, "radio", "picker_modality")
    _opts = list(sel.options)   # AppTest exposes the DISPLAY labels (format_func output), one per role
    assert any("Femur" in o for o in _opts), _opts
    assert any("Center-RF" in o for o in _opts), _opts
    assert any("Sweep" in o for o in _opts), _opts
    assert _has(at, "button", "femur_cine_scan_btn")
    assert not _has(at, "button", "femur_scan_btn"), "sweep femur pre-scan must be gated off on multimodal"
    assert "center-RF" in _all_text(at)   # modality-aware sidebar counts

    _pick_all(at, fem, rf, swp)

    # Per-modality review (phase 3): named review, RF purpose caption, NO representative-frame button.
    p04_text = _all_text(at)
    assert "Per-modality review" in p04_text
    assert "Sweep (lateral" in p04_text   # the named texture-robustness sweep block
    assert not any(_has(at, "button", f"make_repr_{fb}") for fb in fem + rf + swp), \
        "the representative-frame designation is retired on a multimodal study"

    _gate_and_lock(at, fem + rf + swp)

    # Assessment (phase 4): the femur+RF two-pane, the sweep anchor gone, routed correctly.
    assert at.session_state["phase"] == 4
    assert not at.exception, at.exception
    assert sorted(at.session_state["run_result"]["routed_modalities"]) == ["femur", "rf"]
    # Phase 3: the run record captures the RF-texture provenance (the synthetic views agree -> verified;
    # 'texture_choice' key present, None = auto) so the cross-run view + running median can read it.
    _rec = at.session_state["run_result"]["record"]
    assert _rec.get("texture_state") == "verified" and "texture_choice" in _rec
    txt = _all_text(at)
    assert "Femur cine — all measurements" in txt   # femur view → ALL measurements (Akadir 2026-07-14)
    assert "Center-RF cine — echo texture" in txt   # center-RF view → echo texture ONLY
    assert "Echo cross-check" in txt            # the long sweep's only job (own-study EI cross-check)
    assert "Anchor = " not in txt, "the legacy single sweep anchor must not render on a routed study"
    # Story caption softened to acquisition-level; the locked label retained.
    assert "parenchyma" not in txt and "fatty/fibrotic" not in txt
    assert "fascicular contrast" in txt
    # RF echo-intensity is gain/probe-sensitive — surfaced on the int_cv card, routed-from-RF.
    assert "measured on the center-RF cine" in txt


def test_multimodal_picker_keeps_active_modality_across_pick(ui_app_multimodal) -> None:
    """Issue 3: selecting Center-RF then picking a frame must KEEP the picker on Center-RF — a pick
    reruns, and the keyed radio with STABLE labels persists, so it must NOT bounce back to Femur. (The
    original bug embedded the live pick count in the radio label; the changing label reset the keyed
    widget to its default. Reproduced live + in AppTest, and fixed by stabilising the labels.)"""
    at = ui_app_multimodal
    entry, fem, rf, swp = _to_picker(at)
    _select_modality(at, "rf")
    assert at.session_state["picker_modality"] == "rf"
    _click(at, f"pick_rf_{rf[0]}")
    assert at.session_state["picker_modality"] == "rf", "picking an RF frame must not bounce back to femur"
    # a SECOND RF pick lands without re-selecting → proves the grid stayed on Center-RF
    _click(at, f"pick_rf_{rf[1]}")
    assert at.session_state["picker_modality"] == "rf"
    rf_picks = at.session_state["picked_frames__rf"] if "picked_frames__rf" in at.session_state else []
    assert sorted(rf_picks) == sorted(rf[:2])


def test_multimodal_segment_gate_requires_both_views(ui_app_multimodal) -> None:
    """Segment + continue must stay BLOCKED until BOTH measurement views are picked (Akadir issue 2):
    a femur-only selection names the missing center-RF view and does not advance; adding an RF pick
    clears the block. (The gate caption and the button's disabled state share one predicate, so the
    caption is a faithful proxy; the disabled visual is also verified in the render pass.)"""
    at = ui_app_multimodal
    entry, fem, rf, swp = _to_picker(at)
    # Pick ONLY a femur frame.
    _select_modality(at, "femur")
    _click(at, f"pick_femur_{fem[0]}")
    assert "still need: center-RF" in _all_text(at), "femur-only must block + name the missing view"
    seg = _widget(at, "button", "seg_continue")
    if hasattr(seg, "disabled"):
        assert seg.disabled, "seg_continue must be disabled with a femur-only pick"
    # Add a center-RF pick → the block clears.
    _select_modality(at, "rf")
    _click(at, f"pick_rf_{rf[0]}")
    assert "still need" not in _all_text(at), "picking both views must clear the gate"
    seg2 = _widget(at, "button", "seg_continue")
    if hasattr(seg2, "disabled"):
        assert not seg2.disabled


@pytest.mark.parametrize("source", ["old", "new", "mixed"])
def test_multimodal_headline_percentile_honors_cohort_source(ui_app_multimodal, source) -> None:
    """The headline per-card percentile places against the SELECTED population reference (old / new /
    mixed) — consistent with the box plots + landmark (Akadir #17). Exercises the real p01 toggle values
    end-to-end. Set the source BEFORE the first run to sidestep the cache."""
    at = ui_app_multimodal
    at.session_state["cohort_source"] = source
    entry, fem, rf, swp = _to_picker(at)
    _pick_all(at, fem, rf, swp)
    _gate_and_lock(at, fem + rf + swp)
    assert at.session_state["phase"] == 4
    rd = at.session_state["run_result"]
    agg = rd["aggregate"]
    age, sex = entry.meta.age, entry.meta.sex
    for f in ("rf_thickness_mm", "full_thickness_q50_mm"):
        exp = percentile(f, agg.get(f), age=age, sex=sex, source=source).overall
        got = (rd["placements"].get(f) or {}).get("overall")
        assert got == exp, f"{f}: headline placement {got} != cohort '{source}' percentile {exp}"

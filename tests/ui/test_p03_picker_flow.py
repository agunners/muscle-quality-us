"""L3a AppTest — the p03 frame picker driven through the REAL app flow.

Guards the 2026-06-09 study-level under-gain demotion: when dark_clip_high fires
across ~every frame of a study (a pervasively under-gained acquisition), the
picker shows ONE study-level banner and SUPPRESSES the per-frame dark cue —
instead of painting "large dark areas" on every thumbnail (the AKA9001 flood).

Drives the real assessment_app.py via the ui_app fixture; the only heavy seam
mocked is the GPU sweep scan (scan_sweep_for_femur), replaced with a synthetic
mostly-dark scan of the same shape.
"""
from __future__ import annotations

from labeling_app.assessment_pages import femur_scan


def _widget(at, kind, key):
    pool = list(getattr(at, kind))
    try:
        pool += list(getattr(at.sidebar, kind))
    except Exception:
        pass
    for el in pool:
        if getattr(el, "key", None) == key:
            return el
    raise KeyError(f"{kind}[{key!r}] not found; have "
                   f"{[getattr(e, 'key', None) for e in pool]}")


def _fake_dark_scan(frame_bases, learn, bm, raw_dir, *, mask_cache, final_cache,
                    image_quality_cache=None, progress=None):
    """90% of frames fire dark_clip_high -> the study is pervasively under-gained.
    Shape mirrors femur_scan.scan_sweep_for_femur's return."""
    out = []
    for i, fb in enumerate(frame_bases):
        dark = (i % 10) != 0     # 90% dark, crosses the 0.8 pervasive threshold
        iq = {
            "image_quality_validity_flags": ["dark_clip_high"] if dark else [],
            "image_quality_pending_calibration_flags": (
                ["dark_clip_high"] if dark else []),
            "image_quality_detector_metadata": (
                {"dark_clip_high": {"pending_calibration": True}} if dark else {}),
            "image_quality_aggregate_tier": "valid",
        }
        if image_quality_cache is not None:
            image_quality_cache[fb] = iq
        out.append({"frame_base": fb, "tier": 1, "label": "Tier 1",
                    "provenance": "standard_qmt", "femur_present": True,
                    "image_quality": iq})
    return out


def test_pervasively_dark_study_demotes_the_per_frame_flood(ui_app, monkeypatch):
    monkeypatch.setattr(femur_scan, "scan_sweep_for_femur", _fake_dark_scan)
    at = ui_app
    at.run()
    _widget(at, "button", "pick_AGZ855").click(); at.run()
    _widget(at, "button", "load_model_continue").click(); at.run()
    # On the frame picker now — run the (mocked) sweep pre-scan.
    _widget(at, "button", "femur_scan_btn").click(); at.run()
    assert not at.exception

    # ONE study-level under-gain banner (st.warning).
    warns = " ".join(str(getattr(w, "value", "")) for w in at.warning)
    assert "under-gained" in warns, \
        f"no study-level under-gain banner rendered; warnings={warns!r}"

    # The per-frame dark cue is SUPPRESSED — not painted on every thumbnail.
    md = " ".join(str(getattr(m, "value", "")) for m in at.markdown)
    assert "large dark areas" not in md, \
        "per-frame dark cue still painted on thumbnails under study-level demotion"


def _fake_clean_scan_with_masks(frame_bases, learn, bm, raw_dir, *, mask_cache,
                                final_cache, image_quality_cache=None, progress=None):
    """Clean frames with DECREASING RF area (frame i is wider the smaller i is) —
    populates mask_cache so the REAL repr_scorer.score_sweep ranks them, making
    frames 0,1,2 the three largest-clean → the picker's recommended set."""
    import numpy as np
    from labeling_app.measurements import LABEL_RF
    out = []
    for i, fb in enumerate(frame_bases):
        w = max(4, 100 - i * 3)                       # width shrinks with i
        m = np.zeros((128, 128), dtype=int)
        m[50:70, 64 - w // 2:64 + w // 2] = LABEL_RF  # centered RF rectangle
        mask_cache[fb] = m
        iq = {"image_quality_validity_flags": [],
              "image_quality_pending_calibration_flags": [],
              "image_quality_aggregate_tier": "valid"}
        if image_quality_cache is not None:
            image_quality_cache[fb] = iq
        out.append({"frame_base": fb, "tier": 1, "label": "Tier 1",
                    "provenance": "standard_qmt", "femur_present": True,
                    "image_quality": iq})
    return out


def test_picker_recommends_top3_largest_clean_before_selection(ui_app, monkeypatch):
    monkeypatch.setattr(femur_scan, "scan_sweep_for_femur", _fake_clean_scan_with_masks)
    at = ui_app
    at.run()
    _widget(at, "button", "pick_AGZ855").click(); at.run()
    _widget(at, "button", "load_model_continue").click(); at.run()
    _widget(at, "button", "femur_scan_btn").click(); at.run()
    assert not at.exception

    # NOTHING is picked yet — the recommendation is surfaced BEFORE selection, so
    # the operator sees the three best frames without adding them first.
    md = " ".join(str(getattr(m, "value", "")) for m in at.markdown)
    assert md.count("recommended") == 3, \
        f"expected exactly 3 'recommended' badges before selection, got {md.count('recommended')}"
    assert "largest clean RF" in md


def test_picker_drops_ai_suggests_as_repr_after_selecting_a_frame(ui_app, monkeypatch):
    monkeypatch.setattr(femur_scan, "scan_sweep_for_femur", _fake_clean_scan_with_masks)
    at = ui_app
    at.run()
    _widget(at, "button", "pick_AGZ855").click(); at.run()
    _widget(at, "button", "load_model_continue").click(); at.run()
    _widget(at, "button", "femur_scan_btn").click(); at.run()
    # select a frame
    pick = next(b for b in at.button if str(getattr(b, "key", "")).startswith("pick_"))
    pick.click(); at.run()
    assert not at.exception
    md = " ".join(str(getattr(m, "value", "")) for m in at.markdown)
    # the old per-pick repr hint is GONE (it conflicted with "recommended" and
    # could land on a non-recommended picked frame); repr is confirmed on p04.
    assert "AI suggests as repr" not in md
    assert "recommended" in md          # the selection guidance stays through a pick

"""Serve-side payload for the assessment page's Strength card.

One question in, one dict out: for a study_uid, what may the card honestly show?
    payload_for_uid(uid) -> {state, measured, held, no_session, band, band_refusal,
                             concordance, model, uid}

Everything is read at serve time from the two artifacts that own the facts:
  * `data/qa_master.csv` — the ledger-gated analysis substrate (new-round rows). A row the
    measurement gate holds serves NO criterion cells there by construction (the no-force rule),
    so this module physically cannot quote a held value; it can only *say* a hold exists
    (`measurement_flags` receipt).
  * `data/cohort_stats/force_model.json` — the frozen expected-range model (force_model.py).
    Prediction here is a plain dot product; sklearn never enters the app.

Columns are read through an explicit whitelist — `study_name`, `rel_path`, operator identities
and every other PHI-adjacent column are never loaded (see StudyMeta's PHI note).

Display tiers implement the ratified CV policy (2026-08-11: "CV<=10% only where a per-subject
criterion value is displayed; flag 10-15, drop nothing") — quotable <=10, marginal 10-15,
unstable >15 (value not quoted). Analysis row sets are untouched by all of this.

Pure logic lives in build_payload(row, art) so tests can drive every state without a file.
Contract: docs/force_card_spec.md.
"""
from __future__ import annotations

import csv
import json
import math
import os
from typing import Optional

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
QA_CSV = os.environ.get("FORCE_QA_CSV") or os.path.join(_ROOT, "data", "qa_master.csv")
MODEL_JSON = (os.environ.get("FORCE_MODEL_JSON")
              or os.path.join(_ROOT, "data", "cohort_stats", "force_model.json"))

# The only qa_master columns this module is allowed to see.
WHITELIST = [
    "study_uid", "cohort_round", "izo_torque_nm", "izo_cv_pct", "measurement_flags",
    "age", "sex", "height_cm", "weight_kg",
    "rf_thickness_mm", "full_thickness_q50_mm", "visible_rf_area_mm2", "qmt_mm",
]

CV_QUOTABLE_MAX = 10.0     # ratified display gate
CV_MARGINAL_MAX = 15.0

_cache: dict = {}


def _mtimed(path: str, loader):
    """Reload-on-change cache: one parse per file version, shared across reruns."""
    try:
        key = (path, os.path.getmtime(path))
    except OSError:
        return None
    if _cache.get("key_" + path) != key:
        _cache["key_" + path] = key
        _cache["val_" + path] = loader(path)
    return _cache["val_" + path]


def _load_artifact(path: str) -> Optional[dict]:
    # unreadable == absent: a corrupt artifact degrades to the visible no_model caption
    # instead of an exception the page's fail-silent wrapper would swallow (red-team M3).
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def _load_qa_rows(path: str) -> dict:
    rows = {}
    for r in csv.DictReader(open(path, encoding="utf-8")):
        if r.get("cohort_round") != "new":
            continue
        rows[r.get("study_uid", "")] = {k: (r.get(k) or "").strip() for k in WHITELIST}
    return rows


def artifact() -> Optional[dict]:
    return _mtimed(MODEL_JSON, _load_artifact)


def qa_row(study_uid: str) -> Optional[dict]:
    rows = _mtimed(QA_CSV, _load_qa_rows)
    return (rows or {}).get(study_uid)


def _num(row: dict, col: str) -> Optional[float]:
    v = (row.get(col) or "").strip()
    if v in ("", "None"):
        return None
    try:
        x = float(v)
    except ValueError:
        return None
    # a nan criterion would compare False against both band edges and read "within";
    # the harness guards isfinite (force_ladder.py:65) — so does the serve path.
    return x if math.isfinite(x) else None


def _measured(row: dict) -> Optional[dict]:
    """The measured side: value + its own session repeatability, tiered per the CV policy."""
    value = _num(row, "izo_torque_nm")
    if value is None:
        return None
    cv = _num(row, "izo_cv_pct")
    if cv is None:
        tier, note = "marginal", "session repeatability not recorded"
    elif cv <= CV_QUOTABLE_MAX:
        tier, note = "quotable", None
    elif cv <= CV_MARGINAL_MAX:
        tier, note = "marginal", "above the ≤10% repeatability target"
    else:
        tier, note = "unstable", (f"session repeats varied ±{cv:.0f}% — beyond the "
                                  f"{CV_MARGINAL_MAX:.0f}% display limit, so the value is not quoted")
    if tier == "unstable":
        # structural, not advisory: the withheld value is NOT in the payload at all, so no
        # consumer (figure, copy, future caller) can leak it by forgetting the display flag.
        return {"value": None, "cv_pct": cv, "session_sd_nm": None,
                "tier": tier, "note": note, "display_value": False}
    return {"value": value, "cv_pct": cv,
            "session_sd_nm": round(value * cv / 100.0, 1) if cv is not None else None,
            "tier": tier, "note": note, "display_value": True}


def _band(row: dict, art: dict) -> tuple[Optional[dict], Optional[list]]:
    """The expected range: ridge prediction + sex-stratified OOF residual quantiles.
    Refuses (None, reasons) on missing features or any feature outside the fit envelope."""
    sex = (row.get("sex") or "").strip()
    feats = {}
    missing = []
    for c in art["features"]:
        if c == "sex_m":
            if sex in art["sex_encoding"]:
                feats[c] = art["sex_encoding"][sex]
            else:
                missing.append("sex")
        else:
            v = _num(row, c)
            if v is None:
                missing.append(c)
            else:
                feats[c] = v
    if missing:
        return None, [f"missing input: {', '.join(missing)}"]
    outside = []
    for c in art["features"]:
        lo, hi = art["envelope"][c]
        if not (lo <= feats[c] <= hi):
            label = "sex" if c == "sex_m" else c
            outside.append(f"{label} {feats[c]:g} outside the fitted range [{lo:g}, {hi:g}]")
    if outside:
        return None, outside
    z = [(feats[c] - m) / s for c, m, s in
         zip(art["features"], art["scaler_mean"], art["scaler_scale"])]
    pred = art["intercept"] + sum(zi * ci for zi, ci in zip(z, art["coef"]))
    q = art["residual_quantiles"][sex]
    return {"predicted": round(pred, 1), "sex": sex,
            "lo": round(pred + q["q10"], 1), "hi": round(pred + q["q90"], 1),
            "lo95": round(pred + q["q2_5"], 1), "hi95": round(pred + q["q97_5"], 1)}, None


def build_payload(row: Optional[dict], art: Optional[dict]) -> dict:
    """Pure assembly of the card payload — every state in the spec's table, no I/O."""
    if art is None:
        return {"state": "no_model"}
    model = {
        "n": art["n"], "n_by_sex": art["n_by_sex"],
        "r2": art["oof_receipt"]["r2_mean"], "r2_sd": art["oof_receipt"]["r2_sd"],
        "rmse_nm": art["oof_receipt"]["rmse_mean"],
        "band_label": art["band"]["label"],
        "target_min_nm": art["target_stats"]["min_nm"],
        "target_max_nm": art["target_stats"]["max_nm"],
        "wobble_nm": art["within_subject_sd_nm"],
        "features": art["features"], "date": art["date"], "version": art["version"],
    }
    if row is None:
        return {"state": "not_in_substrate", "model": model}
    held = ({"receipt": row["measurement_flags"]} if (row.get("measurement_flags") or "").strip()
            else None)
    measured = None if held else _measured(row)
    no_session = held is None and measured is None
    band, band_refusal = _band(row, art)

    concordance = None
    if band and measured and measured["display_value"]:
        v = measured["value"]
        if v < band["lo"]:
            concordance = {"verdict": "below", "margin_nm": round(band["lo"] - v, 1)}
        elif v > band["hi"]:
            concordance = {"verdict": "above", "margin_nm": round(v - band["hi"], 1)}
        else:
            concordance = {"verdict": "within", "margin_nm": 0.0}

    showable_measured = measured is not None and measured["display_value"]
    if band and showable_measured:
        state = "full"
    elif band:
        state = "expected_only"
    elif showable_measured:
        state = "measured_only"
    else:
        state = "refused"
    return {"state": state, "uid": row.get("study_uid"), "measured": measured, "held": held,
            "no_session": no_session, "band": band, "band_refusal": band_refusal,
            "concordance": concordance, "model": model}


def payload_for_uid(study_uid: Optional[str]) -> Optional[dict]:
    """The app entry point. None only when there is no uid to ask about. Never raises:
    artifact schema drift or a malformed substrate degrades to the visible no_model
    caption rather than an exception the page's fail-silent wrapper would hide."""
    if not study_uid:
        return None
    try:
        return build_payload(qa_row(study_uid), artifact())
    except Exception:
        return {"state": "no_model"}


def should_render(payload: Optional[dict], is_new_round: bool) -> bool:
    """The page-side gate (v1 scope: new-round only). A study missing from the substrate
    gets the bring-it-in hint ONLY if it is new-round (`study_is_new_round` — the
    docstring-owned legacy/new split). A legacy study renders nothing: it has no
    dynamometer data, and telling its operator to 'run ingest' would be wrong advice.
    History: the first cut gated on `study_modalities()` truthiness, which reports a
    'sweep' role for EVERY study with raw frames — the card appeared on legacy AGZ855;
    caught by the browser loop AND the red-team (B1), 2026-08-12."""
    if payload is None:
        return False
    if payload.get("state") != "not_in_substrate":
        return True
    return bool(is_new_round)

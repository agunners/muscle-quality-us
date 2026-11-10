"""Unified per-study QA — ONE computation from the raw zip, rendered to two audiences.

Supersedes the split between `check_study` (acquisition, absolute-depth — buggy on the tweaked-deeper
femurs the v8 protocol now encourages) and `capture_audit` (score-integrity). Both re-derived the same
facts (roles, depths, pairing) their own way and could contradict each other — Kontrol once said
"femur eksik" on the exact study the audit called CLEAN. This computes those facts ONCE, from `plan_zip`
(the intake-time relative-rank classifier + DICOM pairing, ~0.03 s, torch-free, no writes); the operator's
Kontrol cell renders from it, and the analyst audit (capture_audit) reads the SAME relative-rank model via
the manifest plan_zip writes. So the two ride one model and stop contradicting each other where they
overlap — two renders over disjoint, deduped scopes (acquisition here; score-integrity in capture_audit),
not one identical verdict.

The model (materialize's, Akadir-ratified — "no fixed protocol depths"):
  * ROLES by RELATIVE RANK — femur = the deepest cine, RF = the shallowest short cine, sweep = the
    longest clip. Robust to a tweaked-deeper femur and to probe 831 vs 829.
  * RF is the ONE fixed anchor (~4.9 cm) — the single absolute check: if even the shallowest capture is
    deep, no real RF was recorded. Femur + sweep depths float.
  * TWO-WITNESS geometry — a capture's depth from its jpg width (1967/width) must agree with its paired
    DICOM depth (Rows x PixelSpacing). Disagreement = the wrong ruler (the AKA017 +14.5% bug). The 128L
    width<->depth constant is pilot-confirmed (docs/leltek_128L_depth_deconfliction.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from materialize_studies import plan_zip
from preflight_helpers.leltek_deconfliction import depth_from_jpg_width

RF_DEPTH_CM = 4.9          # the one fixed anchor: the RF capture should sit here
RF_DEPTH_TOL_CM = 0.6      # within this of 4.9 = anchored (accepts 4.93); a device step off is a soft verify
RF_FEMUR_PLANE_CM = 6.4    # RF at/above this = on the FEMUR plane = no real shallow RF (re-record -> fail)
GEOM_TOL_CM = 0.35         # the two depth witnesses (width vs DICOM) must agree within this
_SCORED = ("femur", "rf")  # the score rides on these; the sweep is EI-cross-check only


@dataclass
class CaptureQA:
    label: str
    role: str                          # femur | rf | sweep | unknown
    width_px: Optional[int]
    width_depth_cm: Optional[float]    # witness 1: 1967 / width (the universal signal)
    dcm_depth_cm: Optional[float]      # witness 2: Rows x PixelSpacing (the paired .dcm), or None
    has_cine: bool                     # True = mp4-backed; False = a stills-only orphan
    flags: List[str] = field(default_factory=list)


@dataclass
class StudyQA:
    study: str
    counts: Dict[str, int]             # {femur, rf, sweep}
    captures: List[CaptureQA]
    verdict: str                       # ok | warn | fail
    reasons: List[str]                 # flags that drive the verdict (operator + analyst)
    notes: List[str] = field(default_factory=list)   # surfaced but non-flag (sweep-orphan, geometry)


def study_qa(zip_path: str, operator: str, *, study: Optional[str] = None,
             declared_profile: Optional[str] = None, brightness: Optional[float] = None,
             plan=None) -> StudyQA:
    """The single intake-time QA record. Reads only the raw zip (via plan_zip); no materialize needed.

    `plan`: an already-built StudyPlan to reuse instead of re-planning. materialize_studies passes
    its own plan here so it can stamp THIS verdict into the manifest without re-reading the zip —
    and, critically, without recursing (plan_zip is what would otherwise be called again). Only the
    capture / DICOM fields are read; `plan.verdict` never is.
    """
    study = study or Path(zip_path).stem
    p = plan if plan is not None else plan_zip(zip_path, operator)

    caps = ([p.primary] if p.primary else []) + list(p.annex)
    rows: List[CaptureQA] = []
    for c in caps:
        wdepth = round(depth_from_jpg_width(c.width), 2) if c.width else None
        is_sweep_primary = (p.primary is not None and c is p.primary)
        dcm_member = (p.primary_dcm if is_sweep_primary else p.dcm_for.get(c.raw_folder))
        ddepth = p.dcm_depths.get(dcm_member) if dcm_member else None
        flags: List[str] = []
        # two-witness geometry: the jpg-width depth and the paired-DICOM depth must agree (AKA017).
        if wdepth is not None and ddepth is not None and abs(wdepth - ddepth) > GEOM_TOL_CM:
            flags.append(f"RULER_MISMATCH(width {wdepth} vs dcm {ddepth})")
        rows.append(CaptureQA(
            label="sweep" if is_sweep_primary else Path(c.raw_folder).name,
            role=c.identity, width_px=c.width, width_depth_cm=wdepth, dcm_depth_cm=ddepth,
            has_cine=bool(c.mp4), flags=flags))

    counts = {k: sum(1 for c in caps if c.identity == k) for k in ("femur", "rf", "sweep")}
    reasons: List[str] = []
    # geometry surfacing (always present): per capture, what we have + whether the two witnesses agree
    notes: List[str] = [
        f"{r.label}({r.role}): genişlik→{r.width_depth_cm}cm · "
        f"dcm→{(str(r.dcm_depth_cm) + 'cm') if r.dcm_depth_cm is not None else 'yok'}" for r in rows]

    # nothing parsed (a v7 / bad zip plan_zip couldn't read) — defer, do NOT report a false 'all eksik'.
    # Surface the planner's OWN reason when it has one. Without it every hold reads "bozuk zip",
    # which told an operator their recording was corrupt when the real causes were a zip exported
    # with no RawImages and a shallow plane recorded one device step deep — two different problems,
    # one of them the operator could have fixed on the spot, neither of them a corrupt file
    # (2026-08-03). `plan.reason` was computed and then never read.
    if not caps:
        why = (getattr(p, "reason", "") or "").strip()
        return StudyQA(study=study, counts=counts, captures=rows, verdict="warn",
                       reasons=[f"kayıt işlenemedi — {why} (ertelendi)" if why
                                else "kayıt okunamadı — v8 dışı / bozuk zip (ertelendi)"],
                       notes=notes)
    # subcohort (both-legs, 8-capture methodology arm) is a DIFFERENT protocol — DEFERRED (Akadir
    # 2026-07-07: focus the v8 QA on the single-leg 66; refine + fold subcohort in later). Do NOT run the
    # v8-main completeness on it (it would false-fail); surface a clear 'deferred' so nothing silently passes.
    if declared_profile == "subcohort" or len(caps) > 4:
        return StudyQA(study=study, counts=counts, captures=rows, verdict="warn",
                       reasons=[f"subkohort ({len(caps)} kayıt) — v8 tek-bacak QA kapsamı dışı (ertelendi)"],
                       notes=notes)

    # ---- completeness (RELATIVE-RANK counts — a deep femur counts as the femur, no false eksik) ----
    if counts["femur"] == 0:
        reasons.append("femur eksik")
    if counts["sweep"] == 0:
        reasons.append("sweep eksik")

    # ---- the ONE absolute check: is a real RF (shallow ~4.9) present? ----
    # RF is the one fixed anchor, but the accept band must be device-STEP-aware: the 128L steps (4.0 / 4.9 /
    # 5.9) are ~1cm apart — WIDER than the ±0.6 band — so a two-tier, DIRECTION-aware check. On the femur
    # plane (>=6.4) = no real shallow RF -> re-record (fail). One step off 4.9 (a ~4.0 shallow or ~5.9 deep) =
    # a soft 'verify', not a re-record. (Was a single symmetric ±0.6 that called BOTH neighbours "gerçek RF
    # yok", mislabelling 6/66 well-formed studies incl. a textbook 6.9 femur — red-team 2026-07-07.)
    rf_rows = [r for r in rows if r.role == "rf"]
    if counts["rf"] == 0:
        reasons.append("RF eksik")                              # no RF capture at all -> fail
    elif rf_rows and all(r.width_depth_cm is not None for r in rf_rows):
        d = rf_rows[0].width_depth_cm
        if d >= RF_FEMUR_PLANE_CM:
            reasons.append(f"gerçek RF yok — RF {d}cm femur düzleminde (~4.9 bekleniyor)")
        elif abs(d - RF_DEPTH_CM) > RF_DEPTH_TOL_CM:
            reasons.append(f"RF hafif {'sığ' if d < RF_DEPTH_CM else 'derin'} ({d}cm) — doğrulayın")

    # ---- a SCORED role riding on a stills-only orphan (femur/RF with no cine) -> the score's input is bad
    for r in rows:
        if r.role in _SCORED and not r.has_cine:
            reasons.append(f"{r.role} cine yok (orphan)")
    # ---- the sweep as stills-only is a NOTE, not a flag (the sweep is not scored) ----
    for r in rows:
        if r.role == "sweep" and not r.has_cine:
            notes.append("sweep sadece sabit (cine yok)")

    # ---- ruler mismatches (per capture) drive the verdict ----
    for r in rows:
        for f in r.flags:
            reasons.append(f"{r.label}:{f.split('(')[0]}")

    # ---- brightness / washout (advisory) ----
    if brightness is not None and brightness > 90.0:
        reasons.append(f"görüntü çok parlak ({brightness})")

    # a re-record-worthy problem (a missing modality, or the RF on the femur plane = no usable RF) -> fail;
    # softer deviations (RF a step off, a ruler/orphan/brightness note) -> warn.
    verdict = ("fail" if any(("eksik" in x or "gerçek RF yok" in x) for x in reasons)
               else "warn" if reasons else "ok")
    return StudyQA(study=study, counts=counts, captures=rows, verdict=verdict,
                   reasons=reasons, notes=notes)


def _mrd(counts: Dict[str, int]) -> str:
    return f"femur{counts['femur']} rf{counts['rf']} sweep{counts['sweep']}"


def render_cell(qa: StudyQA) -> str:
    """The operator's `Kontrol Sonucu` cell — same shape as the legacy check_study headline
    (❌/⚠️/✅ {femur1 rf1 sweep1} · reasons|tam), so the sheet reads identically."""
    mr = _mrd(qa.counts)
    if qa.verdict == "fail":
        return f"❌ {mr} · " + "; ".join(dict.fromkeys(qa.reasons))
    if qa.verdict == "warn":
        return f"⚠️ {mr} · " + "; ".join(list(dict.fromkeys(qa.reasons))[:2])
    return f"✅ {mr} · tam"

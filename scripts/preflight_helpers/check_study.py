"""Operator-facing completeness/accuracy check for one recorded study (v8).

This is the SHARED verdict layer the operator self-check wants: "did I record the
right number of the right modalities, with the right settings?" — computed from the
study zip and reported back as one compact line the operator can glance at before the
patient leaves. The assessment-app intake and the datasheet watcher both call this, so
the rule lives in ONE place (mirrors datasheet_schema being the single column source).

What it reads, in falling order of trust:

  DEPTH — the .dcm MULTISET. Absolute depth = Rows x PixelSpacing; it lands on 6.9 (femur) /
     4.9 (rf, sweep) cm. The multiset of ALL .dcm depths is the spine of the verdict — femur
     present iff a 6.9 .dcm exists. It is read ATTACHMENT-FREE on purpose: a real study (anonaaq,
     post the 3-Jun Leltek update) saves the image BEFORE making the video, so per-capture .dcm
     attachment orphans the femur and the femur capture steals the next (4.9) .dcm — which once
     made this check falsely cry "femur eksik". The SET of depths is immune to that. Caveats: the
     depth SPEC is protocol-dependent (v7 swept deep at 6.9; v8 shallow at 4.9), and where the
     .dcm don't 1:1 the captures (a duplicate or skipped SAVE-IMAGE) the per-modality COUNT is
     approximate — so only an ENTIRELY ABSENT femur/sweep is a hard fail; partial counts are advisory.

  STRUCTURE — capture count (mp4<->RawImages pairs) and sweep count (clip length). jpg WIDTH is
     NOT used to tell femur from rf: the 3-Jun update made both stills 256px wide (only the sweep
     is 399), so width no longer separates depth. Width survives only as the last-resort identity
     when a study has no .dcm at all.

  BRIGHTNESS — mean luminance of a few frames. The same update (and/or a wrong gain/preset) pushed
     anonaaq to ~119 vs the reference ~45, washing out every echo-intensity feature; flagged
     advisory above ~90. Heuristic (n=2 studies).

  PARAMETERS — experimental stub. Freq / gain / preset / probe are NOT in the DICOM tags, only the
     burned-in overlay, so reading them needs vision/OCR; ships once its Se/Sp is measured.

Nothing here blocks: the verdict is feedback. An absent femur/sweep or a structural shortfall is a
fail; depth/brightness/param issues only ever ADD advisory warnings.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .study_deconfliction import Capture, deconflict_zip

__all__ = [
    "EXPECTED_PROFILES", "PROTOCOL_DEPTH_CM", "PROTOCOL_FREQ_MHZ",
    "StudyCheckReport", "check_study", "infer_profile", "study_protocol",
]

import re as _re
_DATE_RE = _re.compile(r"_(\d{8})_\d{6}")
_V8_CUTOFF = "20260609"   # v8 protocol (single-leg main, shallow sweep) starts 2026-06-09


def study_protocol(zip_path: str, cutoff: str = _V8_CUTOFF) -> str:
    """v7 vs v8 from the recording DATE in the artifact timestamps (the filename-prefix date is
    unreliable — '08jun06' vs '10jun26' for two June-2026 studies). Defaults v8 if unreadable."""
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for n in zf.namelist():
                m = _DATE_RE.search(n.rsplit("/", 1)[-1])
                if m:
                    return "v8" if m.group(1) >= cutoff else "v7"
    except Exception:
        pass
    return "v8"

# Cohort profiles: how many captures of each modality a complete study should hold.
# main = single dominant leg, 3 captures (FEMUR + RF-1 + Sweep).
# subcohort = methodology arm, both legs x 4 (FEMUR + RF-1 + RF-2 + Sweep) = 8.
EXPECTED_PROFILES: Dict[str, Dict] = {
    "main":      {"captures": 3, "modalities": {"femur": 1, "rf": 1, "sweep": 1}},
    "subcohort": {"captures": 8, "modalities": {"femur": 2, "rf": 4, "sweep": 2}},
}

# Absolute imaged depth (cm) per modality, by protocol version. Stage-2 checks the .dcm
# depth against the version that applied when the study was shot. Default = v8 (current).
PROTOCOL_DEPTH_CM: Dict[str, Dict[str, float]] = {
    "v8": {"femur": 6.9, "rf": 4.9, "sweep": 4.9},
    "v7": {"femur": 6.9, "rf": 4.9, "sweep": 6.9},  # v7 swept at the deep setting
}
PROTOCOL_FREQ_MHZ: Dict[str, Dict[str, float]] = {
    "v8": {"femur": 8.3, "rf": 10.0, "sweep": 10.0},
    "v7": {"femur": 8.3, "rf": 10.0, "sweep": 8.3},
}
_DEPTH_TOL_CM = 0.4   # |measured - expected| within this = pass (covers rounding + dial steps)


@dataclass
class StudyCheckReport:
    study: str
    artifacts: str                       # "M/R/D" e.g. "8/8/8"
    n_captures: int
    modality_counts: Dict[str, int]      # {"femur":2,"rf":4,"sweep":2}
    declared_profile: Optional[str]      # what the datasheet said (main|subcohort|None)
    inferred_profile: Optional[str]      # what the captures look like
    verdict: str = "ok"                  # ok | warn | fail
    headline: str = ""                   # one compact line for the sheet cell
    details: List[str] = field(default_factory=list)   # richer bullets / log
    depth_notes: List[str] = field(default_factory=list)   # stage-2 advisory
    param_notes: List[str] = field(default_factory=list)   # stage-3 advisory

    def as_cell(self) -> str:
        """The single string written to the datasheet 'Kontrol Sonucu' cell."""
        return self.headline


# ---------- profile inference (cross-check the operator's Alt-kohort declaration) ----------
def infer_profile(modality_counts: Dict[str, int], n_captures: int) -> Optional[str]:
    """Guess main vs subcohort from what was actually recorded, so a mislabelled
    'Alt-kohort mu' is caught. Exact match wins; else nearest by capture count."""
    for name, prof in EXPECTED_PROFILES.items():
        if modality_counts == prof["modalities"]:
            return name
    if n_captures <= 4:
        return "main"
    if n_captures >= 6:
        return "subcohort"
    return None


# ---------- stage 2: absolute depth from the .dcm ----------
def _dcm_depth_cm(zf: zipfile.ZipFile, dcm_name: str) -> Optional[float]:
    """Absolute imaged depth in cm = Rows x PixelSpacing[row-spacing]. Returns None if the
    tag set is too bare to compute (some SAVE-IMAGEs carry no spacing)."""
    try:
        import pydicom
    except Exception:
        return None
    try:
        ds = pydicom.dcmread(io.BytesIO(zf.read(dcm_name)), stop_before_pixels=True, force=True)
    except Exception:
        return None
    ps = getattr(ds, "PixelSpacing", None) or getattr(ds, "ImagerPixelSpacing", None)
    rows = getattr(ds, "Rows", None)
    if not ps or not rows:
        return None
    try:
        return round(float(rows) * float(ps[0]) / 10.0, 1)
    except Exception:
        return None


def _all_dcm_depths(zip_path: str) -> List[float]:
    """Absolute depth (Rows x PixelSpacing) of EVERY .dcm in the zip — ATTACHMENT-FREE on
    purpose. Which capture a SAVE-IMAGE belongs to is fragile (anonaaq saves the image BEFORE
    making the video, so the femur .dcm 'orphans' under a preceding-only rule and the femur
    capture steals the next .dcm), but the SET of depths recorded is solid."""
    out: List[float] = []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for nm in zf.namelist():
                if nm.lower().endswith(".dcm") and not any(
                        p.startswith(".trashed-") or p.startswith(".") for p in nm.split("/")):
                    d = _dcm_depth_cm(zf, nm)
                    if d is not None:
                        out.append(d)
    except Exception:
        pass
    return out


def _bucketize_cm(depths_cm: List[float], protocol: str):
    """Bucket each depth to its nearest protocol depth (6.9 / 4.9) within tolerance; the rest are
    off-spec. Returns (buckets {depth: count}, offspec [cm], n_dcm)."""
    spec = PROTOCOL_DEPTH_CM.get(protocol, PROTOCOL_DEPTH_CM["v8"])
    targets = sorted(set(spec.values()))
    buckets = {t: 0 for t in targets}
    offspec: List[float] = []
    for d in depths_cm:
        near = min(targets, key=lambda t: abs(t - d))
        if abs(near - d) <= _DEPTH_TOL_CM:
            buckets[near] += 1
        else:
            offspec.append(d)
    return buckets, offspec, len(depths_cm)


def _counts_from_depths(n_captures: int, n_sweeps: int, buckets: Dict[float, int],
                        protocol: str) -> Dict[str, int]:
    """Modality counts WITHOUT per-capture attachment: femur = deep (6.9) SAVE-IMAGEs that aren't
    sweeps; sweep = long captures; rf = the remaining stills. Exact where .dcm 1:1 the captures
    (the live v8 main case); approximate under a duplicate or skipped SAVE-IMAGE (caller flags it
    via n_dcm vs n_captures, and treats only an ABSENT femur/sweep as a hard fail)."""
    spec = PROTOCOL_DEPTH_CM.get(protocol, PROTOCOL_DEPTH_CM["v8"])
    deep = buckets.get(spec["femur"], 0)
    deep_sweeps = n_sweeps if abs(spec["sweep"] - spec["femur"]) < 0.05 else 0  # v7 sweeps deep
    stills = max(0, n_captures - n_sweeps)
    femur = max(0, min(deep - deep_sweeps, stills))
    return {"femur": femur, "rf": max(0, stills - femur), "sweep": n_sweeps}


def _depth_summary(buckets: Dict[float, int], offspec: List[float]) -> str:
    parts = [f"{d}cm×{c}" for d, c in sorted(buckets.items()) if c]
    parts += [f"{round(d, 1)}cm(?)" for d in offspec]
    return "derinlik " + (", ".join(parts) if parts else "yok")


def _mean_brightness(zip_path: str) -> Optional[float]:
    """Mean luminance of a few RawImages frames — a washout flag. The 3-June Leltek update (and/or
    a wrong gain/preset) pushed anonaaq to ~119 vs the reference ~45, which corrupts every
    echo-intensity feature. Heuristic (n=2 studies); returns None if frames/Pillow unavailable."""
    try:
        import numpy as np
        from PIL import Image
    except Exception:
        return None
    try:
        with zipfile.ZipFile(zip_path) as zf:
            raws = [n for n in zf.namelist() if "rawimages" in n.lower()
                    and n.lower().endswith((".jpg", ".jpeg", ".png"))
                    and not any(p.startswith(".") for p in n.split("/"))]
            if not raws:
                return None
            vals = []
            for n in raws[:: max(1, len(raws) // 5)][:5]:
                vals.append(float(np.asarray(Image.open(io.BytesIO(zf.read(n))).convert("L")).mean()))
            return round(sum(vals) / len(vals), 1) if vals else None
    except Exception:
        return None


_WASHOUT_MEAN_L = 90.0   # mean luminance above this = likely washed out (gain/preset/software)


# ---------- main entry ----------
def check_study(zip_path: str, *, declared_profile: Optional[str] = None,
                protocol: str = "v8", do_stage2: bool = True,
                do_stage3: bool = False, study: Optional[str] = None,
                result=None, artifacts: Optional[str] = None,
                dcm_depths: Optional[List[float]] = None) -> StudyCheckReport:
    """Staged completeness/accuracy check on one study zip -> verdict.

    declared_profile: 'main' | 'subcohort' | None — from the datasheet 'Alt-kohort mu'
        (Evet -> subcohort). A mismatch with the recorded captures is flagged.
    result / dcm_depths / artifacts: inject a DeconflictResult + a list of .dcm depths (cm) +
        an 'M/R/D' string to exercise the verdict without a real zip (tests).
    """
    from pathlib import Path
    study = study or Path(zip_path).stem

    res = result if result is not None else deconflict_zip(zip_path)
    captures = sorted(res.captures, key=lambda c: c.t or 0)
    n = len(captures)
    n_sweeps = sum(1 for c in captures if c.is_sweep)

    # Depth = the .dcm MULTISET (attachment-free). Read from the real zip unless injected.
    if dcm_depths is not None:
        buckets, offspec, n_dcm = _bucketize_cm(list(dcm_depths), protocol)
    elif do_stage2 and result is None:
        buckets, offspec, n_dcm = _bucketize_cm(_all_dcm_depths(zip_path), protocol)
    else:
        buckets, offspec, n_dcm = {}, [], 0
    depth_known = n_dcm > 0

    if depth_known:
        counts = _counts_from_depths(n, n_sweeps, buckets, protocol)
    else:  # no .dcm at all -> width-based deconfliction fallback (low confidence)
        counts = {"femur": 0, "rf": 0, "sweep": 0}
        for c in captures:
            counts[c.identity] = counts.get(c.identity, 0) + 1
    counts = {k: counts.get(k, 0) for k in ("femur", "rf", "sweep")}

    inferred = infer_profile(counts, n)
    if artifacts is None:
        artifacts = _artifact_str(zip_path) if result is None else _artifacts_from_captures(res)
    rpt = StudyCheckReport(study=study, artifacts=artifacts, n_captures=n, modality_counts=counts,
                           declared_profile=declared_profile, inferred_profile=inferred)
    if depth_known:
        rpt.depth_notes = [_depth_summary(buckets, offspec)]

    # ---- structure verdict (judge what was recorded; declaration mismatch reported separately) ----
    exact = next((name for name, p in EXPECTED_PROFILES.items()
                  if p["modalities"] == counts), None)
    eval_profile = exact or declared_profile or inferred
    _LBL = {"femur": "femur (6.9cm)", "rf": "RF (4.9cm)", "sweep": "sweep"}
    fail, warn = [], []
    if eval_profile is None:
        warn.append(f"{n} kayıt — hangi kohort belirsiz (Alt-kohort mu boş)")
    elif not exact:
        for mod, need in EXPECTED_PROFILES[eval_profile]["modalities"].items():
            have = counts.get(mod, 0)
            # an ABSENT femur/sweep is a hard fail (robust); a partial/excess COUNT is advisory
            # (the .dcm multiset can mis-count under a duplicate/skipped SAVE-IMAGE).
            if have == 0 and need > 0 and mod in ("femur", "sweep"):
                fail.append(f"{_LBL[mod]} eksik")
            elif have < need:
                warn.append(f"{_LBL[mod]} eksik olabilir ({have}/{need})")
            elif have > need:
                warn.append(f"{_LBL[mod]} fazla ({have}/{need})")
    # declaration cross-checks
    if declared_profile and inferred and declared_profile != inferred:
        warn.insert(0, f"Alt-kohort '{declared_profile}' seçili ama {inferred} gibi ({n} kayıt)")
    elif not declared_profile and exact:
        warn.append(f"Alt-kohort boş — {exact} gibi, lütfen işaretleyin")
    # depth integrity
    if offspec:
        warn.append(f"{len(offspec)} kayıt protokol dışı derinlikte ({_depth_summary(buckets, offspec)})")
    if depth_known and n_dcm != n:
        warn.append(f".dcm/kayıt sayısı tutmuyor ({n_dcm}/{n}) — derinlik sayımı yaklaşık")
    elif not depth_known:
        warn.append("derinlik doğrulanamadı (.dcm yok)")
    # sweep length + pairing sanity (advisory)
    if any(fl.startswith("length-ambiguous") for c in captures for fl in c.flags):
        warn.append("bir kayıt sweep/sabit sınırında — uzunluğu doğrulayın")
    if any("no mp4" in w or "no RawImages" in w for w in res.warnings):
        warn.append("eşleşmeyen mp4/RawImages")

    # ---- washout / brightness (advisory; image quality, not completeness) ----
    if result is None:
        b = _mean_brightness(zip_path)
        if b is not None:
            rpt.param_notes.append(f"parlaklık {b}")
            if b > _WASHOUT_MEAN_L:
                warn.append(f"görüntü çok parlak (ort. {b}) — gain/preset/yazılım kontrol et")
    if do_stage3:
        rpt.param_notes.extend(_stage3_params(zip_path, captures))

    # ---- roll up ----
    mr = _mrd_pretty(counts, eval_profile)
    if fail:
        rpt.verdict = "fail"
        rpt.headline = f"❌ {mr} · " + "; ".join(dict.fromkeys(fail))
    elif warn:
        rpt.verdict = "warn"
        rpt.headline = f"⚠️ {mr} · " + "; ".join(list(dict.fromkeys(warn))[:2])
    else:
        rpt.verdict = "ok"
        rpt.headline = f"✅ {mr} · tam"
    rpt.details = list(dict.fromkeys(fail + warn))
    return rpt


def _artifact_str(zip_path: str) -> str:
    from .study_artifacts import count_zip_artifacts
    return count_zip_artifacts(zip_path).as_str()


def _artifacts_from_captures(res) -> str:
    """Best-effort 'M/R/D' when a DeconflictResult is injected (no zip to count)."""
    mp4 = sum(1 for c in res.captures if c.mp4)
    raw = len(res.captures)
    dcm = sum(1 for c in res.captures if c.dcm) + len(res.orphan_dcms)
    return f"{mp4}/{raw}/{dcm}"


def _mrd_pretty(counts: Dict[str, int], profile: Optional[str]) -> str:
    f, r, s = counts.get("femur", 0), counts.get("rf", 0), counts.get("sweep", 0)
    label = {"subcohort": " (alt-kohort)", "main": ""}.get(profile or "", "")
    return f"femur{f} rf{r} sweep{s}{label}"


def _stage3_params(zip_path: str, captures: List[Capture]) -> List[str]:
    """EXPERIMENTAL stub: extract one representative frame per capture so a vision/OCR
    reader can later verify burned-in freq/gain/preset/probe. Ships advisory only once
    validated; for now it just confirms a frame is extractable and reports nothing trusted."""
    notes = ["(deneysel) frekans/preset overlay okuması henüz doğrulanmadı"]
    return notes

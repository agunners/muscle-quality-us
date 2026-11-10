"""Capture-association audit — torch-free consistency checks over the materialized tree.

Two association layers, both of which broke on AKA017 (2026-07-01, [[project_capture_association_audit]]):

  1. role -> capture   : is the capture the scorer uses for a role a legitimate cine? An orphan
                         stills-only group (no .mp4) mislabeled as the RF is the AKA017 bug; the
                         manifest already flags it (`ambiguous_captures`) but nothing surfaces it.
  2. DICOM -> frame    : does the .dcm attached to a capture sit at the SAME depth as the capture's
                         own frames? A capture's depth is intrinsic — `depth_cm = 1967 / jpg_width`
                         (128L, jpg height fixed 512) — so a mismatch means the wrong ruler.

SCOPE — a POST-MATERIALIZE score-integrity check over the SCORED roles (femur/RF) ONLY. Acquisition QA —
"were the 3 shots taken, at protocol depth?" — is check_studies' job (the Kontrol column, from the raw
zip). This audit flags: a scored role riding on a stills-only orphan (the score's input is a non-cine —
AKA017's orphan-hijack), a wrong-depth ruler (the .dcm paired to a scored capture disagrees with its own
frames), a duplicated scored role. It does NOT flag anything about the non-scored SWEEP, nor off-grid
depth — those are acquisition facts Kontrol owns (deduped 2026-07-07). One deliberate overlap remains: a
scored-role-on-stills DOES co-flag with Kontrol — from the materialized tree you cannot tell an
acquisition gap (no cine recorded) from an assignment bug (a cine existed but an orphan was picked), and
both leave the score riding on a non-cine, so the audit flags all of them; the ruler + duplicate-role
checks are the audit's alone.

Pure functions over a study directory + its `materialize_manifest.json`, so they unit-test on
synthetic trees. No torch, no model — runs cohort-wide in seconds.
"""
from __future__ import annotations

import glob
import json
import re
import statistics
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from preflight_helpers.leltek_deconfliction import depth_from_jpg_width, FOV_CONST_128L  # noqa: F401

CONTENT_JPG = re.compile(r"^\d{8}_\d{9}$")          # a real content frame (not the 256px aux png)
DEPTH_TOL_CM = 0.35                                  # frame-depth vs dcm-depth agreement tolerance
ROLE_KEYS = ("sweep", "rf", "femur")
SCORED_ROLES = ("rf", "femur")   # the score rides on femur + RF; the sweep is EI-cross-check only, so a
# sweep capture's cine-vs-stills / off-grid depth is Kontrol's acquisition concern, NOT score-integrity.


# ---------- frame geometry (a capture's intrinsic depth, DICOM-free) ----------

def _content_jpgs(raw_dir: Path) -> list[str]:
    return [p for p in glob.glob(glob.escape(str(raw_dir)) + "/*.jpg")
            if CONTENT_JPG.match(Path(p).stem)]


def frame_width_px(raw_dir: Path) -> Optional[float]:
    from PIL import Image
    ws = [Image.open(p).width for p in _content_jpgs(raw_dir)]
    return statistics.median(ws) if ws else None


def dcm_depth_cm(dcm_path: Path) -> Optional[float]:
    import pydicom
    try:
        ds = pydicom.dcmread(str(dcm_path), stop_before_pixels=True)
    except Exception:
        return None
    ps, rows = ds.get("PixelSpacing"), ds.get("Rows")
    if ps and rows:
        return round(float(rows) * float(ps[0]) / 10.0, 3)
    return None


# ---------- per-capture + per-study audit ----------

@dataclass
class CaptureRow:
    label: str                          # c1_rf_shallow / sweep(root) / ...
    identity: Optional[str]             # role the manifest gave it
    nframes: int
    width_px: Optional[float]
    frame_depth_cm: Optional[float]     # 1967 / width  — the capture's own depth
    has_mp4: bool                       # a real cine has one; an orphan stills group does not
    attached_dcm: Optional[str]
    attached_dcm_depth_cm: Optional[float]
    flags: list[str] = field(default_factory=list)


@dataclass
class StudyAudit:
    study: str
    verdict: str                        # CLEAN | FLAGGED
    captures: list[dict]
    scorer_roles: dict                  # role -> {capture, frame_depth, dcm_depth, flags} the score actually rides on
    manifest_ambiguous: list[str]       # ambiguous_captures — a capture the pipeline couldn't place (flag-driving)
    manifest_caveats: list[str]         # non-flag-driving notes: primary.caveats + non-scored ambiguities
    reasons: list[str]                  # study-level flag reasons


def _capture_records(study: Path, mf: dict) -> list[tuple[str, Path, Optional[str], Optional[str], Optional[float]]]:
    """(label, raw_image_dir, identity, dcm_member, dcm_depth_cm) for the sweep + every annex capture,
    driven by the manifest (falls back to disk enumeration if a manifest field is missing)."""
    recs = []
    prim = mf.get("primary") or {}
    for d in sorted(glob.glob(str(study) + "/RawImagesForResearch*")):
        if _content_jpgs(Path(d)):
            recs.append(("sweep(root)", Path(d), prim.get("identity", "sweep"),
                         prim.get("dcm"), prim.get("dcm_depth_cm")))
            break
    ann_by_dir = {a.get("dir"): a for a in mf.get("annex", [])}
    for cap in sorted(glob.glob(str(study) + "/captures/c*")):
        raws = [r for r in sorted(glob.glob(cap + "/RawImagesForResearch*")) if _content_jpgs(Path(r))]
        if not raws:
            continue
        a = ann_by_dir.get(Path(cap).name, {})
        recs.append((Path(cap).name, Path(raws[0]), a.get("identity"),
                     a.get("dcm"), a.get("dcm_depth_cm")))
    return recs


def _has_mp4(capture_dir: Path) -> bool:
    return bool(glob.glob(str(capture_dir) + "/*.mp4"))


def audit_study(study_dir: str | Path) -> StudyAudit:
    study = Path(study_dir)
    mf_path = study / "materialize_manifest.json"
    mf = json.loads(mf_path.read_text()) if mf_path.exists() else {}

    rows: list[CaptureRow] = []
    identities: list[str] = []
    reasons: list[str] = []

    for label, raw, identity, dcm_member, dcm_depth in _capture_records(study, mf):
        cap_dir = raw.parent
        width = frame_width_px(raw)
        fdepth = round(depth_from_jpg_width(width), 3) if width else None
        # resolve attached dcm depth: manifest value, else read the file
        adepth = dcm_depth
        if adepth is None and dcm_member:
            for p in glob.glob(str(cap_dir) + "/*.dcm"):
                adepth = dcm_depth_cm(Path(p))
                break
        has_mp4 = _has_mp4(cap_dir)
        flags: list[str] = []
        # every check is scoped to the SCORED roles (femur/RF); the sweep is EI-cross-check-only, so its
        # cine-vs-stills, ruler pairing, and depth are Kontrol's acquisition concern, never a score one.
        if identity in SCORED_ROLES and not has_mp4:
            flags.append("ROLE_ON_ORPHAN")                        # score's scored-role rides on a non-cine
        if identity in SCORED_ROLES and fdepth is not None and adepth is not None \
                and abs(fdepth - adepth) > DEPTH_TOL_CM:
            # the .dcm attached to this scored capture sits at a different depth than its OWN frames = the
            # wrong ruler (AKA017's 7.9cm dcm on 6.9cm frames). A materialize decision Kontrol can't see.
            flags.append(f"DEPTH_MISMATCH(frame {fdepth} vs dcm {adepth})")
        if identity in SCORED_ROLES and adepth is None:
            flags.append("NO_DCM")                                # scored role with no depth ruler -> unanchored mm
        if identity:
            identities.append(identity)
        rows.append(CaptureRow(label, identity, len(_content_jpgs(raw)), width, fdepth,
                               has_mp4, dcm_member, adepth, flags))

    # study-level: a scored role assigned to more than one capture (two 'femur' on AKA017).
    for role in SCORED_ROLES:
        if identities.count(role) > 1:
            reasons.append(f"DUPLICATE_ROLE:{role} x{identities.count(role)}")
    for r in rows:
        for f in r.flags:
            reasons.append(f"{r.label}:{f.split('(')[0]}")

    # surface the manifest's OWN buried flags — but SCOPE what drives the verdict to score-integrity.
    # `ambiguous_captures` = the pipeline couldn't confidently place a capture. An ambiguous SCORED role
    # (femur/RF — AKA017's orphan RF) -> FLAG; an ambiguous SWEEP (non-scored) is the same acquisition
    # fact Kontrol owns ('eşleşmeyen mp4/RawImages') -> surface as a note, do NOT drive the verdict.
    # `primary.caveats` (e.g. "length-ambiguous near the sweep/still boundary") -> notes, never flag.
    manifest_ambiguous, amb_info = [], []
    for a in mf.get("ambiguous_captures", []):
        line = f"ambiguous[{a.get('folder', a.get('identity'))}]: {a.get('reason')}"
        (manifest_ambiguous if a.get("identity") in SCORED_ROLES else amb_info).append(line)
    # notes = non-scored ambiguities + primary.caveats, deduped by reason text — materialize records the
    # same orphan-sweep note in BOTH ambiguous_captures and caveats, so surface it once, not twice.
    manifest_caveats, _seen = [], set()
    for c in amb_info + [f"caveat: {c}" for c in (mf.get("primary") or {}).get("caveats", [])]:
        key = c.split(": ", 1)[-1]
        if key not in _seen:
            _seen.add(key)
            manifest_caveats.append(c)

    # what the SCORER actually rides on (its per-role capture pick) — the load-bearing view
    scorer_roles = _scorer_roles(study, rows)

    verdict = "FLAGGED" if (reasons or manifest_ambiguous) else "CLEAN"
    return StudyAudit(study.name, verdict, [asdict(r) for r in rows], scorer_roles,
                      manifest_ambiguous, manifest_caveats, sorted(set(reasons)))


def _scorer_roles(study: Path, rows: list[CaptureRow]) -> dict:
    """Map each role the SCORER resolves (via study_modalities) to the capture it uses, and carry that
    capture's flags — so a report reader sees 'the RF the score rides on is an orphan', not just that
    some capture somewhere is an orphan."""
    out: dict = {}
    try:
        from preflight_helpers.study_discovery import study_modalities
        mods = study_modalities(study)
    except Exception:
        return out
    row_by_label = {r.label: r for r in rows}
    for role, m in mods.items():
        raw = getattr(m, "raw_image_dir", None)
        if raw is None:
            out[role] = {"capture": None, "flags": ["(no capture)"]}
            continue
        parent = Path(raw).parent.name
        label = "sweep(root)" if parent == study.name else parent   # sweep raw sits at the study root
        row = row_by_label.get(label)
        out[role] = {
            "capture": label,
            "depth_cm": getattr(m, "depth_cm", None),
            "frame_depth_cm": row.frame_depth_cm if row else None,
            "attached_dcm_depth_cm": row.attached_dcm_depth_cm if row else None,
            "flags": row.flags if row else ["(unmatched)"],
        }
    return out


def audit_tree(root: str | Path) -> list[StudyAudit]:
    """Every materialized (captures/-bearing) study under root."""
    import os
    studies = set()
    for dp, _, _ in os.walk(root, followlinks=True):
        if Path(dp).name == "captures":
            studies.add(Path(dp).parent)
    return [audit_study(s) for s in sorted(studies, key=lambda p: p.name)]

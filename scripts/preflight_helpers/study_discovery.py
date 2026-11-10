"""Walk studies_root, validate each candidate, return a list of valid studies.

Validation rule: a study is valid iff an *addressable capture* is present — a
RawImagesForResearch_* dir (the frames) plus a DICOM (.dcm, for pixel spacing /
depth). Those are exactly what the assessment path reads.

An .mp4 is NOT required. Nothing under labeling_app/ opens one, and
``study_modalities()`` addresses every capture through raw dirs + .dcm alone; the
requirement was vestigial from the era when a study was one flat capture triple,
and it silently dropped studies whose sweep take had no video. Its absence now
rides as a visible caveat instead (feedback_flag_carry_caveats_dont_gate).

Two layouts are accepted. The legacy flat study keeps everything at the root;
the v8 study is sweep-primary, with femur + center-RF cines under
captures/c*_<role>_*/. The root triple is preferred and captures/ is the
fallback, so a study whose sweep take is missing stays usable via its cines.

    studies_root/
      <annotator>/
        <session>/
          <study_dirname>/
            *.dcm                       # sweep (v8) or the only capture (legacy)
            *.mp4                       # optional — a caveat when absent
            RawImagesForResearch_*/
            captures/c*_<role>_*/       # v8 only: femur, center-RF

Returns UI-safe metadata via study_metadata.lookup_study() — folder names
never leave this module's output for UI surfaces.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field, replace
from pathlib import Path
from typing import Iterator, Optional

import pandas as pd

from . import env
from .study_metadata import StudyMeta, lookup_study, study_uid_for_dirname


@dataclass(frozen=True)
class StudyEntry:
    """One discovered study — UI-safe slice + raw_path for ledger only."""
    annotator: str
    session: str
    study_path: Path
    meta: StudyMeta
    valid: bool
    drop_reason: Optional[str] = None
    raw_image_dir: Optional[Path] = None      # RawImagesForResearch_<timestamp>
    dicom_path: Optional[Path] = None
    mp4_path: Optional[Path] = None
    # Usable-but-deviant conditions. A caveat NEVER blocks selection; it rides visibly so the
    # operator decides. Codes: "no_video" (no .mp4 anywhere) · "no_sweep_take" (the root sweep
    # triple is absent; the study is addressed through its captures/ cines).
    caveats: tuple[str, ...] = ()

    def ui_dict(self) -> dict:
        """Drops raw_path / _phi from output — safe to render."""
        return {
            **self.meta.to_ui_dict(),
            "annotator": self.annotator,
            "session": self.session,
            "valid": self.valid,
            "drop_reason": self.drop_reason,
            "caveats": list(self.caveats),
        }


def _scan_study_dir(study_path: Path) -> tuple[bool, Optional[str],
                                                Optional[Path],
                                                Optional[Path],
                                                Optional[Path],
                                                tuple[str, ...]]:
    """Return (valid, drop_reason, raw_dir, dcm_path, mp4_path, caveats).

    Hard requirements are a .dcm and a RawImagesForResearch_ dir — see the module
    docstring for why an .mp4 is a caveat rather than a gate. The root is preferred;
    captures/ is the fallback so a v8 study with no sweep take still resolves.
    """
    if not study_path.is_dir():
        return False, "not_a_directory", None, None, None, ()
    dcm: Optional[Path] = None
    mp4: Optional[Path] = None
    raw: Optional[Path] = None
    for child in sorted(study_path.iterdir()):
        if child.is_file() and child.name.endswith(".dcm") and dcm is None:
            dcm = child
        elif child.is_file() and child.name.endswith(".mp4") and mp4 is None:
            mp4 = child
        elif child.is_dir() and child.name.startswith("RawImagesForResearch_") and raw is None:
            raw = child

    caveats: list[str] = []
    # v8 sweep-primary fallback: the femur / center-RF cines carry their own raw dir + .dcm.
    if raw is None or dcm is None:
        cap = study_path / "captures"
        if cap.is_dir():
            root_raw_missing = raw is None
            for d in sorted(p for p in cap.iterdir()
                            if p.is_dir() and not p.name.startswith("_")):
                raw = raw or _first_raw_dir(d)
                dcm = dcm or _first_dcm(d)
                mp4 = mp4 or next(iter(sorted(d.glob("*.mp4"))), None)
            if root_raw_missing and raw is not None:
                caveats.append("no_sweep_take")

    if dcm is None:
        return False, "missing_dicom", raw, dcm, mp4, ()
    if raw is None:
        return False, "missing_raw_images_dir", raw, dcm, mp4, ()
    if mp4 is None:
        caveats.append("no_video")
    return True, None, raw, dcm, mp4, tuple(caveats)


def discover_studies(root: Path = env.STUDIES_ROOT) -> list[StudyEntry]:
    """Walk root → annotator → session → study folders; return entries."""
    if not root.exists():
        return []
    entries: list[StudyEntry] = []
    for annotator in sorted(root.iterdir()):
        if not annotator.is_dir():
            continue
        for session in sorted(annotator.iterdir()):
            if not session.is_dir():
                continue
            for study_path in sorted(session.iterdir()):
                if not study_path.is_dir():
                    continue
                valid, reason, raw, dcm, mp4, caveats = _scan_study_dir(study_path)
                meta = lookup_study(study_path)
                entries.append(StudyEntry(
                    annotator=annotator.name,
                    session=session.name,
                    study_path=study_path,
                    meta=meta,
                    valid=valid,
                    drop_reason=reason,
                    raw_image_dir=raw,
                    dicom_path=dcm,
                    mp4_path=mp4,
                    caveats=caveats,
                ))
    return entries


_FRAME_BASE_RE = __import__("re").compile(r"^\d{8}_\d{9}$")


def list_frame_bases(raw_image_dir: Path) -> list[str]:
    """List frame_base tokens by globbing {base}.jpg in the RawImagesForResearch_ dir.

    A frame_base looks like `20260127_151045625` (8-digit date + 9-digit time).
    The corresponding bare `.jpg` (no modality suffix) is the original frame.
    Suffixed files (e.g., `{base}_raw.png`, `{base}_beforeDSC.png`) are
    processed variants and don't count as frame bases on their own.
    """
    if raw_image_dir is None or not raw_image_dir.is_dir():
        return []
    bases: set[str] = set()
    for p in raw_image_dir.iterdir():
        if p.is_file() and p.suffix == ".jpg" and _FRAME_BASE_RE.match(p.stem):
            bases.add(p.stem)
    return sorted(bases)


# ---------------------------------------------------------------------------
# v8 modalities — a materialized study's three addressable captures.
#
# materialize_studies writes the dominant-leg SWEEP as the discoverable root triple and the
# femur + center-RF cines under captures/c*_<role>_*/ (each with its own depth-matched .dcm).
# The 3-phase picker selects per modality; this loader is how it (and the future feature router)
# addresses each one. A LEGACY single-modality study (the 282-cohort, old self-studies — no
# captures/) returns just {'sweep': ...}, so the picker shows one phase exactly as before.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Modality:
    role: str                            # "femur" (geometry) | "rf" (echo-intensity) | "sweep"
    raw_image_dir: Path
    dicom_path: Optional[Path] = None
    depth_cm: Optional[float] = None     # measured, from materialize_manifest.json (None if legacy)


def _first_raw_dir(d: Path) -> Optional[Path]:
    if not d.is_dir():
        return None
    return next((c for c in sorted(d.iterdir())
                 if c.is_dir() and c.name.startswith("RawImagesForResearch_")), None)


def _first_dcm(d: Path) -> Optional[Path]:
    return next(iter(sorted(d.glob("*.dcm"))), None) if d.is_dir() else None


def _manifest_depths(study_path: Path) -> dict:
    """{role: depth_cm} from materialize_manifest.json — {} for a legacy/un-materialized study."""
    mf = study_path / "materialize_manifest.json"
    if not mf.is_file():
        return {}
    try:
        m = json.loads(mf.read_text(encoding="utf-8"))
    except Exception:
        return {}
    out: dict = {}
    prim = m.get("primary") or {}
    if prim.get("identity"):
        out[prim["identity"]] = prim.get("dcm_depth_cm")
    for a in (m.get("annex") or []):
        if a.get("identity"):
            out[a["identity"]] = a.get("dcm_depth_cm")
    return out


def study_modalities(study_path: Path) -> dict[str, Modality]:
    """Role -> Modality for a materialized (or legacy) study. The discoverable SWEEP is the root
    triple; femur + center-RF live under captures/c*_<role>_*/. Depths come from the materialize
    manifest when present. Insertion order is sweep, then captures/ order (femur, rf)."""
    study_path = Path(study_path)
    depths = _manifest_depths(study_path)
    out: dict[str, Modality] = {}
    root_raw = _first_raw_dir(study_path)
    if root_raw is not None:
        out["sweep"] = Modality("sweep", root_raw, _first_dcm(study_path), depths.get("sweep"))
    cap = study_path / "captures"
    if cap.is_dir():
        for d in sorted(p for p in cap.iterdir() if p.is_dir() and not p.name.startswith("_")):
            role = "femur" if "femur" in d.name else ("rf" if "_rf_" in d.name else None)
            if role is None or role in out:
                continue
            raw = _first_raw_dir(d)
            if raw is not None:
                out[role] = Modality(role, raw, _first_dcm(d), depths.get(role))
    return out


def study_is_new_round(study_path) -> bool:
    """True iff the study carries the new 3-modality protocol captures — a femur or center-RF cine
    under captures/ — i.e. it opens the v8 tabbed picker (femur · center-RF · sweep). A legacy
    single-sweep study (no captures/) returns False. Cheap: one stat for the common legacy case.
    Used by the p01 'Show studies from' filter (Akadir 2026-06-25) to split the list by source —
    mirrors study_modalities' role test so the filter and the picker agree on what 'new' means."""
    cap = Path(study_path) / "captures"
    if not cap.is_dir():
        return False
    return any(p.is_dir() and not p.name.startswith("_")
               and ("femur" in p.name or "_rf_" in p.name)
               for p in cap.iterdir())


# ---------------------------------------------------------------------------
# Slice G — the alive backup cohort.
#
# studies_root reaches only ~4 legacy centers (+ self-studies); the full
# 272-study ALIVE cohort's raw sweeps live in goruntuler_backup, addressed via
# configs/study_codes.csv's relative_frame_dir. These functions surface that
# cohort so the app opens the real data, not just the ~5 symlinked studies.
# ---------------------------------------------------------------------------

_alive_cohort_cache: Optional[list[tuple[str, str]]] = None


def _alive_cohort() -> list[tuple[str, str]]:
    """`(study_code, relative_frame_dir)` for the 272 alive studies.

    Alive = `exports/decision_log_full_3operators.csv` rows where
    operator_id=='akadir' AND decision_status=='completed', deduped on
    study_code (2 re-saves -> 272 distinct), joined to study_codes.csv for the
    backup-corpus relative_frame_dir. Returns [] when the gitignored,
    machine-local 3-operator log is absent (a non-DGX checkout) — the app then
    shows only the studies_root studies, never crashes.
    """
    global _alive_cohort_cache
    if _alive_cohort_cache is not None:
        return _alive_cohort_cache
    if not env.DECISION_LOG_3OP.exists():
        _alive_cohort_cache = []
        return _alive_cohort_cache
    dl = pd.read_csv(env.DECISION_LOG_3OP)
    sub = dl[(dl["operator_id"] == "akadir")
             & (dl["decision_status"] == "completed")]
    sub = sub.sort_values("saved_at_utc").drop_duplicates("study_code", keep="last")
    codes = pd.read_csv(env.STUDY_CODES_CSV)[["study_code", "relative_frame_dir"]]
    merged = sub[["study_code"]].merge(codes, on="study_code", how="left")
    out: list[tuple[str, str]] = []
    for row in merged.itertuples(index=False):
        rel = row.relative_frame_dir
        if isinstance(rel, str) and rel:
            out.append((str(row.study_code), rel))
    _alive_cohort_cache = out
    return out


def _backup_raw_dir(study_path: Path) -> Optional[Path]:
    """First RawImagesForResearch_* under a backup study dir, or None."""
    if not study_path.is_dir():
        return None
    raws = sorted(study_path.glob("RawImagesForResearch_*"))
    return raws[0] if raws else None


_demo_cache: Optional[dict] = None


def _decision_log_demographics() -> dict:
    """`{study_code: {age, sex, height_cm, weight_kg, bmi}}` from the 3-operator
    log — the curated demographics that fill the ~39% of backup folder names
    that don't parse (e.g. `QQF025`). UI-safe (study_code-keyed, no names)."""
    global _demo_cache
    if _demo_cache is not None:
        return _demo_cache
    out: dict = {}
    if env.DECISION_LOG_3OP.exists():
        dl = pd.read_csv(env.DECISION_LOG_3OP)
        dl = dl.sort_values("saved_at_utc").drop_duplicates("study_code", keep="last")
        sex_map = {"M": "M", "F": "F"}            # "unknown" / anything else -> None

        def _i(v):
            return int(v) if pd.notna(v) else None

        for r in dl.itertuples(index=False):
            out[str(r.study_code)] = {
                "age": _i(r.age),
                "sex": sex_map.get(str(r.sex)),
                "height_cm": _i(r.height_cm),
                "weight_kg": _i(r.weight_kg),
                "bmi": round(float(r.bmi), 1) if pd.notna(r.bmi) else None,
            }
    _demo_cache = out
    return out


def _enriched_meta(meta: StudyMeta, study_code: str) -> StudyMeta:
    """Fill any missing demographic field from the decision log — the
    folder-parse value wins when present, the log fills the gaps. Without this,
    ~39% of backup studies render with blank age / sex / BMI cards."""
    d = _decision_log_demographics().get(study_code)
    if not d:
        return meta
    filled = {
        "age": meta.age if meta.age is not None else d["age"],
        "sex": meta.sex if meta.sex is not None else d["sex"],
        "height_cm": meta.height_cm if meta.height_cm is not None else d["height_cm"],
        "weight_kg": meta.weight_kg if meta.weight_kg is not None else d["weight_kg"],
        "bmi": meta.bmi if meta.bmi is not None else d["bmi"],
    }
    if all(filled[k] == getattr(meta, k) for k in filled):
        return meta                               # nothing to fill
    from dataclasses import replace
    return replace(meta, **filled)


def discover_backup_cohort(
    backup_root: Path = env.GORUNTULER_BACKUP,
) -> list[StudyEntry]:
    """Discover the 272 alive studies from goruntuler_backup.

    Validation is intentionally RELAXED vs studies_root's DICOM+MP4+raw trio:
    a backup study is valid iff its RawImagesForResearch_* dir exists with at
    least one frame. The per-study .dcm/.mp4 are NOT required, because spacing
    resolves from transport_metadata.csv (all 272 alive studies are covered
    there) — so a no-DICOM backup study still measures end-to-end.

    PHI: study_code-addressed. `study_path` carries the patient-name folder
    path for the loader/ledger only; `StudyEntry.ui_dict` drops it.
    """
    entries: list[StudyEntry] = []
    for study_code, rel in _alive_cohort():
        study_path = backup_root / rel
        raw = _backup_raw_dir(study_path)
        bases = list_frame_bases(raw) if raw is not None else []
        if raw is None:
            valid, reason = False, "missing_raw_images_dir"
        elif not bases:
            valid, reason = False, "no_frames"
        else:
            valid, reason = True, None
        parts = rel.split("/")
        annotator = parts[0] if parts else ""
        session = parts[1] if len(parts) > 1 else ""
        meta = _enriched_meta(
            lookup_study(study_path, default_code=study_code), study_code)
        dcm = (next(iter(sorted(study_path.glob("*.dcm"))), None)
               if study_path.is_dir() else None)
        mp4 = (next(iter(sorted(study_path.glob("*.mp4"))), None)
               if study_path.is_dir() else None)
        entries.append(StudyEntry(
            annotator=annotator,
            session=session,
            study_path=study_path,
            meta=meta,
            valid=valid,
            drop_reason=reason,
            raw_image_dir=raw,
            dicom_path=dcm,
            mp4_path=mp4,
        ))
    return entries


def discover_all_studies(root: Path = env.STUDIES_ROOT) -> list[StudyEntry]:
    """studies_root discovery + the alive backup cohort, merged + deduped by the
    collision-free study_uid (folder-name sha) — NOT by the AKA display pseudonym.

    Keying the merge on study_code silently dropped one member of every AKA<seq>
    collision (distinct studies that happen to share a sequence number). Keying on
    the uid keeps both; a study reached from BOTH sources still dedups (same folder
    name -> same uid), and the studies_root entry wins (full local DICOM+MP4 trio +
    any live edits). Result sorted study_code-ascending for a stable picker, then
    display-disambiguated so a surviving collision shows two distinct labels.
    """
    by_uid: dict[str, StudyEntry] = {}
    for e in discover_studies(root):
        by_uid[study_uid_for_dirname(e.study_path.name)] = e
    for e in discover_backup_cohort():
        by_uid.setdefault(study_uid_for_dirname(e.study_path.name), e)
    return _disambiguate_display_codes(
        sorted(by_uid.values(), key=lambda e: e.meta.study_code))


def _disambiguate_display_codes(entries: list[StudyEntry]) -> list[StudyEntry]:
    """When two SURVIVING studies share an AKA display code (a resolved collision),
    suffix each with a short uid tail so the picker shows distinct, stable labels.
    Identity/pooling is already keyed on the uid — this only fixes the human label;
    non-colliding codes are left untouched."""
    from collections import Counter
    counts = Counter(e.meta.study_code for e in entries)
    out: list[StudyEntry] = []
    for e in entries:
        if counts[e.meta.study_code] > 1:
            tail = study_uid_for_dirname(e.study_path.name)[2:6]   # 4 hex after 'NR'
            new_meta = replace(e.meta, study_code=f"{e.meta.study_code}-{tail}")
            out.append(replace(e, meta=new_meta))
        else:
            out.append(e)
    return out

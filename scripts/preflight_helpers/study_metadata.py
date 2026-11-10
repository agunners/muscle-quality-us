"""PHI-guarded study metadata: folder-name parse + study_codes.csv lookup.

CRITICAL — display discipline:
  * Folder names carry patient identifiers (surname, firstname encoded in path).
  * Demographics extracted here are SAFE to render in the UI.
  * Surname / firstname are kept in `raw_path` and the local `_phi` sub-dict
    only — never logged to user-facing surfaces. The run-ledger JSONL is
    gitignored and considered local-only.

Folder-name pattern (cohort + new sweeps):
    {numeric_id}_{surname}_{firstname}_{age}{sex}_{height_cm}_{weight_kg}
  where {sex} is 'e' (erkek=male) or 'k' (kadın=female).

For cohort cases, study_code is looked up in configs/study_codes.csv.
For new sweeps with no entry, a pseudonym is generated (AKA<timestamp>).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from . import env
from . import study_name_grammar as grammar

# Tolerates: 'e'/'k'/'m'/'f' sex markers + 'y' (years-only, no sex); empty surname
# (some CVAT exports have `id__firstname...` with double underscore); compound
# firstnames separated by an underscore; trailing single-token marker (e.g., `_as`).
_PATTERN = re.compile(
    r"^(?P<id>\d+)_"
    r"(?P<surname>[^_]*)_"                                # allow empty
    r"(?P<firstname>[^_]+(?:_[a-zA-ZçğıöşüÇĞİÖŞÜ]+)?)_"
    r"(?P<age>\d+)(?P<sex>[eEkKyYmMfF])_"
    r"(?P<height>\d+)_"
    r"(?P<weight>\d+)"
    r"(?P<trailer>(?:_[a-zA-Z]+)?)$"
)

# `e` = erkek (male), `k` = kadın (female). `m`/`f` accepted for English exports.
# `y` = "years" marker only (no sex encoded in the folder name) — return None so the
# UI renders gracefully.
_SEX_MAP = {"e": "M", "m": "M", "k": "F", "f": "F", "y": None}
# Imaged leg from the name's ASIS suffix -> Dominant Bacak (r = right = SAĞ, l = left = SOL).
_LEG_TO_BACAK = {"r": "SAĞ", "l": "SOL"}

# Forward-compat: the june-protocol / new schema names folders DATE-first
# (`16jun26_012_831_anonadx_anonabm_67k_163_70_041.5`) rather than id-first like the cohort.
# These don't appear under studies_root today (live folders are `<Date>_<seq>` or the legacy
# id-first cohort), but parsing them keeps the app from degrading to a hash pseudonym with no
# demographics if one ever lands. The structural parse is the shared `study_name_grammar`; this
# guard just keeps the date-first FALLBACK from claiming a non-date-shaped (e.g. cohort) name.
_DATE_FIRST_RE = re.compile(r"^\d{2}[A-Za-z]{3,}\d{2}$")       # 16jun26 / 14May26 / 11june26


@dataclass(frozen=True)
class StudyMeta:
    """Demographics safe for UI display.

    raw_path is for the ledger only — never render to user-facing surface.
    _phi (surname, firstname) likewise — kept here purely so the function
    surface enumerates what we are NOT exposing.
    """
    study_code: str
    age: Optional[int]
    sex: Optional[str]            # 'M' | 'F' | None
    height_cm: Optional[int]
    weight_kg: Optional[int]
    bmi: Optional[float]
    asis_patella: Optional[float] = None   # cm — new schema only (else None)
    probe: Optional[int] = None            # new schema only (else None)
    dominant_bacak: str = ""               # "SAĞ"/"SOL" from the name's leg suffix (else "")
    raw_path: Optional[Path] = None     # full path under goruntuler — ledger only
    _phi: dict = field(default_factory=dict)  # {'surname': ..., 'firstname': ...}

    def to_ui_dict(self) -> dict:
        """Drop _phi and raw_path — UI-safe slice."""
        return {
            "study_code": self.study_code,
            "age": self.age,
            "sex": self.sex,
            "height_cm": self.height_cm,
            "weight_kg": self.weight_kg,
            "bmi": self.bmi,
            "asis_patella": self.asis_patella,
            "probe": self.probe,
            "dominant_bacak": self.dominant_bacak,
        }

    @property
    def study_uid(self) -> Optional[str]:
        """Collision-free study identity for keying the run ledger + joining the
        cohort — see `study_uid_for_dirname`. Derived from raw_path (the folder
        name); None when raw_path is absent. Distinct from `study_code`, which is
        a DISPLAY pseudonym (AKA<seq>) whose sequence numbers repeat across
        dates/annotators — never key identity on it."""
        return study_uid_for_dirname(self.raw_path.name) if self.raw_path else None


def _bmi(height_cm: Optional[int], weight_kg: Optional[int]) -> Optional[float]:
    if not height_cm or not weight_kg:
        return None
    h_m = height_cm / 100.0
    if h_m <= 0:
        return None
    return round(weight_kg / (h_m * h_m), 1)


def _parse_date_first(folder_name: str) -> dict:
    """Parse a DATE-first demographic folder name (june-protocol / new schema) — an app-facing
    ADAPTER over the shared `study_name_grammar`. The grammar does the structural work (anchor
    on <age><sex>, probe by value, count optional, boy/kilo/ASIS positional); this maps it to
    the UI-safe demographics dict + the PHI `_phi` sub-dict, applying the app's sex policy
    (`y` -> None, not a raise). The date-shape guard keeps this fallback from claiming a
    non-date name (e.g. a cohort id-first name the regex path missed). {} if it doesn't fit."""
    sn = grammar.parse(folder_name)
    if not sn.matched or not _DATE_FIRST_RE.match(sn.date or ""):
        return {}
    surname = sn.name_tokens[0] if sn.name_tokens else ""
    firstname = "_".join(sn.name_tokens[1:]) if len(sn.name_tokens) > 1 else ""
    return {
        "age": sn.age,
        "sex": _SEX_MAP.get(sn.sex),       # 'y' (no sex) -> None; the app renders gracefully
        "height_cm": sn.height,
        "weight_kg": sn.weight,
        "bmi": _bmi(sn.height, sn.weight),
        "asis_patella": sn.asis,
        "probe": sn.probe,
        "dominant_bacak": _LEG_TO_BACAK.get(sn.leg, ""),
        "_phi": {"surname": surname, "firstname": firstname, "numeric_id": sn.count or ""},
    }


def parse_folder_demographics(folder_name: str) -> dict:
    """Parse demographics from a goruntuler study folder name.

    Handles the legacy id-first cohort names (`0000145_anonadm_anonabu_46e_171_110`) and,
    as forward-compat, the date-first june-protocol / new-schema names. Returns a dict
    with only UI-safe fields (+ asis_patella / probe, None unless the new schema carries
    them). Surname / firstname are intentionally kept out of the top level (PHI guardrail,
    in `_phi` only). Returns {} when neither shape matches — caller decides how to handle.
    """
    folder_name = re.sub(r"\s*\(\d+\)$", "", folder_name)  # drop a trailing " (1)" de-dup suffix
    m = _PATTERN.match(folder_name)
    if not m:
        return _parse_date_first(folder_name)   # try the date-first shape before giving up
    age = int(m.group("age"))
    sex = _SEX_MAP.get(m.group("sex").lower())
    height = int(m.group("height"))
    weight = int(m.group("weight"))
    return {
        "age": age,
        "sex": sex,
        "height_cm": height,
        "weight_kg": weight,
        "bmi": _bmi(height, weight),
        "asis_patella": None,   # cohort id-first names never carry these
        "probe": None,
        "dominant_bacak": "",
        "_phi": {  # ONLY populated for ledger; UI must drop
            "surname": m.group("surname"),
            "firstname": m.group("firstname"),
            "numeric_id": m.group("id"),
        },
    }


_study_codes_cache: Optional[pd.DataFrame] = None


def _load_study_codes() -> pd.DataFrame:
    global _study_codes_cache
    if _study_codes_cache is None:
        _study_codes_cache = pd.read_csv(env.STUDY_CODES_CSV)
    return _study_codes_cache


def study_code_for_dirname(study_dirname: str) -> Optional[str]:
    df = _load_study_codes()
    hits = df[df["study_dirname"] == study_dirname]
    if hits.empty:
        return None
    return str(hits.iloc[0]["study_code"])


def study_uid_for_dirname(dirname: str) -> str:
    """Stable, unique, PHI-safe study identity — the SAME 'NR'+sha8 scheme the
    new-round cohort mints (`derive_new_round_cohort._study_code`), so a study's
    run-ledger identity and its cohort-join key are ONE value. Keyed on the folder
    name (distinct studies always have distinct folder names), so it never
    collides — unlike the AKA<seq> DISPLAY pseudonym, whose sequence numbers repeat
    across dates/annotators. Reserve `study_code` for display; key identity here."""
    import hashlib
    return "NR" + hashlib.sha256(dirname.encode("utf-8")).hexdigest()[:8].upper()


def run_study_uid(record: dict) -> Optional[str]:
    """The collision-free identity of the study a ledger run belongs to, from the
    run's stored `study_path` (every record carries it — `run_pipeline` stamps it).
    None for a legacy record with no study_path; callers then fall back to the
    (collision-prone) study_code — safe only because such records predate the
    colliding new-round batch. The one place run-by-study filtering derives uid."""
    sp = record.get("study_path")
    return study_uid_for_dirname(Path(sp).name) if sp else None


def lookup_study(study_path: Path, *, default_code: Optional[str] = None) -> StudyMeta:
    """Build a StudyMeta from a study folder path.

    For cohort cases — joins folder name with study_codes.csv to recover
    the canonical study_code.

    For new sweeps with no cohort entry — generates a pseudonym (AKA<id>)
    so the UI still has something to display, while the raw folder name
    stays out of view.
    """
    study_path = Path(study_path)
    dirname = study_path.name
    demo = parse_folder_demographics(dirname)

    code = default_code or study_code_for_dirname(dirname)
    if code is None:
        # Pseudonym: numeric_id from the folder name if present (cohort, or a new-schema name
        # that carries a seq), else a stable hash of the name (date-first names may omit the id).
        numeric_id = (demo.get("_phi") or {}).get("numeric_id")
        if numeric_id:
            code = f"AKA{numeric_id}"
        else:
            import hashlib
            h = hashlib.sha256(dirname.encode("utf-8")).hexdigest()[:6].upper()
            code = f"AKA{h}"

    return StudyMeta(
        study_code=code,
        age=demo.get("age"),
        sex=demo.get("sex"),
        height_cm=demo.get("height_cm"),
        weight_kg=demo.get("weight_kg"),
        bmi=demo.get("bmi"),
        asis_patella=demo.get("asis_patella"),
        probe=demo.get("probe"),
        dominant_bacak=demo.get("dominant_bacak", ""),
        raw_path=study_path,
        _phi=demo.get("_phi", {}),
    )

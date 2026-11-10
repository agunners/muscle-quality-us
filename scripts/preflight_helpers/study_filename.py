"""Parse a Round-3 study-zip filename into hasta_kayit_v7 datasheet fields.

Spec: "Görüntü Alma Kılavuzu v7", EK-2 (worked example). The operator types the
patient info on the Leltek device; the exported study is uploaded — zipped — to its
operator subfolder under `goruntuler/`. The zip's *name* is the only metadata the
intake CLI (scripts/sync_records.py) needs to fill the patient datasheet.

Filename convention — generations coexist (2026-06-17); all are handled
-------------------
    CANONICAL: <tarih>_<seq>_<probe>_<soyad>_<ad>_<yas><cinsiyet>_<boy>_<kilo>_<ASIS-patella>[r/l]
    FIRST CUT: <tarih>_<seq>_<soyad>_<ad>_<yas><cinsiyet>_<boy>_<kilo>_<ASIS-patella>_<probe>
    OLD:       <tarih>_[seq]_<soyad>_<ad>_<yas><cinsiyet>_<boy>_<kilo>          (no ASIS / probe)

    07jun06_093_anonabr_anonaab_49e_179_82            (old)
    16jun26_012_831_anonadx_anonabm_67k_163_70_041.5     (canonical: probe up front, ASIS at tail)
    |       |   |   |      |      |   |  |  +--------- ASIS-patella (cm, decimal; +r/l = imaged leg)
    |       |   |   |      |      |   |  +------------ kilo (kg)                   70
    |       |   |   |      |      |   +--------------- boy  (cm)                   163
    |       |   |   |      |      +------------------- yas+cinsiyet   67 + k -> K
    |       |   |   |      +-------------------------- ad   (first name)          anonabm
    |       |   |   +--------------------------------- soyad (last name)          anonadx
    |       |   +------------------------------------- probe no (by VALUE, front or tail)  831
    |       +----------------------------------------- seq  (per-date, OPTIONAL)  012
    +------------------------------------------------- tarih (ddMMMyy)            16jun26

Parse strategy — we ANCHOR on the <yas><cinsiyet> token (unambiguous: digits +
one letter) rather than counting from the end. Tokens before it (after the date) are
the optional seq + (canonically) the probe + the name; tokens after it are the
positional numeric run boy, kilo, ASIS-patella. The PROBE is pulled out by VALUE
(one of the known Leltek probes) wherever it sits — front (canonical) or tail (first
cut) — so its position never shifts boy/kilo. All structural work lives in the shared
`study_name_grammar`; this module is a thin intake adapter over it.

Notes
-----
* Operator is NOT in the filename — it is the `goruntuler/<operator>/` subfolder.
* `tarih` is stored verbatim (yy is ambiguous — see the v7 `...jun06` example).
* `seq` is OPTIONAL (Akadir 2026-06-16): a name can go straight from date to the
  name (`12jun06_anonaam_anonacx_25e_174_70`) — that is valid, not a warning.
* SEX is mandatory here: the anchor letter must be a sex (e/E/m/M/k/K/f/F). A no-sex
  `<age>y` marker (tolerated by the app/cohort parser, where some legacy names use it)
  RAISES on the intake path, so a sexless capture surfaces as an intake error rather
  than landing demographics with no sex. Round-3 captures always encode sex.
* Name order defaults to `soyad_ad`; pass name_order="ad_soyad" if a device export
  concatenates First_Last.
* DECIMALS are kept ONLY for ASIS-patella. boy/kilo/probe are integers, so a
  misplaced dot (real case: weight `85.5`) is FLOORED, never kept (a soft warning
  records the floor so the operator typo still surfaces).
* BMI = kilo_kg / (boy_cm / 100) ** 2, rounded to 1 dp.
* Soft issues (odd date, implausible age, unexpected token count, floored decimal)
  land in `.warnings`; only a structurally unusable name (too few tokens, no
  parsable <yas><cinsiyet> anchor) raises.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePath
from typing import List, Optional

from . import study_name_grammar as grammar

__all__ = [
    "ParsedStudyName",
    "StudyFilenameError",
    "normalize_sex",
    "compute_bmi",
    "parse_study_filename",
]

_DATE_RE = re.compile(r"^(\d{2})([A-Za-z]{3})(\d{2})$")   # ddMMMyy — the soft date-format check
# erkek/male -> E, kadin/female -> K (accepts Turkish E/K and English M/F, any case)
_SEX_MAP = {"E": "E", "M": "E", "K": "K", "F": "K"}
# Imaged leg from the name's ASIS suffix -> the Dominant Bacak column value: r = right = SAĞ,
# l = left = SOL (Akadir 2026-06-17).
_LEG_TO_BACAK = {"r": "SAĞ", "l": "SOL"}
_VALID_NAME_ORDERS = ("soyad_ad", "ad_soyad")


class StudyFilenameError(ValueError):
    """A zip filename cannot be parsed to the v7 datasheet schema."""


@dataclass
class ParsedStudyName:
    """Demographics recovered from a study-zip filename (v7 EK-2).

    `asis_patella` / `probe` are populated only by the NEW schema (2026-06-16);
    they stay None for old names so an old-schema row leaves those datasheet cells
    blank for manual entry."""

    study_adi: str
    tarih: str
    seq: str                      # "" when the name omits the per-date count (allowed)
    hasta_soyadi: str
    hasta_adi: str
    yas: Optional[int]
    cinsiyet: str
    boy_cm: Optional[int]
    kilo_kg: Optional[int]
    bmi: Optional[float]
    asis_patella: Optional[float] = None   # cm, the ONLY field that keeps decimals
    probe: Optional[int] = None
    dominant_bacak: str = ""               # "SAĞ" (r) / "SOL" (l) from the name's leg suffix, else ""
    warnings: List[str] = field(default_factory=list)


def normalize_sex(raw: str) -> str:
    """Map e/E/m/M -> 'E' (erkek) and k/K/f/F -> 'K' (kadin). Raise on anything else."""
    key = (raw or "").strip().upper()
    if key not in _SEX_MAP:
        raise StudyFilenameError(f"unrecognized sex token: {raw!r}")
    return _SEX_MAP[key]


def compute_bmi(boy_cm: Optional[int], kilo_kg: Optional[int]) -> Optional[float]:
    """BMI = kilo / (boy_m ** 2), 1 dp. None if either input is missing/non-positive."""
    if not boy_cm or not kilo_kg or boy_cm <= 0 or kilo_kg <= 0:
        return None
    return round(kilo_kg / (boy_cm / 100.0) ** 2, 1)


def _strip_ext(filename: str) -> str:
    stem = PurePath(filename.strip()).name
    if stem.lower().endswith(".zip"):
        stem = stem[: -len(".zip")]
    # Drop a trailing filesystem/Drive de-dup suffix " (1)" / " (2)" so it can't swallow the
    # last field — a real id always ends in a number (weight/probe), never " (N)". Without this,
    # `…_178_115 (1)` loses the weight (the `115 (1)` token isn't numeric). Matches the viewer.
    stem = re.sub(r"\s*\(\d+\)$", "", stem)
    return stem


def _split_name(name_tokens: List[str], name_order: str, warnings: List[str]):
    """name_tokens (soyad, ad, …) -> (hasta_soyadi, hasta_adi), flagging odd counts."""
    if len(name_tokens) == 2:
        first, second = name_tokens
    elif len(name_tokens) == 1:
        warnings.append(f"only one name token {name_tokens[0]!r}; ad/soyad cannot be split")
        first, second = name_tokens[0], ""
    elif len(name_tokens) == 0:
        warnings.append("no name token between the date and <yas><cinsiyet>")
        first, second = "", ""
    else:  # > 2 — join the surplus, but flag it
        warnings.append(f"{len(name_tokens)} name tokens {name_tokens!r}; expected 2 — joined the surplus")
        if name_order == "soyad_ad":
            first, second = name_tokens[0], " ".join(name_tokens[1:])
        else:
            first, second = " ".join(name_tokens[:-1]), name_tokens[-1]
    return (first, second) if name_order == "soyad_ad" else (second, first)


def parse_study_filename(filename: str, *, name_order: str = "soyad_ad") -> ParsedStudyName:
    """Parse a study-zip filename into datasheet demographics — an intake-facing ADAPTER over
    the shared `study_name_grammar` (the single source of truth for the date-first schema).

    The grammar does the structural work (anchor on <yas><cinsiyet>; probe by value; count
    optional; boy/kilo/ASIS positional, decimals kept only for ASIS, the rest floored+warned).
    This adapter adds the intake rules: strip the .zip / " (1)" suffix, split soyad/ad per
    name_order, validate the date format and age range, REQUIRE a sex (a no-sex `<age>y`
    raises — Round-3 always encodes sex), and flag missing height/weight. Raises
    StudyFilenameError on a structurally unusable name (no <yas><cinsiyet> anchor)."""
    if name_order not in _VALID_NAME_ORDERS:
        raise ValueError(f"name_order must be one of {_VALID_NAME_ORDERS}")

    stem = _strip_ext(filename)
    if not stem:
        raise StudyFilenameError("empty filename")

    sn = grammar.parse(stem)
    if not sn.matched:
        raise StudyFilenameError(
            f"{stem!r}: cannot find a <yas><cinsiyet> token (e.g. 50e) to anchor on"
        )

    warnings: List[str] = list(sn.warnings)
    if not _DATE_RE.match(sn.date or ""):
        warnings.append(f"tarih {sn.date!r} is not ddMMMyy (e.g. 08jun06)")

    hasta_soyadi, hasta_adi = _split_name(sn.name_tokens, name_order, warnings)
    cinsiyet = normalize_sex(sn.sex)       # raises on a non-sex letter (e.g. a stray 'y')
    if not (0 < (sn.age or 0) <= 100):
        warnings.append(f"yas {sn.age} outside 1-100")
    if sn.height is None:
        warnings.append("boy (height) missing")
    if sn.weight is None:
        warnings.append("kilo (weight) missing")

    return ParsedStudyName(
        study_adi=stem,
        tarih=sn.date,
        seq=sn.count or "",
        hasta_soyadi=hasta_soyadi,
        hasta_adi=hasta_adi,
        yas=sn.age,
        cinsiyet=cinsiyet,
        boy_cm=sn.height,
        kilo_kg=sn.weight,
        bmi=compute_bmi(sn.height, sn.weight),
        asis_patella=sn.asis,
        probe=sn.probe,
        dominant_bacak=_LEG_TO_BACAK.get(sn.leg, ""),
        warnings=warnings,
    )

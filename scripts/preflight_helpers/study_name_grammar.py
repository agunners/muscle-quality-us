"""Single source of truth for parsing june-protocol study NAMES (the date-first schema).

All generations of the name are parsed by ONE function so the grammar can't drift between
the consumers — the drift that once put the probe number in the weight cell. Adapters layer
their specifics on top of this:
  - scripts/preflight_helpers/study_filename.py  (intake -> datasheet rows; raises/ warns)
  - scripts/preflight_helpers/study_metadata.py  (app StudyMeta, date-first path; PHI _phi)
  - scripts/preflight_helpers/datasheet_schema.py::array_formulas (the in-sheet REGEXEXTRACT,
    which MIRRORS this grammar in RE2 and is pinned by a contract test)
  - the viewer's discovery.parse_study_name (separate repo; mirrors the shared test cases)

Canonical format (Akadir 2026-06-17) — logistics up front, patient group at the back:
    <date>_<count>_<probe>_<soyad>_<ad>_<age><sex>_<boy>_<kilo>_<ASIS-patella>
    16jun26_012_831_anonadx_anonabm_67k_163_70_041.5
Older generations coexist and are handled: the first new-schema cut put probe + ASIS at the
TAIL (`..._70_041.5_831`); the OLD style (10-12 june) has neither
(`<date>_[count]_<soyad>_<ad>_<age><sex>_<boy>_<kilo>`).

Robustness spine
----------------
  * ANCHOR on the <age><sex> token (digits + a sex letter) — the one unambiguous pivot.
  * The PROBE is identified BY VALUE (one of PROBE_NUMBERS), wherever it sits — front
    (canonical) or tail (first cut). Nothing else in a name reaches that range (heights<=250,
    weights<=300, ASIS ~40-55, counts ~001-0xx), so this is position-independent and a
    forgotten count can never be mistaken for the probe.
  * COUNT is the other (non-probe) numeric in the front group; it is OPTIONAL.
  * After the anchor, the remaining numerics (probe removed) are boy, kilo, ASIS positionally.
    DECIMALS are kept ONLY for ASIS; boy/kilo floor a misplaced dot (and warn).

This module is torch-free and dependency-free so every consumer can import it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

# The known Leltek probes. SINGLE SOURCE — datasheet_schema imports this for the dropdown, and
# the parser uses it to tell the probe from the count. Add a new probe HERE (and nowhere else).
PROBE_NUMBERS = (829, 830, 831)

_AGESEX = re.compile(r"^(\d{1,3})([eEkKyYmMfF])$")   # 67k / 50e / 29y — the pivot token
_NUM = re.compile(r"^\d+(?:\.\d+)?$")                 # integer or dotted-decimal token
# The last field may carry the imaged leg glued to the ASIS value with NO underscore (Akadir
# 2026-06-17): `041.5r` = right, `041.5l` = left. r/l are not sex letters, so this never
# collides with the <age><sex> anchor; we peel the trailing letter off and keep the number.
_NUM_LEG = re.compile(r"^(\d+(?:\.\d+)?)([rRlL])$")


def _floor(anonadn: str) -> int:
    return int(float(anonadn))


def _floor_in_range(anonadn: str, label: str, lo: int, hi: int, warnings: List[str]) -> int:
    """Floor a numeric token and range-check it. A dropped decimal (operator misplaced the
    dot, e.g. weight '85.5') is floored AND warned, so the typo surfaces rather than corrupts."""
    v = _floor(anonadn)
    if float(anonadn) != v:
        warnings.append(f"{label} {anonadn!r} floored to {v} (decimal dropped — likely a misplaced dot)")
    if not (lo <= v <= hi):
        warnings.append(f"{label} {v} outside {lo}-{hi}")
    return v


@dataclass
class StudyName:
    """Structural parse of a date-first study name. Neutral — adapters apply their own sex
    policy, name splitting, and output shape on top. `matched` is False when no <age><sex>
    anchor was found (an unusable name); callers decide whether that raises or degrades."""
    matched: bool = False
    date: Optional[str] = None
    count: Optional[str] = None            # kept as a string (leading zeros matter); optional
    probe: Optional[int] = None
    name_tokens: List[str] = field(default_factory=list)  # alpha tokens before the anchor, in order
    age: Optional[int] = None
    sex: Optional[str] = None              # raw lowercase letter ('e'/'k'/'y'/'m'/'f') or None
    height: Optional[int] = None
    weight: Optional[int] = None
    asis: Optional[float] = None           # the ONLY field that keeps decimals
    leg: Optional[str] = None              # imaged leg from the ASIS suffix: 'r' (right) / 'l' (left)
    warnings: List[str] = field(default_factory=list)


def parse(raw_name: str) -> StudyName:
    """Parse a date-first study name into its structural fields. Never raises.

    Anchors on the <age><sex> token; identifies the probe by value (PROBE_NUMBERS) wherever it
    sits; treats the other front numeric as the optional count; reads boy/kilo/ASIS positionally
    from the post-anchor numerics with the probe removed (decimals kept only for ASIS)."""
    out = StudyName()
    # Strip a trailing filesystem/Drive de-dup suffix " (1)" / " (2)" here, so the grammar is
    # self-sufficient — every caller gets it for free and a duplicate upload `foo (1)` parses
    # like `foo` (its real fields), instead of the " (1)" swallowing the last field.
    raw_name = re.sub(r"\s*\(\d+\)$", "", raw_name.strip())
    toks = [t for t in raw_name.split("_") if t != ""]   # drop empties (double underscore)
    if not toks:
        out.warnings.append("empty name")
        return out
    out.date = toks[0]

    anchor = next((k for k in range(1, len(toks)) if _AGESEX.match(toks[k])), None)
    if anchor is None:
        out.warnings.append("no <age><sex> anchor token (e.g. 50e / 67k) found")
        return out
    out.matched = True
    m = _AGESEX.match(toks[anchor])
    out.age, out.sex = int(m.group(1)), m.group(2).lower()

    front = toks[1:anchor]
    out.name_tokens = [t for t in front if not _NUM.match(t)]   # alpha tokens (Unicode names ok)
    front_nums = [t for t in front if _NUM.match(t)]
    # Tail = the post-anchor numerics. A token may carry the imaged leg glued to the ASIS value
    # (`041.5r`) — peel the trailing r/l off and keep the number; the rest are plain numerics.
    tail_nums: List[str] = []
    for t in toks[anchor + 1:]:
        mleg = _NUM_LEG.match(t)
        if mleg:
            out.leg = mleg.group(2).lower()      # 'r' = right, 'l' = left
            tail_nums.append(mleg.group(1))      # the numeric part (the ASIS value)
        elif _NUM.match(t):
            tail_nums.append(t)
        elif any(c.isdigit() for c in t) or t.lower() in ("r", "l"):
            # looks like a botched number or leg suffix (e.g. '041.5rl', '041.5x', a stray 'r') —
            # ignored so no wrong value lands, but warned so the operator can fix the name
            out.warnings.append(
                f"trailing token {t!r} is not a number or number+leg (e.g. 041.5 or 041.5r) — ignored")

    # PROBE by value, wherever it is (front = canonical, tail = first cut).
    probe_toks = [t for t in front_nums + tail_nums if _floor(t) in PROBE_NUMBERS]
    if probe_toks:
        out.probe = _floor(probe_toks[0])
        if len(probe_toks) > 1:
            out.warnings.append(f"{len(probe_toks)} probe-valued tokens; used the first")

    # COUNT = first front numeric that isn't the probe (optional). Extra non-probe front
    # numerics mean a field (usually ASIS) is misplaced before the name — flag it for a rename.
    count_nums = [t for t in front_nums if _floor(t) not in PROBE_NUMBERS]
    if count_nums:
        out.count = count_nums[0]
        if len(count_nums) > 1:
            out.warnings.append(
                f"{len(count_nums)} non-probe numerics before the name {count_nums!r}; expected "
                f"one count — the extra(s) look like a misplaced ASIS/probe (rename the study)")

    # boy / kilo / ASIS = the post-anchor numerics with the probe removed, read positionally.
    body = [t for t in tail_nums if _floor(t) not in PROBE_NUMBERS]
    if len(body) >= 1:
        out.height = _floor_in_range(body[0], "boy", 50, 250, out.warnings)
    if len(body) >= 2:
        out.weight = _floor_in_range(body[1], "kilo", 20, 300, out.warnings)
    if len(body) >= 3:
        out.asis = float(body[2])           # decimals kept here only
    if len(body) > 3:
        out.warnings.append(
            f"{len(body)} body numerics after <age><sex>; expected <=3 (boy, kilo, ASIS)")
    return out

"""Single source of truth for the Round-3 patient datasheet (hasta_kayit_v7).

Both the structure builder (scripts/sync_records.py --setup: headers + dropdowns +
the BMI formula) and the intake sync (which fills rows) import this, so the column
set can never drift between them.

Spec: Görüntü Alma Kılavuzu v7, EK-2 (worked example) + Akadir's column directives.
Operator-judgment fields (leg, injury, sub-cohort, software version, presets…) carry a
DROPDOWN but NO pre-filled value — the operator must actively select, never inherit a
passive default (Akadir 2026-06-10). So the CLI writes only what the id/zip encode
(Study Adı, Operator, Artifacts); the dropdowns + BMI formula carry the rest, and the
'Kontrol Sonucu'/'Kontrol Detayı' columns are written by the check_studies.py watcher.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Tuple

from .study_name_grammar import PROBE_NUMBERS

__all__ = [
    "Column", "COLUMNS", "HEADERS", "KEY_COLUMN", "CHOOSE_OPTION", "PROBE_OPTIONS",
    "col_index", "col_letter", "bmi_formula", "build_row", "merge_fill_missing", "row_to_list",
    "dropdown_values", "is_unanswered",
]

EVET_HAYIR: Tuple[str, ...] = ("Evet", "Hayır")
# The Probe No dropdown options, derived from the grammar's SINGLE source (PROBE_NUMBERS), so
# the parser and the dropdown can never list different probes. Add a probe in study_name_grammar.
PROBE_OPTIONS: Tuple[str, ...] = tuple(str(p) for p in PROBE_NUMBERS)

# The default shown in every operator-judgment dropdown until the operator picks a real value
# (Akadir 2026-06-17, replacing the earlier empty default + invisible blank revert-row). "Seçiniz"
# = Turkish "please select": it is the seeded default, the revert target (re-pick it to undo a
# wrong choice), AND a normal dropdown option. It is NOT a real answer, so it preserves the
# "operator must actively choose" rule while making "unanswered" loud and explicit instead of an
# ambiguous empty cell. CRITICAL: every reader must treat it as unanswered (use is_unanswered),
# so it never leaks downstream as if it were a real category.
CHOOSE_OPTION = "Seçiniz"


def is_unanswered(value: str) -> bool:
    """True if a manual cell is effectively empty — blank OR still on the CHOOSE_OPTION default.
    Use this everywhere a datasheet manual value is read, so 'Seçiniz' counts as 'not answered'."""
    return (value or "").strip() in ("", CHOOSE_OPTION)


@dataclass(frozen=True)
class Column:
    header: str
    source: str            # filename | subfolder | seq | default | formula | computed | manual
    default: str = ""
    options: Tuple[str, ...] = ()   # data-validation dropdown (empty = free text)
    allow_other: bool = False       # dropdown that still accepts other typed values
    note: str = ""


# Order IS the sheet column order (A, B, ...). 31 columns (24 base + 5 force/strength criterion
# + Force Operator + the measurement-check column). New columns append at the END: the force join
# is POSITIONAL (sync_force_measures writes by schema index), so inserting mid-schema would shift
# Y–AC. ⚠ The sheet is LIVE and operators can add columns the schema does not know — "Force
# Operator" (AD) existed for ~120 rows before the schema learned it on 2026-08-12, invisible
# because every reader stops at the schema's last column. Before targeting a "free" column for a
# write, READ it on the live sheet (sync_force_measures does; keep that discipline).
COLUMNS: Tuple[Column, ...] = (
    Column("Sıra", "seq", note="1-based row index"),
    Column("Tarih", "filename", note="ddMMMyy from Study Adı, stored verbatim"),
    Column("Study Adı", "filename", note="zip basename — the match / idempotency key"),
    Column("Hasta Adı", "filename"),
    Column("Hasta Soyadı", "filename"),
    Column("Cinsiyet", "filename", options=("E", "K")),
    Column("Yaş", "filename"),
    Column("Boy (cm)", "filename"),
    Column("Kilo (kg)", "filename"),
    Column("BMI", "formula", note="=ROUND(Kilo/(Boy/100)^2,1)"),
    # Dominant Bacak is name-ENCODED via the ASIS leg suffix (r=SAĞ, l=SOL). FORMULA-filled, no
    # dropdown (Akadir 2026-06-22): the name is the single source — blank if it carries no leg.
    Column("Dominant Bacak", "filename",
           note="imaged leg — in-sheet FORMULA from the name's r/l suffix (r=SAĞ, l=SOL); blank if absent"),
    # ASIS-patella is name-ENCODED: the single value flows into BOTH leg columns. FORMULA-filled
    # (Akadir 2026-06-22), blank when the name carries no ASIS.
    Column("ASIS-Patella Sol (cm)", "filename",
           note="in-sheet FORMULA from the name's ASIS value (into both legs); blank if absent"),
    Column("ASIS-Patella Sağ (cm)", "filename",
           note="in-sheet FORMULA from the name's ASIS value (into both legs); blank if absent"),
    # operator-judgment dropdowns — default to CHOOSE_OPTION ('Seçiniz'); the operator must pick.
    Column("Yaralanma/Cerrahi (alt ekstremite, son 6 ay)", "manual", default=CHOOSE_OPTION, options=EVET_HAYIR),
    Column("Alt-kohort mu", "manual", default=CHOOSE_OPTION, options=EVET_HAYIR, note="Evet -> 8-capture sub-cohort"),
    Column("Operator", "subfolder"),   # dropdown options injected at setup from config; CLI-filled
    Column("Software Version", "manual", default=CHOOSE_OPTION, allow_other=True,
           note="non-strict dropdown seeded from config at setup; defaults to Seçiniz"),
    Column("Presets Ayarlı mı", "manual", default=CHOOSE_OPTION, options=EVET_HAYIR),
    # Probe is name-encoded (a value in PROBE_NUMBERS, front or tail). FORMULA-filled, no dropdown
    # (Akadir 2026-06-22): the name is the single source — blank if it carries no known probe.
    Column("Probe No", "filename",
           note="in-sheet FORMULA: the name's probe (one of PROBE_NUMBERS); blank otherwise"),
    Column("Artifacts (mp4/RawImages/dcm)", "computed", note="filled by intake from the zip"),
    Column("Dosyalar Tam mı", "manual", default=CHOOSE_OPTION, options=EVET_HAYIR),
    Column("Notlar", "manual"),   # free text — no Seçiniz default
    Column("Kontrol Sonucu", "check",
           note="completeness/accuracy verdict written by the check_studies.py watcher"),
    Column("Kontrol Detayı", "check",
           note="watcher detail: dcm depth multiset + brightness + specifics"),
    # --- Force / strength CRITERION (Akadir 2026-06-29) — the external gold-standard the
    # muscle-condition US score is validated against. Entered from the isokinetic-dynamometry
    # session (SEPARATE from the scan), per the imaged/tested leg, EXTENSION = quadriceps. Free
    # numeric, operator/physio-typed, text-formatted so a value never date/locale-coerces. Each
    # mode's CV sits RIGHT AFTER its torque (grouped 2026-07-01 at Dr. M's request): isometric peak
    # torque (primary, most reliable) + its CV, then isokinetic peak torque & avg power (secondary,
    # dynamic) + the isokinetic CV. A CV is a reliability GATE + paper descriptor, not a target (a
    # high isokinetic CV, e.g. 26.8%, flags its torque/power as untrustworthy; isometric CV ~7% is
    # typically clean — which is the reassurance Dr. M wants recorded).
    Column("İzometrik Tepe Tork (Nm)", "manual",
           note="isometric peak knee-EXTENSION torque, tested leg — PRIMARY strength criterion"),
    Column("İzometrik CV (%)", "manual",
           note="coeff. of variation of the isometric reps — reliability GATE + paper descriptor "
                "(added 2026-07-01 at Dr. M's request); grouped right after its torque"),
    Column("İzokinetik Tepe Tork Ekst (Nm)", "manual",
           note="isokinetic peak EXTENSION torque @ 60 deg/s — secondary (dynamic) criterion"),
    Column("İzokinetik Ort. Güç Ekst (W)", "manual",
           note="isokinetic average EXTENSION power @ 60 deg/s — secondary criterion"),
    Column("İzokinetik CV (%)", "manual",
           note="coeff. of variation of the isokinetic reps — reliability GATE, not a target; a "
                "high CV invalidates the isokinetic torque/power (best practice <= 10-15%)"),
    # Which physio ran the dynamometer session — operator-maintained, discovered LIVE on the
    # sheet 2026-08-12 (AD, ~120 rows filled, never written by any script). In the schema so no
    # write can ever target its column and so readers key it by name. Audit-relevant: both
    # kgf·m-as-Nm suspects (rows 87, 92) carry the same force operator.
    Column("Force Operator", "manual",
           note="who ran the dynamometer session — operator-typed; never script-written"),
    # MEASUREMENT plausibility — a SEPARATE instrument from the recording verdict (Kontrol
    # Sonucu), per the recording-vs-measurement dedup lesson. Written surgically by the
    # sync_force_measures gate: ⚠️ when a dynamometer entry carries an implausibility signature
    # (kgf·m-as-Nm, CV exactly 0, impossible iso/isok ratio…), ❌/✅ when a human kill/clear
    # verdict lands in the flag→kill ledger (data/kill_list.jsonl).
    Column("Ölçüm Kontrol", "check",
           note="dynamometer-entry plausibility flag written by the sync_force_measures gate; "
                "recording verdicts stay in Kontrol Sonucu"),
)

HEADERS: Tuple[str, ...] = tuple(c.header for c in COLUMNS)
KEY_COLUMN = "Study Adı"

_FILENAME_VALUE: Dict[str, Callable] = {
    "Tarih": lambda p: p.tarih,
    "Study Adı": lambda p: p.study_adi,
    "Hasta Adı": lambda p: p.hasta_adi,
    "Hasta Soyadı": lambda p: p.hasta_soyadi,
    "Cinsiyet": lambda p: p.cinsiyet,
    "Yaş": lambda p: "" if p.yas is None else str(p.yas),
    "Boy (cm)": lambda p: "" if p.boy_cm is None else str(p.boy_cm),
    "Kilo (kg)": lambda p: "" if p.kilo_kg is None else str(p.kilo_kg),
    # New schema: ASIS-patella into BOTH legs (downstream resolves which) — a NUMBER, so absent
    # -> "" (blank for manual numeric entry, not the Seçiniz dropdown default).
    "ASIS-Patella Sol (cm)": lambda p: "" if p.asis_patella is None else f"{p.asis_patella:g}",
    "ASIS-Patella Sağ (cm)": lambda p: "" if p.asis_patella is None else f"{p.asis_patella:g}",
    # Dominant Bacak (from the leg suffix) and Probe No are DROPDOWNS — absent -> CHOOSE_OPTION,
    # so the operator is prompted to pick (consistent with the other judgment dropdowns).
    "Dominant Bacak": lambda p: p.dominant_bacak or CHOOSE_OPTION,
    "Probe No": lambda p: str(p.probe) if p.probe else CHOOSE_OPTION,
}


def col_index(header: str) -> int:
    for i, c in enumerate(COLUMNS):
        if c.header == header:
            return i
    raise KeyError(header)


def col_letter(idx0: int) -> str:
    """0-based column index -> A1 letter (0->A, 25->Z, 26->AA)."""
    s, n = "", idx0
    while True:
        s = chr(ord("A") + n % 26) + s
        n = n // 26 - 1
        if n < 0:
            break
    return s


def bmi_formula(row_1based: int, sep: str = ",") -> str:
    """BMI = ROUND(Kilo/(Boy/100)^2, 1). `sep` is the formula argument separator —
    ',' for an en-locale sheet, ';' for tr_TR (where ',' is the decimal mark)."""
    boy = col_letter(col_index("Boy (cm)"))
    kilo = col_letter(col_index("Kilo (kg)"))
    return f'=IFERROR(ROUND({kilo}{row_1based}/({boy}{row_1based}/100)^2{sep}1){sep}"")'


def build_row(parsed, operator: str, artifacts: str, *, row_1based: int,
              formula_sep: str = ",") -> Dict[str, str]:
    """{header: value} for a NEW row: id/name-derived filename fields + operator + the artifacts
    string + a BMI formula. Operator-judgment dropdowns are seeded with their CHOOSE_OPTION
    default ('Seçiniz' — the operator must actively pick a real value); free-text manual fields
    and the watcher-owned check fields stay blank."""
    vals: Dict[str, str] = {}
    for c in COLUMNS:
        if c.source == "seq":
            vals[c.header] = str(row_1based - 1)
        elif c.source == "filename":
            vals[c.header] = _FILENAME_VALUE[c.header](parsed) or ""
        elif c.source == "subfolder":
            vals[c.header] = operator
        elif c.source == "formula":
            vals[c.header] = bmi_formula(row_1based, formula_sep)
        elif c.source == "computed":
            vals[c.header] = artifacts or ""
        else:  # manual / check — seed the per-column default (Seçiniz for judgment dropdowns, else "")
            vals[c.header] = c.default
    return vals


def merge_fill_missing(existing: Dict[str, str], new: Dict[str, str]) -> Dict[str, str]:
    """Upsert rule: keep every non-empty existing cell (never clobber an operator's
    entry); fill only the blanks from `new`."""
    out = dict(existing)
    for header in HEADERS:
        cur = (out.get(header) or "").strip()
        if not cur:
            out[header] = new.get(header, "")
    return out


def row_to_list(vals: Dict[str, str]) -> List[str]:
    """Dict -> ordered list in COLUMNS order, ready to write to a sheet row."""
    return [vals.get(h, "") for h in HEADERS]


def dropdown_values(col: Column, options: Tuple[str, ...]) -> Tuple[List[str], bool]:
    """(dropdown values, strict?) for a column's data validation.

    Operator-editable dropdowns get a leading CHOOSE_OPTION ('Seçiniz') and strict=False: it is
    the seeded default + the revert target (re-pick it to undo a wrong choice), and strict=False
    also lets the cell be cleared/retyped. The formula-filled + protected Cinsiyet column (the
    only dropdown in AUTO_HEADERS) keeps its plain strict list — the operator never edits it."""
    if col.header in AUTO_HEADERS:
        strict = not col.allow_other and col.header != "Operator"
        return list(options), strict
    return [CHOOSE_OPTION] + list(options), False


# --- EVERY name-encoded field is now a LIVE formula keyed off the Study Adı cell, so a row fully
# auto-fills (date -> demographics -> ASIS -> probe -> leg direction) the instant the id is present
# — operator-typed OR CLI-written — and self-corrects if it is edited. On a NEW row the CLI writes
# only what the NAME can't encode: Study Adı + Operator + Artifacts + the operator-judgment
# dropdowns seeded with their 'Seçiniz' default. AUTO columns are owned by the formulas;
# 'Kontrol Sonucu'/'Detayı' by the watcher; Notlar is free text. (Akadir 2026-06-22: the leg/ASIS/
# probe dropdowns were REMOVED — the name is the single source for them.)
AUTO_HEADERS = ("Sıra", "Tarih", "Hasta Adı", "Hasta Soyadı", "Cinsiyet",
                "Yaş", "Boy (cm)", "Kilo (kg)", "BMI",
                "Dominant Bacak", "ASIS-Patella Sol (cm)", "ASIS-Patella Sağ (cm)", "Probe No")
CLI_HEADERS = ("Study Adı",
               "Yaralanma/Cerrahi (alt ekstremite, son 6 ay)", "Alt-kohort mu", "Operator",
               "Software Version", "Presets Ayarlı mı",
               "Artifacts (mp4/RawImages/dcm)", "Dosyalar Tam mı")
# String columns typed literally (CLI/operator/watcher) -> force plain-text to block
# date-coercion (and, for ASIS, locale decimal-mark coercion). AUTO columns are NOT here:
# text format would turn their formula into literal text.
TEXT_FORMAT_HEADERS = ("Study Adı",
                       "Yaralanma/Cerrahi (alt ekstremite, son 6 ay)", "Alt-kohort mu",
                       "Operator", "Software Version", "Presets Ayarlı mı",
                       "Artifacts (mp4/RawImages/dcm)", "Dosyalar Tam mı", "Notlar",
                       "Kontrol Sonucu", "Kontrol Detayı",
                       # force/strength criterion — operator-typed numerics; text-format so a value
                       # like 26.8 or 267.0 stays literal (no date / locale-decimal coercion).
                       "İzometrik Tepe Tork (Nm)", "İzometrik CV (%)",
                       "İzokinetik Tepe Tork Ekst (Nm)", "İzokinetik Ort. Güç Ekst (W)",
                       "İzokinetik CV (%)",
                       # operator-typed name + machine-written flag text — literal, never coerced
                       "Force Operator", "Ölçüm Kontrol")


def array_formulas(sep: str = ",") -> Dict[str, str]:
    """{header: row-2 ARRAYFORMULA} parsing the Study Adı column ($C$2:$C). Demographics are
    extracted by ANCHORING on the <age><sex> token (digits + one letter), NOT by counting from
    the end — the new schema appends `_<ASIS>_<probe>`, so an END-anchored read would put the
    PROBE in the weight cell and blank age/sex/height. The trailing fields are simply not
    matched (no `$`). Names stay positional from the front. `sep` is the formula argument
    separator (',' en-locale, ';' tr_TR)."""
    c, s = "$C$2:$C", sep
    # Every extractor parses `folded` — the name with runs of underscores collapsed to one — the
    # sheet-formula mirror of the pipeline's `_norm` fold. Without it, a Study Adı pasted
    # byte-exact from a zip carrying a '__' typo (anonaau_anonadl__20k, 2026-08-24) blanks the
    # NAME cells while demographics still render: the name patterns require exactly one '_'
    # between tokens. Letter-level typos stay un-bridgeable by design — only delimiter runs fold.
    folded = f'REGEXREPLACE({c}{s}"_+"{s}"_")'
    yas = f'VALUE(REGEXEXTRACT({folded}{s}"_(\\d+)[A-Za-z]_\\d+_\\d+"))'
    boy = f'VALUE(REGEXEXTRACT({folded}{s}"_\\d+[A-Za-z]_(\\d+)_\\d+"))'
    kilo = f'VALUE(REGEXEXTRACT({folded}{s}"_\\d+[A-Za-z]_\\d+_(\\d+)"))'
    letter = f'UPPER(REGEXEXTRACT({folded}{s}"_\\d+([A-Za-z])_\\d+_\\d+"))'
    expr = {
        "Sıra": f"ROW({c})-1",
        "Tarih": f'REGEXEXTRACT({folded}{s}"^([^_]+)_")',
        # Names anchored BACKWARD off the <age><sex>_<boy>_<kilo> run (not counted from the
        # front), so the logistics group up front (count, probe) can't shift them. A name token
        # can't start with a digit ([^_0-9]…), so the probe/count are never mistaken for a name.
        "Hasta Soyadı": f'REGEXEXTRACT({folded}{s}"_([^_0-9][^_]*)_[^_0-9][^_]*_\\d+[A-Za-z]_\\d+_\\d+")',
        "Hasta Adı": f'REGEXEXTRACT({folded}{s}"_([^_0-9][^_]*)_\\d+[A-Za-z]_\\d+_\\d+")',
        "Cinsiyet": f'IF(REGEXMATCH({letter}{s}"K|F"){s}"K"{s}"E")',
        "Yaş": yas,
        "Boy (cm)": boy,
        "Kilo (kg)": kilo,
        "BMI": f'ROUND({kilo}/({boy}/100)^2{s}1)',
    }
    # name-encoded fields, now FORMULA-filled too (Akadir 2026-06-22): leg -> SAĞ/SOL (its valid
    # domain); probe -> a value in PROBE_NUMBERS (validated whitelist, front OR tail); ASIS -> the
    # post-anchor number, routed to the DOMINANT leg's column by the l/r suffix (the dominant leg is
    # the only one measured). Each blanks via the IFERROR wrapper when the name omits it.
    leg = f'IFERROR(UPPER(REGEXEXTRACT({folded}{s}"_\\d+(?:\\.\\d+)?([rRlL])$")){s}"")'
    asis = f'IFERROR(VALUE(REGEXEXTRACT({folded}{s}"_\\d+[A-Za-z]_\\d+_\\d+_(\\d+(?:\\.\\d+)?)")){s}"")'
    probe_alt = "|".join(str(p) for p in PROBE_NUMBERS)
    expr["Dominant Bacak"] = f'IF({leg}="R"{s}"SAĞ"{s}IF({leg}="L"{s}"SOL"{s}""))'
    # ASIS goes to the imaged leg only: left -> Sol, right -> Sağ; the other leg stays blank.
    expr["ASIS-Patella Sol (cm)"] = f'IF({leg}="L"{s}{asis}{s}"")'
    expr["ASIS-Patella Sağ (cm)"] = f'IF({leg}="R"{s}{asis}{s}"")'
    expr["Probe No"] = f'REGEXEXTRACT({folded}{s}"_({probe_alt})(?:_|$)")'
    return {h: f'=ARRAYFORMULA(IF({c}=""{s}""{s}IFERROR({expr[h]}{s}"")))' for h in AUTO_HEADERS}


def auto_column_ranges() -> List[Tuple[int, int]]:
    """Contiguous [start, end) 0-based column index ranges of the AUTO formula columns, to
    protect from operator edits (a hand-edit into a spilled cell breaks the whole ARRAYFORMULA).
    The editable manual columns (Study Adı, the judgment dropdowns) sit between them, so this
    returns several ranges, e.g. (Sıra,Tarih), (Hasta Adı … ASIS Sağ), and (Probe No)."""
    idxs = sorted(col_index(h) for h in AUTO_HEADERS)
    ranges: List[Tuple[int, int]] = []
    start = prev = idxs[0]
    for i in idxs[1:]:
        if i == prev + 1:
            prev = i
        else:
            ranges.append((start, prev + 1))
            start = prev = i
    ranges.append((start, prev + 1))
    return ranges


def cli_cells(built: Dict[str, str]) -> Dict[str, str]:
    """The subset of a build_row() dict the CLI actually writes (id can't encode
    Operator/Artifacts; defaults are editable). AUTO columns are left to the formulas."""
    return {h: built.get(h, "") for h in CLI_HEADERS}

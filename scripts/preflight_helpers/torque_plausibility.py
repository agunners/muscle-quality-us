"""Dynamometer-entry plausibility — the pure rules behind the measurement gate.

Why this exists (DATA_LIFECYCLE §7 item 8, closed by this module): `check_studies` validates
*recordings*; nothing validated *measurements*. Two kgf·m-as-Nm entries (isometric ~1/9 of the same
subject's isokinetic torque, CV exactly 0.0) sat in the criterion for weeks and, kept in, would have
nearly halved the scan's measured strength value (force-ladder sensitivity SENS-C, 2026-08-11).

Criteria are Akadir's (2026-08-12, [[project_flag_kill_ledger]]): RATIO-based, never absolute
difference — isometric normally EXCEEDS isokinetic (cohort median iso/isok = 1.28, n=125), and a
large absolute gap can be valid. Tiered:

  HARD (entry-error signature -> flag, held out of new analyses by default, pending human verdict):
    CV exactly 0.0 · iso/isok below ~0.3 · iso x 9.81 lands on isok (kgf·m read as Nm) ·
    Nm/kg beyond human capability · a torque <= 0 ·
    isometric CV above 15% (not an entry error — a RELIABILITY rule, Akadir 2026-08-13).
  SOFT (atypical -> flag only, STAYS in analysis): iso/isok below ~0.7 · an unparseable cell.

This module is pure classification over raw cell strings — no sheet, no ledger, no I/O — so it is
unit-testable against the three real cases and reusable by any caller (the sync gate, the substrate
build, an ad-hoc sweep). Recording verdicts (Kontrol Sonucu) are a DIFFERENT instrument; nothing
here touches them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Tuple

# The five criterion cells, by their SUBSTRATE (qa_master.csv) column names — the ledger's field
# vocabulary. The kayit sheet headers map onto these in the callers.
F_IZO_TORQUE = "izo_torque_nm"
F_IZO_CV = "izo_cv_pct"
F_ISOK_TORQUE = "isok_torque_ext_nm"
F_ISOK_POWER = "isok_mean_power_ext_w"
F_ISOK_CV = "isok_cv_pct"
FORCE_FIELDS: Tuple[str, ...] = (F_IZO_TORQUE, F_IZO_CV, F_ISOK_TORQUE, F_ISOK_POWER, F_ISOK_CV)

# --- thresholds, each pinned to the real data that set it -------------------------------------
# iso/isok on the two confirmed suspects: 9.1/81.3 = 0.112 and 8.7/84.5 = 0.103. Cohort median is
# 1.28; the single borderline (sheet row 24) sits at 0.64. 0.30 separates impossible from
# borderline with a wide margin on both sides.
RATIO_HARD_MAX = 0.30
# The 2026-08-12 sweep over all 125 found exactly ONE row below 0.70 that lacks the entry-error
# signature (row 24, ratio 0.64, CV 4.6%) — flagged for audit, deliberately kept in analysis.
RATIO_SOFT_MAX = 0.70
# kgf·m read as Nm: iso x g should land on the isokinetic value. Real cases: 9.1x9.81 = 89.3 vs
# 81.3 (off 9.8%) and 8.7x9.81 = 85.3 vs 84.5 (off 1.0%). 15% covers both with headroom while a
# genuine ratio-0.3 pair (iso x 9.81 = 2.9x isok, off 190%) can never match.
KGF_G = 9.81
KGF_TOL = 0.15
# Isometric knee-extension peak torque tops out near ~4.5 Nm/kg in elite athletes; 6.0 is beyond
# human. No LOW bound on purpose: genuine weakness exists and the low-side entry errors are what
# the ratio rules catch. (The one-time "cohort floor is 8 Nm" justification was itself row 92's
# kgf·m entry — killed by Akadir 2026-08-13; the served floor is 75.5 Nm at n=101.)
NM_PER_KG_MAX = 6.0
# Akadir's reliability rule (2026-08-13 answers doc: "I don't think these should be part of
# analysis, part of cohort placement or force calculations. Let's do this → above 15 CV"): an
# isometric CV above 15% marks the session too unstable to serve anywhere — completing the
# ratified ≤10 / 10–15 / >15 display tiers into a hold. ISOMETRIC only (the criterion mode):
# isokinetic CV runs hot legitimately (a real row reads 26.8) and is never the served value.
# Exactly two rows today: CV 15.1 and 19.2. The boundary 15.0 itself stays in.
CV_UNRELIABLE_PCT = 15.0


@dataclass(frozen=True)
class Finding:
    code: str            # one of OPERATOR_TR's keys
    tier: str            # "hard" | "soft"
    fields: Tuple[str, ...]   # the impugned cells (substrate names) — what a hard flag HOLDS
    evidence: str        # one English sentence carrying the numbers


# Operator-facing one-liners (Turkish, like every other sheet-visible string). Keyed by Finding.code
# so the code list and its human meaning cannot drift apart.
OPERATOR_TR: Dict[str, str] = {
    "kgf_m_as_nm": "tork birimi şüpheli (kgf·m, Nm olarak girilmiş olabilir)",
    "impossible_ratio": "izometrik/izokinetik oranı imkânsız düşük",
    "low_ratio": "izometrik/izokinetik oranı düşük",
    "cv_zero": "CV tam 0.0 — tek değer girilmiş olabilir",
    "cv_unreliable": "izometrik CV %15 üzeri — ölçüm kararsız, tekrar önerilir",
    "impossible_nm_per_kg": "Nm/kg fizyolojik aralığın dışında",
    "nonpositive_torque": "tork değeri sıfır veya negatif",
    "unparseable": "değer sayı olarak okunamıyor",
}


def parse_num(raw: Optional[str]) -> Optional[float]:
    """A cell string -> float, or None. Tolerates whitespace and a Turkish decimal comma
    ('26,8' -> 26.8). Anything else non-numeric -> None — the CALLER decides whether a non-blank
    unparseable cell is a finding (it is), so this never guesses at '26.8 / 3'-style strings."""
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return float(s.replace(",", "."))
    except ValueError:
        return None


def classify(values: Mapping[str, str]) -> List[Finding]:
    """The plausibility verdicts for ONE study's criterion cells.

    `values`: raw cell strings under FORCE_FIELDS keys, plus optional "weight_kg". Blank cells are
    facts about people (no dynamometer session), never findings. Returns [] for a clean row.
    """
    out: List[Finding] = []
    num: Dict[str, Optional[float]] = {}
    for f in FORCE_FIELDS:
        raw = (values.get(f) or "").strip()
        num[f] = parse_num(raw)
        if raw and num[f] is None:
            out.append(Finding("unparseable", "soft", (f,),
                               f"{f} = {raw!r} does not parse as a number"))

    iso, isok = num[F_IZO_TORQUE], num[F_ISOK_TORQUE]
    weight = parse_num(values.get("weight_kg"))

    # A recorded torque of <= 0 Nm is not a measurement. Checked before any ratio so the ratio
    # rules below can safely divide.
    for f, v in ((F_IZO_TORQUE, iso), (F_ISOK_TORQUE, isok)):
        if v is not None and v <= 0:
            out.append(Finding("nonpositive_torque", "hard", (f, ),
                               f"{f} = {v:g} Nm"))

    if iso is not None and isok is not None and iso > 0 and isok > 0:
        ratio = iso / isok
        if ratio < RATIO_HARD_MAX:
            if abs(iso * KGF_G - isok) / isok <= KGF_TOL:
                # the specific unit signature: the ISOMETRIC pair is the impugned side (it reads
                # as kgf·m), the isokinetic value is the one x9.81 reproduces.
                out.append(Finding(
                    "kgf_m_as_nm", "hard", (F_IZO_TORQUE, F_IZO_CV),
                    f"iso {iso:g} vs isok {isok:g} Nm (ratio {ratio:.2f}); "
                    f"iso x {KGF_G:g} = {iso * KGF_G:.1f} lands on isok"))
            else:
                # impossible but unattributable — neither side can be trusted, hold all five.
                out.append(Finding(
                    "impossible_ratio", "hard", FORCE_FIELDS,
                    f"iso {iso:g} vs isok {isok:g} Nm (ratio {ratio:.2f} < {RATIO_HARD_MAX})"))
        elif ratio < RATIO_SOFT_MAX:
            out.append(Finding(
                "low_ratio", "soft", (F_IZO_TORQUE, F_ISOK_TORQUE),
                f"iso {iso:g} vs isok {isok:g} Nm (ratio {ratio:.2f} < {RATIO_SOFT_MAX}; "
                f"cohort median 1.28)"))

    # Isometric CV above 15% — the measurement is real but too unstable to serve (Akadir's rule,
    # 2026-08-13). Deliberately NOT symmetric: the isokinetic mode is never the served criterion.
    izo_cv = num[F_IZO_CV]
    if izo_cv is not None and izo_cv > CV_UNRELIABLE_PCT and iso is not None:
        out.append(Finding("cv_unreliable", "hard", (F_IZO_TORQUE, F_IZO_CV),
                           f"isometric CV {izo_cv:g}% > {CV_UNRELIABLE_PCT:g}% — session too "
                           f"unstable to serve (rule of 2026-08-13)"))

    # CV exactly 0.0 beside a recorded torque marks a single-value entry (no human triplicate is
    # perfectly identical). Symmetric over both modes; each holds its OWN mode's pair.
    if num[F_IZO_CV] == 0.0 and iso is not None:
        out.append(Finding("cv_zero", "hard", (F_IZO_TORQUE, F_IZO_CV),
                           f"isometric CV exactly 0.0 with torque {iso:g} Nm present"))
    if num[F_ISOK_CV] == 0.0 and isok is not None:
        out.append(Finding("cv_zero", "hard", (F_ISOK_TORQUE, F_ISOK_POWER, F_ISOK_CV),
                           f"isokinetic CV exactly 0.0 with torque {isok:g} Nm present"))

    if iso is not None and weight is not None and weight > 0:
        nm_kg = iso / weight
        if nm_kg > NM_PER_KG_MAX:
            out.append(Finding("impossible_nm_per_kg", "hard", (F_IZO_TORQUE, F_IZO_CV),
                               f"iso {iso:g} Nm / {weight:g} kg = {nm_kg:.2f} Nm/kg "
                               f"> {NM_PER_KG_MAX:g}"))
    return out

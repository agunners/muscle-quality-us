"""
Cohort-stats pre-compute for the Friday MVP assessment view.

Reads:
  (1) canonical labelling-experiment decision_log (3 raters × 282 completed
      studies) — provides per-study geometric measurements + demographics +
      mode quality_label;
  (2) white-paper study_features.csv (Round-2 canonical run) — provides
      two validated quality-axis features:
        - int_cv_within (gain-invariant within-frame echo CV;
          intensity family, agg_primary, canonical column =
          `final_rf_int_cv_within_agg_median`)
        - full_thickness_q50_mm (per-column median fascia-to-fascia
          thickness; geometry family, anchor_primary, canonical column =
          `final_rf_full_thickness_q50_mm_repr`)

Emits four artifacts:
  cohort_summary.json     overall + by-grade + by-sex + by-age-band distribution
                          stats per measurement (mean/median/std/percentile
                          breakpoints).
  per_study_cohort.csv    deduplicated per-study table (one row per study_code)
                          for the dashboard to render distributions/scatters.
  triple_placement.json   case-study triple (ACL762/AGZ855/DFN534) percentile
                          placement for each measurement against (a) overall,
                          (b) age-matched (+/-10y), (c) sex-matched.
  three_way_agreement.json
                          F3 reactive number — fraction of triple-rated studies
                          where all 3 raters gave the same final quality_label.

v2.2 (2026-05-12): added int_cv_within + full_thickness_q50_mm per Akadir's
"real carrot from quality work" directive. The principle of "stay quantitative"
refined: validated features (Stage F survivors with audited white-paper
backing) are in regardless of family; unvalidated raw stats (rf_echo,
vi_echo) remain out.

v2.3 (2026-05-14): qmt_mm + star_ratio are Tier-1-gated. The per-study cohort
value is the median of that study's `standard_qmt` (femur-anchored) rater rows
only; a study with no standard_qmt rater contributes NaN and drops out of the
QMT/STAR distributions. A per-study `qmt_provenance` label records the
composition. Tier-1 cohort n=224 of 282 — the box-plot barely moves. Mirrors
the run-side provenance gate (QMT fix 2/3, preflight_helpers/aggregation.py).

v2.4 (2026-05-14): vi_thickness_mm + vi_area_mm2 dropped from MEASUREMENT_COLS
— the VI segmentation is not robust enough to surface, so VI is removed from
the assessment pipeline + UI. VI stays a computational primitive (QMT spans
the RF+VI stack; muscle_fat_ratio uses VI area) — only standalone VI features
go. MEASUREMENT_COLS 9 -> 7. Mirrors run_pipeline.SECONDARY_FEATURES.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

# Inputs that live OUTSIDE this repo (2026-09-05, machine-transition audit). The decision log is
# byte-identical to the copy this repo carries (configs/decision_log.csv, md5 5fa4d398…), so read that
# and keep the historical path as a fallback. The run artifact is the exploration repo's gitignored
# output; it does not travel by git or by the handover drive — TRANSITION.yaml records how to fetch it.
DECISION_LOG = Path(__file__).resolve().parent.parent / "configs" / "decision_log.csv"
if not DECISION_LOG.is_file():
    DECISION_LOG = Path(
        str(Path(__file__).resolve().parents[1] / "private" / "muscle_quality_exploration_codex_implementation/decision_log.csv")
    )
STUDY_FEATURES = Path(os.environ.get(
    "MUSCLE_STUDY_FEATURES_CSV",
    str(Path(__file__).resolve().parents[1] / "private" / "muscle_quality_exploration/runs/20260503/034929_default_infer/study_features.csv"),
))
OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "cohort_stats"

WHITE_PAPER_FEATURES = {
    "int_cv_within": "final_rf_int_cv_within_agg_median",
    "full_thickness_q50_mm": "final_rf_full_thickness_q50_mm_repr",
}

MEASUREMENT_COLS = [
    "rf_thickness_mm",
    "qmt_mm",
    "visible_rf_area_mm2",
    "muscle_fat_ratio",
    "star_ratio",
    "int_cv_within",
    "full_thickness_q50_mm",
    "fat_thickness_q50_mm",
    "fat_thickness_max_mm",
    "fat_to_depth_q50",
]
DEMO_COLS = ["age", "sex", "bmi"]
GRADES = ["PRESERVED", "INTERMEDIATE", "POOR"]
AGE_BANDS = [(20, 40), (40, 60), (60, 80)]
TRIPLE_CODES = ["ACL762", "AGZ855", "DFN534"]


def distribution_stats(values: pd.Series) -> dict:
    v = values.dropna()
    if len(v) == 0:
        return {"n": 0}
    return {
        "n": int(len(v)),
        "mean": float(v.mean()),
        "median": float(v.median()),
        "std": float(v.std()),
        "min": float(v.min()),
        "p10": float(v.quantile(0.10)),
        "p25": float(v.quantile(0.25)),
        "p50": float(v.quantile(0.50)),
        "p75": float(v.quantile(0.75)),
        "p90": float(v.quantile(0.90)),
        "max": float(v.max()),
    }


def mode_or_none(s: pd.Series):
    m = s.mode()
    return m.iloc[0] if not m.empty else None


def percentile_of(value: float, ref: pd.Series) -> float | None:
    r = ref.dropna()
    if len(r) == 0:
        return None
    return float((r < value).sum() / len(r) * 100)


def _study_qmt_class(provenances: pd.Series) -> str:
    """Per-study QMT provenance class across this study's rater rows."""
    provs = set(provenances.dropna())
    if "standard_qmt" in provs:
        return "standard"
    if "femur_supported_proxy" in provs:
        return "proxy_only"
    if "no_femur_proxy" in provs:
        return "no_femur"
    return "none"


def _tier1_cohort_qmt(group: pd.DataFrame) -> pd.Series:
    """QMT + STAR for the cohort baseline — Tier-1 (`standard_qmt`) rows only.

    The cohort reference distribution must be one consistent measurement, so a
    study contributes its QMT/STAR only from its femur-anchored rater rows; a
    study with no `standard_qmt` rater contributes NaN and drops out. Mirrors
    the run-side provenance gate (QMT fix 2/3, preflight_helpers/aggregation.py).
    """
    std = group[group["qmt_provenance"] == "standard_qmt"]
    return pd.Series({
        "qmt_mm": float(std["qmt_mm"].median()) if len(std) else float("nan"),
        "star_ratio": float(std["star_ratio"].median()) if len(std) else float("nan"),
        "qmt_provenance": _study_qmt_class(group["qmt_provenance"]),
    })


def write_triple_placement(per_study: pd.DataFrame) -> dict:
    """Build + write triple_placement.json — the case-study triple's percentile
    placements against the cohort. Extracted so cohort_landmark_precompute can
    regenerate the snapshot whenever the RF-landmark columns change, keeping the
    cohort artifacts consistent. A MEASUREMENT_COLS entry missing from `per_study`
    is skipped (not a crash)."""
    triple_placement: dict = {}
    for sc in TRIPLE_CODES:
        row = per_study[per_study["study_code"] == sc]
        if row.empty:
            triple_placement[sc] = {"error": "not_found"}
            continue
        r = row.iloc[0]
        place: dict = {
            "demographics": {
                "age": None if pd.isna(r["age"]) else float(r["age"]),
                "sex": None if pd.isna(r["sex"]) else str(r["sex"]),
                "bmi": None if pd.isna(r["bmi"]) else float(r["bmi"]),
            },
            "quality_label": None if pd.isna(r["quality_label"]) else str(r["quality_label"]),
        }
        for col in MEASUREMENT_COLS:
            if col not in per_study.columns:
                continue
            val = r[col]
            entry: dict = {"value": None if pd.isna(val) else float(val)}
            if pd.isna(val):
                place[col] = entry
                continue
            entry["pctile_overall"] = percentile_of(val, per_study[col])
            entry["n_overall"] = int(per_study[col].notna().sum())
            if not pd.isna(r["age"]):
                age_lo, age_hi = float(r["age"]) - 10, float(r["age"]) + 10
                age_band = per_study[
                    (per_study["age"] >= age_lo) & (per_study["age"] <= age_hi)
                ][col]
                if age_band.notna().sum() > 0:
                    entry["pctile_age_matched"] = percentile_of(val, age_band)
                    entry["n_age_matched"] = int(age_band.notna().sum())
                    entry["age_band"] = f"{int(age_lo)}-{int(age_hi)}"
            if r["sex"] in ["M", "F"]:
                sx_band = per_study[per_study["sex"] == r["sex"]][col]
                if sx_band.notna().sum() > 0:
                    entry["pctile_sex_matched"] = percentile_of(val, sx_band)
                    entry["n_sex_matched"] = int(sx_band.notna().sum())
                    entry["sex_band"] = str(r["sex"])
            place[col] = entry
        triple_placement[sc] = place

    with open(OUT_DIR / "triple_placement.json", "w") as f:
        json.dump(triple_placement, f, indent=2)
    print(f"Wrote {OUT_DIR/'triple_placement.json'}")
    return triple_placement


def main() -> None:
    df = pd.read_csv(DECISION_LOG, low_memory=False)
    print(f"Loaded {len(df)} rows from {DECISION_LOG.name}")
    print(f"  decision_status: {df['decision_status'].value_counts(dropna=False).to_dict()}")
    print(f"  unique study_codes: {df['study_code'].nunique()}")
    print(f"  operators: {sorted(df['operator_id'].dropna().unique())}")

    completed = df[df["decision_status"] == "completed"].copy()
    print(f"Completed decisions: {len(completed)} across "
          f"{completed['study_code'].nunique()} studies")

    rater_count = completed.groupby("study_code")["operator_id"].nunique()
    triple_rated_studies = rater_count[rater_count >= 3].index
    print(f"Triple-rated studies (>=3 distinct raters): {len(triple_rated_studies)}")

    decision_log_measurement_cols = [
        c for c in MEASUREMENT_COLS if c in completed.columns
    ]
    # qmt_mm + star_ratio get the Tier-1 provenance gate (below); every other
    # measurement keeps the first-rater value.
    generic_cols = [c for c in decision_log_measurement_cols
                    if c not in ("qmt_mm", "star_ratio")]
    agg = {c: "first" for c in generic_cols + DEMO_COLS}
    per_study = (
        completed.groupby("study_code", as_index=False).agg(agg)
    )
    labels = (
        completed.groupby("study_code")["quality_label"]
        .apply(mode_or_none)
        .reset_index()
    )
    per_study = per_study.merge(labels, on="study_code", how="left")
    per_study["triple_rated"] = per_study["study_code"].isin(triple_rated_studies)

    # qmt_mm + star_ratio — Tier-1 (standard_qmt) cohort baseline. Studies with
    # no standard_qmt rater get NaN and drop out of the QMT/STAR distributions;
    # a per-study qmt_provenance label records why.
    qmt_per_study = (
        completed.groupby("study_code")[["qmt_provenance", "qmt_mm", "star_ratio"]]
        .apply(_tier1_cohort_qmt)
        .reset_index()
    )
    per_study = per_study.merge(qmt_per_study, on="study_code", how="left")
    qmt_class_counts = per_study["qmt_provenance"].value_counts(dropna=False).to_dict()
    print(f"  QMT Tier-1 gate: per-study provenance = {qmt_class_counts}")
    print(f"  QMT cohort n: {int(per_study['qmt_mm'].notna().sum())} Tier-1 studies "
          f"(was {int(completed['qmt_mm'].notna().sum())} rater-rows, any provenance)")

    sf = pd.read_csv(STUDY_FEATURES, low_memory=False)
    print(f"Loaded {len(sf)} rows from {STUDY_FEATURES.name}")
    wp_subset = sf[["study_code"] + list(WHITE_PAPER_FEATURES.values())].rename(
        columns={src: dst for dst, src in WHITE_PAPER_FEATURES.items()}
    )
    per_study = per_study.merge(wp_subset, on="study_code", how="left")
    wp_coverage = {
        feat: int(per_study[feat].notna().sum())
        for feat in WHITE_PAPER_FEATURES
    }
    print(f"  white-paper feature coverage: {wp_coverage} of {len(per_study)} studies")

    # Fat-q50 cohort backfill (2026-06-04). Fat thickness has no white-paper
    # column, so it is computed per study over the anchor frame by
    # scripts/cohort_fat_backfill.py (matching the run-side compute_fat_geometry)
    # and merged here as fat_thickness_q50_mm / fat_thickness_max_mm / fat_to_depth_q50.
    fat_csv = OUT_DIR / "fat_q50_by_study.csv"
    if fat_csv.exists():
        fat_df = pd.read_csv(fat_csv)
        fat_cols = [c for c in ("fat_thickness_q50_mm", "fat_thickness_max_mm",
                                "fat_to_depth_q50") if c in fat_df.columns]
        per_study = per_study.merge(
            fat_df[["study_code"] + fat_cols], on="study_code", how="left")
        fat_cov = {c: int(per_study[c].notna().sum()) for c in fat_cols}
        print(f"  fat coverage: {fat_cov} of {len(per_study)} studies")

    per_study.to_csv(OUT_DIR / "per_study_cohort.csv", index=False)
    print(f"Wrote {OUT_DIR/'per_study_cohort.csv'} ({len(per_study)} studies)")

    cohort_stats: dict = {
        "n_studies_total": int(len(per_study)),
        "n_studies_triple_rated": int(len(triple_rated_studies)),
        "overall": {},
        "by_grade": {},
        "by_sex": {},
        "by_age_band": {},
    }

    for col in MEASUREMENT_COLS:
        cohort_stats["overall"][col] = distribution_stats(per_study[col])

    for col in MEASUREMENT_COLS:
        cohort_stats["by_grade"][col] = {
            g: distribution_stats(per_study[per_study["quality_label"] == g][col])
            for g in GRADES
        }

    for col in MEASUREMENT_COLS:
        cohort_stats["by_sex"][col] = {
            sx: distribution_stats(per_study[per_study["sex"] == sx][col])
            for sx in ["M", "F"]
        }

    for col in MEASUREMENT_COLS:
        cohort_stats["by_age_band"][col] = {
            f"{lo}-{hi}": distribution_stats(
                per_study[(per_study["age"] >= lo) & (per_study["age"] < hi)][col]
            )
            for (lo, hi) in AGE_BANDS
        }

    with open(OUT_DIR / "cohort_summary.json", "w") as f:
        json.dump(cohort_stats, f, indent=2)
    print(f"Wrote {OUT_DIR/'cohort_summary.json'}")

    triple_placement = write_triple_placement(per_study)

    rater_label_sets = completed.groupby("study_code")["quality_label"].apply(set)
    rater_count_per_study = completed.groupby("study_code")["operator_id"].nunique()
    triple_studies = rater_count_per_study[rater_count_per_study >= 3].index
    triple_rater_labels = rater_label_sets.loc[triple_studies]
    three_way_agreed = triple_rater_labels.apply(lambda s: len(s) == 1)
    n_total = int(len(triple_studies))
    n_agreed = int(three_way_agreed.sum())
    rate = (n_agreed / n_total * 100) if n_total else 0.0

    by_grade_agreement: dict = {}
    for g in GRADES:
        is_g = triple_rater_labels.apply(lambda s: s == {g})
        by_grade_agreement[g] = int(is_g.sum())

    agreement = {
        "studies_triple_rated": n_total,
        "three_way_agreement_count": n_agreed,
        "three_way_agreement_rate_pct": round(rate, 2),
        "agreement_by_grade": by_grade_agreement,
        "disagreement_count": n_total - n_agreed,
    }
    with open(OUT_DIR / "three_way_agreement.json", "w") as f:
        json.dump(agreement, f, indent=2)
    print(f"Wrote {OUT_DIR/'three_way_agreement.json'}")
    print(
        f"  Three-way rate: {n_agreed}/{n_total} = {rate:.1f}%  "
        f"(by grade: {by_grade_agreement})"
    )

    print("\nTriple placement summary:")
    for sc in TRIPLE_CODES:
        p = triple_placement[sc]
        if "error" in p:
            print(f"  {sc}: NOT FOUND")
            continue
        star = p.get("star_ratio", {})
        thick = p.get("rf_thickness_mm", {})
        star_v = star.get("value")
        star_str = (
            f"{star_v:.2f} (overall p{star.get('pctile_overall', float('nan')):.0f}, "
            f"age p{star.get('pctile_age_matched', float('nan')):.0f})"
            if star_v is not None else "n/a (no Tier-1 QMT)"
        )
        thick_v = thick.get("value")
        thick_str = (
            f"{thick_v:.1f}mm (overall p{thick.get('pctile_overall', float('nan')):.0f})"
            if thick_v is not None else "n/a"
        )
        print(
            f"  {sc} {p['quality_label']:>13} "
            f"age={p['demographics']['age']:.0f} bmi={p['demographics']['bmi']:.1f}  "
            f"STAR={star_str}  RFthk={thick_str}"
        )


if __name__ == "__main__":
    main()

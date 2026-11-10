"""Decision-log I/O.

Canonical mental model:
  - Each (study_code, operator_id) pair can have ≥1 row (``study_annotation_index``
    can be 2 for a few re-annotations). We keep the row with the latest
    ``saved_at_utc`` per (study_code, operator_id) — **NOT** per study_code,
    which Folder B did and which is wrong for a 3-rater manifest.
  - ``akadir`` is the QC authority. If akadir's latest row for a study is
    ``quality_failed``, the study is excluded from modeling (Track A and B).
  - Track A primary target = majority vote of latest ``preliminary_label``
    across the three latest-(study, operator) rows, after exclusion.
  - Track B complete-case target = majority ``quality_label`` + fixed
    covariates (age, sex, bmi, star_ratio). ``sex == 'unknown'`` is a valid
    category, not missing.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import pandas as pd


LABEL_LEVELS: tuple[str, ...] = ("preserved", "intermediate", "poor")


@dataclass(frozen=True)
class LabelCounts:
    n_rows: int
    n_completed: int
    n_quality_failed: int
    n_studies: int
    n_completed_studies: int
    n_triple_rated_completed: int


def load_decision_log(path: str | Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    # normalise label casing defensively
    for col in ("preliminary_label", "quality_label"):
        if col in df.columns:
            df[col] = df[col].astype("string").str.strip().str.lower()
    for col in ("saved_at_utc",):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
    return df


def raw_counts(df: pd.DataFrame) -> LabelCounts:
    completed_mask = df["decision_status"] != "quality_failed"
    completed = df[completed_mask]
    by_study = completed.groupby("study_code")["operator_id"].nunique()
    return LabelCounts(
        n_rows=int(len(df)),
        n_completed=int(completed_mask.sum()),
        n_quality_failed=int((~completed_mask).sum()),
        n_studies=int(df["study_code"].nunique()),
        n_completed_studies=int(completed["study_code"].nunique()),
        n_triple_rated_completed=int((by_study == 3).sum()),
    )


def latest_rows(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (study_code, operator_id), chosen by latest ``saved_at_utc``.

    Falls back to ``review_save_index`` then row order if timestamps tie.
    """
    if "saved_at_utc" not in df.columns:
        raise KeyError("decision_log missing 'saved_at_utc'")
    sort_keys = ["saved_at_utc"]
    if "review_save_index" in df.columns:
        sort_keys.append("review_save_index")
    if "study_annotation_index" in df.columns:
        sort_keys.append("study_annotation_index")
    out = (
        df.sort_values(sort_keys, kind="mergesort")
        .groupby(["study_code", "operator_id"], sort=False, as_index=False)
        .tail(1)
        .reset_index(drop=True)
    )
    return out


def akadir_quality_failed_studies(latest: pd.DataFrame) -> set[str]:
    if "operator_id" not in latest.columns:
        raise KeyError("latest_rows missing 'operator_id'")
    m = (latest["operator_id"] == "akadir") & (latest["decision_status"] == "quality_failed")
    return set(latest.loc[m, "study_code"])


def _majority(values: Iterable[str | float]) -> tuple[str | None, bool]:
    clean = [v for v in values if isinstance(v, str) and v in LABEL_LEVELS]
    if not clean:
        return None, False
    c = Counter(clean)
    top_count = max(c.values())
    winners = [lbl for lbl, cnt in c.items() if cnt == top_count]
    if len(winners) == 1:
        return winners[0], False
    # tie: ambiguous (e.g. 3-way split, or 1-1 on 2 valid raters)
    return None, True


@dataclass(frozen=True)
class StudyTarget:
    study_code: str
    label_prelim: str | None
    label_quality: str | None
    ambiguous_prelim: bool
    ambiguous_quality: bool
    n_raters: int
    n_prelim: int
    n_quality: int
    triple_rated: bool
    unanimous_prelim: bool
    unanimous_quality: bool
    any_label_changed: bool


def build_study_targets(latest: pd.DataFrame, exclude_studies: set[str]) -> pd.DataFrame:
    """Returns one row per study_code with Track A/B targets + flags.

    Studies in ``exclude_studies`` are dropped entirely; we do not keep them
    with null labels because they are a QC-authority exclusion.
    """
    keep = latest[~latest["study_code"].isin(exclude_studies)]
    rows: list[dict] = []
    for study, grp in keep.groupby("study_code", sort=False):
        prelim_labels = grp["preliminary_label"].dropna().tolist()
        qual_labels = grp["quality_label"].dropna().tolist()
        pl, pl_amb = _majority(prelim_labels)
        ql, ql_amb = _majority(qual_labels)
        unan_pl = len(set(prelim_labels)) == 1 and len(prelim_labels) >= 3
        unan_ql = len(set(qual_labels)) == 1 and len(qual_labels) >= 3
        any_changed = bool(grp["label_changed"].fillna(False).astype(bool).any())
        rows.append({
            "study_code": study,
            "label_prelim": pl,
            "label_quality": ql,
            "ambiguous_prelim": pl_amb,
            "ambiguous_quality": ql_amb,
            "n_raters": int(grp["operator_id"].nunique()),
            "n_prelim": int(len(prelim_labels)),
            "n_quality": int(len(qual_labels)),
            "triple_rated": int(grp["operator_id"].nunique()) == 3,
            "unanimous_prelim": unan_pl,
            "unanimous_quality": unan_ql,
            "any_label_changed": any_changed,
        })
    return pd.DataFrame(rows)


def build_analysis_manifest(
    latest: pd.DataFrame,
    targets: pd.DataFrame,
    exclude_studies: set[str],
) -> pd.DataFrame:
    """One row per latest (study_code, operator_id) with modeling flags.

    Columns:
      - track_a_use (prelim target non-null & not ambiguous)
      - track_b_use (quality target non-null & not ambiguous & all 4 covariates present)
      - excluded_akadir_quality_failed
      - triple_rated, complete_case_track_b, label_changed, ambiguous_majority
    """
    m = latest.copy()
    m["excluded_akadir_quality_failed"] = m["study_code"].isin(exclude_studies)

    # per-study targets join
    t = targets.set_index("study_code")
    m = m.join(t, on="study_code")

    # demographic completeness (Track B complete case)
    demo_cols = ["age", "sex", "bmi", "star_ratio"]
    demo_complete = pd.Series(True, index=m.index)
    for col in demo_cols:
        if col in m.columns:
            if col == "sex":
                # "unknown" is valid; we only reject truly missing
                demo_complete &= m[col].notna()
            else:
                demo_complete &= m[col].notna()
        else:
            demo_complete &= False
    m["complete_case_track_b"] = demo_complete & (~m["excluded_akadir_quality_failed"])

    amb_prelim = m["ambiguous_prelim"].astype("boolean").fillna(False)
    amb_quality = m["ambiguous_quality"].astype("boolean").fillna(False)
    m["ambiguous_majority"] = (amb_prelim | amb_quality).astype(bool)

    m["track_a_use"] = (
        (~m["excluded_akadir_quality_failed"])
        & m["label_prelim"].notna()
        & (~m["ambiguous_prelim"].astype("boolean").fillna(True))
    ).astype(bool)
    m["track_b_use"] = (
        (~m["excluded_akadir_quality_failed"])
        & m["label_quality"].notna()
        & (~m["ambiguous_quality"].astype("boolean").fillna(True))
        & m["complete_case_track_b"]
    ).astype(bool)

    keep_cols = [
        "study_code", "operator_id", "saved_at_utc", "decision_status",
        "preliminary_label", "quality_label", "label_changed",
        "center_frame_base", "age", "sex", "bmi", "star_ratio",
        "label_prelim", "label_quality",
        "ambiguous_prelim", "ambiguous_quality", "ambiguous_majority",
        "triple_rated", "unanimous_prelim", "unanimous_quality",
        "excluded_akadir_quality_failed", "complete_case_track_b",
        "track_a_use", "track_b_use",
    ]
    keep_cols = [c for c in keep_cols if c in m.columns]
    return m[keep_cols].reset_index(drop=True)

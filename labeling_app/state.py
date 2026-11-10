"""SQLite-backed runtime state, export helpers, and decision-log utilities."""
from __future__ import annotations

import csv
import json
import random
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Set

import pandas as pd

DECISION_LOG_COLUMNS = [
    "operator_id",
    "saved_at_utc",
    "session_id",
    "review_visit_id",
    "study_code",
    "decision_status",
    "quality_gate_pass",
    "preliminary_label",
    "preliminary_impression",
    "quality_label",
    "quality_rationale",
    "label_changed",
    "change_reason",
    "center_frame_base",
    "representative_frame_index",
    "representative_frame_reason",
    "rf_thickness_px",
    "rf_thickness_mm",
    "rf_column",
    "vi_thickness_px",
    "vi_thickness_mm",
    "vi_column",
    "qmt_px",
    "qmt_mm",
    "qmt_provenance",
    "qmt_column",
    "qmt_annotation_columns",
    "qmt_muscle_occupancy_pct",
    "qmt_plateau_supported",
    "qmt_central_zone_supported",
    "qmt_femur_slope_abs",
    "qmt_fascia_slope_abs",
    "qmt_validity_flags",
    "qmt_guidance_status",
    "qmt_rf_px",
    "qmt_rf_mm",
    "qmt_vi_px",
    "qmt_vi_mm",
    "star_ratio",
    "rf_echo",
    "vi_echo",
    "visible_rf_area_px",
    "visible_rf_area_mm2",
    "vi_area_px",
    "vi_area_mm2",
    "muscle_fat_ratio",
    "frame_rf_consistency_px",
    "mask_coverage_pct",
    "is_anchor_study",
    "prior_label",
    "anchor_agreement",
    "review_context",
    "review_save_index",
    "study_annotation_index",
    "session_study_number",
    "frame_note_1",
    "frame_note_2",
    "frame_note_3",
    "frame_note_4",
    "age",
    "sex",
    "height_cm",
    "weight_kg",
    "bmi",
    "comorbidities",
    "other_comorbidities",
    "pixel_spacing_x",
    "pixel_spacing_y",
    "dcm_rows",
    "jpg_height",
    "jpg_width",
    "mask_height",
    "mask_width",
]

_HANDLED_STATUSES = {"completed", "skipped", "quality_failed"}


def now_utc_str() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def initialize_runtime_storage(db_path: str) -> None:
    path = Path(db_path)
    try:
        _initialize_runtime_storage_once(db_path)
    except sqlite3.DatabaseError:
        # Clean-slate policy: discard any incompatible prior runtime file.
        if path.exists():
            path.unlink()
        _initialize_runtime_storage_once(db_path)


def _initialize_runtime_storage_once(db_path: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                operator_id TEXT NOT NULL,
                saved_at_utc TEXT NOT NULL,
                session_id TEXT NOT NULL,
                review_visit_id TEXT NOT NULL,
                study_code TEXT NOT NULL,
                review_save_index INTEGER,
                decision_status TEXT NOT NULL,
                is_anchor_study INTEGER DEFAULT 0,
                payload_json TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS review_sessions (
                session_id TEXT PRIMARY KEY,
                operator_id TEXT NOT NULL,
                started_at_utc TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS anchor_queue (
                session_id TEXT NOT NULL,
                operator_id TEXT NOT NULL,
                queue_order INTEGER NOT NULL,
                study_code TEXT NOT NULL,
                consumed INTEGER NOT NULL DEFAULT 0,
                created_at_utc TEXT NOT NULL,
                consumed_at_utc TEXT,
                PRIMARY KEY (session_id, queue_order)
            )
            """
        )


def reset_runtime_storage(db_path: str, export_path: Optional[str] = None) -> None:
    db = Path(db_path)
    if db.exists():
        db.unlink()
    if export_path:
        export = Path(export_path)
        if export.exists():
            export.unlink()


def register_session(
    db_path: str,
    session_id: str,
    operator_id: str,
    started_at_utc: str,
) -> None:
    initialize_runtime_storage(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO review_sessions (session_id, operator_id, started_at_utc)
            VALUES (?, ?, ?)
            """,
            (session_id, operator_id, started_at_utc),
        )


def load_anchor_queue(db_path: str, session_id: str, operator_id: str) -> List[str]:
    initialize_runtime_storage(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT study_code
            FROM anchor_queue
            WHERE session_id = ? AND operator_id = ? AND consumed = 0
            ORDER BY queue_order ASC
            """,
            (session_id, operator_id),
        ).fetchall()
    return [str(row["study_code"]) for row in rows]


def seed_anchor_queue(
    db_path: str,
    session_id: str,
    operator_id: str,
    study_codes: Iterable[str],
    *,
    seed: int,
    limit: int = 5,
) -> List[str]:
    codes = list(dict.fromkeys(str(code) for code in study_codes))
    if not codes:
        return []

    initialize_runtime_storage(db_path)
    with _connect(db_path) as conn:
        existing_active = conn.execute(
            """
            SELECT study_code
            FROM anchor_queue
            WHERE session_id = ? AND operator_id = ? AND consumed = 0
            ORDER BY queue_order ASC
            """,
            (session_id, operator_id),
        ).fetchall()
        if existing_active:
            return [str(row["study_code"]) for row in existing_active]

        prior_codes = {
            str(row["study_code"])
            for row in conn.execute(
                """
                SELECT study_code
                FROM anchor_queue
                WHERE session_id = ? AND operator_id = ?
                """,
                (session_id, operator_id),
            ).fetchall()
        }
        next_queue_order = int(
            conn.execute(
                """
                SELECT COALESCE(MAX(queue_order), 0)
                FROM anchor_queue
                WHERE session_id = ? AND operator_id = ?
                """,
                (session_id, operator_id),
            ).fetchone()[0]
        ) + 1

        candidate_codes = [code for code in codes if code not in prior_codes] or codes
        rng = random.Random(seed)
        sampled = rng.sample(candidate_codes, min(limit, len(candidate_codes)))
        created_at = now_utc_str()
        conn.executemany(
            """
            INSERT INTO anchor_queue (
                session_id, operator_id, queue_order, study_code, consumed, created_at_utc
            )
            VALUES (?, ?, ?, ?, 0, ?)
            """,
            [
                (session_id, operator_id, idx, study_code, created_at)
                for idx, study_code in enumerate(sampled, start=next_queue_order)
            ],
        )
    return sampled


def consume_next_anchor(db_path: str, session_id: str, operator_id: str) -> Optional[str]:
    initialize_runtime_storage(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT queue_order, study_code
            FROM anchor_queue
            WHERE session_id = ? AND operator_id = ? AND consumed = 0
            ORDER BY queue_order ASC
            LIMIT 1
            """,
            (session_id, operator_id),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            """
            UPDATE anchor_queue
            SET consumed = 1, consumed_at_utc = ?
            WHERE session_id = ? AND queue_order = ?
            """,
            (now_utc_str(), session_id, int(row["queue_order"])),
        )
        return str(row["study_code"])


def append_row(db_path: str, row_dict: Dict, export_path: Optional[str] = None) -> None:
    initialize_runtime_storage(db_path)
    row = {col: row_dict.get(col, None) for col in DECISION_LOG_COLUMNS}
    row["saved_at_utc"] = row.get("saved_at_utc") or now_utc_str()
    row["session_id"] = row.get("session_id") or "adhoc_session"
    row["review_visit_id"] = row.get("review_visit_id") or row["saved_at_utc"]
    study_code = str(row.get("study_code") or "").strip()
    if not study_code:
        raise ValueError("append_row requires a non-empty study_code")
    row["study_code"] = study_code
    with _connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO decisions (
                operator_id,
                saved_at_utc,
                session_id,
                review_visit_id,
                study_code,
                review_save_index,
                decision_status,
                is_anchor_study,
                payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row.get("operator_id"),
                row.get("saved_at_utc"),
                row.get("session_id"),
                row.get("review_visit_id"),
                row.get("study_code"),
                _coerce_int(row.get("review_save_index")),
                row.get("decision_status"),
                1 if _truthy(row.get("is_anchor_study")) else 0,
                json.dumps(row, ensure_ascii=True),
            ),
        )
    if export_path:
        export_log(db_path, export_path)


def load_log(db_path: str) -> pd.DataFrame:
    initialize_runtime_storage(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT payload_json FROM decisions ORDER BY id ASC"
        ).fetchall()
    records = [json.loads(str(row["payload_json"])) for row in rows]
    if not records:
        return pd.DataFrame(columns=DECISION_LOG_COLUMNS)
    return pd.DataFrame(records, columns=DECISION_LOG_COLUMNS)


def export_log(db_path: str, export_path: str) -> None:
    df = load_log(db_path)
    out = Path(export_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if df.empty:
        with out.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=DECISION_LOG_COLUMNS)
            writer.writeheader()
        return
    df.to_csv(out, index=False)


def get_effective_decisions(log_df: pd.DataFrame) -> Dict[tuple, pd.Series]:
    if log_df.empty:
        return {}
    sorted_df = log_df.sort_values("saved_at_utc", ascending=True, na_position="first")
    result: Dict[tuple, pd.Series] = {}
    for _, row in sorted_df.iterrows():
        key = (str(row.get("operator_id", "")), str(row.get("study_code", "")))
        result[key] = row
    return result


def get_labeled_set(log_df: pd.DataFrame, operator_id: str) -> Set[str]:
    if log_df.empty:
        return set()
    subset = log_df[
        (log_df["operator_id"].astype(str) == str(operator_id))
        & (log_df["decision_status"].astype(str).isin(_HANDLED_STATUSES))
    ]
    return set(subset["study_code"].dropna().astype(str).unique())


def get_anchorable_set(log_df: pd.DataFrame, operator_id: str) -> Set[str]:
    """Return studies whose latest effective decision is a completed label."""
    effective = get_effective_decisions(log_df)
    anchorable: Set[str] = set()
    for (op, study_code), row in effective.items():
        if op != str(operator_id):
            continue
        if str(row.get("decision_status", "")) != "completed":
            continue
        if not str(row.get("quality_label", "")).strip():
            continue
        anchorable.add(str(study_code))
    return anchorable


def session_summary(
    log_df: pd.DataFrame,
    operator_id: str,
    session_id: str,
    session_start_utc: datetime,
) -> Dict:
    if log_df.empty:
        return {"count": 0, "label_distribution": {}, "elapsed_min": 0.0}

    subset = log_df[
        (log_df["operator_id"].astype(str) == str(operator_id))
        & (log_df["session_id"].astype(str) == str(session_id))
    ].copy()
    if subset.empty:
        elapsed = _elapsed_minutes(session_start_utc)
        return {"count": 0, "label_distribution": {}, "elapsed_min": elapsed}

    first_pass = subset[
        subset["review_save_index"].fillna(0).astype(int) == 1
    ]
    label_dist: Dict[str, int] = {}
    for lbl in first_pass["quality_label"].dropna():
        label = str(lbl)
        label_dist[label] = label_dist.get(label, 0) + 1

    return {
        "count": int(len(first_pass)),
        "label_distribution": label_dist,
        "elapsed_min": _elapsed_minutes(session_start_utc),
    }


def select_anchor_candidates(
    log_df: pd.DataFrame,
    operator_id: str,
    n: int = 5,
    session_count: int = 0,
) -> List[str]:
    anchorable = list(get_anchorable_set(log_df, operator_id))
    if not anchorable:
        return []
    rng = random.Random(42 + session_count)
    return rng.sample(anchorable, min(n, len(anchorable)))


def build_row(
    operator_id: str,
    session_id: str,
    review_visit_id: str,
    study_row: pd.Series,
    metrics: Dict,
    quality_label: str,
    quality_rationale: str,
    decision_status: str,
    quality_gate_pass: Optional[bool],
    preliminary_label: Optional[str],
    preliminary_impression: Optional[str],
    center_frame_base: Optional[str],
    representative_frame_index: int,
    representative_frame_reason: str,
    frame_notes: Optional[List[str]],
    is_anchor: bool,
    prior_label: Optional[str],
    review_context: str,
    review_save_index: int,
    study_annotation_index: int,
    session_study_number: int,
    change_reason: Optional[str],
) -> Dict:
    final_label = quality_label if decision_status == "completed" else None
    label_changed = (
        final_label != preliminary_label
        if (final_label and preliminary_label)
        else False
    )
    anchor_agreement = (
        (prior_label == final_label) if (is_anchor and prior_label and final_label) else None
    )
    notes = frame_notes or []
    qmt_cols = metrics.get("qmt_annotation_columns")
    qmt_cols_str = None
    if isinstance(qmt_cols, (list, tuple)):
        qmt_cols_str = "|".join(str(col) for col in qmt_cols)
    qmt_flags = metrics.get("qmt_validity_flags")
    qmt_flags_str = None
    if isinstance(qmt_flags, (list, tuple)):
        qmt_flags_str = "|".join(str(flag) for flag in qmt_flags)
    elif isinstance(qmt_flags, str):
        qmt_flags_str = qmt_flags or None

    return {
        "operator_id": operator_id,
        "saved_at_utc": now_utc_str(),
        "session_id": session_id,
        "review_visit_id": review_visit_id,
        "study_code": str(study_row.get("study_code", "")),
        "decision_status": decision_status,
        "quality_gate_pass": quality_gate_pass,
        "preliminary_label": preliminary_label,
        "preliminary_impression": preliminary_impression,
        "quality_label": final_label,
        "quality_rationale": quality_rationale,
        "label_changed": label_changed,
        "change_reason": change_reason if label_changed else None,
        "center_frame_base": center_frame_base,
        "representative_frame_index": representative_frame_index,
        "representative_frame_reason": representative_frame_reason,
        "rf_thickness_px": metrics.get("rf_thickness_px"),
        "rf_thickness_mm": metrics.get("rf_thickness_mm"),
        "rf_column": metrics.get("rf_column"),
        "vi_thickness_px": metrics.get("vi_thickness_px"),
        "vi_thickness_mm": metrics.get("vi_thickness_mm"),
        "vi_column": metrics.get("vi_column"),
        "qmt_px": metrics.get("qmt_px"),
        "qmt_mm": metrics.get("qmt_mm"),
        "qmt_provenance": metrics.get("qmt_provenance"),
        "qmt_column": metrics.get("qmt_column"),
        "qmt_annotation_columns": qmt_cols_str,
        "qmt_muscle_occupancy_pct": metrics.get("qmt_muscle_occupancy_pct"),
        "qmt_plateau_supported": metrics.get("qmt_plateau_supported"),
        "qmt_central_zone_supported": metrics.get("qmt_central_zone_supported"),
        "qmt_femur_slope_abs": metrics.get("qmt_femur_slope_abs"),
        "qmt_fascia_slope_abs": metrics.get("qmt_fascia_slope_abs"),
        "qmt_validity_flags": qmt_flags_str,
        "qmt_guidance_status": metrics.get("qmt_guidance_status"),
        "qmt_rf_px": metrics.get("qmt_rf_px"),
        "qmt_rf_mm": metrics.get("qmt_rf_mm"),
        "qmt_vi_px": metrics.get("qmt_vi_px"),
        "qmt_vi_mm": metrics.get("qmt_vi_mm"),
        "star_ratio": metrics.get("star_ratio"),
        "rf_echo": metrics.get("rf_echo"),
        "vi_echo": metrics.get("vi_echo"),
        "visible_rf_area_px": metrics.get("visible_rf_area_px"),
        "visible_rf_area_mm2": metrics.get("visible_rf_area_mm2"),
        "vi_area_px": metrics.get("vi_area_px"),
        "vi_area_mm2": metrics.get("vi_area_mm2"),
        "muscle_fat_ratio": metrics.get("muscle_fat_ratio"),
        "frame_rf_consistency_px": metrics.get("frame_rf_consistency_px"),
        "mask_coverage_pct": metrics.get("mask_coverage_pct"),
        "is_anchor_study": is_anchor,
        "prior_label": prior_label if is_anchor else None,
        "anchor_agreement": anchor_agreement,
        "review_context": review_context,
        "review_save_index": review_save_index,
        "study_annotation_index": study_annotation_index,
        "session_study_number": session_study_number,
        "frame_note_1": notes[0] if len(notes) > 0 else None,
        "frame_note_2": notes[1] if len(notes) > 1 else None,
        "frame_note_3": notes[2] if len(notes) > 2 else None,
        "frame_note_4": notes[3] if len(notes) > 3 else None,
        "age": study_row.get("age"),
        "sex": study_row.get("gender"),
        "height_cm": study_row.get("height_cm"),
        "weight_kg": study_row.get("weight_kg"),
        "bmi": study_row.get("bmi"),
        "comorbidities": study_row.get("comorbidities"),
        "other_comorbidities": study_row.get("other_comorbidities"),
        "pixel_spacing_x": study_row.get("pixel_spacing_x"),
        "pixel_spacing_y": study_row.get("pixel_spacing_y"),
        "dcm_rows": study_row.get("dcm_rows"),
        "jpg_height": study_row.get("jpg_height"),
        "jpg_width": study_row.get("jpg_width"),
        "mask_height": study_row.get("mask_height"),
        "mask_width": study_row.get("mask_width"),
    }


def _coerce_int(value: object) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).lower() in {"1", "true", "yes"}


def _elapsed_minutes(session_start_utc: datetime) -> float:
    try:
        now = datetime.now(timezone.utc)
        return round(
            (now - session_start_utc.replace(tzinfo=timezone.utc)).total_seconds() / 60,
            1,
        )
    except Exception:
        return 0.0

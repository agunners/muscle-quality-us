"""Streamlit labeling app entry point."""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import streamlit as st

_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from labeling_app.config import LabelingConfig, load_config, resolve_config_path
from labeling_app.data import build_study_manifest, select_center_frame_details
from labeling_app.measurements import compute_all_metrics
from labeling_app.state import (
    append_row,
    build_row,
    consume_next_anchor,
    get_effective_decisions,
    get_labeled_set,
    load_anchor_queue,
    load_log,
    register_session,
    select_anchor_candidates,
    seed_anchor_queue,
    session_summary,
)
from labeling_app.visualization import (
    draw_qmt_annotations,
    load_image_gray,
    load_image_pil,
    load_label_palette,
    load_mask_pil,
    overlay_on_image,
)

_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "configs" / "labeling.yaml"

_GRADE_DEFS = {
    "PRESERVED": (
        "RF + VI clearly demarcated, homogeneous echo texture, "
        "no visible fat infiltration, fascial planes intact."
    ),
    "INTERMEDIATE": (
        "Partial fat infiltration visible, slightly heterogeneous echo, "
        "tissue boundaries still discernible."
    ),
    "POOR": (
        "Significant fat replacement, loss of tissue homogeneity, "
        "boundaries poorly defined or absent."
    ),
}
_GRADE_OPTIONS = ["PRESERVED", "INTERMEDIATE", "POOR"]
_MIN_RATIONALE_CHARS = 10
_QUEUE_FILTERS = ["Unreviewed", "Completed", "Skipped", "Quality failed", "All"]


@st.cache_resource
def _get_config(config_path: str) -> LabelingConfig:
    return load_config(config_path)


def _get_config_path() -> Path:
    return resolve_config_path(_DEFAULT_CONFIG_PATH)


@st.cache_resource
def _get_palette(_cfg: LabelingConfig) -> dict:
    return load_label_palette(_cfg.palette_path)


@st.cache_data(ttl=60)
def _get_manifest(
    transport_root: str,
    meta_csv_path: str,
    study_code_map_path: str,
) -> pd.DataFrame:
    return build_study_manifest(meta_csv_path, transport_root, study_code_map_path)


def _reload_log(cfg: LabelingConfig) -> pd.DataFrame:
    return load_log(cfg.runtime_db_path)


def _new_session_id() -> str:
    return uuid.uuid4().hex


def _new_review_visit_id() -> str:
    return uuid.uuid4().hex


def _init_session(cfg: LabelingConfig) -> None:
    if "initialized" in st.session_state:
        return
    st.session_state["initialized"] = True
    st.session_state["operator_id"] = ""
    st.session_state["session_id"] = _new_session_id()
    st.session_state["session_start"] = datetime.now(timezone.utc)
    st.session_state["phase"] = 1
    st.session_state["prelim_label"] = None
    st.session_state["prelim_impression"] = ""
    st.session_state["metrics_cache"] = {}
    st.session_state["queue_filter"] = "Unreviewed"
    st.session_state["current_study_code"] = None
    st.session_state["is_anchor_study"] = False
    st.session_state["prior_label_for_anchor"] = None
    st.session_state["review_visit_token"] = 0
    st.session_state["review_visit_id"] = _new_review_visit_id()
    st.session_state["review_visit_key"] = None
    st.session_state["review_context"] = None
    st.session_state["review_save_count"] = 0
    register_session(
        cfg.runtime_db_path,
        st.session_state["session_id"],
        "",
        st.session_state["session_start"].isoformat(),
    )


def _reset_operator_session(cfg: LabelingConfig, operator_id: str) -> None:
    st.session_state["operator_id"] = operator_id
    st.session_state["session_id"] = _new_session_id()
    st.session_state["session_start"] = datetime.now(timezone.utc)
    st.session_state["phase"] = 1
    st.session_state["prelim_label"] = None
    st.session_state["prelim_impression"] = ""
    st.session_state["current_study_code"] = None
    st.session_state["is_anchor_study"] = False
    st.session_state["prior_label_for_anchor"] = None
    st.session_state["review_visit_token"] = 0
    st.session_state["review_visit_id"] = _new_review_visit_id()
    st.session_state["review_visit_key"] = None
    st.session_state["review_context"] = None
    st.session_state["review_save_count"] = 0
    st.session_state["metrics_cache"] = {}
    register_session(
        cfg.runtime_db_path,
        st.session_state["session_id"],
        operator_id,
        st.session_state["session_start"].isoformat(),
    )


def _text_meets_min_chars(text: Optional[str], min_chars: int = _MIN_RATIONALE_CHARS) -> bool:
    return len((text or "").strip()) >= min_chars


def _phase1_submit_disabled(prelim_grade: str, prelim_text: Optional[str]) -> bool:
    return prelim_grade == "(not set)" or not _text_meets_min_chars(prelim_text)


def _phase2_save_disabled(
    prelim_label: Optional[str],
    final_grade: str,
    rationale: Optional[str],
    change_reason: Optional[str],
) -> bool:
    if not _text_meets_min_chars(rationale):
        return True
    if prelim_label and final_grade != prelim_label and not _text_meets_min_chars(change_reason):
        return True
    return False


def _derive_review_context(is_anchor: bool, prior_row: Optional[pd.Series]) -> str:
    if is_anchor:
        return "anchor"
    return "revisit" if prior_row is not None else "fresh"


def _next_study_annotation_index(log_df: pd.DataFrame, operator_id: str, study_code: str) -> int:
    if log_df.empty:
        return 1
    study_key_col = "study_code" if "study_code" in log_df.columns else "study_dirname"
    mask = (
        (log_df["operator_id"].astype(str) == str(operator_id))
        & (log_df[study_key_col].astype(str) == str(study_code))
    )
    return int(mask.sum()) + 1


def _can_return_to_phase1(review_save_count: int) -> bool:
    return review_save_count >= 1


def _set_current_study(
    study_code: str,
    *,
    is_anchor: bool = False,
    prior_label: Optional[str] = None,
) -> None:
    st.session_state["current_study_code"] = study_code
    st.session_state["current_study_dirname"] = study_code
    st.session_state["phase"] = 1
    st.session_state["prelim_label"] = None
    st.session_state["prelim_impression"] = ""
    st.session_state["is_anchor_study"] = is_anchor
    st.session_state["prior_label_for_anchor"] = prior_label
    st.session_state["review_visit_token"] = st.session_state.get("review_visit_token", 0) + 1
    st.session_state["review_visit_id"] = _new_review_visit_id()
    st.session_state["review_visit_key"] = None
    st.session_state["review_context"] = None
    st.session_state["review_save_count"] = 0


def _ensure_review_visit_state(operator_id: str, study_code: str, effective: Dict) -> None:
    visit_key = (
        operator_id,
        study_code,
        st.session_state.get("review_visit_id"),
    )
    if st.session_state.get("review_visit_key") == visit_key:
        return
    prior_row = effective.get((operator_id, study_code))
    is_anchor = bool(st.session_state.get("is_anchor_study", False))
    st.session_state["review_visit_key"] = visit_key
    st.session_state["review_context"] = _derive_review_context(is_anchor, prior_row)
    st.session_state["review_save_count"] = 0


def _fmt(val: object, unit: str = "", decimals: int = 2) -> str:
    if val is None or val == "":
        return "n/a"
    try:
        return f"{float(val):.{decimals}f}{unit}"
    except (TypeError, ValueError):
        return str(val)


def _queue_empty_message(filter_mode: str) -> str:
    messages = {
        "Unreviewed": "No unreviewed studies remain in the current queue.",
        "Completed": "No completed studies are available for this operator yet.",
        "Skipped": "No skipped studies are available for this operator yet.",
        "Quality failed": "No quality-failed studies are available for this operator yet.",
        "All": "No studies match the current filter.",
    }
    return messages.get(filter_mode, "No studies match the current filter.")


def _qmt_provenance_badge(provenance: str) -> str:
    badges = {
        "standard_qmt": ("Standard QMT", "#e7f3ec", "#155b2b"),
        "femur_supported_proxy": ("Femur-Supported Proxy", "#fff2d9", "#8a4b08"),
        "no_femur_proxy": ("No-Femur Proxy", "#fbe8d6", "#8a3c00"),
        "no_measurement": ("No Measurement", "#eceff3", "#475467"),
    }
    label, bg, fg = badges.get(provenance, (provenance or "Unknown", "#eceff3", "#475467"))
    return (
        "<span style='display:inline-block;padding:0.18rem 0.65rem;"
        f"border-radius:999px;background:{bg};color:{fg};font-weight:600;'>{label}</span>"
    )


def _qmt_flags(metrics: Dict) -> List[str]:
    flags = metrics.get("qmt_validity_flags") or []
    if isinstance(flags, str):
        return [flag for flag in flags.split("|") if flag]
    if isinstance(flags, (list, tuple)):
        return [str(flag) for flag in flags if str(flag)]
    return []


def _qmt_guidance_text(metrics: Dict) -> str:
    status = str(metrics.get("qmt_guidance_status") or "no_measurement")
    flags = _qmt_flags(metrics)
    primary_flag = flags[0] if flags else None

    if status == "good_standard_window":
        return "Guidance: good central standard-QMT window."
    if status == "proxy_due_to_missing_femur":
        return "Guidance: femur absent from field of view; using central RF+VI proxy."
    if status == "proxy_due_to_geometry":
        messages = {
            "outside_central_zone": "Guidance: central femur zone not captured; proxy used instead of standard QMT.",
            "femur_slope_high": "Guidance: femur contour is too sloped at the measurement region; proxy used instead.",
            "fascia_slope_high": "Guidance: upper fascia is too oblique for a valid central vertical QMT.",
            "low_muscle_occupancy": "Guidance: RF/VI occupancy is too low along the vertical path; proxy used instead.",
            "rf_or_vi_missing_in_path": "Guidance: RF and VI are not both well represented along the path; proxy used instead.",
        }
        return messages.get(primary_flag or "", "Guidance: proxy used because the central QMT geometry is not reliable.")
    return "Guidance: no measurable QMT or RF+VI proxy was found."


def _qmt_display_names(provenance: str) -> tuple[str, str, str]:
    prov_label = {
        "standard_qmt": "standard",
        "femur_supported_proxy": "femur proxy",
        "no_femur_proxy": "no-femur proxy",
        "no_measurement": "none",
    }.get(provenance, provenance)
    is_proxy = provenance in {"femur_supported_proxy", "no_femur_proxy"}
    qmt_name = "QMT* (px)" if is_proxy else "QMT (px)"
    qmt_mm_name = "QMT* (mm) [proxy]" if is_proxy else f"QMT (mm) [{prov_label}]"
    star_name = "STAR* ratio" if is_proxy else "STAR ratio"
    return qmt_name, qmt_mm_name, star_name


def _select_study_from_manifest(
    manifest: pd.DataFrame,
    effective: Dict,
    operator_id: str,
    filter_mode: str,
) -> List[str]:
    study_key_col = "study_code" if "study_code" in manifest.columns else "study_dirname"
    all_codes = manifest[study_key_col].tolist()
    statuses = {
        code: str(row.get("decision_status", ""))
        for (op, code), row in effective.items()
        if op == operator_id
    }
    if filter_mode == "All":
        return all_codes
    if filter_mode == "Unreviewed":
        return [code for code in all_codes if statuses.get(code) not in {"completed", "skipped", "quality_failed"}]
    if filter_mode == "Completed":
        return [code for code in all_codes if statuses.get(code) == "completed"]
    if filter_mode == "Skipped":
        return [code for code in all_codes if statuses.get(code) == "skipped"]
    if filter_mode == "Quality failed":
        return [code for code in all_codes if statuses.get(code) == "quality_failed"]
    return all_codes


def _load_mask_for_frame(path: Optional[str]) -> Optional[object]:
    if path and Path(path).is_file():
        return load_mask_pil(path)
    return None


def _representative_frame_caption(reason: str, index: int) -> str:
    labels = {
        "temporal_center_standard_qmt": "temporal center with strict QMT support",
        "nearest_standard_qmt": "nearest non-center frame with strict QMT support",
        "temporal_center_fallback": "temporal center fallback (no strict-QMT frame available)",
    }
    return f"Representative frame: index {index} ({labels.get(reason, reason)})"


def _ordered_channels(ch_dict: Dict[str, Optional[str]]) -> List[str]:
    preferred = ["raw", "beforeDSC", "final"]
    available = [k for k, v in ch_dict.items() if v and Path(v).is_file()]
    preferred_present = [key for key in preferred if key in available]
    remaining = [key for key in available if key not in preferred_present]
    return preferred_present + remaining


def _show_video(video_path: Optional[str]) -> None:
    if not video_path or not Path(video_path).is_file():
        st.info("No video available for this study. Frame-based review only.")
        return
    try:
        st.video(str(video_path), loop=True, autoplay=True, muted=True)
    except TypeError:
        try:
            st.video(str(video_path))
        except Exception:
            video_bytes = Path(video_path).read_bytes()
            import base64

            b64 = base64.b64encode(video_bytes).decode()
            st.components.v1.html(
                f'<video autoplay loop muted controls style="width:100%; max-height:400px;">'
                f'<source src="data:video/mp4;base64,{b64}" type="video/mp4"></video>',
                height=420,
            )


def _show_study_identity(study_row: pd.Series) -> None:
    st.markdown(f"**Study Code:** `{study_row['study_code']}`")


def _show_subject_metadata(study_row: pd.Series) -> None:
    st.subheader("Phase 2 Subject Context")
    cols = st.columns(5)
    cols[0].metric("Age", _fmt(study_row.get("age"), decimals=0))
    cols[1].metric("Sex", str(study_row.get("gender") or "—"))
    cols[2].metric("Height (cm)", _fmt(study_row.get("height_cm"), decimals=0))
    cols[3].metric("Weight (kg)", _fmt(study_row.get("weight_kg"), decimals=0))
    cols[4].metric("BMI", _fmt(study_row.get("bmi")))
    extras: List[str] = [f"Frames: {study_row.get('frame_count', '?')}"]
    if study_row.get("comorbidities"):
        extras.append(f"Comorbidities: {study_row.get('comorbidities')}")
    if study_row.get("other_comorbidities"):
        extras.append(f"Other comorbidities: {study_row.get('other_comorbidities')}")
    st.caption(" | ".join(extras))


def _show_frame_panel(
    study_row: pd.Series,
    center_idx: int,
    palette: dict,
    cfg: LabelingConfig,
    phase: int,
    metrics: Dict,
) -> List[Optional[str]]:
    frame_count = int(study_row["frame_count"])
    frame_bases = study_row["frame_bases"]
    channel_paths = study_row["channel_paths"]
    mask_paths = study_row["mask_paths"]
    cols = st.columns(frame_count)
    notes: List[Optional[str]] = [None] * frame_count

    frame_labels = []
    for i in range(frame_count):
        if i < center_idx:
            frame_labels.append("Lateral")
        elif i == center_idx:
            frame_labels.append("Center ★")
        else:
            frame_labels.append("Medial")

    for i, col in enumerate(cols):
        with col:
            st.markdown(f"**{frame_labels[i]}**")
            ch_dict = channel_paths[i] if i < len(channel_paths) else {}
            available_channels = _ordered_channels(ch_dict)
            default_channel = "final" if "final" in available_channels else (available_channels[0] if available_channels else None)
            ch_key = f"channel_{study_row['study_code']}_{i}"
            if ch_key not in st.session_state:
                st.session_state[ch_key] = default_channel

            if available_channels:
                selected_channel = st.selectbox(
                    "Variant",
                    options=available_channels,
                    key=ch_key,
                    label_visibility="collapsed",
                )
                base_path = ch_dict.get(selected_channel)
            else:
                base_path = None
                st.caption("No images available")

            base_img = load_image_pil(base_path)
            if base_img is not None:
                st.image(base_img, width="stretch")
            else:
                st.caption("Image missing")

            mask = _load_mask_for_frame(mask_paths[i] if i < len(mask_paths) else None)
            if mask is not None and base_img is not None and palette:
                if i == center_idx and phase == 2:
                    annotated = overlay_on_image(base_img, mask, palette, cfg.overlay_alpha)
                    annotated = draw_qmt_annotations(annotated, mask, metrics)
                    st.image(annotated, width="stretch", caption="Annotated overlay")
                else:
                    overlay = overlay_on_image(base_img, mask, palette, cfg.overlay_alpha)
                    st.image(overlay, width="stretch", caption="Mask overlay")
            elif mask is None:
                st.caption("Mask missing")

            note_key = f"note_{study_row['study_code']}_{i}"
            with st.expander("Frame note (optional)"):
                note = st.text_input("Note", key=note_key, label_visibility="collapsed")
                notes[i] = note.strip() or None

    return notes


def _show_metrics_panel(metrics: Dict, has_spacing: bool, frame_note: str) -> None:
    st.subheader("Center-Frame Metrics")
    st.caption(frame_note)
    prov = str(metrics.get("qmt_provenance", "no_measurement"))
    st.markdown(_qmt_provenance_badge(prov), unsafe_allow_html=True)
    st.caption(_qmt_guidance_text(metrics))
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("RF thickness — peak (px)", _fmt(metrics.get("rf_thickness_px"), decimals=0))
        st.metric("RF thickness — peak (mm)", _fmt(metrics.get("rf_thickness_mm")) if has_spacing else "n/a")
        st.metric("RF echo intensity", _fmt(metrics.get("rf_echo")))
        st.metric("Visible RF area (mm²)", _fmt(metrics.get("visible_rf_area_mm2")) if has_spacing else "n/a")
    with col2:
        st.metric("VI thickness (px)", _fmt(metrics.get("vi_thickness_px"), decimals=0))
        st.metric("VI thickness (mm)", _fmt(metrics.get("vi_thickness_mm")) if has_spacing else "n/a")
        st.metric("VI echo intensity", _fmt(metrics.get("vi_echo")))
        st.metric("VI area (mm²)", _fmt(metrics.get("vi_area_mm2")) if has_spacing else "n/a")
    with col3:
        qmt_name, qmt_mm_name, star_name = _qmt_display_names(prov)
        st.metric(qmt_name, _fmt(metrics.get("qmt_px"), decimals=0))
        st.metric(qmt_mm_name, _fmt(metrics.get("qmt_mm")) if has_spacing else "n/a")
        if has_spacing and metrics.get("qmt_rf_mm") is not None and metrics.get("qmt_vi_mm") is not None:
            st.caption(f"RF: {_fmt(metrics.get('qmt_rf_mm'))} mm | VI: {_fmt(metrics.get('qmt_vi_mm'))} mm")
        st.metric(star_name, _fmt(metrics.get("star_ratio")) if metrics.get("star_ratio") is not None else "—")
        st.metric("Muscle/Fat ratio", _fmt(metrics.get("muscle_fat_ratio")))
        occupancy_text = _fmt(metrics.get("qmt_muscle_occupancy_pct"), unit="%")
        st.caption(
            "Occupancy: "
            f"{occupancy_text} | "
            f"Femur slope: {_fmt(metrics.get('qmt_femur_slope_abs'), decimals=3)} | "
            f"Fascia slope: {_fmt(metrics.get('qmt_fascia_slope_abs'), decimals=3)}"
        )
    st.caption(
        f"QMT provenance: {metrics.get('qmt_provenance', 'n/a')} | "
        f"Frame RF consistency σ: {_fmt(metrics.get('frame_rf_consistency_px'))} px | "
        f"Mask coverage: {_fmt(metrics.get('mask_coverage_pct'))} %"
    )


def _get_cached_metrics(study_row: pd.Series, cfg: LabelingConfig) -> Dict:
    cache = st.session_state.get("metrics_cache", {})
    study_code = str(study_row["study_code"])
    if study_code in cache:
        return cache[study_code]

    center_idx, center_reason = select_center_frame_details(study_row)
    mask_paths = study_row["mask_paths"]
    final_paths = study_row["final_paths"]
    center_mask = _load_mask_for_frame(mask_paths[center_idx] if center_idx < len(mask_paths) else None)
    center_img_gray = load_image_gray(final_paths[center_idx] if center_idx < len(final_paths) else None)
    all_masks = [_load_mask_for_frame(mask_path) for mask_path in mask_paths]
    all_masks = [mask for mask in all_masks if mask is not None]

    center_frame_base = study_row["frame_bases"][center_idx] if center_idx < len(study_row["frame_bases"]) else None
    metrics: Dict = {}
    if center_mask is not None:
        spacing = {
            "pixel_spacing_x": study_row.get("pixel_spacing_x"),
            "pixel_spacing_y": study_row.get("pixel_spacing_y"),
            "dcm_rows": study_row.get("dcm_rows"),
            "jpg_height": study_row.get("jpg_height"),
            "jpg_width": study_row.get("jpg_width"),
            "mask_height": study_row.get("mask_height"),
            "mask_width": study_row.get("mask_width"),
            "bmi": study_row.get("bmi"),
        }
        metrics = compute_all_metrics(center_mask, center_img_gray, all_masks, spacing)

    cache[study_code] = {
        "metrics": metrics,
        "center_idx": center_idx,
        "center_frame_base": center_frame_base,
        "center_reason": center_reason,
    }
    st.session_state["metrics_cache"] = cache
    return cache[study_code]


def _do_save(
    cfg: LabelingConfig,
    operator_id: str,
    session_id: str,
    review_visit_id: str,
    study_row: pd.Series,
    metrics: Dict,
    *,
    quality_label: str,
    quality_rationale: str,
    decision_status: str,
    quality_gate_pass: Optional[bool],
    preliminary_label: Optional[str],
    preliminary_impression: Optional[str],
    center_frame_base: Optional[str],
    representative_frame_index: int,
    representative_frame_reason: str,
    frame_notes: List[Optional[str]],
    is_anchor: bool,
    prior_label: Optional[str],
    session_study_number: int,
    change_reason: Optional[str] = None,
) -> None:
    log_df = _reload_log(cfg)
    study_annotation_index = _next_study_annotation_index(log_df, operator_id, str(study_row.get("study_code", "")))
    review_save_index = st.session_state.get("review_save_count", 0) + 1
    review_context = st.session_state.get("review_context") or ("anchor" if is_anchor else "fresh")
    row = build_row(
        operator_id=operator_id,
        session_id=session_id,
        review_visit_id=review_visit_id,
        study_row=study_row,
        metrics=metrics,
        quality_label=quality_label,
        quality_rationale=quality_rationale,
        decision_status=decision_status,
        quality_gate_pass=quality_gate_pass,
        preliminary_label=preliminary_label,
        preliminary_impression=preliminary_impression,
        center_frame_base=center_frame_base,
        representative_frame_index=representative_frame_index,
        representative_frame_reason=representative_frame_reason,
        frame_notes=frame_notes,
        is_anchor=is_anchor,
        prior_label=prior_label,
        review_context=review_context,
        review_save_index=review_save_index,
        study_annotation_index=study_annotation_index,
        session_study_number=session_study_number,
        change_reason=change_reason,
    )
    append_row(cfg.runtime_db_path, row, cfg.decision_export_path)
    st.session_state["review_save_count"] = review_save_index
    st.success(f"Saved: {study_row['study_code']} → {decision_status}")


def _post_save_anchor_toast(is_anchor: bool, prior_label: Optional[str], current_label: str) -> None:
    if is_anchor and prior_label:
        match = prior_label == current_label
        icon = "✅" if match else "⚠️"
        st.toast(
            f"{icon} Calibration study: your previous label was {prior_label}, "
            f"you labeled {current_label}.",
            icon=icon,
        )


def _advance_after_save(study_list: List[str], current_study_code: str) -> None:
    if current_study_code in study_list:
        idx = study_list.index(current_study_code)
        next_idx = min(idx + 1, len(study_list) - 1)
    else:
        next_idx = 0
    _set_current_study(study_list[next_idx])
    st.rerun()


def main() -> None:
    st.set_page_config(
        page_title="Muscle Quality Labeler",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    try:
        config_path = _get_config_path()
        cfg = _get_config(str(config_path))
    except Exception as exc:
        st.error(f"Failed to load config from {config_path}:\n\n{exc}")
        st.stop()

    manifest = _get_manifest(cfg.transport_root, cfg.meta_csv_path, cfg.study_code_map_path)
    palette = _get_palette(cfg)
    _init_session(cfg)
    log_df = _reload_log(cfg)
    effective = get_effective_decisions(log_df)

    operator_id = str(st.session_state.get("operator_id", ""))
    session_id = str(st.session_state.get("session_id", ""))
    session_start = st.session_state.get("session_start", datetime.now(timezone.utc))

    with st.sidebar:
        st.title("Muscle Quality Labeler")
        new_operator_id = st.text_input(
            "Operator ID",
            value=operator_id,
            placeholder="e.g. dr_smith",
        ).strip()
        if new_operator_id != operator_id:
            _reset_operator_session(cfg, new_operator_id)
            st.rerun()

        operator_id = str(st.session_state.get("operator_id", ""))
        session_id = str(st.session_state.get("session_id", ""))
        session_start = st.session_state.get("session_start", datetime.now(timezone.utc))

        st.divider()
        if "queue_filter" not in st.session_state:
            st.session_state["queue_filter"] = "Unreviewed"
        filter_mode = st.selectbox("Queue filter", options=_QUEUE_FILTERS, key="queue_filter")

        if operator_id:
            study_list = _select_study_from_manifest(manifest, effective, operator_id, filter_mode)
        else:
            study_list = manifest["study_code"].tolist()

        if not study_list:
            st.info(_queue_empty_message(filter_mode))
            current_code = None
            current_idx = 0
        else:
            current_code = st.session_state.get("current_study_code")
            if current_code not in study_list:
                current_code = study_list[0]
                _set_current_study(current_code)
            current_idx = study_list.index(current_code)

        if study_list:
            selected = st.selectbox("Study", options=study_list, index=current_idx)
            if selected != st.session_state.get("current_study_code"):
                _set_current_study(selected)
                st.rerun()
        else:
            st.selectbox("Study", options=["(none)"], index=0, disabled=True)

        col_prev, col_next = st.columns(2)
        with col_prev:
            if st.button("◀ Prev", width="stretch", disabled=not study_list):
                new_idx = max(0, current_idx - 1)
                _set_current_study(study_list[new_idx])
                st.rerun()
        with col_next:
            if st.button("Next ▶", width="stretch", disabled=not study_list):
                new_idx = min(len(study_list) - 1, current_idx + 1)
                _set_current_study(study_list[new_idx])
                st.rerun()

        labeled_set = get_labeled_set(log_df, operator_id) if operator_id else set()
        total = len(manifest)
        done = len(labeled_set)
        st.progress(done / total if total > 0 else 0)
        st.caption(f"Progress: {done}/{total}")

        summary = session_summary(log_df, operator_id, session_id, session_start)
        sess_count = summary["count"]
        elapsed = summary["elapsed_min"]
        st.caption(
            f"Session: {sess_count} reviewed | warning at {cfg.session_warning_threshold} | ⏱ {elapsed:.0f} min"
        )
        if operator_id and sess_count >= cfg.session_warning_threshold:
            st.warning(
                f"You have reviewed {sess_count} studies this session. Consider taking a short break.",
                icon="⏱",
            )

        st.divider()
        st.markdown("**Grade Definitions**")
        for grade, definition in _GRADE_DEFS.items():
            with st.expander(grade, expanded=True):
                st.caption(definition)

        st.divider()
        export_path = Path(cfg.decision_export_path)
        if export_path.is_file():
            st.download_button(
                "⬇ Download decision_log.csv",
                data=export_path.read_bytes(),
                file_name="decision_log.csv",
                mime="text/csv",
            )

    if not operator_id:
        st.info("Enter your Operator ID in the sidebar to begin.")
        return

    if not study_list:
        if filter_mode == "Unreviewed":
            st.success("All studies in the unreviewed queue are cleared for this operator.")
        else:
            st.info(_queue_empty_message(filter_mode))
        return

    current_code = st.session_state.get("current_study_code") or study_list[0]
    study_matches = manifest[manifest["study_code"] == current_code]
    if study_matches.empty:
        st.error(f"Study not found in manifest: {current_code}")
        return
    study_row = study_matches.iloc[0]
    cached = _get_cached_metrics(study_row, cfg)
    metrics = cached["metrics"]
    center_idx = cached["center_idx"]
    center_frame_base = cached["center_frame_base"]
    center_reason = cached["center_reason"]

    summary = session_summary(log_df, operator_id, session_id, session_start)
    if int(st.session_state.get("review_save_count", 0)) > 0:
        session_study_number = summary["count"]
    else:
        session_study_number = summary["count"] + 1

    if summary["count"] > 0 and summary["count"] % cfg.anchor_interval == 0 and not st.session_state.get("is_anchor_study", False):
        queue = load_anchor_queue(cfg.runtime_db_path, session_id, operator_id)
        if not queue:
            anchor_candidates = select_anchor_candidates(
                log_df,
                operator_id,
                n=5,
                session_count=summary["count"],
            )
            seed_anchor_queue(
                cfg.runtime_db_path,
                session_id,
                operator_id,
                anchor_candidates,
                seed=42 + summary["count"],
            )
            queue = load_anchor_queue(cfg.runtime_db_path, session_id, operator_id)
        if queue and current_code not in set(queue):
            anchor_code = consume_next_anchor(cfg.runtime_db_path, session_id, operator_id)
            prior_row = effective.get((operator_id, anchor_code)) if anchor_code else None
            prior_label = str(prior_row.get("quality_label", "")) if prior_row is not None else None
            if anchor_code:
                _set_current_study(anchor_code, is_anchor=True, prior_label=prior_label)
                st.rerun()

    _ensure_review_visit_state(operator_id, current_code, effective)
    review_visit_id = str(st.session_state.get("review_visit_id"))
    phase = int(st.session_state.get("phase", 1))

    st.subheader("Quality Gate")
    _show_study_identity(study_row)
    qg_key = f"quality_gate_{current_code}"
    if qg_key not in st.session_state:
        prior = effective.get((operator_id, current_code))
        prior_gate = str(prior.get("quality_gate_pass", "")) if prior is not None else ""
        st.session_state[qg_key] = "FAIL" if prior_gate.lower() == "false" else "PASS"

    quality_choice = st.radio(
        "Image quality",
        options=["PASS", "FAIL"],
        key=qg_key,
        horizontal=True,
    )
    quality_gate_pass = quality_choice == "PASS"
    st.divider()

    st.subheader("Video")
    _show_video(str(study_row.get("video_path")) if study_row.get("video_path") else None)
    st.divider()

    st.subheader("Frames")
    frame_notes = _show_frame_panel(study_row, center_idx, palette, cfg, phase, metrics)
    st.divider()

    if not quality_gate_pass:
        st.warning(
            "Quality FAIL excludes this study from scoring. Record the exclusion or move to another study.",
            icon="⚠️",
        )
        if st.button("Record quality fail", key=f"quality_fail_{current_code}"):
            _do_save(
                cfg,
                operator_id,
                session_id,
                review_visit_id,
                study_row,
                {},
                quality_label="",
                quality_rationale="Quality gate failed",
                decision_status="quality_failed",
                quality_gate_pass=False,
                preliminary_label=None,
                preliminary_impression=None,
                center_frame_base=center_frame_base,
                representative_frame_index=center_idx,
                representative_frame_reason=center_reason,
                frame_notes=frame_notes,
                is_anchor=bool(st.session_state.get("is_anchor_study", False)),
                prior_label=st.session_state.get("prior_label_for_anchor"),
                session_study_number=session_study_number,
            )
            if study_list:
                _advance_after_save(study_list, current_code)
        return

    if cfg.phase_lock and phase == 1:
        st.subheader("Phase 1: Visual Assessment")
        st.caption(
            "Study code, video, frames, and overlays are visible. Subject metadata and metrics remain hidden until submission."
        )
        p1_grade_key = f"p1_grade_{review_visit_id}"
        p1_text_key = f"p1_text_{review_visit_id}"
        if p1_grade_key not in st.session_state:
            st.session_state[p1_grade_key] = st.session_state.get("prelim_label") or "(not set)"
        if p1_text_key not in st.session_state:
            st.session_state[p1_text_key] = st.session_state.get("prelim_impression") or ""

        p1_grade = st.radio(
            "Preliminary grade",
            options=["(not set)"] + _GRADE_OPTIONS,
            horizontal=True,
            key=p1_grade_key,
        )
        p1_impression = st.text_area(
            f"Phase 1 rationale (visual only, >= {_MIN_RATIONALE_CHARS} characters required)",
            height=68,
            placeholder="Briefly describe what you see.",
            key=p1_text_key,
        )
        if st.button(
            "Submit Phase 1 →",
            disabled=_phase1_submit_disabled(p1_grade, p1_impression),
            key=f"submit_phase1_{review_visit_id}",
        ):
            st.session_state["prelim_label"] = p1_grade
            st.session_state["prelim_impression"] = p1_impression.strip()
            st.session_state["phase"] = 2
            st.rerun()

        if st.button("Skip this study", key=f"skip_phase1_{review_visit_id}"):
            _do_save(
                cfg,
                operator_id,
                session_id,
                review_visit_id,
                study_row,
                {},
                quality_label="",
                quality_rationale="Skipped",
                decision_status="skipped",
                quality_gate_pass=True,
                preliminary_label=None,
                preliminary_impression=None,
                center_frame_base=center_frame_base,
                representative_frame_index=center_idx,
                representative_frame_reason=center_reason,
                frame_notes=frame_notes,
                is_anchor=bool(st.session_state.get("is_anchor_study", False)),
                prior_label=st.session_state.get("prior_label_for_anchor"),
                session_study_number=session_study_number,
            )
            _advance_after_save(study_list, current_code)
        return

    if not cfg.phase_lock:
        st.session_state["phase"] = 2
        phase = 2

    if phase == 2:
        _show_subject_metadata(study_row)
        st.divider()
        has_spacing = bool(study_row.get("has_pixel_spacing"))
        _show_metrics_panel(
            metrics,
            has_spacing,
            _representative_frame_caption(center_reason, center_idx),
        )
        st.divider()

        st.subheader("Final Decision")
        prelim_label = st.session_state.get("prelim_label")
        if prelim_label not in _GRADE_OPTIONS:
            prelim_label = None

        final_grade_key = f"final_grade_{review_visit_id}"
        rationale_key = f"phase2_rationale_{review_visit_id}"
        change_reason_key = f"change_reason_{review_visit_id}"
        if final_grade_key not in st.session_state:
            st.session_state[final_grade_key] = prelim_label or _GRADE_OPTIONS[0]
        if rationale_key not in st.session_state:
            st.session_state[rationale_key] = ""
        if change_reason_key not in st.session_state:
            st.session_state[change_reason_key] = ""

        final_grade = st.radio("Final grade", options=_GRADE_OPTIONS, horizontal=True, key=final_grade_key)
        rationale = st.text_area(
            f"Rationale (>= {_MIN_RATIONALE_CHARS} characters required)",
            height=100,
            placeholder="Explain your decision, referencing the visual and/or metric evidence.",
            key=rationale_key,
        )

        change_reason = None
        if prelim_label and final_grade != prelim_label:
            change_reason = st.text_input(
                f"Reason for revision (preliminary was {prelim_label}, >= {_MIN_RATIONALE_CHARS} characters)",
                placeholder="Explain why the final decision differs from the preliminary impression.",
                key=change_reason_key,
            )

        save_disabled = _phase2_save_disabled(prelim_label, final_grade, rationale, change_reason)
        col_save, col_next, col_skip = st.columns(3)
        with col_save:
            btn_save = st.button("Save", disabled=save_disabled, key=f"save_{review_visit_id}")
        with col_next:
            btn_save_next = st.button("Save & Next ▶", disabled=save_disabled, key=f"save_next_{review_visit_id}")
        with col_skip:
            btn_skip = st.button("Skip", key=f"skip_phase2_{review_visit_id}")

        if btn_save or btn_save_next:
            _do_save(
                cfg,
                operator_id,
                session_id,
                review_visit_id,
                study_row,
                metrics,
                quality_label=final_grade,
                quality_rationale=rationale.strip(),
                decision_status="completed",
                quality_gate_pass=True,
                preliminary_label=prelim_label,
                preliminary_impression=st.session_state.get("prelim_impression"),
                center_frame_base=center_frame_base,
                representative_frame_index=center_idx,
                representative_frame_reason=center_reason,
                frame_notes=frame_notes,
                is_anchor=bool(st.session_state.get("is_anchor_study", False)),
                prior_label=st.session_state.get("prior_label_for_anchor"),
                session_study_number=session_study_number,
                change_reason=change_reason,
            )
            _post_save_anchor_toast(
                bool(st.session_state.get("is_anchor_study", False)),
                st.session_state.get("prior_label_for_anchor"),
                final_grade,
            )
            if btn_save_next:
                _advance_after_save(study_list, current_code)

        if btn_skip:
            _do_save(
                cfg,
                operator_id,
                session_id,
                review_visit_id,
                study_row,
                {},
                quality_label="",
                quality_rationale="Skipped",
                decision_status="skipped",
                quality_gate_pass=True,
                preliminary_label=prelim_label,
                preliminary_impression=st.session_state.get("prelim_impression"),
                center_frame_base=center_frame_base,
                representative_frame_index=center_idx,
                representative_frame_reason=center_reason,
                frame_notes=frame_notes,
                is_anchor=bool(st.session_state.get("is_anchor_study", False)),
                prior_label=st.session_state.get("prior_label_for_anchor"),
                session_study_number=session_study_number,
            )
            _post_save_anchor_toast(
                bool(st.session_state.get("is_anchor_study", False)),
                st.session_state.get("prior_label_for_anchor"),
                "skipped",
            )
            _advance_after_save(study_list, current_code)

        if cfg.phase_lock:
            if st.button(
                "← Back to Phase 1 (visual re-assessment)",
                disabled=not _can_return_to_phase1(st.session_state.get("review_save_count", 0)),
            ):
                st.session_state["phase"] = 1
                st.rerun()


if __name__ == "__main__":
    main()

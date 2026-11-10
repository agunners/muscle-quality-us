"""Data loading, study-code mapping, and demographics parsing."""
from __future__ import annotations

import ast
import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from PIL import Image

STUDY_CODE_COLUMNS = [
    "study_code",
    "study_dirname",
    "numeric_study_id",
    "annotator",
    "session",
    "relative_frame_dir",
    "created_at_utc",
]

# ---------------------------------------------------------------------------
# Demographics parser
# ---------------------------------------------------------------------------

_COMORBIDITY_TOKENS = {"ht", "dm", "ht+dm"}
_GENDER_MAP = {"k": "F", "e": "M", "y": "unknown"}
_KNOWN_COMORBIDITY_TOKENS = {"ht", "dm"}
_WEIGHT_TOKEN_RE = re.compile(r"(\d+)(kg)?", re.IGNORECASE)
_HEIGHT_TOKEN_RE = re.compile(r"\d+")
_FUSED_AGE_GENDER_HEIGHT_RE = re.compile(r"(\d+)([key])(\d+)", re.IGNORECASE)
_AGE_GENDER_TOKEN_RE = re.compile(r"(\d+)([key])", re.IGNORECASE)
_AGE_ONLY_TOKEN_RE = re.compile(r"(\d+)(?:y)?", re.IGNORECASE)
_STANDALONE_GENDER_TOKEN_RE = re.compile(r"[key]", re.IGNORECASE)


def _canonicalize_comorbidities(tokens: List[str]) -> Optional[str]:
    """Normalize known structured comorbidity tokens to ht | dm | ht+dm."""
    seen: List[str] = []
    for token in tokens:
        tl = token.lower()
        if tl == "ht+dm":
            for part in ("ht", "dm"):
                if part not in seen:
                    seen.append(part)
        elif tl in _KNOWN_COMORBIDITY_TOKENS and tl not in seen:
            seen.append(tl)
    if "ht" in seen and "dm" in seen:
        return "ht+dm"
    if "ht" in seen:
        return "ht"
    if "dm" in seen:
        return "dm"
    return None


def _extract_age_gender(tokens: List[str]) -> Tuple[Optional[int], Optional[str], List[str]]:
    """Parse age/gender from the right edge of tokens, if present."""
    if not tokens:
        return None, None, tokens

    if len(tokens) >= 2 and _STANDALONE_GENDER_TOKEN_RE.fullmatch(tokens[-1]):
        age_match = _AGE_ONLY_TOKEN_RE.fullmatch(tokens[-2])
        if age_match:
            age = int(age_match.group(1))
            gender = _GENDER_MAP.get(tokens[-1].lower(), "unknown")
            return age, gender, tokens[:-2]

    age_gender = _AGE_GENDER_TOKEN_RE.fullmatch(tokens[-1])
    if age_gender:
        age = int(age_gender.group(1))
        gender = _GENDER_MAP.get(age_gender.group(2).lower(), "unknown")
        return age, gender, tokens[:-1]

    age_only = _AGE_ONLY_TOKEN_RE.fullmatch(tokens[-1])
    if age_only:
        age = int(age_only.group(1))
        return age, "unknown", tokens[:-1]

    return None, None, tokens


def parse_demographics(study_dirname: str) -> Dict[str, Any]:
    """Parse study_dirname into a demographics dict.

    Uses a staged fallback parser that tolerates:
    - dual comorbidity tokens such as ht_dm / dm_ht
    - trailing unknown suffix tokens after weight
    - bare numeric age
    - split age/gender tokens (25y_e)
    - fused age+gender+height tokens (23k160)
    - weight tokens with kg suffix
    - doubled or trailing underscores

    Returns dict with keys:
        id, last_name, first_name, age, gender,
        height_cm, weight_kg, bmi, comorbidities, other_comorbidities, parse_ok
    """
    result: Dict[str, Any] = {
        "id": None,
        "last_name": None,
        "first_name": None,
        "age": None,
        "gender": None,
        "height_cm": None,
        "weight_kg": None,
        "bmi": None,
        "comorbidities": None,
        "other_comorbidities": None,
        "parse_ok": False,
    }

    dirname = study_dirname.rstrip("_")
    tokens = dirname.split("_")
    if not tokens:
        return result

    result["id"] = tokens[0]
    remaining = [anonadn for anonadn in tokens[1:] if anonadn != ""]
    if not remaining:
        return result

    weight_idx: Optional[int] = None
    for idx in range(len(remaining) - 1, -1, -1):
        if _WEIGHT_TOKEN_RE.fullmatch(remaining[idx]):
            weight_idx = idx
            break

    if weight_idx is not None:
        suffix_tokens = remaining[weight_idx + 1:]
        known_suffix_tokens: List[str] = []
        other_suffix_tokens: List[str] = []
        for token in suffix_tokens:
            tl = token.lower()
            if tl in _COMORBIDITY_TOKENS:
                known_suffix_tokens.append(tl)
            else:
                other_suffix_tokens.append(token)
        result["comorbidities"] = _canonicalize_comorbidities(known_suffix_tokens)
        result["other_comorbidities"] = "+".join(other_suffix_tokens) or None
        remaining = remaining[:weight_idx + 1]

    if remaining and _WEIGHT_TOKEN_RE.fullmatch(remaining[-1]):
        weight_match = _WEIGHT_TOKEN_RE.fullmatch(remaining[-1])
        result["weight_kg"] = int(weight_match.group(1))
        remaining = remaining[:-1]

    if remaining and _FUSED_AGE_GENDER_HEIGHT_RE.fullmatch(remaining[-1]):
        fused = _FUSED_AGE_GENDER_HEIGHT_RE.fullmatch(remaining[-1])
        result["age"] = int(fused.group(1))
        result["gender"] = _GENDER_MAP.get(fused.group(2).lower(), "unknown")
        result["height_cm"] = int(fused.group(3))
        remaining = remaining[:-1]
    else:
        if result["weight_kg"] is not None and remaining and _HEIGHT_TOKEN_RE.fullmatch(remaining[-1]):
            result["height_cm"] = int(remaining[-1])
            remaining = remaining[:-1]

        age, gender, remaining = _extract_age_gender(remaining)
        if age is not None:
            result["age"] = age
            result["gender"] = gender

    if len(remaining) >= 1:
        result["last_name"] = remaining[0]
    if len(remaining) >= 2:
        result["first_name"] = " ".join(remaining[1:])

    if result["height_cm"] and result["weight_kg"] and result["height_cm"] > 0:
        h_m = result["height_cm"] / 100.0
        result["bmi"] = round(result["weight_kg"] / (h_m ** 2), 1)

    result["parse_ok"] = (
        result["age"] is not None
        and result["gender"] is not None
        and result["height_cm"] is not None
        and result["weight_kg"] is not None
    )

    return result


# ---------------------------------------------------------------------------
# Channel path helpers
# ---------------------------------------------------------------------------

_CHANNEL_SUFFIXES: Dict[str, str] = {
    "raw": "_raw.png",
    "beforeDSC": "_beforeDSC.png",
    "final": "_final.png",
    "clahe": "_clahe.png",
    "voi": "_voi.png",
    "log": "_log.png",
    "contrast": "_contrast.png",
    "edge": "_edge.png",
    "hist": "_hist.png",
    "speckle": "_speckle.png",
    "depth": "_depth.png",
    "frangi": "_frangi.png",
    "diff": "_diff.png",
}

# CSV column name → channel key
_CSV_PATH_COLS: Dict[str, str] = {
    "raw_path": "raw",
    "before_dsc_path": "beforeDSC",
    "final_path": "final",
    "clahe_path": "clahe",
    "voi_path": "voi",
    "log_path": "log",
    "contrast_path": "contrast",
    "edge_path": "edge",
    "hist_path": "hist",
    "speckle_path": "speckle",
    "depth_path": "depth",
    "frangi_path": "frangi",
    "diff_path": "diff",
}


def _safe_path(val: Any) -> Optional[str]:
    """Return string path if val is a non-empty, non-NaN string, else None."""
    if pd.isna(val) or val == "":
        return None
    return str(val)


def _frame_channel_paths(row: pd.Series, transport_root: str) -> Dict[str, Optional[str]]:
    """Build {channel_key: absolute_path_or_None} for a single CSV row (frame)."""
    root = Path(transport_root)
    paths: Dict[str, Optional[str]] = {}
    for col, key in _CSV_PATH_COLS.items():
        rel = _safe_path(row.get(col))
        paths[key] = str(root / rel) if rel else None
    return paths


_STUDY_CODE_RE = re.compile(r"^[A-Z]{3}\d{3}$")


def _hash_study_code(study_dirname: str, salt: int = 0) -> str:
    """Return a deterministic `LLLDDD` code derived from a study identifier."""
    digest = hashlib.sha1(f"{study_dirname}|{salt}".encode("utf-8")).digest()
    letters = "".join(chr(ord("A") + (digest[i] % 26)) for i in range(3))
    digits = int.from_bytes(digest[3:5], byteorder="big") % 1000
    return f"{letters}{digits:03d}"


def _assign_study_codes(study_dirnames: List[str]) -> Dict[str, str]:
    """Assign unique deterministic `LLLDDD` codes to study dirnames."""
    assigned: Dict[str, str] = {}
    used_codes: set[str] = set()
    for study_dirname in sorted(dict.fromkeys(str(name) for name in study_dirnames)):
        salt = 0
        while True:
            code = _hash_study_code(study_dirname, salt=salt)
            if code not in used_codes:
                assigned[study_dirname] = code
                used_codes.add(code)
                break
            salt += 1
    return assigned


def generate_study_code_map(
    meta_csv_path: str,
    output_path: str,
) -> pd.DataFrame:
    """Create a deterministic study-code map for all studies in the corpus."""
    meta = pd.read_csv(meta_csv_path, dtype=str)
    records: List[Dict[str, str]] = []
    created_at = datetime.now(timezone.utc).isoformat()
    code_lookup = _assign_study_codes(meta["study_dirname"].dropna().astype(str).tolist())

    grouped = meta.groupby("study_dirname", sort=False)
    for study_dirname, grp in grouped:
        first = grp.sort_values("frame_base").iloc[0]
        records.append(
            {
                "study_code": code_lookup[str(study_dirname)],
                "study_dirname": study_dirname,
                "numeric_study_id": str(study_dirname).split("_")[0],
                "annotator": str(first.get("annotator", "")),
                "session": str(first.get("session", "")),
                "relative_frame_dir": str(first.get("relative_frame_dir", "")),
                "created_at_utc": created_at,
            }
        )

    code_map = pd.DataFrame(records, columns=STUDY_CODE_COLUMNS).sort_values(
        ["numeric_study_id", "annotator", "session", "study_dirname"],
        kind="stable",
    )
    out = Path(output_path).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    code_map.to_csv(out, index=False)
    return code_map


def load_study_code_map(path: str) -> pd.DataFrame:
    """Load and validate the persisted study-code map."""
    code_map_path = Path(path).expanduser().resolve()
    if not code_map_path.is_file():
        raise FileNotFoundError(f"Study-code map not found: {code_map_path}")

    code_map = pd.read_csv(code_map_path, dtype=str)
    missing = [col for col in STUDY_CODE_COLUMNS if col not in code_map.columns]
    if missing:
        raise ValueError(f"Study-code map missing columns: {missing}")
    if code_map["study_code"].isna().any() or code_map["study_dirname"].isna().any():
        raise ValueError("Study-code map contains blank study_code or study_dirname values")
    if code_map["study_code"].duplicated().any():
        raise ValueError("Study-code map contains duplicate study_code values")
    if code_map["study_dirname"].duplicated().any():
        raise ValueError("Study-code map contains duplicate study_dirname values")
    invalid_codes = code_map.loc[
        ~code_map["study_code"].astype(str).str.fullmatch(_STUDY_CODE_RE.pattern),
        "study_code",
    ].tolist()
    if invalid_codes:
        raise ValueError(f"Study-code map contains invalid study codes: {invalid_codes[:5]}")
    return code_map[STUDY_CODE_COLUMNS].copy()


# ---------------------------------------------------------------------------
# Manifest builder
# ---------------------------------------------------------------------------

def build_study_manifest(
    meta_csv_path: str,
    transport_root: str,
    study_code_map_path: Optional[str] = None,
) -> pd.DataFrame:
    """Collapse 956 CSV rows → 319 study-level rows.

    Each row has:
        study_dirname, annotator, session, relative_frame_dir,
        frame_bases (list[str]), frame_count,
        id, last_name, first_name, age, gender,
        height_cm, weight_kg, bmi, comorbidities, other_comorbidities,
        has_video, has_mask, has_pixel_spacing,
        final_paths (list), mask_paths (list),
        channel_paths (list[dict]),  ← per frame
        pixel_spacing_x, pixel_spacing_y,
        dcm_rows, jpg_height, jpg_width, mask_height, mask_width,
        video_path (str|None)
    """
    meta = pd.read_csv(meta_csv_path, dtype=str)
    transport_root = str(Path(transport_root).resolve())
    code_map = load_study_code_map(study_code_map_path) if study_code_map_path else None
    code_lookup = (
        dict(zip(code_map["study_dirname"], code_map["study_code"]))
        if code_map is not None
        else {}
    )

    records = []
    for study_dirname, grp in meta.groupby("study_dirname", sort=False):
        grp = grp.sort_values("frame_base").reset_index(drop=True)
        first = grp.iloc[0]

        demo = parse_demographics(study_dirname)

        # Spacing: take from first row; may be empty string / NaN
        def _num(col: str) -> Optional[float]:
            v = _safe_path(first.get(col, ""))
            try:
                return float(v) if v else None
            except (ValueError, TypeError):
                return None

        px_x = _num("pixel_spacing_x")
        px_y = _num("pixel_spacing_y")
        dcm_rows = _num("dcm_rows")
        jpg_h = _num("jpg_height")
        jpg_w = _num("jpg_width")
        mask_h = _num("mask_height")
        mask_w = _num("mask_width")

        # Per-frame paths
        frame_bases = grp["frame_base"].tolist()
        final_paths: List[Optional[str]] = []
        mask_paths: List[Optional[str]] = []
        channel_paths_list: List[Dict[str, Optional[str]]] = []

        for _, row in grp.iterrows():
            root = Path(transport_root)
            fp = _safe_path(row.get("final_path"))
            final_paths.append(str(root / fp) if fp else None)

            mp = _safe_path(row.get("mask_path"))
            mask_paths.append(str(root / mp) if mp else None)

            channel_paths_list.append(_frame_channel_paths(row, transport_root))

        # Video
        mp4_rel = _safe_path(first.get("source_mp4_path"))
        video_path = str(Path(transport_root) / mp4_rel) if mp4_rel else None

        # Flags
        has_video = video_path is not None and Path(video_path).is_file()
        has_mask = any(mp and Path(mp).is_file() for mp in mask_paths)
        has_pixel_spacing = px_x is not None and px_y is not None

        records.append(
            {
                "study_code": code_lookup.get(study_dirname),
                "study_dirname": study_dirname,
                "annotator": str(first.get("annotator", "")),
                "session": str(first.get("session", "")),
                "relative_frame_dir": str(first.get("relative_frame_dir", "")),
                "frame_bases": frame_bases,
                "frame_count": len(frame_bases),
                # demographics
                **{k: demo[k] for k in demo},
                # flags
                "has_video": has_video,
                "has_mask": has_mask,
                "has_pixel_spacing": has_pixel_spacing,
                # per-frame paths
                "final_paths": final_paths,
                "mask_paths": mask_paths,
                "channel_paths": channel_paths_list,
                # spacing
                "pixel_spacing_x": px_x,
                "pixel_spacing_y": px_y,
                "dcm_rows": dcm_rows,
                "jpg_height": jpg_h,
                "jpg_width": jpg_w,
                "mask_height": mask_h,
                "mask_width": mask_w,
                "video_path": video_path,
            }
        )
    manifest = pd.DataFrame(records)
    if code_map is not None:
        missing_codes = manifest[manifest["study_code"].isna()]["study_dirname"].tolist()
        extra_codes = sorted(set(code_map["study_dirname"]) - set(manifest["study_dirname"]))
        if missing_codes or extra_codes:
            raise ValueError(
                "Study-code map does not match manifest coverage: "
                f"missing={missing_codes[:5]}, extra={extra_codes[:5]}"
            )
    return manifest


# ---------------------------------------------------------------------------
# Center frame selection
# ---------------------------------------------------------------------------

def _load_mask_safe(path: Optional[str]) -> Optional[np.ndarray]:
    """Load a mask PNG as uint8 array; return None on failure."""
    if path is None:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    try:
        img = Image.open(p).convert("L")
        return np.array(img, dtype=np.uint8)
    except Exception:
        return None


def select_center_frame(study_row: pd.Series) -> int:
    """Return index of the 'representative center frame' for a study.

    Priority order:
      1. Temporal middle frame (n // 2) if its mask loads and yields standard_qmt.
      2. Any other frame that yields standard_qmt (prefer closest to middle).
      3. Temporal middle frame regardless (even if it only supports a proxy QMT or no mask).

    Rationale: the sweep goes lateral → mid → medial; the temporal center is the
    anatomical mid-belly of the muscle. We only deviate when that frame genuinely
    cannot provide a valid QMT measurement.
    """
    mask_paths = study_row["mask_paths"]
    n = len(mask_paths)
    temporal_center = n // 2

    from labeling_app.measurements import compound_thickness_px  # lazy import

    # Load all masks once
    masks = [_load_mask_safe(mp) for mp in mask_paths]

    # 1. Try temporal center with standard_qmt
    if masks[temporal_center] is not None:
        _, prov = compound_thickness_px(masks[temporal_center])
        if prov == "standard_qmt":
            return temporal_center

    # 2. Try other frames for standard_qmt (sorted by distance from temporal center)
    other_indices = sorted(
        [i for i in range(n) if i != temporal_center],
        key=lambda i: abs(i - temporal_center),
    )
    for i in other_indices:
        if masks[i] is not None:
            _, prov = compound_thickness_px(masks[i])
            if prov == "standard_qmt":
                return i

    # 3. Fall back to temporal center regardless
    return temporal_center


def select_center_frame_details(study_row: pd.Series) -> Tuple[int, str]:
    """Return the representative frame index plus a short provenance label."""
    mask_paths = study_row["mask_paths"]
    n = len(mask_paths)
    temporal_center = n // 2

    from labeling_app.measurements import compound_thickness_px  # lazy import

    masks = [_load_mask_safe(mp) for mp in mask_paths]
    if masks[temporal_center] is not None:
        _, prov = compound_thickness_px(masks[temporal_center])
        if prov == "standard_qmt":
            return temporal_center, "temporal_center_standard_qmt"

    other_indices = sorted(
        [i for i in range(n) if i != temporal_center],
        key=lambda i: abs(i - temporal_center),
    )
    for i in other_indices:
        if masks[i] is not None:
            _, prov = compound_thickness_px(masks[i])
            if prov == "standard_qmt":
                return i, "nearest_standard_qmt"

    return temporal_center, "temporal_center_fallback"

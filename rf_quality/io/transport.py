"""Transport metadata + study_codes join.

Resolves ``(study_code, frame_base, modality) → concrete frame path + mask
path`` with fail-fast behaviour when a completed study's ``center_frame_base``
does not resolve in transport.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


MODALITY_COLUMNS = {
    "final": "final_path",
    "raw": "raw_path",
    "before_dsc": "before_dsc_path",
}


@dataclass(frozen=True)
class FrameRow:
    study_code: str
    frame_base: str
    modality: str
    image_path: Path
    mask_path: Path
    mask_height: int
    mask_width: int
    jpg_height: int | None
    jpg_width: int | None
    dcm_rows: float | None
    dcm_cols: float | None
    pixel_spacing_x: float | None
    pixel_spacing_y: float | None


class TransportIndex:
    def __init__(self, transport: pd.DataFrame, study_codes: pd.DataFrame, transport_root: Path):
        self.transport_root = transport_root
        # map study_id (in transport) → study_code via study_codes
        sc = study_codes[["study_code", "relative_frame_dir", "study_dirname", "annotator", "session"]]
        # derive transport study_id pattern: "{annotator}_{session}_{study_dirname}"
        sc = sc.assign(
            study_id=sc["annotator"] + "_" + sc["session"] + "_" + sc["study_dirname"],
        )[["study_code", "study_id", "relative_frame_dir"]]

        merged = transport.merge(sc, on="study_id", how="left", validate="many_to_one")
        missing = merged["study_code"].isna().sum()
        if missing:
            # permit — transport may carry frames from unassigned studies; log upstream
            pass
        self._frames = merged
        self._by_study: dict[str, pd.DataFrame] = {
            code: grp for code, grp in merged.dropna(subset=["study_code"]).groupby("study_code")
        }

    def has_study(self, study_code: str) -> bool:
        return study_code in self._by_study

    def frames_for_study(self, study_code: str) -> pd.DataFrame:
        return self._by_study.get(study_code, pd.DataFrame())

    def frame_bases_for_study(self, study_code: str) -> list[str]:
        df = self.frames_for_study(study_code)
        return df["frame_base"].tolist() if not df.empty else []

    def resolve_frame(self, study_code: str, frame_base: str, modality: str) -> FrameRow | None:
        df = self.frames_for_study(study_code)
        if df.empty:
            return None
        row = df[df["frame_base"] == frame_base]
        if row.empty:
            return None
        r = row.iloc[0]
        path_col = MODALITY_COLUMNS[modality]
        rel_image = r[path_col]
        rel_mask = r["mask_path"]
        if not isinstance(rel_image, str) or not isinstance(rel_mask, str):
            return None
        image_path = self.transport_root / rel_image
        mask_path = self.transport_root / rel_mask
        return FrameRow(
            study_code=study_code,
            frame_base=frame_base,
            modality=modality,
            image_path=image_path,
            mask_path=mask_path,
            mask_height=int(r["mask_height"]),
            mask_width=int(r["mask_width"]),
            jpg_height=int(r["jpg_height"]) if pd.notna(r["jpg_height"]) else None,
            jpg_width=int(r["jpg_width"]) if pd.notna(r["jpg_width"]) else None,
            dcm_rows=float(r["dcm_rows"]) if pd.notna(r["dcm_rows"]) else None,
            dcm_cols=float(r["dcm_cols"]) if pd.notna(r["dcm_cols"]) else None,
            pixel_spacing_x=float(r["pixel_spacing_x"]) if pd.notna(r["pixel_spacing_x"]) else None,
            pixel_spacing_y=float(r["pixel_spacing_y"]) if pd.notna(r["pixel_spacing_y"]) else None,
        )


def load_transport_index(
    transport_metadata_csv: Path,
    study_codes_csv: Path,
    transport_root: Path,
) -> TransportIndex:
    tp = pd.read_csv(transport_metadata_csv)
    sc = pd.read_csv(study_codes_csv)
    return TransportIndex(tp, sc, transport_root)


def assert_center_frame_bases_resolve(
    transport: TransportIndex,
    latest_completed: pd.DataFrame,
) -> list[str]:
    """Every completed study's ``center_frame_base`` must resolve in transport.

    Returns the list of offending ``study_code``. Caller decides whether to
    fail-fast (plan: fail-fast on Phase 1); we keep it a list so the QA
    report can enumerate.
    """
    missing: list[str] = []
    completed = latest_completed[latest_completed["decision_status"] != "quality_failed"]
    for study, grp in completed.groupby("study_code"):
        cf = grp["center_frame_base"].dropna().astype(str).unique()
        if len(cf) == 0:
            missing.append(f"{study}: no center_frame_base")
            continue
        anchor = cf[0]
        bases = set(transport.frame_bases_for_study(study))
        if anchor not in bases:
            missing.append(f"{study}: center_frame_base={anchor} not in transport")
    return missing

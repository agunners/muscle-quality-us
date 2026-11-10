"""Shared loader for the force-band analysis. Imports the harness's row-set logic with its QA
path pointed at the LIVE repo substrate (read-only; sha256 recorded in every output), so
PRIMARY / SENS_A / SENS_B are set-identical to the prereg harness. Nothing here writes into
the repo: the harness main is never called and its OUT is re-pointed at the work dir."""
from __future__ import annotations
import hashlib, importlib.util, json
from pathlib import Path
import numpy as np, pandas as pd

REPO = Path(__file__).resolve().parents[3]
QA = REPO / "data/qa_master.csv"
RESULTS = REPO / "scripts/analysis/results"          # frozen receipts (read-only)
COHORT = REPO / "data/cohort_stats"                    # served card model (read-only)
WORK = Path(__file__).resolve().parent / "results"   # receipts beside the scripts (was the session scratchpad)

def substrate_identity() -> dict:
    b = QA.read_bytes()
    return {"file": str(QA), "sha256_16": hashlib.sha256(b).hexdigest()[:16], "bytes": len(b)}

def load_harness():
    spec = importlib.util.spec_from_file_location("force_ladder", REPO / "scripts/analysis/force_ladder.py")
    fl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fl)
    fl.QA = QA
    fl.OUT = WORK / "_never_used"      # belt-and-braces: main() is never called
    return fl

def harness_sets():
    fl = load_harness()
    return fl, fl.row_sets(fl.load_rows())

def qa_frame() -> pd.DataFrame:
    df = pd.read_csv(QA, dtype={"study_uid": str, "sex": str, "cohort_round": str}, low_memory=False)
    return df.drop(columns=[c for c in ("study_name", "rel_path", "operator") if c in df.columns])  # PHI guard

def sens_a_frame() -> pd.DataFrame:
    """SENS_A_no_texture rows (n=143) in harness order, joined to the full qa_master row."""
    fl, sets = harness_sets()
    uids = [r["uid"] for r in sets["SENS_A_no_texture"]]
    out = qa_frame().set_index("study_uid").loc[uids].copy()
    out["sex_m"] = out["sex"].map({"M": 1.0, "F": 0.0})
    out.index.name = "study_uid"
    return out.reset_index()

R2_COLS = ["age", "sex_m", "height_cm", "weight_kg",
           "rf_thickness_mm", "full_thickness_q50_mm", "visible_rf_area_mm2", "qmt_mm"]

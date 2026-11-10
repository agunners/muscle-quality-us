"""Feature-set definitions shared by q3 and q5. Everything here exists at test time WITHOUT a
dynamometer (isok_*, izo_cv_pct are criterion-side and never used as features)."""
import numpy as np, pandas as pd
from common import RESULTS, R2_COLS

DEMO5 = ["age", "sex_m", "height_cm", "weight_kg", "bmi"]
GEO5 = ["rf_thickness_mm", "full_thickness_q50_mm", "visible_rf_area_mm2", "qmt_mm", "star_ratio"]
FAT5 = ["fat_thickness_q50_mm", "fat_thickness_max_mm", "fat_to_depth_q50", "muscle_fat_ratio", "fat_contribution"]
TEX1 = ["int_cv_within"]
R3_COLS = R2_COLS + ["fat_thickness_q50_mm", "fat_thickness_max_mm", "fat_to_depth_q50"]   # harness rung 3
R4_COLS = R3_COLS + TEX1                                                                     # harness rung 4
ALL16 = DEMO5 + GEO5 + FAT5 + TEX1
ALL15 = DEMO5 + GEO5 + FAT5               # texture-free (SENS_A-complete)
ALL18 = ALL16 + ["condition_score", "texture_contribution"]
QA_HAND = ["fat_thickness_q50_mm", "fat_thickness_max_mm", "fat_to_depth_q50", "int_cv_within"]

CAP_META = ["n_frames_in_capture", "n_frames_segmented", "seconds_total", "full_area_px"]
CAP_SIZE = ["full_area_mm2", "full_max_thickness_mm", "full_thickness_q50_mm", "area_retained_fraction_after_erosion",
            "anchor_rf_thickness_mm", "anchor_rf_area_mm2"]
CAP_FAT = ["fat_thickness_mm", "fat_to_area_ratio", "ei_to_fat_ratio", "fat_int_mean", "multi_otsu_fat_fraction", "skin_thickness_mm"]

def capture_rf() -> pd.DataFrame:
    """rf-role capture features: numeric, non-constant, NaN-free, with meta + muscle-SIZE columns removed."""
    cf = pd.read_csv(RESULTS / "capture_features.csv")
    rf = cf[cf.role == "rf"].set_index("study_uid")
    num = rf.select_dtypes(include=[np.number])
    drop = set(CAP_META) | set(CAP_SIZE) | {c for c in num.columns if c.endswith("_young_refit")}
    keep = [c for c in num.columns if c not in drop and num[c].notna().all() and num[c].nunique() > 1]
    return num[keep]

def capture_sets(rf: pd.DataFrame):
    img_all = list(rf.columns)                       # texture/brightness/glcm/depth-band/fat/fascia (no size)
    fat_like = set(CAP_FAT) | {c for c in img_all if c.endswith("_fat_corrected_young_lit")}
    img_nofat = [c for c in img_all if c not in fat_like]
    return img_all, img_nofat

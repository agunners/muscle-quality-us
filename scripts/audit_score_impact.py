#!/usr/bin/env python
"""D3 — per-model score impact for AKA017, from the D2 segmentation (data/audit/measurement_derivation.json).

Scores the three condition models (A/B/C) under three RF states, holding the femur-derived axes
(STAR, fat-fraction — the sound ones) fixed:
  CURRENT     : RF from c1 with its attached 7.9cm DICOM (the live bug — 6.9cm frames, 7.9cm ruler)
  SPACING-FIX : RF from c1 rescaled to its true 6.9cm (width-derived)
  INTENDED-RF : RF from c3 (the 7.9cm mp4 cine the pipeline should have used)

Shows that C (STAR/fat/texture) is spacing-robust while A/B (RF-geometry size) move, and that the
capture swap (c1->c3) shifts even C via int_cv. Re-runnable; prints a table.
"""
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))
from preflight_helpers import condition_model as cm  # noqa: E402

DERIV = REPO / "data/audit/measurement_derivation.json"
AKA017 = "21jun26_026_829_anonaat_anonabz_45k_152_89_45r"


def _bmi(name: str):
    # grammar: ..._agesex_boy_kilo_ASIS  -> height then weight are ADJACENT tokens (155_90)
    toks = name.split("_")
    for i, t in enumerate(toks):
        if t.isdigit() and 120 < int(t) < 210:                  # height
            for w in toks[i + 1:]:
                if w.isdigit() and 35 < int(w) < 200:           # the next weight-range token
                    return round(int(w) / (int(t) / 100) ** 2, 2)
    return None


def main():
    d = json.loads(DERIV.read_text())[AKA017]["captures"]
    bmi = _bmi(AKA017)
    femur = d["c2_femur_deep"]["features_attached_dicom"]        # c2 dcm is correct (9.9)
    qmt = femur["qmt_mm"]
    star = round(qmt / bmi, 4)
    fat_frac = femur["fat_to_depth_q50"]                          # the score's fat axis (sound, from femur)
    print(f"# AKA017  BMI={bmi}  femur(c2): qmt={qmt}  STAR={star}  fat_to_depth={fat_frac}\n")

    def rf(cap, spacing):
        return d[cap][f"features_{spacing}"]

    scenarios = {
        "CURRENT (c1 @7.9 DICOM, the bug)": rf("c1_rf_shallow", "attached_dicom"),
        "SPACING-FIX (c1 @6.9 width)":      rf("c1_rf_shallow", "width_derived"),
        "INTENDED-RF (c3 @7.9 mp4 cine)":   rf("c3_femur_deep", "width_derived"),
    }

    art = cm.load()
    print(f"{'scenario':34s} | {'full_thk':>9s} {'int_cv':>7s} |   A            B            C")
    print("-" * 96)
    for name, r in scenarios.items():
        feats = {
            "star_ratio": star, "fat_to_depth_q50": fat_frac, "qmt_mm": qmt,
            "full_thickness_q50_mm": r["full_thickness_q50_mm"],
            "rf_thickness_mm": r["rf_thickness_mm"],
            "fat_thickness_q50_mm": r["fat_thickness_q50_mm"],
            "visible_rf_area_mm2": r["visible_rf_area_mm2"],
            "int_cv_within": r["int_cv_within"],
        }
        res = cm.score_patient(art, feats)["options"]

        def cell(k):
            o = res[k]
            return f"{o['condition']:>3.0f} {o['band'][:4]:<4s}" if o.get("available") else "  n/a    "
        ft = r["full_thickness_q50_mm"]
        print(f"{name:34s} | {str(ft)[:9]:>9s} {r['int_cv_within']:>7.3f} |  "
              f"{cell('A')}   {cell('B')}   {cell('C')}")

    print("\nA = full_thk/fat/texture · B = 8 features · C(headline) = STAR/fat/texture")
    print("STAR + fat_to_depth are from the deep 9.9cm femur (c2, correct spacing) — identical across scenarios.")
    print("NOTE: which capture is the true RF (c1 fixed-ruler vs c3) needs human review; c2 femur content is thin.")


if __name__ == "__main__":
    main()

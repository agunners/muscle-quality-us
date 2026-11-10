"""Pre-UI checklist Item 2 — verify the canonical model bundle.

Acceptance criteria (HARD GATE):
  - model.head.out_channels == 10  (probed from loaded model, NOT JSON)
  - pinned class IDs accepted by AppCfg override (femur=7, not the default 4)
  - Dice(predicted_RF, annotator_RF) >= 0.85 on AGZ855 anchor frame

Usage:
    source scripts/env.sh && .venv/bin/python scripts/verify_model.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Launcher preamble: prepend scripts/ + ensure the runtime bridge.
SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
import preflight_helpers.env_bootstrap  # noqa: F401  side-effect: sets sys.path

import numpy as np
import torch
from PIL import Image

from preflight_helpers import env
from preflight_helpers.segmenter import (
    LABEL_RF, LABEL_FEMUR, LABEL_FASCIA, LABEL_FAT, LABEL_SKIN, LABEL_VM,
    BundleMeta, dice, load_learner_safe, model_output_channels,
    pinned_mask_cfg, predict_mask, stack_modalities,
)

REPO_ROOT = SCRIPTS_DIR.parent
MODEL_DIR = REPO_ROOT / "model_"
PALETTE_PATH = MODEL_DIR / "label_palette.json"

# AGZ855 anchor frame (the centerpiece demo case). Addressed through `env`, never a hardcoded
# absolute path: this pointed at the pre-rename repo (`..._claude_PROMPTED/transport/...`), which
# no longer exists, so L1.2 of the test ladder hard-failed here with FileNotFoundError and looked
# like missing data. The channel PNGs are in the in-repo transport/ and always were.
_AGZ855_REL = Path("anonabn_olcum") / "27Jan26_03" / "0000145_anonadm_anonabu_46e_171_110"
AGZ855_RAW_DIR = env.TRANSPORT_ROOT / _AGZ855_REL
AGZ855_ANCHOR_BASE = "20260127_151045625"
AGZ855_ANNOTATOR_MASK = (
    env.ANNOTATOR_MASKS_ROOT / _AGZ855_REL / f"{AGZ855_ANCHOR_BASE}_mask.png"
)

DICE_PASS = 0.85
DICE_HARD_STOP = 0.70  # below this, the plan says STOP and report root cause


def find_canonical_bundle() -> Path:
    # The pinned canonical bundle (configs/canonical_bundle.json), sha-guarded —
    # so the Dice gate verifies the bundle actually served, not the newest mtime.
    from preflight_helpers.model_registry import resolve_canonical_pkl
    p = resolve_canonical_pkl()
    if p is None:
        raise SystemExit(f"No canonical bundle resolvable (pointer + {MODEL_DIR})")
    print(f"[note] verifying canonical bundle: {p.name}")
    return p


def main() -> int:
    # ---------- Bundle discovery and metadata
    pkl = find_canonical_bundle()
    meta = BundleMeta.from_pkl(pkl)
    print(f"[bundle] {pkl.name}")
    print(f"  modalities    : {list(meta.modalities)}")
    print(f"  stats mean    : {meta.stats_mean}")
    print(f"  stats std     : {meta.stats_std}")
    print(f"  resize_to (HW): {meta.resize_to}")
    print(f"  rrc_size  (HW): {meta.rrc_size}")
    print(f"  rf_class_id   : {meta.rf_class_id_in_json} (from JSON train_cfg)")

    # ---------- Palette sanity (just check it loads + has 10 entries)
    with open(PALETTE_PATH) as f:
        palette = json.load(f)
    print(f"[palette] {PALETTE_PATH.name}: {len(palette)} entries — labels are opaque,"
          " semantic IDs come from segmenter.LABEL_* constants")

    # ---------- Load
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"[device] {device}")
    t0 = time.time()
    learn = load_learner_safe(pkl)
    print(f"[load]   {time.time() - t0:.2f}s")

    # ---------- Probe output channels FROM THE LOADED MODEL (not the JSON)
    out_ch = model_output_channels(learn, in_channels=len(meta.modalities), device=device)
    print(f"[probe]  model output channels = {out_ch}")
    if out_ch != 10:
        print(f"  FAIL: expected 10, got {out_ch}")
        return 2

    # ---------- Apply pinned MaskCfg override and report what was overridden
    mask_cfg = pinned_mask_cfg()
    print(f"[pinned-MaskCfg]")
    print(f"  RF={mask_cfg.muscle_class_id} VI(implicit)={LABEL_RF + 1}"
          f" VM={LABEL_VM} fascia={mask_cfg.fascia_class_id}"
          f" skin={mask_cfg.skin_class_id} fat={mask_cfg.fat_class_id}"
          f" FEMUR={mask_cfg.femur_class_id}  ← overrides the default 4 (VM)")
    assert mask_cfg.femur_class_id == LABEL_FEMUR == 7, "femur override broken"
    assert mask_cfg.muscle_class_id == LABEL_RF == 1, "RF pin broken"

    # ---------- AGZ855 anchor frame Dice
    stacked = stack_modalities(AGZ855_RAW_DIR, AGZ855_ANCHOR_BASE, meta.modalities)
    print(f"[AGZ855] anchor frame stacked shape: {stacked.shape}, dtype={stacked.dtype}")

    pred = predict_mask(learn, stacked, meta, device=device)
    print(f"[AGZ855] predicted mask shape: {pred.shape}, unique classes:"
          f" {sorted(np.unique(pred).tolist())}")

    with Image.open(AGZ855_ANNOTATOR_MASK) as im:
        annot = np.asarray(im.convert("L"), dtype=np.uint8)
    if annot.shape != pred.shape:
        # Resize annot to pred shape with nearest (or the other way).
        pil = Image.fromarray(annot).resize((pred.shape[1], pred.shape[0]), Image.NEAREST)
        annot = np.asarray(pil, dtype=np.uint8)
    print(f"[AGZ855] annotator mask shape (after align): {annot.shape},"
          f" unique classes: {sorted(np.unique(annot).tolist())}")

    rf_dice = dice(pred, annot, LABEL_RF)
    print(f"[AGZ855] Dice(predicted_RF, annotator_RF) = {rf_dice:.4f}")

    # Also report Dice for a few other classes — sanity not a gate.
    for cid, label in [(LABEL_VM, "VM(class 4)"), (LABEL_FASCIA, "fascia"),
                       (LABEL_FAT, "fat"), (LABEL_FEMUR, "femur(class 7)")]:
        d = dice(pred, annot, cid)
        n_pred = int((pred == cid).sum())
        n_true = int((annot == cid).sum())
        print(f"  Dice {label:18s} = {d:.4f}  (pred={n_pred}px, annot={n_true}px)")

    # ---------- Verdict
    print()
    print("=" * 60)
    if rf_dice >= DICE_PASS:
        print(f"PASS  out_channels=10, RF Dice {rf_dice:.4f} >= {DICE_PASS}")
        return 0
    if rf_dice >= DICE_HARD_STOP:
        print(f"WARN  RF Dice {rf_dice:.4f} between hard-stop {DICE_HARD_STOP}"
              f" and pass {DICE_PASS} — investigate before proceeding")
        return 1
    print(f"FAIL  RF Dice {rf_dice:.4f} below hard-stop {DICE_HARD_STOP}")
    print(f"      Plan says STOP: silent bundle corruption (fastai version skew) or")
    print(f"      class pinning incorrect for this bundle. Do not proceed to UI build.")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())

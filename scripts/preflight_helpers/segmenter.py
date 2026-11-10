"""Segmenter contract — class IDs pinned, NOT derived from palette.

The label_palette.json holds ('a', 'b', 'c', ...) labels — opaque text used for color
rendering only. The semantic class IDs used downstream are pinned here as constants,
and AppCfg() defaults from rf_quality.config are overridden at preflight startup so
no module silently uses femur_class_id=4 (which is actually VL, not femur).

Validation that the pinning is correct for any given model bundle: run inference on
the AGZ855 anchor frame, compute Dice of the predicted class-1 mask against the
annotator's class-1 mask. Acceptance: Dice >= 0.85. See scripts/verify_model.py.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import torch
import torch.nn.functional as F
import torchvision.transforms.functional as TF
from PIL import Image

# Semantic class IDs — palette is for rendering, these are for code.
LABEL_BACKGROUND = 0
LABEL_RF         = 1   # rectus femoris
LABEL_VI         = 2   # vastus intermedius
LABEL_VL         = 3   # vastus lateralis (right-leg sweeps, left-to-right)
LABEL_VM         = 4   # vastus medialis  (NOT femur — rf_quality.config default is wrong)
LABEL_FASCIA     = 5
LABEL_SKIN       = 6
LABEL_FEMUR      = 7   # bone
LABEL_FAT        = 8
LABEL_SARTORIUS  = 9


def pinned_mask_cfg():
    """Build a rf_quality.config.MaskCfg with the pinned IDs (overrides femur=4 default).

    Returns a MaskCfg dataclass. Callers can `replace(cfg.mask, ...)` or build their
    own AppCfg with this mask block.
    """
    from rf_quality.config import MaskCfg
    return MaskCfg(
        muscle_class_id=LABEL_RF,
        fascia_class_id=LABEL_FASCIA,
        skin_class_id=LABEL_SKIN,
        fat_class_id=LABEL_FAT,
        femur_class_id=LABEL_FEMUR,  # override the dangerous default of 4
        erosion_px=3,
    )


@dataclass(frozen=True)
class BundleMeta:
    pkl_path: Path
    json_path: Path
    modalities: tuple[str, ...]
    stats_mean: tuple[float, ...]
    stats_std: tuple[float, ...]
    resize_to: tuple[int, int]      # H, W as in JSON — the item-transform pad box
    rrc_size: tuple[int, int]       # H, W — the validation OUTPUT (what the model sees)
    rf_class_id_in_json: int
    # Validation-transform geometry — the RandomResizedCropGPU valid-time center
    # crop (`fastai_pipeline.create_dataloaders`). Defaulted so any BundleMeta
    # built before these fields (e.g. test fakes) still constructs.
    rrc_ratio: tuple[float, float] = (1.0, 1.0)   # W/H crop-ratio bracket
    rrc_valid_scale: float = 1.0                  # validation center-crop area scale
    # Item-transform method + pad mode, IF the training config records them. Current
    # fastai bundles don't stamp these (the method is fixed by the shared
    # `fastai_pipeline.create_dataloaders`), so they default None → the contract
    # check enforces the aspect invariant and skips the method check. When a future
    # training notebook stamps them, `assert_faithful_preprocess` starts enforcing.
    resize_method: str | None = None
    pad_mode: str | None = None

    @classmethod
    def from_pkl(cls, pkl_path: Path) -> "BundleMeta":
        pkl_path = Path(pkl_path)
        json_path = pkl_path.with_suffix(".json")
        with open(json_path) as f:
            meta = json.load(f)
        modalities = tuple(meta["modalities"])
        means, stds = meta["stats"]
        train_cfg = meta["train_cfg"]
        rt = tuple(train_cfg["resize_to"])
        rrc = tuple(train_cfg["rrc_size"])
        rrc_ratio = tuple(float(v) for v in train_cfg.get("rrc_ratio", (1.0, 1.0)))
        rrc_valid_scale = float(train_cfg.get("rrc_valid_scale", 1.0))
        out = cls(
            pkl_path=pkl_path,
            json_path=json_path,
            modalities=modalities,
            stats_mean=tuple(means),
            stats_std=tuple(stds),
            resize_to=rt,
            rrc_size=rrc,
            rf_class_id_in_json=int(train_cfg.get("rf_class_id", LABEL_RF)),
            rrc_ratio=rrc_ratio,
            rrc_valid_scale=rrc_valid_scale,
            resize_method=(train_cfg.get("resize_method") or train_cfg.get("method")),
            pad_mode=train_cfg.get("pad_mode"),
        )
        # Enforce, at load, that this bundle's transform is one `preprocess_for_model`
        # can faithfully reproduce. All current bundles pass (no behaviour change);
        # a future non-reproducible bundle fails loudly here instead of being served
        # with a silently-wrong reproduction.
        assert_faithful_preprocess(out)
        return out


class PreprocessingContractError(RuntimeError):
    """A bundle's training config violates a precondition the serving
    letterbox-preprocess relies on, so `preprocess_for_model` could NOT
    faithfully reproduce its validation transform. Raised at load — loudly — so a
    non-reproducible bundle is never silently served with a wrong reproduction."""


def assert_faithful_preprocess(meta: "BundleMeta", *, aspect_tol: float = 1e-3) -> None:
    """Enforce the preconditions the faithful `preprocess_for_model` path relies on
    (the fastai-pad reproduction). Non-destructive for every current bundle; a loud
    failure for any future bundle that would otherwise be silently mis-preprocessed.

    Checks:
      1. aspect(resize_to) == aspect(rrc_size)  [HARD] — the item-pad box and the
         validation output share aspect, which is exactly what makes 'letterbox
         straight into rrc_size' equal training's pad->resize_to->crop-to-rrc_size
         (`_validation_geometry`). If they diverge, the shortcut is simply wrong.
      2. resize_method, when the config records one, is 'pad'      [HARD].
      3. pad_mode, when the config records one, is 'zeros'         [HARD].

    The transform *method* is convention today (no current JSON stamps it — it is
    fixed by the shared `fastai_pipeline.create_dataloaders`). This reads and
    enforces it the moment a training notebook starts recording it, turning the
    convention into a checked contract WITHOUT touching the training repo.

    Reproduction is EXACT when rrc_valid_scale == 1.0 (every current bundle). A
    bundle with rrc_valid_scale < 1.0 carries the documented center-crop residual
    (`_validation_geometry`) but is NOT rejected — the crop is intentionally
    omitted so the skin/fat and deep-femur anchors survive."""
    rt_h, rt_w = meta.resize_to
    rrc_h, rrc_w = meta.rrc_size
    if not (rt_h and rt_w and rrc_h and rrc_w):
        raise PreprocessingContractError(
            f"{meta.pkl_path.name}: resize_to={meta.resize_to} / rrc_size={meta.rrc_size} "
            f"incomplete — cannot verify the preprocess contract."
        )
    a_rt, a_rrc = rt_w / rt_h, rrc_w / rrc_h
    if abs(a_rt - a_rrc) > aspect_tol:
        raise PreprocessingContractError(
            f"{meta.pkl_path.name}: resize_to aspect {a_rt:.4f} (W/H {rt_w}/{rt_h}) != "
            f"rrc_size aspect {a_rrc:.4f} ({rrc_w}/{rrc_h}). The letterbox-into-rrc_size "
            f"reproduction assumes equal aspect; this bundle would be mis-preprocessed."
        )
    if meta.resize_method is not None and str(meta.resize_method).lower() != "pad":
        raise PreprocessingContractError(
            f"{meta.pkl_path.name}: train_cfg resize method '{meta.resize_method}' != 'pad'. "
            f"The serving preprocess reproduces a letterbox pad only."
        )
    if meta.pad_mode is not None and str(meta.pad_mode).lower() not in ("zeros", "zero"):
        raise PreprocessingContractError(
            f"{meta.pkl_path.name}: train_cfg pad_mode '{meta.pad_mode}' != 'zeros'. "
            f"The serving preprocess zero-pads."
        )


def load_learner_safe(pkl_path: Path):
    """Wrap fastai.load_learner with the noqa imports required by the pickle."""
    # The pickle stores callables from these modules; importing them registers
    # the names so unpickling succeeds.
    from fastai_pipeline import (  # noqa: F401
        SegmentationAlbumentationsTransform,
        _stack_modalities,
        _load_mask,
    )
    from fastai.learner import load_learner
    return load_learner(str(pkl_path), cpu=False)


def stack_modalities(frame_dir: Path, frame_base: str, modalities: Sequence[str]) -> np.ndarray:
    """Load pre-computed modality PNGs from `transport/` and stack to HWC uint8.

    `frame_base` is e.g. '20260127_151045625'; modality PNG = '{base}_{modality}.png'.
    Returns ndarray of shape (H, W, len(modalities)), dtype uint8.

    Use this only when transport-side modality PNGs exist (cohort 3-frame triples).
    For arbitrary sweep frames, use `synthesize_final_u8` + `build_voi_hist_clahe_stack`.
    """
    frame_dir = Path(frame_dir)
    arrs = []
    for m in modalities:
        p = frame_dir / f"{frame_base}_{m}.png"
        with Image.open(p) as img:
            arrs.append(np.asarray(img.convert("L"), dtype=np.uint8))
    return np.stack(arrs, axis=-1)


def target_size_from_raw_png(raw_image_dir: Path, frame_base: str) -> tuple[int, int]:
    """Return (width, height) by reading the `_raw.png` sibling — always present.

    Mirrors `goruntuler/scripts/build_transport_dataset.py:353` which uses
    `raw_img.size` as the target shape for `_final.png`. PIL .size is (W, H).
    """
    with Image.open(Path(raw_image_dir) / f"{frame_base}_raw.png") as im:
        return im.size  # (W, H)


def synthesize_final_u8(raw_image_dir: Path, frame_base: str,
                        target_size: tuple[int, int] | None = None) -> np.ndarray:
    """Synthesise the canonical "final" modality from the bare .jpg.

    Mirrors `goruntuler/scripts/build_transport_dataset.py:141` `_write_final_png`:

        Image.open(jpg).convert("L").resize(size, Image.BILINEAR)

    If `target_size` is None, read it from the `_raw.png` sibling (the same
    source the canonical builder uses). Returns uint8 HxW (H, W). PIL operates
    in (W, H); we transpose conceptually by returning np.asarray (which gives
    HxW with rows=H, cols=W).
    """
    raw_image_dir = Path(raw_image_dir)
    if target_size is None:
        target_size = target_size_from_raw_png(raw_image_dir, frame_base)
    with Image.open(raw_image_dir / f"{frame_base}.jpg") as im:
        gray = im.convert("L")
        if gray.size != target_size:
            gray = gray.resize(target_size, Image.BILINEAR)
        return np.asarray(gray, dtype=np.uint8)


def build_voi_hist_clahe_stack(final_u8: np.ndarray,
                                modalities: Sequence[str] = ("voi", "hist", "clahe")) -> np.ndarray:
    """Build the live channel stack expected by the segmenter.

    Mirrors the per-channel functions in `goruntuler/rf_png_channel_utils.py`:
        voi_channel    → identity (device-applied VOI already baked into jpg)
        hist_channel   → cv2.equalizeHist
        clahe_channel  → cv2.createCLAHE(clipLimit=6.0, tileGridSize=(8,8))

    Verified bit-exact against canonical `_{voi,hist,clahe}.png` for the
    AGZ855 anchor frame (delta 0 across all three channels).

    Returns HxWx3 uint8.
    """
    # Import here to keep module-level imports lean if these helpers
    # aren't always used.
    import sys
    for p in (str(Path(__file__).resolve().parents[2]), str(Path(__file__).resolve().parents[2] / "private" / "goruntuler")):
        if p not in sys.path:
            sys.path.insert(0, p)
    from rf_png_channel_utils import (  # noqa: WPS433
        clahe_channel, hist_channel, voi_channel,
    )

    fn = {"voi": voi_channel, "hist": hist_channel, "clahe": clahe_channel}
    out = []
    for m in modalities:
        if m not in fn:
            raise ValueError(f"unsupported live modality '{m}'; supported: {list(fn)}")
        out.append(fn[m](final_u8))
    return np.stack(out, axis=-1)


def _validation_geometry(in_h: int, in_w: int, meta: BundleMeta) -> dict:
    """Letterbox geometry mapping a native (in_h, in_w) frame into the bundle's
    validation resolution `rrc_size` (= out_h x out_w — what the model actually
    sees), so the forward preprocess and the inverse mask-remap stay in exact
    agreement. Returns the content sub-box + the output size.

    This is the aspect-preserving PAD half of the training transform
    (`fastai_pipeline.create_dataloaders`: `Resize(method="pad",
    pad_mode="zeros")`), landed directly at `rrc_size` (`resize_to` and
    `rrc_size` share aspect, so pad->resize_to->downscale-to-rrc_size ==
    letterbox straight into rrc_size).

    DELIBERATE DEVIATION: training additionally applies a `RandomResizedCropGPU`
    validation center-crop (for the padded_800x640 bundle that crops ~10% off the
    top AND bottom — the skin/near-field and the deepest tissue; the rule is
    `measurement_utils.compute_sample_geometry`). We do NOT replicate that crop:
    muscle_mania's feature compute needs the WHOLE frame (skin at the top anchors
    `fat_to_depth_q50`; the femur sits at the deep edge), and replicating the crop
    blanks those bands and nulls the fat axis. The dominant train/serve gap — the
    aspect DISTORTION that fabricated the spurious RF blob — is removed by the
    pad; the un-cropped 10% bands are a small, documented residual.
    """
    out_h, out_w = int(meta.rrc_size[0]), int(meta.rrc_size[1])
    s = min(out_w / in_w, out_h / in_h)
    content_w = max(1, int(round(in_w * s)))
    content_h = max(1, int(round(in_h * s)))
    pad_left = (out_w - content_w) // 2
    pad_top = (out_h - content_h) // 2
    return {
        "out_h": out_h, "out_w": out_w,
        "content_h": content_h, "content_w": content_w,
        "pad_top": pad_top, "pad_left": pad_left,
    }


def preprocess_for_model(
    stacked_hwc: np.ndarray,
    meta: BundleMeta,
    device: torch.device | str = "cuda",
    *,
    legacy_distort_resize: bool = False,
) -> torch.Tensor:
    """Replicate the validation-time transform, returning a (1, C, H, W) float32
    tensor on `device` ready for `learn.model(x)`.

    Faithful path (default): aspect-preserving **pad** (letterbox) of the frame
    into the bundle's `rrc_size` — the resolution the model was validated at —
    matching training's `Resize(method="pad", pad_mode="zeros")`. See
    `_validation_geometry` for why the validation center-crop is intentionally
    omitted.

    `legacy_distort_resize=True` reproduces the pre-fix behaviour — a single
    `TF.resize` straight to `resize_to` with NO pad — which *distorts* aspect
    whenever the frame's W/H differs from the box's (a tall 256x512 frame into
    the padded_800x640 box, W/H 0.5 -> 0.8, is stretched wide). Kept only so the
    item-4 train/serve-skew sensitivity + the regression test can A/B the paths.
    """
    # HWC uint8 -> CHW float32 in [0, 1]
    x = torch.from_numpy(stacked_hwc).permute(2, 0, 1).float() / 255.0
    if legacy_distort_resize:
        H, W = meta.resize_to
        x = TF.resize(x, [H, W], antialias=True)
    else:
        in_h, in_w = stacked_hwc.shape[:2]
        g = _validation_geometry(in_h, in_w, meta)
        # Fit-inside resize + zero pad -> (out_h, out_w) == rrc_size.
        x = TF.resize(x, [g["content_h"], g["content_w"]], antialias=True)
        pad_right = g["out_w"] - g["content_w"] - g["pad_left"]
        pad_bottom = g["out_h"] - g["content_h"] - g["pad_top"]
        x = TF.pad(x, [g["pad_left"], g["pad_top"], pad_right, pad_bottom], fill=0.0)
    # Normalize per channel.
    mean = torch.tensor(meta.stats_mean).view(-1, 1, 1)
    std  = torch.tensor(meta.stats_std).view(-1, 1, 1)
    x = (x - mean) / std
    return x.unsqueeze(0).to(device)


# ---------------------------------------------------------------------------
# Segmentation-confidence signal (RF Image-Quality QA plan § 5.4)
# ---------------------------------------------------------------------------

def _label_boundary(labels: torch.Tensor) -> torch.Tensor:
    """(h, w) integer label map -> (h, w) bool: True where a 4-neighbour pixel
    carries a different label — the inter-class mask boundary."""
    diff_v = labels[:-1, :] != labels[1:, :]
    diff_h = labels[:, :-1] != labels[:, 1:]
    b = torch.zeros_like(labels, dtype=torch.bool)
    b[:-1, :] |= diff_v
    b[1:, :] |= diff_v
    b[:, :-1] |= diff_h
    b[:, 1:] |= diff_h
    return b


def _dilate_bool(mask: torch.Tensor, radius: int) -> torch.Tensor:
    """Dilate a 2-D bool mask by `radius` pixels (square structuring element)."""
    if radius <= 0:
        return mask
    pooled = F.max_pool2d(
        mask[None, None].float(), kernel_size=2 * radius + 1, stride=1, padding=radius
    )
    return pooled[0, 0] > 0.5


def segmentation_confidence_from_logits(
    logits: torch.Tensor,
    argmax_low: torch.Tensor,
    *,
    boundary_band_px: int = 3,
) -> dict:
    """Reduce a segmenter logits tensor to a per-pixel confidence map + a
    mask-boundary-entropy scalar — the `segmentation_low_confidence` signal
    (RF Image-Quality QA plan § 5.4).

    logits           : (1, n_classes, h, w) float tensor — the model's raw
                       output (the object `predict_mask` already materialises).
    argmax_low       : (h, w) integer tensor — the argmax label map, at the
                       same (h, w) as `logits`.
    boundary_band_px : half-width, in low-res pixels, of the band dilated
                       around the inter-class mask boundary that the scalar
                       averages entropy over.

    Returns a dict:
      'confidence_map'   : (h, w) float32 — per-pixel max softmax probability
                           (1.0 = the model is certain of the label it picked
                           here; the heatmap the § 8.3 UI renders).
      'entropy_map'      : (h, w) float32 — per-pixel Shannon entropy (nats) of
                           the class distribution; the raw uncertainty field.
      'boundary_entropy' : float — mean entropy over the boundary band; the
                           scalar the `segmentation_low_confidence` detector
                           (Threshold-tier B) calibrates against (§ 5.4).
      'boundary_band_px' : int — the band half-width used.
      'n_classes'        : int.

    Pure — no model, no I/O — so it is unit-testable on a synthetic logits
    tensor. The reduction is a softmax + an entropy sum + a max-pool, all cheap
    at frame scale and running on whatever device `logits` is on, so § 5.4's
    "reduce on-GPU before `out` leaves scope" holds.
    """
    probs = torch.softmax(logits, dim=1)                 # (1, C, h, w)
    n_classes = int(probs.shape[1])
    # Shannon entropy per pixel, in nats. clamp_min keeps the log finite at
    # p == 0 (0 * log 0 -> 0, the entropy convention).
    entropy = -(probs * probs.clamp_min(1e-12).log()).sum(dim=1).squeeze(0)  # (h, w)
    confidence = probs.amax(dim=1).squeeze(0)            # (h, w) — max class prob
    boundary = _label_boundary(argmax_low)
    band = _dilate_bool(boundary, int(boundary_band_px))
    band_vals = entropy[band]
    boundary_entropy = float(band_vals.mean()) if band_vals.numel() else 0.0
    return {
        "confidence_map": confidence.detach().cpu().numpy().astype(np.float32),
        "entropy_map": entropy.detach().cpu().numpy().astype(np.float32),
        "boundary_entropy": boundary_entropy,
        "boundary_band_px": int(boundary_band_px),
        "n_classes": n_classes,
    }


def _remap_model_output_to_native(
    low: np.ndarray, g: dict, native_hw: tuple[int, int], resample
) -> np.ndarray:
    """Invert `_validation_geometry`: map a model-output field (at rrc_size /
    out_h x out_w) back to the native (in_h x in_w) frame it came from.

    Undo the letterbox: slice out the zero-padded content sub-box, then resize it
    to native. Keeps the returned mask spatially aligned with the native frame
    (and with the intensity image), which the downstream mask-space
    spacing -> mm conversion relies on.
    """
    native_h, native_w = native_hw
    content = low[g["pad_top"]:g["pad_top"] + g["content_h"],
                  g["pad_left"]:g["pad_left"] + g["content_w"]]
    out = Image.fromarray(content).resize((native_w, native_h), resample=resample)
    return np.asarray(out, dtype=low.dtype)


def predict_mask(
    learn,
    stacked_hwc: np.ndarray,
    meta: BundleMeta,
    device: torch.device | str = "cuda",
    *,
    confidence_out: dict | None = None,
    boundary_band_px: int = 3,
    legacy_distort_resize: bool = False,
) -> np.ndarray:
    """Run inference and return the argmax mask at native (input HWC) resolution.

    Faithful path (default): the model operates at `rrc_size` (the validation
    output resolution) on a pad-then-center-cropped input; we invert that
    geometry (`_remap_model_output_to_native`) so the returned mask aligns with
    the native frame. `legacy_distort_resize=True` pairs the distorting forward
    resize with the pre-fix inverse (a plain nearest resize of the model field,
    which was at `resize_to`, straight back to native) — the A/B for the item-4
    sensitivity.

    `confidence_out` (the optional-out-parameter idiom of `final_u8_out` in
    `run_pipeline._load_or_predict_masks`): when a dict is passed in, it is
    populated with the segmentation-confidence signal (RF Image-Quality QA plan
    § 5.4) — the keys `segmentation_confidence_from_logits` returns, plus
    `confidence_map_native`, the per-pixel confidence map resized to the native
    HxW so it aligns with the returned mask (the § 8.3 UI heatmap). When omitted
    (None) the softmax / entropy reduction is skipped entirely.
    """
    x = preprocess_for_model(stacked_hwc, meta, device=device,
                             legacy_distort_resize=legacy_distort_resize)
    learn.model.eval()
    with torch.no_grad():
        out = learn.model(x)
    argmax_low = out.argmax(dim=1).squeeze(0)
    pred_low = argmax_low.cpu().numpy().astype(np.uint8)
    native_H, native_W = stacked_hwc.shape[:2]
    geom = None if legacy_distort_resize else _validation_geometry(native_H, native_W, meta)
    if confidence_out is not None:
        with torch.no_grad():
            signal = segmentation_confidence_from_logits(
                out, argmax_low, boundary_band_px=boundary_band_px
            )
        # Map the per-pixel map to the native HxW so it aligns with the mask —
        # bilinear, since the confidence map is continuous (unlike the argmax).
        if geom is None:
            conf_native = np.asarray(Image.fromarray(signal["confidence_map"]).resize(
                (native_W, native_H), resample=Image.BILINEAR), dtype=np.float32)
        else:
            conf_native = _remap_model_output_to_native(
                signal["confidence_map"], geom, (native_H, native_W), Image.BILINEAR)
        confidence_out.update(signal)
        confidence_out["confidence_map_native"] = conf_native.astype(np.float32)
    # Map the argmax mask back to native HWC shape using nearest.
    if geom is None:
        pil = Image.fromarray(pred_low).resize((native_W, native_H), resample=Image.NEAREST)
        return np.asarray(pil, dtype=np.uint8)
    return _remap_model_output_to_native(pred_low, geom, (native_H, native_W), Image.NEAREST)


def model_output_channels(learn, in_channels: int = 3, device: torch.device | str = "cuda") -> int:
    """Probe the learner's model for output channel count via a synthetic forward pass."""
    learn.model.eval()
    # 128x128 is enough — smp.DeepLabV3Plus is fully convolutional.
    x = torch.zeros(1, in_channels, 128, 128, device=device)
    with torch.no_grad():
        out = learn.model(x)
    return int(out.shape[1])


def dice(pred_mask: np.ndarray, true_mask: np.ndarray, class_id: int) -> float:
    """Binary Dice for a single class — symmetric, in [0, 1]."""
    p = (pred_mask == class_id)
    t = (true_mask == class_id)
    denom = p.sum() + t.sum()
    if denom == 0:
        return 1.0  # both empty — perfect by convention; flag at call site if needed
    return float(2 * np.logical_and(p, t).sum() / denom)

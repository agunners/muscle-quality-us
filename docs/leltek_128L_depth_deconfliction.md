# Leltek 128L — Depth & Artifact Deconfliction Reference (Round-3)

**What this is.** A lookup that recovers the *depth setting* of any captured artifact (sweep or still) from its intrinsic properties, and the logic that thereby allocates each artifact in a one‑study‑per‑patient acquisition to its measurement — **without relying on capture order.** Derived from the backup cohort (DICOM PixelSpacing + RawImages jpg dimensions), 2026‑06‑02.

**What it serves.** Round‑3 records **one device‑study per patient**, holding both legs and several captures (sweep + FEMUR at 7 cm; RF‑1/RF‑2 at 5 cm), with **no device‑level label** of which artifact is which. This table lets the ingestion code decide, per artifact, its depth — and from depth + clip‑length + `.dcm`‑presence, its identity (sweep / FEMUR / RF).

**Scope: 128L only.** Round‑3 uses the Leltek **128L (L8.5)** linear probe exclusively. The legacy backup was *mixed* — ≈265 studies on 128L + ≈44 on 128LH (L15). The probe is **not** in the DICOM tags (only burned into the image pixels + recoverable from the FOV). The FOV constant below (`width × depth ≈ 1967`) is **128L‑specific**; 128LH is ≈1310.

## Mechanism

- **`Rows` is fixed at 585** on this device → depth is encoded in the vertical pixel spacing:
  **`depth_cm = 585 × dy_mm / 10`**, i.e. **`dy_mm = depth_cm × 0.017094`**.
- `dy` (DICOM `PixelSpacing[0]`) exists on **stills only** (the SAVE IMAGE `.dcm`). Sweeps (MAKE VIDEO only) have no `.dcm` → use the jpg.
- The RawImages **`.jpg` height is fixed at 512**; **`width = FOV_const / depth_cm`**, `FOV_const ≈ 1967` for 128L. So **`jpg_width_px ≈ 1967 / depth_cm`** — the universal depth signal that also covers the sweeps.

## Draft table (128L, from backup — confirm the exact device steps on the first pilot)

| Round‑3 setting | device step (cm) | dy (mm) | jpg width (px) |
| :-- | :-- | :-- | :-- |
| **RF — "5 cm"** | ~4.9 | **~0.084** | **~399** |
| **sweep + FEMUR — "7 cm"** | ~6.9 | **~0.118** | **~285** |

Full observed 128L bands (depth_cm → dy_mm → jpg_w_px): 4.0→0.068→499 · 4.9→0.084→399 · 5.9→0.101→332 · 6.9→0.118→285 · 7.9→0.135→249 · 8.9→0.152→222 · 9.9→0.169→199 · 10.9→0.186→181.

> The device snaps to discrete steps ending in ~.9; the "5 cm" / "7 cm" buttons most likely render ~4.9 / ~6.9. **Confirm on the first pilot study and lock the two values.**

## Artifact allocation (per study, order‑independent)

1. **Sweep** = long clip, no `.dcm` → depth is 7 cm by protocol (cross‑check jpg width ≈ 285).
2. **FEMUR still** = short clip + `.dcm`, **dy ≈ 0.118 / width ≈ 285** (7 cm).
3. **RF‑1 / RF‑2** = short clips + `.dcm`, **dy ≈ 0.084 / width ≈ 399** (5 cm); the two are the lift‑replace pair.
4. **Sweep vs FEMUR** (both 7 cm) split by clip‑length / `.dcm`‑presence, *not* depth.
5. **Left vs right leg** is a separate problem (probe‑marker convention + segmenter VL/VM, or explicit recording) — not in this table.

## Separation margin (5 vs 7 cm)

dy: 0.084 vs 0.118 (gap 0.034 mm). jpg width: 399 vs 285 (gap 114 px). Both cleanly separable with one fixed probe; no overlap.

## Caveats

- 128L only — the `1967` FOV constant does not hold for 128LH; mixing probes re‑introduces the confound.
- Backup‑derived; the exact "5"/"7" device steps need one pilot anchor to lock.
- The probe identity itself is not in the DICOM — Round‑3 records it in the `Probe Model + Serial` datasheet column.

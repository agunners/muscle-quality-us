# RF Frame Image-Quality Contract — v0

> **SUPERSEDED 2026-05-20** — replaced by [`rf_qa_canonical_plan_v0_1.md`](rf_qa_canonical_plan_v0_1.md), the single canonical RF Image-Quality QA/QC plan, which absorbs this document's load-bearing content in full. Preserved in place as audit trail; see the canonical plan's § 16 CHANGELOG and Appendix A.1.

*Step-0 design artifact for the RF frame QA/QC initiative. See [`rf_frame_qa_qc_brainstorm_en_tr.md`](rf_frame_qa_qc_brainstorm_en_tr.md) for the 6-step build order this contract underpins. This document defines: what gets graded, how, with what reason codes, and how the result surfaces to the operator. No code touched here — this is the half-day design pass that everything else (Steps 1–6) keys off.*

*Status: draft v0. Author: Claude Opus 4.7 (1M context) on session of 2026-05-18, ratifying decisions made by Akadir in-session. Pending: Akadir review + Anonaao sanity-check on the failure-mode catalogue.*

---

## How to read this document

| You are | Read | Skip-OK |
|---|---|---|
| **Akadir (design review)** | § 0, § 3, § 6, § 7 | § 4, § 5 on first pass |
| **Anonaao (clinical authority)** | § 0, § 1, § 2, § 3, § 6 | § 4, § 5 (engineering) |
| **Implementer (future Claude/me)** | All of it, straight through | nothing |
| **Paper reviewer (future)** | § 0–§ 3, § 6, § 7 | § 5 (engineering only) |

---

## § 0 — Scope, discipline, and ratified decisions

### What this contract covers

A **per-frame image-quality grading system** for RF (rectus femoris) ultrasound frames, applied at the **assessment phase** — *after* a sweep has been captured and stored, *before* (or during) the operator's Phase-3 frame selection. The contract answers one question per frame per measurement:

> *"Is this frame good enough that the assessment pipeline can extract* measurement-X *from it reliably?"*

The answer is structured: a per-frame tuple of reason-coded flags, plus a per-measurement validity tier derived from those flags.

### What this contract does NOT cover

- **Muscle health quality.** The frame is being graded on *measurability*, not on what the muscle looks like clinically. A frame can be high-quality and the muscle clinically poor; the contract grades only the former.
- **Acquisition-time QA.** The same flag taxonomy *could* run at scan time (push checks back to the probe, never save a bad frame). That is a deliberate strategic extension — see Brainstorm § 5 Item 2 — and is **out of scope for v0**. The contract is designed so the same detectors can later be reused at scan time without re-architecture.
- **Multi-frame / sweep-level consistency.** `frame_rf_consistency_px` is already computed post-hoc as a sensitivity metric ([`labeling_app/measurements.py:1168`](../labeling_app/measurements.py#L1168)) and Brainstorm Step 4 promotes it from sensitivity to gate. That promotion is downstream — this contract is per-frame.
- **Auto-recommendation of N best frames.** The diversity-aware recommender — pick 3–5 frames that are individually good AND collectively spread across the muscle — is named as a Step-7 future direction (§ 8). It depends on this contract but is not part of it.

### Ratified strategic decisions (Akadir, 2026-05-18 in-session)

The Brainstorm's § 5 surfaced five strategic forks; four are ratified, one is pending.

| # | Fork | Decision | Reasoning |
|---|---|---|---|
| 1 | One scalar score vs. per-measurement flags | **Per-measurement flags** | Measurements fail independently (see § 1 dependency table). The QMT precedent already commits to per-measurement validity; uniform pattern. |
| 2 | Assessment-gating vs. acquisition-time scope | **Assessment-gating only for v0** | Acquisition-time has a different latency budget and a different consumer (the scanner operator at the bedside, not the post-hoc labeller). Detectors are designed to be reusable but the v0 surface is assessment only. |
| 3 | Run QA on raw `.jpg` vs. synthesized `_final.png` | **`_final.png` (the "final" modality)** | Intensity features (`rf_echo`, `int_cv_within`) are computed on the synthesized grayscale ([`scripts/preflight_helpers/segmenter.py:129`](../scripts/preflight_helpers/segmenter.py#L129) — `jpg → convert("L") → resize`). QA must run on the same array the measurement uses or it audits the wrong image. Per [[feedback_final_png_modality]]. |
| 4 | Show only operator-graded frames vs. auto-grade everything before user touches the picker | **Auto-grade all frames; surface visibly before operator selects** | The operator should see "what's viable and what's not" *before* committing to a selection. Mirrors the existing discipline at [`p03_frame_picker.py:38`](../labeling_app/assessment_pages/p03_frame_picker.py#L38) — status display, never auto-pick. |
| 5 | Operator-picked vs. whole-sweep label capture for Step 1 | **Picked + 5 sweep-stratified unpicked** | Akadir ratified 2026-05-18: sample 1 unpicked frame from each of 5 equal temporal zones of the sweep. Guarantees spatial spread; prevents random sampler from clustering; anchors the future Step-7 diversity-aware recommender. ~30 sec/study extra labelling cost. |
| 6 | Is subcutaneous fat segmentation available for a Tier-0 `subq_fat_compressed` detector? | **Yes — `LABEL_FAT = 8`** | [`labeling_app/measurements.py:36`](../labeling_app/measurements.py#L36); palette `model_/label_palette.json:91-101` ("fat"); already used in `muscle_fat_ratio()` at line 1164. Subq compression detector moves from Tier-1/deferred to Tier-0/deterministic. |

### Sweep stratification — implementation note (Decision 5)

The 5 unpicked-frame samples are drawn as follows:

```python
def stratified_unpicked_sample(
    all_frame_indices: list[int],
    operator_picked: set[int],
    *,
    n_per_zone: int = 1,
    n_zones: int = 5,
    rng: random.Random,
) -> list[int]:
    """Divide the sweep into n_zones equal temporal bins; sample
    n_per_zone unpicked frames from each. Returns up to n_per_zone * n_zones
    indices (fewer if a zone has no unpicked frames)."""
    total = len(all_frame_indices)
    bin_size = total / n_zones
    selected: list[int] = []
    for zone in range(n_zones):
        lo, hi = int(zone * bin_size), int((zone + 1) * bin_size)
        zone_unpicked = [i for i in all_frame_indices[lo:hi] if i not in operator_picked]
        if zone_unpicked:
            selected.extend(rng.sample(zone_unpicked, min(n_per_zone, len(zone_unpicked))))
    return selected
```

**Caveat — monotonic sweep assumption.** Temporal zones equal spatial zones *only if* the operator sweeps the probe monotonically (proximal→distal or lateral→medial). If the operator backtracks mid-sweep, temporal zone ≠ spatial zone. Most sweeps are monotonic so this is fine for v0; if Step-2's cohort run reveals significant backtracking, the stratifier may need a re-sort by inferred probe position (which itself would be Step-7 dependency work).

### Open forks (post-ratification)

| # | Open question | Default lean | Needs Akadir call |
|---|---|---|---|
| OF-2 | When to run Tier-1 (confidence map) work — sequential after Step 2 cohort run, or parallel? | Sequential (Step 2 results may reveal Tier-0 is enough for most failure modes) | Yes |

### The four transparency rules (apply to all UI surfaces)

These rules are non-negotiable design constraints, ratified in-session. They appear again in § 6 with concrete UI sketches; they live here at the top of the contract because they bind every downstream decision.

1. **Never hide a frame for QA reasons.** Operator sees every frame in the picker. Filtering is opt-in (operator-driven sort/filter controls), never default. Silent filtering produces unauditable decisions.
2. **Always show the "why."** Every flag carries a hover/click rationale: which detector fired, which threshold tripped, which measurement(s) the flag taints. No black-box badges.
3. **Always allow override.** Operator can pick a flagged-as-invalid frame anyway; the override is logged and becomes the training data for the Step-5 learned grader. Disagreement is data, not noise.
4. **Pre-fill, don't pre-decide.** Phase-4's three human gates get *suggested* values from the contract with reasoning shown next to each. The human confirms or overrides; the suggestion is a hypothesis, not a verdict.

---

## § 1 — The measurement landscape

The assessment pipeline currently produces this set of per-frame measurements ([`scripts/preflight_helpers/feature_compute.py:78–112`](../scripts/preflight_helpers/feature_compute.py#L78), `FrameFeatures` dataclass — VI fields removed post-`960e198`):

### Headline four (the demo card carries these — per `final_execution_plan_01.md` v3 P5)

| Measurement | What it is | Computation root |
|---|---|---|
| `qmt_mm` + `star_ratio` | Quadriceps-muscle thickness (fascia→femur) + STAR (QMT / BMI) | `measurements.py:573–595` |
| `full_thickness_q50_mm` | Median full-RF thickness over the eroded ROI | white-paper block, `feature_compute.py:99` |
| `int_cv_within` ("Fascicular contrast") | Coefficient of variation of intensity inside the eroded ROI | white-paper block, `feature_compute.py:104` |
| `rf_thickness_mm` | RF thickness at the centre column | `measurements.py:compute_all_metrics` |

### Secondary (collapsible card content)

`muscle_fat_ratio`, `rf_area_mm2`, `full_area_mm2`, `full_max_thickness_mm`, `area_retained_fraction_after_erosion`, plus the intensity supporting stats `rf_echo`, `int_mean`, `int_std`, `int_median`, `int_entropy`.

### Structure dependencies — what each measurement physically requires

This table is the core of the contract. The "structures" column lists the *image structures* whose presence and quality the measurement depends on. The "failure modes that kill it" column comes from the matrix in § 2. *A frame is good* for measurement X *iff every structure in X's row is intact.*

| Measurement | Structures required | Failure modes that kill it |
|---|---|---|
| `rf_thickness_mm` | superficial + deep RF fascia (aponeuroses) | oblique_plane, anisotropy_dropout_superficial / _deep, motion_blur, edge_clip_vertical |
| `rf_area_mm2`, `full_area_mm2` | complete in-frame RF cross-section + fascia | edge_clip_lateral, edge_clip_bottom, edge_clip_top, oblique_plane, anisotropy_dropout_* |
| `full_thickness_q50_mm`, `full_max_thickness_mm` | both fascia continuous across the eroded ROI width | anisotropy_dropout_*, oblique_plane, motion_blur, edge_clip_vertical |
| `area_retained_fraction_after_erosion` | mask plausibility (downstream of segmentation quality) | (degenerate masks — touches_edge, is_empty already gate) |
| `qmt_mm` | RF superficial fascia + femur cortex + acoustic shadow in central zone | femur_out_of_frame, femur_off_central_zone, oblique_plane, anisotropy_dropout_*, motion_blur |
| `star_ratio` | (= `qmt_mm` / BMI) — inherits all `qmt_mm` failures + needs valid BMI | inherits qmt_mm flags + demographic-missing (not an image-quality flag) |
| `muscle_fat_ratio` | subcutaneous fat layer present + uncompressed + RF mask | subq_fat_compressed, edge_clip_top, oblique_plane |
| `rf_echo`, `int_mean`, `int_median` | ROI grayscale fidelity, no saturation/dark-clip | saturation_high, dark_clip_high, tgc_banding |
| `int_cv_within`, `int_std`, `int_entropy` | ROI grayscale fidelity + no homogenization artifact | saturation_high, dark_clip_high, tgc_banding, motion_blur (smears texture) |

**Observation 1 — the geometry block and the intensity block fail on disjoint flags.** A frame can have perfect intensity (no saturation, no TGC banding) but a missing femur cortex — `rf_echo` survives, `qmt_mm` dies. This is why the per-measurement structure is non-negotiable: pooling these into one scalar throws away the most useful information.

**Observation 2 — `qmt_mm` is the most fragile measurement.** It depends on three structures (RF fascia, femur cortex, acoustic shadow) all being present and clean. The QMT validity-flag system at `measurements.py:51` already encodes much of this; the new contract generalizes the pattern.

**Observation 3 — the orthogonal failure mode: `wrong_site`.** If the operator scanned the wrong muscle (e.g., VL instead of RF), *every* measurement is silently wrong. This needs its own top-priority flag because no per-measurement detector catches it — the anatomy looks plausible, just of the wrong thing.

---

## § 2 — Failure-mode catalogue

Each failure mode below is named, given a physical mechanism, a visual signature the operator can recognise, the measurements it taints, and the detector class (per Brainstorm § 3) that would fire it. Detector design specs are in § 3 alongside each reason code.

| # | Failure mode | Physical mechanism | Visual signature | Measurements tainted | Detector class |
|---|---|---|---|---|---|
| F1 | **wrong_site** | Scanned VL/VM/biceps femoris instead of RF | Anatomy doesn't match RF mid-thigh: muscle belly shape, fascia angle, neighbouring structures all "off" | ALL measurements, silently | D (structural, hardest) + human |
| F2 | **edge_clip_lateral** | Muscle belly extends past the left/right edge of the frame | RF mask touches the lateral frame border | `rf_area_mm2`, `full_area_mm2`, `rf_thickness_mm` (if column is the clipped one) | B (deterministic — already exists as [`touches_edge`](../scripts/preflight_helpers/post_processing.py#L106)) |
| F3 | **edge_clip_vertical** | Probe pressed shallow or pulled deep — fascia near top/bottom edge | Either superficial fascia near top edge OR deep fascia near bottom edge (within N px) | `rf_thickness_mm`, `full_thickness_*`, `qmt_mm` if femur is then off-bottom | B (deterministic — extend `touches_edge` with directional info) |
| F4 | **femur_out_of_frame** | Probe too shallow, or wrong depth setting | No bright continuous cortical line + clean shadow visible anywhere below the deep fascia | `qmt_mm`, `star_ratio` | D (structural — already encoded as `femur_absent` flag in `QMT_VALIDITY_FLAG_PRIORITY`) |
| F5 | **femur_off_central_zone** | Femur present but off to one side of the frame | Femur cortex line + shadow exist but are in the lateral 25% of the frame, not the central 50% | `qmt_mm` quality (degrades to proxy), `star_ratio` | D (already encoded as `outside_central_zone`) |
| F6 | **oblique_plane** | Probe rotated off the muscle's long axis | Fascia lines converge / fade across width; fascicular striations out of plane | `rf_thickness_*`, `qmt_mm`, `rf_area_*`, `full_*`, `int_cv_within` (smears texture) | D (structural — Radon/Hough for fascia parallelism) + existing `fascia_slope_high` |
| F7 | **anisotropy_dropout_superficial** | Beam tilt: superficial fascia loses specular reflection at one segment of width | One contiguous segment of the superficial fascia darkens / disappears (the rest is bright) | `rf_thickness_mm` (if column hits dropout), `rf_area_mm2`, `full_*` | C (confidence map inside the superficial fascia band) |
| F8 | **anisotropy_dropout_deep** | Same as F7 but on the deep fascia / RF-VI interface | Same as F7 but on the deep edge | `rf_thickness_mm`, `qmt_mm` (if deep edge is unclear), `full_*` | C (confidence map in the deep fascia band) |
| F9 | **saturation_high** | Gain too high or strong specular reflector — pixels clipped at 255 | Flat-white regions; histogram piled at the max | `rf_echo`, `int_mean`, `int_std`, `int_cv_within`, `int_median`, `int_entropy` | B (deterministic — fraction of pixels above N) |
| F10 | **dark_clip_high** | Gain too low or deep attenuation — pixels clipped at 0 | Flat-black regions inside or under the muscle | `rf_echo`, `int_*`, and (if it touches the deep fascia band) → propagates to anisotropy_dropout_deep | B (deterministic — fraction below N) |
| F11 | **tgc_banding** | TGC mis-set: depth-dependent gain produces horizontal bright/dark bands | Horizontal stripes of obviously different brightness, not following anatomy | `rf_echo`, `int_*` (depth-dependent bias) | B (deterministic — row-wise brightness profile non-monotonic / banded) |
| F12 | **motion_blur** | Probe or patient moved during frame acquisition | Fascia lines smeared (thick + soft, not crisp + thin); fascicles smeared | All geometry (border detection), `int_cv_within` (smears texture) | B (deterministic — variance-of-Laplacian / Tenengrad below threshold) |
| F13 | **subq_fat_compressed** | Operator pressed too hard on the probe | Subcutaneous fat layer is thin and flat, or absent | `muscle_fat_ratio` (biased) — all thicknesses underestimated to lesser degree | B–D (deterministic on fat-layer thickness from segmentation mask, IF the segmentation has a fat class) |
| F14 | **air_gap_reverberation** | Poor coupling — air bubble between probe and skin | Reverberation bands at the top of the frame; dark wedge | Signal loss everywhere in the affected column | B (deterministic — vertical brightness profile shows reverberation pattern) |
| F15 | **deep_attenuation_high** | Deep / large patient — beam attenuates before reaching deep structures | Deep aponeurosis and femur are faint, low-contrast | `qmt_mm`, deep `full_*` | C (confidence map shows low confidence in deep regions) |
| F16 | **segmentation_low_confidence** | The segmentation model is uncertain about the mask | (no human-visible signature — it's a model-internal signal) | All geometry (mask-derived) | G (pull from segmentation model uncertainty — see Brainstorm § 3 Class G) |

The matrix above defines the universe of v0 reason codes. Below are sketches of how each gets detected and gated.

---

## § 3 — Reason-code taxonomy

The taxonomy is a frozen priority tuple, modelled on `QMT_VALIDITY_FLAG_PRIORITY` ([`labeling_app/measurements.py:51`](../labeling_app/measurements.py#L51)):

```python
# To live in labeling_app/measurements.py adjacent to QMT_VALIDITY_FLAG_PRIORITY.
IMAGE_QUALITY_FLAG_PRIORITY = (
    # Tier-0: orthogonal — kills everything
    "wrong_site",
    # Tier-0: structural — already partially encoded for QMT
    "femur_out_of_frame",
    "femur_off_central_zone",
    # Tier-0: geometric / framing
    "edge_clip_lateral",
    "edge_clip_vertical",
    # Tier-0: gross intensity
    "saturation_high",
    "dark_clip_high",
    "tgc_banding",
    # Tier-0: gross sharpness
    "motion_blur",
    # Tier-0: coupling
    "air_gap_reverberation",
    # Tier-1: ultrasound-specific (Class C)
    "oblique_plane",
    "anisotropy_dropout_superficial",
    "anisotropy_dropout_deep",
    "deep_attenuation_high",
    # Tier-1: anatomical (Class D)
    "subq_fat_compressed",
    # Tier-2: model-derived
    "segmentation_low_confidence",
)
```

Priority order matters for two things: (a) the "dominant reason" surfaced in the UI when a frame has multiple flags (highest-priority flag shown first), and (b) future debugging when conflicting flags require precedence.

### Per-code specification

Each reason code below carries: **what it means** physically; **how the detector works** (Tier-0/1/2 spec); **what threshold** (where applicable, with a sensible default to be calibrated empirically); **which measurements it taints**; **override semantics** (when the operator overrides this flag, what does that override mean clinically).

#### `wrong_site` (Tier-0/D, but mostly human)

**Meaning.** The frame is not of RF — could be VL, VM, biceps femoris, or non-thigh entirely.

**Detector.** No reliable v0 deterministic detector. Class-D structural classifiers (anatomical pattern matching) require labelled training data we don't have. Strategy for v0: **human-only flag** raised in Phase-4. Tier-0 emits *nothing* for this; the operator's "Use for analysis: No" with reason `wrong_site` is the only signal. Future (Step 5+): a learned anatomical classifier trained on the labelled set.

**Threshold.** N/A.

**Measurements tainted.** All.

**Override.** Override semantics: "Operator confirms the anatomy is RF despite the system's uncertainty." For v0 this is the default — the system always says "I don't know" — so override-vs-not is moot until the learned classifier exists.

#### `femur_out_of_frame` (Tier-0/D)

**Meaning.** No femur cortex line + acoustic shadow visible in the lower portion of the frame. The frame is unusable for `qmt_mm` and therefore `star_ratio`.

**Detector.** *Already exists* as `femur_absent` in `QMT_VALIDITY_FLAG_PRIORITY` ([`measurements.py:51`](../labeling_app/measurements.py#L51)). Detection: search for a near-horizontal high-intensity line below the RF mask with an acoustic-shadow region beneath. The existing detector is column-wise; v0 lifts the column-wise verdict to a frame-level flag when no column finds femur.

**Threshold.** Already calibrated for QMT pipeline. Reuse.

**Measurements tainted.** `qmt_mm`, `star_ratio`.

**Override.** Operator confirms femur is present despite detector miss (rare — typically the detector is right). Override means "Use this frame for QMT anyway with caveat".

#### `femur_off_central_zone` (Tier-0/D)

**Meaning.** Femur present but in the lateral 25% of the frame, not the central 50% where the perpendicular fascia-to-femur measurement is reliable.

**Detector.** *Already exists* as `outside_central_zone` in `QMT_VALIDITY_FLAG_PRIORITY`. Detection: femur column position vs. central-zone column range.

**Threshold.** Already calibrated. Central zone = [W/4, 3W/4] historically; verify in code.

**Measurements tainted.** `qmt_mm` (degrades from `standard_qmt` to `femur_supported_proxy`), `star_ratio`.

**Override.** Operator accepts the proxy QMT despite off-centre femur. Already the existing default behaviour (tier-2 in aggregation).

#### `edge_clip_lateral` / `edge_clip_vertical` (Tier-0/B)

**Meaning.** Muscle mask touches the left/right (lateral) or top/bottom (vertical) frame edge — measurements that depend on the full cross-section or on fascia near the edge get truncated.

**Detector.** Build on existing `touches_edge` at [`scripts/preflight_helpers/post_processing.py:106`](../scripts/preflight_helpers/post_processing.py#L106). Currently a single boolean; v0 splits into directional info:

```python
def edge_clip_flags(mask: np.ndarray) -> set[str]:
    h, w = mask.shape
    flags = set()
    if (mask[:, 0] > 0).any() or (mask[:, -1] > 0).any():
        flags.add("edge_clip_lateral")
    if (mask[0, :] > 0).any() or (mask[-1, :] > 0).any():
        flags.add("edge_clip_vertical")
    return flags
```

**Threshold.** Boundary contact = any pixel of the muscle mask in the outermost N-pixel ring. Default N=1 (current behaviour); calibration may push to N=2–5 to also flag "almost-clipping."

**Measurements tainted.** Lateral: `rf_area_mm2`, `full_area_mm2`, `rf_thickness_mm` if column is the clipped one. Vertical: `rf_thickness_mm`, `full_thickness_*`, `qmt_mm` if femur is then off-bottom.

**Override.** Operator confirms the clipped region is anatomically empty (e.g., the muscle ends within the frame; the mask touching the edge is incidental). Rare.

#### `saturation_high` / `dark_clip_high` (Tier-0/B)

**Meaning.** Pixels clipped at 255 (saturation) or 0 (dark clip). Corrupts intensity statistics and (for saturation) makes border detection unreliable in saturated regions.

**Detector.**

```python
def intensity_clip_flags(img_gray: np.ndarray, rf_mask: np.ndarray) -> set[str]:
    roi = img_gray[rf_mask > 0]
    if roi.size == 0:
        return set()
    sat_frac = (roi >= 250).sum() / roi.size
    dark_frac = (roi <= 5).sum() / roi.size
    flags = set()
    if sat_frac > 0.02:
        flags.add("saturation_high")
    if dark_frac > 0.02:
        flags.add("dark_clip_high")
    return flags
```

**Threshold.** 2% of ROI pixels at the clip rail. Borrowed from the QMT muscle-occupancy 2% tolerance precedent (memory: [[project_qmt_provenance_gate]] — "2% contamination tolerance"). Calibration may move this; the value is a sensible starting point.

**Measurements tainted.** `rf_echo`, `int_*`. Also: if saturation hits the fascia band, can mask anisotropy dropout — interactive with F7/F8.

**Override.** Operator accepts the intensity bias for non-intensity measurements (geometry-only use of the frame). UI should let operator restrict the override to specific measurements ("use this frame for geometry only, not for intensity").

#### `tgc_banding` (Tier-0/B)

**Meaning.** Time-gain compensation mis-set: horizontal bright/dark bands not following anatomy. Depth-dependent gain bias contaminates intensity stats.

**Detector.** Row-wise mean brightness profile of the ROI columns. Compute:

```python
def tgc_banding_score(img_gray: np.ndarray, rf_mask: np.ndarray) -> float:
    # Mean brightness per row, restricted to columns intersecting the RF mask
    cols_with_mask = (rf_mask > 0).any(axis=0)
    row_means = img_gray[:, cols_with_mask].mean(axis=1)
    # A clean profile should be smoothly monotonic (decreasing with depth, ideally).
    # Compute second-difference variance: a banded profile has high curvature variance.
    if len(row_means) < 3:
        return 0.0
    second_diff = np.diff(row_means, n=2)
    return float(second_diff.std())
```

**Threshold.** TBD — needs empirical calibration on the cohort. Suggested starting point: flag when second-difference σ > 3× the smooth-profile baseline σ.

**Measurements tainted.** `rf_echo`, `int_*` (depth-dependent bias).

**Override.** Operator confirms the apparent banding is anatomical (e.g., a real fascial layer) and not gain-induced. Edge case.

#### `motion_blur` (Tier-0/B)

**Meaning.** Probe or patient motion during frame capture smears fascia and fascicles, makes border detection unreliable.

**Detector.** Variance-of-Laplacian in the ROI:

```python
def motion_blur_score(img_gray: np.ndarray, rf_mask: np.ndarray) -> float:
    lap = cv2.Laplacian(img_gray, cv2.CV_64F)
    roi_lap = lap[rf_mask > 0]
    return float(roi_lap.var())
```

**Threshold.** Calibrate on the cohort: bottom 10th percentile of variance → flag. (Variance-of-Laplacian is unit-free up to scale; calibration must be done per-cohort.)

**Measurements tainted.** All geometry (border detection), `int_cv_within` (smears texture).

**Override.** Operator confirms the apparent blur is anatomical (smooth muscle vs. fibrotic muscle — but this should be rare for RF).

#### `air_gap_reverberation` (Tier-0/B)

**Meaning.** Air between probe and skin produces vertical reverberation artifacts at the top of the frame; signal loss in the affected column.

**Detector.** Top-of-frame brightness pattern + periodic spacing detection. Sketch:

```python
def air_gap_score(img_gray: np.ndarray) -> float:
    # In the top N rows (skin contact zone), look for periodic bright bands
    # at fixed depth intervals — the signature of multiple-reverberation.
    top = img_gray[:50, :]
    # FFT along the row axis to find dominant frequency
    fft_mag = np.abs(np.fft.fft(top.mean(axis=1)))
    return float(fft_mag[1:25].max() / fft_mag[0])  # normalized
```

**Threshold.** TBD — empirical calibration needed.

**Measurements tainted.** All measurements in the affected column.

**Override.** Rare — air-gap signature is usually obvious.

#### `oblique_plane` (Tier-1/D)

**Meaning.** Probe rotated off the muscle's long axis. Fascia lines converge / fade across image width; fascicular striations are out of plane.

**Detector.** Two complementary signals:
- *Existing:* `fascia_slope_high` in `QMT_VALIDITY_FLAG_PRIORITY` already flags slope of the deep fascia line. Reuse.
- *New:* Radon-transform-based parallelism check — compute the dominant orientation of the superficial fascia and the deep fascia; flag when their orientations diverge by > N degrees (suggesting probe rotation rather than anatomy).

**Threshold.** `fascia_slope_high` is already calibrated (`QMT_FASCIA_SLOPE_MAX_ABS = 0.25` at [`measurements.py:50`](../labeling_app/measurements.py#L50)). New Radon-based check needs calibration.

**Measurements tainted.** `rf_thickness_*`, `qmt_mm`, `rf_area_*`, `full_*`, `int_cv_within`.

**Override.** Operator confirms the muscle anatomy genuinely has a non-horizontal fascia at that location (rare in RF mid-thigh).

#### `anisotropy_dropout_superficial` / `anisotropy_dropout_deep` (Tier-1/C)

**Meaning.** A contiguous segment of one aponeurosis darkens / disappears due to beam-angle dependence of specular reflection. Caliper placement in that segment becomes ambiguous.

**Detector.** Class C — ultrasound confidence map, restricted to the aponeurosis band.

- Compute Karamalis-style confidence map across the frame.
- Sample the confidence in a band of ±N pixels around the superficial (or deep) fascia line.
- Flag if the *fraction of band columns with confidence below threshold* exceeds M%.

This is the most algorithmically novel part of v0 — confidence-map implementation is non-trivial (random-walk solver). **Defer to Tier-1 work (Brainstorm Step 3); v0 can ship without these** if Tier-0 catches the gross cases. Acknowledge in the contract that some borderline frames will pass Tier-0 but fail anisotropy checks later.

**Threshold.** TBD after confidence-map implementation.

**Measurements tainted.** `rf_thickness_mm` if column hits dropout; `qmt_mm` for deep dropout; `rf_area_mm2`, `full_*` if dropout is wide enough to break the mask contour.

**Override.** Operator visually confirms the fascia continues despite the algorithmic dropout flag.

#### `deep_attenuation_high` (Tier-1/C)

**Meaning.** Deep / high-BMI patient: ultrasound beam attenuates before reaching deep structures. The deep aponeurosis and femur are faint, low-contrast.

**Detector.** Class C — confidence map in the deep half of the frame. Flag when mean confidence in the deep half is below a threshold AND femur detector struggles.

**Threshold.** TBD.

**Measurements tainted.** `qmt_mm`, deep `full_*`.

**Override.** Operator accepts the lower-confidence deep measurement as the best available (BMI-driven patient population).

#### `subq_fat_compressed` (Tier-0/B — promoted from Tier-1 after OF-3 ratification)

**Meaning.** Probe compression has flattened the subcutaneous fat layer. `muscle_fat_ratio` is biased; all thicknesses are slightly underestimated.

**Detector.** Subcutaneous fat IS segmented in the current pipeline — `LABEL_FAT = 8` at [`labeling_app/measurements.py:36`](../labeling_app/measurements.py#L36), palette entry [`model_/label_palette.json:91-101`](../model_/label_palette.json#L91) ("fat"), already used in `muscle_fat_ratio()` at line 1164. Detector geometry:

```python
def subq_fat_compressed_score(
    mask: np.ndarray,           # multi-class segmentation
    sy_mm_per_px: float,        # vertical mm/pixel scale
    *, central_zone_only: bool = True,
) -> Optional[float]:
    """Return median fat-layer thickness in mm across the central-zone columns
    (or all columns where fat is present, if central_zone_only=False).
    None if no fat present anywhere."""
    h, w = mask.shape
    cols = range(w // 4, 3 * w // 4) if central_zone_only else range(w)
    thicknesses_px: list[int] = []
    for c in cols:
        col = mask[:, c]
        fat_px_in_col = int((col == LABEL_FAT).sum())
        if fat_px_in_col > 0:
            thicknesses_px.append(fat_px_in_col)
    if not thicknesses_px:
        return None
    return float(np.median(thicknesses_px)) * sy_mm_per_px
```

Flag `subq_fat_compressed` when median fat thickness in the central zone is below threshold.

**Threshold.** Needs cohort or clinical-literature calibration (Tier-B in § 11 nomenclature — not physics-grounded, requires data). Plausible starting point: 3 mm based on rough clinical typical fat thickness in non-lean adults; *do not deploy without empirical calibration on the existing cohort.* See § 11 for the calibration process.

**Measurements tainted.** `muscle_fat_ratio` primarily; all thicknesses to a lesser degree (the assumption is that compression flattens but doesn't completely remove the fat layer, so the geometric effect on RF thickness is small — second-order).

**Override.** Operator confirms the apparently thin fat layer is genuinely thin (lean patient population) and not compression-induced. This override is anatomical, not pathological — important to log as a *demographic* signal.

#### `segmentation_low_confidence` (Tier-2/G)

**Meaning.** The segmentation model itself is uncertain about the mask. A free signal that catches "weird" frames where the model's output is unreliable.

**Detector.** Class G — pull from the segmentation model's existing or addable uncertainty signal. Options:
- Output entropy at the mask boundary.
- MC-dropout disagreement (requires multiple forward passes).
- Test-time augmentation disagreement.

**Threshold.** Requires the uncertainty signal to be implemented + cohort calibration.

**Measurements tainted.** All geometry (mask-derived).

**Override.** Operator visually confirms the mask is correct despite the model's uncertainty.

---

## § 4 — Per-measurement validity rule (`image_quality_tier()`)

The validity rule is a function mirroring `qmt_tier()` ([`scripts/preflight_helpers/aggregation.py:58`](../scripts/preflight_helpers/aggregation.py#L58)) — but applied per-measurement rather than once per frame.

### Function signature

```python
def image_quality_tier(measurement: str, flags: tuple[str, ...]) -> str:
    """
    Return one of: 'valid', 'borderline', 'invalid'.

    Each measurement has a defined set of flags that disqualify it (invalid)
    and a set that degrade it (borderline). Flags not in either set are
    transparent to that measurement.
    """
```

### Per-measurement gating table

Cell value: `INV` = invalid if flag is set; `BDR` = borderline if flag is set; blank = transparent.

| flag \ measurement | rf_thickness_mm | qmt_mm | rf_area_mm2 | full_thickness_q50_mm | rf_echo | int_cv_within | muscle_fat_ratio | star_ratio |
|---|---|---|---|---|---|---|---|---|
| wrong_site | INV | INV | INV | INV | INV | INV | INV | INV |
| femur_out_of_frame | | INV | | | | | | INV |
| femur_off_central_zone | | BDR | | | | | | BDR |
| edge_clip_lateral | BDR | | INV | BDR | | | | |
| edge_clip_vertical | INV | BDR | | INV | | | | BDR |
| saturation_high | | | | | INV | INV | | |
| dark_clip_high | | BDR | | | INV | INV | | BDR |
| tgc_banding | | | | | BDR | BDR | | |
| motion_blur | INV | INV | BDR | INV | | BDR | | INV |
| air_gap_reverberation | BDR | BDR | BDR | BDR | BDR | BDR | BDR | BDR |
| oblique_plane | INV | INV | INV | INV | | BDR | | INV |
| anisotropy_dropout_superficial | BDR | | BDR | BDR | | | | |
| anisotropy_dropout_deep | BDR | INV | BDR | BDR | | | | INV |
| deep_attenuation_high | | BDR | | | | | | BDR |
| subq_fat_compressed | | | | | | | INV | |
| segmentation_low_confidence | BDR | BDR | BDR | BDR | | | BDR | BDR |

**Aggregate frame tier** (the badge shown in the picker):
- **Valid** — at least 60% of measurements valid + headline-4 all valid.
- **Borderline** — at least 1 of headline-4 borderline + none invalid (or any measurement borderline).
- **Invalid** — any headline-4 measurement invalid.

(Thresholds tentative; calibrate after Step 2 cohort run.)

### Tier semantics

- **Valid:** The measurement can be used in pooling, cohort placement, paper reporting, with no caveat.
- **Borderline:** Use is permitted but the value gets a flag in downstream reports. Cohort placement carries a "borderline-input" annotation. Aggregation may prefer valid frames if available.
- **Invalid:** The measurement is *not* pooled into aggregates. The frame remains in the dataset and the value is still computed and displayed (per Transparency Rule 1), but it doesn't contribute to the aggregate.

---

## § 5 — Integration sketch

### 5.1 — `FrameFeatures` extension

Add to the dataclass at [`scripts/preflight_helpers/feature_compute.py:78`](../scripts/preflight_helpers/feature_compute.py#L78):

```python
@dataclass(frozen=True)
class FrameFeatures:
    ...existing fields...
    # Image-quality QA — per-frame
    image_quality_validity_flags: tuple[str, ...]
    image_quality_detector_metadata: dict  # raw scores from each detector, for debugging
    image_quality_tier_per_measurement: dict[str, str]  # 'valid' | 'borderline' | 'invalid'
    image_quality_aggregate_tier: str  # 'valid' | 'borderline' | 'invalid'
```

### 5.2 — Detector entry point

A new module `labeling_app/image_quality.py` (or `scripts/preflight_helpers/image_quality.py` — placement TBD, both work):

```python
def compute_image_quality_flags(
    img_gray: np.ndarray,      # the _final.png modality, post-synthesize
    mask: np.ndarray,           # the multi-class segmentation mask
    *,
    detectors: dict | None = None,  # per-detector config, optional override
) -> dict:
    """
    Run all Tier-0 detectors. Return {
        'flags': tuple[str, ...],  # ordered by IMAGE_QUALITY_FLAG_PRIORITY
        'detector_metadata': dict, # raw scores (for debug + UI rationale)
    }
    """
```

### 5.3 — Aggregation hook

The existing `_aggregate_qmt` pattern in [`aggregation.py:84`](../scripts/preflight_helpers/aggregation.py#L84) — *prefer Tier-1, fall back to Tier-2, never pool across tiers* — generalises cleanly. Add `_aggregate_with_image_quality` that filters frames by `image_quality_tier_per_measurement[measurement]` before pooling.

### 5.4 — Phase-4 form change (Step 1 of brainstorm)

Edit [`labeling_app/assessment_pages/p04_frame_review.py:150`](../labeling_app/assessment_pages/p04_frame_review.py#L150). Replace the single `st.text_input("Drop reason (optional)", ...)` with a checkbox group whose options are the reason codes from `IMAGE_QUALITY_FLAG_PRIORITY`. Pre-fill the checkboxes from the algorithmic flags. Keep a free-text "Other" field for codes not in the taxonomy yet — surface these in a periodic review to grow the taxonomy.

The gates-dict init at line 109 grows:

```python
gates = st.session_state.gates.setdefault(fb, {
    "usable": None, "segmentation_perfect": None, "use_for_analysis": None,
    "drop_reasons": set(),          # NEW — structured checkboxes
    "drop_reason_other": "",        # NEW — free text for non-taxonomy reasons
    "qa_overrides": dict(),         # NEW — operator override of algorithmic flags
})
```

### 5.5 — Picker render change (the user-facing badge)

Edit [`labeling_app/assessment_pages/p03_frame_picker.py`](../labeling_app/assessment_pages/p03_frame_picker.py). Add a per-thumbnail badge area rendering `image_quality_aggregate_tier` + click-to-expand showing the drilldown panel sketched in § 6.

### 5.6 — Order of implementation

Step 0 → this doc → Step 1 (Phase-4 checkbox capture) → Step 2 (Tier-0 detectors, cohort run for failure-mode distribution) → Step 3 (Tier-1 confidence-map detectors) → Step 4 (promote `frame_rf_consistency` to gate) → Step 5 (Tier-2 learned grader, *only after* enough Step-1 labels exist) → Step 6 (wire into provenance gate).

**Critical sequencing:** Step 1 ships *before* Step 2. The label-capture infrastructure must exist before the algorithmic detectors, or the detector calibration has nothing to validate against.

---

## § 6 — Transparency contract and UI sketches

The four transparency rules from § 0 are restated here with concrete UI sketches.

### Rule 1: Never hide a frame for QA reasons

Picker default view shows **all frames** in the sweep. Filtering is opt-in.

### Rule 2: Always show the "why"

Every flag carries a hover/click rationale: detector name, threshold tripped, measurements tainted.

### Rule 3: Always allow override

A "Use this frame anyway" action on every flagged frame. Override is logged.

### Rule 4: Pre-fill, don't pre-decide

Phase-4 gates pre-fill from `image_quality_tier()`; operator confirms or overrides each.

### Mockup A — Phase-3 picker grid

```
┌─────────────────────────────────────────────────────────────────┐
│ Phase 3 — Frame picker  [study: AGZ855]                         │
│                                                                 │
│ Sort: [Tier ▼]  Filter: [All ▾] [☑ QMT-valid] [☐ Intensity-OK]  │
│ Show:  ✓ Valid (24)  ✓ Borderline (11)  ✓ Invalid (8)  ✓ All    │
├─────────────────────────────────────────────────────────────────┤
│ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐          │
│ │frame   │ │frame   │ │frame   │ │frame   │ │frame   │          │
│ │ 0042   │ │ 0043   │ │ 0044   │ │ 0045   │ │ 0046   │          │
│ │  ●●●   │ │  ●●●   │ │  ●●●   │ │  ●●●   │ │  ●●●   │          │
│ │ Valid  │ │ Borderl│ │Invalid │ │ Valid  │ │ Borderl│          │
│ └────────┘ └────────┘ └────────┘ └────────┘ └────────┘          │
│                       └ click → drilldown                       │
└─────────────────────────────────────────────────────────────────┘
```

Three dots = three structure groups (geometry / femur / intensity). Click any frame → drilldown panel (Mockup B).

### Mockup B — Frame QA drilldown panel

```
┌─────────────────────────────────────────────────────────────────┐
│ Frame 0044 — QA detail                                          │
├─────────────────────────────────────────────────────────────────┤
│ Overall: Invalid for 4 of 11 measurements                       │
│                                                                 │
│ Geometry  ●  rf_thickness_mm     ✓ Valid                        │
│              qmt_mm              ✗ Invalid — femur_out_of_frame │
│              rf_area_mm2         ⚠ Borderline — edge_clip_lat   │
│              full_thickness_q50  ✓ Valid                        │
│                                                                 │
│ Femur     ✗  qmt_mm + star_ratio invalidated                    │
│              [femur_out_of_frame]  Femur cortex not detected    │
│              in the central zone (Tier-0 detector: column-wise  │
│              search for bright continuous line below ROI).      │
│              → Confidence: deterministic, no human override     │
│                in training data yet.                            │
│                                                                 │
│ Intensity ✓  rf_echo, int_cv_within all valid                   │
│                                                                 │
│ [Use this frame anyway] [Drop with reason ▾]                    │
└─────────────────────────────────────────────────────────────────┘
```

### Mockup C — Phase-4 with pre-fill + override

```
┌─────────────────────────────────────────────────────────────────┐
│ Phase 4 — Frame review (frame 0044)                             │
├─────────────────────────────────────────────────────────────────┤
│ 1. Is this frame usable?                                        │
│    ◉ No  (pre-filled — see why ▾)                               │
│       └ femur_out_of_frame (Tier-0)                             │
│         edge_clip_lateral (Tier-0)                              │
│       Disagree?  ○ Override to Yes                              │
│                                                                 │
│ 2. Is segmentation perfect?                                     │
│    ○ Yes  ○ No  (no pre-fill — needs human judgment)            │
│                                                                 │
│ 3. Use for analysis?                                            │
│    ◉ No  (pre-filled — depends on Q1)                           │
│                                                                 │
│ Drop reason (if dropping):                                      │
│    [☑ femur_out_of_frame]  [☑ edge_clip_lateral]                │
│    [☐ oblique_plane]  [☐ anisotropy_dropout]  [☐ Other ___]     │
│    └─ pre-filled from QA flags; operator can add/remove         │
│                                                                 │
│ [Save & next]                                                   │
└─────────────────────────────────────────────────────────────────┘
```

### UI explainability discipline

Per Akadir's 2026-05-18 ratification: **the contract carries the rigor; the UI is a calibrated summarization.** v0 ships with the three mockups above as the minimum-viable surface, and we iterate UI depth based on operator reactions. The contract document is the authoritative source — UI surfaces NEVER carry information not derivable from the contract, and the contract's depth is independent of UI bandwidth.

---

## § 7 — Decisions ratified + open forks

### Ratified (2026-05-18 in-session with Akadir)

| # | Decision | Captured at |
|---|---|---|
| D1 | Per-measurement flags (not a scalar score) | § 0, § 3 |
| D2 | Assessment-gating scope only for v0; acquisition-time is future extension | § 0 |
| D3 | QA runs on `_final.png` modality (parity with measurement input) | § 0; per [[feedback_final_png_modality]] |
| D4 | Auto-grade all frames; surface visibly before operator selects | § 0, § 6 |
| D5 | Contract document carries full rigor; UI explainability is calibrated separately | § 6 |
| D6 | Transparency rules: never hide, always show why, always allow override, pre-fill don't pre-decide | § 0, § 6 |
| D7 | Step 1 label capture: picked + 5 sweep-stratified unpicked (1 per temporal zone) | § 0 (Decision 5, stratifier code sketch) |
| D8 | `subq_fat_compressed` is Tier-0 (fat is `LABEL_FAT = 8`, already segmented and used in `muscle_fat_ratio`) | § 0 (Decision 6), § 3 detector spec |

### Open forks (post-ratification)

| # | Open question | Default lean | Needs Akadir call |
|---|---|---|---|
| OF-2 | When to run Tier-1 (confidence map) work — sequential after Step 2 cohort run, or parallel? | Sequential (Step 2 results may reveal Tier-0 is enough for most failure modes) | Yes |

---

## § 8 — Future direction: diversity-aware multi-frame recommendation (Step 7)

The natural follow-on to this contract is a recommender that picks 3–5 frames per study which are *individually* good (per this contract) AND *collectively* spread across the muscle's long axis. The motivation, raised in-session by Akadir 2026-05-18:

> *"If you select all 5 frames from the center of the RF muscle because there is where it looks good — that is a problem. We should auto-select frames that are good and altogether, reflect the muscle in a more holistic way."*

### Why this is deferred (not part of v0)

The recommender requires capabilities this contract does not provide:

1. **A frame-pair diversity metric.** Either spatial (probe position from sweep DICOM metadata, if exposed) or visual (content similarity from segmentation / intensity statistics). Neither is on the shelf — needs design.
2. **A selection algorithm.** Greedy "pick best, then pick best-among-far-from-picked"? K-means cluster + pick best per cluster? Optimization over an objective combining quality and diversity? Out of scope for half-day design.
3. **A definition of "holistic coverage."** Spread along the muscle long axis? Different fascial geometries? Different intensity textures? The right answer depends on what cohort-level statistic the recommendation supports — needs strategic input.

### Dependencies the contract DOES provide for the recommender

- Per-frame quality scores (this contract).
- Per-measurement validity flags (this contract).
- Label-capture infrastructure (Step 1 if OF-1 lands on option (b), capturing diverse-frame labels).

### Recommendation discipline (when Step 7 lands)

The recommender is **additive highlighting, never subtractive hiding** — the picker shows all frames with a ★ on the N recommended, exactly as Mockup A illustrates. Operator may follow the recommendation or not. Anything else violates Transparency Rule 1.

---

## § 9 — Build sequence reference

Restated from Brainstorm § 4 for completeness:

| Step | What | Status | Estimate |
|---|---|---|---|
| **Step 0** | **This contract document** | **In review (you are reading it)** | Half day |
| Step 1 | Phase-4 free-text → checkbox capture (§ 5.4) | Spec'd, awaits Akadir ratification + OF-1 decision | 1 day |
| Step 2 | Tier-0 deterministic detectors (§ 3) + cohort failure-mode distribution | Spec'd in § 3, code anchors in § 5 | 3–5 days |
| Step 3 | Tier-1 ultrasound-specific (confidence map) detectors | Spec'd in § 3 (anisotropy + deep attenuation) | 5–10 days (confidence-map implementation is non-trivial) |
| Step 4 | Promote `frame_rf_consistency` to gate; generalize QMT-validity pattern | Spec'd at high level; needs code-anchor pass | 2 days |
| Step 5 | Tier-2 learned grader | NOT before Step 1 has accumulated labels | 1–2 weeks training + validation |
| Step 6 | Wire `image_quality_tier()` into the provenance-gate aggregation (§ 5.3) | Spec'd in § 5; trivial once Steps 2–4 land | 1 day |
| Step 7 | Diversity-aware multi-frame recommender (§ 8) | Deferred — depends on Steps 1–6 + new design | TBD |

---

## § 10 — Open questions for Akadir's review

Highest-leverage decisions to make on first pass:

1. **§ 4 gating table:** Does the per-measurement INV/BDR/blank cell assignment match clinical reality? Anonaao input recommended here — the contract's clinical correctness depends on this table.
2. **§ 3 reason-code priority order:** Does the priority tuple capture the right "dominant reason" ordering for UI surfacing? In particular: is `wrong_site` top-priority correct, or should it be at the *bottom* (always-shown) since for v0 it's purely a human flag?
3. **§ 5.1 dataclass placement:** Add new image-quality fields to existing `FrameFeatures` (current spec), or factor into a separate `ImageQualityFeatures` dataclass that the aggregation joins? Engineering preference; current spec is simpler.
4. **§ 8 Step-7 timing:** Should Step 7 (diversity-aware recommender) be scoped now for sequencing, or genuinely parked until Steps 1–6 ship?

---

## § 11 — Calibration methodology

*Akadir 2026-05-18: "We don't have the numbers. Do we have the process? Robust, accurate, practical?" — this section is the answer.*

### 11.1 — The two tiers of threshold provenance

Not every threshold needs calibration. The detectors split into:

**Tier A — Physics-grounded universals.** Thresholds derive from the signal physics, not the cohort. These ship with sensible defaults on day 1 and run on the cohort *before* labels exist.

| Detector | Threshold | Justification |
|---|---|---|
| `saturation_high` | ≥250 grayscale, >2% ROI pixels | 255 is the 8-bit saturation rail regardless of cohort. The 2% borrows the QMT muscle-occupancy tolerance precedent ([[project_qmt_provenance_gate]]). |
| `dark_clip_high` | ≤5 grayscale, >2% ROI pixels | Same physics, opposite rail. |
| `edge_clip_lateral` / `_vertical` | Any mask pixel in outermost N-pixel ring | N=1 = existing `touches_edge` behaviour ([`scripts/preflight_helpers/post_processing.py:106`](../scripts/preflight_helpers/post_processing.py#L106)). Universal. |
| `femur_out_of_frame`, `femur_off_central_zone` | Reuse existing QMT detector thresholds | Already calibrated for the QMT pipeline. |
| `oblique_plane` (fascia-slope component) | `QMT_FASCIA_SLOPE_MAX_ABS = 0.25` | Already calibrated ([`measurements.py:50`](../labeling_app/measurements.py#L50)). |

Tier-A detectors can ship in Step 2 with these defaults and start producing cohort-wide failure-mode histograms immediately. Calibration sharpens them later but does not gate them.

**Tier B — Cohort-calibrated.** Thresholds depend on scale, cohort, or implementation specifics; must come from data.

| Detector | Score | Why calibration needed |
|---|---|---|
| `motion_blur` | variance-of-Laplacian | Unit-free; depends on probe / gain / target resolution. |
| `tgc_banding` | second-difference σ of row-mean brightness | Depends on TGC curve shape, gain range. |
| `air_gap_reverberation` | FFT magnitude ratio in skin-contact zone | Depends on probe footprint and reverberation period. |
| `oblique_plane` (Radon-orientation component) | Dominant orientation divergence | Anatomy-dependent; calibrate per-site. |
| `subq_fat_compressed` | Fat layer thickness (mm) from `LABEL_FAT` mask | Need clinical anchor or cohort distribution for "compressed" cut-off. |
| Tier-1 confidence-map detectors | Confidence threshold + band coverage % | Requires confidence-map implementation first, then cohort calibration. |

### 11.2 — The five-step calibration process (for Tier-B detectors)

1. **Generate scores.** Run the detector on every frame in the cohort. Pair each frame's raw score with its structured Phase-4 label (drop / accept + reason codes) where available. Expected cohort: ~319 studies × ~14 picked frames ≈ 4,500 frames now; +5 sweep-stratified unpicked per study under D7 → ~6,000 frames once Step 1 has run a labelling round.

2. **Plot score distribution by label class.** For each detector, three histograms on the same axis:
   - All frames (the natural distribution)
   - Frames the operator dropped for *this specific reason code* (positives)
   - Frames the operator accepted (negatives)

   Separation between the positive and negative distributions is the strongest empirical indicator of whether the detector is useful at all. Heavy overlap = the detector is fundamentally weak; we know now, not after deployment.

3. **Pick the operating point via ROC.** Compute sensitivity / specificity across candidate thresholds. For QA we bias toward **higher sensitivity than specificity**: prefer false-flags (operator overrides — Transparency Rule 3) over silent misses (contaminated measurements). Target: ~80–90% sensitivity at ~70–85% specificity. Push higher sensitivity for downstream-fragile failure modes (a missed `femur_out_of_frame` poisons every QMT aggregate; a missed `tgc_banding` only biases one frame's intensity).

4. **Hold out and validate.** Split labelled set 70/30 calibration/validation. Fit thresholds on calibration; report sensitivity/specificity on held-out validation. Never report numbers on the same data the thresholds were fit on — that's the training-set overfit trap.

5. **Sanity-check with the operator.** Per detector:
   - Sample 10 *false positives* (detector flagged, operator accepted). Operator re-reviews. Is the detector catching something real the operator missed, or firing on a benign feature? Adjust threshold or logic.
   - Sample 10 *false negatives* (operator dropped, detector silent). Why did the detector miss? Different failure mode (suggesting a new detector or extending an existing one)? Or threshold issue?

   This step IS the practical accuracy floor — catches detector design errors that pure ROC analysis cannot surface.

### 11.3 — Robustness disciplines

- **Inter-rater reliability (IRR) noise floor.** Two operators independently label the same ~50-frame sample. Compute % agreement per reason code. If two humans disagree 20% of the time on a label, no detector can be reported as ">80% accurate" — that's noise, not signal. The IRR rate is the floor below which detector accuracy is meaningless. **Run an IRR study before publishing any threshold.**

- **Cross-cohort generalization.** If the cohort changes (new ultrasound machine, new operator, new patient population), re-run calibration. Tier-A thresholds survive cohort change; Tier-B may not.

- **Drift tracking.** A re-runnable calibration script that outputs:
  - Current threshold + recommended threshold per detector
  - Sensitivity/specificity at current threshold on the validation set
  - Drift indicator — warn if recommended threshold moved >X% from current
  
  Re-run when cohort grows >50% or segmenter changes.

### 11.4 — Implementation: the calibration script

Lives at `scripts/calibrate_image_quality_detectors.py` (proposed; ships as part of Step 2 work). Outputs per detector:

- ROC curve PNG → `runs/calibration/<date>/<detector>_roc.png`
- Score-distribution histogram PNG → `runs/calibration/<date>/<detector>_histogram.png`
- Calibration report JSON → `runs/calibration/<date>/report.json` with threshold + sens/spec + cohort metadata + IRR baseline.

Cheap, idempotent, re-runnable in minutes once the cohort + labels are loaded.

### 11.5 — What "accuracy" means honestly when reporting these detectors

The publish-worthy format for each detector at deployment:

> *"`motion_blur` Tier-0 detector. Threshold: variance-of-Laplacian < 47.3 (calibrated on 4,800 frames from 280 studies, 2026-06-XX). Held-out validation (1,200 frames, 39 studies): sensitivity 86%, specificity 78%. IRR floor on this label: 15% inter-rater disagreement. Detector accuracy is above the IRR floor; values are meaningful."*

Anything less specific is hand-waving. This format is what survives professional scrutiny.

### 11.6 — What we can do today (before Step 1 finishes)

Two parallel actions:

1. **Ship Tier-A detectors with physics-grounded defaults.** Run them on the cohort *today*; the failure-mode-distribution histogram is itself useful intelligence even before validation. The histogram tells us how big each problem is — which informs whether Tier-1 work is needed for a given failure mode or whether Tier-0 already catches enough.

2. **Re-code existing Phase-4 free-text drop reasons into the structured taxonomy.** ~319 studies of historical drops, manually re-coded against `IMAGE_QUALITY_FLAG_PRIORITY`. Probably a few hundred labels. Not as clean as Step-1 structured capture, but gives a preliminary calibration substrate before Step 1 fully rolls out.

### 11.7 — What CANNOT be calibrated honestly

These items will NOT pass the methodology bar above and the contract should not pretend otherwise:

- `wrong_site` — no detector, no calibration. Human-only signal in v0.
- Tier-2 learned grader — needs ≥several hundred labelled-per-class frames before training is honest. Do not deploy until Step 1 has accumulated.
- `segmentation_low_confidence` — needs segmenter to expose uncertainty signal first (currently doesn't). Calibration is gated on that engineering work.

Surface these as "known-uncalibrated" in any UI rendering and any paper write-up. Honesty about what we cannot grade is itself a quality dimension of the contract.

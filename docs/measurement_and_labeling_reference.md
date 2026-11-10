# Measurement and Labeling Reference

This document is the authoritative reference for what the app measures, calculates, records, and visualizes.

It describes the current production behavior implemented in:

- `labeling_app/data.py`
- `labeling_app/measurements.py`
- `labeling_app/visualization.py`
- `labeling_app/state.py`
- `labeling_app/app.py`

It also defines the reviewer-facing runtime model:

- Active runtime source of truth: `runtime/labeling.db`
- Reviewer-facing export: `exports/decision_log.csv`
- Private identity map: `configs/study_codes.csv`

This is a clean-slate system. Legacy CSV logging in the repo root is not part of the active runtime path.

## 1. Production Scope

The app does four things at once:

1. It measures morphology and image-derived metrics from the representative frame and, for one metric, from all frames.
2. It calculates derived physical units and ratios from pixel-space measurements plus spacing metadata.
3. It records a code-only decision log for reviewer workflow, grading, audit, calibration, and downstream analysis.
4. It visualizes the study video, frame variants, mask overlays, and annotated measurement overlays for reviewer interpretation.

All four are first-class. The app is not only a labeling UI and not only a metric viewer.

## 2. Identity and Privacy Model

### 2.1 Reviewer-facing identity

The app shows only `study_code` to the reviewer. `study_code` is a fixed `LLLDDD` pseudonym such as `ZVW174`.

The reviewer does not see:

- `study_dirname`
- patient name tokens embedded in the original folder name
- annotator
- source session
- relative transport path

### 2.2 Private identity map

`configs/study_codes.csv` is the private lookup table linking pseudonyms to real study folder names.

Columns:

| Column | Meaning |
| --- | --- |
| `study_code` | Stable reviewer-facing pseudonym. Must match `^[A-Z]{3}\d{3}$`. |
| `study_dirname` | Real study folder name from the transport metadata. |
| `numeric_study_id` | Numeric prefix parsed from `study_dirname`. |
| `annotator` | Source annotator from metadata. |
| `session` | Source session from metadata. |
| `relative_frame_dir` | Relative frame directory from metadata. |
| `created_at_utc` | Timestamp when the map file was generated. |

Rules:

- One row per study in the manifest.
- `study_code` must be unique.
- `study_dirname` must be unique.
- The map must cover every manifest study exactly once.
- The app fails closed if the mapping file is missing or coverage is inconsistent.

### 2.3 Generation policy

Study codes are generated once and then persisted. They are not regenerated on app startup.

The generation method is deterministic and hash-like:

- input: `study_dirname`
- output format: `LLLDDD`
- collisions are resolved deterministically by salted retries during generation

Operationally, the important rule is persistence, not re-generation.

## 3. Review Workflow

### 3.1 Phase model

The app uses a phase-locked two-stage review flow.

Phase 1 shows:

- `study_code`
- quality gate
- video
- per-frame image variants
- mask overlays
- grading instructions

Phase 1 hides:

- age
- sex
- height
- weight
- BMI
- comorbidities
- other comorbidities
- center-frame metrics

Phase 2 becomes available only after a valid preliminary submission.

Phase 2 reveals:

- subject metadata
- center-frame metrics
- representative-frame provenance note
- final decision controls

### 3.2 Quality gate

The reviewer must choose `PASS` or `FAIL`.

If the gate is `FAIL`:

- scoring is blocked
- metrics are not shown
- final grading controls are not shown
- the only terminal action is `Record quality fail`

This is persisted as `decision_status = quality_failed`, not mixed into `completed` or `skipped`.

### 3.3 Session behavior

`session_warning_threshold` is a warning, not a hard stop.

Current default:

- threshold: `60`

When the threshold is reached, the app shows a break suggestion but the reviewer can continue immediately.

### 3.4 Session counting

"Studies reviewed this session" means first save per review visit, not raw row count.

The implementation uses:

- `review_visit_id`: unique id for one visit to one study
- `review_save_index`: save number within that visit

Only rows with `review_save_index == 1` count toward:

- session totals
- session label distribution
- anchor cadence

### 3.5 Anchor studies

Anchor studies are calibration re-checks of already completed studies.

Rules:

- cadence: every `anchor_interval` reviewed studies
- default interval: `15`
- candidate pool: latest effective decisions whose status is `completed` and whose `quality_label` is non-empty
- queue seed: deterministic random seed `42 + session_count`
- queue size: up to `5`

Important:

- skipped studies are not anchor candidates
- quality-failed studies are not anchor candidates
- the app stores anchor status and prior label agreement in the decision log

## 4. Data Ingest and Demographics Parsing

### 4.1 Manifest source

The study manifest is built from `transport/transport_metadata.csv`.

Study-level rows are created by grouping CSV rows by `study_dirname`.

The current corpus size is:

- 956 frame rows
- 319 study rows

### 4.2 Per-study manifest fields

Each manifest row includes:

- `study_code`
- `study_dirname`
- `annotator`
- `session`
- `relative_frame_dir`
- `frame_bases`
- `frame_count`
- parsed demographics
- per-frame channel paths
- per-frame mask paths
- video path
- spacing metadata
- flags such as `has_video`, `has_mask`, `has_pixel_spacing`

### 4.3 Demographics parser

The parser extracts from `study_dirname`:

- numeric id
- last name
- first name
- age
- gender
- height in cm
- weight in kg
- BMI
- structured comorbidities
- other comorbidities
- `parse_ok`

Supported patterns include:

- standard age-gender tokens such as `53k` or `48e`
- bare numeric ages
- split age and gender tokens such as `25y_e`
- fused age+gender+height tokens such as `23k160`
- weight tokens with `kg`
- dual structured comorbidities such as `ht_dm` or `dm_ht`
- trailing unknown suffixes
- doubled underscores and trailing underscores

Canonical comorbidities:

- `ht`
- `dm`
- `ht+dm`

Unknown suffix tokens are preserved in `other_comorbidities`.

### 4.4 BMI

If height and weight are both available:

`BMI = weight_kg / (height_m ^ 2)`

The stored BMI is rounded to one decimal place.

## 5. Label Semantics

The segmentation masks are expected to use these numeric labels:

| ID | Label | Role in production metrics |
| --- | --- | --- |
| `0` | background | excluded from positive-mask area; allowed in QMT path |
| `1` | RF | used directly |
| `2` | VI | used directly |
| `3` | VL | treated as QMT path contaminant in strict mode |
| `4` | VM | treated as QMT path contaminant in strict mode |
| `5` | fascia | upper QMT boundary in standard mode |
| `6` | skin | not used in current production metrics |
| `7` | femur | lower QMT anchor and femur plateau support |
| `8` | fat | used in muscle/fat ratio |
| `9` | Sartorius | not used directly in current production metrics |

## 6. Representative Frame Selection

The app chooses one representative frame per study before computing center-frame metrics and annotated overlays.

Policy:

1. Compute the temporal center index: `n // 2`.
2. If that frame yields `standard_qmt`, keep it.
3. Otherwise search other frames for `standard_qmt`, preferring the nearest frame to the temporal center.
4. If no frame yields `standard_qmt`, fall back to the temporal center anyway.

Possible provenance labels:

- `temporal_center_standard_qmt`
- `nearest_standard_qmt`
- `temporal_center_fallback`

This selection affects:

- center-frame measurements
- annotated visualization
- exported representative-frame fields

## 7. What Is Measured

### 7.1 RF thickness

Function:

- `rf_thickness_px(mask)`

Definition:

- maximum contiguous RF run length in a single column

How the column is chosen:

- scan all columns
- compute the maximum contiguous run of label `1` in each column
- choose the column with the largest run
- ties go to the first leftmost maximum

Stored support field:

- `rf_column`

### 7.2 VI thickness

Function:

- `vi_thickness_px(mask)`

Definition:

- maximum vertical extent of VI, using a femur-plateau-aware lower bound when femur exists

How the column is chosen:

- use `vi_max_extent_column(mask)`
- when a femur plateau exists, search only plateau columns
- clamp VI lower boundary to just above the local plateau femur row
- without femur, fall back to full-image VI extent search

Stored support field:

- `vi_column`

### 7.3 Femur plateau

Function:

- `find_femur_plateau_columns(mask, slope_pct=8.0)`

Purpose:

- define a flat or near-flat central femur segment for VI, standard-QMT validity, and proxy-QMT centering

Method:

1. For each column with femur, record the topmost femur row.
2. Median-smooth that femur-top contour with a 5-column window over existing femur columns only.
3. Find the minimum smoothed femur row.
4. Use the median column of all smoothed columns within 2 px of that minimum as `col_ref`.
5. Accept columns satisfying:

`femur_top[col] <= min_row + abs(col - col_ref) * 0.08`

6. Keep the longest contiguous run of accepted columns.

The exported plateau row values remain the actual topmost femur rows for those accepted columns. The smoothing is used only to decide which columns belong to the plateau.

This is not directly exported by itself, but it affects:

- VI thickness
- standard-QMT central-validity checks
- proxy-QMT center preference
- support-column placement
- reviewer guidance fields

### 7.4 Standard QMT

Primary production function:

- `_qmt_measurement_details(mask)` via `_select_qmt_column(mask, strict=True)`

Standard QMT is the preferred production QMT.

Important methodological point:

- production QMT is intentionally a vertical, columnwise measurement
- the app does not use the perpendicular-QMT helper as the production metric
- validity is enforced by central geometry, not by converting the measurement line to a free-angle perpendicular segment

Definition:

- vertical distance from the first muscle pixel immediately below upper fascia to the topmost femur pixel at one valid column

Important detail:

- `top_y` is not the top fascia pixel itself
- `top_y` is the first muscle pixel below the upper fascia

So:

`qmt_px = femur_top_y - fascia_top_y`

where `fascia_top_y` already points to the first muscle pixel under fascia.

Column validity requirements:

- femur must exist in the column
- RF or upper fascia must define the upper boundary
- `top_y < bottom_y`
- contamination in the path from `top_y` to `bottom_y` must be `<= 2%`

Strict-mode allowed labels in the path:

- background `0`
- RF `1`
- VI `2`
- fascia `5`

VM and VL are not allowed in strict mode.

Additional central-validity requirements for `standard_qmt`:

- the column must lie inside the `central_femur_zone`
- muscle occupancy within the fascia-to-femur path must be `>= 85%`
- absolute local femur slope on the smoothed femur contour must be `<= 0.15 px/col` when enough neighboring femur columns exist
- absolute local upper-boundary slope must be `<= 0.25 px/col` when enough neighboring top-boundary points exist

The `central_femur_zone` is defined as:

- `plateau_core` expanded by 15 px left and right, clipped to columns that still contain femur
- if the plateau is too small or degenerate, the zone falls back to `median_femur_col ± 15 px`

When multiple columns satisfy standard-QMT validity, the ranking order is:

1. inside `plateau_core`
2. both RF and VI present in the path
3. higher muscle occupancy
4. smaller distance to the central-zone center
5. flatter femur slope
6. flatter fascia/top-boundary slope
7. larger QMT thickness
8. smaller column index as deterministic tie-break

### 7.5 QMT support columns

Function:

- `_strict_qmt_annotation_columns(mask, primary)`

For standard QMT only, the app collects:

- the true measurement column
- nearest eligible left support column inside the `central_femur_zone`
- nearest eligible right support column inside the `central_femur_zone`

Rules:

- support columns must themselves satisfy full standard-QMT validity
- left/right support columns are optional
- maximum total count is 3 including the primary column

Exported field:

- `qmt_annotation_columns`

Visualization:

- primary column is the true measured line
- additional columns are dashed guides

### 7.6 Proxy QMT fallback

If no valid standard-QMT column exists, the app falls back to proxy QMT instead of forcing `no_measurement`.

Provenance:

- `femur_supported_proxy`
- `no_femur_proxy`

Definition:

- vertical span covering the best available RF+VI proxy column

Bounds:

- if both RF and VI exist in the column, top is RF min and bottom is VI max
- if only one of RF or VI exists, use that label's min/max span
- zero or negative spans are rejected

`femur_supported_proxy` selection policy:

1. femur is present in the frame, but no column satisfies standard-QMT geometry
2. prefer proxy columns inside the `central_femur_zone`
3. if no proxy candidate exists inside that zone, search all RF/VI-bearing columns
4. rank by:
   - both RF and VI present
   - higher RF+VI occupancy
   - smaller distance to the central-zone center
   - larger proxy span

`no_femur_proxy` selection policy:

1. femur is absent from the frame entirely
2. define the muscle center as the median column of all RF/VI-bearing columns
3. rank proxy candidates by:
   - both RF and VI present
   - smaller distance to the muscle center
   - larger proxy span

`no_measurement` is used only when no RF-or-VI span can be formed at all.

### 7.7 QMT RF and VI sub-portions

Exported fields:

- `qmt_rf_px`
- `qmt_rf_mm`
- `qmt_vi_px`
- `qmt_vi_mm`

These are populated only for `standard_qmt`.

Method:

- count RF and VI pixels inside the strict QMT path on the true QMT column

For proxy QMT:

- these fields are `None`

### 7.8 STAR

Exported field:

- `star_ratio`

Definition:

`STAR = qmt_mm / BMI`

Rules:

- requires `qmt_mm`
- requires numeric BMI
- BMI must be greater than zero

Important:

- if QMT provenance is proxy, STAR is proxy-derived as well
- the UI labels this as `STAR*`

### 7.9 Echo intensity

Exported fields:

- `rf_echo`
- `vi_echo`

Definition:

- mean grayscale intensity of the center-frame image where the mask equals RF or VI

Requirements:

- a readable grayscale center image
- target label present in the mask

Not measured from:

- video
- all frames
- overlay image

Measured from:

- the selected representative frame's grayscale base image

### 7.10 Area

Exported fields:

- `rf_area_px`
- `rf_area_mm2`
- `vi_area_px`
- `vi_area_mm2`

Pixel area:

- simple pixel count of the target label in the representative-frame mask

Physical area:

`dcm_cols_usg = jpg_width * dcm_rows / jpg_height`

`mm_per_mask_x = pixel_spacing_x * dcm_cols_usg / mask_width`

`mm_per_mask_y = pixel_spacing_y * dcm_rows / mask_height`

`area_mm2 = px_count * mm_per_mask_x * mm_per_mask_y`

Important:

- this uses original mask space
- no resize factor is applied beyond the metadata-derived conversion above

### 7.11 Muscle/fat ratio

Exported field:

- `muscle_fat_ratio`

Definition:

`(RF_px + VI_px) / max(fat_px, 1)`

Rules:

- returns `None` if both RF and VI are absent
- denominator is clamped to at least `1` to avoid divide-by-zero

### 7.12 Frame RF consistency

Exported field:

- `frame_rf_consistency_px`

Definition:

- sample standard deviation of RF thickness across all valid frame masks in the study

Requirements:

- at least 2 valid RF thickness measurements across frames

This is the only production metric that explicitly aggregates across all study frames.

### 7.13 Mask coverage

Exported field:

- `mask_coverage_pct`

Definition:

- percent of mask pixels with any positive non-background label

Formula:

`positive_pixels / total_pixels * 100`

## 8. What Is Calculated

The app calculates more than the direct measurements above.

Calculated items include:

- representative frame index and provenance
- BMI from folder-name tokens when possible
- mm conversions from px counts
- mm^2 conversions from px area
- STAR from QMT mm and BMI
- progress counts from handled statuses
- session counts from first-save review visits
- anchor schedules from session counts
- label-change flags
- anchor agreement flags

## 9. What Is Recorded

### 9.1 Runtime persistence model

The runtime source of truth is SQLite:

- file: `runtime/labeling.db`
- table: `decisions`
- supporting tables: `review_sessions`, `anchor_queue`

The CSV export is derived from SQLite, not vice versa.

### 9.2 Decision statuses

Current production statuses:

- `completed`
- `skipped`
- `quality_failed`

Queue semantics:

- `completed`, `skipped`, and `quality_failed` are all considered handled for "Unreviewed" filtering

### 9.3 Decision log field definitions

#### Review and identity fields

| Field | Meaning | When populated | Why it matters |
| --- | --- | --- | --- |
| `operator_id` | Reviewer identifier entered in the sidebar. | Every saved row. | Reviewer-level audit and analysis. |
| `saved_at_utc` | Save timestamp in UTC. | Every saved row. | Ordering, audit, effective-decision resolution. |
| `session_id` | Unique id for one reviewer session. | Every saved row. | Session grouping. |
| `review_visit_id` | Unique id for one visit to one study. | Every saved row. | Distinguishes repeat saves within one visit from separate revisits. |
| `study_code` | Reviewer-facing pseudonym. | Every saved row. | Primary join key for reviewer-facing data. |
| `decision_status` | Terminal row status. | Every saved row. | Queue handling, audit, downstream filtering. |
| `quality_gate_pass` | Pass/fail outcome of the initial quality gate. | Saved rows after a gate decision. | Separates unusable studies from scored studies. |

#### Phase 1 and final-decision fields

| Field | Meaning | When populated | Why it matters |
| --- | --- | --- | --- |
| `preliminary_label` | Phase 1 preliminary grade. | Completed phase-2 saves; skipped rows after phase 1 may also carry it. | Measures effect of metadata/metrics on final grading. |
| `preliminary_impression` | Phase 1 rationale text. | Same cases as `preliminary_label`. | Qualitative audit of the first-pass impression. |
| `quality_label` | Final label. | `completed` rows only. | Primary study outcome. |
| `quality_rationale` | Final or terminal rationale text. | All saved rows. For `quality_failed` and `skipped`, this is the terminal explanation string. | Auditability and reviewer traceability. |
| `label_changed` | Whether final label differs from preliminary label. | `completed` rows with both labels present. | Quantifies revision frequency. |
| `change_reason` | Reviewer explanation for label revision. | Only when `label_changed` is true. | Captures why phase 2 changed the phase 1 impression. |

#### Representative-frame fields

| Field | Meaning | When populated | Why it matters |
| --- | --- | --- | --- |
| `center_frame_base` | Frame basename of the representative frame. | Every saved row. | Reproducibility back to the source frame. |
| `representative_frame_index` | Representative frame index within the study. | Every saved row. | Reproducibility and audit. |
| `representative_frame_reason` | Why that frame was chosen. | Every saved row. | Explains whether the center frame was strict-QMT-valid or a fallback choice. |

#### Metric fields

| Field | Meaning | When populated | Why it matters |
| --- | --- | --- | --- |
| `rf_thickness_px` | RF thickness in px. | Quality-pass rows with a readable representative mask. | Muscle morphology. |
| `rf_thickness_mm` | RF thickness in mm. | Same, but only with spacing metadata. | Physical comparability. |
| `rf_column` | RF measurement column. | When RF thickness is measurable. | Reproducibility and visualization audit. |
| `vi_thickness_px` | VI thickness in px. | Quality-pass rows with a readable representative mask. | Muscle morphology. |
| `vi_thickness_mm` | VI thickness in mm. | Same, but only with spacing metadata. | Physical comparability. |
| `vi_column` | VI measurement column. | When VI thickness is measurable. | Reproducibility and visualization audit. |
| `qmt_px` | QMT in px. | Quality-pass rows with a measurable QMT or proxy QMT. | Main thickness outcome. |
| `qmt_mm` | QMT in mm. | Same, but only with spacing metadata. | Physical comparability. |
| `qmt_provenance` | `standard_qmt`, `femur_supported_proxy`, `no_femur_proxy`, or `no_measurement`. | Quality-pass rows. | Distinguishes central-valid standard QMT from femur-supported proxy, no-femur proxy, and no measurement. |
| `qmt_column` | True measurement column used for QMT. | When QMT or proxy QMT exists. | Reproducibility and overlay correctness. |
| `qmt_annotation_columns` | Pipe-delimited list of the primary column plus support columns. | Standard QMT or proxy QMT rows. Proxy stores one column only. | Visualization provenance. |
| `qmt_muscle_occupancy_pct` | Percent of the selected QMT path occupied by RF+VI. | When QMT or proxy QMT exists. | Flags whether the vertical path is dominated by muscle or by gaps/background. |
| `qmt_plateau_supported` | Whether the selected QMT column lies inside `plateau_core`. | When QMT or proxy QMT exists. | Indicates how central/flat the femur support is. |
| `qmt_central_zone_supported` | Whether the selected QMT column lies inside the central femur zone. | When QMT or proxy QMT exists. | Indicates whether the chosen line stayed inside the intended acquisition window. |
| `qmt_femur_slope_abs` | Absolute local femur slope at the selected column. | When slope is estimable. | Quantifies whether vertical measurement is being taken on a shallow femur segment. |
| `qmt_fascia_slope_abs` | Absolute local upper-boundary slope at the selected column. | When slope is estimable. | Quantifies whether the upper boundary is sufficiently smile-like / non-oblique. |
| `qmt_validity_flags` | Pipe-delimited caveat flags for the selected column. | Standard or proxy QMT rows. | Encodes why the app accepted standard QMT or downgraded to proxy. |
| `qmt_guidance_status` | `good_standard_window`, `proxy_due_to_geometry`, `proxy_due_to_missing_femur`, or `no_measurement`. | Quality-pass rows. | Reviewer-facing acquisition guidance summary. |
| `qmt_rf_px` | RF portion inside strict QMT path in px. | `standard_qmt` only. | Decomposes QMT into component muscles. |
| `qmt_rf_mm` | RF portion inside strict QMT path in mm. | `standard_qmt` only with spacing. | Physical decomposition of QMT. |
| `qmt_vi_px` | VI portion inside strict QMT path in px. | `standard_qmt` only. | Decomposes QMT into component muscles. |
| `qmt_vi_mm` | VI portion inside strict QMT path in mm. | `standard_qmt` only with spacing. | Physical decomposition of QMT. |
| `star_ratio` | QMT mm divided by BMI. | When QMT mm and BMI are both available and BMI > 0. | BMI-normalized thickness index. |
| `rf_echo` | Mean grayscale intensity for RF. | Only when grayscale center image exists and RF pixels exist. | Echo texture summary. |
| `vi_echo` | Mean grayscale intensity for VI. | Only when grayscale center image exists and VI pixels exist. | Echo texture summary. |
| `rf_area_px` | RF area in px. | Quality-pass rows with a readable representative mask. | Segment size summary. |
| `rf_area_mm2` | RF area in mm^2. | Same with spacing metadata. | Physical area summary. |
| `vi_area_px` | VI area in px. | Quality-pass rows with a readable representative mask. | Segment size summary. |
| `vi_area_mm2` | VI area in mm^2. | Same with spacing metadata. | Physical area summary. |
| `muscle_fat_ratio` | `(RF_px + VI_px) / max(fat_px, 1)`. | Quality-pass rows with a readable representative mask. | Relative muscle-vs-fat burden. |
| `frame_rf_consistency_px` | RF thickness SD across frames. | Only when at least 2 frame RF values exist. | Sweep consistency / stability signal. |
| `mask_coverage_pct` | Percent of non-background pixels in the representative mask. | Quality-pass rows with a readable representative mask. | Segmentation occupancy summary. |

#### Calibration and workflow fields

| Field | Meaning | When populated | Why it matters |
| --- | --- | --- | --- |
| `is_anchor_study` | Whether the row belongs to a calibration revisit. | Every saved row. | Separates anchors from normal review flow. |
| `prior_label` | Previously effective label for an anchor study. | Anchor rows when a prior completed label exists. | Calibration reference. |
| `anchor_agreement` | Whether the new anchor label matches `prior_label`. | Anchor rows with both labels present. | Reviewer consistency metric. |
| `review_context` | `fresh`, `revisit`, or `anchor`. | Every saved row. | Distinguishes first-pass review, revisits, and anchors. |
| `review_save_index` | Save number within the current `review_visit_id`. | Every saved row. | Separates first-save counting from within-visit edits. |
| `study_annotation_index` | Running count of saved rows for this operator and study code. | Every saved row. | Tracks repeat annotations/revisions. |
| `session_study_number` | Session-order number for the current review visit. | Every saved row. | Session sequencing in reviewer audit. |

#### Frame-note fields

| Field | Meaning | When populated | Why it matters |
| --- | --- | --- | --- |
| `frame_note_1` | Optional note for frame 1. | If entered. | Reviewer frame-specific observations. |
| `frame_note_2` | Optional note for frame 2. | If entered. | Reviewer frame-specific observations. |
| `frame_note_3` | Optional note for frame 3. | If entered. | Reviewer frame-specific observations. |
| `frame_note_4` | Optional note for frame 4. | If entered. | Reviewer frame-specific observations. |

#### Subject-context fields

| Field | Meaning | When populated | Why it matters |
| --- | --- | --- | --- |
| `age` | Parsed age. | If parseable from `study_dirname`. | Clinical context. |
| `sex` | Parsed sex/gender code mapped to `F`, `M`, or `unknown`. | If parseable. | Clinical context. |
| `height_cm` | Parsed height in cm. | If parseable. | BMI and body-size context. |
| `weight_kg` | Parsed weight in kg. | If parseable. | BMI and body-size context. |
| `bmi` | Parsed or derived BMI. | If height and weight are parseable. | Needed for STAR. |
| `comorbidities` | Canonical structured comorbidity token. | If recognized. | Clinical stratification. |
| `other_comorbidities` | Free-form unrecognized suffix token bundle. | If present. | Preserves context not captured by the canonical list. |

#### Spacing and geometry fields

| Field | Meaning | When populated | Why it matters |
| --- | --- | --- | --- |
| `pixel_spacing_x` | DICOM spacing in the x direction. | If present in metadata. | Area conversion. |
| `pixel_spacing_y` | DICOM spacing in the y direction. | If present in metadata. | Length and area conversion. |
| `dcm_rows` | Original DICOM row count. | If present in metadata. | Length and area conversion. |
| `jpg_height` | JPG frame height. | If present in metadata. | Area conversion. |
| `jpg_width` | JPG frame width. | If present in metadata. | Area conversion. |
| `mask_height` | Mask image height. | If present in metadata. | Length and area conversion. |
| `mask_width` | Mask image width. | If present in metadata. | Area conversion. |

### 9.4 What is intentionally not recorded in the reviewer-facing export

These are intentionally excluded from `exports/decision_log.csv`:

- `study_dirname`
- `relative_frame_dir`
- annotator name
- source session name

Those remain available only through the private study-code map.

## 10. What Is Visualized

### 10.1 Study selector

The Study dropdown shows:

- `study_code` only

It does not show real folder names.

### 10.2 Video

If `source.mp4` exists, the app shows a looping muted video.

If it is missing:

- the app explicitly says video is unavailable
- frame-based review still works

### 10.3 Frame variants

For each frame, the reviewer can switch among available channel variants.

Preferred ordering:

1. `raw`
2. `beforeDSC`
3. `final`
4. any remaining available channels

Additional channels may include:

- `clahe`
- `voi`
- `log`
- `contrast`
- `edge`
- `hist`
- `speckle`
- `depth`
- `frangi`
- `diff`

### 10.4 Overlays

For each frame with both base image and mask:

- non-center frames show a standard mask overlay
- the representative frame in phase 2 shows an annotated overlay

The overlay alpha is configurable through `overlay_alpha`.

### 10.5 Annotated overlay

The representative-frame annotated overlay draws:

- RF run at `rf_column` in green
- VI run at `vi_column` in cyan
- primary QMT span at the true measurement column in white
- QMT support columns as dashed magenta guides when `standard_qmt`
- QMT label text: `QMT` for strict, `QMT*` for proxy

Important visualization rule:

- support columns are guides only
- the primary column is the actual recorded QMT measurement

### 10.6 Representative-frame provenance note

The metrics panel shows a short note explaining why the representative frame was selected.

Examples:

- temporal center with strict QMT support
- nearest non-center frame with strict QMT support
- temporal center fallback because no strict-QMT frame exists

## 11. Implemented but Not Currently Used in Production Metrics

Two measurement functions exist in `labeling_app/measurements.py` but are not part of the current exported production metrics:

- `vi_thickness_perpendicular_px`
- `qmt_thickness_perpendicular`

These are geometry-aware perpendicular methods intended to follow curved tissue boundaries more faithfully than pure column-based measurements.

Current status:

- implemented
- not used by `compute_all_metrics`
- not displayed in the app
- not exported to the decision log

They should be treated as exploratory methods until explicitly promoted into production.

## 12. Known Limitations and Practical Notes

1. If spacing metadata is missing, px-space metrics still exist but mm and mm^2 outputs become `None`.
2. Echo intensity requires a readable grayscale representative image. If the base image is missing, echo fields are `None`.
3. Standard QMT is intentionally strict. Some anatomically plausible columns are rejected if contamination exceeds the 2% threshold.
4. Proxy QMT is a fallback, not interchangeable with strict femur-based QMT. The UI marks it as proxy.
5. Only the private study-code map links back to real study names. If that file is lost, reviewer-facing exports remain pseudonymized but no longer re-linkable.
6. Frame notes currently support up to 4 study frames because the corpus currently spans 2 to 4 frames per study.
7. `review_context = revisit` means the study already had an effective row for this reviewer. It is not limited to prior completed labels.
8. The active runtime store is SQLite. The CSV export is a derived artifact and should not be edited as if it were the source of truth.

## 13. Operational Readiness Checklist

At the app level, production readiness now depends on these files remaining valid:

- `configs/labeling.yaml`
- `configs/study_codes.csv`
- `.streamlit/config.toml`
- `transport/transport_metadata.csv`
- `label_palette.json`

And on these runtime expectations:

- `runtime/labeling.db` exists or can be created
- `exports/decision_log.csv` can be written
- Streamlit usage telemetry is disabled
- CORS and XSRF protection remain enabled

This document should be updated whenever measurement logic, exported fields, or reviewer-visible workflow changes.

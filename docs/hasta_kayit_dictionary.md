# hasta_kayit + qa data dictionary

PHI-FREE reference for the Round-3 `hasta_kayit` datasheet and its generated `qa` projection. Study identity here is the **study_uid** only (`NR`+sha8 of the folder name) — never a patient name, `Study Adı`, or raw dirname.

_GENERATED sections are rendered from the live registries by `scripts/build_qa_sheet.py:render_dictionary()` — do not hand-edit them; re-run `build_qa_sheet.py --emit-dict`. Measurement-code hash at generation: `c7ba5c106fb5863f`._

## hasta_kayit columns (GENERATED — `datasheet_schema.COLUMNS`)

| # | Column | Source | Meaning |
|---|--------|--------|---------|
| A | Sıra | seq | 1-based row index |
| B | Tarih | filename | ddMMMyy from Study Adı, stored verbatim |
| C | Study Adı | filename | zip basename — the match / idempotency key |
| D | Hasta Adı | filename |  |
| E | Hasta Soyadı | filename |  |
| F | Cinsiyet | filename |  |
| G | Yaş | filename |  |
| H | Boy (cm) | filename |  |
| I | Kilo (kg) | filename |  |
| J | BMI | formula | =ROUND(Kilo/(Boy/100)^2,1) |
| K | Dominant Bacak | filename | imaged leg — in-sheet FORMULA from the name's r/l suffix (r=SAĞ, l=SOL); blank if absent |
| L | ASIS-Patella Sol (cm) | filename | in-sheet FORMULA from the name's ASIS value (into both legs); blank if absent |
| M | ASIS-Patella Sağ (cm) | filename | in-sheet FORMULA from the name's ASIS value (into both legs); blank if absent |
| N | Yaralanma/Cerrahi (alt ekstremite, son 6 ay) | manual |  |
| O | Alt-kohort mu | manual | Evet -> 8-capture sub-cohort |
| P | Operator | subfolder |  |
| Q | Software Version | manual | non-strict dropdown seeded from config at setup; defaults to Seçiniz |
| R | Presets Ayarlı mı | manual |  |
| S | Probe No | filename | in-sheet FORMULA: the name's probe (one of PROBE_NUMBERS); blank otherwise |
| T | Artifacts (mp4/RawImages/dcm) | computed | filled by intake from the zip |
| U | Dosyalar Tam mı | manual |  |
| V | Notlar | manual |  |
| W | Kontrol Sonucu | check | completeness/accuracy verdict written by the check_studies.py watcher |
| X | Kontrol Detayı | check | watcher detail: dcm depth multiset + brightness + specifics |
| Y | İzometrik Tepe Tork (Nm) | manual | isometric peak knee-EXTENSION torque, tested leg — PRIMARY strength criterion |
| Z | İzometrik CV (%) | manual | coeff. of variation of the isometric reps — reliability GATE + paper descriptor (added 2026-07-01 at Dr. M's request); grouped right after its torque |
| AA | İzokinetik Tepe Tork Ekst (Nm) | manual | isokinetic peak EXTENSION torque @ 60 deg/s — secondary (dynamic) criterion |
| AB | İzokinetik Ort. Güç Ekst (W) | manual | isokinetic average EXTENSION power @ 60 deg/s — secondary criterion |
| AC | İzokinetik CV (%) | manual | coeff. of variation of the isokinetic reps — reliability GATE, not a target; a high CV invalidates the isokinetic torque/power (best practice <= 10-15%) |

## Displayed measurement features (GENERATED — `feature_catalog`)

| Feature | Label | Tier |
|---------|-------|------|
| `star_ratio` | STAR (QMT/BMI) | headline |
| `full_thickness_q50_mm` | RF thickness — median (mm) | headline |
| `int_cv_within` | Fascicular contrast (echo CV) | headline |
| `rf_thickness_mm` | RF thickness — at the landmark cross-section (mm) | headline |
| `fat_thickness_q50_mm` | Fat thickness — median (mm) | secondary |
| `qmt_mm` | QMT (mm) | secondary |
| `visible_rf_area_mm2` | Visible RF area (mm²) | secondary |
| `fat_to_depth_q50` | Fat fraction (fat / skin-to-femur) | headline |
| `fat_thickness_max_mm` | Fat thickness — max (mm) | card-only |
| `muscle_fat_ratio` | Muscle / fat ratio (diagnostic) | card-only |

## Image-quality QA detectors (GENERATED — `image_quality._DETECTOR_REGISTRY` + `qa_thresholds.THRESHOLDS`)

Active detectors (14): `wrong_site`, `femur_out_of_frame`, `femur_off_central_zone`, `edge_clip_lateral`, `edge_clip_vertical`, `saturation_high_global`, `saturation_high_regional`, `dark_clip_high`, `tgc_banding`, `motion_blur`, `air_gap_reverberation`, `near_field_clutter`, `vessel_shadow_in_roi`, `anisotropy_dropout_superficial`

| Threshold key | Value | Units | Tier | Calibrated |
|---------------|-------|-------|------|------------|
| `saturation_high__pixel_value_min` | 250 | grayscale (8-bit DN) | A | n/a (Tier-A) |
| `dark_clip_high__pixel_value_max` | 5 | grayscale (8-bit DN) | A | n/a (Tier-A) |
| `edge_clip__boundary_ring_px` | 1 | pixels (boundary-ring width) | A | n/a (Tier-A) |
| `femur_central_zone__margin_px` | 15 | pixels (central-zone inset) | A | n/a (Tier-A) |
| `wrong_site__rf_column_coverage_min` | 0.5 | fraction of image columns with ≥1 cleaned-RF pixel | A | n/a (Tier-A) |
| `anisotropy_dropout_superficial__continuity_fraction_min` | 0.7 | fraction of muscle-top columns with a fascia ridge | A | n/a (Tier-A) |
| `saturation_high_global__roi_fraction_max` | 0.02 | fraction of ROI pixels | B | pending |
| `dark_clip_high__roi_fraction_max` | 0.1 | fraction of ROI pixels | B | pending |
| `saturation_high_regional__block_fraction_max` | 0.1 | fraction of block pixels | B | pending |
| `saturation_high_regional__block_size_px` | 32 | pixels (square block edge) | B | pending |
| `tgc_banding__detrended_residual_sigma_max` | nan (pending) | DN (detrended row-mean residual σ) | B | pending |
| `motion_blur__fascia_band_tenengrad_min` | nan (pending) | fascia-band Tenengrad (unit-free) | B | pending |
| `air_gap_reverberation__comb_peak_magnitude_min` | nan (pending) | FFT comb-peak magnitude (unit-free) | B | pending |
| `air_gap_reverberation__comb_period_px_min` | 3 | pixels (reverberation period) | B | pending |
| `air_gap_reverberation__comb_period_px_max` | 8 | pixels (reverberation period) | B | pending |
| `near_field_clutter__clutter_energy_max` | nan (pending) | near-field clutter energy (unit-free) | B | pending |
| `vessel_shadow_in_roi__column_darkness_max` | nan (pending) | DN (mean column intensity) | B | pending |
| `subq_fat_compressed__central_fat_thickness_mm_min` | 3.0 | mm (median central-zone fat thickness) | B | pending |
| `segmentation_low_confidence__boundary_entropy_max` | nan (pending) | nats (mean entropy over the mask-boundary band) | B | pending |
| `aggregate_tier__headline_secondary_valid_fraction_min` | 0.6 | fraction of the 8 gating-table measurements valid | B | pending |
| `frame_rf_consistency__modified_z_max` | 3.5 | Iglewicz-Hoaglin modified Z-score (MAD-based, unit-free) | A | n/a (Tier-A) |
| `frame_rf_consistency__min_frames` | 4 | frames (minimum sweep size for a robust outlier test) | A | n/a (Tier-A) |

## Acquisition-QA verdict vocabulary

### study_qa (operator `Kontrol Sonucu`) — verdict levels + reason phrases

`study_qa` renders one of three levels; `render_cell` prefixes the cell:

| Level | Prefix | Meaning |
|-------|--------|---------|
| fail | ❌ | a re-record-worthy problem: a missing modality (`femur/RF/sweep eksik`) or `gerçek RF yok` (RF on the femur plane) |
| warn | ⚠️ | a softer deviation: `RF hafif derin/sığ (Ncm)`, a ruler mismatch, a scored-role orphan (`… cine yok`), high brightness, or a deferred sub-cohort |
| ok | ✅ | `tam` — all three cines present, RF anchored |

### study_audit.CHECKS (analyst reconciliation registry — GENERATED via `legend()`)

| Code | Severity | Audience | Action | Boundary | Blurb |
|------|----------|----------|--------|----------|-------|
| NAME-COLLISION | WARN | OPERATOR | REMIND | B0 | a study NUMBER (date_count, e.g. 26jun26_021) is reused; full-name join keys differ so nothing breaks — a nudge for next time, both rows kept |
| SET-D-NO-Z | WARN | ANALYST | ASK | B0 | a datasheet row with no uploaded zip (upload pending?) |
| SET-M-NO-R | WARN | ANALYST | ASK | B0 | materialized but never scored (no run record) |
| SET-R-NO-M | WARN | ANALYST | ASK | B0 | a finalized run with no current materialized tree (ledger drift — e.g. a since-deleted study) |
| SET-Z-NO-D | WARN | ANALYST | ASK | B0 | a zip exists with no datasheet row (log it?) |
| ARTIFACT-COUNT | WARN | OPERATOR | ASK | B1 | the datasheet Artifacts M/R/D claim disagrees with the real zip count |
| MODALITY-MISSING | FAIL | OPERATOR | ASK | B1 | a required modality (femur / sweep) is absent |
| NAME-UNPARSED | WARN | OPERATOR | ASK | B1 | the study name does not parse via the one study-name grammar (joins/derived cols at risk) |
| OPERATOR-FOLDER-MISMATCH | WARN | OPERATOR | REMIND | B1 | the datasheet Operator disagrees with the zip's Drive folder (after alias-normalize) |
| WASHOUT | WARN | OPERATOR | REMIND | B1 | frames are washed out (mean luminance high) — echo-intensity features unusable |
| DEPTH-OFFSPEC | WARN | OPERATOR | REMIND | B2 | a materialized capture's depth is off-spec (wrong dial) — DEFERRED: v8 assigns each capture's depth by RELATIVE rank (deepest=femur), not a fixed protocol depth, so a deep-imaging probe (831 ~9cm) is in-spec; the loader leaves depth_in_spec=None until a fixed-depth protocol returns _(deferred)_ |
| MANIFEST-MISMATCH | WARN | ANALYST | ASK | B2 | the materialize manifest capture count disagrees with what was expected |
| MATERIALIZE-HOLD | WARN | ANALYST | ASK | B2 | held at materialize (not processed) with a reason |
| ORPHAN-RAWIMAGES | INFO | OPERATOR | AUTO | B2 | a re-recorded take's orphan RawImages was auto-excluded at materialize (source untouched) |
| PAIRING-AMBIGUOUS | WARN | ANALYST | ASK | B2 | a capture's mp4<->RawImages pairing is ambiguous — kept for review, not auto-acted |
| FAT-DEPTH>1 | FAIL | ANALYST | ASK | B3 | fat_to_depth > 1.0 — fat thicker than the imaged depth (impossible) |
| FRAME-DROPS | INFO | ANALYST | REMIND | B3 | surfaced: the union of per-frame drop_reasons + qmt_validity_flags behind the aggregate |
| INT-CV<0 | FAIL | ANALYST | ASK | B3 | int_cv_within < 0 — a coefficient of variation cannot be negative |
| PLACEMENT-BLANK | WARN | ANALYST | ASK | B3 | cohort placement is blank on a finalized run (data-lineage gap) |
| QMT-NULLED | WARN | ANALYST | ASK | B3 | QMT is null though a femur capture is present (the edge_clip_femur regression class) |
| RUN-NOT-FINALIZED | WARN | ANALYST | ASK | B3 | run(s) exist but none are finalized (draft-stuck) |
| COV-GATE | WARN | ANALYST | ASK | B4 | isokinetic CoV exceeds the reliability gate — isokinetic torque/power unreliable (isometric is the primary criterion and is always kept) |
| FORCE-BLANK | WARN | ANALYST | REMIND | B4 | a matched force row carried no values |
| FORCE-ORPHAN | WARN | ANALYST | REMIND | B4 | a force-sheet study has no datasheet (kayit) row |
| CHECK-ERROR | INFO | ANALYST | ASK | ENGINE | an audit check raised — fail-open diagnostic |

## Acquisition protocol — the 3-cine scheme (CURATED)

Each single-leg study is three cines, classified by **length + depth** (relative rank, no fixed protocol depth — `study_qa`):

| Cine | Length | Depth | Role in scoring |
|------|--------|-------|-----------------|
| **RF** | short | ~4.9 cm, ALWAYS shallow (the one fixed anchor) | texture (int_cv / echo) from the center-RF frame |
| **femur** | short | 6.9 cm+, FREE (deepest cine) | all geometry + fat + QMT/STAR |
| **sweep** | long | ~4.9 cm, MAY go deep | EI cross-check only (not scored) |

Roles are assigned by RELATIVE rank: femur = deepest cine, RF = shallowest short cine, sweep = longest clip. Robust to a tweaked-deeper femur and to probe 831 (~9cm) vs 829 (~4.9cm). The ONE absolute check: if even the shallowest capture sits on the femur plane (≥6.4 cm), no real RF was recorded → fail.

## Study encoding (CURATED)

- **study_uid** = `NR` + first 8 hex of sha256(folder-name), uppercased (`study_metadata.study_uid_for_dirname`). Collision-free (distinct studies have distinct folder names), PHI-safe, and identical to the new-round cohort's join key. **This is the `qa` tab's row key.**

- **AKA<seq>** = a DISPLAY pseudonym only (`lookup_study`). Its sequence numbers repeat across dates/annotators, so it is **NOT collision-safe** — never key identity on it.

- **run_study_uid** (`study_metadata.run_study_uid`) derives the same `NR`+sha8 from a ledger run's stored `study_path` basename — so the record overlay joins to the spine by study_uid, NOT by the colliding study_code.

## Standing assumptions & known exceptions (CURATED)

- **RF-deep = FLAG, not withhold.** A slightly-deep RF (`RF hafif derin (Ncm)`) is surfaced for the reviewer to verify; it does not withhold the measurement. Only RF on the femur plane (`gerçek RF yok`) is a re-record fail.

- **Texture withheld ⇒ no condition score.** When `int_cv_within` is null (11/66 new-round studies, low-reliability RF texture), headline C AND the no-femur fallback A both lose their texture axis → `condition_score` is blank. This is honest, not a bug.

- **QMT provenance tiers:** `standard_qmt` (femur-anchored), `femur_supported_proxy` (femur present but the standard bounds degrade), `no_femur_proxy` (no measurable femur). Only `standard_qmt` is fully trusted for QMT/STAR.

- **`configs/study_codes.csv` is a committed PHI exception** — it maps folder dirnames (PHI) to display pseudonyms and is deliberately in-repo (both repos private; no public flip without a PHI scrub). Every OTHER committed artifact (this dictionary included) is study_uid-only.

- **Batch spine rows are `auto-frames`** — measured on the automated top-5-by-RF-area selection. A row flips to `operator-frames (run <date>)` only when a finalized runs.jsonl run matches its study_uid (the record overlay). Same scorer either way: only the FRAME SELECTION differs, so this is a sensitivity readout, not a human verdict on the score.

- **femur_qmt_consistency is EXCLUDED** from the `qa` tab (deferred: selection-dependent + a provisional 0.05 CV threshold, memory `project_qmt_cv_flag_calibration`).

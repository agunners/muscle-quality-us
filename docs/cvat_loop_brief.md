# Brief for a fresh session — the segmentation-refinement loop through CVAT (the new cohort)

*Written 2026-09-04 for the session that builds this. Akadir: "I want to do this properly, I have time." Read this,
then `docs/DIRECTION.md`, then the two memories named at the end, then the notebooks. Do not start from the code.*

## The goal, in his words

"Select 3 frames from the lateral-medialis sweep, one from the lateral, one from the center and one from the medialis
side of the RF, just like I had manually selected and uploaded to the cvat, for the doctors/annotators to go ahead do
the segmentation annotations … upload these 3 frames, along with the initial segmentation (draft of the selected
model), so that annotators can refine. The refined annotations, with all the required data/modalities are collected
and served to the model training folder under ~/dev/muscle_model_training … so we improve the models as we get more
data, seamlessly."

## What exists (surveyed 2026-09-04, read-only)

- **CVAT** at `https://cvat.example.com` on the Hetzner host (admin credentials are in his 04sep26 notes doc,
  not in any file). One project, **319 tasks**, one per old-cohort study, **3 frames each**, task names
  `<operator>_<DDMonYY>_<NN>` (e.g. `anonabn_13Feb26_18`), status `annotation`, assignee = the annotator. Labels = the
  9-class palette in the old chassis `label_palette.json` (RF, VI, …, fasya, skin, bone, fat).
- **The old pipeline** (`~/dev/goruntuler/scripts/`): `build_transport_dataset.py` (frames + channels + masks →
  `transport/` + `transport_metadata.csv`), `ingest_cvat_annotations.py`, `check_cvat_sync.py`,
  `validate_annotations.py`, `smoke_cvat_export.py`, `smoke_multi_xml_inbox.py`. Read them before writing anything.
- **Training** (`~/dev/muscle_model_training/`): `transport/` is the canonical training data (`transport_metadata.csv`);
  README flow: transport arrives → `scripts/transform_metadata.py --skip-base` → `scripts/merge_metadata.py` → the
  training notebook (fastai; latest `notebooks/0319_voi_hist_clahe_45seed_native_512x256_v5___300_samples____fold4_…`)
  → `apps/study_review_app.py` for outlier frames. The served model in muscle_mania is the bundle
  `native_512x256_v5___300_samples____fold4` (5-fold, 80 % training data).
- **The new cohort** (`muscle_mania/data/studies_root/`, registry `data/study_registry.csv`): 175 r5 studies, each with
  a sweep folder (`RawImagesForResearch_*`, 63–150 jpg frames lateral → medial), a femur cine and a centre-RF cine
  (15 frames each). Frame files are timestamp-named; **folder and mp4/dcm names carry patient names** (PHI): every
  artefact that leaves the machine is uid-keyed and passes the name gate (see the grading pack builder).

## Decisions already taken (his words, 04sep26 evening)

- Annotators: the existing CVAT users **anonabn, anonacl, anonabx, anonaaq**, tasks assigned equally.
- Frame selection: mine — **the cleanest large-RF frame in each third of the sweep** (lateral, centre, medial), using
  the same ranker the freeze uses (`repr_scorer.top_clean_frames` on the frozen bundle's masks), falling back to the
  third's middle frame when no clean frame exists. He will criticise when something goes wrong.
- Draft masks: the **frozen bundle** that produced the served numbers.
- Retraining sequence (my recommendation, he agreed in principle): final `annotation.xml` → a new transport →
  **five-fold models as the evidence + one all-data model for deployment** (not a single 93 % split) → the
  re-measure-and-diff comparability gate (NORTHSTAR R6) → only then a freeze on the new model. A model change moves
  every served number and every packed frame.

## The build, in order

1. **Read** the CVAT ingest/validate scripts and the transport builder; run `check_cvat_sync.py` against the live
   server to learn the exact task/label/XML shapes. Open the latest training notebook end to end.
2. **`scripts/cvat/build_cvat_pack.py`** in muscle_mania (GPU): per r5 study → three sweep frames by the rule above →
   predict masks with the frozen bundle → write frames + masks in the shape the old transport used (`_final.png`,
   `_mask.png`, spacing from the study's dicom) under uid-named task folders; a manifest with sha256; the PHI name
   gate (reuse `build_grading_pack._run_name_gate`).
3. **Upload** through the CVAT API: one task per study, named per the convention with the operator/session tokens
   the old tasks used (derive from the registry, never the folder name), 3 frames, pre-annotations imported as
   masks/polygons, assignee round-robin over the four users. Smoke on 2 tasks first; then the 175.
4. **Ingest** refined annotations with the existing scripts into a new transport tree in `muscle_model_training`;
   `transform_metadata.py` / `merge_metadata.py`; verify counts.
5. **Retrain** (five-fold + all-data), record Dice per fold, compare with the served bundle on the same folds; then the
   R6 diff before any freeze.

## Effort

About two sessions for steps 1–3, one for step 4, one or more for step 5 (GPU time).

## Memories to read first

`reference_cvat_segmentation_loop`, `project_grading_table_build`, `feedback_phi_guardrail`,
`feedback_heredoc_backticks_execute`, `feedback_venv_copy_between_releases`.

# muscle_mania

Research code for measuring the quadriceps on musculoskeletal ultrasound: segmentation-based measurements of the rectus femoris and the
vastus intermedius (thickness, quadriceps muscle thickness, echo intensity, fat and texture features), frame-quality checks, cohort
placement and a condition-score model, and a Streamlit app for labelling and per-case assessment.

It is research code. It is not a medical device and it is not validated for clinical use.

## What is not in this repository

- **No patient data.** Ultrasound images, study folders, the study registry, per-study measurements and cohort tables stay with the
  clinical team. Study names inside test fixtures are pseudonymised (`anon…`).
- **No trained models.** The segmentation weights and the fitted condition and force models were trained on patient data and are not
  distributed.
- **No segmenter training code.** It needs the trained weights, which are not distributed.
- **No internal notes.** Planning documents, reports and working notes were left out; the technical documents under `docs/` remain.

The app and the data pipelines therefore need your own studies, laid out as `docs/DATA_LIFECYCLE.md` and
`docs/measurement_and_labeling_reference.md` describe. `configs/round3_intake.example.json` shows the intake configuration with
placeholder identifiers.

## Install and test

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest
```

On x86_64 Linux, add `--extra-index-url https://download.pytorch.org/whl/cpu` to the install line to get the CPU build of torch
instead of the much larger CUDA one; Linux aarch64 gets the CPU build by default.

Tests that need the private study data are skipped with the reason "needs the private study data"; the list is in `conftest.py`.
Every push runs the same install and tests on GitHub (`.github/workflows/tests.yml`).

## Layout

- `labeling_app/`: the Streamlit app and the measurement library (`measurements.py`, `visualization.py`, `data.py`, the scorers).
- `rf_quality/`: the feature and quality-check package the pipeline imports (from the sibling exploration project).
- `scripts/`: ingest, preflight, quality checks, cohort statistics and the analysis scripts.
- `tests/`: unit, preflight and UI tests.
- `docs/`: methods, specifications, operator checklists and runbooks.
- `deploy/`: example systemd units and server configuration.

## Licence

MIT, see `LICENSE`.

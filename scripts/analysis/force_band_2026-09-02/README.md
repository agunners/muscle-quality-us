# agent_force_band — the force band, decomposed (2026-09-02)

Read-only analysis against the live repo (`/home/akadir/dev/muscle_mania`, HEAD `ad9c32a`, `data/qa_master.csv` sha256₁₆ `5a0ad2a3f3418385`). Nothing in the repo was written or modified. Row set = SENS_A_no_texture (n = 143), the served strength card's fit set; every metric is nested-CV out-of-fold with the harness's own models.

**Deliverable:** `force_band_decomposition.md` — bottom line, receipts, Q1–Q6, caveats, machine-generated appendix.

| File | What it is |
|---|---|
| `common.py` | loader: imports `scripts/analysis/force_ladder.py` for row sets and `make_model`; substrate sha; PHI-guarded frame |
| `oof.py` | harness-identical nested-CV OOF engine (verified bit-identical to `force_ladder_results.csv` at seed 0) |
| `features.py` | feature-set definitions (tabular rungs; capture-feature bank with meta + muscle-size columns removed; fat-like split) |
| `verify_rowsets.py` → `verify_rowsets.json` | receipts: row sets, `fit_uids` == SENS_A, seed-0 reproduction, capture overlap, force coverage |
| `q1_q4_band_decomposition.py` → `q1_q4_results.json`, `oof_residuals_sensA.csv` | Q1 decomposition + k-session + CV subsets; Q4 sex × age bands; the 10-seed residual pool |
| `q2_learning_curve.py` → `q2_results.json`, `q2_learning_curve_points.csv` | Q2 learning curve + 1/n, log, √n extrapolations |
| `q3_feature_ceiling.py` → `q3_results.json` | Q3 feature ceiling (tabular, capture bank, HGB), model-form variants, heteroscedasticity |
| `q5_residual_target.py` → `q5_results.json` | Q5 residual reliability, correlations, permutation-null nested prediction, circularity variants |
| `*.out` | stdout of each run (warnings included) |

Re-run order: `verify_rowsets.py` → `q1_q4_band_decomposition.py` → (`q2`, `q3`, `q5` in any order; q2 and q5 read q1's outputs). Each takes 1–7 min on CPU with `source scripts/env.sh && .venv/bin/python <script>`.

"""Single source of truth for paths used by the preflight pipeline."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------- Data corpus (read-only, external)
GORUNTULER_ROOT = Path(str(Path(__file__).resolve().parents[2] / "private" / "goruntuler"))
GORUNTULER_BACKUP = Path(str(Path(__file__).resolve().parents[2] / "private" / "goruntuler_backup"))
ANNOTATOR_MASKS_ROOT = GORUNTULER_BACKUP / "annotations" / "masks"

# Transport (frames + masks + processed channels) — symlinked into muscle_mania
TRANSPORT_ROOT = REPO_ROOT / "transport"
TRANSPORT_METADATA = TRANSPORT_ROOT / "transport_metadata.csv"

# Decision log + study codes for cohort-side metadata
DECISION_LOG = REPO_ROOT / "configs" / "decision_log.csv"  # in-repo (was a codex-variant path)
# The 3-operator quality-gate log (gitignored, machine-local) — the source of
# the "alive cohort" filter (akadir AND completed -> 272 studies). The
# muscle_mania-local decision_log.csv is an unrelated `me` self-test stub.
DECISION_LOG_3OP = REPO_ROOT / "exports" / "decision_log_full_3operators.csv"
STUDY_CODES_CSV = REPO_ROOT / "configs" / "study_codes.csv"

# Cohort stats (pre-computed)
COHORT_STATS_DIR = REPO_ROOT / "data" / "cohort_stats"
COHORT_SUMMARY = COHORT_STATS_DIR / "cohort_summary.json"
TRIPLE_PLACEMENT = COHORT_STATS_DIR / "triple_placement.json"

# ---------- Model registry
MODEL_DIR_LOCAL = REPO_ROOT / "model_"  # bundles shipping with muscle_mania
MODEL_DIR_TRANSPORT = Path(str(Path(__file__).resolve().parents[2] / "private" / "muscle_model_training/transport/models"))  # 5-fold ensemble
PALETTE_PATH = MODEL_DIR_LOCAL / "label_palette.json"

# ---------- Runs / ledger / per-run artefacts (local-only, gitignored)
DATA_ROOT = REPO_ROOT / "data"
RUNS_JSONL = DATA_ROOT / "runs.jsonl"
RUNS_DIR = DATA_ROOT / "runs"
# N4 run lifecycle: a phase-5 result is a *draft* (scratch dir) until the user
# clicks "Accept run". Drafts never enter runs.jsonl — they live here, one
# JSON per run_id, gitignored, swept by run_lifecycle.gc_drafts().
DRAFTS_DIR = DATA_ROOT / "runs_drafts"
TRAINING_CANDIDATES_DIR = DATA_ROOT / "training_candidates"
PREFLIGHT_DIR = DATA_ROOT / "preflight"
STUDIES_ROOT = DATA_ROOT / "studies_root"
# L3b screenshot walkthrough artefacts (gitignored).
TEST_ARTIFACTS_DIR = DATA_ROOT / "test_artifacts"


def ensure_local_dirs() -> None:
    for d in (RUNS_DIR, DRAFTS_DIR, TRAINING_CANDIDATES_DIR, PREFLIGHT_DIR,
              STUDIES_ROOT, DATA_ROOT):
        d.mkdir(parents=True, exist_ok=True)

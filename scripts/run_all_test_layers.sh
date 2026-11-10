#!/usr/bin/env bash
# CI entrypoint — runs the 4-layer verification ladder L1 → L2 → L3a → L3b in
# order. `set -e` so each layer gates the next: if L1 fails the rest never run.
# L4 (red-team sub-agent review of the L3b screenshots) is the agent step
# *after* this script — it is not automatable here.
#
# Usage:  source scripts/env.sh && bash scripts/run_all_test_layers.sh
#   (env.sh must already be sourced — this script does not source it for you,
#    so a missing PYTHONPATH bridge surfaces loudly rather than being masked.)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
PY="$REPO/.venv/bin/python"

# Belt-and-braces: the Python launchers self-bootstrap the bridge, but warn if
# the caller forgot to `source scripts/env.sh`.
if ! "$PY" -c "import rf_quality" 2>/dev/null; then
  echo "[note] rf_quality not importable from a bare python — relying on each"
  echo "       launcher's env_bootstrap fallback. Prefer: source scripts/env.sh first."
fi

sep() { echo; echo "============================================================"; echo "$1"; echo "============================================================"; }

# ---------------------------------------------------------------------------
sep "L1 — pre-UI HARD GATE"
# ---------------------------------------------------------------------------
echo "[L1.1] runtime bridge"
"$PY" -c "from rf_quality.config import AppCfg; from fastai.learner import load_learner; print('  bridge OK')"

echo "[L1.2] model bundle (verify_model.py)"
"$PY" scripts/verify_model.py

echo "[L1.3] stage1 — AGZ855 annotator-mask reproduction"
"$PY" scripts/preflight.py stage1 --study-code AGZ855

echo "[L1.4] stage2 — AGZ855 predicted-mask smoke"
# stage2 returns 5 (WARN) for the *documented* model VI over-prediction on
# AGZ855 — acceptable per the verification-ladder memory. Only a hard FAIL
# (any other non-zero) halts the ladder; silent corruption is the thing this
# guards against, and the headline RF/full-thickness/int_cv_within features
# staying within band is what proves it is not that.
set +e
"$PY" scripts/preflight.py stage2 --study-code AGZ855
s2=$?
set -e
if [ "$s2" -eq 0 ]; then
  echo "  stage2 PASS"
elif [ "$s2" -eq 5 ]; then
  echo "  stage2 WARN (exit 5) — documented AGZ855 VI over-prediction; acceptable."
else
  echo "  stage2 FAILED (exit $s2) — not a documented deviation; STOP."
  exit "$s2"
fi

echo "[L1.5] brain-doc continuity-gate grep (must be empty)"
if grep -rn "thickness-vs-full_thickness\|continuity gate.*thickness" \
     ~/dev/brain/wiki ~/dev/brain/decisions 2>/dev/null; then
  echo "  FAIL — stale continuity-gate wording still present"; exit 1
else
  echo "  clean"
fi

echo "[L1.6] stage3 — non-canonical sweep frame"
MANIFEST="$(mktemp /tmp/preflight_stage3_noncanon_XXXX.json)"
"$PY" - "$MANIFEST" <<'PYEOF'
import json, sys
sys.path.insert(0, "scripts")
import preflight_helpers.env_bootstrap  # noqa
from preflight_helpers import env
from preflight_helpers.study_discovery import discover_studies, list_frame_bases
COHORT_TRIPLE = {"20260127_151031646", "20260127_151045625", "20260127_151052505"}
entry = next(e for e in discover_studies() if e.meta.study_code == "AGZ855")
bases = list_frame_bases(entry.raw_image_dir)
noncanon = next(b for b in bases if b not in COHORT_TRIPLE)
manifest = {
    "study_path": str(entry.study_path),
    "study_code": "AGZ855",
    "frames": [{"frame_base": noncanon, "is_anchor": True,
                "gates": {"usable": True, "segmentation_perfect": False,
                          "use_for_analysis": True}}],
}
with open(sys.argv[1], "w") as f:
    json.dump(manifest, f, indent=2)
print(f"  manifest frame = {noncanon} (non-canonical)")
PYEOF
"$PY" scripts/preflight.py stage3 --manifest "$MANIFEST" --model transport-default
rm -f "$MANIFEST"

# ---------------------------------------------------------------------------
sep "L2 — pytest component tests"
# ---------------------------------------------------------------------------
"$PY" -m pytest tests/ --ignore=tests/ui -q

# ---------------------------------------------------------------------------
sep "L3a — Streamlit AppTest click-through"
# ---------------------------------------------------------------------------
"$PY" -m pytest tests/ui -q

# ---------------------------------------------------------------------------
sep "L3b — Playwright headless screenshot walkthrough"
# ---------------------------------------------------------------------------
"$PY" scripts/ui_screenshot_walkthrough.py

sep "ALL LAYERS PASSED — L1 → L2 → L3a → L3b"
echo "Next: L4 — the implementing agent Reads data/test_artifacts/latest/*.png"
echo "      and writes a per-screenshot assessment, then a red-team sub-agent"
echo "      independently reviews the same screenshots + manifest.json."

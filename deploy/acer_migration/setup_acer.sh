#!/usr/bin/env bash
# setup_acer.sh — rebuild the FOUR .venvs on the Acer from requirements. Runs seamlessly:
# auto-detects python, checks the venv module up front, then builds (no prompts).
# Skip the build with: SETUP_ACER_NOBUILD=1 bash setup_acer.sh
set -uo pipefail
DEV="$HOME/dev"
JV="$HOME/Downloads/june_protocol_goruntuler/_viewer"

# --- pick a python (prefer 3.12 to match the DGX; fall back gracefully) ---
PY=""
for c in python3.12 python3.11 python3.10 python3; do command -v "$c" >/dev/null 2>&1 && { PY=$c; break; }; done
[ -n "$PY" ] || { echo "ERROR: no python3 found —  sudo apt install python3 python3-venv"; exit 1; }
echo "=== python: $PY ($($PY --version 2>&1)) ==="
if ! "$PY" -c 'import ensurepip' 2>/dev/null; then
  echo "ERROR: '$PY' cannot create venvs (ensurepip missing). Fix then re-run:"
  echo "   sudo apt install ${PY}-venv     # or:  sudo apt install python3-venv"
  exit 1
fi

echo "=== GPU / driver ==="
command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi | sed -n '1,12p' || echo "no nvidia-smi — CPU-only (segmenter still runs)."
echo "(Acer = CUDA 13.0, same as the DGX, so torch from requirements.txt should just work.)"
echo "Exact DGX pins if ever needed: <T7>/muscle_migration/freezes/*.freeze.txt"

[ "${SETUP_ACER_NOBUILD:-0}" = 1 ] && { echo "SETUP_ACER_NOBUILD set — skipping the builds."; exit 0; }

build(){  # label  repo-dir — replicates your ~/.bashrc ignite(): venv + requirements-dgx/fastai/txt
  local d="$2"
  echo; echo "---- $1 ----"
  [ -d "$d" ] || { echo "  missing repo dir, skip"; return; }
  if ( set -e
       cd "$d"
       "$PY" -m venv .venv
       .venv/bin/pip install -U pip wheel
       [ -f requirements-dgx.txt ]    && .venv/bin/pip install -r requirements-dgx.txt
       [ -f requirements-fastai.txt ] && { .venv/bin/pip install -r requirements-fastai.txt
                                           .venv/bin/pip install segmentation-models-pytorch==0.5.0 fastai==2.8.5 --no-deps; }
       [ -f requirements.txt ]        && .venv/bin/pip install -r requirements.txt
       # 2026-09-05: muscle_model_training keeps its REAL list here — without this its venv
       # comes up with the Jupyter stack only (no fastai/smp/timm/pydicom) and nothing fails
       # until you try to retrain.
       [ -f requirements_final.txt ]  && .venv/bin/pip install -r requirements_final.txt
       exit 0 ); then
    echo "  $1 venv OK"
  else
    echo "  $1 venv FAILED — see the pip error above."
    echo "    exact-env fallback:  cd $d && .venv/bin/pip install -r <T7>/muscle_migration/freezes/$1.freeze.txt"
  fi
}
build muscle_mania               "$DEV/muscle_mania"
# The render walk (scripts/ui_screenshot_walkthrough.py) needs the BROWSER, not just the pip
# package. Cheap and idempotent; skip with SETUP_ACER_NO_BROWSER=1.
if [ "${SETUP_ACER_NO_BROWSER:-0}" != 1 ] && [ -x "$DEV/muscle_mania/.venv/bin/python" ]; then
  echo "---- playwright chromium (for render+LOOK) ----"
  "$DEV/muscle_mania/.venv/bin/python" -m playwright install chromium >/dev/null 2>&1 \
    && echo "  chromium OK" || echo "  ⚠ chromium install failed — run it by hand before any UI verification"
fi
build muscle_quality_exploration "$DEV/muscle_quality_exploration"
build muscle_model_training      "$DEV/muscle_model_training"
build june_viewer                "$JV"

# 2026-09-05 — goruntuler: .git/config sets an nbstripout clean filter that runs
# <repo>/.venv/bin/python3. Without that venv EVERY git command there fails
# ("clean filter 'nbstripout' failed"), including the unpacker's tracked-file restore.
echo; echo "---- goruntuler (nbstripout filter venv only) ----"
if [ -d "$DEV/goruntuler/.git" ]; then
  ( set -e; cd "$DEV/goruntuler"; "$PY" -m venv .venv
    .venv/bin/pip install -q -U pip nbstripout
    git status --short >/dev/null ) \
    && echo "  goruntuler filter OK (git works)" || echo "  goruntuler filter FAILED — git ops there will error"
else
  echo "  missing repo dir, skip"
fi

# 2026-09-04 — the grading table: a plain venv (streamlit 1.52.2, pandas, numpy, pillow, pyyaml, pytest, scipy)
echo; echo "---- muscle_grading_table ----"
if [ -d "$DEV/muscle_grading_table" ]; then
  ( set -e; cd "$DEV/muscle_grading_table"; "$PY" -m venv .venv; .venv/bin/pip install -U pip wheel
    .venv/bin/pip install -r requirements-analysis.txt; .venv/bin/python -m grading_app.cli doctor ) \
    && echo "  muscle_grading_table venv OK (doctor passed)" || echo "  muscle_grading_table venv/doctor FAILED — see above"
else
  echo "  missing repo dir, skip"
fi

cat <<'EOF'

=== the two apps (your definition of done) ===
1) muscle_mania assessment app
   cd ~/dev/muscle_mania && source scripts/env.sh
   .venv/bin/python -m pytest tests/preflight -q      # floor, not proof
   ./run_app.sh                                       # :8502 -> render AKA012 + LOOK

2) june viewer — SINGLE SOURCE (the SAME studies the assessment app scores)
   VIEWER_DATA_ROOT=~/dev/muscle_mania/data/studies_root_flat \
     bash ~/Downloads/june_protocol_goruntuler/_viewer/run.sh   # :8520 -> all 66 studies
   # (drop VIEWER_DATA_ROOT to fall back to the viewer's own ~/Downloads copy = the legacy 21)

gws re-auth first (per-machine). Femur class=7 is in code; gh pushes from your terminal.
EOF

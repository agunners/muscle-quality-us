#!/usr/bin/env bash
# Launch (or stop) the muscle_mania assessment app for a local click-through.
#
#   ./run_app.sh            # start — first free port at/above 8502
#   ./run_app.sh 8600       # start — first free port at/above 8600
#   ./run_app.sh stop       # stop every assessment-app instance
#
# Robust by construction:
#   - cd's to the repo root, sources scripts/env.sh (the runtime bridge), and
#     uses the repo's own .venv streamlit;
#   - if the port is already taken — usually a leftover instance from an
#     earlier run, the common cause of "it doesn't work" — it steps to the
#     next free port instead of failing;
#   - headless: no browser auto-open (that auto-open can race the server and
#     leave the page stuck on the spinner) — it prints the URL for you.
#
# Stop the app with Ctrl-C in its terminal; if that terminal is gone, run
# `./run_app.sh stop`.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO"

APP="labeling_app/assessment_app.py"
_MATCH="streamlit run.*assessment_app\.py"   # matches every assessment-app server

# --- stop mode -------------------------------------------------------------
if [[ "${1:-}" == "stop" ]]; then
    pids="$(pgrep -f "$_MATCH" 2>/dev/null || true)"
    if [[ -z "$pids" ]]; then
        echo "No assessment-app instance is running."
        exit 0
    fi
    for pid in $pids; do
        echo "Stopping assessment app (pid $pid)."
        kill "$pid" 2>/dev/null || true
    done
    exit 0
fi

# --- start mode ------------------------------------------------------------
if [[ ! -x .venv/bin/streamlit ]]; then
    echo "ERROR: .venv/bin/streamlit not found — the muscle_mania .venv is not set up here." >&2
    exit 1
fi

# Default 8502, not Streamlit's usual 8501: on this machine the browser holds a
# stale cache pinned to the :8501 origin that leaves the page stuck on the
# spinner — any other port loads clean (the :8501 *server* itself is fine, curl
# returns 200). Pass `8501` explicitly if that browser state is ever cleared.
PORT="${1:-8502}"
if ! [[ "$PORT" =~ ^[0-9]+$ ]]; then
    echo "ERROR: '$PORT' is not a port number. Usage: ./run_app.sh [PORT|stop]" >&2
    exit 1
fi

# Step past any port already in use (a leftover instance, most often).
port="$PORT"
while ss -ltnH 2>/dev/null | grep -qE "[^0-9]${port}[[:space:]]"; do
    port=$((port + 1))
done
if [[ "$port" != "$PORT" ]]; then
    echo "note: port ${PORT} is busy (likely a leftover instance — clear those"
    echo "      with './run_app.sh stop') — using port ${port} instead."
fi

# scripts/env.sh exports the rf_quality + segmenter PYTHONPATH bridge; it must
# be sourced, never executed in a subshell (it says so itself).
# shellcheck disable=SC1091
source scripts/env.sh

echo
echo "  Starting the assessment app ..."
echo "  In ~15 s (torch/fastai load) a 'URL:' line prints below. Then open"
echo "      http://127.0.0.1:${port}"
echo "  in a browser. If a tab ever sticks on the loading spinner, hard-"
echo "  refresh it (Ctrl-Shift-R). Stop: Ctrl-C here, or  ./run_app.sh stop"
echo

exec .venv/bin/streamlit run "$APP" \
    --server.port "${port}" \
    --server.address 127.0.0.1 \
    --server.headless true \
    --browser.gatherUsageStats false

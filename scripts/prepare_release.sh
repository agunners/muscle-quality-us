#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RELEASE_ID="${1:-$(date -u +%Y%m%dT%H%M%SZ)}"
OUT_DIR="${2:-$ROOT/dist/releases/$RELEASE_ID}"

mkdir -p "$OUT_DIR"

rsync -av --delete \
  --exclude '.claude/' \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude '.playwright-cli/' \
  --exclude 'configs/labeling.yaml' \
  --exclude 'configs/study_codes.csv' \
  --exclude 'dist/' \
  --exclude 'transport/' \
  --exclude 'runtime/' \
  --exclude 'exports/' \
  --exclude 'runs/' \
  --exclude 'tmp/' \
  --exclude 'output/' \
  --exclude 'decision_log*.csv' \
  "$ROOT/" "$OUT_DIR/"

printf 'Prepared release: %s\n' "$OUT_DIR"

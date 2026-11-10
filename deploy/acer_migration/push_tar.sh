#!/usr/bin/env bash
# push_tar.sh — DGX -> T7 (UNENCRYPTED, practical). Plain tar = ONE sequential stream per repo,
# which is exfat's happy path (no loop device, no mkfs, no sudo). Preserves symlinks
# (muscle_mania/transport), exec bits, and .git.
#
# PHI: these .tar files hold patient folder names in CLEARTEXT on the T7. pull_tar.sh reminds
#      you to delete them off the drive once the Acer has unpacked + verified.
set -euo pipefail

T7="${1:-${T7:-/media/akadir/T7 Shield}}"
DEV="$HOME/dev"; DL="$HOME/Downloads"
PROMPTED="$HOME/dev/muscle_quality_labeling_exercise_claude_PROMPTED"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="$T7/muscle_migration"
die(){ echo "ERROR: $*" >&2; exit 1; }
[ -d "$T7" ] || die "T7 not mounted at: $T7"
mkdir -p "$OUT/freezes"

COMMON=(--warning=no-file-changed --warning=no-file-removed
        --exclude='*/.venv' --exclude='*/__pycache__' --exclude='*/.pytest_cache'
        --exclude='*/.ipynb_checkpoints' --exclude='*/.mypy_cache' --exclude='*.pyc'
        --exclude='*/.DS_Store')

tar_one(){  # name  parent  basename  [extra excludes...]
  local name="$1" parent="$2" base="$3"; shift 3
  echo; echo "---- $name  (tar $parent/$base) ----"
  [ -e "$parent/$base" ] || { echo "  MISSING: $parent/$base — skipped"; return 0; }
  tar -cf "$OUT/$name.tar" "${COMMON[@]}" "$@" -C "$parent" "$base" \
    || { local rc=$?; [ "$rc" = 1 ] || { echo "  tar FAILED (rc=$rc)"; return 1; }; }
  echo "  -> $name.tar  $(du -h "$OUT/$name.tar" | cut -f1)"
}

freeze(){ [ -x "$2/bin/python" ] && "$2/bin/python" -m pip freeze >"$OUT/freezes/$1.freeze.txt" 2>/dev/null \
            && echo "  froze $1 ($(wc -l <"$OUT/freezes/$1.freeze.txt") pkgs)" || true; }
echo "== venv freezes (exact pins for the Acer rebuild) =="
freeze muscle_mania               "$DEV/muscle_mania/.venv"
freeze muscle_quality_exploration "$DEV/muscle_quality_exploration/.venv"
freeze muscle_model_training      "$DEV/muscle_model_training/.venv"
freeze june_viewer                "$DL/june_protocol_goruntuler/_viewer/.venv"
freeze muscle_grading_table       "$DEV/muscle_grading_table/.venv"

tar_one muscle_mania "$DEV" muscle_mania \
  --exclude='muscle_mania/data/intake_cache' --exclude='muscle_mania/runtime' --exclude='muscle_mania/download.txt'

tar_one muscle_quality_exploration "$DEV" muscle_quality_exploration \
  --exclude='muscle_quality_exploration/muscle_metrics_and_the_target_09Nisan' \
  --exclude='muscle_quality_exploration/muscle_metrics_and_the_target_09Nisan_ch' \
  --exclude='muscle_quality_exploration/runs'

tar_one muscle_model_training "$DEV" muscle_model_training \
  --exclude='muscle_model_training/backups' --exclude='muscle_model_training/channels_roi' \
  --exclude='muscle_model_training/harmonized' \
  --exclude='muscle_model_training/DeepACSA_1' --exclude='muscle_model_training/DeepACSA_2' \
  --exclude='muscle_model_training/DeepACSA_3' --exclude='muscle_model_training/DeepACSA_4' \
  --exclude='muscle_model_training/DeepACSA_5' \
  --exclude='muscle_model_training/temporary_new_repo_will_be_removed'
# (2026-09-04: notebooks/, kaggle_notebooks/, data/, PURE_DICOM_VALIDATION/ and hacettepe_test_images* now
#  TRAVEL — the CVAT/retraining session on the Acer reads the notebooks end to end; ≈1.1 GB.)

# 2026-09-04 — the grading table (its own repo, full .git + item lists + versions; .venv excluded by COMMON;
# the 6.4 GB grading PACK travels INSIDE muscle_mania.tar at data/grading_pack/ — nothing to do here)
tar_one muscle_grading_table "$DEV" muscle_grading_table \
  --exclude='muscle_grading_table/dist' --exclude='muscle_grading_table/output'
# the CVAT ingest scripts + the raw operator exports (the segmentation-loop session starts from these)
tar_one goruntuler "$DEV" goruntuler
# the old labeler chassis WITHOUT its transport (that is prompted_transport.tar): the palette the pack
# builder reads, the old round's runtime DB + export (the archive of the 960-row round), the configs
tar_one prompted_chassis "$HOME/dev" muscle_quality_labeling_exercise_claude_PROMPTED \
  --exclude='muscle_quality_labeling_exercise_claude_PROMPTED/transport' \
  --exclude='muscle_quality_labeling_exercise_claude_PROMPTED/dist' \
  --exclude='muscle_quality_labeling_exercise_claude_PROMPTED/tmp' \
  --exclude='muscle_quality_labeling_exercise_claude_PROMPTED/output' \
  --exclude='muscle_quality_labeling_exercise_claude_PROMPTED/.playwright-cli'

# the segmenter/cohort-provenance target of the muscle_mania/transport symlink
tar_one prompted_transport "$PROMPTED" transport

# the june recordings + the _viewer app (its .venv is excluded by COMMON)
tar_one june_protocol_goruntuler "$DL" june_protocol_goruntuler

# reference-cohort frames (env.py:10 goruntuler_backup) — the 272 cohort validate off these
if [ -d "$HOME/dev/goruntuler_backup" ]; then
  echo; echo "---- goruntuler_backup (272-cohort frames) ----"
  tar -cf "$OUT/goruntuler_backup.tar" "${COMMON[@]}" -C "$HOME/dev" goruntuler_backup \
    && echo "  -> goruntuler_backup.tar $(du -h "$OUT/goruntuler_backup.tar" | cut -f1)"
fi
# studies_root operators that are SYMLINKS (-> goruntuler) -> --dereference to REAL data (self-contained)
srsl=(); for op in "$DEV/muscle_mania/data/studies_root"/*; do [ -L "$op" ] && srsl+=("$(basename "$op")"); done
if [ "${#srsl[@]}" -gt 0 ]; then
  echo "---- studies_root symlinked operators (deref -> real): ${srsl[*]} ----"
  tar --dereference -cf "$OUT/studies_root_symlinked_ops.tar" --warning=no-file-changed --warning=no-file-removed \
    -C "$DEV/muscle_mania/data/studies_root" "${srsl[@]}"
else
  # 2026-09-04: no operator is a symlink any more — a STALE archive from an older pack must not survive, or the
  # arrival helper would unpack old-era folders over newer data (caught in the simulated arrival).
  rm -f "$OUT/studies_root_symlinked_ops.tar"
fi
# decision_log + env.py fix on the drive (decision_log is gitignored — clinical fields, PHI-cautious)
mkdir -p "$OUT/fixes"
cp "$DEV/muscle_mania/configs/decision_log.csv" "$OUT/fixes/" 2>/dev/null || true
cp "$DEV/muscle_mania/scripts/preflight_helpers/env.py" "$OUT/fixes/" 2>/dev/null || true

cp -v "$HERE/pull_tar.sh" "$HERE/setup_acer.sh" "$HERE/apply_fixes_acer.sh" "$HERE/audit_tars.sh" "$HERE/RUNBOOK.md" "$OUT/" 2>/dev/null || true
sync
echo; echo "== DONE — tars on the T7 =="; du -sh "$OUT"/*.tar 2>/dev/null; echo "total: $(du -sh "$OUT" | cut -f1)"
echo "Eject cleanly:  sync && udisksctl unmount -b /dev/sda1   (then unplug)"

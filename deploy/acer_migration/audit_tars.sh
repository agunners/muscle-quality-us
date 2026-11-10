#!/usr/bin/env bash
# audit_tars.sh — verify the migration tars are intact + hold the load-bearing members.
# Run on the DGX after push, and/or on the Acer before unpack.  Read-only.
set -uo pipefail
T7="${1:-${T7:-/media/akadir/T7 Shield}}"
OUT="$T7/muscle_migration"
[ -d "$OUT" ] || { echo "no muscle_migration/ at $OUT"; exit 1; }
tmp=$(mktemp -d)

chk(){ # name  must-have(|)  must-NOT(|)
  local t="$OUT/$1.tar"; echo; echo "==== $1.tar ===="
  [ -f "$t" ] || { echo "  MISSING FILE"; return; }
  if tar -tf "$t" >"$tmp/$1" 2>"$tmp/$1.err"; then
    echo "  integrity: OK ($(wc -l <"$tmp/$1") members)"
  else
    echo "  integrity: FAIL — archive truncated/corrupt:"; sed 's/^/    /' "$tmp/$1.err"; return
  fi
  local IFS='|' m
  for m in $2; do grep -qE "$m" "$tmp/$1" && echo "  ok  has   $m" || echo "  ✗✗  MISSING $m"; done
  for m in $3; do grep -qE "$m" "$tmp/$1" && echo "  ✗✗  LEAKED $m" || echo "  ok  excl  $m"; done
}

chk muscle_mania \
  'muscle_mania/transport$|data/studies_root/|cohort_stats/per_study_cohort.csv|muscle_mania/.git/|scripts/env.sh|scripts/preflight_helpers/env.py|data/grading_pack/pack_r5_20260904/manifest.json|data/study_registry.csv' \
  'data/intake_cache|muscle_mania/.venv/'
chk muscle_model_training \
  'transport/models/|muscle_model_training/requirements.txt|muscle_model_training/src/|muscle_model_training/notebooks/' \
  'muscle_model_training/backups/|muscle_model_training/.venv/|channels_roi'
chk muscle_quality_exploration \
  'muscle_quality_exploration/rf_quality/|muscle_quality_exploration/requirements.txt' \
  '09Nisan|muscle_quality_exploration/.venv/'
chk prompted_transport 'transport/transport_metadata.csv' 'x_no_exclude_x'
chk muscle_grading_table \
  'muscle_grading_table/grading_app/app.py|muscle_grading_table/item_lists/b1/manifest.json|muscle_grading_table/docs/prereg_b1_r5.md|muscle_grading_table/.git/' \
  'muscle_grading_table/.venv/'
chk goruntuler 'goruntuler/scripts/ingest_cvat_annotations.py|goruntuler/.git/' 'goruntuler/.venv/'
chk prompted_chassis \
  'label_palette.json|runtime/labeling.db|configs/study_codes.csv|exports/decision_log.csv' \
  'muscle_quality_labeling_exercise_claude_PROMPTED/transport/|/.venv/'
chk june_protocol_goruntuler \
  'june_protocol_goruntuler/_viewer/app.py|june_protocol_goruntuler/_viewer/run.sh' \
  'june_protocol_goruntuler/_viewer/.venv/'

echo; echo "==== the transport symlink is stored AS a symlink (not a 3.9G copy)? ===="
tar -tvf "$OUT/muscle_mania.tar" 2>/dev/null | grep -E ' muscle_mania/transport ->' || echo "  ✗✗ transport symlink not found as a link"

echo; echo "==== data readback — extract critical files to /dev/null, confirm non-empty bytes ===="
rb(){ local n; n=$(tar -xOf "$OUT/$1.tar" --wildcards --occurrence=1 "$2" 2>/dev/null | wc -c); printf '  %-55s %s bytes %s\n' "$2" "$n" "$([ "$n" -gt 0 ] && echo OK || echo '✗✗ EMPTY')"; }
rb muscle_mania '*/cohort_stats/per_study_cohort.csv'
rb muscle_mania '*/condition_models.json'
rb muscle_model_training '*/transport/models/*.json'
rb prompted_transport 'transport/transport_metadata.csv'
rb june_protocol_goruntuler '*/_viewer/app.py'
rb muscle_mania '*/grading_pack/pack_r5_20260904/manifest.json'
rb muscle_grading_table '*/item_lists/b1/manifest.json'
rb prompted_chassis '*/label_palette.json'

echo; echo "==== study counts (sanity) ===="
echo "  muscle_mania studies_root operators: $(grep -cE 'data/studies_root/[^/]+/$' "$tmp/muscle_mania" 2>/dev/null)"
echo "  june study folders:                  $(grep -cE 'june_protocol_goruntuler/[^/_.][^/]*/$' "$tmp/june_protocol_goruntuler" 2>/dev/null)"
rm -rf "$tmp"
echo; echo "audit done."

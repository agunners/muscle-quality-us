#!/usr/bin/env bash
# apply_fixes_acer.sh — apply the post-migration fixes on the Acer (run AFTER pull_tar + setup_acer,
# on an already-set-up Acer). Restores the 272 reference cohort frames, the 4 symlinked-operator
# studies (anonaaq incl AKA049, anonabc, anonabn, anonabn_olcum) as REAL data, and the
# decision-log config + its env.py path fix. Safe to re-run.
#   Usage:  bash "/media/akadir/T7 Shield/muscle_migration/apply_fixes_acer.sh"
set -uo pipefail
T7="${1:-/media/akadir/T7 Shield}"; OUT="$T7/muscle_migration"
MM="$HOME/dev/muscle_mania"
[ -d "$OUT" ] || { echo "ERROR: $OUT not found — is the T7 mounted?"; exit 1; }

echo "== 1/4  goruntuler_backup — the 272 reference-cohort frames  ->  ~/dev/goruntuler_backup =="
if [ -f "$OUT/goruntuler_backup.tar" ]; then
  tar -xf "$OUT/goruntuler_backup.tar" -C "$HOME/dev" \
    && echo "   ok — $(find "$HOME/dev/goruntuler_backup" -maxdepth 4 -name 'RawImagesForResearch_*' -type d 2>/dev/null | wc -l) frame dirs"
else echo "   SKIP (goruntuler_backup.tar missing)"; fi

echo "== 2/4  studies_root symlinked operators  ->  REAL data (fixes AKA049 + the new-round gap) =="
if [ -f "$OUT/studies_root_symlinked_ops.tar" ]; then
  for op in anonaaq anonabc anonabn anonabn_olcum; do rm -f "$MM/data/studies_root/$op"; done  # drop the dangling symlinks
  tar -xf "$OUT/studies_root_symlinked_ops.tar" -C "$MM/data/studies_root" \
    && echo "   anonacs — anonaaq now has $(ls "$MM/data/studies_root/anonaaq" 2>/dev/null | wc -l) real sessions"
else echo "   SKIP (studies_root_symlinked_ops.tar missing)"; fi

echo "== 3/4  decision_log.csv  ->  configs/   +   env.py path fix =="
[ -f "$OUT/fixes/decision_log.csv" ] && cp "$OUT/fixes/decision_log.csv" "$MM/configs/decision_log.csv" && echo "   decision_log.csv placed in configs/"
[ -f "$OUT/fixes/env.py" ]           && cp "$OUT/fixes/env.py" "$MM/scripts/preflight_helpers/env.py" && echo "   env.py updated (-> configs/decision_log.csv)"

echo "== 4/4  sanity =="
echo "   studies_root operators (all should be real dirs now): $(ls "$MM/data/studies_root" 2>/dev/null | tr '\n' ' ')"
echo "   any still dangling? $(find "$MM/data/studies_root" -maxdepth 1 -xtype l 2>/dev/null | wc -l) (want 0)"

cat <<EOF

== DONE — restart the assessment app and hit Refresh in the study picker. ==
Expect now: ~326 valid (the 272 cohort validates) and AKA049 + the anonaaq new-round studies appear.
The june viewer's single-source count also fills in (its flat symlinks resolve once anonaaq is real).

PHI cleanup once you've verified (decision_log carries clinical fields):
   rm -f "$OUT/fixes/decision_log.csv"
EOF

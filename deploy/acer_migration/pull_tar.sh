#!/usr/bin/env bash
# pull_tar.sh — T7 -> Acer. Unpacks the lean payload to ~/dev and ~/Downloads.
# Runs from the drive:  T7="/media/akadir/T7 Shield" "/media/akadir/T7 Shield/muscle_migration/pull_tar.sh"
set -euo pipefail
T7="${1:-${T7:-/media/akadir/T7 Shield}}"
OUT="$T7/muscle_migration"
DEV="$HOME/dev"; DL="$HOME/Downloads"
die(){ echo "ERROR: $*" >&2; exit 1; }
[ -d "$OUT" ] || die "no muscle_migration/ on the T7 at $OUT"
[ "$USER" = akadir ] || echo "WARN: username '$USER' != akadir — after unpack, edit the /home/akadir/dev
  hardcodes in muscle_mania/scripts/env.sh (lines 11,22) and scripts/preflight_helpers/env.py:32."

mkdir -p "$DEV" "$DL"
untar(){  # tarname  dest-parent
  local t="$OUT/$1.tar" dest="$2"
  [ -f "$t" ] || { echo "  SKIP (missing): $1.tar"; return 0; }
  echo "---- $1  ->  $dest/ ----"
  mkdir -p "$dest"
  tar -xf "$t" -C "$dest"
}
untar muscle_mania               "$DEV"
untar muscle_quality_exploration "$DEV"
untar muscle_model_training      "$DEV"
untar june_protocol_goruntuler   "$DL"
untar muscle_grading_table       "$DEV"      # 2026-09-04: the grading table (own repo, full .git)
untar goruntuler                 "$DEV"      # the CVAT scripts + raw operator exports
untar prompted_chassis           "$DEV"      # the old labeler chassis without transport (see below)

# muscle_mania/transport came out of the tar as a SYMLINK (-> the old PROMPTED repo). Replace it
# with a REAL folder so muscle_mania is self-contained on this machine: no symlink, no separate
# PROMPTED repo to keep in sync. env.py's TRANSPORT_ROOT = REPO_ROOT/"transport" finds it in place.
MM="$DEV/muscle_mania"
if [ -f "$OUT/prompted_transport.tar" ]; then
  echo "---- transport  ->  $MM/transport/  (real folder, not a symlink) ----"
  rm -f "$MM/transport"                                  # remove only the symlink, not a target
  tar -xf "$OUT/prompted_transport.tar" -C "$MM"         # members are transport/... -> $MM/transport/
fi

# The old chassis expects its transport at <chassis>/transport; muscle_mania/transport is that same tree
# (prompted_transport.tar). Link it so the grading-pack builder's TRANSPORT path resolves on this machine.
CH="$DEV/muscle_quality_labeling_exercise_claude_PROMPTED"
if [ -d "$CH" ] && [ ! -e "$CH/transport" ] && [ -d "$MM/transport" ]; then
  ln -s "$MM/transport" "$CH/transport" && echo "---- chassis transport -> symlink to $MM/transport ----"
fi

# ---- make `git pull` clean (2026-09-04, caught in the simulated arrival) ----
# The archives freeze the WORKING TREE: tracked files mid-edit at packing time (the departure commit on
# origin carries their final versions) and tracked files the packer excluded (e.g. exploration's runs/)
# arrive modified or missing, and a plain `git pull` refuses. Restore every tracked file from the repo,
# keeping the one standing exclusion (muscle_mania/data/calibration_labels.jsonl is deliberately
# uncommitted and must travel as-is). Untracked files are never touched.
# 2026-09-05: goruntuler's .git/config sets an nbstripout clean filter that runs
# <repo>/.venv/bin/python3 — a venv that does not exist yet at unpack time, so every git
# call below died ("clean filter 'nbstripout' failed") and its tracked files were never
# restored. Neutralise the filter for these two calls; setup_acer.sh builds the real venv.
NOFILTER='-c filter.nbstripout.clean=cat -c filter.nbstripout.smudge=cat'
for r in muscle_mania muscle_quality_exploration muscle_model_training muscle_grading_table goruntuler; do
  d="$DEV/$r"; [ -d "$d/.git" ] || continue
  keep="x_none_x"; [ "$r" = muscle_mania ] && keep="data/calibration_labels.jsonl"
  files=$(git -C "$d" $NOFILTER status --short --no-renames | awk '$1 != "??" {print $2}' | grep -vx "$keep" || true)
  if [ -n "$files" ]; then
    echo "$files" | xargs -r git -C "$d" $NOFILTER checkout --
    echo "---- $r: restored $(echo "$files" | wc -l) tracked file(s) from the repo (git pull will be clean) ----"
  fi
done

echo; echo "== unpacked. sanity: transport is a REAL dir holding the metadata =="
ls -ld "$MM/transport" 2>/dev/null
[ -f "$MM/transport/transport_metadata.csv" ] && echo "  OK: real folder, transport_metadata.csv present" \
  || echo "  NOTE: transport_metadata.csv missing — check prompted_transport.tar unpacked"

# ---- refresh requirements*.txt to CURRENT (the tar copies can be stale) + stage your ignite ----
if [ -d "$OUT/current_requirements" ]; then
  echo "---- refreshing requirements*.txt to current (tar copies can be stale) ----"
  for r in muscle_mania muscle_quality_exploration muscle_model_training muscle_grading_table; do
    [ -d "$OUT/current_requirements/$r" ] && cp "$OUT/current_requirements/$r"/requirements*.txt "$DEV/$r/" 2>/dev/null && echo "  $r ✓"
  done
  [ -d "$OUT/current_requirements/june_viewer" ] && cp "$OUT/current_requirements/june_viewer"/requirements*.txt "$DL/june_protocol_goruntuler/_viewer/" 2>/dev/null
fi
[ -f "$OUT/ignite_function.sh" ] && cp "$OUT/ignite_function.sh" "$HOME/ignite_function.sh" \
  && echo "  staged your DGX ignite() -> ~/ignite_function.sh"

# ---- Claude migration (your context brains + global setup) ----
if [ -f "$OUT/claude_projects.tar" ]; then
  echo "---- claude project brains  ->  ~/.claude/projects/  (memory + history) ----"
  tar -xf "$OUT/claude_projects.tar" -C "$HOME"   # additive by slug; same ~/dev paths => same slugs
fi
if [ -f "$OUT/claude_config.tar" ]; then
  echo "---- claude global config  ->  ~/.claude_from_dgx/  (STAGED — merge when ready) ----"
  rm -rf "$HOME/.claude_from_dgx"; mkdir -p "$HOME/.claude_from_dgx"
  tar -xf "$OUT/claude_config.tar" -C "$HOME/.claude_from_dgx"
fi

# ---- data completeness: cohort frames + symlinked-operator real data + decision-log config ----
[ -f "$OUT/apply_fixes_acer.sh" ] && { echo; echo "---- applying data fixes (cohort + symlinked ops + decision_log) ----"; bash "$OUT/apply_fixes_acer.sh" "$T7"; }

echo; echo "== next: rebuild the 4 venvs (your ignite recipe, non-interactive) =="
echo "  bash $OUT/setup_acer.sh"
echo "  (venv + requirements-dgx/fastai/txt per repo — same as your ~/.bashrc ignite. Your DGX"
echo "   ignite() is at ~/ignite_function.sh if you'd rather run 'ignite' per repo, or want to"
echo "   compare it with the Acer's copy:  diff <(declare -f ignite) ~/ignite_function.sh )"
echo
echo "== Claude on the Acer =="
echo "  - project memory/history is already in place (~/.claude/projects) — a fresh Claude session"
echo "    here picks it up because the repo paths match (~/dev/muscle_mania)."
echo "  - adopt your DGX global setup (CLAUDE.md, hooks, skills, ledger):"
echo "      cp -rn ~/.claude_from_dgx/.claude/* ~/.claude/   # -n = never overwrite; drop -n to force"
echo "  - re-login (auth token was deliberately NOT copied):   claude   then /login"
echo "  - re-auth gws + any MCP servers (per-machine)."
echo "  - published artifacts live on claude.ai — just log in; nothing to unpack."
echo
echo "== PHI: after the two apps are verified, delete the cleartext tars off the drive: =="
echo "  rm -rf \"$OUT\""

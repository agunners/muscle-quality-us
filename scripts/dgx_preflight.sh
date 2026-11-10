#!/usr/bin/env bash
# dgx_preflight.sh — is the arriving machine ready to receive the handover?
#
# Read-only. Writes nothing, needs no auth beyond what is already there, and prints no PHI (counts
# and paths only, never a study folder name). Run it ON the DGX, or from the Acer over ssh:
#
#     bash scripts/dgx_preflight.sh                      # on the machine itself
#     ssh dgx-host 'bash -s' < scripts/dgx_preflight.sh # from here, if ssh works
#
# Everything it checks is something that does NOT travel by git and therefore cannot be verified
# from the other machine — which is exactly the class of thing that turns a "seamless" handover
# into an afternoon. See docs/ACER_SETUP.md §F.
set -u
REPO="${REPO:-$HOME/dev/muscle_mania}"
ok() { printf '  \033[32mOK\033[0m   %s\n' "$1"; }
no() { printf '  \033[31mNO\033[0m   %s\n' "$1"; }
inf(){ printf '       %s\n' "$1"; }

echo "=================== DGX PREFLIGHT ==================="
echo "host: $(hostname)   user: $(whoami)   date: $(date +%F\ %T)"

echo
echo "-- 1. repos (code travels by git; everything below does not) --"
for r in muscle_mania muscle_quality_exploration muscle_model_training; do
  d="$HOME/dev/$r"
  if [ -d "$d/.git" ]; then
    head=$(git -C "$d" rev-parse --short HEAD 2>/dev/null)
    date=$(git -C "$d" log -1 --format=%ad --date=short 2>/dev/null)
    dirty=$(git -C "$d" status --porcelain 2>/dev/null | wc -l)
    # `behind` against a STALE remote-tracking ref reads 0 on a machine that simply cannot fetch —
    # which is exactly what this reported the first time (0648d76, five weeks old, "behind=0").
    # So probe the remote for real and say when the answer is unknowable.
    if git -C "$d" ls-remote --exit-code origin HEAD >/dev/null 2>&1; then
      git -C "$d" fetch origin --quiet 2>/dev/null
      behind=$(git -C "$d" rev-list --count HEAD..@{u} 2>/dev/null || echo '?')
    else
      behind="UNKNOWN — cannot reach origin (no credentials?)"
    fi
    ok "$r @ $head ($date)   uncommitted=$dirty   behind=$behind"
    [ "$dirty" != "0" ] && git -C "$d" status --porcelain 2>/dev/null | sed 's/^/         /'
  else
    no "$r — not a git repo at $d"
  fi
done
[ -d "$HOME/projects/brain/.git" ] && ok "brain @ $(git -C "$HOME/projects/brain" rev-parse --short HEAD)" \
  || no "brain not found at ~/projects/brain (is the ~/projects -> ~/nb bridge there?)"

echo
echo "-- 2. runtime --"
if [ -x "$REPO/.venv/bin/python" ]; then
  ok ".venv python $("$REPO/.venv/bin/python" -V 2>&1 | cut -d' ' -f2)"
  # -c, NOT a heredoc: this script is designed to be piped in (`ssh host bash -s < script`), and a
  # heredoc inside would read from the SAME stdin the script is arriving on. That is not theoretical —
  # the first real run of this file reported "dependency import failed" and "cannot run the receipts"
  # on a machine where every dependency was present. A preflight that reports false failures is worse
  # than none: it sends you to fix what is not broken.
  "$REPO/.venv/bin/python" -c '
import importlib.util as u
miss = [m for m in ("cv2","yaml","streamlit","pydicom","SimpleITK","pandas","numpy") if not u.find_spec(m)]
print("  \033[32mOK\033[0m   deps: all present" if not miss else f"  \033[31mNO\033[0m   deps MISSING: {miss}")
try:
    import torch
    print(f"  \033[32mOK\033[0m   torch {torch.__version__} cuda={torch.version.cuda} available={torch.cuda.is_available()}")
except Exception as e:
    print(f"  \033[31mNO\033[0m   torch: {type(e).__name__}")
' 2>/dev/null || no "python could not run — venv broken?"
else
  no ".venv missing at $REPO/.venv — rebuild it before anything else"
fi

echo
echo "-- 3. data that git cannot carry --"
sr="$REPO/data/studies_root"
if [ -d "$sr" ]; then
  # -L FOLLOWS SYMLINKS, and that is the whole point: some operator dirs are symlinks into
  # ~/dev/goruntuler (ACER_SETUP §E). A plain `find -type d` counted 54 on the DGX while the app's
  # own census counted 71 — and the app is right, because pathlib's is_dir() resolves links. The
  # shortcut made the machine look 17 studies emptier than it is, and I reported that as fact.
  n=$(find -L "$sr" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | wc -l)
  real=$(find "$sr" -mindepth 3 -maxdepth 3 -type d 2>/dev/null | wc -l)
  links=$(find "$sr" -maxdepth 1 -type l 2>/dev/null | wc -l)
  ok "studies_root: $n study dirs the app can see ($real on this disk, $links symlinked operator dir(s))"
  [ "$links" != "0" ] && inf "⚠ symlinked operators resolve elsewhere — an rsync into studies_root would"
  [ "$links" != "0" ] && inf "  write THROUGH them into the link target. Replace them before copying."
else
  no "studies_root MISSING — nothing to score until you copy it"
fi
for f in data/study_registry.csv data/qa_master.csv configs/decision_log.csv; do
  p="$REPO/$f"
  if [ -f "$p" ]; then
    ok "$f  ($(wc -l < "$p") lines, $(date -r "$p" +%F))"
  else
    no "$f MISSING"
  fi
done
[ -d "$REPO/transport" ] && ok "transport/ present" || no "transport/ MISSING"
[ -d "$REPO/exports" ]   && ok "exports/ present"   || no "exports/ MISSING (3-operator decision log lives here)"

echo
echo "-- 4. committed artifacts that SHOULD have arrived by git --"
for f in per_study_cohort_new.csv new_round_freeze_log.jsonl condition_models_v1.3.json probe_classification.csv; do
  p="$REPO/data/cohort_stats/$f"
  [ -f "$p" ] && ok "cohort_stats/$f ($(wc -l < "$p") lines)" || no "cohort_stats/$f MISSING — did the pull land?"
done

echo
echo "-- 5. machine-local Claude/brain loaders (never travel) --"
[ -f "$HOME/.claude/CLAUDE.md" ] && ok "~/.claude/CLAUDE.md present" || no "~/.claude/CLAUDE.md MISSING"
[ -e "$HOME/.claude/skills/usta" ] && ok "usta skill bridged" || no "usta skill NOT bridged"
[ -e "$HOME/projects" ] && ok "~/projects -> $(readlink -f "$HOME/projects")" || no "~/projects bridge MISSING"
command -v gws >/dev/null && ok "gws on PATH" || no "gws NOT on PATH"

echo
echo "-- 6. room for the study copy --"
df -h "$REPO" | awk 'NR==2 {printf "       %s free of %s on %s\n", $4, $2, $6}'
[ -d "$sr" ] && du -sh "$sr" 2>/dev/null | awk '{printf "       studies_root currently %s\n", $1}'

echo
echo "-- 7. the two receipts (only meaningful once the data is in place) --"
if [ -x "$REPO/.venv/bin/python" ] && [ -f "$REPO/scripts/ingest_all.py" ]; then
  ( cd "$REPO" && . scripts/env.sh >/dev/null 2>&1
    "$REPO/.venv/bin/python" scripts/ingest_all.py --status --offline 2>&1 \
      | grep -E '=> ingestion|parity baseline|on disk|new-round cohort|substrate ' | sed 's/^/       /'
    "$REPO/.venv/bin/python" scripts/check_parity.py 2>&1 | grep -E 'DATA PARITY|MISMATCH|✗' | sed 's/^/       /'
  )
else
  no "cannot run the receipts — venv or repo incomplete"
fi
echo
echo "=================== END PREFLIGHT ==================="

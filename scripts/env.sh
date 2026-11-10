# Source me — do NOT `bash` me.
#   GOOD:  source scripts/env.sh && python scripts/preflight.py stage1 ...
#   BAD :  bash scripts/env.sh && python ...      # subshell-isolated; PYTHONPATH dies
#
# Exports the runtime bridge for muscle_mania:
#   - rf_quality              (in ~/dev/muscle_quality_exploration)
#   - segmenter inference     (in ~/dev/muscle_model_training/src)
#
# muscle_mania's own .venv is the runtime; no pip install -e of these external repos.

_BRIDGE="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)/private/muscle_quality_exploration:$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)/private/muscle_model_training/src"

# Prepend only if not already present (idempotent).
case ":${PYTHONPATH:-}:" in
  *":${_BRIDGE%:*}:"*) ;;
  *) export PYTHONPATH="${_BRIDGE}:${PYTHONPATH:-}" ;;
esac
unset _BRIDGE

# Optional: surface the canonical venv if the caller wants to use `python` directly.
# (Not auto-activated — left to the caller to honor virtualenv etiquette.)
export MUSCLE_MANIA_VENV="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/.." && pwd)/.venv"

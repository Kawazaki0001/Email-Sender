#!/usr/bin/env bash
#
# run.sh — create a virtual environment, install dependencies,
#          run setup.py to configure email_body.json, then send.
#
# Usage:
#   ./run.sh                    # full flow: venv → deps → setup.py
#   ./run.sh --recreate         # wipe and recreate the venv first
#   ./run.sh --no-setup         # venv + deps only
#   ./run.sh --run              # venv + deps + setup.py + main.py
#   ./run.sh --run --dry-run    # ... preview instead of sending
#   PYTHON=python3.11 ./run.sh
#
# Unrecognised options after --run are forwarded to main.py
# (e.g. --dry-run, --delay 5, --no-resume, -v).
#
set -euo pipefail

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON="${PYTHON:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"
REQUIREMENTS="requirements.txt"
SETUP_SCRIPT="setup.py"
MAIN_SCRIPT="main.py"

# --------------------------------------------------------------------------- #
# Colors / logging
# --------------------------------------------------------------------------- #
if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; RED=$'\033[31m'; GREEN=$'\033[32m'
  YELLOW=$'\033[33m'; BLUE=$'\033[34m'; RESET=$'\033[0m'
else
  BOLD=""; RED=""; GREEN=""; YELLOW=""; BLUE=""; RESET=""
fi

info() { printf '%s[*]%s %s\n' "$BLUE"   "$RESET" "$*"; }
ok()   { printf '%s[+]%s %s\n' "$GREEN"  "$RESET" "$*"; }
warn() { printf '%s[!]%s %s\n' "$YELLOW" "$RESET" "$*" >&2; }
die()  { printf '%s[x]%s %s\n' "$RED"    "$RESET" "$*" >&2; exit 1; }

banner() { printf '\n%s%s== %s ==%s\n' "$BOLD" "$BLUE" "$*" "$RESET"; }

# --------------------------------------------------------------------------- #
# Flags
# --------------------------------------------------------------------------- #
RECREATE=0
NO_SETUP=0
RUN_AFTER=0
MAIN_ARGS=()

for arg in "$@"; do
  case "$arg" in
    --recreate)  RECREATE=1 ;;
    --no-setup)  NO_SETUP=1 ;;
    --run)       RUN_AFTER=1 ;;
    -h|--help)
      sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) MAIN_ARGS+=("$arg") ;;
  esac
done

# --------------------------------------------------------------------------- #
# Step 1 — verify interpreter
# --------------------------------------------------------------------------- #
banner "Step 1/4 — Check Python"

command -v "$PYTHON" >/dev/null 2>&1 \
  || die "Python interpreter '$PYTHON' not found. Set PYTHON=... to override."

PY_VERSION="$("$PYTHON" --version 2>&1)"
ok "$PY_VERSION at $(command -v "$PYTHON")"

# Require >= 3.8 (f-strings, walrus in our code are fine, but be explicit).
"$PYTHON" - <<'PY' || die "Python 3.8+ required."
import sys
if sys.version_info < (3, 8):
    sys.exit(1)
PY

# --------------------------------------------------------------------------- #
# Step 2 — create virtual environment
# --------------------------------------------------------------------------- #
banner "Step 2/4 — Virtual environment"

if (( RECREATE )) && [[ -d "$VENV_DIR" ]]; then
  warn "Removing existing $VENV_DIR (--recreate)."
  rm -rf "$VENV_DIR"
fi

if [[ -d "$VENV_DIR" ]]; then
  ok "$VENV_DIR already exists — reusing."
else
  info "Creating $VENV_DIR ..."
  "$PYTHON" -m venv "$VENV_DIR" \
    || die "Failed to create venv. Is the 'venv' module installed?"
  ok "Created $VENV_DIR."
fi

# Activate it.
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
ok "Activated: $(command -v python) ($(python --version 2>&1))"

# --------------------------------------------------------------------------- #
# Step 3 — install dependencies
# --------------------------------------------------------------------------- #
banner "Step 3/4 — Dependencies"

# Always upgrade pip quietly.
info "Upgrading pip / setuptools / wheel ..."
python -m pip install --upgrade pip setuptools wheel --quiet \
  || warn "pip upgrade failed — continuing with existing version."

if [[ -f "$REQUIREMENTS" ]]; then
  info "Installing from $REQUIREMENTS ..."
  # Filter comments/blank lines so we can tell "really empty" apart from
  # "only comments". The project has no external deps today.
  if grep -vE '^\s*(#|$)' "$REQUIREMENTS" >/dev/null 2>&1; then
    python -m pip install -r "$REQUIREMENTS" \
      || die "pip install -r $REQUIREMENTS failed."
    ok "Dependencies installed."
  else
    ok "$REQUIREMENTS has no packages (stdlib-only project)."
  fi
else
  warn "No $REQUIREMENTS found — assuming stdlib-only project."
fi

# Verify core imports work inside the venv.
info "Verifying imports ..."
python - <<'PY' || die "Import check failed inside venv."
import json, ssl, smtplib, email.mime.text, email.header, argparse, logging
print("    all core modules import OK")
PY
ok "Environment is ready."

# --------------------------------------------------------------------------- #
# Step 4 — run setup.py (and optionally run.sh)
# --------------------------------------------------------------------------- #
if (( NO_SETUP )); then
  banner "Step 4/4 — Skipped (--no-setup)"
  ok "Virtualenv is ready at $(pwd)/$VENV_DIR"
  info "Activate manually with: source $VENV_DIR/bin/activate"
  exit 0
fi

banner "Step 4/4 — Configure email body"

[[ -f "$SETUP_SCRIPT" ]] || die "$SETUP_SCRIPT not found in $SCRIPT_DIR"
info "Launching $SETUP_SCRIPT ..."
printf '\n'
python "$SETUP_SCRIPT" || die "setup.py exited with an error."
printf '\n'
ok "email_body.json is ready."

# --------------------------------------------------------------------------- #
# Optional: chain into run.sh
# --------------------------------------------------------------------------- #
if (( RUN_AFTER )); then
  banner "Step 5/5 — Running $MAIN_SCRIPT"
  [[ -f "$MAIN_SCRIPT" ]] || die "$MAIN_SCRIPT not found in $SCRIPT_DIR"
  info "python $MAIN_SCRIPT ${MAIN_ARGS[*]}"
  python "$MAIN_SCRIPT" ${MAIN_ARGS[@]+"${MAIN_ARGS[@]}"}
  exit $?
fi

# --------------------------------------------------------------------------- #
# Footer
# --------------------------------------------------------------------------- #
banner "Done"
ok "Virtualenv : $(pwd)/$VENV_DIR"
ok "Email body : $(pwd)/email_body.json"
printf '\n'
info "Next steps:"
printf '    source %s/bin/activate\n' "$VENV_DIR"
printf '    python main.py --dry-run      # preview\n'
printf '    python main.py                # send\n'
printf '    ./run.sh --run --dry-run       # or: bootstrap + preview\n'

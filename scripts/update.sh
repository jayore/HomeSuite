#!/usr/bin/env bash
set -euo pipefail

# Update an existing Home Suite node without overwriting local work or config.
INSTALL_DIR="${HOMESUITE_DIR:-$HOME/homesuite}"
REMOTE="${HOMESUITE_REMOTE:-origin}"
RESTART_SERVICE=0
SKIP_DEPS=0

require_supported_python() {
  local interpreter="$1"
  if ! "$interpreter" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
    echo "Home Suite requires Python 3.9 or newer. Found: $($interpreter --version 2>&1)" >&2
    exit 1
  fi
}

usage() {
  cat <<'USAGE'
Home Suite safe updater

Usage:
  scripts/update.sh [--restart] [--skip-deps]

Options:
  --restart    restart active Home Suite services after a successful required-config check
  --skip-deps  skip Python dependency installation

Environment overrides:
  HOMESUITE_DIR     Existing checkout. Default: $HOME/homesuite
  HOMESUITE_REMOTE  Git remote to update from. Default: origin
  HOMESUITE_BRANCH  Current branch to update. Defaults to the checked-out branch.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --restart) RESTART_SERVICE=1 ;;
    --skip-deps) SKIP_DEPS=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

if ! git -C "$INSTALL_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Home Suite checkout not found at $INSTALL_DIR" >&2
  exit 1
fi

if [[ -n "$(git -C "$INSTALL_DIR" status --porcelain)" ]]; then
  echo "Refusing to update: $INSTALL_DIR has local changes." >&2
  echo "Commit, stash, or resolve them first. This updater never overwrites local work." >&2
  exit 1
fi

CURRENT_BRANCH="$(git -C "$INSTALL_DIR" branch --show-current)"
if [[ -z "$CURRENT_BRANCH" ]]; then
  echo "Refusing to update a detached HEAD; check out a named branch first." >&2
  exit 1
fi

BRANCH="${HOMESUITE_BRANCH:-$CURRENT_BRANCH}"
if [[ "$BRANCH" != "$CURRENT_BRANCH" ]]; then
  echo "Refusing to switch branches during an update ($CURRENT_BRANCH -> $BRANCH)." >&2
  echo "Check out the intended branch yourself, then rerun this updater." >&2
  exit 1
fi

if ! git -C "$INSTALL_DIR" remote get-url "$REMOTE" >/dev/null 2>&1; then
  echo "Git remote $REMOTE is not configured for $INSTALL_DIR" >&2
  exit 1
fi

VENV_PYTHON="$INSTALL_DIR/.venv/bin/python"
if [[ ! -x "$VENV_PYTHON" ]]; then
  echo "Missing .venv/bin/python. Run scripts/install.sh to create the environment." >&2
  exit 1
fi
require_supported_python "$VENV_PYTHON"

cd "$INSTALL_DIR"
echo "Fetching $REMOTE/$BRANCH..."
git fetch "$REMOTE" "$BRANCH"
echo "Fast-forwarding $BRANCH..."
git merge --ff-only "$REMOTE/$BRANCH"

if [[ "$SKIP_DEPS" == "0" ]]; then
  echo "Installing declared Python dependencies..."
  .venv/bin/python -m pip install -r requirements.txt
fi

HOMESUITE_DIR="$INSTALL_DIR" scripts/install_shortcuts.sh

echo "Checking required configuration..."
if ! .venv/bin/python tools/doctor.py; then
  echo "Update applied, but required configuration checks failed; service was not restarted." >&2
  exit 1
fi

if [[ "$RESTART_SERVICE" == "1" ]]; then
  if command -v sudo >/dev/null 2>&1; then
    SUDO=sudo
  else
    SUDO=""
  fi

  restart_active_service() {
    local service="$1"
    if ! $SUDO systemctl is-active --quiet "$service"; then
      echo "Skipping $service (not active)."
      return
    fi
    echo "Restarting $service..."
    $SUDO systemctl restart "$service"
    $SUDO systemctl --no-pager --full status "$service"
  }

  echo "Restarting homesuite.service..."
  $SUDO systemctl restart homesuite.service
  $SUDO systemctl --no-pager --full status homesuite.service

  # These processes import the same checkout independently. Reload active
  # frontends too so an update cannot leave them running stale Python modules.
  restart_active_service homesuite-console.service
  restart_active_service piphone-telegram.service
fi

echo "Update complete. Run 'homesuite doctor --live' and the relevant acceptance checks before relying on the node."

#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${HOMESUITE_REPO_URL:-https://github.com/jayore/HomeSuite.git}"
INSTALL_DIR="${HOMESUITE_DIR:-$HOME/homesuite}"
BRANCH="${HOMESUITE_BRANCH:-main}"
INSTALL_SYSTEMD=0
START_SERVICE=0
SKIP_APT=0
EXISTING_CHECKOUT=0

require_supported_python() {
  local interpreter="$1"
  if ! "$interpreter" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 9) else 1)'; then
    echo "Home Suite requires Python 3.9 or newer. Found: $($interpreter --version 2>&1)" >&2
    exit 1
  fi
}

usage() {
  cat <<'USAGE'
Home Suite native installer

Usage:
  scripts/install.sh [--systemd] [--start] [--skip-apt]

Environment overrides:
  HOMESUITE_REPO_URL   Git URL to clone. Default: https://github.com/jayore/HomeSuite.git
  HOMESUITE_DIR        Install directory. Default: $HOME/homesuite
  HOMESUITE_BRANCH     Branch to checkout. Default: main

Examples:
  curl -fsSL https://raw.githubusercontent.com/jayore/HomeSuite/main/scripts/install.sh | bash -s -- --start
  curl -fsSL https://raw.githubusercontent.com/jayore/HomeSuite/main/scripts/install.sh | bash
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --systemd) INSTALL_SYSTEMD=1 ;;
    --start) INSTALL_SYSTEMD=1; START_SERVICE=1 ;;
    --skip-apt) SKIP_APT=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
  shift
done

if command -v sudo >/dev/null 2>&1; then
  SUDO=sudo
else
  SUDO=""
fi

echo "Installing Home Suite into $INSTALL_DIR"

if [[ "$SKIP_APT" != "1" ]]; then
  echo "Installing OS packages..."
  $SUDO apt-get update
  $SUDO apt-get install -y     git curl ca-certificates     python3 python3-venv python3-pip python3-dev build-essential     portaudio19-dev libasound2-dev libffi-dev     mpg123 alsa-utils     python3-rpi.gpio python3-gpiozero python3-pigpio python3-pyaudio
fi

require_supported_python python3

if [[ -d "$INSTALL_DIR" ]] && git -C "$INSTALL_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  EXISTING_CHECKOUT=1
  echo "Updating existing checkout..."
  if [[ -n "$(git -C "$INSTALL_DIR" status --porcelain)" ]]; then
    echo "Refusing to update an existing checkout with local changes." >&2
    echo "Commit, stash, or otherwise resolve those changes first, then rerun." >&2
    exit 1
  fi
  if [[ -e "$INSTALL_DIR/.venv" ]]; then
    if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]]; then
      echo "Existing virtual environment is incomplete: $INSTALL_DIR/.venv" >&2
      echo "Repair or replace it before updating the checkout." >&2
      exit 1
    fi
    require_supported_python "$INSTALL_DIR/.venv/bin/python"
  fi
  git -C "$INSTALL_DIR" fetch origin "$BRANCH"
  git -C "$INSTALL_DIR" checkout "$BRANCH"
  git -C "$INSTALL_DIR" pull --ff-only origin "$BRANCH"
else
  echo "Cloning $REPO_URL..."
  git clone --branch "$BRANCH" "$REPO_URL" "$INSTALL_DIR"
fi

cd "$INSTALL_DIR"

if [[ ! -d .venv ]]; then
  python3 -m venv --system-site-packages .venv
fi

require_supported_python .venv/bin/python

.venv/bin/python -m pip install --upgrade pip wheel "setuptools<81"
.venv/bin/python -m pip install -r requirements.txt

mkdir -p logs state backups

if [[ ! -f private_config.py ]]; then
  cp private_config.example.py private_config.py
  generated_api_key="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
  sed -i "s|^HOMESUITE_HTTP_API_KEY = \"\"$|HOMESUITE_HTTP_API_KEY = \"$generated_api_key\"|" private_config.py
  unset generated_api_key
  chmod 0600 private_config.py
  echo "Created private_config.py from private_config.example.py"
  echo "Generated HOMESUITE_HTTP_API_KEY in private_config.py"
  printf '%s\n' 'pending' > state/console_bootstrap_pending
  chmod 0600 state/console_bootstrap_pending
  echo "Enabled one-time browser console claiming"
fi

if [[ ! -f local_prefs.py ]]; then
  cp local_prefs.example.py local_prefs.py
  chmod 0600 local_prefs.py
  echo "Created local_prefs.py from local_prefs.example.py"
fi

if [[ ! -f deployment_config.py ]]; then
  if [[ "$EXISTING_CHECKOUT" == "0" ]]; then
    cp deployment_config.example.py deployment_config.py
    chmod 0600 deployment_config.py
    echo "Created deployment_config.py from deployment_config.example.py"
  else
    echo "Existing checkout has no deployment_config.py; tracked app_config.py behavior is unchanged."
    echo "See docs/ROOM_CONFIGURATION.md to migrate shared topology when convenient."
  fi
fi

HOMESUITE_DIR="$INSTALL_DIR" scripts/install_shortcuts.sh

if [[ "$INSTALL_SYSTEMD" == "1" ]]; then
  if [[ ! -f deploy/systemd/homesuite.service.template ]]; then
    echo "Missing deploy/systemd/homesuite.service.template" >&2
    exit 1
  fi
  if [[ ! -f deploy/systemd/homesuite-console.service.template ]]; then
    echo "Missing deploy/systemd/homesuite-console.service.template" >&2
    exit 1
  fi
  if [[ ! -f deploy/systemd/homesuite-runtime.path.template ]]; then
    echo "Missing deploy/systemd/homesuite-runtime.path.template" >&2
    exit 1
  fi
  tmp_unit="$(mktemp)"
  tmp_console_unit="$(mktemp)"
  tmp_runtime_path="$(mktemp)"
  sed     -e "s#@HOMESUITE_USER@#$(id -un)#g"     -e "s#@HOMESUITE_DIR@#$INSTALL_DIR#g"     deploy/systemd/homesuite.service.template > "$tmp_unit"
  sed     -e "s#@HOMESUITE_USER@#$(id -un)#g"     -e "s#@HOMESUITE_DIR@#$INSTALL_DIR#g"     deploy/systemd/homesuite-console.service.template > "$tmp_console_unit"
  sed     -e "s#@HOMESUITE_DIR@#$INSTALL_DIR#g"     deploy/systemd/homesuite-runtime.path.template > "$tmp_runtime_path"
  $SUDO install -m 0644 "$tmp_unit" /etc/systemd/system/homesuite.service
  $SUDO install -m 0644 "$tmp_console_unit" /etc/systemd/system/homesuite-console.service
  $SUDO install -m 0644 "$tmp_runtime_path" /etc/systemd/system/homesuite-runtime.path
  rm -f "$tmp_unit" "$tmp_console_unit" "$tmp_runtime_path"
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable homesuite-console.service homesuite-runtime.path
  if [[ "$START_SERVICE" == "1" ]]; then
    $SUDO systemctl restart homesuite-runtime.path
    $SUDO systemctl restart homesuite-console.service
    $SUDO systemctl --no-pager --full status homesuite-console.service || true
    echo "Validating configuration before service start..."
    if ! .venv/bin/python tools/doctor.py; then
      echo "Home Suite is installed and waiting for guided browser setup."
      echo "The runtime will remain stopped until required checks pass."
    else
      .venv/bin/python -c 'from pathlib import Path; from console_setup import ConsoleSetupManager; ConsoleSetupManager(root=Path(".")).request_activation()'
      $SUDO systemctl restart homesuite.service
      $SUDO systemctl --no-pager --full status homesuite.service || true
    fi
  fi
fi

node_name="$(hostname -s 2>/dev/null || hostname)"
if [[ "$node_name" == *.local ]]; then
  console_url="http://$node_name:8766"
else
  console_url="http://$node_name.local:8766"
fi
ip_address="$(hostname -I 2>/dev/null | awk '{print $1}')"

cat <<EOF

Home Suite install complete.

Next steps:
EOF

if [[ "$START_SERVICE" == "1" ]]; then
  cat <<EOF
  1. Open the Home Suite Console:
       $console_url
EOF
elif [[ "$INSTALL_SYSTEMD" == "1" ]]; then
  cat <<EOF
  1. Start the management console:
       sudo systemctl start homesuite-console.service
  2. Open:
       $console_url
EOF
else
  cat <<EOF
  1. Start the management console in the foreground:
       homesuite console
  2. Open:
       $console_url
EOF
fi

if [[ -n "$ip_address" ]]; then
  cat <<EOF
     If .local names are unavailable, use http://$ip_address:8766
EOF
fi

cat <<EOF
  - On a fresh install, create the console passphrase in your browser.
  - Follow Setup to connect Home Assistant, choose this node's role, test it,
     and activate the runtime. No additional terminal commands are required.

Manual fallback:
       homesuite console
       homesuite doctor --live

For later updates, use:
       cd $INSTALL_DIR
       bash scripts/update.sh

EOF

#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${HOMESUITE_REPO_URL:-https://github.com/jayore/HomeSuite.git}"
INSTALL_DIR="${HOMESUITE_DIR:-$HOME/homesuite}"
BRANCH="${HOMESUITE_BRANCH:-main}"
INSTALL_SYSTEMD=0
START_SERVICE=0
SKIP_APT=0

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
  curl -fsSL https://raw.githubusercontent.com/jayore/HomeSuite/main/scripts/install.sh | bash
  curl -fsSL https://raw.githubusercontent.com/jayore/HomeSuite/main/scripts/install.sh | bash -s -- --systemd --start
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

if [[ -d "$INSTALL_DIR/.git" ]]; then
  echo "Updating existing checkout..."
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

.venv/bin/python -m pip install --upgrade pip setuptools wheel
.venv/bin/python -m pip install -r requirements.txt

if [[ ! -f private_config.py ]]; then
  cp private_config.example.py private_config.py
  echo "Created private_config.py from private_config.example.py"
fi

if [[ ! -f local_prefs.py ]]; then
  cp local_prefs.example.py local_prefs.py
  echo "Created local_prefs.py from local_prefs.example.py"
fi

mkdir -p logs state backups

HOMESUITE_DIR="$INSTALL_DIR" scripts/install_shortcuts.sh

if [[ "$INSTALL_SYSTEMD" == "1" ]]; then
  if [[ ! -f deploy/systemd/homesuite.service.template ]]; then
    echo "Missing deploy/systemd/homesuite.service.template" >&2
    exit 1
  fi
  tmp_unit="$(mktemp)"
  sed     -e "s#@HOMESUITE_USER@#$(id -un)#g"     -e "s#@HOMESUITE_DIR@#$INSTALL_DIR#g"     deploy/systemd/homesuite.service.template > "$tmp_unit"
  $SUDO install -m 0644 "$tmp_unit" /etc/systemd/system/homesuite.service
  rm -f "$tmp_unit"
  $SUDO systemctl daemon-reload
  $SUDO systemctl enable homesuite.service
  if [[ "$START_SERVICE" == "1" ]]; then
    $SUDO systemctl restart homesuite.service
    $SUDO systemctl --no-pager --full status homesuite.service || true
  fi
fi

cat <<EOF

Home Suite install complete.

Next steps:
  1. Edit $INSTALL_DIR/private_config.py with your Home Assistant/OpenAI/service credentials.
  2. Edit $INSTALL_DIR/local_prefs.py for this device's room, audio, and hardware role.
  3. Check setup and test command routing:
       homesuite-doctor
       pptest "service status"
  4. Start or restart the service when ready:
       sudo systemctl restart homesuite.service

EOF

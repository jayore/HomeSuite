# HomeSuite install guide

This is the first public-alpha install path for HomeSuite. It targets a native
Raspberry Pi OS install because HomeSuite currently touches local audio, GPIO,
systemd, Home Assistant, and optional wake-word hardware directly.

Docker may make sense later for a central brain/server role, but the native Pi
installer is the simplest path for the current appliance runtime.

## Quick install

On a Raspberry Pi or Debian-like host:

```bash
curl -fsSL https://raw.githubusercontent.com/jayore/HomeSuite/main/scripts/install.sh | bash
```

To install and enable the systemd service in the same pass:

```bash
curl -fsSL https://raw.githubusercontent.com/jayore/HomeSuite/main/scripts/install.sh | bash -s -- --systemd
```

To install, enable, and start the service:

```bash
curl -fsSL https://raw.githubusercontent.com/jayore/HomeSuite/main/scripts/install.sh | bash -s -- --start
```

Until the GitHub repo exists, use `HOMESUITE_REPO_URL` to point the installer at
another Git remote:

```bash
HOMESUITE_REPO_URL=https://github.com/owner/HomeSuite.git \
  bash scripts/install.sh --systemd
```

## What the installer does

`scripts/install.sh`:

* installs required OS packages with `apt-get`
* clones or updates the repo into `$HOME/homesuite` by default
* creates `.venv` with `--system-site-packages` so Raspberry Pi OS hardware
  bindings remain available
* installs Python packages from `requirements.txt`
* copies `private_config.example.py` to `private_config.py` if missing
* copies `local_prefs.example.py` to `local_prefs.py` if missing
* creates `logs/`, `state/`, and `backups/`
* optionally installs `/etc/systemd/system/homesuite.service` from the service
  template

## Configuration files

HomeSuite uses two local configuration files:

* `private_config.py` contains credentials, tokens, URLs, and service API keys.
* `local_prefs.py` contains per-device overrides such as room, audio routing,
  handset mode, wake-word mode, and device-specific timing.

Public deployments should track only:

* `private_config.example.py`
* `local_prefs.example.py`

Real `private_config.py` and `local_prefs.py` should stay local to each device.

Private development repos may track real `private_config.py` values for convenience.
Do not push that private history directly to a public GitHub repo. For a public
launch, create a sanitized public repo or public branch that never includes real
secrets.

## Native service

The public service template is:

```text
deploy/systemd/homesuite.service.template
```

The installer replaces these placeholders:

* `@HOMESUITE_USER@`
* `@HOMESUITE_DIR@`

The currently running private Pi may also keep a captured reference unit at:

```text
deploy/systemd/homesuite.service.current
```

That `.current` file is documentation of one live deployment, not the portable
unit users should install.

## Validation

After editing config, test command routing before starting the live service:

```bash
cd ~/homesuite
.venv/bin/python tools/test_commands.py "service status" --capture
```

For syntax checks:

```bash
.venv/bin/python -m py_compile main.py command_dispatch.py app_config.py
```

For service checks:

```bash
sudo systemctl restart homesuite.service
sudo systemctl status homesuite.service --no-pager -l
curl -sS http://localhost:8765/health
```

## Current public-alpha limitations

The installer is intentionally conservative. It sets up the native runtime, but
users still need to configure their own Home Assistant entities, rooms, media
services, audio devices, and optional hardware.

Known areas that still need more public-release polish:

* first-run interactive configuration
* generated Home Assistant/entity mapping helpers
* a server-only mode with no GPIO/audio assumptions
* satellite install mode
* Docker packaging for the central brain role
* sanitized public GitHub history

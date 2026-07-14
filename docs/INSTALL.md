# Home Suite install guide

This is the current beta-readiness install path for Home Suite. It targets a native
Raspberry Pi OS or Debian-like install because Home Suite currently touches local
audio, optional GPIO, systemd, Home Assistant, and optional wake-word hardware
directly.

Docker may make sense later for a central brain/server role, but the native Pi
installer is the simplest path for the current appliance runtime.

The portable core runtime requires CPython 3.9 or newer. The portable test suite
runs on both 3.9 and 3.13; physical audio and GPIO behavior still needs
validation on the target Pi. Experimental applets can have additional platform
requirements and are not installed with the core dependency set.

For a shorter walkthrough, start with [GETTING_STARTED.md](GETTING_STARTED.md).

## Quick install

On a Raspberry Pi or Debian-like host:

```bash
curl -fsSL https://raw.githubusercontent.com/jayore/HomeSuite/main/scripts/install.sh | bash
```

To install and enable the systemd service in the same pass:

```bash
curl -fsSL https://raw.githubusercontent.com/jayore/HomeSuite/main/scripts/install.sh | bash -s -- --systemd
```

To install, enable, and start the service after required configuration already
exists (the installer runs `homesuite doctor` first):

```bash
curl -fsSL https://raw.githubusercontent.com/jayore/HomeSuite/main/scripts/install.sh | bash -s -- --start
```

To install from a fork or alternate remote, download the script first and set `HOMESUITE_REPO_URL`:

```bash
curl -fsSLo install-homesuite.sh https://raw.githubusercontent.com/jayore/HomeSuite/main/scripts/install.sh
HOMESUITE_REPO_URL=https://github.com/owner/HomeSuite.git bash install-homesuite.sh --systemd
```

From an existing local checkout, you can also run:

```bash
HOMESUITE_REPO_URL=https://github.com/owner/HomeSuite.git bash scripts/install.sh --systemd
```

## What the installer does

`scripts/install.sh`:

* installs required OS packages with `apt-get`
* refuses an unsupported system Python or existing virtual environment before
  changing the checkout
* clones or updates the repo into `$HOME/homesuite` by default
* creates `.venv` with `--system-site-packages` so Raspberry Pi OS hardware
  bindings remain available
* installs Python packages from `requirements.txt`
* copies `private_config.example.py` to `private_config.py` if missing
* generates a random `HOMESUITE_HTTP_API_KEY` for a fresh private config
* copies `deployment_config.example.py` to `deployment_config.py` on fresh installs
* copies `local_prefs.example.py` to `local_prefs.py` if missing
* creates `logs/`, `state/`, and `backups/`
* installs convenience shortcuts into `$HOME/.local/bin`
* optionally installs `/etc/systemd/system/homesuite.service` from the service
  template

Experimental applets are separate from this core install. In particular, Note
Lights needs a heavier pitch-analysis stack and is not supported on the current
32-bit Pi 3 environment. See the [applet guide](../applets/README.md) before
installing it on a Pi 4-class device.

## Shortcut Commands

The installer writes shortcuts to `$HOME/.local/bin`:

* `homesuite` - canonical node CLI for setup, testing, logs, and support bundles
* `homesuite doctor` - configuration, role, and reachability checks
* `homesuite repl` - safe interactive command test shell
* `homesuite test --live "phrase"` - explicit live one-shot test
* `pptest` and `pplive` - retained compatibility aliases for the safe and live command harnesses
* `ppchattest` - safe chat-style test shell
* `ppchat` - live chat-style shell
* `homesuite-youtube-pair` and `homesuite-youtube-oauth` - YouTube setup helpers

If the commands are not found immediately after install, open a new shell or add `$HOME/.local/bin` to `PATH`.

## Configuration files

Fresh installs use three local configuration files:

* `private_config.py` contains credentials, tokens, URLs, and service API keys.
* `deployment_config.py` contains shared rooms, entities, and non-secret home topology.
* `local_prefs.py` contains per-device overrides such as room, audio routing,
  handset mode, wake-word mode, and device-specific timing.

Public deployments should track only:

* `private_config.example.py`
* `deployment_config.example.py`
* `local_prefs.example.py`

Real local config files should remain outside the upstream Git history.

Do not push real `private_config.py`, `deployment_config.py`, or
`local_prefs.py` files to a public repo.
If you publish a fork, make sure secrets never existed in that public history.

Existing Home Suite deployments that intentionally keep topology in tracked
`app_config.py` remain compatible. They are not given a generic deployment file
during update. See [Room configuration](ROOM_CONFIGURATION.md) for a deliberate
migration path that avoids replacing working room mappings.

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

After editing config, run the doctor and test command routing before starting the live service:

```bash
cd ~/homesuite
homesuite doctor
homesuite doctor --live
homesuite repl
```

At the `homesuite >` prompt, type a phrase such as `service status`.

For syntax checks:

```bash
.venv/bin/python -m py_compile main.py command_dispatch.py app_config.py
```

The GitHub workflow is the portable baseline: it installs dependencies, compiles
the source, and runs the full unit and command-contract suite on CPython 3.9
and 3.13. It cannot validate a particular microphone, speaker, GPIO wiring,
Home Assistant topology, or provider account. Run the role-specific steps in
[ACCEPTANCE.md](ACCEPTANCE.md) before relying on a new appliance.

For service checks:

```bash
sudo systemctl restart homesuite.service
sudo systemctl status homesuite.service --no-pager -l
curl -sS http://localhost:8765/health
```

## Current beta-readiness gaps

The installer is intentionally conservative. It sets up the native runtime, but
users still need to configure their own Home Assistant entities, rooms, media
services, audio devices, and optional hardware.

Known areas that still need more public-release polish:

* first-run interactive configuration
* generated Home Assistant/entity mapping helpers
* clearer OAuth setup helpers for media services
* a server-only mode with fewer local audio/GPIO assumptions
* satellite install mode
* Docker packaging for the central brain role

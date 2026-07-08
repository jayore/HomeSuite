# HomeSuite

HomeSuite is a local-first command layer for Home Assistant, media playback, and homelab status. It lets one natural-language command brain answer from a physical Raspberry Pi appliance, a shell, HTTP, Telegram, Raycast, scheduled jobs, and future satellite devices.

The project started as PiPhone: a vintage telephone with a Raspberry Pi inside it. Lifting the handset started a push-to-talk session, the handset became the microphone and speaker, and physical buttons mapped to house actions. HomeSuite keeps that appliance spirit, but the useful part has grown into a broader home-control runtime.

## What It Does

HomeSuite routes plain-language requests through deterministic handlers first, then falls back to conversational AI only when no device or media command claims the request. That keeps home-control behavior predictable while still allowing normal questions and follow-up conversation.

Current public-alpha capabilities include:

* Home Assistant lights, switches, locks, scenes, scripts, and state queries
* Sonos playback, grouping, volume, sources, announcements, and TTS routing
* Plex movie/show playback with follow-up context like "watch it"
* Spotify search/playback helpers for Sonos-backed music setups
* Apple TV transport and app launch behavior
* YouTube lounge control, channel digests, and playlist/reel helpers
* alarms, timers, and scheduled natural-language commands
* homelab status via Home Assistant and optional direct service APIs
* qBittorrent status and completed-download actions
* Seerr request status
* Uptime Kuma status page summaries
* Synology/Reolink/Speedtest-style status when exposed through Home Assistant
* HTTP and WebSocket access for external clients
* Telegram, Raycast, menu-bar, and physical-button style frontends

See [docs/FEATURES.md](docs/FEATURES.md) for example phrases and supported surfaces.

## Status

HomeSuite is public-alpha software. It works as a real daily-driver system in the original deployment, but the public install path is still new. Expect to configure Home Assistant entities, service credentials, rooms, and audio hardware for your own home.

The first supported install target is a native Raspberry Pi OS style deployment. Docker and satellite packaging may come later, especially for a central "brain" plus lightweight device model.

## Quick Install

On a Raspberry Pi or Debian-like host:

```bash
curl -fsSL https://raw.githubusercontent.com/jayore/HomeSuite/main/scripts/install.sh | bash
```

Install, enable, and start the systemd service:

```bash
curl -fsSL https://raw.githubusercontent.com/jayore/HomeSuite/main/scripts/install.sh | bash -s -- --start
```

The installer creates:

* `.venv/`
* `private_config.py` from `private_config.example.py`
* `local_prefs.py` from `local_prefs.example.py`
* `logs/`, `state/`, and `backups/`
* an optional `homesuite.service` systemd unit

After install, edit your local config files:

```bash
cd ~/homesuite
nano private_config.py
nano local_prefs.py
```

Then test routing:

```bash
.venv/bin/python tools/test_commands.py "service status" --capture
```

Detailed install notes live in [docs/INSTALL.md](docs/INSTALL.md). Credential setup lives in [docs/CONFIGURATION.md](docs/CONFIGURATION.md).

## Configuration Model

HomeSuite separates shared defaults from local/private values:

* `app_config.py` - shared non-secret defaults and feature mappings
* `private_config.py` - local credentials, tokens, service URLs, and API keys
* `local_prefs.py` - per-device room, audio, hardware, and behavior overrides

Only the example files are meant to be committed in public deployments:

* `private_config.example.py`
* `local_prefs.example.py`

Real local config files should stay private.

## Interfaces

The core runtime is `main.py`, but the same command brain can be reached through several surfaces:

* handset / local voice appliance
* `command_repl.py`
* `ppchat.py`
* HTTP `POST /command`
* WebSocket `/ws`
* Telegram bot frontend
* scheduler and alarms
* physical button mappings
* external clients such as Raycast or a menu-bar app

The companion clients are intentionally separate from the core. As those projects become publishable, they should live in their own repos and link back here. This repo should remain the HomeSuite runtime, API, docs, and install path.

## HTTP API

When the in-process server is enabled, HomeSuite exposes:

* `GET /health`
* `GET /manifest`
* `GET /state/{room_id}`
* `POST /command`
* `GET /ws`

Clients should send the API key configured in `HOMESUITE_HTTP_API_KEY`.

Example command request:

```bash
curl -sS http://homesuite.local:8765/command \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $HOMESUITE_HTTP_API_KEY" \
  -d '{"text":"turn on the living room lights"}'
```

## Project Shape

Important files:

* `main.py` - production runtime
* `command_runtime.py` - shared machine-facing command executor
* `command_dispatch.py` - main deterministic routing pipeline
* `interaction_flow.py` - text/chat response behavior
* `spoken_text.py` - TTS-only text normalization
* `homelab_controls.py` and `homelab_clients.py` - homelab status and direct service APIs
* `unified_server.py` - in-process HTTP/WebSocket server
* `tools/test_commands.py` - command harness for validation

More docs:

* [docs/INSTALL.md](docs/INSTALL.md)
* [docs/CONFIGURATION.md](docs/CONFIGURATION.md)
* [docs/FEATURES.md](docs/FEATURES.md)
* [ROADMAP.md](ROADMAP.md)

## Security Notes

Never commit your real `private_config.py`. If you fork or publish a deployment repo, create a fresh public history or scrub history carefully. Deleting a secret in a later commit does not remove it from earlier commits.

## License

A license has not been selected yet. Until a license is added, treat the code as source-available rather than freely reusable open-source software.

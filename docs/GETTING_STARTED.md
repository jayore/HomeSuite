# Getting Started

This guide takes a fresh HomeSuite install to the first useful command. Start small: connect Home Assistant, add an OpenAI key if you want conversational fallback, then add optional media and homelab services one at a time.

## 1. Install

On a Raspberry Pi or Debian-like host:

```bash
curl -fsSL https://raw.githubusercontent.com/jayore/HomeSuite/main/scripts/install.sh | bash
```

To also install and start the systemd service:

```bash
curl -fsSL https://raw.githubusercontent.com/jayore/HomeSuite/main/scripts/install.sh | bash -s -- --start
```

The installer creates `~/homesuite`, a Python virtual environment, local config files, state folders, convenience shortcuts, and optionally a `homesuite.service` unit.

The most useful shortcuts are:

* `homesuite-doctor` - check local configuration
* `pptest "your phrase"` - safe one-shot command test
* `pplive "your phrase"` - one-shot live command that can control devices
* `ppchattest` - safe chat-style test shell
* `ppchat` - live chat-style shell

## 2. Edit Local Config

```bash
cd ~/homesuite
nano private_config.py
nano local_prefs.py
```

Minimum useful `private_config.py` values:

```python
OPENAI_API_KEY = "..."
HA_URL = "http://homeassistant.local:8123"
HA_TOKEN = "..."
HOMESUITE_HTTP_API_KEY = "choose-a-long-random-local-key"
PIPHONE_HTTP_API_KEY = HOMESUITE_HTTP_API_KEY
```

Minimum useful `local_prefs.py` values for a simple non-handset test device:

```python
DEFAULT_ROOM = "living_room"
DEFAULT_SONOS_ROOM = "living_room"
ASSISTANT_AUDIO_OUTPUT_MODE = "local"
WAKEWORD_ENABLED = False
PTT_ENABLED = False
HANDSET_PRESENT = False
```

Leave optional service keys blank until you actually connect those services. Missing optional integrations should produce a clear not-configured response instead of blocking the whole app.

## 3. Run Doctor

Check your local setup:

```bash
homesuite-doctor
```

Run safe network checks for configured services:

```bash
homesuite-doctor --live
```

Fix any `FAIL` items first. `WARN` and `SKIP` items are usually optional services or next-step polish.

## 4. Try a Command

Use capture mode before starting live audio or hardware flows:

```bash
pptest "what lights are on?"
pptest "service status"
```

For chat-style text testing:

```bash
ppchattest
```

## 5. Start or Restart the Service

If you installed the systemd unit:

```bash
sudo systemctl restart homesuite.service
sudo systemctl status homesuite.service --no-pager -l
```

Check the local HTTP health endpoint when the server is enabled:

```bash
curl -sS http://localhost:8765/health
```

## 6. Add Optional Integrations

Once the core path works, add services one at a time:

* Plex for library playback by title or description
* Spotify for music search, library saves, and playlist control
* Uptime Kuma for homelab status
* qBittorrent and Seerr for richer download/request summaries
* Telegram or HTTP clients for remote text access
* YouTube OAuth for lounge and digest features

See [INTEGRATIONS.md](INTEGRATIONS.md) for the keys each service needs and where to get them.

## Troubleshooting Loop

When something does not work:

1. Run `homesuite-doctor --live`.
2. Run the phrase through `pptest "your phrase"`.
3. Check `logs/`.
4. Confirm the entity, room, or service works directly in Home Assistant.

HomeSuite is easiest to debug when each integration is added and tested separately.

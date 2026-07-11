# Getting Started

This guide takes a fresh Home Suite install to the first useful command. Start small: get Home Assistant and conversational fallback working, then add optional media and homelab services one at a time.

The goal of first setup is not to configure everything. The goal is to make `homesuite-doctor` pass its core checks, open `pptest`, and get one safe plain-English phrase returning a sensible result.

## Before You Start

You should already have:

* a Raspberry Pi or Debian-like host on the same network as Home Assistant
* Home Assistant running and reachable from that host
* a Home Assistant long-lived access token
* an OpenAI API key for the currently supported conversational path
* basic comfort editing files over SSH

Optional services such as Plex, Spotify, Uptime Kuma, qBittorrent, Seerr, Telegram, and wake-word hardware can wait until after the core path works.

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
* `pptest` - safe interactive command test shell
* `pplive` - live interactive command shell that can control devices
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
ASSISTANT_AUDIO_OUTPUT_MODE = "local"
WAKEWORD_ENABLED = False
PTT_ENABLED = False
HANDSET_PRESENT = False
```

Leave optional service keys blank until you actually connect those services. For optional integrations, blank is better than fake. Fake hostnames can make diagnostics look configured when nothing is actually reachable.

Missing optional integrations should produce a clear not-configured response instead of blocking the whole app.

Room names and Home Assistant targets are shared deployment configuration in
`app_config.py`. Start with one room, explicitly set unsupported capabilities
to `None`, and use empty lists or mappings for optional collections. See
[Room Configuration](ROOM_CONFIGURATION.md) before adding more rooms.

## 3. Run Doctor

Check your local setup:

```bash
homesuite-doctor
```

Run safe network checks for configured services:

```bash
homesuite-doctor --live
```

Fix any `FAIL` items first. `WARN` items are usually useful follow-ups. `SKIP` items usually mean optional services are intentionally blank.

## 4. Try a Command

Use the safe test shell before starting live audio or hardware flows:

```bash
pptest
```

Then type phrases such as:

```text
what lights are on?
service status
```

For a one-shot check from the normal shell, use `pptest "service status"`.

For chat-style text testing without live device effects:

```bash
ppchattest
```

## 5. Decide When To Go Live

Use `pptest` and `ppchattest` while configuring. Use `pplive`, `ppchat`, or the systemd service only when you are ready for commands to control real devices. You can also run `pplive "exact phrase"` for a single live command.

## 6. Start or Restart the Service

If you installed the systemd unit:

```bash
sudo systemctl restart homesuite.service
sudo systemctl status homesuite.service --no-pager -l
```

Check the local HTTP health endpoint when the server is enabled:

```bash
curl -sS http://localhost:8765/health
```

## 7. Add Optional Integrations

Once the core path works, add services one at a time:

* Plex for library playback by title or description
* Spotify for music search, library saves, and playlist control
* Uptime Kuma for homelab status
* qBittorrent and Seerr for richer download/request summaries
* Telegram or HTTP clients for remote text access
* YouTube OAuth for lounge and digest features

See [INTEGRATIONS.md](INTEGRATIONS.md) for the keys each service needs and where to get them. See [FAQ.md](FAQ.md) for common setup questions.

## Troubleshooting Loop

When something does not work:

1. Run `homesuite-doctor --live`.
2. Run the phrase through `pptest`, or use `pptest "your exact phrase"` for a one-shot check.
3. Check `logs/`.
4. Confirm the entity, room, or service works directly in Home Assistant.

Home Suite is easiest to debug when each integration is added and tested separately. If a phrase behaves oddly, first confirm whether Home Suite's natural-language router claimed it or handed it to conversational fallback; `pptest` output and `logs/` are usually enough to tell.

# Getting Started

This guide takes a fresh Home Suite install to the first useful command. Start
small: get Home Assistant and deterministic text commands working, then add
conversation, voice, media, and homelab services one at a time.

The goal of first setup is not to configure everything. The goal is to make
`homesuite doctor` pass its core checks, open `homesuite repl`, and get one
safe plain-English phrase returning a sensible result.

## Before You Start

You should already have:

* a Raspberry Pi or Debian-like host on the same network as Home Assistant
* CPython 3.9 or newer (the installer checks this before creating or reusing its virtual environment)
* Home Assistant running and reachable from that host
* a Home Assistant long-lived access token
* basic comfort editing files over SSH

An OpenAI API key is optional for deterministic text commands. It is required
for the current conversational and hosted speech-to-text paths.

Optional services such as Plex, Spotify, Uptime Kuma, qBittorrent, Seerr, Telegram, and wake-word hardware can wait until after the core path works.

## 1. Install

On a Raspberry Pi or Debian-like host:

```bash
curl -fsSL https://raw.githubusercontent.com/jayore/HomeSuite/main/scripts/install.sh | bash
```

To install and enable the systemd service without starting an unconfigured
runtime:

```bash
curl -fsSL https://raw.githubusercontent.com/jayore/HomeSuite/main/scripts/install.sh | bash -s -- --systemd
```

The installer creates `~/homesuite`, a Python virtual environment, local config files, state folders, convenience shortcuts, and optionally separate `homesuite.service` and `homesuite-console.service` units.

Fresh installs receive a randomly generated `HOMESUITE_HTTP_API_KEY` in
`private_config.py`; the value is not printed. Reuse that key only in trusted
companion clients.

The canonical node command is `homesuite`:

* `homesuite doctor` - check configuration and enabled node roles
* `homesuite test "phrase"` - run one safe command against real HA state
* `homesuite repl` - open the safe interactive command shell
* `homesuite console` - run the browser management and text console
* `homesuite logs` - show the bounded runtime log
* `homesuite support-bundle` - create a redacted diagnostic bundle

`pptest`, `pplive`, `ppchattest`, and `ppchat` remain available as familiar
compatibility aliases. `pptest` maps to the safe REPL with no arguments and to
the safe one-shot test command when given a phrase.

## 2. Edit Local Config

```bash
cd ~/homesuite
nano private_config.py
nano deployment_config.py
nano local_prefs.py
```

Minimum useful `private_config.py` values:

```python
OPENAI_API_KEY = ""  # Add for conversation or voice.
HA_URL = "http://homeassistant.local:8123"
HA_TOKEN = "..."
HOMESUITE_HTTP_API_KEY = "choose-a-long-random-local-key"
```

Minimum useful `local_prefs.py` values for a simple non-PTT test device:

```python
DEFAULT_ROOM = "living_room"
ASSISTANT_AUDIO_OUTPUT_MODE = "local"
WAKEWORD_ENABLED = False
PTT_ENABLED = False
```

Leave optional service keys blank until you actually connect those services. For optional integrations, blank is better than fake. Fake hostnames can make diagnostics look configured when nothing is actually reachable.

Missing optional integrations should produce a clear not-configured response instead of blocking the whole app.

Room names and Home Assistant targets are shared deployment configuration in
the ignored `deployment_config.py`. Start with one room, explicitly set unsupported capabilities
to `None`, and use empty lists or mappings for optional collections. See
[Room Configuration](ROOM_CONFIGURATION.md) before adding more rooms.

## 3. Run Doctor

Check your local setup:

```bash
homesuite doctor
```

Run safe network checks for configured services:

```bash
homesuite doctor --live
```

Fix any `FAIL` items first. `WARN` items are usually useful follow-ups. `SKIP` items usually mean optional services are intentionally blank.

## 4. Try a Command

Use the safe test shell before starting live audio or hardware flows:

```bash
homesuite repl
```

Then type phrases such as:

```text
what lights are on?
service status
```

For a one-shot check from the normal shell, use
`homesuite test "service status"`.

For chat-style text testing without live device effects:

```bash
ppchattest
```

The browser console provides the same kind of setup loop with configuration
and Doctor context alongside it:

```bash
sudo systemctl start homesuite-console.service
```

Open `http://<homesuite-host>:8766` and sign in with
`HOMESUITE_CONSOLE_KEY`, or `HOMESUITE_HTTP_API_KEY` when the separate console
key is blank. **Configuration > Edit settings** provides guided fields for
common node settings and credentials, including descriptions, examples, and
setup guidance. **Audio** discovers local microphones and outputs, supports
safe playback testing and guided calibration, and keeps PTT and wake-word
profiles device-specific. **Rooms** manages shared room topology. The console
always opens its text surface in Test mode.

## 5. Decide When To Go Live

`homesuite repl` and `homesuite test` read real Home Assistant state but block
writes. Use `homesuite repl --live`, `homesuite test --live "exact phrase"`,
`ppchat`, or the systemd service only when you are ready for commands to affect
real devices.

## 6. Start or Restart the Service

If you installed the systemd unit:

```bash
sudo systemctl restart homesuite-console.service
sudo systemctl restart homesuite.service
sudo systemctl status homesuite-console.service --no-pager -l
sudo systemctl status homesuite.service --no-pager -l
```

Check the local HTTP health endpoint when the server is enabled:

```bash
curl -sS http://localhost:8765/health
```

The server is enabled by default. Every route except the `/health` and
`/healthz` monitoring aliases requires `HOMESUITE_HTTP_API_KEY`; startup fails
closed for the API component when that key is blank.

The management console is a separate authenticated listener on port `8766`.
See [CONSOLE.md](CONSOLE.md) for its guided configuration editor, backup and
restart behavior, text-mode contract, and security model.

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

1. Run `homesuite doctor --live`.
2. Run the phrase through `homesuite repl`, or use
   `homesuite test "your exact phrase"` for a one-shot check.
3. Check `homesuite logs` or `homesuite logs --events`.
4. Confirm the entity, room, or service works directly in Home Assistant.

Home Suite is easiest to debug when each integration is added and tested
separately. If a phrase behaves oddly, first confirm whether Home Suite's
natural-language router claimed it or handed it to conversational fallback; the
REPL output and bounded logs are usually enough to tell.

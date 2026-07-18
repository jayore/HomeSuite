# Getting Started

This guide takes a fresh Home Suite install to the first useful command. Start
small: get Home Assistant and deterministic text commands working, then add
conversation, voice, media, and homelab services one at a time.

The goal of first setup is not to configure everything. The goal is to claim
the browser console, configure the core runtime, pass the required checks, and
get one plain-English phrase returning a sensible result in Chat.

## Before You Start

You should already have:

* a Raspberry Pi or Debian-like host on the same network as Home Assistant
* CPython 3.9 or newer (the installer checks this before creating or reusing its virtual environment)
* Home Assistant running and reachable from that host
* a Home Assistant long-lived access token

An OpenAI API key is optional for deterministic text commands. It is required
for the current conversational and hosted speech-to-text paths.

Optional services such as Plex, Spotify, Uptime Kuma, qBittorrent, Seerr, Telegram, and wake-word hardware can wait until after the core path works.

## 1. Install

On a Raspberry Pi or Debian-like host:

```bash
curl -fsSL https://raw.githubusercontent.com/jayore/HomeSuite/main/scripts/install.sh | bash -s -- --start
```

The installer creates `~/homesuite`, a Python virtual environment, local config
files, state folders, convenience shortcuts, and separate management-console
and runtime services. It starts the console immediately. The runtime remains
stopped on an unconfigured node and is activated from the browser only after
required checks pass.

Fresh installs receive a randomly generated `HOMESUITE_HTTP_API_KEY` in
`private_config.py`; the value is not printed. Reuse that key only in trusted
companion clients.

At the end, the installer prints a browser address such as:

```text
http://homesuite-host.local:8766
```

It also prints an IP-address fallback when one is available.

## 2. Protect The Console

Open the printed address. A fresh installation asks you to create a console
passphrase of at least 12 characters and signs that browser in immediately.
The passphrase is written atomically to the ignored `private_config.py`, a
private backup is retained under `backups/console/`, and the one-time claim is
then disabled.

First-visit claiming is intentionally limited to installer-created fresh
nodes. Keep the new Pi on a trusted home LAN while claiming it: the first
person who reaches an unclaimed console can set its passphrase. Existing
installations and manually blank credentials do not silently enter claim mode.

## 3. Follow Setup

The **Setup** view adapts to the selected node roles and reuses the console's
normal editors. It does not maintain a second configuration store.

1. **Connect Home Assistant**: enter `HA_URL` and a long-lived `HA_TOKEN`, then
   run the read-only connection test.
2. **Review your first room**: match it to a Home Assistant area and select the
   lighting, media, and optional helper targets that exist in your home.
3. **Choose how this node listens**: keep text/API only, enable PTT, enable
   wake-word listening, or enable PTT and wake word together.
4. **Set up voice and audio**: this appears only for a voice role. Choose the
   microphone and playback device, configure OpenAI for the current hosted
   speech path, and run microphone calibration in the real room.
5. **Choose wake words**: this appears only for a wake-word role. Activate one
   or several available models, or add a compatible OpenWakeWord `.onnx` file.
6. **Verify and activate**: run the required live checks and start the runtime.

Optional providers such as Plex, Spotify, Telegram, Alpaca, Uptime Kuma, and
YouTube can wait. Configure them later from **Integrations** as you need them.

## 4. Activate Home Suite

Return to **Setup** and choose **Activate Home Suite**. The console repeats
Home Suite Doctor with bounded live network checks. Required failures block
activation and appear in Diagnostics with links to the owning setup surface;
warnings do not block it.

Activation writes one fixed private marker consumed by an installer-owned
systemd path unit. The browser cannot supply a command, service name, or shell
argument. Once the marker is accepted, the page waits for the local runtime
health endpoint and reports when Home Suite is active.

No additional terminal command is required for the normal path.

## 5. Start With Chat

Once activation succeeds, choose **Open Chat**. Chat sends text through the
same live deterministic and conversational runtime used by voice, Telegram,
and companion clients. It is a useful first interface before those additional
surfaces are configured, and it remains available afterward.

Messages can control devices and create persistent actions. Start with a
read-only question if you want to verify routing without changing anything:

```text
what lights are on?
what is on my calendar today?
```

For a dry run that resolves against current Home Assistant state while blocking
writes, use `homesuite test "phrase"` or `homesuite repl` from the CLI.

## 6. CLI Fallback

The canonical node command remains available for advanced setup and recovery:

* `homesuite doctor` - check configuration and enabled node roles
* `homesuite doctor --live` - include bounded provider and Home Assistant checks
* `homesuite test "phrase"` - run one safe command against real HA state
* `homesuite repl` - open the safe interactive command shell
* `homesuite console` - run the browser management console and Chat
* `homesuite logs` - show the bounded runtime log
* `homesuite support-bundle` - create a redacted diagnostic bundle

`pptest`, `pplive`, `ppchattest`, and `ppchat` remain available as familiar
compatibility aliases. `pptest` maps to the safe REPL with no arguments and to
the safe one-shot test command when given a phrase.

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

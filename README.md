# Home Suite

**Natural, room-aware control for Home Assistant across voice, text, Telegram,
and HTTP.**

Home Suite is a context-aware command and voice layer that runs on your own
hardware. It resolves natural commands against rooms, active media, configured
devices, and recent interaction context, then executes predictable actions
through explicit handlers.

Home Assistant remains the source of truth for devices, areas, scenes, scripts,
and state. Home Suite adds a shared command brain above it: speak in terms of
what you want instead of memorizing entity IDs and service calls. You can still
name a specific device whenever precision matters.

> **Status:** Public alpha. Home Suite is already used daily in its original
> deployment, but installation and hardware setup still assume a comfortable
> tinkerer.

**Name note:** the user-facing name is **Home Suite**. The GitHub repository, install directory, service name, and commands still use `HomeSuite` / `homesuite` as technical identifiers.

## Room Context In Practice

Home Assistant is organized around entities and services, but people usually
think in rooms and intentions. Home Suite can treat a configured room as a
coordinated control surface for its lights, speakers, television, and current
media state.

| You say | How Home Suite resolves it |
| --- | --- |
| `pause` | Pauses the focused or currently active player for the request's room. |
| `what's playing?` | Reports media from the appropriate configured player. |
| `turn it up` | Adjusts the focused media target instead of every speaker at once. |
| `turn off the lights` | Uses the request's room unless you name another room or device. |
| `lights to 30%` | Applies the room's configured brightness strategy: area lights, explicit entities, or a proxy control. |

Exact behavior depends on the room topology and integrations you configure.
Fixed voice devices can have a default room; mobile clients can carry sticky
room focus. Explicit commands such as `turn off the kitchen pendant` remain
available when room-level control is not specific enough.

See [docs/ROOM_CONFIGURATION.md](docs/ROOM_CONFIGURATION.md) for the room model
and [docs/COMMANDS.md](docs/COMMANDS.md) for phrases supported by the current
router.

## Why It Exists

Most assistants are either too rigid or too opaque. Home Suite aims for a useful
middle: natural phrasing on the outside and constrained, inspectable execution
on the inside.

It is designed to:

* understand natural phrases without requiring exact command syntax or Home Assistant entity IDs
* route known home and media actions through deterministic parsing and handlers
* track room, source, transport, and media context so follow-ups can work naturally
* use AI for conversation, summarization, and interpretation where it helps
* expose the same command brain through many frontends
* stay self-hosted and understandable enough to debug

That means you can ask a question conversationally, then follow up with an
action, while execution still goes through a handler constrained to configured
rooms, devices, and services. AI can help with conversation and bounded
interpretation; it is not handed an unrestricted tool for inventing entities or
arbitrary Home Assistant service calls.

Most routine commands do not need an AI call at all. That keeps common control paths faster, cheaper, more predictable, and conservative with token usage.

## What It Is Not

* **Not a replacement for Home Assistant.** It depends on sensible entities,
  areas, scripts, scenes, and state from your existing installation.
* **Not a fully local assistant.** The runtime is self-hosted, but features can
  use OpenAI, gTTS, Spotify, Telegram, and other network services. Which cloud
  dependencies are active is visible and configurable.
* **Not an unconstrained LLM controlling your home.** Known actions use
  deterministic routes; AI-assisted interpretation feeds bounded resolvers and
  configured integrations.
* **Not limited to room-level commands.** Room context removes repetition, but
  direct device and explicit-room commands remain available.

## Minimum Setup

The smallest useful setup is:

* Home Assistant reachable from the Home Suite host
* a Home Assistant long-lived access token
* `private_config.py` for credentials and service endpoints
* `local_prefs.py` for device-specific room, audio, and hardware behavior

An OpenAI API key is required for the currently supported OpenAI speech and
conversational paths, including current-information web search. It is not
required for text-only deterministic commands. Other integrations are optional.

If you do not use Plex, Spotify, Telegram, Uptime Kuma, qBittorrent, Seerr, or
wake-word hardware, leave those settings blank. Startup logs and command
responses should identify unavailable integrations without preventing the core
runtime from starting.

## What It Can Do

Home Suite is built around a shared natural-language processing and command runtime. Current public-alpha areas include:

* plain-English Home Assistant control for lights, switches, locks, scenes, scripts, and state questions
* room-aware defaults and sticky room focus for fixed or mobile command sources
* media and transport focus for Sonos, Apple TV, Plex, Spotify, and YouTube
* media playback by title or description, resolved against your real libraries and services
* announcements and assistant speech routed locally or through room speakers
* alarms, timers, reminders, and scheduled Home Assistant actions in plain English
* homelab and self-hosted service status through Home Assistant and optional direct APIs
* qBittorrent, Seerr, Uptime Kuma, NAS, camera, and internet-status style queries
* AI conversation with continuity into deterministic follow-up actions
* optional web search for current conversational questions such as news and recent events
* HTTP and WebSocket APIs for external clients

See [docs/COMMANDS.md](docs/COMMANDS.md) for example phrases,
[docs/FEATURES.md](docs/FEATURES.md) for a capability overview, and
[docs/WAKEWORD.md](docs/WAKEWORD.md) for the complete wake-word audio pipeline
and setup guide.

## Voice And Interaction

Voice is a first-class interface rather than a separate command implementation.
Both handset push-to-talk and wake-word appliances feed the same interaction
and routing layers used by text clients. The current voice stack includes:

* persistent, per-device microphone profiles and a repeatable calibration tool
* continuous wake-word capture with same-stream command handoff
* streaming speech-to-text with bounded fallback behavior
* VAD-based speech start and endpoint detection
* configurable OpenWakeWord models, thresholds, near-miss logging, and rearm policy
* wake-word-only asynchronous speech and barge-in support
* silent `cancel` and `never mind` interaction dismissal

PTT and wake-word trigger mechanics remain intentionally isolated so tuning one
does not silently alter the other. Hardware quality still matters: far-field
arrays with beamforming and acoustic echo cancellation can substantially improve
room-scale detection and interruption while the assistant is speaking.

See [docs/WAKEWORD.md](docs/WAKEWORD.md) for setup, calibration, diagnostics,
and tuning.

## How It Works

Home Suite routes each request in layers:

1. Normalize the text and attach request context, such as source and room.
2. Let the deterministic natural-language routing layer try to claim the request.
3. If a handler claims it, execute the action through Home Assistant, Plex, Spotify, qBittorrent, or another configured service.
4. If no handler claims it and the request looks conversational, send it to AI fallback, optionally with web search for current information.
5. Store useful context from answers so later commands can refer back to the conversation.

AI can help identify what you are talking about, resolve bounded descriptions,
or answer conversational questions. Actions are carried out by explicit
integrations constrained to your configured environment.

## Core Ideas

* **Home Assistant first:** rooms, entities, scenes, scripts, and most device state should be made sensible in Home Assistant before teaching Home Suite about them.
* **Plain English over entity IDs:** use room names, intentions, and follow-ups instead of forcing every command to mention a specific Home Assistant entity.
* **Context-aware routing:** Home Suite tracks source room, sticky room focus, media/transport focus, and recent AI/media context so short follow-up commands can land in the right place.
* **NLP before AI:** Home Suite first uses deterministic natural-language processing to parse and route commands. Most home-control phrases should never need an AI call.
* **AI where it helps:** conversational fallback, current-information web search, summaries, and bounded media/context interpretation can use AI, but AI is not given unrestricted control of your home.
* **Conservative by default:** deterministic routes make common actions faster, cheaper, easier to test, and more cautious with tokens and credentials.
* **One runtime, many surfaces:** voice, chat, HTTP, Telegram, scheduler jobs, physical buttons, and future satellites all feed the same command router.
* **Optional integrations:** configure only the services you use. Missing optional services should degrade gracefully.

## Ways To Talk To It

The same command brain can be reached through several surfaces:

* a local Raspberry Pi voice appliance
* `pptest` and `pplive` for command-line testing
* `ppchattest` and `ppchat` for chat-style text interaction
* HTTP `POST /command`
* WebSocket `/ws`
* Telegram bot frontend
* scheduler and alarm jobs
* physical button mappings
* external clients such as Raycast or a menu-bar app

Companion clients should live separately from the core runtime as the ecosystem grows. This repo is the Home Suite brain, API, install path, and docs.

## Status

Home Suite is public-alpha software. It is already used as a daily-driver home assistant layer in its original deployment, but the public install and configuration experience is still young. Expect rough edges around first-run setup, hardware differences, OAuth flows, and entity naming.

Optional services are meant to degrade gracefully: configure the pieces you have, leave the rest blank, and missing integrations should explain what credential or URL is needed.

The first supported install target is a native Raspberry Pi OS style deployment. Docker and satellite packaging may come later, especially for a central brain plus lightweight device model.

Home Suite is best for comfortable tinkerers today. It is not yet a polished consumer appliance, and it assumes you are willing to edit config files and look at logs while setting up your own home.

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
* shortcuts such as `homesuite-doctor`, `pptest`, `pplive`, `ppchattest`, and `ppchat`
* an optional `homesuite.service` systemd unit

After install, edit your local config files:

```bash
cd ~/homesuite
nano private_config.py
nano local_prefs.py
```

Then check your setup and open the safe test shell:

```bash
homesuite-doctor
pptest
```

Inside `pptest`, type a phrase such as `service status`. For a single reproducible check, you can also run `pptest "service status"`.

The installer creates shortcuts in `$HOME/.local/bin`, including `homesuite-doctor`, `pptest`, `pplive`, `ppchattest`, and `ppchat`.

If you are new to the project, start with [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md). Detailed install notes live in [docs/INSTALL.md](docs/INSTALL.md), general settings live in [docs/CONFIGURATION.md](docs/CONFIGURATION.md), room topology lives in [docs/ROOM_CONFIGURATION.md](docs/ROOM_CONFIGURATION.md), account and key setup lives in [docs/CREDENTIALS.md](docs/CREDENTIALS.md), service-specific behavior lives in [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md), and voice-appliance setup lives in [docs/WAKEWORD.md](docs/WAKEWORD.md).

## Configuration Model

Home Suite separates shared defaults from local/private values:

* `app_config.py` - tracked, shared non-secret defaults and home topology
* `private_config.py` - local credentials, tokens, service URLs, and API keys
* `local_prefs.py` - per-device room, audio, hardware, and behavior overrides

`app_config.py` is part of the repository because its settings are shared by
devices in one deployment. Review topology changes like code changes.

The real private and per-device files are gitignored; only their examples are
committed:

* `private_config.example.py`
* `local_prefs.example.py`

Real `private_config.py` and `local_prefs.py` files should stay private.

## HTTP API

When the in-process server is enabled, Home Suite exposes:

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
* `command_dispatch.py` - main deterministic natural-language routing pipeline
* `interaction_flow.py` - text/chat response behavior
* `spoken_text.py` - TTS-only text normalization
* `homelab_controls.py` and `homelab_clients.py` - homelab status and direct service APIs
* `unified_server.py` - in-process HTTP/WebSocket server
* `homesuite-doctor` - setup/configuration diagnostics
* `pptest` and `pplive` - interactive and one-shot command harnesses for validation

More docs:

* [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)
* [docs/INSTALL.md](docs/INSTALL.md)
* [docs/CONFIGURATION.md](docs/CONFIGURATION.md)
* [docs/CREDENTIALS.md](docs/CREDENTIALS.md)
* [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md)
* [docs/FEATURES.md](docs/FEATURES.md)
* [docs/COMMANDS.md](docs/COMMANDS.md)
* [docs/FAQ.md](docs/FAQ.md)

## Security Notes

Home Suite can control your home. Treat API keys, Home Assistant tokens, Telegram bots, and HTTP clients as sensitive control surfaces.

Never commit your real `private_config.py`. If you fork or publish a deployment repo, create a fresh public history or scrub history carefully. Deleting a secret in a later commit does not remove it from earlier commits.

## License

A license has not been selected yet. Until a license is added, treat the code as source-available rather than freely reusable open-source software.

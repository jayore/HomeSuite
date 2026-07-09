# Home Suite

Home Suite is a context-aware plain-English command brain for Home Assistant homes. It lets you control rooms, media, announcements, alarms, schedules, homelab services, and AI conversation through one shared runtime that can be reached from voice, text, HTTP, Telegram, physical buttons, and future satellite devices.

Home Assistant remains the source of truth for devices, rooms, scenes, scripts, and state. Home Suite sits above it and turns natural language into predictable actions.

In practice, Home Suite is for people who want to say or type things like `turn off the downstairs lights`, `dim it`, `play music here`, `announce dinner is ready`, `turn off the porch lights at sunset`, `watch the movie where people live in a simulation`, `is anything down?`, or `what is this movie about?`, and have those requests route through their actual home systems instead of a black-box assistant guessing what to do.

**Name note:** the user-facing name is **Home Suite**. The GitHub repository, install directory, service name, and commands still use `HomeSuite` / `homesuite` as technical identifiers.

## Why This Is Useful

Home Assistant is powerful, but everyday control often happens in intentions, rooms, and follow-ups rather than entity IDs. Home Suite adds a deterministic natural-language layer that understands room defaults, source context, media focus, announcements, alarms, scheduled actions, and optional homelab services.

That means you can configure the real devices and services once, then interact with them in plain English across multiple surfaces:

* say `turn off the kitchen lights` instead of remembering entity IDs
* say `I'm in the bedroom` once from a mobile client, then use bare room commands there
* ask `what's playing?`, `pause`, `resume`, or `watch it` without restating the device every time
* announce messages to the right speaker or route assistant speech locally or through Sonos
* create timers, alarms, reminders, and scheduled Home Assistant actions in plain English
* query homelab status without opening dashboards

The result is a home that feels addressable by room, context, and intent, while Home Assistant still owns the underlying devices and state.

## Why It Exists

Most assistants are either too rigid or too magical. Home Suite tries to sit in the useful middle: natural phrasing on the outside, a deterministic natural-language processing and routing layer on the inside.

It is designed to:

* understand natural phrases without requiring exact command syntax or Home Assistant entity IDs
* route home and media actions through deterministic natural-language parsing and handlers, not AI guesses
* track room, source, transport, and media context so follow-ups can work naturally
* use AI for conversation, summarization, and interpretation where it helps
* expose the same command brain through many frontends
* stay local-first and understandable enough to debug

That means you can ask a question conversationally, then follow up with an action, while the action itself still goes through a real handler that checks your actual services and devices. AI can help interpret context, but Home Suite keeps real home actions inspectable and testable.

Most routine commands do not need an AI call at all. That keeps common control paths faster, cheaper, more predictable, and conservative with token usage.

## What To Expect

Home Suite is not a replacement for Home Assistant, Plex, Spotify, Uptime Kuma, or other services. It is the layer that lets you talk to those systems consistently.

The smallest useful setup is:

* Home Assistant reachable from the Home Suite host
* a Home Assistant long-lived access token
* an OpenAI API key for conversational fallback and interpretation
* `private_config.py` and `local_prefs.py` filled in for your device

Everything else is optional. If you do not use Plex, Spotify, Telegram, Uptime Kuma, qBittorrent, Seerr, or wake-word hardware, leave those settings blank. The matching commands should explain what is missing instead of breaking startup.

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
* HTTP and WebSocket APIs for external clients

See [docs/FEATURES.md](docs/FEATURES.md) for example phrases.

## How It Works

Home Suite routes each request in layers:

1. Normalize the text and attach request context, such as source and room.
2. Let the deterministic natural-language routing layer try to claim the request.
3. If a handler claims it, execute the action through Home Assistant, Plex, Spotify, qBittorrent, or another configured service.
4. If no handler claims it and the request looks conversational, send it to AI fallback.
5. Store useful context from answers so later commands can refer back to the conversation.

AI can help identify what you are talking about, but Home Suite does not let AI directly operate your home. Actions are carried out by deterministic integrations after the natural-language router decides what should happen.

## Core Ideas

* **Home Assistant first:** rooms, entities, scenes, scripts, and most device state should be made sensible in Home Assistant before teaching Home Suite about them.
* **Plain English over entity IDs:** use room names, intentions, and follow-ups instead of forcing every command to mention a specific Home Assistant entity.
* **Context-aware routing:** Home Suite tracks source room, sticky room focus, media/transport focus, and recent AI/media context so short follow-up commands can land in the right place.
* **NLP before AI:** Home Suite first uses deterministic natural-language processing to parse and route commands. Most home-control phrases should never need an AI call.
* **AI where it helps:** conversational fallback, summaries, and media/context interpretation can use AI, but AI is not given direct unsupervised control of your home.
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

If you are new to the project, start with [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md). Detailed install notes live in [docs/INSTALL.md](docs/INSTALL.md), credential setup lives in [docs/CONFIGURATION.md](docs/CONFIGURATION.md), and service-specific setup lives in [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).

## Configuration Model

Home Suite separates shared defaults from local/private values:

* `app_config.py` - shared non-secret defaults and feature mappings
* `private_config.py` - local credentials, tokens, service URLs, and API keys
* `local_prefs.py` - per-device room, audio, hardware, and behavior overrides

Only the example files are meant to be committed in public deployments:

* `private_config.example.py`
* `local_prefs.example.py`

Real local config files should stay private.

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
* [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md)
* [docs/FEATURES.md](docs/FEATURES.md)
* [docs/FAQ.md](docs/FAQ.md)

## Security Notes

Home Suite can control your home. Treat API keys, Home Assistant tokens, Telegram bots, and HTTP clients as sensitive control surfaces.

Never commit your real `private_config.py`. If you fork or publish a deployment repo, create a fresh public history or scrub history carefully. Deleting a secret in a later commit does not remove it from earlier commits.

## License

A license has not been selected yet. Until a license is added, treat the code as source-available rather than freely reusable open-source software.

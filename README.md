# HomeSuite

HomeSuite is a local-first natural-language control system for Home Assistant, media playback, and homelab services. It began as PiPhone, a Raspberry Pi-based physical telephone appliance.

The original project was a vintage telephone with a Raspberry Pi installed inside it. The handset acts as the audio input and output, and lifting the handset triggers a push-to-talk style interaction. Buttons on the phone are wired into GPIO and act as physical controls.

Over time, the same command system proved compelling beyond the physical handset, and PiPhone evolved into a broader command platform with multiple interfaces:

* handset voice control
* shell and REPL interaction
* text chat interaction
* HTTP access
* Telegram access
* Raycast integration through the HTTP surface
* scheduler and physical button execution paths

At this point, PiPhone is best understood as a highly customized, room-aware, context-aware natural-language control system whose original and still-canonical embodiment is the physical phone.


## Public-alpha install

HomeSuite is not fully packaged for public release yet, but the native Raspberry Pi install path has started. See `docs/INSTALL.md` for details.

Quick install target for the future public GitHub repo:

```bash
curl -fsSL https://raw.githubusercontent.com/jayore/HomeSuite/main/scripts/install.sh | bash
```

Systemd install/start variant:

```bash
curl -fsSL https://raw.githubusercontent.com/jayore/HomeSuite/main/scripts/install.sh | bash -s -- --start
```

The installer creates local `private_config.py` and `local_prefs.py` files from examples. Real credentials and per-device preferences should stay private and untracked in public deployments.

---

## Core project philosophy

PiPhone is built around a few consistent priorities:

* reliability over novelty
* deterministic behavior over clever behavior
* preserving working behavior unless there is a clear reason to change it
* reusing one shared command brain across multiple interfaces
* favoring small, validated changes over broad speculative rewrites

The current system is deterministic-first:

* speech or text comes in
* deterministic command routing tries to handle it
* AI and conversational fallback are used only when deterministic logic does not claim the input and the request appears conversational enough

That design keeps normal smart-home and media control fast, predictable, and auditable.

---

## What PiPhone can do

PiPhone currently supports a broad set of smart-home, media, and automation interactions, including:

* voice control through the physical handset
* text-based command and chat interaction
* lights, switches, and lock control
* brightness, color, kelvin, and RGB / hex light control
* Sonos control
* Apple TV transport and app preflight behavior
* Plex media launch and playback control
* Spotify-related control paths
* HA-backed homelab/service status for qBittorrent, Seerr, Radarr, Sonarr, Lidarr, Synology NAS, Speedtest, and Reolink sensors
* scene and script triggering through Home Assistant
* now-playing and state-query style responses
* alarms, timers, and scheduled command execution
* physical button shortcuts mapped into the same command brain

This is not a generic assistant shell. It is a deeply customized home-control system shaped around the actual devices, rooms, workflows, and behavior expectations of the environment it runs in.

---

## Configuration and secrets

Runtime credentials and private service URLs live in `private_config.py`.

This private repo currently commits `private_config.py` so multiple Pis can share
the same Home Assistant, OpenAI, Plex, Spotify, Telegram, YouTube OAuth, and
related service credentials. For a portable or public deployment, use
`private_config.example.py` as the committed template and keep real
`private_config.py` values private.

The file is intentionally not named `secrets.py`, because that would shadow
Python's standard library `secrets` module when running from the repo root.
There is also intentionally no `assistant_secrets.py` compatibility shim; stale
imports should fail during testing instead of being masked.

Rename map for git history and older notes:

* `piphone_prefs.py` -> `app_config.py`
* `piphone_local_prefs.py` -> `local_prefs.py`
* `local_secrets.py` -> `private_config.py`
* `local_secrets.example.py` -> `private_config.example.py`
* `gpio_ptt.py` -> `main.py`
* `piphone_repl.py` -> `command_repl.py`
* `gpio_ptt.service` -> `homesuite.service`
* `gpio_ptt.log` -> `homesuite.log`
* `/tmp/gpio_ptt.lock` -> `/tmp/homesuite.lock`
* `deploy/systemd/gpio_ptt.service.current` -> `deploy/systemd/homesuite.service.current`

For environment variables, new deployments should prefer `HOMESUITE_*` names.
The original `PIPHONE_*` names remain accepted as legacy aliases through
`env_compat.py` so older services and satellite clients continue to run.

Normal assistant response audio is controlled in `app_config.py` or a
device-specific `local_prefs.py`:

* `CHATGPT_MODEL` controls the conversational fallback model used when a
  request is routed to ChatGPT instead of a deterministic device handler.
  The default is `gpt-5.4-mini`; use `gpt-5.5` when you want stronger
  deeper-conversation behavior and are comfortable with higher cost.
* `MEDIA_REFERENT_EXTRACTION_ENABLED` lets ChatGPT answers leave short-lived
  searchable media breadcrumbs for deterministic follow-up actions, such as
  "play it" after discussing a song or "watch that" after discussing a movie.
  These breadcrumbs store names/kinds for Plex and Spotify resolution, not
  model-invented service IDs.
* `ASSISTANT_AUDIO_OUTPUT_MODE = "local"` plays responses through the PiPhone
  audio output.
* `ASSISTANT_AUDIO_OUTPUT_MODE = "sonos"` routes responses to a Sonos speaker
  using `ASSISTANT_AUDIO_OUTPUT_ROOM`, the request room, then
  `DEFAULT_SONOS_ROOM`.
* `SONOS_TTS_BACKEND = "gtts"` makes PiPhone generate a gTTS MP3 and hand it
  to Sonos for native announce playback.
* `SONOS_TTS_BACKEND = "home_assistant"` calls Home Assistant `tts.speak`;
  set `SONOS_HA_TTS_ENTITY` to a `tts.*` entity such as `tts.google_en_com`.

---

## How it works at a high level

At a high level, PiPhone works like this:

### Physical handset path

* lift the handset
* PiPhone starts a session
* audio is recorded
* STT transcribes the utterance
* deterministic command routing runs first
* if no deterministic path claims the utterance and the input looks conversational, AI fallback may respond
* spoken or non-spoken feedback is returned depending on the result and current behavior

### Text and remote paths

The same shared command brain can also be reached through non-handset interfaces such as:

* REPL
* text chat shell
* HTTP
* Telegram
* Raycast via HTTP
* scheduler and delayed execution
* physical button triggers

That means PiPhone is not just a voice shell around a phone — it is a shared natural-language control system with multiple frontends.

---

## Architecture at a glance

PiPhone is best understood as a few major layers.

### 1. Appliance runtime

`main.py` is still the canonical production runtime.

It owns or coordinates:

* handset lifecycle
* GPIO
* recording and VAD
* STT entry
* deterministic command routing
* TTS and chimes
* Home Assistant calls
* runtime state
* warmup behavior
* physical button integration

This is the main “phone appliance” runtime.

### 2. Shared command runtime

`command_runtime.py` is the shared machine-facing command executor.

This is the key piece that allows PiPhone’s command brain to be reused across:

* REPL-style command execution
* text chat
* HTTP
* Telegram
* scheduler and alarms
* other future machine-facing integrations

This is one of the most important architectural evolutions in the project.

### 3. Text interaction layer

`interaction_flow.py` adapts raw command execution into more natural text-facing behavior.

This is what makes interfaces like `ppchat.py` and other text clients feel more like a proper conversational control surface instead of a raw debug shell.

### 4. Request context and source tagging

PiPhone now also has a first-pass request-context foundation through `request_context.py`.

This currently supports:

* source identity metadata
* origin/channel metadata
* active request-context installation during execution across several machine-facing and frontend surfaces

Current source-tagged execution surfaces include:

* command-runtime and harness execution
* scheduler
* physical buttons
* HTTP
* Telegram

Important current-state note:

* request context is installed and available
* request context is not yet broadly wired into routing behavior or room-default behavior

That work is still intentionally deferred.

### 5. Feature-specific subsystems

PiPhone now has a number of dedicated subsystems for feature families such as:

* lights
* Sonos and transport
* Apple TV
* Plex
* homelab and self-hosted service status
* scenes and scripts
* alarms and scheduling
* state queries

This means the system is no longer one monolithic command parser, even though `main.py` still remains the central orchestration runtime.

---

## Main entrypoints

Some of the most important current entrypoints are:

### Production runtime
* `main.py`

### Shared or machine-facing runtime
* `command_runtime.py`

### Text and interactive frontends
* `command_repl.py`
* `ppchat.py`

### Remote frontends
* `piphone_wsh.py`
* `pptelegram.py`

### Test and validation tools
* `tools/test_commands.py`

### Key orchestration modules
* `scheduler.py`
* `alarm_controls.py`
* `schedule_controls.py`

### Homelab status
* `homelab_controls.py`

`homelab_controls.py` is the HA-backed first pass at self-hosted service status,
with optional direct qBittorrent and Seerr support for richer details/actions.
It reads configured Home Assistant entities from `app_config.HOMELAB_SERVICES`
and answers phrases such as:

* "how's the homelab?"
* "how many torrents are active?"
* "how many torrents are completed?"
* "what movies are downloading?"
* "media request status"
* "how's the NAS?"
* "are the drives healthy?"
* "how much storage is used?"
* "how's the internet?"
* "any camera alerts?"

This layer is intentionally HA-first for portability. Home Assistant supplies
service counts, speeds, health, request totals, and camera binary-sensor state.
When direct credentials are present in `private_config.py`, PiPhone can also list
active qBittorrent download titles, pause completed torrents, and read Seerr
request counts directly.

---

## Current frontends and interfaces

PiPhone now exists in several forms.

### The physical phone

This is the original and still most distinctive form of the project.

It includes:

* a vintage telephone body
* a Raspberry Pi installed inside
* handset audio input and output
* GPIO-connected physical controls
* off-hook and on-hook session behavior

This is why the project is called PiPhone, even though it has now grown beyond the phone itself.

### REPL and chat-style interaction

The development REPL started as a practical testing tool, but it also revealed that text interaction with the command system was compelling in its own right.

That led to:

* REPL-style command interaction
* `ppchat.py` for more natural text-facing responses

### Remote access

Remote access later grew through:

* HTTP
* Telegram
* Raycast integration through the HTTP layer

This evolution was not accidental. It reflects the fact that the same underlying command system is useful across multiple interfaces, not just the handset.

---

## Important architectural realities

A few things are especially important to understand before working on the codebase.

### `main.py` is still central

Even though many features have moved into dedicated modules, `main.py` is still the canonical production runtime and a high-coupling file.

### Deterministic routing is precedence-sensitive

`process_device_commands()` is order-dependent.
Many regressions come from changing the ordering or broadness of handler claims.

### Not all matching is supposed to be generic

Different parts of the system have intentionally different matching logic:

* generic device resolution
* light target resolution
* runnable scene and script matching
* state-query matching

Trying to force everything into one generic matcher is not automatically an improvement.

### Shared runtime reuse is now a core part of the design

PiPhone is no longer “just the phone script.”
The shared command runtime is a foundational piece of the architecture.

---

## Development and testing

For detailed development, testing, runtime-mode, and maintenance guidance, see:

* `docs/DEV_AND_TESTING.md`

That file covers things like:

* runtime modes
* stub vs live validation
* service-safe testing
* known pitfalls
* maintenance conventions
* future iteration guidance

If you are modifying code, especially in sensitive areas, read that file first.

---

## Project direction

PiPhone began as a physical handset appliance, but it is increasingly evolving toward a broader natural-language control platform.

Likely future directions include:

* additional interfaces beyond handset push-to-talk interaction
* multi-device or satellite-device deployment models
* more local and self-hosted STT, TTS, and LLM infrastructure
* gradual cleanup and generalization for possible broader or open-source use

The detailed future-facing ideas for the project live in:

* `ROADMAP.md`

---

## Where to start if you are new to the codebase

If you are orienting yourself in the project, start here:

### 1. `README.md`
Project identity and architecture overview.

### 2. `docs/DEV_AND_TESTING.md`
Detailed runtime, testing, maintenance, and handoff guidance.

### 3. `ROADMAP.md`
Future directions, possible architecture evolution, and strategic backlog themes.

### 4. `docs/`
Everything else: workflow conventions (`docs/CLAUDE_CODE_WORKFLOW.md`,
`docs/AI_*.md`), the running session log (`docs/AI_HANDOFF_LOG.md`), and
per-session handoff snapshots in `docs/handoffs/`. Start with
`docs/README.md` for an index.

### 5. `main.py`
Canonical production runtime.

### 6. `command_runtime.py`
Shared machine-facing command brain.

### 7. `interaction_flow.py`
Text-facing response and confirmation layer.

After that, explore the feature modules that matter most to the behavior you want to understand.

---

## Summary

PiPhone started as a literal Raspberry Pi telephone project and grew into a broader natural-language control system.

Its identity now includes both:

* a distinctive physical handset appliance
* a shared command brain that powers voice, text, remote, and scheduled interfaces

The physical phone is still the canonical embodiment of the project, but the broader architecture now matters just as much.

If you want the higher-level orientation, start here.  
If you want the operational truth and maintenance rules, continue into `docs/DEV_AND_TESTING.md`.  
If you want the longer-term future direction, continue into `ROADMAP.md`.

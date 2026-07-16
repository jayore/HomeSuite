# Home Suite Roadmap

This document records plausible future work for Home Suite. It is directional,
not a release promise. Current behavior belongs in `README.md`, feature guides,
and `docs/DEV_AND_TESTING.md`; completed work should not remain here as a
future-looking idea.

## Current Baseline

Home Suite already includes:

* one shared deterministic command brain used by PTT, wakeword, HTTP,
  WebSocket, Telegram, scheduled jobs, physical buttons, and test shells
* source-scoped room focus and typed follow-up context for devices, media,
  timers, alarms, locations, and protected command confirmations
* Home Assistant control across lights, switches, locks, covers, fans, climate,
  vacuums, scenes, scripts, rooms, and state queries
* Plex, Spotify, Sonos, YouTube, announcements, and transport focus
* alarms, timers, reminders, bounded delayed actions, sunrise/sunset
  scheduling, and queryable conditional temporary light restoration
* deterministic date, time, weather, distance, astronomy, stock, calendar, and
  homelab queries, plus confirmed Home Assistant calendar event creation and
  conversational web-enabled AI fallback
* continuous OpenWakeWord detection, one-breath command handoff, streaming STT,
  microphone profiles, guided browser/CLI calibration, and local-TTS barge-in
* a public installer, example configuration, operating documentation, and a
  sanitized public GitHub export
* portable GitHub Actions coverage of the unit and command-contract suite on
  CPython 3.9 and 3.13; real Pi audio and GPIO validation remains deliberate

Those are maintained capabilities, not roadmap items.

## Near-Term Quality Work

### Far-Field Voice Hardware

The next meaningful voice improvement is integration and measurement of the
planned far-field microphone hardware. Work should concentrate on:

* confirming hardware acoustic echo cancellation and beamforming behavior
* creating a repeatable microphone profile and calibration baseline
* measuring wakeword false accepts, false rejects, command-word loss, and
  transcription quality at realistic distances
* retuning thresholds from labeled recordings rather than isolated examples
* training deployment-specific wakeword models when the hardware path is stable

Software noise suppression is not a substitute for synchronized acoustic echo
cancellation. PTT behavior must remain isolated from wakeword-specific tuning.

### Automated Validation

The portable test suite runs in GitHub Actions on CPython 3.9 and 3.13. Useful
next steps include:

* a larger multi-turn utterance corpus covering positive routes, collisions,
  unresolved language, and source-scoped continuity
* replayable regression cases derived from real failures without committing
  private utterance history

### Operational Feedback And Privacy

`logs/events.jsonl` can become a practical quality loop rather than a write-only
record. A bounded reporting tool could summarize:

* unhandled commands and error outcomes
* deterministic versus AI routing
* command latency and slow outliers
* recurring phrases that deserve parser or documentation work

Raw utterances may be sensitive. Event logging already provides explicit
enablement, opt-in text storage, and bounded retention. Any reporting tool
should preserve those defaults and work from metadata unless a user has
deliberately enabled command-text capture.

### Sensitive-Action Policy

Home Suite now has a reusable, source-scoped confirmation gate and an opt-in
unlock policy. The remaining security work is a broader allow/confirm/deny
matrix for locks, garage doors, gates, and authenticated network clients.
Policies should eventually distinguish trusted source classes and user identity
where one exists; shared microphones must never imply identity. Unverified
sources and targets must continue to fail closed.

### Documentation Maintenance

Documentation should continue to track behavior rather than historical plans.
Small, periodic passes should:

* remove shipped work from this roadmap
* keep configuration examples synchronized with `app_config.py`
* expose useful existing phrasing that is easy to overlook
* preserve clear boundaries between supported, experimental, and planned work

## Candidate User-Facing Extensions

These are worthwhile candidates, but should be prioritized by actual household
use rather than feature count.

### Recurring Schedules

Common forms such as `every weekday at 7`, `every Tuesday`, and `on weekends`
would extend alarms and reminders naturally. Recurrence requires explicit list,
query, cancellation, persistence, and daylight-saving behavior rather than
expanding only the creation parser.

### External Lists And Tasks

Shopping and to-do support should integrate with an existing source of truth
rather than create a Home Suite-only list. Home Assistant `todo` entities are a
portable option. Apple Reminders would require a separate bridge or API-capable
host and should be evaluated only if it can remain reliable and maintainable.

### Broader Read-Only Home State

The current state-query path intentionally handles a bounded set of domains and
device classes. Capability-aware readbacks could later cover explicit sensors
such as air quality, power use, leaks, and water state without fabricating
entities or exposing every Home Assistant attribute indiscriminately.

### Weather Alerts

Forecasts do not currently include authoritative severe-weather alerts. A
future alert source should be location-appropriate, clearly attributed, and
kept separate from ordinary forecast inference.

### Voice Recovery Phrases

Small recovery affordances may be valuable, especially for wakeword use:

* `what did you hear?` to read back the latest transcript
* clearer retry behavior after an unclaimed or low-confidence command
* natural elliptical follow-ups such as `what about Friday?` where typed context
  can resolve them safely

These should be added from observed failures, not through an unbounded list of
special-case regular expressions.

## Longer-Term Architecture

### Central Brain And Thin Satellites

Every current Pi runs a complete command runtime even though the HTTP command
contract can already serve a brainless client. A future deployment could place
one authoritative runtime on a server or primary device and use room-bound
satellites for capture, playback, and local hardware.

This remains a topology choice, not a prerequisite for adding more frontends.
The self-contained appliance model should stay supported while it remains
useful.

### Local AI Providers

STT, TTS, and conversational reasoning should remain replaceable provider
boundaries. A local AI host could improve privacy and resilience, but should not
require rewriting deterministic commands or weakening the existing cloud path.

### Tool-Using AI

AI may eventually interpret higher-level intent and invoke verified Home Suite
tools. Deterministic handlers should remain the trusted action layer, with real
entity resolution, narrow schemas, source context, and explicit failure. The
model should not invent device identifiers or call Home Assistant services
directly from free-form text.

### Multiple Users

Shared microphones currently cannot identify who is speaking. Per-user
calendar, profile, preference, and authorization behavior requires a trustworthy
identity signal. It should not be inferred from a household-wide wakeword alone.

## Architectural Guardrails

Future work should preserve these principles:

* one command brain across interfaces
* deterministic, auditable execution for real device actions
* typed and source-scoped continuity instead of global pronoun guessing
* configuration-driven rooms, devices, credentials, and hardware differences
* explicit ownership boundaries so one domain does not steal another domain's
  language
* feature additions justified by real use, reliability, or portability
* focused changes with tests proportional to behavioral risk

The physical phone remains a distinctive interface, while Home Suite remains
the reusable command-and-context system behind it. Neither needs to displace the
other.

# Future Architecture Notes

## Purpose

This document captures likely future architectural directions for PiPhone.

It is not a commitment to immediate implementation.
Its purpose is to preserve design thinking that may guide future work, especially as PiPhone evolves beyond a single handset appliance.

This note is intentionally forward-looking.

For current-state maintenance and cleanup guidance, see:

* `AI_THREAD_GUIDE.md`
* `AI_WORKFLOW_PREFERENCES.md`
* `DEV_AND_TESTING.md`

---

## Core direction

PiPhone began as a physical handset project, but it has increasingly become a broader command brain with multiple frontends.

A likely long-term direction is to treat the system as three separable layers:

* frontends
* command brain
* AI service providers

This would allow the system to grow without forcing all concerns to live in one appliance-specific runtime.

---

## Frontends

A frontend is any device or interface that captures user intent and sends it into the PiPhone system.

Existing or plausible future frontends include:

* physical handset device
* push-to-talk Raspberry Pi devices
* wake-word / far-field satellites
* physical button devices
* text chat interfaces
* HTTP clients
* Telegram
* Raycast
* other future remote or local interfaces

A frontend may have local hardware responsibilities such as:

* microphones
* speakers
* LEDs
* buttons
* GPIO
* wake-word detection
* local UX cues

But frontends should ideally remain as lightweight as practical.

---

## Command brain

The command brain is the central orchestration and decision layer.

It should remain the place where the system owns:

* deterministic command routing
* room-aware and device-aware defaults
* stateful context
* Home Assistant integration
* media/device/scene/script logic
* fallback decisions for conversational AI
* shared command semantics across all frontends

In a future architecture, the brain may run as:

* a Raspberry Pi runtime
* a dedicated machine process
* a Docker container
* a service on a NAS or home server

The brain should not be tightly coupled to a specific AI provider or a specific hardware box.

---

## AI services as separate network surfaces

A likely future architecture is to treat these as separate services:

* STT
* TTS
* LLM

The brain may call any of these over the network, regardless of whether they happen to run:

* on the same host
* on a local AI PC
* in containers
* through cloud APIs

This separation is important because it allows:

* cloud or local STT
* cloud or local TTS
* cloud or local LLM
* mixed cloud/local combinations
* provider flexibility over time

The command brain should depend on the service interfaces, not on co-location with the service processes.

Even if all services run on one machine, it is still desirable to think of them as networked surfaces.

---

## Brain and satellite model

A likely future direction is a central brain with multiple frontend satellites.

### Satellite responsibilities

A satellite may handle:

* wake-word detection
* local audio capture
* local metadata collection
* local LEDs/chimes
* local physical buttons or GPIO
* sending requests/events to the brain
* optionally local playback of returned speech or cues

### Brain responsibilities

The brain would handle:

* STT
* deterministic routing
* AI fallback decisions
* Home Assistant calls
* room/device defaults
* command context
* response generation
* arbitration when multiple satellites hear the same utterance

This model keeps satellites comparatively lightweight and preserves the command brain as the central source of truth.

---

## Source arbitration across satellites

If multiple satellites hear the same wake word and utterance, the brain may arbitrate which source is most likely the intended origin.

Possible metadata used for this decision may include:

* wake timestamp
* capture start timestamp
* peak volume
* average energy / loudness
* future signal quality metrics

A simple initial heuristic could be:

* pick the loudest/closest likely source
* use that satellite’s room identity and defaults
* ignore or discard other overlapping captures

This would allow room-aware behavior without requiring all room context to be inferred from natural language alone.

---

## Fixed satellites and room-aware defaults

A fixed satellite can be associated with a known room.

That means it can naturally supply:

* room identity
* default speaker
* default TV/media device
* room-local defaults for brightness, volume, or other controls

This works well with the room-aware concepts that PiPhone already has.

In many cases, adding fixed satellites would allow room-local behavior to come for free simply by making the frontend origin explicit.

---

## Portable satellites and room focus

A future portable satellite introduces a different problem: the device itself may not have a single permanent room.

In that case, room identity cannot always come from the satellite device alone.

A likely solution is a **room focus** model.

### Room focus concept

Room focus would work similarly to transport focus.

The basic idea:

* when a user explicitly targets a room or a device known to belong to a room, the satellite’s room focus is set to that room
* later bare room-sensitive commands inherit that room focus by default
* the focus may later be replaced, cleared, or expire

Example:

* user says: `turn on kitchen lights`
    * room focus becomes `kitchen`
* later user says: `set brightness to 20 percent`
    * the command defaults to `kitchen`

This model would make portable satellites more conversational and less dependent on repeated explicit room naming.

Room focus should likely be:

* per originating satellite or frontend
* not a single global system-wide focus
* overridable by explicit room targeting
* potentially time-limited or replaceable over time

---

## Same codebase, different deployment modes

A practical near-term architecture may still use one codebase with different runtime modes, for example:

* `brain` mode
* `satellite` mode

This could allow shared:

* configuration conventions
* logging patterns
* wire protocol structures
* support utilities

without immediately forcing a full codebase split into separate products.

Later, if the architecture grows enough, the internal separation could be made more explicit.

---

## Open-source implications

If PiPhone is eventually opened to outside users, the brain/satellite and service-separation architecture would make the project much easier to understand and extend.

For example, outside contributors could potentially add support for:

* alternative speakers
* different TV/media integrations
* alternate satellite hardware
* new button devices
* different STT/TTS/LLM providers

This argues for preserving a modular future architecture even if the current runtime remains appliance-centric for now.

---

## Suggested future milestones

A reasonable future sequence may look like this:

### 1. Continue stabilizing the current command brain
Focus on documentation, observability, and bounded cleanup.

### 2. Define a brain/satellite MVP protocol
Decide what metadata and payloads satellites send to the brain.

### 3. Build one wake-word far-field satellite prototype
Prefer off-the-shelf hardware and existing wake-word tooling where practical.

### 4. Keep STT/TTS/LLM provider surfaces configurable
Move toward provider abstraction over time.

### 5. Optionally containerize the brain
Only when the interfaces and architecture are stable enough to justify it.

---

## Notes on future public documentation

If the project is eventually shared more broadly, it will likely need documentation beyond the README.

That may eventually include:

* feature documentation
* configuration and settings documentation
* room/device/default behavior documentation
* device focus and transport focus explanation
* physical button and GPIO documentation
* push-to-talk and frontend behavior documentation
* weather/time/location setup
* media, grouping, and audio behavior documentation
* satellite setup and future brain/satellite deployment documentation

That user-facing documentation is distinct from this architecture note and can be built later as a wiki or docs set.

---

## Status

This note records plausible future directions.
It does not mean these ideas are all immediate priorities.

Its main purpose is to preserve the architectural direction so it can be revisited later without having to rediscover it from scratch.

# PiPhone Roadmap and Future Directions

This document captures likely future directions for PiPhone.

It is intentionally more forward-looking and more speculative than `README.md` or `docs/DEV_AND_TESTING.md`.

Its purpose is to preserve strategic ideas, likely next evolutions, and architectural direction without confusing those ideas with the project’s current state.

This document is directional rather than authoritative. It records plausible future directions, design tensions, and backlog-shaped ideas, not a fixed implementation plan or promise.

These ideas are being captured now so they are not lost across threads, even when they are not immediate priorities.

For current-state architecture and maintenance guidance, see:

* `README.md`
* `docs/DEV_AND_TESTING.md`

---

## 1. Current position

PiPhone began as a physical handset appliance: a Raspberry Pi embedded in a vintage telephone, with GPIO-connected controls and handset-driven audio interaction.

It has since grown into something broader:

* a shared natural-language command system
* multiple frontends
* room-aware and context-aware control behavior
* deterministic smart-home and media control with AI fallback

The physical phone is still the canonical embodiment of the project, but the command brain has become the deeper long-term asset.

This roadmap reflects that broader direction.

---

## 2. Why capture these directions now

PiPhone has already evolved beyond its original form, and future work is likely to continue pulling it in multiple directions at once:

* physical appliance refinement
* broader frontend expansion
* cleaner architecture for multiple devices
* deeper local AI support
* eventual generalization for wider use

Capturing these ideas now helps future work stay intentional rather than purely reactive.

It also helps preserve strategic context that might otherwise disappear across separate development threads.

---

## 3. Near-term themes

A few high-level themes are likely to shape future work:

* broader access to the same shared command brain
* cleaner multi-device support
* better separation between frontends and central logic
* more local and self-hosted AI infrastructure
* stronger context handling across deterministic and AI paths
* gradual cleanup and generalization for possible broader sharing or open-source use

---

## 4. Possible future directions

### A. Wake-word and far-field variants

PiPhone currently centers on handset-driven push-to-talk interaction.

A natural future direction is support for a separate wake-word-based device model using:

* far-field microphones
* always-listening or semi-always-listening wake detection
* a different UX from the physical handset

This could exist:

* instead of the current handset interaction in some contexts
* or alongside it as another frontend

Important design goal:

* preserve the same underlying command brain where possible
* avoid creating a completely separate logic stack just because the trigger model changes

### B. Multi-device and satellite PiPhone support

Status note (updated 2026-06-05):

* foundational request-context and source-tagging groundwork was completed on 2026-05-11 across command-runtime/harness, scheduler, physical buttons, HTTP, and Telegram
* the **receive side of the satellite model already exists**: the in-process `/command` endpoint (`unified_server.py`) accepts `source_id` / `source_type` / `source_room` / `target_room` (with explicit `"satellite"` detection), runs the command through the central brain, and returns the full result — so a satellite frontend needs no command logic of its own
* **portable room focus shipped 2026-06-05**: mobile sources (menubar, raycast, telegram) can say "I'm in the bedroom" to set a sticky room focus, so their bare commands route to that room until changed; stationary sources (handset, buttons) are fixed and refuse. Per-source mobility/grouping lives in `home_registry.SOURCES` (`mobile` / `device_group`); focus persists in `state/source_rooms.json`. See DEV_AND_TESTING.md "Portable room focus".
* what is still missing: (a) an actual satellite client, and (b) broader source-aware *routing/defaults* beyond room focus — most request-context fields are still carried as **metadata only**
* automatic room-focus expiry and source-aware output/transport targeting remain intentionally deferred

A likely major evolution is support for multiple PiPhone devices.

This could include:

* multiple physical PiPhone units in different rooms
* smaller satellite devices
* room-specific microphones or frontends
* frontends that identify which device or room they belong to

A likely model is:

* the originating device sends the command plus device identity and room context
* a central command system performs the real logic
* the response is returned to the originating device

This would shift some room awareness from utterance-only inference toward explicit device-provided context.

That would make PiPhone more like a distributed control system instead of a single self-contained appliance.

### C. Centralized command-brain deployment

Status note (updated 2026-06-05):

* **What exists today:** a single *shared command brain* (`command_dispatch.py`)
  that every frontend routes through — handset, in-process HTTP/WS server
  (`unified_server.py`), scheduler, physical buttons, Telegram, REPL — plus a
  network `/command` API that can already serve a brainless satellite.
* **What does NOT exist today:** the *centralized deployment topology* this
  section describes. Each Pi runs its **own complete brain in-process**
  (Pi 3B = live, Pi 4 = test). It is replicated per-device, not one central
  host with thin clients. There is no containerized/single-host command runtime.
* So the building blocks (shared brain + network API) are done; the topology
  change (one central host, satellites as thin clients) is the open work.

Related to multi-device support, PiPhone may eventually separate into:

* lightweight client or satellite devices
* one central runtime that handles command logic

That central runtime could potentially live in something like:

* a Docker container
* a server process on a primary PiPhone machine
* a dedicated home server

This would support:

* easier reuse across devices
* centralized logs, context, and state
* cleaner separation between frontend hardware and command logic

### D. Local AI infrastructure

A major future direction is reducing or eliminating cloud dependence.

This could include running local equivalents for:

* STT
* TTS
* language models

A future local AI machine could potentially host:

* speech recognition
* speech synthesis
* command-adjacent AI reasoning
* possibly even a local LLM

An ideal architecture would make this largely a configuration or integration swap rather than a total rewrite.

In other words:

* cloud STT / TTS / LLM today
* local STT / TTS / LLM later
* same overall command architecture where possible

Local hosting would also improve:

* privacy
* resilience
* independence from cloud service changes
* control over the full stack

### E. AI earlier in the loop

Today, PiPhone is deterministic-first with AI fallback.

A possible future architecture is one where AI participates earlier in the flow without replacing the deterministic command system.

One possible model:

* deterministic command handlers still execute fast, explicit commands
* successful deterministic actions are written into a rolling context log
* AI sees that log and can use it as state and context
* when requests are too ambiguous, expressive, or high-level for deterministic handling, AI can interpret intent and use PiPhone’s own tools or commands as building blocks

This could enable things like:

* better recovery from edge cases or near-misses
* richer interpretation of descriptive requests
* tool-use patterns where AI leverages PiPhone’s own command capabilities

Examples of future AI-enhanced intent:

* `set the light to the color of the sky`
* `make the room feel warmer`
* `play something calm in here`

The central principle should remain:

* AI augments deterministic control
* AI does not erase deterministic control

The deterministic command system should continue to function as the trusted tool layer.

### F. Open-source preparation and generalization

There is a real possibility that PiPhone may eventually be opened up for broader outside use.

If that happens, likely preparation work would include:

* depersonalizing config and environment assumptions
* moving more hardcoded local preferences into preference or config files
* making service paths and setup more portable
* reducing machine-specific assumptions
* improving installation and setup documentation
* eventually adding broader user-facing documentation or a wiki covering:
    * feature-by-feature capabilities
    * settings and configuration
    * room/device/default behavior
    * device focus and transport focus
    * media grouping, swapping, and audio behavior
    * weather and time/location setup
    * push-to-talk behavior
    * physical buttons and GPIO behavior
    * frontend and satellite setup
* making project structure and naming easier for outside users to understand
* renaming or reorganizing some files into more conventional forms

This may also eventually raise naming questions, for example:

* whether older runtime, service, and environment names should eventually follow the newer `main.py` naming
* whether the broader project should keep the `PiPhone` name or adopt something that reflects its larger current scope

That kind of change is not urgent, but it is worth preserving as a future possibility.

---

## 5. Architectural directions worth preserving

Even as the project evolves, some architectural principles seem worth preserving.

### Shared command brain across interfaces

One of the strongest directions in the project is that the same core command logic should be reused across:

* handset
* text
* remote
* scheduler
* physical buttons
* future satellites

This is better than creating separate command forks per interface.

### Deterministic commands remain important

Even if AI becomes more central later, deterministic command routing is still a major strength:

* fast
* predictable
* auditable
* easy to reason about
* well suited to smart-home and transport actions

Future AI integration should probably build on top of this rather than replacing it wholesale.

### Frontends and command logic should become more separable over time

PiPhone began as a tight appliance runtime.

As the number of frontends grows, it becomes more important that:

* frontend-specific concerns stay near the edge
* the shared command brain remains reusable
* device identity and room context can be passed into the core cleanly

### Room awareness should become more explicit

Today, some room awareness is inferred from:

* phrasing
* defaults
* device maps

In a multi-device future, room context will likely need to become a first-class concept rather than something inferred ad hoc.

### The physical phone should remain a strategically valuable interface

Even in a broader multi-frontend future, the physical phone may remain an important product-defining interface rather than merely an early prototype.

Its interaction model is distinctive and worth preserving, even if the broader system grows far beyond it.

---

## 6. Questions and design tensions to revisit later

These are not immediate tasks, but they are useful future design questions.

### How centralized should the system become?
Should PiPhone remain:

* primarily a self-contained appliance per device

or evolve toward:

* thin clients or satellites with one central logic host?

### How much AI should be in the loop?
Should AI remain:

* a fallback only

or evolve toward:

* a tool-using, context-aware layer that participates much earlier?

### How much project identity should stay tied to the phone?
The physical phone origin is distinctive and valuable.

But as the system expands, it may eventually make sense to ask:

* is PiPhone still primarily “the phone”?
* or is PiPhone now the broader control system, with the phone as one frontend?

### How much should be generalized for outside users?
If the project opens up more publicly, some of the current design may need to shift from:

* highly personalized and environment-specific

toward:

* configurable and reusable

without losing the practical strengths that came from building it for a real home.

---

## 7. Things that should happen before ambitious future expansions

Before large architecture changes, it would be beneficial to continue improving:

* documentation quality
* clear testing workflows
* runtime-mode clarity
* separation between runtime layers
* confidence in scheduler and alarm behavior
* confidence in transport and media edge cases
* reduction of unnecessary vestigial code
* cleaner config and preference boundaries

This is not glamorous work, but it makes the larger future directions much safer.

---

## 8. Suggested roadmap framing

If future work is prioritized, a plausible rough ordering might be:

### Shorter-term
* continue cleanup and clarification work
* improve docs and handoff quality
* harden test and validation workflows
* keep reducing confusion between frontends and runtime layers

### Medium-term
* improve shared-runtime abstractions
* make room and device identity cleaner
* expand homelab support beyond HA-backed status into optional direct service APIs
  for qBittorrent / Seerr-style title lists, completed-download actions, and
  richer service-native workflows
* prepare for multi-device support
* make local-vs-cloud STT/TTS/LLM integration cleaner

### Longer-term
* multi-device or satellite architecture
* centralized containerized command-brain deployment
* local AI stack
* richer AI-in-the-loop architecture
* broader open-source generalization

This is a directional ordering, not a commitment.

---

## 9. Relationship to the current docs

Use the documents like this:

* `README.md`
    * what PiPhone is today
    * current architecture at a glance
    * where to start
* `docs/DEV_AND_TESTING.md`
    * runtime truth
    * testing guidance
    * maintenance conventions
    * handoff-critical project knowledge
* `ROADMAP.md`
    * future directions
    * architectural possibilities
    * backlog-shaped strategic ideas

---

## 10. Closing thought

PiPhone’s most interesting future may not be choosing between:

* physical appliance
or
* broader natural-language system

It may be preserving both:

* a distinctive physical interface
* and a reusable command-and-context engine behind it

That combination is what makes the project unusual.

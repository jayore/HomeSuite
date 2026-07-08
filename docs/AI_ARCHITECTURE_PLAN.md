# AI Architecture Plan

**Working title for the broader system: HomeSuite** (proposed rename from PiPhone if/when the project goes public — see §11).

---

## About this document

This is a **directional design document**, not an implementation plan or a commitment. It captures the architecture, principles, and sequencing for evolving PiPhone from a deterministic-first command system with AI fallback into a coherent event-driven home runtime where the deterministic system and AI co-operate through a single shared model of state, memory, intent, and focus.

The core of this document originated in a long design conversation with ChatGPT (2026-05-22) where Jason worked through the architecture in detail. That substance is preserved here in full — restructured for coherence and supplemented with implementation-grade commentary, concrete sequencing guidance, and pragmatic critique woven in. Where commentary or critique was added on top of the original brief, it is marked clearly:

> **🔶 Editor's note** — added by Claude to flag implementation realities, weak points, or sequencing concerns. These are honest critical reactions to the design as written, not agreement-by-default.

The high-level goal, in Jason's own words:

> "Meaningfully merge the AI and the deterministic NL logic into one system with a common memory and understanding."

This document is intended to survive thread boundaries. If you are picking this up in a future session: read it end-to-end before proposing changes to the design. The conceptual structure here is the product of substantial back-and-forth and the principles are interconnected — pulling one out without understanding the others usually breaks the model.

**Status:** design captured 2026-05-22. No code work toward this plan has begun. The current PiPhone implementation is the starting point. See §10 for the recommended sequencing against other in-flight work (notably the Mac menubar app).

---

## Table of contents

- [1. Vision](#1-vision)
- [2. System architecture](#2-system-architecture)
- [3. AI as semantic compiler](#3-ai-as-semantic-compiler)
- [4. Intent and event model](#4-intent-and-event-model)
- [5. When the deterministic system calls AI](#5-when-the-deterministic-system-calls-ai)
- [6. Media resolution (Plex & Spotify)](#6-media-resolution-plex--spotify)
- [7. Knowledge persistence](#7-knowledge-persistence)
- [8. Production hardening](#8-production-hardening)
- [9. Implementation reality — critical read](#9-implementation-reality--critical-read)
- [10. Concrete path forward](#10-concrete-path-forward)
- [11. Renaming: PiPhone → HomeSuite](#11-renaming-piphone--homesuite)
- [12. Key insights to hold onto](#12-key-insights-to-hold-onto)
- [Appendix A: Glossary](#appendix-a-glossary)
- [Appendix B: Open questions for future sessions](#appendix-b-open-questions-for-future-sessions)

---

## 1. Vision

### 1.1 One-sentence definition

> A **stateful home orchestration system with deterministic execution and AI-assisted semantic reasoning**, where all interfaces (voice, buttons, apps, chat) converge into a single event-driven control plane.

### 1.2 What HomeSuite is and is not

It is **not**:
- a voice assistant
- an LLM agent
- a smart-home dashboard

It is:
- a **home runtime (Home OS layer)** with optional AI cognition

> **🔶 Editor's note** — the "Home OS / kernel" framing is conceptually clean and helps prevent the system from drifting toward a generic LLM-agent shape. But it's also aspirational marketing language that can mislead implementation: PiPhone is a Python service on a Raspberry Pi, not an operating system. Use the framing as a *constraint check* ("does this feature violate kernel-vs-userspace boundaries?") rather than as a *modeling target* ("we need a scheduler subsystem like Linux's"). Avoid building elaborate kernel abstractions that don't earn their keep at the scale of a household control system.

### 1.3 Three ways AI could integrate (orienting framework)

> **🔶 Editor's note** — added by Claude as an orienting framework. Before diving into the principles below, it's useful to understand that AI can integrate at three meaningfully different points in a system like this. The architecture in this document is a *composition* of all three, with strict boundaries between them.

There are three meaningfully different ways AI could integrate with a deterministic command system:

**1. AI as a smarter dispatcher.** Instead of (or alongside) rigid regex-based routing, an LLM classifies intent and routes to the right handler. Cheap if you cache common phrasings, expensive if every utterance hits an API. Useful for ambiguous phrasings the regexes miss.

**2. AI as a context layer over deterministic actions.** Every command that fires gets logged to a structured event store ("you set living room to red at 9:14pm"). The LLM reads that store as memory and can answer questions like "what did I just do to the kitchen lights?" or "set things back to how they were an hour ago." This is the *shared memory* idea — the deterministic system and AI read/write the same log.

**3. AI as a tool-using agent.** The LLM has access to PiPhone's command catalog as tools (just like Claude has tool-use). For a request too expressive for the deterministic system ("make the room feel cozier"), the LLM can chain multiple deterministic commands to achieve it. The deterministic system stays the trusted action layer; AI is the planner.

**These compose.** The shared memory underlying #2 is what enables #3 to be effective ("cozier than what? cozier than now, based on current state, your historical preferences"). And #1 can be the gating mechanism that decides which path a given utterance takes. The architecture below picks pieces of all three and assigns each a strict boundary.

### 1.4 Core design principles (non-negotiable foundations)

These are the rules everything else grows from. They are stated in priority order; later rules defer to earlier ones.

#### 1. Deterministic system owns truth
- state
- focus
- device control
- scheduling
- execution
- logging

#### 2. AI never directly mutates state
AI can:
- interpret
- propose
- plan
- request

But cannot:
- execute directly
- bypass orchestration
- own scheduling or focus

#### 3. Everything becomes an event
Every action produces:
- a structured event log entry
- an updated state snapshot
- optionally triggers follow-up reasoning

#### 4. AI is episodic, not continuous
AI runs in:
- triggered invocations
- scheduled invocations
- follow-up invocations

Not:
- polling loops
- background agents

> **🔶 Editor's note** — these four principles are the strongest part of the design. They map cleanly onto what PiPhone already does (deterministic system owns truth, AI is fallback-only) and they prevent the most common LLM-integration failure modes (state drift, hidden agentic loops, unbounded execution). If you remember nothing else from this document, remember these four.

---

## 2. System architecture

### 2.1 Layered mental model

#### (A) Input layer
All surfaces:
- PTT device
- wake word
- Telegram
- Raycast
- Mac menubar (future)
- HTTP API
- ESP32 / e-ink / dashboards (future)

All inputs converge here.

#### (B) Session + focus layer
Maintains:
- current room focus
- device focus
- last active entity
- follow-on context ("it", "more", "that")

This is short-term working context.

#### (C) Event log (shared memory core)
Append-only structured log.

Contains:
- intent
- entities
- actions
- results
- timestamps
- source surface
- focus transitions

This is the unified memory for both the deterministic system and AI.

#### (D) Deterministic orchestrator (the "OS kernel")
Responsible for:
- intent routing
- confidence scoring
- entity resolution
- state mutation
- scheduling
- validation
- execution
- logging

This is the core brain of the system.

The orchestrator exposes a small, **explicit internal API** that everything else (including AI) goes through:

```
execute_intent(intent)    →  validate, route, execute, log
set_focus(scope)          →  update the focus stack
schedule_event(spec)      →  persist a scheduled re-invocation
query_state(scope)        →  read filtered state for a caller
```

This API is the single integration surface for AI requests, NL handlers, and any new client. Every operation passes through here so that logging, validation, and ownership of state remain centralized. It's the implementation of the "tool boundary clarity" rule (§8.8).

#### (E) AI reasoning layer (episodic)
Invoked only when needed. Operating modes:
- **helper** (fill gaps)
- **fallback** (no NL claim)
- **planner** (multi-step reasoning)
- **recheck** (scheduled follow-up)

AI output is **always structured**.

#### (F) Execution modules
- Home Assistant integration
- media control (Plex, Spotify, Sonos, Apple TV)
- lights
- sensors
- notifications
- etc.

Only the orchestrator calls these.

> **🔶 Editor's note** — the current PiPhone implementation already has rough analogs of layers (A), (D), and (F). Layer (B) exists in a primitive form (`request_context.py`, `room_context.py`). Layer (C) does not exist as a structured log. Layer (E) exists as AI fallback but only with ad-hoc context. The biggest greenfield piece of work is (C); the biggest refactor target is (B) (which needs to become a stack — see §2.4).

### 2.2 End-to-end control flow

**Step 1: Input arrives.** Example: *"set the light to ocean color"*

**Step 2: Deterministic NL tries to claim intent.**
Output:
- intent = `set_light_color`
- target = `living_room_light`
- missing = color value
- confidence = medium/high but incomplete

**Step 3: Orchestrator decides.**
- Case A: fully resolvable → execute immediately
- Case B: partial gap → call AI helper
- Case C: unknown intent → call AI fallback

**Step 4: AI invoked (episodic).**
AI receives:
- event log snapshot (filtered)
- focus state
- invocation type (helper / fallback / planner / recheck)
- strict output schema

AI returns:
```json
{ "resolved_color": "#4A90E2", "confidence": 0.86 }
```
or:
```json
{ "intent": "query_event", "query": "next rocket launch vandenberg" }
```

**Step 5: Deterministic validation.**
Orchestrator:
- validates entities
- checks capabilities
- applies focus rules
- resolves final action

**Step 6: Execution.**
- call Home Assistant / modules
- update state
- update focus
- log event

**Step 7: Event emitted.**
This event becomes:
- memory
- future context
- AI input for next invocation

**Step 8: Optional AI re-invocation.**
Triggered by:
- event type
- schedule
- ambiguity
- user follow-up

### 2.3 Scheduler as re-entry mechanism

Scheduler lives **only** in the orchestrator. AI can *request* future re-invocation but cannot own the timing.

AI request example:
```json
{
  "schedule_request": {
    "type": "reinvoke_ai",
    "in_minutes": 10,
    "reason": "waiting for external event resolution"
  }
}
```

or:
```json
{
  "schedule_request": {
    "type": "conditional",
    "trigger": "launch_time_detected"
  }
}
```

Orchestrator decides:
- whether to accept
- when to run
- what context snapshot to include

Conceptual framing: **scheduler = controlled reinvocation of the reasoning system**. Not a background automation engine, not an AI-owned loop, not an independent process manager.

> **🔶 Editor's note** — the current scheduler (`scheduler.py`, fixed on 2026-05-22 to be a single in-process instance inside `gpio_ptt.service`) is already structurally close to what's needed. The piece missing today is the **conditional / state-change trigger** mode. The current scheduler only supports absolute and relative time triggers. Adding event-conditioned triggers (e.g., "fire when X happens") is meaningful work and would need careful design to avoid polling explosions.

### 2.4 Focus: from single value to stack

The current PiPhone focus is essentially a single value (the active room). The design upgrade: **focus is a stack of active contexts.**

Example stack:
```
[Home]
  → [Living room]
    → [TV session]
      → [Rocket launch task]
```

This enables:
- "pause that"
- "go back"
- "resume previous context"
- "apply to current focus only"

Focus also has **types**:
- **spatial focus** (room / device)
- **interaction focus** (last entity)
- **temporal focus** (current session intent)
- **task focus** (active plan, e.g., "rocket prep")

The deeper role of focus, beyond UX:

> **Focus is the semantic compression layer.** It is what prevents AI from needing full-world reasoning every time. It compresses "what matters right now" / "what this interaction is about." Without it, AI always over-reasons, context windows bloat, and ambiguity increases. With it, follow-ons become natural, and short commands ("more", "same again") become powerful.

> **🔶 Editor's note** — the focus stack is one of the more genuinely valuable upgrades in this brief. Today's PiPhone routing reads `_request_room` and a few other globals in a flat way; "follow-ons" like "more" and "turn it up" mostly don't work because there's no place to anchor them. Making focus an explicit stack is non-trivial (touches every handler that reads room context) but pays off in actual UX. Worth ~3-5 sessions of work when it's time.

### 2.5 Threading (Interaction Binding Context)

A **thread** is a lightweight semantic session overlay that scopes "what we are currently reasoning about" — distinct from focus, which scopes "where we are."

| Concept | Purpose |
|---|---|
| **Focus** | physical / contextual location (room, device, activity) |
| **Thread** | semantic continuity of reasoning |

Example:
- Focus: living room
- Thread: "ocean color lighting adjustment"

A thread contains:
```json
{
  "active_thread_id": "abc123",
  "thread_owner": "nl | ai | mixed",
  "expires_at": "...",
  "scope": "focus_limited"
}
```

Threads are created when AI gets involved in a multi-turn flow. Subsequent inputs are checked against active threads — if they belong to the thread's scope, they route into the thread rather than to fresh NL parsing.

This is what gives you natural follow-ups ("more", "same", "that one", "do it again") without inventing a brittle "AI takeover mode."

### 2.6 Routing chain

When input arrives, the orchestrator routes through:

```
1. Thread check    → is there an active thread that owns this input?
2. Focus check     → what context does this input apply in?
3. NL resolution   → try deterministic claim
4. AI fallback     → if uncertain, invoke AI in the right mode
```

**AI should never override routing.** AI only influences interpretation *inside a thread* it has been invited into.

### 2.7 Continuity scope: context bubbles per endpoint

A subtle but important design principle: **conversational continuity should be scoped per "context bubble" — typically one logical setting or modality — rather than shared globally across every frontend.**

The instinct when introducing shared command logic is to also share conversational state: one history, one set of pronouns, one focus, all visible to every frontend. This sounds clean but inverts how humans actually use the system. A user is almost always in a single setting at a time: at a desk on the laptop, in the kitchen on the handset, in bed on the phone. Context belongs to the setting. Leaking context across settings is a bug, not a feature — the Tampa weather you asked about at your desk two hours ago is noise when you pick up the handset in the kitchen.

#### Bubbles today

| Bubble | Members | Why grouped |
|---|---|---|
| Desk / laptop | menubar, Raycast, Telegram, REPL | All operate from the same physical setting and modality |
| Handset PTT | PTT loop on Pi 3B | Distinct physical setting and modality |
| Wakeword / far-field | (future) | Bound to its room |

The handset and wakeword bubbles deserve to share continuity when they coexist in the same room — a wakeword question followed by picking up the handset is a single thought.

#### Implications

- **Don't unify just to share state.** Operational simplicity (one process, one log) is a legitimate reason to unify runtimes. Cross-frontend context coherence usually isn't.
- **If you unify the runtime, keep the bubbles explicit in code.** Continuity boundaries should exist because they were chosen, not because they happen to fall on process boundaries.
- **Multi-device strengthens this.** Kitchen and bedroom PiPhones should each be their own bubble. They share the deterministic command brain (HA state, scenes, schedules) but not conversational context.
- **Threads (§2.5) live within a bubble.** A thread is "what we're reasoning about right now"; a bubble is "where this conversation lives."

#### What this means for the event log

The event log (§2.2 (C)) is global — every command from every bubble lands there, because it represents what happened in the home. But the **AI context window** (§3.5) built for a given AI invocation should be scoped to the requesting bubble's recent history, not a global firehose. This keeps AI responses contextually relevant to the user's current setting.

---

## 3. AI as semantic compiler

### 3.1 The core framing

AI is a **semantic compiler**, not a controller, agent, executor, or memory.

```
natural language + context → AI → structured intent → orchestrator → execution
```

This framing solves nearly every architectural ambiguity. AI translates ambiguity into machine-readable intent; the deterministic system does everything else.

### 3.2 Same AI, different roles

The AI model is **the same engine across all invocations** — but invoked with different roles, contracts, and framing contexts depending on why it was called.

#### A. Variable fill mode (slot filler)
Used when NL is mostly correct but missing a parameter.

Examples:
- "color of the ocean"
- "make it warmer"
- "like yesterday"

AI prompt role: *"You are resolving missing parameters in an otherwise valid structured intent."*

Output constraint: only fill slots, no new intent creation.

#### B. Fallback interpretation mode
Used when NL fails entirely.

Examples:
- "do the thing I usually do when I leave"
- "fix the vibe"

AI prompt role: *"You are interpreting ambiguous user intent into a valid structured command."*

Output: full intent allowed (still constrained to known capabilities).

#### C. Planner mode
Used for multi-step workflows.

Examples:
- rocket launch prep
- bedtime routines
- scenes + scheduling chains

AI prompt role: *"You are generating a multi-step plan expressed as structured intents."*

Output: ordered actions, optional scheduling requests.

#### D. Re-entry / follow-up mode
Used after scheduler wakes the system.

Examples:
- "what changed?"
- "continue previous plan"

AI prompt role: *"You are resuming a partially completed workflow using updated system state."*

#### Why role-framing matters

Without explicit roles, AI behavior becomes inconsistent across calls — the same input yields different reasoning styles, debugging becomes impossible. With role-framing, AI becomes predictable because **its role defines its constraint space**, not its prompt text alone.

> **Single AI, multiple contracts — not multiple AIs.** You are not building separate models per function. You are building one model + multiple strict contracts. This is what keeps the system from becoming prompt spaghetti.

### 3.3 What AI is allowed to do

- read event log (filtered slices)
- read focus state
- read device state (via orchestrator tools)
- propose structured intents
- propose schedules
- propose multi-step plans
- request more context (through the orchestrator)

### 3.4 What AI is never allowed to do

- direct execution
- direct state mutation
- bypass orchestrator
- uncontrolled loops
- persist state between invocations
- store knowledge directly
- override routing

**AI is stateless, even when it feels like it isn't.** Even when AI does planning, re-checks, or continues tasks, it must always be treated as *a fresh function call over system state*. NOT a continuing agent, a memory-bearing entity, or a persistent process. This is what makes the system reproducible and debuggable.

### 3.5 AI context packet (canonical structure)

Every AI invocation receives a **structured snapshot packet**, never raw history or freeform context.

```json
{
  "mode": "slot_fill | fallback | planner | reentry",
  "focus": ["home", "living_room", "tv_session"],
  "events": [
    "light_set_to_warm_white",
    "user: make it like yesterday",
    "scene_applied: evening_relax"
  ],
  "intent_so_far": {
    "intent": "set_light_color",
    "target": "living_room_light",
    "color": null
  },
  "entities": ["living_room_light", "tv", "sonos_speaker"],
  "constraints": {
    "no_new_devices": true,
    "must_use_focus": true
  }
}
```

**Context budget rules:**
- recent events only (last N, normalized)
- focus stack
- active schedules
- device state summary
- current session metadata
- invocation mode

**Never include:**
- full history
- entire event log
- global memory dump

**Prefer state diffs over full history.** Instead of sending the full event log on every invocation, send:
- the last N events (or a summary of them)
- plus the **state diff since the last AI invocation in this thread**

The diff-based approach reduces token load, improves relevance (AI focuses on what changed, not what's already known), and prevents "overfitting to old context." It's especially valuable in re-entry/recheck mode, where AI is being asked "what's different now?" — the answer is literally a diff.

The orchestrator must do **context compression before every AI invocation**. This is what prevents bloated prompts, irrelevant reasoning, and hallucinated long-range causality.

> **🔶 Editor's note** — this section is conceptually right but skips the hardest part: **what's the actual token budget, and how do you compress?** A realistic prompt budget for a modern model is ~10-50k tokens depending on cost tolerance. Recent N events with full detail can blow that budget fast. Real implementations need a *compression layer* (event summarization, embedding-based event retrieval, or both) before this works at scale. Worth designing concretely before any of this ships.

### 3.6 AI requesting more context

If AI needs more info, it cannot fetch it directly. It must request it via a structured response:

```json
{
  "request": "get_last_scene_state",
  "reason": "resolve lighting ambiguity"
}
```

Then the orchestrator responds and AI continues. This prevents uncontrolled tool access creep — AI only sees tools through the orchestrator API, even if it could call modules.

### 3.7 Bounded planning

AI planning must always be bounded.

- max steps per plan
- validation per step
- execution checkpointing

Conceptual framing: *AI proposes; system executes step-by-step with checkpoints.* This prevents runaway orchestration chains, keeps logs readable, and ensures recovery is possible.

> **AI outputs are not actions — they are intent proposals.** Even when AI is confident, it never outputs "do this now." It outputs "this is the structured intent you should execute." That keeps logging clean, replay possible, and validation centralized.

---

## 4. Intent and event model

### 4.1 Canonical intent schema

Everything converges into a canonical intent object before execution:

```json
{
  "intent_id": "abc123",
  "intent": "set_light_color",
  "target": "living_room_light",
  "parameters": { "color": "#4A90E2" },
  "confidence": 0.86,
  "source": "nl | ai_slot_fill | ai_fallback | ai_planner",
  "thread_id": "t_ocean_light"
}
```

**Structured intent is the only execution contract.** NL output → intent. AI output → intent. Fallback chat → intent (if actionable). Follow-ons → intent. Without this principle, the system becomes dual-language (NL vs AI vs raw text). With it, everything becomes composable.

The **intent ownership rule:**
- NL owns: initial claim
- AI owns: semantic completion or reinterpretation
- Orchestrator owns: final intent + execution

This prevents duplicate reasoning authority, conflicting interpretations, and "AI vs NL disagreement loops."

### 4.2 Event log as causality, not just memory

The event log is **not just memory / history** — it is the **causal graph of the system**.

That unlocks:
- "why did the lights turn on?"
- "what caused this state?"
- replay / debug mode
- AI reasoning grounded in actual system causality

This is a major differentiator vs Alexa/Siri-style systems.

Even if you never train a model on it, the event log behaves as **a self-improving interaction graph**:
- you can analyze failure modes
- tune routing logic
- adjust confidence thresholds
- refine focus behavior

Treat the event log as: instrumentation first, memory second, intelligence substrate third.

> **🔶 Editor's note** — the brief is strong on *why* the event log matters but silent on *what* it actually looks like in implementation. Concrete design questions that need answering before any of this ships:
>
> - **Storage**: SQLite, JSONL append-only file, or systemd journal? SQLite gives you query power (which AI context selection benefits from); JSONL is simpler but harder to query at scale.
> - **Cardinality**: how many events per day? If it's >10k, JSONL becomes painful for ad-hoc queries.
> - **Retention**: keep events forever? Truncate? Roll into summaries?
> - **Schema**: which fields? `{timestamp, source, intent, target, parameters, result, focus_snapshot, thread_id, intent_id}` is a starting point.
> - **Indexes**: at minimum on timestamp, thread_id, intent type. Probably also on entities mentioned.
> - **Privacy**: voice utterances captured here are potentially sensitive. Worth thinking about now if open-source release is on the table.
>
> This is the single piece of work that unlocks the most subsequent value, and it can be done in a small, bounded scope (1-2 sessions) without committing to the broader architecture.

### 4.3 State snapshot contract

Every AI call receives a structured snapshot, not ad-hoc context. (See §3.5.) The minimum snapshot includes:
- current focus stack
- last N events (normalized)
- active schedules
- device state summary
- current session metadata
- invocation mode

Without this, AI becomes inconsistent across calls, follow-ups degrade quickly, and reproducibility goes out the window.

### 4.4 Idempotency

Every intent has a unique execution identity:

```json
{ "intent_id": "abc123", "action": "turn_on_light", "target": "living_room" }
```

The orchestrator must ensure:
- if already executed → ignore or reconcile
- if partially executed → resume safely or no-op

This becomes critical with:
- scheduler re-invocations
- AI "check again" loops
- multi-device triggers

> **🔶 Editor's note** — idempotency is currently NOT enforced anywhere in PiPhone. The same command via PTT and Telegram simultaneously would fire twice. We just spent a session fixing a related bug (dual-scheduler executing each job twice). Adding `intent_id` and a dedup layer is real work but small (one map keyed on intent_id with TTL); worth doing before the system gets more inputs.

### 4.5 Feedback loop closure

Every action produces:
- outcome + success/failure + reason
- not just "executed"

```json
{ "intent_id": "abc123", "status": "failed", "reason": "device offline" }
```

This feeds back into:
- AI reasoning
- NL confidence tuning
- follow-up suggestions

Without this, AI becomes blind to reality.

### 4.6 Versioning

You will keep changing:
- NL rules
- AI prompts
- routing logic

So you need **versioned behavior contracts**. Otherwise old logs become incompatible with new logic and debugging past behavior becomes impossible.

> **🔶 Editor's note** — this is harder than the brief makes it sound. Real questions:
> - When you change an AI prompt mid-flight (a thread is open with v1 prompt, you deploy v2), what happens to the thread? Kill it? Migrate it? Pin to v1?
> - Event log entries written under one schema version need to remain readable under future versions. That means schema evolution rules from day one.
> - Persisted plans (a `planner mode` AI output that turned into 5 scheduled steps) carry an implicit assumption about the executing system's behavior. If you change handler routing between when the plan was made and when step 3 executes, what happens?
>
> Versioning isn't a feature; it's a discipline. Start by stamping every event and every persisted artifact with a `schema_version` integer. The rest emerges from there.

---

## 5. When the deterministic system calls AI

### 5.1 Decision function

The orchestrator is always trying to answer:

> "Can I produce a high-confidence structured intent without external reasoning?"

If yes → execute. If no → invoke AI with a specific role.

The deterministic system does NOT ask "is this AI-worthy?" It asks "can I resolve this into a canonical intent without hallucinating?"

### 5.2 Trigger class A: Slot failure (structured intent mostly known)

NL produces a partial intent missing required parameters:

```
intent: set_light_color
target: living_room_light
color: ???   (missing)
```

Trigger: **missing required parameter with no deterministic resolver.**

AI role: **slot filler.**

### 5.3 Trigger class B: Semantic ambiguity (multiple valid interpretations)

Examples:
- "make it like yesterday"
- "do the usual thing"
- "turn it warmer"

Trigger: **multiple plausible mappings to known intents, or unclear transformation rule.**

AI role: **disambiguator using context.**

### 5.4 Trigger class C: World knowledge gap

Examples:
- "what is the color of the ocean"
- "when is next rocket launch"
- "what does 'focus mode like yesterday' refer to"

Trigger: **system cannot answer from event log, state, known mappings, or local rules.**

AI role: **external semantic + factual resolver.**

### 5.5 Trigger class D: Multi-step reasoning requirement

Examples:
- "get ready for rocket launch"
- "set everything up for bedtime"

Trigger: **requires sequencing + planning across modules.**

AI role: **planner.**

### 5.6 Routing in detail

Combining the routing chain (§2.6) with the AI triggers:

```
1. Thread check    →  is there an active thread for this input?
2. Focus check     →  what context does this input apply in?
3. NL resolution   →  try deterministic claim
4. Decide:
   a. Fully resolvable        →  execute
   b. Slot failure (class A)  →  AI in slot_fill mode
   c. Ambiguous (class B)     →  AI in disambiguator mode
   d. Knowledge gap (class C) →  AI in fallback mode
   e. Multi-step (class D)    →  AI in planner mode
   f. None of the above       →  error tone / clarification request
```

### 5.7 AI never overrides routing

Even when AI is engaged in a thread, **routing remains deterministic**. The thread *biases* routing toward AI interpretation within its scope — it does not put the whole system in "AI mode."

### 5.8 Threading and follow-ups

Once AI is engaged in a flow, a thread is created:

```json
thread_owner = "ai"
thread_id    = "t1"
```

Subsequent user input is checked against active threads. If a follow-up belongs to a thread's scope, it routes back to AI within that thread's context — not to fresh NL parsing.

This is what makes "more", "same", "that one", "do it again" feel natural without breaking AI continuity or hijacking the whole system.

### 5.9 Implicit intent resolution boundaries

Follow-ons resolve against **focus + recent event window only**. Never:
- full history
- global AI inference
- ambiguous long-range memory

Why this matters: without this rule, you get "creeped out" behavior — the system makes assumptions from too-distant context, gets things confidently wrong, and becomes brittle.

The rule keeps implicit intents feeling **precise** rather than **presumptuous**.

---

## 6. Media resolution (Plex & Spotify)

### 6.1 Why media is different

Media introduces a problem the rest of the system doesn't have:

> The target is not an entity, it's a **searchable semantic object in a catalog.**

Instead of "turn on light" / "set volume", you now have:

```
fuzzy description → retrieval → ranking → playback decision → device routing
```

This is exactly where NL alone breaks down and AI + deterministic orchestration becomes powerful.

**The deeper observation from the original brief, worth preserving verbatim:**

> Media is where your system stops being:
> > "smart home control system"
> and becomes:
> > "semantic retrieval + orchestration OS"

This is the moment the architecture earns its general-purpose framing. The same primitives (intent, focus, threading, event log, orchestrator) that handle "turn on the lights" now handle "play that movie with the guy from Star Trek but he's a professor in a wheelchair" — and they do it without becoming a different kind of system. That generalization is what makes this design worth the additional structure relative to a simpler command router.

### 6.2 Division of labor

#### Deterministic system owns:
- Plex / Spotify API calls
- search execution
- ranking rules
- playback devices (Sonos, TV, etc.)
- state (what's playing where)
- focus (which room gets media)

#### AI owns:
- semantic interpretation of fuzzy descriptions
- query expansion
- disambiguation suggestions
- ranking hints (optional)
- structured "search intent"

### 6.3 The X-Men example, end-to-end

User: *"play that movie with the guy from Star Trek but he's a professor in a wheelchair"*

**Step 1: NL fails structured match.** No known intent matches "Star Trek + professor + wheelchair". Trigger = semantic media retrieval needed.

**Step 2: AI invoked (media_resolution mode).** AI receives a very constrained task:
```json
{
  "mode": "media_resolution",
  "type": "movie",
  "query_raw": "that movie with the guy from Star Trek but he's a professor in a wheelchair",
  "context": { "service": "plex", "focus": "living_room_tv" }
}
```

**Step 3: AI outputs structured search intent (NOT a decision).**
```json
{
  "search_terms": [
    "x-men",
    "professor x wheelchair",
    "patrick stewart wheelchair character movies"
  ],
  "candidates": [
    { "title": "X-Men", "confidence": 0.92 }
  ]
}
```

Important: AI does NOT pick the movie as final truth — it proposes candidates.

**Step 4: Deterministic system executes retrieval.**
- queries Plex library
- optionally queries external metadata (TMDB, etc.)
- applies ranking rules:
  - title match
  - metadata similarity
  - popularity bias
  - user history (optional)

**Step 5: Candidate resolution.**
```json
{
  "selected": "X-Men (2000)",
  "alternatives": ["X2", "Logan"],
  "confidence": 0.88
}
```

**Step 6: Playback intent created (canonical).**
```json
{
  "intent": "media.play",
  "service": "plex",
  "item": "X-Men (2000)",
  "device": "living_room_tv"
}
```

**Step 7: Execution via deterministic layer.**
- Plex API: play item
- Focus updated: "movie_session"
- Event logged
- Thread created: "media_session_xmen"

**Step 8: AI optional follow-up.**
AI may then be re-invoked for:
- "skip recap"
- "who is that actor?"
- "what else is like this?"

But only if triggered.

### 6.4 Resolution hierarchy

The deterministic system applies a fixed hierarchy when matching AI's candidates against the actual library:

1. exact title + edition match
2. title match + best default edition
3. metadata similarity fallback
4. user preference memory (if exists)
5. last played version bias

This is what guarantees correct edition selection even when AI proposes only a title concept.

### 6.5 Why AI must NOT do final matching

This is the **subtle failure mode** Jason was already worried about:

> AI thinks in "concept space": "X-Men (2000)", "X-Men First Class", "Professor X appearances". Your library is in **Plex IDs, multiple editions, mismatched naming conventions, local metadata quirks.**

If AI does final selection:
- it will confidently pick the wrong cut
- or hallucinate availability
- or miss your exact version (theatrical vs extended vs director's cut)

So:

> **AI must NOT resolve final items.**
> AI produces concept-space candidates. The deterministic system resolves them against the library.

AI suggestions *can* be inputs to ranking ("user historically prefers theatrical cuts") — but they are inputs, not final decisions.

### 6.6 Threading for media

Media introduces persistent context naturally. After "play X-Men", a thread is created:

```
media_session_xmen
```

Subsequent inputs:
- "pause that"
- "who is that actor"
- "skip intro"
- "turn it up"

All resolve via the media thread, not new intent parsing. This is huge for UX.

### 6.7 Spotify vs Plex diverging strategies

| System | AI role |
|---|---|
| **Spotify** | light semantic assist |
| **Plex** | retrieval + disambiguation assist |

**Spotify:**
- fuzzy search is cheap and good already
- ranking is strong
- AI mainly helps with: artist ambiguity, vibe-based requests ("something chill like yesterday")

**Plex:**
- local library constraints
- metadata inconsistency
- needs stronger disambiguation
- AI is more valuable here

### 6.8 Media resolution pipeline (specialized)

A specialized version of the general resolution pipeline:

```
1. NL intent attempt
2. media thread detection
3. AI semantic expansion (if needed)
4. deterministic catalog search
5. ranking engine
6. playback selection
7. execution
8. thread persistence
```

> **🔶 Editor's note** — the brief mentions embeddings parenthetically ("optional later") but in implementation, **a local embedding index over the Plex catalog is the most powerful tool for the fuzzy-match problem**, and it's not particularly hard to build. A one-time pass to embed every title + actor list + plot summary, stored in a vector DB or even just a numpy array, gives sub-100ms semantic search without an LLM round-trip. The LLM is still useful for query expansion ("professor in wheelchair" → "Professor X / Patrick Stewart"), but the *matching* itself benefits enormously from embeddings. I'd promote this from "optional later" to "Phase 2 cornerstone."

---

## 7. Knowledge persistence

### 7.1 The three tiers

#### Tier A: Hard persistent memory (AI does NOT hold this)
Owned by deterministic system:
- Home Assistant state
- device states
- focus stack
- event log
- schedule queue
- user preferences (high-level)

This is the **truth layer.**

#### Tier B: Cached semantic indexes (system-owned, AI-queryable)
- list of movies in Plex
- Spotify playlists
- artists frequently played
- recently added media
- metadata index

**Crucial rule:** AI does NOT store this. The system maintains it. AI only gets:
- query results
- filtered subsets
- ranked candidates

#### Tier C: External live systems (queried on demand)
- Plex API search
- Spotify API search
- TMDB
- etc.

These are queried when needed or when cache misses.

### 7.2 What AI should know persistently

**Short answer: almost nothing.**

> The AI should not own knowledge. It should only request slices of knowledge from the system when needed.

So instead of:
- AI has library knowledge
- AI has Spotify catalog memory
- AI has Plex metadata memory

You want:
- AI has a **toolbox for querying** structured system knowledge

### 7.3 Cache strategy

**Cache (good):**
- metadata indexes (titles, IDs, actors, genres)
- embeddings (Phase 2 cornerstone, see §6 editor's note)
- playlists
- recently played items
- derived summaries ("user likes sci-fi", "frequently played artists")

**Don't cache (bad):**
- full media descriptions in AI prompts
- full catalogs in prompts
- entire Spotify library snapshots

**Why caching matters:**
- fast resolution
- deterministic ranking layer
- reduced API calls
- stable AI inputs

But: cache lives in the orchestrator, **not** in AI memory.

### 7.4 Semantic retrieval layer (new subsystem)

A deterministic subsystem sitting between AI, orchestrator, and Plex/Spotify APIs.

Responsibilities:
- indexing
- caching
- search
- ranking
- filtering
- summarization

APIs it exposes:
- `search_movies(query)`
- `get_recently_played()`
- `get_library_summary()`
- `get_artist_top_tracks(artist)`
- `get_similar_content(item_id)`
- `get_user_listening_profile()`

This is what makes media feel "magical" without bloating AI prompts.

### 7.5 AI as query planner

Final mental model:

> AI is NOT a database, memory system, or catalog holder.
> AI IS a **query planner + semantic interpreter.**

The orchestrator is the cache owner, index owner, truth owner, execution engine. The media system is a searchable knowledge space, not a prompt input.

**The inversion that makes this scale:**

> Don't build an AI that knows your media library.
> Build an AI that can ask the system intelligent questions about your media library.

---

## 8. Production hardening

These are the constraints and edge-case protections that prevent the system from degrading over time. None of them are new architecture — they are **enforcement rules** on top of what's already designed.

### 8.1 Confidence and arbitration

A unified decision engine that arbitrates who gets control of interpretation. Outputs:
- claim strength (NL vs AI vs ambiguous)
- confidence score
- required next step

Example:
```json
{
  "claim": "nl_partial",
  "confidence": 0.74,
  "missing_slots": ["color"],
  "next_step": "ai_helper"
}
```

Prevents NL-vs-AI branching chaos, gives one consistent decision surface, makes debugging much easier.

> **🔶 Editor's note** — confidence scores are tricky. An uncalibrated confidence (regex-length-based, or "longest match wins") is worse than no confidence — it gives false signals that propagate. If you adopt this, *measure* whether your confidence correlates with actual correctness. Track "AI helper had to fix the slot" or "user re-issued the same intent" as a signal that NL was overconfident. Calibration matters more than the scoring formula.

### 8.2 Concurrency control

Real life is not linear — PTT, Telegram, wake word, scheduled events, AI rechecks can all hit at once.

You need:
- a single serialized event queue, OR
- strict locking per focus/context

Otherwise you get conflicting focus changes, double execution, inconsistent state snapshots. (We just fixed an instance of this with the dual-scheduler bug.)

### 8.3 Resolution pipeline stages (explicit)

```
1. normalize input
2. detect intent candidates
3. resolve entities
4. apply focus context
5. compute confidence
6. decide route (execute / AI helper / AI fallback)
7. validate final intent schema
8. execute
9. emit event
10. update state + focus
```

This matters because:
- debugging becomes possible
- AI doesn't leak into the wrong stage
- components can be swapped independently

### 8.4 Capability registry

A single source of truth for what the system can actually do:

```json
{
  "lights.set_color":   true,
  "media.play":         true,
  "home.lock_doors":    false
}
```

Why this matters:
- AI won't hallucinate capabilities
- NL won't route invalid actions
- future devices integrate cleanly

Prevents "fake smart assistant" behavior.

### 8.5 Time model consistency

As scheduling grows, you need:
- canonical time source
- timezone handling rules
- delayed execution guarantees
- cancellation semantics

Otherwise you get "ghost alarms" and drift between AI expectation and execution reality.

### 8.6 Human override priority

Any real-time user input overrides AI + scheduled actions.

If AI scheduled something and the user says "stop that", the system must:
- cancel pending intents
- update focus immediately
- log override event

This is what keeps the system feeling like a living thing instead of an agent that drifts away.

### 8.7 Multi-surface input convergence

All inputs must converge **before any interpretation** — input normalization layer is mandatory.

Otherwise: duplicated intents, conflicting focus updates, inconsistent logs.

### 8.8 Tool boundary clarity

A strict rule: **AI only sees tools through the orchestrator API, never direct module access.**

Even if AI could call modules, don't let it. Why:
- prevents abstraction leakage
- keeps focus logic consistent
- avoids bypassing logging/state

### 8.9 One truth per layer

> State truth lives ONLY in the deterministic orchestrator.
> AI never stores state.
> NL never stores state.
> Scheduler never stores derived intent state.

Everything else is a *projection*. This prevents long-term divergence bugs.

### 8.10 Action replay / simulation mode

Because you have event logs + structured intents, you can:
- replay a session
- simulate what would happen
- debug AI decisions
- test new routing logic

This is a huge advantage over Alexa-style systems.

### 8.11 Explainability layer

With event log + structured intents + AI decisions, you can eventually support:

> "Why did the system do that?"

Massive trust + debugging tool — especially when AI is in the loop.

### 8.12 Deterministic fallback as UX feature

Even when AI exists, deterministic execution stays valuable for:
- speed-critical actions ("volume up")
- offline scenarios
- predictable behaviors
- muscle-memory interactions

**AI should never become the default path for common actions.** Keeps the system feeling instant and physical.

---

## 9. Implementation reality — critical read

> **🔶 This entire section is editorial commentary on the brief above.** It's Claude's honest read on what's strong, what's missing, and what the gap is between "conceptually complete design" and "shippable code." Preserved here because the design above is at risk of feeling complete enough to discourage scrutiny.

### 9.1 What's conceptually strong

The non-negotiables (§1.4) are right and consistent with how PiPhone has already been built. Most importantly:

1. **"AI never directly mutates state"** — keeps the deterministic system as the trusted execution layer.
2. **"AI is episodic, not continuous"** — prevents agentic drift; keeps everything reproducible.
3. **Thread-based continuity (IBC) instead of a global "AI mode" flag** — scopes continuity without hijacking the whole system.
4. **Media split: AI does concept resolution, deterministic system does library resolution** — exactly right for the extended-cut problem.
5. **Focus stack instead of single-value focus** — genuine UX upgrade.
6. **Append-only event log as causal truth, not just memory** — the most underrated idea in the whole brief.

### 9.2 What's missing or underspecified

#### No concrete first-value path
Phase 1 of the brief is "stabilize core, implement event log, formalize focus state, unify execution paths" — a major refactor before any AI feature ships. A user could get real value from a much smaller scope first. The brief reads like architecture astronautics in places — thorough conceptual completeness, but no answer to *"what's the smallest thing I'd build that demonstrably improves my daily use?"*

#### No cost / latency model
This matters a lot for a daily-driver home system. Specifically: per-utterance AI calls × roundtrip latency × monthly cost.

If "set the light to ocean color" adds 800ms and $0.001 — fine. If it adds 3s and $0.05 — non-starter for routine use. The brief never quantifies this. Concrete questions that need answers before serious work begins:

- Which model? (gpt-4o, gpt-4o-mini, Claude Haiku, local Llama?)
- Average tokens in / out per slot_fill call?
- Average tokens in / out per planner call?
- Round-trip latency budget for slot fill (target < 500ms?)
- Round-trip latency budget for planner (target < 2s?)
- Monthly cost ceiling for normal household usage?

These shape design decisions like context budget, caching strategy, and even which use cases to bother adding AI to.

#### Event log is described abstractly but never made concrete
See §4.2 editor's note. Storage, schema, retention, indexes, privacy — all need real design before this ships.

#### Confidence scoring is hand-waved
See §8.1 editor's note. Calibration matters more than the formula.

#### Embeddings underweighted
See §6.8 editor's note. A local embedding index is the most powerful tool for the fuzzy-match problem and is not particularly hard to build.

#### Versioned behavior contracts gets one line for a deeply hard problem
See §4.6 editor's note. Real schema evolution discipline is harder than the brief implies.

#### "Home OS / kernel" framing is aspirational and can mislead
See §1.2 editor's note. The framing is useful as a constraint check, not as an architectural target.

### 9.3 Costs and latency — a concrete first model

Until measured, here are reasonable working assumptions for sequencing decisions:

- **slot_fill** (color resolver, scene name fuzzy match): ~200 tokens in, ~50 out. ~300-700ms with gpt-4o-mini. ~$0.0001 per call.
- **fallback** (full intent interpretation): ~500-1500 tokens in, ~100-300 out. ~600-1500ms. ~$0.0005 per call.
- **planner** (multi-step): ~2000-5000 tokens in, ~500-1500 out. ~1500-4000ms. ~$0.002-$0.01 per call.
- **media resolution** (semantic expansion): ~300-600 tokens in, ~100-200 out. ~400-800ms. ~$0.0002 per call.

For 100 utterances/day with 20% touching AI: ~$0.05-0.50/month. Trivial cost. Latency is the binding constraint, not money.

> **🔶 Editor's note** — measure these for real before locking in design. Latency in particular varies wildly with network conditions and prompt size; treat the above as a starting point for "is this even in the realm of feasible," not a budget to design against.

### 9.4 Avoiding the over-engineering trap

The biggest risk in this whole design is **building the architecture before building the value.** Some of the brief's "concepts to introduce" are real work and only earn their keep at scale:

- Capability registry — necessary when you have many AI-generated intents to validate, redundant at small scale
- Full IBC threading state machine — shines when AI is in multi-turn flows; less valuable until then
- Versioning discipline — hard problem, can defer until you've actually hit a version boundary
- Planner mode — most exotic, most cost-uncertain
- Explainability layer — great trust feature, low priority

The right move is to implement the **minimum** infrastructure needed for the **first** valuable use case, then let real experience guide what gets built next. This is how good systems get built; design-everything-first tends to produce systems that don't ship.

---

## 10. Concrete path forward

> **🔶 This section is editorial guidance — a pragmatic alternative to the brief's phased plan (which is conceptually right but starts too big). The original 7-phase plan is preserved verbatim in §10.1 below, with the alternative starting at §10.2.**

### 10.1 The brief's original 7-phase evolution plan (preserved)

This is the phased plan ChatGPT proposed in the original brief, preserved here in full. It is conceptually correct end-to-end, but each phase contains substantial work and the plan as a whole defers user-visible AI value until well into phase 2 or 3. See §10.2+ for a pragmatic alternative that achieves the same destination via smaller increments with value delivery at every step.

#### Phase 1 — Stabilize core (current state)
- clean NL routing
- implement event log
- formalize focus state
- unify execution paths

#### Phase 2 — Introduce AI helper layer
- gap filling (colors, entities, ambiguity)
- structured outputs only
- no planning yet

#### Phase 3 — Add fallback AI routing
- unknown intent handling
- conversational queries
- consistent schema enforcement

#### Phase 4 — Add scheduler integration
- AI can request future re-invocation
- orchestrator owns timing
- event-driven re-entry

#### Phase 5 — Add planning mode
- multi-step workflows
- structured plans
- deterministic validation of steps

#### Phase 6 — Rich focus model
- temporal focus
- task focus
- ambient context states

#### Phase 7 — Cross-surface coherence
- identical behavior across voice, Telegram, Raycast, PTT, UI (menubar)

> **🔶 Editor's note** — the destinations here are right. The phasing is the issue: Phase 1 alone is a substantial refactor (event log + focus formalization + execution path unification) before any AI feature ships. Phase 2 — the first user-visible AI — only arrives after Phase 1 lands. A user could feel value much sooner by interleaving: ship the smallest valuable AI feature (media semantic resolution) with the smallest event log (JSONL append-only) and skip the formalization work until it's genuinely needed. See §10.2 for that alternative.

### 10.2 Smallest valuable steps

If I had to turn the 5000-word brief above into a working first step that demonstrably improves daily use, I'd identify these three as highest-leverage:

#### 1. Event log
Start writing structured events to a SQLite file or JSONL on every executed intent. Single hook point (the end of `process_device_commands`). Bounded scope. Unblocks debugging AND future AI context generation AND the eventual "why did the system do that" UX. ~1-2 sessions.

#### 2. AI semantic resolution for **media** (one domain)
Specifically the "X-Men case": fuzzy media descriptions → Plex search. Most user-visible AI win because NL alone obviously fails here. Doesn't require focus stack, threading, capability registry, or any other architectural piece. ~2-3 sessions.

#### 3. AI slot-filling for **one** parameter type
Pick one: semantic colors ("ocean", "sunset", "moody"), or scene names ("evening", "movie time", "wind down"). Plug into the existing color or scene handler. Constrained JSON schema. Use OpenAI (already in the stack for STT). ~1-2 sessions.

That's the MVP. Everything else (focus stack, threads, capability registry, planner mode, recheck mode) is V2+.

### 10.3 Effort and value table

| Piece | Effort | Value timeline | Priority |
|---|---|---|---|
| Event log | ~1-2 sessions | Immediate: debugging + foundation for everything else | **High — do first** |
| AI media resolution (Plex/Spotify) | ~2-3 sessions | Immediate + user-visible | **High — first AI feature** |
| AI slot-fill (one domain) | ~1-2 sessions | Narrow but demonstrable | High |
| Focus stack | ~2-3 sessions | Enables better follow-ups; no feature on its own | Medium — when needed |
| Threading (IBC) | ~3-4 sessions | Shines with AI multi-turn flows | Medium — after first AI features ship |
| Capability registry | ~3-5 sessions | Needed at scale | Low — defer until needed |
| Intent schema formalization | ~3-5 sessions | Enables planner mode | Medium — when planner is needed |
| Planner mode | ~5+ sessions | The agentic stuff | Low — defer |
| Recheck mode | ~3-5 sessions | Temporal continuity | Low — defer |
| Confidence calibration | ~2-3 sessions ongoing | Iterative improvement | Ongoing |
| Versioning discipline | Forever | Foundational hygiene | Start with `schema_version` field; defer real work |
| Embedding index for media | ~2-3 sessions | Big jump in media UX | **Phase 2 priority** |

**Total if you did all of it: ~25-40 focused sessions. Not all-or-nothing.**

### 10.4 Sequencing vs the Mac menubar

**Honest take: menubar first, then a focused AI slice — not the full architecture refactor.**

Reasons:
1. The menubar work is concrete and bounded — there's a clear endpoint where it's "done and useful daily."
2. The menubar produces a UI surface that AI integration would *benefit from* — a "make it cozy" button, a chat input that uses the planner, status displays informed by the event log. Building UI first gives the AI work a natural client.
3. The AI architecture is large enough that doing it before the menubar would delay the menubar by months.
4. The most valuable AI piece (media semantic resolution) is independent — doesn't require the focus stack, IBC, or capability registry. You can do it as a small standalone after menubar Phase 1 ships.

### 10.5 Recommended sequencing

1. **Now → next ~2 weeks:** Mac menubar Phase 1. (Handoff doc ready; do it on the Mac.)
2. **Parallel side-work, small chunks:** event log. Lowest-effort, highest-leverage foundation piece. Doesn't require committing to the full architecture.
3. **After menubar Phase 1:** AI semantic resolution for media (Plex). Limited scope, high visibility. The X-Men case becomes a real demo.
4. **Then evaluate:** does this feel right? Is the cost/latency working? Do I want more of this, or is the current shape enough? Decide based on actual experience.
5. **If yes:** focus stack → AI threading (IBC) → AI slot-fill for colors → planner mode, in that order, as use cases demand each one.
6. **Defer indefinitely:** capability registry, behavior versioning, recheck mode, "explainability layer." These are real but only earn their keep when the system gets substantially bigger.

This way: ship something useful every step. Never commit to a refactor without proven value. The architecture you end up with is shaped by real use rather than upfront design.

---

## 11. Renaming: PiPhone → HomeSuite

The brief implicitly committed to the rename; the explicit reasoning:

**PiPhone is:**
- distinctive
- poetic
- tied to one form factor (the physical handset)
- not descriptive of what the system has become

**HomeSuite is:**
- generic enough to scale
- specific enough to convey what it is (a suite of home tools)
- product-ready

**Recommendation:** keep internal name as PiPhone for now (no value in renaming before public release). Rename at the moment of public release. The rename is mostly:
- repo rename
- python package rename
- service unit renames
- documentation updates
- some user-facing strings

Not a major engineering effort. Maybe a half-day total when it's time.

---

## 12. Key insights to hold onto

If the rest of this document gets lost or rotted out of memory, these are the points that matter most:

### 12.1 The four immutable principles
1. Deterministic system owns truth.
2. AI never directly mutates state.
3. Everything becomes an event.
4. AI is episodic, not continuous.

### 12.2 Single AI, role-framed contracts
Not multiple AIs. Not "the AI." One reasoning engine, multiple strict invocation contracts (slot_fill, fallback, planner, recheck) each with their own role description and output schema.

### 12.3 Intent is the only execution contract
Everything converges into a canonical intent object before execution. NL output → intent. AI output → intent. Follow-ons → intent. This is what makes the system composable.

### 12.4 Event log is causality, not just history
The event log is the causal graph of the system. Treat it as instrumentation first, memory second, intelligence substrate third. **This is the single piece of work that unlocks the most subsequent value.**

### 12.5 AI is a semantic compiler, not an actor
NL + context → AI → structured intent → orchestrator → execution. AI is not controller, agent, memory, or executor. It is the semantic bridge that translates ambiguity into structured machine-readable intent.

### 12.6 Threads, not "AI mode"
Continuity scopes to threads. AI participates in threads. Routing remains deterministic. Never put the whole system in "AI mode."

### 12.7 Media: AI does concepts, deterministic does library
AI generates concept-space candidates. The deterministic system resolves them against the real library. AI suggestions are inputs to ranking, never final decisions.

### 12.8 Focus is the compression layer
Focus is not just "which room is active." It's the semantic compression that prevents AI from needing full-world reasoning every call.

### 12.9 AI does not own knowledge
The system maintains caches and indexes. AI requests slices, never holds them. The right inversion: build an AI that can ask intelligent questions about your library, not an AI that *knows* your library.

### 12.10 System coherence > intelligence
Priority order: correctness → continuity → responsiveness → intelligence → autonomy. Most AI systems invert this and break.

### 12.11 The biggest lurking failure mode
Multiple layers maintaining their own version of truth. Enforce: **state lives only in the deterministic orchestrator.** Everything else is a projection.

### 12.12 The biggest implementation pitfall
Building the architecture before building the value. Resist the urge to do "Phase 1: stabilize core" as a giant refactor before anything user-facing ships. Start with one valuable AI use case and grow the architecture under it.

### 12.13 Old mental model vs real model

A useful mental shift, preserved from the brief, that captures how the design's framing has matured:

**Old mental model (naive):**
- NL layer
- AI layer
- device layer

**Real model (this design):**
- event-driven OS
- deterministic kernel (orchestrator)
- AI as episodic reasoning process
- unified intent language
- shared state + focus stack
- scheduler as time subsystem

The naive model treats NL, AI, and devices as parallel concerns that need glue. The real model treats them as layers of one system, with the orchestrator as kernel, intents as the only syscall format, and AI as a userspace reasoning process. That reframing is what makes everything else in this document cohere.

### 12.14 What makes this different from Alexa / Siri (the real value)

> What makes your system different from Alexa/Siri isn't AI.

The actual differentiators:
- **focus stack** — persistent contextual scope across utterances
- **event log causality** — provable answers to "why did the system do that?"
- **deterministic orchestration** — predictable, auditable, instant for common actions
- **cross-surface continuity** — same intent, same routing, same behavior whether spoken / typed / pressed
- **structured intent model** — composable across NL, AI, scheduler, and clients

AI is just the **semantic bridge** that lets the user phrase things in natural language. The substance of the system is everything else. This matters for product framing (when HomeSuite goes public, the pitch is NOT "another AI assistant" — it's "the home OS layer that happens to use AI when it helps").

### 12.15 Three synthesis statements, preserved verbatim

The brief offered three different one-paragraph syntheses of the system at different points in the conversation. Preserving all three because each emphasizes a different facet:

**Synthesis 1 (from §10 of original brief):**

> A unified event-driven home runtime where deterministic orchestration owns state and execution, and AI operates as an episodic reasoning engine that converts ambiguous human intent into structured, validated actions and multi-step plans.

**Synthesis 2 (from end of "anything else?" round):**

> A deterministic event-driven home operating system with AI as a stateless semantic co-processor and scheduler-driven re-entry model for temporal continuity.

**Synthesis 3 (from end of "AI roles per invocation" round):**

> A deterministic event-driven home OS where all inputs are normalized into structured intents, AI is used as a role-based semantic compiler for ambiguity resolution and planning, and all execution, scheduling, and state transitions are handled by a central orchestrator that maintains focus, causality, and continuity across all interfaces.

These say the same thing from three angles. Statement 1 emphasizes the deterministic/AI division. Statement 2 emphasizes statelessness and time-driven re-entry. Statement 3 emphasizes the intent-normalization invariant. All three are correct; pick whichever lands best when you're explaining the system to someone new.

---

## Appendix A: Glossary

- **Capability registry** — declarative manifest of what the system can do; prevents AI hallucinating actions
- **Canonical intent** — the single structured object format every action passes through before execution
- **Confidence score** — a numeric estimate of how sure NL or AI is about an interpretation; needs calibration to be useful
- **Deterministic orchestrator** — the central "kernel" that owns state, focus, execution, and routing
- **Event log** — append-only structured record of every intent, state change, AI output, and focus transition
- **Focus** — current physical/contextual scope (room, device, activity); maintained as a stack
- **HomeSuite** — proposed public name for the broader system (current internal name: PiPhone)
- **Idempotency** — guarantee that the same intent executed twice produces the result of executing once
- **Intent** — structured representation of an action, with intent name, target, parameters, confidence
- **Interaction Binding Context (IBC)** — session overlay tracking the active semantic thread of reasoning
- **Knowledge tiers** — Tier A: persistent system state (truth); Tier B: cached semantic indexes; Tier C: external live systems
- **Planner mode** — AI invocation mode that produces multi-step ordered intent sequences
- **Recheck mode** — AI invocation triggered by scheduled re-entry, with updated state
- **Resolution pipeline** — explicit ordered stages from raw input to executed action
- **Semantic compiler** — the framing for what AI is: translates fuzzy human language into structured machine intent
- **Semantic retrieval layer** — deterministic subsystem that indexes/caches/searches/ranks; the API surface AI uses to ask about media libraries
- **Slot fill mode** — AI invocation mode that fills missing parameters in an otherwise valid structured intent
- **State snapshot contract** — required structured shape of context passed to every AI invocation
- **Thread** — semantic continuity scope (vs focus which is contextual scope); created when AI enters a multi-turn flow
- **Versioning** — discipline of stamping behavior, prompts, and persisted artifacts with schema versions to enable evolution

---

## Appendix B: Open questions for future sessions

These are real design questions that need answers before significant work on this architecture begins. Capturing them here so they don't get lost:

### B.1 Event log storage
- SQLite vs JSONL vs systemd journal?
- Retention policy?
- Privacy considerations (voice utterances captured)?
- Indexes (timestamp, thread_id, entities, intent type)?

### B.2 AI model selection and cost
- Which model for slot_fill? (gpt-4o-mini? Haiku? local?)
- Which model for planner? (Could differ from slot_fill)
- Budget per call, per day, per month?
- Local-model fallback strategy?

### B.3 Embedding strategy
- Where do embeddings get computed (local? cloud?)
- Where stored (numpy file? SQLite-vec? FAISS? Chroma?)
- Refresh strategy when Plex library changes?
- Same approach for Spotify (or rely on Spotify's own embeddings via API)?

### B.4 Cross-Pi state
- Event log lives where if both Pi 3B and Pi 4 are in use?
- Single source on one Pi with other Pis subscribing via HTTP?
- Or replicated?

### B.5 Threading semantics
- Thread expiration: time-based, focus-change-based, or both?
- Can threads be nested?
- Can a thread span devices/surfaces?
- What happens to an open thread when the user goes silent for an hour?
- **Cross-surface threading specifically**: if a thread starts via Telegram ("play that movie with the professor in a wheelchair"), can it continue via voice ("pause that") on the Pi? What's the identity model — is it per-user, per-device, or global? Cross-surface continuity is one of the strongest UX promises in §12.14, but the threading mechanics that make it work need explicit design.

### B.6 Focus stack semantics
- How deep can the stack go?
- Auto-pop rules vs explicit pop ("go back")?
- What counts as pushing a new frame?
- How does focus interact with multiple simultaneous users (future)?

### B.7 Confidence calibration
- What signals get used for the score?
- How is it measured / improved?
- What threshold triggers AI fallback?

### B.8 Cost/latency budgets
- Hard cap on AI round-trip latency before falling back to error or asking for clarification?
- Caching strategy for repeated identical queries?
- Batching when multiple actions are queued?

### B.9 Schema versioning
- Initial schema for events, intents, threads?
- Migration story when schemas evolve?
- How are persisted plans (planner output) versioned and aged?

### B.10 The "make it cozy" problem
- What's the right user feedback when AI doesn't know what you want?
- Clarification request? Confidence threshold? Default to fallback?
- How does this differ across surfaces (voice can't easily ask a clarifying question without being annoying; chat can)?

### B.11 Privacy and data sovereignty
- Voice utterances captured in the event log are potentially sensitive — what's the retention policy?
- Local LLM vs cloud LLM: what data leaves the house?
  - Cloud (OpenAI/Anthropic) is the easy path; data leaves on every AI call.
  - Local (Llama, Mistral, etc.) keeps data home but adds GPU/compute requirements and latency variance.
- If the system goes open-source, this becomes a user-configurable decision rather than a deployment choice — design the AI client surface so swapping providers is a small change, not a refactor.
- Event log encryption at rest? Probably not for v1 (it's on-device, behind your home network) but worth thinking about for any future cloud-sync or backup story.
- What happens to event log data when the user wants to delete it? "Forget what I just said" as a first-class command?

---

*Document created 2026-05-22. Living document — update in place as design evolves.*

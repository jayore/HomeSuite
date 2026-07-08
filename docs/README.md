# PiPhone documentation

All non-entrypoint docs live here. Top-level repo has just two:

- `../README.md` — project overview, what PiPhone is
- `../ROADMAP.md` — future directions

Everything else moved into this folder during the 2026-05-21 cleanup. If
you're an AI client picking up a new session, the docs you most likely
want are:

## Read these first (workflow & maintenance rules)

| File | What's in it |
|---|---|
| [`CLAUDE_CODE_WORKFLOW.md`](CLAUDE_CODE_WORKFLOW.md) | Workflow conventions specific to Claude Code (takes precedence over AI_WORKFLOW_PREFERENCES). Backup before edits, one change at a time, venv Python, etc. |
| [`AI_WORKFLOW_PREFERENCES.md`](AI_WORKFLOW_PREFERENCES.md) | Older / broader AI workflow guidance. CLAUDE_CODE_WORKFLOW.md wins where they overlap. |
| [`AI_THREAD_GUIDE.md`](AI_THREAD_GUIDE.md) | Guidance for picking up a thread cleanly. |
| [`DEV_AND_TESTING.md`](DEV_AND_TESTING.md) | The detailed runtime, testing, and maintenance guide. Architecture, file roles, gotchas. |
| [`FUTURE_ARCHITECTURE.md`](FUTURE_ARCHITECTURE.md) | Forward-looking architectural ideas. Less concrete than ROADMAP. |

## Running log

| File | What's in it |
|---|---|
| [`AI_HANDOFF_LOG.md`](AI_HANDOFF_LOG.md) | Reverse-chronological session log. **Append a new entry here at end of each significant session.** |

## Per-session handoff snapshots

Detailed handoff documents from specific work sessions live in
[`handoffs/`](handoffs/). These are point-in-time snapshots, not living
docs — they capture state and decisions at a moment, and they age out.
The most recent ones are usually still relevant; older ones are
historical context.

Most recent first:

- [`handoffs/NEXT_THREAD_HANDOFF_2026-05-28_COMMAND_DISPATCH_EXTRACTION.md`](handoffs/NEXT_THREAD_HANDOFF_2026-05-28_COMMAND_DISPATCH_EXTRACTION.md)
- [`handoffs/NEXT_THREAD_HANDOFF_2026-05-26_MENUBAR_PHASE2_PIPHONE_WS.md`](handoffs/NEXT_THREAD_HANDOFF_2026-05-26_MENUBAR_PHASE2_PIPHONE_WS.md)
- [`handoffs/NEXT_THREAD_HANDOFF_2026-05-22_MAC_MENUBAR_APP_KICKOFF.md`](handoffs/NEXT_THREAD_HANDOFF_2026-05-22_MAC_MENUBAR_APP_KICKOFF.md)
- [`handoffs/NEXT_THREAD_HANDOFF_2026-05-15_FARFIELD_MIC_PREP.md`](handoffs/NEXT_THREAD_HANDOFF_2026-05-15_FARFIELD_MIC_PREP.md)
- [`handoffs/NEXT_THREAD_HANDOFF_2026-05-15_PI3B_UNIFY_MIGRATION.md`](handoffs/NEXT_THREAD_HANDOFF_2026-05-15_PI3B_UNIFY_MIGRATION.md)
- [`handoffs/HANDOFF_WAKEWORD_UX_20260514.md`](handoffs/HANDOFF_WAKEWORD_UX_20260514.md)
- [`handoffs/NEXT_THREAD_HANDOFF_2026-05-13_OPENWAKEWORD_RUNTIME_AND_RETRIGGER.md`](handoffs/NEXT_THREAD_HANDOFF_2026-05-13_OPENWAKEWORD_RUNTIME_AND_RETRIGGER.md)
- [`handoffs/HANDOFF_2026-05-13_ENV_PYTHON_BREAKAGE.md`](handoffs/HANDOFF_2026-05-13_ENV_PYTHON_BREAKAGE.md)
- [`handoffs/NEXT_THREAD_HANDOFF_2026-05-12_ROOM_AWARE_MEDIA_HARDENING.md`](handoffs/NEXT_THREAD_HANDOFF_2026-05-12_ROOM_AWARE_MEDIA_HARDENING.md)
- [`handoffs/NEXT_THREAD_HANDOFF_2026-05-12_ROOM_AWARE_MEDIA_AND_SATELLITE_MVP.md`](handoffs/NEXT_THREAD_HANDOFF_2026-05-12_ROOM_AWARE_MEDIA_AND_SATELLITE_MVP.md)
- [`handoffs/NEXT_THREAD_HANDOFF_2026-05-11_ALARMS_SCOPE.md`](handoffs/NEXT_THREAD_HANDOFF_2026-05-11_ALARMS_SCOPE.md)
- [`handoffs/NEXT_THREAD_HANDOFF_2026-05-11.md`](handoffs/NEXT_THREAD_HANDOFF_2026-05-11.md)
- [`handoffs/NEXT_THREAD_HANDOFF_2026-05-09.md`](handoffs/NEXT_THREAD_HANDOFF_2026-05-09.md)

When starting a new handoff at the end of a session, write it into
`handoffs/` with the filename pattern `NEXT_THREAD_HANDOFF_YYYY-MM-DD_<topic>.md`
and append a short summary entry into `AI_HANDOFF_LOG.md`.

# Commands To Try

Home Suite commands are natural-language examples, not a strict grammar. Exact results depend on your Home Assistant rooms, entity names, scenes, scripts, media libraries, and configured services.

Use the safe REPL while you are setting things up:

```bash
homesuite repl
```

Then type a phrase at the prompt. For a single reproducible check, run:

```bash
homesuite test "what lights are on?"
```

Safe command modes read real Home Assistant state but block writes. Use
`homesuite repl --live`, `homesuite test --live "phrase"`, `ppchat`, voice,
Telegram, HTTP, or other clients only when you are ready for commands to affect
real devices. `homesuite test` parses alarm and timer requests but does not
persist them or create scheduler jobs.

## First Checks

These are good early tests after `homesuite doctor` passes its core checks:

* `what lights are on?`
* `turn on the kitchen lights`
* `service status`
* `where am I?`
* `say this is a speech test`

## Conversational Command Forms

Common deterministic commands accept bounded request wrappers and safe
paraphrases, so ordinary phrasing does not have to match one exact sentence:

* `could you turn the stair light off for me?`
* `would you mind turning on the kitchen lights?`
* `make the stair light half brightness`
* `set the kitchen to half volume`
* `give me five minutes`

Home Suite removes only harmless conversational framing. It preserves payloads
for commands such as `play`, `say`, and `announce`, and it does not discard
timing clauses or target words merely because they sound conversational.

After a successful deterministic command, compatible short follow-ups can
reuse its typed intent. For example, `make the stair light red` can be followed
by `actually make it blue` and then `and the desk lamp too`. Target-only
follow-ups also accept natural forms such as `side lamp too`, `side lamp also`,
and `now the side lamp`. A typed value can change in the same cadence, such as
`now white` after a color command or `now half` after a brightness command. Weather,
astronomy, and calendar queries support similarly bounded refinements such as
`what about Thursday?`, `what about Saturn?`, and `I meant Friday` when the
immediately preceding intent makes the domain unambiguous. A complete new
command always supersedes the old intent. Directional adjustments can be
continued naturally: `volume down` followed by `more`, or `make the stair light
dimmer` followed by `a little more`. These short forms expire with the prior
intent and never apply to an unrelated absolute setting or device action.

When a short device name matches two to four live entities, Home Suite asks
which one and accepts a distinguishing name such as `floor` or `desk`. The
selected option is replayed through ordinary entity resolution and live-state
checks. Configured device aliases and room targets remain authoritative; with
many matches, Home Suite asks for a more specific full name instead of guessing.

## Home Assistant Control

These routes go through Home Assistant entities, areas, scenes, scripts, and services. Good Home Assistant naming makes these much easier to use.

* `turn on the kitchen lights`
* `turn off the downstairs lights`
* `turn off the stair light and side lamp`
* `turn off all the lights`
* `dim the living room to 40 percent`
* `make the lamp blue`
* `set the stair light and side lamp to red`
* `set the stair light and side lamp to 30 percent`
* `make the stair light and side lamp dimmer`
* `set the bedroom lights to warm white`
* `turn off the living room lights in 20 minutes`
* `lock the front door`
* `is the stair light on?` followed by `turn it off`
* `is the front door locked?` followed by `unlock it`
* `is the garage door open?`
* `are any windows open?`
* `what lights are on?`
* `what is the bedroom humidity?`
* `what's the front door battery?`
* `run movie night`

State questions use current Home Assistant state. A successful named-device
question establishes short-lived device focus, so a compatible follow-up such
as `turn it off`, `toggle it`, or `unlock it` can use the same verified entity.
Home Suite rechecks that entity against the latest state snapshot before an
action. Aggregate questions do not create a singular target. Room-scoped
questions depend on the room's `ha_area_id`; door, window, humidity,
temperature, and battery answers depend on the corresponding Home Assistant
`device_class`. Aggregate light summaries and whole-home light actions omit
entities configured through `ASSISTANT_BULK_EXCLUDED_ENTITY_IDS` and
`ASSISTANT_BULK_EXCLUDED_ENTITY_PATTERNS`.

Advanced light forms:

* `set the lamp to 3000K`
* `set the lamp to hex FF00AA`
* `set the lamp to RGB 255 0 170`

Temporary changes currently require one resolved `light.*` entity:

* `set the stair light to red for 10 minutes`
* `dim the desk lamp to 30 percent for 1 hour`
* `turn off the porch light for 20 minutes`
* `what temporary changes are active?`
* `how long until the stair light restores?`
* `is the stair light temporary?`
* `restore the stair light now`
* `keep the stair light as it is`

Home Suite snapshots the light before changing it. When the duration expires,
it restores that snapshot only if the light still matches the temporary state.
A later manual or voice change therefore wins. Whole-room and multi-light
temporary changes fail explicitly instead of guessing an inverse action.
`restore ... now` is an explicit override: it reapplies the saved baseline even
if the light changed again. `keep ... as it is` removes the pending restoration
without changing the current light. The default maximum is 24 hours, and
changes longer than six hours require a source-scoped confirmation.

## Covers, Fans, Climate, And Vacuums

These commands require a matching Home Assistant entity in the expected domain. Home Suite validates the resolved domain before calling a capability-specific service, and supported modes still depend on what the individual device reports.

* `open the office blinds`
* `close the garage door`
* `set the office blinds to 50 percent`
* `how open are the office blinds?`
* `set the bedroom fan speed to 45 percent`
* `set the bedroom fan to high`
* `set the bedroom fan to sleep`
* `increase the bedroom fan speed`
* `what is the bedroom fan speed?`
* `set the hallway thermostat to 72 degrees`
* `set the hallway thermostat mode to cool`
* `what is the hallway thermostat set to?`
* `start the vacuum`
* `pause the vacuum`
* `send the vacuum home`
* `what is the vacuum doing?`

## Rooms, Focus, And Defaults

Home Suite can use a fixed room for a device or a sticky room focus for mobile/text clients. That lets shorter commands route to the right room without repeating the room every time.

* `I'm in the bedroom`
* `where am I?`
* `clear my room focus`
* `turn on the lights`
* `dim it to 30 percent`
* `play music here`
* `announce laundry is done in the kitchen`

## Media And Transport Control

These commands use the currently focused room, source, or media player when possible.

* `what's playing?`
* `pause`
* `resume`
* `next track`
* `previous track`
* `volume up`
* `volume down`
* `set the living room volume to 25`
* `mute the TV`
* `play music in the kitchen`
* `switch the living room Sonos to TV audio`
* `group kitchen with living room`
* `ungroup`

## Plex

Plex commands depend on Plex configuration and your actual Plex library. Playback is resolved through deterministic media handlers rather than AI directly controlling Plex.

* `watch The Matrix`
* `watch the movie where people live in a simulation`
* `watch the next episode of The Bear`
* `what's playing?`
* `what is it about?`
* `watch it`
* `play it`

## Spotify And Sonos

Spotify commands require Spotify API credentials and a playback path your Sonos/Home Assistant setup can start.

* `play Abbey Road`
* `play music by Talking Heads`
* `play Discover Weekly`
* `play my dinner playlist`
* `save this song`
* `play music here`
* `play music in the kitchen`
* `play KCLU` (when `PINNED_RADIO_STATIONS` contains `kclu`)

## Date, Time, Weather, Location, Astronomy, And Repeat

These are deterministic utility routes rather than AI answers:

* `what time is it?`
* `what time is it in Tokyo?`
* `what's the date?`
* `what day is it?`
* `what date is it in Tokyo?`
* `what's the weather?`
* `what's the weather in Tokyo?`
* `what's the weather tomorrow?`
* `what's the weather in Tokyo tomorrow?`
* `what's the weather tonight?`
* `what's the weather in Tokyo tonight?`
* `what's the weather this weekend?`
* `will it rain tomorrow?`
* `do I need an umbrella Thursday?`
* `hourly forecast for the next 6 hours`
* `what's the forecast for Thursday?`
* `what's the weather this week?`
* `forecast for next week`
* `seven-day forecast in Tokyo`
* `how far away is San Francisco?`
* `how far is San Francisco from home?`
* `how far is San Francisco from Los Angeles?`
* `what direction is San Francisco from home?`
* `when is sunrise?`
* `what time is sunset tomorrow?`
* `when is dawn Thursday?`
* `when does the moon rise?`
* `when is moonset tomorrow?`
* `what's the moon phase?`
* `what phase will the moon be Thursday?`
* `when is the next full moon?`
* `when is the next new moon?`
* `is the sun up?`
* `is the moon up right now?`
* `when does Jupiter rise?`
* `when does Saturn set tomorrow?`
* `is Mars up right now?`
* `where is Venus?`
* `can I see Jupiter right now?`
* `what's the best time to see Venus tonight?`
* `what planets are visible tonight?`
* `say that again`
* `repeat that`
* `what did you say?`

Current date and time questions do not use the AI fallback. Without a named
place they use the Home Suite host's local clock and timezone. A place named
with `in ...` is geocoded and answered in that location's timezone.

Location-distance questions use keyless Open-Meteo geocoding and local
great-circle math. Answers are explicitly straight-line distances, not road
distances. A registered fixed source can use configured home coordinates when
the origin is omitted. Mobile and unknown sources ask for an origin; replying
`from home` or `from Los Angeles` completes that pending request within the
same source-scoped context. Measurement units follow `ASSISTANT_PROFILE`.

Weather questions prefer Home Assistant and fall back to Open-Meteo when home
coordinates are configured. Tonight and next-hours questions use hourly data;
weekend and weekday questions use daily forecasts. Rain and umbrella questions
receive a direct precipitation answer. Severe-weather alerts are not currently
supported because they require a separate authoritative alert feed.

Astronomy questions use the coordinates and timezone in `HOME_LOCATION`.
Astral answers Sun and Moon questions. Skyfield and its packaged JPL ephemeris
answer rise, set, position, and potential naked-eye visibility questions for
Mercury through Neptune without making a request at command time. Visibility
answers assume clear skies and an unobstructed horizon. Sunrise and sunset can
also be used for scheduling; lunar and planetary events are query-only.

## Stock Quotes And Market Hours

These read-only requests require Alpaca market-data credentials:

* `how's Apple stock?`
* `what's Apple's stock price?`
* `Apple stock price`
* `what is AAPL trading at?`
* `how is Nvidia stock doing today?`
* `stock quote for Apple and Microsoft`
* `how did Apple close?`
* `is the stock market open?`
* `when does the stock market open?`
* `when does the stock market close?`

Ticker symbols work directly. Common company names are built in, and
deployment-specific spoken names can be added with
`STOCK_SYMBOL_ALIAS_OVERRIDES`. Home Suite quotes at most five symbols per
request by default. The Alpaca Basic configuration uses IEX market data; it does
not submit trades or inspect an account portfolio.

## YouTube Lounge

YouTube features require pairing with the TV YouTube app. Digest and playlist-style features may require YouTube Data API OAuth.

* `watch my daily reel`
* `what's new on YouTube?`
* `watch Veritasium on YouTube`
* `add channel at handle to my digest`
* `next video`

## Homelab And Services

Home Suite prefers Home Assistant for broad status portability. Optional direct APIs add richer qBittorrent, Seerr, and Uptime Kuma behavior.

Homelab ownership requires explicit local action or live-status grammar. General
conversation that merely mentions words such as `requests`, `services`,
`internet`, or `cameras` remains available to the AI fallback.

* `service status`
* `is anything down?`
* `how's the homelab?`
* `how many torrents are active?`
* `how many torrents are completed?`
* `what movies are downloading?`
* `pause completed downloads`
* `media request status`
* `how's the NAS?`
* `are the drives healthy?`
* `how's the internet?`
* `any camera alerts?`

## Alarms, Timers, Reminders, And Scheduling

Scheduled jobs feed back through the same command brain as live requests, so delayed actions use the same routing and safety checks.

* `set a timer for 10 minutes`
* `set an audit timer for 10 minutes`
* `pause the timer`
* `resume the pasta timer`
* `add 5 minutes to the timer`
* `take 2 minutes off the pasta timer`
* `set the pasta timer to 15 minutes`
* `how much time is left on my pasta timer?`
* `okay, add 5 minutes to it`
* `set it to 20 minutes`
* `how much time is left?`
* `cancel it`
* `set an alarm for 7 AM`
* `set an alarm for 7 tomorrow morning`
* `what time is my work alarm?`
* `snooze the work alarm for 5 minutes`
* `wake me up with music at 7 AM`
* `wake me up at 7 AM with my morning playlist`
* `remind me to check the laundry in 45 minutes`
* `remind me tomorrow at 7 AM to call Mom`
* `tomorrow at 7 AM turn on the porch lights`
* `turn off the living room lights in 20 minutes`
* `turn on the porch lights at sunset`
* `tomorrow at sunrise open the bedroom blinds`
* `turn on the porch lights 20 minutes before sunset`
* `in 2 days turn off the porch light`
* `cancel my timer`
* `cancel my reminder`
* `what alarms are set?`
* `what reminders are set?`
* `what's scheduled?`
* `list scheduled actions`
* `cancel the last schedule`
* `cancel all schedules`
* `what's pending?`

Scheduling accepts relative durations, explicit clock times, tomorrow dayparts,
sunrise/sunset with optional offsets, and spoken reminders. Timer changes require
the word `timer`; bare `pause` and `resume` remain media commands. When several
timers could match, Home Suite asks for a name or room instead of guessing.
Direct `set ... timer to ...` requests replace the remaining duration rather
than creating another timer. Bare `snooze` uses `ALARM_DEFAULT_SNOOZE_MINUTES`.
Snoozing only applies to plain alarms and timers that fired within
`ALARM_SNOOZE_RECENT_WINDOW_SECONDS`. Pending timers can be extended or assigned
a new remaining duration instead. Alarms with attached music or device actions
fail closed so Home Suite cannot accidentally replay them.

General scheduled actions accept relative days and weeks in addition to shorter
durations. They default to a 30-day maximum and require confirmation when more
than one day away. Month/year and otherwise unsupported timing language is
recognized and rejected before any immediate device handler can act. `what's
pending?` gives a concise combined count across timers, alarms, reminders,
general schedules, and temporary restorations, plus the next due item.

Short follow-ups use source-scoped structured dialogue state. Home Suite stores
the stable ID of an unambiguously selected timer, alarm, light, location, or
media item and resolves only referents compatible with the new command. Thus
`add 5 minutes to it` can resolve a timer while `play it` can resolve media.
Creation and successful named actions also update focus, so `set a timer for 5
minutes` can be followed immediately by `add 1 minute to it` without first
querying the timer.
Explicit names always take precedence, expired or missing context is not
guessed, and unrelated request sources do not share pronouns unless their
`SOURCES` entries intentionally share a continuity group.

Successful deterministic actions and queries may also publish a short-lived
typed intent frame containing only the command shape and safe slots needed for
compatible corrections, target transfers, or query refinements. It is separate
from the stable-ID referent above: every rewritten follow-up goes back through
the normal dispatcher, resolver, confirmation policy, and current-state checks.

## Calendars

Calendar commands use only the `calendar.*` entities configured through Home
Assistant:

* `what's on my calendar today?`
* `what's on the family calendar tomorrow?`
* `what's on my calendar this week?`
* `what is my next event?`
* `when is my dentist appointment?`
* `add dentist appointment to my calendar on July 20 at 4:30 PM`
* `add an event to my calendar on July 20 at 4:30 PM`
* `add dentist appointment to my calendar`

The last two forms start a source-scoped draft and ask for the missing title or
date/time. Timed events default to
`CALENDAR_DEFAULT_EVENT_DURATION_MINUTES`. Creation remains disabled unless
`CALENDAR_WRITES_ENABLED` is true and the selected calendar is marked
`writable`; when confirmation is enabled, Home Suite does not write until the
user accepts the complete event summary. The approval prompt ends with `Is
that right?`; `yes` creates the event, while `no` keeps it as a short-lived
editable draft and asks which detail to change. You can answer directly with a
replacement such as `at 10:45 PM`, or select a field first with `the time`,
`the day`, `the title`, `the duration`, or `the calendar`. Compact corrections
such as `no, at 10:45 PM` also work. `cancel` or `never mind` discards the
pending draft without writing anything.

Final calendar approval uses the same typed confirmation gate as other
protected commands. Exact replies such as `yes`, `confirm`, `do it`, or `go
ahead` approve only the pending action in that request source. Calendar writes
use the same gate with the bounded revision behavior above. Any unrelated
utterance supersedes the confirmation and routes as a new command, so a later
stray `yes` cannot authorize stale work.

## Announcements And Speech Testing

Use `say` for local speech/TTS testing. Use `announce` when you want the message routed through configured speakers.

* `say this is a speech test`
* `say the quick brown fox jumps over the lazy dog`
* `announce dinner is ready`
* `announce dinner is ready in the kitchen`
* `announce the dryer is done upstairs`

## Chat, AI Fallback, And Follow-Ups

AI can answer conversational questions and leave short-lived context breadcrumbs. Follow-up actions still route through deterministic Plex, Spotify, or Home Assistant handlers.

Dedicated joke requests avoid the 50 most recently generated jokes. That
bounded history is stored in `state/recent_jokes.json`, so it survives service
and device restarts. Immediate follow-ups such as `another` stay in joke mode
only for the same request source.

* `what's the latest news?`
* `tell me a joke`
* `another`
* `what is the most popular Beatles song?`
* `play it`
* `what movie has Darth Vader telling Luke he is his father?`
* `watch it`
* `what is this movie about?`
* `tell me more about that`
* `how far is that by car?`
* `how far is that to drive?`
* `how long would that take?`

## External Interfaces

These are not spoken commands, but they are useful ways to send the same command text into Home Suite.

* `homesuite repl`
* `homesuite test "service status"`
* `homesuite repl --live`
* `pptest` and `pplive` (legacy aliases)
* `ppchattest`
* `ppchat`
* HTTP `POST /command`
* WebSocket `/ws`
* Telegram bot frontend
* Raycast or menu-bar clients that call the HTTP/WebSocket API

## Dismissing An Accidental Summon

After a wakeword or PTT capture begins, say one of these exact phrases to end
the current interaction silently:

```text
cancel
never mind
nevermind
```

Home Suite performs no device action, sends nothing to ChatGPT, and plays
neither a success nor an error tone. Wakeword mode rearms normally; an off-hook
PTT session returns to its listening loop. Longer commands such as `cancel my
timer` are not dismissals and continue to their normal handlers. A dismissal
also clears any source-scoped calendar draft or command confirmation while
leaving ordinary light, timer, media, and location follow-up context intact.

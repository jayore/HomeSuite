# Features

This is a public-alpha overview of what Home Suite can do. Exact behavior depends on your Home Assistant entities, room names, media libraries, and configured services.

For a categorized list of phrases to try, see [COMMANDS.md](COMMANDS.md).

## Plain-English Home Assistant Control

Home Suite expects most device control to flow through Home Assistant, but the day-to-day command surface is plain English. Scenes, scripts, areas, entities, and friendly names should be made sensible there first. If a phrase does not route well, improve the Home Assistant naming before adding more Home Suite-specific configuration.

Examples include lights, switches, locks, scenes, scripts, state questions, and scheduled Home Assistant actions.

## Rooms, Focus, And Defaults

Fixed devices can have fixed room defaults. Mobile clients such as chat, Telegram, Raycast-style launchers, or future satellites can keep sticky room focus. That lets Home Suite route short commands to the room or media player that makes sense without forcing every request to mention a Home Assistant entity.

A room can coordinate multiple kinds of targets rather than pretending they are
one physical entity. Its configuration may include media players, Sonos
speakers, Apple TV devices, brightness strategies, and aliases. Commands such
as `pause`, `what's playing?`, or `lights to 30%` resolve against that topology
and current state. Explicit room and device names remain supported when the
default context is not appropriate.

## Media Control

Home Suite keeps room, source, and media context so follow-ups can work when possible. Bare commands such as `pause`, `resume`, or `volume up` can route to the focused player instead of requiring an entity name every time.

## Plex

Plex actions use deterministic natural-language routing. Home Suite uses stored context and your actual Plex library rather than letting AI invent Plex IDs.

## Spotify And Sonos

Spotify support depends on Spotify API credentials and a playback path that your Sonos/Home Assistant setup can actually start. If Spotify is blank, Home Suite should skip or explain that the integration is not configured.

## YouTube Lounge

YouTube features require pairing with the TV YouTube app and, for playlist/reel management, YouTube Data API OAuth.

## Homelab And Services

Home Suite prefers Home Assistant for broad status portability. Optional direct APIs add richer qBittorrent and Seerr behavior. Uptime Kuma is a good first homelab integration because it can expose a read-only status page.

## Alarms, Timers, And Scheduling

Scheduled jobs execute through the same command brain as live requests, so delayed actions use the same plain-English routing and safety checks.

## Date, Time, Weather, And Astronomy

Current date and time, current weather, daily forecasts, and local astronomy
questions use deterministic handlers rather than conversational guesses. Date
and time can use the host clock or a geocoded named location. Home Suite can
also answer sunrise, sunset, civil dawn and dusk, moonrise, moonset, lunar
phase, the next full or new moon date, whether the sun or moon is above the
horizon, planet rise and set times, current planetary positions, and which
naked-eye planets should be visible tonight. Astral calculates Sun and Moon
events; Skyfield uses a packaged JPL ephemeris for planetary answers. Both run
locally from the configured home coordinates and timezone. Current Home
Assistant `sun.sun` and `sensor.moon_phase` state is preferred when available.

Planet visibility is an observing estimate, not a claim about actual sky
conditions. It applies configurable darkness, altitude, magnitude, and minimum
duration thresholds, then clearly assumes clear skies and an unobstructed local
horizon.

Sunrise and sunset can anchor scheduled Home Assistant actions. Lunar events
are query-only in the current release.

## Stock Quotes And Market Hours

An optional deterministic Alpaca integration answers current stock quotes,
daily movement, completed-session closes, multi-symbol requests, and U.S.
regular-market open/close questions. It uses a short cache and bounded network
timeouts so voice, Telegram, and HTTP all receive the same behavior without
turning repeat requests into unnecessary provider calls.

The default free-data configuration uses Alpaca's IEX feed rather than a
consolidated whole-market quote. Home Suite does not expose portfolio data,
recommendations, or trading actions; the integration calls only market
snapshots and the market clock.

## Announcements And TTS

The `say` path is useful when tuning TTS cadence, punctuation normalization, or audio routing. Announcements can target configured room speakers instead of requiring raw media player entity IDs.

## Chat And Conversational Fallback

The AI fallback can answer conversational questions, optionally use hosted web
search for current information, and leave short-lived media breadcrumbs.
Follow-up actions still route through deterministic Plex, Spotify, or Home
Assistant handlers. The runtime is self-hosted, but OpenAI and other configured
integrations are network services rather than local-only dependencies.

## Voice, PTT, And Wake Words

Handset push-to-talk and wake-word capture share transcription, interaction,
and command-routing behavior while keeping trigger-specific audio mechanics
separate. The wake-word path supports continuous capture, same-stream command
handoff, VAD endpointing, microphone profiles, calibration, near-miss logging,
asynchronous response speech, barge-in, and configurable OpenWakeWord models.
See [WAKEWORD.md](WAKEWORD.md) for the hardware and tuning details.

## External Interfaces

The same command brain can be used from a local handset or wake-word appliance, `pptest`, `pplive`, `ppchattest`, `ppchat`, HTTP `POST /command`, WebSocket `/ws`, Telegram, scheduled jobs, physical buttons, and companion apps.

Raycast, menu-bar, and other satellite-style clients should become separate repos that link back to Home Suite's HTTP/WebSocket API.

## Experimental Hardware And Applets

Home Suite includes an auxiliary physical-button mapper and a small applet
lifecycle registry. Buttons translate gestures into normal command strings, so
they reuse the same deterministic router. Applets can run an independent
subprocess or temporarily remap buttons for a mode such as an Apple TV remote.

These are extension points rather than polished portable features today. Pin
maps, gestures, and `PHYSICAL_BUTTON_ACTIONS` belong in `local_prefs.py`.
Applet registrations currently live in `applet_controls.py` and may require
device-specific dependencies or exclusive microphone ownership.

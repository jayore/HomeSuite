# Features and Things to Try

This is a public-alpha overview of what Home Suite can do. Exact phrasing depends on your Home Assistant entities, room names, media libraries, and configured services.

Use `pptest` to try examples safely in an interactive test shell. Use `pptest "phrase here"` for a one-shot check. Use `pplive` only when you are ready for real device effects.

The examples below are meant to show the shape of the system: plain-English commands, room-aware routing, follow-up context, media focus, announcements, schedules, and optional homelab status all flow through the same command brain.

## Smart Home

Try:

* `turn on the kitchen lights`
* `turn off the downstairs lights`
* `dim the living room to 40 percent`
* `make the lamp blue`
* `set the bedroom lights to warm white`
* `lock the front door`
* `is the garage door open?`
* `what lights are on?`
* `run movie night`

Home Suite expects most device control to flow through Home Assistant, but the day-to-day command surface is plain English. Scenes, scripts, areas, entities, and friendly names should be made sensible there first. If a phrase does not route well, improve the Home Assistant naming before adding more Home Suite-specific configuration.

## Media Control

Try:

* `what's playing?`
* `pause`
* `resume`
* `next track`
* `volume up`
* `set the living room volume to 25`
* `play music in the kitchen`
* `switch the living room Sonos to TV audio`

Home Suite keeps room, source, and media context so follow-ups can work when possible. Bare commands such as `pause`, `resume`, or `volume up` can route to the focused player instead of requiring an entity name every time.

## Rooms, Focus, and Plain-English Defaults

Try:

* `I'm in the bedroom`
* `where am I?`
* `clear my room focus`
* `turn on the lights`
* `dim it to 30 percent`
* `play music here`
* `announce laundry is done in the kitchen`

Fixed devices can have fixed room defaults. Mobile clients such as chat, Telegram, Raycast-style launchers, or future satellites can keep sticky room focus. That lets Home Suite route short commands to the room or media player that makes sense without forcing every request to mention a Home Assistant entity.

## Plex

Try:

* `watch The Matrix`
* `watch the movie where people live in a simulation`
* `watch the next episode of The Bear`
* `what's playing?`
* `what is it about?`
* `watch it`

Plex actions use deterministic natural-language routing: Home Suite uses stored context and your actual Plex library rather than letting AI invent Plex IDs.

## Spotify and Sonos

Try:

* `play Abbey Road`
* `play music by Talking Heads`
* `play Discover Weekly`
* `play my dinner playlist`
* `save this song`

Spotify support depends on Spotify API credentials and a playback path that your Sonos/Home Assistant setup can actually start. If Spotify is blank, Home Suite should skip or explain that the integration is not configured.

## YouTube Lounge

Try:

* `watch my daily reel`
* `what's new on YouTube?`
* `watch Veritasium on YouTube`
* `add channel at handle to my digest`
* `next video`

YouTube features require pairing with the TV YouTube app and, for playlist/reel management, YouTube Data API OAuth.

## Homelab and Services

Try:

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

Home Suite prefers Home Assistant for broad status portability. Optional direct APIs add richer qBittorrent and Seerr behavior. Uptime Kuma is a good first homelab integration because it can expose a read-only status page.

## Alarms, Timers, and Scheduling

Try:

* `set a timer for 10 minutes`
* `set an alarm for 7 tomorrow morning`
* `wake me up with music at 7`
* `remind me to check the laundry in 45 minutes`
* `turn on the porch lights at sunset`
* `turn off the living room lights in 20 minutes`
* `cancel my timer`
* `what alarms are set?`

Scheduled jobs execute through the same command brain as live requests, so delayed actions use the same plain-English routing and safety checks.

## Local Say / TTS Testing

Try:

* `say this is a speech test`
* `announce dinner is ready`
* `announce dinner is ready in the kitchen`
* `announce the dryer is done upstairs`

The `say` path is useful when tuning TTS cadence, punctuation normalization, or audio routing. Announcements can target configured room speakers instead of requiring raw media player entity IDs.

## Chat and Conversational Fallback

Try:

* `what is the most popular Beatles song?`
* `play it`
* `what movie has Darth Vader telling Luke he is his father?`
* `watch it`

The AI fallback can leave short-lived media breadcrumbs. Follow-up actions still route through deterministic Plex/Spotify natural-language handlers.

## External Interfaces

The same command brain can be used from:

* local handset or wake-word appliance
* `pptest` and `pplive`
* `ppchattest` and `ppchat`
* HTTP `POST /command`
* WebSocket `/ws`
* Telegram bot frontend
* Raycast extension
* macOS menu-bar app
* scheduled jobs
* physical buttons

Raycast, menu-bar, and other satellite-style clients should become separate repos that link back to Home Suite's HTTP/WebSocket API.

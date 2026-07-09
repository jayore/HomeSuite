# Features and Things to Try

This is a public-alpha overview of what HomeSuite can do. Exact phrasing depends on your Home Assistant entities, room names, media libraries, and configured services.

Use `pptest` to try examples safely in an interactive test shell. Use `pptest "phrase here"` for a one-shot check. Use `pplive` only when you are ready for real device effects.

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

HomeSuite expects most device control to flow through Home Assistant. Scenes, scripts, areas, entities, and friendly names should be made sensible there first. If a phrase does not route well, improve the Home Assistant naming before adding more HomeSuite-specific configuration.

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

HomeSuite keeps request context so follow-ups can work when possible.

## Plex

Try:

* `watch The Matrix`
* `watch the movie where people live in a simulation`
* `watch the next episode of The Bear`
* `what's playing?`
* `what is it about?`
* `watch it`

Plex actions use deterministic natural-language routing: HomeSuite uses stored context and your actual Plex library rather than letting AI invent Plex IDs.

## Spotify and Sonos

Try:

* `play Abbey Road`
* `play music by Talking Heads`
* `play Discover Weekly`
* `play my dinner playlist`
* `save this song`

Spotify support depends on Spotify API credentials and a playback path that your Sonos/Home Assistant setup can actually start. If Spotify is blank, HomeSuite should skip or explain that the integration is not configured.

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

HomeSuite prefers Home Assistant for broad status portability. Optional direct APIs add richer qBittorrent and Seerr behavior. Uptime Kuma is a good first homelab integration because it can expose a read-only status page.

## Alarms, Timers, and Scheduling

Try:

* `set a timer for 10 minutes`
* `set an alarm for 7 tomorrow morning`
* `remind me to check the laundry in 45 minutes`
* `turn on the porch lights at sunset`
* `cancel my timer`
* `what alarms are set?`

Scheduled jobs execute through the same command brain as live requests.

## Local Say / TTS Testing

Try:

* `say this is a speech test`
* `announce dinner is ready`

The `say` path is useful when tuning TTS cadence, punctuation normalization, or audio routing.

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

Raycast, menu-bar, and other satellite-style clients should become separate repos that link back to HomeSuite's HTTP/WebSocket API.

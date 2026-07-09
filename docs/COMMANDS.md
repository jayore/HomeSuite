# Commands To Try

Home Suite commands are natural-language examples, not a strict grammar. Exact results depend on your Home Assistant rooms, entity names, scenes, scripts, media libraries, and configured services.

Use `pptest` for safe testing while you are setting things up:

```bash
pptest
```

Then type a phrase at the prompt. For a single reproducible check, run:

```bash
pptest "what lights are on?"
```

Use `pplive`, `ppchat`, voice, Telegram, HTTP, or other clients only when you are ready for commands to affect real devices.

## First Checks

These are good early tests after `homesuite-doctor` passes its core checks:

* `what lights are on?`
* `turn on the kitchen lights`
* `service status`
* `where am I?`
* `say this is a speech test`

## Home Assistant Control

These routes go through Home Assistant entities, areas, scenes, scripts, and services. Good Home Assistant naming makes these much easier to use.

* `turn on the kitchen lights`
* `turn off the downstairs lights`
* `dim the living room to 40 percent`
* `make the lamp blue`
* `set the bedroom lights to warm white`
* `turn on the porch lights at sunset`
* `turn off the living room lights in 20 minutes`
* `lock the front door`
* `is the garage door open?`
* `what lights are on?`
* `run movie night`

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

## YouTube Lounge

YouTube features require pairing with the TV YouTube app. Digest and playlist-style features may require YouTube Data API OAuth.

* `watch my daily reel`
* `what's new on YouTube?`
* `watch Veritasium on YouTube`
* `add channel at handle to my digest`
* `next video`

## Homelab And Services

Home Suite prefers Home Assistant for broad status portability. Optional direct APIs add richer qBittorrent, Seerr, and Uptime Kuma behavior.

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
* `set an alarm for 7 tomorrow morning`
* `wake me up with music at 7`
* `remind me to check the laundry in 45 minutes`
* `turn on the porch lights at sunset`
* `turn off the living room lights in 20 minutes`
* `cancel my timer`
* `what alarms are set?`

## Announcements And Speech Testing

Use `say` for local speech/TTS testing. Use `announce` when you want the message routed through configured speakers.

* `say this is a speech test`
* `say the quick brown fox jumps over the lazy dog`
* `announce dinner is ready`
* `announce dinner is ready in the kitchen`
* `announce the dryer is done upstairs`

## Chat, AI Fallback, And Follow-Ups

AI can answer conversational questions and leave short-lived context breadcrumbs. Follow-up actions still route through deterministic Plex, Spotify, or Home Assistant handlers.

* `what is the most popular Beatles song?`
* `play it`
* `what movie has Darth Vader telling Luke he is his father?`
* `watch it`
* `what is this movie about?`
* `tell me more about that`

## External Interfaces

These are not spoken commands, but they are useful ways to send the same command text into Home Suite.

* `pptest`
* `pptest "service status"`
* `pplive`
* `ppchattest`
* `ppchat`
* HTTP `POST /command`
* WebSocket `/ws`
* Telegram bot frontend
* Raycast or menu-bar clients that call the HTTP/WebSocket API

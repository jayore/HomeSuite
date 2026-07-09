# Features

This is a public-alpha overview of what Home Suite can do. Exact behavior depends on your Home Assistant entities, room names, media libraries, and configured services.

For a categorized list of phrases to try, see [COMMANDS.md](COMMANDS.md).

## Plain-English Home Assistant Control

Home Suite expects most device control to flow through Home Assistant, but the day-to-day command surface is plain English. Scenes, scripts, areas, entities, and friendly names should be made sensible there first. If a phrase does not route well, improve the Home Assistant naming before adding more Home Suite-specific configuration.

Examples include lights, switches, locks, scenes, scripts, state questions, and scheduled Home Assistant actions.

## Rooms, Focus, And Defaults

Fixed devices can have fixed room defaults. Mobile clients such as chat, Telegram, Raycast-style launchers, or future satellites can keep sticky room focus. That lets Home Suite route short commands to the room or media player that makes sense without forcing every request to mention a Home Assistant entity.

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

## Announcements And TTS

The `say` path is useful when tuning TTS cadence, punctuation normalization, or audio routing. Announcements can target configured room speakers instead of requiring raw media player entity IDs.

## Chat And Conversational Fallback

The AI fallback can leave short-lived media breadcrumbs. Follow-up actions still route through deterministic Plex/Spotify/Home Assistant handlers.

## External Interfaces

The same command brain can be used from a local handset or wake-word appliance, `pptest`, `pplive`, `ppchattest`, `ppchat`, HTTP `POST /command`, WebSocket `/ws`, Telegram, scheduled jobs, physical buttons, and companion apps.

Raycast, menu-bar, and other satellite-style clients should become separate repos that link back to Home Suite's HTTP/WebSocket API.

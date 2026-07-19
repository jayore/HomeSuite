# Configuration Guide

This is the setting and behavior reference. Credential acquisition belongs in
[CREDENTIALS.md](CREDENTIALS.md), while service capabilities and operational
prerequisites belong in [INTEGRATIONS.md](INTEGRATIONS.md).

Home Suite separates application defaults, shared deployment topology,
device-specific settings, and secrets:

* `app_config.py` - tracked application defaults; normally do not edit in a public install
* `deployment_config.py` - ignored shared room topology and entity mappings
* `private_config.py` - credentials, tokens, service URLs, and API keys
* `local_prefs.py` - device-specific room, audio, hardware, and behavior overrides

Start by copying the examples if you installed manually:

```bash
cp private_config.example.py private_config.py
cp deployment_config.example.py deployment_config.py
cp local_prefs.example.py local_prefs.py
```

Never commit real local config files to a public repo.

The management console presents guided fields and a complete inventory of
effective file-managed overrides. The inventory classifies each assignment as
guided, advanced, deprecated, or unrecognized, so a setting remains visible
even before it receives a dedicated editor. `homesuite doctor` reports the
same deprecated and unrecognized assignments.

Normal first-run and day-to-day settings belong to a feature page: **Settings**,
**Physical controls**, **Wake word**, **Audio**, **Rooms**, or **Integrations**.
The settings needed by the currently exercised text/API, PTT, wake-word, and
satellite roles have guided owners. Remaining documented expert controls are
specialized catalogs, policy thresholds, diagnostic retention, or low-level
compatibility values. They stay file-managed until they have a safe
domain-specific editor and appear under **Settings → Advanced** whenever they
are active. They are not hidden first-run requirements.

Use `deployment_config.py` for non-secret values shared by every device, such
as `ROOMS`, `HOME_LOCATION`, and entity labels. Use `private_config.py` for
shared secrets and endpoints. Use `local_prefs.py` for one device's audio,
wake-word, PTT, source, and output behavior.

The deployment template also starts home-specific catalogs empty. Populate
`HA_DEVICE_ALIASES`, `HA_TRIGGER_ALIASES`, pinned playlists/stations,
`YOUTUBE_CHANNELS`, `HOMELAB_SERVICES`, phonetic device repairs, and TTS
pronunciation overrides there when you need them. This prevents a fresh install
from inheriting the original deployment's entities or personal media choices.

Choose one or more node capabilities before tuning every setting. A text/API node can
keep both `PTT_ENABLED` and `WAKEWORD_ENABLED` false; a PTT node needs only
PTT settings; and a wake-word appliance needs a stable microphone profile and
model. A combined device can enable both; wake-word suppression applies only
while PTT capture is active. See [Deployment roles](DEPLOYMENT_ROLES.md) and run
`homesuite doctor --role <role>` to validate a specific target.

## Optional Integrations

Most integrations are optional. Leave service-specific values blank in `private_config.py` until you actually connect that service. Home Suite should still start, and commands for missing services should return a plain not-configured response instead of crashing.

Avoid placeholder URLs for services you do not run. A blank value tells Home Suite and `homesuite doctor` that the service is intentionally not configured.

For a service-by-service setup guide, see [INTEGRATIONS.md](INTEGRATIONS.md).
For account types, OAuth flows, key acquisition, speech-provider choices, and
security guidance, see [CREDENTIALS.md](CREDENTIALS.md).

## Minimum Useful Setup

For a fresh native install, the normal path is the browser console's **Setup**
view: connect Home Assistant, review a room, select the node roles, configure
audio only when a voice role needs it, test a command, and activate. The values
below document the equivalent file-managed configuration and remain useful for
advanced deployment or recovery.

For deterministic text control with the companion API enabled, set:

```python
OPENAI_API_KEY = ""  # Optional until conversation or voice is enabled.
HA_URL = "http://homeassistant.local:8123"
HA_TOKEN = "..."
HOMESUITE_HTTP_API_KEY = "choose-a-random-local-api-key"
```

Then configure the device role in `local_prefs.py`, for example:

```python
DEFAULT_ROOM = "living_room"
ASSISTANT_AUDIO_OUTPUT_MODE = "local"
WAKEWORD_ENABLED = False
PTT_ENABLED = False
```

Run the doctor command after editing config:

```bash
homesuite doctor
homesuite doctor --live
```

## OpenAI

Set `OPENAI_API_KEY` from the OpenAI API key page:

* https://platform.openai.com/api-keys

Conversational questions can use OpenAI web search for current information.
`CHATGPT_WEB_SEARCH_ENABLED` controls this separately from deterministic home
commands; disabling it avoids web-search tool-call charges. Override
`CHATGPT_WEB_SEARCH_MODEL` only when search should use a different model from
`CHATGPT_MODEL`. Older OpenAI SDKs automatically fall back to non-web chat.

After deterministic handlers decline a request, meaningful unclaimed language
can use the conversational fallback. Imperative home, media, and scheduling
language remains deterministic even when its target cannot be resolved, so the
AI cannot claim that an action occurred. `CHATGPT_CONTINUATION_WINDOW_SECONDS`
defaults to 120 seconds and lets short fragments such as `the one with Neo` or
`Neo` continue the latest AI exchange. Recency and conversation history are
scoped to the requesting source or its explicitly configured continuity group.
Voice input also rejects empty filler and isolated capture debris; typed
surfaces are intentionally more permissive.

Home Suite uses OpenAI for conversational fallback and, depending on configuration, transcription or media breadcrumb extraction. Home commands first go through Home Suite's deterministic natural-language processing and handlers; OpenAI is mainly for open-ended conversation and interpretation.

Most routine home-control commands should not call OpenAI. This keeps common actions faster and conservative with token usage, while preserving AI for cases where language understanding or conversation actually helps.

## Assistant Profile And Conversational Location

Persistent user and home context belongs in the shared, ignored
`deployment_config.py`, not in prompt strings or individual command modules:

```python
HOME_LOCATION = {
    # Optional coarse fields available to conversational AI and web search.
    "city": "Santa Barbara",
    "region": "California",
    "country": "US",

    # Exact fields used locally by deterministic weather and astronomy.
    "latitude": 34.4208,
    "longitude": -119.6982,
    "timezone": "America/Los_Angeles",
    "elevation_m": 30,
}

ASSISTANT_PROFILE = {
    "preferred_name": "Jason",
    "locale": "en-US",
    "units": "imperial",
    "notes": ["Prefers concise spoken answers."],
}
```

Only the optional `city`, `region`, `country`, and `timezone` fields are used
for conversational location context. Latitude, longitude, and elevation remain
local to deterministic calculations and are not copied into OpenAI prompts or
web-search location hints. `country` should be a two-letter ISO code when it
will be used for search localization.

For a fixed source, phrases such as `near me` may use the configured home area.
For a source marked `mobile: True`, Home Suite tells the conversational model
that home is not necessarily the user's current location and does not send a
default web-search locality. An explicit place in the request or recent
conversation always takes precedence.

`preferred_name` is a deployment default, not speaker recognition. A shared
room microphone cannot tell which household member is speaking. Keep secrets,
access codes, precise street addresses, medical details, and other sensitive
information out of `ASSISTANT_PROFILE` and its `notes`.

Profile context is generated fresh for each AI call and is not stored in the
conversation history. Short-lived actionable referents use
`DIALOGUE_REFERENT_TTL_SECONDS`, which defaults to two minutes and may be
overridden in `local_prefs.py`. Compatible deterministic follow-ups also use a
typed command-shape frame controlled by `DIALOGUE_INTENT_FRAME_TTL_SECONDS`,
which defaults to two minutes. See
[ROOM_CONFIGURATION.md](ROOM_CONFIGURATION.md) for source mobility,
`continuity_group`, and `device_group` behavior.

## Home Assistant

Set:

```python
HA_URL = "http://homeassistant.local:8123"
HA_TOKEN = "..."
```

Create a long-lived access token from your Home Assistant user profile. Home Assistant documents long-lived tokens under the user profile security settings:

* https://www.home-assistant.io/docs/authentication/

Home Suite depends heavily on Home Assistant for entity state, service calls, scenes, scripts, rooms, media players, and many homelab integrations. Good Home Assistant naming makes Home Suite dramatically easier to use: keep area names, entity names, scenes, and scripts human-readable.

Assign devices and entities to Home Assistant areas that match each room's configured `ha_area_id`. State questions such as “what is the bedroom humidity?” and “are any windows open?” use those area assignments plus Home Assistant device classes (`temperature`, `humidity`, `battery`, `door`, `window`, `garage_door`, and `opening`). Missing metadata fails explicitly instead of guessing.

Exclude virtual helpers, room proxies, and diagnostic lights from aggregate summaries and whole-home bulk actions with exact IDs or shell-style entity-ID patterns:

```python
ASSISTANT_BULK_EXCLUDED_ENTITY_IDS = [
    "light.living_room_brightness_proxy",
]
ASSISTANT_BULK_EXCLUDED_ENTITY_PATTERNS = [
    "light.*_flicker",
    "light.*scene_trigger*",
    "light.*_status_led",
]
```

These filters do not block an explicit named command. They only keep helper entities out of aggregate answers such as “what lights are on?” and whole-home commands such as “turn off all the lights.”

Spoken reminders use the same room/output routing and persistence as alarms,
but are voice-only by default. Change `REMINDER_SOUND_ENABLED` or
`REMINDER_VOICE_ENABLED` in shared configuration when a deployment needs a
different notification policy.

## Date, Time, Weather, Forecasts, And Astronomy

Current date and time requests require no configuration or external service.
Bare requests such as `what time is it?` and `what's the date?` use the host's
clock and timezone. Named-place requests such as `what date is it in Tokyo?`
use the same keyless geocoding service as named weather questions, then format
the answer in the destination timezone.

Weather settings are shared deployment topology and belong in
`deployment_config.py`:

```python
# None auto-discovers the first weather.* entity with a current temperature.
WEATHER_ENTITY_ID = None

# Home coordinates and IANA timezone for weather, distance, and astronomy.
HOME_LOCATION = {
    "city": "Santa Barbara",
    "region": "California",
    "country": "US",
    "latitude": 34.4208,
    "longitude": -119.6982,
    "timezone": "America/Los_Angeles",
    # Optional; defaults to sea level.
    "elevation_m": 30,
}

# Potential naked-eye planetary visibility criteria.
PLANET_VISIBILITY_PLANETS = ("mercury", "venus", "mars", "jupiter", "saturn")
PLANET_VISIBILITY_MIN_ALTITUDE_DEGREES = 10.0
PLANET_VISIBILITY_MAX_SUN_ALTITUDE_DEGREES = -6.0
PLANET_VISIBILITY_MAX_MAGNITUDE = 6.0
PLANET_VISIBILITY_MIN_DURATION_MINUTES = 15

LOCATION_ALIASES = {
    "la": "Los Angeles",
}
```

Set `WEATHER_ENTITY_ID` explicitly when Home Assistant exposes several weather
providers and one should be canonical. Leave it as `None` for automatic
selection. Home Suite uses the entity's current state for local conditions and
the modern `weather.get_forecasts` response for future daily forecasts.

`HOME_LOCATION` supplies a keyless Open-Meteo weather fallback when Home
Assistant is unavailable or its provider does not return the full requested
horizon. The same coordinates provide the implicit origin for straight-line
distance questions from registered fixed home sources and drive local Astral
Sun/Moon and Skyfield planetary calculations. `elevation_m` is optional and
improves horizon geometry for elevated observing locations. Use an IANA
timezone name such as `America/Los_Angeles`; `None` uses the host's timezone.
Set either coordinate to `None` to disable coordinate-based features. Named
weather and distance locations are geocoded independently. Astronomy questions
currently refer to the configured home location.

Distance units follow `ASSISTANT_PROFILE["units"]`: use `"imperial"` for miles
or `"metric"` for kilometers. Mobile and unknown command sources never assume
that the user is at `HOME_LOCATION`; include `from home` or a named origin.
Driving distance, routes, traffic, and ETA are not calculated by this handler
and can use the AI/web-search fallback when that integration is enabled.

Solar schedules such as `turn on the porch lights at sunset` prefer Home
Assistant's `sun.sun` next-event attributes and normally require no external
request. If an explicit day is not represented by that next event, Home Suite
uses Astral with `HOME_LOCATION` to calculate sunrise or sunset locally.
Without either source, the schedule fails closed and no immediate action runs.

Read-only astronomy questions cover sunrise, sunset, civil dawn and dusk,
moonrise, moonset, lunar phase, and whether the sun or moon is above the
horizon. Current phase prefers Home Assistant's `sensor.moon_phase` when that
integration is present; Astral supplies the fallback and future dates. Skyfield
adds planetary rise/set, current position, best viewing time, and visible-
planets-tonight questions using its locally installed JPL ephemeris. Lunar and
planetary events are not accepted as scheduling anchors in the current release.

The `PLANET_VISIBILITY_*` values define “potentially visible.” The default list
contains the five planets commonly treated as naked-eye targets. Uranus and
Neptune still support named rise, set, and position questions; add either to
`PLANET_VISIBILITY_PLANETS` only when that matches the intended observing setup.
The default thresholds require the planet to remain at least 10 degrees above
the horizon for 15 minutes while the Sun is at least 6 degrees below the
horizon. Magnitude 6 is the theoretical naked-eye limit under dark conditions,
not a guarantee in a light-polluted or obstructed location.

Supported deterministic requests include current conditions, today, tomorrow,
the next occurrence of a weekday, and forecasts of up to 14 days. A bare
weekday means its next occurrence; `next Thursday` means the following week's
Thursday. `this week`, `next week`, and `seven-day forecast` mean seven days
starting today. Forecast dates are resolved in the requested location's
timezone.

Home Assistant remains the preferred local source. Named-place forecasts,
weather fallback, and named-place distance geocoding require internet access to
Open-Meteo, but no Open-Meteo account or API key. Distance and bearing math,
Astral, and Skyfield calculations are local and require no credentials.
Skyfield's ephemeris is installed with the Python dependencies instead of being
downloaded during the first query.

## Calendars

Home Suite uses Home Assistant's calendar building block rather than storing a
second provider credential. After adding Google Calendar or another calendar
integration in Home Assistant, find its `calendar.*` entity IDs and map them in
the ignored `deployment_config.py` file:

```python
CALENDARS = {
    "personal": {
        "entity_id": "calendar.your_name_example_com",
        "label": "Personal",
        "aliases": ["personal", "my calendar"],
        "writable": True,
        "include_in_agenda": True,
    },
    "family": {
        "entity_id": "calendar.family",
        "label": "Family",
        "aliases": ["family"],
        "writable": False,
        "include_in_agenda": True,
    },
}

DEFAULT_CALENDAR = "personal"
CALENDAR_READS_ENABLED = True
CALENDAR_WRITES_ENABLED = False
CALENDAR_CONFIRM_WRITES = True
CALENDAR_DEFAULT_EVENT_DURATION_MINUTES = 60
CALENDAR_DRAFT_TTL_SECONDS = 2 * 60
CALENDAR_QUERY_MAX_EVENTS = 6
```

`include_in_agenda` controls which calendars are merged for an unqualified
agenda query. Naming a configured calendar selects it directly.
`DEFAULT_CALENDAR` is used for event creation when the request does not name a
target. Reads and writes have separate global switches; a write also requires
the selected target's `writable` flag. The shipped default leaves writes off.

Event drafts are short-lived and source-scoped. Home Suite can collect the
title first or the date and time first, applies the configured default duration
when one is omitted, and repeats the complete timed event before writing when
confirmation is enabled. Provider OAuth remains in Home Assistant. Home Suite
calls `calendar.get_events` and `calendar.create_event` with its existing
`HA_TOKEN` and stores no Google secret.

## Command Confirmations

Protected actions use typed, source-scoped confirmation state. An exact
affirmative or negative reply is intercepted before ordinary device routing or
AI fallback. An unrelated utterance supersedes the pending confirmation and
continues as a fresh command. Confirmed deterministic commands are replayed
through their normal handler with a one-use authorization, so the target,
permissions, and live state are resolved again before any effect.

Defaults live in `app_config.py`. Deployment config should override only the
policies it needs:

```python
COMMAND_CONFIRMATION_TTL_SECONDS = 45
COMMAND_CONFIRMATION_POLICY_OVERRIDES = {
    # Opt into confirmation before unlocking a resolved lock entity.
    "unlock": {"enabled": True},

    # Change the default six-hour temporary-action threshold.
    # "temporary_action_long": {"threshold_seconds": 3 * 60 * 60},
}
```

The shipped policies confirm temporary light changes over six hours and
general scheduled actions over one day. Calendar writes use this gate whenever
`CALENDAR_CONFIRM_WRITES` is enabled. Unlock confirmation is available but off
by default to avoid changing established behavior during an upgrade. Policies
may also use `confirm_source_ids`, `skip_source_ids`, or `confirm_origins` to
select where a prompt appears; absent filters apply to every source. These are
prompt filters, not access control. Handlers opt in only after resolving a real
compatible target. The
policy layer does not globally regex-match arbitrary commands or let AI decide
whether an action needs approval.

`cancel`, `never mind`, and `nevermind` silently clear pending interaction
state. A spoken `no` returns the policy's normal cancellation response.
Confirmations belong to the exact request source even when ordinary follow-up
referents share a configured continuity group; collecting drafts follow that
source's configured context bubble.

## Command Clarifications

When an otherwise valid short device name matches two to four live Home
Assistant entities, Home Suite can ask which entity was intended. The pending
choice belongs to the exact request source and expires quickly. A short answer
selects an option; a complete unrelated command supersedes the question.
Selection never authorizes a write by itself: the generated ordinary command
returns through the dispatcher, current entity resolution, capability checks,
and any configured confirmation policy.

Defaults live in `app_config.py` and may be overridden in `local_prefs.py`:

```python
# Command-shape context for corrections and target/query refinements.
DIALOGUE_INTENT_FRAME_TTL_SECONDS = 2 * 60

# Pending "which device?" choices are exact-source scoped.
COMMAND_CLARIFICATION_TTL_SECONDS = 45
```

Configured `HA_DEVICE_ALIASES`, on/off phrase overrides, and room targets take
precedence over inferred ambiguity. Clarification currently covers ordinary
on/off, named-light color, and named-light brightness commands. Bare contextual
targets such as `the lights`, `the TV`, or `brightness` continue through room
and request-context routing rather than being treated as ambiguous entity names.

## Temporary Light Actions

Temporary commands support one resolved Home Assistant `light.*` entity. They
snapshot the original on/off, brightness, color, color-temperature, and effect
attributes, then persist a conditional restore in
`state/temporary_actions.json`. At expiry, Home Suite restores only if the live
light still matches the observed temporary state. A manual change or later
permanent command wins. A newer temporary command on the same light replaces
the older deadline while retaining the first baseline.

Shared bounds may be overridden in `deployment_config.py`:

```python
TEMPORARY_ACTIONS_ENABLED = True
TEMPORARY_ACTION_MAX_SECONDS = 24 * 60 * 60
TEMPORARY_ACTION_OBSERVE_DELAY_SECONDS = 1.0
TEMPORARY_ACTION_OBSERVE_TIMEOUT_SECONDS = 5.0
SCHEDULER_MAX_HORIZON_SECONDS = 30 * 24 * 60 * 60
```

The observation window allows Home Assistant state to catch up with the write
before the restore is armed. Whole-room, multi-entity, and non-light temporary
requests fail explicitly; Home Suite does not invent an inverse command.
Temporary changes longer than the `temporary_action_long` policy threshold
require confirmation; requests beyond `TEMPORARY_ACTION_MAX_SECONDS` are
rejected before a light write. General scheduled commands use the independent
30-day horizon above and the `scheduled_action_long` confirmation policy.

Temporary restorations are queryable and manageable through the command
surface. An explicit restore-now command reapplies the saved baseline regardless
of the current light signature. A keep-current command removes the pending
restore without issuing a Home Assistant write. Automatic expiry remains
conditional, so later manual or permanent changes still win by default.

## Stock Quotes

Alpaca credentials are secrets and belong in `private_config.py`:

```python
ALPACA_API_KEY_ID = "..."
ALPACA_API_SECRET_KEY = "..."
```

Shared stock behavior can be overridden in `deployment_config.py`:

```python
# The Basic Alpaca plan supports IEX. Use another feed only when the account has access.
STOCK_QUOTE_DATA_FEED = "iex"
STOCK_QUOTE_MAX_SYMBOLS = 5
STOCK_QUOTE_CACHE_SECONDS = 15.0
STOCK_MARKET_CLOCK_CACHE_SECONDS = 30.0

# Extend the built-in company names without replacing them.
STOCK_SYMBOL_ALIAS_OVERRIDES = {
    "my company": "ACME",
}
STOCK_SYMBOL_LABEL_OVERRIDES = {
    "ACME": "Acme Corporation",
}
```

Direct ticker requests do not require an alias. Aliases help voice recognition
and support company names or personal shorthand; labels control how a symbol is
spoken back. Keep symbols uppercase and use Alpaca's symbol form, such as
`BRK.B`. `STOCK_QUOTE_TIMEOUT_SECONDS` and the provider base URLs are also
available in `app_config.py` for advanced deployments.

The default IEX feed is not a consolidated U.S. market quote. Home Suite
compares the latest trade with the previous close and uses the market clock to
distinguish an in-progress session from a completed close. Market times use
`HOME_LOCATION["timezone"]`, falling back to the host timezone when it is blank.
These are shared deployment settings, not per-device `local_prefs.py` values.

## Rooms And Targets

For the complete room schema, disabling rules, field reference, examples, and
source-room behavior, see [Room Configuration](ROOM_CONFIGURATION.md).

For public installs, `deployment_config.py` contains the canonical `ROOMS`
override. `DEFAULT_ROOM` stores a stable room ID, not another copy of the room
object:

```python
DEFAULT_ROOM = "living_room"
ROOMS = {
    "living_room": {
        "label": "Living Room",
        "ha_area_id": "living_room",
        "aliases": ["living room"],
        "defaults": {
            # Per-service room targets live here.
        },
    },
}
```

Runtime modules resolve that ID through `home_registry.get_default_room()`.
`home_registry.py` owns lookup, validation, and manifest behavior; it does not
carry a second copy of room topology.

The room's `defaults` object may independently configure brightness, color,
volume, audio, Spotcast, TV, and Plex routing. Flat compatibility maps such as
`SONOS_PLAYERS` are derived from these rooms.

### Brightness

Room-wide brightness commands use `brightness_target` in the room object.
Phrasing such as “brightness 50,” “kitchen lights 50,” and “make the kitchen
brighter” uses the same configured target.

Use one proxy/helper entity:

```python
"brightness_target": {
    "type": "entity",
    "entity_id": "light.living_room_brightness",
}
```

`number.*` and `input_number.*` entities are also supported. Home Suite
uses their `set_value` service.

Control all lights assigned to the room's `ha_area_id`:

```python
"brightness_target": {
    "type": "area",
}
```

Control only a selected set of room lights:

```python
"brightness_target": {
    "type": "entities",
    "entity_ids": [
        "light.ceiling",
        "light.floor_lamp",
    ],
}
```

The area strategy is convenient but intentionally opt-in because HA areas may
contain decorative, grouped, or non-dimmable lights. Run `homesuite doctor`
to see each room's resolved target. Existing `brightness_number` and
`brightness_light` keys remain supported for compatibility, but new
configurations should use `brightness_target`.

### Volume

`volume_target` controls both explicit room phrases and roomless requests:

```python
"volume_target": {
    "type": "entity",
    "entity_id": "number.living_room_volume",
}
```

Use a `number.*` or `input_number.*` helper when an automation distributes
volume, or a `media_player.*` entity for direct speaker control.

### Color, Audio, And Providers

Other room-local mappings use the same defaults object:

```python
"color_light": "light.living_room_color",
"audio_output": "media_player.living_room",
"announcements": "media_player.living_room",
"spotcast_device_name": "Livingroom",
"spotcast_device_aliases": ["sonos"],
"tv": "media_player.living_room_apple_tv",
"tv_remote": "remote.living_room_apple_tv",
```

`spotcast_device_name` is the provider's device name, which may differ from
the Home Suite room ID and Home Assistant entity ID. Leave unsupported
capabilities as `None`; handlers then fail closed instead of targeting another
room.

Use `None` for unsupported scalar values and targets, `[]` for optional lists,
and `{}` for optional mappings. Avoid empty strings. Do not remove a
`brightness_target` or `volume_target` merely to disable it, because omission
may activate legacy fallback behavior.

## Home Suite HTTP API Key

The in-process server is enabled by default. Set one shared local API key for
clients that call Home Suite over HTTP or WebSocket:

```python
HOMESUITE_HTTP_API_KEY = "a-long-random-string"
```

The API component fails closed when the key is blank. `/health` and `/healthz`
remain public; all other routes require authentication. Use the same value in
Raycast, menu-bar clients, satellites, or other tools that call Home Suite.
Telegram loads the shared command runtime in its own companion service and
does not require this API. See [API.md](API.md).

## Management Console

The separate browser console uses these node settings:

```python
# app_config.py defaults; override per node in local_prefs.py when needed.
CONSOLE_HOST = "0.0.0.0"
CONSOLE_PORT = 8766
```

Its optional private passphrase is:

```python
HOMESUITE_CONSOLE_KEY = ""
```

Blank means reuse `HOMESUITE_HTTP_API_KEY`; both blank means the console fails
closed. The console reports effective values, rooms, integration readiness,
and Doctor results. Its guided editors can update an allowlisted set of common
values in `local_prefs.py` and `private_config.py`, plus the canonical shared
`ROOMS` assignment in `deployment_config.py`. Structured editors keep room
topology, auxiliary GPIO button pin/action maps, and device-local audio profiles
aligned without exposing arbitrary Python editing in the browser.
Each field explains its purpose and expected format. Read-only views redact
credentials; authenticated Edit mode loads them into masked, revealable
fields. Home Suite shows a semantic review, creates a private backup, and
validates the resulting Python before an atomic write. It does not restart
services automatically. Low-level deployment policy remains a direct file
edit. The separate Chat surface sends messages through the running Home Suite
service; safe dry runs remain available through the CLI. See
[CONSOLE.md](CONSOLE.md).

## Plex

Set:

```python
PLEX_URL = "http://plex.local:32400"
PLEX_TOKEN = "..."
```

Plex documents the `X-Plex-Token` lookup flow here:

* https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/

The URL should point to your Plex Media Server, not a Plex web page.

## Spotify

Set:

```python
SPOTIFY_CLIENT_ID = "..."
SPOTIFY_CLIENT_SECRET = "..."
SPOTIFY_REFRESH_TOKEN = "..."
SPOTIFY_DISCOVER_WEEKLY_URI = "spotify:playlist:..."  # optional
```

Create a Spotify app in the Spotify Developer Dashboard. Spotify's app page explains that an app provides the Client ID and Client Secret used for authorization:

* https://developer.spotify.com/documentation/web-api/concepts/apps

The console stores Spotify credentials but does not yet conduct the Spotify
OAuth authorization itself. Use an external OAuth helper to obtain
`SPOTIFY_REFRESH_TOKEN` with the scopes needed for playback, library, and
playlist access, then enter the result under **Integrations → Spotify**.

## Telegram

Set:

```python
TELEGRAM_BOT_TOKEN = "..."
TELEGRAM_ALLOWED_USER_IDS = [123456789]
TELEGRAM_ALLOWED_CHAT_IDS = [123456789]
```

Create a bot with Telegram's BotFather. Telegram documents that `/newbot` generates the authentication token:

* https://core.telegram.org/bots/features#botfather

Keep allowlists tight. A Telegram bot connected to Home Suite can control your home.

Telegram polling runs as a separate long-lived process, so shared command-code
changes require both services to restart:

```bash
sudo systemctl restart homesuite.service
sudo systemctl restart piphone-telegram.service
```

The retained `piphone-telegram.service` unit name is for compatibility; its
working directory and interpreter must point to the current Home Suite checkout
and virtual environment. A portable unit is provided at
`deploy/systemd/piphone-telegram.service.template`. Do not point the service at
a retired `/home/.../piphone` checkout or the system Python.

## YouTube

Set:

```python
YOUTUBE_OAUTH_CLIENT_ID = "..."
YOUTUBE_OAUTH_CLIENT_SECRET = "..."
YOUTUBE_OAUTH_REFRESH_TOKEN = "..."
```

Google's YouTube Data API guide explains the need for a Google account, a Cloud project, credentials, and enabling the YouTube Data API v3:

* https://developers.google.com/youtube/v3/getting-started

Home Suite also has local tools:

```bash
homesuite-youtube-pair
homesuite-youtube-oauth
```

These CLI helpers complete pairing and OAuth outside the browser. The console
can store the resulting credentials, but it does not yet host the complete
YouTube authorization flow.

## qBittorrent

Set:

```python
QBITTORRENT_URL = "http://qbittorrent.local:8090"
QBITTORRENT_USERNAME = "..."
QBITTORRENT_PASSWORD = "..."
```

qBittorrent's WebUI API uses the WebUI username/password and cookie-based auth:

* https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-%28qBittorrent-5.0%29

Direct qBittorrent credentials enable richer torrent summaries and actions like pausing completed downloads.

## Seerr, Radarr, Sonarr, and Lidarr

Set whichever services you run:

```python
SEERR_URL = "http://seerr.local:5055"
SEERR_API_KEY = ""
RADARR_URL = "http://radarr.local:7878"
RADARR_API_KEY = ""
SONARR_URL = "http://sonarr.local:8989"
SONARR_API_KEY = ""
LIDARR_URL = "http://lidarr.local:8686"
LIDARR_API_KEY = ""
```

For Radarr/Sonarr/Lidarr, API keys are normally in each app's Settings area under security/general settings. The Servarr wiki covers these settings:

* https://wiki.servarr.com/radarr/settings
* https://wiki.servarr.com/sonarr/settings
* https://wiki.servarr.com/lidarr/settings

Home Suite currently uses Seerr directly for request status and can read Radarr/Sonarr/Lidarr via Home Assistant integrations where configured.

## Uptime Kuma

Set:

```python
UPTIME_KUMA_URL = "http://uptime-kuma.local:3001"
UPTIME_KUMA_STATUS_PAGE_SLUG = "home"
```

Home Suite reads a public/read-only Uptime Kuma status page rather than storing a Kuma admin password. Uptime Kuma documents status pages here:

* https://github.com/louislam/uptime-kuma/wiki/Status-Page

The URL should be the base Kuma URL; the slug is the part after `/status/`.

## Wake Word Engines

Wake-word settings are device-local because microphone hardware, models, gain,
and interaction timing differ by device. Use **Wake Word** in the management
console to install and activate models, then choose **Settings** for detection,
listening behavior, transcription, and advanced timing. The equivalent direct
configuration belongs in `local_prefs.py`; the current recommended engine is
OpenWakeWord:

```python
WAKEWORD_ENABLED = True
WAKEWORD_ENGINE = "openwakeword"
WAKEWORD_MODEL = "your_model_label"
WAKEWORD_MODEL_PATHS = [
    "/home/your-user/wake_models/your_model.onnx",
]
WAKEWORD_USE_STREAMING_STT = True
WAKEWORD_STT_MODE = "realtime_stream"
```

Use the management console's **Audio** view to inspect or edit
`AUDIO_INPUT_PROFILE`, test local playback, and run guided calibration. The
equivalent direct configuration belongs in `local_prefs.py` and may contain
only values that differ from the documented defaults. Prefer a stable
microphone name and ALSA card ID; do not assume a PortAudio device index or
numeric ALSA card number will remain stable after reboots or USB changes.

Porcupine remains supported as a compatibility engine. Its access key is a
secret and belongs in `private_config.py`:

```python
PVPORCUPINE_ACCESS_KEY = "..."
```

Wake-word setup is an advanced hardware path. Follow
[WAKEWORD.md](WAKEWORD.md) for the architecture, microphone profile schema,
gain calibration, OpenWakeWord model validation, endpoint settings, Realtime
fallback behavior, and log-based troubleshooting.

## Companion Clients

Companion clients should use the Home Suite HTTP API:

```text
POST /command
X-API-Key: <HOMESUITE_HTTP_API_KEY>
```

Recommended repo split as the ecosystem grows:

* `HomeSuite` - core runtime and API
* `homesuite-raycast` - Raycast extension
* `homesuite-menubar` - macOS menu-bar client
* `homesuite-telegram` - Telegram-specific packaging/docs if it grows beyond the built-in frontend
* `homesuite-satellite` - lightweight satellite device client

Keep credentials in the core Home Suite install or each client's local settings. Do not bake shared tokens into companion repos.

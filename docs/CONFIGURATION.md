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

Use `deployment_config.py` for non-secret values shared by every device, such
as `ROOMS`, `HOME_LOCATION`, and entity labels. Use `private_config.py` for
shared secrets and endpoints. Use `local_prefs.py` for one device's audio,
wake-word, handset, source, and output behavior.

The deployment template also starts home-specific catalogs empty. Populate
`HA_DEVICE_ALIASES`, `HA_TRIGGER_ALIASES`, pinned playlists/stations,
`YOUTUBE_CHANNELS`, `HOMELAB_SERVICES`, phonetic device repairs, and TTS
pronunciation overrides there when you need them. This prevents a fresh install
from inheriting the original deployment's entities or personal media choices.

## Optional Integrations

Most integrations are optional. Leave service-specific values blank in `private_config.py` until you actually connect that service. Home Suite should still start, and commands for missing services should return a plain not-configured response instead of crashing.

Avoid placeholder URLs for services you do not run. A blank value tells Home Suite and `homesuite-doctor` that the service is intentionally not configured.

For a service-by-service setup guide, see [INTEGRATIONS.md](INTEGRATIONS.md).
For account types, OAuth flows, key acquisition, speech-provider choices, and
security guidance, see [CREDENTIALS.md](CREDENTIALS.md).

## Minimum Useful Setup

For deterministic text control with the companion API enabled, set:

```python
OPENAI_API_KEY = ""  # Optional until conversation or voice is enabled.
HA_URL = "http://homeassistant.local:8123"
HA_TOKEN = "..."
HOMESUITE_HTTP_API_KEY = "choose-a-random-local-api-key"
PIPHONE_HTTP_API_KEY = HOMESUITE_HTTP_API_KEY
```

Then configure the device role in `local_prefs.py`, for example:

```python
DEFAULT_ROOM = "living_room"
ASSISTANT_AUDIO_OUTPUT_MODE = "local"
WAKEWORD_ENABLED = False
PTT_ENABLED = False
HANDSET_PRESENT = False
```

Run the doctor command after editing config:

```bash
homesuite-doctor
homesuite-doctor --live
```

## OpenAI

Set `OPENAI_API_KEY` from the OpenAI API key page:

* https://platform.openai.com/api-keys

Conversational questions can use OpenAI web search for current information.
`CHATGPT_WEB_SEARCH_ENABLED` controls this separately from deterministic home
commands; disabling it avoids web-search tool-call charges. Override
`CHATGPT_WEB_SEARCH_MODEL` only when search should use a different model from
`CHATGPT_MODEL`. Older OpenAI SDKs automatically fall back to non-web chat.

Home Suite uses OpenAI for conversational fallback and, depending on configuration, transcription or media breadcrumb extraction. Home commands first go through Home Suite's deterministic natural-language processing and handlers; OpenAI is mainly for open-ended conversation and interpretation.

Most routine home-control commands should not call OpenAI. This keeps common actions faster and conservative with token usage, while preserving AI for cases where language understanding or conversation actually helps.

## Home Assistant

Set:

```python
HA_URL = "http://homeassistant.local:8123"
HA_TOKEN = "..."
```

Create a long-lived access token from your Home Assistant user profile. Home Assistant documents long-lived tokens under the user profile security settings:

* https://www.home-assistant.io/docs/authentication/

Home Suite depends heavily on Home Assistant for entity state, service calls, scenes, scripts, rooms, media players, and many homelab integrations. Good Home Assistant naming makes Home Suite dramatically easier to use: keep area names, entity names, scenes, and scripts human-readable.

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
contain decorative, grouped, or non-dimmable lights. Run `homesuite-doctor`
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
PIPHONE_HTTP_API_KEY = HOMESUITE_HTTP_API_KEY
```

The API component fails closed when the key is blank. `/health` and `/healthz`
remain public; all other routes require authentication. Use the same value in
Raycast, menu-bar clients, satellites, or other tools that call Home Suite.
Telegram is an in-process frontend and does not require this API. See
[API.md](API.md).

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

The refresh-token flow is still rough in this public-alpha release. Expect to use your own OAuth helper or future Home Suite tooling to obtain `SPOTIFY_REFRESH_TOKEN` with the scopes needed for playback, library, and playlist access.

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

These are still public-alpha quality and may need refinement for a clean first-run setup.

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

Wake-word settings belong in `local_prefs.py` because microphone hardware,
models, gain, and interaction timing differ by device. The current recommended
engine is OpenWakeWord:

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

Define `AUDIO_INPUT_PROFILE` in the same file using a stable microphone name,
the hardware-supported sample rate, and optional ALSA mixer enforcement. Do not
assume a PortAudio device index will remain stable after reboots or USB changes.

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

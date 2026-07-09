# Configuration Guide

HomeSuite uses two local files that the installer creates for you:

* `private_config.py` - credentials, tokens, service URLs, and API keys
* `local_prefs.py` - device-specific room, audio, hardware, and behavior overrides

Start by copying the examples if you installed manually:

```bash
cp private_config.example.py private_config.py
cp local_prefs.example.py local_prefs.py
```

Never commit real local config files to a public repo.

Use `private_config.py` for values that are shared across a deployment, such as Home Assistant tokens and service API keys. Use `local_prefs.py` for values that describe one device, such as its default room, audio output, wake-word behavior, or handset hardware.

## Optional Integrations

Most integrations are optional. Leave service-specific values blank in `private_config.py` until you actually connect that service. HomeSuite should still start, and commands for missing services should return a plain not-configured response instead of crashing.

Avoid placeholder URLs for services you do not run. A blank value tells HomeSuite and `homesuite-doctor` that the service is intentionally not configured.

For a service-by-service setup guide, see [INTEGRATIONS.md](INTEGRATIONS.md).

## Minimum Useful Setup

For the currently supported public-alpha path, set:

```python
OPENAI_API_KEY = "..."
HA_URL = "http://homeassistant.local:8123"
HA_TOKEN = "..."
HOMESUITE_HTTP_API_KEY = "choose-a-random-local-api-key"
PIPHONE_HTTP_API_KEY = HOMESUITE_HTTP_API_KEY
```

Then configure the device role in `local_prefs.py`, for example:

```python
DEFAULT_ROOM = "living_room"
DEFAULT_SONOS_ROOM = "living_room"
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

HomeSuite uses OpenAI for conversational fallback and, depending on configuration, transcription or media breadcrumb extraction. Home commands first go through HomeSuite's deterministic natural-language processing and handlers; OpenAI is mainly for open-ended conversation and interpretation.

## Home Assistant

Set:

```python
HA_URL = "http://homeassistant.local:8123"
HA_TOKEN = "..."
```

Create a long-lived access token from your Home Assistant user profile. Home Assistant documents long-lived tokens under the user profile security settings:

* https://www.home-assistant.io/docs/authentication/

HomeSuite depends heavily on Home Assistant for entity state, service calls, scenes, scripts, rooms, media players, and many homelab integrations. Good Home Assistant naming makes HomeSuite dramatically easier to use: keep area names, entity names, scenes, and scripts human-readable.

## HomeSuite HTTP API Key

Set a local API key for clients that call HomeSuite over HTTP/WebSocket:

```python
HOMESUITE_HTTP_API_KEY = "a-long-random-string"
PIPHONE_HTTP_API_KEY = HOMESUITE_HTTP_API_KEY
```

Use the same value in Raycast, menu-bar clients, satellites, or other tools that send commands to `POST /command`.

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

The refresh-token flow is still rough in this public-alpha release. Expect to use your own OAuth helper or future HomeSuite tooling to obtain `SPOTIFY_REFRESH_TOKEN` with the scopes needed for playback, library, and playlist access.

## Telegram

Set:

```python
TELEGRAM_BOT_TOKEN = "..."
TELEGRAM_ALLOWED_USER_IDS = [123456789]
TELEGRAM_ALLOWED_CHAT_IDS = [123456789]
```

Create a bot with Telegram's BotFather. Telegram documents that `/newbot` generates the authentication token:

* https://core.telegram.org/bots/features#botfather

Keep allowlists tight. A Telegram bot connected to HomeSuite can control your home.

## YouTube

Set:

```python
YOUTUBE_OAUTH_CLIENT_ID = "..."
YOUTUBE_OAUTH_CLIENT_SECRET = "..."
YOUTUBE_OAUTH_REFRESH_TOKEN = "..."
```

Google's YouTube Data API guide explains the need for a Google account, a Cloud project, credentials, and enabling the YouTube Data API v3:

* https://developers.google.com/youtube/v3/getting-started

HomeSuite also has local tools:

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

HomeSuite currently uses Seerr directly for request status and can read Radarr/Sonarr/Lidarr via Home Assistant integrations where configured.

## Uptime Kuma

Set:

```python
UPTIME_KUMA_URL = "http://uptime-kuma.local:3001"
UPTIME_KUMA_STATUS_PAGE_SLUG = "home"
```

HomeSuite reads a public/read-only Uptime Kuma status page rather than storing a Kuma admin password. Uptime Kuma documents status pages here:

* https://github.com/louislam/uptime-kuma/wiki/Status-Page

The URL should be the base Kuma URL; the slug is the part after `/status/`.

## Wake Word Engines

For Porcupine:

```python
PVPORCUPINE_ACCESS_KEY = "..."
```

Set wake-word behavior per device in `local_prefs.py`:

```python
WAKEWORD_ENABLED = True
WAKEWORD_ENGINE = "porcupine"  # or "openwakeword" when installed/configured
```

Wake-word setup depends heavily on hardware, microphone, and runtime mode. Treat it as an advanced setup path for now.

## Companion Clients

Companion clients should use the HomeSuite HTTP API:

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

Keep credentials in the core HomeSuite install or each client's local settings. Do not bake shared tokens into companion repos.

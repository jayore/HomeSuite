# Integrations

Home Suite is designed so most integrations are optional. This guide describes
what each integration enables and its runtime prerequisites. Configure the
services you use, leave the rest blank, and run `homesuite-doctor` when you want
a quick read on what is ready. For account requirements, OAuth flows,
credential acquisition, and supported speech alternatives, see
[CREDENTIALS.md](CREDENTIALS.md). For individual setting behavior, see
[CONFIGURATION.md](CONFIGURATION.md).

As a rule, start with Home Assistant integrations when they expose enough state and control. Add direct API credentials only when Home Suite can do something meaningfully richer with them, such as qBittorrent download actions, Seerr request summaries, Plex library matching, or Spotify library operations.

## Core

### Home Assistant

What it enables:

* device state and control
* rooms, areas, scenes, and scripts
* media players and many homelab integrations
* calendar reads and event creation through configured `calendar.*` entities

Config keys:

```python
HA_URL = "http://homeassistant.local:8123"
HA_TOKEN = "..."
```

Create a long-lived access token from your Home Assistant user profile.

Docs: https://www.home-assistant.io/docs/authentication/

#### Google Calendar Through Home Assistant

Set up Google's integration in Home Assistant first. Home Assistant owns the
Google OAuth grant and exposes each selected calendar as a `calendar.*` entity;
Home Suite needs no Google client ID, refresh token, or additional secret. Add
the entity IDs to `CALENDARS` in `deployment_config.py`, then opt individual
calendars into combined agendas and writes. Reads use
`calendar.get_events`; confirmed writes use `calendar.create_event`.

Docs: https://www.home-assistant.io/integrations/google/

### OpenAI

What it enables:

* conversational fallback
* current-information web search when enabled
* summarization and interpretation
* media breadcrumb extraction for follow-up actions handled by deterministic routes

Config keys:

```python
OPENAI_API_KEY = "..."
```

Get an API key from: https://platform.openai.com/api-keys

Model selection lives in `app_config.py` defaults and can be overridden per device in `local_prefs.py`. Use a capable model for conversation and interpretation, then let Home Suite's deterministic natural-language routes perform the actual home actions.

Because routine control commands use the deterministic NLP layer first, model choice mostly affects open-ended conversation, summaries, and interpretation tasks rather than every light switch or media command.

### Open-Meteo

What it enables:

* named-place geocoding for weather, date/time, and location-distance questions
* weather fallback when Home Assistant cannot provide the requested forecast
* coordinates for local straight-line distance and compass-direction math

Open-Meteo requires network access but no account or API key. Named place text
is sent to its geocoding service. Distance and bearing calculations then run
locally; Open-Meteo does not provide Home Suite with driving routes, traffic,
or travel-time estimates.

### Astral

What it enables:

* local sunrise, sunset, civil dawn, and dusk calculations
* moonrise, moonset, lunar phase, and horizon-status questions
* network-free fallback for explicit-date solar schedules

Astral is a bundled Python calculation library, not a Home Assistant
integration or external account. It uses `HOME_LOCATION` from
`deployment_config.py` and requires no API key or network request. Home Suite
still prefers Home Assistant's current `sun.sun` and `sensor.moon_phase` state
when available.

### Skyfield

What it enables:

* local rise and set times for Mercury through Neptune
* apparent altitude and compass direction from the configured home location
* potential naked-eye planet visibility and best viewing times for a local night

Skyfield is a bundled Python calculation library, not an account or Home
Assistant integration. `skyfield-data` installs the JPL DE421 ephemeris with the
Python dependencies, and Home Suite opens that local file directly. It never
downloads ephemeris data while handling a command and requires no API key.
Visibility estimates cannot account for clouds, light pollution, buildings,
trees, or terrain, so spoken answers include a clear-sky and open-horizon
caveat.

### Alpaca Market Data

What it enables:

* current and multi-symbol stock quotes
* movement relative to the previous close
* completed-session close questions
* U.S. regular-market status and next open/close times

Config keys:

```python
ALPACA_API_KEY_ID = "..."
ALPACA_API_SECRET_KEY = "..."
```

For a personal Home Suite deployment, choose Alpaca's Trading API and create
keys in the paper-trading web dashboard. A free Paper Only account on the Basic
plan is enough; Broker API, a paid market-data plan, Alpaca's SDK, and terminal
setup are not required.

The default `STOCK_QUOTE_DATA_FEED = "iex"` works with Alpaca's Basic market
data plan. IEX is one exchange rather than the consolidated SIP feed, so Home
Suite documents that limitation rather than implying full-market coverage.
Deployments with appropriate market-data access can override the feed.

This is intentionally a market-data-only integration. Home Suite calls Alpaca's
stock snapshot and market-clock endpoints, keeps responses in a short bounded
cache, and never calls account, portfolio, position, or order endpoints. The
configured paper API base URL is used only for the clock endpoint.

Docs: [market data overview](https://docs.alpaca.markets/us/docs/about-market-data-api),
[stock snapshots](https://docs.alpaca.markets/us/reference/stocksnapshots-1), and
[authentication](https://docs.alpaca.markets/us/docs/authentication).

### Home Suite HTTP API

What it enables:

* Raycast, menu-bar, shell, satellite, or custom clients
* `POST /command`
* WebSocket clients

Config keys:

```python
HOMESUITE_HTTP_API_KEY = "choose-a-long-random-local-key"
PIPHONE_HTTP_API_KEY = HOMESUITE_HTTP_API_KEY
```

Use the same key in any client that calls Home Suite. Treat it like a local control token because a client with this key can send commands to your home.

The server is enabled by default but fails closed when the key is blank.
`/health` and `/healthz` are public monitoring aliases; all other routes,
including WebSocket state, require the same passphrase. Telegram is implemented
in-process and does not depend on this API. See [API.md](API.md) for the
complete contract.

## Choosing Home Assistant vs Direct APIs

Home Assistant is the portable baseline. If a service is already integrated there, Home Suite can often query it without another token.

Direct APIs are useful when:

* Home Assistant does not expose enough detail
* Home Suite needs an action Home Assistant does not provide
* the service has a read-only/status endpoint that is safer than an admin credential
* media matching needs access to the real library or account

Do not add direct credentials just because a service has an API. Start with the smallest setup that answers the commands you actually want.

## Media

### Plex

What it enables:

* `watch The Matrix`
* `watch the movie where people live in a simulation`
* `watch it` after an AI/media question

Config keys:

```python
PLEX_URL = "http://your-plex-host:32400"
PLEX_TOKEN = "..."
```

The URL should point to Plex Media Server, not the Plex web app.

Plex token docs: https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/

### Spotify

What it enables:

* music search by artist, album, track, or playlist
* saving the current song
* adding the current song to a playlist
* resolving music follow-ups from conversation

Config keys:

```python
SPOTIFY_CLIENT_ID = "..."
SPOTIFY_CLIENT_SECRET = "..."
SPOTIFY_REFRESH_TOKEN = "..."
SPOTIFY_DISCOVER_WEEKLY_URI = "spotify:playlist:..."  # optional
```

Create a Spotify app in the Spotify Developer Dashboard.

Docs: https://developer.spotify.com/documentation/web-api/concepts/apps

The refresh-token setup still needs more polish in this public-alpha release. You need scopes appropriate to playback, library, and playlist operations.

### YouTube

What it enables:

* YouTube lounge/digest features
* paired TV-style playback flows
* channel and playlist helpers

Config keys:

```python
YOUTUBE_OAUTH_CLIENT_ID = "..."
YOUTUBE_OAUTH_CLIENT_SECRET = "..."
YOUTUBE_OAUTH_REFRESH_TOKEN = "..."
```

Home Suite includes helper scripts:

```bash
homesuite-youtube-pair
homesuite-youtube-oauth
```

YouTube Data API docs: https://developers.google.com/youtube/v3/getting-started

## Homelab

### Uptime Kuma

What it enables:

* `service status`
* `is anything down?`
* summarized status from a read-only status page

Config keys:

```python
UPTIME_KUMA_URL = "http://your-kuma-host:3001"
UPTIME_KUMA_STATUS_PAGE_SLUG = "home"
```

Home Suite reads a public/read-only status page rather than storing an admin password. If your status page is `http://host:3001/status/home`, use `UPTIME_KUMA_URL = "http://host:3001"` and `UPTIME_KUMA_STATUS_PAGE_SLUG = "home"`.

Uptime Kuma status page docs: https://github.com/louislam/uptime-kuma/wiki/Status-Page

### qBittorrent

What it enables:

* active/completed torrent summaries
* download names
* pausing completed downloads

Config keys:

```python
QBITTORRENT_URL = "http://your-qbittorrent-host:8090"
QBITTORRENT_USERNAME = "..."
QBITTORRENT_PASSWORD = "..."
```

Use the Web UI username and password.

qBittorrent WebUI API docs: https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-%28qBittorrent-5.0%29

### Seerr

What it enables:

* media request status
* pending request summaries

Config keys:

```python
SEERR_URL = "http://your-seerr-host:5055"
SEERR_API_KEY = "..."
```

Use the API key from Seerr settings.

### Radarr, Sonarr, and Lidarr

What they enable:

* richer media status when direct APIs are wired in
* Home Assistant can also expose many of these states without direct credentials

Config keys:

```python
RADARR_URL = "http://your-radarr-host:7878"
RADARR_API_KEY = "..."
SONARR_URL = "http://your-sonarr-host:8989"
SONARR_API_KEY = "..."
LIDARR_URL = "http://your-lidarr-host:8686"
LIDARR_API_KEY = "..."
```

API keys are normally in each app's Settings area under security/general settings.

Servarr docs:

* https://wiki.servarr.com/radarr/settings
* https://wiki.servarr.com/sonarr/settings
* https://wiki.servarr.com/lidarr/settings

### Synology, Reolink, Speedtest, and Other Home Assistant Integrations

Home Suite prefers Home Assistant for broad status integrations when possible. If Synology, Reolink, Speedtest.net, or similar services are already integrated with Home Assistant, Home Suite can query the entities Home Assistant exposes without storing separate credentials.

Use sensible entity names in Home Assistant first. That makes Home Suite's natural-language processing easier to route and debug.

## Remote Text Access

### Telegram

What it enables:

* Telegram chat frontend for the same Home Suite command brain

Config keys:

```python
TELEGRAM_BOT_TOKEN = "..."
TELEGRAM_ALLOWED_USER_IDS = [123456789]
TELEGRAM_ALLOWED_CHAT_IDS = [123456789]
```

Create a bot with BotFather. Keep allowlists tight because this can control your home.

BotFather docs: https://core.telegram.org/bots/features#botfather

## Wake Word and Hardware

Wake-word behavior is configured per device and remains isolated from handset
PTT timing. The recommended current path is OpenWakeWord with a named microphone
profile and Realtime streaming transcription.

### OpenWakeWord

Install the optional engine in the project virtual environment:

```bash
cd ~/homesuite
source .venv/bin/activate
pip install openwakeword onnxruntime
```

Then configure the device in `local_prefs.py`:

```python
WAKEWORD_ENABLED = True
WAKEWORD_ENGINE = "openwakeword"
WAKEWORD_MODEL = "your_model_label"
WAKEWORD_MODEL_PATHS = [
    "/home/your-user/wake_models/your_model.onnx",
]
PTT_ENABLED = False
HANDSET_PRESENT = False
```

Use a named `AUDIO_INPUT_PROFILE` so microphone selection and gain survive
device-index changes and restarts. The complete setup, calibration, tuning,
Realtime STT, and troubleshooting procedure is in
[WAKEWORD.md](WAKEWORD.md).

### Porcupine

Porcupine remains available as a compatibility engine. Set its access key in
`private_config.py`:

Config keys:

```python
PVPORCUPINE_ACCESS_KEY = "..."
```

Device behavior belongs in `local_prefs.py`:

```python
WAKEWORD_ENABLED = True
WAKEWORD_ENGINE = "porcupine"
PTT_ENABLED = False
HANDSET_PRESENT = False
```

Start with text or capture-mode command tests before enabling live wake-word or
handset flows. Once routing works, add audio output, microphone capture, the
wake-word model, and any physical controls one layer at a time.

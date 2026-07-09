# Integrations

HomeSuite is designed so most integrations are optional. Configure the services you use, leave the rest blank, and run `homesuite-doctor` when you want a quick read on what is ready.

As a rule, start with Home Assistant integrations when they expose enough state and control. Add direct API credentials only when HomeSuite can do something meaningfully richer with them, such as qBittorrent download actions, Seerr request summaries, Plex library matching, or Spotify library operations.

## Core

### Home Assistant

What it enables:

* device state and control
* rooms, areas, scenes, and scripts
* media players and many homelab integrations

Config keys:

```python
HA_URL = "http://homeassistant.local:8123"
HA_TOKEN = "..."
```

Create a long-lived access token from your Home Assistant user profile.

Docs: https://www.home-assistant.io/docs/authentication/

### OpenAI

What it enables:

* conversational fallback
* summarization and interpretation
* media breadcrumb extraction for follow-up actions handled by deterministic routes

Config keys:

```python
OPENAI_API_KEY = "..."
```

Get an API key from: https://platform.openai.com/api-keys

Model selection lives in `app_config.py` defaults and can be overridden per device in `local_prefs.py`. Use a capable model for conversation and interpretation, then let HomeSuite's deterministic natural-language routes perform the actual home actions.

Because routine control commands use the deterministic NLP layer first, model choice mostly affects open-ended conversation, summaries, and interpretation tasks rather than every light switch or media command.

### HomeSuite HTTP API

What it enables:

* Raycast, menu-bar, shell, satellite, or custom clients
* `POST /command`
* WebSocket clients

Config keys:

```python
HOMESUITE_HTTP_API_KEY = "choose-a-long-random-local-key"
PIPHONE_HTTP_API_KEY = HOMESUITE_HTTP_API_KEY
```

Use the same key in any client that calls HomeSuite. Treat it like a local control token because a client with this key can send commands to your home.

## Choosing Home Assistant vs Direct APIs

Home Assistant is the portable baseline. If a service is already integrated there, HomeSuite can often query it without another token.

Direct APIs are useful when:

* Home Assistant does not expose enough detail
* HomeSuite needs an action Home Assistant does not provide
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

HomeSuite includes helper scripts:

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

HomeSuite reads a public/read-only status page rather than storing an admin password. If your status page is `http://host:3001/status/home`, use `UPTIME_KUMA_URL = "http://host:3001"` and `UPTIME_KUMA_STATUS_PAGE_SLUG = "home"`.

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

HomeSuite prefers Home Assistant for broad status integrations when possible. If Synology, Reolink, Speedtest.net, or similar services are already integrated with Home Assistant, HomeSuite can query the entities Home Assistant exposes without storing separate credentials.

Use sensible entity names in Home Assistant first. That makes HomeSuite's natural-language processing easier to route and debug.

## Remote Text Access

### Telegram

What it enables:

* Telegram chat frontend for the same HomeSuite command brain

Config keys:

```python
TELEGRAM_BOT_TOKEN = "..."
TELEGRAM_ALLOWED_USER_IDS = [123456789]
TELEGRAM_ALLOWED_CHAT_IDS = [123456789]
```

Create a bot with BotFather. Keep allowlists tight because this can control your home.

BotFather docs: https://core.telegram.org/bots/features#botfather

## Wake Word and Hardware

### Porcupine

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

Hardware and audio setup varies by device. Start with text or capture-mode command tests before enabling live wake-word or handset flows. Once the text path works, add audio routing, microphone input, wake word, and any physical controls one layer at a time.

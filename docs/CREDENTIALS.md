# Credentials and Connected Services

Home Suite can start with most optional integrations left blank. Add credentials
only for the services and commands you intend to use.

This guide explains how to obtain and protect credentials. For setting names
and behavior, use [CONFIGURATION.md](CONFIGURATION.md); for what each connected
service does, use [INTEGRATIONS.md](INTEGRATIONS.md).

This guide distinguishes four setup types:

* **API account** - create a developer/platform account and usually enable billing.
* **OAuth app** - register an app, sign in as yourself, and grant scopes.
* **Existing-service credential** - copy a token or password from a service you already run.
* **Locally generated key** - create your own random value; no outside account is involved.

Store shared secrets in `private_config.py`. Store device-specific audio,
wakeword, room, and hardware settings in `local_prefs.py`. Both real files are
gitignored; only their `.example.py` templates should be committed.

## At a Glance

| Integration | Setup type | Account or subscription | What it unlocks |
|---|---|---|---|
| OpenAI | API account and API billing | OpenAI API platform account; ChatGPT billing is separate | Current STT modes, conversation, web search, fuzzy interpretation |
| Home Assistant | Existing-service token | Home Assistant user | Home state, service calls, rooms, scenes, media players |
| Home Suite HTTP API | Locally generated key | None | Satellites, Raycast, menu-bar and custom clients |
| Plex | Existing-service token | Plex account and Plex Media Server | Library-grounded matching and playback |
| Spotify | OAuth developer app | Spotify Premium currently required for development-mode apps | Search, private playlists, library and playlist operations |
| Telegram | Bot token plus allowlists | Telegram account | Remote text frontend |
| YouTube Lounge | TV pairing | YouTube app on the target TV | TV playback control; no Google Cloud project required |
| YouTube Data API | OAuth developer app | Google account and Cloud project | Playlist, reel, channel and duration features |
| qBittorrent | Existing WebUI login | Running qBittorrent WebUI | Torrent details and supported actions |
| Seerr | Existing-service API key | Running Seerr instance | Request status and summaries |
| Uptime Kuma | Public status-page slug | Running Uptime Kuma instance | Read-only service health without an admin password |
| OpenWakeWord | Local model files | None | Recommended local wakeword detection |
| Porcupine | Vendor access key | Free Picovoice Console account for initial use | Alternative local wakeword engine |

Radarr, Sonarr, Lidarr, Synology, Reolink, Speedtest, Sonos, and Apple TV are
currently best exposed through Home Assistant. Home Suite does not need their
separate credentials when Home Assistant already provides the entities and
services it uses.

## Create the Private Config

For a manual installation:

```bash
cd ~/homesuite
cp private_config.example.py private_config.py
cp deployment_config.example.py deployment_config.py
cp local_prefs.example.py local_prefs.py
chmod 600 private_config.py deployment_config.py local_prefs.py
```

Leave unused optional values as empty strings or empty lists. Do not enter fake
placeholder URLs; a blank value means "intentionally not configured" to Home
Suite and `homesuite-doctor`.

After making changes:

```bash
homesuite-doctor
homesuite-doctor --live
```

The live doctor makes bounded requests to configured services. It should be run
on the Home Suite device, where local hostnames and private network addresses are
reachable.

## Core Services

### OpenAI

Set:

```python
OPENAI_API_KEY = "sk-..."
```

1. Sign in to the [OpenAI API platform](https://platform.openai.com/).
2. Configure API billing or prepaid credits in the API platform.
3. Create a key on the [API keys page](https://platform.openai.com/api-keys).
4. Store the key only in the ignored `private_config.py` file or the
   `OPENAI_API_KEY` environment variable. Never commit a real key. If a key is
   ever committed, rotate it in the OpenAI dashboard even after removing the
   file from the current Git revision because it remains present in repository
   history.

A ChatGPT Free, Plus, Pro, Business, or other ChatGPT subscription does **not**
include OpenAI API usage. ChatGPT and API billing are managed separately. See
OpenAI's [API billing explanation](https://help.openai.com/en/articles/8156019-how-can-i-move-my-chatgpt-subscription-to-the-api)
and [developer quickstart](https://platform.openai.com/docs/quickstart).

Home Suite currently uses this one key for:

* OpenAI Realtime streaming transcription
* file transcription through hosted `whisper-1`
* conversational fallback
* hosted web search for current conversational questions when enabled
* fuzzy media and color interpretation where enabled

The current realtime and file-transcription paths send captured command audio
to OpenAI. Conversational fallback and web search send the relevant transcript
and short-lived conversational context. Review that data flow before enabling
voice or AI features in a shared space.

Important: `PIPHONE_STT_MODE=whisper` means the hosted OpenAI Whisper API. It is
not a local Whisper process. All currently implemented STT modes require
`OPENAI_API_KEY` and internet access.

Current alternatives:

* There is no configuration-only local STT replacement today. Local Whisper,
  Wyoming, Vosk, or another provider would require an STT adapter/code change.
* OpenAI text-to-speech is not currently used. The OpenAI key does not control
  Home Suite's output voice.
* There is no configuration-only alternate conversational model provider today.

Review current transcription models and request formats in OpenAI's
[Audio API reference](https://platform.openai.com/docs/api-reference/audio).

### Home Assistant

Set:

```python
HA_URL = "http://homeassistant.local:8123"
HA_TOKEN = "..."
```

No developer account is required. Use a Home Assistant user on your own server:

1. Open the user's Home Assistant profile.
2. Find **Long-Lived Access Tokens** near the bottom.
3. Create a token named `Home Suite`.
4. Copy it immediately; Home Assistant does not show the token again.

Home Assistant documents long-lived tokens in its
[Authentication API guide](https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token).
The token acts with that user's permissions. A dedicated Home Suite user with
only the access your deployment needs is preferable when practical.

Home Assistant is the broadest credential-saving alternative in this project.
If it already exposes Sonos, Apple TV, Synology, Reolink, Radarr, Sonarr, Lidarr,
weather, or another service, Home Suite can often use those entities without a
second direct API credential.

### Home Suite HTTP and WebSocket API

Set:

```python
HOMESUITE_HTTP_API_KEY = "a-long-random-value"
PIPHONE_HTTP_API_KEY = HOMESUITE_HTTP_API_KEY
```

This is not obtained from a provider. Generate it yourself:

```bash
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Give the same value to trusted satellites and clients that call Home Suite.
Anyone holding it can submit commands to your home, so do not embed it in a
public client repository or browser bundle.

The server is enabled by default and requires this key for every route except
the `/health` and `/healthz` monitoring aliases. WebSocket clients use the same
key; there is no separate WebSocket passphrase. See [API.md](API.md) for header
and browser-client authentication.

## Speech Output

### Local gTTS

Local speech currently uses the Python `gTTS` package. It does **not** require a
Google Cloud project, developer account, API key, or service-account JSON.
It does require internet access because it sends text to Google's translation
speech service. Configure language and regional voice behavior with:

```python
TTS_LANGUAGE = "en"
TTS_TLD = "ie"
```

See the [gTTS project documentation](https://gtts.readthedocs.io/).

This should not be confused with the paid Google Cloud Text-to-Speech API. Home
Suite does not currently use Google Cloud TTS credentials.

### Home Assistant TTS for Sonos

For Sonos output, Home Suite can ask Home Assistant to speak instead of creating
a gTTS MP3:

```python
ASSISTANT_AUDIO_OUTPUT_MODE = "sonos"
SONOS_TTS_BACKEND = "home_assistant"
SONOS_HA_TTS_ENTITY = "tts.google_en_com"
```

This uses `HA_TOKEN`; no additional credential is stored in Home Suite. The
selected TTS integration may have its own setup requirements inside Home
Assistant.

A local Piper backend is not wired into the current runtime. Historical Piper
experiments under `archive/` should not be treated as a supported config option.

## Media Services

### Plex

Set:

```python
PLEX_URL = "http://plex.local:32400"
PLEX_TOKEN = "..."
```

You need a Plex account connected to the Plex Media Server you want to control,
but you do not need to register a developer app. Follow Plex's
[X-Plex-Token guide](https://support.plex.tv/articles/204059436-finding-an-authentication-token-x-plex-token/)
and use the server's base URL, not the Plex Web URL.

Treat the token like an account credential. Plex notes that the simple Web App
lookup can yield a temporary token; long-running or multi-user software should
follow Plex's fuller third-party authentication flow.

### Spotify

Set:

```python
SPOTIFY_CLIENT_ID = "..."
SPOTIFY_CLIENT_SECRET = "..."
SPOTIFY_REFRESH_TOKEN = "..."
SPOTIFY_DISCOVER_WEEKLY_URI = "spotify:playlist:..."  # optional
```

Spotify is OAuth, not a password login and not a single API key.

1. Sign in to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard).
2. Create an app and select the Web API.
3. Record its Client ID and Client Secret.
4. Register an exact redirect URI, such as `http://127.0.0.1:8888/callback`.
   Spotify permits HTTP for loopback IP literals but does not permit `localhost`.
5. Run an OAuth **Authorization Code** flow, sign in as the Spotify user, and
   approve the needed scopes.
6. Exchange the returned code for tokens and retain the refresh token. Home
   Suite refreshes short-lived access tokens automatically.

Spotify recommends Authorization Code for a long-running server that can keep a
client secret. See Spotify's [authorization overview](https://developer.spotify.com/documentation/web-api/concepts/authorization),
[app setup](https://developer.spotify.com/documentation/web-api/concepts/apps),
and [redirect URI rules](https://developer.spotify.com/documentation/web-api/concepts/redirect_uri).

Scopes should cover the features you plan to use. Current Home Suite operations
may need:

```text
playlist-read-private
user-read-currently-playing
user-read-playback-state
user-library-modify
playlist-modify-private
playlist-modify-public
```

You can omit mutation scopes if you only want search and playback resolution.
Home Suite currently has no first-party Spotify OAuth pairing helper, so obtain
the refresh token with your own trusted OAuth helper.

As of Spotify's February/March 2026 development-mode changes:

* the app owner must have Spotify Premium;
* development-mode apps are limited to five authenticated users;
* additional users must be added to the app allowlist;
* several library and playlist endpoint names changed.

See Spotify's [quota-mode documentation](https://developer.spotify.com/documentation/web-api/concepts/quota-modes)
and [2026 migration guide](https://developer.spotify.com/documentation/web-api/tutorials/february-2026-migration-guide).
Home Suite's current save-track and add-to-playlist code still uses the older
endpoint names. Test those two mutations against a new development-mode app;
search, resolution, and Home Assistant/Sonos playback are separate paths.

`SPOTIFY_DISCOVER_WEEKLY_URI` is not a credential. Copy the playlist's Spotify
URI when you want a deterministic shortcut rather than account search.

### YouTube: TV Pairing vs Data API

Home Suite uses two independent YouTube connections.

#### Lounge pairing for TV playback

This requires no Google Cloud project, OAuth client, or API key. On the target
TV's YouTube app, open **Settings > Link with TV code**, then run:

```bash
homesuite-youtube-pair
```

The resulting lounge credential is stored in the gitignored
`state/youtube_lounge.json`. Pairing can play and control the TV app but does not
grant YouTube Data API access.

#### YouTube Data API OAuth

This enables persistent playlists, reel/roundup management, channel metadata,
and video durations.

1. Create or select a project in [Google Cloud Console](https://console.cloud.google.com/).
2. Enable **YouTube Data API v3**.
3. Configure the OAuth consent screen/test users as required by Google.
4. Create an OAuth client for **TV and Limited Input devices**.
5. Download its JSON to `state/youtube_oauth_client.json`, or copy the client ID
   and secret into `private_config.py`.
6. Run `homesuite-youtube-oauth`, visit the displayed URL, enter the code, and
   approve access.
7. Keep the generated `state/youtube_oauth.json`, or copy the printed refresh
   token into `YOUTUBE_OAUTH_REFRESH_TOKEN` for another trusted Home Suite node.

Home Suite requests the broad `https://www.googleapis.com/auth/youtube` scope
because it manages playlists. Google's
[credential guide](https://developers.google.com/youtube/registering_an_application)
and [device/installed-app OAuth guide](https://developers.google.com/youtube/v3/guides/auth/installed-apps)
explain the project, consent, and refresh-token lifecycle.

### Sonos and Apple TV

Home Suite currently controls these primarily through Home Assistant. Configure
the corresponding Home Assistant integrations and entity mappings; there is no
separate Sonos or Apple API credential in `private_config.py`.

## Remote Text Access

### Telegram

Set:

```python
TELEGRAM_BOT_TOKEN = "..."
TELEGRAM_ALLOWED_USER_IDS = [123456789]
TELEGRAM_ALLOWED_CHAT_IDS = [123456789]
```

1. Open a conversation with Telegram's official `@BotFather`.
2. Run `/newbot` and follow the prompts.
3. Copy the generated bot token.
4. Send a message to the new bot.
5. Before starting another long-polling bot process, call `getUpdates` and read
   `message.from.id` and `message.chat.id` from the response.

Example:

```bash
export TELEGRAM_BOT_TOKEN='replace-me'
curl -sS "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getUpdates"
unset TELEGRAM_BOT_TOKEN
```

Telegram's [BotFather guide](https://core.telegram.org/bots/features#botfather)
explains bot creation. Keep at least one allowlist populated. A leaked bot token
or overly broad allowlist can expose a remote home-control surface.

## Homelab Services

### qBittorrent

Set:

```python
QBITTORRENT_URL = "http://qbittorrent.local:8090"
QBITTORRENT_USERNAME = "..."
QBITTORRENT_PASSWORD = "..."
```

Use the credentials configured for qBittorrent's WebUI. No developer account is
needed. Home Suite logs in through qBittorrent's cookie-based WebUI API. Review
the official [WebUI API documentation](https://github.com/qbittorrent/qBittorrent/wiki/WebUI-API-%28qBittorrent-5.0%29).
Restrict the WebUI to trusted networks and use a dedicated strong password.

### Seerr

Set:

```python
SEERR_URL = "http://seerr.local:5055"
SEERR_API_KEY = "..."
```

Copy the API key from your Seerr instance's settings. No separate developer
account is needed. The key permits direct request-status queries, so protect it
like an application credential.

### Uptime Kuma

Set:

```python
UPTIME_KUMA_URL = "http://uptime-kuma.local:3001"
UPTIME_KUMA_STATUS_PAGE_SLUG = "home"
```

These values are not secrets. Home Suite reads the public/read-only status-page
API and intentionally does not store an Uptime Kuma administrator password. If
the page is `/status/home`, the slug is `home`.

See Uptime Kuma's [status-page documentation](https://github.com/louislam/uptime-kuma/wiki/Status-Page).
Do not expose private monitor details on a public status page merely to make them
available to Home Suite.

### Radarr, Sonarr, and Lidarr

`private_config.example.py` contains URL/API-key placeholders for these services,
but the current production command path does not use direct clients for them.
Configure their Home Assistant integrations and map the resulting entities in
`HOMELAB_SERVICES` instead. Adding only `RADARR_API_KEY`, `SONARR_API_KEY`, or
`LIDARR_API_KEY` does not currently unlock additional Home Suite behavior.

When a future direct client uses them, API keys are available in each Servarr
application's Settings area. Never expose those APIs directly to the internet.

## Wakeword Engines

Wakeword engine settings and model paths belong in `local_prefs.py`; only vendor
access keys belong in `private_config.py`.

### OpenWakeWord: recommended, local, no key

OpenWakeWord runs model inference on the Home Suite device. It does not require
an account, cloud API, subscription, or credential. Install the optional
packages, provide compatible local `.onnx` model paths, and calibrate the mic as
described in [WAKEWORD.md](WAKEWORD.md).

Project: [OpenWakeWord on GitHub](https://github.com/dscripka/openWakeWord)

OpenWakeWord detection is local, but command transcription still uses the
configured STT service. A keyless wakeword does not make the complete voice
pipeline offline.

### Porcupine: alternative engine, vendor key required

Set:

```python
PVPORCUPINE_ACCESS_KEY = "..."
```

Create a Picovoice Console account, copy the AccessKey from its home page, and
keep it secret. Picovoice currently describes initial signup as free with no
credit card, subject to account limits. Custom `.ppn` wakeword models are trained
for a specific platform in the console.

See the [Porcupine setup guide](https://picovoice.ai/docs/porcupine/).

## Services That Need No Additional Key

Depending on configuration, these paths can work without another credential:

* gTTS local speech: no key, but internet required.
* OpenWakeWord detection: local model, no key.
* YouTube Lounge TV pairing: one-time TV code, no developer project.
* Uptime Kuma public status page: base URL and slug only.
* Open-Meteo weather and geocoding: no key in the current implementation.
* Sonos, Apple TV, Synology, Reolink, Speedtest and many media/homelab devices:
  use the existing Home Assistant token when exposed through HA.

## Security and Rotation

* Never commit `private_config.py`, `local_prefs.py`, OAuth JSON, or state tokens.
* Use separate service users/apps where practical and grant only needed scopes.
* Keep Home Suite and service APIs on a trusted network or behind a properly
  authenticated private access layer.
* Do not put secrets in command-line arguments that remain in shell history.
* Rotate any credential that appears in logs, screenshots, chat, or Git history.
* Revoking an OAuth app/token is different from changing the account password;
  use the provider's connected-app or developer dashboard when possible.
* Back up refresh tokens securely. Losing one may require repeating user consent.

Finish with:

```bash
homesuite-doctor
homesuite-doctor --live
```

A configured credential does not guarantee that the target entity, room, media
player, network route, or provider endpoint is correct. The live doctor verifies
several core connections; use service logs and focused commands for the rest.

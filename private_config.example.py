"""Example private deployment config for Home Suite deployments.

Copy this file to private_config.py and fill in values for the services you use.
Leave unused optional values as empty strings or empty lists.
"""

# Core services
OPENAI_API_KEY = ""
HA_URL = "http://homeassistant.local:8123"
HA_TOKEN = ""

# Home Suite HTTP and WebSocket API
HOMESUITE_HTTP_API_KEY = ""
# Legacy alias accepted by older deployments and clients.
PIPHONE_HTTP_API_KEY = HOMESUITE_HTTP_API_KEY

# Plex
PLEX_URL = ""
PLEX_TOKEN = ""

# Spotify
SPOTIFY_CLIENT_ID = ""
SPOTIFY_CLIENT_SECRET = ""
SPOTIFY_REFRESH_TOKEN = ""
SPOTIFY_DISCOVER_WEEKLY_URI = ""

# Telegram
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_ALLOWED_USER_IDS = []
TELEGRAM_ALLOWED_CHAT_IDS = []

# Wake word engines
PVPORCUPINE_ACCESS_KEY = ""

# YouTube Data API OAuth
YOUTUBE_OAUTH_CLIENT_ID = ""
YOUTUBE_OAUTH_CLIENT_SECRET = ""
YOUTUBE_OAUTH_REFRESH_TOKEN = ""

# Direct homelab service APIs
QBITTORRENT_URL = ''
QBITTORRENT_USERNAME = ''
QBITTORRENT_PASSWORD = ''
SEERR_URL = ''
SEERR_API_KEY = ''
RADARR_URL = ''
RADARR_API_KEY = ''
SONARR_URL = ''
SONARR_API_KEY = ''
LIDARR_URL = ''
LIDARR_API_KEY = ''


# Uptime Kuma read-only status page
UPTIME_KUMA_URL = ''
UPTIME_KUMA_STATUS_PAGE_SLUG = ''

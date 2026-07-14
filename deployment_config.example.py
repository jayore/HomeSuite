"""Example shared, non-secret Home Suite deployment configuration.

Fresh public installs copy this file to the ignored ``deployment_config.py``.
Put room topology and other settings shared by every Home Suite device here.
Keep credentials in ``private_config.py`` and hardware/audio choices for one
device in ``local_prefs.py``.

Replace the generic room below with your Home Assistant area and targets. The
sample deliberately enables only area-based room brightness.
"""

DEFAULT_ROOM = "living_room"

ROOMS = {
    "living_room": {
        "label": "Living Room",
        "ha_area_id": "living_room",
        "aliases": ["living room", "lounge"],
        "defaults": {
            "brightness_target": {"type": "area"},
            "color_light": None,
            "volume_target": None,
            "audio_output": None,
            "announcements": None,
            "spotcast_device_name": None,
            "tv": None,
            "tv_remote": None,
            "tv_on_scene": None,
            "plex_client_name": None,
            "plex_launch_script": None,
        },
        "media_players": [],
        "audio_outputs": [],
        "audio_aliases": {},
        "focus_participants": [],
        "scenes": [],
        "devices": [],
    },
}

# Set a specific weather.* entity when multiple providers exist, or leave None
# to auto-discover one with a current temperature.
WEATHER_ENTITY_ID = None

# Open-Meteo fallback for weather, straight-line location distance, and local
# astronomy calculations. Use an IANA timezone name, or None to use the host
# timezone. Set either coordinate to None to disable coordinate-based features.
HOME_LOCATION = {
    # Optional coarse fields are available to conversational answers and may
    # be sent to the configured AI provider. Exact coordinates stay local.
    "city": None,
    "region": None,
    "country": None,  # Two-letter ISO code, for example "US".
    "latitude": None,
    "longitude": None,
    "timezone": None,
}

# Optional persistent context for conversational answers. Do not put secrets,
# access codes, precise addresses, or other sensitive data here. Shared room
# microphones cannot identify which household member is speaking.
ASSISTANT_PROFILE = {
    "preferred_name": "",
    "locale": "",
    "units": "",
    "notes": [],
}

# Optional Skyfield criteria for potential naked-eye visibility. Named queries
# for Uranus and Neptune still work even though they are not listed here.
PLANET_VISIBILITY_PLANETS = ("mercury", "venus", "mars", "jupiter", "saturn")
PLANET_VISIBILITY_MIN_ALTITUDE_DEGREES = 10.0
PLANET_VISIBILITY_MAX_SUN_ALTITUDE_DEGREES = -6.0
PLANET_VISIBILITY_MAX_MAGNITUDE = 6.0
PLANET_VISIBILITY_MIN_DURATION_MINUTES = 15

# Shared read-only stock behavior. The free Alpaca Basic plan supports IEX;
# select another feed only when the configured account has that entitlement.
STOCK_QUOTE_DATA_FEED = "iex"
STOCK_QUOTE_MAX_SYMBOLS = 5
STOCK_QUOTE_CACHE_SECONDS = 15.0
STOCK_MARKET_CLOCK_CACHE_SECONDS = 30.0

# Common stock/company aliases ship in app_config.py. These maps extend that
# catalog with deployment-specific spoken names and response labels.
STOCK_SYMBOL_ALIAS_OVERRIDES = {}
STOCK_SYMBOL_LABEL_OVERRIDES = {}

LOCATION_ALIASES = {}
ENTITY_LABEL_OVERRIDES = {}

# Optional helper/proxy entities to omit from aggregate state summaries and
# whole-home bulk actions. Explicit named commands remain available.
ASSISTANT_BULK_EXCLUDED_ENTITY_IDS = []
ASSISTANT_BULK_EXCLUDED_ENTITY_PATTERNS = [
    "light.*_flicker",
    "light.*_underwater",
    "light.*scene_trigger*",
    "light.virtual_rgb_*",
    "light.*_status_led",
]

# Home-specific catalogs are empty on a fresh install. Add only the aliases,
# pinned media, channels, and Home Assistant entities your deployment uses.
PINNED_SPOTIFY_PLAYLISTS = {}
PINNED_RADIO_STATIONS = {}
PHONETIC_DEVICE_REPAIRS = {}
HA_TRIGGER_ALIASES = {}
HA_DEVICE_ALIASES = {}
YOUTUBE_CHANNELS = {}
YOUTUBE_REEL_REFRESH_ENABLED = False
TTS_PRONUNCIATION_OVERRIDES = {}
HOMELAB_SERVICES = {}

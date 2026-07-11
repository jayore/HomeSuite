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

# Used only when Home Assistant does not expose a local weather entity.
HOME_LOCATION = {
    "latitude": None,
    "longitude": None,
}

LOCATION_ALIASES = {}
ENTITY_LABEL_OVERRIDES = {}

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

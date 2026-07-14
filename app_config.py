"""Tracked HomeSuite behavior defaults and reference deployment mappings.

Fresh public installs override home-specific topology and catalogs in the
ignored ``deployment_config.py`` file. Machine-specific audio, wakeword, and
hardware choices belong in ``local_prefs.py``; credentials belong in
``private_config.py``. Existing private deployments may continue using the
reference mappings here until they deliberately migrate.

Dictionary keys representing spoken phrases should use ``_norm_key``. Values
that name external library objects, such as Plex titles, must preserve the
exact spelling expected by that service.
"""

import re
from typing import Any, Dict

# =============================
# Normalizers / helpers
# =============================

def _norm_key(s: str) -> str:
    """Normalize a spoken-phrase key for prefs maps (case/space/punctuation tolerant)."""
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _norm_ordinal_key(s: str) -> str:
    """Normalize a franchise/collection key used for ordinal-related lookups."""
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


# =============================
# Home topology
# =============================
# These reference defaults preserve the original deployment. Fresh public
# installs replace them from deployment_config.py. Runtime modules consume the
# effective values through home_registry helpers instead of carrying their own
# room/entity fallbacks.
#
# Per-room defaults currently understood by the runtime include:
#   brightness_target: room brightness strategy (entity, area, or entities)
#   color_light:        entity used by "set color" room shorthand
#   volume_target:      entity used by room volume commands
#   audio_output:       primary media_player for music/announcements
#   spotcast_device_name: provider device name expected by Spotcast
#   tv/tv_remote/...:   room-local television and Plex routing
#
# Use None to explicitly disable an optional scalar/target, [] for an optional
# list, and {} for an optional mapping. Avoid empty strings. Omitting
# brightness_target or volume_target may activate legacy compatibility
# fallbacks, so set either field to None when the capability should be off.
#
# Which room a client (menubar, raycast, etc.) defaults to when the user
# has not picked one explicitly.
DEFAULT_ROOM: str = "living_room"

# Schema version exposed in the manifest. Bump when the shape changes in a
# way clients need to adapt to (additive fields don't require a bump).
MANIFEST_SCHEMA_VERSION: int = 1


ROOMS: Dict[str, Dict[str, Any]] = {
    "living_room": {
        "label": "Living Room",
        "ha_area_id": "living_room",
        "aliases": ["living room"],
        "defaults": {
            "lights": "light.living_room_brightness",
            "color_light": "light.living_room_color",
            "brightness_target": {
                "type": "entity",
                "entity_id": "light.living_room_brightness",
            },
            "volume_target": {
                "type": "entity",
                "entity_id": "number.living_room_volume",
            },
            "audio_output": "media_player.living_room",
            "announcements": "media_player.living_room",
            "spotcast_device_name": "Livingroom",
            "spotcast_device_aliases": ["sonos"],
            "tv": "media_player.living_room_apple_tv",
            "tv_remote": "remote.living_room_apple_tv",
            "tv_on_scene": "scene.tv_on",
            "plex_client_name": "Apple TV",
            "plex_launch_script": "script.launch_plex",
        },
        "media_players": [
            {"entity": "media_player.living_room",          "label": "Living Room"},
            {"entity": "media_player.living_room_apple_tv", "label": "Apple TV"},
            {"entity": "media_player.bookshelf",            "label": "Bookshelf"},
        ],
        "audio_outputs": [
            "media_player.living_room",
            "media_player.bookshelf",
        ],
        "audio_aliases": {
            "bookshelf": "media_player.bookshelf",
        },
        "focus_participants": [
            "media_player.living_room",
            "media_player.bookshelf",
        ],
        # Buttons surfaced in client UIs (menubar, raycast, etc.).
        # Each entry is one of:
        #   {"label": "X", "command": "<NL phrase>"}   → POST to PiPhone /command
        #   {"label": "X", "scene":   "scene.X"}       → direct HA scene.turn_on
        #   {"label": "X", "script":  "script.X"}      → direct HA script.turn_on
        # Mix and match freely.
        "scenes": [
            {"label": "Bright",       "command": "living room bright"},
            {"label": "Medium",       "command": "living room medium"},
            {"label": "Low",          "command": "living room low"},
            {"label": "Dim",          "command": "living room dim"},
            {"label": "Off",          "command": "living room off"},
            {"label": "Stair Light",  "command": "toggle stair light"},
            {"label": "Dining Light", "command": "toggle dining light"},
        ],
        # Devices listed in client UIs with their live state.
        # Each entry: {"label": "X", "entity": "<domain.entity_id>"}
        # Click toggles the entity via HA directly (light.toggle, switch.toggle,
        # lock.lock/unlock, etc.) — domain is inferred from the entity prefix.
        # Add devices you actually want to see at a glance; leave the list empty
        # if you don't want a device section for this room.
        "devices": [
            # Example shapes:
            # {"label": "Stair Light", "entity": "light.stair_light"},
            # {"label": "Side Lamp",   "entity": "light.side_lamp"},
            # {"label": "Front Door",  "entity": "lock.front_door"},
        ],
    },
    "bedroom": {
        "label": "Bedroom",
        "ha_area_id": "bedroom",
        "aliases": ["bedroom"],
        "defaults": {
            "lights": None,
            "color_light": "light.bedroom_color",
            "brightness_target": {
                "type": "entity",
                "entity_id": "light.bedroom_brightness",
            },
            "volume_target": {
                "type": "entity",
                "entity_id": "media_player.bedroom",
            },
            "audio_output": "media_player.bedroom",
            "announcements": "media_player.bedroom",
            "spotcast_device_name": None,
            "tv": None,
        },
        "audio_outputs": [
            "media_player.bedroom",
        ],
        "focus_participants": [
            "media_player.bedroom",
        ],
        "scenes": [
            {"label": "Bright", "command": "bedroom bright"},
            {"label": "Medium", "command": "bedroom medium"},
            {"label": "Low",    "command": "bedroom low"},
            {"label": "Dim",    "command": "bedroom dim"},
            {"label": "Off",    "command": "bedroom off"},
        ],
        "devices": [],
    },
    "kitchen": {
        "label": "Kitchen",
        "ha_area_id": "kitchen",
        "aliases": ["kitchen"],
        "defaults": {
            "lights": None,
            "color_light": None,
            "brightness_target": {
                "type": "entity",
                "entity_id": "light.kitchen_brightness",
            },
            "volume_target": {
                "type": "entity",
                "entity_id": "media_player.kitchen",
            },
            "audio_output": "media_player.kitchen",
            "announcements": "media_player.kitchen",
            "spotcast_device_name": None,
            "tv": None,
        },
        "audio_outputs": [
            "media_player.kitchen",
        ],
        "focus_participants": [
            "media_player.kitchen",
        ],
        "scenes": [
            {"label": "Bright",       "command": "kitchen bright"},
            {"label": "Medium",       "command": "kitchen medium"},
            {"label": "Low",          "command": "kitchen low"},
            {"label": "Dim",          "command": "kitchen dim"},
            {"label": "Off",          "command": "kitchen off"},
            {"label": "Stair Light",  "command": "toggle stair light"},
            {"label": "Dining Light", "command": "toggle dining light"},
        ],
        "devices": [],
    },
    "bathroom": {
        "label": "Bathroom",
        "ha_area_id": "bathroom",
        "aliases": ["bathroom"],
        "defaults": {
            "lights": None,
            "color_light": None,
            "brightness_target": None,
            "volume_target": {
                "type": "entity",
                "entity_id": "media_player.bathroom",
            },
            "audio_output": "media_player.bathroom",
            "announcements": "media_player.bathroom",
            "spotcast_device_name": None,
            "tv": None,
        },
        "audio_outputs": [
            "media_player.bathroom",
        ],
        "focus_participants": [
            "media_player.bathroom",
        ],
        "scenes": [
            {"label": "Bright", "command": "bathroom bright"},
            {"label": "Medium", "command": "bathroom medium"},
            {"label": "Low",    "command": "bathroom low"},
            {"label": "Dim",    "command": "bathroom dim"},
            {"label": "Off",    "command": "bathroom off"},
        ],
        "devices": [],
    },
    "office": {
        "label": "Office",
        "ha_area_id": "office",
        "aliases": ["office"],
        "defaults": {
            "lights": None,
            "color_light": "light.office_color",
            "brightness_target": {
                "type": "entity",
                "entity_id": "light.office_brightness",
            },
            "volume_target": {
                "type": "entity",
                "entity_id": "media_player.office",
            },
            "audio_output": "media_player.office",
            "announcements": "media_player.office",
            "spotcast_device_name": None,
            "tv": None,
        },
        "audio_outputs": [
            "media_player.office",
        ],
        "focus_participants": [
            "media_player.office",
        ],
        "scenes": [
            {"label": "Bright", "command": "office bright"},
            {"label": "Medium", "command": "office medium"},
            {"label": "Low",    "command": "office low"},
            {"label": "Dim",    "command": "office dim"},
            {"label": "Off",    "command": "office off"},
        ],
        "devices": [],
    },
}



SOURCES: Dict[str, Dict[str, Any]] = {
    # Local/default appliance source for the current PiPhone runtime.
    #
    # `mobile`: whether the source can change its own room focus at runtime via
    # an "I'm in the <room>" command. Stationary devices (the handset, physical
    # buttons, the out-loud wakeword option) are fixed to their room and must
    # refuse room changes. Portable frontends (the menubar app, Raycast,
    # Telegram) are mobile and remember a sticky room per `device_group`/id.
    "default_piphone": {
        "label": "Default PiPhone",
        "type": "piphone",
        "room": None,
        "inherit_default_room": True,
        "mobile": False,
        "default_scope": "room_local",
        "focus_policy": "sticky",
        "output_mode": "inherit_room",
    },

    # Room-agnostic sources.
    "telegram": {
        "label": "Telegram",
        "type": "telegram",
        "room": None,
        "mobile": True,
        "default_scope": "none",
        "focus_policy": "sticky_recent_room",
        "output_mode": "none",
    },
    "http": {
        "label": "HTTP",
        "type": "http",
        "room": None,
        "mobile": True,
        "default_scope": "none",
        "focus_policy": "explicit_or_recent_room",
        "output_mode": "inherit_request",
    },
    # Mac menubar app and Raycast extension run on the same laptop, so they
    # share one logical "laptop" room focus via `device_group`.
    "menubar": {
        "label": "Menubar app",
        "type": "remote",
        "room": None,
        "mobile": True,
        "device_group": "laptop",
        "default_scope": "none",
        "focus_policy": "explicit_or_recent_room",
        "output_mode": "inherit_request",
    },
    "raycast": {
        "label": "Raycast",
        "type": "remote",
        "room": None,
        "mobile": True,
        "device_group": "laptop",
        "default_scope": "none",
        "focus_policy": "explicit_or_recent_room",
        "output_mode": "inherit_request",
    },
    "scheduler": {
        "label": "Scheduler",
        "type": "scheduler",
        "room": None,
        "mobile": False,
        "default_scope": "none",
        "focus_policy": "none",
        "output_mode": "none",
    },
    "physical_button": {
        "label": "Physical Button",
        "type": "button",
        "room": None,
        "inherit_default_room": True,
        "mobile": False,
        "default_scope": "room_local",
        "focus_policy": "sticky",
        "output_mode": "none",
    },
}

# Optional preferred Home Assistant weather entity. None auto-discovers the
# first weather.* entity with a current temperature.
WEATHER_ENTITY_ID = None

# Used for Open-Meteo weather fallback, straight-line location distance, and
# local astronomy calculations. The timezone is an IANA name; None falls back
# to the host's local timezone. Set either coordinate to None to disable
# coordinate-based features.
HOME_LOCATION = {
    # Coarse fields may be shared with the conversational provider. Exact
    # coordinates remain local to deterministic weather/astronomy code.
    "city": "Santa Barbara",
    "region": "California",
    "country": "US",
    "latitude": 34.4208,
    "longitude": -119.6982,
    "timezone": "America/Los_Angeles",
}

# Optional persistent context for conversational answers. Keep secrets and
# precise addresses out of this mapping. Room voice sources do not identify
# speakers, so preferred_name is a single-user/default-household convenience.
ASSISTANT_PROFILE = {
    "preferred_name": "",
    "locale": "en-US",
    "units": "imperial",
    "notes": [],
}

# Local Skyfield criteria for "visible tonight" answers. The default catalog
# contains the five commonly naked-eye planets; Uranus and Neptune remain
# available for named rise/set and position questions. Visibility is potential
# visibility only: local obstructions, light pollution, and clouds are unknown.
PLANET_VISIBILITY_PLANETS = ("mercury", "venus", "mars", "jupiter", "saturn")
PLANET_VISIBILITY_MIN_ALTITUDE_DEGREES = 10.0
PLANET_VISIBILITY_MAX_SUN_ALTITUDE_DEGREES = -6.0
PLANET_VISIBILITY_MAX_MAGNITUDE = 6.0
PLANET_VISIBILITY_MIN_DURATION_MINUTES = 15

# Read-only Alpaca stock quotes. The Basic Alpaca plan supports the IEX feed;
# deployments with consolidated SIP access can override STOCK_QUOTE_DATA_FEED.
STOCK_QUOTE_DATA_BASE_URL = "https://data.alpaca.markets"
STOCK_QUOTE_TRADING_BASE_URL = "https://paper-api.alpaca.markets"
STOCK_QUOTE_DATA_FEED = "iex"
STOCK_QUOTE_TIMEOUT_SECONDS = 5.0
STOCK_QUOTE_CACHE_SECONDS = 15.0
STOCK_MARKET_CLOCK_CACHE_SECONDS = 30.0
STOCK_QUOTE_MAX_SYMBOLS = 5
STOCK_SYMBOL_ALIASES = {
    "apple": "AAPL",
    "microsoft": "MSFT",
    "nvidia": "NVDA",
    "tesla": "TSLA",
    "amazon": "AMZN",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "meta": "META",
    "facebook": "META",
    "jp morgan": "JPM",
    "jpmorgan": "JPM",
    "exxon": "XOM",
    "exxon mobil": "XOM",
    "united health": "UNH",
    "unitedhealth": "UNH",
    "game stop": "GME",
    "gamestop": "GME",
    "berkshire hathaway": "BRK.B",
    "berkshire": "BRK.B",
}
STOCK_SYMBOL_LABELS = {
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "Nvidia",
    "TSLA": "Tesla",
    "AMZN": "Amazon",
    "GOOGL": "Alphabet",
    "META": "Meta",
    "JPM": "JPMorgan",
    "XOM": "Exxon Mobil",
    "UNH": "UnitedHealth",
    "GME": "GameStop",
    "BRK.B": "Berkshire Hathaway",
}
# Deployment overrides extend the built-in spoken names and labels without
# requiring a user to duplicate the common catalog above.
STOCK_SYMBOL_ALIAS_OVERRIDES = {}
STOCK_SYMBOL_LABEL_OVERRIDES = {}

# Optional spoken shorthand for geocoded locations.
LOCATION_ALIASES = {
    "la": "Los Angeles",
    "sb": "Santa Barbara",
    "santa barbara ca": "Santa Barbara",
}

# Display labels used when an entity ID alone would produce an awkward name.
ENTITY_LABEL_OVERRIDES = {
    "media_player.living_room_apple_tv": "Apple TV",
}

# Entities omitted from assistant-wide summaries and whole-home bulk actions.
# Explicit named commands can still target them. Exact IDs cover this reference
# deployment's room proxy lights; patterns cover common virtual helper entities.
ASSISTANT_BULK_EXCLUDED_ENTITY_IDS = [
    "light.living_room_brightness",
    "light.living_room_color",
    "light.bedroom_brightness",
    "light.bedroom_color",
    "light.kitchen_brightness",
    "light.office_brightness",
    "light.office_color",
]
ASSISTANT_BULK_EXCLUDED_ENTITY_PATTERNS = [
    "light.*_flicker",
    "light.*_underwater",
    "light.*scene_trigger*",
    "light.virtual_rgb_*",
    "light.*_status_led",
]


# =============================
# Global behavior prefs
# =============================

# -----------------------------
# Announcements
# -----------------------------
# Minimum volume (0-100) to use for Sonos 'announce' playback.
# If current volume is lower, we'll bump to this floor temporarily.
ANNOUNCE_VOLUME_FLOOR = 15

# gTTS voice selection. Keep lang as "en" and use TLD for the regional voice.
# Common TLDs include "com", "co.uk", "com.au", and "ie".
TTS_LANGUAGE = "en"
TTS_TLD = "ie"

# Conversational fallback model used when a request is routed to ChatGPT
# instead of a deterministic device/service handler.
#
# Suggested options:
#   "gpt-5.4-mini" = practical default for voice: capable, faster, lower cost
#   "gpt-5.5"      = stronger model for deeper reasoning/conversation
CHATGPT_MODEL = "gpt-5.4-mini"

# Allow the conversational fallback to use OpenAI's hosted web-search tool for
# current questions such as news, scores, schedules, and recent events. Device
# commands still run through deterministic handlers first and never require
# web search. Disable this to avoid web-search tool-call charges.
CHATGPT_WEB_SEARCH_ENABLED = True
CHATGPT_WEB_SEARCH_MODEL = CHATGPT_MODEL

# Structured follow-up state such as a recent timer, light, location, or media
# item. The state is process-local, source/context-bubble scoped, and handlers
# still revalidate stable IDs before acting. Individual domains may preserve a
# longer or shorter established TTL when they remember an object.
DIALOGUE_REFERENT_TTL_SECONDS = 2 * 60

# Extract short-lived media breadcrumbs from ChatGPT answers so deterministic
# Plex/Spotify handlers can resolve follow-ups like "play it" or "watch that".
# The extractor stores searchable names/kinds only, never model-invented IDs.
MEDIA_REFERENT_EXTRACTION_ENABLED = True
MEDIA_REFERENT_MODEL = CHATGPT_MODEL
MEDIA_REFERENT_TTL_SECONDS = 5 * 60

# Announcement/alarm speech inherits the assistant voice by default so local
# and Sonos-routed responses sound consistent. Override these independently if
# you want announcements or alarms to use a different gTTS voice.
ANNOUNCEMENT_TTS_TLD = TTS_TLD
ALARM_TTS_TLD = TTS_TLD

# How text destined for Sonos should become speech.
# Supported values:
#   "gtts"
#       PiPhone generates an MP3 with gTTS, then Sonos plays it using native
#       announce playback. SONOS_HA_TTS_ENTITY is ignored in this mode.
#   "home_assistant"
#       PiPhone calls Home Assistant's tts.speak service and lets HA generate
#       and send speech to the Sonos speaker. Requires SONOS_HA_TTS_ENTITY.
SONOS_TTS_BACKEND = "gtts"

# Required only when SONOS_TTS_BACKEND = "home_assistant".
# Set this to a Home Assistant tts.* entity, for example:
#   SONOS_HA_TTS_ENTITY = "tts.google_en_com"
SONOS_HA_TTS_ENTITY = None


# --- Video / TV-on policy ---
# We cannot query physical TV power state; we only have triggers.
# Rate-limit the TV-on trigger during watch sessions.
# Default: 10 minutes.
TV_ON_COOLDOWN_SECONDS = 10 * 60

# --- Spoken confirmations (two independent knobs) ---
# ACTION = device control ("turning off the lights") — default off (tone only).
# MEDIA  = now-playing content ("Playing The Matrix" / "Playing your science
#          roundup") across Plex, YouTube, and music — default on so you hear what
#          content was matched. Independent: silence media chatter without losing
#          device confirmations, or vice versa.
SPEAK_ACTION_CONFIRMATIONS = False
SPEAK_MEDIA_CONFIRMATIONS = True

# Exact transcripts that silently dismiss the current interaction. Matching is
# case/punctuation-insensitive and permits one leading or trailing "please".
# Keep this narrow so commands such as "cancel my timer" still reach their
# normal deterministic handlers.
INTERACTION_CANCEL_PHRASES = (
    "cancel",
    "never mind",
    "nevermind",
)

# --- Handset Feedback ---
# Handset-to-ear delay BEFORE the start chime. Only relevant on devices
# with a physical handset (HANDSET_PRESENT=True). Override in each
# device's local_prefs.py.
# Pi 3B with handset hardware: 0.65 (set in local prefs).
# Default 0.0 = no extra delay for devices without a handset.
START_CHIME_DELAY_SECONDS = 0.0

# --- Realtime STT keep-warm policy ---
# Warm streaming on boot
RT_WARMUP_ON_BOOT_ENABLED = True
#Check if warm when handset lifted, rewarm if not already warm
RT_OFFHOOK_WARMUP_ENABLED = False #NOTE: currently True makes system fall back to whisper


# =============================
# Audio capture / VAD config
# =============================
# Canonical audio-capture constants. Used by both main.py (PTT capture
# orchestration) and audio_capture.py (frame-driven VAD engine and streaming
# STT helpers). Frame timing values are derived from FRAME_MS so they stay
# in sync when FRAME_MS changes.
SAMPLE_RATE = 16000
FRAME_MS = 10                    # 160 samples @16kHz = 10ms
FRAME_SAMPLES = 160
MAX_UTTERANCE_SECONDS = 20       # hard cap so it never listens forever
SILENCE_END_MS = 550             # stop ~0.7s after you stop talking
PRE_ROLL_MS = 300                # capture a bit before speech start
MIN_SPEECH_MS = 250              # ignore tiny blips

SILENCE_END_FRAMES = int(SILENCE_END_MS / FRAME_MS)
PRE_ROLL_FRAMES = int(PRE_ROLL_MS / FRAME_MS)
MIN_SPEECH_FRAMES = int(MIN_SPEECH_MS / FRAME_MS)

# webrtcvad aggressiveness (0..3). 3 is most aggressive at filtering non-speech.
VAD_MODE = 3


# Optional per-device microphone profile. A device may override this dictionary
# in local_prefs.py without changing the wakeword implementation. Leave mixer
# fields unset for microphones that expose no ALSA hardware gain control.
AUDIO_INPUT_PROFILE = {
    "name": "default",
    "device_match": "USB",
    "device_index": None,
    "sample_rate": 48000,
    "channels": 1,
    "stream_latency": "low",
    "strict_device_match": False,
    "alsa_card": None,
    "mixer_control": None,
    "mixer_value": None,
    "verify_interval_sec": 0,
    "noise_suppression_level": 0,
    "auto_gain_dbfs": 0,
    "volume_multiplier": 1.0,
    "command_noise_suppression_level": 0,
    "command_auto_gain_dbfs": 0,
    "command_volume_multiplier": 1.0,
    # PTT-only software gain applied before VAD and streaming STT.
    "ptt_volume_multiplier": 1.0,
    "aec_mode": "none",
}


# =============================
# Wake-word / far-field runtime config
# =============================
# Wake-word behavior is wired in main.py and remains config-gated so each
# device can opt in safely through local_prefs.py.

# Master wake-word feature flag.
# Default off — each device opts in via local_prefs.py.
# Pi 4 wakeword box: True. Pi 3B PTT-only: False.
WAKEWORD_ENABLED = False

# Initial engine target for first prototype work.
# Expected future values may include: "porcupine", "openwakeword"
WAKEWORD_ENGINE = "openwakeword"

# Optional engine/model path or name. Empty means engine default.
WAKEWORD_MODEL = "hey_mycroft"

# Optional explicit openWakeWord model paths. Leave empty to use built-in models.
WAKEWORD_MODEL_PATHS = []

# Detection threshold for openWakeWord model scores.
WAKEWORD_THRESHOLD = 0.45

# Silero VAD gate threshold inside OpenWakeWord. The wake word model only
# fires when its own score is above WAKEWORD_THRESHOLD AND Silero VAD also
# scores the same audio chunk above this value. This is the single most
# effective control for cutting false positives on non-speech sounds like
# room noise, fan hum, USB interference, and reverb tails.
# Range 0.0 - 1.0. 0.5 is a sensible default.
WAKEWORD_VAD_THRESHOLD = 0.5

# Minimum time between accepted positives. Hysteresis below is the primary
# duplicate-hit protection; this is only a short timing floor.
WAKEWORD_DEBOUNCE_SEC = 0.30

# Trigger smoothing and hysteresis. After a hit, scoring does not re-arm until
# every model falls below the deactivation threshold for several frames.
WAKEWORD_ACTIVATION_WINDOW_FRAMES = 3
WAKEWORD_DEACTIVATION_THRESHOLD = 0.20
WAKEWORD_DEACTIVATION_FRAMES = 3

# Conservative default: only listen for wake word while handset is on-hook.
WAKEWORD_ONLY_ONHOOK = True

# Optional ready/listen chime after wake-word detection.
WAKEWORD_CHIME = True

# Cooldown after a wake-word-triggered interaction before re-arming listener.
WAKEWORD_REARM_SEC = 0.35

# Wakeword same-stream capture UX tuning. Command VAD consumes only audio after
# the detector hit and retains this much leading context while it locks on.
WAKEWORD_STREAM_PRE_ROLL_MS = 800
# A short pre-hit tail is appended after endpointing only when speech starts
# quickly enough to indicate one-breath "wakeword + command" speech. It never
# drives VAD, so a pause after the wake word still works normally.
WAKEWORD_STREAM_PRETRIGGER_INCLUDE_MS = 300
WAKEWORD_ONE_BREATH_MAX_SPEECH_START_MS = 450
# Frames are buffered immediately while VAD ignores the detector handoff tail.
# This does not delay recording or Realtime streaming.
WAKEWORD_STREAM_VAD_ARM_DELAY_MS = 250
# The acknowledgement cue can look like speech to WebRTC VAD. Keep its strong
# leading edge in pre-roll without allowing it to start command endpointing.
WAKEWORD_STREAM_CUE_GUARD_MS = 1000

# Wakeword-only acknowledgement cue. PTT keeps its existing start sound.
WAKEWORD_CHIME_SOUND_FILE = "assets/Blow.mp3"
WAKEWORD_CHIME_VOLUME = 1.0

# Wakeword capture should be more forgiving than handset PTT.
# In wakeword mode the user may naturally pause after the chime or between words,
# and there is no lifted-handset state to indicate "still in session".
WAKEWORD_STREAM_SILENCE_END_MS = 550   # matches PTT's SILENCE_END_MS (main.py:931)
WAKEWORD_STREAM_MIN_SPEECH_MS = 250   # matches PTT's MIN_SPEECH_MS (main.py:933)
# Wakeword-only endpointing tolerates occasional false-positive speech frames
# from room noise and speaker bleed. Set WINDOW_MS to 0 to restore the strict
# consecutive-silence rule. PTT never enables this rolling policy.
WAKEWORD_STREAM_ENDPOINT_WINDOW_MS = 700
WAKEWORD_STREAM_ENDPOINT_MIN_SILENCE_RATIO = 0.70
WAKEWORD_STREAM_ENDPOINT_TRAILING_SILENCE_MS = 80

# Hard cap for wake-word command capture. This prevents continuous room audio
# (TV, music, podcasts, guests) from holding the wake path for a long time.
WAKEWORD_STREAM_MAX_SECONDS = 8.0

# Optional room-media mitigation for far-field wake-word setups. When enabled,
# Home Suite pauses currently-playing TV/Sonos media in the active/default room
# before capturing the command so STT hears the user instead of the room audio.
WAKEWORD_PAUSE_MEDIA_DURING_CAPTURE = False
WAKEWORD_STREAM_POST_MEDIA_PAUSE_DRAIN_MS = 150

# After the wakeword chime, if VAD has not detected speech_start within this
# many ms, abort the capture cleanly (no error tone, no transcript). This
# prevents the listener from staying suppressed in a long silent capture when
# the user said the wake word but did not follow up. Default 4000 ms gives
# normal users plenty of time to speak; pathological hangs are bounded.
WAKEWORD_STREAM_FIRST_SPEECH_TIMEOUT_MS = 4000

# Step 7 (2026-05-14): control whether the wakeword capture uses the realtime
# streaming STT during capture. The realtime websocket setup
# (_rt_stream_create_runtime) takes 0.4-2.5 seconds and was the dominant
# cause of the silent gap between the user saying the wake word and the
# chime acknowledging it. With this False, capture writes a WAV and
# transcribe_audio() post-transcribes via realtime_streaming_stt's file
# upload path (same gpt-4o-transcribe model, no websocket setup).
# True restores the previous streaming-during-capture behavior.
WAKEWORD_USE_STREAMING_STT = True

# Wakeword capture is post-transcribed from a completed WAV. Keep this separate
# from PTT's realtime-streaming mode; the realtime websocket file feeder can
# collapse otherwise valid far-field recordings to a single syllable.
WAKEWORD_STT_MODE = "realtime_stream"

# Below this score, OWW didn't think the audio sounded like the wake word
# at all (well-known silence / non-speech). Between this and
# WAKEWORD_THRESHOLD we log a NEAR_MISS so we can diagnose "I said the
# wake word and nothing happened" cases.
WAKEWORD_NEAR_MISS_MIN_SCORE = 0.25

# During wakeword UX tuning, avoid interruptive error tones for partial/empty
# captures. Successful commands still get the normal success behavior.
WAKEWORD_ERROR_TONE_ENABLED = True

# Wakeword-only response behavior. Async local TTS lets the detector resume
# while a spoken answer plays; a new wakeword detection can then terminate the
# local player before capturing the replacement command. Defaults stay off so
# PTT and existing wakeword installations retain synchronous speech.
WAKEWORD_ASYNC_TTS_ENABLED = False
WAKEWORD_BARGE_IN_ENABLED = False
# Optional lower OpenWakeWord score threshold while local assistant speech is
# active. It has no effect unless barge-in is enabled. Keeping it equal to the
# normal threshold preserves existing behavior; tune per microphone/speaker.
WAKEWORD_BARGE_IN_THRESHOLD = WAKEWORD_THRESHOLD

# Whether the wakeword listener should be suppressed while any UI sound
# (start chime, finish chime, error tone) is playing.
# Default True is the conservative behavior: prevents OWW from scoring on
# its own audio bleeding back through the mic. Trade-off: adds ~1.5s of
# dead time after each interaction while the tone finishes playing.
# Set False to keep the listener active during SFX — saves the dead time
# but allows phantom triggers IF a chime/tone happens to score above the
# wake-word threshold (unlikely with custom phrase models, but possible).
WAKEWORD_SUPPRESS_DURING_SFX = True
# Maximum time the interaction callback waits for a completion/error cue before
# releasing the detector. Devices that tolerate scoring during the cue tail can
# set this to zero alongside WAKEWORD_SUPPRESS_DURING_SFX=False.
WAKEWORD_REARM_SFX_DRAIN_MAX_SEC = 1.0

# Hardware: whether this device has a physical handset wired to GPIO.
# Set False on devices that have no handset (e.g. the wakeword-only Pi 4 rig,
# or a future push-and-hold-button-only device). When False, the runtime
# treats the handset as always "lifted" so spoken responses are not suppressed
# and wakeword/push-and-hold UX works normally. Setting it True preserves the
# original Pi 3 handset behavior. Fresh installs default to no handset hardware.
HANDSET_PRESENT = False

# Hardware input paths are opt-in per device. Fresh installs begin as safe text
# command nodes until local_prefs.py explicitly enables a handset or wakeword.
PTT_ENABLED = False

# Assistant audio output policy for normal assistant responses.
# Supported values:
#   "local" = local PiPhone audio path
#   "sonos" = room speaker output path via Sonos native announce playback
ASSISTANT_AUDIO_OUTPUT_MODE = "local"

# Optional fixed room target for assistant audio output when using a remote
# speaker path such as Sonos. None means runtime/default resolution.
ASSISTANT_AUDIO_OUTPUT_ROOM = None

# Minimum volume (0-100) to use when assistant responses are routed through
# Sonos native announcement playback.
ASSISTANT_SONOS_ANNOUNCE_VOLUME_FLOOR = ANNOUNCE_VOLUME_FLOOR

# =============================
# Audio / media shortcuts
# =============================

# -----------------------------
# Pinned Spotify playlists
# -----------------------------
# Spoken phrase (normalized) -> Spotify URI
PINNED_SPOTIFY_PLAYLISTS = {
    _norm_key("discover weekly"): "spotify:playlist:37i9dQZEVXcRBai5kWKP7J",

    # Examples you can add:
    # _norm_key("release radar"): "spotify:playlist:37i9dQZEVXc....",
    # _norm_key("modest"): "spotify:playlist:4kdj919SapHbsVGAzuHQOF",
}

# -----------------------------
# Pinned radio stations (Sonos-playable URIs)
# -----------------------------
# Spoken phrase (normalized) -> media_content_id
PINNED_RADIO_STATIONS = {
    _norm_key("kclu"): "x-rincon-mp3radio://https://kclustream.callutheran.edu/kclump3",
}


# =============================
# Phoenetic Token Repairs
# =============================
# Deterministic text repairs for common STT mishearings.
#
# Important: we split repairs into two buckets:
#   1) PHONETIC_ROUTING_REPAIRS: "safe" command/transport rewrites (pause, arrow keys, etc.)
#      These may be applied early in the device pipeline (before handler routing).
#   2) PHONETIC_DEVICE_REPAIRS: "risky" noun/device rewrites (e.g. "dynamite" -> "dining light")
#      These are only applied when the utterance is deemed device-likely AND the first pass did not claim anything.
#
# We keep PHONETIC_TOKEN_REPAIRS as a merged, backward-compatible view for older code paths,
# but new code should prefer the split maps above.

PHONETIC_ROUTING_REPAIRS = {
    # For quick AppleTV nav in REPL
    "up": [
        "^[[A",
    ],
    "down": [
        "^[[B",
    ],
    "right": [
        "^[[C",
    ],
    "left": [
        "^[[D",
    ],

    # Transport verbs
    "pause": [
        "paws",
        "paw's",
        "paul's",
        "pa's"
    ],
}

PHONETIC_DEVICE_REPAIRS = {
    # multi-word (these should generally win over single-word fixes)
    "dining light": [
        "timing light",
        "downing light",
        "dimming light",
        "timing rate",
        "downing rate",
        "dynamite",
        "down light",
    ],
    "side lamp": [
        "sidelamp",
        "sidelamps",
        "aside lamp",
        "a side lamp",
        "sad lamp",
    ],
    "stair light": [
        "stairlight",
        "starlight",
        "star light",
        "stare light",
        "Sterilite",
        "Sterlite",
        "starlite",
    ],
    "sink light": [
        "sync light",
    ],
}

# Backward-compat: merged view
PHONETIC_TOKEN_REPAIRS = {}
PHONETIC_TOKEN_REPAIRS.update(PHONETIC_DEVICE_REPAIRS)
PHONETIC_TOKEN_REPAIRS.update(PHONETIC_ROUTING_REPAIRS)



# =============================
# Home Assistant trigger aliases (scenes/scripts)
# =============================
# Map HA entity_id -> list of phrases that should trigger it.
# Phrases are normalized internally; keep them human-friendly.
#
# Notes:
# - These run via the runnable-cache path (scenes/scripts) before device controls.
# - This is intentionally entity-first (readable, many-to-one).
HA_TRIGGER_ALIASES = {
    "scene.tv_on": [
        "turn on tv",
        "turn on the tv",
        "tv on",
        "power on tv",
        "switch on tv",
    ],
    "scene.tv_off": [
        "turn off tv",
        "turn off the tv",
        "tv off",
        "shut off tv",
        "shut off the tv",
        "power off tv",
        "switch off tv",
    ],
    "script.screensaver": [
        "screensaver",
        "screen saver",
        "tv screensaver",
        "turn on screensaver",
        "show screensaver",
        "watch screensaver",
        "display screensaver",
        "apple tv screensaver",
    ],
    "scene.living_room_dim": [
        "living room gym",
    ],
    "script.controlcenter": [
        "control center",
        "controls",
        "control",
    ],
    "script.back": [
        "back",
        "go back",
    ],
    "script.edit": [
        "edit",
        "info",
    ],
    "script.switch_apps": [
        "switch apps",
        "app switcher",
        "app switch",
        "switcher",
    ],
    "script.launch_plex": [
        "launch plex",
        "open plex",
        "plex",
    ],
    "script.apple_tv_launch_hbo_max": [
        "launch hbo max",
        "launch hbo",
        "open hbo",
        "hbo",
    ],
    "script.apple_tv_launch_youtube": [
        "launch youtube",
        "open youtube",
        "youtube",
    ],
    "script.apple_tv_launch_netflix": [
        "launch netflix",
        "open netflix",
        "netflix",
    ],
    "script.apple_tv_launch_cnn": [
        "launch cnn",
        "open cnn",
        "cnn",
    ],
    "script.apple_tv_launch_disney_plus": [
        "launch disney+",
        "open disney+",
        "disney+",
        "launch disney plus",
        "open disney plus",
        "disney plus",
    ],
    "script.apple_tv_launch_paramount_plus": [
        "launch paramount+",
        "open paramount+",
        "paramount+",
        "launch paramount plus",
        "open paramount plus",
        "paramount plus",
    ],
    "script.apple_tv_launch_prime_video": [
        "launch prime video",
        "open prime video",
        "prime video",
        "launch amazon prime video",
        "open amazon prime video",
        "amazon prime video",
    ],
    "script.apple_tv_launch_youtube_tv": [
        "launch youtube tv",
        "open youtube tv",
        "youtube tv",
        "launch you tube tv",
        "open you tube tv",
        "you tube tv",
    ],
    "script.apple_tv_launch_settings": [
        "launch settings",
        "open settings",
        "settings",
    ],
    "script.watch_tv": [
        "watch tv",
        "open settings",
        "settings",
    ],
    "script.turn_on_night_sound": [
        "turn on night sound",
    ],
    "script.turn_off_night_sound": [
        "turn off night sound",
    ],
    "script.turn_on_speech_enhancement": [
        "turn on speech enhancement",
    ],
    "script.turn_off_speech_enhancement": [
        "turn off speech enhancement",
    ],
    "script.turn_off_everything": [
        "turn off everything",
    ],
    "script.find_my_phone": [
        "find my phone",
        "call my phone",
        "locate my phone",
        "find jason's phone",
        "call jason's phone",
        "locate jason's phone",
    ],
}


# =============================
# YouTube channel registry (curated seeds)
# =============================
# Seeds youtube_channels.py (merged under saved state in state/youtube_channels.json).
# Keyed by canonical channel_id (UC...). Resolved 2026-06-07 via resolve_handle_to_id.
# Per entry: title, handle, aliases[] (extra spoken names), in_digest (reel subset),
# groups[], and optional filters: include_title[]/exclude_title[]/exclude_description[]
# (case-insensitive substrings), min_duration_sec (needs Stage A.2 durations),
# max_per_channel (default 1 = newest upload per channel within the 24h window).
YOUTUBE_CHANNELS = {
    "UCVTyTA7-g9nopHeHbeuvpRA": {
        "title": "Late Night with Seth Meyers",
        "handle": "@LateNightSeth",
        "aliases": ["seth meyers", "late night", "seth"],
        "in_digest": True,
        "groups": ["latenight"],
        "min_duration_sec": 120,  # drop Shorts/short clips (needs Data API durations)
        # Monologue = "A Closer Look". Full segment titles contain "a closer look"
        # (spaces); the Shorts use the "#ACloserLook" hashtag (no spaces), so the
        # include filter keeps the segment and drops the clips.
        "include_title": ["a closer look"],
    },
    "UCa6vGFO9ty8v5KZJXQxdhaw": {
        "title": "Jimmy Kimmel Live",
        "handle": "@JimmyKimmelLive",
        "aliases": ["jimmy kimmel", "kimmel"],
        "in_digest": True,
        "groups": ["latenight"],
        "min_duration_sec": 120,  # drop Shorts/short clips (needs Data API durations)
        # No monologue keyword in Kimmel's titles, so we can't include-filter the
        # monologue cleanly — only exclude obvious recurring non-monologue segments.
        # Brittle; the real fix is a min-duration filter once Stage A.2 lands.
        "exclude_title": ["unnecessary censorship", "guillermo"],
    },
    "UCwWhs_6x42TyRM4Wstoq8HA": {
        "title": "The Daily Show",
        "handle": "@TheDailyShow",
        "aliases": ["daily show"],
        "in_digest": True,
        "groups": ["latenight"],
        "min_duration_sec": 120,  # drop Shorts/short clips (needs Data API durations)
        # Full segments end with "| The Daily Show"; Shorts/correspondent clips use
        # "#dailyshow"/"#<name>" hashtags instead, so this keeps the real segments.
        "include_title": ["| the daily show"],
    },
    # --- science roundup ("play my science roundup") ---
    # in_digest=True AND groups=["science"]: these appear in both the global daily
    # reel and the dedicated science roundup.
    "UC7_gcs09iThXybpVgjHZ_7g": {
        "title": "PBS Space Time",
        "handle": "@pbsspacetime",
        "aliases": ["space time", "spacetime", "pbs space time"],
        "in_digest": True,
        "groups": ["science"],
        "min_duration_sec": 120,  # drop Shorts/short clips (needs Data API durations)
    },
    "UCciQ8wFcVoIIMi-lfu8-cjQ": {
        "title": "Anton Petrov",
        "handle": "@whatdamath",
        "aliases": ["anton petrov", "anton", "what da math"],
        "in_digest": True,
        "groups": ["science"],
        "min_duration_sec": 120,  # drop Shorts/short clips (needs Data API durations)
    },
    "UCpMcsdZf2KkAnfmxiq2MfMQ": {
        "title": "Arvin Ash",
        "handle": "@ArvinAsh",
        "aliases": ["arvin ash", "arvin"],
        "in_digest": True,
        "groups": ["science"],
        "min_duration_sec": 120,  # drop Shorts/short clips (needs Data API durations)
    },
    "UCHnyfMqiRRG1u-2MsSQLbXA": {
        "title": "Veritasium",
        "handle": "@veritasium",
        "aliases": ["veritasium"],
        "in_digest": True,
        "groups": ["science"],
        "min_duration_sec": 120,  # drop Shorts/short clips (needs Data API durations)
    },
    "UCUeZBocfxALSUdOgNJB5ySA": {
        "title": "Dr Ben Miles",
        "handle": "@DrBenMiles",
        "aliases": ["ben miles", "dr ben miles"],
        "in_digest": True,
        "groups": ["science"],
        "min_duration_sec": 120,  # drop Shorts/short clips (needs Data API durations)
    },
}


# =============================
# YouTube reel playlist refresh (Stage B scheduler)
# =============================
# The in-process scheduler rebuilds the "PiPhone · …" playlists during an evening
# window so they pull in newly-posted episodes. First refresh each day wipes +
# rebuilds; later refreshes diff-add only what's new.
YOUTUBE_REEL_REFRESH_ENABLED = True
YOUTUBE_REEL_WINDOW = (16, 30, 22, 30)   # (start_h, start_m, end_h, end_m), local time
YOUTUBE_REEL_REFRESH_INTERVAL_S = 300    # 5 minutes

# Seconds to wait after foregrounding the YouTube app (via play_media) before
# sending the Lounge play command, so it's ready. Runs on every playback; the
# Lounge command also retries, so this can be short. Raise if first-try playback
# misses; lower for snappier already-open playback.
YOUTUBE_APP_LAUNCH_SETTLE_S = 1.5


# =============================
# Home Assistant device aliases (real HA entities)
# =============================
# Map HA entity_id -> list of phrases that should resolve to that entity.
# This is used by main.resolve_device_entity(...) so all device control modules benefit.
#
# Examples (edit to match YOUR entity_ids):
HA_DEVICE_ALIASES = {
    "switch.espresso_machine": [
        "espresso machine",
        "coffee machine",
        "coffee maker",
        "espresso maker",
     ],
     "lock.front_door_lock": [
        "front door lock",
        "front door",
        "the door",
        "door",
     ],
     "light.dining_light": [
        "dining light",
     ],
#     "light.side_lamp": [
#        "sidelamp",
#        "sidelamps",
#        "aside lamp",
#        "a side lamp",
#     ],
#     "light.stair_light": [
#        "stairlight",
#        "starlight",
#        "star light",
#        "stare light",
#     ],
#     "light.dining_light": [
#        "timing light",
#        "diamond light",
#     ],
}


# =============================
# Plex preferences
# =============================

PLEX_TITLE_ALIASES = {
    "WALL-E": [
        "wall e",
        "walle",
        "wally",
    ],
    "Se7en": [
        "seven",
        "seven movie",
    ],
    "It": [
        "it movie",
    ],
    "Antz": [
        "ants",
        "aunts",
    ],
    "3:10 To Yuma": [
        "3 10 to yuma",
        "310 to yuma",
    ],
    "Crazy/Beautiful": [
        "crazy beautiful",
    ],
    "Fahrenheit 9/11": [
        "fahrenheit 9 11",
        "fahrenheit nine eleven",
    ],
    "Face/Off": [
        "face off",
        "facoff",
    ],
    "The Lion King 1½": [
        "lion king one and a half",
        "lion king one and one half",
    ],
    "The Moor": [
        "the more",
    ],
    "Juror #2": [
        "juror number two",
        "juror two",
    ],
    "The Naked Gun 2½: The Smell of Fear": [
        "the naked gun two and a half",
        "the naked gun two and one half",
        "the naked gun two",
    ],
    "Naked Gun 33 1/3: The Final Insult": [
        "the naked gun thirty three and a third",
        "the naked gun thirty three and one third",
        "the naked gun three",
    ],
    "The Super Mario Bros. Movie": [
        "the super mario brothers movie",
        "the super mario brothers",
        "the mario brothers",
    ],
}

PLEX_ORDINAL_EXCLUDES = {
    "dune": {
        "dune drifter",
    },
}

PLEX_COLLECTION_ALIASES = {
    "james bond": [
        "007",
        "bond",
        "bond movies",
        "james bond movies",
    ],
    "star wars": [
        "star wars saga",
        "the star wars movies",
    ],
    "28 Days/Weeks/Years Later": [
        "28 movie",
    ],
    "AVP": [
        "alien vs predator",
        "alien versus predator",
        "alien v predator",
    ],
    "Godzilla (Showa)": [
        "Godzilla",
        "original godzilla",
        "vintage godzilla",
    ],
    "Hannibal Lecter": [
        "hannibal",
    ],
    "The Pink Panther (Original)": [
        "the pink panther",
        "pink panther",
    ],
    "Star trek: Alternate Reality": [
        "star trek alternate",
        "modern star trek",
        "new star trek",
    ],
    "Star Trek: The Next Generation": [
        "star trek tng",
        "next generation",
        "star trek next generation",
    ],
    "Star Trek: The Original Series": [
        "original star trek",
        "original star trek movies",
        "original star trek movie",
        "star trek movies",
        "star trek movie",
    ],
    "This Is Spinal Tap": [
        "spinal tap",
    ],
    "Sonic The Hedgehog": [
        "sonic",
    ],
}

# =========================
# NOW PLAYING (formatting)
# =========================
# Parts: "device", "app", "channel", "title"
# Order is applied first, then include flags determine what shows up.

NOW_PLAYING_APPLE_TV_DEVICE_NAME = "Apple TV"

# Your preferred default right now:
NOW_PLAYING_APPLE_TV_ORDER = ["channel", "title", "app", "device"]

# Which pieces of metadata to include when answering ' what's playing?'
NOW_PLAYING_APPLE_TV_INCLUDE = {
    "device": False,
    "app": False,
    "channel": True,
    "title": True,
}

# If True, include S/E numbers when available for episodic content
NOW_PLAYING_APPLE_TV_INCLUDE_EP_NUMBERS = False


# =========================
# TTS Pronunciation Correction (for announcements only)
# =========================
#Correct "Live" pronounces as 'liv' to 'laiv' instead
TTS_PRONUNCIATION_OVERRIDES = {
   
 # Your fix (keeps prosody + fixes "Live")
    "jimmy kimmel live": "jimmy kimmel live!",

    # Acronyms (gTTS often does better with spaced letters)
    "snl": "S N L",
    "hdmi": "H D M I",
    "cnn": "C N N",

    # Brand casing/spacing that sometimes helps
    "youtube": "you tube",
}

# =============================
# Scheduler safety policy
# =============================
# Default philosophy:
# - Anything PiPhone can already do is schedulable unless blocked here.
# - Prefer blocking by resolved HA service/entity instead of enumerating phrases.
#
# Service format matches Home Assistant service calls used by PiPhone:
#   "domain/service"
# Examples:
#   "lock/unlock"
#   "alarm_control_panel/disarm"
#
# Entity prefixes match resolved entity_ids:
#   "lock." blocks lock.front_door_lock, lock.back_door_lock, etc.

# Exact HA services that may not be scheduled.
# Default: block delayed unlock/disarm actions, while still allowing useful actions
# like "lock the front door at 10 pm" unless you block lock.* below.
SCHEDULER_BLOCKED_SERVICES = [
    "lock/unlock",
    "alarm_control_panel/disarm",
]

# HA service prefixes that may not be scheduled.
# Example to block all alarm panel actions:
#   "alarm_control_panel/"
SCHEDULER_BLOCKED_SERVICE_PREFIXES = [
]

# Entity prefixes that may not be scheduled.
# If you want to block ALL lock scheduling, add:
#   "lock.",
SCHEDULER_BLOCKED_ENTITY_PREFIXES = [
    "alarm_control_panel.",
]

# Phrase-level fallback blocklist. This is intentionally secondary to
# resolved-action blocking, but useful for non-HA actions or ambiguous commands.
SCHEDULER_BLOCKED_COMMAND_REGEXES = [
    r"\bunlock\b",
    r"\bdisarm\b",
]

# Backward-compatible simple substring blocklists. Prefer the structured lists above.
SCHEDULER_BLOCKLIST = [
]

SCHEDULER_COMMAND_BLOCKLIST = [
]

# Fallback timeout for standalone scheduler.py subprocess execution.
# Production homesuite.service normally uses in-process execution instead.
SCHEDULER_COMMAND_TIMEOUT_SEC = 60.0

# =============================
# Alarms / Timers
# =============================
# Output modes:
#   "local" = play through this PiPhone's local audio output
#   "sonos" = announce through the default room's Sonos speaker
#
# Per-command phrases can override this:
#   "set a timer for 5 minutes on speaker"
#   "set a timer for 5 minutes in living room"
#   "set a timer for 5 minutes locally"
ALARM_DEFAULT_OUTPUT = "sonos"
# If enabled, play ALARM_SOUND_FILE before the spoken message.
ALARM_SOUND_ENABLED = True

# If enabled, speak "Your timer is done" / "Your pasta timer is done".
ALARM_VOICE_ENABLED = True

# Reminders share alarm persistence and output routing but default to a concise
# spoken message instead of the full alarm sound. Enable the sound explicitly
# when reminders should use ALARM_SOUND_FILE too.
REMINDER_SOUND_ENABLED = False
REMINDER_VOICE_ENABLED = True

# Relative to repo root unless absolute. If missing, PiPhone skips the sound
# and still speaks if ALARM_VOICE_ENABLED is True.
ALARM_SOUND_FILE = "assets/Stargaze.mp3"

# Sonos announcement behavior.
ALARM_SONOS_ANNOUNCE_VOLUME_FLOOR = ANNOUNCE_VOLUME_FLOOR
ALARM_SONOS_SOUND_TO_VOICE_DELAY_SEC = 3.0

# If True, confirmations include the output target:
#   "5 second timer set on the living room speaker."
# Default False keeps timer/alarm confirmations short.
ALARM_CONFIRM_INCLUDE_OUTPUT_TARGET = False

# Bare "snooze" uses this delay. Only plain alarms and timers can be snoozed;
# attached music/device actions fail closed because replaying them may have side effects.
ALARM_DEFAULT_SNOOZE_MINUTES = 10

# How long a completed alarm or timer remains eligible for a follow-up snooze.
ALARM_SNOOZE_RECENT_WINDOW_SECONDS = 15 * 60

# Max seconds to allow an attached alarm/timer command to run.
ALARM_ATTACHED_COMMAND_TIMEOUT_SEC = 30.0

# If True, an alarm/timer with attached music will use the music itself as
# the notification. No alarm chime or spoken "your alarm is going off" first.
ALARM_MUSIC_REPLACES_NOTIFICATION = True

# If True, attached non-music actions run immediately when the alarm fires,
# before sound/voice notification. This makes HA actions feel simultaneous
# with the alarm instead of delayed until after the announcement.
ALARM_ACTION_BEFORE_NOTIFICATION = True

# =============================
# Sonos / Music routing
# =============================

def _room_aliases(room_id: str, room: Dict[str, Any]) -> list[str]:
    aliases = {str(room_id).strip().lower().replace("_", " ")}
    aliases.update(
        str(alias).strip().lower().replace("_", " ")
        for alias in (room.get("aliases") or [])
        if str(alias).strip()
    )
    return sorted(aliases)


def _derive_sonos_players() -> Dict[str, str]:
    """Build the legacy player lookup from canonical room audio settings."""
    players: Dict[str, str] = {}
    for room_id, room in (ROOMS or {}).items():
        if not isinstance(room, dict):
            continue
        defaults = room.get("defaults") or {}
        entity_id = str(defaults.get("audio_output") or "").strip()
        if entity_id:
            for alias in _room_aliases(room_id, room):
                players[alias] = entity_id
        for alias, extra_entity in (room.get("audio_aliases") or {}).items():
            key = str(alias or "").strip().lower().replace("_", " ")
            value = str(extra_entity or "").strip()
            if key and value:
                players[key] = value
    return players


def _derive_default_sonos_room() -> str:
    return str(DEFAULT_ROOM).strip().lower().replace("_", " ")


def _default_room_setting(key: str):
    room = ROOMS.get(DEFAULT_ROOM) or {}
    defaults = room.get("defaults") or {}
    return defaults.get(key)


# Compatibility views for modules that still consume a flat player map.
# They are derived from ROOMS so room topology has one source of truth.
DEFAULT_SONOS_ROOM = _derive_default_sonos_room()
SONOS_PLAYERS = _derive_sonos_players()

# If False, My Sonos / Sonos Favorites only responds to explicit favorite
# phrases like "play favorite KCLU" or "play my sonos KCLU".
# This prevents generic Spotify requests from being stolen by Sonos favorites.
SONOS_MY_SONOS_GENERIC_PLAY_ENABLED = False

# =============================
# TV / Apple TV defaults
# =============================

# Default skip interval for room TV remotes that support seek commands.
APPLE_TV_DEFAULT_SKIP_SECONDS = 10

# Compatibility names for older TV helpers. Both derive from DEFAULT_ROOM;
# request-aware routing reads each room's TV settings directly.
APPLE_TV_ENTITY = _default_room_setting("tv")
APPLE_TV_REMOTE = _default_room_setting("tv_remote")

# =============================
# Physical command buttons
# =============================
# Auxiliary physical buttons, separate from the handset hook/PTT switch.
#
# Backend:
#   "pigpio" = use the local pigpiod daemon already running on this Pi
# Default off — each device opts in via local_prefs.py.
# Pi 3B has 8 buttons wired: True (with pin map and actions in local prefs).
# Pi 4 has no buttons wired: False.
PHYSICAL_BUTTONS_ENABLED = False
PHYSICAL_BUTTON_BACKEND = "pigpio"
PHYSICAL_BUTTON_PIGPIO_HOST = "127.0.0.1"
PHYSICAL_BUTTON_PIGPIO_PORT = 8888

# BCM pin numbering for physical buttons. Device-specific — defined in each
# device's local_prefs.py, since pin assignments depend on what's
# physically wired to that Pi.
# Default empty: a device with no buttons (Pi 4) leaves this empty.
PHYSICAL_BUTTON_PINS = {}

# Buttons are wired GPIO -> button -> common ground.
# Idle is pulled high, pressed reads low.
PHYSICAL_BUTTON_ACTIVE_LOW = True
PHYSICAL_BUTTON_PULL_UP = True

# Timing tuned to match the old working Homebridge RPi behavior.
PHYSICAL_BUTTON_DEBOUNCE_MS = 40
PHYSICAL_BUTTON_SETTLE_MS = 25
PHYSICAL_BUTTON_DOUBLE_PRESS_WINDOW_MS = 350
PHYSICAL_BUTTON_LONG_PRESS_MS = 800
PHYSICAL_BUTTON_HOLD_REPEAT_INTERVAL_MS = 350
PHYSICAL_BUTTON_HOLD_REPEAT_MAX_REPEATS = 30

PHYSICAL_BUTTON_IGNORE_WHILE_HANDSET_UP = False

# Turn off raw debug mode for normal operation.
PHYSICAL_BUTTON_DEBUG_RAW_MODE = False

# Button-to-command mapping. Device-specific — defined in each device's
# local_prefs.py, since the meaning of each physical button depends
# on what scenes/commands make sense for that device's location and use.
# Default empty: a device with no buttons (Pi 4) leaves this empty.
PHYSICAL_BUTTON_ACTIONS = {}


# =============================================================================
# Unified runtime: in-process HTTP/WS server
# =============================================================================
# When True, main.main() starts unified_server.start_in_background_thread()
# during boot. This makes homesuite.service serve the HTTP/WS surface formerly
# provided by piphone-wsh.service (port 8765 by default), sharing entity_cache
# and request handlers in-process with the voice and text command paths.
#
# Enabled by default so HTTP and WebSocket companion clients work after normal
# setup. Startup fails closed for this component when HOMESUITE_HTTP_API_KEY is
# blank; the rest of the Home Suite runtime continues without a network API.
UNIFIED_SERVER_ENABLED = True
UNIFIED_SERVER_PORT = 8765


# =============================================================================
# Homelab / self-hosted service status
# =============================================================================
# HA-backed service entity mapping used by homelab_controls.py.
#
# This is deliberately configuration, not secrets: entity IDs are safe to keep
# in source and easy for another deployment to override in local_prefs.py.
# Tokens, passwords, and direct service URLs belong in the secrets module once
# direct APIs are added.
#
# Set any entity to None if your Home Assistant does not expose it. Direct API
# integrations can later extend this without changing the HA-first behavior.
HOMELAB_SERVICES = {
    "qbittorrent": {
        "label": "qBittorrent",
        "ha_entities": {
            "status": "sensor.qbittorrent_status",
            "connection": "sensor.qbittorrent_connection_status",
            "download_speed": "sensor.qbittorrent_download_speed",
            "upload_speed": "sensor.qbittorrent_upload_speed",
            "all_torrents": "sensor.qbittorrent_all_torrents",
            "active_torrents": "sensor.qbittorrent_active_torrents",
            "inactive_torrents": "sensor.qbittorrent_inactive_torrents",
            "paused_torrents": "sensor.qbittorrent_paused_torrents",
            "errored_torrents": "sensor.qbittorrent_errored_torrents",
        },
    },
    "overseerr": {
        "label": "Overseerr",
        "ha_entities": {
            "total_requests": "sensor.overseerr_total_requests",
            "movie_requests": "sensor.overseerr_movie_requests",
            "tv_requests": "sensor.overseerr_tv_requests",
            "pending_requests": "sensor.overseerr_pending_requests",
            "processing_requests": "sensor.overseerr_processing_requests",
            "available_requests": "sensor.overseerr_available_requests",
            "open_issues": "sensor.overseerr_open_issues",
        },
    },
    "radarr": {
        "label": "Radarr",
        "ha_entities": {
            "health": "binary_sensor.radarr_health",
            "calendar": "calendar.radarr",
            "disk_movies": "sensor.radarr_disk_space_movies",
            "disk_torrents": "sensor.radarr_disk_space_torrents",
        },
    },
    "sonarr": {
        "label": "Sonarr",
        "ha_entities": {
            "upcoming": "sensor.sonarr_upcoming",
        },
    },
    "lidarr": {
        "label": "Lidarr",
        "ha_entities": {
            "queue": "sensor.lidarr_queue",
            "disk_space": "sensor.lidarr_disk_space",
        },
    },
    "speedtest": {
        "label": "Internet",
        "ha_entities": {
            "ping": "sensor.speedtest_ping",
            "download": "sensor.speedtest_download",
            "upload": "sensor.speedtest_upload",
        },
    },
    "synology": {
        "label": "YoreNAS",
        "ha_entities": {
            "nas_state": "sensor.yorenas",
            "security_status": "binary_sensor.yorenas_security_status",
            "dsm_update": "update.yorenas_dsm_update",
            "plex_update": "update.plex_media_server_yorenas",
            "temperature": "sensor.yorenas_temperature",
            "cpu_total": "sensor.yorenas_cpu_utilization_total",
            "memory_usage": "sensor.yorenas_memory_usage_real",
            "download_throughput": "sensor.yorenas_download_throughput",
            "upload_throughput": "sensor.yorenas_upload_throughput",
            "volume_1_status": "sensor.yorenas_volume_1_status",
            "volume_1_used": "sensor.yorenas_volume_1_volume_used",
            "volume_1_used_space": "sensor.yorenas_volume_1_used_space",
            "volume_1_temp": "sensor.yorenas_volume_1_average_disk_temp",
            "volume_2_status": "sensor.yorenas_volume_2_status",
            "volume_2_used": "sensor.yorenas_volume_2_volume_used",
            "volume_2_used_space": "sensor.yorenas_volume_2_used_space",
            "volume_2_temp": "sensor.yorenas_volume_2_average_disk_temp",
            "drive_1_status": "sensor.yorenas_drive_1_status",
            "drive_1_temp": "sensor.yorenas_drive_1_temperature",
            "drive_2_status": "sensor.yorenas_drive_2_status",
            "drive_2_temp": "sensor.yorenas_drive_2_temperature",
            "drive_3_status": "sensor.yorenas_drive_3_status",
            "drive_3_temp": "sensor.yorenas_drive_3_temperature",
            "drive_4_status": "sensor.yorenas_drive_4_status",
            "drive_4_temp": "sensor.yorenas_drive_4_temperature",
            "cache_1_status": "sensor.yorenas_cache_device_1_status",
            "cache_1_temp": "sensor.yorenas_cache_device_1_temperature",
            "cache_2_status": "sensor.yorenas_cache_device_2_status",
            "cache_2_temp": "sensor.yorenas_cache_device_2_temperature",
            "usb_disk_2_status": "sensor.yorenas_usb_disk_2_status",
            "usb_disk_2_partition_2_used": "sensor.yorenas_usb_disk_2_partition_2_partition_used",
        },
        "alert_entities": [
            "binary_sensor.yorenas_security_status",
            "binary_sensor.yorenas_drive_1_below_min_remaining_life",
            "binary_sensor.yorenas_drive_1_exceeded_max_bad_sectors",
            "binary_sensor.yorenas_drive_2_below_min_remaining_life",
            "binary_sensor.yorenas_drive_2_exceeded_max_bad_sectors",
            "binary_sensor.yorenas_drive_3_below_min_remaining_life",
            "binary_sensor.yorenas_drive_3_exceeded_max_bad_sectors",
            "binary_sensor.yorenas_drive_4_below_min_remaining_life",
            "binary_sensor.yorenas_drive_4_exceeded_max_bad_sectors",
            "binary_sensor.yorenas_cache_device_1_below_min_remaining_life",
            "binary_sensor.yorenas_cache_device_1_exceeded_max_bad_sectors",
            "binary_sensor.yorenas_cache_device_2_below_min_remaining_life",
            "binary_sensor.yorenas_cache_device_2_exceeded_max_bad_sectors",
        ],
    },
    "reolink": {
        "label": "Cameras",
        "alert_entities": [
            "binary_sensor.front_camera_motion",
            "binary_sensor.front_camera_person",
            "binary_sensor.front_camera_vehicle",
            "binary_sensor.front_camera_animal",
            "binary_sensor.reolink_lumus_pro_motion",
            "binary_sensor.reolink_lumus_pro_person",
            "binary_sensor.reolink_lumus_pro_vehicle",
            "binary_sensor.reolink_lumus_pro_animal",
            "binary_sensor.e1_zoom_motion",
            "binary_sensor.e1_zoom_person",
            "binary_sensor.e1_zoom_pet",
            "binary_sensor.living_room_camera_baby_crying",
            "binary_sensor.reolink_onvif_motion_alarm",
            "binary_sensor.reolink_onvif_person_detection",
            "binary_sensor.reolink_onvif_vehicle_detection",
            "binary_sensor.reolink_onvif_pet_detection",
            "binary_sensor.reolink_onvif_visitor_detection",
            "binary_sensor.reolink_onvif_package_detection",
        ],
    },
}


# =============================================================================
# Deployment and per-device overrides
# =============================================================================
# Public/source checkouts may keep shared home topology in the ignored
# `deployment_config.py` file so upstream updates never collide with room and
# entity mappings. Existing private deployments that intentionally version
# topology in app_config.py remain compatible when this file is absent.
DEPLOYMENT_CONFIG_LOADED = False
DEPLOYMENT_CONFIG_KEYS = []
try:
    import deployment_config as _deployment_config
    for _k in dir(_deployment_config):
        if _k.startswith("_"):
            continue
        globals()[_k] = getattr(_deployment_config, _k)
        DEPLOYMENT_CONFIG_KEYS.append(_k)
    DEPLOYMENT_CONFIG_LOADED = True
    del _deployment_config, _k
except ModuleNotFoundError as _deployment_error:
    if _deployment_error.name != "deployment_config":
        raise

# Each physical device then applies its own local overrides.
# Each physical device (e.g. Pi 3B with PTT-only handset, Pi 4 with wakeword)
# places its own `local_prefs.py` at the project root with ONLY the
# values that differ from the defaults defined above. That file is gitignored
# so device-specific configuration never enters version control.
#
# Typical contents of local_prefs.py:
#     WAKEWORD_ENABLED = True
#     HANDSET_PRESENT  = False
#     PTT_ENABLED      = True
#
# We iterate keys explicitly (rather than `from local_prefs import *`)
# so we can record which keys were overridden, which gets logged at boot in
# main.main() for debuggability.
LOCAL_PREFS_LOADED = False
LOCAL_PREFS_KEYS = []
try:
    import local_prefs as _local_prefs
    for _k in dir(_local_prefs):
        if _k.startswith("_"):
            continue
        globals()[_k] = getattr(_local_prefs, _k)
        LOCAL_PREFS_KEYS.append(_k)
    LOCAL_PREFS_LOADED = True
    del _local_prefs, _k
except ImportError:
    pass

# Recompute room-derived compatibility views after local preferences load.
# Explicit legacy overrides remain honored for installations that still set
# these names directly.
_CONFIG_OVERRIDE_KEYS = set(DEPLOYMENT_CONFIG_KEYS) | set(LOCAL_PREFS_KEYS)
if "DEFAULT_SONOS_ROOM" not in _CONFIG_OVERRIDE_KEYS:
    DEFAULT_SONOS_ROOM = _derive_default_sonos_room()
if "SONOS_PLAYERS" not in _CONFIG_OVERRIDE_KEYS:
    SONOS_PLAYERS = _derive_sonos_players()
if "APPLE_TV_ENTITY" not in _CONFIG_OVERRIDE_KEYS:
    APPLE_TV_ENTITY = _default_room_setting("tv")
if "APPLE_TV_REMOTE" not in _CONFIG_OVERRIDE_KEYS:
    APPLE_TV_REMOTE = _default_room_setting("tv_remote")

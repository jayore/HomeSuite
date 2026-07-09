"""Example per-device Home Suite overrides.

Copy this file to local_prefs.py on each device and uncomment only the values
that should differ from app_config.py. Keep local_prefs.py out of git because it
usually describes one specific Pi, room, audio device, or speaker target.
"""

# ---------------------------------------------------------------------------
# Device identity and hardware role
# ---------------------------------------------------------------------------

# Friendly source id for request context, logs, dashboards, and future satellites.
# SOURCE_ID = "kitchen_pi"

# Set True on a physical handset build where GPIO/off-hook handling is present.
# HANDSET_PRESENT = False

# Enable push-to-talk / handset interaction on devices with the needed hardware.
# PTT_ENABLED = False

# Enable wake-word listening for far-field devices.
# WAKEWORD_ENABLED = False

# Wake-word engine. Common values are "openwakeword" or "porcupine".
# WAKEWORD_ENGINE = "openwakeword"

# If a handset needs a slight delay before the chime so it reaches your ear.
# START_CHIME_DELAY_SECONDS = 0.0


# ---------------------------------------------------------------------------
# Room defaults
# ---------------------------------------------------------------------------

# Default room used when a request has no better source-room context.
# DEFAULT_ROOM = "living_room"

# Default Sonos room for music, announcements, and routed assistant speech.
# DEFAULT_SONOS_ROOM = "living_room"

# Route assistant responses locally or through a Sonos speaker.
# Supported values: "local", "sonos"
# ASSISTANT_AUDIO_OUTPUT_MODE = "local"

# Optional explicit room for assistant speech when ASSISTANT_AUDIO_OUTPUT_MODE="sonos".
# If unset, Home Suite falls back to request room, then DEFAULT_SONOS_ROOM.
# ASSISTANT_AUDIO_OUTPUT_ROOM = "living_room"


# ---------------------------------------------------------------------------
# Audio device hints
# ---------------------------------------------------------------------------

# ALSA output device for local playback. Examples:
# HOMESUITE_ALSA_DEVICE = "default"
# HOMESUITE_ALSA_DEVICE = "dmix:CARD=Device,DEV=0"

# Sounddevice input matching/index are usually set as systemd environment vars,
# but can also be captured here for documentation of the device role.
# HOMESUITE_SD_INPUT_MATCH = "USB PnP Sound Device"
# HOMESUITE_SD_INPUT_INDEX = 1
# HOMESUITE_SD_SAMPLERATE = 48000


# ---------------------------------------------------------------------------
# Conversation and speech behavior
# ---------------------------------------------------------------------------

# Conversational fallback model.
# CHATGPT_MODEL = "gpt-5.4-mini"

# Supported values: "gtts", "home_assistant"
# SONOS_TTS_BACKEND = "gtts"

# Required only when SONOS_TTS_BACKEND="home_assistant".
# SONOS_HA_TTS_ENTITY = "tts.google_en_com"

# gTTS regional voice. Common TLDs: "com", "co.uk", "com.au", "ie".
# TTS_TLD = "ie"

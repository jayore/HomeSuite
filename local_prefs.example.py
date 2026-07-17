"""Example per-device Home Suite overrides.

Copy this file to local_prefs.py on each device and uncomment only the values
that should differ from app_config.py. Keep local_prefs.py out of git because it
usually describes one specific Pi, room, audio device, or speaker target.
"""

# ---------------------------------------------------------------------------
# Device role and hardware
# ---------------------------------------------------------------------------

# Enable a maintained push-to-talk input. Home Suite listens for as long as the
# selected BCM GPIO remains at PTT_LISTEN_LEVEL.
# PTT_ENABLED = False
# PTT_GPIO_PIN = 11
# PTT_LISTEN_LEVEL = "low"  # "low" for a switch to ground; "high" for inverse wiring
# PTT_END_BEHAVIOR = "cancel"  # "submit" for a conventional release-to-send button
# WAKEWORD_SUPPRESS_WHILE_PTT = True

# Optional auxiliary GPIO buttons execute command phrases but do not control
# microphone capture. Button IDs connect the pin and action maps.
# PHYSICAL_BUTTONS_ENABLED = False
# PHYSICAL_BUTTON_ACTIVE_LOW = True
# PHYSICAL_BUTTON_PULL_UP = True
# PHYSICAL_BUTTON_PINS = {
#     1: 2,
#     2: 3,
# }
# PHYSICAL_BUTTON_ACTIONS = {
#     1: {"press": "turn on the office light", "long_press": "turn it off"},
#     2: {"press": "toggle play pause"},
# }

# Enable wake-word listening for far-field devices.
# WAKEWORD_ENABLED = False
#
# PTT and wake-word listening may both be enabled on one device. With
# WAKEWORD_SUPPRESS_WHILE_PTT=True, wake-word detection pauses only during an
# active PTT session and resumes when that session closes.

# Wake-word engine. Common values are "openwakeword" or "porcupine".
# WAKEWORD_ENGINE = "openwakeword"
# WAKEWORD_MODEL = "your_model_label"
# WAKEWORD_MODEL_PATHS = ["/home/your-user/wake_models/model.onnx"]
# WAKEWORD_THRESHOLD = 0.5
# WAKEWORD_VAD_THRESHOLD = 0.5

# Completed wakeword commands can use file STT independently of PTT streaming.
# WAKEWORD_USE_STREAMING_STT = True
# WAKEWORD_STT_MODE = "realtime_stream"

# Wakeword-only endpoint tuning. Audio is still buffered and streamed during
# the cue guard. The rolling window tolerates isolated VAD false positives.
# WAKEWORD_STREAM_CUE_GUARD_MS = 1000
# WAKEWORD_STREAM_ENDPOINT_WINDOW_MS = 700
# WAKEWORD_STREAM_ENDPOINT_MIN_SILENCE_RATIO = 0.70
# WAKEWORD_STREAM_ENDPOINT_TRAILING_SILENCE_MS = 80

# Fast rearm for devices whose completion cue does not trigger their model.
# This keeps the cue unchanged but permits a new wakeword during its tail.
# WAKEWORD_SUPPRESS_DURING_SFX = False
# WAKEWORD_REARM_SFX_DRAIN_MAX_SEC = 0.0

# Interrupt local assistant speech with another wakeword. A separate speaking
# threshold keeps normal idle detection strict. Hardware AEC is strongly
# preferred for reliable barge-in.
# WAKEWORD_ASYNC_TTS_ENABLED = True
# WAKEWORD_BARGE_IN_ENABLED = True
# WAKEWORD_BARGE_IN_THRESHOLD = 0.4

# Optional delay before the first PTT cue; usually zero for a held button.
# START_CHIME_DELAY_SECONDS = 0.0


# ---------------------------------------------------------------------------
# Room defaults
# ---------------------------------------------------------------------------

# Default room used when a request has no better source-room context.
# DEFAULT_ROOM = "living_room"

# DEFAULT_SONOS_ROOM is normally derived from DEFAULT_ROOM and app_config.ROOMS.
# Override it only for compatibility with a custom flat SONOS_PLAYERS map.
# DEFAULT_SONOS_ROOM = "living room"

# Route assistant responses locally or through a Sonos speaker.
# Supported values: "local", "sonos"
# ASSISTANT_AUDIO_OUTPUT_MODE = "local"

# Optional explicit room for assistant speech when ASSISTANT_AUDIO_OUTPUT_MODE="sonos".
# If unset, Home Suite falls back to request room, then DEFAULT_SONOS_ROOM.
# ASSISTANT_AUDIO_OUTPUT_ROOM = "living_room"


# ---------------------------------------------------------------------------
# Audio device hints
# ---------------------------------------------------------------------------

# Optional ALSA output override for local playback. Leave unset to use the
# service environment or ALSA default. Stable card IDs survive card renumbering.
# HOMESUITE_ALSA_DEVICE = "default"
# HOMESUITE_ALSA_DEVICE = "dmix:CARD=Device,DEV=0"

# A profile keeps microphone selection, fixed hardware gain, and frontend
# processing together. Mixer fields may be None for a mic with onboard gain.
# AUDIO_INPUT_PROFILE = {
#     "name": "room_mic",
#     "device_match": "USB PnP Sound Device",
#     "device_index": None,
#     "sample_rate": 48000,
#     "channels": 1,
#     "stream_latency": "low",  # try "high" if source status reports overflow
#     "strict_device_match": True,
#     "alsa_card": None,
#     "mixer_control": None,
#     "mixer_value": None,
#     "verify_interval_sec": 0,
#     "noise_suppression_level": 2,
#     "auto_gain_dbfs": 0,
#     "volume_multiplier": 1.0,
#     "command_noise_suppression_level": 0,
#     "command_auto_gain_dbfs": 0,
#     "command_volume_multiplier": 1.0,
#     "ptt_volume_multiplier": 1.0,
#     "aec_mode": "hardware",  # use "none" unless the mic provides AEC
# }


# ---------------------------------------------------------------------------
# Conversation and speech behavior
# ---------------------------------------------------------------------------

# Conversational fallback model.
# CHATGPT_MODEL = "gpt-5.4-mini"
# CHATGPT_WEB_SEARCH_ENABLED = True
# CHATGPT_WEB_SEARCH_MODEL = CHATGPT_MODEL

# Supported values: "gtts", "home_assistant"
# SONOS_TTS_BACKEND = "gtts"

# Required only when SONOS_TTS_BACKEND="home_assistant".
# SONOS_HA_TTS_ENTITY = "tts.google_en_com"

# gTTS regional voice. Common TLDs: "com", "co.uk", "com.au", "ie".
# TTS_TLD = "ie"


# ---------------------------------------------------------------------------
# Companion API
# ---------------------------------------------------------------------------

# The in-process HTTP/WebSocket server is enabled by default and requires the
# shared HOMESUITE_HTTP_API_KEY from private_config.py. Disable it on devices
# that should not accept companion-client connections.
# UNIFIED_SERVER_ENABLED = True
# UNIFIED_SERVER_PORT = 8765

# A voice satellite still records, transcribes, and plays responses locally,
# but sends transcript text to another Home Suite node for command routing and
# execution. Enter either the brain server URL or its full /command URL. The
# satellite defaults to this node's hostname as its stable source ID and to
# DEFAULT_ROOM as its physical room.
# COMMAND_PROCESSING_MODE = "satellite"
# SATELLITE_BRAIN_URL = "http://homesuite-brain.local:8765"
# SATELLITE_SOURCE_ID = None
# SATELLITE_SOURCE_ROOM = None
# SATELLITE_COMMAND_TIMEOUT_SECONDS = 20.0

# The separate authenticated management console defaults to every LAN
# interface on port 8766. Bind to 127.0.0.1 when using an SSH tunnel only.
# CONSOLE_HOST = "0.0.0.0"
# CONSOLE_PORT = 8766


# ---------------------------------------------------------------------------
# Local diagnostics and privacy
# ---------------------------------------------------------------------------

# Runtime and structured command logs rotate automatically. Command metadata is
# recorded without utterance text by default; opt in only while diagnosing a
# problem, then turn it back off.
# RUNTIME_LOG_MAX_BYTES = 5 * 1024 * 1024
# RUNTIME_LOG_BACKUP_COUNT = 3
# COMMAND_EVENT_LOG_ENABLED = True
# COMMAND_EVENT_LOG_STORE_TEXT = False
# COMMAND_EVENT_LOG_MAX_BYTES = 2 * 1024 * 1024
# COMMAND_EVENT_LOG_BACKUP_COUNT = 3

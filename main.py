"""HomeSuite device runtime and voice-interaction orchestrator.

This process owns the Raspberry Pi hardware lifecycle and composes the shared
audio, transcription, routing, Home Assistant, applet, and response modules.
There are two intentionally distinct capture paths:

* PTT opens an utterance when the handset/button state requests it.
* Wakeword mode continuously scores one microphone stream and hands that same
  stream to command capture after detection, preserving one-breath commands.

Both paths converge at :func:`process_audio`, after which command routing and
response behavior are shared. Keep trigger-specific timing and audio changes
inside their respective capture paths so a wakeword experiment cannot regress
PTT-only devices.
"""

# --- Streaming resampler: pre-import once at boot (avoid first-utterance stall) ---
try:
    from scipy.signal import resample_poly as _resample_poly  # type: ignore
except Exception:
    _resample_poly = None

import asyncio
from realtime_streaming_stt import StreamingTranscriber
from wakeword_listener import WakewordListener
# ---------------------------------------------------------------------------
# RT streaming warmup (OFFHOOK): prime the realtime streaming STT connection
# so the first utterance doesn't lose its opening words.
# Controlled via:
#   PIPHONE_STT_MODE in ("realtime_stream","rt_stream","realtime_streaming")
#   PIPHONE_RT_WARMUP_SILENCE_MS (default 700)
#   PIPHONE_RT_WARMUP_MAX_AGE_SEC (default 20)
# ---------------------------------------------------------------------------

_RT_WARMUP_STATE = {"rt": None, "ts": 0.0}

def _rt_warmup_enabled() -> bool:
    try:
        m = (os.getenv("PIPHONE_STT_MODE", "whisper") or "").strip().lower()
    except Exception:
        m = "whisper"
    return m in ("realtime_stream", "rt_stream", "realtime_streaming")

def _rt_warmup_cancel(rt):
    try:
        if rt is not None:
            rt.cancel()
    except Exception:
        pass

def _rt_warmup_start_on_offhook():
    """Start and prime a streaming transcriber when the PTT handset goes off-hook."""
    try:
        from app_config import RT_OFFHOOK_WARMUP_ENABLED
        if not bool(RT_OFFHOOK_WARMUP_ENABLED):
            logging.info("RT_WARMUP_OFFHOOK_DISABLED")
            return False
    except Exception:
        pass
    if not _rt_warmup_enabled():
        return
    # Only warm once per offhook session (or until taken).
    if _RT_WARMUP_STATE.get("rt") is not None:
        return

    rt = None
    t0 = time.time()
    try:
        rt_model = (os.getenv("PIPHONE_RT_MODEL", "") or "").strip() or "gpt-4o-transcribe"
        rt_lang  = (os.getenv("PIPHONE_RT_LANG", "en") or "en").strip() or "en"
        logging.info("RT_WARMUP_OFFHOOK_BEGIN model=%r lang=%r", rt_model, rt_lang)

        # Construct the transcriber (this may not fully connect until first append).
        try:
            rt = StreamingTranscriber(model=rt_model, language=rt_lang)
        except TypeError:
            rt = StreamingTranscriber(model=rt_model)

        # Feed silence to force any lazy websocket/TLS/session init to complete BEFORE user speaks.
        try:
            warm_ms = int(float(os.getenv("PIPHONE_RT_WARMUP_SILENCE_MS", "700")))
        except Exception:
            warm_ms = 700
        warm_ms = max(50, min(3000, warm_ms))

        sr = 24000
        # 10ms @ 24kHz = 240 samples = 480 bytes (int16)
        frame_bytes = 480
        chunks = max(1, int(round(warm_ms / 10.0)))
        silence = b"\x00" * frame_bytes

        for _ in range(chunks):
            rt.append_pcm16(silence, sr_in=sr)

        _RT_WARMUP_STATE["rt"] = rt
        _RT_WARMUP_STATE["ts"] = time.time()
        logging.info("RT_WARMUP_OFFHOOK_OK warm_ms=%d dt=%.3f", warm_ms, time.time() - t0)
    except Exception:
        logging.exception("RT_WARMUP_OFFHOOK_FAIL")
        _rt_warmup_cancel(rt)
        _RT_WARMUP_STATE["rt"] = None
        _RT_WARMUP_STATE["ts"] = 0.0

def _rt_warmup_take_if_ready():
    """Take ownership of the warmed transcriber (one-shot). Returns rt or None."""
    rt = _RT_WARMUP_STATE.get("rt")
    ts = float(_RT_WARMUP_STATE.get("ts") or 0.0)
    if rt is None:
        return None
    try:
        max_age = float(os.getenv("PIPHONE_RT_WARMUP_MAX_AGE_SEC", "20"))
    except Exception:
        max_age = 20.0
    age = time.time() - ts
    if age > max_age:
        logging.info("RT_WARMUP_STALE age=%.2f max_age=%.2f; cancelling", age, max_age)
        _rt_warmup_cancel(rt)
        _RT_WARMUP_STATE["rt"] = None
        _RT_WARMUP_STATE["ts"] = 0.0
        return None

    # one-shot take
    _RT_WARMUP_STATE["rt"] = None
    _RT_WARMUP_STATE["ts"] = 0.0
    logging.info("RT_WARMUP_TAKEN age=%.2f", age)
    return rt
#!/usr/bin/env python3
import os

try:
    from env_compat import install_homesuite_env_aliases
    install_homesuite_env_aliases()
except Exception:
    pass
import time
import types
import uuid
from datetime import datetime
from realtime_transcribe import realtime_transcribe_wav
from pathlib import Path

from sonos_utils import homesuite_media_url_for_path, sonos_play_media


def _env_truthy(name: str, default: str = "1") -> bool:
    v = os.environ.get(name, default)
    if v is None:
        return False
    v = str(v).strip().lower()
    return v in ("1", "true", "yes", "y", "on")


# =========================
# RUNTIME CONFIG HELPERS
# Read defaults from app_config.py, including any local_prefs.py
# overrides imported there.
# =========================
def _prefs_module():
    try:
        import app_config as _prefs
        return _prefs
    except Exception:
        return None


def _pref_value(name: str, default=None):
    try:
        prefs = _prefs_module()
        if prefs is None:
            return default
        return getattr(prefs, name, default)
    except Exception:
        return default


def _pref_bool(name: str, default: bool = False) -> bool:
    try:
        v = _pref_value(name, default)
        if isinstance(v, bool):
            return v
        if v is None:
            return bool(default)
        return str(v).strip().lower() in ("1", "true", "yes", "y", "on")
    except Exception:
        return bool(default)


def _pref_str(name: str, default: str = "") -> str:
    try:
        v = _pref_value(name, default)
        if v is None:
            return str(default)
        return str(v)
    except Exception:
        return str(default)


def _pref_float(name: str, default: float = 0.0) -> float:
    try:
        v = _pref_value(name, default)
        return float(v)
    except Exception:
        return float(default)


def _ptt_enabled() -> bool:
    return _pref_bool("PTT_ENABLED", False)


def _wakeword_enabled() -> bool:
    return _pref_bool("WAKEWORD_ENABLED", False)


def _wakeword_engine_name() -> str:
    return (_pref_str("WAKEWORD_ENGINE", "porcupine") or "porcupine").strip().lower()


def _wakeword_model_name() -> str:
    return (_pref_str("WAKEWORD_MODEL", "") or "").strip()


def _wakeword_only_onhook() -> bool:
    return _pref_bool("WAKEWORD_ONLY_ONHOOK", True)


def _wakeword_chime_enabled() -> bool:
    return _pref_bool("WAKEWORD_CHIME", False)


def _wakeword_rearm_sec() -> float:
    return _pref_float("WAKEWORD_REARM_SEC", 1.5)


def _assistant_audio_output_mode() -> str:
    return (_pref_str("ASSISTANT_AUDIO_OUTPUT_MODE", "local") or "local").strip().lower()


def _assistant_audio_output_room():
    v = _pref_value("ASSISTANT_AUDIO_OUTPUT_ROOM", None)
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _chatgpt_model() -> str:
    return (_pref_str("CHATGPT_MODEL", "gpt-5.4-mini") or "gpt-5.4-mini").strip()


def _sonos_tts_backend() -> str:
    return (_pref_str("SONOS_TTS_BACKEND", "gtts") or "gtts").strip().lower()


def _sonos_ha_tts_entity():
    v = _pref_value("SONOS_HA_TTS_ENTITY", None)
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _wakeword_suppress_during_sfx() -> bool:
    return _pref_bool("WAKEWORD_SUPPRESS_DURING_SFX", True)


def _wakeword_barge_in_enabled() -> bool:
    """Return whether local wakeword TTS may be interrupted by a new detection."""
    return (
        _pref_bool("WAKEWORD_BARGE_IN_ENABLED", False)
        and _assistant_audio_output_mode() == "local"
    )


def _wakeword_detection_threshold() -> float:
    """Use the separately tuned threshold only while local TTS is playing."""
    normal = _pref_float("WAKEWORD_THRESHOLD", 0.5)
    if _wakeword_barge_in_enabled() and bool(globals().get("is_speaking")):
        return _pref_float("WAKEWORD_BARGE_IN_THRESHOLD", normal)
    return normal


def _wakeword_async_tts_enabled() -> bool:
    """Keep wakeword response speech off the detector thread when enabled."""
    return (
        _pref_bool("WAKEWORD_ASYNC_TTS_ENABLED", False)
        and _assistant_audio_output_mode() == "local"
    )


# =========================
# RT_WARMUP
# Realtime streaming STT warmup helpers
# =========================

def warmup_rt_streaming_on_boot() -> bool:
    """
    Best-effort boot-time priming for realtime streaming STT.
    This intentionally does NOT maintain a long-lived readiness flag.
    It simply exercises the streaming path once during startup so the first
    real utterance is less likely to pay one-time setup costs.
    """
    try:
        from app_config import RT_WARMUP_ON_BOOT_ENABLED
        if not bool(RT_WARMUP_ON_BOOT_ENABLED):
            logging.info("RT_WARMUP_BOOT_DISABLED")
            return False
    except Exception:
        pass

    if not _rt_warmup_enabled():
        logging.info("RT_WARMUP_BOOT_SKIP mode_disabled")
        return False

    rt = None
    t0 = time.time()
    try:
        rt_model = (os.getenv("PIPHONE_RT_MODEL", "") or "").strip() or "gpt-4o-transcribe"
        rt_lang = (os.getenv("PIPHONE_RT_LANG", "en") or "en").strip() or "en"
        logging.info("RT_WARMUP_BOOT_BEGIN model=%r lang=%r", rt_model, rt_lang)

        try:
            rt = StreamingTranscriber(model=rt_model, language=rt_lang)
        except TypeError:
            rt = StreamingTranscriber(model=rt_model)

        try:
            warm_ms = int(float(os.getenv("PIPHONE_RT_WARMUP_SILENCE_MS", "700")))
        except Exception:
            warm_ms = 700
        warm_ms = max(50, min(3000, warm_ms))

        sr = 24000
        frame_bytes = 480
        chunks = max(1, int(round(warm_ms / 10.0)))
        silence = b"\x00" * frame_bytes

        for _ in range(chunks):
            rt.append_pcm16(silence, sr_in=sr)

        try:
            rt.commit_and_wait()
        except Exception:
            pass

        logging.info("RT_WARMUP_BOOT_OK warm_ms=%d dt=%.3f", warm_ms, time.time() - t0)
        return True
    except Exception:
        logging.exception("RT_WARMUP_BOOT_FAIL")
        return False
    finally:
        _rt_warmup_cancel(rt)
        logging.info("RT_WARMUP_BOOT_DONE dt=%.3f", time.time() - t0)


def warmup_audio_on_boot():
    """
    Pre-warm ALSA/sounddevice so the FIRST offhook behaves like the SECOND.
    This intentionally runs at service startup (systemd boot path).
    """
    try:
        if not _env_truthy("PIPHONE_WARMUP_AUDIO_ON_BOOT", "1"):
            logging.info("WARMUP_AUDIO_BOOT_SKIP env=PIPHONE_WARMUP_AUDIO_ON_BOOT")
            return
    except Exception:
        # If env parsing fails for any reason, don't block startup.
        return

    t0 = time.monotonic()
    logging.info("WARMUP_AUDIO_BOOT_BEGIN")

    # 1) Run the same ensure/cleanup path you normally do right before recording (if available)
    try:
        if "ensure_audio_device_available" in globals() and callable(globals()["ensure_audio_device_available"]):
            ensure_audio_device_available()
            logging.info("WARMUP_AUDIO_BOOT_ENSURE_OK dt=%.3f", time.monotonic() - t0)
        else:
            logging.info("WARMUP_AUDIO_BOOT_ENSURE_SKIP missing=ensure_audio_device_available")
    except Exception:
        logging.exception("WARMUP_AUDIO_BOOT_ENSURE_FAIL")

    # 2) Open the mic stream once and let it run briefly (forces driver/device warmup)
    try:
        import sounddevice as sd  # local import so failure can't break module import
        from audio_input_profile import (
            enforce_capture_settings,
            get_audio_input_profile,
            pick_sounddevice_input_index,
        )

        profile = get_audio_input_profile()
        enforce_capture_settings(profile, logger=logging, reason="boot_warmup")
        sr = int(profile.get("sample_rate") or 48000)
        channels = int(profile.get("channels") or 1)
        device = pick_sounddevice_input_index(sd, profile)
        dev_name = None
        try:
            d = sd.query_devices(device if device >= 0 else None)
            dev_name = str(d.get("name") or "")
        except Exception:
            pass

        warm_ms = int((os.environ.get("PIPHONE_WARMUP_AUDIO_MS") or "250").strip() or "250")
        with sd.InputStream(
            device=(device if device >= 0 else None),
            channels=channels,
            samplerate=sr,
            dtype="int16",
        ):
            sd.sleep(warm_ms)
        enforce_capture_settings(
            profile,
            logger=logging,
            reason="boot_warmup_post_stream",
            force=True,
        )
        logging.info(
            "WARMUP_AUDIO_BOOT_STREAM_OK ms=%d sr=%d dev=%s name=%r profile=%r dt=%.3f",
            warm_ms,
            sr,
            device,
            dev_name,
            profile.get("name"),
            time.monotonic() - t0,
        )
    except Exception:
        logging.exception("WARMUP_AUDIO_BOOT_STREAM_FAIL")

    logging.info("WARMUP_AUDIO_BOOT_DONE dt=%.3f", time.monotonic() - t0)


PIPHONE_NO_RUNTIME_INIT = (os.environ.get("PIPHONE_NO_RUNTIME_INIT") == "1")
PIPHONE_LIGHT_IMPORT = (os.environ.get("PIPHONE_LIGHT_IMPORT") == "1")

# Give ALSA/devices a moment to settle after boot (skip in test/light modes)
if not PIPHONE_NO_RUNTIME_INIT and not PIPHONE_LIGHT_IMPORT:
    time.sleep(2)

import RPi.GPIO as GPIO
import logging

# =========================
# PERF LOGGING (opt-in)
# Enable with: Environment=PIPHONE_PERF=1
# =========================
_PIPHONE_PERF = (os.environ.get("PIPHONE_PERF", "0").strip() == "1")
# STT STREAMING (Realtime transcription during capture)
PIPHONE_STT_STREAMING = (os.environ.get('PIPHONE_STT_STREAMING','0').strip() == '1')

_PIPHONE_PERF_T0 = time.monotonic()


# =========================
# IDLE_MIC_EXERCISER_V2
# Keep the local mic/driver "warm" BEFORE OFFHOOK by periodically exercising the input device.
# This is local-only (no OpenAI calls, no tokens).
# Controls:
#   PIPHONE_MIC_EXERCISE_BOOT_MS        (default 0)   e.g. 12000 to pre-warm at startup
#   PIPHONE_MIC_EXERCISE_EVERY_SEC      (default 0)   e.g. 300 to keep warm every 5 minutes
#   PIPHONE_MIC_EXERCISE_BURST_MS       (default 250) e.g. 250-500ms per periodic keepalive
# =========================
try:
    _MIC_EXERCISE_THREAD_STARTED
except Exception:
    _MIC_EXERCISE_THREAD_STARTED = False

def _mic_exercise_once(label="idle", duration_ms=250):
    try:
        import sounddevice as sd
        import time
        import logging
        import os

        try:
            duration_ms = int(duration_ms)
        except Exception:
            duration_ms = 250
        if duration_ms <= 0:
            return

        # Avoid exercising while actively off-hook / speaking / processing
        try:
            if globals().get("button_pressed") or globals().get("is_speaking") or globals().get("is_processing"):
                return
        except Exception:
            pass

        # If GPIO available and handset is up, skip
        try:
            if 'GPIO' in globals() and 'GPIO_PIN' in globals():
                try:
                    if GPIO.input(GPIO_PIN) == 0:
                        return
                except Exception:
                    pass
        except Exception:
            pass

        try:
            sd_dev = _pick_sd_input_device_index()
        except Exception:
            sd_dev = -1
        try:
            sr = _pick_sd_samplerate()
        except Exception:
            sr = 48000

        frame_ms = 10
        frame_samples = int(sr * frame_ms / 1000)

        t0 = time.monotonic()
        try:
            _perf("MIC_EXERCISE_BEGIN", label=label, ms=int(duration_ms), sr=int(sr), dev=int(sd_dev))
        except Exception:
            pass

        with sd.InputStream(
            device=(sd_dev if sd_dev >= 0 else None),
            samplerate=sr,
            channels=1,
            dtype="int16",
            blocksize=frame_samples,
            latency="low",
        ) as stream:
            n = max(1, int(round(duration_ms / float(frame_ms))))
            for _ in range(n):
                try:
                    stream.read(frame_samples)
                except Exception:
                    break
                time.sleep(0.001)

        dt = time.monotonic() - t0
        try:
            _perf("MIC_EXERCISE_DONE", label=label, ms=int(duration_ms), dt=round(dt,3))
        except Exception:
            pass
        try:
            logging.info("MIC_EXERCISE_DONE label=%s ms=%s dt=%.3f", label, duration_ms, dt)
        except Exception:
            pass
    except Exception:
        try:
            import logging
            logging.exception("MIC_EXERCISE_FAIL label=%s", label)
        except Exception:
            pass

def _mic_exercise_daemon():
    import time, os
    while True:
        try:
            every = int(float(os.getenv("PIPHONE_MIC_EXERCISE_EVERY_SEC", "0")))
        except Exception:
            every = 0
        if every <= 0:
            return
        time.sleep(every)
        try:
            burst = int(float(os.getenv("PIPHONE_MIC_EXERCISE_BURST_MS", "250")))
        except Exception:
            burst = 250
        _mic_exercise_once(label="idle", duration_ms=burst)

def start_mic_exercise_daemon():
    global _MIC_EXERCISE_THREAD_STARTED
    if _MIC_EXERCISE_THREAD_STARTED:
        return
    _MIC_EXERCISE_THREAD_STARTED = True
    try:
        import threading
        t = threading.Thread(target=_mic_exercise_daemon, daemon=True, name="mic_exercise")
        t.start()
        try:
            import logging
            logging.info("MIC_EXERCISE_DAEMON_STARTED")
        except Exception:
            pass
    except Exception:
        _MIC_EXERCISE_THREAD_STARTED = False


def _rt_keepwarm_should_skip_reason() -> str:
    try:
        if not _rt_warmup_enabled():
            return "disabled"
    except Exception:
        return "disabled"

    try:
        if globals().get("_rt_warmup_inflight"):
            return "inflight"
    except Exception:
        pass

    try:
        if globals().get("is_speaking") or globals().get("is_processing"):
            return "busy"
    except Exception:
        pass

    try:
        if globals().get("button_pressed"):
            return "offhook"
    except Exception:
        pass

    try:
        if 'GPIO' in globals() and 'GPIO_PIN' in globals():
            try:
                if GPIO.input(GPIO_PIN) == 0:
                    return "offhook"
            except Exception:
                pass
    except Exception:
        pass

    return ""

def _rt_keepwarm_daemon():
    while True:
        interval = _rt_keepwarm_interval_sec()
        if interval <= 0:
            logging.info("RT_KEEPWARM_DISABLED")
            return

        time.sleep(interval)

        try:
            age = _rt_warmup_age_sec()
            ready = bool(globals().get("_rt_warmup_ready", False))
        except Exception:
            age = None
            ready = False

        age_disp = "unknown" if age is None else f"{age:.1f}"
        logging.info("RT_KEEPWARM_TICK interval=%.1f ready=%s age=%s", interval, ready, age_disp)

        reason = _rt_keepwarm_should_skip_reason()
        if reason:
            logging.info("RT_KEEPWARM_SKIP reason=%s", reason)
            continue

        logging.info("RT_KEEPWARM_PROBE_BEGIN ready=%s age=%s", ready, age_disp)
        started = _start_rt_warmup(reason="keepwarm", force=True)
        if started:
            logging.info("RT_KEEPWARM_PROBE_STARTED")
        else:
            logging.info("RT_KEEPWARM_SKIP reason=not_started")

def start_rt_keepwarm_daemon():
    if not _rt_keepwarm_enabled():
        logging.info("RT_KEEPWARM_DAEMON_DISABLED")
        return
    global _RT_KEEPWARM_THREAD_STARTED
    if _RT_KEEPWARM_THREAD_STARTED:
        return
    _RT_KEEPWARM_THREAD_STARTED = True
    try:
        import threading
        t = threading.Thread(target=_rt_keepwarm_daemon, daemon=True, name="rt_keepwarm")
        t.start()
        logging.info("RT_KEEPWARM_DAEMON_STARTED interval=%.1f", _rt_keepwarm_interval_sec())
    except Exception:
        _RT_KEEPWARM_THREAD_STARTED = False
        logging.exception("RT_KEEPWARM_DAEMON_START_FAIL")

def _perf(tag: str, **kv):
    if not _PIPHONE_PERF:
        return
    try:
        dt = time.monotonic() - _PIPHONE_PERF_T0
        extra = " ".join([f"{k}={v}" for k, v in kv.items()])
        if extra:
            logging.info("PERF %s t=%.3f %s", tag, dt, extra)
        else:
            logging.info("PERF %s t=%.3f", tag, dt)
    except Exception:
        pass

import subprocess
try:
    import openai
except ImportError:
    openai = None
import requests
import json
import traceback
import sys
import signal
import threading
import fcntl
from gtts import gTTS
import re
import difflib
from spoken_text import normalize_for_tts, tokenize_for_gtts
from media_referents import capture_from_chatgpt_turn
from assistant_context import (
    build_assistant_runtime_context,
    build_web_search_tool,
    contextualize_chat_messages,
)
from volume_controls import handle_volume_controls
from announcement_controls import handle_announcement_controls

from brightness_controls import handle_brightness_controls
import brightness_controls as _brightness_controls_mod
logging.info(f"IMPORT_PATH brightness_controls={getattr(_brightness_controls_mod, '__file__', None)}")
from color_controls import handle_color_controls
from color_resolver import resolve_color_description
from on_off_controls import handle_on_off_controls
from lock_controls import handle_lock_controls
from state_query_controls import handle_state_query_controls
from alarm_controls import handle_alarm_controls, set_command_executor as set_alarm_command_executor
from schedule_controls import handle_schedule_controls

from now_playing_controls import handle_now_playing_controls
from kelvin_controls import handle_kelvin_controls
from rgb_hex_controls import handle_rgb_hex_controls
from applet_controls import handle_applet_controls, register_exclusive_audio_hooks
from datetime import datetime
from collections import deque
from typing import Optional, Tuple, Dict, Any

if not PIPHONE_LIGHT_IMPORT:
    import sounddevice as sd
    import numpy as np
    import webrtcvad
    from scipy.io.wavfile import write as wav_write
else:
    sd = None
    np = None
    webrtcvad = None
    wav_write = None
import atexit

from apple_tv_controls import handle_apple_tv_controls
from plex_controls import handle_plex_controls
from spotify_controls import handle_spotcast_play_controls, resolve_typed_play_request
from sonos_controls import handle_sonos_controls
from sonos_source_controls import handle_sonos_source_controls
import sonos_controls as _sonos_controls_mod
logging.info(f"SONOS_CONTROLS_MODULE_FILE: {_sonos_controls_mod.__file__}")
from spotify_controls import handle_spotify_controls, resolve_play_request, resolve_typed_play_request
from semantic_router import route_utterance, RouteOutcome
from normalize_helpers import (
    _looks_like_device_command,
    _parse_number_words,
    _normalize_device_text,
)

from sonos_spotify_browse_controls import handle_sonos_spotify_browse_play
from sonos_my_sonos_controls import handle_sonos_my_sonos_controls
from radio_controls import handle_pinned_radio_controls
from play_by_name_controls import handle_play_by_name_controls
from spotify_resolver import resolve_spotify_description
from plex_resolver import resolve_plex_description
from scene_script_controls import refresh_runnable_cache, try_run_runnable_from_text, get_runnable_cache_size

# Base directory of the piPhone project (where main.py lives)
BASE_DIR = Path(__file__).resolve().parent


# Debug: focus/now-playing routing (set PIPHONE_DEBUG_FOCUS=1)
PIPHONE_DEBUG_FOCUS = os.getenv('PIPHONE_DEBUG_FOCUS', '0') == '1'

def _dbg_focus(msg: str):
    if not PIPHONE_DEBUG_FOCUS:
        return
    try:
        logging.info(f"[FOCUSDBG] {msg}")
    except Exception:
        pass
    try:
        print(f"[FOCUSDBG] {msg}")
    except Exception:
        pass

# =========================
# CONFIG
# =========================

# gTTS language: keep lang as "en" (en-ie deprecated) + use TLD for voice.
TTS_LANGUAGE = _pref_str("TTS_LANGUAGE", "en") or "en"
TTS_TLD = _pref_str("TTS_TLD", "ie") or "ie"
ANNOUNCEMENT_TTS_TLD = _pref_str("ANNOUNCEMENT_TTS_TLD", TTS_TLD) or TTS_TLD

# Handset-to-ear delay BEFORE playing wake chime
try:
    from app_config import START_CHIME_DELAY_SECONDS
except Exception:
    START_CHIME_DELAY_SECONDS = 0.75
START_CHIME_MIN_INTERVAL_SECONDS = 0.0  # prevent start-chime spam on rapid retries

# Natural endpointing (VAD) — values now live in app_config.py.
# Re-imported here so the existing in-file references keep working.
from app_config import (
    SAMPLE_RATE,
    FRAME_MS,
    FRAME_SAMPLES,
    MAX_UTTERANCE_SECONDS,
    SILENCE_END_MS,
    PRE_ROLL_MS,
    MIN_SPEECH_MS,
    SILENCE_END_FRAMES,
    PRE_ROLL_FRAMES,
    MIN_SPEECH_FRAMES,
)

# Session memory TTL: used for pronoun resolution ("it") and continuity
SESSION_TTL_SECONDS = 30 * 60

# Flat media-player compatibility maps are derived from app_config.ROOMS.
from app_config import DEFAULT_SONOS_ROOM, SONOS_PLAYERS
from room_context import (
    _norm_sonos_room_key,
    _request_room_to_sonos_room,
    _request_default_sonos_room,
    _registry_room_id_from_any,
    _known_room_aliases_for_text,
    _extract_explicit_room_id_from_text,
    _request_default_tv_context,
    _get_last_sonos_master_room,
    _set_last_sonos_master_room,
)
from command_preamble import (
    apply_routing_repairs as _apply_routing_repairs,
    resolve_request_context as _resolve_request_context,
)

# On unrecognized/failed device command, play this sound instead of verbose speech.
ERROR_SOUND = str(BASE_DIR / "assets" / "Pop.mp3")


def _sd_input_device_index():
    """
    Returns an int PortAudio device index from env var PIPHONE_SD_INPUT_INDEX.
    If unset/invalid, returns None so sounddevice uses its default.
    """
    try:
        import os
        v = (os.environ.get("PIPHONE_SD_INPUT_INDEX", "") or "").strip()
        if v == "":
            return None
        return int(v)
    except Exception:
        return None



# --- REPL-safe HA helpers ---

def _pick_sd_input_device_index() -> int:
    """Pick a PortAudio input device index by substring match.
    # If explicitly pinned, use it (no per-utterance probing)
    try:
        import os
        v = (os.environ.get('PIPHONE_SD_INPUT_INDEX','') or '').strip()
        if v:
            return int(v)
    except Exception:
        pass


    Env:
      PIPHONE_SD_INPUT_MATCH: case-insensitive substring (default: 'USB')
    Returns:
      device index, or -1 if not found (meaning: let sounddevice use default)
    """
    try:
        import os
        import sounddevice as sd
        want = (os.environ.get("PIPHONE_SD_INPUT_MATCH", "USB") or "").strip().lower()
        if not want:
            return -1
        for i, d in enumerate(sd.query_devices()):
            try:
                if d.get("max_input_channels", 0) <= 0:
                    continue
                name = str(d.get("name", "")).lower()
                if want in name:
                    return int(i)
            except Exception:
                continue
    except Exception:
        pass
    return -1


# In REPL we often run with PIPHONE_NO_RUNTIME_INIT=1, so runtime init may not define HEADERS/HA_SESSION.
# These helpers prevent NameError and preserve pptest/pplive behavior.


def _pick_sd_samplerate() -> int:
    """Return a fixed capture sample rate.

    We do NOT probe each time (avoids delays and random fall-through).
    Set via env PIPHONE_SD_SAMPLERATE; default 48000.
    """
    try:
        import os
        v = (os.environ.get('PIPHONE_SD_SAMPLERATE', '48000') or '48000').strip()
        sr = int(v)
        if sr <= 0:
            sr = 48000
        return sr
    except Exception:
        return 48000

def _ha_headers_safe():
    # Prefer local HEADERS if present
    try:
        h = globals().get("HEADERS", None)
        if isinstance(h, dict) and h.get("Authorization"):
            return h
    except Exception:
        pass

    # Fall back to ha_client.HEADERS if present
    try:
        import ha_client  # local module
        h2 = getattr(ha_client, "HEADERS", None)
        if isinstance(h2, dict) and h2.get("Authorization"):
            return h2
    except Exception:
        pass

    # Build minimal headers from HA_TOKEN if available
    tok = None
    try:
        tok = globals().get("HA_TOKEN", None)
    except Exception:
        tok = None
    if not tok:
        try:
            from private_config import HA_TOKEN as _TOK  # type: ignore
            tok = _TOK
        except Exception:
            tok = None

    if tok:
        return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    return None

def _ha_session_safe():
    # Prefer local HA_SESSION if present
    try:
        s = globals().get("HA_SESSION", None)
        if s is not None:
            return s
    except Exception:
        pass

    # Fall back to ha_client.HA_SESSION if present
    try:
        import ha_client
        s2 = getattr(ha_client, "HA_SESSION", None)
        if s2 is not None:
            return s2
    except Exception:
        pass

    # Only create a session in live mode; in pptest return None.
    live = False
    try:
        live = str(__import__("os").environ.get("PIPHONE_LIVE", "")).strip() == "1"
    except Exception:
        live = False
    if not live:
        return None

    try:
        import requests
        return requests.Session()
    except Exception:
        return None
# --- /REPL-safe HA helpers ---

# =========================
# SECRETS / SETUP
# =========================

try:
    from private_config import OPENAI_API_KEY, HA_URL, HA_TOKEN, PLEX_URL, PLEX_TOKEN

    import ha_client
    from ha_client import configure_ha, call_ha_service, ha_get_states, ha_get_state
    configure_ha(ha_url=HA_URL, ha_token=HA_TOKEN)
    # --- Patch B: wrap HA service calls so successful HA actions count as ACTION_OCCURRED ---
    # This fixes the "error tone after successful silent actions" regression when modules return ''.
    _PIPHONE_HA_WRAPPER_INSTALLED = True
    _piphone_real_call_ha_service = call_ha_service

    def call_ha_service(service, data=None, *args, **kwargs):
        """
        Wrapper around ha_client.call_ha_service that marks ACTION_OCCURRED only when
        the HA call appears successful.
        Accepts extra args/kwargs to be forward-compatible with ha_client changes.
        """
        try:
            if service == "media_player/media_play":
                import inspect
                stk = inspect.stack()
                caller1 = stk[1].function if len(stk) > 1 else "?"
                caller2 = stk[2].function if len(stk) > 2 else "?"
                caller3 = stk[3].function if len(stk) > 3 else "?"
                logging.info(
                    "HA_MEDIA_PLAY_CALLER service=%r data=%r caller1=%s caller2=%s caller3=%s",
                    service,
                    data,
                    caller1,
                    caller2,
                    caller3,
                )
        except Exception:
            pass

        t_ha0 = time.monotonic()
        _perf('HA_CALL', svc=service)
        resp = _piphone_real_call_ha_service(service, data, *args, **kwargs)
        _perf('HA_DONE', svc=service, dt=round(time.monotonic()-t_ha0, 3), resp_type=type(resp).__name__)
        try:
            ok = False
            if resp is None:
                ok = False
            elif isinstance(resp, bool):
                ok = resp
            elif hasattr(resp, "status_code"):
                try:
                    sc = int(getattr(resp, "status_code", 0) or 0)
                except Exception:
                    sc = 0
                ok = (200 <= sc < 300)
            else:
                # If ha_client returns a dict/string/etc on success, treat it as success.
                ok = True

            if ok:
                try:
                    mark_action_occurred()
                except Exception:
                    pass
        except Exception:
            pass
        return resp
    # --- /Patch B ---

    # Alias HA client globals for legacy call-sites (REPL-safe)
    try:
        HEADERS = ha_client.HEADERS
        HA_SESSION = ha_client.HA_SESSION
    except Exception:
        # In pptest / partial init, ha_client may not be configured yet.
        pass
except ImportError:
    print("Error: Could not import secrets. Please create private_config.py with your API keys.")
    sys.exit(1)

LOG_PATH = str(BASE_DIR / "homesuite.log")
try:
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    # Make logging deterministic: remove any pre-existing handlers.
    for h in list(root.handlers):
        root.removeHandler(h)

    fmt = logging.Formatter("%(asctime)s - %(levelname)s - %(threadName)s - %(message)s")

    fh = logging.FileHandler(LOG_PATH)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    logging.info(f"LOGGING_INIT OK pid={os.getpid()} log_path={LOG_PATH}")

    # Single-instance lock (prevents duplicate chimes / GPIO contention)
    if not PIPHONE_NO_RUNTIME_INIT and os.environ.get('PIPHONE_SKIP_PID_LOCK') != '1':
        try:
            _lock_fh = open("/tmp/homesuite.lock", "w")
            fcntl.flock(_lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            _lock_fh.write(str(os.getpid()))
            _lock_fh.flush()
            logging.info("PID_LOCK_ACQUIRED /tmp/homesuite.lock")
        except Exception as e:
            logging.error(f"PID_LOCK_FAILED: {e} (another instance likely running). Exiting.")
            raise SystemExit(1)
    else:
        logging.info("PID_LOCK_SKIPPED (PIPHONE_NO_RUNTIME_INIT=1)")

except Exception as e:
    print(f"LOGGING_INIT FAILED: {e}")

# GPIO setup
GPIO_PIN = 11
if not PIPHONE_NO_RUNTIME_INIT:
    try:
        GPIO.cleanup()
    except Exception:
        pass
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(GPIO_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
else:
    logging.info("GPIO_INIT_SKIPPED (PIPHONE_NO_RUNTIME_INIT=1)")

# Audio configuration
FILENAME = str(BASE_DIR / "recording.wav")
TEMP_AUDIO = "/tmp/tts_output.mp3"
ASSETS_DIR = BASE_DIR / "assets"
START_SOUND   = str(ASSETS_DIR / "Blow.mp3")
FINISH_SOUND  = str(ASSETS_DIR / "Glass.mp3")

# Force mpg123 to ALSA + your default device (respects .asoundrc)
# Force mpg123 to ALSA. Use a specific ALSA device to avoid intermittent "default" mapping issues.
# Override via env: PIPHONE_ALSA_DEVICE (examples from `aplay -L`: plughw:CARD=Device,DEV=0)
PIPHONE_ALSA_DEVICE = os.environ.get("PIPHONE_ALSA_DEVICE", "default")
MPG123_CMD = ["mpg123", "-o", "alsa", "-a", PIPHONE_ALSA_DEVICE, "-q"]

# Track nonblocking mpg123 processes (chimes/fx) so TTS can preempt them.
active_fx_procs = []  # list[subprocess.Popen]
OPENAI_CLIENT = (openai.OpenAI(api_key=OPENAI_API_KEY) if openai is not None else None)

# =========================
# GLOBAL STATE
# =========================

is_speaking = False
is_processing = False
button_pressed = False
_WAKEWORD_LISTENER = None
_WAKEWORD_DETECTION_IN_PROGRESS = False

last_start_chime_ts = 0.0


_audio_ensured_in_session = False  # reset per off-hook session

_start_delay_applied_in_session = False

_start_chime_played_in_session = False
  # reset on handset lift; gates START_CHIME_DELAY_SECONDS

last_tts_end_ts = 0.0
TTS_COOLDOWN_SECONDS = 0.8
lock = threading.Lock()
audio_device_lock = threading.Lock()

# Track current TTS proc so we can stop it instantly on hang-up
current_tts_proc = None
tts_proc_lock = threading.Lock()

# Session memory
last_interaction_ts = 0

# Joke memory (avoid repeats)
from collections import deque
from weather_utils import geocode_location, _ha_local_weather, _open_meteo_weather
from transport_helpers import (
    get_transport_focus as _get_transport_focus,
    set_transport_focus as _set_transport_focus,
    ha_ok as _ha_ok,
    get_state as _get_state,
    is_playing as _is_playing,
    is_activeish as _is_activeish,
    pick_sonos_player as _pick_sonos_player,
    mark_transport as _mark_transport,
    focus_is_valid as _focus_is_valid,
    call_media_transport as _call_media_transport,
    get_state_obj as _get_state_obj,
    get_attr as _get_attr,
    get_state_str as _get_state_str,
    ensure_apple_tv_awake as _ensure_apple_tv_awake,
    maybe_turn_on_tv_scene as _maybe_turn_on_tv_scene,
    ensure_apple_tv_app as _ensure_apple_tv_app,
    get_local_transport_context as _get_local_transport_context,
    decide_local_play_pause_toggle as _decide_local_play_pause_toggle,
)
from phonetic_repairs import (
    should_apply_routing_repairs as _should_apply_routing_repairs,
    apply_phonetic_routing_repairs as _apply_phonetic_routing_repairs,
    apply_phonetic_device_repairs as _apply_phonetic_device_repairs,
    should_try_device_repairs_pass2 as _should_try_device_repairs_pass2,
)
from device_phrase_helpers import (
    sanitize_device_phrase as _sanitize_device_phrase,
    light_entity_id as _light_entity_id,
    resolve_light_target as _resolve_light_target,
    try_light_turn_on as _try_light_turn_on,
    normalize_scene_phrase as _normalize_scene_phrase,
)
from request_context import (
    build_request_context,
    replace_current_request_context,
    set_current_request_context,
    get_active_room_for_request_defaults,
)
from event_log import log_command_event
from home_registry import get_room, find_room_by_alias
import interaction_flow
import command_dispatch
from command_dispatch import (
    _now_ts,
    mark_action_occurred,
    process_device_commands,
    _strip_for_tts,
    reset_dispatch_state,
)
# Wire callbacks into command_dispatch now that both the module is imported
# and HA/OpenAI objects are defined above.
command_dispatch.call_ha_service = call_ha_service
command_dispatch.ha_get_states = ha_get_states
command_dispatch.ha_get_state = ha_get_state
command_dispatch.OPENAI_CLIENT = OPENAI_CLIENT

RECENT_JOKES_MAX = 50
recent_jokes = deque(maxlen=RECENT_JOKES_MAX)  # store full joke strings

# Semantic-router ChatGPT continuation memory
last_chatgpt_ts: Optional[float] = None

# _TEXT_CONFIRM_CONTEXT and helpers live in command_dispatch (imported below).

# =========================
# HELPERS
# =========================


def _trace_audio_event(tag: str, **kwargs):
    try:
        import inspect
        stk = inspect.stack()
        caller1 = stk[1].function if len(stk) > 1 else "?"
        caller2 = stk[2].function if len(stk) > 2 else "?"
        caller3 = stk[3].function if len(stk) > 3 else "?"
        extra = " ".join(f"{k}={v!r}" for k, v in (kwargs or {}).items())
        logging.info(
            "TRACE_AUDIO_EVENT tag=%s caller1=%s caller2=%s caller3=%s %s",
            tag, caller1, caller2, caller3, extra
        )
    except Exception:
        pass


# Synchronous SFX-playing counter.
#
# play_sound("start"/"finish"/"error", blocking=False) spawns a worker thread
# that ultimately calls play_mp3_blocking() -> subprocess.run(...). That path
# does NOT touch active_fx_procs (only play_mp3_nonblocking does). Without an
# explicit signal, wakeword gating would be blind to start/finish/error chimes.
#
# We increment this counter synchronously at play_sound() entry (BEFORE the
# worker thread is spawned, eliminating the race where wakeword could rearm
# while the thread is still scheduling) and decrement it when the worker
# finishes. _is_sfx_playing() consults both this counter AND active_fx_procs.
_sfx_play_counter = 0
_sfx_play_counter_lock = threading.Lock()


def _sfx_enter():
    global _sfx_play_counter
    with _sfx_play_counter_lock:
        _sfx_play_counter += 1
        try:
            logging.info("SFX_ENTER counter=%s", _sfx_play_counter)
        except Exception:
            pass


def _sfx_exit():
    global _sfx_play_counter
    with _sfx_play_counter_lock:
        _sfx_play_counter = max(0, _sfx_play_counter - 1)
        try:
            logging.info("SFX_EXIT counter=%s", _sfx_play_counter)
        except Exception:
            pass


def _is_sfx_playing() -> bool:
    """
    Return True if any SFX (start chime, finish chime, error tone) is currently
    playing through the speaker.

    Consults two independent signals:
      1) the synchronous _sfx_play_counter (set by play_sound() worker threads),
      2) the legacy ``active_fx_procs`` list (set by play_mp3_nonblocking()).

    This is the seam wakeword gating uses to avoid self-retriggering on its own
    output cues bleeding back into an always-open mic.
    """
    try:
        with _sfx_play_counter_lock:
            if _sfx_play_counter > 0:
                return True
    except Exception:
        pass
    try:
        procs = list(active_fx_procs or [])
    except Exception:
        return False
    for p in procs:
        try:
            if p is not None and p.poll() is None:
                return True
        except Exception:
            continue
    return False


from command_dispatch import (
    clear_text_confirm_context,
    set_text_confirm_context,
    get_text_confirm_context,
)

# Wake-word prefix stripper for the "Option A" continuous-phrase UX.
# The listener pre-trigger captures the wake word so the user can speak the
# command continuously without pausing for the chime; the wake word ends up
# at the front of the STT transcript and must be removed before the router
# sees it. Handles common STT mis-recognitions of configured wake words.
_WAKEWORD_PREFIX_RE = re.compile(
    r"^\s*"
    r"(?:hey|hi|hello|ok|okay|yo)?\s*[,]?\s*"
    r"(?:mycroft|my\s*croft|mike(?:roft)?|jarvis|jaris|jervis|jarvi|hal|hall|hell|yo\s*(?:bitch|bish|beach|bit))"
    r"[\s,.;:!?-]*",
    re.IGNORECASE,
)


def _strip_wakeword_prefix(text: str) -> Optional[str]:
    if not text:
        return text
    stripped = _WAKEWORD_PREFIX_RE.sub("", text, count=1).strip()
    if stripped != (text or "").strip():
        try:
            logging.info(
                "WAKEWORD_PREFIX_STRIP raw=%r stripped=%r", text, stripped,
            )
        except Exception:
            pass
    return stripped


def reset_session():
    global last_interaction_ts
    interaction_flow.reset_history()
    last_interaction_ts = _now_ts()
    reset_dispatch_state()
    logging.info("Session reset (TTL elapsed)")

def touch_session():
    global last_interaction_ts
    now = _now_ts()
    if last_interaction_ts and (now - last_interaction_ts) > SESSION_TTL_SECONDS:
        reset_session()
    else:
        last_interaction_ts = now


# =========================
# CLEANUP / PROCESS CONTROL
# =========================




def _pause_wakeword_capture_media_if_needed() -> list:
    """Pause active room media before far-field wake-word command capture."""
    try:
        if not _pref_bool("WAKEWORD_PAUSE_MEDIA_DURING_CAPTURE", False):
            return []
    except Exception:
        return []

    targets = []
    seen = set()

    def _add(label, entity_id):
        eid = (entity_id or "").strip()
        if not eid or eid in seen:
            return
        seen.add(eid)
        targets.append((label, eid))

    room_id = None
    try:
        tv_ctx = _request_default_tv_context() or {}
        room_id = tv_ctx.get("room_id")
        _add("tv", tv_ctx.get("tv_entity"))
    except Exception:
        logging.exception("WAKEWORD_MEDIA_PAUSE_TV_CONTEXT_FAIL")

    try:
        sonos_room = _request_default_sonos_room(room_id)
        _add("sonos", (SONOS_PLAYERS or {}).get(sonos_room))
    except Exception:
        logging.exception("WAKEWORD_MEDIA_PAUSE_SONOS_CONTEXT_FAIL")

    paused = []
    raw_call = globals().get("_piphone_real_call_ha_service")
    if not callable(raw_call):
        raw_call = call_ha_service

    for label, eid in targets:
        try:
            st_obj = ha_get_state(eid) or {}
            st = str(st_obj.get("state") or "").strip().lower()
            if st != "playing":
                logging.info("WAKEWORD_MEDIA_PAUSE_SKIP label=%r entity=%r state=%r", label, eid, st)
                continue
            ok = bool(raw_call("media_player/media_pause", {"entity_id": eid}))
            logging.info("WAKEWORD_MEDIA_PAUSE label=%r entity=%r ok=%r", label, eid, ok)
            if ok:
                paused.append(eid)
        except Exception:
            logging.exception("WAKEWORD_MEDIA_PAUSE_FAIL label=%r entity=%r", label, eid)

    return paused

def _record_audio_with_vad_wakeword() -> Optional[str]:
    """
    Wakeword-triggered utterance capture wrapper.

    Uses the shared VAD capture core but does NOT rely on handset off-hook state.
    This keeps endpointing behavior aligned with the existing PTT capture path.
    """
    _trace_audio_event("wakeword_record_legacy_enter")
    global is_processing, is_speaking

    if is_speaking or is_processing:
        try:
            _perf('WAKEWORD_REC_RETURN_NONE', reason='busy', is_speaking=bool(is_speaking), is_processing=bool(is_processing))
        except Exception:
            pass
        logging.info("WAKEWORD_CAPTURE_SKIP reason=busy")
        return None

    logging.info("WAKEWORD_CAPTURE_BEGIN")
    _perf("WAKEWORD_CAPTURE_PREPARE")

    os.makedirs(os.path.dirname(FILENAME), exist_ok=True)
    absolute_filename = os.path.abspath(FILENAME)
    _rotate_active_stt_artifacts(absolute_filename)

    try:
        ensure_audio_device_available()

        if _wakeword_chime_enabled():
            try:
                chime_volume = max(0.0, min(1.0, _pref_float("WAKEWORD_CHIME_VOLUME", 0.35)))
                logging.info("WAKEWORD_CHIME_PLAY volume=%.2f", chime_volume)
                play_sound("wakeword_start", chime_volume, blocking=False)
            except Exception:
                logging.exception("WAKEWORD_CHIME_FAIL")

        start_ts = time.time()

        out = _record_audio_with_vad_capture_core(
            absolute_filename,
            continue_recording_fn=lambda: (
                (time.time() - start_ts) < MAX_UTTERANCE_SECONDS
                and not bool(globals().get("is_speaking"))
                and not bool(globals().get("button_pressed"))
            ),
            cancelled_fn=lambda: bool(globals().get("button_pressed")),
        )

        if out:
            logging.info("WAKEWORD_CAPTURE_OK file=%r", out)
        else:
            logging.info("WAKEWORD_CAPTURE_EMPTY")
        return out

    except Exception as e:
        logging.exception("WAKEWORD_CAPTURE_FAIL")
        try:
            _perf('WAKEWORD_REC_RETURN_NONE', reason='exception', err=repr(e))
        except Exception:
            pass
        return None


def _record_audio_with_vad_wakeword_stream(
    *,
    frame_reader,
    sample_rate: int,
    frame_samples: int,
    pre_trigger_frames=None,
    pre_trigger_sample_rate: Optional[int] = None,
    pre_trigger_frame_samples: Optional[int] = None,
) -> Optional[str]:
    """
    Wakeword-triggered utterance capture from an already-open input stream.

    This is the professional wakeword handoff path:
    * OpenWakeWord owns the input stream.
    * On detection, wakeword scoring pauses.
    * This helper consumes subsequent frames from that same stream.
    * No second sounddevice.InputStream is opened.
    """
    _trace_audio_event(
        "wakeword_record_stream_enter",
        sample_rate=sample_rate,
        frame_samples=frame_samples,
        has_frame_reader=bool(callable(frame_reader)),
        pre_trigger_frame_count=(len(pre_trigger_frames) if pre_trigger_frames else 0),
        pre_trigger_sample_rate=pre_trigger_sample_rate,
        pre_trigger_frame_samples=pre_trigger_frame_samples,
    )
    global is_processing, is_speaking

    if is_speaking or is_processing:
        try:
            _perf(
                'WAKEWORD_STREAM_REC_RETURN_NONE',
                reason='busy',
                is_speaking=bool(is_speaking),
                is_processing=bool(is_processing),
            )
        except Exception:
            pass
        logging.info("WAKEWORD_STREAM_CAPTURE_SKIP reason=busy")
        return None

    if not callable(frame_reader):
        logging.info("WAKEWORD_STREAM_CAPTURE_SKIP reason=no_frame_reader")
        return None

    try:
        sr = int(sample_rate)
    except Exception:
        sr = 48000

    try:
        expected_frame_samples = int(frame_samples)
    except Exception:
        expected_frame_samples = int(sr * FRAME_MS / 1000)

    expected_frame_samples = max(1, expected_frame_samples)

    logging.info(
        "WAKEWORD_STREAM_CAPTURE_BEGIN sr=%s frame_samples=%s",
        sr,
        expected_frame_samples,
    )
    _perf("WAKEWORD_STREAM_CAPTURE_PREPARE", sr=sr, frame_samples=expected_frame_samples)

    os.makedirs(os.path.dirname(FILENAME), exist_ok=True)
    absolute_filename = os.path.abspath(FILENAME)
    _rotate_active_stt_artifacts(absolute_filename)

    try:
        wake_preroll_ms = float(_pref_float("WAKEWORD_STREAM_PRE_ROLL_MS", 700.0))
    except Exception:
        wake_preroll_ms = 700.0
    wake_preroll_ms = max(float(PRE_ROLL_MS), min(2000.0, wake_preroll_ms))

    try:
        wake_vad_arm_delay_ms = float(_pref_float("WAKEWORD_STREAM_VAD_ARM_DELAY_MS", 250.0))
    except Exception:
        wake_vad_arm_delay_ms = 250.0
    wake_vad_arm_delay_ms = max(0.0, min(1500.0, wake_vad_arm_delay_ms))

    try:
        wake_cue_guard_ms = float(_pref_float("WAKEWORD_STREAM_CUE_GUARD_MS", 1000.0))
    except Exception:
        wake_cue_guard_ms = 1000.0
    wake_cue_guard_ms = max(0.0, min(3000.0, wake_cue_guard_ms))

    try:
        wake_silence_end_ms = float(_pref_float("WAKEWORD_STREAM_SILENCE_END_MS", 1200.0))
    except Exception:
        wake_silence_end_ms = 1200.0
    wake_silence_end_ms = max(500.0, min(3000.0, wake_silence_end_ms))

    try:
        wake_endpoint_window_ms = float(
            _pref_float("WAKEWORD_STREAM_ENDPOINT_WINDOW_MS", 700.0)
        )
    except Exception:
        wake_endpoint_window_ms = 700.0
    wake_endpoint_window_ms = max(0.0, min(3000.0, wake_endpoint_window_ms))

    try:
        wake_endpoint_silence_ratio = float(
            _pref_float("WAKEWORD_STREAM_ENDPOINT_MIN_SILENCE_RATIO", 0.70)
        )
    except Exception:
        wake_endpoint_silence_ratio = 0.70
    wake_endpoint_silence_ratio = max(
        0.0,
        min(1.0, wake_endpoint_silence_ratio),
    )

    try:
        wake_endpoint_trailing_silence_ms = float(
            _pref_float("WAKEWORD_STREAM_ENDPOINT_TRAILING_SILENCE_MS", 80.0)
        )
    except Exception:
        wake_endpoint_trailing_silence_ms = 80.0
    wake_endpoint_trailing_silence_ms = max(
        0.0,
        min(1000.0, wake_endpoint_trailing_silence_ms),
    )

    try:
        wake_min_speech_ms = float(_pref_float("WAKEWORD_STREAM_MIN_SPEECH_MS", 300.0))
    except Exception:
        wake_min_speech_ms = 300.0
    wake_min_speech_ms = max(100.0, min(1500.0, wake_min_speech_ms))

    try:
        wake_first_speech_timeout_ms = float(_pref_float("WAKEWORD_STREAM_FIRST_SPEECH_TIMEOUT_MS", 4000.0))
    except Exception:
        wake_first_speech_timeout_ms = 4000.0
    wake_first_speech_timeout_ms = max(1000.0, min(20000.0, wake_first_speech_timeout_ms))

    try:
        wake_max_seconds = float(_pref_float("WAKEWORD_STREAM_MAX_SECONDS", 8.0))
    except Exception:
        wake_max_seconds = 8.0
    wake_max_seconds = max(2.0, min(20.0, wake_max_seconds))

    try:
        post_media_pause_drain_ms = float(_pref_float("WAKEWORD_STREAM_POST_MEDIA_PAUSE_DRAIN_MS", 150.0))
    except Exception:
        post_media_pause_drain_ms = 150.0
    post_media_pause_drain_ms = max(0.0, min(1000.0, post_media_pause_drain_ms))

    try:
        pretrigger_include_ms = float(_pref_float("WAKEWORD_STREAM_PRETRIGGER_INCLUDE_MS", 0.0))
    except Exception:
        pretrigger_include_ms = 0.0
    pretrigger_include_ms = max(0.0, min(900.0, pretrigger_include_ms))

    wake_pre_roll_frames = max(
        int(PRE_ROLL_FRAMES),
        int(round(wake_preroll_ms / float(FRAME_MS))),
    )
    wake_chime_enabled = _wakeword_chime_enabled()
    wake_vad_prime_ms = max(
        wake_vad_arm_delay_ms,
        wake_cue_guard_ms if wake_chime_enabled else 0.0,
    )
    wake_vad_prime_frames = max(0, int(round(wake_vad_prime_ms / float(FRAME_MS))))
    wake_pre_roll_frames = max(wake_pre_roll_frames, wake_vad_prime_frames)
    wake_silence_end_frames = max(1, int(round(wake_silence_end_ms / float(FRAME_MS))))
    wake_endpoint_window_frames = max(
        0,
        int(round(wake_endpoint_window_ms / float(FRAME_MS))),
    )
    wake_endpoint_trailing_silence_frames = max(
        0,
        int(round(wake_endpoint_trailing_silence_ms / float(FRAME_MS))),
    )
    wake_min_speech_frames = max(1, int(round(wake_min_speech_ms / float(FRAME_MS))))

    logging.info(
        "WAKEWORD_STREAM_CAPTURE_CFG pre_roll_ms=%.1f pre_roll_frames=%s "
        "vad_arm_delay_ms=%.1f cue_guard_ms=%.1f vad_prime_frames=%s "
        "silence_end_ms=%.1f silence_end_frames=%s endpoint_window_ms=%.1f "
        "endpoint_window_frames=%s endpoint_silence_ratio=%.2f "
        "endpoint_trailing_silence_ms=%.1f endpoint_trailing_silence_frames=%s "
        "min_speech_ms=%.1f min_speech_frames=%s max_seconds=%.1f "
        "post_media_pause_drain_ms=%.1f pretrigger_include_ms=%.1f",
        wake_preroll_ms,
        wake_pre_roll_frames,
        wake_vad_arm_delay_ms,
        wake_cue_guard_ms if wake_chime_enabled else 0.0,
        wake_vad_prime_frames,
        wake_silence_end_ms,
        wake_silence_end_frames,
        wake_endpoint_window_ms,
        wake_endpoint_window_frames,
        wake_endpoint_silence_ratio,
        wake_endpoint_trailing_silence_ms,
        wake_endpoint_trailing_silence_frames,
        wake_min_speech_ms,
        wake_min_speech_frames,
        wake_max_seconds,
        post_media_pause_drain_ms,
        pretrigger_include_ms,
    )

    try:
        paused_media = _pause_wakeword_capture_media_if_needed()
        if paused_media and post_media_pause_drain_ms > 0:
            try:
                drain_frames = int(round(post_media_pause_drain_ms / float(FRAME_MS)))
                for _ in range(max(0, drain_frames)):
                    frame_reader()
                logging.info(
                    "WAKEWORD_MEDIA_PAUSE_DRAIN frames=%s approx_ms=%.1f",
                    max(0, drain_frames),
                    post_media_pause_drain_ms,
                )
            except Exception:
                logging.exception("WAKEWORD_MEDIA_PAUSE_DRAIN_FAIL")

        # A short wakeword-only cue fires before STT setup. Capture audio keeps
        # accumulating in the continuous source while the websocket connects.
        if wake_chime_enabled:
            try:
                chime_volume = max(
                    0.0,
                    min(1.0, _pref_float("WAKEWORD_CHIME_VOLUME", 0.35)),
                )
                logging.info("WAKEWORD_CHIME_PLAY volume=%.2f", chime_volume)
                play_sound("wakeword_start", chime_volume, blocking=False)
            except Exception:
                logging.exception("WAKEWORD_CHIME_FAIL")

        # Wakeword streaming is independently gated from PTT. Its session uses
        # manual commit so local VAD, not server VAD, owns the utterance boundary.
        use_streaming = bool(_pref_bool("WAKEWORD_USE_STREAMING_STT", False))
        rt_stream_runtime = None
        if use_streaming and _rt_stream_mode_enabled():
            rt_stream_runtime = _rt_stream_create_runtime(
                wake_pre_roll_frames,
                manual_commit=True,
            )
            if rt_stream_runtime and rt_stream_runtime.get("rt") is not None:
                logging.info(
                    "STT_RT_STREAM_INIT model=%r lang=%r source='wakeword'",
                    rt_stream_runtime.get("model"),
                    rt_stream_runtime.get("lang"),
                )

        start_ts = time.monotonic()
        def _continue_recording():
            return (
                (time.monotonic() - start_ts) < wake_max_seconds
                and not bool(globals().get("is_speaking"))
                and not bool(globals().get("button_pressed"))
            )

        # Keep a capped pre-trigger tail available for one-breath recovery, but
        # never feed it into command VAD. Feeding the wake word itself through
        # VAD makes capture start at t=0 and can endpoint before the command.
        pretrigger_prefix = []
        pretrigger_frame_ms = float(FRAME_MS)
        if pre_trigger_frames and pretrigger_include_ms > 0:
            try:
                if pre_trigger_sample_rate and pre_trigger_frame_samples:
                    pretrigger_frame_ms = (
                        float(pre_trigger_frame_samples)
                        * 1000.0
                        / max(1.0, float(pre_trigger_sample_rate))
                    )
                keep_frames = int(round(pretrigger_include_ms / max(1.0, pretrigger_frame_ms)))
                if keep_frames > 0:
                    pretrigger_prefix = list(pre_trigger_frames)[-keep_frames:]
                logging.info(
                    "WAKEWORD_STREAM_PRETRIGGER_STAGED frames=%s requested_ms=%.1f approx_ms=%.1f total_frames=%s",
                    len(pretrigger_prefix),
                    pretrigger_include_ms,
                    len(pretrigger_prefix) * pretrigger_frame_ms,
                    len(pre_trigger_frames),
                )
            except Exception:
                pretrigger_prefix = []
                logging.exception("WAKEWORD_STREAM_PRETRIGGER_STAGE_FAIL")
        elif pre_trigger_frames:
            try:
                logging.info(
                    "WAKEWORD_STREAM_PRETRIGGER_SKIP frames=%s reason=pretrigger_include_disabled",
                    len(pre_trigger_frames),
                )
            except Exception:
                pass

        if rt_stream_runtime is not None and pretrigger_prefix:
            try:
                for pretrigger_frame in pretrigger_prefix:
                    _rt_stream_append_frame(rt_stream_runtime, pretrigger_frame, sr)
                logging.info(
                    "WAKEWORD_STREAM_RT_PRETRIGGER_APPENDED frames=%s approx_ms=%.1f",
                    len(pretrigger_prefix),
                    len(pretrigger_prefix) * pretrigger_frame_ms,
                )
            except Exception:
                logging.exception("WAKEWORD_STREAM_RT_PRETRIGGER_APPEND_FAIL")

        first_speech_deadline_ts = start_ts + (wake_first_speech_timeout_ms / 1000.0)

        capture = _capture_utterance_from_frame_source(
            frame_reader=frame_reader,
            sample_rate=sr,
            continue_recording_fn=_continue_recording,
            cancelled_fn=lambda: bool(globals().get("button_pressed")),
            pre_roll_frames=wake_pre_roll_frames,
            silence_end_frames=wake_silence_end_frames,
            min_speech_frames=wake_min_speech_frames,
            prime_only_frames=wake_vad_prime_frames,
            endpoint_window_frames=wake_endpoint_window_frames,
            endpoint_min_silence_ratio=wake_endpoint_silence_ratio,
            endpoint_trailing_silence_frames=wake_endpoint_trailing_silence_frames,
            perf_prefix="WAKEWORD_STREAM_VAD",
            sleep_per_frame_sec=0.001,
            rt_stream_runtime=rt_stream_runtime,
            first_speech_deadline_ts=first_speech_deadline_ts,
        )

        if not capture:
            logging.info("WAKEWORD_STREAM_CAPTURE_EMPTY")
            print("No speech detected")
            return None

        audio_data = capture["audio_data"]
        speech_start_ms = capture.get("speech_start_elapsed_ms")
        one_breath_limit_ms = max(
            0.0,
            min(1500.0, _pref_float("WAKEWORD_ONE_BREATH_MAX_SPEECH_START_MS", 450.0)),
        )
        if (
            pretrigger_prefix
            and speech_start_ms is not None
            and float(speech_start_ms) <= one_breath_limit_ms
        ):
            audio_data = np.concatenate(
                [np.asarray(frame, dtype=np.int16).reshape(-1) for frame in pretrigger_prefix]
                + [audio_data]
            )
            logging.info(
                "WAKEWORD_STREAM_PRETRIGGER_APPLIED frames=%s approx_ms=%.1f speech_start_ms=%.1f limit_ms=%.1f",
                len(pretrigger_prefix),
                len(pretrigger_prefix) * pretrigger_frame_ms,
                float(speech_start_ms),
                one_breath_limit_ms,
            )
        elif pretrigger_prefix:
            logging.info(
                "WAKEWORD_STREAM_PRETRIGGER_SKIPPED reason=separate_command speech_start_ms=%r limit_ms=%.1f",
                speech_start_ms,
                one_breath_limit_ms,
            )

        out_sr = 16000
        out_audio = audio_data.astype(np.int16, copy=False)

        if sr != out_sr:
            try:
                from scipy.signal import resample_poly
                import math

                g = math.gcd(int(sr), int(out_sr))
                up = int(out_sr // g)
                down = int(sr // g)

                ds = resample_poly(out_audio.astype("float32", copy=False), up=up, down=down)
                out_audio = np.clip(np.rint(ds), -32768, 32767).astype(np.int16)
                logging.info(
                    "WAKEWORD_STREAM_RESAMPLE_OK sr=%s out_sr=%s up=%s down=%s samples_in=%s samples_out=%s",
                    sr,
                    out_sr,
                    up,
                    down,
                    int(audio_data.shape[0]),
                    int(out_audio.shape[0]),
                )
            except Exception as e:
                logging.warning("WAKEWORD_STREAM_RESAMPLE_FAIL keeping sr=%s err=%r", sr, e)
                out_sr = sr
                out_audio = audio_data.astype(np.int16, copy=False)

        if out_sr == 16000:
            try:
                from audio_input_profile import get_audio_input_profile
                from wakeword_frontend import clean_command_audio_16k

                input_profile = get_audio_input_profile()
                command_ns_level = int(
                    input_profile.get("command_noise_suppression_level") or 0
                )
                command_auto_gain_dbfs = int(
                    input_profile.get("command_auto_gain_dbfs") or 0
                )
                command_volume_multiplier = float(
                    input_profile.get("command_volume_multiplier") or 1.0
                )
                out_audio = clean_command_audio_16k(
                    out_audio,
                    noise_suppression_level=command_ns_level,
                    auto_gain_dbfs=command_auto_gain_dbfs,
                    volume_multiplier=command_volume_multiplier,
                    logger=logging,
                )
                logging.info(
                    "WAKEWORD_STREAM_COMMAND_FRONTEND_APPLIED profile=%r ns_level=%s auto_gain_dbfs=%s volume_multiplier=%.3f",
                    input_profile.get("name"),
                    command_ns_level,
                    command_auto_gain_dbfs,
                    command_volume_multiplier,
                )
            except Exception:
                logging.exception("WAKEWORD_STREAM_FRONTEND_FAIL using_unprocessed_audio=True")

        wav_write(absolute_filename, out_sr, out_audio)

        try:
            if rt_stream_runtime is not None:
                _rt_stream_finalize_to_sidecar(
                    rt_stream_runtime,
                    absolute_filename + ".transcript",
                )
        except Exception as e:
            logging.error(f"STT_RT_STREAM_ERR finalize failed source='wakeword' err={e}")

        _perf(
            "WAKEWORD_STREAM_WAV_WRITTEN",
            out_sr=out_sr,
            bytes=os.path.getsize(absolute_filename),
            speech_frames=capture["speech_frames"],
            silence_frames=capture["silence_frames"],
        )

        logging.info("WAKEWORD_STREAM_CAPTURE_OK file=%r", absolute_filename)
        return absolute_filename

    except Exception as e:
        logging.exception("WAKEWORD_STREAM_CAPTURE_FAIL")
        try:
            _perf('WAKEWORD_STREAM_REC_RETURN_NONE', reason='exception', err=repr(e))
        except Exception:
            pass
        return None


def _process_wakeword_stream_interaction(
    *,
    frame_reader,
    sample_rate: int,
    frame_samples: int,
    pre_trigger_frames=None,
    pre_trigger_sample_rate: Optional[int] = None,
    pre_trigger_frame_samples: Optional[int] = None,
) -> bool:
    """Run one wakeword interaction using the listener's open audio stream."""
    _trace_audio_event(
        "wakeword_process_stream_enter",
        sample_rate=sample_rate,
        frame_samples=frame_samples,
        has_frame_reader=bool(callable(frame_reader)),
    )
    global is_processing

    try:
        if bool(globals().get("is_speaking")) or bool(globals().get("is_processing")):
            logging.info(
                "WAKEWORD_STREAM_INTERACTION_SKIP reason=busy is_speaking=%r is_processing=%r",
                bool(globals().get("is_speaking")),
                bool(globals().get("is_processing")),
            )
            return False
    except Exception:
        pass

    logging.info("WAKEWORD_STREAM_INTERACTION_BEGIN")
    audio_file = _record_audio_with_vad_wakeword_stream(
        frame_reader=frame_reader,
        sample_rate=sample_rate,
        frame_samples=frame_samples,
        pre_trigger_frames=pre_trigger_frames,
        pre_trigger_sample_rate=pre_trigger_sample_rate,
        pre_trigger_frame_samples=pre_trigger_frame_samples,
    )

    if not audio_file:
        logging.info("WAKEWORD_STREAM_INTERACTION_ABORT reason=no_audio")
        return False

    with lock:
        is_processing = True

    try:
        logging.info("WAKEWORD_STREAM_PROCESS_BEGIN file=%r", audio_file)
        process_audio(audio_file, trigger="wakeword")
        logging.info("WAKEWORD_STREAM_PROCESS_DONE")
        return True
    finally:
        with lock:
            is_processing = False


def _process_wakeword_interaction() -> bool:
    """
    End-to-end wakeword interaction path:
    * capture one utterance using shared VAD core
    * process through normal PiPhone pipeline

    Current limitation:
    * spoken on-hook responses still depend on existing speak_text() behavior
    * this phase is focused on trigger + capture + processing correctness
    """
    _trace_audio_event("wakeword_process_legacy_enter")
    global is_processing

    try:
        if bool(globals().get("is_speaking")) or bool(globals().get("is_processing")):
            logging.info(
                "WAKEWORD_INTERACTION_SKIP reason=busy is_speaking=%r is_processing=%r",
                bool(globals().get("is_speaking")),
                bool(globals().get("is_processing")),
            )
            return False
    except Exception:
        pass

    logging.info("WAKEWORD_INTERACTION_BEGIN")
    audio_file = _record_audio_with_vad_wakeword()
    if not audio_file:
        logging.info("WAKEWORD_INTERACTION_ABORT reason=no_audio")
        return False

    with lock:
        is_processing = True
    try:
        logging.info("WAKEWORD_PROCESS_BEGIN file=%r", audio_file)
        process_audio(audio_file, trigger="wakeword")
        logging.info("WAKEWORD_PROCESS_DONE")
        return True
    finally:
        with lock:
            is_processing = False



def _wakeword_suppress_reason() -> str:
    try:
        if not _wakeword_enabled():
            return "feature_disabled"
    except Exception:
        return "feature_disabled"

    try:
        if _wakeword_only_onhook() and bool(globals().get("button_pressed")):
            return "offhook"
    except Exception:
        pass

    try:
        if _wakeword_only_onhook():
            if GPIO.input(GPIO_PIN) == 0:
                return "offhook"
    except Exception:
        pass

    try:
        if bool(globals().get("is_speaking")) and not _wakeword_barge_in_enabled():
            return "speaking"
    except Exception:
        pass

    try:
        if _wakeword_suppress_during_sfx() and _is_sfx_playing():
            return "sfx_playing"
    except Exception:
        pass

    try:
        if bool(globals().get("is_processing")):
            return "processing"
    except Exception:
        pass

    try:
        if bool(globals().get("_WAKEWORD_DETECTION_IN_PROGRESS")):
            return "wakeword_active"
    except Exception:
        pass

    return ""


def _wakeword_should_listen() -> bool:
    return (_wakeword_suppress_reason() == "")


def _handle_wakeword_detected(**kwargs) -> None:
    """
    Wakeword detection callback.

    Trigger one wakeword interaction through either:
    * the same-stream wakeword handoff path when a frame_reader is supplied
    * the legacy wakeword capture path otherwise

    The same-stream path avoids opening the input device a second time.
    """
    _trace_audio_event(
        "wakeword_detected_callback_enter",
        has_frame_reader=bool(callable((kwargs or {}).get("frame_reader"))),
        sample_rate=(kwargs or {}).get("sample_rate"),
        frame_samples=(kwargs or {}).get("frame_samples"),
        wakeword_label=(kwargs or {}).get("wakeword_label"),
        wakeword_score=(kwargs or {}).get("wakeword_score"),
    )
    global _WAKEWORD_DETECTION_IN_PROGRESS
    try:
        if _wakeword_barge_in_enabled() and bool(globals().get("is_speaking")):
            logging.info("WAKEWORD_BARGE_IN_STOP_TTS")
            stop_speaking_now()

        _WAKEWORD_DETECTION_IN_PROGRESS = True
        logging.info(
            "WAKEWORD_DETECTED_CALLBACK phase=stream output_mode=%r output_room=%r has_frame_reader=%s",
            _assistant_audio_output_mode(),
            _assistant_audio_output_room(),
            bool(callable((kwargs or {}).get("frame_reader"))),
        )

        frame_reader = (kwargs or {}).get("frame_reader")
        sample_rate = (kwargs or {}).get("sample_rate")
        frame_samples = (kwargs or {}).get("frame_samples")
        pre_trigger_frames = (kwargs or {}).get("pre_trigger_frames")
        pre_trigger_sample_rate = (kwargs or {}).get("pre_trigger_sample_rate")
        pre_trigger_frame_samples = (kwargs or {}).get("pre_trigger_frame_samples")

        if callable(frame_reader):
            _process_wakeword_stream_interaction(
                frame_reader=frame_reader,
                sample_rate=sample_rate,
                frame_samples=frame_samples,
                pre_trigger_frames=pre_trigger_frames,
                pre_trigger_sample_rate=pre_trigger_sample_rate,
                pre_trigger_frame_samples=pre_trigger_frame_samples,
            )
        else:
            _process_wakeword_interaction()
    finally:
        # Smart rearm:
        #   1) Wait for any in-flight SFX (success/finish/error chime) to finish,
        #      bounded by a sane upper limit, so the listener does not score
        #      our own audio cues bleeding back into the mic.
        #   2) Add a small acoustic settle window for room reverb.
        #   3) Honor the configured WAKEWORD_REARM_SEC as a floor in case the
        #      whole interaction was completely silent.
        t_finally_start = time.monotonic()
        try:
            min_delay = max(0.0, float(_wakeword_rearm_sec()))
        except Exception:
            min_delay = 0.35

        # Upper bound on how long we will wait for SFX to drain.
        # With AGC off and a stable input chain we no longer need a long
        # settle window for reverb. The drain max protects against runaway
        # SFX (which should never happen) without dragging out idle UX.
        sfx_drain_max_sec = max(
            0.0,
            min(3.0, _pref_float("WAKEWORD_REARM_SFX_DRAIN_MAX_SEC", 1.0)),
        )
        # Post-SFX settle is now zero - the rearm completes as soon as
        # SFX playback ends. Reverb is no longer retriggering wake word.
        sfx_settle_sec = 0.0

        sfx_seen = False
        sfx_drain_start = time.monotonic()
        while True:
            try:
                if _is_sfx_playing():
                    sfx_seen = True
                    if (time.monotonic() - sfx_drain_start) >= sfx_drain_max_sec:
                        break
                    time.sleep(0.02)
                    continue
            except Exception:
                pass
            break

        if sfx_seen:
            time.sleep(sfx_settle_sec)

        elapsed = time.monotonic() - t_finally_start
        if elapsed < min_delay:
            time.sleep(min_delay - elapsed)

        total = time.monotonic() - t_finally_start
        # Ask the listener to flush its OWW preprocessor buffers + diagnostic
        # deques BEFORE clearing the in-progress flag. This closes the gap
        # where the listener's own suppress->listen flush is skipped on
        # error-path interactions that play no SFX (so the listener never
        # observes a fresh suppress->listen transition).
        try:
            listener = globals().get("_WAKEWORD_LISTENER")
            if listener is not None and hasattr(listener, "request_flush"):
                listener.request_flush()
        except Exception:
            pass
        _WAKEWORD_DETECTION_IN_PROGRESS = False
        logging.info(
            "WAKEWORD_REARM_READY total_sec=%.2f sfx_seen=%s min_floor_sec=%.2f "
            "sfx_drain_max_sec=%.2f",
            total,
            sfx_seen,
            min_delay,
            sfx_drain_max_sec,
        )


def _start_wakeword_listener_if_enabled() -> bool:
    global _WAKEWORD_LISTENER

    try:
        if not _wakeword_enabled():
            logging.info("WAKEWORD_LISTENER_SKIP reason=feature_disabled")
            return False
    except Exception:
        logging.info("WAKEWORD_LISTENER_SKIP reason=feature_disabled")
        return False

    if _WAKEWORD_LISTENER is not None:
        logging.info("WAKEWORD_LISTENER_ALREADY_STARTED")
        return True

    try:
        _WAKEWORD_LISTENER = WakewordListener(
            engine=_wakeword_engine_name(),
            model=_wakeword_model_name(),
            should_listen_fn=_wakeword_should_listen,
            suppress_reason_fn=_wakeword_suppress_reason,
            threshold_fn=_wakeword_detection_threshold,
            on_detected_fn=_handle_wakeword_detected,
            rearm_sec=_wakeword_rearm_sec(),
            logger=logging,
        )
        _WAKEWORD_LISTENER.start()
        return True
    except Exception:
        logging.exception("WAKEWORD_LISTENER_START_FAIL")
        _WAKEWORD_LISTENER = None
        return False

def _stop_wakeword_listener_for_exclusive_audio_applet(name: str) -> None:
    global _WAKEWORD_LISTENER
    try:
        listener = globals().get("_WAKEWORD_LISTENER")
        if listener is None:
            logging.info("APPLET_AUDIO_HANDOFF_WAKEWORD_ALREADY_STOPPED applet=%s", name)
            return
        logging.info("APPLET_AUDIO_HANDOFF_WAKEWORD_STOP applet=%s", name)
        try:
            listener.stop()
        except Exception:
            logging.exception("APPLET_AUDIO_HANDOFF_WAKEWORD_STOP_FAIL applet=%s", name)
        _WAKEWORD_LISTENER = None
        time.sleep(0.6)
    except Exception:
        logging.exception("APPLET_AUDIO_HANDOFF_BEFORE_START_FAIL applet=%s", name)


def _restart_wakeword_listener_after_exclusive_audio_applet(name: str) -> None:
    try:
        logging.info("APPLET_AUDIO_HANDOFF_WAKEWORD_RESTART applet=%s", name)
        _start_wakeword_listener_if_enabled()
    except Exception:
        logging.exception("APPLET_AUDIO_HANDOFF_AFTER_STOP_FAIL applet=%s", name)


try:
    register_exclusive_audio_hooks(
        before_start=_stop_wakeword_listener_for_exclusive_audio_applet,
        after_stop=_restart_wakeword_listener_after_exclusive_audio_applet,
    )
    logging.info("APPLET_EXCLUSIVE_AUDIO_HOOKS_REGISTERED")
except Exception:
    logging.exception("APPLET_EXCLUSIVE_AUDIO_HOOKS_REGISTER_FAIL")



def cleanup_audio_processes():
    # DEBUG: trace who is triggering audio cleanup (caller + thread + state)
    try:
        import inspect
        import threading
        _stk = inspect.stack()
        _caller  = _stk[1].function if len(_stk) > 1 else "?"
        _caller2 = _stk[2].function if len(_stk) > 2 else ""
        logging.info(
            "AUDIO_CLEANUP_CALL caller=%s caller2=%s thread=%s pid=%s is_processing=%r is_speaking=%r button_pressed=%r",
            _caller, _caller2, threading.current_thread().name, os.getpid(),
            globals().get("is_processing"), globals().get("is_speaking"), globals().get("button_pressed"),
        )
    except Exception:
        pass

    """
    IMPORTANT: do NOT pkill mpg123 globally.
    We only terminate the specific TTS process we started (current_tts_proc).
    Global pkill cuts off chimes and causes weird follow-up chime clipping.

    NOTE: These pkills can be slow on Pi; we PERF them and bound them with short timeouts.
    """
    t0 = time.monotonic()
    try:
        _perf("AUDIO_CLEANUP_BEGIN")
    except Exception:
        pass

    # Keep behavior for now, but make it bounded + measurable.
    for cmd in (
        ["pkill", "-f", "arecord"],
        ["pkill", "-f", "aplay"],
        ["pkill", "-f", "mplayer"],
    ):
        c0 = time.monotonic()
        rc = None
        timed_out = False
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=0.25)
            rc = 0
        except subprocess.TimeoutExpired:
            timed_out = True
        except Exception:
            rc = None
        try:
            _perf("AUDIO_CLEANUP_CMD", cmd=" ".join(cmd), dt=round(time.monotonic() - c0, 4), timeout=timed_out, rc=rc)
        except Exception:
            pass

    try:
        _perf("AUDIO_CLEANUP_DONE", dt=round(time.monotonic() - t0, 4))
    except Exception:
        pass
def stop_speaking_now():
    global current_tts_proc, is_speaking
    with tts_proc_lock:
        if current_tts_proc and current_tts_proc.poll() is None:
            try:
                current_tts_proc.terminate()
                try:
                    current_tts_proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    current_tts_proc.kill()
            except Exception:
                pass
        current_tts_proc = None
        global last_tts_end_ts
        last_tts_end_ts = _now_ts()
    with lock:
        is_speaking = False

def cleanup_handler(signum=None, frame=None):
    # Shut down the unified HTTP/WS server first so its TCP listener
    # releases the port cleanly before the rest of cleanup runs. Daemon
    # thread would die with the process either way, but explicit cleanup
    # avoids TIME_WAIT bind failures on quick restarts.
    try:
        import sys as _sys
        if "unified_server" in _sys.modules:
            try:
                _sys.modules["unified_server"].shutdown(timeout=3.0)
            except Exception:
                logging.exception("UNIFIED_SERVER_CLEANUP_FAIL")
    except Exception:
        pass

    try:
        listener = globals().get("_WAKEWORD_LISTENER")
        if listener is not None:
            logging.info("WAKEWORD_LISTENER_CLEANUP_STOP")
            try:
                listener.stop()
            except Exception:
                logging.exception("WAKEWORD_LISTENER_CLEANUP_STOP_FAIL")
            try:
                time.sleep(0.25)
            except Exception:
                pass
    except Exception:
        pass

    try:
        stop_speaking_now()
    except Exception:
        pass
    try:
        cleanup_audio_processes()
    except Exception as e:
        logging.error(f"Cleanup error: {e}")
    try:
        GPIO.cleanup()
    except Exception:
        pass
    logging.info("Exiting due to signal or exit")
    print("Exiting due to signal or exit")
    if signum is not None:
        sys.exit(0)

if not PIPHONE_NO_RUNTIME_INIT:
    atexit.register(cleanup_handler)
    signal.signal(signal.SIGTERM, cleanup_handler)
    signal.signal(signal.SIGINT, cleanup_handler)

# =========================
# AUDIO PLAYBACK HELPERS
# =========================

def play_mp3_blocking(path: str, volume: float = 1.0):
    try:
        scale = max(1, min(32768, int(round(32768 * float(volume)))))
        cmd = list(MPG123_CMD)
        if scale != 32768:
            cmd.extend(["-f", str(scale)])
        cmd.append(path)
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception as e:
        logging.error(f"MP3 playback error: {e}")

def play_mp3_nonblocking(path: str):
    """
    Start mpg123 playback and return immediately.
    Cleanup/timeout handling happens in a daemon thread so the main loop can
    proceed (e.g., VAD can begin while the start-chime plays).
    """
    try:
        import threading
        import subprocess
        import logging
    except Exception:
        return None

    try:
        cmd = list(MPG123_CMD) + [path]
    except Exception:
        cmd = ["mpg123", "-q", path]

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception:
        logging.exception("mpg123(nonblocking) failed to start: %r", path)
        return None

    try:
        active_fx_procs.append(proc)
    except Exception:
        pass

    def _reap():
        err = ""
        rc = None
        try:
            try:
                rc = proc.wait(timeout=FX_MAX_SECONDS)
            except Exception:
                # TimeoutExpired or other: try graceful terminate then kill
                try:
                    proc.terminate()
                except Exception:
                    pass
                try:
                    rc = proc.wait(timeout=KILL_GRACE)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    try:
                        rc = proc.wait(timeout=KILL_GRACE)
                    except Exception:
                        rc = None

            try:
                if proc.stderr:
                    err = (proc.stderr.read() or "").strip()
            except Exception:
                err = ""

            if rc not in (0, None):
                logging.info("mpg123(nonblocking) rc=%s err=%r file=%s", rc, err, path)
        finally:
            # Remove just this proc from the list if present
            try:
                if proc in active_fx_procs:
                    active_fx_procs.remove(proc)
            except Exception:
                pass

    try:
        threading.Thread(target=_reap, daemon=True).start()
    except Exception:
        # Worst case: fallback to no cleanup thread
        pass

    return proc
def play_sound(sound_type: str, volume: float = 1.0, blocking: bool = False):
    _trace_audio_event(
        "play_sound",
        sound_type=sound_type,
        blocking=bool(blocking),
        is_processing=bool(globals().get("is_processing")),
        is_speaking=bool(globals().get("is_speaking")),
        button_pressed=bool(globals().get("button_pressed")),
        wakeword_active=bool(globals().get("_WAKEWORD_DETECTION_IN_PROGRESS")),
    )
    """
    UI tones (start/finish). Respect `blocking`.

    If blocking=False, we play in a daemon thread. We still serialize playback
    via audio_device_lock inside the worker to reduce ALSA contention.
    """
    if sound_type == "start":
        sound_file = START_SOUND
    elif sound_type == "wakeword_start":
        configured = _pref_str("WAKEWORD_CHIME_SOUND_FILE", "assets/play.mp3").strip()
        candidate = Path(configured or "assets/play.mp3")
        sound_file = str(candidate if candidate.is_absolute() else BASE_DIR / candidate)
    elif sound_type == "finish":
        sound_file = FINISH_SOUND
    elif sound_type == "error":
        sound_file = ERROR_SOUND
    else:
        sound_file = FINISH_SOUND

    def _do_play():
        t0 = time.monotonic()
        try:
            with audio_device_lock:
                play_mp3_blocking(sound_file, volume=volume)
        except Exception as e:
            logging.error(f"Sound playback error: {e}")
        finally:
            try:
                _perf("SFX_DONE", kind=sound_type, dt=round(time.monotonic() - t0, 3))
            except Exception:
                pass
            try:
                _sfx_exit()
            except Exception:
                pass

    try:
        _perf("SFX_BEGIN", kind=sound_type, blocking=bool(blocking))
    except Exception:
        pass

    # Mark SFX as playing SYNCHRONOUSLY before any thread spawn or playback,
    # so wakeword gating cannot race ahead while the worker thread is still
    # scheduling. _sfx_exit() is called inside the worker's finally block.
    try:
        _sfx_enter()
    except Exception:
        pass

    if blocking:
        _do_play()
    else:
        t = threading.Thread(target=_do_play, daemon=True)
        t.start()
def play_error_sound():
    """Play the error tone via the same pipeline as other UI tones."""
    # Non-blocking so the wakeword rearm-finally SFX-drain loop can wait
    # for it concurrently (bounded by sfx_drain_max_sec=1.0s) instead of
    # the caller blocking ~1.7s before rearm even starts. This makes
    # failure rearm latency symmetric with success rearm latency (both
    # ~1s post-action). Previous blocking=True was a guard against
    # later cleanup/start-chime cutting off the tone, but the wakeword
    # path keeps WAKEWORD_DETECTION_IN_PROGRESS=True during rearm so no
    # competing audio is initiated during the drain window.
    play_sound("error", 1.0, blocking=False)

def ensure_audio_device_available() -> bool:
    with audio_device_lock:
        t0 = time.monotonic()
        try:
            _perf("AUDIO_ENSURE_BEGIN")
        except Exception:
            pass
        try:
            cleanup_audio_processes()
            try:
                _perf("AUDIO_ENSURE_OK", dt=round(time.monotonic() - t0, 4))
            except Exception:
                pass
            return True
        except Exception as e:
            try:
                _perf("AUDIO_ENSURE_ERR", dt=round(time.monotonic() - t0, 4), err=repr(e))
            except Exception:
                pass
            print(f"Error freeing audio device: {e}")
            return False
# =========================
# CHIME HELPERS (START CHIME THROTTLE)
# =========================

# =========================
# RECORDING (VAD ENDPOINTING)
# =========================


# --- Audio capture cluster (extracted to audio_capture.py) ---
# Pure VAD + streaming-STT helpers moved out of main runtime for clarity.
# The hardware-bridge wrapper _record_audio_with_vad_capture_core stays
# here because it owns sounddevice + GPIO + _pick_sd_* coupling.
import audio_capture as _audio_capture
from audio_capture import (
    _rotate_active_stt_artifacts,
    _VadUtteranceAccumulator,
    _rt_stream_mode_enabled,
    _rt_stream_create_runtime,
    _rt_stream_prepare_pcm,
    _rt_stream_append_frame,
    _rt_stream_finalize_to_sidecar,
    _capture_utterance_from_frame_source,
    scale_int16_audio,
)
_audio_capture.set_perf_logger(_perf)


def _record_audio_with_vad_capture_core(
    absolute_filename: str,
    *,
    continue_recording_fn=None,
    cancelled_fn=None,
) -> Optional[str]:
    """
    Internal capture core shared by PTT today and intended for wake-word reuse later.

    Parameters:
      - absolute_filename: destination wav path
      - continue_recording_fn: optional callable returning True while capture
        should continue. If omitted, preserves current PTT off-hook behavior.
      - cancelled_fn: optional callable returning True if the capture should be
        treated as cancelled after recording. If omitted, preserves current PTT
        hang-up cancellation behavior.

    Current behavior remains unchanged for the PTT wrapper.
    """
    try:
        if continue_recording_fn is None:
            continue_recording_fn = lambda: (GPIO.input(GPIO_PIN) == 0)
        if cancelled_fn is None:
            cancelled_fn = lambda: bool(GPIO.input(GPIO_PIN))

        print(f"Recording to: {absolute_filename}")

        # Realtime streaming STT (optional): stream audio while capturing.
        stt = None
        stt_transcript = ""
        stt_started = False
        start_time = time.time()

        sd_dev = _pick_sd_input_device_index()
        sr = _pick_sd_samplerate()
        # FRAME_SAMPLES/SAMPLE_RATE encodes your frame duration (typically 10ms). Keep that duration.
        frame_ms = int(round(1000.0 * FRAME_SAMPLES / float(SAMPLE_RATE)))
        frame_samples = int(sr * frame_ms / 1000)
        logging.info('Audio capture: sd_dev=%s sr=%s frame_ms=%s frame_samples=%s', sd_dev, sr, frame_ms, frame_samples)
        _perf("VAD_STREAM_CFG", sd_dev=sd_dev, sr=sr, frame_ms=frame_ms, frame_samples=frame_samples)

        _perf('REC_BEFORE_STREAM_OPEN')

        # Prepare realtime STT before opening the microphone. Creating the
        # websocket session can take hundreds of milliseconds; doing that with
        # a low-latency input stream already open causes ALSA input overruns.
        stt_mode = (os.getenv("PIPHONE_STT_MODE", "whisper") or "").strip().lower()
        rt_streaming = stt_mode in ("realtime_stream", "rt_stream", "realtime_streaming")
        logging.info(
            "STT_PATH_DECISION mode=%s requested_streaming=%s",
            stt_mode,
            rt_streaming,
        )
        rt_stream_runtime = None
        stt_transcript = None
        if rt_streaming:
            rt_stream_runtime = _rt_stream_create_runtime(
                PRE_ROLL_FRAMES,
                fast_48k_downsample=True,
            )
            logging.info(
                "STT_RT_STREAM_PREPARED active=%s source=pre_open",
                bool(rt_stream_runtime and rt_stream_runtime.get("rt") is not None),
            )

        t_cap0 = time.monotonic()
        _perf('VAD_OPEN', sd_dev=sd_dev, sr=sr, frame_ms=frame_ms, frame_samples=frame_samples)
        with sd.InputStream(
            device=(sd_dev if sd_dev >= 0 else None),
            samplerate=sr,
            channels=1,
            dtype="int16",
            blocksize=frame_samples,
            # This device's high-latency setting is still only about 35 ms and
            # gives the Pi enough scheduling headroom to avoid dropped input.
            latency="high",
        ) as stream:
            _perf('VAD_OPENED')
            # --- Realtime streaming STT (true streaming: feed frames during recording) ---
            logging.info(
                "STT_PATH_DECISION_POST mode=%s streaming_active=%s",
                stt_mode,
                rt_streaming,
            )

            logging.info("STT_PATH_RECORD streaming_active=%s", int(bool(rt_streaming)))
            if rt_streaming:
                if rt_stream_runtime and rt_stream_runtime.get("rt") is not None:
                    logging.info(
                        "STT_RT_STREAM_INIT model=%r lang=%r",
                        rt_stream_runtime.get("model"),
                        rt_stream_runtime.get("lang"),
                    )

            from audio_input_profile import get_audio_input_profile

            input_profile = get_audio_input_profile()
            ptt_volume_multiplier = max(
                0.1,
                min(4.0, float(input_profile.get("ptt_volume_multiplier") or 1.0)),
            )
            overflow_count = 0
            frames_read = 0

            def _read_frame():
                nonlocal overflow_count, frames_read
                frames, overflowed = stream.read(frame_samples)
                frames_read += 1
                if overflowed:
                    overflow_count += 1
                arr = np.frombuffer(frames, dtype=np.int16)
                return scale_int16_audio(arr, ptt_volume_multiplier)

            capture = _capture_utterance_from_frame_source(
                frame_reader=_read_frame,
                sample_rate=sr,
                continue_recording_fn=continue_recording_fn,
                cancelled_fn=cancelled_fn,
                pre_roll_frames=PRE_ROLL_FRAMES,
                silence_end_frames=SILENCE_END_FRAMES,
                min_speech_frames=MIN_SPEECH_FRAMES,
                prime_only_until_ts=0.0,
                perf_prefix="VAD",
                # stream.read() already blocks for one hardware frame. Sleeping
                # again only reduces the time available before ALSA overruns.
                sleep_per_frame_sec=0.0,
                rt_stream_runtime=rt_stream_runtime,
            )

            log_fn = logging.warning if overflow_count else logging.info
            log_fn(
                "PTT_AUDIO_CAPTURE overflows=%s frames_read=%s gain=%.3f",
                overflow_count,
                frames_read,
                ptt_volume_multiplier,
            )
            _perf(
                "PTT_AUDIO_CAPTURE",
                overflows=overflow_count,
                frames_read=frames_read,
                gain=round(ptt_volume_multiplier, 3),
            )

        t_cap1 = time.monotonic()

        if not capture:
            try:
                _perf(
                    'VAD_DONE',
                    dt=round(t_cap1 - t_cap0, 3),
                    speech_started=False,
                    frames=0,
                )
            except Exception:
                pass
            print("No speech detected")
            return None

        _perf(
            'VAD_DONE',
            dt=round(t_cap1 - t_cap0, 3),
            speech_started=capture["speech_started"],
            frames=capture["captured_len"],
        )
        try:
            _perf(
                'VAD_POST',
                gpio=int(GPIO.input(GPIO_PIN)),
                speech_started=bool(capture["speech_started"]),
                captured_len=int(capture["captured_len"]),
                speech_frames=int(capture["speech_frames"]),
                silence_frames=int(capture["silence_frames"]),
            )
        except Exception:
            pass

        # Cancel any in-flight streaming STT session on hang-up
        try:
            if stt is not None:
                stt.cancel()
        except Exception:
            pass

        audio_data = capture["audio_data"]

        _perf(
            "VAD_CAPTURE_DONE",
            speech_frames=capture["speech_frames"],
            silence_frames=capture["silence_frames"],
        )

        # --- Downsample for STT: write/upload 16k mono int16 for smaller STT payloads ---
        out_sr = 16000
        out_audio = audio_data
        try:
            import audioop
            # Ensure int16 PCM bytes
            pcm = audio_data.astype(np.int16, copy=False).tobytes()
            if sr != out_sr:
                pcm, _ = audioop.ratecv(pcm, 2, 1, sr, out_sr, None)
            out_audio = np.frombuffer(pcm, dtype=np.int16)
        except Exception as e:
            logging.warning(f"Downsample failed; keeping original sr={sr}: {e}")
            out_sr = sr
            out_audio = audio_data.astype(np.int16, copy=False)

        # If realtime streaming was enabled, finalize transcript now (before writing sidecar below)
        if rt_stream_runtime is not None:
            try:
                stt_transcript = _rt_stream_finalize_to_sidecar(
                    rt_stream_runtime,
                    absolute_filename + ".transcript",
                )
            except Exception as e:
                logging.error(f"STT_RT_STREAM_ERR finalize failed: {e}")

        wav_write(absolute_filename, out_sr, out_audio)

        # Optional: generate a sidecar transcript via realtime transcription (file-based).
        # This does NOT change VAD/recording behavior; it just produces recording.wav.transcript
        # so transcribe_audio() can skip Whisper.
        try:
            stt_mode = (os.environ.get("PIPHONE_STT_MODE", "") or "").strip().lower()
        except Exception:
            stt_mode = ""
        if stt_mode in ("realtime_file", "realtimefile", "rt_file", "rtfile"):
            try:
                from realtime_streaming_stt import transcribe_wav_file as _rt_transcribe_wav_file
                rt_txt = (_rt_transcribe_wav_file(absolute_filename) or "").strip()
                if rt_txt:
                    with open(absolute_filename + ".transcript", "w", encoding="utf-8") as f:
                        f.write(rt_txt + "\n")
                    try:
                        _perf("STT_SIDECAR_WROTE", bytes=os.path.getsize(absolute_filename + ".transcript"))
                    except Exception:
                        pass
            except Exception as e:
                logging.warning(f"Realtime-file STT failed (falling back to Whisper): {e}")

        _perf("VAD_WAV_WROTE", out_sr=out_sr, bytes=os.path.getsize(absolute_filename))

        # Persist streaming transcript (if available) as a sidecar next to the wav.
        # transcribe_audio() will prefer this if non-empty.
        try:
            if stt_transcript and str(stt_transcript).strip():
                with open(absolute_filename + ".transcript", "w", encoding="utf-8") as f:
                    f.write(str(stt_transcript).strip())
                    f.write("\n")
        except Exception as e:
            logging.warning(f"Sidecar transcript write failed: {e}")

        _perf('WAV_WRITTEN', out_sr=out_sr, samples=int(getattr(audio_data, 'shape', [0])[0]))
        try:
            _perf('REC_RETURN_OK', bytes=int(os.path.getsize(absolute_filename)), out_sr=int(out_sr))
        except Exception:
            pass
        return absolute_filename

    except Exception as e:
        try:
            logging.exception('Recording error (stack)')
        except Exception:
            pass
        try:
            _perf('REC_RETURN_NONE', reason='exception', err=repr(e))
        except Exception:
            pass
        logging.error(f"Recording error: {e}")
        print(f"Recording error: {e}")
        traceback.print_exc()
        return None



def record_audio_with_vad() -> Optional[str]:
    _trace_audio_event("ptt_record_enter")
    """
    Records one utterance:
      - Delay for handset-to-ear
      - Plays start chime
      - Captures audio until speech ends (silence hangover) or max duration
      - Returns WAV path or None
    """
    global is_processing, is_speaking

    if is_speaking or is_processing:
        try:
            _perf('REC_RETURN_NONE', reason='busy', is_speaking=bool(is_speaking), is_processing=bool(is_processing))
        except Exception:
            pass
        print("Cannot record: system is busy")
        return None

    logging.info("Preparing to record")
    _perf("VAD_PREPARE")

    _perf('REC_PREPARE')
    os.makedirs(os.path.dirname(FILENAME), exist_ok=True)
    absolute_filename = os.path.abspath(FILENAME)
    _rotate_active_stt_artifacts(absolute_filename)

    try:
        global _audio_ensured_in_session
        if not _audio_ensured_in_session:
            _perf('REC_ENSURE_BEGIN')
            ensure_audio_device_available()
            _perf('REC_ENSURE_DONE')
            _audio_ensured_in_session = True
        else:
            _perf('REC_ENSURE_SKIPPED')

        global _start_delay_applied_in_session
        if not _start_delay_applied_in_session:
            _perf('REC_START_DELAY_BEGIN', sec=START_CHIME_DELAY_SECONDS)
            time.sleep(START_CHIME_DELAY_SECONDS)
            _perf('REC_START_DELAY_DONE')
            _start_delay_applied_in_session = True
        else:
            _perf('REC_START_DELAY_SKIPPED', sec=START_CHIME_DELAY_SECONDS)

        _perf('REC_START_CHIME_BEGIN')
        global _start_chime_played_in_session
        if not _start_chime_played_in_session:
            play_sound("start", 1.0, blocking=False)
            _start_chime_played_in_session = True
        _perf('REC_START_CHIME_DONE')
        return _record_audio_with_vad_capture_core(
            absolute_filename,
            continue_recording_fn=lambda: (GPIO.input(GPIO_PIN) == 0),
            cancelled_fn=lambda: bool(GPIO.input(GPIO_PIN)),
        )
    except Exception as e:
        try:
            logging.exception('Recording error (stack)')
        except Exception:
            pass
        try:
            _perf('REC_RETURN_NONE', reason='exception', err=repr(e))
        except Exception:
            pass
        logging.error(f"Recording error: {e}")
        print(f"Recording error: {e}")
        traceback.print_exc()
        return None

# =========================
# STT / CHAT
# =========================

def _looks_like_joke_request(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    if re.search(r"\b(tell me a joke|tell a joke|joke)\b", t):
        return True
    if re.fullmatch(r"(another one|one more|more|again)", t):
        return True
    return False

def get_chatgpt_joke_response(text: str) -> str:
    # Joke mode: higher variety + explicit no-repeat list
    try:
        # Keep the no-repeat constraint short-ish; last 50 as requested
        recent = list(recent_jokes)[-RECENT_JOKES_MAX:]
        avoid = "\n".join([f"- {j}" for j in recent]) if recent else ""

        joke_system = {
            "role": "system",
            "content": (
                "You are a joke-telling assistant. "
                "Tell ONE short joke (1-3 sentences). "
                "Do not repeat jokes from the recent list. "
                "Keep it clean and friendly."
            ),
        }

        user_msg = {
            "role": "user",
            "content": (
                f"User request: {text.strip()}\n\n"
                f"Recent jokes (do not repeat):\n{avoid}" if avoid else f"User request: {text.strip()}"
            ),
        }

        response = OPENAI_CLIENT.chat.completions.create(
            model=_chatgpt_model(),
            messages=[joke_system, user_msg],
            temperature=0.9,
        )

        joke = (response.choices[0].message.content or "").strip()
        if joke:
            recent_jokes.append(joke)
        return joke or "I couldn't think of a joke right now."
    except Exception as e:
        logging.error(f"Joke mode error: {e}")
        return "I couldn't think of a joke right now."

    # STT mode toggle: default remains Whisper-file; set PIPHONE_STT_MODE=realtime to use Realtime transcription
    if os.getenv('PIPHONE_STT_MODE', 'whisper').lower() in ('realtime','rt','realtime_file'):
        return asyncio.run(realtime_transcribe_wav(
            audio_file,
            model=os.getenv('PIPHONE_REALTIME_TRANSCRIBE_MODEL','gpt-4o-transcribe'),
            language=os.getenv('PIPHONE_REALTIME_TRANSCRIBE_LANG','en'),
        ))
def transcribe_audio(
    audio_file: Optional[str],
    *,
    mode_override: Optional[str] = None,
) -> str:
    if audio_file is None or not os.path.exists(audio_file):
        return ""

    try:
        _mode = (
            mode_override
            if mode_override is not None
            else os.environ.get("PIPHONE_STT_MODE", "")
        )
        _mode = (_mode or "").strip().lower()
    except Exception:
        _mode = "?"
    try:
        logging.info("STT_ENTRY mode=%r audio=%r pid=%s", _mode, audio_file, os.getpid())
        logging.info("STT_TRANSCRIBE_BEGIN mode=%s filename=%s", _mode, audio_file)
    except Exception:
        pass

    # ============================================================
    # Realtime STT (best-effort) + sidecar
    # Controlled by: PIPHONE_STT_MODE in
    #   ('realtime','rt','realtime_file','realtime_stream','rt_stream','realtime_streaming')
    #
    # Behavior:
    #  1) If <wav>.transcript exists and non-empty -> use it (skip Whisper).
    #  2) Else try realtime_streaming_stt.transcribe_wav_file(wav) -> if non-empty,
    #     write <wav>.transcript and return it.
    #  3) Else fall back to Whisper-file path below.
    #
    # Streaming-mode aliases also provide a corrected completed-WAV Realtime
    # fallback when live capture did not produce a sidecar.
    # ============================================================
    try:
        stt_mode = _mode
    except Exception:
        stt_mode = ""
    if stt_mode in ("realtime", "rt", "realtime_file",
                    "realtime_stream", "rt_stream", "realtime_streaming"):
        sidecar_path = audio_file + ".transcript"

        # Prefer existing sidecar if present
        try:
            if os.path.exists(sidecar_path):
                with open(sidecar_path, "r", encoding="utf-8") as f:
                    sc_txt = (f.read() or "").strip()
                if sc_txt:
                    logging.info("STT_PATH_USED mode=%s method=sidecar transcript_file=%s", stt_mode, sidecar_path)
                    logging.info("Transcription (sidecar): %s", sc_txt)
                    logging.info("TRANSCRIPTION_TEXT: %r", sc_txt)
                    try:
                        _perf("STT_SIDECAR_USED", bytes=os.path.getsize(sidecar_path), chars=len(sc_txt))
                    except Exception:
                        pass
                    return sc_txt
        except Exception as e:
            logging.error(f"STT_SIDECAR_READ_ERR: {e}")

        # Otherwise, attempt realtime transcription now
        try:
            from realtime_streaming_stt import transcribe_wav_file as _rt_transcribe_wav_file
            try:
                _perf("STT_RT_BEGIN", bytes=os.path.getsize(audio_file))
            except Exception:
                pass

            t0 = time.monotonic()
            rt_txt = (_rt_transcribe_wav_file(audio_file) or "").strip()
            dt = round(time.monotonic() - t0, 3)

            try:
                _perf("STT_RT_DONE", dt=dt, chars=len(rt_txt))
            except Exception:
                pass

            logging.info(f"STT_RT_TEXT: {rt_txt!r}")

            if rt_txt:
                logging.info("STT_PATH_USED mode=%s method=realtime_file filename=%s", stt_mode, audio_file)
                try:
                    with open(sidecar_path, "w", encoding="utf-8") as f:
                        f.write(rt_txt)
                        f.write("\n")
                    try:
                        _perf("STT_SIDECAR_WROTE", bytes=os.path.getsize(sidecar_path), chars=len(rt_txt))
                    except Exception:
                        pass
                except Exception as e:
                    logging.error(f"STT_SIDECAR_WRITE_ERR: {e}")

                logging.info("Transcription (realtime): %s", rt_txt)
                logging.info("TRANSCRIPTION_TEXT: %r", rt_txt)
                return rt_txt

            # If realtime returned empty, fall through to Whisper below.
            logging.info("STT_RT_EMPTY: falling back to Whisper-file STT")
        except Exception as e:
            logging.error(f"STT_RT_ERR: {e}")
            try:
                import traceback
                traceback.print_exc()
            except Exception:
                pass
            logging.info("STT_RT_FAIL: falling back to Whisper-file STT")

    # If record_audio_with_vad() produced a streaming transcript sidecar, prefer it.
    # This keeps your routing logic intact while letting STT happen during capture.
    try:
        sidecar = audio_file + ".transcript"
        # Sidecar transcripts are ONLY valid for realtime STT modes; otherwise they can go stale and repeat forever.
        try:
            _mode_l = (_mode or '').strip().lower()
        except Exception:
            _mode_l = 'whisper'
        _realtime_modes = {
            'realtime_stream','rt_stream','realtime_streaming',
            'realtime_file','realtimefile','rt_file','rtfile',
        }
        if _mode_l not in _realtime_modes and os.path.exists(sidecar):
            try:
                os.remove(sidecar)
                logging.info('STT_SIDECAR_PURGED_NONREALTIME')
            except Exception as e:
                logging.info('STT_SIDECAR_PURGE_NONREALTIME_ERR %r', e)
        if _mode_l in _realtime_modes and os.path.exists(sidecar):
            txt = open(sidecar, "r", encoding="utf-8").read().strip()

            if txt:
                try:
                    _perf("STT_STREAM_USED")
                except Exception:
                    pass
                logging.info("STT_PATH_USED mode=%s method=sidecar transcript_file=%s", _mode_l, sidecar)
                logging.info("STT_STREAM_USED sidecar=%s", sidecar)
                return txt
    except Exception:
        pass


    _perf("STT_BEGIN", bytes=os.path.getsize(audio_file))
    logging.info(f"Transcribing: {audio_file}")

    t_stt0 = time.monotonic()

    _perf('STT_START', bytes=os.path.getsize(audio_file))
    print(f"Transcribing file ({os.path.getsize(audio_file)} bytes)")

    try:
        with open(audio_file, "rb") as file:
            logging.info("STT_PATH_USED mode=%s method=whisper_file filename=%s", _mode, audio_file)
            transcription = OPENAI_CLIENT.audio.transcriptions.create(
                model="whisper-1",
                file=file,
                language="en",
                prompt="This is a voice command or question for an AI assistant.",
            )

        t_stt1 = time.monotonic()

        _perf('STT_DONE', dt=round(t_stt1 - t_stt0, 3))

        _perf("STT_DONE")

        text = (transcription.text or "").strip()
        logging.info(f"STT: {text}")
        print(f"Transcription: {text}")
        logging.info(f"TRANSCRIPTION_TEXT: {text!r}")
        return text
    except Exception as e:
        logging.error(f"Transcription error: {e}")
        print(f"Transcription error: {e}")
        return ""

def get_chatgpt_response(text: str) -> str:
    logging.info("Getting AI response")
    print("Getting AI response")

    try:
        interaction_flow.append_history_message("user", text)
        history = interaction_flow.get_history_snapshot()
        runtime_context = build_assistant_runtime_context()

        assistant_text = ""
        web_search_used = False
        web_search_enabled = _pref_bool("CHATGPT_WEB_SEARCH_ENABLED", True)
        responses_api = getattr(OPENAI_CLIENT, "responses", None)

        if web_search_enabled and responses_api is not None:
            try:
                response = responses_api.create(
                    model=(
                        _pref_str("CHATGPT_WEB_SEARCH_MODEL", _chatgpt_model())
                        or _chatgpt_model()
                    ),
                    input=history,
                    instructions=runtime_context.instructions,
                    tools=[build_web_search_tool(runtime_context)],
                )
                assistant_text = (getattr(response, "output_text", "") or "").strip()
                web_search_used = any(
                    getattr(item, "type", "") == "web_search_call"
                    for item in (getattr(response, "output", None) or [])
                )
                logging.info(
                    "CHATGPT_RESPONSES_DONE web_search_used=%s chars=%s",
                    web_search_used,
                    len(assistant_text),
                )
            except Exception:
                logging.exception("CHATGPT_WEB_SEARCH_FAIL_FALLBACK_CHAT_COMPLETIONS")
        elif web_search_enabled:
            logging.warning("CHATGPT_WEB_SEARCH_UNAVAILABLE reason=old_openai_sdk")

        if not assistant_text:
            response = OPENAI_CLIENT.chat.completions.create(
                model=_chatgpt_model(),
                messages=contextualize_chat_messages(history, runtime_context),
            )
            assistant_text = (response.choices[0].message.content or "").strip()
            logging.info("CHATGPT_CHAT_COMPLETIONS_DONE fallback=%s", web_search_enabled)

        print(f"AI response: {assistant_text}")

        interaction_flow.append_history_message("assistant", assistant_text)
        capture_from_chatgpt_turn(
            text,
            assistant_text,
            OPENAI_CLIENT,
            default_model=_chatgpt_model(),
        )
        return assistant_text
    except Exception as e:
        logging.error(f"AI response error: {e}")
        print(f"AI response error: {e}")
        return "I'm sorry, I couldn't process your request."

# =========================
# TTS (gTTS -> mpg123, abortable)
# =========================

def _generate_tts_mp3(text: str, out_path: str, *, tld: str = "") -> bool:
    try:
        kwargs = {
            "text": text,
            "lang": TTS_LANGUAGE,
            "slow": False,
            "tokenizer_func": tokenize_for_gtts,
        }
        if tld:
            kwargs["tld"] = tld
        tts = gTTS(**kwargs)
        tts.save(out_path)
        return True
    except Exception:
        logging.exception("TTS_GENERATE_FAIL out=%r", out_path)
        return False


def _assistant_sonos_target_entity() -> tuple[str, str]:
    room = _request_default_sonos_room(_assistant_audio_output_room())
    entity_id = (SONOS_PLAYERS or {}).get(room, "")
    return room, entity_id


def _play_tts_local_file(path: str) -> None:
    ensure_audio_device_available()

    # Preempt any in-flight nonblocking chimes/fx so TTS can grab the ALSA device.
    # (We do NOT pkill mpg123 globally; only the procs we started via play_mp3_nonblocking.)
    try:
        with audio_device_lock:
            for pfx in list(active_fx_procs):
                try:
                    if pfx and pfx.poll() is None:
                        pfx.terminate()
                except Exception:
                    pass
    except Exception:
        pass

    with tts_proc_lock:
        global current_tts_proc
        current_tts_proc = subprocess.Popen(
            MPG123_CMD + [path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # While speaking, if handset is hung up, stop immediately
    while True:
        with tts_proc_lock:
            proc = current_tts_proc

        if proc is None:
            break
        if proc.poll() is not None:
            break
        # Stop mid-speech if handset is hung up. Only applies on devices
        # with a real handset; bypassed when HANDSET_PRESENT=False.
        if _pref_bool("HANDSET_PRESENT", True) and GPIO.input(GPIO_PIN) == 1:
            stop_speaking_now()
            break

        time.sleep(0.03)


def _play_tts_sonos_file(path: str) -> bool:
    room, entity_id = _assistant_sonos_target_entity()
    if not entity_id:
        logging.error("ASSISTANT_SONOS_NO_TARGET room=%r players=%r", room, sorted((SONOS_PLAYERS or {}).keys()))
        return False

    try:
        floor = int(_pref_float("ASSISTANT_SONOS_ANNOUNCE_VOLUME_FLOOR", 15))
    except Exception:
        floor = 15

    media_url = homesuite_media_url_for_path(path)
    ok = sonos_play_media(
        entity_id=entity_id,
        media_url=media_url,
        media_type="music",
        announce=True,
        announce_volume_floor=floor,
    )
    logging.info(
        "ASSISTANT_OUTPUT_SONOS room=%r entity_id=%r url=%r ok=%r",
        room,
        entity_id,
        media_url,
        bool(ok),
    )
    return bool(ok)


def _speak_tts_sonos_via_ha(text: str) -> bool:
    room, entity_id = _assistant_sonos_target_entity()
    if not entity_id:
        logging.error("ASSISTANT_HA_TTS_NO_SONOS_TARGET room=%r players=%r", room, sorted((SONOS_PLAYERS or {}).keys()))
        return False

    tts_entity = _sonos_ha_tts_entity()
    if not tts_entity:
        logging.error("ASSISTANT_HA_TTS_NO_TTS_ENTITY")
        return False

    payload = {
        "entity_id": tts_entity,
        "media_player_entity_id": entity_id,
        "message": text,
        "cache": True,
    }
    ok = call_ha_service("tts/speak", payload)
    logging.info(
        "ASSISTANT_OUTPUT_HA_TTS room=%r speaker=%r tts_entity=%r ok=%r",
        room,
        entity_id,
        tts_entity,
        bool(ok),
    )
    return bool(ok)


def speak_text(text: str):
    global is_speaking, current_tts_proc

    if not text:
        return

    command_dispatch.last_spoken_text = text

    # Don't speak if handset is already hung up.
    # Skipped on devices with no physical handset (HANDSET_PRESENT=False),
    # where the GPIO line reads "hung up" forever and would otherwise mute
    # all wakeword/button-triggered spoken responses.
    if _pref_bool("HANDSET_PRESENT", True) and GPIO.input(GPIO_PIN) == 1:
        return

    with lock:
        is_speaking = True

    audio_path = TEMP_AUDIO
    remove_audio = True
    try:
        raw_text = text
        try:
            from app_config import TTS_PRONUNCIATION_OVERRIDES
        except Exception:
            TTS_PRONUNCIATION_OVERRIDES = {}
        speakable = normalize_for_tts(raw_text, pronunciation_overrides=TTS_PRONUNCIATION_OVERRIDES)
        output_mode = _assistant_audio_output_mode()
        if output_mode not in ("local", "sonos"):
            logging.warning("ASSISTANT_OUTPUT_UNKNOWN_MODE mode=%r; falling back to local", output_mode)
            output_mode = "local"

        if output_mode == "sonos":
            audio_path = f"/tmp/assistant_response_{uuid.uuid4().hex}.mp3"
            remove_audio = False

        sonos_backend = _sonos_tts_backend() if output_mode == "sonos" else "gtts"
        if sonos_backend not in ("gtts", "home_assistant"):
            logging.warning("SONOS_TTS_UNKNOWN_BACKEND backend=%r; falling back to gtts", sonos_backend)
            sonos_backend = "gtts"

        logging.info(f"Speaking with TLD {TTS_TLD} via {output_mode}/{sonos_backend}: {speakable}")
        print(f"Speaking with TLD {TTS_TLD} via {output_mode}/{sonos_backend}: {speakable}")
        logging.info("TTS_SAY: %r (from %r)", speakable, raw_text)

        if output_mode == "sonos" and sonos_backend == "home_assistant":
            if _speak_tts_sonos_via_ha(speakable):
                return
            logging.error("ASSISTANT_OUTPUT_HA_TTS_FAIL_FALLBACK_GTTS")

        if not _generate_tts_mp3(speakable, audio_path, tld=TTS_TLD):
            return

        if output_mode == "sonos":
            if _play_tts_sonos_file(audio_path):
                return
            logging.error("ASSISTANT_OUTPUT_SONOS_FAIL_FALLBACK_LOCAL")
            remove_audio = True

        _play_tts_local_file(audio_path)

    except Exception as e:
        logging.error(f"Speech error: {e}")
        print(f"Speech error: {e}")
        traceback.print_exc()
    finally:
        with tts_proc_lock:
            current_tts_proc = None
        with lock:
            is_speaking = False
        if remove_audio and os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except Exception:
                pass

# =========================
# HOME ASSISTANT
# =========================

# ------------------------------------------------------------------
# Announcements support helpers (TTS generate + Sonos play_media)
# ------------------------------------------------------------------

_announce_httpd = None
_announce_http_thread = None

def tts_generate_audio(text: str, out_path: str) -> bool:
    """
    Generate an MP3 at out_path using gTTS (no playback).
    """
    try:
        raw_text = text

        try:
            from app_config import TTS_PRONUNCIATION_OVERRIDES
        except Exception:
            TTS_PRONUNCIATION_OVERRIDES = {}

        safe = normalize_for_tts(raw_text, pronunciation_overrides=TTS_PRONUNCIATION_OVERRIDES)
        logging.info("TTS_SAY: %r (from %r)", safe, raw_text)
        if not _generate_tts_mp3(safe, out_path, tld=ANNOUNCEMENT_TTS_TLD):
            return False
        logging.info("CLAIM: tts_generate_audio out=%r", out_path)
        return True
    except Exception:
        logging.exception("tts_generate_audio failed out=%r", out_path)
        return False

# Wire tts_generate_audio callback into command_dispatch
try:
    command_dispatch.tts_generate_audio = tts_generate_audio
except Exception:
    pass

# HA Number helpers
def _set_number_value(entity_id: str, value: int) -> bool:
    pct = max(0, min(100, int(value)))
    return call_ha_service("number/set_value", {
        "entity_id": entity_id,
        "value": pct,
    })

# =========================
# RUNNABLES: SCENES + SCRIPTS (AUTO DISCOVER)
# =========================



# =========================
# ACTION RESULT TRACKING + CHATGPT INTENT (ADDED)
# =========================

class ActionResult:
    NONE = "none"
    DEVICE = "device"
    CHATGPT = "chatgpt"


def _looks_like_failure_response(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    return (
        ("couldn't" in t)
        or ("could not" in t)
        or ("can't" in t)
        or ("didn't" in t and "play" in t)
        or ("failed" in t)
    )


def _looks_like_chatgpt_intent(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t:
        return False
    if "?" in t:
        return True
    if re.search(r"\b(what|why|how|when|where|who|explain|tell me|do you know|can you|could you)\b", t):
        return True
    return False

# =========================
# FLOW CONTROL
# =========================

def continue_listening():
    # Deprecated: main() owns the off-hook session loop.
    return


def _speak_text_for_trigger(text: str, trigger: str) -> bool:
    """Speak synchronously, or launch wakeword-only local TTS in a daemon.

    Returns True when speech was launched asynchronously. Callers use that to
    avoid starting a completion tone underneath the spoken response.
    """
    trigger_name = str(trigger or "").strip().lower()
    if trigger_name == "wakeword" and _wakeword_async_tts_enabled():
        thread = threading.Thread(
            target=speak_text,
            args=(text,),
            daemon=True,
            name="wakeword_tts",
        )
        thread.start()
        logging.info("WAKEWORD_TTS_ASYNC_START chars=%s", len(text or ""))
        return True

    speak_text(text)
    return False


def process_audio(audio_file: str, *, trigger: str = "ptt"):
    global is_processing
    previous_request_ctx = None
    request_ctx = None
    request_ctx_installed = False

    try:
        trigger_name = str(trigger or "").strip().lower()
        request_ctx = build_request_context(
            source_id="default_piphone",
            origin=trigger_name or "ptt",
        )
        previous_request_ctx = replace_current_request_context(request_ctx)
        request_ctx_installed = True
        _trace_audio_event("process_audio_enter", trigger=trigger, audio_file=audio_file)
        logging.info("PROCESS_AUDIO_BEGIN trigger=%r audio_file=%r", trigger, audio_file)
        touch_session()
        refresh_runnable_cache(
            ha_get_states=ha_get_states,
            normalize_scene_phrase=lambda s: _normalize_scene_phrase(s, logger=logging),
            ttl_seconds=10 * 60,
            force=False,
        )

        _perf('PIPE_BEFORE_STT')

        stt_mode_override = None
        if trigger_name == "wakeword":
            stt_mode_override = (
                _pref_str("WAKEWORD_STT_MODE", "realtime_stream")
                or "realtime_stream"
            ).strip().lower()
            logging.info(
                "WAKEWORD_STT_MODE_SELECTED mode=%r service_mode=%r",
                stt_mode_override,
                (os.environ.get("PIPHONE_STT_MODE", "") or "").strip().lower(),
            )
        text = transcribe_audio(audio_file, mode_override=stt_mode_override)

        # Option A (Step 6): wakeword captures intentionally include the wake
        # word at the front of the transcript (via the listener pre-trigger
        # buffer) so the user can speak the wake word and command in one
        # continuous phrase. Strip the wake-word prefix before routing so the
        # router only sees the actual command.
        if (str(trigger or "").strip().lower() == "wakeword") and text:
            text = _strip_wakeword_prefix(text) or ""

        if not text:
            with lock:
                is_processing = False
            return

        # A mistaken summon can be dismissed without entering device routing,
        # ChatGPT, conversation history, or outcome-tone logic. Wakeword
        # interactions rearm through their existing outer finally block; PTT
        # interactions simply return to the off-hook capture loop.
        if interaction_flow.is_interaction_cancel(text):
            command_dispatch._ACTION_OCCURRED = False
            try:
                clear_text_confirm_context()
            except Exception:
                pass
            logging.info(
                "INTERACTION_CANCEL trigger=%r text=%r outcome=silent",
                trigger,
                text,
            )
            _trace_audio_event(
                "process_audio_cancelled",
                trigger=trigger,
                text=(text or "")[:120],
            )
            return

        # On devices with no physical handset (HANDSET_PRESENT=False), treat the
        # handset as always "up" so wake-word / button-triggered commands can
        # produce spoken responses.
        if _pref_bool("HANDSET_PRESENT", True):
            handset_up = (GPIO.input(GPIO_PIN) == 0)
        else:
            handset_up = True

        command_dispatch._ACTION_OCCURRED = False

        action_result = ActionResult.NONE

        _t0_cmd = time.monotonic()
        device_response = process_device_commands(text)

        logging.info(f"DEVICE_RESPONSE: {device_response!r} ACTION_OCCURRED={command_dispatch._ACTION_OCCURRED}")
        async_response_started = False

        # Speak only if we actually have words to say (time/weather or confirmations enabled)
        if handset_up and device_response:
            async_response_started = _speak_text_for_trigger(device_response, trigger)
            # Bridge the deterministic/AI seam: inject informational responses into
            # conversation_history so follow-up AI queries have context
            # ("what time is it there?" after a deterministic weather response).
            force_history = False
            assistant_context_text = None
            try:
                force_history = command_dispatch._is_np_query(text)
                if force_history:
                    assistant_context_text = f"Currently playing: {device_response}"
            except Exception:
                force_history = False
            interaction_flow.inject_into_history(
                text,
                device_response,
                force=force_history,
                assistant_context_text=assistant_context_text,
            )

        # Mark DEVICE only if something truly happened:
        # - HA call succeeded (_ACTION_OCCURRED), OR
        # - local utility returned a non-empty string (time/weather)
        if command_dispatch._ACTION_OCCURRED or (device_response is not None and device_response.strip()):
            action_result = ActionResult.DEVICE
        # Semantic router (DEVICE vs CHATGPT vs ERROR)
        global last_chatgpt_ts
        if action_result == ActionResult.NONE and handset_up:
            now_ts = _now_ts()
            rr = route_utterance(text=text, now_ts=now_ts, last_chatgpt_ts=last_chatgpt_ts)
            if rr.outcome == RouteOutcome.CHATGPT:
                if _looks_like_joke_request(text):
                    response = (get_chatgpt_joke_response(text) or "").strip()
                else:
                    response = (get_chatgpt_response(text) or "").strip()
                if response:
                    last_chatgpt_ts = now_ts
                    action_result = ActionResult.CHATGPT
                    if handset_up:
                        async_response_started = _speak_text_for_trigger(response, trigger)

        # Event log: record every voiced command with outcome
        try:
            _log_result = types.SimpleNamespace(
                handled=(action_result != ActionResult.NONE),
                action_occurred=bool(command_dispatch._ACTION_OCCURRED),
                source=("deterministic" if action_result == ActionResult.DEVICE
                        else "chatgpt" if action_result == ActionResult.CHATGPT
                        else None),
            )
            log_command_event(text, request_ctx, _log_result, int((time.monotonic() - _t0_cmd) * 1000))
        except Exception:
            pass

        # Single decision point for error tone:
        # only if NO real action occurred and no ChatGPT response
        try:
            logging.info("ACTION_DECISION action_result=%r action_occurred=%r handset_up=%r text=%r norm=%r", action_result, command_dispatch._ACTION_OCCURRED, handset_up, (text or "")[:120], getattr(command_dispatch, "_LAST_STT_NORM_OUT", None))
        except Exception:
            pass
        _trace_audio_event(
            "process_audio_decision",
            trigger=trigger,
            action_result=action_result,
            action_occurred=bool(command_dispatch._ACTION_OCCURRED),
            handset_up=bool(handset_up),
        )

        if action_result == ActionResult.NONE and not command_dispatch._ACTION_OCCURRED:
            error_tone_enabled = True
            try:
                if str(trigger or "").strip().lower() == "wakeword":
                    error_tone_enabled = _pref_bool("WAKEWORD_ERROR_TONE_ENABLED", False)
            except Exception:
                error_tone_enabled = True

            if error_tone_enabled:
                try:
                    logging.info("ERROR_TONE_PLAY trigger=%r", trigger)
                except Exception:
                    pass
                play_error_sound()
            else:
                try:
                    logging.info("ERROR_TONE_SKIP trigger=%r reason=wakeword_error_tone_disabled", trigger)
                except Exception:
                    pass
        else:
            try:
                logging.info("ERROR_TONE_SKIP")
            except Exception:
                pass

            # Success/handled tone (formerly the 'finish' tone).
            # We only play this when something was actually handled (DEVICE or CHATGPT).
            if (
                action_result in (ActionResult.DEVICE, ActionResult.CHATGPT)
                and not _looks_like_failure_response(device_response)
                and not async_response_started
            ):
                try:
                    logging.info("SUCCESS_TONE_PLAY")
                except Exception:
                    pass
                try:
                    play_sound("finish", 1.0, blocking=False)
                except Exception:
                    pass

            # Optional tiny pause after outcome tone (defaults to 0).
            try:
                _post_ms = int(float(os.getenv("PIPHONE_POST_TONE_DELAY_MS", "0")))
            except Exception:
                _post_ms = 0
            if _post_ms > 0:
                time.sleep(_post_ms / 1000.0)

        # Follow-up chime + keep listening while handset stays up
        # NOTE: main() already plays the ready/start chime after process_audio() returns.
        # Keeping a second start-chime here causes a double-chime UX.
        # (Disabled by patch_dedupe_start_chime)
        return

    except Exception as e:
        logging.error(f"Processing error: {e}")
        print(f"Processing error: {e}")
        traceback.print_exc()
        with lock:
            is_processing = False
        return
    finally:
        if request_ctx_installed:
            set_current_request_context(previous_request_ctx)

def process_audio_async(audio_file: str):
    # Deprecated: main() owns the off-hook session loop; run synchronously.
    process_audio(audio_file)

# =========================
# MAIN
# =========================


def _execute_scheduled_command_in_process(
    command: str,
    *,
    source_id: str = "scheduler",
    origin: str = "scheduler",
):
    command = (command or "").strip()
    if not command:
        return {
            "handled": False,
            "return_value": None,
            "action_occurred": False,
        }

    old_action = command_dispatch._ACTION_OCCURRED
    request_ctx = build_request_context(
        source_id=source_id,
        origin=origin,
    )
    previous_ctx = replace_current_request_context(request_ctx)

    try:
        try:
            logging.info("REQUEST_CONTEXT %s", request_ctx.to_log_dict())
        except Exception:
            pass

        command_dispatch._ACTION_OCCURRED = False
        rv = process_device_commands(command)
        handled = bool(rv is not None or command_dispatch._ACTION_OCCURRED)
        try:
            logging.info(
                "SCHED_EXEC_INPROC command=%r handled=%r rv=%r action=%r",
                command,
                handled,
                rv,
                command_dispatch._ACTION_OCCURRED,
            )
        except Exception:
            pass
        return {
            "handled": handled,
            "return_value": rv,
            "action_occurred": bool(command_dispatch._ACTION_OCCURRED),
        }

    finally:
        command_dispatch._ACTION_OCCURRED = old_action
        set_current_request_context(previous_ctx)


def main():
    logging.info("STARTUP_SIGNATURE main.py loaded")
    try:
        # Surface shared and per-device override status in the boot log.
        try:
            import app_config as _pp
            _deployment_loaded = bool(getattr(_pp, "DEPLOYMENT_CONFIG_LOADED", False))
            _deployment_keys = list(getattr(_pp, "DEPLOYMENT_CONFIG_KEYS", []) or [])
            _local_loaded = bool(getattr(_pp, "LOCAL_PREFS_LOADED", False))
            _local_keys = list(getattr(_pp, "LOCAL_PREFS_KEYS", []) or [])
        except Exception:
            _deployment_loaded = False
            _deployment_keys = []
            _local_loaded = False
            _local_keys = []
        logging.info(
            "DEPLOYMENT_CONFIG loaded=%s keys=%r",
            _deployment_loaded, _deployment_keys,
        )
        logging.info(
            "LOCAL_PREFS loaded=%s keys=%r",
            _local_loaded, _local_keys,
        )
        logging.info(
            "FEATURE_FLAGS ptt_enabled=%s wakeword_enabled=%s wakeword_engine=%r wakeword_model=%r output_mode=%r output_room=%r chatgpt_model=%r wakeword_only_onhook=%s wakeword_chime=%s wakeword_rearm_sec=%.2f",
            _ptt_enabled(),
            _wakeword_enabled(),
            _wakeword_engine_name(),
            _wakeword_model_name(),
            _assistant_audio_output_mode(),
            _assistant_audio_output_room(),
            _chatgpt_model(),
            _wakeword_only_onhook(),
            _wakeword_chime_enabled(),
            _wakeword_rearm_sec(),
        )
    except Exception:
        logging.exception("FEATURE_FLAGS_LOG_FAIL")
    try:
        import sonos_controls as _sc
        logging.info(f"SONOS_CONTROLS_FILE_RUNTIME: {_sc.__file__}")
    except Exception as e:
        logging.error(f"SONOS_CONTROLS_FILE_RUNTIME error: {e}")
    warmup_audio_on_boot()
    # IDLE_MIC_EXERCISER_V2: disabled on wakeword-enabled box.
    # The wakeword listener owns the input stream continuously, so any mic
    # exerciser attempt would race for the device. Helpers remain defined in
    # this module for the original PTT-only Pi, but are not invoked here.
    try:
        logging.info("MIC_EXERCISE_BOOT_SKIP reason=wakeword_box")
    except Exception:
        pass
    try:
        _start_wakeword_listener_if_enabled()
    except Exception:
        logging.exception("WAKEWORD_LISTENER_BOOT_CALL_FAIL")
    def _rt_boot_warmup_worker():
        try:
            warmup_rt_streaming_on_boot()
        except Exception:
            logging.exception("RT_WARMUP_BOOT_CALL_FAIL")

    try:
        threading.Thread(
            target=_rt_boot_warmup_worker,
            name="rt_boot_warmup",
            daemon=True,
        ).start()
        logging.info("RT_WARMUP_BOOT_THREAD_STARTED")
    except Exception:
        logging.exception("RT_WARMUP_BOOT_THREAD_START_FAIL")

    # -----------------------------------------------------------------
    # Unified runtime: in-process HTTP/WS companion server.
    # When enabled, replaces piphone-wsh.service by serving the same
    # surface area from inside this process. The server itself fails closed
    # when its shared client key is missing; the local runtime keeps running.
    # -----------------------------------------------------------------
    try:
        import app_config as _pp
        if bool(getattr(_pp, "UNIFIED_SERVER_ENABLED", True)):
            try:
                import unified_server
                import sys as _sys
                try:
                    import private_config as _private_config
                    _UNIFIED_API_KEY = (
                        getattr(_private_config, "HOMESUITE_HTTP_API_KEY", "")
                        or getattr(_private_config, "PIPHONE_HTTP_API_KEY", "")
                        or ""
                    )
                except Exception:
                    _UNIFIED_API_KEY = ""
                unified_server.start_in_background_thread(
                    port=int(getattr(_pp, "UNIFIED_SERVER_PORT", 8765)),
                    api_key=_UNIFIED_API_KEY,
                    ha_url=HA_URL,
                    ha_token=HA_TOKEN,
                    runtime_module=_sys.modules[__name__],
                )
                logging.info("UNIFIED_SERVER_START_REQUESTED port=%d", int(getattr(_pp, "UNIFIED_SERVER_PORT", 8765)))
            except Exception:
                logging.exception("UNIFIED_SERVER_START_FAIL")
        else:
            logging.info("UNIFIED_SERVER_DISABLED pref=False")
    except Exception:
        logging.exception("UNIFIED_SERVER_PREF_READ_FAIL")


    try:
        import scheduler
        scheduler.set_executor(_execute_scheduled_command_in_process)

        try:
            import youtube_reel_scheduler
            youtube_reel_scheduler.register()
        except Exception:
            logging.exception("YT_REEL_SCHEDULER_REGISTER_FAIL")

        try:
            set_alarm_command_executor(_execute_scheduled_command_in_process)
            logging.info("ALARM_COMMAND_EXECUTOR_REGISTERED")
        except Exception:
            logging.exception("ALARM_COMMAND_EXECUTOR_REGISTER_FAIL")

        scheduler.start_scheduler()
        logging.info("SCHEDULER_STARTED_FROM_HOMESUITE mode=in_process")
    except Exception:
        logging.exception("SCHEDULER_START_FAIL")


    # Start auxiliary physical command buttons.
    #
    # These are separate from the handset hook/PTT switch. They execute normal
    # PiPhone command phrases through the same in-process command brain used by
    # scheduler/alarm attachments.
    try:
        import physical_button_controls

        def _physical_buttons_handset_is_up():
            try:
                return bool(globals().get("button_pressed", False))
            except Exception:
                return False

        physical_button_controls.start_physical_buttons(
            command_executor=lambda cmd: _execute_scheduled_command_in_process(
                cmd,
                source_id="physical_button",
                origin="button",
            ),
            handset_is_up=_physical_buttons_handset_is_up,
        )
        logging.info("PHYSICAL_BUTTONS_START_ATTEMPTED")
    except Exception:
        logging.exception("PHYSICAL_BUTTONS_START_FAIL")


    if _ptt_enabled():
        print("\nSystem ready - lift the handset to speak")
    else:
        print("\nSystem ready - PTT handset disabled")

    try:
        global is_processing, is_speaking, button_pressed, last_interaction_ts
        last_interaction_ts = _now_ts()

        with lock:
            is_processing = False
            is_speaking = False

        # warm the runnable cache once at startup (non-fatal if HA unreachable)
        try:
            refresh_runnable_cache(
                ha_get_states=ha_get_states,
                normalize_scene_phrase=lambda s: _normalize_scene_phrase(s, logger=logging),
                ttl_seconds=10 * 60,
                force=True,
            )
            logging.info(f"Runnable cache loaded: {get_runnable_cache_size()} items")
        except Exception:
            pass

        while True:
            if not _ptt_enabled():
                time.sleep(1.0)
                continue

            current_button_state = not GPIO.input(GPIO_PIN)

            if current_button_state and not button_pressed:
                with lock:
                    is_processing = False
                    is_speaking = False

                button_pressed = True
                # Reset per-offhook-session flags (so start-chime can play again on each handset lift)
                try:
                    globals()['_start_chime_played_in_session'] = False
                    globals()['_start_delay_applied_in_session'] = False
                    globals()['_audio_ensured_in_session'] = False
                    try:
                        logging.info('OFFHOOK_SESSION_BEGIN reset_session_flags=1')
                        # Optional fallback realtime warmup on off-hook
                        try:
                            _rt_warmup_start_on_offhook()
                        except Exception:
                            pass

                    except Exception:
                        pass
                except Exception:
                    pass
                global _start_delay_applied_in_session

                _start_delay_applied_in_session = False


                _start_chime_played_in_session = False
                # Optional post-TTS cooldown (prevents tail self-transcription). Set to 0 for instant follow-ups.
                _tts_cooldown_seconds = float(os.getenv(
                    "PIPHONE_TTS_COOLDOWN_SECONDS",
                    str(TTS_COOLDOWN_SECONDS or 0)
                ))
                print("Button pressed")

                # Off-hook session loop: record -> process one utterance -> repeat until hang up
                while GPIO.input(GPIO_PIN) == 0:
                    # Prevent recording while TTS is playing / just ended (avoids self-transcription loop)
                    if is_speaking:
                        if not getattr(main, '_wait_speaking', False):
                            _perf('PIPE_WAIT_SPEAKING_BEGIN')
                            main._wait_speaking = True
                        time.sleep(0.05)
                        continue
                    else:
                        if getattr(main, '_wait_speaking', False):
                            _perf('PIPE_WAIT_SPEAKING_END')
                            main._wait_speaking = False
                    # Optional cooldown after TTS ends. Set PIPHONE_TTS_COOLDOWN_SECONDS=0 for zero-latency follow-ups.
                    if _tts_cooldown_seconds > 0 and (_now_ts() - last_tts_end_ts) < _tts_cooldown_seconds:
                        time.sleep(0.01)
                        continue
                    _perf('PIPE_RECORD_CALL')
                    t_rec0 = time.monotonic()
                    audio_file = record_audio_with_vad()
                    _perf('PIPE_RECORD_RETURN', dt=round(time.monotonic()-t_rec0,4), ok=bool(audio_file))
                    # If handset was released during recording, treat as cancel and exit
                    if GPIO.input(GPIO_PIN) == 1:
                        break

                    # No speech captured; keep waiting while off-hook
                    if audio_file is None:
                        time.sleep(0.25)
                        continue

                    # Outcome tones are played inside process_audio() (success vs error).
                    process_audio(audio_file)

                    _perf('PIPE_AFTER_PROCESS_AUDIO')
                    # Ready chime for next utterance
                    if GPIO.input(GPIO_PIN) == 0:
                        pass

            elif not current_button_state and button_pressed:

                button_pressed = False
                print("Button released")

                # Immediately stop speech on hang-up
                stop_speaking_now()

                with lock:
                    is_processing = False
                    is_speaking = False

            time.sleep(0.005)

    except KeyboardInterrupt:
        cleanup_handler()
    except Exception as e:
        logging.error(f"Main error: {e}")
        print(f"Main error: {e}")
        traceback.print_exc()
        cleanup_handler()

if __name__ == "__main__":
    main()

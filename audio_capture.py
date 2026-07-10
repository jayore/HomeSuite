"""
Audio capture: pure VAD + streaming-STT helpers extracted from main.py.

This module owns:
  * frame-driven VAD utterance accumulation (_VadUtteranceAccumulator)
  * the canonical frame-source capture engine (_capture_utterance_from_frame_source)
  * realtime-streaming STT lifecycle helpers (_rt_stream_*)
  * STT artifact rotation (_rotate_active_stt_artifacts)

It does NOT own hardware acquisition: sounddevice InputStream + GPIO live
in gpio_ptt._record_audio_with_vad_capture_core, which composes these
helpers with the audio device boundary.

Perf logging: gpio_ptt wires its real _perf via set_perf_logger() at import
time. Until then, _perf is a no-op so this module remains importable in
isolation (tests, REPL).
"""

import math
import os

try:
    from env_compat import install_homesuite_env_aliases
    install_homesuite_env_aliases()
except Exception:
    pass
import time
import logging
from collections import deque
from pathlib import Path

import numpy as np

try:
    from scipy.signal import resample_poly as _resample_poly
except Exception:
    _resample_poly = None

try:
    import webrtcvad
except Exception:
    webrtcvad = None

from app_config import (
    MAX_UTTERANCE_SECONDS,
    VAD_MODE,
)


# Module-level VAD instance shared by the cluster. Mirrors the legacy
# gpio_ptt-resident `vad` global.
vad = webrtcvad.Vad(VAD_MODE) if webrtcvad is not None else None


# --- Perf-logger injection -----------------------------------------------
def _perf(tag: str, **kv):  # default no-op; overridden by gpio_ptt at import
    pass


def set_perf_logger(fn):
    """Allow gpio_ptt to wire its real _perf logger into this module."""
    global _perf
    _perf = fn


# --- STT artifact rotation -----------------------------------------------
def _rotate_active_stt_artifacts(audio_file: str) -> None:
    """
    Preserve the previous active recording/transcript for debugging while ensuring
    the *active* paths are clean for the next utterance.

    Example:
      recording.wav -> recording.prev.wav
      recording.wav.transcript -> recording.prev.wav.transcript
    """
    try:
        audio_path = Path(audio_file)
        transcript_path = Path(audio_file + ".transcript")

        moves = (
            (audio_path, audio_path.with_name(audio_path.stem + ".prev" + audio_path.suffix)),
            (
                transcript_path,
                transcript_path.with_name(
                    audio_path.stem + ".prev" + audio_path.suffix + ".transcript"
                ),
            ),
        )

        rotated = []
        for src, dst in moves:
            try:
                if not src.exists():
                    continue
                if dst.exists():
                    dst.unlink()
                src.replace(dst)
                rotated.append(f"{src.name}->{dst.name}")
            except Exception as e:
                logging.info("STT_ARTIFACT_ROTATE_ERR src=%s dst=%s err=%r", src, dst, e)

        if rotated:
            logging.info("STT_ARTIFACT_ROTATE %s", ", ".join(rotated))
        else:
            logging.info("STT_ARTIFACT_ROTATE none")
    except Exception as e:
        logging.info("STT_ARTIFACT_ROTATE_FAIL err=%r", e)


# --- VAD utterance accumulator -------------------------------------------
class _VadUtteranceAccumulator:
    """
    Frame-driven VAD utterance accumulator.

    This helper intentionally owns only VAD state, not audio-device ownership.
    That allows the existing PTT path to keep using its own InputStream while
    giving the wakeword path a future seam for same-stream capture.

    Input frames are expected to be mono int16 numpy arrays at a WebRTC VAD
    compatible sample rate and frame duration.
    """

    def __init__(
        self,
        *,
        vad_obj,
        pre_roll_frames: int,
        silence_end_frames: int,
        min_speech_frames: int,
        endpoint_window_frames: int = 0,
        endpoint_min_silence_ratio: float = 1.0,
        endpoint_trailing_silence_frames: int = 0,
    ):
        self.vad_obj = vad_obj
        self.pre_roll = deque(maxlen=int(pre_roll_frames))
        self.silence_end_frames = int(silence_end_frames)
        self.min_speech_frames = int(min_speech_frames)
        self.endpoint_window_frames = max(0, int(endpoint_window_frames))
        self.endpoint_min_silence_ratio = max(
            0.0,
            min(1.0, float(endpoint_min_silence_ratio)),
        )
        self.endpoint_required_silence_frames = int(
            math.ceil(
                self.endpoint_window_frames * self.endpoint_min_silence_ratio
            )
        )
        self.endpoint_trailing_silence_frames = max(
            0,
            int(endpoint_trailing_silence_frames),
        )
        self.endpoint_window = (
            deque(maxlen=self.endpoint_window_frames)
            if self.endpoint_window_frames > 0
            else None
        )
        self.captured = []
        self.speech_started = False
        self.silence_frames = 0
        self.speech_frames = 0
        self.done = False

    @property
    def captured_len(self) -> int:
        try:
            return int(len(self.captured))
        except Exception:
            return 0

    def prime(self, arr) -> None:
        """
        Add a frame to pre-roll without allowing it to start speech.

        Used by wakeword capture to keep immediate post-wakeword audio while
        preventing the acknowledgement chime / wakeword tail from triggering
        VAD endpointing.
        """
        if self.done or self.speech_started:
            return
        try:
            self.pre_roll.append(arr)
        except Exception:
            pass

    def push(self, arr, sample_rate: int) -> str:
        """
        Consume one PCM frame.

        Returns one of:
        * ""              no notable transition
        * "speech_start"  first speech frame accepted
        * "endpoint"      endpoint reached after speech + silence
        """
        if self.done:
            return ""

        is_voice = bool(self.vad_obj.is_speech(arr.tobytes(), int(sample_rate)))

        self.pre_roll.append(arr)

        if not self.speech_started:
            if is_voice:
                self.speech_started = True
                self.captured.extend(list(self.pre_roll))
                self.pre_roll.clear()
                self.speech_frames += 1
                self.silence_frames = 0
                if self.endpoint_window is not None:
                    self.endpoint_window.append(False)
                return "speech_start"
            return ""

        self.captured.append(arr)

        if is_voice:
            self.speech_frames += 1
            self.silence_frames = 0
        else:
            self.silence_frames += 1

        if self.endpoint_window is not None:
            self.endpoint_window.append(not is_voice)
            endpoint_ready = (
                len(self.endpoint_window) >= self.endpoint_window_frames
                and sum(self.endpoint_window)
                >= self.endpoint_required_silence_frames
                and self.silence_frames
                >= self.endpoint_trailing_silence_frames
            )
        else:
            endpoint_ready = self.silence_frames >= self.silence_end_frames

        if endpoint_ready and self.speech_frames >= self.min_speech_frames:
            self.done = True
            return "endpoint"

        return ""

    def has_audio(self) -> bool:
        return bool(self.speech_started and self.captured)

    def audio_data(self):
        if not self.captured:
            return None
        return np.concatenate(self.captured)


# --- Realtime streaming STT helpers --------------------------------------
def _rt_stream_mode_enabled() -> bool:
    try:
        stt_mode = (os.getenv("PIPHONE_STT_MODE", "whisper") or "").strip().lower()
    except Exception:
        stt_mode = "whisper"
    return stt_mode in ("realtime_stream", "rt_stream", "realtime_streaming")


def _rt_stream_create_runtime(
    pre_roll_frames: int,
    *,
    manual_commit: bool = False,
):
    """
    Create a small runtime object for shared realtime-streaming STT capture.

    This centralizes the previously PTT-only logic so both PTT and wakeword
    capture can feed the same streaming transcription path.
    """
    if not _rt_stream_mode_enabled():
        return None

    try:
        from realtime_streaming_stt import StreamingTranscriber
    except Exception as e:
        logging.error(f"STT_RT_STREAM_ERR import failed: {e}")
        return None

    try:
        rt_model = (os.getenv("PIPHONE_RT_MODEL", "") or "").strip() or "gpt-4o-transcribe"
        rt_lang = (os.getenv("PIPHONE_RT_LANG", "en") or "en").strip()
        try:
            rt = StreamingTranscriber(
                model=rt_model,
                language=rt_lang,
                manual_commit=manual_commit,
                timeout_s=(8.0 if manual_commit else 4.0),
            )
        except TypeError:
            rt = StreamingTranscriber(model=rt_model)
    except Exception as e:
        logging.error(f"STT_RT_STREAM_ERR init failed: {e}")
        return None

    try:
        from collections import deque as _rt_deque
        rt_pre_roll_pcm = _rt_deque(maxlen=int(pre_roll_frames))
    except Exception:
        rt_pre_roll_pcm = []

    try:
        rt_dump = bool(int((os.getenv("PIPHONE_RT_DUMP_SENT_WAV", "0") or "0").strip() or "0"))
    except Exception:
        rt_dump = False

    try:
        rt_dump_max_sec = float(os.getenv("PIPHONE_RT_DUMP_SENT_SECONDS", "6") or "6")
    except Exception:
        rt_dump_max_sec = 6.0

    return {
        "rt": rt,
        "model": rt_model,
        "lang": rt_lang,
        "manual_commit": bool(manual_commit),
        "pre_roll_pcm": rt_pre_roll_pcm,
        "voice_started": False,
        "last_rt_log": time.time(),
        "dump": rt_dump,
        "dump_max_sec": rt_dump_max_sec,
        "dump_sr": None,
        "dump_bytes": bytearray(),
        "final_text": "",
    }


def _rt_stream_prepare_pcm(arr, sr: int):
    """
    Prepare PCM for realtime-streaming STT transport.

    Existing behavior:
    * 48kHz capture is downsampled to 24kHz for streaming transport
    * otherwise the original PCM is passed through
    """
    pcm_sr = sr
    pcm_bytes = arr.tobytes()

    if sr == 48000:
        try:
            if _resample_poly is not None:
                ds = _resample_poly(arr.astype("float32", copy=False), up=1, down=2)
                ds_i16 = np.clip(np.rint(ds), -32768, 32767).astype(np.int16)
                pcm_sr = 24000
                pcm_bytes = ds_i16.tobytes()
            else:
                pcm_sr = 24000
                pcm_bytes = arr[::2].tobytes()
        except Exception:
            pcm_sr = 24000
            pcm_bytes = arr[::2].tobytes()

    return pcm_sr, pcm_bytes


def _rt_stream_append_frame(runtime, arr, sr: int):
    if not runtime:
        return

    rt = runtime.get("rt")
    if rt is None:
        return

    try:
        pcm_sr, pcm_bytes = _rt_stream_prepare_pcm(arr, sr)
        runtime["pre_roll_pcm"].append((pcm_sr, pcm_bytes))

        rt_is_voice = False
        try:
            rt_is_voice = bool(vad.is_speech(arr.tobytes(), sr))
        except Exception:
            rt_is_voice = False

        if rt_is_voice and not runtime.get("voice_started", False):
            try:
                for _sr0, _b0 in list(runtime.get("pre_roll_pcm") or []):
                    rt.append_pcm16(_b0, sr_in=_sr0)
                    if runtime.get("dump"):
                        if runtime.get("dump_sr") is None:
                            runtime["dump_sr"] = _sr0
                        if runtime.get("dump_sr") == _sr0 and len(runtime.get("dump_bytes", b"")) < int(runtime.get("dump_max_sec", 6.0) * _sr0 * 2):
                            runtime["dump_bytes"].extend(_b0)
            except Exception:
                pass
            runtime["voice_started"] = True

        if runtime.get("voice_started", False):
            rt.append_pcm16(pcm_bytes, sr_in=pcm_sr)
            now = time.time()
            if (now - float(runtime.get("last_rt_log", 0.0))) >= 1.0:
                logging.info(f"STT_RT_STREAM_APPEND sr={pcm_sr} bytes={len(pcm_bytes)}")
                runtime["last_rt_log"] = now

            if runtime.get("dump"):
                if runtime.get("dump_sr") is None:
                    runtime["dump_sr"] = pcm_sr
                if runtime.get("dump_sr") == pcm_sr and len(runtime.get("dump_bytes", b"")) < int(runtime.get("dump_max_sec", 6.0) * pcm_sr * 2):
                    runtime["dump_bytes"].extend(pcm_bytes)

    except Exception as e:
        logging.error(f"STT_RT_STREAM_ERR append failed: {e}")
        runtime["rt"] = None


def _rt_stream_finalize_to_sidecar(runtime, transcript_path: str):
    if not runtime:
        return ""

    rt = runtime.get("rt")
    if rt is None:
        return ""

    final_text = ""
    try:
        final_text = (rt.commit_and_wait() or "").strip()
        runtime["final_text"] = final_text
        if final_text:
            logging.info(f"STT_RT_STREAM_FINAL: {final_text!r}")
            try:
                with open(transcript_path, "w", encoding="utf-8") as f:
                    f.write(final_text)
                    f.write("\n")
            except Exception as e:
                logging.warning(f"Sidecar transcript write failed: {e}")

            try:
                if runtime.get("dump") and runtime.get("dump_bytes"):
                    import wave
                    _p = '/tmp/piphone_rt_sent.wav'
                    _srw = int(runtime.get("dump_sr") or 24000)
                    with wave.open(_p, 'wb') as _w:
                        _w.setnchannels(1)
                        _w.setsampwidth(2)
                        _w.setframerate(_srw)
                        _w.writeframes(bytes(runtime.get("dump_bytes")))
                    logging.info('RT_DUMP_WAV_WROTE path=%s sr=%s bytes=%s', _p, _srw, len(runtime.get("dump_bytes")))
            except Exception as _e:
                logging.info('RT_DUMP_WAV_ERR %r', _e)

    except Exception as e:
        logging.error(f"STT_RT_STREAM_ERR commit failed: {e}")
    finally:
        try:
            rt.cancel()
        except Exception:
            pass

    return final_text


# --- Frame-driven capture engine -----------------------------------------
def _capture_utterance_from_frame_source(
    *,
    frame_reader,
    sample_rate: int,
    continue_recording_fn,
    cancelled_fn,
    pre_roll_frames: int,
    silence_end_frames: int,
    min_speech_frames: int,
    prime_only_until_ts: float = 0.0,
    prime_only_frames: int = 0,
    endpoint_window_frames: int = 0,
    endpoint_min_silence_ratio: float = 1.0,
    endpoint_trailing_silence_frames: int = 0,
    perf_prefix: str = "VAD",
    sleep_per_frame_sec: float = 0.001,
    rt_stream_runtime=None,
    first_speech_deadline_ts: float = 0.0,
):
    """
    Canonical frame-driven utterance capture engine.

    This is the seam shared by:
    * PTT capture (stream owned inside the PTT wrapper)
    * wakeword same-stream capture (stream owned by wakeword listener)

    Policy differences remain in the wrappers.
    Capture behavior should live here.
    """
    vad_capture = _VadUtteranceAccumulator(
        vad_obj=vad,
        pre_roll_frames=pre_roll_frames,
        silence_end_frames=silence_end_frames,
        min_speech_frames=min_speech_frames,
        endpoint_window_frames=endpoint_window_frames,
        endpoint_min_silence_ratio=endpoint_min_silence_ratio,
        endpoint_trailing_silence_frames=endpoint_trailing_silence_frames,
    )

    start_ts = time.monotonic()
    speech_start_elapsed_ms = None
    frames_seen = 0
    prime_only_frame_count = max(0, int(prime_only_frames))

    while continue_recording_fn():
        if (time.monotonic() - start_ts) >= MAX_UTTERANCE_SECONDS:
            break

        # First-speech timeout: if VAD hasn't seen speech_start within the
        # configured window after capture began, abort cleanly. Prevents the
        # listener from staying suppressed in a long silent capture when the
        # user said the wake word but did not follow up.
        if (
            first_speech_deadline_ts
            and not vad_capture.speech_started
            and time.monotonic() >= first_speech_deadline_ts
        ):
            try:
                logging.info(
                    "%s_FIRST_SPEECH_TIMEOUT elapsed_sec=%.2f",
                    perf_prefix, time.monotonic() - start_ts,
                )
            except Exception:
                pass
            try:
                _perf(f"{perf_prefix}_FIRST_SPEECH_TIMEOUT")
            except Exception:
                pass
            break

        try:
            arr = frame_reader()
        except Exception:
            logging.exception("%s_FRAME_READ_FAIL", perf_prefix)
            return None

        if arr is None:
            time.sleep(sleep_per_frame_sec)
            continue

        try:
            arr = np.asarray(arr, dtype=np.int16)
            if arr.ndim > 1:
                arr = arr[:, 0]
        except Exception:
            logging.exception("%s_FRAME_NORMALIZE_FAIL", perf_prefix)
            return None

        if arr.size <= 0:
            time.sleep(sleep_per_frame_sec)
            continue

        if rt_stream_runtime is not None:
            _rt_stream_append_frame(rt_stream_runtime, arr, sample_rate)

        frames_seen += 1
        now = time.monotonic()
        if (
            frames_seen <= prime_only_frame_count
            or (prime_only_until_ts and now < prime_only_until_ts)
        ):
            vad_capture.prime(arr)
            time.sleep(sleep_per_frame_sec)
            continue

        try:
            vad_event = vad_capture.push(arr, sample_rate)
        except Exception:
            logging.exception("%s_VAD_PUSH_FAIL", perf_prefix)
            return None

        if vad_event == "speech_start":
            speech_start_elapsed_ms = (now - start_ts) * 1000.0
            try:
                logging.info("%s_SPEECH_START elapsed_ms=%.1f", perf_prefix, speech_start_elapsed_ms)
            except Exception:
                pass
            _perf(f"{perf_prefix}_SPEECH_START")
        elif vad_event == "endpoint":
            _perf(
                f"{perf_prefix}_ENDPOINT",
                silence_frames=vad_capture.silence_frames,
                speech_frames=vad_capture.speech_frames,
            )
            break

        time.sleep(sleep_per_frame_sec)

    _perf(
        f"{perf_prefix}_DONE",
        speech_started=vad_capture.speech_started,
        frames=vad_capture.captured_len,
        speech_frames=vad_capture.speech_frames,
        silence_frames=vad_capture.silence_frames,
    )

    if cancelled_fn():
        try:
            _perf(
                f"{perf_prefix}_RETURN_NONE",
                reason="cancelled_after_record",
                speech_started=bool(vad_capture.speech_started),
                captured_len=int(vad_capture.captured_len),
            )
        except Exception:
            pass
        return None

    if not vad_capture.has_audio():
        try:
            _perf(
                f"{perf_prefix}_RETURN_NONE",
                reason="no_speech_or_empty",
                speech_started=bool(vad_capture.speech_started),
                captured_len=int(vad_capture.captured_len),
                speech_frames=int(vad_capture.speech_frames),
                silence_frames=int(vad_capture.silence_frames),
            )
        except Exception:
            pass
        return None

    audio_data = vad_capture.audio_data()
    if audio_data is None:
        try:
            _perf(
                f"{perf_prefix}_RETURN_NONE",
                reason="audio_data_empty",
                speech_started=bool(vad_capture.speech_started),
                captured_len=int(vad_capture.captured_len),
            )
        except Exception:
            pass
        return None

    return {
        "audio_data": audio_data,
        "speech_started": bool(vad_capture.speech_started),
        "captured_len": int(vad_capture.captured_len),
        "speech_frames": int(vad_capture.speech_frames),
        "silence_frames": int(vad_capture.silence_frames),
        "speech_start_elapsed_ms": speech_start_elapsed_ms,
    }

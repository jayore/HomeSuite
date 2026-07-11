"""Wakeword engine lifecycle and detection callback adapter.

``WakewordListener`` selects an engine, owns its background thread, applies
shared suppression and rearm policy, and translates a detection into the
callback contract consumed by ``main.py``. Engine-specific OpenWakeWord audio
capture lives in :mod:`wakeword_openwakeword`; Porcupine remains here as the
legacy alternative.

The listener detects only. Command recording and processing are delegated to
the callback so wakeword-specific capture cannot silently replace the PTT path.
"""

from __future__ import annotations

import logging
import math
import os

try:
    from env_compat import install_homesuite_env_aliases
    install_homesuite_env_aliases()
except Exception:
    pass
import threading
import time
from collections import deque
from typing import Callable, Optional


def _get_porcupine_access_key() -> str:
    try:
        k = (os.environ.get("PVPORCUPINE_ACCESS_KEY", "") or "").strip()
        if k:
            return k
    except Exception:
        pass
    try:
        import private_config
        k = getattr(private_config, "PVPORCUPINE_ACCESS_KEY", "") or ""
        k = str(k).strip()
        if k:
            return k
    except Exception:
        pass
    return ""


class WakewordListener:
    """Run one configured wakeword engine and emit debounced detections.

    ``should_listen_fn`` and ``suppress_reason_fn`` are evaluated by the engine
    loop so the application remains the source of truth for busy, off-hook,
    playback, and processing state. ``on_detected_fn`` may receive a live frame
    reader plus pre-trigger audio when the engine supports same-stream handoff.
    """

    def __init__(
        self,
        *,
        engine: str,
        model: str = "",
        should_listen_fn: Optional[Callable[[], bool]] = None,
        suppress_reason_fn: Optional[Callable[[], str]] = None,
        threshold_fn: Optional[Callable[[], float]] = None,
        on_detected_fn: Optional[Callable[[], None]] = None,
        rearm_sec: float = 1.5,
        logger=None,
    ):
        self.engine = (engine or "disabled").strip().lower()
        self.model = (model or "").strip()
        self.should_listen_fn = should_listen_fn or (lambda: False)
        self.suppress_reason_fn = suppress_reason_fn or (lambda: "unknown")
        self.threshold_fn = threshold_fn
        self.on_detected_fn = on_detected_fn
        self.rearm_sec = float(rearm_sec or 1.5)
        self.log = logger or logging.getLogger(__name__)

        self._thread = None
        self._stop_event = threading.Event()
        self._started = False
        self._last_suppress_reason = None
        self._last_suppress_log_ts = 0.0
        self._last_detect_ts = 0.0
        # External flush request. Set True by request_flush() to make the
        # listener loop clear its OWW preprocessor buffers + diagnostic deques
        # at the top of the next iteration, regardless of suppression state.
        # Closes the gap where the in-loop suppress->listen flush was skipped
        # on error-path interactions that played no SFX.
        self._pending_flush = False
        self._owned_model = None
        self._owned_source = None
        self._owned_pretrigger_buffer = None
        self._owned_hit_audio_chunks = None

    def start(self) -> bool:
        if self._started:
            return True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="wakeword_listener")
        self._thread.start()
        self._started = True
        self.log.info(
            "WAKEWORD_LISTENER_START engine=%r model=%r rearm_sec=%.2f",
            self.engine, self.model, self.rearm_sec,
        )
        return True

    def stop(self) -> None:
        self._stop_event.set()
        self._started = False
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=3.0)
        self._thread = None
        self.log.info("WAKEWORD_LISTENER_STOP")

    def request_flush(self) -> None:
        """Ask the listener thread to flush its OWW preprocessor buffers and
        diagnostic deques at the top of its next loop iteration. Safe to call
        from any thread (just a flag write)."""
        self._pending_flush = True

    def _should_log_suppression(self, reason: str) -> bool:
        now = time.monotonic()
        if reason != self._last_suppress_reason:
            self._last_suppress_reason = reason
            self._last_suppress_log_ts = now
            return True
        if (now - self._last_suppress_log_ts) >= 10.0:
            self._last_suppress_log_ts = now
            return True
        return False

    def _emit_detected(self, **kwargs) -> bool:
        """Apply the engine-independent rearm guard and invoke the callback."""
        now = time.monotonic()
        if (now - self._last_detect_ts) < self.rearm_sec:
            self.log.info(
                "WAKEWORD_DETECTED_IGNORED reason=rearm dt=%.2f rearm_sec=%.2f",
                now - self._last_detect_ts, self.rearm_sec,
            )
            return False
        self._last_detect_ts = now
        self.log.info("WAKEWORD_DETECTED engine=%r model=%r", self.engine, self.model)

        if callable(self.on_detected_fn):
            try:
                self.on_detected_fn(**kwargs)
            except TypeError:
                try:
                    self.on_detected_fn()
                except Exception:
                    self.log.exception("WAKEWORD_ON_DETECTED_FAIL")
                    return False
            except Exception:
                self.log.exception("WAKEWORD_ON_DETECTED_FAIL")
                return False
        return True

    def _run(self) -> None:
        """Select and run the configured engine inside the listener thread."""
        try:
            if self.engine in ("", "disabled", "none"):
                self.log.info("WAKEWORD_LISTENER_DISABLED engine=%r", self.engine)
                return
            if self.engine == "stub":
                self._run_stub()
                return
            if self.engine == "porcupine":
                self._run_porcupine()
                return
            if self.engine == "openwakeword":
                from wakeword_openwakeword import run_openwakeword

                run_openwakeword(self)
                return
            self.log.warning("WAKEWORD_ENGINE_UNSUPPORTED engine=%r", self.engine)
        except Exception:
            self.log.exception("WAKEWORD_LISTENER_CRASH")

    def _run_stub(self) -> None:
        self.log.info("WAKEWORD_ENGINE_STUB_READY")
        while not self._stop_event.is_set():
            if not self.should_listen_fn():
                reason = self.suppress_reason_fn() or "suppressed"
                if self._should_log_suppression(reason):
                    self.log.info("WAKEWORD_SUPPRESS reason=%s", reason)
                time.sleep(0.25)
                continue
            time.sleep(0.25)

    def _pick_sd_input_device_index(self) -> int:
        try:
            v = (os.environ.get("PIPHONE_SD_INPUT_INDEX", "") or "").strip()
            if v:
                return int(v)
        except Exception:
            pass
        try:
            import sounddevice as sd
            want = (os.environ.get("PIPHONE_SD_INPUT_MATCH", "USB") or "").strip().lower()
            if not want:
                return -1
            for i, d in enumerate(sd.query_devices()):
                try:
                    if int(d.get("max_input_channels", 0) or 0) <= 0:
                        continue
                    name = str(d.get("name", "") or "").lower()
                    if want in name:
                        return int(i)
                except Exception:
                    continue
        except Exception:
            pass
        return -1

    def _pref(self, name, default):
        try:
            import app_config
            return getattr(app_config, name, default)
        except Exception:
            return default

    def _get_openwakeword_threshold(self) -> float:
        if callable(self.threshold_fn):
            try:
                return float(self.threshold_fn())
            except Exception:
                self.log.exception("WAKEWORD_THRESHOLD_PROVIDER_FAIL")
        return float(self._pref("WAKEWORD_THRESHOLD", 0.5))

    def _get_openwakeword_vad_threshold(self) -> float:
        return float(self._pref("WAKEWORD_VAD_THRESHOLD", 0.5))

    def _get_openwakeword_debounce_sec(self) -> float:
        return float(self._pref("WAKEWORD_DEBOUNCE_SEC", 1.5))

    def _get_openwakeword_near_miss_min(self) -> float:
        return float(self._pref("WAKEWORD_NEAR_MISS_MIN_SCORE", 0.25))

    def _get_openwakeword_model_paths(self):
        paths = self._pref("WAKEWORD_MODEL_PATHS", []) or []
        if isinstance(paths, (list, tuple)):
            return [str(p).strip() for p in paths if str(p).strip()]
        return []

    def _get_pretrigger_buffer_ms(self) -> int:
        ms = int(self._pref("WAKEWORD_STREAM_PRE_ROLL_MS", 900))
        return max(200, min(2000, ms + 100))

    # ------------------------------------------------------------------ #
    #  Porcupine engine (unchanged from original)                        #
    # ------------------------------------------------------------------ #

    def _run_porcupine(self) -> None:
        try:
            import struct
            import sounddevice as sd
            import pvporcupine
        except Exception:
            self.log.exception("WAKEWORD_ENGINE_PORCUPINE_IMPORT_FAIL")
            return

        porcupine = None
        stream = None
        last_active_log_ts = 0.0
        active_logged = False

        try:
            access_key = _get_porcupine_access_key()
            if not access_key:
                self.log.error("WAKEWORD_ENGINE_PORCUPINE_NO_ACCESS_KEY")
                return

            create_kwargs = {"access_key": access_key}
            if self.model:
                if "/" in self.model or self.model.endswith(".ppn"):
                    create_kwargs["keyword_paths"] = [self.model]
                else:
                    create_kwargs["keywords"] = [self.model]
            else:
                create_kwargs["keywords"] = ["porcupine"]

            porcupine = pvporcupine.create(**create_kwargs)
            frame_length = int(porcupine.frame_length)
            sample_rate = int(porcupine.sample_rate)
            sd_dev = self._pick_sd_input_device_index()

            self.log.info(
                "WAKEWORD_ENGINE_PORCUPINE_READY model=%r sample_rate=%s frame_length=%s sd_dev=%s",
                self.model, sample_rate, frame_length, sd_dev,
            )

            stream = sd.InputStream(
                device=(sd_dev if sd_dev >= 0 else None),
                samplerate=sample_rate,
                channels=1,
                dtype="int16",
                blocksize=frame_length,
                latency="low",
            )
            stream.start()

            while not self._stop_event.is_set():
                if not self.should_listen_fn():
                    active_logged = False
                    reason = self.suppress_reason_fn() or "suppressed"
                    if self._should_log_suppression(reason):
                        self.log.info("WAKEWORD_SUPPRESS reason=%s", reason)
                    time.sleep(0.10)
                    continue

                now = time.time()
                if not active_logged:
                    self.log.info("WAKEWORD_ENGINE_PORCUPINE_LISTENING")
                    active_logged = True
                    last_active_log_ts = now
                elif (now - last_active_log_ts) >= 10.0:
                    self.log.info("WAKEWORD_ENGINE_PORCUPINE_IDLE_TICK")
                    last_active_log_ts = now

                data, _ = stream.read(frame_length)
                pcm = data[:, 0] if getattr(data, "ndim", 1) > 1 else data
                pcm_bytes = pcm.tobytes()
                try:
                    frame = struct.unpack_from("h" * frame_length, pcm_bytes)
                except Exception:
                    continue
                try:
                    result = porcupine.process(frame)
                except Exception:
                    self.log.exception("WAKEWORD_ENGINE_PORCUPINE_PROCESS_FAIL")
                    time.sleep(0.10)
                    continue
                if result >= 0:
                    self._emit_detected()
                    time.sleep(0.05)

        except Exception:
            self.log.exception("WAKEWORD_ENGINE_PORCUPINE_RUNTIME_FAIL")
        finally:
            try:
                if stream is not None:
                    stream.stop()
                    stream.close()
            except Exception:
                pass
            try:
                if porcupine is not None:
                    porcupine.delete()
            except Exception:
                pass

    def trigger_test_detection(self) -> bool:
        return self._emit_detected()

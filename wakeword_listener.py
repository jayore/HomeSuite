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
    """
    Reference-style wakeword listener using OpenWakeWord with built-in
    Silero VAD gating and debounce.
    """

    def __init__(
        self,
        *,
        engine: str,
        model: str = "",
        should_listen_fn: Optional[Callable[[], bool]] = None,
        suppress_reason_fn: Optional[Callable[[], str]] = None,
        on_detected_fn: Optional[Callable[[], None]] = None,
        rearm_sec: float = 1.5,
        logger=None,
    ):
        self.engine = (engine or "disabled").strip().lower()
        self.model = (model or "").strip()
        self.should_listen_fn = should_listen_fn or (lambda: False)
        self.suppress_reason_fn = suppress_reason_fn or (lambda: "unknown")
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
        self.log.info("WAKEWORD_LISTENER_STOP")

    def request_flush(self) -> None:
        """Ask the listener thread to flush its OWW preprocessor buffers and
        diagnostic deques at the top of its next loop iteration. Safe to call
        from any thread (just a flag write)."""
        self._pending_flush = True

    def _should_log_suppression(self, reason: str) -> bool:
        now = time.time()
        if reason != self._last_suppress_reason:
            self._last_suppress_reason = reason
            self._last_suppress_log_ts = now
            return True
        if (now - self._last_suppress_log_ts) >= 10.0:
            self._last_suppress_log_ts = now
            return True
        return False

    def _emit_detected(self, **kwargs) -> bool:
        now = time.time()
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
                self._run_openwakeword()
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
    #  OpenWakeWord engine                                                #
    # ------------------------------------------------------------------ #

    def _run_openwakeword(self) -> None:
        try:
            import sounddevice as sd
            import numpy as np
            from openwakeword.model import Model
        except Exception:
            self.log.exception("WAKEWORD_ENGINE_OPENWAKEWORD_IMPORT_FAIL")
            return

        # Watchdog: the listener loop can hang silently if PortAudio's
        # stream.read() blocks forever (e.g. ALSA XRUN with no recovery,
        # USB audio device hiccup). The existing try/except around read()
        # only catches thrown exceptions, not indefinite blocks. We track
        # the timestamp of every loop iteration and a daemon thread
        # force-exits the process if it goes stale; systemd's
        # Restart=always then brings it back up in ~2s.
        import os as _os
        watchdog_stale_sec = 30.0
        self._last_loop_ts = time.time()

        def _watchdog():
            while not self._stop_event.is_set():
                time.sleep(5.0)
                try:
                    age = time.time() - self._last_loop_ts
                except Exception:
                    continue
                if age > watchdog_stale_sec:
                    try:
                        self.log.error(
                            "WAKEWORD_ENGINE_OPENWAKEWORD_WATCHDOG_KILL "
                            "loop_age_sec=%.1f stale_threshold_sec=%.1f — exiting for systemd restart",
                            age, watchdog_stale_sec,
                        )
                    except Exception:
                        pass
                    _os._exit(1)
                    return

        threading.Thread(target=_watchdog, name="wakeword_watchdog", daemon=True).start()

        model = None
        stream = None
        last_active_log_ts = 0.0
        active_logged = False

        try:
            threshold = self._get_openwakeword_threshold()
            vad_threshold = self._get_openwakeword_vad_threshold()
            debounce_sec = self._get_openwakeword_debounce_sec()
            near_miss_min = self._get_openwakeword_near_miss_min()
            last_near_miss_log_ts = 0.0
            model_paths = self._get_openwakeword_model_paths()
            selected_label = (self.model or "").strip()

            # ---- Audio rates ----
            # OWW operates at 16 kHz internally. Many USB audio dongles only
            # support 44100/48000, so we open at the device's preferred rate
            # and resample each chunk to 16 kHz before feeding OWW.
            target_sr = 16000
            oww_chunk = 1280  # 80 ms at 16 kHz

            sd_dev = self._pick_sd_input_device_index()

            input_sr = None
            try:
                env_sr = (os.environ.get("PIPHONE_SD_SAMPLERATE", "") or "").strip()
                if env_sr:
                    input_sr = int(env_sr)
            except Exception:
                input_sr = None
            if not input_sr:
                try:
                    dev_info = sd.query_devices(sd_dev if sd_dev >= 0 else None)
                    input_sr = int(float(dev_info.get("default_samplerate", 48000)))
                except Exception:
                    input_sr = 48000

            # Scale chunk so each read produces ~80 ms of audio.
            frame_duration_sec = float(oww_chunk) / float(target_sr)
            input_blocksize = max(1, int(round(input_sr * frame_duration_sec)))

            # Precompute resample factors.
            need_resample = (input_sr != target_sr)
            if need_resample:
                _g = math.gcd(int(input_sr), int(target_sr))
                resample_up = int(target_sr // _g)
                resample_down = int(input_sr // _g)

            # ---- OWW Model ----
            speex_available = False
            try:
                import speexdsp_ns  # noqa: F401
                speex_available = True
            except Exception:
                pass

            model_kwargs = {
                "vad_threshold": vad_threshold,
                "enable_speex_noise_suppression": speex_available,
            }
            if model_paths:
                # OpenWakeWord's Model() takes `wakeword_model_paths` (the older
                # `wakeword_models` name was an incorrect guess that silently
                # tripped the TypeError fallback and loaded default built-ins).
                model_kwargs["wakeword_model_paths"] = model_paths
            try:
                model = Model(**model_kwargs)
            except TypeError:
                self.log.warning("WAKEWORD_ENGINE_OPENWAKEWORD_KWARGS_FALLBACK")
                model = Model()

            self.log.info(
                "WAKEWORD_ENGINE_OPENWAKEWORD_READY model=%r threshold=%.3f "
                "vad_threshold=%.3f debounce_sec=%.2f speex=%s sd_dev=%s "
                "input_sr=%s target_sr=%s input_blocksize=%s",
                selected_label, threshold, vad_threshold, debounce_sec,
                speex_available, sd_dev, input_sr, target_sr, input_blocksize,
            )
            try:
                available = sorted(list(getattr(model, "models", {}).keys()))
                self.log.info(
                    "WAKEWORD_ENGINE_OPENWAKEWORD_MODELS available=%r selected=%r",
                    available, selected_label,
                )
            except Exception:
                pass

            # ---- Input stream ----
            stream = sd.InputStream(
                device=(sd_dev if sd_dev >= 0 else None),
                samplerate=input_sr,
                channels=1,
                dtype="int16",
                blocksize=input_blocksize,
                latency="low",
            )
            stream.start()

            # ---- Pre-trigger ring buffer (10 ms frames at input_sr) ----
            pretrigger_ms = self._get_pretrigger_buffer_ms()
            pretrigger_frame_ms = 10
            pretrigger_frame_samples = max(1, int(round(input_sr * pretrigger_frame_ms / 1000.0)))
            pretrigger_max_frames = max(1, int(round(pretrigger_ms / float(pretrigger_frame_ms))))
            pretrigger_buffer = deque(maxlen=pretrigger_max_frames)
            self.log.info(
                "WAKEWORD_ENGINE_OPENWAKEWORD_PRETRIGGER ms=%s frame_ms=%s "
                "frame_samples=%s max_frames=%s",
                pretrigger_ms, pretrigger_frame_ms,
                pretrigger_frame_samples, pretrigger_max_frames,
            )

            # ---- HIT audio dump ring buffer (1.5s of raw chunks) ----
            hit_dump_max = max(2, int(round(1.5 / frame_duration_sec)))
            hit_audio_chunks = deque(maxlen=hit_dump_max)

            # Publish key state to the instance so request_flush() (called
            # from another thread) can be honored at the top of the loop.
            self._owned_model = model
            self._owned_pretrigger_buffer = pretrigger_buffer
            self._owned_hit_audio_chunks = hit_audio_chunks

            # ================================================================
            #  Main listener loop
            # ================================================================
            while not self._stop_event.is_set():
                # Watchdog heartbeat: every iteration of this loop is a sign
                # of life — both during active listening (we read frames) and
                # during suppression (we sleep 100ms and continue). If this
                # stops updating, _watchdog() will kill the process.
                self._last_loop_ts = time.time()

                # External flush request (e.g. from gpio_ptt's
                # _handle_wakeword_detected finally block). Runs before the
                # suppression check so an interaction-end flush happens even
                # when no SFX-driven suppress->listen transition occurs.
                if self._pending_flush:
                    self._pending_flush = False
                    try:
                        model.reset()
                        pp = getattr(model, "preprocessor", None)
                        if pp is not None:
                            try:
                                pp.raw_data_buffer.clear()
                            except Exception:
                                pass
                            try:
                                if hasattr(pp, "feature_buffer") and pp.feature_buffer is not None:
                                    pp.feature_buffer = np.zeros_like(pp.feature_buffer)
                            except Exception:
                                pass
                            try:
                                if hasattr(pp, "melspectrogram_buffer") and pp.melspectrogram_buffer is not None:
                                    pp.melspectrogram_buffer = np.zeros_like(pp.melspectrogram_buffer)
                            except Exception:
                                pass
                        try:
                            pretrigger_buffer.clear()
                        except Exception:
                            pass
                        try:
                            hit_audio_chunks.clear()
                        except Exception:
                            pass
                        self.log.info("WAKEWORD_ENGINE_OPENWAKEWORD_EXTERNAL_FLUSH done=True")
                    except Exception:
                        self.log.exception("WAKEWORD_ENGINE_OPENWAKEWORD_EXTERNAL_FLUSH_FAIL")

                if not self.should_listen_fn():
                    active_logged = False
                    reason = self.suppress_reason_fn() or "suppressed"
                    if self._should_log_suppression(reason):
                        self.log.info("WAKEWORD_SUPPRESS reason=%s", reason)
                    time.sleep(0.10)
                    continue

                now = time.time()
                if not active_logged:
                    # Drain the ENTIRE input buffer that accumulated during
                    # suppression. While suppressed (TTS playing, success/error
                    # chime, etc.) the InputStream keeps buffering audio. If we
                    # don't drain it all, OWW will process seconds of TTS/chime
                    # bleed and self-trigger a phantom wake-word HIT.
                    drained_frames = 0
                    try:
                        while True:
                            avail = int(getattr(stream, "read_available", 0) or 0)
                            if avail <= 0:
                                break
                            chunk = min(avail, input_blocksize * 4)
                            stream.read(chunk)
                            drained_frames += chunk
                    except Exception:
                        self.log.exception("WAKEWORD_ENGINE_OPENWAKEWORD_DRAIN_FAIL")
                    try:
                        drained_ms = int(drained_frames * 1000 / max(1, input_sr))
                    except Exception:
                        drained_ms = 0
                    self.log.info(
                        "WAKEWORD_ENGINE_OPENWAKEWORD_POST_SUPPRESS_DRAIN frames=%d approx_ms=%d",
                        drained_frames, drained_ms,
                    )
                    # Reset OWW state. NOTE: upstream Model.reset() only
                    # clears the prediction-history dict — it does NOT clear
                    # the preprocessor's raw_data_buffer, feature_buffer, or
                    # melspectrogram_buffer. Those retain ~1+ second of audio
                    # / features from before suppression. Without explicitly
                    # clearing them, OWW will re-score the previous wake-word
                    # features the moment the listener resumes, producing a
                    # phantom 0.999 HIT (the actual root cause of the
                    # post-interaction "follow-on chime" bug). Verified via
                    # direct inspection of the openwakeword.model source.
                    buffers_cleared = False
                    try:
                        model.reset()
                        pp = getattr(model, "preprocessor", None)
                        if pp is not None:
                            try:
                                pp.raw_data_buffer.clear()
                            except Exception:
                                pass
                            try:
                                if hasattr(pp, "feature_buffer") and pp.feature_buffer is not None:
                                    pp.feature_buffer = np.zeros_like(pp.feature_buffer)
                            except Exception:
                                pass
                            try:
                                if hasattr(pp, "melspectrogram_buffer") and pp.melspectrogram_buffer is not None:
                                    pp.melspectrogram_buffer = np.zeros_like(pp.melspectrogram_buffer)
                            except Exception:
                                pass
                            buffers_cleared = True
                        self.log.info(
                            "WAKEWORD_ENGINE_OPENWAKEWORD_POST_SUPPRESS_RESET buffers_cleared=%s",
                            buffers_cleared,
                        )
                    except Exception:
                        self.log.exception("WAKEWORD_ENGINE_OPENWAKEWORD_RESET_FAIL")
                    self.log.info("WAKEWORD_ENGINE_OPENWAKEWORD_LISTENING")
                    active_logged = True
                    last_active_log_ts = now
                elif (now - last_active_log_ts) >= 10.0:
                    self.log.info("WAKEWORD_ENGINE_OPENWAKEWORD_IDLE_TICK")
                    last_active_log_ts = now

                # ---- Read chunk from device ----
                try:
                    data, overflowed = stream.read(input_blocksize)
                except Exception:
                    self.log.exception("WAKEWORD_ENGINE_OPENWAKEWORD_READ_FAIL")
                    time.sleep(0.10)
                    continue

                pcm = data[:, 0] if getattr(data, "ndim", 1) > 1 else data
                if pcm.dtype != np.int16:
                    pcm = pcm.astype(np.int16, copy=False)

                # Diagnostic ring buffer (raw input_sr chunks).
                try:
                    hit_audio_chunks.append(pcm)
                except Exception:
                    pass

                # Pre-trigger ring (10 ms frames at input_sr).
                try:
                    n_full = len(pcm) // pretrigger_frame_samples
                    for _i in range(n_full):
                        seg = pcm[_i * pretrigger_frame_samples : (_i + 1) * pretrigger_frame_samples]
                        if seg.size == pretrigger_frame_samples:
                            pretrigger_buffer.append(seg.copy())
                except Exception:
                    pass

                # ---- Resample to 16 kHz for OWW ----
                pcm_for_oww = pcm
                if need_resample:
                    try:
                        from scipy.signal import resample_poly
                        pcm_f = pcm.astype(np.float32, copy=False)
                        pcm_16k = resample_poly(pcm_f, up=resample_up, down=resample_down)
                        pcm_for_oww = np.clip(np.rint(pcm_16k), -32768, 32767).astype(np.int16)
                    except Exception:
                        ratio = float(target_sr) / float(input_sr)
                        out_len = max(1, int(round(len(pcm) * ratio)))
                        x_old = np.linspace(0, 1, num=len(pcm), endpoint=False)
                        x_new = np.linspace(0, 1, num=out_len, endpoint=False)
                        pcm_for_oww = np.interp(x_new, x_old, pcm.astype(np.float32)).astype(np.int16)

                # ---- OWW predict ----
                try:
                    scores = model.predict(
                        pcm_for_oww,
                        threshold={selected_label: threshold} if selected_label else None,
                        debounce_time=debounce_sec,
                    )
                except TypeError:
                    try:
                        scores = model.predict(pcm_for_oww)
                    except Exception:
                        self.log.exception("WAKEWORD_ENGINE_OPENWAKEWORD_PROCESS_FAIL")
                        time.sleep(0.10)
                        continue
                except Exception:
                    self.log.exception("WAKEWORD_ENGINE_OPENWAKEWORD_PROCESS_FAIL")
                    time.sleep(0.10)
                    continue

                if not isinstance(scores, dict):
                    continue

                best_label = None
                best_score = 0.0
                for label, score in scores.items():
                    try:
                        score_f = float(score)
                    except Exception:
                        continue
                    if score_f > best_score:
                        best_score = score_f
                        best_label = str(label)

                # Near-miss: OWW scored something meaningfully above silence
                # but below threshold. Throttle to once per 500 ms to avoid
                # spam. Useful for diagnosing "I said the wake word and
                # nothing happened" cases — tells us whether OWW heard
                # almost-but-not-quite vs. nothing at all.
                if (
                    best_label
                    and best_score >= near_miss_min
                    and best_score < threshold
                ):
                    _now_nm = time.time()
                    if (_now_nm - last_near_miss_log_ts) >= 0.5:
                        last_near_miss_log_ts = _now_nm
                        if not selected_label or best_label == selected_label:
                            self.log.info(
                                "WAKEWORD_NEAR_MISS label=%r score=%.3f threshold=%.3f",
                                best_label, best_score, threshold,
                            )

                if best_label and best_score >= threshold:
                    if selected_label and best_label != selected_label:
                        continue

                    self.log.info(
                        "WAKEWORD_ENGINE_OPENWAKEWORD_HIT label=%r score=%.3f threshold=%.3f",
                        best_label, best_score, threshold,
                    )

                    # Diagnostic: dump audio in background thread.
                    try:
                        _snap = list(hit_audio_chunks)
                        _sr = int(input_sr)
                        _lbl = str(best_label)
                        _sc = float(best_score)
                        _pid = os.getpid()

                        def _dump(_snap, _sr, _lbl, _sc, _pid):
                            try:
                                import numpy as _dnp
                                import wave as _dw
                                if not _snap:
                                    return
                                _pcm = _dnp.concatenate(_snap).astype(_dnp.int16, copy=False)
                                _ts = time.strftime("%Y%m%d_%H%M%S")
                                _p = "/tmp/piphone_wake_hit_{}_score{:.3f}_pid{}.wav".format(_ts, _sc, _pid)
                                with _dw.open(_p, "wb") as w:
                                    w.setnchannels(1)
                                    w.setsampwidth(2)
                                    w.setframerate(_sr)
                                    w.writeframes(_pcm.tobytes())
                                logging.info("WAKEWORD_HIT_DUMP_WROTE path=%r sr=%s score=%.3f", _p, _sr, _sc)
                            except Exception:
                                pass

                        threading.Thread(target=_dump, args=(_snap, _sr, _lbl, _sc, _pid), daemon=True).start()
                    except Exception:
                        pass

                    try:
                        model.reset()
                    except Exception:
                        pass

                    # Build frame reader + pre-trigger snapshot for gpio_ptt capture.
                    vad_frame_samples = max(1, int(round(input_sr * 10 / 1000.0)))

                    def _read_vad_frame():
                        fd, _ = stream.read(vad_frame_samples)
                        return fd[:, 0] if getattr(fd, "ndim", 1) > 1 else fd

                    pretrigger_snapshot = list(pretrigger_buffer)
                    self._emit_detected(
                        frame_reader=_read_vad_frame,
                        sample_rate=input_sr,
                        frame_samples=vad_frame_samples,
                        wakeword_label=best_label,
                        wakeword_score=best_score,
                        pre_trigger_frames=pretrigger_snapshot,
                        pre_trigger_sample_rate=input_sr,
                        pre_trigger_frame_samples=pretrigger_frame_samples,
                    )
                    time.sleep(0.05)

        except Exception:
            self.log.exception("WAKEWORD_ENGINE_OPENWAKEWORD_RUNTIME_FAIL")
        finally:
            try:
                if stream is not None:
                    stream.stop()
                    stream.close()
            except Exception:
                pass

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

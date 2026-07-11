"""OpenWakeWord scoring loop and same-stream command handoff.

This engine owns a continuously drained PortAudio source. Device-rate frames
are retained in a short ring for pre-trigger audio and separately resampled for
OpenWakeWord scoring. After a confirmed detection, scoring pauses while the
application callback consumes command audio through an independent cursor on
that same source. This avoids reopening the microphone or losing speech spoken
immediately after the wakeword.

Suppression, debounce, and application busy state are supplied by
``WakewordListener``. Microphone selection and persistent mixer enforcement are
supplied by :mod:`audio_input_profile`.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict, deque


def _reset_openwakeword_state(model, frontend, np_module) -> None:
    """Clear temporal model/frontend state before scoring fresh room audio."""
    try:
        model.reset()
    except Exception:
        pass

    try:
        vad = getattr(model, "vad", None)
        if vad is not None:
            try:
                vad.reset_states()
            except Exception:
                pass
            try:
                vad.prediction_buffer.clear()
            except Exception:
                pass
    except Exception:
        pass

    try:
        preprocessor = getattr(model, "preprocessor", None)
        if preprocessor is not None:
            try:
                preprocessor.raw_data_buffer.clear()
            except Exception:
                pass
            try:
                if getattr(preprocessor, "feature_buffer", None) is not None:
                    preprocessor.feature_buffer = np_module.zeros_like(preprocessor.feature_buffer)
            except Exception:
                pass
            try:
                if getattr(preprocessor, "melspectrogram_buffer", None) is not None:
                    preprocessor.melspectrogram_buffer = np_module.zeros_like(
                        preprocessor.melspectrogram_buffer
                    )
            except Exception:
                pass
    except Exception:
        pass

    try:
        frontend.reset()
    except Exception:
        pass


def _dump_hit_audio(frames, sample_rate: int, score: float, label: str) -> None:
    try:
        import numpy as np
        import wave

        if not frames:
            return
        pcm = np.concatenate(frames).astype(np.int16, copy=False)
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        path = "/tmp/piphone_wake_hit_{}_score{:.3f}_pid{}.wav".format(
            timestamp, score, os.getpid()
        )
        with wave.open(path, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(int(sample_rate))
            wav_file.writeframes(pcm.tobytes())
        logging.info(
            "WAKEWORD_HIT_DUMP_WROTE path=%r sr=%s score=%.3f label=%r",
            path, sample_rate, score, label,
        )
    except Exception:
        logging.exception("WAKEWORD_HIT_DUMP_FAIL")


def run_openwakeword(listener) -> None:
    """Run OpenWakeWord until the listener stop event is set or capture fails."""
    try:
        import numpy as np
        import sounddevice as sd
        from openwakeword.model import Model

        from audio_input_profile import (
            CaptureSettingsGuardian,
            enforce_capture_settings,
            get_audio_input_profile,
            pick_sounddevice_input_index,
            profile_for_log,
        )
        from wakeword_audio_source import ContinuousAudioSource
        from wakeword_frontend import WakewordFrontend
    except Exception:
        listener.log.exception("WAKEWORD_ENGINE_OPENWAKEWORD_IMPORT_FAIL")
        return

    model = None
    source = None
    guardian = None
    watchdog_progress = {"ts": time.monotonic()}

    def _watchdog() -> None:
        stale_sec = 30.0
        while not listener._stop_event.wait(5.0):
            progress = watchdog_progress["ts"]
            if source is not None and source.last_frame_monotonic:
                progress = source.last_frame_monotonic
            age = time.monotonic() - progress
            if age > stale_sec:
                try:
                    listener.log.error(
                        "WAKEWORD_ENGINE_OPENWAKEWORD_WATCHDOG_KILL audio_age_sec=%.1f stale_threshold_sec=%.1f",
                        age, stale_sec,
                    )
                finally:
                    os._exit(1)

    threading.Thread(target=_watchdog, name="wakeword_watchdog", daemon=True).start()

    try:
        profile = get_audio_input_profile()
        listener.log.info("MIC_PROFILE_ACTIVE %r", profile_for_log(profile))
        if not enforce_capture_settings(profile, logger=listener.log, reason="wakeword_start"):
            listener.log.warning("MIC_PROFILE_START_VERIFY_FAILED profile=%r", profile.get("name"))
        guardian = CaptureSettingsGuardian(profile, logger=listener.log)
        guardian.start()

        threshold = listener._get_openwakeword_threshold()
        vad_threshold = listener._get_openwakeword_vad_threshold()
        debounce_sec = listener._get_openwakeword_debounce_sec()
        near_miss_min = listener._get_openwakeword_near_miss_min()
        model_paths = listener._get_openwakeword_model_paths()
        selected_label = str(listener.model or "").strip()

        activation_window = max(
            1, min(10, int(listener._pref("WAKEWORD_ACTIVATION_WINDOW_FRAMES", 3)))
        )
        deactivation_threshold = max(
            0.0, min(1.0, float(listener._pref("WAKEWORD_DEACTIVATION_THRESHOLD", 0.20)))
        )
        deactivation_frames = max(
            1, min(20, int(listener._pref("WAKEWORD_DEACTIVATION_FRAMES", 3)))
        )

        device_index = pick_sounddevice_input_index(sd, profile)
        sample_rate = int(profile.get("sample_rate") or 48000)
        channels = int(profile.get("channels") or 1)
        frame_ms = 10
        frame_samples = max(1, int(round(sample_rate * frame_ms / 1000.0)))
        target_sample_rate = 16000
        prediction_samples = 1280

        model_kwargs = {"vad_threshold": vad_threshold}
        if model_paths:
            model_kwargs["wakeword_model_paths"] = model_paths
        model = Model(**model_kwargs)
        listener._owned_model = model

        available = sorted(str(name) for name in getattr(model, "models", {}).keys())
        if selected_label and selected_label not in available:
            raise RuntimeError(
                f"Selected wakeword model {selected_label!r} is not loaded; available={available!r}"
            )

        frontend = WakewordFrontend(
            sample_rate,
            output_sample_rate=target_sample_rate,
            output_chunk_samples=prediction_samples,
            noise_suppression_level=int(profile.get("noise_suppression_level") or 0),
            auto_gain_dbfs=int(profile.get("auto_gain_dbfs") or 0),
            volume_multiplier=float(profile.get("volume_multiplier") or 1.0),
            logger=listener.log,
        )

        source = ContinuousAudioSource(
            sd,
            device=(device_index if device_index >= 0 else None),
            sample_rate=sample_rate,
            channels=channels,
            frame_ms=frame_ms,
            ring_ms=max(4000, listener._get_pretrigger_buffer_ms() + 1000),
            stream_latency=profile.get("stream_latency", "low"),
            logger=listener.log,
        )
        listener._owned_source = source
        source.start()
        enforce_capture_settings(
            profile,
            logger=listener.log,
            reason="wakeword_stream_open",
            force=True,
        )
        detection_cursor = source.create_cursor(live=True)

        listener.log.info(
            "WAKEWORD_ENGINE_OPENWAKEWORD_READY model=%r threshold=%.3f vad_threshold=%.3f "
            "debounce_floor_sec=%.2f sd_dev=%s input_sr=%s target_sr=%s frame_ms=%s latency=%r "
            "activation_window=%s deactivation_threshold=%.3f deactivation_frames=%s",
            selected_label, threshold, vad_threshold, debounce_sec, device_index,
            sample_rate, target_sample_rate, frame_ms,
            profile.get("stream_latency", "low"), activation_window,
            deactivation_threshold, deactivation_frames,
        )
        listener.log.info(
            "WAKEWORD_ENGINE_OPENWAKEWORD_MODELS available=%r selected=%r",
            available, selected_label,
        )
        listener.log.info("WAKEWORD_AUDIO_SOURCE_READY stats=%r", source.stats())

        pretrigger_frame_count = max(
            1, int(round(listener._get_pretrigger_buffer_ms() / float(frame_ms)))
        )
        score_history = defaultdict(lambda: deque(maxlen=activation_window))
        last_near_miss_log_ts = 0.0
        last_idle_log_ts = 0.0
        active_logged = False
        armed = False
        low_score_frames = 0
        last_hit_ts = -1e9

        while not listener._stop_event.is_set():
            listener._last_loop_ts = time.monotonic()
            watchdog_progress["ts"] = listener._last_loop_ts

            if listener._pending_flush:
                listener._pending_flush = False
                _reset_openwakeword_state(model, frontend, np)
                detection_cursor.seek_live()
                score_history.clear()
                armed = False
                low_score_frames = 0
                listener.log.info("WAKEWORD_ENGINE_OPENWAKEWORD_EXTERNAL_FLUSH done=True")

            if not listener.should_listen_fn():
                reason = listener.suppress_reason_fn() or "suppressed"
                if listener._should_log_suppression(reason):
                    listener.log.info("WAKEWORD_SUPPRESS reason=%s", reason)
                active_logged = False
                time.sleep(0.05)
                continue

            now = time.monotonic()
            if not active_logged:
                detection_cursor.seek_live()
                _reset_openwakeword_state(model, frontend, np)
                score_history.clear()
                armed = False
                low_score_frames = 0
                active_logged = True
                last_idle_log_ts = now
                listener.log.info("WAKEWORD_ENGINE_OPENWAKEWORD_LISTENING")
            elif now - last_idle_log_ts >= 10.0:
                last_idle_log_ts = now
                listener.log.info(
                    "WAKEWORD_ENGINE_OPENWAKEWORD_IDLE_TICK source=%r cursor_drops=%s armed=%s",
                    source.stats(), detection_cursor.dropped_frames, armed,
                )

            frame = detection_cursor.read_frame(timeout=0.25)
            if frame is None:
                continue
            block_end_sequence = detection_cursor.next_sequence - 1
            prediction_chunks = frontend.push(frame)
            if not prediction_chunks:
                continue

            interaction_started = False
            for prediction_pcm in prediction_chunks:
                # Barge-in can be more sensitive during local TTS without
                # weakening normal idle wakeword detection.
                threshold = listener._get_openwakeword_threshold()
                try:
                    scores = model.predict(prediction_pcm)
                except Exception:
                    listener.log.exception("WAKEWORD_ENGINE_OPENWAKEWORD_PROCESS_FAIL")
                    continue
                if not isinstance(scores, dict):
                    continue

                filtered_scores = {}
                for label, value in scores.items():
                    label = str(label)
                    if selected_label and label != selected_label:
                        continue
                    try:
                        filtered_scores[label] = float(value)
                    except (TypeError, ValueError):
                        continue
                if not filtered_scores:
                    continue

                for label, score in filtered_scores.items():
                    score_history[label].append(score)
                smoothed = {
                    label: max(history) if history else 0.0
                    for label, history in score_history.items()
                    if label in filtered_scores
                }

                if not armed:
                    if max(filtered_scores.values(), default=0.0) <= deactivation_threshold:
                        low_score_frames += 1
                    else:
                        low_score_frames = 0
                    if (
                        low_score_frames >= deactivation_frames
                        and now - last_hit_ts >= debounce_sec
                    ):
                        armed = True
                        low_score_frames = 0
                        score_history.clear()
                        listener.log.info("WAKEWORD_TRIGGER_ARMED")
                    continue

                best_label, best_score = max(smoothed.items(), key=lambda item: item[1])
                if near_miss_min <= best_score < threshold:
                    near_miss_now = time.monotonic()
                    if near_miss_now - last_near_miss_log_ts >= 0.5:
                        last_near_miss_log_ts = near_miss_now
                        listener.log.info(
                            "WAKEWORD_NEAR_MISS label=%r score=%.3f threshold=%.3f",
                            best_label, best_score, threshold,
                        )
                if best_score < threshold:
                    continue

                hit_now = time.monotonic()
                if hit_now - last_hit_ts < debounce_sec:
                    listener.log.info(
                        "WAKEWORD_DETECTED_IGNORED reason=debounce dt=%.3f floor=%.3f",
                        hit_now - last_hit_ts, debounce_sec,
                    )
                    continue

                last_hit_ts = hit_now
                armed = False
                low_score_frames = 0
                listener.log.info(
                    "WAKEWORD_ENGINE_OPENWAKEWORD_HIT label=%r score=%.3f threshold=%.3f",
                    best_label, best_score, threshold,
                )

                hit_frames = source.snapshot(
                    end_sequence=block_end_sequence,
                    frame_count=max(2, int(round(1500 / frame_ms))),
                )
                threading.Thread(
                    target=_dump_hit_audio,
                    args=(hit_frames, sample_rate, best_score, best_label),
                    name="wakeword_hit_dump",
                    daemon=True,
                ).start()

                pretrigger_snapshot = source.snapshot(
                    end_sequence=block_end_sequence,
                    frame_count=pretrigger_frame_count,
                )
                command_cursor = source.create_cursor(
                    next_sequence=int(block_end_sequence) + 1,
                    live=False,
                )

                listener._emit_detected(
                    frame_reader=command_cursor.read_frame,
                    sample_rate=sample_rate,
                    frame_samples=frame_samples,
                    wakeword_label=best_label,
                    wakeword_score=best_score,
                    pre_trigger_frames=pretrigger_snapshot,
                    pre_trigger_sample_rate=sample_rate,
                    pre_trigger_frame_samples=frame_samples,
                )

                _reset_openwakeword_state(model, frontend, np)
                detection_cursor.seek_live()
                score_history.clear()
                interaction_started = True
                break

            if interaction_started:
                continue

    except Exception:
        listener.log.exception("WAKEWORD_ENGINE_OPENWAKEWORD_RUNTIME_FAIL")
    finally:
        try:
            if source is not None:
                source.stop()
        except Exception:
            listener.log.exception("WAKEWORD_AUDIO_SOURCE_STOP_FAIL")
        try:
            if guardian is not None:
                guardian.stop()
        except Exception:
            listener.log.exception("MIC_PROFILE_GUARDIAN_STOP_FAIL")
        listener._owned_source = None
        listener._owned_model = None

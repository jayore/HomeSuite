from __future__ import annotations

import sys
import unittest
from unittest import mock
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _FakeStream:
    def __init__(self, **kwargs):
        self.callback = kwargs["callback"]
        self.started = False
        self.closed = False

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True


class _FakeSoundDevice:
    def __init__(self):
        self.stream = None

    def InputStream(self, **kwargs):
        self.stream = _FakeStream(**kwargs)
        return self.stream


class _SequenceVad:
    def __init__(self, decisions):
        self.decisions = list(decisions)

    def is_speech(self, _pcm, _sample_rate):
        if not self.decisions:
            raise AssertionError("VAD decision sequence exhausted")
        return self.decisions.pop(0)


class ContinuousAudioSourceTests(unittest.TestCase):
    def test_cursor_reports_ring_overrun_and_reads_in_sequence(self):
        from wakeword_audio_source import ContinuousAudioSource

        sounddevice = _FakeSoundDevice()
        source = ContinuousAudioSource(
            sounddevice,
            device=3,
            sample_rate=1000,
            frame_ms=10,
            ring_ms=200,
        )
        source.start()
        cursor = source.create_cursor(live=True)

        for value in range(25):
            frame = np.full((10, 1), value, dtype=np.int16)
            sounddevice.stream.callback(frame, 10, {}, None)

        first = cursor.read_frame(timeout=0.0)
        self.assertEqual(cursor.dropped_frames, 5)
        self.assertTrue(np.all(first == 5))
        self.assertEqual(
            [int(frame[0]) for frame in source.snapshot(frame_count=3)],
            [22, 23, 24],
        )
        source.stop()
        self.assertTrue(sounddevice.stream.closed)


class VadEndpointTests(unittest.TestCase):
    def test_default_policy_still_requires_consecutive_silence(self):
        from audio_capture import _VadUtteranceAccumulator

        vad = _SequenceVad(
            [True, True, True, False, False, False, True, False, False, False]
        )
        capture = _VadUtteranceAccumulator(
            vad_obj=vad,
            pre_roll_frames=2,
            silence_end_frames=4,
            min_speech_frames=3,
        )
        frame = np.zeros(160, dtype=np.int16)

        events = [capture.push(frame, 16000) for _ in range(10)]

        self.assertNotIn("endpoint", events)
        self.assertIsNone(capture.endpoint_window)

    def test_rolling_policy_tolerates_an_isolated_false_voice_frame(self):
        from audio_capture import _VadUtteranceAccumulator

        vad = _SequenceVad(
            [True, True, True, False, False, False, True,
             False, False, False, False, False]
        )
        capture = _VadUtteranceAccumulator(
            vad_obj=vad,
            pre_roll_frames=2,
            silence_end_frames=4,
            min_speech_frames=3,
            endpoint_window_frames=9,
            endpoint_min_silence_ratio=0.80,
            endpoint_trailing_silence_frames=3,
        )
        frame = np.zeros(160, dtype=np.int16)

        events = [capture.push(frame, 16000) for _ in range(12)]

        self.assertEqual(events[-1], "endpoint")

    def test_audio_frame_guard_primes_without_delaying_capture(self):
        import audio_capture

        frames = [np.full(160, value, dtype=np.int16) for value in range(1, 10)]
        vad = _SequenceVad([True, True, False, False, False, False])

        def read_frame():
            return frames.pop(0)

        with mock.patch.object(audio_capture, "vad", vad):
            result = audio_capture._capture_utterance_from_frame_source(
                frame_reader=read_frame,
                sample_rate=16000,
                continue_recording_fn=lambda: bool(frames),
                cancelled_fn=lambda: False,
                pre_roll_frames=5,
                silence_end_frames=99,
                min_speech_frames=2,
                prime_only_frames=3,
                endpoint_window_frames=4,
                endpoint_min_silence_ratio=0.75,
                endpoint_trailing_silence_frames=2,
                sleep_per_frame_sec=0.0,
            )

        self.assertIsNotNone(result)
        self.assertEqual(result["speech_frames"], 2)
        self.assertEqual(int(result["audio_data"][0]), 1)


class AudioProfileTests(unittest.TestCase):
    def test_device_name_match_is_stable_across_index_changes(self):
        from audio_input_profile import pick_sounddevice_input_index

        class Devices:
            @staticmethod
            def query_devices(index=None):
                devices = [
                    {"name": "Built-in", "max_input_channels": 0},
                    {"name": "Other USB Mic", "max_input_channels": 1},
                    {"name": "MOVO X1 MINI: USB Audio", "max_input_channels": 1},
                ]
                return devices[index] if index is not None else devices

        index = pick_sounddevice_input_index(
            Devices,
            {
                "device_index": None,
                "device_match": "MOVO X1 MINI",
                "strict_device_match": True,
            },
        )
        self.assertEqual(index, 2)

    def test_strict_missing_device_fails_closed(self):
        from audio_input_profile import pick_sounddevice_input_index

        class Devices:
            @staticmethod
            def query_devices(index=None):
                devices = [{"name": "Built-in", "max_input_channels": 1}]
                return devices[index] if index is not None else devices

        with self.assertRaises(RuntimeError):
            pick_sounddevice_input_index(
                Devices,
                {
                    "device_index": None,
                    "device_match": "far field array",
                    "strict_device_match": True,
                },
            )

    def test_named_profile_ignores_legacy_device_hint(self):
        import app_config
        from audio_input_profile import get_audio_input_profile

        configured = {"name": "named_array", "device_match": "New Array"}
        with mock.patch.object(app_config, "AUDIO_INPUT_PROFILE", configured):
            with mock.patch.dict(
                "os.environ",
                {"PIPHONE_SD_INPUT_MATCH": "obsolete microphone"},
                clear=False,
            ):
                profile = get_audio_input_profile()
        self.assertEqual(profile["name"], "named_array")
        self.assertEqual(profile["device_match"], "New Array")

    def test_named_profile_accepts_explicit_mic_override(self):
        import app_config
        from audio_input_profile import get_audio_input_profile

        configured = {"name": "named_array", "device_match": "New Array"}
        with mock.patch.object(app_config, "AUDIO_INPUT_PROFILE", configured):
            with mock.patch.dict(
                "os.environ",
                {"PIPHONE_MIC_DEVICE_MATCH": "future far field"},
                clear=False,
            ):
                profile = get_audio_input_profile()
        self.assertEqual(profile["device_match"], "future far field")


class WakewordFrontendTests(unittest.TestCase):
    def test_streaming_resampler_emits_openwakeword_sized_chunks(self):
        from wakeword_frontend import WakewordFrontend

        frontend = WakewordFrontend(48000, noise_suppression_level=0)
        chunks = []
        for _ in range(20):
            chunks.extend(frontend.push(np.zeros(480, dtype=np.int16)))
        self.assertGreaterEqual(len(chunks), 1)
        self.assertTrue(all(chunk.shape == (1280,) for chunk in chunks))

    def test_command_cleaner_preserves_sample_count(self):
        from wakeword_frontend import clean_command_audio_16k

        pcm = np.arange(997, dtype=np.int16)
        cleaned = clean_command_audio_16k(pcm, noise_suppression_level=2)
        self.assertEqual(cleaned.shape, pcm.shape)
        self.assertEqual(cleaned.dtype, np.int16)


if __name__ == "__main__":
    unittest.main()

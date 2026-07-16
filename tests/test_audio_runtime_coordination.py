from __future__ import annotations

import os
import unittest
from unittest import mock


class _Listener:
    def __init__(self) -> None:
        self.stop_calls = 0

    def stop(self) -> None:
        self.stop_calls += 1


class AudioRuntimeCoordinationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.dict(os.environ, {"PIPHONE_NO_RUNTIME_INIT": "1"}):
            import main

        cls.main = main

    def setUp(self):
        self.original_listener = self.main._WAKEWORD_LISTENER
        self.original_lease = self.main._AUDIO_CALIBRATION_LEASE
        self.original_timer = self.main._AUDIO_CALIBRATION_TIMER
        self.main._WAKEWORD_LISTENER = None
        self.main._AUDIO_CALIBRATION_LEASE = None
        self.main._AUDIO_CALIBRATION_TIMER = None

    def tearDown(self):
        timer = self.main._AUDIO_CALIBRATION_TIMER
        if timer is not None:
            timer.cancel()
        self.main._WAKEWORD_LISTENER = self.original_listener
        self.main._AUDIO_CALIBRATION_LEASE = self.original_lease
        self.main._AUDIO_CALIBRATION_TIMER = self.original_timer

    def test_release_restarts_listener_paused_by_matching_lease(self):
        listener = _Listener()
        self.main._WAKEWORD_LISTENER = listener
        with (
            mock.patch.object(self.main, "_audio_calibration_busy_reason", return_value=""),
            mock.patch.object(self.main, "_schedule_audio_calibration_expiry"),
            mock.patch.object(self.main, "_wakeword_enabled", return_value=True),
            mock.patch.object(self.main, "_ptt_enabled", return_value=False),
            mock.patch.object(self.main, "_start_wakeword_listener_if_enabled", return_value=True) as restart,
        ):
            lease = self.main.acquire_audio_calibration_lease(lease_seconds=20)
            result = self.main.release_audio_calibration_lease(lease["token"])

        self.assertEqual(listener.stop_calls, 1)
        restart.assert_called_once_with()
        self.assertTrue(result["wakeword_restarted"])
        self.assertIsNone(self.main._AUDIO_CALIBRATION_LEASE)

    def test_acquire_failure_recovers_stopped_listener(self):
        listener = _Listener()
        self.main._WAKEWORD_LISTENER = listener
        with (
            mock.patch.object(self.main, "_audio_calibration_busy_reason", return_value=""),
            mock.patch.object(
                self.main,
                "_schedule_audio_calibration_expiry",
                side_effect=RuntimeError("timer failed"),
            ),
            mock.patch.object(self.main, "_start_wakeword_listener_if_enabled", return_value=True) as restart,
        ):
            with self.assertRaisesRegex(RuntimeError, "timer failed"):
                self.main.acquire_audio_calibration_lease(lease_seconds=20)

        self.assertEqual(listener.stop_calls, 1)
        restart.assert_called_once_with()
        self.assertIsNone(self.main._AUDIO_CALIBRATION_LEASE)

    def test_ptt_only_calibration_uses_runtime_high_latency(self):
        with (
            mock.patch("audio_input_profile.get_audio_input_profile", return_value={"stream_latency": "low"}),
            mock.patch.object(self.main, "_ptt_enabled", return_value=True),
            mock.patch.object(self.main, "_wakeword_enabled", return_value=False),
        ):
            profile = self.main._audio_calibration_capture_profile()

        self.assertEqual(profile["stream_latency"], "high")

    def test_wakeword_calibration_preserves_profile_latency(self):
        with (
            mock.patch("audio_input_profile.get_audio_input_profile", return_value={"stream_latency": "low"}),
            mock.patch.object(self.main, "_ptt_enabled", return_value=False),
            mock.patch.object(self.main, "_wakeword_enabled", return_value=True),
        ):
            profile = self.main._audio_calibration_capture_profile()

        self.assertEqual(profile["stream_latency"], "low")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from unittest import mock


class VoiceTimingTests(unittest.TestCase):
    def test_wake_and_capture_timing_uses_epoch_events_and_monotonic_durations(self):
        import voice_timing

        with (
            mock.patch.object(voice_timing, "unix_time_ms", return_value=1_700_000_000_500),
            mock.patch.object(voice_timing.time, "monotonic_ns", return_value=9_000_000_000),
            mock.patch.object(voice_timing.uuid, "uuid4") as uuid4,
        ):
            uuid4.return_value.hex = "abcdef1234567890"
            timing = voice_timing.new_voice_timing(
                trigger="wakeword",
                source_id="piphone1",
                wakeword_label="hal_v2",
                wakeword_score=0.91,
                wake_detected_at_unix_ms=1_700_000_000_200,
                wake_detected_monotonic_ns=8_200_000_000,
                wake_audio_end_at_unix_ms=1_700_000_000_150,
                wake_audio_end_monotonic_ns=8_150_000_000,
            )

        voice_timing.apply_capture_timing(
            timing,
            {
                "capture_started_at_unix_ms": 1_700_000_000_160,
                "capture_ended_at_unix_ms": 1_700_000_001_500,
                "speech_started_at_unix_ms": 1_700_000_000_300,
                "speech_ended_at_unix_ms": 1_700_000_001_200,
                "capture_started_monotonic_ns": 8_160_000_000,
                "capture_ended_monotonic_ns": 9_500_000_000,
                "speech_started_monotonic_ns": 8_300_000_000,
                "speech_ended_monotonic_ns": 9_200_000_000,
            },
        )

        self.assertEqual(
            timing["utterance_id"],
            "piphone1-1700000000150-abcdef1234",
        )
        self.assertEqual(timing["wakeword"]["decision_latency_ms"], 50.0)
        self.assertEqual(timing["wake_to_speech_ms"], 150.0)
        self.assertEqual(timing["speech"]["duration_ms"], 900.0)

    def test_transport_strips_local_monotonic_values_and_brain_adds_receive_time(self):
        import voice_timing

        timing = {
            "schema_version": 1,
            "utterance_id": "piphone1-1-abc",
            "clock": {"ntp_synchronized": True},
            "_local": {"speech_started_monotonic_ns": 123},
        }
        with mock.patch.object(voice_timing, "unix_time_ms", return_value=1_010):
            sent = voice_timing.timing_for_transport(timing)
        received = voice_timing.timing_with_brain_receive(
            sent,
            received_at_ms=1_025,
        )

        self.assertNotIn("_local", sent)
        self.assertEqual(sent["satellite_sent_at_ms"], 1_010)
        self.assertEqual(received["brain_received_at_ms"], 1_025)
        self.assertEqual(received["apparent_transit_ms"], 15)


if __name__ == "__main__":
    unittest.main()

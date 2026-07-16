from __future__ import annotations

import unittest
import time

import numpy as np

from audio_calibration import (
    audio_metrics,
    calibration_adjustments,
    calibration_recommendations,
    calibration_status,
    capture_audio_segment,
)


class _FakeInputStream:
    def __init__(self, owner, **kwargs):
        self.owner = owner
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, samples):
        time.sleep(0.1)
        self.owner.reads += 1
        return np.full((samples, 1), 1000, dtype=np.int16), self.owner.reads == 1


class _FakeSoundDevice:
    def __init__(self):
        self.reads = 0

    def query_devices(self, index=None, kind=None):
        devices = [
            {
                "name": "Test Array: Audio (hw:2,0)",
                "max_input_channels": 1,
                "max_output_channels": 0,
                "default_samplerate": 16000,
            }
        ]
        if index is not None:
            return devices[index]
        if kind == "input":
            return devices[0]
        return devices

    def InputStream(self, **kwargs):
        return _FakeInputStream(self, **kwargs)


class AudioCalibrationTests(unittest.TestCase):
    def test_metrics_report_level_and_clipping(self):
        samples = np.array([0, 1000, -1000, 32767, -32768], dtype=np.int16)
        metrics = audio_metrics(samples, 5, block_ms=200)
        self.assertEqual(metrics["duration"], 1.0)
        self.assertEqual(metrics["peak"], 32768)
        self.assertEqual(metrics["clip_count"], 2)
        self.assertAlmostEqual(metrics["peak_dbfs"], 0.0, places=3)

    def test_recommendations_include_overflow_and_clipping(self):
        speech = {
            "peak_dbfs": 0.0,
            "p90_dbfs": -5.0,
            "clip_pct": 0.1,
        }
        lines = calibration_recommendations(None, speech, overflow_count=2)
        self.assertTrue(any("dropped 2" in line for line in lines))
        self.assertTrue(any("Lower capture gain" in line for line in lines))
        self.assertEqual(calibration_status(None, speech, overflow_count=0), "poor")

    def test_healthy_levels_are_classified_healthy(self):
        noise = {"p20_dbfs": -50.0}
        speech = {"peak_dbfs": -7.0, "p90_dbfs": -18.0, "clip_pct": 0.0}
        self.assertEqual(calibration_status(noise, speech), "healthy")

    def test_clipping_points_to_managed_hardware_gain(self):
        adjustments = calibration_adjustments(
            None,
            {"peak_dbfs": 0.0, "p90_dbfs": -5.0, "clip_pct": 0.1},
            {
                "alsa_card": "Device",
                "mixer_control": "Mic",
                "mixer_value": 7,
                "stream_latency": "low",
            },
            wakeword_enabled=True,
        )

        gain = next(item for item in adjustments if item["code"] == "lower_hardware_gain")
        self.assertEqual(gain["setting"], "mixer_value")
        self.assertEqual(gain["current_value"], 7)
        self.assertEqual(gain["suggested_value"], 6)
        self.assertEqual(gain["title"], "Speech input is too loud")
        self.assertIn("0.0 dBFS", gain["detail"])
        self.assertIn("-12 to -3 dBFS", gain["detail"])

    def test_quiet_input_includes_diagnosis_and_next_gain_value(self):
        adjustments = calibration_adjustments(
            None,
            {"peak_dbfs": -22.0, "p90_dbfs": -34.0, "clip_pct": 0.0},
            {
                "alsa_card": "Device",
                "mixer_control": "Mic",
                "mixer_value": 7,
                "stream_latency": "high",
            },
            ptt_enabled=True,
        )

        gain = next(item for item in adjustments if item["code"] == "raise_hardware_gain")
        self.assertEqual(gain["title"], "Speech input is too quiet")
        self.assertEqual(gain["suggested_value"], 8)
        self.assertIn("-22.0 dBFS", gain["detail"])

    def test_overflow_recommends_high_latency_for_wakeword(self):
        adjustments = calibration_adjustments(
            None,
            {"peak_dbfs": -7.0, "p90_dbfs": -18.0, "clip_pct": 0.0},
            {"stream_latency": "low"},
            overflow_count=2,
            wakeword_enabled=True,
        )

        latency = next(item for item in adjustments if item["code"] == "use_high_latency")
        self.assertEqual(latency["setting"], "stream_latency")
        self.assertEqual(latency["suggested_value"], "high")

    def test_healthy_result_recommends_no_setting_change(self):
        adjustments = calibration_adjustments(
            {"p20_dbfs": -50.0},
            {"peak_dbfs": -7.0, "p90_dbfs": -18.0, "clip_pct": 0.0},
            {"mixer_value": 7, "alsa_card": "Device", "mixer_control": "Mic"},
        )

        self.assertEqual(adjustments[0]["code"], "keep_capture_gain")
        self.assertIsNone(adjustments[0]["setting"])

    def test_capture_uses_profile_and_counts_overflows(self):
        sd = _FakeSoundDevice()
        samples, overflows, device = capture_audio_segment(
            {
                "device_match": "Test Array",
                "device_index": None,
                "sample_rate": 16000,
                "channels": 1,
                "stream_latency": "high",
                "strict_device_match": True,
            },
            seconds=0.25,
            block_ms=100,
            sd_module=sd,
        )
        self.assertEqual(samples.size, 4000)
        self.assertEqual(overflows, 1)
        self.assertEqual(device["index"], 0)
        self.assertIn("Test Array", device["name"])


if __name__ == "__main__":
    unittest.main()

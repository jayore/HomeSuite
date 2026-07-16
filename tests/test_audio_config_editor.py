from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from audio_config_editor import AudioConfigEditor
from config_editor import ConfigEditError


PROFILE = {
    "name": "test_mic",
    "device_match": "Test USB Mic",
    "device_index": None,
    "sample_rate": 48000,
    "channels": 1,
    "stream_latency": "high",
    "strict_device_match": True,
    "alsa_card": "Device",
    "mixer_control": "Mic",
    "mixer_value": 7,
    "verify_interval_sec": 10,
    "noise_suppression_level": 2,
    "auto_gain_dbfs": 0,
    "volume_multiplier": 1.0,
    "command_noise_suppression_level": 0,
    "command_auto_gain_dbfs": 0,
    "command_volume_multiplier": 1.0,
    "ptt_volume_multiplier": 1.2,
    "aec_mode": "none",
}


class AudioConfigEditorTests(unittest.TestCase):
    def build_editor(self, root: Path) -> AudioConfigEditor:
        (root / "local_prefs.py").write_text(
            "KEEP_ME = 'yes'\n"
            f"AUDIO_INPUT_PROFILE = {PROFILE!r}\n",
            encoding="utf-8",
        )
        app_config = SimpleNamespace(
            AUDIO_INPUT_PROFILE=PROFILE,
            HOMESUITE_ALSA_DEVICE=None,
            ASSISTANT_AUDIO_OUTPUT_MODE="local",
            WAKEWORD_ENABLED=True,
            PTT_ENABLED=False,
        )
        return AudioConfigEditor(root=root, app_config=app_config, backup_root=root / "backups")

    @mock.patch("audio_config_editor.discover_audio_hardware")
    def test_public_state_reports_effective_service_output(self, discover):
        discover.return_value = {"available": True, "inputs": [], "outputs": []}
        with tempfile.TemporaryDirectory() as temp:
            editor = self.build_editor(Path(temp))
            with mock.patch.dict("os.environ", {"HOMESUITE_ALSA_DEVICE": "plughw:CARD=Headphones,DEV=0"}, clear=False):
                state = editor.public_state()
        self.assertEqual(state["profile"]["name"], "test_mic")
        self.assertEqual(state["output_effective"], "plughw:CARD=Headphones,DEV=0")
        self.assertEqual(state["output_source"], "service_environment")

    @mock.patch("audio_config_editor.discover_audio_hardware", return_value={"available": True, "inputs": [], "outputs": []})
    def test_partial_profile_inherits_supported_defaults(self, _discover):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            partial = {
                "name": "ptt_usb",
                "device_match": "USB PnP Sound Device",
                "sample_rate": 48000,
                "ptt_volume_multiplier": 1.2,
            }
            (root / "local_prefs.py").write_text(
                f"AUDIO_INPUT_PROFILE = {partial!r}\n",
                encoding="utf-8",
            )
            editor = AudioConfigEditor(
                root=root,
                app_config=SimpleNamespace(
                    AUDIO_INPUT_PROFILE=partial,
                    HOMESUITE_ALSA_DEVICE=None,
                    ASSISTANT_AUDIO_OUTPUT_MODE="local",
                    WAKEWORD_ENABLED=False,
                    PTT_ENABLED=True,
                ),
                backup_root=root / "backups",
            )
            state = editor.public_state()

        self.assertEqual(state["profile"]["channels"], 1)
        self.assertEqual(state["profile"]["stream_latency"], "low")
        self.assertEqual(state["profile"]["ptt_volume_multiplier"], 1.2)

    @mock.patch("audio_config_editor.discover_audio_hardware", return_value={"available": True, "inputs": [], "outputs": []})
    def test_apply_is_atomic_and_preserves_unrelated_settings(self, _discover):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            editor = self.build_editor(root)
            state = editor.public_state()
            proposed = dict(state["profile"], stream_latency="low", mixer_value=8)
            preview = editor.preview(proposed, "dmix:CARD=Device,DEV=0")
            self.assertEqual(preview["change_count"], 2)
            result = editor.apply(proposed, "dmix:CARD=Device,DEV=0", preview["revision"])
            self.assertTrue(result["applied"])
            source = (root / "local_prefs.py").read_text(encoding="utf-8")
            self.assertIn("KEEP_ME = 'yes'", source)
            self.assertIn("HOMESUITE_ALSA_DEVICE", source)
            self.assertIn("'mixer_value': 8", source)
            self.assertTrue(Path(result["backup_dir"]).is_dir())

    @mock.patch("audio_config_editor.discover_audio_hardware", return_value={"available": True, "inputs": [], "outputs": []})
    def test_stale_revision_is_rejected(self, _discover):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            editor = self.build_editor(root)
            state = editor.public_state()
            proposed = dict(state["profile"], mixer_value=8)
            with self.assertRaisesRegex(ConfigEditError, "changed after this review"):
                editor.apply(proposed, None, "stale")

    def test_invalid_gain_without_mixer_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            editor = self.build_editor(Path(temp))
            state = editor.public_state()
            proposed = dict(state["profile"], alsa_card=None, mixer_control=None, mixer_value=5)
            with self.assertRaisesRegex(ConfigEditError, "needs both"):
                editor.preview(proposed, None)


if __name__ == "__main__":
    unittest.main()

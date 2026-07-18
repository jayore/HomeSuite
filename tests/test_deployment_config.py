from __future__ import annotations

import json
import os
import runpy
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class DeploymentConfigTests(unittest.TestCase):
    def test_ptt_input_has_portable_defaults_and_legacy_aliases(self):
        with mock.patch.dict(
            sys.modules,
            {
                "deployment_config": ModuleType("deployment_config"),
                "local_prefs": ModuleType("local_prefs"),
            },
        ):
            values = runpy.run_path(str(ROOT / "app_config.py"))

        self.assertEqual(values["PTT_GPIO_PIN"], 11)
        self.assertEqual(values["PTT_LISTEN_LEVEL"], "low")
        self.assertEqual(values["PTT_END_BEHAVIOR"], "cancel")
        self.assertEqual(values["HANDSET_GPIO_PIN"], 11)
        self.assertFalse(values["PTT_ENABLED"])
        self.assertEqual(values["HANDSET_PRESENT"], values["PTT_ENABLED"])
        self.assertEqual(
            values["WAKEWORD_ONLY_ONHOOK"],
            values["WAKEWORD_SUPPRESS_WHILE_PTT"],
        )

    def test_legacy_handset_pin_override_feeds_the_canonical_ptt_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "local_prefs.py").write_text(
                "PTT_ENABLED = True\nHANDSET_GPIO_PIN = 17\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = str(ROOT)
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import json, app_config; "
                        "print(json.dumps({"
                        "'pin': app_config.PTT_GPIO_PIN, "
                        "'legacy_pin': app_config.HANDSET_GPIO_PIN, "
                        "'enabled': app_config.PTT_ENABLED}))"
                    ),
                ],
                cwd=tmp,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            payload = json.loads(result.stdout.strip())

        self.assertEqual(payload, {"pin": 17, "legacy_pin": 17, "enabled": True})

    def test_legacy_handset_role_enables_canonical_ptt_when_ptt_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "local_prefs.py").write_text(
                "HANDSET_PRESENT = True\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = str(ROOT)
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import json, app_config; "
                        "print(json.dumps({"
                        "'ptt': app_config.PTT_ENABLED, "
                        "'handset': app_config.HANDSET_PRESENT}))"
                    ),
                ],
                cwd=tmp,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            payload = json.loads(result.stdout.strip())

        self.assertEqual(payload, {"ptt": True, "handset": True})

    def test_public_example_neutralizes_home_specific_catalogs(self):
        values = runpy.run_path(str(ROOT / "deployment_config.example.py"))

        for key in (
            "PINNED_SPOTIFY_PLAYLISTS",
            "PINNED_RADIO_STATIONS",
            "PHONETIC_DEVICE_REPAIRS",
            "HA_TRIGGER_ALIASES",
            "HA_DEVICE_ALIASES",
            "YOUTUBE_CHANNELS",
            "TTS_PRONUNCIATION_OVERRIDES",
            "HOMELAB_SERVICES",
        ):
            self.assertEqual(values[key], {}, key)
        self.assertEqual(values["ASSISTANT_BULK_EXCLUDED_ENTITY_IDS"], [])
        self.assertIn(
            "light.*scene_trigger*",
            values["ASSISTANT_BULK_EXCLUDED_ENTITY_PATTERNS"],
        )
        self.assertEqual(values["ASSISTANT_PROFILE"]["preferred_name"], "")
        self.assertEqual(values["ASSISTANT_PROFILE"]["notes"], [])
        self.assertIsNone(values["HOME_LOCATION"]["city"])
        self.assertIsNone(values["HOME_LOCATION"]["region"])
        self.assertIsNone(values["HOME_LOCATION"]["country"])

    def test_public_local_preferences_disable_device_local_youtube_refresh(self):
        values = runpy.run_path(str(ROOT / "local_prefs.example.py"))

        self.assertFalse(values["YOUTUBE_REEL_REFRESH_ENABLED"])

    def test_error_inside_deployment_config_is_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "deployment_config.py").write_text(
                "import homesuite_dependency_that_does_not_exist\n",
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = str(ROOT)
            result = subprocess.run(
                [sys.executable, "-c", "import app_config"],
                cwd=tmp,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("homesuite_dependency_that_does_not_exist", result.stderr)

    def test_shared_override_loads_before_derived_views(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "deployment_config.py").write_text(
                textwrap.dedent(
                    """
                    DEFAULT_ROOM = "lab"
                    ROOMS = {
                        "lab": {
                            "label": "Lab",
                            "aliases": ["workshop"],
                            "defaults": {
                                "audio_output": "media_player.lab",
                                "tv": "media_player.lab_tv",
                                "tv_remote": "remote.lab_tv",
                            },
                            "audio_aliases": {},
                        },
                    }
                    """
                ),
                encoding="utf-8",
            )
            env = dict(os.environ)
            env["PYTHONPATH"] = str(ROOT)
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import json, app_config; "
                        "print(json.dumps({"
                        "'loaded': app_config.DEPLOYMENT_CONFIG_LOADED, "
                        "'room': app_config.DEFAULT_ROOM, "
                        "'sonos': app_config.SONOS_PLAYERS, "
                        "'tv': app_config.APPLE_TV_ENTITY}))"
                    ),
                ],
                cwd=tmp,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            payload = json.loads(result.stdout.strip())

        self.assertTrue(payload["loaded"])
        self.assertEqual(payload["room"], "lab")
        self.assertEqual(payload["sonos"]["lab"], "media_player.lab")
        self.assertEqual(payload["tv"], "media_player.lab_tv")


if __name__ == "__main__":
    unittest.main()

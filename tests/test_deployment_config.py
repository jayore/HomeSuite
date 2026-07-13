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


ROOT = Path(__file__).resolve().parents[1]


class DeploymentConfigTests(unittest.TestCase):
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

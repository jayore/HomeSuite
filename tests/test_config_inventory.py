from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

from config_inventory import build_config_inventory


class ConfigInventoryTests(unittest.TestCase):
    def build_root(self, root: Path) -> None:
        (root / "app_config.py").write_text(
            'PTT_ENABLED = False\n'
            'AUDIO_INPUT_PROFILE = {}\n'
            'CALENDARS = {}\n',
            encoding="utf-8",
        )
        (root / "local_prefs.example.py").write_text(
            '# PTT_ENABLED = False\n'
            '# AUDIO_INPUT_PROFILE = {}\n',
            encoding="utf-8",
        )
        (root / "deployment_config.example.py").write_text(
            'CALENDARS = {}\n',
            encoding="utf-8",
        )
        (root / "private_config.example.py").write_text(
            'HOMESUITE_HTTP_API_KEY = ""\n'
            'PIPHONE_HTTP_API_KEY = HOMESUITE_HTTP_API_KEY\n',
            encoding="utf-8",
        )
        (root / "local_prefs.py").write_text(
            'PTT_ENABLED = True\n'
            'AUDIO_INPUT_PROFILE = {"sample_rate": 16000}\n'
            'HANDSET_PRESENT = True\n'
            'UNUSED_OLD_SETTING = "value"\n',
            encoding="utf-8",
        )
        (root / "deployment_config.py").write_text(
            'CALENDARS = {"personal": {"entity_id": "calendar.personal"}}\n',
            encoding="utf-8",
        )
        (root / "private_config.py").write_text(
            'HOMESUITE_HTTP_API_KEY = ""\n'
            'PIPHONE_HTTP_API_KEY = "shared-secret"\n',
            encoding="utf-8",
        )

    def test_inventory_classifies_active_settings_and_redacts_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.build_root(root)
            inventory = build_config_inventory(
                root=root,
                app_config=SimpleNamespace(
                    PTT_ENABLED=True,
                    AUDIO_INPUT_PROFILE={"sample_rate": 16000},
                    CALENDARS={"personal": {"entity_id": "calendar.personal"}},
                    HANDSET_PRESENT=True,
                    UNUSED_OLD_SETTING="value",
                ),
                private_config=SimpleNamespace(
                    HOMESUITE_HTTP_API_KEY="",
                    PIPHONE_HTTP_API_KEY="shared-secret",
                ),
            )

        rows = {(row["scope"], row["key"]): row for row in inventory["rows"]}
        self.assertEqual(rows[("device", "PTT_ENABLED")]["classification"], "guided")
        self.assertEqual(
            rows[("device", "AUDIO_INPUT_PROFILE")]["classification"],
            "guided",
        )
        self.assertEqual(
            rows[("deployment", "CALENDARS")]["classification"],
            "advanced",
        )
        self.assertEqual(
            rows[("device", "HANDSET_PRESENT")]["replacement"],
            "PTT_ENABLED",
        )
        self.assertEqual(
            rows[("device", "UNUSED_OLD_SETTING")]["classification"],
            "unknown",
        )
        self.assertEqual(
            rows[("credentials", "PIPHONE_HTTP_API_KEY")]["value_summary"],
            "Configured",
        )
        self.assertNotIn("shared-secret", repr(inventory))
        self.assertEqual(inventory["summary"]["deprecated_active"], 2)
        self.assertEqual(inventory["summary"]["unknown_active"], 1)
        self.assertEqual(inventory["summary"]["documented_available"], 5)
        self.assertEqual(inventory["summary"]["file_managed_available"], 2)

    def test_deployment_assignment_reports_when_device_override_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.build_root(root)
            (root / "app_config.py").write_text(
                (root / "app_config.py").read_text(encoding="utf-8")
                + 'DEFAULT_ROOM = "living_room"\n',
                encoding="utf-8",
            )
            (root / "local_prefs.example.py").write_text(
                (root / "local_prefs.example.py").read_text(encoding="utf-8")
                + '# DEFAULT_ROOM = "living_room"\n',
                encoding="utf-8",
            )
            (root / "deployment_config.example.py").write_text(
                (root / "deployment_config.example.py").read_text(encoding="utf-8")
                + 'DEFAULT_ROOM = "living_room"\n',
                encoding="utf-8",
            )
            (root / "local_prefs.py").write_text(
                (root / "local_prefs.py").read_text(encoding="utf-8")
                + 'DEFAULT_ROOM = "office"\n',
                encoding="utf-8",
            )
            (root / "deployment_config.py").write_text(
                (root / "deployment_config.py").read_text(encoding="utf-8")
                + 'DEFAULT_ROOM = "living_room"\n',
                encoding="utf-8",
            )
            inventory = build_config_inventory(
                root=root,
                app_config=SimpleNamespace(DEFAULT_ROOM="office"),
                private_config=SimpleNamespace(PIPHONE_HTTP_API_KEY="shared-secret"),
            )

        deployment = next(
            row
            for row in inventory["rows"]
            if row["scope"] == "deployment" and row["key"] == "DEFAULT_ROOM"
        )
        self.assertFalse(deployment["effective"])
        self.assertEqual(deployment["value_summary"], "Overridden on this device")

    def test_removed_settings_have_explicit_deprecation_guidance(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.build_root(root)
            (root / "local_prefs.py").write_text(
                (root / "local_prefs.py").read_text(encoding="utf-8")
                + "WAKEWORD_ALLOW_ONHOOK_TTS = True\n",
                encoding="utf-8",
            )
            (root / "private_config.py").write_text(
                (root / "private_config.py").read_text(encoding="utf-8")
                + 'TELEGRAM_BOT_ID = "12345"\n',
                encoding="utf-8",
            )
            inventory = build_config_inventory(
                root=root,
                app_config=SimpleNamespace(WAKEWORD_ALLOW_ONHOOK_TTS=True),
                private_config=SimpleNamespace(
                    PIPHONE_HTTP_API_KEY="shared-secret",
                    TELEGRAM_BOT_ID="12345",
                ),
            )

        rows = {row["key"]: row for row in inventory["rows"]}
        for key in ("WAKEWORD_ALLOW_ONHOOK_TTS", "TELEGRAM_BOT_ID"):
            self.assertEqual(rows[key]["classification"], "deprecated")
            self.assertIsNone(rows[key]["replacement"])
            self.assertTrue(rows[key]["guidance"])


if __name__ == "__main__":
    unittest.main()

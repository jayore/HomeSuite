from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest import mock

from console_snapshot import build_doctor_report, build_snapshot
from tools.doctor import Check


class ConsoleSnapshotTests(unittest.TestCase):
    def test_snapshot_reports_effective_source_without_exposing_credentials(self):
        app_config = SimpleNamespace(
            LOCAL_PREFS_KEYS=["PTT_ENABLED"],
            DEPLOYMENT_CONFIG_KEYS=["DEFAULT_ROOM", "ROOMS"],
            DEFAULT_ROOM="office",
            ROOMS={
                "office": {
                    "label": "Office",
                    "aliases": ["study"],
                    "ha_area_id": "office",
                    "defaults": {"brightness_target": {"type": "area"}, "tv": None},
                    "devices": [],
                    "scenes": [],
                    "media_players": [],
                }
            },
            PTT_ENABLED=True,
            PTT_GPIO_PIN=17,
            PTT_LISTEN_LEVEL="low",
            PTT_END_BEHAVIOR="cancel",
            PHYSICAL_BUTTONS_ENABLED=True,
            PHYSICAL_BUTTON_PINS={1: 2},
            PHYSICAL_BUTTON_ACTIONS={1: {"press": "turn on the office light"}},
            WAKEWORD_ENABLED=True,
            UNIFIED_SERVER_ENABLED=True,
            UNIFIED_SERVER_PORT=8765,
            CONSOLE_HOST="0.0.0.0",
            CONSOLE_PORT=8766,
            HOME_LOCATION={"latitude": 1.0, "longitude": 2.0},
            CALENDARS={},
            WEATHER_ENTITY_ID=None,
        )
        private_config = SimpleNamespace(
            HA_URL="http://homeassistant.local:8123",
            HA_TOKEN="ha-super-secret",
            OPENAI_API_KEY="openai-super-secret",
        )

        snapshot = build_snapshot(app_config=app_config, private_config=private_config)
        encoded = json.dumps(snapshot)

        self.assertNotIn("ha-super-secret", encoded)
        self.assertNotIn("openai-super-secret", encoded)
        self.assertEqual(
            snapshot["overview"]["roles"],
            ["text", "api", "ptt", "wakeword"],
        )
        ptt = next(row for row in snapshot["node"] if row["key"] == "PTT_ENABLED")
        ptt_pin = next(row for row in snapshot["node"] if row["key"] == "PTT_GPIO_PIN")
        default_room = next(row for row in snapshot["node"] if row["key"] == "DEFAULT_ROOM")
        self.assertEqual(ptt["source"], "device")
        self.assertEqual(ptt["label"], "PTT enabled")
        self.assertEqual(ptt_pin["value"], 17)
        self.assertEqual(default_room["source"], "deployment")
        self.assertTrue(snapshot["rooms"][0]["is_default"])

    def test_detailed_doctor_report_redacts_secret_values(self):
        class FakeDoctor:
            def __init__(self, **_kwargs):
                self.private_config = SimpleNamespace(OPENAI_API_KEY="secret-value")

            def run(self, *, report):
                self.assert_report = report
                return 0

            def relevant_checks(self):
                return [Check("Live checks", "WARN", "provider", "failed with secret-value")]

            def role_summary(self):
                return [{"role": "text", "status": "WARN", "required_failures": 0, "warnings": 1}]

        with mock.patch("tools.doctor.Doctor", FakeDoctor):
            report = build_doctor_report(live=True)

        self.assertTrue(report["ok"])
        self.assertTrue(report["live"])
        self.assertEqual(report["checks"][0]["detail"], "failed with [redacted]")

    def test_unhealthy_checks_receive_relevant_console_actions(self):
        class FakeDoctor:
            def __init__(self, **_kwargs):
                self.private_config = SimpleNamespace()

            def run(self, *, report):
                return 1

            def relevant_checks(self):
                return [
                    Check("Live checks", "FAIL", "Home Assistant reachable", "HTTP 401", required=True),
                    Check("Runtime readiness", "WARN", "configured audio input", "not found"),
                    Check("Rooms", "WARN", "room brightness", "missing entity"),
                    Check("Config files", "FAIL", "local_prefs.py imports", "invalid", required=True),
                    Check("Config files", "OK", "private_config.py", "found", required=True),
                ]

            def role_summary(self):
                return []

        with mock.patch("tools.doctor.Doctor", FakeDoctor):
            report = build_doctor_report(live=True)

        self.assertEqual(report["checks"][0]["action"]["view"], "integrations")
        self.assertEqual(report["checks"][1]["action"]["view"], "audio")
        self.assertEqual(report["checks"][2]["action"]["view"], "rooms")
        self.assertEqual(report["checks"][3]["action"]["view"], "configuration")
        self.assertNotIn("action", report["checks"][4])


if __name__ == "__main__":
    unittest.main()

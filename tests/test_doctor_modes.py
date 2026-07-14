from __future__ import annotations

import unittest
from unittest import mock
from types import SimpleNamespace

from tools.doctor import Doctor


def _doctor(*, private_values=None, pref_values=None):
    doctor = Doctor.__new__(Doctor)
    doctor.live = False
    doctor.timeout = 1.0
    doctor.requested_roles = ()
    doctor.json_output = False
    doctor.checks = []
    doctor.private_config = SimpleNamespace(**(private_values or {}))
    doctor.app_config = SimpleNamespace(**(pref_values or {}))
    doctor.local_prefs = SimpleNamespace()
    return doctor


class DoctorModeTests(unittest.TestCase):
    def test_active_roles_follow_enabled_node_capabilities(self):
        doctor = _doctor(
            pref_values={
                "UNIFIED_SERVER_ENABLED": True,
                "PTT_ENABLED": True,
                "WAKEWORD_ENABLED": True,
            }
        )

        self.assertEqual(doctor.active_roles(), ("text", "api", "ptt", "wakeword"))

    def test_explicit_role_limits_the_readiness_summary(self):
        doctor = _doctor(pref_values={"UNIFIED_SERVER_ENABLED": True})
        doctor.requested_roles = ("wakeword",)

        self.assertEqual(doctor.active_roles(), ("wakeword",))

    def test_explicit_role_excludes_unrelated_required_failures(self):
        doctor = _doctor()
        doctor.requested_roles = ("ptt",)
        doctor._active_roles = doctor.active_roles()
        doctor.add("Core", "FAIL", "API key", required=True, roles=("api",))
        doctor.add("Runtime readiness", "OK", "PTT GPIO", roles=("ptt",))

        self.assertEqual([check.label for check in doctor.relevant_checks()], ["PTT GPIO"])
        self.assertEqual(doctor.required_failures(), [])

    def test_text_only_mode_warns_when_openai_is_missing(self):
        doctor = _doctor(
            private_values={
                "HA_URL": "http://homeassistant.local:8123",
                "HA_TOKEN": "token",
                "HOMESUITE_HTTP_API_KEY": "shared-key",
                "OPENAI_API_KEY": "",
            },
            pref_values={
                "PTT_ENABLED": False,
                "WAKEWORD_ENABLED": False,
                "UNIFIED_SERVER_ENABLED": True,
            },
        )

        doctor.check_core_config()

        openai = next(check for check in doctor.checks if check.label == "OpenAI API key")
        self.assertEqual(openai.status, "WARN")
        self.assertFalse(openai.required)

    def test_voice_mode_requires_openai(self):
        doctor = _doctor(
            private_values={
                "HA_URL": "http://homeassistant.local:8123",
                "HA_TOKEN": "token",
                "HOMESUITE_HTTP_API_KEY": "shared-key",
                "OPENAI_API_KEY": "",
            },
            pref_values={
                "PTT_ENABLED": True,
                "WAKEWORD_ENABLED": False,
                "UNIFIED_SERVER_ENABLED": True,
            },
        )

        doctor.check_core_config()

        openai = next(check for check in doctor.checks if check.label == "OpenAI API key")
        self.assertEqual(openai.status, "FAIL")
        self.assertTrue(openai.required)

    def test_enabled_server_requires_shared_api_key(self):
        doctor = _doctor(
            private_values={
                "HA_URL": "http://homeassistant.local:8123",
                "HA_TOKEN": "token",
                "HOMESUITE_HTTP_API_KEY": "",
                "OPENAI_API_KEY": "key",
            },
            pref_values={
                "PTT_ENABLED": False,
                "WAKEWORD_ENABLED": False,
                "UNIFIED_SERVER_ENABLED": True,
            },
        )

        doctor.check_core_config()

        api = next(check for check in doctor.checks if "HTTP/WebSocket API key" in check.label)
        self.assertEqual(api.status, "FAIL")
        self.assertTrue(api.required)

    def test_disabled_server_does_not_require_shared_api_key(self):
        doctor = _doctor(
            private_values={
                "HA_URL": "http://homeassistant.local:8123",
                "HA_TOKEN": "token",
                "HOMESUITE_HTTP_API_KEY": "",
                "OPENAI_API_KEY": "key",
            },
            pref_values={
                "PTT_ENABLED": False,
                "WAKEWORD_ENABLED": False,
                "UNIFIED_SERVER_ENABLED": False,
            },
        )

        doctor.check_core_config()

        api = next(
            check for check in doctor.checks if check.label == "Home Suite HTTP/WebSocket API"
        )
        self.assertEqual(api.status, "SKIP")
        self.assertFalse(api.required)

    def test_explicit_none_brightness_is_a_supported_opt_out(self):
        import home_registry

        doctor = _doctor()
        rooms = {"quiet_room": {"defaults": {"brightness_target": None}}}

        with mock.patch.dict(home_registry.ROOMS, rooms, clear=True):
            doctor.check_room_brightness()

        brightness = next(
            check for check in doctor.checks if check.label == "quiet_room brightness"
        )
        self.assertEqual(brightness.status, "SKIP")
        self.assertEqual(brightness.detail, "disabled")

    def test_malformed_non_null_brightness_still_warns(self):
        import home_registry

        doctor = _doctor()
        rooms = {"bad_room": {"defaults": {"brightness_target": {"type": "bogus"}}}}

        with mock.patch.dict(home_registry.ROOMS, rooms, clear=True):
            doctor.check_room_brightness()

        brightness = next(
            check for check in doctor.checks if check.label == "bad_room brightness"
        )
        self.assertEqual(brightness.status, "WARN")
        self.assertEqual(brightness.detail, "invalid configuration")

    def test_live_topology_reports_missing_configured_entities_without_failing_core_setup(self):
        doctor = _doctor(
            private_values={"HA_URL": "http://homeassistant.local:8123", "HA_TOKEN": "token"},
            pref_values={
                "ROOMS": {
                    "office": {
                        "defaults": {"color_light": "light.missing"},
                        "media_players": ["media_player.present"],
                    }
                },
                "CALENDARS": {},
                "WEATHER_ENTITY_ID": None,
                "HA_DEVICE_ALIASES": {},
                "HA_TRIGGER_ALIASES": {},
            },
        )
        doctor.get_url = mock.Mock(
            return_value=(
                200,
                '[{"entity_id": "media_player.present"}]',
            )
        )

        doctor.check_live_topology()

        topology = next(check for check in doctor.checks if check.label == "configured HA entities")
        self.assertEqual(topology.status, "WARN")
        self.assertIn("light.missing", topology.detail)

    def test_live_topology_accepts_all_configured_entities_present_in_home_assistant(self):
        doctor = _doctor(
            private_values={"HA_URL": "http://homeassistant.local:8123", "HA_TOKEN": "token"},
            pref_values={
                "ROOMS": {"office": {"defaults": {"color_light": "light.office"}}},
                "CALENDARS": {"personal": {"entity_id": "calendar.personal"}},
                "WEATHER_ENTITY_ID": "weather.home",
                "HA_DEVICE_ALIASES": {},
                "HA_TRIGGER_ALIASES": {},
            },
        )
        doctor.get_url = mock.Mock(
            return_value=(
                200,
                '[{"entity_id": "light.office"}, {"entity_id": "calendar.personal"}, {"entity_id": "weather.home"}]',
            )
        )

        doctor.check_live_topology()

        topology = next(check for check in doctor.checks if check.label == "configured HA entities")
        self.assertEqual(topology.status, "OK")
        self.assertIn("3 configured", topology.detail)


if __name__ == "__main__":
    unittest.main()

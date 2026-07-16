from __future__ import annotations

import sys
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

    def test_ptt_readiness_uses_canonical_gpio_settings(self):
        doctor = _doctor(
            pref_values={
                "PTT_ENABLED": True,
                "HANDSET_PRESENT": False,
                "PTT_GPIO_PIN": 17,
                "PTT_LISTEN_LEVEL": "high",
            }
        )
        doctor.module_available = mock.Mock(return_value=True)

        doctor.check_ptt_runtime()

        self.assertEqual(len(doctor.checks), 1)
        self.assertEqual(doctor.checks[0].label, "PTT GPIO input")
        self.assertEqual(doctor.checks[0].status, "OK")
        self.assertIn("BCM GPIO 17", doctor.checks[0].detail)
        self.assertIn("while high", doctor.checks[0].detail)

    def test_ptt_readiness_rejects_an_invalid_listen_level(self):
        doctor = _doctor(
            pref_values={
                "PTT_ENABLED": True,
                "PTT_GPIO_PIN": 11,
                "PTT_LISTEN_LEVEL": "pressed",
            }
        )
        doctor.module_available = mock.Mock(return_value=True)

        doctor.check_ptt_runtime()

        self.assertEqual(doctor.checks[0].status, "FAIL")
        self.assertTrue(doctor.checks[0].required)

    def test_busy_alsa_capture_device_warns_instead_of_failing(self):
        doctor = _doctor(
            pref_values={
                "AUDIO_INPUT_PROFILE": {
                    "device_match": "MOVO X1 MINI",
                    "sample_rate": 16000,
                    "channels": 1,
                }
            }
        )
        sounddevice = SimpleNamespace(
            query_devices=lambda: [
                {
                    "name": "MOVO X1 MINI: USB Audio (hw:3,0)",
                    "max_input_channels": 0,
                }
            ]
        )
        doctor._alsa_capture_card = mock.Mock(return_value=3)

        with mock.patch.dict(sys.modules, {"sounddevice": sounddevice}):
            doctor.check_audio_input_profile(("wakeword",))

        self.assertEqual(doctor.checks[0].status, "WARN")
        self.assertFalse(doctor.checks[0].required)
        self.assertIn("ALSA card 3", doctor.checks[0].detail)

    def test_missing_audio_device_remains_a_required_failure(self):
        doctor = _doctor(
            pref_values={
                "AUDIO_INPUT_PROFILE": {
                    "device_match": "missing microphone",
                    "sample_rate": 16000,
                    "channels": 1,
                }
            }
        )
        sounddevice = SimpleNamespace(query_devices=lambda: [])
        doctor._alsa_capture_card = mock.Mock(return_value=None)

        with mock.patch.dict(sys.modules, {"sounddevice": sounddevice}):
            doctor.check_audio_input_profile(("wakeword",))

        self.assertEqual(doctor.checks[0].status, "FAIL")
        self.assertTrue(doctor.checks[0].required)

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

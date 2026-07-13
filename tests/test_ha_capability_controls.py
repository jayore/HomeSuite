from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class CapabilityControlTests(unittest.TestCase):
    def _handle(self, text, resolved, states=None):
        from ha_capability_controls import handle_ha_capability_controls

        calls = []

        def call(service, payload):
            calls.append((service, payload))
            return True

        resolver = mock.Mock(return_value=resolved)
        result = handle_ha_capability_controls(
            tl=text,
            states_snapshot=states or [],
            resolve_device_entity=resolver,
            call_ha_service=call,
            maybe_say=lambda response: response,
        )
        return result, calls, resolver

    def test_open_cover_uses_cover_service(self):
        result, calls, _resolver = self._handle(
            "open the office blinds",
            ("cover.office_blinds", "cover"),
        )

        self.assertEqual(result, "Okay.")
        self.assertEqual(
            calls,
            [("cover/open_cover", {"entity_id": "cover.office_blinds"})],
        )

    def test_cover_command_rejects_wrong_domain(self):
        result, calls, _resolver = self._handle(
            "close the garage door",
            ("button.garage_door", "button"),
        )

        self.assertEqual(result, "I couldn't find that cover.")
        self.assertEqual(calls, [])

    def test_cover_position_is_bounded(self):
        result, calls, resolver = self._handle(
            "set the office blinds to 120 percent",
            ("cover.office_blinds", "cover"),
        )

        self.assertEqual(result, "Cover position must be between 0 and 100 percent.")
        self.assertEqual(calls, [])
        resolver.assert_not_called()

    def test_cover_position_query_reads_current_position(self):
        states = [
            {
                "entity_id": "cover.office_blinds",
                "state": "open",
                "attributes": {"current_position": 42},
            }
        ]
        result, calls, _resolver = self._handle(
            "how open are the office blinds",
            ("cover.office_blinds", "cover"),
            states,
        )

        self.assertEqual(result, "It's at about 42 percent.")
        self.assertEqual(calls, [])

    def test_fan_percentage_and_named_speed(self):
        result, calls, _resolver = self._handle(
            "set the bedroom fan speed to 45 percent",
            ("fan.bedroom", "fan"),
        )
        self.assertEqual(result, "Okay.")
        self.assertEqual(
            calls,
            [("fan/set_percentage", {"entity_id": "fan.bedroom", "percentage": 45})],
        )

        result, calls, _resolver = self._handle(
            "set the bedroom fan to high",
            ("fan.bedroom", "fan"),
        )
        self.assertEqual(result, "Okay.")
        self.assertEqual(
            calls,
            [("fan/set_percentage", {"entity_id": "fan.bedroom", "percentage": 100})],
        )

    def test_fan_preset_must_be_reported_by_entity(self):
        states = [
            {
                "entity_id": "fan.bedroom",
                "state": "on",
                "attributes": {"preset_modes": ["Auto", "Sleep"]},
            }
        ]
        result, calls, _resolver = self._handle(
            "set the bedroom fan to sleep",
            ("fan.bedroom", "fan"),
            states,
        )

        self.assertEqual(result, "Okay.")
        self.assertEqual(
            calls,
            [("fan/set_preset_mode", {"entity_id": "fan.bedroom", "preset_mode": "Sleep"})],
        )

        result, calls, _resolver = self._handle(
            "set the bedroom fan to turbo",
            ("fan.bedroom", "fan"),
            states,
        )
        self.assertEqual(result, "That fan doesn't report that preset mode.")
        self.assertEqual(calls, [])

    def test_fan_speed_step_uses_documented_service(self):
        result, calls, _resolver = self._handle(
            "increase the bedroom fan speed",
            ("fan.bedroom", "fan"),
        )

        self.assertEqual(result, "Okay.")
        self.assertEqual(
            calls,
            [("fan/increase_speed", {"entity_id": "fan.bedroom"})],
        )

    def test_fan_speed_query_reads_percentage(self):
        states = [
            {
                "entity_id": "fan.bedroom",
                "state": "on",
                "attributes": {"percentage": 55},
            }
        ]
        result, calls, _resolver = self._handle(
            "what is the bedroom fan speed",
            ("fan.bedroom", "fan"),
            states,
        )

        self.assertEqual(result, "It's at about 55 percent.")
        self.assertEqual(calls, [])

    def test_thermostat_temperature_respects_entity_range(self):
        states = [
            {
                "entity_id": "climate.hallway",
                "state": "heat",
                "attributes": {"min_temp": 50, "max_temp": 90},
            }
        ]
        result, calls, _resolver = self._handle(
            "set the hallway thermostat to 72 degrees",
            ("climate.hallway", "climate"),
            states,
        )
        self.assertEqual(result, "Okay.")
        self.assertEqual(
            calls,
            [("climate/set_temperature", {"entity_id": "climate.hallway", "temperature": 72.0})],
        )

        result, calls, _resolver = self._handle(
            "set the hallway thermostat to 95 degrees",
            ("climate.hallway", "climate"),
            states,
        )
        self.assertEqual(
            result,
            "That thermostat accepts temperatures from 50 to 90 degrees.",
        )
        self.assertEqual(calls, [])

    def test_thermostat_mode_is_validated(self):
        states = [
            {
                "entity_id": "climate.hallway",
                "state": "heat",
                "attributes": {"hvac_modes": ["off", "heat", "cool"]},
            }
        ]
        result, calls, _resolver = self._handle(
            "set the hallway thermostat mode to cool",
            ("climate.hallway", "climate"),
            states,
        )
        self.assertEqual(result, "Okay.")
        self.assertEqual(
            calls,
            [("climate/set_hvac_mode", {"entity_id": "climate.hallway", "hvac_mode": "cool"})],
        )

        result, calls, _resolver = self._handle(
            "set the hallway thermostat mode to dry",
            ("climate.hallway", "climate"),
            states,
        )
        self.assertEqual(result, "That thermostat doesn't support that mode.")
        self.assertEqual(calls, [])

    def test_thermostat_target_query_is_read_only(self):
        states = [
            {
                "entity_id": "climate.hallway",
                "state": "heat",
                "attributes": {"temperature": 68},
            }
        ]
        result, calls, _resolver = self._handle(
            "what is the hallway thermostat set to",
            ("climate.hallway", "climate"),
            states,
        )

        self.assertEqual(result, "It's set to 68 degrees.")
        self.assertEqual(calls, [])

    def test_thermostat_range_query_accepts_string_attributes(self):
        states = [
            {
                "entity_id": "climate.hallway",
                "state": "heat_cool",
                "attributes": {
                    "target_temp_low": "66",
                    "target_temp_high": "74",
                },
            }
        ]
        result, calls, _resolver = self._handle(
            "what is the hallway thermostat set to",
            ("climate.hallway", "climate"),
            states,
        )

        self.assertEqual(result, "It's set from 66 to 74 degrees.")
        self.assertEqual(calls, [])

    def test_vacuum_start_and_dock_use_specific_services(self):
        result, calls, _resolver = self._handle(
            "start the vacuum",
            ("vacuum.downstairs", "vacuum"),
        )
        self.assertEqual(result, "Okay.")
        self.assertEqual(
            calls,
            [("vacuum/start", {"entity_id": "vacuum.downstairs"})],
        )

        result, calls, _resolver = self._handle(
            "send the vacuum home",
            ("vacuum.downstairs", "vacuum"),
        )
        self.assertEqual(result, "Okay.")
        self.assertEqual(
            calls,
            [("vacuum/return_to_base", {"entity_id": "vacuum.downstairs"})],
        )

    def test_vacuum_status_is_read_only(self):
        states = [{"entity_id": "vacuum.downstairs", "state": "returning"}]
        result, calls, _resolver = self._handle(
            "what is the vacuum doing",
            ("vacuum.downstairs", "vacuum"),
            states,
        )

        self.assertEqual(result, "It's returning.")
        self.assertEqual(calls, [])

    def test_unrelated_language_falls_through(self):
        result, calls, resolver = self._handle(
            "tell me about thermodynamics",
            None,
        )

        self.assertIsNone(result)
        self.assertEqual(calls, [])
        resolver.assert_not_called()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import json
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class OnOffSafetyTests(unittest.TestCase):
    def test_all_lights_uses_only_verified_light_entities(self):
        from on_off_controls import handle_on_off_controls

        call = mock.Mock(return_value=True)
        resolver = mock.Mock()
        states = [
            {"entity_id": "switch.coffee"},
            {"entity_id": "light.floor"},
            {"entity_id": "button.unrelated"},
            {"entity_id": "light.ceiling"},
        ]

        result = handle_on_off_controls(
            tl="turn off all the lights",
            call_ha_service=call,
            maybe_say=lambda text: text,
            resolve_device_entity=resolver,
            states_snapshot=states,
        )

        self.assertEqual(result, "Okay.")
        call.assert_called_once_with(
            "light/turn_off",
            {"entity_id": ["light.ceiling", "light.floor"]},
        )
        resolver.assert_not_called()

    def test_all_lights_excludes_configured_helper_entities(self):
        import on_off_controls

        call = mock.Mock(return_value=True)
        with mock.patch.object(
            on_off_controls,
            "is_assistant_bulk_entity_allowed",
            side_effect=lambda entity_id: entity_id != "light.virtual_helper",
        ):
            result = on_off_controls.handle_on_off_controls(
                tl="turn off all the lights",
                call_ha_service=call,
                maybe_say=lambda text: text,
                resolve_device_entity=mock.Mock(),
                states_snapshot=[
                    {"entity_id": "light.ceiling"},
                    {"entity_id": "light.virtual_helper"},
                ],
            )

        self.assertEqual(result, "Okay.")
        call.assert_called_once_with(
            "light/turn_off",
            {"entity_id": ["light.ceiling"]},
        )

    def test_supported_switch_uses_its_real_binary_service(self):
        from on_off_controls import handle_on_off_controls

        call = mock.Mock(return_value=True)
        result = handle_on_off_controls(
            tl="turn off the coffee maker",
            call_ha_service=call,
            maybe_say=lambda text: text,
            resolve_device_entity=lambda _phrase: ("switch.coffee", "switch"),
        )

        self.assertEqual(result, "Turning off coffee maker.")
        call.assert_called_once_with("switch/turn_off", {"entity_id": "switch.coffee"})

    def test_unsupported_button_domain_is_rejected_without_a_write(self):
        from on_off_controls import handle_on_off_controls

        call = mock.Mock(return_value=True)
        result = handle_on_off_controls(
            tl="turn off the coffee maker",
            call_ha_service=call,
            maybe_say=lambda text: text,
            resolve_device_entity=lambda _phrase: ("button.unrelated", "button"),
        )

        self.assertEqual(result, "I can't turn coffee maker off.")
        call.assert_not_called()

    def test_all_room_lights_remains_area_scoped(self):
        import on_off_controls

        call = mock.Mock(return_value=True)
        resolver = mock.Mock()
        with (
            mock.patch.object(on_off_controls, "find_room_by_alias", return_value="kitchen"),
            mock.patch.object(
                on_off_controls,
                "get_room",
                return_value={"ha_area_id": "kitchen_area"},
            ),
        ):
            result = on_off_controls.handle_on_off_controls(
                tl="turn off all the kitchen lights",
                call_ha_service=call,
                maybe_say=lambda text: text,
                resolve_device_entity=resolver,
                states_snapshot=[{"entity_id": "light.somewhere_else"}],
            )

        self.assertEqual(result, "Okay.")
        call.assert_called_once_with("light/turn_off", {"area_id": "kitchen_area"})
        resolver.assert_not_called()


class AreaRegistryTests(unittest.TestCase):
    def test_area_lookup_prefers_fast_template_endpoint(self):
        import ha_client

        response = mock.Mock(status_code=200)
        response.text = json.dumps(["light.ceiling", "sensor.humidity", "sensor.humidity"])
        session = mock.Mock()
        session.post.return_value = response
        with (
            mock.patch.object(ha_client, "_HA_URL", "http://ha.test:8123"),
            mock.patch.object(ha_client, "HA_SESSION", session),
            mock.patch.object(ha_client, "ha_refresh_registry_cache") as refresh,
        ):
            result = ha_client.ha_get_entities_for_area("kitchen", domains={"sensor"})

        self.assertEqual(result, ["sensor.humidity"])
        refresh.assert_not_called()
        self.assertEqual(
            session.post.call_args.kwargs["json"]["template"],
            '{{ area_entities("kitchen") | tojson }}',
        )

    def test_registry_refresh_uses_one_websocket_for_all_lists(self):
        import ha_client

        ws = mock.Mock()
        ws.recv.side_effect = [
            json.dumps({"type": "auth_required"}),
            json.dumps({"type": "auth_ok"}),
            json.dumps({"id": 1002, "type": "result", "success": True, "result": []}),
            json.dumps({"id": 1001, "type": "result", "success": True, "result": []}),
            json.dumps({"id": 1003, "type": "result", "success": True, "result": []}),
        ]
        with (
            mock.patch.object(ha_client.websocket, "create_connection", return_value=ws) as connect,
            mock.patch.object(ha_client, "_ha_ws_url", return_value="ws://ha.test/api/websocket"),
            mock.patch.object(ha_client, "_ha_access_token", return_value="token"),
            mock.patch.object(ha_client, "_REGISTRY_CACHE", {"ts": 0}),
        ):
            result = ha_client.ha_refresh_registry_cache(force=True)

        self.assertTrue(result)
        connect.assert_called_once_with("ws://ha.test/api/websocket", timeout=10)
        self.assertEqual(ws.send.call_count, 4)
        ws.close.assert_called_once_with(timeout=0.25)

    def test_generic_area_lookup_filters_domains_and_follows_device_area(self):
        import ha_client

        cache = {
            "ts": 0,
            "areas": [],
            "devices": [{"id": "device-1", "area_id": "kitchen"}],
            "entities": [
                {"entity_id": "sensor.direct", "area_id": "kitchen"},
                {"entity_id": "sensor.via_device", "device_id": "device-1"},
                {"entity_id": "light.via_device", "device_id": "device-1"},
                {"entity_id": "sensor.other", "area_id": "office"},
            ],
        }
        with mock.patch.object(ha_client, "_REGISTRY_CACHE", cache):
            result = ha_client.ha_get_entities_for_area(
                "kitchen",
                domains={"sensor"},
                refresh_if_needed=False,
            )

        self.assertEqual(result, ["sensor.direct", "sensor.via_device"])


class AssistantBulkFilterTests(unittest.TestCase):
    def test_exact_ids_and_glob_patterns_are_filtered(self):
        import home_registry

        with (
            mock.patch.object(
                home_registry,
                "ASSISTANT_BULK_EXCLUDED_ENTITY_IDS",
                ["light.room_proxy"],
            ),
            mock.patch.object(
                home_registry,
                "ASSISTANT_BULK_EXCLUDED_ENTITY_PATTERNS",
                ["light.*_flicker"],
            ),
        ):
            self.assertFalse(
                home_registry.is_assistant_bulk_entity_allowed("light.room_proxy")
            )
            self.assertFalse(
                home_registry.is_assistant_bulk_entity_allowed("light.lamp_flicker")
            )
            self.assertTrue(
                home_registry.is_assistant_bulk_entity_allowed("light.floor_lamp")
            )


class StateQueryContractTests(unittest.TestCase):
    def _handle(self, text, states, resolver=None, remember_entity=None):
        from state_query_controls import handle_state_query_controls

        return handle_state_query_controls(
            text,
            states_snapshot=states,
            resolve_device_entity=resolver or (lambda _phrase: None),
            remember_entity=remember_entity,
        )

    def test_shared_matcher_covers_documented_and_sensor_queries(self):
        from state_query_controls import looks_like_state_query

        for phrase in (
            "what lights are on?",
            "is the garage door open?",
            "what is the bedroom humidity?",
            "what's the front door battery?",
            "are any windows open?",
        ):
            with self.subTest(phrase=phrase):
                self.assertTrue(looks_like_state_query(phrase))

    def test_which_lights_are_on_lists_only_on_lights(self):
        states = [
            {
                "entity_id": "light.ceiling",
                "state": "on",
                "attributes": {"friendly_name": "Ceiling Light"},
            },
            {
                "entity_id": "light.floor",
                "state": "off",
                "attributes": {"friendly_name": "Floor Lamp"},
            },
            {"entity_id": "switch.coffee", "state": "on"},
        ]

        result = self._handle("what lights are on?", states)

        self.assertEqual(result, "Ceiling Light is on.")

    def test_light_summary_omits_configured_helpers(self):
        import state_query_controls

        states = [
            {
                "entity_id": "light.ceiling",
                "state": "on",
                "attributes": {"friendly_name": "Ceiling Light"},
            },
            {
                "entity_id": "light.virtual_helper",
                "state": "on",
                "attributes": {"friendly_name": "Virtual Helper"},
            },
        ]
        with mock.patch.object(
            state_query_controls,
            "is_assistant_bulk_entity_allowed",
            side_effect=lambda entity_id: entity_id != "light.virtual_helper",
        ):
            result = self._handle("what lights are on?", states)

        self.assertEqual(result, "Ceiling Light is on.")

    def test_open_garage_binary_sensor_answers_truthfully(self):
        states = [
            {
                "entity_id": "binary_sensor.garage_door",
                "state": "on",
                "attributes": {
                    "friendly_name": "Garage Door",
                    "device_class": "garage_door",
                },
            }
        ]
        resolver = lambda _phrase: ("binary_sensor.garage_door", "binary_sensor")

        self.assertEqual(self._handle("is the garage door open?", states, resolver), "Yes.")
        self.assertEqual(self._handle("is the garage door closed?", states, resolver), "No.")

    def test_any_open_window_uses_aggregate_sensor_state(self):
        states = [
            {
                "entity_id": "binary_sensor.kitchen_window",
                "state": "on",
                "attributes": {
                    "friendly_name": "Kitchen Window",
                    "device_class": "window",
                },
            }
        ]

        result = self._handle("are any windows open?", states)

        self.assertEqual(result, "Yes. 1 window is open.")

    def test_unavailable_binary_state_does_not_become_no(self):
        states = [{"entity_id": "switch.coffee", "state": "unavailable"}]
        resolver = lambda _phrase: ("switch.coffee", "switch")

        result = self._handle("is the coffee maker on?", states, resolver)

        self.assertEqual(result, "I couldn't read that device right now.")

    def test_named_state_query_publishes_only_a_verified_entity(self):
        states = [{"entity_id": "light.stair", "state": "on"}]
        remember = mock.Mock()

        result = self._handle(
            "is the stair light on?",
            states,
            lambda _phrase: ("light.stair", "light"),
            remember,
        )

        self.assertEqual(result, "Yes.")
        remember.assert_called_once_with("light.stair", "light")

    def test_unreadable_state_does_not_publish_a_referent(self):
        states = [{"entity_id": "light.stair", "state": "unavailable"}]
        remember = mock.Mock()

        self._handle(
            "is the stair light on?",
            states,
            lambda _phrase: ("light.stair", "light"),
            remember,
        )

        remember.assert_not_called()

    def test_room_humidity_uses_area_membership(self):
        import state_query_controls

        states = [
            {
                "entity_id": "sensor.bedroom_humidity",
                "state": "46",
                "attributes": {
                    "friendly_name": "Bedroom Humidity",
                    "device_class": "humidity",
                    "unit_of_measurement": "%",
                },
            },
            {
                "entity_id": "sensor.outdoor_humidity",
                "state": "90",
                "attributes": {
                    "friendly_name": "Outdoor Humidity",
                    "device_class": "humidity",
                    "unit_of_measurement": "%",
                },
            },
        ]
        with (
            mock.patch.object(
                state_query_controls,
                "get_room_alias_map",
                return_value={"bedroom": "bedroom"},
            ),
            mock.patch.object(
                state_query_controls,
                "get_room",
                return_value={"ha_area_id": "bedroom_area"},
            ),
            mock.patch.object(
                state_query_controls,
                "get_room_label",
                return_value="Bedroom",
            ),
            mock.patch.object(
                state_query_controls,
                "ha_get_entities_for_area",
                return_value=["sensor.bedroom_humidity"],
            ) as area_lookup,
        ):
            result = self._handle("what is the bedroom humidity?", states)

        self.assertEqual(result, "It's about 46 percent in Bedroom.")
        area_lookup.assert_called_once_with("bedroom_area", domains={"sensor"})

    def test_named_battery_query_selects_the_requested_sensor(self):
        states = [
            {
                "entity_id": "sensor.front_door_battery",
                "state": "82",
                "attributes": {
                    "friendly_name": "Front Door Battery",
                    "device_class": "battery",
                    "unit_of_measurement": "%",
                },
            },
            {
                "entity_id": "sensor.back_door_battery",
                "state": "61",
                "attributes": {
                    "friendly_name": "Back Door Battery",
                    "device_class": "battery",
                    "unit_of_measurement": "%",
                },
            },
        ]

        result = self._handle("what's the front door battery?", states)

        self.assertEqual(result, "Front Door Battery is at 82 percent.")


class DeviceReferentContinuityTests(unittest.TestCase):
    def setUp(self):
        import dialogue_state

        dialogue_state.reset_dialogue_state(all_scopes=True)

    def tearDown(self):
        import dialogue_state

        dialogue_state.reset_dialogue_state(all_scopes=True)

    def test_state_query_then_turn_it_off_uses_the_verified_entity_id(self):
        import command_dispatch
        from on_off_controls import handle_on_off_controls
        from state_query_controls import handle_state_query_controls

        states = [{"entity_id": "light.stair", "state": "on"}]
        call = mock.Mock(return_value=True)
        query = handle_state_query_controls(
            "is the stair light on?",
            states_snapshot=states,
            resolve_device_entity=lambda _phrase: ("light.stair", "light"),
            remember_entity=lambda eid, domain: command_dispatch._remember_resolved_entity(
                eid,
                domain,
                source="state_query",
            ),
        )
        action = handle_on_off_controls(
            tl="turn it off",
            call_ha_service=call,
            maybe_say=lambda text: text,
            resolve_device_entity=lambda phrase: command_dispatch._resolve_device_entity_with_context(
                phrase,
                states,
                capability="binary_control",
            ),
            states_snapshot=states,
        )

        self.assertEqual(query, "Yes.")
        self.assertEqual(action, "Turning it off.")
        call.assert_called_once_with("light/turn_off", {"entity_id": "light.stair"})

    def test_missing_live_entity_invalidates_referent_without_a_write(self):
        import command_dispatch
        from on_off_controls import handle_on_off_controls

        command_dispatch._remember_resolved_entity(
            "light.stair",
            "light",
            source="test",
        )
        call = mock.Mock(return_value=True)
        with mock.patch.dict(os.environ, {"PIPHONE_LIVE": "1"}, clear=False):
            action = handle_on_off_controls(
                tl="turn it off",
                call_ha_service=call,
                maybe_say=lambda text: text,
                resolve_device_entity=lambda phrase: command_dispatch._resolve_device_entity_with_context(
                    phrase,
                    [],
                    capability="binary_control",
                ),
                states_snapshot=[],
            )

        self.assertIsNone(action)
        call.assert_not_called()


class AlarmDryRunTests(unittest.TestCase):
    @staticmethod
    def _parsed(kind):
        return {
            "kind": kind,
            "label": None,
            "run_at": 2_000_000_000.0,
            "phrase": "in 10 minutes" if kind == "timer" else "at 7 AM",
            "output": {"mode": "local"},
            "action_command": None,
            "music_command": None,
        }

    def test_test_mode_parses_timer_without_persisting_or_scheduling(self):
        import alarm_controls

        with (
            mock.patch.dict(os.environ, {"PIPHONE_TEST_MODE": "1"}, clear=True),
            mock.patch.object(
                alarm_controls,
                "_parse_create_alarm",
                return_value=self._parsed("timer"),
            ),
            mock.patch.object(alarm_controls, "_save_new_alarm") as save,
            mock.patch.object(alarm_controls, "_schedule_alarm_fire") as schedule,
        ):
            result = alarm_controls.handle_alarm_controls(tl="set a timer for 10 minutes")

        self.assertIn("timer set", result.lower())
        save.assert_not_called()
        schedule.assert_not_called()

    def test_capture_mode_does_not_persist_alarm(self):
        import alarm_controls

        with (
            mock.patch.dict(os.environ, {"PIPHONE_COMMAND_RUNTIME": "1"}, clear=True),
            mock.patch.object(
                alarm_controls,
                "_parse_create_alarm",
                return_value=self._parsed("alarm"),
            ),
            mock.patch.object(alarm_controls, "_save_new_alarm") as save,
            mock.patch.object(alarm_controls, "_schedule_alarm_fire") as schedule,
        ):
            result = alarm_controls.handle_alarm_controls(tl="set an alarm for 7 am")

        self.assertIn("alarm set", result.lower())
        save.assert_not_called()
        schedule.assert_not_called()

    def test_command_runtime_live_mode_still_persists(self):
        import alarm_controls

        with (
            mock.patch.dict(
                os.environ,
                {"PIPHONE_COMMAND_RUNTIME": "1", "PIPHONE_LIVE": "1"},
                clear=True,
            ),
            mock.patch.object(
                alarm_controls,
                "_parse_create_alarm",
                return_value=self._parsed("timer"),
            ),
            mock.patch.object(alarm_controls, "_save_new_alarm") as save,
            mock.patch.object(
                alarm_controls,
                "_schedule_alarm_fire",
                return_value={"id": "job-1"},
            ) as schedule,
            mock.patch.object(alarm_controls, "_update_alarm") as update,
        ):
            result = alarm_controls.handle_alarm_controls(tl="set a timer for 10 minutes")

        self.assertIn("timer set", result.lower())
        save.assert_called_once()
        schedule.assert_called_once()
        update.assert_called_once()


if __name__ == "__main__":
    unittest.main()

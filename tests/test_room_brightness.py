from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _room(defaults, *, area_id="test_room"):
    return {
        "label": "Test Room",
        "aliases": ["test room"],
        "ha_area_id": area_id,
        "defaults": defaults,
    }


class RoomBrightnessStrategyTests(unittest.TestCase):
    def _calls(self):
        calls = []

        def call(service, payload):
            calls.append((service, payload))
            return True

        return calls, call

    def test_checked_in_living_room_keeps_proxy_entity(self):
        from room_brightness import get_room_brightness_target

        self.assertEqual(
            get_room_brightness_target("living room"),
            {
                "type": "entity",
                "entity_id": "light.living_room_brightness",
                "room_id": "living_room",
            },
        )

    def test_proxy_light_receives_absolute_level(self):
        import home_registry
        from room_brightness import apply_room_brightness

        calls, call = self._calls()
        remembered = []
        rooms = {
            "test_room": _room({
                "brightness_target": {
                    "type": "entity",
                    "entity_id": "light.test_brightness_proxy",
                },
            }),
        }
        with mock.patch.dict(home_registry.ROOMS, rooms, clear=True):
            ok = apply_room_brightness(
                "test room",
                42,
                call_ha_service=call,
                remember_light=remembered.append,
            )

        self.assertTrue(ok)
        self.assertEqual(calls, [(
            "light/turn_on",
            {"entity_id": "light.test_brightness_proxy", "brightness_pct": 42},
        )])
        self.assertEqual(remembered, ["light.test_brightness_proxy"])

    def test_number_proxy_uses_number_service(self):
        import home_registry
        from room_brightness import apply_room_brightness

        calls, call = self._calls()
        rooms = {
            "test_room": _room({
                "brightness_target": {
                    "type": "entity",
                    "entity_id": "number.test_brightness",
                },
            }),
        }
        with mock.patch.dict(home_registry.ROOMS, rooms, clear=True):
            ok = apply_room_brightness("test_room", 55, call_ha_service=call)

        self.assertTrue(ok)
        self.assertEqual(calls, [(
            "number/set_value",
            {"entity_id": "number.test_brightness", "value": 55},
        )])

    def test_area_strategy_targets_configured_ha_area(self):
        import home_registry
        from room_brightness import apply_room_brightness

        calls, call = self._calls()
        rooms = {
            "test_room": _room(
                {"brightness_target": {"type": "area"}},
                area_id="ha_test_area",
            ),
        }
        with mock.patch.dict(home_registry.ROOMS, rooms, clear=True):
            ok = apply_room_brightness("test room", 60, call_ha_service=call)

        self.assertTrue(ok)
        self.assertEqual(calls, [(
            "light/turn_on",
            {"area_id": "ha_test_area", "brightness_pct": 60},
        )])

    def test_area_zero_turns_room_lights_off(self):
        import home_registry
        from room_brightness import apply_room_brightness

        calls, call = self._calls()
        rooms = {
            "test_room": _room({"brightness_target": {"type": "area"}}),
        }
        with mock.patch.dict(home_registry.ROOMS, rooms, clear=True):
            ok = apply_room_brightness("test_room", 0, call_ha_service=call)

        self.assertTrue(ok)
        self.assertEqual(calls, [("light/turn_off", {"area_id": "test_room"})])

    def test_area_relative_step_targets_room(self):
        import home_registry
        from room_brightness import apply_room_brightness_step

        calls, call = self._calls()
        rooms = {
            "test_room": _room({"brightness_target": {"type": "area"}}),
        }
        with mock.patch.dict(home_registry.ROOMS, rooms, clear=True):
            ok = apply_room_brightness_step(
                "test_room",
                -15,
                call_ha_service=call,
            )

        self.assertTrue(ok)
        self.assertEqual(calls, [(
            "light/turn_on",
            {"area_id": "test_room", "brightness_step_pct": -15},
        )])

    def test_explicit_entities_strategy_targets_only_listed_lights(self):
        import home_registry
        from room_brightness import apply_room_brightness

        calls, call = self._calls()
        rooms = {
            "test_room": _room({
                "brightness_target": {
                    "type": "entities",
                    "entity_ids": ["light.ceiling", "light.floor_lamp"],
                },
            }),
        }
        with mock.patch.dict(home_registry.ROOMS, rooms, clear=True):
            ok = apply_room_brightness("test_room", 25, call_ha_service=call)

        self.assertTrue(ok)
        self.assertEqual(calls, [(
            "light/turn_on",
            {
                "entity_id": ["light.ceiling", "light.floor_lamp"],
                "brightness_pct": 25,
            },
        )])

    def test_invalid_target_fails_closed(self):
        import home_registry
        from room_brightness import apply_room_brightness

        calls, call = self._calls()
        rooms = {
            "test_room": _room({
                "brightness_target": {
                    "type": "entities",
                    "entity_ids": ["light.ceiling", "switch.not_a_light"],
                },
                "brightness_light": "light.must_not_fallback",
            }),
        }
        with mock.patch.dict(home_registry.ROOMS, rooms, clear=True):
            ok = apply_room_brightness("test_room", 25, call_ha_service=call)

        self.assertFalse(ok)
        self.assertEqual(calls, [])

    def test_legacy_brightness_light_remains_supported(self):
        import home_registry
        from room_brightness import get_room_brightness_target

        rooms = {
            "test_room": _room({
                "brightness_number": None,
                "brightness_light": "light.legacy_proxy",
            }),
        }
        with mock.patch.dict(home_registry.ROOMS, rooms, clear=True):
            target = get_room_brightness_target("test_room")

        self.assertEqual(target["entity_id"], "light.legacy_proxy")

    def test_relative_number_strategy_uses_current_state(self):
        import home_registry
        from room_brightness import apply_room_brightness_step

        calls, call = self._calls()
        rooms = {
            "test_room": _room({
                "brightness_target": {
                    "type": "entity",
                    "entity_id": "number.test_brightness",
                },
            }),
        }
        states = [{"entity_id": "number.test_brightness", "state": "40"}]
        with mock.patch.dict(home_registry.ROOMS, rooms, clear=True):
            ok = apply_room_brightness_step(
                "test_room",
                10,
                call_ha_service=call,
                states_snapshot=states,
            )

        self.assertTrue(ok)
        self.assertEqual(calls, [(
            "number/set_value",
            {"entity_id": "number.test_brightness", "value": 50},
        )])


class RoomBrightnessParserTests(unittest.TestCase):
    def setUp(self):
        from request_context import clear_current_request_context

        clear_current_request_context()

    def tearDown(self):
        from request_context import clear_current_request_context

        clear_current_request_context()

    def _handler_kwargs(self, calls):
        def call(service, payload):
            calls.append((service, payload))
            return True

        return {
            "states_snapshot": [],
            "call_ha_service": call,
            "maybe_say": lambda text: text,
            "resolve_light_target": lambda _text: (None, False),
            "remember_light": lambda _entity_id: None,
            "get_recent_light": lambda: None,
            "entity_exists": lambda _eid, _states: False,
            "set_number_value": lambda _eid, _value: False,
            "default_brightness_number": "",
            "brightness_numbers": {},
            "light_phrase_overrides": {},
        }

    def test_global_brightness_uses_active_room_proxy(self):
        import home_registry
        from brightness_controls import handle_brightness_controls
        from request_context import RequestContext, set_current_request_context

        calls = []
        rooms = {
            "test_room": _room({
                "brightness_target": {
                    "type": "entity",
                    "entity_id": "light.test_proxy",
                },
            }),
        }
        set_current_request_context(RequestContext(
            source_id="default_piphone",
            source_room="test_room",
            effective_target_room="test_room",
        ))
        with mock.patch.dict(home_registry.ROOMS, rooms, clear=True):
            response = handle_brightness_controls(
                tl="brightness 50",
                **self._handler_kwargs(calls),
            )

        self.assertEqual(response, "Brightness 50 percent.")
        self.assertEqual(calls, [(
            "light/turn_on",
            {"entity_id": "light.test_proxy", "brightness_pct": 50},
        )])

    def test_lights_level_uses_room_area_strategy(self):
        import home_registry
        from room_lights_controls import handle_room_lights_controls

        calls = []

        def call(service, payload):
            calls.append((service, payload))
            return True

        rooms = {
            "test_room": _room(
                {"brightness_target": {"type": "area"}},
                area_id="ha_test_area",
            ),
        }
        with mock.patch.dict(home_registry.ROOMS, rooms, clear=True):
            response = handle_room_lights_controls(
                tl="test room lights 35",
                call_ha_service=call,
                maybe_say=lambda text: text,
            )

        self.assertEqual(response, "Okay.")
        self.assertEqual(calls, [(
            "light/turn_on",
            {"area_id": "ha_test_area", "brightness_pct": 35},
        )])

    def test_lights_level_uses_room_proxy_strategy(self):
        import home_registry
        from room_lights_controls import handle_room_lights_controls

        calls = []

        def call(service, payload):
            calls.append((service, payload))
            return True

        rooms = {
            "test_room": _room({
                "brightness_target": {
                    "type": "entity",
                    "entity_id": "light.test_proxy",
                },
            }),
        }
        with mock.patch.dict(home_registry.ROOMS, rooms, clear=True):
            response = handle_room_lights_controls(
                tl="test room lights 35",
                call_ha_service=call,
                maybe_say=lambda text: text,
            )

        self.assertEqual(response, "Okay.")
        self.assertEqual(calls, [(
            "light/turn_on",
            {"entity_id": "light.test_proxy", "brightness_pct": 35},
        )])

    def test_explicit_room_brightness_uses_area_strategy(self):
        import home_registry
        from brightness_controls import handle_brightness_controls

        calls = []
        rooms = {
            "test_room": _room(
                {"brightness_target": {"type": "area"}},
                area_id="ha_test_area",
            ),
        }
        with mock.patch.dict(home_registry.ROOMS, rooms, clear=True):
            response = handle_brightness_controls(
                tl="set test room brightness to 65",
                **self._handler_kwargs(calls),
            )

        self.assertEqual(response, "Setting test room to 65 percent.")
        self.assertEqual(calls, [(
            "light/turn_on",
            {"area_id": "ha_test_area", "brightness_pct": 65},
        )])

    def test_named_light_brightness_remains_direct(self):
        from brightness_controls import handle_brightness_controls

        calls = []
        kwargs = self._handler_kwargs(calls)
        kwargs["resolve_light_target"] = (
            lambda text: ("light.stair_light", False)
            if text == "stair light"
            else (None, False)
        )
        response = handle_brightness_controls(
            tl="set stair light to 20",
            **kwargs,
        )

        self.assertEqual(response, "Setting stair light to 20 percent.")
        self.assertEqual(calls, [(
            "light/turn_on",
            {"entity_id": "light.stair_light", "brightness_pct": 20},
        )])


if __name__ == "__main__":
    unittest.main()

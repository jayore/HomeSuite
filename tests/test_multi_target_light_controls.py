from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


LIGHTS = {
    "stair light": "light.stair",
    "side lamp": "light.side_lamp",
}


def _resolve_light(target: str):
    return LIGHTS.get(target), False


def _resolve_device(target: str):
    entity_id = LIGHTS.get(target)
    return (entity_id, "light") if entity_id else None


class MultiTargetLightControlTests(unittest.TestCase):
    def test_binary_action_controls_every_resolved_target(self):
        from on_off_controls import handle_on_off_controls

        service = mock.Mock(return_value=True)
        remember = mock.Mock()
        result = handle_on_off_controls(
            tl="turn off stair light and side lamp",
            call_ha_service=service,
            maybe_say=lambda text: text,
            resolve_device_entity=_resolve_device,
            remember_entity=remember,
        )

        self.assertEqual(result, "Okay.")
        self.assertEqual(
            service.call_args_list,
            [
                mock.call("light/turn_off", {"entity_id": "light.stair"}),
                mock.call("light/turn_off", {"entity_id": "light.side_lamp"}),
            ],
        )
        self.assertEqual(
            remember.call_args_list,
            [
                mock.call("light.stair", "light"),
                mock.call("light.side_lamp", "light"),
            ],
        )

    def test_binary_action_validates_the_whole_list_before_writing(self):
        from on_off_controls import handle_on_off_controls

        service = mock.Mock(return_value=True)
        result = handle_on_off_controls(
            tl="turn on stair light and imaginary lamp",
            call_ha_service=service,
            maybe_say=lambda text: text,
            resolve_device_entity=_resolve_device,
        )

        self.assertIsNone(result)
        service.assert_not_called()

    def test_toggle_controls_every_resolved_target(self):
        from on_off_controls import handle_toggle_controls

        service = mock.Mock(return_value=True)
        remember = mock.Mock()
        result = handle_toggle_controls(
            tl="toggle stair light and side lamp",
            call_ha_service=service,
            maybe_say=lambda text: text,
            resolve_device_entity=_resolve_device,
            remember_entity=remember,
        )

        self.assertEqual(result, "Okay.")
        self.assertEqual(
            service.call_args_list,
            [
                mock.call("light/toggle", {"entity_id": "light.stair"}),
                mock.call("light/toggle", {"entity_id": "light.side_lamp"}),
            ],
        )

    def test_absolute_brightness_controls_every_target(self):
        from brightness_controls import handle_brightness_controls

        service = mock.Mock(return_value=True)
        remember = mock.Mock()
        result = handle_brightness_controls(
            tl="set stair light and side lamp to 30%",
            states_snapshot=[],
            call_ha_service=service,
            maybe_say=lambda text: text,
            resolve_light_target=_resolve_light,
            remember_light=remember,
            get_recent_light=lambda: None,
        )

        self.assertEqual(result, "Okay.")
        self.assertEqual(
            service.call_args_list,
            [
                mock.call(
                    "light/turn_on",
                    {"entity_id": "light.stair", "brightness_pct": 30},
                ),
                mock.call(
                    "light/turn_on",
                    {"entity_id": "light.side_lamp", "brightness_pct": 30},
                ),
            ],
        )

    def test_relative_brightness_controls_every_target(self):
        from brightness_controls import handle_brightness_controls

        service = mock.Mock(return_value=True)
        remember = mock.Mock()
        result = handle_brightness_controls(
            tl="make stair light and side lamp dimmer",
            states_snapshot=[],
            call_ha_service=service,
            maybe_say=lambda text: text,
            resolve_light_target=_resolve_light,
            remember_light=remember,
            get_recent_light=lambda: None,
        )

        self.assertEqual(result, "Making them dimmer.")
        self.assertEqual(
            service.call_args_list,
            [
                mock.call(
                    "light/turn_on",
                    {"entity_id": "light.stair", "brightness_step_pct": -10},
                ),
                mock.call(
                    "light/turn_on",
                    {"entity_id": "light.side_lamp", "brightness_step_pct": -10},
                ),
            ],
        )

    def test_named_color_controls_every_target(self):
        from color_controls import handle_color_controls

        service = mock.Mock(return_value=True)
        result = handle_color_controls(
            tl="set stair light and side lamp to red",
            call_ha_service=service,
            maybe_say=lambda text: text,
            resolve_light_target=_resolve_light,
            remember_light=mock.Mock(),
            color_lights={},
            default_color_room="",
        )

        self.assertEqual(result, "Okay.")
        self.assertEqual(
            service.call_args_list,
            [
                mock.call(
                    "light/turn_on",
                    {"entity_id": "light.stair", "color_name": "red"},
                ),
                mock.call(
                    "light/turn_on",
                    {"entity_id": "light.side_lamp", "color_name": "red"},
                ),
            ],
        )

    def test_kelvin_controls_every_target(self):
        from kelvin_controls import handle_kelvin_controls

        turn_on = mock.Mock(return_value=True)
        result = handle_kelvin_controls(
            tl="set stair light and side lamp to 3000k",
            call_ha_service=mock.Mock(),
            maybe_say=lambda text: text,
            resolve_light_target=_resolve_light,
            remember_light=mock.Mock(),
            try_light_turn_on=turn_on,
        )

        self.assertEqual(result, "Okay.")
        self.assertEqual([call.args[0] for call in turn_on.call_args_list], [
            "light.stair",
            "light.side_lamp",
        ])

    def test_rgb_controls_every_target(self):
        from rgb_hex_controls import handle_rgb_hex_controls

        turn_on = mock.Mock(return_value=True)
        result = handle_rgb_hex_controls(
            tl="set stair light and side lamp to rgb 255 0 170",
            call_ha_service=mock.Mock(),
            maybe_say=lambda text: text,
            resolve_light_target=_resolve_light,
            remember_light=mock.Mock(),
            try_light_turn_on=turn_on,
        )

        self.assertEqual(result, "Okay.")
        self.assertEqual(
            turn_on.call_args_list,
            [
                mock.call("light.stair", [{"rgb_color": [255, 0, 170]}]),
                mock.call("light.side_lamp", [{"rgb_color": [255, 0, 170]}]),
            ],
        )


if __name__ == "__main__":
    unittest.main()

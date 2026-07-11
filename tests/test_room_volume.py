from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _room(volume_entity: str, *, audio_output: str = "media_player.test") -> dict:
    return {
        "label": "Test Room",
        "aliases": ["test room"],
        "defaults": {
            "volume_target": {
                "type": "entity",
                "entity_id": volume_entity,
            },
            "audio_output": audio_output,
        },
    }


class RoomRegistryTests(unittest.TestCase):
    def test_app_config_is_the_canonical_room_object(self):
        import app_config
        import home_registry

        self.assertIs(home_registry.ROOMS, app_config.ROOMS)
        self.assertEqual(home_registry.get_default_room_id(), "living_room")
        self.assertIs(
            home_registry.get_default_room(),
            app_config.ROOMS["living_room"],
        )
        self.assertEqual(
            home_registry.get_source_room("default_piphone"),
            "living_room",
        )

        with mock.patch.object(home_registry, "DEFAULT_ROOM", "bedroom"):
            self.assertEqual(
                home_registry.get_source_room("default_piphone"),
                "bedroom",
            )

    def test_living_room_keeps_proxy_targets(self):
        from home_registry import get_room_volume_target
        from room_brightness import get_room_brightness_target

        self.assertEqual(
            get_room_brightness_target("living room")["entity_id"],
            "light.living_room_brightness",
        )
        self.assertEqual(
            get_room_volume_target("living room")["entity_id"],
            "number.living_room_volume",
        )

    def test_spotcast_aliases_come_from_room_config(self):
        from home_registry import (
            get_room_spotcast_device_name,
            get_spotcast_device_aliases,
        )

        self.assertEqual(
            get_room_spotcast_device_name("living room"),
            "Livingroom",
        )
        self.assertEqual(
            get_spotcast_device_aliases()["sonos"],
            "Livingroom",
        )


class RoomVolumeStrategyTests(unittest.TestCase):
    def _calls(self):
        calls = []

        def call(service, payload):
            calls.append((service, payload))
            return True

        return calls, call

    def test_number_proxy_receives_absolute_volume(self):
        import home_registry
        from room_volume import apply_room_volume

        calls, call = self._calls()
        rooms = {"test_room": _room("number.test_volume")}
        with mock.patch.dict(home_registry.ROOMS, rooms, clear=True):
            ok = apply_room_volume(
                "test room",
                42,
                call_ha_service=call,
            )

        self.assertTrue(ok)
        self.assertEqual(calls, [(
            "number/set_value",
            {"entity_id": "number.test_volume", "value": 42},
        )])

    def test_media_player_receives_absolute_volume(self):
        import home_registry
        from room_volume import apply_room_volume

        calls, call = self._calls()
        rooms = {"test_room": _room("media_player.test")}
        with mock.patch.dict(home_registry.ROOMS, rooms, clear=True):
            ok = apply_room_volume(
                "test room",
                35,
                call_ha_service=call,
            )

        self.assertTrue(ok)
        self.assertEqual(calls, [(
            "media_player/volume_set",
            {"entity_id": "media_player.test", "volume_level": 0.35},
        )])

    def test_relative_number_proxy_reads_current_state(self):
        import home_registry
        from room_volume import apply_room_volume_step

        calls, call = self._calls()
        rooms = {"test_room": _room("number.test_volume")}
        states = [{"entity_id": "number.test_volume", "state": "40"}]
        with mock.patch.dict(home_registry.ROOMS, rooms, clear=True):
            ok = apply_room_volume_step(
                "test room",
                10,
                call_ha_service=call,
                states_snapshot=states,
            )

        self.assertTrue(ok)
        self.assertEqual(calls, [(
            "number/set_value",
            {"entity_id": "number.test_volume", "value": 50},
        )])

    def test_explicit_room_command_uses_configured_proxy(self):
        import home_registry
        from volume_controls import handle_volume_controls

        calls, call = self._calls()
        rooms = {"test_room": _room("number.test_volume")}
        with mock.patch.dict(home_registry.ROOMS, rooms, clear=True):
            response = handle_volume_controls(
                tl="set test room volume to 25",
                call_ha_service=call,
                maybe_say=lambda text: text,
                sonos_players={"test room": "media_player.test"},
                default_sonos_room="test room",
            )

        self.assertEqual(response, "Test Room volume 25 percent.")
        self.assertEqual(calls, [(
            "number/set_value",
            {"entity_id": "number.test_volume", "value": 25},
        )])

    def test_roomless_command_can_use_helper_without_sonos_mapping(self):
        import home_registry
        from volume_controls import handle_volume_controls

        calls, call = self._calls()
        rooms = {"test_room": _room("number.test_volume", audio_output="")}
        with mock.patch.dict(home_registry.ROOMS, rooms, clear=True):
            response = handle_volume_controls(
                tl="volume 30",
                call_ha_service=call,
                maybe_say=lambda text: text,
                sonos_players={},
                default_sonos_room=None,
                default_volume_room="test room",
            )

        self.assertEqual(response, "Volume 30 percent.")
        self.assertEqual(calls, [(
            "number/set_value",
            {"entity_id": "number.test_volume", "value": 30},
        )])

    def test_non_room_audio_alias_does_not_fall_back_to_default_room(self):
        import home_registry
        from volume_controls import handle_volume_controls

        calls, call = self._calls()
        rooms = {"test_room": _room("number.test_volume")}
        with mock.patch.dict(home_registry.ROOMS, rooms, clear=True):
            response = handle_volume_controls(
                tl="set bookshelf volume to 20",
                call_ha_service=call,
                maybe_say=lambda text: text,
                sonos_players={
                    "test room": "media_player.test",
                    "bookshelf": "media_player.bookshelf",
                },
                default_sonos_room="test room",
            )

        self.assertEqual(response, "Bookshelf volume 20 percent.")
        self.assertEqual(calls, [(
            "media_player/volume_set",
            {"entity_id": "media_player.bookshelf", "volume_level": 0.2},
        )])

    def test_explicit_none_disables_room_volume(self):
        import home_registry

        rooms = {
            "test_room": {
                "label": "Test Room",
                "aliases": ["test room"],
                "defaults": {
                    "volume_target": None,
                    "audio_output": "media_player.must_not_fallback",
                },
            },
        }
        with mock.patch.dict(home_registry.ROOMS, rooms, clear=True):
            target = home_registry.get_room_volume_target("test room")

        self.assertIsNone(target)


if __name__ == "__main__":
    unittest.main()

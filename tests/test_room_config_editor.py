from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from config_editor import ConfigEditError
from room_config_editor import RoomConfigEditor


def _room(
    label: str = "Living Room",
    *,
    area: str = "living_room",
    brightness=None,
) -> dict:
    if brightness is None:
        brightness = {
            "type": "entity",
            "entity_id": "light.living_room_brightness",
        }
    return {
        "label": label,
        "ha_area_id": area,
        "aliases": [label.lower()],
        "defaults": {
            "brightness_target": brightness,
            "color_light": "light.living_room_color",
            "volume_target": {
                "type": "entity",
                "entity_id": "number.living_room_volume",
            },
            "audio_output": "media_player.living_room",
            "announcements": "media_player.living_room",
            "spotcast_device_name": "Livingroom",
            "tv": "media_player.living_room_tv",
            "tv_remote": "remote.living_room_tv",
            "tv_on_scene": "scene.tv_on",
            "plex_client_name": "Apple TV",
            "plex_launch_script": "script.launch_plex",
            "future_default": {"keep": True},
        },
        "media_players": [
            {"entity": "media_player.living_room", "label": "Living Room"},
        ],
        "audio_outputs": ["media_player.living_room"],
        "audio_aliases": {"speaker": "media_player.living_room"},
        "focus_participants": ["media_player.living_room"],
        "scenes": [{"label": "Bright", "command": "living room bright"}],
        "devices": [{"label": "Lamp", "entity": "light.floor_lamp"}],
        "future_room_field": ["keep-me"],
    }


class RoomConfigEditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.rooms = {"living_room": _room()}
        self.app_config = SimpleNamespace(
            ROOMS=self.rooms,
            DEFAULT_ROOM="living_room",
            LOCAL_PREFS_KEYS=[],
            DEPLOYMENT_CONFIG_KEYS=["DEFAULT_ROOM", "ROOMS"],
        )
        self.private_config = SimpleNamespace(HA_URL="", HA_TOKEN="")

    def tearDown(self):
        self.tmp.cleanup()

    def editor(self) -> RoomConfigEditor:
        return RoomConfigEditor(
            root=self.root,
            app_config=self.app_config,
            private_config=self.private_config,
            backup_root=self.root / "backups",
        )

    def write_deployment(self, rooms=None) -> None:
        payload = self.rooms if rooms is None else rooms
        (self.root / "deployment_config.py").write_text(
            "# shared topology\n"
            "DEFAULT_ROOM = 'living_room'\n"
            f"ROOMS = {payload!r}\n"
            "WEATHER_ENTITY_ID = 'weather.home'\n",
            encoding="utf-8",
        )

    def test_public_state_reads_deployment_literal_and_reports_source(self):
        deployed = {"living_room": _room(label="Deployed Living Room")}
        self.write_deployment(deployed)

        state = self.editor().public_state()

        self.assertEqual(state["rooms"]["living_room"]["label"], "Deployed Living Room")
        self.assertEqual(state["default_room"], "living_room")
        self.assertEqual(state["default_source"], "deployment")
        self.assertTrue(state["managed_file_exists"])
        self.assertEqual(len(state["revision"]), 64)

    def test_preview_preserves_proxy_and_unknown_fields(self):
        self.write_deployment()
        proposed = {"living_room": _room()}
        proposed["living_room"]["aliases"].append("lounge")

        preview = self.editor().preview(proposed)
        saved = preview["rooms"]["living_room"]

        self.assertEqual(preview["change_count"], 1)
        self.assertEqual(
            saved["defaults"]["brightness_target"],
            {"type": "entity", "entity_id": "light.living_room_brightness"},
        )
        self.assertEqual(saved["defaults"]["future_default"], {"keep": True})
        self.assertEqual(saved["future_room_field"], ["keep-me"])

    def test_duplicate_spoken_room_name_is_rejected(self):
        proposed = {
            "living_room": _room(),
            "lounge": _room(label="Lounge", area="lounge"),
        }
        proposed["lounge"]["aliases"] = ["living room"]

        with self.assertRaisesRegex(ConfigEditError, "shared by"):
            self.editor().preview(proposed)

    def test_invalid_brightness_domain_is_rejected(self):
        proposed = {
            "living_room": _room(
                brightness={"type": "entity", "entity_id": "switch.not_a_brightness_proxy"}
            )
        }

        with self.assertRaisesRegex(ConfigEditError, "brightness entity.*domains"):
            self.editor().preview(proposed)

    def test_area_brightness_requires_an_area(self):
        proposed = {
            "living_room": _room(area="", brightness={"type": "area"}),
        }

        with self.assertRaisesRegex(ConfigEditError, "needs a Home Assistant area"):
            self.editor().preview(proposed)

    def test_current_default_room_cannot_be_removed(self):
        proposed = {"office": _room(label="Office", area="office")}
        proposed["office"]["aliases"] = ["office"]

        with self.assertRaisesRegex(ConfigEditError, "cannot be removed"):
            self.editor().preview(proposed)

    def test_apply_rewrites_only_rooms_and_creates_backup(self):
        self.write_deployment()
        editor = self.editor()
        proposed = {"living_room": _room()}
        proposed["living_room"]["label"] = "Main Living Room"
        preview = editor.preview(proposed)

        result = editor.apply(proposed, preview["revision"])
        source = (self.root / "deployment_config.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        assignments = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        }

        self.assertTrue(result["applied"])
        self.assertEqual(result["written_files"], ["deployment_config.py"])
        self.assertEqual(assignments["DEFAULT_ROOM"], "living_room")
        self.assertEqual(assignments["WEATHER_ENTITY_ID"], "weather.home")
        self.assertEqual(assignments["ROOMS"]["living_room"]["label"], "Main Living Room")
        self.assertTrue((Path(result["backup_dir"]) / "deployment_config.py").is_file())

    def test_apply_rejects_stale_revision(self):
        self.write_deployment()
        editor = self.editor()
        proposed = {"living_room": _room(label="Main Living Room")}
        preview = editor.preview(proposed)
        path = self.root / "deployment_config.py"
        path.write_text(path.read_text(encoding="utf-8") + "# external edit\n", encoding="utf-8")

        with self.assertRaisesRegex(ConfigEditError, "changed after this review"):
            editor.apply(proposed, preview["revision"])

    def test_absent_deployment_file_is_created_without_clobbering_effective_rooms(self):
        editor = self.editor()
        proposed = {"living_room": _room(label="Updated Living Room")}
        preview = editor.preview(proposed)

        result = editor.apply(proposed, preview["revision"])

        self.assertTrue(result["applied"])
        self.assertIn("ROOMS =", (self.root / "deployment_config.py").read_text(encoding="utf-8"))
        self.assertTrue(
            (Path(result["backup_dir"]) / "deployment_config.py.absent").is_file()
        )

    def test_catalog_falls_back_cleanly_without_home_assistant(self):
        self.assertEqual(
            self.editor().catalog(),
            {
                "available": False,
                "reason": "Home Assistant is not configured on this node.",
                "areas": [],
                "entities": [],
            },
        )

    def test_catalog_returns_only_non_disabled_registry_entities(self):
        self.private_config = SimpleNamespace(HA_URL="http://ha.local:8123", HA_TOKEN="token")
        registry = {
            "areas": [{"area_id": "living_room", "name": "Living Room"}],
            "devices": [{"id": "device-1", "area_id": "living_room"}],
            "entities": [
                {"entity_id": "light.floor_lamp", "device_id": "device-1"},
                {"entity_id": "light.disabled", "disabled_by": "user"},
            ],
        }
        states = [
            {"entity_id": "light.floor_lamp", "attributes": {"friendly_name": "Floor Lamp"}}
        ]
        fake_ha_client = SimpleNamespace(
            configure_ha=mock.Mock(),
            ha_get_registry_snapshot=mock.Mock(return_value=registry),
            ha_get_states=mock.Mock(return_value=states),
        )
        with mock.patch.dict(sys.modules, {"ha_client": fake_ha_client}):
            catalog = self.editor().catalog(force=True)

        fake_ha_client.configure_ha.assert_called_once_with(
            ha_url="http://ha.local:8123",
            ha_token="token",
        )
        self.assertTrue(catalog["available"])
        self.assertEqual(catalog["areas"], [{"id": "living_room", "label": "Living Room"}])
        self.assertEqual(
            catalog["entities"],
            [
                {
                    "id": "light.floor_lamp",
                    "label": "Floor Lamp",
                    "domain": "light",
                    "area_id": "living_room",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()

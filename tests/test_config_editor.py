from __future__ import annotations

import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

from config_editor import ConfigEditError, ConfigEditor


class ConfigEditorTests(unittest.TestCase):
    def make_editor(self, root: Path) -> ConfigEditor:
        (root / "app_config.py").write_text(
            'WAKEWORD_MODEL = "default_model"\n'
            'DEFAULT_ROOM: str = "living_room"\n'
            'WAKEWORD_ENABLED = False\n'
            'PTT_ENABLED = False\n'
            'PTT_GPIO_PIN = 11\n'
            'PTT_LISTEN_LEVEL = "low"\n'
            'PTT_END_BEHAVIOR = "cancel"\n'
            'PHYSICAL_BUTTONS_ENABLED = False\n'
            'PHYSICAL_BUTTON_PINS = {}\n'
            'PHYSICAL_BUTTON_ACTIONS = {}\n'
            'COMMAND_PROCESSING_MODE = "local"\n'
            'SATELLITE_BRAIN_URL = ""\n'
            'UNIFIED_SERVER_PORT = 8765\n',
            encoding="utf-8",
        )
        (root / "deployment_config.py").write_text(
            'DEFAULT_ROOM = "office"\n',
            encoding="utf-8",
        )
        if not (root / "local_prefs.py").exists():
            (root / "local_prefs.py").write_text(
                '# keep this comment\nWAKEWORD_MODEL = "old_model"  # keep inline\n',
                encoding="utf-8",
            )
        if not (root / "private_config.py").exists():
            (root / "private_config.py").write_text(
                'HA_URL = "http://ha.local:8123"\n'
                'HA_TOKEN = "old-ha-token"\n'
                'OPENAI_API_KEY = ""\n'
                'HOMESUITE_HTTP_API_KEY = "api-key"\n'
                'SATELLITE_BRAIN_API_KEY = ""\n'
                'HOMESUITE_CONSOLE_KEY = ""\n',
                encoding="utf-8",
            )
        app_config = SimpleNamespace(
            WAKEWORD_MODEL="old_model",
            DEFAULT_ROOM="office",
            WAKEWORD_ENABLED=False,
            PTT_ENABLED=False,
            PTT_GPIO_PIN=11,
            PTT_LISTEN_LEVEL="low",
            PTT_END_BEHAVIOR="cancel",
            PHYSICAL_BUTTONS_ENABLED=False,
            PHYSICAL_BUTTON_PINS={},
            PHYSICAL_BUTTON_ACTIONS={},
            COMMAND_PROCESSING_MODE="local",
            SATELLITE_BRAIN_URL="",
            UNIFIED_SERVER_PORT=8765,
            CONSOLE_PORT=8766,
            ROOMS={
                "living_room": {"label": "Living room"},
                "office": {"label": "Office"},
            },
            DEPLOYMENT_CONFIG_KEYS=["DEFAULT_ROOM"],
            LOCAL_PREFS_KEYS=["WAKEWORD_MODEL"],
        )
        private_config = SimpleNamespace(
            HA_URL="http://ha.local:8123",
            HA_TOKEN="old-ha-token",
            OPENAI_API_KEY="",
            HOMESUITE_HTTP_API_KEY="api-key",
            SATELLITE_BRAIN_API_KEY="",
            HOMESUITE_CONSOLE_KEY="",
        )
        return ConfigEditor(
            root=root,
            app_config=app_config,
            private_config=private_config,
        )

    def test_public_schema_redacts_secrets_and_includes_guidance(self):
        with tempfile.TemporaryDirectory() as directory:
            editor = self.make_editor(Path(directory))
            state = editor.public_state()

        fields = {field["key"]: field for field in state["fields"]}
        self.assertEqual(state["schema_version"], 2)
        self.assertTrue(all(section["surface"] for section in state["sections"]))
        self.assertIsNone(fields["HA_TOKEN"]["value"])
        self.assertTrue(fields["HA_TOKEN"]["configured"])
        self.assertIn("Long-Lived Access Token", fields["HA_TOKEN"]["help_text"])
        self.assertEqual(fields["WAKEWORD_MODEL"]["placeholder"], "hal_v2")
        self.assertEqual(fields["WAKEWORD_MODEL"]["source"], "device")
        self.assertEqual(fields["WAKEWORD_MODEL"]["surface"], "managed")
        self.assertEqual(fields["PTT_GPIO_PIN"]["surface"], "controls")
        self.assertEqual(fields["DEFAULT_ROOM"]["source"], "deployment")
        self.assertIn(
            {"value": "office", "label": "Office"},
            fields["DEFAULT_ROOM"]["choices"],
        )
        self.assertIn("inventory", state)
        self.assertNotIn("old-ha-token", repr(state["inventory"]))

    def test_authenticated_edit_state_can_include_existing_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            editor = self.make_editor(Path(directory))
            state = editor.public_state(include_secrets=True)

        fields = {field["key"]: field for field in state["fields"]}
        self.assertEqual(fields["HA_TOKEN"]["value"], "old-ha-token")
        self.assertEqual(fields["HOMESUITE_HTTP_API_KEY"]["value"], "api-key")

    def test_authenticated_edit_state_uses_legacy_api_key_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            editor = self.make_editor(root)
            private_source = (root / "private_config.py").read_text(encoding="utf-8")
            (root / "private_config.py").write_text(
                private_source.replace(
                    'HOMESUITE_HTTP_API_KEY = "api-key"',
                    'HOMESUITE_HTTP_API_KEY = ""',
                ),
                encoding="utf-8",
            )
            editor.private_config.HOMESUITE_HTTP_API_KEY = ""
            editor.private_config.PIPHONE_HTTP_API_KEY = "legacy-api-key"

            redacted = editor.public_state()
            state = editor.public_state(include_secrets=True)

        redacted_field = next(
            item for item in redacted["fields"] if item["key"] == "HOMESUITE_HTTP_API_KEY"
        )
        field = next(
            item for item in state["fields"] if item["key"] == "HOMESUITE_HTTP_API_KEY"
        )
        self.assertIsNone(redacted_field["value"])
        self.assertNotIn("legacy-api-key", repr(redacted))
        self.assertTrue(field["configured"])
        self.assertEqual(field["value"], "legacy-api-key")

    def test_preview_and_apply_preserve_source_and_redact_secret_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            editor = self.make_editor(root)
            changes = [
                {"key": "WAKEWORD_MODEL", "action": "set", "value": "new_model"},
                {"key": "WAKEWORD_ENABLED", "action": "set", "value": True},
                {"key": "HA_TOKEN", "action": "set", "value": "new-secret-token"},
            ]
            preview = editor.preview(changes)
            serialized = repr(preview)
            self.assertNotIn("old-ha-token", serialized)
            self.assertNotIn("new-secret-token", serialized)
            result = editor.apply(changes, preview["revisions"])

            local_source = (root / "local_prefs.py").read_text(encoding="utf-8")
            private_source = (root / "private_config.py").read_text(encoding="utf-8")
            backup_dir = Path(result["backup_dir"])
            backup_private = (backup_dir / "private_config.py").read_text(encoding="utf-8")
            backup_mode = stat.S_IMODE(backup_dir.stat().st_mode)

        self.assertTrue(result["applied"])
        self.assertIn('WAKEWORD_MODEL = \'new_model\'  # keep inline', local_source)
        self.assertIn("# keep this comment", local_source)
        self.assertIn("WAKEWORD_ENABLED = True", local_source)
        self.assertIn("new-secret-token", private_source)
        self.assertIn("old-ha-token", backup_private)
        self.assertEqual(backup_mode, 0o700)
        self.assertEqual(set(result["written_files"]), {"local_prefs.py", "private_config.py"})

    def test_reset_removes_device_override_and_reports_inherited_value(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            editor = self.make_editor(root)
            changes = [{"key": "DEFAULT_ROOM", "action": "set", "value": "living_room"}]
            first = editor.preview(changes)
            editor.apply(changes, first["revisions"])

            reset = [{"key": "DEFAULT_ROOM", "action": "reset"}]
            preview = editor.preview(reset)
            self.assertEqual(preview["changes"][0]["after"], "office (inherited)")
            editor.apply(reset, preview["revisions"])
            source = (root / "local_prefs.py").read_text(encoding="utf-8")

        self.assertNotIn("DEFAULT_ROOM", source)

    def test_stale_revision_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            editor = self.make_editor(root)
            changes = [{"key": "WAKEWORD_MODEL", "action": "set", "value": "new_model"}]
            preview = editor.preview(changes)
            original = (root / "local_prefs.py").read_text(encoding="utf-8")
            (root / "local_prefs.py").write_text(original + "# external edit\n", encoding="utf-8")

            with self.assertRaisesRegex(ConfigEditError, "changed after this review"):
                editor.apply(changes, preview["revisions"])

            self.assertNotIn("new_model", (root / "local_prefs.py").read_text(encoding="utf-8"))

    def test_apply_follows_config_symlink_instead_of_replacing_it(self):
        with tempfile.TemporaryDirectory() as directory, tempfile.TemporaryDirectory() as target_directory:
            root = Path(directory)
            target = Path(target_directory) / "local_prefs.py"
            target.write_text('WAKEWORD_MODEL = "linked_model"\n', encoding="utf-8")
            (root / "local_prefs.py").symlink_to(target)
            editor = self.make_editor(root)
            changes = [{"key": "WAKEWORD_MODEL", "action": "set", "value": "updated_model"}]
            preview = editor.preview(changes)
            editor.apply(changes, preview["revisions"])

            self.assertTrue((root / "local_prefs.py").is_symlink())
            self.assertIn("updated_model", target.read_text(encoding="utf-8"))

    def test_rejects_port_collision_and_required_secret_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            editor = self.make_editor(Path(directory))
            with self.assertRaisesRegex(ConfigEditError, "cannot use the same port"):
                editor.preview(
                    [{"key": "UNIFIED_SERVER_PORT", "action": "set", "value": 8766}]
                )
            with self.assertRaisesRegex(ConfigEditError, "cannot be cleared"):
                editor.preview([{"key": "HA_TOKEN", "action": "clear"}])

    def test_satellite_mode_requires_a_brain_url_and_accepts_shared_api_key(self):
        with tempfile.TemporaryDirectory() as directory:
            editor = self.make_editor(Path(directory))
            with self.assertRaisesRegex(ConfigEditError, "Enter a brain URL"):
                editor.preview(
                    [{"key": "COMMAND_PROCESSING_MODE", "action": "set", "value": "satellite"}]
                )

            preview = editor.preview(
                [
                    {"key": "COMMAND_PROCESSING_MODE", "action": "set", "value": "satellite"},
                    {
                        "key": "SATELLITE_BRAIN_URL",
                        "action": "set",
                        "value": "http://piphone.local:8765",
                    },
                ]
            )

        self.assertEqual(len(preview["changes"]), 2)

    def test_gpio_button_maps_are_validated_and_written_as_structured_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            editor = self.make_editor(root)
            changes = [
                {
                    "key": "PHYSICAL_BUTTON_PINS",
                    "action": "set",
                    "value": '{"1": 2, "2": 3}',
                },
                {
                    "key": "PHYSICAL_BUTTON_ACTIONS",
                    "action": "set",
                    "value": {"1": {"press": "turn on the office light"}},
                },
                {"key": "PHYSICAL_BUTTONS_ENABLED", "action": "set", "value": True},
            ]

            preview = editor.preview(changes)
            editor.apply(changes, preview["revisions"])
            source = (root / "local_prefs.py").read_text(encoding="utf-8")

        self.assertIn("PHYSICAL_BUTTON_PINS", source)
        self.assertIn("PHYSICAL_BUTTON_ACTIONS", source)
        self.assertIn("{1: 2, 2: 3}", source)
        self.assertIn("turn on the office light", source)

    def test_gpio_validation_rejects_ptt_pin_conflicts_and_unmapped_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            editor = self.make_editor(Path(directory))
            with self.assertRaisesRegex(ConfigEditError, "cannot be shared"):
                editor.preview(
                    [
                        {"key": "PTT_ENABLED", "action": "set", "value": True},
                        {"key": "PHYSICAL_BUTTONS_ENABLED", "action": "set", "value": True},
                        {"key": "PHYSICAL_BUTTON_PINS", "action": "set", "value": {"1": 11}},
                    ]
                )
            with self.assertRaisesRegex(ConfigEditError, "do not have assigned GPIO pins"):
                editor.preview(
                    [
                        {
                            "key": "PHYSICAL_BUTTON_ACTIONS",
                            "action": "set",
                            "value": {"2": {"press": "turn on the office light"}},
                        }
                    ]
                )

    def test_gpio_validation_rejects_dead_gestures_and_empty_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            editor = self.make_editor(Path(directory))
            with self.assertRaisesRegex(ConfigEditError, "unsupported gesture"):
                editor.preview(
                    [
                        {"key": "PHYSICAL_BUTTON_PINS", "action": "set", "value": {"1": 2}},
                        {
                            "key": "PHYSICAL_BUTTON_ACTIONS",
                            "action": "set",
                            "value": {"1": {"triple_press": "turn on the office light"}},
                        },
                    ]
                )
            with self.assertRaisesRegex(ConfigEditError, "needs at least one command"):
                editor.preview(
                    [
                        {"key": "PHYSICAL_BUTTON_PINS", "action": "set", "value": {"1": 2}},
                        {
                            "key": "PHYSICAL_BUTTON_ACTIONS",
                            "action": "set",
                            "value": {"1": {"press": ""}},
                        },
                    ]
                )

    def test_gpio_advanced_hold_action_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            editor = self.make_editor(root)
            changes = [
                {"key": "PHYSICAL_BUTTON_PINS", "action": "set", "value": {"1": 2}},
                {
                    "key": "PHYSICAL_BUTTON_ACTIONS",
                    "action": "set",
                    "value": {
                        "1": {
                            "long_press": {
                                "commands": ["volume up", "volume up"],
                                "repeat_while_held": True,
                                "repeat_interval_ms": 250,
                                "max_repeats": 12,
                            }
                        }
                    },
                },
            ]

            preview = editor.preview(changes)
            editor.apply(changes, preview["revisions"])
            source = (root / "local_prefs.py").read_text(encoding="utf-8")

        self.assertIn("'repeat_while_held': True", source)
        self.assertIn("'repeat_interval_ms': 250", source)
        self.assertIn("'max_repeats': 12", source)


if __name__ == "__main__":
    unittest.main()

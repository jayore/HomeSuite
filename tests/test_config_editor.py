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
            'WAKEWORD_THRESHOLD = 0.45\n'
            'WAKEWORD_DEACTIVATION_THRESHOLD = 0.20\n'
            'WAKEWORD_NEAR_MISS_MIN_SCORE = 0.25\n'
            'WAKEWORD_ASYNC_TTS_ENABLED = False\n'
            'WAKEWORD_BARGE_IN_ENABLED = False\n'
            'WAKEWORD_STT_MODE = "realtime_stream"\n'
            'WAKEWORD_USE_STREAMING_STT = True\n'
            'WAKEWORD_STREAM_ENDPOINT_WINDOW_MS = 700\n'
            'WAKEWORD_STREAM_ENDPOINT_TRAILING_SILENCE_MS = 80\n'
            'WAKEWORD_CHIME_SOUND_FILE = "assets/Blow.mp3"\n'
            'YOUTUBE_REEL_REFRESH_ENABLED = True\n'
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
            'DEFAULT_ROOM = "office"\n'
            'ASSISTANT_PROFILE = {"preferred_name": "", "locale": "", "units": "", "notes": []}\n'
            'CALENDARS = {"personal": {"entity_id": "calendar.personal", "label": "Personal", "writable": True}, "work": {"entity_id": "calendar.work", "label": "Work"}}\n'
            'DEFAULT_CALENDAR = "personal"\n'
            'CALENDAR_READS_ENABLED = True\n'
            'CALENDAR_WRITES_ENABLED = False\n'
            'CALENDAR_CONFIRM_WRITES = True\n'
            'CALENDAR_DEFAULT_EVENT_DURATION_MINUTES = 60\n',
            encoding="utf-8",
        )
        if not (root / "local_prefs.py").exists():
            (root / "local_prefs.py").write_text(
                '# keep this comment\nWAKEWORD_MODEL = "old_model"  # keep inline\nYOUTUBE_REEL_REFRESH_ENABLED = False\n',
                encoding="utf-8",
            )
        assets = root / "assets"
        assets.mkdir(exist_ok=True)
        (assets / "Blow.mp3").write_bytes(b"test-audio")
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
            WAKEWORD_THRESHOLD=0.45,
            WAKEWORD_DEACTIVATION_THRESHOLD=0.20,
            WAKEWORD_NEAR_MISS_MIN_SCORE=0.25,
            WAKEWORD_ASYNC_TTS_ENABLED=False,
            WAKEWORD_BARGE_IN_ENABLED=False,
            WAKEWORD_STT_MODE="realtime_stream",
            WAKEWORD_USE_STREAMING_STT=True,
            WAKEWORD_STREAM_ENDPOINT_WINDOW_MS=700,
            WAKEWORD_STREAM_ENDPOINT_TRAILING_SILENCE_MS=80,
            WAKEWORD_CHIME_SOUND_FILE="assets/Blow.mp3",
            YOUTUBE_REEL_REFRESH_ENABLED=False,
            PTT_ENABLED=False,
            PTT_GPIO_PIN=11,
            PTT_LISTEN_LEVEL="low",
            PTT_END_BEHAVIOR="cancel",
            PHYSICAL_BUTTONS_ENABLED=False,
            PHYSICAL_BUTTON_PINS={},
            PHYSICAL_BUTTON_ACTIONS={},
            COMMAND_PROCESSING_MODE="local",
            SATELLITE_BRAIN_URL="",
            ASSISTANT_PROFILE={"preferred_name": "", "locale": "", "units": "", "notes": []},
            CALENDARS={
                "personal": {"entity_id": "calendar.personal", "label": "Personal", "writable": True},
                "work": {"entity_id": "calendar.work", "label": "Work"},
            },
            DEFAULT_CALENDAR="personal",
            CALENDAR_READS_ENABLED=True,
            CALENDAR_WRITES_ENABLED=False,
            CALENDAR_CONFIRM_WRITES=True,
            CALENDAR_DEFAULT_EVENT_DURATION_MINUTES=60,
            UNIFIED_SERVER_PORT=8765,
            CONSOLE_PORT=8766,
            ROOMS={
                "living_room": {"label": "Living room"},
                "office": {"label": "Office"},
            },
            DEPLOYMENT_CONFIG_KEYS=[
                "DEFAULT_ROOM",
                "ASSISTANT_PROFILE",
                "CALENDARS",
                "DEFAULT_CALENDAR",
                "CALENDAR_READS_ENABLED",
                "CALENDAR_WRITES_ENABLED",
                "CALENDAR_CONFIRM_WRITES",
                "CALENDAR_DEFAULT_EVENT_DURATION_MINUTES",
            ],
            LOCAL_PREFS_KEYS=["WAKEWORD_MODEL", "YOUTUBE_REEL_REFRESH_ENABLED"],
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
        self.assertEqual(fields["WAKEWORD_CHIME"]["surface"], "wakeword")
        self.assertIn(
            {"value": "assets/Blow.mp3", "label": "Blow"},
            fields["WAKEWORD_CHIME_SOUND_FILE"]["choices"],
        )
        self.assertEqual(fields["YOUTUBE_REEL_REFRESH_ENABLED"]["surface"], "integrations")
        self.assertEqual(fields["YOUTUBE_REEL_REFRESH_ENABLED"]["target_file"], "local_prefs.py")
        self.assertTrue(fields["YOUTUBE_REEL_REFRESH_ENABLED"]["can_reset"])
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

    def test_deployment_settings_are_exposed_and_written_atomically(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            editor = self.make_editor(root)
            state = editor.public_state()
            fields = {field["key"]: field for field in state["fields"]}

            self.assertEqual(fields["ASSISTANT_PROFILE"]["target_file"], "deployment_config.py")
            self.assertIn(
                {"value": "personal", "label": "Personal"},
                fields["DEFAULT_CALENDAR"]["choices"],
            )
            changes = [
                {"key": "CALENDAR_WRITES_ENABLED", "action": "set", "value": True},
                {"key": "CALENDAR_DEFAULT_EVENT_DURATION_MINUTES", "action": "set", "value": 45},
            ]
            preview = editor.preview(changes)
            result = editor.apply(changes, preview["revisions"])
            source = (root / "deployment_config.py").read_text(encoding="utf-8")

        self.assertEqual(result["written_files"], ["deployment_config.py"])
        self.assertIn("CALENDAR_WRITES_ENABLED = True", source)
        self.assertIn("CALENDAR_DEFAULT_EVENT_DURATION_MINUTES = 45", source)

    def test_deployment_structures_are_validated_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            editor = self.make_editor(Path(directory))
            with self.assertRaisesRegex(ConfigEditError, r"calendar\.\* entity ID"):
                editor.preview(
                    [{"key": "CALENDARS", "action": "set", "value": {"personal": {"entity_id": "sensor.calendar"}}}]
                )
            with self.assertRaisesRegex(ConfigEditError, "Mark at least one"):
                editor.preview(
                    [
                        {"key": "CALENDARS", "action": "set", "value": {"personal": {"entity_id": "calendar.personal"}}},
                        {"key": "CALENDAR_WRITES_ENABLED", "action": "set", "value": True},
                    ]
                )
            with self.assertRaisesRegex(ConfigEditError, "notes must be a list"):
                editor.preview(
                    [{"key": "ASSISTANT_PROFILE", "action": "set", "value": {"notes": "not a list"}}]
                )

    def test_dynamic_calendar_choice_can_be_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            editor = self.make_editor(Path(directory))
            preview = editor.preview(
                [{"key": "DEFAULT_CALENDAR", "action": "set", "value": "work"}]
            )

        self.assertEqual(preview["changes"][0]["after"], "work")

    def test_wakeword_relationships_are_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            editor = self.make_editor(Path(directory))
            with self.assertRaisesRegex(ConfigEditError, "Rearm score"):
                editor.preview(
                    [{"key": "WAKEWORD_DEACTIVATION_THRESHOLD", "action": "set", "value": 0.5}]
                )
            with self.assertRaisesRegex(ConfigEditError, "background spoken responses"):
                editor.preview(
                    [{"key": "WAKEWORD_BARGE_IN_ENABLED", "action": "set", "value": True}]
                )
            with self.assertRaisesRegex(ConfigEditError, "Realtime transcription"):
                editor.preview(
                    [{"key": "WAKEWORD_STT_MODE", "action": "set", "value": "whisper"}]
                )
            with self.assertRaisesRegex(ConfigEditError, "Trailing silence"):
                editor.preview(
                    [{"key": "WAKEWORD_STREAM_ENDPOINT_TRAILING_SILENCE_MS", "action": "set", "value": 800}]
                )

    def test_valid_wakeword_setting_pairs_can_be_previewed_together(self):
        with tempfile.TemporaryDirectory() as directory:
            editor = self.make_editor(Path(directory))
            barge_in = editor.preview(
                [
                    {"key": "WAKEWORD_ASYNC_TTS_ENABLED", "action": "set", "value": True},
                    {"key": "WAKEWORD_BARGE_IN_ENABLED", "action": "set", "value": True},
                ]
            )
            recorded_audio = editor.preview(
                [
                    {"key": "WAKEWORD_USE_STREAMING_STT", "action": "set", "value": False},
                    {"key": "WAKEWORD_STT_MODE", "action": "set", "value": "whisper"},
                ]
            )

        self.assertEqual(len(barge_in["changes"]), 2)
        self.assertEqual(len(recorded_audio["changes"]), 2)

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
            with self.assertRaisesRegex(ConfigEditError, "Enter the other Home Suite URL"):
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

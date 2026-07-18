from __future__ import annotations

from pathlib import Path
import unittest

from config_schema import (
    DEPLOYMENT_CONFIG_FILE,
    EDITABLE_FIELDS,
    LOCAL_PREFS_FILE,
    PRIVATE_CONFIG_FILE,
)


ROOT = Path(__file__).resolve().parents[1]

ACTIVE_WAKEWORD_OVERRIDE_KEYS = {
    "WAKEWORD_ASYNC_TTS_ENABLED",
    "WAKEWORD_BARGE_IN_ENABLED",
    "WAKEWORD_BARGE_IN_THRESHOLD",
    "WAKEWORD_CHIME",
    "WAKEWORD_CHIME_SOUND_FILE",
    "WAKEWORD_CHIME_VOLUME",
    "WAKEWORD_ONE_BREATH_MAX_SPEECH_START_MS",
    "WAKEWORD_PAUSE_MEDIA_DURING_CAPTURE",
    "WAKEWORD_REARM_SFX_DRAIN_MAX_SEC",
    "WAKEWORD_STREAM_CUE_GUARD_MS",
    "WAKEWORD_STREAM_ENDPOINT_MIN_SILENCE_RATIO",
    "WAKEWORD_STREAM_ENDPOINT_TRAILING_SILENCE_MS",
    "WAKEWORD_STREAM_ENDPOINT_WINDOW_MS",
    "WAKEWORD_STREAM_MAX_SECONDS",
    "WAKEWORD_STREAM_POST_MEDIA_PAUSE_DRAIN_MS",
    "WAKEWORD_STREAM_PRETRIGGER_INCLUDE_MS",
    "WAKEWORD_STREAM_VAD_ARM_DELAY_MS",
    "WAKEWORD_STT_MODE",
    "WAKEWORD_SUPPRESS_DURING_SFX",
    "WAKEWORD_USE_STREAMING_STT",
}


class ConfigSchemaTests(unittest.TestCase):
    def test_schema_keys_are_unique_and_targets_are_allowlisted(self):
        keys = [field.key for field in EDITABLE_FIELDS]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(keys)
        self.assertNotIn("SOURCE_ID", keys)
        self.assertNotIn("HANDSET_PRESENT", keys)
        self.assertNotIn("HANDSET_GPIO_PIN", keys)
        self.assertIn("PTT_GPIO_PIN", keys)
        self.assertIn("PTT_LISTEN_LEVEL", keys)
        self.assertIn("PTT_END_BEHAVIOR", keys)
        self.assertIn("PHYSICAL_BUTTON_PINS", keys)
        self.assertIn("PHYSICAL_BUTTON_ACTIONS", keys)
        for field in EDITABLE_FIELDS:
            self.assertIn(
                field.target_file,
                {DEPLOYMENT_CONFIG_FILE, LOCAL_PREFS_FILE, PRIVATE_CONFIG_FILE},
            )

    def test_guidance_and_document_references_are_present(self):
        for field in EDITABLE_FIELDS:
            with self.subTest(field=field.key):
                self.assertTrue(field.label.strip())
                self.assertTrue(field.description.strip())
                self.assertTrue(field.help_text.strip())
                self.assertTrue(field.docs_path.strip())
                self.assertTrue((ROOT / field.docs_path).is_file(), field.docs_path)
                if field.value_type not in {"boolean", "choice", "secret"}:
                    self.assertTrue(field.placeholder.strip())

    def test_choice_fields_define_a_source_of_choices(self):
        for field in EDITABLE_FIELDS:
            if field.value_type != "choice":
                continue
            with self.subTest(field=field.key):
                self.assertTrue(field.choices or field.dynamic_choices)

    def test_fields_have_one_console_owner(self):
        fields = {field.key: field for field in EDITABLE_FIELDS}
        allowed = {"settings", "controls", "wakeword", "integrations", "managed"}
        self.assertTrue(all(field.surface in allowed for field in EDITABLE_FIELDS))
        self.assertEqual(fields["PTT_ENABLED"].surface, "controls")
        self.assertEqual(fields["PHYSICAL_BUTTON_ACTIONS"].surface, "controls")
        self.assertEqual(fields["WAKEWORD_THRESHOLD"].surface, "wakeword")
        self.assertEqual(fields["WAKEWORD_CHIME"].surface, "wakeword")
        self.assertEqual(fields["WAKEWORD_STT_MODE"].surface, "wakeword")
        self.assertEqual(fields["WAKEWORD_STREAM_ENDPOINT_WINDOW_MS"].surface, "wakeword")
        self.assertEqual(fields["WAKEWORD_MODEL_PATHS"].surface, "managed")
        self.assertEqual(fields["YOUTUBE_REEL_REFRESH_ENABLED"].surface, "integrations")
        self.assertEqual(fields["YOUTUBE_REEL_REFRESH_ENABLED"].target_file, LOCAL_PREFS_FILE)
        self.assertEqual(fields["HA_TOKEN"].surface, "integrations")
        self.assertEqual(fields["HOMESUITE_HTTP_API_KEY"].surface, "settings")

    def test_active_wakeword_overrides_are_owned_by_the_wakeword_page(self):
        fields = {field.key: field for field in EDITABLE_FIELDS}

        self.assertTrue(ACTIVE_WAKEWORD_OVERRIDE_KEYS <= fields.keys())
        for key in ACTIVE_WAKEWORD_OVERRIDE_KEYS:
            with self.subTest(key=key):
                self.assertEqual(fields[key].surface, "wakeword")
                self.assertEqual(fields[key].target_file, LOCAL_PREFS_FILE)


if __name__ == "__main__":
    unittest.main()

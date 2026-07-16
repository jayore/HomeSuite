from __future__ import annotations

from pathlib import Path
import unittest

from config_schema import EDITABLE_FIELDS, LOCAL_PREFS_FILE, PRIVATE_CONFIG_FILE


ROOT = Path(__file__).resolve().parents[1]


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
            self.assertIn(field.target_file, {LOCAL_PREFS_FILE, PRIVATE_CONFIG_FILE})

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


if __name__ == "__main__":
    unittest.main()

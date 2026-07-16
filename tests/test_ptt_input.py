from __future__ import annotations

import unittest

from ptt_input import (
    exit_cancels_capture,
    input_is_listening,
    listen_level_value,
    normalize_end_behavior,
    normalize_listen_level,
)


class PttInputTests(unittest.TestCase):
    def test_configured_gpio_level_controls_the_listening_state(self):
        self.assertTrue(input_is_listening(0, "low"))
        self.assertFalse(input_is_listening(1, "low"))
        self.assertTrue(input_is_listening(1, "high"))
        self.assertFalse(input_is_listening(0, "high"))
        self.assertEqual(listen_level_value("LOW"), 0)

    def test_invalid_listen_level_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "low.*high"):
            normalize_listen_level("pressed")

    def test_ptt_end_behavior_distinguishes_submit_from_cancel(self):
        self.assertFalse(exit_cancels_capture("submit"))
        self.assertTrue(exit_cancels_capture("cancel"))
        self.assertEqual(normalize_end_behavior("SUBMIT"), "submit")

    def test_invalid_end_behavior_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "cancel.*submit"):
            normalize_end_behavior("ignore")


if __name__ == "__main__":
    unittest.main()

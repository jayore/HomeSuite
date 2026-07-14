"""Tests for homelab intent ownership and storage status routing."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from homelab_controls import handle_homelab_controls, looks_like_homelab_query
from semantic_router import RouteOutcome, route_utterance


class HomelabIntentOwnershipTests(unittest.TestCase):
    def test_explicit_storage_status_language_is_claimed(self):
        for text in (
            "how's the NAS?",
            "are the drives healthy?",
            "drive health",
            "show me the drive status",
        ):
            with self.subTest(text=text):
                self.assertTrue(looks_like_homelab_query(text))
                self.assertEqual(
                    route_utterance(text=text).outcome,
                    RouteOutcome.DEVICE,
                )

    def test_travel_drive_language_is_not_claimed(self):
        for text in (
            "how far is that to drive?",
            "how long is the drive?",
            "is it a long drive?",
        ):
            with self.subTest(text=text):
                self.assertFalse(looks_like_homelab_query(text))
                self.assertIsNone(
                    handle_homelab_controls(
                        text,
                        states_snapshot=[],
                        service_config={},
                    )
                )
                self.assertEqual(
                    route_utterance(text=text).outcome,
                    RouteOutcome.CHATGPT,
                )

        text = "should I drive to Seattle?"
        self.assertFalse(looks_like_homelab_query(text))
        self.assertIsNone(
            handle_homelab_controls(
                text,
                states_snapshot=[],
                service_config={},
            )
        )


if __name__ == "__main__":
    unittest.main()

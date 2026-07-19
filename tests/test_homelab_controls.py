"""Tests for homelab intent ownership and storage status routing."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from homelab_controls import (
    handle_homelab_controls,
    looks_like_homelab_intent,
    looks_like_homelab_query,
)
from semantic_router import RouteOutcome, route_utterance


class HomelabIntentOwnershipTests(unittest.TestCase):
    def test_supported_actions_and_live_status_queries_remain_deterministic(self):
        for text in (
            "service status",
            "is anything down?",
            "how's the homelab?",
            "how many torrents are active?",
            "what movies are downloading?",
            "pause completed downloads",
            "media request status",
            "how many requests are pending?",
            "Seerr status",
            "Radarr queue",
            "how's the NAS?",
            "are the drives healthy?",
            "how's the internet?",
            "any camera alerts?",
        ):
            with self.subTest(text=text):
                self.assertTrue(looks_like_homelab_intent(text))
                self.assertEqual(
                    route_utterance(text=text).outcome,
                    RouteOutcome.DEVICE,
                )

    def test_domain_vocabulary_in_conversation_does_not_reserve_the_turn(self):
        for text in (
            (
                "I'm mostly just testing it out. Any action requests should get "
                "picked up by a deterministic NL and anything else should get to "
                "you, ChatGPT"
            ),
            "explain how media requests work",
            "what is qBittorrent?",
            "tell me about Synology",
            "how does the internet work?",
            "camera technology is improving",
            "services should be deterministic",
            "the request should reach ChatGPT",
        ):
            with self.subTest(text=text):
                self.assertFalse(looks_like_homelab_intent(text))
                self.assertIsNone(
                    handle_homelab_controls(
                        text,
                        states_snapshot=[],
                        service_config={},
                    )
                )
                self.assertEqual(
                    route_utterance(text=text, source_type="telegram").outcome,
                    RouteOutcome.CHATGPT,
                )

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

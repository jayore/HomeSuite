from __future__ import annotations

import unittest
from unittest import mock

import command_dispatch
import pptelegram
from interaction_flow import InteractionResult
from request_context import (
    build_request_context,
    clear_current_request_context,
    get_current_request_context,
    set_current_request_context,
)


class RoomFocusTests(unittest.TestCase):
    def setUp(self):
        clear_current_request_context()

    def tearDown(self):
        clear_current_request_context()

    def test_telegram_can_set_room_with_natural_phrase(self):
        set_current_request_context(
            build_request_context(source_id="telegram", origin="telegram")
        )

        with mock.patch.object(command_dispatch, "_set_focus_room", return_value=True) as save:
            response = command_dispatch.process_device_commands("I'm in the living room")

        save.assert_called_once_with("telegram", "living_room")
        self.assertEqual(
            response,
            "Okay \u2014 you're in the Living Room now. I'll send your commands there.",
        )

    def test_telegram_adapter_preserves_mobile_source_context(self):
        seen = {}

        def handle(_runtime, text):
            seen["text"] = text
            seen["context"] = get_current_request_context()
            return InteractionResult(
                handled=True,
                action_occurred=False,
                response_text="Room updated.",
                source="device_text",
            )

        with (
            mock.patch.object(pptelegram, "_is_allowed", return_value=True),
            mock.patch.object(pptelegram, "handle_text_interaction", side_effect=handle),
        ):
            response = pptelegram._handle_message(
                object(),
                {"chat": {"id": 123}, "text": "I'm in the living room"},
            )

        self.assertEqual(response, "Room updated.")
        self.assertEqual(seen["text"], "I'm in the living room")
        self.assertEqual(seen["context"].source_id, "telegram")
        self.assertEqual(seen["context"].source_type, "telegram")
        self.assertEqual(seen["context"].origin, "telegram")
        self.assertIsNone(get_current_request_context())


if __name__ == "__main__":
    unittest.main()

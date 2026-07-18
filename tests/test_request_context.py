from __future__ import annotations

import unittest
from unittest import mock

import home_registry
import request_context


class RequestContextTests(unittest.TestCase):
    def test_dynamic_console_session_inherits_portable_source_policy(self):
        source = home_registry.get_source("console_browser_session_123")

        self.assertEqual(source["type"], "console")
        self.assertTrue(home_registry.is_source_mobile("console_browser_session_123"))
        self.assertFalse(home_registry.is_source_mobile("unregistered_browser_session"))

    def test_dynamic_console_session_recovers_its_sticky_room(self):
        with mock.patch.object(request_context, "_get_remembered_room", return_value="kitchen") as remembered:
            context = request_context.build_request_context(
                source_id="console_browser_session_123",
                source_type="console",
                origin="console_live",
            )

        remembered.assert_called_once_with("console_browser_session_123")
        self.assertEqual(context.source_room, "kitchen")
        self.assertEqual(context.effective_target_room, "kitchen")

    def test_explicit_console_room_wins_over_sticky_focus(self):
        with mock.patch.object(request_context, "_get_remembered_room") as remembered:
            context = request_context.build_request_context(
                source_id="console_browser_session_123",
                source_type="console",
                source_room="office",
                effective_target_room="office",
            )

        remembered.assert_not_called()
        self.assertEqual(context.source_room, "office")
        self.assertEqual(context.effective_target_room, "office")


if __name__ == "__main__":
    unittest.main()

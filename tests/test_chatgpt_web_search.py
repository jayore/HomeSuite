from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assistant_context import AssistantRuntimeContext  # noqa: E402


class ChatGPTWebSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with mock.patch.dict(os.environ, {"PIPHONE_NO_RUNTIME_INIT": "1"}):
            import main

        cls.main = main

    def setUp(self):
        self.main.interaction_flow.reset_history()

    def test_responses_api_can_use_web_search(self):
        response = SimpleNamespace(
            output_text="Here are today's top stories.",
            output=[SimpleNamespace(type="web_search_call")],
        )
        client = mock.Mock()
        client.responses.create.return_value = response
        runtime_context = AssistantRuntimeContext(
            instructions="Trusted runtime context with current local date.",
            web_search_user_location={
                "type": "approximate",
                "city": "Santa Barbara",
                "region": "California",
                "country": "US",
            },
        )

        with (
            mock.patch.object(self.main, "OPENAI_CLIENT", client),
            mock.patch.object(self.main, "_pref_bool", return_value=True),
            mock.patch.object(self.main, "_pref_str", return_value="gpt-5.4-mini"),
            mock.patch.object(
                self.main,
                "build_assistant_runtime_context",
                return_value=runtime_context,
            ),
            mock.patch.object(self.main, "capture_from_chatgpt_turn"),
        ):
            result = self.main.get_chatgpt_response("What's the latest news?")

        self.assertEqual(result, "Here are today's top stories.")
        client.responses.create.assert_called_once()
        self.assertEqual(
            client.responses.create.call_args.kwargs["tools"],
            [
                {
                    "type": "web_search",
                    "user_location": {
                        "type": "approximate",
                        "city": "Santa Barbara",
                        "region": "California",
                        "country": "US",
                    },
                }
            ],
        )
        self.assertIn(
            "current local date",
            client.responses.create.call_args.kwargs["instructions"],
        )
        client.chat.completions.create.assert_not_called()

    def test_failed_web_search_falls_back_to_chat_completions(self):
        client = mock.Mock()
        client.responses.create.side_effect = RuntimeError("search unavailable")
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content="A non-web answer."),
                )
            ]
        )
        runtime_context = AssistantRuntimeContext(
            instructions="Trusted runtime context for fallback.",
        )

        with (
            mock.patch.object(self.main, "OPENAI_CLIENT", client),
            mock.patch.object(self.main, "_pref_bool", return_value=True),
            mock.patch.object(self.main, "_pref_str", return_value="gpt-5.4-mini"),
            mock.patch.object(
                self.main,
                "build_assistant_runtime_context",
                return_value=runtime_context,
            ),
            mock.patch.object(self.main, "capture_from_chatgpt_turn"),
        ):
            result = self.main.get_chatgpt_response("Explain photosynthesis.")

        self.assertEqual(result, "A non-web answer.")
        client.responses.create.assert_called_once()
        client.chat.completions.create.assert_called_once()
        messages = client.chat.completions.create.call_args.kwargs["messages"]
        self.assertIn("Trusted runtime context for fallback.", messages[0]["content"])


if __name__ == "__main__":
    unittest.main()

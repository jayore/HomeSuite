from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assistant_context import (  # noqa: E402
    AssistantRuntimeContext,
    build_assistant_runtime_context,
    build_web_search_tool,
    contextualize_chat_messages,
)
from request_context import RequestContext  # noqa: E402


PROFILE = {
    "preferred_name": "Jason",
    "locale": "en-US",
    "units": "imperial",
    "notes": ["Prefers especially concise spoken answers."],
}
HOME_LOCATION = {
    "city": "Santa Barbara",
    "region": "California",
    "country": "US",
    "latitude": 34.4208,
    "longitude": -119.6982,
    "timezone": "America/Los_Angeles",
}
NOW = datetime(2026, 7, 13, 10, 30, tzinfo=timezone.utc)


class AssistantContextTests(unittest.TestCase):
    def test_fixed_home_source_gets_coarse_context_and_search_locality(self):
        context = build_assistant_runtime_context(
            now=NOW,
            profile=PROFILE,
            home_location=HOME_LOCATION,
            request_context=RequestContext(
                source_id="default_piphone",
                source_type="piphone",
                source_room="living_room",
            ),
        )

        self.assertIn("Jason", context.instructions)
        self.assertIn("Santa Barbara, California, US", context.instructions)
        self.assertIn("July 13, 2026 at 3:30 AM", context.instructions)
        self.assertIn("fixed home source in the Living Room", context.instructions)
        self.assertNotIn("34.4208", context.instructions)
        self.assertNotIn("-119.6982", context.instructions)
        self.assertEqual(
            context.web_search_user_location,
            {
                "type": "approximate",
                "city": "Santa Barbara",
                "region": "California",
                "country": "US",
                "timezone": "America/Los_Angeles",
            },
        )

    def test_mobile_source_knows_home_but_does_not_claim_current_location(self):
        context = build_assistant_runtime_context(
            now=NOW,
            profile=PROFILE,
            home_location=HOME_LOCATION,
            request_context=RequestContext(
                source_id="telegram",
                source_type="telegram",
            ),
        )

        self.assertIn("configured home area is Santa Barbara", context.instructions)
        self.assertIn("not necessarily the user's current location", context.instructions)
        self.assertIsNone(context.web_search_user_location)
        self.assertEqual(build_web_search_tool(context), {"type": "web_search"})

    def test_unknown_source_does_not_send_default_search_location(self):
        context = build_assistant_runtime_context(
            now=NOW,
            profile=PROFILE,
            home_location=HOME_LOCATION,
            request_context=RequestContext(source_id="unregistered_client"),
        )

        self.assertIn("mobility is unknown", context.instructions)
        self.assertIsNone(context.web_search_user_location)

    def test_fixed_source_without_coarse_area_asks_for_location(self):
        context = build_assistant_runtime_context(
            now=NOW,
            profile=PROFILE,
            home_location={"timezone": "America/Los_Angeles"},
            request_context=RequestContext(source_id="default_piphone"),
        )

        self.assertIn("no coarse home area is configured", context.instructions)
        self.assertIsNone(context.web_search_user_location)

    def test_invalid_country_name_is_not_truncated_into_iso_code(self):
        home_location = dict(HOME_LOCATION, country="United States")
        context = build_assistant_runtime_context(
            now=NOW,
            profile=PROFILE,
            home_location=home_location,
            request_context=RequestContext(source_id="default_piphone"),
        )

        self.assertNotIn("country", context.web_search_user_location or {})

    def test_chat_context_is_ephemeral_and_does_not_mutate_history(self):
        history = [
            {"role": "system", "content": "Base instructions."},
            {"role": "user", "content": "Hello"},
        ]
        runtime_context = AssistantRuntimeContext(
            instructions="Trusted runtime context.",
            web_search_user_location={"type": "approximate", "country": "US"},
        )

        contextualized = contextualize_chat_messages(history, runtime_context)

        self.assertEqual(history[0]["content"], "Base instructions.")
        self.assertIn("Base instructions.", contextualized[0]["content"])
        self.assertIn("Trusted runtime context.", contextualized[0]["content"])
        self.assertEqual(
            build_web_search_tool(runtime_context),
            {
                "type": "web_search",
                "user_location": {"type": "approximate", "country": "US"},
            },
        )


if __name__ == "__main__":
    unittest.main()

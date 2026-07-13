from __future__ import annotations

from datetime import datetime
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from date_controls import format_date_response, parse_date_query
from semantic_router import RouteOutcome, route_utterance


class DateQueryParserTests(unittest.TestCase):
    def test_common_current_date_phrases_are_recognized(self):
        phrases = (
            "what's the date?",
            "what date is it",
            "what's today's date",
            "what day is it",
            "what day of the week is it",
            "tell me the date",
        )

        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertIsNotNone(parse_date_query(phrase))

    def test_named_location_is_preserved(self):
        query = parse_date_query("what day is it in Tokyo?")

        self.assertIsNotNone(query)
        self.assertEqual(query.location, "tokyo")

    def test_location_pronoun_is_left_for_shared_context_resolution(self):
        query = parse_date_query("what's the date there?")

        self.assertIsNotNone(query)
        self.assertIsNone(query.location)

    def test_broader_calendar_questions_are_not_claimed(self):
        phrases = (
            "what day is Christmas",
            "what is the date of the next eclipse",
            "what date was last Thursday",
        )

        for phrase in phrases:
            with self.subTest(phrase=phrase):
                self.assertIsNone(parse_date_query(phrase))

    def test_response_includes_weekday_month_day_and_year(self):
        response = format_date_response(datetime(2026, 7, 13, 9, 30))

        self.assertEqual(response, "Today is Monday, July 13, 2026.")

    def test_date_routes_as_deterministic_utility(self):
        result = route_utterance(text="what's today's date?")

        self.assertEqual(result.outcome, RouteOutcome.DEVICE)

    def test_existing_time_route_remains_deterministic(self):
        result = route_utterance(text="what time is it?")

        self.assertEqual(result.outcome, RouteOutcome.DEVICE)


if __name__ == "__main__":
    unittest.main()

"""Tests for deterministic named-place distance and direction questions."""

from __future__ import annotations

from contextlib import ExitStack, contextmanager
from pathlib import Path
import sys
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dialogue_state
import interaction_flow
import location_controls
import location_utils
from location_controls import (
    LocationQuery,
    answer_location_query,
    parse_location_query,
)
from request_context import (
    RequestContext,
    clear_current_request_context,
    set_current_request_context,
)
from semantic_router import RouteOutcome, route_utterance


HOME_LOCATION = {
    "city": "Santa Barbara",
    "region": "California",
    "country": "US",
    "latitude": 34.4208,
    "longitude": -119.6982,
    "timezone": "America/Los_Angeles",
}
PLACES = {
    "san francisco": {
        "lat": 37.7749,
        "lon": -122.4194,
        "name": "San Francisco",
        "admin1": "California",
        "display": "San Francisco, California, United States",
    },
    "los angeles": {
        "lat": 34.0522,
        "lon": -118.2437,
        "name": "Los Angeles",
        "admin1": "California",
        "display": "Los Angeles, California, United States",
    },
    "tokyo": {
        "lat": 35.6762,
        "lon": 139.6503,
        "name": "Tokyo",
        "admin1": "Tokyo",
        "display": "Tokyo, Japan",
    },
}


def fake_geocode(value: str):
    result = PLACES.get(str(value or "").strip().lower())
    return dict(result) if result else None


class LocationParserTests(unittest.TestCase):
    def test_parses_common_distance_forms(self):
        cases = {
            "how far away is San Francisco?": ("san francisco", None),
            "how far is San Francisco from home": ("san francisco", "home"),
            "how far is it to Tokyo": ("tokyo", None),
            "what's the distance from Los Angeles to San Francisco": (
                "san francisco",
                "los angeles",
            ),
            "what is the distance between Los Angeles and San Francisco": (
                "san francisco",
                "los angeles",
            ),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                query = parse_location_query(text)
                self.assertIsNotNone(query)
                self.assertEqual((query.destination, query.origin), expected)

    def test_parses_direction_forms(self):
        query = parse_location_query(
            "what direction is San Francisco from Los Angeles?"
        )
        self.assertEqual(query.intent, "direction")
        self.assertEqual(query.destination, "san francisco")
        self.assertEqual(query.origin, "los angeles")

        to_query = parse_location_query("which direction is it to Tokyo?")
        self.assertEqual(to_query.intent, "direction")
        self.assertEqual(to_query.destination, "tokyo")

    def test_route_and_travel_language_is_left_for_ai(self):
        for text in (
            "how far is San Francisco by car?",
            "what's the driving distance to San Francisco?",
            "what route should I take to San Francisco?",
            "how long would that take?",
            "how far is that to drive?",
        ):
            with self.subTest(text=text):
                self.assertIsNone(parse_location_query(text))
                self.assertEqual(
                    route_utterance(text=text).outcome,
                    RouteOutcome.CHATGPT,
                )

    def test_celestial_distance_is_not_mistaken_for_a_place(self):
        self.assertIsNone(parse_location_query("how far away is the moon?"))
        self.assertEqual(
            route_utterance(text="how far away is the moon?").outcome,
            RouteOutcome.CHATGPT,
        )

    def test_pending_origin_followup_is_narrowly_scoped(self):
        query = parse_location_query(
            "from home",
            pending_destination="San Francisco",
        )
        self.assertEqual(query.destination, "San Francisco")
        self.assertEqual(query.origin, "home")
        self.assertIsNone(
            parse_location_query(
                "turn off the lights",
                pending_destination="San Francisco",
            )
        )


class LocationMathAndAnswerTests(unittest.TestCase):
    def test_known_distance_and_bearing(self):
        distance = location_utils.great_circle_distance_km(
            34.4208,
            -119.6982,
            37.7749,
            -122.4194,
        )
        bearing = location_utils.initial_bearing_degrees(
            34.4208,
            -119.6982,
            37.7749,
            -122.4194,
        )
        self.assertAlmostEqual(distance, 447.0, delta=2.0)
        self.assertEqual(location_utils.compass_direction(bearing), "northwest")

    def test_fixed_source_defaults_to_configured_home(self):
        answer = answer_location_query(
            LocationQuery(destination="San Francisco"),
            home_location=HOME_LOCATION,
            source_is_fixed=True,
            geocoder=fake_geocode,
        )
        self.assertIn("San Francisco is about 277 miles", answer.text)
        self.assertIn("northwest of home in Santa Barbara", answer.text)
        self.assertIn("as the crow flies", answer.text)
        self.assertFalse(answer.needs_origin)

    def test_mobile_source_requires_an_origin(self):
        answer = answer_location_query(
            LocationQuery(destination="San Francisco"),
            home_location=HOME_LOCATION,
            source_is_fixed=False,
            geocoder=fake_geocode,
        )
        self.assertTrue(answer.needs_origin)
        self.assertEqual(answer.destination, "San Francisco")
        self.assertIn("From where?", answer.text)
        self.assertIn("from home", answer.text)
        self.assertIn("for San Francisco", answer.text)

    def test_mobile_source_can_explicitly_use_home(self):
        answer = answer_location_query(
            LocationQuery(destination="San Francisco", origin="home"),
            home_location=HOME_LOCATION,
            source_is_fixed=False,
            geocoder=fake_geocode,
        )
        self.assertIn("home in Santa Barbara", answer.text)
        self.assertFalse(answer.needs_origin)

    def test_explicit_named_origin_overrides_home(self):
        answer = answer_location_query(
            LocationQuery(destination="San Francisco", origin="Los Angeles"),
            home_location=HOME_LOCATION,
            source_is_fixed=True,
            geocoder=fake_geocode,
        )
        self.assertIn("northwest of Los Angeles", answer.text)
        self.assertNotIn("home in Santa Barbara", answer.text)

    def test_metric_profile_formats_kilometers(self):
        answer = answer_location_query(
            LocationQuery(destination="San Francisco", origin="home"),
            home_location=HOME_LOCATION,
            units="metric",
            source_is_fixed=False,
            geocoder=fake_geocode,
        )
        self.assertIn("446 kilometers", answer.text)
        self.assertNotIn("miles", answer.text)

    def test_location_pronoun_uses_typed_recalled_place(self):
        answer = answer_location_query(
            LocationQuery(destination="there", origin="home"),
            home_location=HOME_LOCATION,
            source_is_fixed=False,
            recalled_location="Tokyo",
            geocoder=fake_geocode,
        )
        self.assertIn("Tokyo", answer.text)
        self.assertEqual(answer.destination, "Tokyo")


class LocationProviderTests(unittest.TestCase):
    def setUp(self):
        location_utils.clear_geocode_cache()

    def tearDown(self):
        location_utils.clear_geocode_cache()

    def test_geocoder_returns_shared_metadata_and_caches_success(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "results": [
                {
                    "name": "Los Angeles",
                    "admin1": "California",
                    "country": "United States",
                    "country_code": "US",
                    "latitude": 34.0522,
                    "longitude": -118.2437,
                    "timezone": "America/Los_Angeles",
                }
            ]
        }
        with mock.patch.object(location_utils.requests, "get", return_value=response) as get:
            first = location_utils.geocode_location("LA")
            second = location_utils.geocode_location("la")

        self.assertEqual(first["name"], "Los Angeles")
        self.assertEqual(first["country_code"], "US")
        self.assertEqual(second, first)
        get.assert_called_once()
        self.assertEqual(get.call_args.kwargs["params"]["name"], "Los Angeles")


class LocationDispatchIntegrationTests(unittest.TestCase):
    def setUp(self):
        clear_current_request_context()
        dialogue_state.reset_dialogue_state(all_scopes=True)
        interaction_flow.reset_history(all_scopes=True)

    def tearDown(self):
        clear_current_request_context()
        dialogue_state.reset_dialogue_state(all_scopes=True)
        interaction_flow.reset_history(all_scopes=True)

    @contextmanager
    def _patched_dispatch(self):
        import command_dispatch

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(
                command_dispatch,
                "handle_schedule_controls",
                return_value=None,
            ))
            stack.enter_context(mock.patch.object(
                command_dispatch,
                "handle_stock_quote_query",
                return_value=None,
            ))
            stack.enter_context(mock.patch.object(
                location_controls,
                "geocode_location",
                side_effect=fake_geocode,
            ))
            states = stack.enter_context(
                mock.patch.object(command_dispatch, "ha_get_states")
            )
            yield states

    def test_fixed_pi_answer_avoids_home_assistant_snapshot(self):
        import command_dispatch

        set_current_request_context(RequestContext(source_id="default_piphone"))
        with self._patched_dispatch() as states:
            response = command_dispatch.process_device_commands(
                "how far is San Francisco?"
            )

        self.assertIn("home in Santa Barbara", response)
        states.assert_not_called()
        self.assertEqual(
            dialogue_state.resolve_referent(kinds={"location"})["key"].casefold(),
            "san francisco",
        )

    def test_mobile_clarification_and_from_home_followup_share_scope(self):
        import command_dispatch

        set_current_request_context(RequestContext(source_id="telegram"))
        with self._patched_dispatch() as states:
            clarification = command_dispatch.process_device_commands(
                "how far is San Francisco?"
            )
            answer = command_dispatch.process_device_commands("from home")

        self.assertIn("From where?", clarification)
        self.assertIn("home in Santa Barbara", answer)
        self.assertIsNone(
            dialogue_state.resolve_referent(kinds={"location_distance_origin"})
        )
        states.assert_not_called()

    def test_spoken_answer_supplies_ai_followup_history(self):
        import command_dispatch

        set_current_request_context(RequestContext(source_id="default_piphone"))
        with self._patched_dispatch() as states:
            response = command_dispatch.process_device_commands(
                "how far is San Francisco?"
            )
            interaction_flow.inject_into_history(
                "how far is San Francisco?",
                response,
            )

        history = interaction_flow.get_history_snapshot()
        self.assertIn("San Francisco", history[-1]["content"])
        self.assertIn("home in Santa Barbara", history[-1]["content"])
        self.assertEqual(
            route_utterance(text="how far is that by car?").outcome,
            RouteOutcome.CHATGPT,
        )


if __name__ == "__main__":
    unittest.main()

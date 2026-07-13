from __future__ import annotations

from datetime import date, datetime
import sys
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import astronomy_controls
import astronomy_utils
import planetary_utils
from semantic_router import RouteOutcome, route_utterance


PACIFIC = ZoneInfo("America/Los_Angeles")
HOME_LOCATION = {
    "latitude": 34.4208,
    "longitude": -119.6982,
    "timezone": "America/Los_Angeles",
}
NOW = datetime(2026, 7, 13, 3, 0, tzinfo=PACIFIC)


class AstronomyQueryParserTests(unittest.TestCase):
    def test_moonrise_tomorrow_is_an_event_query(self):
        query = astronomy_controls.parse_astronomy_query(
            "When does the moon rise tomorrow?"
        )

        self.assertEqual(query.intent, "event")
        self.assertEqual(query.event, "moonrise")
        self.assertEqual(query.day_offset, 1)
        self.assertTrue(query.explicit_day)

    def test_sunset_weekday_and_next_weekday_are_distinct(self):
        upcoming = astronomy_controls.parse_astronomy_query(
            "What time is sunset Thursday?"
        )
        following = astronomy_controls.parse_astronomy_query(
            "What time is sunset next Thursday?"
        )

        self.assertEqual(upcoming.weekday, 3)
        self.assertFalse(upcoming.next_weekday)
        self.assertEqual(following.weekday, 3)
        self.assertTrue(following.next_weekday)

    def test_phase_and_horizon_questions_are_parsed(self):
        phase = astronomy_controls.parse_astronomy_query(
            "What's the moon phase tomorrow?"
        )
        status = astronomy_controls.parse_astronomy_query("Is the moon up right now?")

        self.assertEqual((phase.intent, phase.body, phase.day_offset), ("phase", "moon", 1))
        self.assertEqual((status.intent, status.body), ("status", "moon"))

    def test_next_full_and_new_moon_are_phase_event_queries(self):
        full = astronomy_controls.parse_astronomy_query(
            "When is the next full moon?"
        )
        new = astronomy_controls.parse_astronomy_query(
            "What date is the next new moon?"
        )

        self.assertEqual((full.intent, full.phase), ("phase_event", "full moon"))
        self.assertEqual((new.intent, new.phase), ("phase_event", "new moon"))

    def test_planet_event_position_and_visibility_queries_are_parsed(self):
        rise = astronomy_controls.parse_astronomy_query("When does Jupiter rise?")
        position = astronomy_controls.parse_astronomy_query("Where is Saturn?")
        visible = astronomy_controls.parse_astronomy_query(
            "What planets are visible tonight?"
        )
        best = astronomy_controls.parse_astronomy_query(
            "What's the best time to see Venus tonight?"
        )

        self.assertEqual(
            (rise.intent, rise.planet, rise.event),
            ("planet_event", "jupiter", "rise"),
        )
        self.assertEqual(
            (position.intent, position.planet),
            ("planet_position", "saturn"),
        )
        self.assertEqual(visible.intent, "visible_planets")
        self.assertTrue(visible.night_window)
        self.assertEqual((best.intent, best.planet), ("planet_best", "venus"))

    def test_planet_current_and_future_visibility_are_distinct(self):
        current = astronomy_controls.parse_astronomy_query(
            "Can I see Mars right now?"
        )
        future = astronomy_controls.parse_astronomy_query(
            "Is Mars visible tomorrow night?"
        )

        self.assertEqual(current.intent, "planet_visible")
        self.assertEqual(future.intent, "planet_best")
        self.assertEqual(future.day_offset, 1)
        self.assertTrue(future.night_window)

    def test_schedule_phrase_is_not_claimed_as_a_query(self):
        query = astronomy_controls.parse_astronomy_query(
            "turn on the porch lights at sunset"
        )

        self.assertIsNone(query)

    def test_astronomy_query_routes_as_deterministic_utility(self):
        result = route_utterance(text="when is moonrise tomorrow")

        self.assertEqual(result.outcome, RouteOutcome.DEVICE)

    def test_planet_query_routes_as_deterministic_utility(self):
        result = route_utterance(text="what planets are visible tonight")

        self.assertEqual(result.outcome, RouteOutcome.DEVICE)


class AstronomyResponseTests(unittest.TestCase):
    def test_current_phase_prefers_home_assistant(self):
        response = astronomy_controls.handle_astronomy_query(
            "what's the moon phase",
            home_location=HOME_LOCATION,
            states_snapshot=[
                {"entity_id": "sensor.moon_phase", "state": "waning_crescent"}
            ],
            now=NOW,
        )

        self.assertEqual(response, "The moon phase is waning crescent.")

    def test_sun_status_prefers_home_assistant(self):
        response = astronomy_controls.handle_astronomy_query(
            "is the sun up",
            home_location={},
            states_snapshot=[{"entity_id": "sun.sun", "state": "below_horizon"}],
            now=NOW,
        )

        self.assertEqual(response, "No. The sun is below the horizon.")

    def test_bare_event_returns_the_next_occurrence(self):
        target = datetime(2026, 7, 13, 4, 49, tzinfo=PACIFIC)
        with mock.patch.object(
            astronomy_controls,
            "find_next_astronomy_event",
            return_value=target,
        ) as resolver:
            response = astronomy_controls.handle_astronomy_query(
                "when is moonrise",
                home_location=HOME_LOCATION,
                now=NOW,
            )

        self.assertEqual(response, "Moonrise is at 4:49 AM today.")
        resolver.assert_called_once_with(
            "moonrise",
            after=NOW,
            home_location=HOME_LOCATION,
        )

    def test_explicit_eventless_date_is_explained(self):
        with mock.patch.object(
            astronomy_controls,
            "resolve_astronomy_event",
            return_value=None,
        ):
            response = astronomy_controls.handle_astronomy_query(
                "when is moonrise tomorrow",
                home_location=HOME_LOCATION,
                now=NOW,
            )

        self.assertEqual(response, "There is no moonrise here tomorrow.")

    def test_missing_coordinates_are_not_silently_guessed(self):
        response = astronomy_controls.handle_astronomy_query(
            "when is moonrise",
            home_location={"latitude": None, "longitude": None},
            now=NOW,
        )

        self.assertEqual(response, "Home location isn't configured for astronomy yet.")

    def test_spoken_event_time_rounds_to_nearest_minute(self):
        target = datetime(2026, 7, 13, 5, 56, 45, tzinfo=PACIFIC)
        with mock.patch.object(
            astronomy_controls,
            "resolve_astronomy_event",
            return_value=target,
        ):
            response = astronomy_controls.handle_astronomy_query(
                "when is sunrise today",
                home_location=HOME_LOCATION,
                now=NOW,
            )

        self.assertEqual(response, "Sunrise is at 5:57 AM today.")

    def test_next_phase_questions_return_calendar_dates(self):
        full = astronomy_controls.handle_astronomy_query(
            "when is the next full moon",
            home_location=HOME_LOCATION,
            now=NOW,
        )
        new = astronomy_controls.handle_astronomy_query(
            "when is the next new moon",
            home_location=HOME_LOCATION,
            now=NOW,
        )

        self.assertEqual(full, "The next full moon is on Wednesday, July 29.")
        self.assertEqual(new, "The next new moon is tomorrow, July 14.")

    def test_phase_question_cannot_trigger_sonos_next_track(self):
        from sonos_controls import handle_sonos_controls

        call_ha_service = mock.Mock(return_value=True)
        for text in (
            "when is the next full moon",
            "when is the next new moon",
        ):
            with self.subTest(text=text):
                response = handle_sonos_controls(
                    tl=text,
                    states_snapshot=[],
                    call_ha_service=call_ha_service,
                    maybe_say=lambda value: value,
                    players_map={"living room": "media_player.living_room"},
                    default_room="living room",
                )
                self.assertIsNone(response)
        call_ha_service.assert_not_called()

    def test_planet_rise_returns_a_spoken_local_time(self):
        target = datetime(2026, 7, 13, 6, 53, 28, tzinfo=PACIFIC)
        with mock.patch.object(
            astronomy_controls,
            "planetary_available",
            return_value=True,
        ), mock.patch.object(
            astronomy_controls,
            "find_next_planet_event",
            return_value=target,
        ) as resolver:
            response = astronomy_controls.handle_astronomy_query(
                "when does Jupiter rise",
                home_location=HOME_LOCATION,
                now=NOW,
            )

        self.assertEqual(response, "Jupiter rises at 6:53 AM today.")
        resolver.assert_called_once_with(
            "jupiter",
            "rise",
            after=NOW,
            home_location=HOME_LOCATION,
        )

    def test_visible_planets_answer_includes_best_times_and_caveat(self):
        windows = [
            planetary_utils.PlanetVisibility(
                planet="venus",
                start=datetime(2026, 7, 13, 20, 45, tzinfo=PACIFIC),
                end=datetime(2026, 7, 13, 21, 45, tzinfo=PACIFIC),
                best_time=datetime(2026, 7, 13, 20, 45, tzinfo=PACIFIC),
                best_altitude_degrees=21.4,
                best_direction="west",
                magnitude=-4.1,
            ),
            planetary_utils.PlanetVisibility(
                planet="saturn",
                start=datetime(2026, 7, 14, 1, 10, tzinfo=PACIFIC),
                end=datetime(2026, 7, 14, 5, 30, tzinfo=PACIFIC),
                best_time=datetime(2026, 7, 14, 5, 25, tzinfo=PACIFIC),
                best_altitude_degrees=55.9,
                best_direction="southeast",
                magnitude=0.7,
            ),
        ]
        with mock.patch.object(
            astronomy_controls,
            "planetary_available",
            return_value=True,
        ), mock.patch.object(
            astronomy_controls,
            "visible_planets",
            return_value=windows,
        ):
            response = astronomy_controls.handle_astronomy_query(
                "what planets are visible tonight",
                home_location=HOME_LOCATION,
                now=NOW,
            )

        self.assertEqual(
            response,
            "Venus and Saturn should be visible tonight. Best viewing is Venus "
            "around 8:45 PM in the west and Saturn around 5:25 AM in the "
            "southeast. This assumes clear skies and an unobstructed view.",
        )

    def test_planet_up_response_is_read_only_position_data(self):
        position = planetary_utils.PlanetPosition(
            planet="mars",
            at=NOW,
            altitude_degrees=24.4,
            azimuth_degrees=112.0,
            direction="southeast",
            magnitude=1.3,
            sun_altitude_degrees=-18.0,
            potentially_visible=True,
        )
        with mock.patch.object(
            astronomy_controls,
            "planetary_available",
            return_value=True,
        ), mock.patch.object(
            astronomy_controls,
            "planet_position",
            return_value=position,
        ):
            response = astronomy_controls.handle_astronomy_query(
                "is Mars up right now",
                home_location=HOME_LOCATION,
                now=NOW,
            )

        self.assertEqual(
            response,
            "Yes. Mars is about 24 degrees above the southeast horizon right now.",
        )


class AstralCalculationTests(unittest.TestCase):
    def test_real_moonrise_and_sunrise_use_configured_timezone(self):
        moonrise = astronomy_utils.resolve_astronomy_event(
            "moonrise",
            date(2026, 7, 13),
            home_location=HOME_LOCATION,
        )
        sunrise = astronomy_utils.resolve_astronomy_event(
            "sunrise",
            date(2026, 7, 13),
            home_location=HOME_LOCATION,
        )

        self.assertEqual(moonrise, datetime(2026, 7, 13, 4, 49, tzinfo=PACIFIC))
        self.assertEqual(sunrise.date(), date(2026, 7, 13))
        self.assertEqual((sunrise.hour, sunrise.minute), (5, 56))
        self.assertEqual(sunrise.tzinfo, PACIFIC)

    def test_phase_mapping_does_not_call_pre_new_moon_new_too_early(self):
        self.assertEqual(
            astronomy_utils.moon_phase_name(date(2026, 7, 13)),
            "waning crescent",
        )

    def test_next_new_and_full_moon_dates(self):
        self.assertEqual(
            astronomy_utils.find_next_moon_phase_date(
                "new moon",
                start_date=date(2026, 7, 13),
            ),
            date(2026, 7, 14),
        )
        self.assertEqual(
            astronomy_utils.find_next_moon_phase_date(
                "full moon",
                start_date=date(2026, 7, 13),
            ),
            date(2026, 7, 29),
        )


@unittest.skipUnless(
    planetary_utils.planetary_available(),
    "Skyfield and its packaged ephemeris are not installed",
)
class SkyfieldCalculationTests(unittest.TestCase):
    def test_real_jupiter_rise_uses_configured_timezone(self):
        rising = planetary_utils.find_next_planet_event(
            "jupiter",
            "rise",
            after=NOW,
            home_location=HOME_LOCATION,
        )

        self.assertIsNotNone(rising)
        self.assertEqual(rising.date(), date(2026, 7, 13))
        self.assertEqual((rising.hour, rising.minute), (6, 53))
        self.assertEqual(rising.tzinfo, PACIFIC)

    def test_real_visible_planets_are_filtered_by_darkness_and_altitude(self):
        windows = planetary_utils.visible_planets(
            date(2026, 7, 13),
            home_location=HOME_LOCATION,
        )

        names = [window.planet for window in windows]
        self.assertEqual(names, ["venus", "saturn", "mars"])
        self.assertNotIn("jupiter", names)
        self.assertTrue(all(window.start < window.end for window in windows))


if __name__ == "__main__":
    unittest.main()

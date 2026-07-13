from __future__ import annotations

import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo


PACIFIC = ZoneInfo("America/Los_Angeles")


class SolarScheduleParserTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 12, 12, 0, tzinfo=PACIFIC)

    def test_command_first_sunset_schedule(self):
        from schedule_controls import parse_schedule_request

        target = datetime(2026, 7, 12, 20, 15, tzinfo=PACIFIC)
        resolver = mock.Mock(return_value=target)

        parsed = parse_schedule_request(
            "turn on the porch lights at sunset",
            now=self.now,
            solar_resolver=resolver,
        )

        self.assertEqual(parsed.command, "turn on the porch lights")
        self.assertEqual(parsed.run_at, target.timestamp())
        self.assertEqual(parsed.phrase, "at sunset")
        resolver.assert_called_once_with("sunset", "next", self.now)

    def test_time_first_tomorrow_sunrise_schedule(self):
        from schedule_controls import parse_schedule_request

        target = datetime(2026, 7, 13, 5, 52, tzinfo=PACIFIC)
        resolver = mock.Mock(return_value=target)

        parsed = parse_schedule_request(
            "tomorrow at sunrise open the bedroom blinds",
            now=self.now,
            solar_resolver=resolver,
        )

        self.assertEqual(parsed.command, "open the bedroom blinds")
        self.assertEqual(parsed.run_at, target.timestamp())
        self.assertEqual(parsed.phrase, "tomorrow at sunrise")
        resolver.assert_called_once_with("sunrise", "tomorrow", self.now)

    def test_offset_before_sunset(self):
        from schedule_controls import parse_schedule_request

        sunset = datetime(2026, 7, 12, 20, 15, tzinfo=PACIFIC)
        parsed = parse_schedule_request(
            "turn on the porch lights twenty minutes before sunset",
            now=self.now,
            solar_resolver=lambda event, day, now: sunset,
        )

        self.assertEqual(parsed.run_at, sunset.timestamp() - (20 * 60))
        self.assertEqual(parsed.phrase, "20 minutes before sunset")

    def test_unresolved_solar_schedule_fails_closed(self):
        from schedule_controls import handle_schedule_controls

        validate = mock.Mock(return_value=(True, "validated", {}))
        response = handle_schedule_controls(
            tl="turn on the porch lights at sunset",
            validate_command=validate,
            solar_resolver=lambda event, day, now: None,
        )

        self.assertIn("couldn't resolve", response)
        validate.assert_not_called()


class SolarResolverTests(unittest.TestCase):
    def test_next_event_uses_home_assistant_without_fallback(self):
        from solar_utils import resolve_solar_event

        now = datetime(2026, 7, 12, 12, 0, tzinfo=PACIFIC)
        states = [
            {
                "entity_id": "sun.sun",
                "attributes": {"next_setting": "2026-07-13T03:15:00+00:00"},
            }
        ]
        with mock.patch("astronomy_utils.find_next_astronomy_event") as fallback:
            resolved = resolve_solar_event(
                "sunset",
                "next",
                now=now,
                states_provider=lambda: states,
                home_location={
                    "latitude": 34.42,
                    "longitude": -119.70,
                    "timezone": "America/Los_Angeles",
                },
            )

        self.assertEqual(resolved, datetime(2026, 7, 12, 20, 15, tzinfo=PACIFIC))
        fallback.assert_not_called()

    def test_tomorrow_uses_astral_when_ha_only_has_today(self):
        from solar_utils import resolve_solar_event

        now = datetime(2026, 7, 12, 4, 0, tzinfo=PACIFIC)
        states = [
            {
                "entity_id": "sun.sun",
                "attributes": {"next_rising": "2026-07-12T12:50:00+00:00"},
            }
        ]

        target = datetime(2026, 7, 13, 5, 57, tzinfo=PACIFIC)
        with mock.patch(
            "astronomy_utils.resolve_astronomy_event",
            return_value=target,
        ) as calculate:
            resolved = resolve_solar_event(
                "sunrise",
                "tomorrow",
                now=now,
                states_provider=lambda: states,
                home_location={
                    "latitude": 34.42,
                    "longitude": -119.70,
                    "timezone": "America/Los_Angeles",
                },
            )

        self.assertEqual(resolved, target)
        calculate.assert_called_once_with(
            "sunrise",
            target.date(),
            home_location={
                "latitude": 34.42,
                "longitude": -119.70,
                "timezone": "America/Los_Angeles",
            },
        )

    def test_missing_location_fails_closed_when_ha_cannot_answer(self):
        from solar_utils import resolve_solar_event

        now = datetime(2026, 7, 12, 12, 0, tzinfo=PACIFIC)
        resolved = resolve_solar_event(
            "sunset",
            "tomorrow",
            now=now,
            states_provider=lambda: [],
            home_location={"latitude": None, "longitude": None},
        )

        self.assertIsNone(resolved)


if __name__ == "__main__":
    unittest.main()

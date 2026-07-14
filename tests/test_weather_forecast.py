from __future__ import annotations

from datetime import date, datetime, timezone
import sys
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ha_client
import weather_utils
from semantic_router import RouteOutcome, route_utterance
from weather_utils import DailyForecast, HourlyForecast, WeatherQuery


class WeatherQueryParserTests(unittest.TestCase):
    def test_current_weather_keeps_named_location(self):
        query = weather_utils.parse_weather_query("What's the weather in Tokyo?")

        self.assertEqual(query.mode, "current")
        self.assertEqual(query.location, "tokyo")

    def test_tomorrow_can_come_before_or_after_location(self):
        after = weather_utils.parse_weather_query("weather in Tokyo tomorrow")
        before = weather_utils.parse_weather_query("weather tomorrow in Tokyo")

        for query in (after, before):
            self.assertEqual(query.mode, "day")
            self.assertEqual(query.day_offset, 1)
            self.assertEqual(query.location, "tokyo")

    def test_weekday_forecast_is_parsed(self):
        query = weather_utils.parse_weather_query("forecast for Thursday")

        self.assertEqual(query.mode, "day")
        self.assertEqual(query.weekday, 3)

    def test_next_week_and_seven_day_forms_are_ranges(self):
        next_week = weather_utils.parse_weather_query(
            "forecast for next week in Tokyo"
        )
        this_week = weather_utils.parse_weather_query("what is the weather this week")
        seven_day = weather_utils.parse_weather_query("seven-day forecast")

        self.assertEqual((next_week.mode, next_week.days), ("range", 7))
        self.assertEqual(next_week.location, "tokyo")
        self.assertEqual((this_week.mode, this_week.days), ("range", 7))
        self.assertEqual((seven_day.mode, seven_day.days), ("range", 7))

    def test_bare_forecast_means_today_not_current_conditions(self):
        query = weather_utils.parse_weather_query("what's the forecast")

        self.assertEqual(query.mode, "day")
        self.assertEqual(query.day_offset, 0)

    def test_tonight_and_hourly_requests_are_distinct(self):
        tonight = weather_utils.parse_weather_query("weather in Tokyo tonight")
        hourly = weather_utils.parse_weather_query("forecast for the next 8 hours")

        self.assertEqual(tonight.mode, "hourly")
        self.assertEqual(tonight.period, "tonight")
        self.assertEqual(tonight.location, "tokyo")
        self.assertEqual(hourly.mode, "hourly")
        self.assertEqual(hourly.hours, 8)

    def test_weekend_dates_start_on_saturday(self):
        query = weather_utils.parse_weather_query("weather this weekend")
        next_query = weather_utils.parse_weather_query("weather next weekend")
        monday = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)

        self.assertEqual(
            weather_utils.weather_query_dates(query, timezone_name="UTC", now=monday),
            [date(2026, 7, 18), date(2026, 7, 19)],
        )
        self.assertEqual(
            weather_utils.weather_query_dates(next_query, timezone_name="UTC", now=monday),
            [date(2026, 7, 25), date(2026, 7, 26)],
        )

    def test_precipitation_question_does_not_require_weather_keyword(self):
        query = weather_utils.parse_weather_query("will it rain tomorrow in Tokyo")

        self.assertEqual(query.mode, "day")
        self.assertEqual(query.day_offset, 1)
        self.assertEqual(query.focus, "precipitation")
        self.assertEqual(query.phenomenon, "rain")
        self.assertEqual(query.location, "tokyo")

    def test_target_dates_use_destination_timezone(self):
        query = WeatherQuery(mode="day", day_offset=1)
        now = datetime(2026, 7, 13, 1, 0, tzinfo=timezone.utc)

        los_angeles = weather_utils.weather_query_dates(
            query,
            timezone_name="America/Los_Angeles",
            now=now,
        )
        tokyo = weather_utils.weather_query_dates(
            query,
            timezone_name="Asia/Tokyo",
            now=now,
        )

        self.assertEqual(los_angeles, [date(2026, 7, 13)])
        self.assertEqual(tokyo, [date(2026, 7, 14)])

    def test_forecast_routes_as_deterministic_utility(self):
        result = route_utterance(text="forecast for next week")

        self.assertEqual(result.outcome, RouteOutcome.DEVICE)


class WeatherProviderTests(unittest.TestCase):
    def setUp(self):
        weather_utils._clear_weather_cache()

    def test_ha_rows_convert_celsius_and_ignore_precipitation_amount(self):
        rows = [
            {
                "datetime": "2026-07-13T00:00:00+00:00",
                "condition": "partlycloudy",
                "temperature": 20,
                "templow": 10,
                "precipitation": 0.4,
            }
        ]

        forecasts = weather_utils._normalize_daily_rows(rows, "°C")

        self.assertEqual(len(forecasts), 1)
        self.assertEqual(forecasts[0].condition, "partly cloudy")
        self.assertAlmostEqual(forecasts[0].high_f, 68.0)
        self.assertAlmostEqual(forecasts[0].low_f, 50.0)
        self.assertIsNone(forecasts[0].precipitation_probability)

    def test_open_meteo_retains_all_daily_rows_and_weather_codes(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "timezone": "America/Los_Angeles",
            "current": {"temperature_2m": 70.2, "weather_code": 2},
            "daily": {
                "time": ["2026-07-12", "2026-07-13", "2026-07-14"],
                "weather_code": [2, 0, 61],
                "temperature_2m_max": [73, 75, 69],
                "temperature_2m_min": [56, 58, 55],
                "precipitation_probability_max": [10, 0, 60],
                "precipitation_sum": [0, 0, 0.2],
            },
        }

        with mock.patch.object(
            weather_utils.requests, "get", return_value=response
        ) as get:
            report = weather_utils._open_meteo_report(
                34.4, -119.7, forecast_days=3
            )

        self.assertEqual(len(report.daily), 3)
        self.assertEqual(report.current_condition, "partly cloudy")
        self.assertEqual(report.daily[2].condition, "light rain")
        self.assertEqual(report.daily[2].precipitation_probability, 60)
        self.assertEqual(get.call_args.kwargs["params"]["forecast_days"], 3)

    def test_open_meteo_normalizes_hourly_rows(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "timezone": "America/Los_Angeles",
            "current": {},
            "daily": {},
            "hourly": {
                "time": ["2026-07-13T18:00", "2026-07-13T19:00"],
                "weather_code": [1, 61],
                "temperature_2m": [72, 69],
                "precipitation_probability": [10, 60],
            },
        }

        with mock.patch.object(weather_utils.requests, "get", return_value=response):
            report = weather_utils._open_meteo_report(34.4, -119.7, forecast_days=1)

        self.assertEqual(len(report.hourly), 2)
        self.assertEqual(report.hourly[1].condition, "light rain")
        self.assertEqual(report.hourly[1].precipitation_probability, 60)
        self.assertEqual(str(report.hourly[0].forecast_time.tzinfo), "America/Los_Angeles")

    def test_open_meteo_report_is_cached(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "current": {},
            "daily": {
                "time": ["2026-07-12"],
                "weather_code": [0],
                "temperature_2m_max": [70],
                "temperature_2m_min": [50],
                "precipitation_probability_max": [0],
            },
        }

        with mock.patch.object(
            weather_utils.requests, "get", return_value=response
        ) as get:
            weather_utils._open_meteo_report(34.4, -119.7, forecast_days=1)
            weather_utils._open_meteo_report(34.4, -119.7, forecast_days=1)

        get.assert_called_once()

    def test_ha_forecast_response_helper_unwraps_service_response(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "changed_states": [],
            "service_response": {
                "weather.forecast_home": {
                    "forecast": [{"datetime": "2026-07-12", "temperature": 72}]
                }
            },
        }
        session = mock.Mock()
        session.post.return_value = response

        with (
            mock.patch.object(ha_client, "_HA_URL", "http://ha.test:8123"),
            mock.patch.object(ha_client, "HA_SESSION", session),
        ):
            rows = ha_client.ha_get_weather_forecasts("weather.forecast_home")

        self.assertEqual(rows[0]["temperature"], 72)
        self.assertTrue(
            session.post.call_args.args[0].endswith(
                "/api/services/weather/get_forecasts?return_response"
            )
        )


class WeatherFormatterTests(unittest.TestCase):
    NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)

    def test_tomorrow_response_is_concise_and_complete(self):
        query = WeatherQuery(mode="day", day_offset=1)
        rows = [
            DailyForecast(
                date(2026, 7, 13),
                condition="sunny",
                high_f=75.2,
                low_f=58.1,
                precipitation_probability=10,
            )
        ]

        response = weather_utils.format_forecast_response(
            query,
            rows,
            timezone_name="UTC",
            now=self.NOW,
        )

        self.assertEqual(
            response,
            "Tomorrow: sunny, high 75, low 58, 10 percent chance of precipitation.",
        )

    def test_range_mentions_each_day_and_suppresses_low_precip_chances(self):
        query = WeatherQuery(mode="range", days=3)
        rows = [
            DailyForecast(date(2026, 7, 12), "cloudy", 70, 55, 10),
            DailyForecast(date(2026, 7, 13), "sunny", 74, 57, 0),
            DailyForecast(date(2026, 7, 14), "rain", 68, 54, 60),
        ]

        response = weather_utils.format_forecast_response(
            query,
            rows,
            timezone_name="UTC",
            now=self.NOW,
        )

        self.assertTrue(response.startswith("Three-day forecast."))
        self.assertIn("Today: cloudy", response)
        self.assertIn("Tomorrow: sunny", response)
        self.assertIn("Tuesday: rain", response)
        self.assertNotIn("10 percent", response)
        self.assertIn("60 percent chance of precipitation", response)

    def test_precipitation_question_gets_a_direct_answer(self):
        query = WeatherQuery(
            mode="day",
            day_offset=1,
            focus="precipitation",
            phenomenon="rain",
        )
        rows = [DailyForecast(date(2026, 7, 13), "light rain", 70, 55, 65)]

        response = weather_utils.format_forecast_response(
            query,
            rows,
            timezone_name="UTC",
            now=self.NOW,
        )

        self.assertEqual(response, "Yes. Tomorrow has a 65 percent chance of rain.")

    def test_tonight_hourly_response_uses_evening_low_and_rain_peak(self):
        query = WeatherQuery(mode="hourly", hours=12, period="tonight")
        rows = [
            HourlyForecast(
                datetime(2026, 7, 12, 18, 0, tzinfo=timezone.utc),
                "clear",
                70,
                5,
            ),
            HourlyForecast(
                datetime(2026, 7, 12, 21, 0, tzinfo=timezone.utc),
                "clear",
                62,
                20,
            ),
        ]

        response = weather_utils.format_hourly_response(
            query,
            rows,
            timezone_name="UTC",
            now=self.NOW,
        )

        self.assertEqual(
            response,
            "Tonight: clear, low 62, up to a 20 percent chance of precipitation.",
        )

    def test_hourly_response_samples_without_becoming_chatty(self):
        query = WeatherQuery(mode="hourly", hours=4)
        rows = [
            HourlyForecast(
                datetime(2026, 7, 12, hour, 0, tzinfo=timezone.utc),
                "sunny",
                70 + hour,
                0,
            )
            for hour in range(12, 16)
        ]

        response = weather_utils.format_hourly_response(
            query,
            rows,
            timezone_name="UTC",
            now=self.NOW,
        )

        self.assertTrue(response.startswith("Next 4 hours."))
        self.assertIn("12 PM: 82 degrees, sunny", response)
        self.assertIn("3 PM: 85 degrees, sunny", response)

    def test_hourly_utc_rows_use_local_clock_when_provider_omits_timezone(self):
        query = WeatherQuery(mode="hourly", hours=2)
        pacific = ZoneInfo("America/Los_Angeles")
        now = datetime(2026, 7, 13, 15, 30, tzinfo=pacific)
        rows = [
            HourlyForecast(
                datetime(2026, 7, 13, 23, 0, tzinfo=timezone.utc),
                "clear",
                75,
                0,
            ),
            HourlyForecast(
                datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc),
                "clear",
                72,
                0,
            ),
        ]

        response = weather_utils.format_hourly_response(query, rows, now=now)

        self.assertIn("4 PM: 75 degrees, clear", response)
        self.assertIn("5 PM: 72 degrees, clear", response)


if __name__ == "__main__":
    unittest.main()

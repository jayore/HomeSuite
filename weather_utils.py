"""Parse weather requests and adapt Home Assistant/Open-Meteo data.

Current local conditions prefer Home Assistant. Future local forecasts prefer
Home Assistant's ``weather.get_forecasts`` response and fall back to
Open-Meteo when coordinates are configured. Named-place requests use
Open-Meteo after geocoding so their dates are evaluated in the place's own
timezone.

This module owns weather-specific parsing, provider normalization, caching,
and concise spoken formatting. Command dispatch remains responsible for
conversation memory and user-facing provider errors.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
import re
import time
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

import requests

from app_config import LOCATION_ALIASES, WEATHER_ENTITY_ID


try:
    import ha_client
except Exception:
    ha_client = None


WEATHER_CACHE_TTL_SECONDS = 10 * 60
MAX_FORECAST_DAYS = 14

_OPEN_METEO_CACHE = {}
_HA_FORECAST_CACHE = {}

_WEEKDAYS = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}
_WEEKDAY_PATTERN = "|".join(_WEEKDAYS)
_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
}
_COUNT_PATTERN = r"\d+|" + "|".join(_NUMBER_WORDS)

_WMO_CONDITIONS = {
    0: "clear",
    1: "mostly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "foggy",
    48: "foggy",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "freezing drizzle",
    57: "freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light rain showers",
    81: "rain showers",
    82: "heavy rain showers",
    85: "snow showers",
    86: "heavy snow showers",
    95: "thunderstorms",
    96: "thunderstorms with hail",
    99: "thunderstorms with hail",
}

_HA_CONDITIONS = {
    "clear-night": "clear",
    "partlycloudy": "partly cloudy",
    "lightning": "thunderstorms",
    "lightning-rainy": "thunderstorms with rain",
    "pouring": "heavy rain",
    "rainy": "rain",
    "snowy": "snow",
    "snowy-rainy": "rain and snow",
    "sunny": "sunny",
    "windy-variant": "partly cloudy and windy",
}


@dataclass(frozen=True)
class WeatherQuery:
    """A deterministic weather intent independent of any provider."""

    location: Optional[str] = None
    mode: str = "current"  # current, day, or range
    day_offset: Optional[int] = None
    weekday: Optional[int] = None
    next_weekday: bool = False
    days: int = 1


@dataclass(frozen=True)
class DailyForecast:
    forecast_date: date
    condition: Optional[str] = None
    high_f: Optional[float] = None
    low_f: Optional[float] = None
    precipitation_probability: Optional[int] = None


@dataclass(frozen=True)
class OpenMeteoReport:
    current_temperature_f: Optional[float]
    current_condition: Optional[str]
    daily: tuple[DailyForecast, ...]
    timezone: Optional[str]


def _location_clean_for_geo(loc: str) -> str:
    """Normalize a spoken location before geocoding it."""
    s = (loc or "").strip()
    s = s.replace("?", " ").replace("!", " ").replace(".", " ").replace(",", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"^(in|at)\s+", "", s, flags=re.IGNORECASE).strip()


def _parse_count(value: Optional[str], default: int = 7) -> int:
    raw = (value or "").strip().lower()
    if not raw:
        return default
    try:
        count = int(raw)
    except ValueError:
        count = _NUMBER_WORDS.get(raw, default)
    return max(1, min(MAX_FORECAST_DAYS, count))


def _extract_weather_location(text: str) -> Optional[str]:
    """Extract an explicit ``in PLACE`` while removing date qualifiers."""
    match = re.search(r"\bin\s+(.+)$", text)
    if not match:
        return None

    location = match.group(1).strip()
    range_patterns = (
        rf"\b(?:for\s+)?(?:this\s+week|(?:the\s+)?next\s+"
        rf"(?:week|(?:{_COUNT_PATTERN})\s+days?))\b",
        rf"\b(?:for\s+)?(?:{_COUNT_PATTERN})\s*[- ]day\s+forecast\b",
    )
    for pattern in range_patterns:
        location = re.sub(pattern, " ", location)

    location = re.sub(r"\b(?:for|on)\s+(?:today|tomorrow)\b", " ", location)
    location = re.sub(r"\b(?:today|tomorrow)\b", " ", location)
    location = re.sub(
        rf"\b(?:for|on)\s+(?:next\s+)?(?:{_WEEKDAY_PATTERN})\b",
        " ",
        location,
    )
    location = re.sub(
        rf"\b(?:next\s+)?(?:{_WEEKDAY_PATTERN})\b$",
        " ",
        location,
    )
    location = re.sub(r"\s+", " ", location).strip(" ,.?!")
    location = re.sub(r"\b(?:for|on|the)\s*$", "", location).strip()

    if location in {"there", "over there", "here", "in there"}:
        return None
    return location or None


def parse_weather_query(text: str) -> Optional[WeatherQuery]:
    """Parse supported current, day, weekday, and multi-day weather phrases."""
    normalized = re.sub(r"[^a-z0-9'\s-]+", " ", (text or "").lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not re.search(r"\b(?:weather|forecast)\b", normalized):
        return None

    location = _extract_weather_location(normalized)

    if re.search(r"\b(?:this|(?:the\s+)?next)\s+week\b", normalized):
        return WeatherQuery(location=location, mode="range", days=7)

    range_match = re.search(
        rf"\b(?P<count>{_COUNT_PATTERN})\s*[- ]day\s+forecast\b",
        normalized,
    )
    if not range_match:
        range_match = re.search(
            rf"\bforecast(?:\s+for)?(?:\s+the)?\s+(?:next\s+)?"
            rf"(?P<count>{_COUNT_PATTERN})\s+days?\b",
            normalized,
        )
    if not range_match:
        range_match = re.search(
            rf"\bnext\s+(?P<count>{_COUNT_PATTERN})\s+days?(?:\s+forecast)?\b",
            normalized,
        )
    if range_match:
        days = _parse_count(range_match.group("count"))
        if days > 1:
            return WeatherQuery(location=location, mode="range", days=days)
        return WeatherQuery(location=location, mode="day", day_offset=0)

    if re.search(r"\btomorrow\b", normalized):
        return WeatherQuery(location=location, mode="day", day_offset=1)
    if re.search(r"\btoday\b", normalized):
        return WeatherQuery(location=location, mode="day", day_offset=0)

    weekday_match = re.search(
        rf"\b(?P<next>next\s+)?(?P<weekday>{_WEEKDAY_PATTERN})\b",
        normalized,
    )
    if weekday_match:
        return WeatherQuery(
            location=location,
            mode="day",
            weekday=_WEEKDAYS[weekday_match.group("weekday")],
            next_weekday=bool(weekday_match.group("next")),
        )

    # A bare forecast asks for today's high/low. A bare weather request keeps
    # the established current-conditions behavior.
    if re.search(r"\bforecast\b", normalized):
        return WeatherQuery(location=location, mode="day", day_offset=0)
    return WeatherQuery(location=location, mode="current")


def _now_for_timezone(timezone_name: Optional[str], now: Optional[datetime]) -> datetime:
    tz = None
    if timezone_name:
        try:
            tz = ZoneInfo(timezone_name)
        except Exception:
            tz = None

    if now is None:
        return datetime.now(tz) if tz else datetime.now().astimezone()
    if tz and now.tzinfo is not None:
        return now.astimezone(tz)
    if tz:
        return now.replace(tzinfo=tz)
    return now


def weather_query_dates(
    query: WeatherQuery,
    *,
    timezone_name: Optional[str] = None,
    now: Optional[datetime] = None,
) -> list[date]:
    """Resolve a parsed query to calendar dates in the target timezone."""
    today = _now_for_timezone(timezone_name, now).date()
    if query.mode == "range":
        return [today + timedelta(days=i) for i in range(max(1, query.days))]
    if query.weekday is not None:
        delta = (query.weekday - today.weekday()) % 7
        if query.next_weekday:
            delta += 7
        return [today + timedelta(days=delta)]
    return [today + timedelta(days=query.day_offset or 0)]


def forecast_days_needed(
    query: WeatherQuery,
    *,
    timezone_name: Optional[str] = None,
    now: Optional[datetime] = None,
) -> int:
    today = _now_for_timezone(timezone_name, now).date()
    targets = weather_query_dates(query, timezone_name=timezone_name, now=now)
    furthest = max((target - today).days for target in targets)
    return max(1, min(16, furthest + 1))


def geocode_location(loc: str) -> Optional[dict]:
    """Use Open-Meteo geocoding and return lat/lon/display/timezone."""
    loc = _location_clean_for_geo(loc)
    if not loc:
        return None

    loc = (LOCATION_ALIASES or {}).get(loc.lower(), loc)
    params = {"name": loc, "count": 1, "language": "en", "format": "json"}
    try:
        response = requests.get(
            "https://geocoding-api.open-meteo.com/v1/search",
            params=params,
            timeout=10,
        )
        if response.status_code != 200:
            return None
        results = (response.json() or {}).get("results") or []
        if not results:
            return None
        top = results[0]
        name = top.get("name") or loc
        admin1 = top.get("admin1") or ""
        country = top.get("country") or ""

        parts = [str(name).strip()]
        if admin1 and str(admin1).strip().lower() != str(name).strip().lower():
            parts.append(str(admin1).strip())
        if country:
            parts.append(str(country).strip())

        return {
            "lat": float(top["latitude"]),
            "lon": float(top["longitude"]),
            "display": ", ".join(part for part in parts if part),
            "timezone": top.get("timezone") or None,
        }
    except Exception:
        return None


def ha_get_states():
    """Compatibility shim around the configured Home Assistant client."""
    try:
        fn = getattr(ha_client, "ha_get_states", None) if ha_client else None
        return fn() if callable(fn) else None
    except Exception:
        return None


def _configured_weather_entity(states: Optional[Sequence[dict]]) -> Optional[dict]:
    weather_states = [
        state
        for state in (states or [])
        if isinstance(state, dict)
        and isinstance(state.get("entity_id"), str)
        and state["entity_id"].startswith("weather.")
    ]

    configured = str(WEATHER_ENTITY_ID or "").strip()
    if configured:
        for state in weather_states:
            if state.get("entity_id") == configured:
                return state
        return {"entity_id": configured, "attributes": {}}

    for state in weather_states:
        if "temperature" in (state.get("attributes") or {}):
            return state
    return weather_states[0] if weather_states else None


def _is_celsius(unit: Optional[str]) -> bool:
    normalized = str(unit or "").strip().lower()
    return normalized in {"c", "celsius", "degc", "degree c", "degrees c"} or "°c" in normalized


def _float_or_none(value) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _int_or_none(value) -> Optional[int]:
    try:
        return int(round(float(value))) if value is not None else None
    except (TypeError, ValueError):
        return None


def _to_fahrenheit(value, unit: Optional[str]) -> Optional[float]:
    parsed = _float_or_none(value)
    if parsed is None:
        return None
    return (parsed * 9.0 / 5.0) + 32.0 if _is_celsius(unit) else parsed


def _human_condition(condition: Optional[str]) -> Optional[str]:
    raw = str(condition or "").strip().lower()
    if not raw or raw == "unknown":
        return None
    return _HA_CONDITIONS.get(raw, raw.replace("-", " ").replace("_", " "))


def _ha_local_weather(states: Optional[Sequence[dict]] = None) -> Optional[str]:
    """Return current local conditions from Home Assistant when available."""
    chosen = _configured_weather_entity(states if states is not None else ha_get_states())
    if not chosen:
        return None

    attrs = chosen.get("attributes") or {}
    unit = attrs.get("temperature_unit") or attrs.get("unit_of_measurement") or "°F"
    temp = attrs.get("temperature")
    condition = _human_condition(chosen.get("state"))

    # Older HA providers may still expose today's forecast inline. Retain that
    # compatibility while modern future requests use weather.get_forecasts.
    high = low = precip = None
    inline = attrs.get("forecast")
    if isinstance(inline, list) and inline and isinstance(inline[0], dict):
        high = inline[0].get("temperature")
        low = inline[0].get("templow")
        precip = inline[0].get("precipitation_probability")

    if _is_celsius(unit):
        temp = _to_fahrenheit(temp, unit)
        high = _to_fahrenheit(high, unit)
        low = _to_fahrenheit(low, unit)
        unit = "°F"

    parts = []
    if temp is not None:
        parts.append(f"It's {temp}{unit} right now")
    if condition:
        parts.append(condition)
    if high is not None and low is not None:
        parts.append(f"High {high}{unit}, low {low}{unit}")
    if precip is not None:
        parts.append(f"Max precip chance {precip} percent")
    return ". ".join(parts) + "." if parts else None


def _cache_get(cache: dict, key):
    row = cache.get(key)
    if not row:
        return None
    cached_at, value = row
    if (time.monotonic() - cached_at) > WEATHER_CACHE_TTL_SECONDS:
        cache.pop(key, None)
        return None
    return value


def _cache_set(cache: dict, key, value):
    cache[key] = (time.monotonic(), value)
    return value


def _clear_weather_cache() -> None:
    """Clear provider caches; primarily useful to focused tests and diagnostics."""
    _OPEN_METEO_CACHE.clear()
    _HA_FORECAST_CACHE.clear()


def _parse_forecast_date(value) -> Optional[date]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _normalize_daily_rows(rows: Sequence[dict], temperature_unit: Optional[str]) -> list[DailyForecast]:
    forecasts = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        forecast_date = _parse_forecast_date(row.get("datetime") or row.get("date") or row.get("time"))
        if not forecast_date:
            continue
        forecasts.append(
            DailyForecast(
                forecast_date=forecast_date,
                condition=_human_condition(row.get("condition")),
                high_f=_to_fahrenheit(row.get("temperature"), temperature_unit),
                low_f=_to_fahrenheit(row.get("templow"), temperature_unit),
                precipitation_probability=_int_or_none(row.get("precipitation_probability")),
            )
        )
    return forecasts


def _ha_daily_forecasts(
    states: Optional[Sequence[dict]] = None,
) -> Optional[list[DailyForecast]]:
    """Fetch normalized daily forecasts from Home Assistant."""
    chosen = _configured_weather_entity(states if states is not None else ha_get_states())
    if not chosen or not ha_client:
        return None

    entity_id = str(chosen.get("entity_id") or "").strip()
    if not entity_id:
        return None
    attrs = chosen.get("attributes") or {}
    unit = attrs.get("temperature_unit") or attrs.get("unit_of_measurement") or "°F"
    cache_key = (entity_id, str(unit))
    cached = _cache_get(_HA_FORECAST_CACHE, cache_key)
    if cached is not None:
        return list(cached)

    try:
        rows = ha_client.ha_get_weather_forecasts(entity_id, forecast_type="daily")
    except Exception:
        logging.exception("Home Assistant daily forecast request failed")
        return None
    forecasts = _normalize_daily_rows(rows or [], unit)
    if not forecasts:
        return None
    _cache_set(_HA_FORECAST_CACHE, cache_key, tuple(forecasts))
    return forecasts


def _open_meteo_report(lat: float, lon: float, *, forecast_days: int = 7) -> Optional[OpenMeteoReport]:
    """Fetch current conditions and normalized daily forecasts from Open-Meteo."""
    days = max(1, min(16, int(forecast_days or 1)))
    cache_key = (round(float(lat), 4), round(float(lon), 4), days)
    cached = _cache_get(_OPEN_METEO_CACHE, cache_key)
    if cached is not None:
        return cached

    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,weather_code",
        "daily": (
            "weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max,precipitation_sum"
        ),
        "forecast_days": days,
        "timezone": "auto",
        "temperature_unit": "fahrenheit",
        "windspeed_unit": "mph",
        "precipitation_unit": "inch",
    }
    try:
        response = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params=params,
            timeout=10,
        )
        if response.status_code != 200:
            return None
        data = response.json() or {}
        current = data.get("current") or {}
        daily = data.get("daily") or {}

        dates = daily.get("time") or []
        codes = daily.get("weather_code") or []
        highs = daily.get("temperature_2m_max") or []
        lows = daily.get("temperature_2m_min") or []
        probabilities = daily.get("precipitation_probability_max") or []

        forecasts = []
        for index, raw_date in enumerate(dates):
            forecast_date = _parse_forecast_date(raw_date)
            if not forecast_date:
                continue
            code = _int_or_none(codes[index]) if index < len(codes) else None
            forecasts.append(
                DailyForecast(
                    forecast_date=forecast_date,
                    condition=_WMO_CONDITIONS.get(code) if code is not None else None,
                    high_f=_float_or_none(highs[index]) if index < len(highs) else None,
                    low_f=_float_or_none(lows[index]) if index < len(lows) else None,
                    precipitation_probability=(
                        _int_or_none(probabilities[index])
                        if index < len(probabilities)
                        else None
                    ),
                )
            )

        current_code = _int_or_none(current.get("weather_code"))
        report = OpenMeteoReport(
            current_temperature_f=_float_or_none(current.get("temperature_2m")),
            current_condition=_WMO_CONDITIONS.get(current_code) if current_code is not None else None,
            daily=tuple(forecasts),
            timezone=data.get("timezone") or None,
        )
        return _cache_set(_OPEN_METEO_CACHE, cache_key, report)
    except Exception:
        return None


def _open_meteo_weather(lat: float, lon: float) -> Optional[str]:
    """Compatibility wrapper for the established current-weather response."""
    report = _open_meteo_report(lat, lon, forecast_days=1)
    if not report:
        return None
    first = report.daily[0] if report.daily else None
    parts = []
    if report.current_temperature_f is not None:
        parts.append(f"It's {round(report.current_temperature_f, 1)} degrees right now")
    if report.current_condition:
        parts.append(report.current_condition)
    if first and first.high_f is not None and first.low_f is not None:
        parts.append(f"High {round(first.high_f, 1)}, low {round(first.low_f, 1)}")
    if first and first.precipitation_probability is not None:
        parts.append(f"Max precip chance {first.precipitation_probability} percent")
    return ". ".join(parts) + "." if parts else None


def _temperature_text(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    return str(int(round(value)))


def _day_label(target: date, today: date) -> str:
    if target == today:
        return "Today"
    if target == today + timedelta(days=1):
        return "Tomorrow"
    return target.strftime("%A")


def _daily_spoken_part(forecast: DailyForecast, label: str, *, include_precip: bool) -> Optional[str]:
    details = []
    if forecast.condition:
        details.append(forecast.condition)
    high = _temperature_text(forecast.high_f)
    low = _temperature_text(forecast.low_f)
    if high is not None:
        details.append(f"high {high}")
    if low is not None:
        details.append(f"low {low}")
    probability = forecast.precipitation_probability
    if include_precip and probability is not None and probability > 0:
        details.append(f"{probability} percent chance of precipitation")
    if not details:
        return None
    return f"{label}: " + ", ".join(details)


def format_forecast_response(
    query: WeatherQuery,
    forecasts: Sequence[DailyForecast],
    *,
    timezone_name: Optional[str] = None,
    now: Optional[datetime] = None,
    allow_partial: bool = False,
) -> Optional[str]:
    """Format normalized daily rows as a concise TTS-friendly response."""
    today = _now_for_timezone(timezone_name, now).date()
    target_dates = weather_query_dates(query, timezone_name=timezone_name, now=now)
    by_date = {forecast.forecast_date: forecast for forecast in forecasts or []}
    selected = [by_date[target] for target in target_dates if target in by_date]
    if not selected or (not allow_partial and len(selected) != len(target_dates)):
        return None

    if query.mode == "day":
        forecast = selected[0]
        part = _daily_spoken_part(
            forecast,
            _day_label(forecast.forecast_date, today),
            include_precip=True,
        )
        return f"{part}." if part else None

    parts = []
    for forecast in selected:
        part = _daily_spoken_part(
            forecast,
            _day_label(forecast.forecast_date, today),
            include_precip=(forecast.precipitation_probability or 0) >= 20,
        )
        if part:
            parts.append(part)
    if not parts:
        return None

    count_words = {
        2: "Two",
        3: "Three",
        4: "Four",
        5: "Five",
        6: "Six",
        7: "Seven",
        8: "Eight",
        9: "Nine",
        10: "Ten",
    }
    count = count_words.get(len(parts), str(len(parts)))
    return f"{count}-day forecast. " + "; ".join(parts) + "."

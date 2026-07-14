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
from collections import Counter
import logging
import math
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
    mode: str = "current"  # current, day, range, or hourly
    day_offset: Optional[int] = None
    weekday: Optional[int] = None
    next_weekday: bool = False
    days: int = 1
    hours: int = 6
    period: Optional[str] = None  # tonight, weekend, or next_weekend
    focus: str = "summary"  # summary or precipitation
    phenomenon: str = "precipitation"  # rain, snow, or precipitation


@dataclass(frozen=True)
class DailyForecast:
    forecast_date: date
    condition: Optional[str] = None
    high_f: Optional[float] = None
    low_f: Optional[float] = None
    precipitation_probability: Optional[int] = None


@dataclass(frozen=True)
class HourlyForecast:
    forecast_time: datetime
    condition: Optional[str] = None
    temperature_f: Optional[float] = None
    precipitation_probability: Optional[int] = None


@dataclass(frozen=True)
class OpenMeteoReport:
    current_temperature_f: Optional[float]
    current_condition: Optional[str]
    daily: tuple[DailyForecast, ...]
    hourly: tuple[HourlyForecast, ...]
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
        r"\b(?:for\s+)?(?:this|next)\s+weekend\b",
        rf"\b(?:for\s+)?(?:the\s+)?next\s+(?:{_COUNT_PATTERN})\s+hours?\b",
    )
    for pattern in range_patterns:
        location = re.sub(pattern, " ", location)

    location = re.sub(r"\b(?:for|on)\s+(?:today|tomorrow)\b", " ", location)
    location = re.sub(r"\b(?:today|tomorrow|tonight)\b", " ", location)
    location = re.sub(r"\b(?:this|next)\s+weekend\b", " ", location)
    location = re.sub(r"\b(?:hourly\s+forecast|later\s+today)\b", " ", location)
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


def _parse_hour_count(value: Optional[str], default: int = 6) -> int:
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    try:
        count = int(raw)
    except ValueError:
        count = _NUMBER_WORDS.get(raw, default)
    return max(1, min(24, count))


def parse_weather_query(text: str) -> Optional[WeatherQuery]:
    """Parse current, hourly, calendar-day, range, and precipitation requests."""
    normalized = re.sub(r"[^a-z0-9'\s-]+", " ", (text or "").lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()

    precipitation_question = bool(
        re.search(
            r"\b(?:will|is)\s+it\s+(?:going\s+to\s+)?(?:rain|snow)\b|"
            r"\b(?:chance|chances|risk)\s+of\s+(?:rain|snow|precipitation)\b|"
            r"\bdo\s+i\s+need\s+(?:an\s+)?umbrella\b",
            normalized,
        )
    )
    if not re.search(r"\b(?:weather|forecast)\b", normalized) and not precipitation_question:
        return None

    location = _extract_weather_location(normalized)
    focus = "precipitation" if precipitation_question else "summary"
    if re.search(r"\bsnow\b", normalized):
        phenomenon = "snow"
    elif re.search(r"\brain\b|\bumbrella\b", normalized):
        phenomenon = "rain"
    else:
        phenomenon = "precipitation"

    hourly_match = re.search(
        rf"\b(?:the\s+)?next\s+(?P<count>{_COUNT_PATTERN})\s+hours?\b",
        normalized,
    )
    if hourly_match or re.search(r"\bhourly\s+(?:weather|forecast)\b", normalized):
        return WeatherQuery(
            location=location,
            mode="hourly",
            hours=_parse_hour_count(hourly_match.group("count") if hourly_match else None),
            focus=focus,
            phenomenon=phenomenon,
        )

    if re.search(r"\btonight\b|\blater\s+today\b", normalized):
        return WeatherQuery(
            location=location,
            mode="hourly",
            hours=12,
            period="tonight",
            focus=focus,
            phenomenon=phenomenon,
        )

    weekend_match = re.search(r"\b(?P<which>this|next)?\s*weekend\b", normalized)
    if weekend_match:
        period = "next_weekend" if weekend_match.group("which") == "next" else "weekend"
        return WeatherQuery(
            location=location,
            mode="range",
            days=2,
            period=period,
            focus=focus,
            phenomenon=phenomenon,
        )

    if re.search(r"\b(?:this|(?:the\s+)?next)\s+week\b", normalized):
        return WeatherQuery(
            location=location,
            mode="range",
            days=7,
            focus=focus,
            phenomenon=phenomenon,
        )

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
            return WeatherQuery(
                location=location,
                mode="range",
                days=days,
                focus=focus,
                phenomenon=phenomenon,
            )
        return WeatherQuery(
            location=location,
            mode="day",
            day_offset=0,
            focus=focus,
            phenomenon=phenomenon,
        )

    if re.search(r"\btomorrow\b", normalized):
        return WeatherQuery(
            location=location,
            mode="day",
            day_offset=1,
            focus=focus,
            phenomenon=phenomenon,
        )
    if re.search(r"\btoday\b", normalized):
        return WeatherQuery(
            location=location,
            mode="day",
            day_offset=0,
            focus=focus,
            phenomenon=phenomenon,
        )

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
            focus=focus,
            phenomenon=phenomenon,
        )

    # A bare forecast asks for today's high/low. A bare weather request keeps
    # the established current-conditions behavior.
    if re.search(r"\bforecast\b", normalized):
        return WeatherQuery(
            location=location,
            mode="day",
            day_offset=0,
            focus=focus,
            phenomenon=phenomenon,
        )
    if precipitation_question:
        return WeatherQuery(
            location=location,
            mode="day",
            day_offset=0,
            focus=focus,
            phenomenon=phenomenon,
        )
    return WeatherQuery(location=location, mode="current")


def looks_like_weather_query(text: str) -> bool:
    """Return whether text belongs to the deterministic weather surface."""
    return parse_weather_query(text) is not None


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
    if query.period in {"weekend", "next_weekend"}:
        if today.weekday() == 6 and query.period == "weekend":
            return [today]
        days_until_saturday = (5 - today.weekday()) % 7
        if query.period == "next_weekend":
            days_until_saturday += 7
        saturday = today + timedelta(days=days_until_saturday)
        return [saturday, saturday + timedelta(days=1)]
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
    now_local = _now_for_timezone(timezone_name, now)
    today = now_local.date()
    if query.mode == "hourly" and query.period != "tonight":
        hours_to_cover = max(1, int(query.hours or 6))
        return max(1, min(16, int(math.ceil((now_local.hour + hours_to_cover + 1) / 24.0))))
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


def _parse_forecast_datetime(
    value,
    *,
    timezone_name: Optional[str] = None,
) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None and timezone_name:
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
        except Exception:
            pass
    return parsed


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


def _normalize_hourly_rows(
    rows: Sequence[dict],
    temperature_unit: Optional[str],
    *,
    timezone_name: Optional[str] = None,
) -> list[HourlyForecast]:
    forecasts = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        forecast_time = _parse_forecast_datetime(
            row.get("datetime") or row.get("time"),
            timezone_name=timezone_name,
        )
        if not forecast_time:
            continue
        forecasts.append(
            HourlyForecast(
                forecast_time=forecast_time,
                condition=_human_condition(row.get("condition")),
                temperature_f=_to_fahrenheit(row.get("temperature"), temperature_unit),
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
    cache_key = (entity_id, str(unit), "daily")
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


def _ha_hourly_forecasts(
    states: Optional[Sequence[dict]] = None,
) -> Optional[list[HourlyForecast]]:
    """Fetch normalized hourly forecasts from Home Assistant."""
    chosen = _configured_weather_entity(states if states is not None else ha_get_states())
    if not chosen or not ha_client:
        return None

    entity_id = str(chosen.get("entity_id") or "").strip()
    if not entity_id:
        return None
    attrs = chosen.get("attributes") or {}
    unit = attrs.get("temperature_unit") or attrs.get("unit_of_measurement") or "°F"
    timezone_name = attrs.get("timezone") or None
    cache_key = (entity_id, str(unit), "hourly")
    cached = _cache_get(_HA_FORECAST_CACHE, cache_key)
    if cached is not None:
        return list(cached)

    try:
        rows = ha_client.ha_get_weather_forecasts(entity_id, forecast_type="hourly")
    except Exception:
        logging.exception("Home Assistant hourly forecast request failed")
        return None
    forecasts = _normalize_hourly_rows(
        rows or [],
        unit,
        timezone_name=timezone_name,
    )
    if not forecasts:
        return None
    _cache_set(_HA_FORECAST_CACHE, cache_key, tuple(forecasts))
    return forecasts


def _open_meteo_report(lat: float, lon: float, *, forecast_days: int = 7) -> Optional[OpenMeteoReport]:
    """Fetch current, hourly, and daily forecasts from Open-Meteo."""
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
        "hourly": "temperature_2m,weather_code,precipitation_probability",
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
        hourly = data.get("hourly") or {}
        timezone_name = data.get("timezone") or None

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

        hourly_times = hourly.get("time") or []
        hourly_codes = hourly.get("weather_code") or []
        hourly_temperatures = hourly.get("temperature_2m") or []
        hourly_probabilities = hourly.get("precipitation_probability") or []
        hourly_forecasts = []
        for index, raw_time in enumerate(hourly_times):
            forecast_time = _parse_forecast_datetime(
                raw_time,
                timezone_name=timezone_name,
            )
            if not forecast_time:
                continue
            code = _int_or_none(hourly_codes[index]) if index < len(hourly_codes) else None
            hourly_forecasts.append(
                HourlyForecast(
                    forecast_time=forecast_time,
                    condition=_WMO_CONDITIONS.get(code) if code is not None else None,
                    temperature_f=(
                        _float_or_none(hourly_temperatures[index])
                        if index < len(hourly_temperatures)
                        else None
                    ),
                    precipitation_probability=(
                        _int_or_none(hourly_probabilities[index])
                        if index < len(hourly_probabilities)
                        else None
                    ),
                )
            )

        current_code = _int_or_none(current.get("weather_code"))
        report = OpenMeteoReport(
            current_temperature_f=_float_or_none(current.get("temperature_2m")),
            current_condition=_WMO_CONDITIONS.get(current_code) if current_code is not None else None,
            daily=tuple(forecasts),
            hourly=tuple(hourly_forecasts),
            timezone=timezone_name,
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


def _daily_spoken_part(
    forecast: DailyForecast,
    label: str,
    *,
    include_precip: bool,
    include_high: bool = True,
) -> Optional[str]:
    details = []
    if forecast.condition:
        details.append(forecast.condition)
    high = _temperature_text(forecast.high_f)
    low = _temperature_text(forecast.low_f)
    if include_high and high is not None:
        details.append(f"high {high}")
    if low is not None:
        details.append(f"low {low}")
    probability = forecast.precipitation_probability
    if include_precip and probability is not None and probability > 0:
        details.append(f"{probability} percent chance of precipitation")
    if not details:
        return None
    return f"{label}: " + ", ".join(details)


def _condition_matches_phenomenon(condition: Optional[str], phenomenon: str) -> bool:
    condition = str(condition or "").lower()
    if phenomenon == "snow":
        return "snow" in condition
    if phenomenon == "rain":
        return any(word in condition for word in ("rain", "drizzle", "shower", "thunderstorm"))
    return any(
        word in condition
        for word in ("rain", "drizzle", "shower", "thunderstorm", "snow", "sleet")
    )


def _single_precipitation_response(
    forecast: DailyForecast,
    *,
    label: str,
    phenomenon: str,
) -> Optional[str]:
    probability = forecast.precipitation_probability
    matches = _condition_matches_phenomenon(forecast.condition, phenomenon)
    noun = phenomenon if phenomenon in {"rain", "snow"} else "precipitation"

    if probability is not None:
        if probability >= 50 or matches:
            lead = "Yes" if matches or probability >= 60 else "Likely"
        elif probability >= 20:
            lead = "Possibly"
        else:
            lead = "Probably not"
        return f"{lead}. {label} has a {probability} percent chance of {noun}."

    if forecast.condition:
        if matches:
            return f"Yes. The forecast for {label.lower()} calls for {forecast.condition}."
        return f"Probably not. The forecast for {label.lower()} is {forecast.condition}."
    return None


def _range_precipitation_response(
    forecasts: Sequence[DailyForecast],
    *,
    today: date,
    phenomenon: str,
    period: Optional[str],
) -> Optional[str]:
    noun = phenomenon if phenomenon in {"rain", "snow"} else "precipitation"
    parts = []
    for forecast in forecasts:
        label = _day_label(forecast.forecast_date, today)
        probability = forecast.precipitation_probability
        if probability is not None:
            parts.append(f"{label} has a {probability} percent chance of {noun}")
        elif forecast.condition:
            parts.append(f"{label} is forecast to be {forecast.condition}")
    if not parts:
        return None
    prefix = "This weekend" if period == "weekend" else "Next weekend" if period == "next_weekend" else "Forecast"
    return f"{prefix}. " + "; ".join(parts) + "."


def _hour_in_timezone(
    forecast: HourlyForecast,
    timezone_name: Optional[str],
    now_local: datetime,
) -> datetime:
    value = forecast.forecast_time
    if timezone_name and value.tzinfo is not None:
        try:
            return value.astimezone(ZoneInfo(timezone_name))
        except Exception:
            pass
    if value.tzinfo is not None and now_local.tzinfo is not None:
        return value.astimezone(now_local.tzinfo)
    if value.tzinfo is None and now_local.tzinfo is not None:
        return value.replace(tzinfo=now_local.tzinfo)
    return value


def _clock_label(value: datetime) -> str:
    try:
        return value.strftime("%-I %p")
    except Exception:
        return value.strftime("%I %p").lstrip("0")


def format_hourly_response(
    query: WeatherQuery,
    forecasts: Sequence[HourlyForecast],
    *,
    timezone_name: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Format hourly rows for next-hours and tonight requests."""
    now_local = _now_for_timezone(timezone_name, now)
    localized = [
        (row, _hour_in_timezone(row, timezone_name, now_local))
        for row in forecasts or []
    ]

    if query.period == "tonight":
        start = now_local.replace(hour=18, minute=0, second=0, microsecond=0)
        if now_local >= start:
            start = now_local
        end = (now_local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        selected = [(row, when) for row, when in localized if start <= when < end]
    else:
        future = [(row, when) for row, when in localized if when >= now_local]
        selected = future[: max(1, int(query.hours or 6))]

    if not selected:
        return None

    probabilities = [
        row.precipitation_probability
        for row, _ in selected
        if row.precipitation_probability is not None
    ]
    max_probability = max(probabilities) if probabilities else None
    conditions = [row.condition for row, _ in selected if row.condition]
    common_condition = Counter(conditions).most_common(1)[0][0] if conditions else None

    if query.focus == "precipitation":
        synthetic = DailyForecast(
            forecast_date=now_local.date(),
            condition=common_condition,
            precipitation_probability=max_probability,
        )
        label = "Tonight" if query.period == "tonight" else f"The next {len(selected)} hours"
        return _single_precipitation_response(
            synthetic,
            label=label,
            phenomenon=query.phenomenon,
        )

    if query.period == "tonight":
        details = []
        if common_condition:
            details.append(common_condition)
        temperatures = [row.temperature_f for row, _ in selected if row.temperature_f is not None]
        if temperatures:
            details.append(f"low {int(round(min(temperatures)))}")
        if max_probability is not None and max_probability > 0:
            details.append(f"up to a {max_probability} percent chance of precipitation")
        return "Tonight: " + ", ".join(details) + "." if details else None

    step = max(1, int(math.ceil(len(selected) / 4.0)))
    samples = selected[::step][:4]
    parts = []
    for row, when in samples:
        details = []
        if row.temperature_f is not None:
            details.append(f"{int(round(row.temperature_f))} degrees")
        if row.condition:
            details.append(row.condition)
        if details:
            parts.append(f"{_clock_label(when)}: " + ", ".join(details))
    if not parts:
        return None
    prefix = f"Next {len(selected)} hour" + ("" if len(selected) == 1 else "s")
    suffix = ""
    if max_probability is not None and max_probability >= 20:
        suffix = f" Rain chance peaks at {max_probability} percent."
    return f"{prefix}. " + "; ".join(parts) + "." + suffix


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

    if query.focus == "precipitation":
        if len(selected) == 1:
            return _single_precipitation_response(
                selected[0],
                label=_day_label(selected[0].forecast_date, today),
                phenomenon=query.phenomenon,
            )
        return _range_precipitation_response(
            selected,
            today=today,
            phenomenon=query.phenomenon,
            period=query.period,
        )

    if query.mode == "hourly" and query.period == "tonight":
        part = _daily_spoken_part(
            selected[0],
            "Tonight",
            include_precip=True,
            include_high=False,
        )
        return f"{part}." if part else None

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
    if query.period == "weekend":
        prefix = "This weekend"
    elif query.period == "next_weekend":
        prefix = "Next weekend"
    else:
        count = count_words.get(len(parts), str(len(parts)))
        prefix = f"{count}-day forecast"
    return prefix + ". " + "; ".join(parts) + "."

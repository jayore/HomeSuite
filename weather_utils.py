"""Weather helpers extracted from main.py.

- geocode_location(): geocode a user-provided location string
- _ha_local_weather(): try HA-local weather first (if configured)
- _open_meteo_weather(): fallback to Open-Meteo API using lat/lon
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Optional

import requests
from app_config import LOCATION_ALIASES

# Normalize a location string before sending to geocoding
def _location_clean_for_geo(loc: str) -> str:
    s = (loc or "").strip()
    # strip common punctuation / trailing question marks
    s = s.replace("?", " ").replace("!", " ").replace(".", " ").replace(",", " ")
    # collapse whitespace
    try:
        import re
        s = re.sub(r"\s+", " ", s).strip()
        # if user says "in seattle" or "at seattle", drop the leading preposition
        s = re.sub(r"^(in|at)\s+", "", s, flags=re.IGNORECASE).strip()
    except Exception:
        pass
    return s


# We rely on ha_client for HA calls/headers/session where relevant.
# (This avoids importing gpio_ptt and creating circular imports.)
try:
    import ha_client
except Exception:
    ha_client = None

# Local shim: gpio_ptt previously provided ha_get_states in module scope.
# After extraction, use ha_client.ha_get_states().
def ha_get_states():
    try:
        if ha_client is None:
            return None
        fn = getattr(ha_client, "ha_get_states", None)
        if callable(fn):
            return fn()
    except Exception:
        return None
    return None

def geocode_location(loc: str) -> Optional[dict]:
    """
    Uses Open-Meteo geocoding. Returns dict with lat/lon/display/timezone.
    """
    loc = _location_clean_for_geo(loc)
    if not loc:
        return None

    loc = (LOCATION_ALIASES or {}).get(loc.lower(), loc)

    url = "https://geocoding-api.open-meteo.com/v1/search"
    params = {"name": loc, "count": 1, "language": "en", "format": "json"}
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json() or {}
        results = data.get("results") or []
        if not results:
            return None
        top = results[0]
        name = top.get("name") or loc
        admin1 = top.get("admin1") or ""
        country = top.get("country") or ""
        timezone = top.get("timezone") or None

        # Dedupe: "Tokyo" + admin1 "Tokyo" -> just "Tokyo"
        parts = [str(name).strip()]
        if admin1 and str(admin1).strip().lower() != str(name).strip().lower():
            parts.append(str(admin1).strip())
        if country:
            parts.append(str(country).strip())

        display = ", ".join([p for p in parts if p])
        return {
            "lat": float(top["latitude"]),
            "lon": float(top["longitude"]),
            "display": display,
            "timezone": timezone,
        }
    except Exception:
        return None


def _ha_local_weather() -> Optional[str]:
    """
    Try to pull local weather from HA's weather.* entities (fast, local, consistent).
    """
    states = ha_get_states()
    if not states:
        return None

    weather_entities = [s for s in states if isinstance(s.get("entity_id"), str) and s["entity_id"].startswith("weather.")]
    if not weather_entities:
        return None

    chosen = None
    for s in weather_entities:
        attrs = s.get("attributes") or {}
        if "temperature" in attrs:
            chosen = s
            break
    if not chosen:
        chosen = weather_entities[0]

    attrs = chosen.get("attributes") or {}
    temp = attrs.get("temperature")
    unit = attrs.get("temperature_unit") or attrs.get("unit_of_measurement") or "°F"
    cond = chosen.get("state") or "unknown"

    high = None
    low = None
    precip = None
    fc = attrs.get("forecast")
    if isinstance(fc, list) and fc:
        first = fc[0] if isinstance(fc[0], dict) else None
        if first:
            high = first.get("temperature")
            low = first.get("templow")
            precip = first.get("precipitation_probability")

    def c_to_f(x):
        return (float(x) * 9.0 / 5.0) + 32.0

    if unit and "c" in str(unit).lower():
        try:
            if temp is not None: temp = round(c_to_f(temp), 1)
            if high is not None: high = round(c_to_f(high), 1)
            if low is not None: low = round(c_to_f(low), 1)
            unit = "°F"
        except Exception:
            pass

    parts = []
    if temp is not None:
        parts.append(f"It's {temp}{unit} right now")
    if cond and cond != "unknown":
        parts.append(f"{cond}")
    if high is not None and low is not None:
        parts.append(f"High {high}{unit}, low {low}{unit}")
    if precip is not None:
        parts.append(f"Max precip chance {precip} percent")

    if not parts:
        return None

    return ". ".join(parts) + "."


def _open_meteo_weather(lat: float, lon: float) -> Optional[str]:
    """
    Pull current temp + daily hi/lo + precip probability.
    Always try to speak Fahrenheit.
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        "timezone": "auto",
        "temperature_unit": "fahrenheit",
        "windspeed_unit": "mph",
        "precipitation_unit": "inch",
    }
    try:
        r = requests.get(url, params=params, timeout=10)
        if r.status_code != 200:
            return None
        data = r.json() or {}
        cur = (data.get("current") or {})
        daily = (data.get("daily") or {})

        temp = cur.get("temperature_2m")
        highs = daily.get("temperature_2m_max") or []
        lows = daily.get("temperature_2m_min") or []
        prec = daily.get("precipitation_probability_max") or []

        hi = highs[0] if highs else None
        lo = lows[0] if lows else None
        pr = prec[0] if prec else None

        parts = []
        if temp is not None:
            parts.append(f"It's {round(float(temp), 1)} degrees right now")
        if hi is not None and lo is not None:
            parts.append(f"High {round(float(hi), 1)}, low {round(float(lo), 1)}")
        if pr is not None:
            parts.append(f"Max precip chance {int(pr)} percent")

        if not parts:
            return None

        return ". ".join(parts) + "."
    except Exception:
        return None

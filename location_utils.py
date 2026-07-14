"""Geocode named places and perform provider-independent location math.

Open-Meteo supplies keyless place lookup for weather, time, and distance
questions. Great-circle distance and initial bearing calculations remain local;
they do not represent road distance, traffic, or travel time.
"""

from __future__ import annotations

import math
import re
import threading
import time
from typing import Optional

import requests

from app_config import LOCATION_ALIASES


GEOCODE_CACHE_TTL_SECONDS = 30 * 60
EARTH_MEAN_RADIUS_KM = 6371.0088

_GEOCODE_CACHE: dict[str, tuple[float, dict]] = {}
_GEOCODE_CACHE_LOCK = threading.Lock()


def _location_clean_for_geo(location: str) -> str:
    """Normalize a spoken location before geocoding it."""
    value = str(location or "").strip()
    value = (
        value.replace("?", " ")
        .replace("!", " ")
        .replace(".", " ")
        .replace(",", " ")
    )
    value = re.sub(r"\s+", " ", value).strip(" ,")
    return re.sub(r"^(?:in|at)\s+", "", value, flags=re.IGNORECASE).strip()


def clear_geocode_cache() -> None:
    """Clear process-local geocoding results, primarily for tests."""
    with _GEOCODE_CACHE_LOCK:
        _GEOCODE_CACHE.clear()


def geocode_location(location: str) -> Optional[dict]:
    """Return coordinates and display metadata for a named place."""
    cleaned = _location_clean_for_geo(location)
    if not cleaned:
        return None

    resolved = str((LOCATION_ALIASES or {}).get(cleaned.lower(), cleaned)).strip()
    if not resolved:
        return None
    cache_key = resolved.casefold()
    now = time.monotonic()

    with _GEOCODE_CACHE_LOCK:
        cached = _GEOCODE_CACHE.get(cache_key)
        if cached and now - cached[0] <= GEOCODE_CACHE_TTL_SECONDS:
            return dict(cached[1])
        if cached:
            _GEOCODE_CACHE.pop(cache_key, None)

    params = {"name": resolved, "count": 1, "language": "en", "format": "json"}
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
        latitude = float(top["latitude"])
        longitude = float(top["longitude"])
        if not _valid_coordinates(latitude, longitude):
            return None

        name = str(top.get("name") or resolved).strip()
        admin1 = str(top.get("admin1") or "").strip()
        country = str(top.get("country") or "").strip()
        country_code = str(top.get("country_code") or "").strip().upper()
        parts = [name]
        if admin1 and admin1.casefold() != name.casefold():
            parts.append(admin1)
        if country:
            parts.append(country)

        result = {
            "lat": latitude,
            "lon": longitude,
            "name": name,
            "admin1": admin1,
            "country": country,
            "country_code": country_code,
            "display": ", ".join(part for part in parts if part),
            "timezone": top.get("timezone") or None,
        }
        with _GEOCODE_CACHE_LOCK:
            _GEOCODE_CACHE[cache_key] = (now, dict(result))
        return result
    except (KeyError, TypeError, ValueError, requests.RequestException):
        return None


def _valid_coordinates(latitude: float, longitude: float) -> bool:
    return (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90.0 <= latitude <= 90.0
        and -180.0 <= longitude <= 180.0
    )


def great_circle_distance_km(
    origin_latitude: float,
    origin_longitude: float,
    destination_latitude: float,
    destination_longitude: float,
) -> float:
    """Calculate surface distance using the haversine formula."""
    coordinates = tuple(
        float(value)
        for value in (
            origin_latitude,
            origin_longitude,
            destination_latitude,
            destination_longitude,
        )
    )
    if not _valid_coordinates(coordinates[0], coordinates[1]) or not _valid_coordinates(
        coordinates[2], coordinates[3]
    ):
        raise ValueError("invalid latitude or longitude")

    lat1, lon1, lat2, lon2 = map(math.radians, coordinates)
    delta_latitude = lat2 - lat1
    delta_longitude = lon2 - lon1
    haversine = (
        math.sin(delta_latitude / 2.0) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(delta_longitude / 2.0) ** 2
    )
    central_angle = 2.0 * math.atan2(
        math.sqrt(haversine),
        math.sqrt(max(0.0, 1.0 - haversine)),
    )
    return EARTH_MEAN_RADIUS_KM * central_angle


def initial_bearing_degrees(
    origin_latitude: float,
    origin_longitude: float,
    destination_latitude: float,
    destination_longitude: float,
) -> Optional[float]:
    """Return the initial great-circle bearing, or None at one location."""
    coordinates = tuple(
        float(value)
        for value in (
            origin_latitude,
            origin_longitude,
            destination_latitude,
            destination_longitude,
        )
    )
    if not _valid_coordinates(coordinates[0], coordinates[1]) or not _valid_coordinates(
        coordinates[2], coordinates[3]
    ):
        raise ValueError("invalid latitude or longitude")
    if math.isclose(coordinates[0], coordinates[2], abs_tol=1e-12) and math.isclose(
        coordinates[1], coordinates[3], abs_tol=1e-12
    ):
        return None

    lat1, lon1, lat2, lon2 = map(math.radians, coordinates)
    delta_longitude = lon2 - lon1
    x = math.sin(delta_longitude) * math.cos(lat2)
    y = (
        math.cos(lat1) * math.sin(lat2)
        - math.sin(lat1) * math.cos(lat2) * math.cos(delta_longitude)
    )
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def compass_direction(bearing_degrees: Optional[float]) -> Optional[str]:
    """Convert a bearing to one of eight concise spoken directions."""
    if bearing_degrees is None:
        return None
    labels = (
        "north",
        "northeast",
        "east",
        "southeast",
        "south",
        "southwest",
        "west",
        "northwest",
    )
    index = int((float(bearing_degrees) + 22.5) // 45.0) % len(labels)
    return labels[index]

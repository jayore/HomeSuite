"""Parse and answer deterministic straight-line location questions.

This handler deliberately stops at geodesic distance and compass direction.
Driving, walking, transit, routes, traffic, and ETA questions remain available
to the conversational web-search path, which can use current information.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Callable, Mapping, Optional

from location_utils import (
    compass_direction,
    geocode_location,
    great_circle_distance_km,
    initial_bearing_degrees,
)


_ROUTE_LANGUAGE = re.compile(
    r"\b(?:by\s+car|drive|driving|road|route|traffic|travel\s+time|"
    r"walk|walking|bike|biking|bicycle|transit|train|flight|fly|flying|eta)\b"
)
_CELESTIAL_TARGETS = {
    "moon",
    "the moon",
    "sun",
    "the sun",
    "mercury",
    "venus",
    "mars",
    "jupiter",
    "saturn",
    "uranus",
    "neptune",
}
_HOME_TERMS = {"home", "my home", "my house", "the house"}
_HERE_TERMS = {"here", "my current location", "current location", "where i am"}
_PLACE_PRONOUNS = {"there", "it", "that", "that place"}

_DISTANCE_PATTERNS = (
    re.compile(r"^how far(?: away)? is it to (?P<destination>.+)$"),
    re.compile(
        r"^how far(?: away)? is (?P<destination>.+?) from (?P<origin>.+)$"
    ),
    re.compile(
        r"^how far(?: away)? from (?P<origin>.+?) to (?P<destination>.+)$"
    ),
    re.compile(r"^how far(?: away)? (?:is )?(?:it )?to (?P<destination>.+)$"),
    re.compile(r"^how far(?: away)? is (?P<destination>.+)$"),
    re.compile(r"^how far(?: away)? (?P<destination>.+)$"),
    re.compile(
        r"^what(?:'s| is) (?:the )?distance from (?P<origin>.+?) "
        r"to (?P<destination>.+)$"
    ),
    re.compile(
        r"^what(?:'s| is) (?:the )?distance between (?P<origin>.+?) "
        r"and (?P<destination>.+)$"
    ),
    re.compile(
        r"^what(?:'s| is) (?:the )?distance (?:to|from) (?P<destination>.+)$"
    ),
)
_DIRECTION_PATTERNS = (
    re.compile(
        r"^(?:what|which) direction is (?P<destination>.+?) from (?P<origin>.+)$"
    ),
    re.compile(r"^(?:what|which) direction is it to (?P<destination>.+)$"),
    re.compile(r"^(?:what|which) direction is (?P<destination>.+)$"),
    re.compile(
        r"^which way is (?P<destination>.+?) from (?P<origin>.+)$"
    ),
    re.compile(r"^which way is (?P<destination>.+)$"),
)
_PENDING_ORIGIN_PATTERN = re.compile(
    r"^(?:from\s+(?P<named_origin>[a-z0-9][a-z0-9 .,'-]{0,100})|"
    r"(?P<short_origin>home|my home|my house|here|current location|"
    r"my current location))$"
)


@dataclass(frozen=True)
class LocationQuery:
    """A straight-line distance or direction request between two places."""

    destination: str
    origin: Optional[str] = None
    intent: str = "distance"


@dataclass(frozen=True)
class LocationAnswer:
    """Spoken answer plus dispatcher hints for typed conversation state."""

    text: str
    destination: Optional[str] = None
    needs_origin: bool = False


def _normalize(text: str) -> str:
    value = str(text or "").lower().replace("’", "'").replace("‘", "'")
    value = re.sub(r"\s+", " ", value).strip(" .,!?\t\r\n")
    return value


def _clean_fragment(value: Optional[str]) -> Optional[str]:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip(" .,!?\t\r\n")
    return cleaned or None


def parse_location_query(
    text: str,
    *,
    pending_destination: Optional[str] = None,
) -> Optional[LocationQuery]:
    """Parse only bounded distance/direction language for named places."""
    normalized = _normalize(text)
    if not normalized or _ROUTE_LANGUAGE.search(normalized):
        return None

    for intent, patterns in (
        ("distance", _DISTANCE_PATTERNS),
        ("direction", _DIRECTION_PATTERNS),
    ):
        for pattern in patterns:
            match = pattern.fullmatch(normalized)
            if not match:
                continue
            destination = _clean_fragment(match.groupdict().get("destination"))
            origin = _clean_fragment(match.groupdict().get("origin"))
            if not destination or destination in _CELESTIAL_TARGETS:
                return None
            return LocationQuery(
                destination=destination,
                origin=origin,
                intent=intent,
            )

    pending = _clean_fragment(pending_destination)
    if pending:
        match = _PENDING_ORIGIN_PATTERN.fullmatch(normalized)
        if match:
            return LocationQuery(
                destination=pending,
                origin=_clean_fragment(
                    match.group("named_origin") or match.group("short_origin")
                ),
            )
    return None


def looks_like_location_query(text: str) -> bool:
    """Report whether the deterministic location handler owns this utterance."""
    return parse_location_query(text) is not None


def _coordinates(value: Mapping[str, object]) -> Optional[tuple[float, float]]:
    try:
        latitude = float(value.get("latitude", value.get("lat")))
        longitude = float(value.get("longitude", value.get("lon")))
    except (TypeError, ValueError):
        return None
    if not (
        math.isfinite(latitude)
        and math.isfinite(longitude)
        and -90.0 <= latitude <= 90.0
        and -180.0 <= longitude <= 180.0
    ):
        return None
    return latitude, longitude


def _home_place(home_location: Mapping[str, object]) -> Optional[dict]:
    coordinates = _coordinates(home_location)
    if not coordinates:
        return None
    city = str(home_location.get("city") or "").strip()
    region = str(home_location.get("region") or "").strip()
    country = str(home_location.get("country") or "").strip()
    display_parts = [part for part in (city, region, country) if part]
    return {
        "lat": coordinates[0],
        "lon": coordinates[1],
        "name": city or "home",
        "admin1": region,
        "country": country,
        "display": ", ".join(display_parts) or "home",
        "is_home": True,
    }


def _place_label(place: Mapping[str, object], *, home: bool = False) -> str:
    name = str(place.get("name") or "").strip()
    display = str(place.get("display") or name).strip()
    if home:
        return f"home in {name}" if name and name.casefold() != "home" else "home"
    return name or display or "that place"


def _resolve_pronoun(value: str, recalled_location: Optional[str]) -> Optional[str]:
    normalized = _normalize(value)
    if normalized not in _PLACE_PRONOUNS:
        return value
    return _clean_fragment(recalled_location)


def _resolve_named_place(
    value: str,
    *,
    home_location: Mapping[str, object],
    recalled_location: Optional[str],
    geocoder: Callable[[str], Optional[dict]],
) -> tuple[Optional[dict], Optional[str]]:
    normalized = _normalize(value)
    if normalized in _HOME_TERMS:
        return _home_place(home_location), "home"
    resolved = _resolve_pronoun(value, recalled_location)
    if not resolved:
        return None, None
    return geocoder(resolved), resolved


def _format_distance(distance_km: float, units: str) -> str:
    normalized_units = _normalize(units)
    metric = normalized_units in {
        "metric",
        "si",
        "kilometer",
        "kilometers",
        "kilometre",
        "kilometres",
        "km",
    }
    value = distance_km if metric else distance_km * 0.6213711922
    unit = "kilometer" if metric else "mile"
    if value < 10.0:
        rounded = round(value, 1)
        number = f"{rounded:.1f}".rstrip("0").rstrip(".")
    else:
        rounded = round(value)
        number = f"{rounded:,.0f}"
    if float(rounded) != 1.0:
        unit += "s"
    return f"{number} {unit}"


def answer_location_query(
    query: LocationQuery,
    *,
    home_location: Mapping[str, object],
    units: str = "imperial",
    source_is_fixed: Optional[bool],
    recalled_location: Optional[str] = None,
    geocoder: Optional[Callable[[str], Optional[dict]]] = None,
) -> LocationAnswer:
    """Resolve and format a deterministic straight-line location answer."""
    geocoder = geocoder or geocode_location
    destination_text = _resolve_pronoun(query.destination, recalled_location)
    if not destination_text:
        return LocationAnswer("I don't have a recent place to use for that.")

    destination, resolved_destination = _resolve_named_place(
        destination_text,
        home_location=home_location,
        recalled_location=recalled_location,
        geocoder=geocoder,
    )
    if not destination:
        return LocationAnswer(f"I couldn't find {destination_text}.")
    destination_label = _place_label(
        destination,
        home=bool(destination.get("is_home")),
    )

    origin_text = _clean_fragment(query.origin)
    if not origin_text or _normalize(origin_text) in _HERE_TERMS:
        if source_is_fixed is not True:
            return LocationAnswer(
                (
                    f"From where? You can say from home or name a starting place "
                    f"for {destination_label}."
                ),
                destination=resolved_destination or destination_text,
                needs_origin=True,
            )
        origin = _home_place(home_location)
        origin_name = "home"
    else:
        origin, origin_name = _resolve_named_place(
            origin_text,
            home_location=home_location,
            recalled_location=recalled_location,
            geocoder=geocoder,
        )

    if not origin:
        if _normalize(origin_name or "") == "home":
            return LocationAnswer(
                "Home coordinates aren't configured yet. Name a starting place instead."
            )
        return LocationAnswer(f"I couldn't find {origin_text}.")

    origin_coordinates = _coordinates(origin)
    destination_coordinates = _coordinates(destination)
    if not origin_coordinates or not destination_coordinates:
        return LocationAnswer("I couldn't calculate that distance right now.")

    distance_km = great_circle_distance_km(
        origin_coordinates[0],
        origin_coordinates[1],
        destination_coordinates[0],
        destination_coordinates[1],
    )
    bearing = initial_bearing_degrees(
        origin_coordinates[0],
        origin_coordinates[1],
        destination_coordinates[0],
        destination_coordinates[1],
    )
    direction = compass_direction(bearing)
    origin_label = _place_label(origin, home=bool(origin.get("is_home")))
    distance_text = _format_distance(distance_km, units)

    if query.intent == "direction" and direction:
        response = (
            f"{destination_label} is {direction} of {origin_label}, about "
            f"{distance_text} as the crow flies."
        )
    elif direction:
        response = (
            f"{destination_label} is about {distance_text} {direction} of "
            f"{origin_label}, as the crow flies."
        )
    else:
        response = (
            f"{destination_label} and {origin_label} are at the same location "
            "for this calculation."
        )
    return LocationAnswer(
        response,
        destination=resolved_destination or destination_text,
    )

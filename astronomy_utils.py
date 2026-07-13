"""Calculate local sun and moon events for queries and scheduling.

Home Assistant's ``sun.sun`` entity remains the preferred source for the next
sunrise or sunset because it is already present in most deployments. Astral
provides the local, keyless calculation path for explicit dates, moon events,
phase, and horizon status. No astronomy calculation in this module requires a
network request.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
from typing import Any, Callable, Iterable, Optional
from zoneinfo import ZoneInfo


try:
    from astral import Observer
    from astral.moon import elevation as _moon_elevation
    from astral.moon import moonrise as _moonrise
    from astral.moon import moonset as _moonset
    from astral.moon import phase as _moon_phase
    from astral.sun import dawn as _dawn
    from astral.sun import dusk as _dusk
    from astral.sun import elevation as _sun_elevation
    from astral.sun import sunrise as _sunrise
    from astral.sun import sunset as _sunset
except ImportError:
    Observer = None
    _moon_elevation = None
    _moonrise = None
    _moonset = None
    _moon_phase = None
    _dawn = None
    _dusk = None
    _sun_elevation = None
    _sunrise = None
    _sunset = None


_SOLAR_EVENT_ATTRIBUTE = {
    "sunrise": "next_rising",
    "sunset": "next_setting",
}
_SUPPORTED_EVENTS = {"dawn", "sunrise", "sunset", "dusk", "moonrise", "moonset"}


def astral_available() -> bool:
    """Return whether the optional import succeeded in this runtime."""
    return Observer is not None


def _aware_now(now: Optional[datetime] = None) -> datetime:
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        return current.astimezone()
    return current


def astronomy_timezone(home_location: Optional[dict], *, now: Optional[datetime] = None):
    """Return the configured home timezone, falling back to the runtime zone."""
    timezone_name = ""
    if isinstance(home_location, dict):
        timezone_name = str(home_location.get("timezone") or "").strip()
    if timezone_name:
        try:
            return ZoneInfo(timezone_name)
        except Exception:
            logging.warning("ASTRONOMY_TIMEZONE_INVALID timezone=%r", timezone_name)

    current = _aware_now(now)
    return current.tzinfo or datetime.now().astimezone().tzinfo


def astronomy_now(
    home_location: Optional[dict],
    *,
    now: Optional[datetime] = None,
) -> datetime:
    """Return an aware current time expressed in the configured home timezone."""
    timezone_info = astronomy_timezone(home_location, now=now)
    current = now or datetime.now(timezone_info)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone_info)
    return current.astimezone(timezone_info)


def _coordinates(home_location: Optional[dict]) -> Optional[tuple[float, float]]:
    if not isinstance(home_location, dict):
        return None
    try:
        latitude = float(home_location.get("latitude"))
        longitude = float(home_location.get("longitude"))
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        return None
    return latitude, longitude


def astronomy_location_configured(home_location: Optional[dict]) -> bool:
    """Return whether valid coordinates are available for local calculations."""
    return _coordinates(home_location) is not None


def _observer(home_location: Optional[dict]):
    coordinates = _coordinates(home_location)
    if coordinates is None or Observer is None:
        return None
    return Observer(latitude=coordinates[0], longitude=coordinates[1])


def _parse_datetime(value: Any, *, fallback_tz) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=fallback_tz)
    return parsed.astimezone(fallback_tz)


def _event_from_ha_states(
    states: Iterable[dict],
    event: str,
    *,
    now: datetime,
) -> Optional[datetime]:
    attribute = _SOLAR_EVENT_ATTRIBUTE.get(event)
    if not attribute:
        return None
    for row in states or []:
        if not isinstance(row, dict) or row.get("entity_id") != "sun.sun":
            continue
        attributes = row.get("attributes") if isinstance(row.get("attributes"), dict) else {}
        return _parse_datetime(attributes.get(attribute), fallback_tz=now.tzinfo)
    return None


def resolve_astronomy_event(
    event: str,
    target_date: date,
    *,
    home_location: Optional[dict],
) -> Optional[datetime]:
    """Calculate one local sun or moon event on a specific civil date."""
    event = str(event or "").strip().lower()
    if event not in _SUPPORTED_EVENTS or not isinstance(target_date, date):
        return None

    observer = _observer(home_location)
    if observer is None:
        return None
    timezone_info = astronomy_timezone(home_location)
    calculators = {
        "dawn": _dawn,
        "sunrise": _sunrise,
        "sunset": _sunset,
        "dusk": _dusk,
        "moonrise": _moonrise,
        "moonset": _moonset,
    }
    calculator = calculators[event]
    if not callable(calculator):
        return None

    try:
        resolved = calculator(observer, target_date, tzinfo=timezone_info)
    except ValueError as exc:
        logging.info(
            "ASTRONOMY_EVENT_ABSENT event=%s date=%s reason=%s",
            event,
            target_date.isoformat(),
            exc,
        )
        return None
    except Exception:
        logging.exception("ASTRONOMY_EVENT_FAIL event=%s date=%s", event, target_date)
        return None

    if not isinstance(resolved, datetime):
        return None
    if resolved.tzinfo is None:
        resolved = resolved.replace(tzinfo=timezone_info)
    return resolved.astimezone(timezone_info)


def find_next_astronomy_event(
    event: str,
    *,
    after: datetime,
    home_location: Optional[dict],
    max_days: Optional[int] = None,
) -> Optional[datetime]:
    """Find the next occurrence after an instant, skipping eventless dates."""
    event = str(event or "").strip().lower()
    if event not in _SUPPORTED_EVENTS:
        return None

    current = astronomy_now(home_location, now=after)
    if max_days is None:
        max_days = 370 if event in _SOLAR_EVENT_ATTRIBUTE or event in {"dawn", "dusk"} else 62

    for day_offset in range(max(0, int(max_days)) + 1):
        target_date = current.date() + timedelta(days=day_offset)
        resolved = resolve_astronomy_event(
            event,
            target_date,
            home_location=home_location,
        )
        if resolved is not None and resolved > current:
            return resolved
    return None


def body_is_up(
    body: str,
    *,
    at: datetime,
    home_location: Optional[dict],
) -> Optional[bool]:
    """Return whether the sun or moon is geometrically above the horizon."""
    observer = _observer(home_location)
    if observer is None:
        return None

    body = str(body or "").strip().lower()
    calculator = _sun_elevation if body == "sun" else _moon_elevation if body == "moon" else None
    if not callable(calculator):
        return None

    current = astronomy_now(home_location, now=at)
    try:
        return float(calculator(observer, current)) > 0.0
    except Exception:
        logging.exception("ASTRONOMY_ELEVATION_FAIL body=%s", body)
        return None


def _moon_phase_value(target_date: date) -> Optional[float]:
    if not callable(_moon_phase) or not isinstance(target_date, date):
        return None
    try:
        return float(_moon_phase(target_date)) % 28.0
    except Exception:
        logging.exception("ASTRONOMY_PHASE_FAIL date=%s", target_date)
        return None


def find_next_moon_phase_date(
    phase_name: str,
    *,
    start_date: date,
    max_days: int = 35,
) -> Optional[date]:
    """Return the date nearest the next new or full moon in one lunar cycle."""
    normalized = str(phase_name or "").strip().lower()
    if normalized in {"new", "full"}:
        normalized = f"{normalized} moon"
    target_value = {"new moon": 0.0, "full moon": 14.0}.get(normalized)
    if target_value is None or not isinstance(start_date, date):
        return None

    best_date = None
    best_distance = None
    for day_offset in range(max(0, int(max_days)) + 1):
        candidate = start_date + timedelta(days=day_offset)
        phase_value = _moon_phase_value(candidate)
        if phase_value is None:
            continue
        if target_value == 0.0:
            distance = min(phase_value, 28.0 - phase_value)
        else:
            distance = abs(phase_value - target_value)
        if best_distance is None or distance < best_distance:
            best_date = candidate
            best_distance = distance
    return best_date


def moon_phase_name(target_date: date) -> Optional[str]:
    """Convert Astral's 0-28 lunar age to a concise conventional phase."""
    phase_value = _moon_phase_value(target_date)
    if phase_value is None:
        return None

    # Keep the four exact phase labels deliberately narrow. The intervals
    # between them are better described as waxing/waning crescent or gibbous.
    if phase_value < 0.75 or phase_value >= 27.25:
        return "new moon"
    if phase_value < 6.25:
        return "waxing crescent"
    if phase_value < 7.75:
        return "first quarter"
    if phase_value < 13.25:
        return "waxing gibbous"
    if phase_value < 14.75:
        return "full moon"
    if phase_value < 20.25:
        return "waning gibbous"
    if phase_value < 21.75:
        return "last quarter"
    return "waning crescent"


def resolve_solar_event(
    event: str,
    day_hint: str,
    *,
    now: Optional[datetime] = None,
    states_provider: Optional[Callable[[], Iterable[dict]]] = None,
    home_location: Optional[dict] = None,
) -> Optional[datetime]:
    """Resolve a future sunrise or sunset for deterministic scheduling."""
    event = str(event or "").strip().lower()
    day_hint = str(day_hint or "next").strip().lower()
    if event not in _SOLAR_EVENT_ATTRIBUTE or day_hint not in {"next", "today", "tomorrow"}:
        return None

    current = astronomy_now(home_location, now=now)
    next_event = None
    if callable(states_provider):
        try:
            next_event = _event_from_ha_states(states_provider() or [], event, now=current)
        except Exception:
            logging.exception("SOLAR_HA_STATE_FAIL event=%s", event)

    expected_date = current.date() + (timedelta(days=1) if day_hint == "tomorrow" else timedelta())
    if next_event is not None and next_event > current:
        if day_hint == "next" or next_event.date() == expected_date:
            return next_event

    if day_hint == "next":
        return find_next_astronomy_event(
            event,
            after=current,
            home_location=home_location,
        )

    resolved = resolve_astronomy_event(
        event,
        expected_date,
        home_location=home_location,
    )
    if resolved is None or resolved <= current:
        return None
    return resolved

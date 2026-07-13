"""Parse and answer deterministic local astronomy questions.

The handler is intentionally read-only. It uses Home Assistant for current sun
and moon-phase state when those canonical entities are present, and Astral for
local event times, future phase, and horizon calculations. Scheduling remains
limited to sunrise and sunset in ``schedule_controls.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import re
from typing import Optional, Sequence

from app_config import (
    PLANET_VISIBILITY_MAX_MAGNITUDE,
    PLANET_VISIBILITY_MAX_SUN_ALTITUDE_DEGREES,
    PLANET_VISIBILITY_MIN_ALTITUDE_DEGREES,
    PLANET_VISIBILITY_MIN_DURATION_MINUTES,
    PLANET_VISIBILITY_PLANETS,
)
from astronomy_utils import (
    astral_available,
    astronomy_location_configured,
    astronomy_now,
    body_is_up,
    find_next_astronomy_event,
    find_next_moon_phase_date,
    moon_phase_name,
    resolve_astronomy_event,
)
from planetary_utils import (
    SUPPORTED_PLANETS,
    PlanetPosition,
    PlanetVisibility,
    find_next_planet_event,
    planet_position,
    planet_visibility_window,
    planetary_available,
    resolve_planet_event_on_date,
    visible_planets,
)


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
_PLANET_PATTERN = "|".join(SUPPORTED_PLANETS)
_EVENT_PATTERNS = (
    ("moonrise", re.compile(r"\b(?:moonrise|moon\s+(?:rise|rises|rising|come\s+up))\b")),
    ("moonset", re.compile(r"\b(?:moonset|moon\s+(?:set|sets|setting|go\s+down))\b")),
    ("sunrise", re.compile(r"\b(?:sunrise|sun\s+(?:rise|rises|rising|come\s+up))\b")),
    ("sunset", re.compile(r"\b(?:sunset|sun\s+(?:set|sets|setting|go\s+down))\b")),
    ("dawn", re.compile(r"\bdawn\b")),
    ("dusk", re.compile(r"\bdusk\b")),
)
_EVENT_LABELS = {
    "dawn": "Dawn",
    "sunrise": "Sunrise",
    "sunset": "Sunset",
    "dusk": "Dusk",
    "moonrise": "Moonrise",
    "moonset": "Moonset",
}
_HA_MOON_PHASES = {
    "new_moon",
    "waxing_crescent",
    "first_quarter",
    "waxing_gibbous",
    "full_moon",
    "waning_gibbous",
    "last_quarter",
    "waning_crescent",
}
_PLANETARY_INTENTS = {
    "planet_event",
    "planet_position",
    "planet_up",
    "planet_visible",
    "planet_best",
    "visible_planets",
}


@dataclass(frozen=True)
class AstronomyQuery:
    """A local astronomy intent independent of Home Assistant and Astral."""

    intent: str
    event: Optional[str] = None
    body: Optional[str] = None
    phase: Optional[str] = None
    planet: Optional[str] = None
    day_offset: Optional[int] = None
    weekday: Optional[int] = None
    next_weekday: bool = False
    explicit_day: bool = False
    night_window: bool = False


def _normalize(text: str) -> str:
    normalized = re.sub(r"[^a-z0-9'\s-]+", " ", (text or "").lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _day_fields(text: str) -> dict:
    if re.search(r"\btomorrow\b", text):
        return {"day_offset": 1, "explicit_day": True}
    if re.search(r"\btoday\b", text):
        return {"day_offset": 0, "explicit_day": True}

    weekday_match = re.search(
        rf"\b(?P<next>next\s+)?(?P<weekday>{_WEEKDAY_PATTERN})\b",
        text,
    )
    if weekday_match:
        return {
            "weekday": _WEEKDAYS[weekday_match.group("weekday")],
            "next_weekday": bool(weekday_match.group("next")),
            "explicit_day": True,
        }
    return {}


def _planet_day_fields(text: str) -> dict:
    fields = _day_fields(text)
    if re.search(r"\btonight\b", text):
        return {"day_offset": 0, "explicit_day": True, "night_window": True}
    if re.search(r"\bnight\b", text):
        fields = dict(fields)
        fields["night_window"] = True
    return fields


def parse_astronomy_query(text: str) -> Optional[AstronomyQuery]:
    """Parse local event-time, moon-phase, and above-horizon questions."""
    normalized = _normalize(text)
    if not normalized:
        return None
    day_fields = _day_fields(normalized)

    next_phase_match = re.search(
        r"\b(?:next\s+)?(?P<phase>full|new)\s+moon\b",
        normalized,
    )
    if (
        next_phase_match
        and re.match(r"^(?:when\b|what\s+(?:date|day)\b)", normalized)
    ):
        return AstronomyQuery(
            intent="phase_event",
            body="moon",
            phase=f"{next_phase_match.group('phase')} moon",
        )

    planet_match = re.search(
        rf"\b(?P<planet>{_PLANET_PATTERN})\b",
        normalized,
    )
    planet = planet_match.group("planet") if planet_match else None
    planet_day_fields = _planet_day_fields(normalized)

    generic_planet_visibility = bool(
        not planet
        and re.search(r"\bplanets?\b", normalized)
        and re.search(r"\b(?:visible|see|view)\b", normalized)
        and re.match(r"^(?:what|which|can\s+i|will\s+i)\b", normalized)
    )
    if generic_planet_visibility:
        if not planet_day_fields:
            planet_day_fields = {
                "day_offset": 0,
                "explicit_day": True,
                "night_window": True,
            }
        return AstronomyQuery(intent="visible_planets", **planet_day_fields)

    if planet:
        if re.match(r"^(?:when\b|what\s+time\b)", normalized):
            if re.search(r"\b(?:rise|rises|rising|come\s+up)\b", normalized):
                return AstronomyQuery(
                    intent="planet_event",
                    event="rise",
                    planet=planet,
                    **planet_day_fields,
                )
            if re.search(r"\b(?:set|sets|setting|go\s+down)\b", normalized):
                return AstronomyQuery(
                    intent="planet_event",
                    event="set",
                    planet=planet,
                    **planet_day_fields,
                )

        asks_best_time = bool(
            re.search(r"\bbest\s+time\b", normalized)
            and re.search(r"\b(?:see|view|visible)\b", normalized)
        )
        asks_when_visible = bool(
            re.match(r"^when\b", normalized)
            and re.search(r"\bvisible\b", normalized)
        )
        asks_future_visibility = bool(
            re.match(r"^(?:is|can\s+i|will\s+i)\b", normalized)
            and re.search(r"\b(?:visible|see)\b", normalized)
            and (
                re.search(r"\b(?:tonight|tomorrow|night)\b", normalized)
                or re.search(rf"\b(?:{_WEEKDAY_PATTERN})\b", normalized)
            )
        )
        if asks_best_time or asks_when_visible or asks_future_visibility:
            if not planet_day_fields:
                planet_day_fields = {
                    "day_offset": 0,
                    "explicit_day": True,
                    "night_window": True,
                }
            return AstronomyQuery(
                intent="planet_best",
                planet=planet,
                **planet_day_fields,
            )

        asks_position = bool(
            re.match(r"^where\b", normalized)
            or re.match(r"^how\s+high\b", normalized)
            or (
                re.match(r"^(?:what|which)\b", normalized)
                and re.search(r"\b(?:direction|part\s+of\s+the\s+sky)\b", normalized)
            )
        )
        if asks_position:
            return AstronomyQuery(intent="planet_position", planet=planet)

        if re.fullmatch(
            rf"is\s+(?:the\s+)?{re.escape(planet)}\s+"
            r"(?:(?:currently|still)\s+)?"
            r"(?:up|above\s+(?:the\s+)?horizon)"
            r"(?:\s+(?:yet|now|right\s+now))?",
            normalized,
        ):
            return AstronomyQuery(intent="planet_up", planet=planet)

        if re.fullmatch(
            rf"(?:is\s+(?:the\s+)?{re.escape(planet)}\s+visible|"
            rf"can\s+i\s+see\s+(?:the\s+)?{re.escape(planet)})"
            r"(?:\s+(?:yet|now|right\s+now))?",
            normalized,
        ):
            return AstronomyQuery(intent="planet_visible", planet=planet)

    phase_question = bool(
        re.match(r"^(?:what|what's|which|tell\s+me)\b", normalized)
        and (
            re.search(r"\bmoon\s+phase\b", normalized)
            or re.search(r"\bphase\b.*\bmoon\b", normalized)
        )
    )
    if phase_question:
        return AstronomyQuery(intent="phase", body="moon", **day_fields)

    status_match = re.fullmatch(
        r"is\s+(?:the\s+)?(?P<body>sun|moon)\s+"
        r"(?:(?:currently|still|actually)\s+)?"
        r"(?:up|out|above\s+(?:the\s+)?horizon)"
        r"(?:\s+(?:yet|now|right\s+now))?",
        normalized,
    )
    if status_match:
        return AstronomyQuery(intent="status", body=status_match.group("body"))

    if not re.match(r"^(?:when\b|what\s+time\b)", normalized):
        return None
    for event, pattern in _EVENT_PATTERNS:
        if pattern.search(normalized):
            return AstronomyQuery(intent="event", event=event, **day_fields)
    return None


def looks_like_astronomy_query(text: str) -> bool:
    return parse_astronomy_query(text) is not None


def astronomy_query_date(
    query: AstronomyQuery,
    *,
    home_location: Optional[dict],
    now: Optional[datetime] = None,
) -> date:
    current = astronomy_now(home_location, now=now)
    if query.weekday is not None:
        delta = (query.weekday - current.date().weekday()) % 7
        if query.next_weekday:
            delta += 7
        return current.date() + timedelta(days=delta)
    return current.date() + timedelta(days=query.day_offset or 0)


def _state_by_entity(states: Optional[Sequence[dict]], entity_id: str) -> Optional[dict]:
    for row in states or []:
        if isinstance(row, dict) and row.get("entity_id") == entity_id:
            return row
    return None


def _ha_moon_phase(states: Optional[Sequence[dict]]) -> Optional[str]:
    preferred = _state_by_entity(states, "sensor.moon_phase")
    rows = [preferred] if preferred else []
    rows.extend(
        row
        for row in (states or [])
        if isinstance(row, dict)
        and str(row.get("entity_id") or "").startswith("sensor.moon")
        and row is not preferred
    )
    for row in rows:
        state = str((row or {}).get("state") or "").strip().lower()
        if state in _HA_MOON_PHASES:
            return state.replace("_", " ")
    return None


def _ha_sun_is_up(states: Optional[Sequence[dict]]) -> Optional[bool]:
    row = _state_by_entity(states, "sun.sun") or {}
    state = str(row.get("state") or "").strip().lower()
    if state == "above_horizon":
        return True
    if state == "below_horizon":
        return False
    return None


def _clock_text(value: datetime) -> str:
    if value.second + (value.microsecond / 1_000_000) >= 30:
        value = value + timedelta(minutes=1)
    value = value.replace(second=0, microsecond=0)
    try:
        return value.strftime("%-I:%M %p")
    except Exception:
        return value.strftime("%I:%M %p").lstrip("0")


def _date_text(target_date: date, today: date) -> str:
    if target_date == today:
        return "today"
    if target_date == today + timedelta(days=1):
        return "tomorrow"
    return f"on {target_date.strftime('%A, %B')} {target_date.day}"


def _phase_response(phase_name: str, target_date: date, today: date) -> str:
    if target_date == today:
        return f"The moon phase is {phase_name}."
    return f"The moon phase {_date_text(target_date, today)} will be {phase_name}."


def _next_phase_response(phase_name: str, target_date: date, today: date) -> str:
    if target_date == today:
        return f"The next {phase_name} is today."
    if target_date == today + timedelta(days=1):
        return f"The next {phase_name} is tomorrow, {target_date.strftime('%B')} {target_date.day}."
    return (
        f"The next {phase_name} is on {target_date.strftime('%A, %B')} "
        f"{target_date.day}."
    )


def _spoken_list(values: Sequence[str]) -> str:
    values = [str(value) for value in values if str(value)]
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])}, and {values[-1]}"


def _night_text(target_date: date, today: date) -> str:
    if target_date == today:
        return "tonight"
    if target_date == today + timedelta(days=1):
        return "tomorrow night"
    return f"on {target_date.strftime('%A, %B')} {target_date.day} night"


def _position_height(position: PlanetPosition) -> int:
    return max(0, int(round(abs(position.altitude_degrees))))


def _planet_position_response(position: PlanetPosition) -> str:
    label = position.planet.title()
    height = _position_height(position)
    if position.altitude_degrees <= 0:
        return f"{label} is about {height} degrees below the horizon right now."
    return (
        f"{label} is about {height} degrees above the "
        f"{position.direction} horizon right now."
    )


def _planet_visible_response(position: PlanetPosition) -> str:
    label = position.planet.title()
    height = _position_height(position)
    if position.potentially_visible:
        return (
            f"{label} should be visible right now, about {height} degrees above "
            f"the {position.direction} horizon, assuming clear skies and an "
            "unobstructed view."
        )
    if position.altitude_degrees <= 0:
        return f"No. {label} is below the horizon right now."
    if position.sun_altitude_degrees > PLANET_VISIBILITY_MAX_SUN_ALTITUDE_DEGREES:
        return f"Not right now. {label} is above the horizon, but the sky is too bright."
    if position.altitude_degrees < PLANET_VISIBILITY_MIN_ALTITUDE_DEGREES:
        return (
            f"Not right now. {label} is only about {height} degrees above the "
            "horizon."
        )
    if (
        position.magnitude is not None
        and position.magnitude > PLANET_VISIBILITY_MAX_MAGNITUDE
    ):
        return (
            f"Not without optical aid. {label} is too faint for typical "
            "naked-eye viewing."
        )
    return f"{label} is not likely to be clearly visible right now."


def _planet_window_response(
    window: PlanetVisibility,
    *,
    target_date: date,
    today: date,
) -> str:
    label = window.planet.title()
    night = _night_text(target_date, today)
    height = max(0, int(round(window.best_altitude_degrees)))
    return (
        f"{label} should be visible {night} from {_clock_text(window.start)} to "
        f"{_clock_text(window.end)}, and highest around "
        f"{_clock_text(window.best_time)}, about {height} degrees up in the "
        f"{window.best_direction}. This assumes clear skies and an unobstructed view."
    )


def _visible_planets_response(
    windows: Sequence[PlanetVisibility],
    *,
    target_date: date,
    today: date,
) -> str:
    night = _night_text(target_date, today)
    if not windows:
        return f"No naked-eye planets are likely to be well placed {night}."
    labels = _spoken_list([window.planet.title() for window in windows])
    details = _spoken_list(
        [
            f"{window.planet.title()} around {_clock_text(window.best_time)} "
            f"in the {window.best_direction}"
            for window in windows
        ]
    )
    return (
        f"{labels} should be visible {night}. Best viewing is {details}. "
        "This assumes clear skies and an unobstructed view."
    )


def handle_astronomy_query(
    text: str,
    *,
    home_location: Optional[dict],
    states_snapshot: Optional[Sequence[dict]] = None,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Return a deterministic spoken answer, or None for unrelated text."""
    query = parse_astronomy_query(text)
    if query is None:
        return None

    current = astronomy_now(home_location, now=now)

    if query.intent in _PLANETARY_INTENTS:
        if not astronomy_location_configured(home_location):
            return "Home location isn't configured for astronomy yet."
        if not planetary_available():
            return "Planetary calculations aren't available right now."

        if query.intent == "planet_event" and query.planet and query.event:
            if query.explicit_day:
                target_date = astronomy_query_date(
                    query,
                    home_location=home_location,
                    now=current,
                )
                resolved = resolve_planet_event_on_date(
                    query.planet,
                    query.event,
                    target_date,
                    home_location=home_location,
                    night_window=query.night_window,
                )
                date_text = (
                    _night_text(target_date, current.date())
                    if query.night_window
                    else _date_text(target_date, current.date())
                )
            else:
                resolved = find_next_planet_event(
                    query.planet,
                    query.event,
                    after=current,
                    home_location=home_location,
                )
                date_text = (
                    _date_text(resolved.date(), current.date())
                    if resolved is not None
                    else "soon"
                )

            label = query.planet.title()
            if resolved is None:
                return f"I couldn't find a {query.event} for {label} {date_text}."
            if query.event == "rise":
                verb = "rose" if resolved <= current else "rises"
            else:
                verb = "set" if resolved <= current else "sets"
            return f"{label} {verb} at {_clock_text(resolved)} {date_text}."

        if query.intent in {"planet_position", "planet_up", "planet_visible"}:
            position = planet_position(
                query.planet or "",
                at=current,
                home_location=home_location,
                min_altitude_degrees=PLANET_VISIBILITY_MIN_ALTITUDE_DEGREES,
                max_sun_altitude_degrees=PLANET_VISIBILITY_MAX_SUN_ALTITUDE_DEGREES,
                max_magnitude=PLANET_VISIBILITY_MAX_MAGNITUDE,
            )
            if position is None:
                return f"I couldn't calculate {query.planet.title()}'s position right now."
            if query.intent == "planet_position":
                return _planet_position_response(position)
            if query.intent == "planet_up":
                answer = "Yes" if position.altitude_degrees > 0 else "No"
                return f"{answer}. {_planet_position_response(position)}"
            return _planet_visible_response(position)

        target_date = astronomy_query_date(
            query,
            home_location=home_location,
            now=current,
        )
        if query.intent == "planet_best" and query.planet:
            window = planet_visibility_window(
                query.planet,
                target_date,
                home_location=home_location,
                min_altitude_degrees=PLANET_VISIBILITY_MIN_ALTITUDE_DEGREES,
                max_sun_altitude_degrees=PLANET_VISIBILITY_MAX_SUN_ALTITUDE_DEGREES,
                max_magnitude=PLANET_VISIBILITY_MAX_MAGNITUDE,
                min_duration_minutes=PLANET_VISIBILITY_MIN_DURATION_MINUTES,
            )
            if window is None:
                night = _night_text(target_date, current.date())
                return (
                    f"{query.planet.title()} isn't likely to be well placed "
                    f"for naked-eye viewing {night}."
                )
            return _planet_window_response(
                window,
                target_date=target_date,
                today=current.date(),
            )

        if query.intent == "visible_planets":
            windows = visible_planets(
                target_date,
                home_location=home_location,
                planets=PLANET_VISIBILITY_PLANETS,
                min_altitude_degrees=PLANET_VISIBILITY_MIN_ALTITUDE_DEGREES,
                max_sun_altitude_degrees=PLANET_VISIBILITY_MAX_SUN_ALTITUDE_DEGREES,
                max_magnitude=PLANET_VISIBILITY_MAX_MAGNITUDE,
                min_duration_minutes=PLANET_VISIBILITY_MIN_DURATION_MINUTES,
            )
            return _visible_planets_response(
                windows,
                target_date=target_date,
                today=current.date(),
            )

    if query.intent == "phase_event" and query.phase:
        if not astral_available():
            return "Astronomy calculations aren't available right now."
        target_date = find_next_moon_phase_date(
            query.phase,
            start_date=current.date(),
        )
        if target_date is None:
            return f"I couldn't calculate the next {query.phase} right now."
        return _next_phase_response(query.phase, target_date, current.date())

    if query.intent == "phase":
        target_date = astronomy_query_date(query, home_location=home_location, now=current)
        phase_name = _ha_moon_phase(states_snapshot) if target_date == current.date() else None
        if phase_name is None:
            if not astral_available():
                return "Astronomy calculations aren't available right now."
            phase_name = moon_phase_name(target_date)
        if phase_name is None:
            return "I couldn't calculate the moon phase right now."
        return _phase_response(phase_name, target_date, current.date())

    if query.intent == "status" and query.body == "sun":
        is_up = _ha_sun_is_up(states_snapshot)
        if is_up is not None:
            position = "above" if is_up else "below"
            return f"{'Yes' if is_up else 'No'}. The sun is {position} the horizon."

    if not astronomy_location_configured(home_location):
        return "Home location isn't configured for astronomy yet."
    if not astral_available():
        return "Astronomy calculations aren't available right now."

    if query.intent == "status":
        is_up = body_is_up(query.body or "", at=current, home_location=home_location)
        if is_up is None:
            return f"I couldn't determine whether the {query.body} is up right now."
        position = "above" if is_up else "below"
        return f"{'Yes' if is_up else 'No'}. The {query.body} is {position} the horizon."

    if query.intent != "event" or not query.event:
        return None

    if query.explicit_day:
        target_date = astronomy_query_date(query, home_location=home_location, now=current)
        resolved = resolve_astronomy_event(
            query.event,
            target_date,
            home_location=home_location,
        )
        if resolved is None:
            event_name = _EVENT_LABELS[query.event].lower()
            return f"There is no {event_name} here {_date_text(target_date, current.date())}."
    else:
        resolved = find_next_astronomy_event(
            query.event,
            after=current,
            home_location=home_location,
        )
        if resolved is None:
            return f"I couldn't find the next {_EVENT_LABELS[query.event].lower()}."
        target_date = resolved.date()

    tense = "was" if resolved <= current else "is"
    return (
        f"{_EVENT_LABELS[query.event]} {tense} at {_clock_text(resolved)} "
        f"{_date_text(target_date, current.date())}."
    )

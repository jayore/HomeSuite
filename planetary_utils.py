"""Calculate local planetary events and observing windows with Skyfield.

This module owns the JPL ephemeris lifecycle so ``astronomy_controls`` can stay
focused on parsing and spoken responses. The ephemeris is supplied by the
``skyfield-data`` package and opened directly from disk; command handling never
downloads astronomy data or calls an external API.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
import logging
import math
from pathlib import Path
import threading
from typing import Any, Iterable, Optional, Sequence

from astronomy_utils import astronomy_now, astronomy_timezone


try:
    import numpy as np
    from skyfield import almanac
    from skyfield.api import Loader, load_file, wgs84
    from skyfield.magnitudelib import planetary_magnitude
    from skyfield_data import get_skyfield_data_path
except ImportError:
    np = None
    almanac = None
    Loader = None
    load_file = None
    wgs84 = None
    planetary_magnitude = None
    get_skyfield_data_path = None


SUPPORTED_PLANETS = (
    "mercury",
    "venus",
    "mars",
    "jupiter",
    "saturn",
    "uranus",
    "neptune",
)
DEFAULT_VISIBLE_PLANETS = ("mercury", "venus", "mars", "jupiter", "saturn")

_PLANET_TARGETS = {
    "mercury": "mercury",
    "venus": "venus",
    "mars": "mars",
    "jupiter": "jupiter barycenter",
    "saturn": "saturn barycenter",
    "uranus": "uranus barycenter",
    "neptune": "neptune barycenter",
}
_DIRECTIONS = (
    "north",
    "northeast",
    "east",
    "southeast",
    "south",
    "southwest",
    "west",
    "northwest",
)
_CONTEXT_UNSET = object()
_CONTEXT: Any = _CONTEXT_UNSET
_CONTEXT_LOCK = threading.Lock()
_VISIBILITY_CACHE: dict[tuple, tuple[Any, ...]] = {}
_VISIBILITY_CACHE_LOCK = threading.Lock()
_VISIBILITY_CACHE_LIMIT = 32


@dataclass(frozen=True)
class PlanetPosition:
    """Apparent topocentric position and basic visibility at one instant."""

    planet: str
    at: datetime
    altitude_degrees: float
    azimuth_degrees: float
    direction: str
    magnitude: Optional[float]
    sun_altitude_degrees: float
    potentially_visible: bool


@dataclass(frozen=True)
class PlanetVisibility:
    """One useful naked-eye observing window during a local night."""

    planet: str
    start: datetime
    end: datetime
    best_time: datetime
    best_altitude_degrees: float
    best_direction: str
    magnitude: Optional[float]


@dataclass(frozen=True)
class _SkyfieldContext:
    timescale: Any
    ephemeris: Any


def planetary_imports_available() -> bool:
    """Return whether Skyfield and the packaged ephemeris dependency import."""
    return all(
        value is not None
        for value in (
            np,
            almanac,
            Loader,
            load_file,
            wgs84,
            planetary_magnitude,
            get_skyfield_data_path,
        )
    )


def _load_context() -> Optional[_SkyfieldContext]:
    global _CONTEXT
    if _CONTEXT is not _CONTEXT_UNSET:
        return _CONTEXT

    with _CONTEXT_LOCK:
        if _CONTEXT is not _CONTEXT_UNSET:
            return _CONTEXT
        if not planetary_imports_available():
            _CONTEXT = None
            return None

        try:
            data_path = Path(get_skyfield_data_path())
            ephemeris_path = data_path / "de421.bsp"
            if not ephemeris_path.is_file():
                logging.error(
                    "PLANETARY_EPHEMERIS_MISSING path=%s",
                    ephemeris_path,
                )
                _CONTEXT = None
                return None

            # load_file() cannot download. Built-in timescale data likewise
            # keeps every command independent of network availability.
            loader = Loader(str(data_path), verbose=False, expire=False)
            _CONTEXT = _SkyfieldContext(
                timescale=loader.timescale(builtin=True),
                ephemeris=load_file(str(ephemeris_path)),
            )
            logging.info("PLANETARY_EPHEMERIS_READY path=%s", ephemeris_path)
        except Exception:
            logging.exception("PLANETARY_EPHEMERIS_LOAD_FAIL")
            _CONTEXT = None
        return _CONTEXT


def planetary_available() -> bool:
    """Return whether local planetary calculations are ready for use."""
    return _load_context() is not None


def normalize_planet_name(value: str) -> Optional[str]:
    """Return a canonical supported planet name."""
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _PLANET_TARGETS else None


def _location_values(home_location: Optional[dict]) -> Optional[tuple[float, float, float]]:
    if not isinstance(home_location, dict):
        return None
    try:
        latitude = float(home_location.get("latitude"))
        longitude = float(home_location.get("longitude"))
        elevation = float(home_location.get("elevation_m") or 0.0)
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        return None
    return latitude, longitude, elevation


def _observer(context: _SkyfieldContext, home_location: Optional[dict]):
    location = _location_values(home_location)
    if location is None:
        return None
    latitude, longitude, elevation = location
    return context.ephemeris["earth"] + wgs84.latlon(
        latitude,
        longitude,
        elevation_m=elevation,
    )


def _target(context: _SkyfieldContext, planet: str):
    normalized = normalize_planet_name(planet)
    if normalized is None:
        return None
    return context.ephemeris[_PLANET_TARGETS[normalized]]


def _direction(azimuth_degrees: float) -> str:
    index = int((float(azimuth_degrees) + 22.5) // 45.0) % len(_DIRECTIONS)
    return _DIRECTIONS[index]


def _magnitude(astrometric) -> Optional[float]:
    try:
        value = float(planetary_magnitude(astrometric))
    except Exception:
        return None
    return value if math.isfinite(value) else None


def planet_position(
    planet: str,
    *,
    at: datetime,
    home_location: Optional[dict],
    min_altitude_degrees: float = 10.0,
    max_sun_altitude_degrees: float = -6.0,
    max_magnitude: float = 6.0,
) -> Optional[PlanetPosition]:
    """Return a planet's apparent local position and potential visibility."""
    normalized = normalize_planet_name(planet)
    context = _load_context()
    if normalized is None or context is None:
        return None
    observer = _observer(context, home_location)
    target = _target(context, normalized)
    if observer is None or target is None:
        return None

    current = astronomy_now(home_location, now=at)
    try:
        sky_time = context.timescale.from_datetime(current.astimezone(timezone.utc))
        astrometric = observer.at(sky_time).observe(target)
        altitude, azimuth, _distance = astrometric.apparent().altaz()
        sun_altitude, _sun_azimuth, _sun_distance = (
            observer.at(sky_time)
            .observe(context.ephemeris["sun"])
            .apparent()
            .altaz()
        )
        magnitude = _magnitude(astrometric)
        bright_enough = magnitude is None or magnitude <= float(max_magnitude)
        potentially_visible = bool(
            altitude.degrees >= float(min_altitude_degrees)
            and sun_altitude.degrees <= float(max_sun_altitude_degrees)
            and bright_enough
        )
        return PlanetPosition(
            planet=normalized,
            at=current,
            altitude_degrees=float(altitude.degrees),
            azimuth_degrees=float(azimuth.degrees),
            direction=_direction(azimuth.degrees),
            magnitude=magnitude,
            sun_altitude_degrees=float(sun_altitude.degrees),
            potentially_visible=potentially_visible,
        )
    except Exception:
        logging.exception("PLANETARY_POSITION_FAIL planet=%s", normalized)
        return None


def _events_between(
    planet: str,
    event: str,
    *,
    start: datetime,
    end: datetime,
    home_location: Optional[dict],
) -> list[datetime]:
    normalized = normalize_planet_name(planet)
    context = _load_context()
    if normalized is None or context is None or event not in {"rise", "set"}:
        return []
    observer = _observer(context, home_location)
    target = _target(context, normalized)
    if observer is None or target is None:
        return []

    timezone_info = astronomy_timezone(home_location, now=start)
    start_local = astronomy_now(home_location, now=start)
    end_local = astronomy_now(home_location, now=end)
    try:
        start_time = context.timescale.from_datetime(start_local.astimezone(timezone.utc))
        end_time = context.timescale.from_datetime(end_local.astimezone(timezone.utc))
        finder = almanac.find_risings if event == "rise" else almanac.find_settings
        event_times, actual_crossings = finder(observer, target, start_time, end_time)
        resolved = []
        for index, actual_crossing in enumerate(actual_crossings):
            if not bool(actual_crossing):
                continue
            local_time = event_times[index].utc_datetime().astimezone(timezone_info)
            if start_local <= local_time <= end_local:
                resolved.append(local_time)
        return resolved
    except Exception:
        logging.exception(
            "PLANETARY_EVENT_FAIL planet=%s event=%s",
            normalized,
            event,
        )
        return []


def find_next_planet_event(
    planet: str,
    event: str,
    *,
    after: datetime,
    home_location: Optional[dict],
    max_days: int = 8,
) -> Optional[datetime]:
    """Return the next real rise or set crossing after an instant."""
    current = astronomy_now(home_location, now=after)
    events = _events_between(
        planet,
        event,
        start=current,
        end=current + timedelta(days=max(1, int(max_days))),
        home_location=home_location,
    )
    return next((value for value in events if value > current), None)


def resolve_planet_event_on_date(
    planet: str,
    event: str,
    target_date: date,
    *,
    home_location: Optional[dict],
    night_window: bool = False,
) -> Optional[datetime]:
    """Return a rise/set crossing on a civil date or its following night."""
    if not isinstance(target_date, date):
        return None
    timezone_info = astronomy_timezone(home_location)
    start_clock = time(hour=12) if night_window else time.min
    start = datetime.combine(target_date, start_clock, tzinfo=timezone_info)
    end = start + timedelta(days=1)
    events = _events_between(
        planet,
        event,
        start=start,
        end=end,
        home_location=home_location,
    )
    return events[0] if events else None


def _contiguous_runs(indices: Iterable[int]) -> list[list[int]]:
    runs: list[list[int]] = []
    for index in indices:
        if not runs or index != runs[-1][-1] + 1:
            runs.append([index])
        else:
            runs[-1].append(index)
    return runs


def visible_planets(
    target_date: date,
    *,
    home_location: Optional[dict],
    planets: Sequence[str] = DEFAULT_VISIBLE_PLANETS,
    min_altitude_degrees: float = 10.0,
    max_sun_altitude_degrees: float = -6.0,
    max_magnitude: float = 6.0,
    min_duration_minutes: int = 15,
    sample_minutes: int = 5,
) -> list[PlanetVisibility]:
    """Return useful potential viewing windows from local noon to noon."""
    context = _load_context()
    if context is None or not isinstance(target_date, date):
        return []
    observer = _observer(context, home_location)
    if observer is None:
        return []

    normalized_planets = []
    for value in planets or ():
        normalized = normalize_planet_name(value)
        if normalized and normalized not in normalized_planets:
            normalized_planets.append(normalized)
    if not normalized_planets:
        return []

    sample_minutes = max(1, int(sample_minutes))
    location = _location_values(home_location)
    if location is None:
        return []
    cache_key = (
        target_date,
        location,
        str((home_location or {}).get("timezone") or ""),
        tuple(normalized_planets),
        float(min_altitude_degrees),
        float(max_sun_altitude_degrees),
        float(max_magnitude),
        int(min_duration_minutes),
        sample_minutes,
    )
    with _VISIBILITY_CACHE_LOCK:
        cached = _VISIBILITY_CACHE.get(cache_key)
    if cached is not None:
        logging.info("PLANETARY_VISIBILITY_CACHE_HIT date=%s", target_date)
        return list(cached)

    timezone_info = astronomy_timezone(home_location)
    start = datetime.combine(target_date, time(hour=12), tzinfo=timezone_info)
    end = start + timedelta(days=1)
    sample_count = int((24 * 60) / sample_minutes) + 1
    datetimes = [
        start + timedelta(minutes=index * sample_minutes)
        for index in range(sample_count)
    ]

    try:
        sky_times = context.timescale.from_datetimes(
            [value.astimezone(timezone.utc) for value in datetimes]
        )
        sun_altitudes = (
            observer.at(sky_times)
            .observe(context.ephemeris["sun"])
            .apparent()
            .altaz()[0]
            .degrees
        )
    except Exception:
        logging.exception("PLANETARY_VISIBILITY_SUN_FAIL date=%s", target_date)
        return []

    min_samples = max(
        1,
        int(math.ceil(max(1, int(min_duration_minutes)) / sample_minutes)),
    )
    windows = []
    for planet in normalized_planets:
        try:
            astrometric = observer.at(sky_times).observe(_target(context, planet))
            altitude, azimuth, _distance = astrometric.apparent().altaz()
            altitudes = np.asarray(altitude.degrees, dtype=float)
            azimuths = np.asarray(azimuth.degrees, dtype=float)
            try:
                magnitudes = np.asarray(
                    planetary_magnitude(astrometric),
                    dtype=float,
                )
            except Exception:
                magnitudes = np.full(altitudes.shape, np.nan)

            bright_enough = np.logical_or(
                np.logical_not(np.isfinite(magnitudes)),
                magnitudes <= float(max_magnitude),
            )
            usable = np.logical_and.reduce(
                (
                    altitudes >= float(min_altitude_degrees),
                    np.asarray(sun_altitudes) <= float(max_sun_altitude_degrees),
                    bright_enough,
                )
            )
            runs = [
                run
                for run in _contiguous_runs(np.flatnonzero(usable).tolist())
                if len(run) >= min_samples
            ]
            if not runs:
                continue

            run = max(runs, key=lambda values: float(np.max(altitudes[values])))
            best_index = max(run, key=lambda value: float(altitudes[value]))
            magnitude = float(magnitudes[best_index])
            if not math.isfinite(magnitude):
                magnitude = None
            window_end = min(
                end,
                datetimes[run[-1]] + timedelta(minutes=sample_minutes),
            )
            windows.append(
                PlanetVisibility(
                    planet=planet,
                    start=datetimes[run[0]],
                    end=window_end,
                    best_time=datetimes[best_index],
                    best_altitude_degrees=float(altitudes[best_index]),
                    best_direction=_direction(azimuths[best_index]),
                    magnitude=magnitude,
                )
            )
        except Exception:
            logging.exception(
                "PLANETARY_VISIBILITY_FAIL planet=%s date=%s",
                planet,
                target_date,
            )

    windows = sorted(windows, key=lambda window: (window.start, window.planet))
    with _VISIBILITY_CACHE_LOCK:
        if len(_VISIBILITY_CACHE) >= _VISIBILITY_CACHE_LIMIT:
            _VISIBILITY_CACHE.pop(next(iter(_VISIBILITY_CACHE)))
        _VISIBILITY_CACHE[cache_key] = tuple(windows)
    return windows


def planet_visibility_window(
    planet: str,
    target_date: date,
    *,
    home_location: Optional[dict],
    min_altitude_degrees: float = 10.0,
    max_sun_altitude_degrees: float = -6.0,
    max_magnitude: float = 6.0,
    min_duration_minutes: int = 15,
) -> Optional[PlanetVisibility]:
    """Return one planet's best useful viewing window during a local night."""
    windows = visible_planets(
        target_date,
        home_location=home_location,
        planets=(planet,),
        min_altitude_degrees=min_altitude_degrees,
        max_sun_altitude_degrees=max_sun_altitude_degrees,
        max_magnitude=max_magnitude,
        min_duration_minutes=min_duration_minutes,
    )
    return windows[0] if windows else None

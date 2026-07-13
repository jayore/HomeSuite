"""Handle capability-specific Home Assistant commands beyond binary on/off.

Covers, fans, climate devices, and vacuums expose verbs and payloads that must
not be synthesized from an arbitrary entity domain. This module recognizes a
small deterministic grammar, resolves one real entity through the shared
resolver, verifies its domain, and calls only documented Home Assistant
services. Unrelated language falls through without being claimed.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Optional, Tuple


ResolveFn = Callable[[str], Optional[Tuple[Any, ...]]]

_COVER_HINT = re.compile(
    r"\b(?:awnings?|blinds?|curtains?|drapes?|garage\s+doors?|gates?|shades?|shutters?|covers?)\b"
)
_FAN_HINT = re.compile(r"\bfans?\b")
_CLIMATE_HINT = re.compile(
    r"\b(?:thermostats?|climate|heaters?|air\s+conditioners?|a\s*c|temperature)\b"
)
_VACUUM_HINT = re.compile(r"\b(?:vacuums?|roombas?|robot\s+(?:vacuum|cleaner))\b")


def _norm(text: str) -> str:
    text = (text or "").strip().lower()
    text = text.replace("’", "'")
    text = re.sub(r"[?.!]+$", "", text).strip()
    return re.sub(r"\s+", " ", text).strip()


def _clean_target(text: str) -> str:
    return re.sub(r"^(?:the|my)\s+", "", (text or "").strip()).strip()


def _resolve_domain(
    phrase: str,
    expected_domain: str,
    resolve_device_entity: ResolveFn,
) -> Optional[str]:
    try:
        resolved = resolve_device_entity(_clean_target(phrase))
    except Exception:
        resolved = None
    if not isinstance(resolved, (tuple, list)) or len(resolved) < 2:
        return None
    entity_id = str(resolved[0] or "").strip()
    resolved_domain = str(resolved[1] or "").strip().lower()
    entity_domain = entity_id.split(".", 1)[0].lower() if "." in entity_id else ""
    if resolved_domain != expected_domain or entity_domain != expected_domain:
        return None
    return entity_id


def _state_obj(states_snapshot: Optional[list], entity_id: str) -> Optional[dict]:
    for state in states_snapshot or []:
        if isinstance(state, dict) and state.get("entity_id") == entity_id:
            return state
    return None


def _attrs(states_snapshot: Optional[list], entity_id: str) -> dict:
    state = _state_obj(states_snapshot, entity_id) or {}
    attrs = state.get("attributes") or {}
    return attrs if isinstance(attrs, dict) else {}


def _say(maybe_say, text: str) -> str:
    if not maybe_say:
        return text
    try:
        result = maybe_say(text)
    except Exception:
        result = None
    return text if result is None else result


def _call_action(
    service: str,
    payload: dict,
    *,
    failure_text: str,
    call_ha_service,
    maybe_say,
) -> str:
    result = call_ha_service(service, payload)
    if result is not None and not bool(result):
        return failure_text
    return _say(maybe_say, "Okay.")


def _handle_cover(
    text: str,
    *,
    states_snapshot: Optional[list],
    resolve_device_entity: ResolveFn,
    call_ha_service,
    maybe_say,
) -> Optional[str]:
    match = re.match(
        r"^(?:set|move)\s+(?:the\s+)?(?P<target>.+?)\s+to\s+"
        r"(?P<position>\d{1,3})\s*(?:percent|%)$",
        text,
    )
    if match and _COVER_HINT.search(match.group("target")):
        position = int(match.group("position"))
        if not 0 <= position <= 100:
            return "Cover position must be between 0 and 100 percent."
        entity_id = _resolve_domain(match.group("target"), "cover", resolve_device_entity)
        if not entity_id:
            return "I couldn't find that cover."
        return _call_action(
            "cover/set_cover_position",
            {"entity_id": entity_id, "position": position},
            failure_text="I couldn't move that cover.",
            call_ha_service=call_ha_service,
            maybe_say=maybe_say,
        )

    match = re.match(
        r"^(?P<verb>open|close|raise|lower)\s+(?:the\s+)?(?P<target>.+)$",
        text,
    )
    if match and _COVER_HINT.search(match.group("target")):
        entity_id = _resolve_domain(match.group("target"), "cover", resolve_device_entity)
        if not entity_id:
            return "I couldn't find that cover."
        service = (
            "cover/open_cover"
            if match.group("verb") in ("open", "raise")
            else "cover/close_cover"
        )
        return _call_action(
            service,
            {"entity_id": entity_id},
            failure_text="I couldn't move that cover.",
            call_ha_service=call_ha_service,
            maybe_say=maybe_say,
        )

    match = re.match(r"^stop\s+(?:the\s+)?(?P<target>.+)$", text)
    if match and _COVER_HINT.search(match.group("target")):
        entity_id = _resolve_domain(match.group("target"), "cover", resolve_device_entity)
        if not entity_id:
            return "I couldn't find that cover."
        return _call_action(
            "cover/stop_cover",
            {"entity_id": entity_id},
            failure_text="I couldn't stop that cover.",
            call_ha_service=call_ha_service,
            maybe_say=maybe_say,
        )

    match = re.match(
        r"^(?:what(?:'s| is)\s+the\s+position\s+of|how\s+open\s+(?:is|are))\s+"
        r"(?:the\s+)?(?P<target>.+)$",
        text,
    )
    if match and _COVER_HINT.search(match.group("target")):
        entity_id = _resolve_domain(match.group("target"), "cover", resolve_device_entity)
        if not entity_id:
            return "I couldn't find that cover."
        position = _attrs(states_snapshot, entity_id).get("current_position")
        try:
            return f"It's at about {int(round(float(position)))} percent."
        except (TypeError, ValueError):
            return "That cover doesn't report its position."

    return None


def _handle_fan(
    text: str,
    *,
    states_snapshot: Optional[list],
    resolve_device_entity: ResolveFn,
    call_ha_service,
    maybe_say,
) -> Optional[str]:
    match = re.match(
        r"^set\s+(?:the\s+)?(?P<target>.+?\bfans?)\s+(?:speed\s+)?to\s+"
        r"(?P<value>\d{1,3})\s*(?:percent|%)$",
        text,
    )
    if match:
        percentage = int(match.group("value"))
        if not 0 <= percentage <= 100:
            return "Fan speed must be between 0 and 100 percent."
        entity_id = _resolve_domain(match.group("target"), "fan", resolve_device_entity)
        if not entity_id:
            return "I couldn't find that fan."
        return _call_action(
            "fan/set_percentage",
            {"entity_id": entity_id, "percentage": percentage},
            failure_text="I couldn't set that fan speed.",
            call_ha_service=call_ha_service,
            maybe_say=maybe_say,
        )

    match = re.match(
        r"^set\s+(?:the\s+)?(?P<target>.+?\bfans?)\s+(?:speed\s+)?to\s+"
        r"(?P<value>[a-z][a-z0-9 _-]*)$",
        text,
    )
    if match:
        entity_id = _resolve_domain(match.group("target"), "fan", resolve_device_entity)
        if not entity_id:
            return "I couldn't find that fan."
        requested = match.group("value").strip().lower()
        percentages = {"off": 0, "low": 33, "medium": 67, "high": 100, "max": 100, "full": 100}
        if requested in percentages:
            return _call_action(
                "fan/set_percentage",
                {"entity_id": entity_id, "percentage": percentages[requested]},
                failure_text="I couldn't set that fan speed.",
                call_ha_service=call_ha_service,
                maybe_say=maybe_say,
            )

        preset_modes = _attrs(states_snapshot, entity_id).get("preset_modes") or []
        presets = {
            str(mode).strip().lower().replace("_", " "): str(mode)
            for mode in preset_modes
            if str(mode).strip()
        }
        normalized = requested.replace("_", " ")
        if normalized not in presets:
            return "That fan doesn't report that preset mode."
        return _call_action(
            "fan/set_preset_mode",
            {"entity_id": entity_id, "preset_mode": presets[normalized]},
            failure_text="I couldn't set that fan preset.",
            call_ha_service=call_ha_service,
            maybe_say=maybe_say,
        )

    match = re.match(
        r"^(?P<verb>increase|decrease|raise|lower)\s+(?:the\s+)?"
        r"(?P<target>.+?\bfans?)(?:\s+speed)?$",
        text,
    ) or re.match(
        r"^(?P<verb>speed\s+up|slow\s+down)\s+(?:the\s+)?(?P<target>.+?\bfans?)$",
        text,
    )
    if match:
        entity_id = _resolve_domain(match.group("target"), "fan", resolve_device_entity)
        if not entity_id:
            return "I couldn't find that fan."
        service = (
            "fan/increase_speed"
            if match.group("verb") in ("increase", "raise", "speed up")
            else "fan/decrease_speed"
        )
        return _call_action(
            service,
            {"entity_id": entity_id},
            failure_text="I couldn't adjust that fan speed.",
            call_ha_service=call_ha_service,
            maybe_say=maybe_say,
        )

    match = re.match(
        r"^what(?:'s| is)\s+(?:the\s+)?(?P<target>.+?\bfans?)\s+speed$",
        text,
    ) or re.match(
        r"^how\s+fast\s+is\s+(?:the\s+)?(?P<target>.+?\bfans?)$",
        text,
    )
    if match:
        entity_id = _resolve_domain(match.group("target"), "fan", resolve_device_entity)
        if not entity_id:
            return "I couldn't find that fan."
        attrs = _attrs(states_snapshot, entity_id)
        preset = str(attrs.get("preset_mode") or "").strip()
        percentage = attrs.get("percentage")
        if preset:
            return f"It's using the {preset} preset."
        try:
            return f"It's at about {int(round(float(percentage)))} percent."
        except (TypeError, ValueError):
            return "That fan doesn't report its speed."

    return None


def _handle_climate(
    text: str,
    *,
    states_snapshot: Optional[list],
    resolve_device_entity: ResolveFn,
    call_ha_service,
    maybe_say,
) -> Optional[str]:
    match = re.match(
        r"^set\s+(?:the\s+)?(?P<target>.+?)\s+(?:temperature\s+)?to\s+"
        r"(?P<temperature>-?\d+(?:\.\d+)?)\s*(?:degrees?)?$",
        text,
    )
    if match and _CLIMATE_HINT.search(match.group("target")):
        target = match.group("target")
        if target in ("temperature", "temp"):
            target = "thermostat"
        entity_id = _resolve_domain(target, "climate", resolve_device_entity)
        if not entity_id:
            return "I couldn't find that thermostat."
        temperature = float(match.group("temperature"))
        attrs = _attrs(states_snapshot, entity_id)
        try:
            min_temp = float(attrs.get("min_temp"))
            max_temp = float(attrs.get("max_temp"))
        except (TypeError, ValueError):
            min_temp = max_temp = None
        if min_temp is not None and not min_temp <= temperature <= max_temp:
            return f"That thermostat accepts temperatures from {min_temp:g} to {max_temp:g} degrees."
        return _call_action(
            "climate/set_temperature",
            {"entity_id": entity_id, "temperature": temperature},
            failure_text="I couldn't set that thermostat.",
            call_ha_service=call_ha_service,
            maybe_say=maybe_say,
        )

    match = re.match(
        r"^set\s+(?:the\s+)?(?P<target>.+?)\s+(?:mode\s+)?to\s+"
        r"(?P<mode>heat|heating|cool|cooling|auto|dry|fan only|fan_only|off|heat cool|heat_cool)$",
        text,
    )
    if match and _CLIMATE_HINT.search(match.group("target")):
        entity_id = _resolve_domain(match.group("target"), "climate", resolve_device_entity)
        if not entity_id:
            return "I couldn't find that thermostat."
        aliases = {
            "heating": "heat",
            "cooling": "cool",
            "fan only": "fan_only",
            "heat cool": "heat_cool",
        }
        mode = aliases.get(match.group("mode"), match.group("mode"))
        hvac_modes = [str(value).strip() for value in (_attrs(states_snapshot, entity_id).get("hvac_modes") or [])]
        if hvac_modes and mode not in hvac_modes:
            return "That thermostat doesn't support that mode."
        return _call_action(
            "climate/set_hvac_mode",
            {"entity_id": entity_id, "hvac_mode": mode},
            failure_text="I couldn't set that thermostat mode.",
            call_ha_service=call_ha_service,
            maybe_say=maybe_say,
        )

    match = re.match(
        r"^what(?:'s| is)\s+(?:the\s+)?(?P<target>.+?)\s+"
        r"(?:set\s+to|target\s+temperature)$",
        text,
    )
    if match and _CLIMATE_HINT.search(match.group("target")):
        entity_id = _resolve_domain(match.group("target"), "climate", resolve_device_entity)
        if not entity_id:
            return "I couldn't find that thermostat."
        attrs = _attrs(states_snapshot, entity_id)
        low = attrs.get("target_temp_low")
        high = attrs.get("target_temp_high")
        if low is not None and high is not None:
            try:
                return f"It's set from {float(low):g} to {float(high):g} degrees."
            except (TypeError, ValueError):
                pass
        target = attrs.get("temperature")
        try:
            return f"It's set to {float(target):g} degrees."
        except (TypeError, ValueError):
            return "That thermostat doesn't report a target temperature."

    match = re.match(
        r"^what\s+mode\s+is\s+(?:the\s+)?(?P<target>.+?)\s+in$",
        text,
    )
    if match and _CLIMATE_HINT.search(match.group("target")):
        entity_id = _resolve_domain(match.group("target"), "climate", resolve_device_entity)
        if not entity_id:
            return "I couldn't find that thermostat."
        state = _state_obj(states_snapshot, entity_id) or {}
        mode = str(state.get("state") or "").strip().replace("_", " ")
        return f"It's in {mode} mode." if mode and mode not in ("unknown", "unavailable") else "I couldn't read that thermostat mode."

    return None


def _handle_vacuum(
    text: str,
    *,
    states_snapshot: Optional[list],
    resolve_device_entity: ResolveFn,
    call_ha_service,
    maybe_say,
) -> Optional[str]:
    if text == "start vacuuming":
        text = "start the vacuum"

    match = re.match(
        r"^(?P<verb>start|run|resume|pause|stop|locate)\s+(?:the\s+)?(?P<target>.+)$",
        text,
    )
    if match and _VACUUM_HINT.search(match.group("target")):
        entity_id = _resolve_domain(match.group("target"), "vacuum", resolve_device_entity)
        if not entity_id:
            return "I couldn't find that vacuum."
        services = {
            "start": "vacuum/start",
            "run": "vacuum/start",
            "resume": "vacuum/start",
            "pause": "vacuum/pause",
            "stop": "vacuum/stop",
            "locate": "vacuum/locate",
        }
        return _call_action(
            services[match.group("verb")],
            {"entity_id": entity_id},
            failure_text="I couldn't control that vacuum.",
            call_ha_service=call_ha_service,
            maybe_say=maybe_say,
        )

    match = re.match(r"^dock\s+(?:the\s+)?(?P<target>.+)$", text) or re.match(
        r"^(?:send|return)\s+(?:the\s+)?(?P<target>.+?)\s+"
        r"(?:home|to\s+(?:the\s+)?(?:base|dock))$",
        text,
    )
    if match and _VACUUM_HINT.search(match.group("target")):
        entity_id = _resolve_domain(match.group("target"), "vacuum", resolve_device_entity)
        if not entity_id:
            return "I couldn't find that vacuum."
        return _call_action(
            "vacuum/return_to_base",
            {"entity_id": entity_id},
            failure_text="I couldn't send that vacuum to its dock.",
            call_ha_service=call_ha_service,
            maybe_say=maybe_say,
        )

    match = re.match(
        r"^what(?:'s| is)\s+(?:the\s+)?(?P<target>.+?)\s+(?:doing|status)$",
        text,
    ) or re.match(r"^where\s+is\s+(?:the\s+)?(?P<target>.+)$", text)
    if match and _VACUUM_HINT.search(match.group("target")):
        entity_id = _resolve_domain(match.group("target"), "vacuum", resolve_device_entity)
        if not entity_id:
            return "I couldn't find that vacuum."
        state = _state_obj(states_snapshot, entity_id) or {}
        status = str(state.get("state") or "").strip().lower().replace("_", " ")
        if not status or status in ("unknown", "unavailable"):
            return "I couldn't read that vacuum right now."
        return f"It's {status}."

    return None


def handle_ha_capability_controls(
    *,
    tl: str,
    states_snapshot: Optional[list],
    resolve_device_entity: ResolveFn,
    call_ha_service,
    maybe_say=None,
) -> Optional[str]:
    """Claim one supported capability command or return ``None``."""
    text = _norm(tl)
    if not text:
        return None

    for handler in (_handle_cover, _handle_fan, _handle_climate, _handle_vacuum):
        result = handler(
            text,
            states_snapshot=states_snapshot,
            resolve_device_entity=resolve_device_entity,
            call_ha_service=call_ha_service,
            maybe_say=maybe_say,
        )
        if result is not None:
            return result
    return None

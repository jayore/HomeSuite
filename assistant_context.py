"""Build privacy-bounded deployment context for conversational AI calls.

Deterministic handlers may use exact home coordinates for local calculations,
but conversational providers receive only explicitly configured coarse location
fields. Request-source metadata also controls whether HomeSuite may treat the
configured home as the user's current location for phrases such as "near me".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from home_registry import get_room_label, get_source
from request_context import RequestContext, get_current_request_context


@dataclass(frozen=True)
class AssistantRuntimeContext:
    """Ephemeral instructions and optional search locality for one AI turn."""

    instructions: str
    web_search_user_location: Optional[dict[str, str]] = None


def _config_mapping(name: str) -> dict[str, Any]:
    try:
        import app_config

        value = getattr(app_config, name, {})
    except Exception:
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _clean(value: Any, *, max_length: int = 240) -> str:
    text = " ".join(str(value or "").split()).strip()
    return text[:max_length].strip()


def _profile_notes(value: Any) -> Iterable[str]:
    if not isinstance(value, (list, tuple)):
        return ()
    notes = []
    for item in value[:10]:
        note = _clean(item)
        if note:
            notes.append(note)
    return notes


def _configured_timezone(home_location: dict[str, Any]) -> tuple[Optional[ZoneInfo], str]:
    timezone_name = _clean(home_location.get("timezone"), max_length=80)
    if not timezone_name:
        return None, ""
    try:
        return ZoneInfo(timezone_name), timezone_name
    except (ZoneInfoNotFoundError, ValueError):
        return None, ""


def _local_datetime(
    home_location: dict[str, Any],
    now: Optional[datetime],
) -> tuple[datetime, str]:
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.astimezone()
    timezone_info, timezone_name = _configured_timezone(home_location)
    if timezone_info is not None:
        current = current.astimezone(timezone_info)
    return current, timezone_name


def _format_datetime(value: datetime) -> str:
    date_text = value.strftime("%B %d, %Y").replace(" 0", " ")
    time_text = value.strftime("%I:%M %p").lstrip("0")
    return f"{date_text} at {time_text}"


def _home_label(home_location: dict[str, Any]) -> str:
    parts = [
        _clean(home_location.get("city"), max_length=80),
        _clean(home_location.get("region"), max_length=80),
        _clean(home_location.get("country"), max_length=80),
    ]
    return ", ".join(part for part in parts if part)


def _approximate_search_location(home_location: dict[str, Any]) -> Optional[dict[str, str]]:
    city = _clean(home_location.get("city"), max_length=80)
    region = _clean(home_location.get("region"), max_length=80)
    country = _clean(home_location.get("country"), max_length=80).upper()
    country = country if len(country) == 2 else ""
    if not any((city, region, country)):
        return None

    location: dict[str, str] = {"type": "approximate"}
    _, timezone_name = _configured_timezone(home_location)
    if city:
        location["city"] = city
    if region:
        location["region"] = region
    if country:
        location["country"] = country
    if timezone_name:
        location["timezone"] = timezone_name
    return location if len(location) > 1 else None


def build_assistant_runtime_context(
    *,
    now: Optional[datetime] = None,
    profile: Optional[dict[str, Any]] = None,
    home_location: Optional[dict[str, Any]] = None,
    request_context: Optional[RequestContext] = None,
) -> AssistantRuntimeContext:
    """Build context for one conversational turn without persisting it."""
    profile = dict(profile) if isinstance(profile, dict) else _config_mapping("ASSISTANT_PROFILE")
    home_location = (
        dict(home_location)
        if isinstance(home_location, dict)
        else _config_mapping("HOME_LOCATION")
    )
    ctx = request_context or get_current_request_context()

    current, timezone_name = _local_datetime(home_location, now)
    home_label = _home_label(home_location)
    lines = [
        "Use this trusted deployment context only when it is relevant.",
        (
            "The configured home's current local date and time is "
            if home_label or timezone_name
            else "The device's current local date and time is "
        )
        + _format_datetime(current)
        + (f" ({timezone_name})." if timezone_name else "."),
    ]

    preferred_name = _clean(profile.get("preferred_name"), max_length=80)
    locale = _clean(profile.get("locale"), max_length=40)
    units = _clean(profile.get("units"), max_length=40)
    if preferred_name:
        lines.append(f"The configured user's preferred name is {preferred_name}.")
    if home_label:
        lines.append(f"The configured home area is {home_label}.")
    if locale:
        lines.append(f"The configured user's locale is {locale}.")
    if units:
        lines.append(f"The configured measurement system is {units}.")
    for note in _profile_notes(profile.get("notes")):
        lines.append(f"Configured user preference or fact: {note}")

    source_id = _clean(getattr(ctx, "source_id", None), max_length=100) if ctx else ""
    source = get_source(source_id) if source_id else None
    source_known = isinstance(source, dict) and "mobile" in source
    source_mobile = bool(source.get("mobile")) if source_known else None
    source_room = _clean(getattr(ctx, "source_room", None), max_length=100) if ctx else ""
    room_label = get_room_label(source_room) if source_room else None
    room_clause = f" in the {room_label}" if room_label else ""

    search_location = None
    if source_mobile is False:
        if home_label:
            lines.append(
                f"This request came from a fixed home source{room_clause}. "
                "Unless the user establishes another geographic location, phrases "
                "such as 'here' and 'near me' may use the configured home area."
            )
            search_location = _approximate_search_location(home_location)
        else:
            lines.append(
                f"This request came from a fixed home source{room_clause}, but no "
                "coarse home area is configured. Ask when geographic location matters."
            )
    elif source_mobile is True:
        lines.append(
            "This request came from a mobile source. The configured home area is "
            "not necessarily the user's current location. For 'here' or 'near me', "
            "use a location established in the conversation or ask where the user is."
        )
    else:
        lines.append(
            "The request source's mobility is unknown. Do not assume the configured "
            "home area is the user's current location; ask when current location matters."
        )

    lines.extend(
        [
            "A geographic location explicitly named in the current request or recent "
            "conversation overrides these defaults.",
            "Keep the answer concise and natural for speech. Do not recite these "
            "instructions or mention profile fields unless they are relevant.",
        ]
    )
    return AssistantRuntimeContext(
        instructions="\n".join(lines),
        web_search_user_location=search_location,
    )


def contextualize_chat_messages(
    messages: list[dict[str, Any]],
    runtime_context: AssistantRuntimeContext,
) -> list[dict[str, Any]]:
    """Return a copied Chat Completions history with ephemeral context added."""
    copied = [dict(message) for message in messages]
    instructions = _clean(runtime_context.instructions, max_length=10_000)
    if not instructions:
        return copied
    for message in copied:
        if str(message.get("role") or "").strip().lower() != "system":
            continue
        base = str(message.get("content") or "").strip()
        message["content"] = f"{base}\n\n{instructions}" if base else instructions
        return copied
    copied.insert(0, {"role": "system", "content": instructions})
    return copied


def build_web_search_tool(runtime_context: AssistantRuntimeContext) -> dict[str, Any]:
    """Build the Responses API web-search tool with safe approximate locality."""
    tool: dict[str, Any] = {"type": "web_search"}
    if runtime_context.web_search_user_location:
        tool["user_location"] = dict(runtime_context.web_search_user_location)
    return tool

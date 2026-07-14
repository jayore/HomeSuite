"""Deterministic Home Assistant calendar queries and guarded event creation.

Calendar provider authentication stays in Home Assistant. HomeSuite reads from
configured ``calendar.*`` entities through ``calendar.get_events`` and creates
events only after a source-scoped draft has every required field and the user
confirms the exact write. The draft flow supports either conversational order:
name first or date/time first.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time as clock_time, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from dialogue_state import forget_referent, remember_referent, resolve_referent
from schedule_controls import _parse_small_number, _parse_spoken_clock, parse_duration_seconds


_DRAFT_KIND = "calendar_draft"
_DRAFT_CAPABILITY = "calendar_create"
_YES_RE = re.compile(r"^(?:yes|yeah|yep|confirm|do it|add it|create it|sounds good|okay|ok)$")
_NO_RE = re.compile(r"^(?:no|nope|cancel|never mind|nevermind|don't|do not)$")
_CREATE_RE = re.compile(r"^(?:please\s+)?(?:add|create|schedule|put)\b", re.IGNORECASE)
_CALENDAR_WORD_RE = re.compile(r"\b(?:calendar|agenda|appointment|appointments|event|events)\b")

_MONTHS = {
    "january": 1, "jan": 1,
    "february": 2, "feb": 2,
    "march": 3, "mar": 3,
    "april": 4, "apr": 4,
    "may": 5,
    "june": 6, "jun": 6,
    "july": 7, "jul": 7,
    "august": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10,
    "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}
_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}
_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
    "eleventh": 11, "twelfth": 12, "thirteenth": 13, "fourteenth": 14,
    "fifteenth": 15, "sixteenth": 16, "seventeenth": 17,
    "eighteenth": 18, "nineteenth": 19, "twentieth": 20,
    "twenty first": 21, "twenty second": 22, "twenty third": 23,
    "twenty fourth": 24, "twenty fifth": 25, "twenty sixth": 26,
    "twenty seventh": 27, "twenty eighth": 28, "twenty ninth": 29,
    "thirtieth": 30, "thirty first": 31,
}


@dataclass(frozen=True)
class CalendarTarget:
    key: str
    entity_id: str
    label: str
    aliases: Tuple[str, ...]
    writable: bool
    include_in_agenda: bool


@dataclass(frozen=True)
class CalendarQuery:
    start: datetime
    end: datetime
    label: str
    next_only: bool = False
    search_term: str = ""


def _norm(value: str) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("’", "'").replace("‘", "'")
    text = re.sub(r"\bp\.?\s*m\.?\b", "pm", text)
    text = re.sub(r"\ba\.?\s*m\.?\b", "am", text)
    text = re.sub(r"[?!]+$", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _local_timezone() -> ZoneInfo:
    try:
        import app_config

        name = str((getattr(app_config, "HOME_LOCATION", {}) or {}).get("timezone") or "")
        if name:
            return ZoneInfo(name)
    except Exception:
        pass
    local = datetime.now().astimezone().tzinfo
    return local if isinstance(local, ZoneInfo) else ZoneInfo("UTC")


def _config_targets() -> Dict[str, CalendarTarget]:
    try:
        import app_config

        raw = getattr(app_config, "CALENDARS", {}) or {}
        default_key = str(getattr(app_config, "DEFAULT_CALENDAR", "") or "").strip()
    except Exception:
        raw, default_key = {}, ""

    targets = {}
    for key, value in raw.items():
        key_n = str(key or "").strip().lower()
        if isinstance(value, str):
            cfg = {"entity_id": value}
        elif isinstance(value, dict):
            cfg = value
        else:
            continue
        entity_id = str(cfg.get("entity_id") or "").strip()
        if not key_n or not entity_id.startswith("calendar."):
            continue
        label = str(cfg.get("label") or key_n.replace("_", " ")).strip()
        aliases = {
            key_n.replace("_", " "),
            label.lower(),
            *[str(alias or "").strip().lower() for alias in (cfg.get("aliases") or [])],
        }
        aliases = {alias for alias in aliases if alias}
        targets[key_n] = CalendarTarget(
            key=key_n,
            entity_id=entity_id,
            label=label,
            aliases=tuple(sorted(aliases, key=len, reverse=True)),
            writable=bool(cfg.get("writable", False)),
            include_in_agenda=bool(cfg.get("include_in_agenda", key_n == default_key)),
        )
    return targets


def _default_target(targets: Dict[str, CalendarTarget]) -> Optional[CalendarTarget]:
    try:
        import app_config

        default_key = str(getattr(app_config, "DEFAULT_CALENDAR", "") or "").strip().lower()
    except Exception:
        default_key = ""
    if default_key in targets:
        return targets[default_key]
    return next(iter(targets.values()), None)


def _explicit_target(text: str, targets: Dict[str, CalendarTarget]) -> Optional[CalendarTarget]:
    t = _norm(text)
    matches = []
    for target in targets.values():
        for alias in target.aliases:
            if re.search(rf"\b{re.escape(alias)}(?:\s+calendar)?\b", t):
                matches.append((len(alias), target))
    return max(matches, key=lambda item: item[0])[1] if matches else None


def _agenda_targets(text: str, targets: Dict[str, CalendarTarget]) -> List[CalendarTarget]:
    explicit = _explicit_target(text, targets)
    if explicit:
        return [explicit]
    agenda = [target for target in targets.values() if target.include_in_agenda]
    default = _default_target(targets)
    return agenda or ([default] if default else [])


def looks_like_calendar_request(text: str) -> bool:
    t = _norm(text)
    if not t:
        return False
    if _CREATE_RE.match(t) and _CALENDAR_WORD_RE.search(t):
        return True
    return bool(
        re.search(r"\b(?:calendar|agenda)\b", t)
        or re.match(r"^(?:what|which)\s+(?:appointments?|events?)\b", t)
        or re.match(r"^what(?:'s| is)\s+(?:my\s+)?next\s+(?:appointment|event)\b", t)
        or re.match(r"^(?:when is|when's)\s+(?:my|the)\s+.+\b(?:appointment|event)\b", t)
        or re.match(r"^(?:do i have|have i got)\b.*\b(?:appointment|event|anything)\b", t)
    )


def _parse_day_number(value: str) -> Optional[int]:
    token = re.sub(r"(?:st|nd|rd|th)$", "", _norm(value))
    if token.isdigit():
        day = int(token)
    else:
        day = _ORDINALS.get(token)
        if day is None:
            day = _parse_small_number(token)
    return day if day is not None and 1 <= day <= 31 else None


def _date_from_text(text: str, *, today: date) -> Optional[date]:
    t = _norm(text)
    if "day after tomorrow" in t:
        return today + timedelta(days=2)
    if re.search(r"\btomorrow\b", t):
        return today + timedelta(days=1)
    if re.search(r"\btoday\b|\btonight\b", t):
        return today

    month_names = "|".join(sorted(_MONTHS, key=len, reverse=True))
    day_words = "|".join(sorted(_ORDINALS, key=len, reverse=True))
    match = re.search(
        rf"\b(?P<month>{month_names})\s+(?P<day>\d{{1,2}}(?:st|nd|rd|th)?|{day_words})"
        rf"(?:\s*,?\s*(?P<year>20\d{{2}}))?\b",
        t,
    )
    if match:
        day = _parse_day_number(match.group("day"))
        year = int(match.group("year") or today.year)
        if day is None:
            return None
        try:
            candidate = date(year, _MONTHS[match.group("month")], day)
        except ValueError:
            return None
        if not match.group("year") and candidate < today:
            candidate = date(year + 1, candidate.month, candidate.day)
        return candidate

    for name, weekday in _WEEKDAYS.items():
        match = re.search(rf"\b(?P<next>next\s+)?{name}\b", t)
        if not match:
            continue
        delta = (weekday - today.weekday()) % 7
        if match.group("next"):
            delta = delta + 7 if delta else 7
        return today + timedelta(days=delta)
    return None


def _clock_from_text(text: str) -> Optional[clock_time]:
    t = _norm(text)
    match = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", t)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        ampm = match.group(3)
        if minute > 59 or hour > 23 or hour < 0:
            return None
        if ampm:
            if hour < 1 or hour > 12:
                return None
            hour = hour % 12 + (12 if ampm == "pm" else 0)
        elif hour <= 12:
            # Natural appointment shorthand: early numbered hours are usually
            # afternoon, while 7-11 are usually morning. The exact inferred
            # time is always repeated in the write confirmation.
            if 1 <= hour <= 6:
                hour += 12
            elif hour == 12:
                hour = 12
        return clock_time(hour, minute)

    spoken = re.search(
        r"\bat\s+([a-z]+(?:[\s-]+[a-z]+){0,2})(?:\s+(am|pm))?(?:\b|$)",
        t,
    )
    if not spoken:
        return None
    parsed = _parse_spoken_clock(spoken.group(1))
    if not parsed:
        return None
    hour, minute, _explicit = parsed
    ampm = spoken.group(2)
    if ampm:
        if hour > 12:
            return None
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    elif 1 <= hour <= 6:
        hour += 12
    return clock_time(hour, minute)


def _duration_from_text(text: str) -> Optional[float]:
    match = re.search(
        r"\bfor\s+(\d{1,4}|[a-z]+(?:[\s-]+[a-z]+)?)\s+"
        r"(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b",
        _norm(text),
    )
    return parse_duration_seconds(match.group(1), match.group(2)) if match else None


def parse_calendar_query(text: str, *, now: Optional[datetime] = None) -> Optional[CalendarQuery]:
    t = _norm(text)
    if not looks_like_calendar_request(t) or _CREATE_RE.match(t):
        return None
    tz = _local_timezone()
    now = (now or datetime.now(tz)).astimezone(tz)
    today = now.date()

    next_only = bool(re.search(r"\bnext\s+(?:appointment|event|thing)\b", t))
    search_term = ""
    named = re.match(r"^(?:when is|when's)\s+(?:my|the)\s+(.+)$", t)
    if named and not next_only:
        search_term = re.sub(r"\s+(?:appointment|event)$", "", named.group(1)).strip()

    if next_only or search_term:
        return CalendarQuery(
            start=now,
            end=now + timedelta(days=366),
            label="your calendar",
            next_only=next_only,
            search_term=search_term,
        )

    if "next week" in t:
        days_to_monday = (7 - today.weekday()) % 7 or 7
        start_day = today + timedelta(days=days_to_monday)
        end_day = start_day + timedelta(days=7)
        label = "next week"
    elif "this week" in t or re.search(r"\bweek\b", t):
        start_day = today
        end_day = today + timedelta(days=(7 - today.weekday()))
        label = "this week"
    elif "weekend" in t:
        days_to_saturday = (5 - today.weekday()) % 7
        start_day = today + timedelta(days=days_to_saturday)
        end_day = start_day + timedelta(days=2)
        label = "this weekend"
    else:
        start_day = _date_from_text(t, today=today) or today
        end_day = start_day + timedelta(days=1)
        if start_day == today:
            label = "today"
        elif start_day == today + timedelta(days=1):
            label = "tomorrow"
        else:
            label = start_day.strftime("%A")

    return CalendarQuery(
        start=datetime.combine(start_day, clock_time.min, tzinfo=tz),
        end=datetime.combine(end_day, clock_time.min, tzinfo=tz),
        label=label,
    )


def _event_datetime(value: Any, tz: ZoneInfo) -> Tuple[Optional[datetime], bool]:
    if isinstance(value, dict):
        value = value.get("dateTime") or value.get("date")
    raw = str(value or "").strip()
    if not raw:
        return None, False
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return datetime.combine(date.fromisoformat(raw), clock_time.min, tzinfo=tz), True
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None, False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz), False


def _fetch_events(
    query: CalendarQuery,
    targets: Iterable[CalendarTarget],
    get_events: Callable[..., Optional[Dict[str, List[dict]]]],
) -> Optional[List[dict]]:
    target_list = list(targets)
    result = get_events(
        [target.entity_id for target in target_list],
        start_date_time=query.start.isoformat(),
        end_date_time=query.end.isoformat(),
    )
    if result is None:
        return None
    labels = {target.entity_id: target.label for target in target_list}
    tz = query.start.tzinfo or _local_timezone()
    events = []
    for entity_id, rows in result.items():
        for row in rows or []:
            start, all_day = _event_datetime(row.get("start"), tz)
            if start is None:
                continue
            event = dict(row)
            event["_start"] = start
            event["_all_day"] = all_day
            event["_calendar_label"] = labels.get(entity_id, entity_id)
            events.append(event)
    events.sort(key=lambda row: row["_start"])
    return events


def _format_event(event: dict, *, include_day: bool, include_calendar: bool) -> str:
    summary = str(event.get("summary") or "Untitled event").strip()
    start = event["_start"]
    if event.get("_all_day"):
        when = f" on {start.strftime('%A')}" if include_day else " all day"
    else:
        clock = start.strftime("%I:%M %p").lstrip("0").replace(":00 ", " ")
        when = f" on {start.strftime('%A')} at {clock}" if include_day else f" at {clock}"
    calendar = f" on {event.get('_calendar_label')}" if include_calendar else ""
    return f"{summary}{when}{calendar}"


def _answer_query(
    *,
    text: str,
    query: CalendarQuery,
    targets: List[CalendarTarget],
    get_events: Callable[..., Optional[Dict[str, List[dict]]]],
) -> str:
    events = _fetch_events(query, targets, get_events)
    if events is None:
        return "I couldn't reach Home Assistant's calendar service right now."

    if query.search_term:
        terms = [token for token in re.findall(r"[a-z0-9]+", query.search_term) if token]
        events = [
            row for row in events
            if all(token in str(row.get("summary") or "").lower() for token in terms)
        ]
    if query.next_only or query.search_term:
        events = [row for row in events if row["_start"] >= query.start]
        if not events:
            if query.search_term:
                return f"I couldn't find an upcoming {query.search_term} on your calendar."
            return "You don't have any upcoming events on your calendar."
        prefix = "Your next event is" if query.next_only else f"Your next {query.search_term} is"
        return f"{prefix} {_format_event(events[0], include_day=True, include_calendar=len(targets) > 1)}."

    if not events:
        return f"You don't have anything on your calendar {query.label}."
    try:
        import app_config

        limit = max(1, int(getattr(app_config, "CALENDAR_QUERY_MAX_EVENTS", 6)))
    except Exception:
        limit = 6
    selected = events[:limit]
    include_day = (query.end - query.start) > timedelta(days=1)
    pieces = [
        _format_event(row, include_day=include_day, include_calendar=len(targets) > 1)
        for row in selected
    ]
    suffix = f" There are {len(events) - limit} more." if len(events) > limit else ""
    return f"For {query.label}, you have " + "; ".join(pieces) + "." + suffix


def _draft_ttl() -> float:
    try:
        import app_config

        return float(getattr(app_config, "CALENDAR_DRAFT_TTL_SECONDS", 120))
    except Exception:
        return 120.0


def _save_draft(data: dict) -> dict:
    key = str(data.get("id") or uuid.uuid4())
    data = dict(data)
    data["id"] = key
    remember_referent(
        _DRAFT_KIND,
        key,
        label=str(data.get("title") or "calendar event"),
        capabilities=[_DRAFT_CAPABILITY],
        data=data,
        ttl_seconds=_draft_ttl(),
        source="calendar_controls",
    )
    return data


def _current_draft() -> Optional[dict]:
    entry = resolve_referent(kinds=[_DRAFT_KIND], capability=_DRAFT_CAPABILITY)
    return dict(entry.get("data") or {}) if entry else None


def _forget_draft(data: dict) -> None:
    forget_referent(_DRAFT_KIND, key=str(data.get("id") or ""))


def _creation_title(text: str, targets: Dict[str, CalendarTarget]) -> Optional[str]:
    t = _norm(text)
    body = _CREATE_RE.sub("", t, count=1).strip()
    called = re.search(r"\b(?:event|appointment)\s+(?:called|named)\s+(.+?)(?=\s+(?:on|at|for|to)\b|$)", body)
    if called:
        return called.group(1).strip().title()

    aliases = sorted(
        {alias for target in targets.values() for alias in target.aliases},
        key=len,
        reverse=True,
    )
    alias_part = "|".join(re.escape(alias) for alias in aliases)
    marker = re.search(
        rf"\s+(?:to|on)\s+(?:(?:my|the)\s+)?(?:(?:{alias_part})\s+)?calendar\b"
        if alias_part else r"\s+(?:to|on)\s+(?:(?:my|the)\s+)?calendar\b",
        body,
    )
    candidate = body[:marker.start()].strip() if marker else body
    candidate = re.sub(r"^(?:an?\s+)?(?:new\s+)?(?:event|appointment)\b", "", candidate).strip()
    candidate = re.split(
        r"\s+(?:on\s+(?:today|tomorrow|next\s+\w+|[a-z]+\s+\d)|at\s+\d|for\s+\w+\s+(?:minutes?|hours?))\b",
        candidate,
        maxsplit=1,
    )[0].strip()
    return candidate.title() if candidate else None


def _parse_creation(text: str, targets: Dict[str, CalendarTarget], now: datetime) -> Optional[dict]:
    t = _norm(text)
    if not _CREATE_RE.match(t) or not _CALENDAR_WORD_RE.search(t):
        return None
    target = _explicit_target(t, targets) or _default_target(targets)
    event_date = _date_from_text(t, today=now.date())
    event_time = _clock_from_text(t)
    duration = _duration_from_text(t)
    return {
        "id": str(uuid.uuid4()),
        "title": _creation_title(t, targets),
        "date": event_date.isoformat() if event_date else None,
        "time": event_time.isoformat(timespec="minutes") if event_time else None,
        "duration_seconds": duration,
        "calendar_key": target.key if target else None,
        "status": "collecting",
    }


def _fill_draft_from_followup(data: dict, text: str, *, now: datetime) -> bool:
    t = _norm(text)
    changed = False
    event_date = _date_from_text(t, today=now.date())
    event_time = _clock_from_text("at " + t if re.match(r"^\d", t) else t)
    duration = _duration_from_text(t)
    if event_date:
        data["date"] = event_date.isoformat()
        changed = True
    if event_time:
        data["time"] = event_time.isoformat(timespec="minutes")
        changed = True
    if duration:
        data["duration_seconds"] = duration
        changed = True
    if not data.get("title") and not changed:
        title = re.sub(r"^(?:call it|name it|title it|the title is)\s+", "", t).strip()
        if title and len(title.split()) <= 12 and not looks_like_calendar_request(title):
            data["title"] = title.title()
            changed = True
    return changed


def _draft_question(data: dict) -> str:
    if not data.get("title"):
        return "What should I call the event?"
    if not data.get("date"):
        return f"What day should I schedule {data['title']}?"
    if not data.get("time"):
        return f"What time should I schedule {data['title']}?"
    return ""


def _duration_phrase(seconds: float) -> str:
    total = int(round(seconds))
    if total % 3600 == 0:
        count, unit = total // 3600, "hour"
    else:
        count, unit = max(1, total // 60), "minute"
    return f"{count} {unit}" + ("" if count == 1 else "s")


def _draft_datetimes(data: dict, tz: ZoneInfo) -> Tuple[datetime, datetime]:
    start = datetime.combine(
        date.fromisoformat(data["date"]),
        clock_time.fromisoformat(data["time"]),
        tzinfo=tz,
    )
    try:
        import app_config

        default_minutes = int(getattr(app_config, "CALENDAR_DEFAULT_EVENT_DURATION_MINUTES", 60))
    except Exception:
        default_minutes = 60
    duration = float(data.get("duration_seconds") or max(1, default_minutes) * 60)
    return start, start + timedelta(seconds=duration)


def _confirmation(data: dict, target: CalendarTarget, tz: ZoneInfo) -> str:
    start, end = _draft_datetimes(data, tz)
    when = start.strftime("%A, %B %-d at %-I:%M %p").replace(":00 ", " ")
    duration = _duration_phrase((end - start).total_seconds())
    return f"Add {data['title']} to {target.label} on {when} for {duration}?"


def _create_event(
    data: dict,
    target: CalendarTarget,
    *,
    tz: ZoneInfo,
    call_service: Callable[[str, dict], bool],
    mark_action: Callable[[], None],
) -> str:
    start, end = _draft_datetimes(data, tz)
    payload = {
        "entity_id": target.entity_id,
        "summary": data["title"],
        "start_date_time": start.isoformat(),
        "end_date_time": end.isoformat(),
    }
    if not call_service("calendar/create_event", payload):
        return "I couldn't add that event to the calendar."
    mark_action()
    _forget_draft(data)
    when = start.strftime("%A at %-I:%M %p").replace(":00 ", " ")
    return f"Added {data['title']} to {target.label} on {when}."


def handle_calendar_controls(
    *,
    tl: str,
    get_events: Callable[..., Optional[Dict[str, List[dict]]]],
    call_service: Callable[[str, dict], bool],
    mark_action: Callable[[], None],
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Handle calendar reads, event drafts, confirmations, and writes."""
    t = _norm(tl)
    tz = _local_timezone()
    now = (now or datetime.now(tz)).astimezone(tz)
    targets = _config_targets()
    draft = _current_draft()

    if draft:
        if _NO_RE.fullmatch(t):
            _forget_draft(draft)
            return "Okay, I didn't add it."
        if draft.get("status") == "confirming" and _YES_RE.fullmatch(t):
            target = targets.get(str(draft.get("calendar_key") or ""))
            if not target or not target.writable:
                _forget_draft(draft)
                return "That calendar is not configured for event creation."
            return _create_event(
                draft,
                target,
                tz=tz,
                call_service=call_service,
                mark_action=mark_action,
            )
        if draft.get("status") == "collecting" and _fill_draft_from_followup(draft, t, now=now):
            question = _draft_question(draft)
            if question:
                _save_draft(draft)
                return question
            draft["status"] = "confirming"
            _save_draft(draft)
            target = targets.get(str(draft.get("calendar_key") or ""))
            if not target:
                _forget_draft(draft)
                return "I don't have a default calendar configured."
            return _confirmation(draft, target, tz)

    creation = _parse_creation(t, targets, now)
    if creation is not None:
        try:
            import app_config

            writes_enabled = bool(getattr(app_config, "CALENDAR_WRITES_ENABLED", False))
            confirm_writes = bool(getattr(app_config, "CALENDAR_CONFIRM_WRITES", True))
        except Exception:
            writes_enabled, confirm_writes = False, True
        if not targets:
            return "Calendar isn't configured yet."
        if not writes_enabled:
            return "Calendar event creation is disabled in HomeSuite's configuration."
        target = targets.get(str(creation.get("calendar_key") or ""))
        if not target or not target.writable:
            return "That calendar is not configured for event creation."
        question = _draft_question(creation)
        if question:
            _save_draft(creation)
            return question
        if confirm_writes:
            creation["status"] = "confirming"
            _save_draft(creation)
            return _confirmation(creation, target, tz)
        return _create_event(
            creation,
            target,
            tz=tz,
            call_service=call_service,
            mark_action=mark_action,
        )

    query = parse_calendar_query(t, now=now)
    if query is None:
        return None
    try:
        import app_config

        reads_enabled = bool(getattr(app_config, "CALENDAR_READS_ENABLED", True))
    except Exception:
        reads_enabled = True
    if not reads_enabled:
        return "Calendar queries are disabled in HomeSuite's configuration."
    selected = _agenda_targets(t, targets)
    if not selected:
        return "Calendar isn't configured yet."
    return _answer_query(text=t, query=query, targets=selected, get_events=get_events)

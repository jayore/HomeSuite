"""Aggregate pending timers, alarms, reminders, schedules, and restorations."""

from __future__ import annotations

import re
import time
from typing import Callable, Iterable, Optional


_PENDING_QUERY_RE = re.compile(
    r"^(?:what(?:'s| is) pending|what do i have pending|"
    r"show(?: me)? (?:my )?pending (?:items|actions)|"
    r"list (?:my )?pending (?:items|actions))$"
)


def _norm(text: str) -> str:
    value = str(text or "").strip().lower().replace("’", "'")
    value = re.sub(r"[?!.]+$", "", value)
    return re.sub(r"\s+", " ", value).strip()


def looks_like_pending_query(text: str) -> bool:
    return bool(_PENDING_QUERY_RE.fullmatch(_norm(text)))


def _load_default_rows() -> tuple[list, list, list]:
    try:
        from alarm_controls import list_active_alarms

        alarms = list_active_alarms()
    except Exception:
        alarms = []
    try:
        from schedule_controls import list_pending_jobs

        schedules = list_pending_jobs()
    except Exception:
        schedules = []
    try:
        from temporary_actions import list_active_overrides

        temporary = list_active_overrides()
    except Exception:
        temporary = []
    return alarms, schedules, temporary


def _plural(count: int, singular: str, plural: Optional[str] = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _join_counts(parts: list[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return ", ".join(parts[:-1]) + f", and {parts[-1]}"


def _due_phrase(run_at: float, now_ts: float) -> str:
    try:
        from schedule_controls import _format_due_phrase

        return _format_due_phrase(run_at, now_ts=now_ts)
    except Exception:
        return "later"


def handle_pending_controls(
    text: str,
    *,
    alarm_rows: Optional[Iterable[dict]] = None,
    schedule_rows: Optional[Iterable[dict]] = None,
    temporary_rows: Optional[Iterable[dict]] = None,
    now_fn: Callable[[], float] = time.time,
) -> Optional[str]:
    """Answer a concise aggregate pending-work query."""
    if not looks_like_pending_query(text):
        return None
    if alarm_rows is None or schedule_rows is None or temporary_rows is None:
        defaults = _load_default_rows()
        if alarm_rows is None:
            alarm_rows = defaults[0]
        if schedule_rows is None:
            schedule_rows = defaults[1]
        if temporary_rows is None:
            temporary_rows = defaults[2]

    alarms = [row for row in alarm_rows or () if isinstance(row, dict)]
    schedules = [row for row in schedule_rows or () if isinstance(row, dict)]
    temporary = [row for row in temporary_rows or () if isinstance(row, dict)]
    counts = {
        "timer": sum(str(row.get("kind") or "") == "timer" for row in alarms),
        "alarm": sum(str(row.get("kind") or "") == "alarm" for row in alarms),
        "reminder": sum(str(row.get("kind") or "") == "reminder" for row in alarms),
        "schedule": len(schedules),
        "temporary": len(temporary),
    }
    if not any(counts.values()):
        return "You don't have anything pending."

    parts = []
    if counts["timer"]:
        parts.append(_plural(counts["timer"], "timer"))
    if counts["alarm"]:
        parts.append(_plural(counts["alarm"], "alarm"))
    if counts["reminder"]:
        parts.append(_plural(counts["reminder"], "reminder"))
    if counts["schedule"]:
        parts.append(_plural(counts["schedule"], "scheduled action"))
    if counts["temporary"]:
        parts.append(_plural(counts["temporary"], "temporary change"))

    now_ts = float(now_fn())
    candidates = []
    for row in alarms:
        try:
            run_at = float(row.get("_run_at_float", row.get("run_at")))
        except (TypeError, ValueError):
            continue
        kind = str(row.get("kind") or "alarm")
        label = str(row.get("label") or "").strip()
        if kind == "timer":
            description = f"your {label + ' ' if label else ''}timer"
        elif kind == "reminder":
            description = f"your reminder{f' to {label}' if label else ''}"
        else:
            description = f"your {label + ' ' if label else ''}alarm"
        candidates.append((run_at, description))
    for row in schedules:
        try:
            run_at = float(row.get("run_at"))
        except (TypeError, ValueError):
            continue
        candidates.append((run_at, str(row.get("command") or "scheduled action")))
    for row in temporary:
        try:
            run_at = float(row.get("_expires_at_float", row.get("expires_at")))
        except (TypeError, ValueError):
            continue
        label = str(row.get("label") or "light").strip()
        candidates.append((run_at, f"the {label} restoration"))

    summary = f"You have {_join_counts(parts)}."
    if candidates:
        run_at, description = min(candidates, key=lambda item: item[0])
        summary += f" Next is {description} {_due_phrase(run_at, now_ts)}."
    return summary

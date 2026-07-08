from __future__ import annotations

import ast
import os

try:
    from env_compat import install_homesuite_env_aliases
    install_homesuite_env_aliases()
except Exception:
    pass
import re
import sys
import time
import subprocess
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


BASE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class ParsedSchedule:
    command: str
    run_at: float
    phrase: str
    delay_seconds: Optional[float] = None


_NUM_WORDS = {
    "zero": 0,
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "to": 2,
    "too": 2,
    "three": 3,
    "four": 4,
    "for": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
}

_TENS_WORDS = {
    "twenty": 20,
    "thirty": 30,
    "forty": 40,
    "fifty": 50,
    "sixty": 60,
    "seventy": 70,
    "eighty": 80,
    "ninety": 90,
}

_REL_UNIT_RE = r"seconds?|secs?|minutes?|mins?|hours?|hrs?"
_NUM_RE = r"\d{1,4}|[a-z]+(?:[\s-]+[a-z]+)?"

# Tokens accepted in spoken clock phrases:
#   "twelve oh eight"
#   "twelve o eight"
#   "twelve zero eight"
#   "seven forty five"
#   "twelve thirty"
_TIME_TOKEN_RE = (
    r"(?:zero|oh|o|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
    r"eighteen|nineteen|twenty|thirty|forty|fifty|\d{1,2})"
)


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("’", "'").replace("‘", "'")
    s = re.sub(r"\bp\.?\s*m\.?\b", "pm", s)
    s = re.sub(r"\ba\.?\s*m\.?\b", "am", s)
    s = re.sub(r"[?!.]+$", "", s).strip()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _parse_small_number(s: str) -> Optional[int]:
    s = _norm(s).replace("-", " ")
    if not s:
        return None

    if s.isdigit():
        try:
            return int(s)
        except Exception:
            return None

    toks = [t for t in s.split() if t]
    if not toks:
        return None

    if len(toks) == 1:
        tok = toks[0]
        if tok.isdigit():
            return int(tok)
        if tok in _NUM_WORDS:
            return _NUM_WORDS[tok]
        if tok in _TENS_WORDS:
            return _TENS_WORDS[tok]
        return None

    if len(toks) == 2:
        a, b = toks
        if a in _TENS_WORDS and b in _NUM_WORDS:
            return _TENS_WORDS[a] + _NUM_WORDS[b]
        if a in _NUM_WORDS and b == "hundred":
            return _NUM_WORDS[a] * 100

    return None


def _clean_command(cmd: str) -> str:
    cmd = (cmd or "").strip()
    cmd = re.sub(r"^[,;:\-\s]+", "", cmd).strip()
    cmd = re.sub(r"[?!.]+$", "", cmd).strip()
    cmd = re.sub(r"\s+", " ", cmd).strip()
    cmd = re.sub(r"^(please|ok|okay)\s+", "", cmd, flags=re.I).strip()
    return cmd


def _is_probably_nested_schedule(cmd: str) -> bool:
    t = _norm(cmd)
    return bool(
        re.match(r"^(in\s+\S+|at\s+\d|tomorrow\s+at\s+\d)", t)
        or re.match(r"^(schedule|please schedule|remind me to|set a timer)", t)
        or re.search(rf"\s+in\s+({_NUM_RE})\s+({_REL_UNIT_RE})\b", t)
        or re.search(r"\s+(?:tomorrow\s+)?at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*$", t)
    )


def _build_relative(*, num_text: str, unit: str, command: str, now: datetime) -> Optional[ParsedSchedule]:
    n = _parse_small_number(num_text)
    if n is None or n <= 0:
        return None

    unit = (unit or "").lower()
    cmd = _clean_command(command)
    if not cmd:
        return None

    if unit.startswith(("sec", "second")):
        delay_seconds = float(n)
        phrase = f"in {n} second" + ("" if n == 1 else "s")
    elif unit.startswith(("min", "minute")):
        delay_seconds = float(n * 60)
        phrase = f"in {n} minute" + ("" if n == 1 else "s")
    elif unit.startswith(("hr", "hour")):
        delay_seconds = float(n * 3600)
        phrase = f"in {n} hour" + ("" if n == 1 else "s")
    else:
        return None

    return ParsedSchedule(
        command=cmd,
        run_at=(now + timedelta(seconds=delay_seconds)).timestamp(),
        phrase=phrase,
        delay_seconds=delay_seconds,
    )


def _parse_relative(t: str, now: datetime) -> Optional[ParsedSchedule]:
    # Time-first:
    #   in 5 seconds turn off holiday
    #   in twenty minutes, turn off holiday
    m = re.match(
        rf"^in\s+(?P<num>{_NUM_RE})\s+(?P<unit>{_REL_UNIT_RE})\s*,?\s+(?P<cmd>.+)$",
        t,
    )
    if m:
        return _build_relative(
            num_text=m.group("num"),
            unit=m.group("unit"),
            command=m.group("cmd"),
            now=now,
        )

    # Command-first:
    #   turn off holiday in 5 seconds
    #   schedule turn off holiday in five minutes
    m = re.match(
        rf"^(?P<cmd>.+?)\s+in\s+(?P<num>{_NUM_RE})\s+(?P<unit>{_REL_UNIT_RE})\s*$",
        t,
    )
    if m:
        return _build_relative(
            num_text=m.group("num"),
            unit=m.group("unit"),
            command=m.group("cmd"),
            now=now,
        )

    # Timer phrasing:
    #   set a timer for 5 minutes to turn off holiday
    #   timer for 5 minutes turn off holiday
    m = re.match(
        rf"^(?:set\s+)?(?:a\s+)?timer\s+(?:for\s+)?(?P<num>{_NUM_RE})\s+(?P<unit>{_REL_UNIT_RE})\s+(?:to\s+)?(?P<cmd>.+)$",
        t,
    )
    if m:
        return _build_relative(
            num_text=m.group("num"),
            unit=m.group("unit"),
            command=m.group("cmd"),
            now=now,
        )

    return None



def _parse_spoken_clock(clock_text: str) -> Optional[Tuple[int, int, bool]]:
    """
    Parse spoken clock text into (hour, minute, minute_was_explicit).

    Examples:
      "twelve oh eight" -> (12, 8, True)
      "twelve o eight"  -> (12, 8, True)
      "twelve zero eight" -> (12, 8, True)
      "twelve thirty" -> (12, 30, True)
      "seven forty five" -> (7, 45, True)
      "seven" -> (7, 0, False)

    This intentionally does NOT decide AM/PM. That is handled by
    _build_absolute(), including next-occurrence behavior for ambiguous
    12-hour spoken times.
    """
    s = _norm(clock_text)
    if not s:
        return None

    s = s.replace("-", " ")
    s = re.sub(r"\bo'?clock\b", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return None

    toks = s.split()
    if not toks:
        return None

    hour = _parse_small_number(toks[0])
    if hour is None:
        return None
    if hour < 1 or hour > 23:
        return None

    rest = toks[1:]
    if not rest:
        return (hour, 0, False)

    minute = None

    # "oh eight", "o eight", "zero eight"
    if rest[0] in ("oh", "o", "zero"):
        if len(rest) == 1:
            minute = 0
        else:
            unit = _parse_small_number(rest[1])
            if unit is None or unit < 0 or unit > 9:
                return None
            minute = unit

    # "thirty", "forty five", "twenty one"
    elif rest[0] in _TENS_WORDS:
        minute = _TENS_WORDS[rest[0]]
        if len(rest) >= 2:
            unit = _parse_small_number(rest[1])
            if unit is None or unit < 0 or unit > 9:
                return None
            minute += unit

    # "fifteen", "eight", "08", "45"
    else:
        if len(rest) == 1:
            val = _parse_small_number(rest[0])
            if val is None:
                return None
            minute = val
        elif len(rest) >= 2:
            # Digit-style phrase: "four five" -> 45
            a = _parse_small_number(rest[0])
            b = _parse_small_number(rest[1])
            if a is not None and b is not None and 0 <= a <= 9 and 0 <= b <= 9:
                minute = (a * 10) + b
            else:
                joined = _parse_small_number(" ".join(rest[:2]))
                if joined is None:
                    return None
                minute = joined

    if minute is None or minute < 0 or minute > 59:
        return None

    return (hour, int(minute), True)


def _build_absolute(
    *,
    hour_text: str,
    minute_text: Optional[str],
    ampm: Optional[str],
    tomorrow: bool,
    command: str,
    now: datetime,
    allow_ambiguous_12h: bool = False,
) -> Optional[ParsedSchedule]:
    hour = int(hour_text)
    minute = int(minute_text or 0)
    ampm = (ampm or "").lower()
    cmd = _clean_command(command)

    if not cmd:
        return None
    if minute < 0 or minute > 59:
        return None

    target = None

    # Explicit AM/PM.
    if ampm:
        if hour < 1 or hour > 12:
            return None
        if ampm == "am":
            hour24 = 0 if hour == 12 else hour
        else:
            hour24 = 12 if hour == 12 else hour + 12
        target = now.replace(hour=hour24, minute=minute, second=0, microsecond=0)

    # 24-hour numeric time.
    elif hour >= 13 and hour <= 23:
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    # Spoken time with explicit minutes but no AM/PM:
    #   "twelve oh eight"
    #   "seven thirty"
    #
    # Pick the next occurrence of that 12-hour clock time, considering both
    # AM and PM. This avoids "twelve oh eight" at 11:50 PM becoming noon
    # tomorrow instead of 12:08 AM.
    elif allow_ambiguous_12h and 1 <= hour <= 12 and minute_text is not None:
        h_am = 0 if hour == 12 else hour
        h_pm = 12 if hour == 12 else hour + 12

        base = now + timedelta(days=1) if tomorrow else now

        candidates = [
            base.replace(hour=h_am, minute=minute, second=0, microsecond=0),
            base.replace(hour=h_pm, minute=minute, second=0, microsecond=0),
        ]

        if tomorrow:
            # If "tomorrow" was explicit, keep candidates on tomorrow's date
            # and pick the earlier clock occurrence that day.
            target = min(candidates, key=lambda dt: dt.timestamp())
        else:
            future = []
            for dt in candidates:
                if dt.timestamp() <= now.timestamp():
                    dt = dt + timedelta(days=1)
                future.append(dt)
            target = min(future, key=lambda dt: dt.timestamp())

    # Conservative fallback:
    # bare "at 10" is rejected as ambiguous.
    else:
        return None

    if target is None:
        return None

    if tomorrow and target.date() == now.date():
        target = target + timedelta(days=1)
    elif not tomorrow and target.timestamp() <= now.timestamp():
        target = target + timedelta(days=1)

    # Confirmation for absolute-time requests should preserve the user's
    # requested style ("at 12:31 PM") instead of converting near-term times
    # into relative phrasing ("in 31 seconds"). Relative phrasing is still used
    # for list/query output.
    try:
        clock = target.strftime("%-I:%M %p").replace(":00", "")
    except Exception:
        clock = target.strftime("%I:%M %p").lstrip("0").replace(":00", "")

    day_part = "tomorrow " if target.date() != now.date() else ""

    return ParsedSchedule(
        command=cmd,
        run_at=target.timestamp(),
        phrase=f"{day_part}at {clock}",
        delay_seconds=None,
    )


def _parse_absolute(t: str, now: datetime) -> Optional[ParsedSchedule]:
    # ------------------------------------------------------------------
    # Numeric time-first:
    #   at 10 pm turn off holiday
    #   at 10:30 pm turn off holiday
    #   tomorrow at 7 am turn on holiday
    # ------------------------------------------------------------------
    m = re.match(
        r"^(?P<tomorrow>tomorrow\s+)?at\s+"
        r"(?P<hour>\d{1,2})"
        r"(?::(?P<minute>\d{2}))?"
        r"\s*(?P<ampm>am|pm)?\s*,?\s+"
        r"(?P<cmd>.+)$",
        t,
    )
    if m:
        # If user gives explicit numeric minutes but omits AM/PM
        # ("at 12:05 ...", "at 7:45 ..."), treat it like a spoken
        # 12-hour clock and pick the next occurrence. Bare "at 7"
        # remains rejected as ambiguous.
        allow_ambiguous = bool(m.group("minute") and not m.group("ampm"))
        return _build_absolute(
            hour_text=m.group("hour"),
            minute_text=m.group("minute"),
            ampm=m.group("ampm"),
            tomorrow=bool(m.group("tomorrow")),
            command=m.group("cmd"),
            now=now,
            allow_ambiguous_12h=allow_ambiguous,
        )

    # ------------------------------------------------------------------
    # Numeric command-first:
    #   turn off holiday at 10 pm
    #   turn off holiday tomorrow at 7 am
    # ------------------------------------------------------------------
    m = re.match(
        r"^(?P<cmd>.+?)\s+"
        r"(?P<tomorrow>tomorrow\s+)?at\s+"
        r"(?P<hour>\d{1,2})"
        r"(?::(?P<minute>\d{2}))?"
        r"\s*(?P<ampm>am|pm)?\s*$",
        t,
    )
    if m:
        # If user gives explicit numeric minutes but omits AM/PM
        # ("at 12:05 ...", "at 7:45 ..."), treat it like a spoken
        # 12-hour clock and pick the next occurrence. Bare "at 7"
        # remains rejected as ambiguous.
        allow_ambiguous = bool(m.group("minute") and not m.group("ampm"))
        return _build_absolute(
            hour_text=m.group("hour"),
            minute_text=m.group("minute"),
            ampm=m.group("ampm"),
            tomorrow=bool(m.group("tomorrow")),
            command=m.group("cmd"),
            now=now,
            allow_ambiguous_12h=allow_ambiguous,
        )

    # ------------------------------------------------------------------
    # Spoken time-first:
    #   at twelve oh eight turn off holiday
    #   at twelve o eight pm turn off holiday
    #   tomorrow at seven forty five am turn on holiday
    # ------------------------------------------------------------------
    spoken_clock_re = rf"(?P<clock>{_TIME_TOKEN_RE}(?:\s+{_TIME_TOKEN_RE}){{0,3}})"

    m = re.match(
        rf"^(?P<tomorrow>tomorrow\s+)?at\s+"
        rf"{spoken_clock_re}"
        r"\s*(?P<ampm>am|pm)?\s*,?\s+"
        r"(?P<cmd>.+)$",
        t,
    )
    if m:
        parsed_clock = _parse_spoken_clock(m.group("clock"))
        if parsed_clock:
            hour, minute, minute_explicit = parsed_clock
            return _build_absolute(
                hour_text=str(hour),
                minute_text=f"{minute:02d}" if minute_explicit else None,
                ampm=m.group("ampm"),
                tomorrow=bool(m.group("tomorrow")),
                command=m.group("cmd"),
                now=now,
                allow_ambiguous_12h=minute_explicit,
            )

    # ------------------------------------------------------------------
    # Spoken command-first:
    #   turn off holiday at twelve oh eight
    #   turn off holiday at twelve o eight pm
    #   turn on holiday tomorrow at seven forty five am
    # ------------------------------------------------------------------
    m = re.match(
        rf"^(?P<cmd>.+?)\s+"
        rf"(?P<tomorrow>tomorrow\s+)?at\s+"
        rf"{spoken_clock_re}"
        r"\s*(?P<ampm>am|pm)?\s*$",
        t,
    )
    if m:
        parsed_clock = _parse_spoken_clock(m.group("clock"))
        if parsed_clock:
            hour, minute, minute_explicit = parsed_clock
            return _build_absolute(
                hour_text=str(hour),
                minute_text=f"{minute:02d}" if minute_explicit else None,
                ampm=m.group("ampm"),
                tomorrow=bool(m.group("tomorrow")),
                command=m.group("cmd"),
                now=now,
                allow_ambiguous_12h=minute_explicit,
            )

    return None


def parse_schedule_request(text: str, *, now: Optional[datetime] = None) -> Optional[ParsedSchedule]:
    t = _norm(text)
    if not t:
        return None

    if now is None:
        now = datetime.now().astimezone()

    t = re.sub(r"^(please\s+)?schedule\s+", "", t).strip()
    t = re.sub(r"^(please\s+)?set\s+up\s+a\s+schedule\s+to\s+", "", t).strip()

    parsed = _parse_relative(t, now)
    if parsed:
        return parsed

    parsed = _parse_absolute(t, now)
    if parsed:
        return parsed

    return None


def looks_like_schedule_attempt(text: str) -> bool:
    """
    Detect utterances that appear intended as schedules even if parsing fails.

    This is a safety guard. If this returns True and parsing failed, we return a
    scheduling error instead of letting immediate-action handlers execute.
    """
    t = _norm(text)
    if not t:
        return False

    if re.match(r"^(please\s+)?schedule\b", t):
        return True

    if re.match(r"^(please\s+)?(?:set\s+)?(?:a\s+)?timer\b", t):
        return True

    if re.match(rf"^in\s+({_NUM_RE})\s+({_REL_UNIT_RE})\b", t):
        return True

    if re.match(r"^(tomorrow\s+)?at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\b", t):
        return True

    if re.search(rf"\s+in\s+({_NUM_RE})\s+({_REL_UNIT_RE})\s*$", t):
        return True

    if re.search(r"\s+(?:tomorrow\s+)?at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*$", t):
        return True

    return False


def _should_persist_schedule() -> bool:
    """
    Return False in dry-run/capture/test mode.

    Production homesuite.service does not necessarily set PIPHONE_LIVE, so we
    cannot require PIPHONE_LIVE globally. But command_runtime capture/test set
    identifiable env flags.
    """
    if os.environ.get("PIPHONE_TEST_MODE") == "1":
        return False

    if os.environ.get("PIPHONE_COMMAND_RUNTIME") == "1" and os.environ.get("PIPHONE_LIVE") != "1":
        return False

    return True


def _load_policy() -> Dict[str, Any]:
    def _get(name: str, default):
        try:
            import app_config
            return getattr(app_config, name, default)
        except Exception:
            return default

    return {
        "blocked_services": _get("SCHEDULER_BLOCKED_SERVICES", []),
        "blocked_service_prefixes": _get("SCHEDULER_BLOCKED_SERVICE_PREFIXES", []),
        "blocked_entity_prefixes": _get("SCHEDULER_BLOCKED_ENTITY_PREFIXES", []),
        "blocked_command_regexes": _get("SCHEDULER_BLOCKED_COMMAND_REGEXES", []),
        "legacy_blocklist": _get("SCHEDULER_BLOCKLIST", []),
        "legacy_command_blocklist": _get("SCHEDULER_COMMAND_BLOCKLIST", []),
    }


def _as_list(x) -> List[str]:
    if isinstance(x, (list, tuple, set)):
        return [str(v).strip() for v in x if str(v).strip()]
    return []


def _extract_entity_ids(data: Any) -> List[str]:
    if not isinstance(data, dict):
        return []

    out = []

    def add(v):
        if isinstance(v, str) and "." in v:
            out.append(v.strip())
        elif isinstance(v, (list, tuple, set)):
            for item in v:
                add(item)

    add(data.get("entity_id"))
    add(data.get("entity_ids"))

    target = data.get("target")
    if isinstance(target, dict):
        add(target.get("entity_id"))

    # Deduplicate preserving order.
    seen = set()
    deduped = []
    for eid in out:
        if eid not in seen:
            seen.add(eid)
            deduped.append(eid)
    return deduped


def _policy_block_reason(command: str, writes: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    policy = _load_policy()
    cmd_l = _norm(command)

    # Legacy simple substring blocklists.
    for item in _as_list(policy.get("legacy_blocklist")) + _as_list(policy.get("legacy_command_blocklist")):
        item_l = item.lower()
        if item_l and item_l in cmd_l:
            return f"blocked by scheduler policy: {item}"

    # Regex command blocklist.
    for pat in _as_list(policy.get("blocked_command_regexes")):
        try:
            if re.search(pat, cmd_l, flags=re.I):
                return f"blocked by scheduler command policy"
        except Exception:
            logging.exception("Invalid scheduler block regex: %r", pat)

    blocked_services = {x.lower() for x in _as_list(policy.get("blocked_services"))}
    service_prefixes = [x.lower() for x in _as_list(policy.get("blocked_service_prefixes"))]
    entity_prefixes = [x.lower() for x in _as_list(policy.get("blocked_entity_prefixes"))]

    for w in writes or []:
        if not isinstance(w, dict):
            continue

        svc = str(w.get("service") or "").strip().lower()
        data = w.get("data") if isinstance(w.get("data"), dict) else {}

        if svc and svc in blocked_services:
            return f"blocked service: {svc}"

        for pref in service_prefixes:
            if pref and svc.startswith(pref):
                return f"blocked service prefix: {pref}"

        for eid in _extract_entity_ids(data):
            eid_l = eid.lower()
            for pref in entity_prefixes:
                if pref and eid_l.startswith(pref):
                    return f"blocked entity prefix: {pref}"

    return None


def _parse_blocked_writes_from_output(out: str) -> List[Dict[str, Any]]:
    """
    Parse command_runtime capture output lines.

    Supported formats:

      Legacy:
        HA_BLOCKED_WRITE args=('light/turn_off', {'entity_id': 'light.holiday'}) kwargs={}

      Current:
        HA_STUB call: light/turn_off {'entity_id': 'light.holiday'}
    """
    writes: List[Dict[str, Any]] = []
    for line in (out or "").splitlines():
        try:
            # Legacy machine-readable format
            if "HA_BLOCKED_WRITE" in line and "args=" in line:
                m = re.search(r"args=(\(.+?\))\s+kwargs=", line)
                if not m:
                    continue
                args = ast.literal_eval(m.group(1))
                if isinstance(args, tuple) and len(args) >= 1:
                    service = args[0]
                    data = args[1] if len(args) >= 2 and isinstance(args[1], dict) else {}
                    if isinstance(service, str):
                        writes.append({"service": service, "data": data})
                continue

            # Current human-friendly stub format
            if "HA_STUB call:" in line:
                m = re.search(r"HA_STUB call:\s+([^\s]+)\s+(\{.*\})\s*$", line)
                if not m:
                    continue
                service = m.group(1).strip()
                data_txt = m.group(2).strip()
                data = ast.literal_eval(data_txt)
                if isinstance(service, str) and isinstance(data, dict):
                    writes.append({"service": service, "data": data})
                continue

        except Exception:
            continue
    return writes


def validate_scheduled_command(command: str, *, timeout_s: float = 20.0) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Validate by running the existing command brain in capture mode.

    This intentionally does not duplicate routing logic. We accept the command
    if capture mode appears to claim it or would have attempted a blocked HA
    write.
    """
    command = _clean_command(command)
    if not command:
        return False, "empty command", {}

    if _is_probably_nested_schedule(command):
        return False, "nested scheduling is not supported", {}

    pre_reason = _policy_block_reason(command, writes=[])
    if pre_reason:
        return False, pre_reason, {}

    cmd = [sys.executable, str(BASE_DIR / "command_runtime.py"), "--capture", command]

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            text=True,
            capture_output=True,
            timeout=float(timeout_s),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "validation timed out", {}
    except Exception as e:
        return False, f"validation failed: {e}", {}

    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    writes = _parse_blocked_writes_from_output(out)
    metadata = {
        "validator": "subprocess",
        "writes": writes,
    }

    if proc.returncode != 0:
        return False, f"validation returned {proc.returncode}", metadata

    policy_reason = _policy_block_reason(command, writes=writes)
    if policy_reason:
        return False, policy_reason, metadata

    positive_markers = (
        "HA_BLOCKED_WRITE",
        "CLAIM:",
        "Plex TEST MODE: would play",
    )
    if any(marker in out for marker in positive_markers):
        return True, "validated", metadata

    return False, "command was not claimed by the command runtime", metadata


def _pending_jobs() -> List[Dict[str, Any]]:
    try:
        import scheduler
        rows = scheduler.list_jobs()
    except Exception:
        logging.exception("SCHED_LIST_FAIL")
        return []

    now = time.time()
    pending = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        if row.get("status") != "pending":
            continue
        try:
            run_at = float(row.get("run_at"))
        except Exception:
            continue
        # Include overdue-but-pending jobs because scheduler may be temporarily offline.
        pending.append(row)

    pending.sort(key=lambda r: float(r.get("run_at") or now))
    return pending


def _format_due_phrase(run_at: float, *, now_ts: Optional[float] = None) -> str:
    if now_ts is None:
        now_ts = time.time()

    try:
        run_at = float(run_at)
    except Exception:
        return "later"

    delta = run_at - float(now_ts)
    if delta < 0:
        delta = 0

    if delta < 90:
        n = max(1, int(round(delta)))
        return f"in {n} second" + ("" if n == 1 else "s")

    if delta < 90 * 60:
        n = max(1, int(round(delta / 60.0)))
        return f"in {n} minute" + ("" if n == 1 else "s")

    now_dt = datetime.fromtimestamp(now_ts).astimezone()
    run_dt = datetime.fromtimestamp(run_at).astimezone()

    try:
        clock = run_dt.strftime("%-I:%M %p").replace(":00", "")
    except Exception:
        clock = run_dt.strftime("%I:%M %p").lstrip("0").replace(":00", "")

    if run_dt.date() == now_dt.date():
        return f"at {clock}"

    if run_dt.date() == (now_dt + timedelta(days=1)).date():
        return f"tomorrow at {clock}"

    try:
        return run_dt.strftime("on %b %-d at %-I:%M %p").replace(":00", "")
    except Exception:
        return run_dt.strftime("on %b %d at %I:%M %p").replace(" 0", " ")


def _job_phrase(job: Dict[str, Any]) -> str:
    cmd = str(job.get("command") or "scheduled action").strip()
    try:
        run_at = float(job.get("run_at"))
    except Exception:
        return cmd
    return f"{cmd} {_format_due_phrase(run_at)}"


def _looks_like_list_request(t: str) -> bool:
    return bool(
        re.search(r"\b(what'?s|what is|what do i have|list|show|tell me)\b.*\b(scheduled|schedule|schedules|timers|timer)\b", t)
        or re.fullmatch(r"(what'?s scheduled|what is scheduled|scheduled actions|list schedules|list scheduled actions|list timers)", t)
    )


def _looks_like_cancel_all(t: str) -> bool:
    return bool(
        re.search(r"\b(cancel|clear|delete|remove)\b.*\b(all|everything)\b.*\b(schedules?|scheduled actions?|timers?)\b", t)
        or re.fullmatch(r"(cancel all schedules|clear schedules|clear all schedules|cancel all timers|clear all timers)", t)
    )


def _looks_like_cancel_latest(t: str) -> bool:
    return bool(
        re.fullmatch(r"(cancel that|cancel it|cancel the last one|cancel last one|cancel the last schedule|cancel last schedule|cancel my scheduled action|cancel scheduled action|cancel schedule|cancel timer|cancel the timer)", t)
    )


def _handle_list_request() -> str:
    jobs = _pending_jobs()
    if not jobs:
        return "You don't have any scheduled actions."

    if len(jobs) == 1:
        return f"You have one scheduled action: {_job_phrase(jobs[0])}."

    parts = [_job_phrase(j) for j in jobs[:3]]
    if len(jobs) > 3:
        return f"You have {len(jobs)} scheduled actions. The next three are: " + "; ".join(parts) + "."
    return f"You have {len(jobs)} scheduled actions: " + "; ".join(parts) + "."


def _handle_cancel_all() -> str:
    jobs = _pending_jobs()
    if not jobs:
        return "You don't have any scheduled actions to cancel."

    try:
        import scheduler
        scheduler.cancel_all()
    except Exception:
        logging.exception("SCHED_CANCEL_ALL_FAIL")
        return "I couldn't cancel your scheduled actions."

    n = len(jobs)
    if n == 1:
        return "Canceled your scheduled action."
    return f"Canceled {n} scheduled actions."


def _handle_cancel_latest() -> str:
    jobs = _pending_jobs()
    if not jobs:
        return "You don't have any scheduled actions to cancel."

    # Most natural "cancel that" behavior: cancel the most recently created pending job.
    def created_at(j):
        try:
            return float(j.get("created_at") or 0)
        except Exception:
            return 0.0

    job = sorted(jobs, key=created_at, reverse=True)[0]
    jid = str(job.get("id") or "")
    if not jid:
        return "I couldn't find that scheduled action."

    try:
        import scheduler
        ok = scheduler.cancel_job(jid)
    except Exception:
        logging.exception("SCHED_CANCEL_LATEST_FAIL id=%r", jid)
        ok = False

    if not ok:
        return "I couldn't cancel that scheduled action."

    return f"Canceled the scheduled action: {str(job.get('command') or 'that').strip()}."


def handle_schedule_controls(
    *,
    tl: str,
    maybe_say=None,
    validate_command=None,
) -> Optional[str]:
    """
    Handle natural-language scheduled command requests.

    Returns:
      - None: not a schedule request
      - str: handled response, including validation/scheduling failures

    Important: schedule_controls must run before immediate action handlers.
    """
    t = _norm(tl)

    # Query / cancel UX first.
    if _looks_like_list_request(t):
        logging.info("CLAIM: schedule_controls list")
        return _handle_list_request()

    if _looks_like_cancel_all(t):
        logging.info("CLAIM: schedule_controls cancel_all")
        return _handle_cancel_all()

    if _looks_like_cancel_latest(t):
        logging.info("CLAIM: schedule_controls cancel_latest")
        return _handle_cancel_latest()

    parsed = parse_schedule_request(tl)
    if not parsed:
        if looks_like_schedule_attempt(tl):
            logging.info("SCHED_PARSE_FAIL_SAFETY text=%r", tl)
            return "I heard a schedule request, but I couldn't understand when to run it."
        return None

    command = _clean_command(parsed.command)
    logging.info("SCHED_INTENT command=%r when=%s phrase=%r", command, parsed.run_at, parsed.phrase)

    if validate_command is not None:
        try:
            result = validate_command(command)
            if isinstance(result, (tuple, list)) and len(result) >= 3:
                ok, reason, metadata = bool(result[0]), str(result[1]), (result[2] if isinstance(result[2], dict) else {})
            elif isinstance(result, (tuple, list)) and len(result) >= 2:
                ok, reason, metadata = bool(result[0]), str(result[1]), {}
            else:
                ok, reason, metadata = bool(result), "validated" if result else "validation failed", {}
        except Exception as e:
            logging.exception("SCHED_VALIDATE_CALLBACK_FAIL command=%r", command)
            ok, reason, metadata = False, f"validation callback failed: {e}", {}
    else:
        ok, reason, metadata = validate_scheduled_command(command)

    writes = []
    try:
        writes = metadata.get("writes") if isinstance(metadata, dict) else []
        if not isinstance(writes, list):
            writes = []
    except Exception:
        writes = []

    policy_reason = _policy_block_reason(command, writes=writes)
    if policy_reason:
        ok = False
        reason = policy_reason

    if not ok:
        logging.info("SCHED_VALIDATE_FAIL command=%r reason=%r metadata=%r", command, reason, metadata)
        if "blocked" in (reason or "").lower():
            return "I can't schedule that action because it's blocked by your scheduler safety settings."
        return "I couldn't schedule that because I couldn't validate the command."

    # Validation can take several seconds on the Pi. For relative schedules,
    # honor the user's delay relative to *now after validation*, not the earlier
    # parse timestamp. This prevents "in 5 seconds" from becoming immediately
    # overdue because validation took several seconds.
    run_at = float(parsed.run_at)
    if parsed.delay_seconds is not None:
        run_at = time.time() + float(parsed.delay_seconds)

    if not _should_persist_schedule():
        logging.info("SCHED_DRY_RUN command=%r run_at=%s metadata=%r", command, run_at, metadata)
        return f"Would schedule {command} {parsed.phrase}."

    try:
        import scheduler
        job = scheduler.schedule_command(
            command,
            run_at,
            metadata={
                "validated_at": time.time(),
                "validation": metadata if isinstance(metadata, dict) else {},
            },
        )
    except Exception:
        logging.exception("SCHED_ADD_FAIL command=%r run_at=%s", command, run_at)
        return "I couldn't save that schedule."

    jid = ""
    try:
        jid = str((job or {}).get("id") or "")
    except Exception:
        jid = ""

    # For creation confirmation, preserve the style of the user's request:
    # - relative request -> "in 20 seconds"
    # - absolute request -> "at 12:31 PM"
    # List/query responses still use _format_due_phrase(run_at) so they can
    # say useful things like "in 8 minutes" for pending jobs.
    due_phrase = parsed.phrase or _format_due_phrase(run_at)
    logging.info("CLAIM: schedule_controls id=%s command=%r phrase=%r metadata=%r", jid, command, due_phrase, metadata)
    return f"Okay, I'll {command} {due_phrase}."

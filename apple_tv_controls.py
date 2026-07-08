import re
import time
from typing import Optional

# Apple TV nav repeat debounce between remote.send_command calls
NAV_REPEAT_SLEEP_SECONDS = 0.0


def _current_media_position(attrs: dict) -> float:
    """
    Return the best estimate of the current playback position in seconds.
    HA's media_position is stale (captured at last poll); media_position_updated_at
    tells us when. We add elapsed real time to get the actual current position.
    """
    pos = attrs.get("media_position")
    if pos is None:
        return 0.0
    current = float(pos)
    try:
        pos_updated_at = attrs.get("media_position_updated_at")
        if pos_updated_at and isinstance(pos_updated_at, str):
            from datetime import datetime, timezone
            ts_str = pos_updated_at.replace("Z", "+00:00")
            updated_dt = datetime.fromisoformat(ts_str)
            if updated_dt.tzinfo is None:
                updated_dt = updated_dt.replace(tzinfo=timezone.utc)
            elapsed = time.time() - updated_dt.timestamp()
            if 0 < elapsed < 7200:  # sanity-check: ignore if stale >2h
                current += elapsed
    except Exception:
        pass
    return current


def handle_apple_tv_controls(
    tl: str,
    *,
    states_snapshot,
    call_ha_service,
    maybe_say,
    entity_id: str,
    remote_entity_id: str,
    default_skip_seconds: int,
    get_fresh_state=None,
) -> Optional[str]:
    """
    Handles Apple TV seek / transport commands.

    Returns:
      - None → not an Apple TV command
      - ""   → handled silently
      - str  → handled and speak confirmation
    """
    t = (tl or "").strip().lower()
    # Guard: 'swap/unswap' are Sonos move/swap intents; do not steal them as Apple TV 'back'.
    if re.match(r'^(swap|unswap)\b', t):
        return None

    if not t:
        return None

    def _get_entity_attrs() -> dict:
        """Return the freshest available attributes for the ATV entity."""
        if get_fresh_state and entity_id:
            try:
                fresh = get_fresh_state(entity_id)
                if fresh:
                    return fresh.get("attributes") or {}
            except Exception:
                pass
        st = next(
            (s for s in (states_snapshot or []) if s.get("entity_id") == entity_id),
            None,
        )
        return (st.get("attributes") or {}) if st else {}

    # -------------------------------------------------
    # Absolute seeks FIRST (so "go back to the beginning" doesn't get stolen by "back")
    # -------------------------------------------------
    if re.search(r"\b(start over|restart|start from beginning|start from the beginning|from beginning|from the beginning|skip to beginning|go to beginning|go back to the beginning|back to the beginning)\b", t):
        ok = call_ha_service(
            "media_player/media_seek",
            {"entity_id": entity_id, "seek_position": 0},
        )
        return maybe_say("Starting over.") if ok else ""

    if re.search(r"\b(skip to end|go to end)\b", t):
        attrs = _get_entity_attrs()
        dur = attrs.get("media_duration")
        if dur is None:
            return ""
        try:
            target = max(0, int(dur) - 1)
        except Exception:
            return ""
        ok = call_ha_service(
            "media_player/media_seek",
            {"entity_id": entity_id, "seek_position": target},
        )
        return maybe_say("Skipping to the end.") if ok else ""

    # -------------------------------------------------
    # Absolute seek to timestamp: "skip to 30 minutes", "go to 1 hour 30 minutes in"
    # Handles word form (most reliable with Whisper) and colon notation (bonus).
    # Must run BEFORE relative-seek so "skip to 30 minutes" doesn't match bare "skip".
    # -------------------------------------------------
    m_abs_kw = re.search(r"\b(?:skip\s+to|go\s+to|jump\s+to|seek\s+to)\b", t)
    if m_abs_kw:
        tail = t[m_abs_kw.end():]

        # Try colon-format first: "1:30" or "1:30:45" (Whisper sometimes produces this)
        m_colon = re.search(r"\b(?P<h>\d+):(?P<m>\d{2})(?::(?P<s>\d{2}))?\b", tail)
        if m_colon:
            h = int(m_colon.group("h"))
            m_ = int(m_colon.group("m"))
            s_ = int(m_colon.group("s") or 0)
            abs_seconds = h * 3600 + m_ * 60 + s_
        else:
            # Word form: optional hours, optional minutes, optional seconds
            mh = re.search(r"\b(?P<h>\d+)\s*(?:hours?|hrs?)", tail)
            mm = re.search(r"\b(?P<m>\d+)\s*(?:minutes?|mins?)", tail)
            ms = re.search(r"\b(?P<s>\d+)\s*(?:seconds?|secs?)", tail)
            if not (mh or mm or ms):
                m_abs_kw = None  # no recognizable time — fall through
            else:
                h = int(mh.group("h")) if mh else 0
                m_ = int(mm.group("m")) if mm else 0
                s_ = int(ms.group("s")) if ms else 0
                abs_seconds = h * 3600 + m_ * 60 + s_

        if m_abs_kw:  # still set means we parsed a time
            attrs = _get_entity_attrs()
            dur = attrs.get("media_duration")
            if dur is None:
                return ""
            try:
                target = max(0, min(abs_seconds, int(float(dur) - 1)))
            except Exception:
                return ""
            ok = call_ha_service(
                "media_player/media_seek",
                {"entity_id": entity_id, "seek_position": target},
            )
            if ok:
                if h and m_:
                    label = f"{h} hour{'s' if h != 1 else ''} {m_} minute{'s' if m_ != 1 else ''}"
                elif h:
                    label = f"{h} hour{'s' if h != 1 else ''}"
                elif m_:
                    label = f"{m_} minute{'s' if m_ != 1 else ''}"
                else:
                    label = f"{s_} second{'s' if s_ != 1 else ''}"
                return maybe_say(f"Jumping to {label} in.")
            return ""

    # -------------------------------------------------
    # Bare remote-button commands (select / menu / home / top menu).
    # Must run BEFORE relative-seek regex so 'menu' doesn't get parsed
    # as something else. Maps directly to remote.send_command.
    # -------------------------------------------------
    _REMOTE_BUTTONS = {
        "select":   "select",
        "menu":     "menu",
        "home":     "home",
        "top menu": "top_menu",
    }
    if t in _REMOTE_BUTTONS:
        if not remote_entity_id:
            return ""
        cmd = _REMOTE_BUTTONS[t]
        ok = call_ha_service(
            "remote/send_command",
            {"entity_id": remote_entity_id, "command": cmd},
        )
        return "" if ok else ""

    # -------------------------------------------------
    # Bare "skip" → skip forward by default amount
    # -------------------------------------------------
    if re.match(r"^skip\s*$", t):
        attrs = _get_entity_attrs()
        pos = attrs.get("media_position")
        dur = attrs.get("media_duration")
        if pos is not None and dur is not None:
            try:
                current_pos = _current_media_position(attrs)
                target = max(0, min(int(current_pos + default_skip_seconds), int(float(dur) - 1)))
                ok = call_ha_service(
                    "media_player/media_seek",
                    {"entity_id": entity_id, "seek_position": target},
                )
                return maybe_say(f"Skipping forward {default_skip_seconds} seconds.") if ok else ""
            except Exception:
                pass
        return ""

    # -------------------------------------------------
    # Relative seeks: "rewind 5 seconds", "skip back 5 minutes", "skip forward 2 minutes"
    # -------------------------------------------------
    m_seek = re.search(
        r"\b(?P<dir>rewind|skip\s+back|skip\s+forward|fast\s+forward|forward|back)\b"
        r"(?:\s+(?P<amt>an?|\d{1,3}))?"
        r"(?:\s+(?P<unit>hours?|hrs?|minutes?|mins?|seconds?|secs?))?\b",
        t,
    )
    if m_seek:
        direction = (m_seek.group("dir") or "").strip()
        amt_raw = m_seek.group("amt")
        unit = (m_seek.group("unit") or "seconds").lower()

        # "an" / "a" -> 1; digit string -> int; absent -> default
        if amt_raw is None:
            amount = int(default_skip_seconds)
        elif amt_raw in ("a", "an"):
            amount = 1
        else:
            try:
                amount = int(amt_raw)
            except Exception:
                amount = int(default_skip_seconds)

        if unit.startswith(("hour", "hr")):
            seconds = amount * 3600
        elif unit.startswith(("min", "minute")):
            seconds = amount * 60
        else:
            seconds = amount

        if ("rewind" in direction) or ("back" in direction):
            seconds = -seconds

        attrs = _get_entity_attrs()
        pos = attrs.get("media_position")
        dur = attrs.get("media_duration")
        if pos is None or dur is None:
            return ""

        current_pos = _current_media_position(attrs)

        try:
            target = max(0, min(int(current_pos + seconds), int(float(dur) - 1)))
        except Exception:
            return ""

        ok = call_ha_service(
            "media_player/media_seek",
            {"entity_id": entity_id, "seek_position": target},
        )

        if ok:
            abs_s = abs(int(seconds))
            if abs_s >= 3600 and abs_s % 3600 == 0:
                label = f"{abs_s // 3600} hour{'s' if abs_s // 3600 != 1 else ''}"
            elif abs_s >= 60 and abs_s % 60 == 0:
                label = f"{abs_s // 60} minute{'s' if abs_s // 60 != 1 else ''}"
            else:
                label = f"{abs_s} second{'s' if abs_s != 1 else ''}"
            spoken = ("Rewinding" if seconds < 0 else "Skipping forward") + f" {label}."
            return maybe_say(spoken)
        return ""

    # -------------------------------------------------
    # Directional navigation (strict): "right 2", "down three", "go left 4"
    # Uses HA remote.send_command so we can repeat N times.
    # -------------------------------------------------
    def _parse_count(tok: str) -> int:
        tok = (tok or "").strip().lower()
        if not tok:
            return 1
        if tok.isdigit():
            try:
                return int(tok)
            except Exception:
                return 1
        words = {
            "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
            "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
            "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
            "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
        }
        return int(words.get(tok, 1))

    # Only match pure nav utterances so we don't steal normal speech.
    m_nav = re.match(
        r"^(?:(?:go|move|press)\s+)?(?P<dir>left|right|up|down)"
        r"(?:\s+(?P<n>(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty))"
        r"(?:\s*(?:x|times?))?)?\s*$",
        t,
    )
    if m_nav:
        direction = m_nav.group("dir")
        n_tok = m_nav.group("n") or ""
        n = _parse_count(n_tok)
        if n < 1:
            n = 1
        if n > 20:
            n = 20
        if not remote_entity_id:
            return ""
        ok_any = False
        for _i in range(n):
            ok = call_ha_service(
                "remote/send_command",
                {"entity_id": remote_entity_id, "command": direction},
            )
            ok_any = ok_any or bool(ok)
            time.sleep(NAV_REPEAT_SLEEP_SECONDS)
        return "" if ok_any else ""

    return None

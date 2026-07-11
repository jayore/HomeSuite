"""Create, persist, list, cancel, and fire spoken alarms and timers.

Alarm rows retain their parsed due time, output target, optional spoken label,
and optional attached HomeSuite command. The shared scheduler invokes
``_fire_alarm`` by ID; firing then chooses local audio or the resolved Sonos
room and executes an attached command only through the command executor
installed by the main runtime.

``handle_alarm_controls`` owns alarm/timer language only. It returns ``None``
when text is not an alarm intent so general scheduled-command handling can try
next. Persistent state is protected by this module's load/update helpers rather
than edited by callers.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List

from request_context import get_active_room_for_request_defaults
from home_registry import get_default_room_id, resolve_room_id
from spoken_text import normalize_for_tts, tokenize_for_gtts

BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = BASE_DIR / "state"
ALARMS_PATH = STATE_DIR / "alarms.json"

INTERNAL_ALARM_FIRE_PREFIX = "__alarm_fire__"
_COMMAND_EXECUTOR = None




def set_command_executor(fn):
    """
    Register an optional in-process command executor for alarm attachments.

    Production main.py sets this so attached actions/music can use the
    already-loaded command brain. Standalone command_runtime/scheduler fallback
    still uses subprocess execution.
    """
    global _COMMAND_EXECUTOR
    _COMMAND_EXECUTOR = fn

# Persistent alarm state and output-target resolution

def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("’", "'").replace("‘", "'")
    s = re.sub(r"[?!.]+$", "", s).strip()
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _prefs(name: str, default):
    try:
        import app_config
        return getattr(app_config, name, default)
    except Exception:
        return default


def _ensure_state_dir() -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)


def _load_alarms() -> list:
    _ensure_state_dir()
    if not ALARMS_PATH.exists():
        return []
    try:
        with ALARMS_PATH.open("r") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        logging.exception("ALARM_LOAD_FAIL")
        return []


def _save_alarms(rows: list) -> None:
    _ensure_state_dir()
    tmp = ALARMS_PATH.with_suffix(".json.tmp")
    with tmp.open("w") as f:
        json.dump(rows, f, indent=2)
    tmp.replace(ALARMS_PATH)


def _update_alarm(alarm_id: str, **updates) -> Optional[dict]:
    rows = _load_alarms()
    found = None
    for row in rows:
        if isinstance(row, dict) and row.get("id") == alarm_id:
            row.update(updates)
            found = row
            break
    _save_alarms(rows)
    return found


def _room_to_sonos_entity(
    room: Optional[str],
    *,
    sonos_players: Optional[dict],
    default_sonos_room: Optional[str],
) -> Tuple[Optional[str], Optional[str]]:
    players = sonos_players or {}
    if not isinstance(players, dict):
        players = {}

    def nr(x):
        return _norm(x or "")

    # Precedence:
    # 1) explicit room passed into this helper
    # 2) effective target room / request-local room from request context
    # 3) legacy default Sonos room fallback
    explicit_room_n = nr(room)
    request_room_n = nr(get_active_room_for_request_defaults())
    default_n = nr(default_sonos_room)

    for candidate in (explicit_room_n, request_room_n, default_n):
        if candidate and candidate in players:
            return candidate, players[candidate]

    # Return the best available room identifier even if unresolved, for clearer diagnostics.
    return explicit_room_n or request_room_n or default_n or None, None


def _extract_output_target(
    text: str,
    *,
    sonos_players: Optional[dict],
    default_sonos_room: Optional[str],
) -> Tuple[str, Dict[str, Any]]:
    """
    Return (cleaned_text, output_target).

    output_target:
      {"mode": "local"}
      {"mode": "sonos", "room": "living room", "entity_id": "..."}
    """
    original = text or ""
    t = _norm(original)

    default_mode = str(_prefs("ALARM_DEFAULT_OUTPUT", "local") or "local").strip().lower()
    if default_mode not in ("local", "sonos"):
        default_mode = "local"

    target: Dict[str, Any] = {"mode": default_mode}

    if default_mode == "sonos":
        room, eid = _room_to_sonos_entity(None, sonos_players=sonos_players, default_sonos_room=default_sonos_room)
        target.update({"room": room, "entity_id": eid})

    # Explicit local output.
    local_patterns = [
        r"\s+(?:locally|local)$",
        r"\s+(?:on|through|over)\s+(?:the\s+)?(?:piphone|phone|handset|local speaker)$",
    ]
    for pat in local_patterns:
        if re.search(pat, t):
            cleaned = re.sub(pat, "", t).strip()
            return cleaned, {"mode": "local"}

    # Explicit generic Sonos/speaker output.
    speaker_patterns = [
        r"\s+(?:on|through|over)\s+(?:the\s+)?(?:speaker|speakers|sonos)$",
        r"\s+(?:out loud)$",
    ]
    for pat in speaker_patterns:
        if re.search(pat, t):
            cleaned = re.sub(pat, "", t).strip()
            room, eid = _room_to_sonos_entity(None, sonos_players=sonos_players, default_sonos_room=default_sonos_room)
            return cleaned, {"mode": "sonos", "room": room, "entity_id": eid}

    # Explicit room output: "... in living room", "... on kitchen"
    players = sonos_players or {}
    if isinstance(players, dict) and players:
        for room in sorted([str(r).strip().lower() for r in players.keys() if str(r).strip()], key=len, reverse=True):
            pat = rf"\s+(?:in|on)\s+(?:the\s+)?{re.escape(room)}$"
            if re.search(pat, t):
                cleaned = re.sub(pat, "", t).strip()
                room_n, eid = _room_to_sonos_entity(room, sonos_players=players, default_sonos_room=default_sonos_room)
                return cleaned, {"mode": "sonos", "room": room_n, "entity_id": eid}

    return t, target


# Creation parsing: time, label, room, and optional attached action

def _parse_when_to_schedule(when_text: str) -> Optional[Tuple[float, str, Optional[float]]]:
    """
    Use schedule_controls' already-improved date/time grammar by wrapping the
    time expression around a harmless placeholder command.
    """
    when_text = (when_text or "").strip()
    if not when_text:
        return None

    try:
        from schedule_controls import parse_schedule_request
    except Exception:
        logging.exception("ALARM_IMPORT_SCHEDULE_CONTROLS_FAIL")
        return None

    wt = _norm(when_text)

    # Duration-style: "5 minutes", "35 minutes", "one hour"
    if re.search(r"\b(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", wt):
        parsed = parse_schedule_request(f"in {wt} __alarm_noop__")
    else:
        # Absolute-style: "10:31", "12:05", "twelve oh eight", "7:30 pm"
        parsed = parse_schedule_request(f"at {wt} __alarm_noop__")

    if not parsed:
        return None

    return (float(parsed.run_at), str(parsed.phrase), getattr(parsed, "delay_seconds", None))



def _extract_attached_action(text: str) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Extract optional action/music attachment from alarm/timer creation text.

    Returns:
      (cleaned_text, action_command, music_command)

    Supported examples:
      - "wake me up at 8 with death cab for cutie"
        -> music_command "play death cab for cutie"

      - "wake me up at 8 to death cab for cutie"
        -> music_command "play death cab for cutie"

      - "set a timer for 5 minutes and play death cab for cutie"
        -> music_command "play death cab for cutie"

      - "set a pasta timer for 5 minutes and turn on kitchen lights"
        -> action_command "turn on kitchen lights"
    """
    t = _norm(text)
    if not t:
        return "", None, None

    # Wake/alarm music shorthand:
    #   wake me up at 8 with X
    #   wake me up at 8 to X
    #   set an alarm for 8 with X
    # We intentionally do this before generic "and ..." splitting.
    m = re.match(r"^(?P<head>.+?)\s+(?:with|to)\s+(?P<music>.+)$", t)
    if m:
        head = (m.group("head") or "").strip()
        tail = (m.group("music") or "").strip()

        # Only treat "with/to" as music attachment for alarm/wake phrases.
        # Avoid stealing room/output phrases like "in living room" which were
        # already handled by _extract_output_target before this helper runs.
        if re.search(r"\b(alarm|wake me up)\b", head) and tail:
            if not re.match(r"^(play|put on|listen to|start)\b", tail):
                tail = "play " + tail
            return head, None, tail

    # Generic attached command:
    #   set a timer for 5 minutes and turn on kitchen lights
    #   set a timer for 5 minutes and play death cab for cutie
    m = re.match(r"^(?P<head>.+?)\s+and\s+(?P<action>.+)$", t)
    if m:
        head = (m.group("head") or "").strip()
        action = (m.group("action") or "").strip()
        if action:
            if re.match(r"^(play|put on|listen to|start)\b", action):
                return head, None, action
            return head, action, None

    return t, None, None


def _execute_attached_command(command: str, *, label: str) -> bool:
    """
    Execute an attached action/music command.

    In production, main.py injects an in-process executor so we reuse the
    already-loaded command brain. If no executor is registered, fall back to
    command_runtime.py subprocess for standalone compatibility.
    """
    command = (command or "").strip()
    if not command:
        return True

    if callable(_COMMAND_EXECUTOR):
        try:
            logging.info("ALARM_ATTACHED_EXEC_INPROC_BEGIN label=%s command=%r", label, command)
            result = _COMMAND_EXECUTOR(command)
            logging.info("ALARM_ATTACHED_EXEC_INPROC_DONE label=%s result=%r", label, result)
            return True
        except Exception:
            logging.exception("ALARM_ATTACHED_EXEC_INPROC_FAIL label=%s command=%r", label, command)
            return False

    try:
        logging.info("ALARM_ATTACHED_EXEC_SUBPROCESS_BEGIN label=%s command=%r", label, command)
        proc = subprocess.run(
            [sys.executable, str(BASE_DIR / "command_runtime.py"), "--live", command],
            cwd=str(BASE_DIR),
            text=True,
            capture_output=True,
            timeout=float(_prefs("ALARM_ATTACHED_COMMAND_TIMEOUT_SEC", 30.0)),
            check=False,
        )
        logging.info(
            "ALARM_ATTACHED_EXEC_SUBPROCESS_DONE label=%s rc=%s stdout=%r stderr=%r",
            label,
            proc.returncode,
            (proc.stdout or "")[-500:],
            (proc.stderr or "")[-500:],
        )
        return proc.returncode == 0
    except Exception:
        logging.exception("ALARM_ATTACHED_EXEC_SUBPROCESS_FAIL label=%s command=%r", label, command)
        return False

def _normalize_alarm_room_before_time(text: str) -> str:
    """
    Normalize forms like:
      "set an alarm in the kitchen for 12 pm"
      "set an alarm on the kitchen for 12 pm"
    into:
      "set an alarm for 12 pm in the kitchen"
    so output-target extraction sees the room at the tail, where it already
    knows how to parse it safely.

    Keep this narrow and only for alarm/timer/wake-me-up phrasing.
    """
    t = _norm(text or "")
    if not t:
        return t

    m = re.match(
        r"^(?P<head>.*?\b(?:alarm|timer|wake\s+me\s+up)\b)\s+"
        r"(?P<prep>in|on)\s+(?P<room>.+?)\s+"
        r"(?P<timeprep>for|at|in)\s+(?P<when>.+)$",
        t,
    )
    if not m:
        return t

    head = (m.group("head") or "").strip()
    prep = (m.group("prep") or "").strip()
    room = (m.group("room") or "").strip()
    timeprep = (m.group("timeprep") or "").strip()
    when = (m.group("when") or "").strip()

    return f"{head} {timeprep} {when} {prep} {room}".strip()


def _parse_create_alarm(
    text: str,
    *,
    sonos_players: Optional[dict],
    default_sonos_room: Optional[str],
) -> Optional[Dict[str, Any]]:
    normalized_text = _normalize_alarm_room_before_time(text)

    raw_clean, output_target = _extract_output_target(
        normalized_text,
        sonos_players=sonos_players,
        default_sonos_room=default_sonos_room,
    )

    raw_clean, action_command, music_command = _extract_attached_action(raw_clean)

    t = _norm(raw_clean)
    if not t:
        return None

    kind = None
    label = None
    when_text = None

    # set a pasta timer for 5 minutes
    # set a timer for 5 minutes
    # timer for 5 minutes
    m = re.match(
        r"^(?:set\s+)?(?:a|an)?\s*(?:(?P<label>[a-z0-9][a-z0-9\s\-]*?)\s+)?timer\s+for\s+(?P<when>.+)$",
        t,
    )
    if m:
        kind = "timer"
        label = (m.group("label") or "").strip() or None
        when_text = (m.group("when") or "").strip()

    # set an alarm for 35 minutes
    # set an alarm for 10:31
    # set alarm at 10:31
    if not when_text:
        m = re.match(
            r"^(?:set\s+)?(?:a|an)\s+alarm\s+(?P<prep>for|at)\s+(?P<when>.+)$",
            t,
        ) or re.match(
            r"^(?:set\s+)?alarm\s+(?P<prep>for|at)\s+(?P<when>.+)$",
            t,
        )
        if m:
            when_text = (m.group("when") or "").strip()
            kind = "timer" if re.search(r"\b(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", when_text) else "alarm"
            label = None

    # set a beach alarm for 8am
    # set beach alarm at 7am
    if not when_text:
        m = re.match(
            r"^(?:set\s+)?(?:(?:a|an)\s+)?(?P<label>[a-z0-9][a-z0-9\s\-]*?)\s+alarm\s+(?P<prep>for|at)\s+(?P<when>.+)$",
            t,
        )
        if m:
            when_text = (m.group("when") or "").strip()
            kind = "timer" if re.search(r"\b(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", when_text) else "alarm"
            label = (m.group("label") or "").strip() or None

    # wake me up at 8
    # wake me up in 35 minutes
    if not when_text:
        m = re.match(r"^wake\s+me\s+up\s+(?P<prep>at|for|in)\s+(?P<when>.+)$", t)
        if m:
            when_text = (m.group("when") or "").strip()
            kind = "alarm"
            label = "wake up"

    if not when_text or not kind:
        return None

    parsed_when = _parse_when_to_schedule(when_text)
    if not parsed_when:
        return None

    run_at, phrase, delay_seconds = parsed_when

    # If the target is Sonos but no entity could be resolved, fail cleanly.
    if output_target.get("mode") == "sonos" and not output_target.get("entity_id"):
        return {
            "error": "sonos_target_unresolved",
            "kind": kind,
            "label": label,
            "when_text": when_text,
        }

    return {
        "kind": kind,
        "label": label,
        "run_at": run_at,
        "phrase": phrase,
        "delay_seconds": delay_seconds,
        "output": output_target,
        "action_command": action_command,
        "music_command": music_command,
    }


# Alarm playback and speech delivery

def _format_due_phrase(run_at: float) -> str:
    try:
        from schedule_controls import _format_due_phrase as fmt
        return fmt(run_at)
    except Exception:
        return "later"


def _alarm_message(alarm: dict) -> str:
    kind = str(alarm.get("kind") or "alarm")
    label = str(alarm.get("label") or "").strip()

    if kind == "timer":
        if label:
            return f"Your {label} timer is done."
        return "Your timer is done."

    if label and label != "wake up":
        return f"Your {label} alarm is going off."
    return "Your alarm is going off."


def _resolve_sound_path() -> Optional[Path]:
    raw = _prefs("ALARM_SOUND_FILE", "")
    if not raw:
        return None

    p = Path(str(raw))
    if not p.is_absolute():
        p = BASE_DIR / p

    if p.exists():
        return p

    logging.warning("ALARM_SOUND_FILE missing: %s", p)
    return None


def _play_local_file(path: Path) -> bool:
    try:
        subprocess.run(["mpg123", "-q", str(path)], timeout=30, check=False)
        return True
    except Exception:
        logging.exception("ALARM_LOCAL_SOUND_FAIL path=%s", path)
        return False


def _tts_to_file(text: str, *, prefix: str) -> Optional[Path]:
    try:
        from gtts import gTTS
        out = Path("/tmp") / f"{prefix}_{uuid.uuid4().hex[:8]}.mp3"
        try:
            from app_config import ALARM_TTS_TLD, TTS_LANGUAGE, TTS_PRONUNCIATION_OVERRIDES
        except Exception:
            ALARM_TTS_TLD = "ie"
            TTS_LANGUAGE = "en"
            TTS_PRONUNCIATION_OVERRIDES = {}
        kwargs = {
            "text": normalize_for_tts(text, pronunciation_overrides=TTS_PRONUNCIATION_OVERRIDES),
            "lang": TTS_LANGUAGE,
            "slow": False,
            "tokenizer_func": tokenize_for_gtts,
        }
        if ALARM_TTS_TLD:
            kwargs["tld"] = ALARM_TTS_TLD
        tts = gTTS(**kwargs)
        tts.save(str(out))
        return out
    except Exception:
        logging.exception("ALARM_TTS_GENERATE_FAIL")
        return None


def _speak_local(text: str) -> bool:
    path = _tts_to_file(text, prefix="homesuite_alarm_voice")
    if not path:
        return False
    try:
        return _play_local_file(path)
    finally:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass


def _sonos_play_file(entity_id: str, path: Path, *, announce: bool = True) -> bool:
    try:
        from sonos_utils import homesuite_media_url_for_path, sonos_play_media
    except Exception:
        logging.exception("ALARM_IMPORT_SONOS_UTILS_FAIL")
        return False

    floor = int(_prefs("ALARM_SONOS_ANNOUNCE_VOLUME_FLOOR", _prefs("ANNOUNCE_VOLUME_FLOOR", 15)))
    return bool(
        sonos_play_media(
            entity_id=entity_id,
            media_url=homesuite_media_url_for_path(path),
            media_type="music",
            announce=announce,
            announce_volume_floor=floor,
        )
    )


def _announce_sonos_text(entity_id: str, text: str) -> bool:
    path = _tts_to_file(text, prefix="homesuite_alarm_sonos_voice")
    if not path:
        return False
    try:
        return _sonos_play_file(entity_id, path, announce=True)
    finally:
        # Give Sonos a chance to fetch the file before deleting it.
        try:
            time.sleep(2.0)
            path.unlink(missing_ok=True)
        except Exception:
            pass



def _concat_audio_files_mp3(parts, *, prefix: str) -> Optional[Path]:
    """
    Concatenate multiple audio files into a single MP3 using ffmpeg filter concat.

    This is more reliable for MP3 inputs than the concat demuxer/file-list path.
    Returns the combined output path, or None on failure.
    """
    try:
        valid = []
        for p in (parts or []):
            if not p:
                continue
            pp = Path(str(p))
            if pp.exists():
                valid.append(pp)

        if not valid:
            return None
        if len(valid) == 1:
            return valid[0]

        out_path = Path("/tmp") / f"{prefix}_{uuid.uuid4().hex[:8]}.mp3"

        cmd = ["ffmpeg", "-y"]
        for p in valid:
            cmd.extend(["-i", str(p)])

        n = len(valid)
        filter_complex = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[aout]"

        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[aout]",
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            str(out_path),
        ])

        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=30,
        )

        if proc.returncode != 0 or not out_path.exists():
            try:
                out_path.unlink(missing_ok=True)
            except Exception:
                pass
            return None

        return out_path

    except Exception:
        logging.exception("ALARM_AUDIO_CONCAT_FAIL")
        return None
def _fire_alarm(alarm_id: str) -> str:
    rows = _load_alarms()
    alarm = next((r for r in rows if isinstance(r, dict) and r.get("id") == alarm_id), None)
    if not alarm:
        logging.error("ALARM_FIRE_MISSING id=%s", alarm_id)
        return ""

    if alarm.get("status") not in ("pending", "scheduled"):
        logging.info("ALARM_FIRE_IGNORED id=%s status=%r", alarm_id, alarm.get("status"))
        return ""

    _update_alarm(alarm_id, status="firing", fired_at=time.time())

    sound_enabled = bool(_prefs("ALARM_SOUND_ENABLED", True))
    voice_enabled = bool(_prefs("ALARM_VOICE_ENABLED", True))
    output = alarm.get("output") if isinstance(alarm.get("output"), dict) else {"mode": "local"}
    mode = str(output.get("mode") or "local").lower()
    message = _alarm_message(alarm)

    music_command = str(alarm.get("music_command") or "").strip()
    action_command = str(alarm.get("action_command") or "").strip()

    music_replaces_notification = bool(_prefs("ALARM_MUSIC_REPLACES_NOTIFICATION", True))
    action_before_notification = bool(_prefs("ALARM_ACTION_BEFORE_NOTIFICATION", True))

    logging.info(
        "ALARM_FIRE id=%s kind=%r label=%r output=%r music=%r action=%r music_replaces_notification=%r action_before_notification=%r",
        alarm_id,
        alarm.get("kind"),
        alarm.get("label"),
        output,
        music_command,
        action_command,
        music_replaces_notification,
        action_before_notification,
    )

    attached_ok = True

    try:
        # Run attached HA/device actions immediately if configured. This avoids
        # actions feeling delayed until after the alarm voice/sound finishes.
        if action_command and action_before_notification:
            attached_ok = _execute_attached_command(action_command, label="action") and attached_ok

        # For music alarms, default behavior is that the music *is* the alarm.
        # So skip chime/voice and start the music as soon as the alarm fires.
        if music_command and music_replaces_notification:
            attached_ok = _execute_attached_command(music_command, label="music") and attached_ok

            _update_alarm(
                alarm_id,
                status="fired" if attached_ok else "fired_with_attachment_error",
                completed_at=time.time(),
            )
            logging.info("ALARM_FIRE_DONE id=%s attached_ok=%r music_replaced_notification=True", alarm_id, attached_ok)
            return ""

        # Normal alarm/timer notification path.
        if mode == "sonos":
            entity_id = output.get("entity_id")
            if not entity_id:
                logging.error("ALARM_FIRE_NO_SONOS_ENTITY id=%s output=%r", alarm_id, output)
            else:
                sound_path = _resolve_sound_path() if sound_enabled else None

                # Preferred path: if both sound + voice are enabled, concatenate
                # them into one temporary announcement so Sonos only ducks once.
                if sound_enabled and voice_enabled and sound_path:
                    tts_path = _tts_to_file(message, prefix="homesuite_alarm_sonos_voice_combo")
                    combo_path = None
                    try:
                        if tts_path:
                            combo_path = _concat_audio_files_mp3(
                                [sound_path, tts_path],
                                prefix="homesuite_alarm_combo",
                            )

                        if combo_path:
                            _sonos_play_file(entity_id, combo_path, announce=True)
                            time.sleep(2.0)
                        else:
                            _sonos_play_file(entity_id, sound_path, announce=True)
                            time.sleep(float(_prefs("ALARM_SONOS_SOUND_TO_VOICE_DELAY_SEC", 1.5)))
                            _announce_sonos_text(entity_id, message)
                    finally:
                        try:
                            if tts_path:
                                Path(tts_path).unlink(missing_ok=True)
                        except Exception:
                            pass
                        try:
                            if combo_path and Path(combo_path).exists():
                                Path(combo_path).unlink(missing_ok=True)
                        except Exception:
                            pass

                else:
                    if sound_enabled and sound_path:
                        _sonos_play_file(entity_id, sound_path, announce=True)
                        if voice_enabled:
                            time.sleep(float(_prefs("ALARM_SONOS_SOUND_TO_VOICE_DELAY_SEC", 1.5)))

                    if voice_enabled:
                        _announce_sonos_text(entity_id, message)

        else:
            if sound_enabled:
                sound_path = _resolve_sound_path()
                if sound_path:
                    _play_local_file(sound_path)
            if voice_enabled:
                _speak_local(message)

        # Remaining attached commands, if prefs request after-notification behavior.
        if music_command and not music_replaces_notification:
            attached_ok = _execute_attached_command(music_command, label="music") and attached_ok

        if action_command and not action_before_notification:
            attached_ok = _execute_attached_command(action_command, label="action") and attached_ok

        _update_alarm(
            alarm_id,
            status="fired" if attached_ok else "fired_with_attachment_error",
            completed_at=time.time(),
        )
        logging.info("ALARM_FIRE_DONE id=%s attached_ok=%r", alarm_id, attached_ok)
        return ""

    except Exception:
        logging.exception("ALARM_FIRE_FAIL id=%s", alarm_id)
        _update_alarm(alarm_id, status="error", error="fire failed", completed_at=time.time())
        return ""


# Scheduler integration and serialized alarm metadata

def _save_new_alarm(alarm: Dict[str, Any]) -> Dict[str, Any]:
    rows = _load_alarms()
    rows.append(alarm)
    _save_alarms(rows)
    return alarm


def _schedule_alarm_fire(alarm_id: str, run_at: float, *, metadata: Optional[dict] = None) -> Optional[dict]:
    import scheduler
    return scheduler.schedule_command(
        f"{INTERNAL_ALARM_FIRE_PREFIX} {alarm_id}",
        run_at,
        metadata=metadata or {"kind": "alarm", "alarm_id": alarm_id},
    )




def _alarm_output_dict(alarm: dict) -> Dict[str, Any]:
    out = alarm.get("output")
    return out if isinstance(out, dict) else {}


# Listing, scoping, matching, and cancellation

def _alarm_associated_room(alarm: dict) -> Optional[str]:
    """
    Current canonical alarm room association.

    For now:
    * prefer explicit top-level room if introduced later
    * otherwise use Sonos output.room when present
    * local alarms remain roomless

    This keeps the design open for future endpoint-aware local alarm handling
    without forcing fake room assignment now.
    """
    room = str(alarm.get("room") or "").strip().lower()
    if room:
        return room

    out = _alarm_output_dict(alarm)
    room = str(out.get("room") or "").strip().lower()
    if room:
        return room

    return None


def _known_alarm_rooms(rows: Optional[list] = None) -> List[str]:
    rooms = set()
    src = rows if isinstance(rows, list) else _load_alarms()
    for row in src:
        if not isinstance(row, dict):
            continue
        room = _alarm_associated_room(row)
        if room:
            rooms.add(room)
    return sorted(rooms, key=len, reverse=True)


def _extract_room_phrase_from_text(text: str, rooms: Optional[List[str]] = None) -> Optional[str]:
    t = _norm(text or "")
    if not t:
        return None

    room_list = rooms or _known_alarm_rooms()
    if not room_list:
        return None

    for room in sorted(room_list, key=len, reverse=True):
        patterns = [
            rf"\bin\s+(?:the\s+)?{re.escape(room)}\b",
            rf"\bon\s+(?:the\s+)?{re.escape(room)}\b",
            rf"\bfor\s+(?:the\s+)?{re.escape(room)}\b",
        ]
        for pat in patterns:
            if re.search(pat, t):
                return room
    return None


def _strip_room_phrase(text: str, room: Optional[str]) -> str:
    t = _norm(text or "")
    if not t or not room:
        return t

    room = _norm(room)
    patterns = [
        rf"\bin\s+(?:the\s+)?{re.escape(room)}\b",
        rf"\bon\s+(?:the\s+)?{re.escape(room)}\b",
        rf"\bfor\s+(?:the\s+)?{re.escape(room)}\b",
    ]
    for pat in patterns:
        t = re.sub(pat, " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _cancel_has_explicit_global_scope(text: str) -> bool:
    t = _norm(text or "")
    if not t:
        return False
    return bool(
        re.search(r"\beverywhere\b", t)
        or re.search(r"\bin\s+(?:the\s+)?house\b", t)
        or re.search(r"\bin\s+(?:the\s+)?home\b", t)
        or re.search(r"\bevery\s+alarm\b", t)
        or re.search(r"\bevery\s+timer\b", t)
    )


def _default_alarm_scope_room(rows: Optional[list] = None) -> Optional[str]:
    room = get_active_room_for_request_defaults() or get_default_room_id()
    room_id = resolve_room_id(room)
    return room_id.replace("_", " ") if room_id else None


def _room_sort_key(alarm: dict, preferred_room: Optional[str]) -> Tuple[int, float]:
    room = _alarm_associated_room(alarm)
    run_at = float(alarm.get("_run_at_float") or alarm.get("run_at") or 0.0)

    if preferred_room and room == preferred_room:
        return (0, run_at)
    if room:
        return (1, run_at)
    return (2, run_at)


def _format_room_phrase(room: Optional[str]) -> str:
    room = str(room or "").strip()
    if not room:
        return ""
    return f"in the {room}"


def _scope_rows_by_room(rows: list, room: Optional[str]) -> list:
    if not room:
        return list(rows or [])
    room_n = _norm(room)
    return [r for r in (rows or []) if _alarm_associated_room(r) == room_n]


def _format_room_list_for_speech(rooms: List[str]) -> str:
    vals = [str(r).strip() for r in (rooms or []) if str(r).strip()]
    if not vals:
        return ""
    if len(vals) == 1:
        return f"in the {vals[0]}"
    if len(vals) == 2:
        return f"in the {vals[0]} and {vals[1]}"
    return "in the " + ", ".join(vals[:-1]) + f", and {vals[-1]}"


def _word_to_hour(token: str) -> Optional[int]:
    token = _norm(token or "")
    mapping = {
        "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
        "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    }
    return mapping.get(token)


def _extract_query_hour_hint(text: str) -> Optional[int]:
    t = _norm(text or "")
    if not t:
        return None

    m = re.search(r"\b(\d{1,2})(?::\d{2})?\s*(am|pm)?\b", t)
    if m:
        try:
            hour = int(m.group(1))
            if 1 <= hour <= 12:
                return hour
        except Exception:
            pass

    m = re.search(r"\b(one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)(?:\s+o'?clock)?\b", t)
    if m:
        return _word_to_hour(m.group(1))

    return None


def _extract_alarm_hour(alarm: dict) -> Optional[int]:
    try:
        run_at = float(alarm.get("run_at"))
    except Exception:
        return None
    try:
        dt = datetime.fromtimestamp(run_at)
        hour24 = int(dt.hour)
        hour12 = hour24 % 12
        return 12 if hour12 == 0 else hour12
    except Exception:
        return None

def _active_alarms(kind: Optional[str] = None) -> list:
    now = time.time()
    rows = _load_alarms()
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        status = str(row.get("status") or "").lower()
        if status not in ("pending", "scheduled"):
            continue

        if kind and str(row.get("kind") or "").lower() != kind:
            continue

        try:
            run_at = float(row.get("run_at"))
        except Exception:
            continue

        # Keep overdue pending alarms visible; scheduler may be catching up.
        row2 = dict(row)
        row2["_run_at_float"] = run_at
        row2["_seconds_left"] = max(0.0, run_at - now)
        out.append(row2)

    out.sort(key=lambda r: float(r.get("_run_at_float") or now))
    return out


def _time_left_phrase(run_at: float) -> str:
    try:
        return _format_due_phrase(float(run_at))
    except Exception:
        return "soon"


def _display_alarm_name(alarm: dict) -> str:
    kind = str(alarm.get("kind") or "alarm").lower()
    label = str(alarm.get("label") or "").strip()

    if kind == "timer":
        if label:
            return f"{label} timer"
        return "timer"

    if label and label != "wake up":
        return f"{label} alarm"
    return "alarm"


def _cancel_display_alarm_name(alarm: dict) -> str:
    """
    More specific name for cancellation confirmations.

    Examples:
      unnamed timer originally set for 5 minutes -> "5 minute timer"
      named timer -> "pasta timer"
      absolute alarm -> "alarm for 12:31 PM"
      wake-up alarm -> "wake up alarm"
    """
    kind = str(alarm.get("kind") or "alarm").lower()
    label = str(alarm.get("label") or "").strip()
    phrase = str(alarm.get("phrase") or "").strip()

    if kind == "timer":
        if label:
            return f"{label} timer"

        # Original duration phrases are stored as:
        #   "in 5 minutes"
        #   "in 30 seconds"
        m = re.match(r"^in\s+(\d+)\s+(second|seconds|minute|minutes|hour|hours)$", phrase, flags=re.I)
        if m:
            n = int(m.group(1))
            unit = m.group(2).lower()
            if unit.endswith("s"):
                unit = unit[:-1]
            return f"{n} {unit} timer"

        if phrase:
            p = phrase
            if p.lower().startswith("at "):
                p = p[3:].strip()
            return f"timer for {p}"

        return "timer"

    # Alarms.
    if label == "wake up":
        return "wake up alarm"

    if label:
        return f"{label} alarm"

    if phrase:
        p = phrase
        if p.lower().startswith("at "):
            p = p[3:].strip()
        return f"alarm for {p}"

    return "alarm"


def _spoken_list_parts(parts: list[str]) -> str:
    parts = [str(p).strip() for p in (parts or []) if str(p).strip()]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]

    out = []
    for i, p in enumerate(parts):
        if i == len(parts) - 1:
            out.append(f"And {p}")
        else:
            out.append(p)
    return ". ".join(out)


def _format_clock_time(run_at: float) -> str:
    try:
        dt = datetime.fromtimestamp(float(run_at))
        try:
            return dt.strftime("%-I:%M %p")
        except Exception:
            return dt.strftime("%I:%M %p").lstrip("0")
    except Exception:
        return "soon"


def _format_remaining_seconds_for_list(seconds_left: float) -> str:
    try:
        s = max(0, int(round(float(seconds_left))))
    except Exception:
        return "soon"

    if s < 60:
        return f"{s} second" if s == 1 else f"{s} seconds"

    m = int(round(s / 60.0))
    if m < 60:
        return f"{m} minute" if m == 1 else f"{m} minutes"

    h = int(round(s / 3600.0))
    return f"{h} hour" if h == 1 else f"{h} hours"


def _format_timer_summary_for_list(alarm: dict) -> str:
    label = str(alarm.get("label") or "").strip()
    room = _alarm_associated_room(alarm)
    room_phrase = _format_room_phrase(room)

    seconds_left = alarm.get("_seconds_left", None)
    if seconds_left is None:
        try:
            run_at = float(alarm.get("run_at"))
            seconds_left = max(0.0, float(run_at) - time.time())
        except Exception:
            return _display_alarm_name(alarm)

    remaining = _format_remaining_seconds_for_list(seconds_left)

    if label:
        return f"{label} timer {room_phrase} with {remaining} remaining".replace("  ", " ").strip()
    return f"one timer {room_phrase} with {remaining} remaining".replace("  ", " ").strip()


def _format_alarm_summary_for_list(alarm: dict) -> str:
    label = str(alarm.get("label") or "").strip()
    room = _alarm_associated_room(alarm)
    room_phrase = _format_room_phrase(room)

    try:
        run_at = float(alarm.get("run_at"))
        when = _format_clock_time(run_at)
    except Exception:
        return _display_alarm_name(alarm)

    if label and label != "wake up":
        return f"{label} alarm {room_phrase} for {when}".replace("  ", " ").strip()
    if label == "wake up":
        return f"wake up alarm {room_phrase} for {when}".replace("  ", " ").strip()
    return f"one alarm {room_phrase} for {when}".replace("  ", " ").strip()


def _format_alarm_summary(alarm: dict) -> str:
    kind = str(alarm.get("kind") or "alarm").lower()
    if kind == "timer":
        return _format_timer_summary_for_list(alarm)
    return _format_alarm_summary_for_list(alarm)


def _list_alarms_response(kind: Optional[str] = None) -> str:
    rows = _active_alarms(kind=kind)

    noun = "scheduled actions"
    if kind == "timer":
        noun = "timers"
    elif kind == "alarm":
        noun = "alarms"

    if not rows:
        if kind == "timer":
            return "You don't have any timers set."
        if kind == "alarm":
            return "You don't have any alarms set."
        return "You don't have any alarms or timers set."

    preferred_room = _default_alarm_scope_room(rows)
    rows = sorted(rows, key=lambda r: _room_sort_key(r, preferred_room))

    if len(rows) == 1:
        item = _format_alarm_summary(rows[0])

        if kind == "timer":
            if item.startswith("one timer "):
                return f"You have {item}."
            if item.startswith("one with "):
                return f"You have one timer {item[len('one with '):]}."
            return f"You have one {item}."

        if kind == "alarm":
            if item.startswith("one alarm "):
                return f"You have {item}."
            return f"You have one {item}."

        if item.startswith("one "):
            return f"You have {item}."
        return f"You have one alarm or timer: {item}."

    parts = [_format_alarm_summary(r) for r in rows[:3]]
    spoken = _spoken_list_parts(parts)

    if kind == "timer":
        intro = f"You have {len(rows)} timers set."
    elif kind == "alarm":
        intro = f"You have {len(rows)} alarms set."
    else:
        intro = f"You have {len(rows)} {noun}."

    return f"{intro} {spoken}."


def _strip_cancel_query_words(text: str) -> str:
    t = _norm(text)

    # Remove common command/query scaffolding.
    t = re.sub(r"^(please\s+)?(cancel|clear|delete|remove|stop)\s+", "", t).strip()
    t = re.sub(r"^(please\s+)?(what'?s|what is|what are|how much|how long|when is|when are)\s+", "", t).strip()
    t = re.sub(r"\b(time\s+is\s+left|time\s+left|left|remaining|set|scheduled|do i have|are set|is set)\b", " ", t)
    t = re.sub(r"\b(my|the|a|an|for|on|of)\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _tokens(s: str) -> set:
    stop = {
        "timer", "timers", "alarm", "alarms", "wake", "up",
        "cancel", "clear", "delete", "remove", "stop",
        "my", "the", "a", "an", "please", "set", "scheduled",
        "oclock", "oclock",
    }
    s = _norm(s)
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    return {x for x in s.split() if x and x not in stop}


def _score_alarm_match(
    alarm: dict,
    query: str,
    *,
    kind_hint: Optional[str],
    explicit_room: Optional[str] = None,
    preferred_room: Optional[str] = None,
) -> int:
    score = 0

    alarm_kind = str(alarm.get("kind") or "").lower()
    label = str(alarm.get("label") or "").strip().lower()
    name = _display_alarm_name(alarm).lower()
    q = _norm(query)
    alarm_room = _alarm_associated_room(alarm)

    if kind_hint and alarm_kind == kind_hint:
        score += 20

    if explicit_room and alarm_room == _norm(explicit_room):
        score += 120
    elif preferred_room and alarm_room == _norm(preferred_room):
        score += 35

    if not q:
        return score

    if label and (q == label or q in label or label in q):
        score += 80

    if name and (q == name or q in name or name in q):
        score += 60

    phrase = str(alarm.get("phrase") or "").strip().lower()
    if phrase and (q in phrase or phrase in q):
        score += 50

    query_hour = _extract_query_hour_hint(q)
    alarm_hour = _extract_alarm_hour(alarm)
    if query_hour is not None and alarm_hour is not None and query_hour == alarm_hour:
        score += 140

    q_toks = _tokens(q)
    hay = " ".join([
        str(alarm.get("label") or ""),
        _display_alarm_name(alarm),
        str(alarm.get("kind") or ""),
        phrase,
        str(alarm_room or ""),
    ])
    h_toks = _tokens(hay)

    if q_toks and h_toks:
        overlap = q_toks & h_toks
        score += len(overlap) * 25
        if q_toks.issubset(h_toks):
            score += 25

    return score


def _find_alarm_matches(
    query: str,
    *,
    kind_hint: Optional[str] = None,
    explicit_room: Optional[str] = None,
    preferred_room: Optional[str] = None,
) -> list:
    rows = _active_alarms(kind=kind_hint)
    if not rows:
        return []

    q = _strip_cancel_query_words(query)
    if explicit_room:
        q = _strip_room_phrase(q, explicit_room)

    q_toks = _tokens(q)

    # Room-only disambiguation such as "cancel the kitchen alarm"
    if explicit_room and (not q_toks or q_toks.issubset({"alarm", "alarms", "timer", "timers"})):
        rows2 = _scope_rows_by_room(rows, explicit_room)
        rows2.sort(key=lambda r: _room_sort_key(r, preferred_room))
        return rows2

    if not q_toks:
        rows2 = list(rows)
        if explicit_room:
            rows2 = _scope_rows_by_room(rows2, explicit_room)
        rows2.sort(key=lambda r: _room_sort_key(r, preferred_room))
        return rows2

    scored = []
    for row in rows:
        sc = _score_alarm_match(
            row,
            q,
            kind_hint=kind_hint,
            explicit_room=explicit_room,
            preferred_room=preferred_room,
        )
        if sc > 0:
            scored.append((sc, row))

    scored.sort(key=lambda x: (x[0], -float(x[1].get("_run_at_float") or 0)), reverse=True)
    return [r for (_sc, r) in scored]


def _cancel_alarm_row(alarm: dict) -> bool:
    alarm_id = str(alarm.get("id") or "")
    if not alarm_id:
        return False

    # Cancel corresponding scheduler job if known.
    job_id = str(alarm.get("scheduler_job_id") or "").strip()
    if job_id:
        try:
            import scheduler
            scheduler.cancel_job(job_id)
        except Exception:
            logging.exception("ALARM_CANCEL_SCHED_JOB_FAIL alarm_id=%s job_id=%s", alarm_id, job_id)

    rows = _load_alarms()
    changed = False
    for row in rows:
        if isinstance(row, dict) and row.get("id") == alarm_id:
            row["status"] = "canceled"
            row["canceled_at"] = time.time()
            changed = True
            break

    if changed:
        _save_alarms(rows)

    logging.info("ALARM_CANCELED id=%s job_id=%s", alarm_id, job_id)
    return changed


def _cancel_all_alarms(
    kind: Optional[str] = None,
    *,
    room: Optional[str] = None,
    global_scope: bool = False,
) -> str:
    rows = _active_alarms(kind=kind)

    if not global_scope:
        rows = _scope_rows_by_room(rows, room)

    if not rows:
        if kind == "timer":
            return "You don't have any timers to cancel."
        if kind == "alarm":
            return "You don't have any alarms to cancel."
        return "You don't have any alarms or timers to cancel."

    affected_rooms = sorted({r for r in (_alarm_associated_room(x) for x in rows) if r})
    n = 0
    for row in rows:
        if _cancel_alarm_row(row):
            n += 1

    room_phrase = _format_room_phrase(room)
    affected_room_phrase = _format_room_list_for_speech(affected_rooms)

    if kind == "timer":
        if global_scope:
            if affected_room_phrase:
                return f"Canceled {n} timers {affected_room_phrase}." if n != 1 else f"Canceled the timer {affected_room_phrase}."
            return "Canceled your timer." if n == 1 else f"Canceled {n} timers."
        if n == 1:
            return f"Canceled the timer {room_phrase}.".replace("  ", " ").strip()
        return f"Canceled {n} timers {room_phrase}.".replace("  ", " ").strip()

    if kind == "alarm":
        if global_scope:
            if affected_room_phrase:
                return f"Canceled {n} alarms {affected_room_phrase}." if n != 1 else f"Canceled the alarm {affected_room_phrase}."
            return "Canceled your alarm." if n == 1 else f"Canceled {n} alarms."
        if n == 1:
            return f"Canceled the alarm {room_phrase}.".replace("  ", " ").strip()
        return f"Canceled {n} alarms {room_phrase}.".replace("  ", " ").strip()

    if global_scope:
        if affected_room_phrase:
            return f"Canceled {n} alarms and timers {affected_room_phrase}."
        return "Canceled your alarm or timer." if n == 1 else f"Canceled {n} alarms and timers."
    return f"Canceled {n} alarms and timers {room_phrase}.".replace("  ", " ").strip()


def _cancel_matching_alarm(query: str, *, kind_hint: Optional[str] = None) -> str:
    explicit_room = _extract_room_phrase_from_text(query)
    preferred_room = _default_alarm_scope_room()

    matches = _find_alarm_matches(
        query,
        kind_hint=kind_hint,
        explicit_room=explicit_room,
        preferred_room=preferred_room,
    )

    if not matches:
        if kind_hint == "timer":
            return "I couldn't find a matching timer to cancel."
        if kind_hint == "alarm":
            return "I couldn't find a matching alarm to cancel."
        return "I couldn't find a matching alarm or timer to cancel."

    if len(matches) > 1:
        q = _strip_cancel_query_words(query)
        if explicit_room:
            q = _strip_room_phrase(q, explicit_room)

        if not _tokens(q):
            examples = "; ".join(_format_alarm_summary(m) for m in matches[:3])
            return f"You have multiple matching alarms or timers: {examples}. Say which one to cancel."

        top_score = _score_alarm_match(
            matches[0], q, kind_hint=kind_hint, explicit_room=explicit_room, preferred_room=preferred_room
        )
        second_score = _score_alarm_match(
            matches[1], q, kind_hint=kind_hint, explicit_room=explicit_room, preferred_room=preferred_room
        )

        # Be more willing to select the top match when the user gave structured
        # disambiguation like room or clock time.
        query_hour = _extract_query_hour_hint(q)
        strong_structure = bool(explicit_room or query_hour is not None)

        if top_score < 50 or ((top_score - second_score) < (10 if strong_structure else 20)):
            examples = "; ".join(_format_alarm_summary(m) for m in matches[:3])
            return f"I found multiple matches: {examples}. Say which one to cancel."

    alarm = matches[0]
    name = _cancel_display_alarm_name(alarm)
    room = _alarm_associated_room(alarm)
    room_phrase = _format_room_phrase(room)

    if _cancel_alarm_row(alarm):
        if room_phrase:
            return f"Canceled the {name} {room_phrase}.".replace("  ", " ").strip()
        return f"Canceled the {name}."

    return "I couldn't cancel that."


def _looks_like_alarm_list_request(t: str) -> Optional[str]:
    t = _norm(t)
    if not t:
        return None

    if re.fullmatch(r"(what'?s|what is)\s+(set|scheduled)", t):
        return "all"

    if re.search(
        r"\b("
        r"list|show|tell me|what'?s|what is|what are|which|"
        r"what do i have|which do i have|do i have|"
        r"when is|when are|how much time|how long"
        r")\b",
        t,
    ):
        if re.search(r"\btimers?\b", t):
            return "timer"
        if re.search(r"\balarms?\b", t):
            return "alarm"
        if re.search(r"\b(alarm|alarms|timer|timers)\b", t):
            return "all"

    # Common natural variants:
    #   what timers are there
    #   what timers are set
    #   what alarms are there
    #   what alarms are set
    #   what timer is set
    #   what alarm is set
    if re.fullmatch(r"(what|which)\s+timers?\s+(are\s+there|are\s+set|is\s+set)", t):
        return "timer"
    if re.fullmatch(r"(what|which)\s+alarms?\s+(are\s+there|are\s+set|is\s+set)", t):
        return "alarm"

    # Very terse forms.
    if re.fullmatch(r"(timers?|alarms?)", t):
        return "timer" if "timer" in t else "alarm"

    return None


def _looks_like_alarm_cancel_request(t: str) -> Tuple[bool, Optional[str], bool]:
    """
    Returns (is_cancel, kind_hint, cancel_all).
    """
    t = _norm(t)
    if not t:
        return False, None, False

    if not re.match(r"^(please\s+)?(cancel|clear|delete|remove|stop)\b", t):
        return False, None, False

    mentions_timer = bool(re.search(r"\btimers?\b", t))
    mentions_alarm = bool(re.search(r"\balarms?\b", t))

    kind_hint = None
    if mentions_timer and not mentions_alarm:
        kind_hint = "timer"
    elif mentions_alarm and not mentions_timer:
        kind_hint = "alarm"

    cancel_all = bool(re.search(r"\b(all|everything)\b", t))

    # Only claim generic cancel requests if they mention alarm/timer-ish words
    # or if they are terse "cancel timer"/"cancel alarm" patterns.
    if not (mentions_timer or mentions_alarm):
        return False, None, False

    return True, kind_hint, cancel_all

# Public dispatch entry point

def handle_alarm_controls(
    *,
    tl: str,
    maybe_say=None,
    sonos_players: Optional[dict] = None,
    default_sonos_room: Optional[str] = None,
) -> Optional[str]:
    """Handle create, query, and cancellation language for alarms and timers."""
    """
    Alarm/timer controls.

    Returns:
      - None: not an alarm/timer command
      - str/"": handled
    """
    t = _norm(tl)

    # Internal command executed by scheduler through command_runtime.py.
    m_fire = re.match(rf"^{re.escape(INTERNAL_ALARM_FIRE_PREFIX)}\s+([a-zA-Z0-9_-]+)$", t)
    if m_fire:
        return _fire_alarm(m_fire.group(1))

    list_kind = _looks_like_alarm_list_request(t)
    if list_kind:
        logging.info("CLAIM: alarm_controls list kind=%s text=%r", list_kind, tl)
        return _list_alarms_response(kind=None if list_kind == "all" else list_kind)

    cancel_req, cancel_kind, cancel_all = _looks_like_alarm_cancel_request(t)
    if cancel_req:
        logging.info("CLAIM: alarm_controls cancel kind=%s all=%r text=%r", cancel_kind, cancel_all, tl)
        if cancel_all:
            explicit_room = _extract_room_phrase_from_text(t)
            global_scope = _cancel_has_explicit_global_scope(t)
            scope_room = explicit_room if explicit_room else (None if global_scope else _default_alarm_scope_room())
            return _cancel_all_alarms(kind=cancel_kind, room=scope_room, global_scope=global_scope)
        return _cancel_matching_alarm(t, kind_hint=cancel_kind)

    parsed = _parse_create_alarm(
        t,
        sonos_players=sonos_players,
        default_sonos_room=default_sonos_room,
    )
    if not parsed:
        # Safety: if it looks like alarm/timer language but parse failed, claim it
        # so schedule_controls / immediate handlers do not produce a confusing result.
        if re.search(r"\b(alarm|timer|wake me up)\b", t):
            return "I heard an alarm or timer request, but I couldn't understand when to set it for."
        return None

    if parsed.get("error") == "sonos_target_unresolved":
        return "I couldn't figure out which speaker to use for that alarm."

    alarm_id = uuid.uuid4().hex[:8]
    now = time.time()
    alarm = {
        "id": alarm_id,
        "kind": parsed["kind"],
        "label": parsed.get("label"),
        "run_at": float(parsed["run_at"]),
        "phrase": parsed.get("phrase"),
        "status": "pending",
        "created_at": now,
        "output": parsed.get("output") or {"mode": "local"},
        "action_command": parsed.get("action_command"),
        "music_command": parsed.get("music_command"),
    }

    try:
        _save_new_alarm(alarm)
        job = _schedule_alarm_fire(
            alarm_id,
            float(alarm["run_at"]),
            metadata={
                "kind": "alarm",
                "alarm_id": alarm_id,
                "alarm_kind": alarm.get("kind"),
                "label": alarm.get("label"),
                "output": alarm.get("output"),
                "action_command": alarm.get("action_command"),
                "music_command": alarm.get("music_command"),
            },
        )
        if isinstance(job, dict) and job.get("id"):
            _update_alarm(alarm_id, scheduler_job_id=job.get("id"))
    except Exception:
        logging.exception("ALARM_CREATE_FAIL")
        return "I couldn't save that alarm."

    kind = alarm["kind"]
    label = str(alarm.get("label") or "").strip()
    phrase = str(alarm.get("phrase") or _format_due_phrase(float(alarm["run_at"]))).strip()

    output = alarm.get("output") or {}
    mode = output.get("mode")
    room = _alarm_associated_room(alarm)

    def _relative_compact(p: str) -> Optional[str]:
        """
        Convert "in 5 seconds" -> "5 second" for concise timer confirmations.
        Returns None for absolute-time phrases.
        """
        p = (p or "").strip()
        m = re.match(r"^in\s+(\d+)\s+(second|seconds|minute|minutes|hour|hours)$", p, flags=re.I)
        if not m:
            return None
        n = int(m.group(1))
        unit = m.group(2).lower()
        if unit.endswith("s"):
            unit = unit[:-1]
        return f"{n} {unit}"

    def _absolute_clean(p: str) -> str:
        p = (p or "").strip()
        if p.lower().startswith("at "):
            return p[3:].strip()
        return p

    rel = _relative_compact(phrase)

    if kind == "timer":
        if label:
            label_title = label[:1].upper() + label[1:]
            if rel:
                base = f"{label_title} timer set for {rel}s."
            else:
                base = f"{label_title} timer set for {_absolute_clean(phrase)}."
        else:
            if rel:
                base = f"{rel} timer set."
            else:
                base = f"Timer set for {_absolute_clean(phrase)}."
    else:
        if label:
            label_title = label[:1].upper() + label[1:]
            if rel:
                base = f"{label_title} alarm set for {rel}s."
            else:
                base = f"{label_title} alarm set for {_absolute_clean(phrase)}."
        else:
            if rel:
                base = f"Alarm set for {rel}s."
            else:
                base = f"Alarm set for {_absolute_clean(phrase)}."

    # Mention attachment briefly, without getting too chatty.
    if alarm.get("music_command"):
        base = base[:-1] + " with music."
    elif alarm.get("action_command"):
        base = base[:-1] + " with action."

    room_phrase = _format_room_phrase(room)
    if room_phrase:
        base = base[:-1] + f" {room_phrase}."

    include_output = bool(_prefs("ALARM_CONFIRM_INCLUDE_OUTPUT_TARGET", False))
    if include_output and mode == "sonos" and room:
        base = base[:-1] + f" on the {room} speaker."

    logging.info("CLAIM: alarm_controls id=%s alarm=%r", alarm_id, alarm)
    return base

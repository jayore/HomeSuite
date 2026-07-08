"""
applet_controls.py — Lifecycle manager for HomeSuite applets.

Applets are standalone Python scripts in ~/homesuite/applets/ that run as
independent subprocesses. This module owns start / stop / status plumbing
so each trigger (GPIO button, voice command, HTTP, Telegram, scheduler)
doesn't reimplement it.

PID tracking uses files in /tmp so multiple HomeSuite services can all
coordinate on the same set of running applets.

CLI for testing / scripting:
    python applet_controls.py list
    python applet_controls.py start note_lights
    python applet_controls.py stop note_lights
    python applet_controls.py toggle note_lights
    python applet_controls.py status note_lights
"""

import logging
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Registry — add new applets here
# ─────────────────────────────────────────────────────────────────────────

# Each applet has a "type" that determines how start/stop behave:
#   "subprocess"  — long-running standalone script. Owns its own process,
#                   launched via subprocess.Popen, tracked by PID file.
#                   Use for applets that need a continuous loop (audio,
#                   periodic polling, etc.).
#   "button_mode" — in-process state change. Activating sets a "mode" file
#                   in /tmp; physical_button_controls consults it before
#                   dispatching, swapping in a different button→action map.
#                   No subprocess, no continuous work — the mode lives only
#                   as long as the marker file exists.
#
# Common fields (all types):
#   description     — short summary for `list` output
#   display_name    — how this is spoken back in voice/chat responses.
#                     Defaults to name.replace("_", " ") if absent.
#   spoken_aliases  — extra phrasings the NL handler should recognize.
#
# Subprocess-only fields:
#   path            — absolute path to the Python script to run.
#
# button_mode-only fields:
#   button_actions  — {button_number: {gesture: command_string}}
#                     Mirrors PHYSICAL_BUTTON_ACTIONS structure. When the
#                     mode is active, unmapped buttons are no-ops — the
#                     mode has absolute control while running.

APPLETS = {
    "note_lights": {
        "type":           "subprocess",
        "path":           str(Path(__file__).resolve().parent / "applets" / "note_lights.py"),
        "description":    "Map instrument notes to Home Assistant light colors",
        "display_name":   "note lights",
        "spoken_aliases": ["note light", "notes lights"],
    },

    "apple_tv_remote": {
        "type":           "button_mode",
        "description":    "Use the physical buttons as an Apple TV remote",
        "display_name":   "apple tv remote",
        "spoken_aliases": ["apple tv nav", "apple tv navigation",
                           "tv remote", "apple tv mode"],
        "button_actions": {
            # Button 1: short = up, long = toggle (same command pairs with
            # the long_press in PHYSICAL_BUTTON_ACTIONS used to ENTER the
            # mode — symmetric: long-press 1 always toggles, regardless of
            # whether we're currently in the mode or out of it).
            1: {"press": "up", "long_press": "toggle apple tv remote"},
            2: {"press": "top menu"},
            3: {"press": "right"},
            4: {"press": "select"},
            5: {"press": "down"},
            6: {"press": "menu"},
            7: {"press": "left"},
            8: {"press": "screensaver"},
        },
    },

    # Future:
    #   "sonos_remote":    {"type": "button_mode", ...},
    #   "rain_sounds":     {"type": "subprocess",  ...},
    #   "morning_routine": {...},
}


# ─────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────

# Python interpreter used to launch subprocess applets. sys.executable means
# "whatever Python is running this code right now", which adapts per device:
#   Native install — the HomeSuite .venv Python
#   Alternate/manual installs — whatever interpreter launched HomeSuite
# Each device's homesuite.service inherits its own Python, so the applet
# launches with the same interpreter the service is using — meaning whatever
# deps that interpreter has are what the applet can use. (Per-applet
# dependencies still need to be installed on every device that runs it.)
PYTHON_BIN       = sys.executable
PIDFILE_DIR      = Path("/tmp")
PIDFILE_PREFIX   = "piphone_applet_"
LOGFILE_PREFIX   = "piphone_applet_"

LIVENESS_WAIT_SEC = 0.3   # how long to wait after launch to verify it stayed alive
STOP_TIMEOUT_SEC  = 3.0   # how long to wait for SIGTERM before SIGKILL

# Sentinel written into the pidfile of a button_mode applet (since there's
# no real PID — the mode is in-memory state, but we use the same file
# convention so cross-process visibility works the same way as subprocess
# applets do via /tmp).
BUTTON_MODE_TOKEN = "mode"


# ─────────────────────────────────────────────────────────────────────────
# PID + liveness helpers (private)
# ─────────────────────────────────────────────────────────────────────────

def _pidfile(name: str) -> Path:
    return PIDFILE_DIR / f"{PIDFILE_PREFIX}{name}.pid"

def _logfile(name: str) -> Path:
    return PIDFILE_DIR / f"{LOGFILE_PREFIX}{name}.log"

def _read_pid(name: str) -> Optional[int]:
    pf = _pidfile(name)
    if not pf.exists():
        return None
    try:
        return int(pf.read_text().strip())
    except (ValueError, OSError):
        return None

def _pid_alive(pid: int) -> bool:
    """True iff a process with this PID currently exists."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by someone else — still "alive"
        return True
    except Exception:
        return False

def _clear_stale_pidfile(name: str) -> None:
    try:
        _pidfile(name).unlink(missing_ok=True)
    except Exception:
        log.exception("PIDFILE_UNLINK_FAIL name=%s", name)


# ─────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────

def _applet_type(name: str) -> str:
    """Look up an applet's type, defaulting to 'subprocess' for backward compat."""
    meta = APPLETS.get(name) or {}
    return str(meta.get("type") or "subprocess").strip().lower()


def is_running(name: str) -> bool:
    """True iff the named applet is currently active."""
    if name not in APPLETS:
        return False
    if _applet_type(name) == "button_mode":
        return _is_button_mode_active(name)
    # subprocess (default)
    pid = _read_pid(name)
    if pid is None:
        return False
    if _pid_alive(pid):
        return True
    _clear_stale_pidfile(name)
    return False


def get_pid(name: str) -> Optional[int]:
    """Current PID of a running subprocess applet, or None.
    button_mode applets have no PID (returns None even when active)."""
    if _applet_type(name) != "subprocess":
        return None
    return _read_pid(name) if is_running(name) else None


def list_applets() -> list:
    """Return [(name, description, is_running)] for every registered applet."""
    return [
        (name, meta.get("description", ""), is_running(name))
        for name, meta in APPLETS.items()
    ]


def list_running() -> list:
    """Return [(name, pid_or_token)] for every applet currently running.
    Token is the int PID for subprocess applets, or the literal string
    BUTTON_MODE_TOKEN for active button-mode applets."""
    out = []
    for name in APPLETS:
        if not is_running(name):
            continue
        if _applet_type(name) == "button_mode":
            out.append((name, BUTTON_MODE_TOKEN))
        else:
            out.append((name, _read_pid(name)))
    return out


# ─────────────────────────────────────────────────────────────────────────
# Button-mode public API — consumed by physical_button_controls
# ─────────────────────────────────────────────────────────────────────────

def _is_button_mode_active(name: str) -> bool:
    pf = _pidfile(name)
    if not pf.exists():
        return False
    try:
        return pf.read_text().strip() == BUTTON_MODE_TOKEN
    except OSError:
        return False


def get_active_button_mode() -> Optional[str]:
    """Name of the currently active button-mode applet, or None.
    Reads from the pidfile every call so cross-process callers (main runtime's
    button dispatcher) see changes made by other processes (HTTP, Telegram)."""
    for name, meta in APPLETS.items():
        if (meta.get("type") or "subprocess") != "button_mode":
            continue
        if _is_button_mode_active(name):
            return name
    return None


_GESTURE_ALIASES = {
    "press":        ("press", "single_press", "single"),
    "double_press": ("double_press", "double"),
    "long_press":   ("long_press", "long", "hold"),
}

def get_button_mode_action(button: int, gesture: str):
    """If a button-mode applet is currently active, return its mapped action
    for this (button, gesture). Returns None if no mode is active OR if
    the mode has no mapping for this button/gesture combo.

    Callers (physical_button_controls) should treat 'mode active but no
    mapping' as a no-op — see get_active_button_mode() to distinguish."""
    active = get_active_button_mode()
    if active is None:
        return None
    bmap = ((APPLETS.get(active) or {}).get("button_actions") or {}).get(int(button))
    if not isinstance(bmap, dict):
        return None
    for key in _GESTURE_ALIASES.get(gesture, (gesture,)):
        if key in bmap:
            return bmap[key]
    return None


# ─────────────────────────────────────────────────────────────────────────
# start/stop dispatchers — route by applet type
# ─────────────────────────────────────────────────────────────────────────

def start_applet(name: str) -> str:
    """Start the named applet. Returns a human-readable status string."""
    if name not in APPLETS:
        return f"Unknown applet: {name!r}"
    t = _applet_type(name)
    if t == "subprocess":
        return _start_subprocess_applet(name)
    if t == "button_mode":
        return _start_button_mode_applet(name)
    return f"Unknown applet type for {name}: {t!r}"


def _start_button_mode_applet(name: str) -> str:
    if _is_button_mode_active(name):
        return f"{name} is already running"

    # One button-mode at a time — stop any other active mode first.
    other = get_active_button_mode()
    if other is not None and other != name:
        _stop_button_mode_applet(other)
        log.info("APPLET_BUTTON_MODE_REPLACED old=%s new=%s", other, name)

    try:
        _pidfile(name).write_text(BUTTON_MODE_TOKEN)
    except OSError as e:
        log.exception("APPLET_MODE_WRITE_FAIL name=%s", name)
        return f"Failed to start {name}: {e}"

    log.info("APPLET_BUTTON_MODE_START name=%s", name)
    return f"Started {name}"


def _start_subprocess_applet(name: str) -> str:
    if is_running(name):
        return f"{name} is already running (pid {_read_pid(name)})"

    path = APPLETS[name].get("path")
    if not path or not Path(path).exists():
        return f"Applet script not found: {path}"

    log_path = _logfile(name)
    try:
        log_fh = open(log_path, "ab")
    except OSError as e:
        log.exception("APPLET_LOG_OPEN_FAIL name=%s", name)
        return f"Failed to open log for {name}: {e}"

    try:
        log_fh.write(f"\n=== {time.ctime()}  starting {name} ===\n".encode())
        log_fh.flush()

        # start_new_session=True puts the child in its own process group so
        # it doesn't inherit signal propagation from the parent service.
        # Means: homesuite.service restarting won't kill the applet.
        proc = subprocess.Popen(
            [PYTHON_BIN, path],
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception as e:
        log_fh.close()
        log.exception("APPLET_POPEN_FAIL name=%s", name)
        return f"Failed to start {name}: {e}"

    # Brief liveness check — catches "script crashes at import" so we
    # report failure with the log tail instead of fake-reporting success.
    time.sleep(LIVENESS_WAIT_SEC)
    if proc.poll() is not None:
        rc = proc.returncode
        log_fh.close()
        try:
            tail = log_path.read_text()[-400:]
        except Exception:
            tail = "(could not read log)"
        log.warning("APPLET_DIED_AT_START name=%s rc=%s", name, rc)
        return f"{name} failed to start (exit {rc}). Tail of log:\n{tail}"

    log_fh.close()  # the child has its own dup'd fd
    _pidfile(name).write_text(str(proc.pid))
    log.info("APPLET_START name=%s pid=%d log=%s", name, proc.pid, log_path)
    return f"Started {name} (pid {proc.pid})"


def stop_applet(name: str) -> str:
    """Stop the named applet. Returns a human-readable status string.
    Idempotent — calling on a non-running applet is fine."""
    if name not in APPLETS:
        return f"Unknown applet: {name!r}"
    t = _applet_type(name)
    if t == "subprocess":
        return _stop_subprocess_applet(name)
    if t == "button_mode":
        return _stop_button_mode_applet(name)
    return f"Unknown applet type for {name}: {t!r}"


def _stop_button_mode_applet(name: str) -> str:
    if not _is_button_mode_active(name):
        _clear_stale_pidfile(name)
        return f"{name} is not running"
    try:
        _pidfile(name).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        log.exception("APPLET_MODE_PIDFILE_UNLINK_FAIL name=%s", name)
    log.info("APPLET_BUTTON_MODE_STOP name=%s", name)
    return f"Stopped {name}"


def _stop_subprocess_applet(name: str) -> str:
    pid = _read_pid(name)
    if pid is None or not _pid_alive(pid):
        _clear_stale_pidfile(name)
        return f"{name} is not running"

    log.info("APPLET_STOP name=%s pid=%d (SIGTERM)", name, pid)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        _clear_stale_pidfile(name)
        return f"{name} already exited"
    except PermissionError as e:
        return f"Cannot signal {name} (pid {pid}): {e}"

    # Wait for graceful exit
    deadline = time.monotonic() + STOP_TIMEOUT_SEC
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            _clear_stale_pidfile(name)
            log.info("APPLET_STOP_DONE name=%s pid=%d", name, pid)
            return f"Stopped {name}"
        time.sleep(0.1)

    # Escalate
    log.warning("APPLET_STOP_KILL name=%s pid=%d (SIGTERM ignored)", name, pid)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    _clear_stale_pidfile(name)
    return f"Force-stopped {name}"


def toggle_applet(name: str) -> str:
    """Start if not running, stop if running."""
    if is_running(name):
        return stop_applet(name)
    return start_applet(name)


# ─────────────────────────────────────────────────────────────────────────
# Natural-language command handler
# ─────────────────────────────────────────────────────────────────────────
#
# Recognizes phrases like:
#   "start note lights"      → start_applet("note_lights")
#   "stop the note lights"   → stop_applet("note_lights")
#   "toggle note lights"     → toggle_applet("note_lights")
#   "launch note lights app" → start_applet("note_lights")
#
# Returns:
#   None  — text wasn't an applet command, or the verb matched but the
#           target wasn't a known applet ("stop the music" → fall through
#           so media handlers get a shot at it)
#   str   — handled; the string is the user-facing response

_START_VERBS  = r"start|run|launch|open|begin|fire\s+up|kick\s+off"
_STOP_VERBS   = r"stop|quit|exit|close|kill|end|cancel|shut\s+down"
_TOGGLE_VERBS = r"toggle"

def _display_name(name: str) -> str:
    meta = APPLETS.get(name) or {}
    return meta.get("display_name") or name.replace("_", " ")

def _resolve_applet_name(target: str) -> Optional[str]:
    """Match a target phrase against the registry — name, display_name, or
    aliases. Tries several variants of the target (with/without leading
    'the', with/without trailing 'applet|app|mode|please') so that 'mode'
    can act as filler in some phrases and as a meaningful disambiguator
    in others (e.g. "apple tv mode" → matches alias 'apple tv mode').
    """
    base = re.sub(r"\s+", " ", target.lower().strip())
    if not base:
        return None

    # Strip trailing filler words iteratively — "note lights applet please"
    # has two stacked fillers that one pass can't handle.
    def _strip_trailing_fillers(s: str) -> str:
        prev = None
        while s != prev:
            prev = s
            s = re.sub(r"\s+(applet|app|mode|please)$", "", s).strip()
        return s

    # Generate candidate strings: cross-product of optional prefix and
    # optional suffix removals. Each variant is checked independently.
    variants = set()
    for strip_prefix in (False, True):
        for strip_suffix in (False, True):
            v = base
            if strip_prefix:
                v = re.sub(r"^the\s+", "", v)
            if strip_suffix:
                v = _strip_trailing_fillers(v)
            variants.add(v.strip())

    for v in variants:
        v_underscored = v.replace(" ", "_").replace("-", "_")
        for name, meta in APPLETS.items():
            if v == name or v_underscored == name:
                return name
            candidates = [
                (meta.get("display_name") or "").lower(),
                name.replace("_", " ").lower(),
            ] + [a.lower() for a in meta.get("spoken_aliases", [])]
            if v in candidates:
                return name
    return None


def handle_applet_controls(text: str) -> Optional[str]:
    """
    NL entry point. Recognize 'start/stop/toggle <applet>' phrases and
    drive the lifecycle. Returns user-facing response string or None.
    """
    if not text:
        return None
    t = text.lower().strip().rstrip(".!?")

    for verb_group, action in (
        (_START_VERBS,  "start"),
        (_STOP_VERBS,   "stop"),
        (_TOGGLE_VERBS, "toggle"),
    ):
        # Pattern: optional "please" + verb + target + optional punct.
        # Leading 'the' and trailing 'applet/app/mode/please' are handled
        # downstream in _resolve_applet_name (since 'mode' / 'please' can
        # be either filler or meaningful depending on the applet).
        pattern = (
            rf"^(?:please\s+)?({verb_group})\s+(.+?)\s*[.?!]*$"
        )
        m = re.match(pattern, t)
        if not m:
            continue

        target = m.group(2).strip()
        applet_name = _resolve_applet_name(target)
        if applet_name is None:
            # Verb matched but target wasn't an applet. Don't claim this —
            # something else (media, lights, etc.) might want it.
            return None

        return _drive_lifecycle(action, applet_name)

    return None


def _drive_lifecycle(action: str, name: str) -> str:
    """Execute the requested lifecycle action with state-aware messages.

    Captures is_running ONCE up front to avoid racing against another
    path (a duplicate command via a different channel, a button-mode exit
    that fired in parallel) that might toggle the state between checks.
    The response reflects what we observed at the start of the call.
    """
    display = _display_name(name)
    cap = display[:1].upper() + display[1:]
    was_running = is_running(name)
    log.info("APPLET_NL_LIFECYCLE action=%s name=%s was_running=%s",
             action, name, was_running)

    try:
        if action == "start":
            if was_running:
                return f"{cap} is already running"
            result = start_applet(name)
            return f"Starting {display}" if is_running(name) else f"Couldn't start {display}: {result}"

        if action == "stop":
            if not was_running:
                return f"{cap} isn't running"
            stop_applet(name)
            # We saw it running and asked it to stop. If something else
            # also stopped it in parallel, that's fine — the user gets a
            # consistent "Stopping" message that matches their intent.
            return f"Stopping {display}"

        if action == "toggle":
            if was_running:
                stop_applet(name)
                return f"Stopping {display}"
            result = start_applet(name)
            return f"Starting {display}" if is_running(name) else f"Couldn't start {display}: {result}"

    except Exception as e:
        log.exception("APPLET_LIFECYCLE_FAIL action=%s name=%s", action, name)
        return f"Error: {e}"

    return f"Unknown action: {action}"


# ─────────────────────────────────────────────────────────────────────────
# CLI — for testing from any shell or wiring into a button/script trigger
# ─────────────────────────────────────────────────────────────────────────

def _main():
    if len(sys.argv) < 2:
        _usage()
        return 1

    action = sys.argv[1]
    name   = sys.argv[2] if len(sys.argv) > 2 else None

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s  %(levelname)-7s  %(message)s",
                        datefmt="%H:%M:%S")

    if action == "list":
        for n, desc, running in list_applets():
            mark = "●" if running else "○"
            print(f"  {mark}  {n:18s}  {desc}")
        return 0

    if action == "running":
        running = list_running()
        if not running:
            print("(none)")
        else:
            for n, pid in running:
                print(f"  {n:18s}  pid={pid}")
        return 0

    if not name:
        _usage()
        return 1

    if action == "start":
        print(start_applet(name)); return 0
    if action == "stop":
        print(stop_applet(name)); return 0
    if action == "toggle":
        print(toggle_applet(name)); return 0
    if action == "status":
        print("running" if is_running(name) else "not running"); return 0

    _usage()
    return 1


def _usage():
    print("usage: applet_controls.py <action> [<name>]")
    print("  list                  — show all registered applets")
    print("  running               — show currently running applets")
    print("  start   <name>")
    print("  stop    <name>")
    print("  toggle  <name>")
    print("  status  <name>")


if __name__ == "__main__":
    sys.exit(_main())

"""Translate configured GPIO button gestures into serialized HomeSuite commands.

The pigpio callbacks enqueue edge events and return quickly. A worker thread
handles debounce, press/hold/repeat state, handset policy, and command execution
outside pigpio's callback thread. Configuration comes from ``app_config`` and
the subsystem remains inert unless physical buttons are enabled.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional


try:
    import pigpio
except Exception:
    pigpio = None


_COMMAND_EXECUTOR = None
_HANDSET_IS_UP = None
_STARTED = False
_STOP_EVENT = threading.Event()
_EVENT_Q: "queue.Queue[dict]" = queue.Queue()
_WORKER_THREAD = None
_PI = None
_CALLBACKS = []
_STATES: Dict[int, "ButtonState"] = {}
_PIN_TO_BUTTON: Dict[int, int] = {}


def _prefs(name: str, default):
    try:
        import app_config
        return getattr(app_config, name, default)
    except Exception:
        return default


def _enabled() -> bool:
    return bool(_prefs("PHYSICAL_BUTTONS_ENABLED", False))


def _backend() -> str:
    return str(_prefs("PHYSICAL_BUTTON_BACKEND", "pigpio") or "pigpio").strip().lower()


def _host() -> str:
    return str(_prefs("PHYSICAL_BUTTON_PIGPIO_HOST", "127.0.0.1") or "127.0.0.1").strip()


def _port() -> int:
    try:
        return int(_prefs("PHYSICAL_BUTTON_PIGPIO_PORT", 8888))
    except Exception:
        return 8888


def _pins() -> Dict[int, int]:
    raw = _prefs("PHYSICAL_BUTTON_PINS", {}) or {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[int, int] = {}
    for k, v in raw.items():
        try:
            out[int(k)] = int(v)
        except Exception:
            continue
    return out


def _actions() -> Dict[int, dict]:
    raw = _prefs("PHYSICAL_BUTTON_ACTIONS", {}) or {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[int, dict] = {}
    for k, v in raw.items():
        try:
            btn = int(k)
        except Exception:
            continue
        if isinstance(v, dict):
            out[btn] = v
    return out


def _active_low() -> bool:
    return bool(_prefs("PHYSICAL_BUTTON_ACTIVE_LOW", True))


def _pull_up() -> bool:
    return bool(_prefs("PHYSICAL_BUTTON_PULL_UP", True))


def _ignore_while_handset_up() -> bool:
    return bool(_prefs("PHYSICAL_BUTTON_IGNORE_WHILE_HANDSET_UP", False))


def _now() -> float:
    return time.monotonic()


def _ms(name: str, default: int) -> float:
    try:
        return max(0, float(_prefs(name, default)) / 1000.0)
    except Exception:
        return float(default) / 1000.0


def _gesture_action(button: int, gesture: str):
    # Check button-mode applet override first. When a button-mode applet
    # is active it has ABSOLUTE control: unmapped buttons are no-ops, not
    # fall-throughs. This prevents surprise behavior — e.g., entering Apple
    # TV remote mode shouldn't leave random buttons triggering their
    # default smart-home actions.
    try:
        from applet_controls import get_active_button_mode, get_button_mode_action
        active_mode = get_active_button_mode()
        if active_mode is not None:
            override = get_button_mode_action(int(button), str(gesture))
            if override is not None:
                logging.info(
                    "BUTTON_MODE_OVERRIDE mode=%s button=%s gesture=%s cmd=%r",
                    active_mode, button, gesture, override,
                )
                return override
            logging.info(
                "BUTTON_MODE_UNMAPPED mode=%s button=%s gesture=%s (no-op)",
                active_mode, button, gesture,
            )
            return None
    except ImportError:
        # applet_controls not on path — should never happen on a normal
        # PiPhone install, but don't let it break button handling.
        pass
    except Exception:
        logging.exception("BUTTON_MODE_LOOKUP_FAIL button=%s gesture=%s", button, gesture)

    # No active button mode — default lookup against PHYSICAL_BUTTON_ACTIONS.
    amap = _actions().get(int(button), {}) or {}
    aliases = {
        "press": ("press", "single_press", "single"),
        "double_press": ("double_press", "double"),
        "long_press": ("long_press", "long", "hold"),
    }
    for key in aliases.get(gesture, (gesture,)):
        if key in amap:
            return amap.get(key)
    return None


def _normalize_action_to_commands(action) -> list[str]:
    if action is None:
        return []
    if isinstance(action, str):
        s = action.strip()
        return [s] if s else []
    if isinstance(action, (list, tuple)):
        out = []
        for item in action:
            out.extend(_normalize_action_to_commands(item))
        return out
    if isinstance(action, dict):
        if "command" in action:
            return _normalize_action_to_commands(action.get("command"))
        if "commands" in action:
            return _normalize_action_to_commands(action.get("commands"))
    return []


def _repeat_config_for_action(action) -> tuple[bool, float, int]:
    if not isinstance(action, dict):
        return False, 0.0, 0

    repeat = bool(
        action.get("repeat_while_held")
        or action.get("repeat")
        or action.get("repeat_until_release")
    )
    if not repeat:
        return False, 0.0, 0

    try:
        interval_ms = float(action.get(
            "repeat_interval_ms",
            _prefs("PHYSICAL_BUTTON_HOLD_REPEAT_INTERVAL_MS", 350),
        ))
    except Exception:
        interval_ms = 350.0

    try:
        max_repeats = int(action.get(
            "max_repeats",
            _prefs("PHYSICAL_BUTTON_HOLD_REPEAT_MAX_REPEATS", 30),
        ))
    except Exception:
        max_repeats = 30

    interval_s = max(0.05, interval_ms / 1000.0)
    max_repeats = max(1, max_repeats)
    return True, interval_s, max_repeats


def set_command_executor(fn):
    global _COMMAND_EXECUTOR
    _COMMAND_EXECUTOR = fn


def _handset_up() -> bool:
    if callable(_HANDSET_IS_UP):
        try:
            return bool(_HANDSET_IS_UP())
        except Exception:
            return False
    return False


@dataclass
class ButtonState:
    button: int
    pin: int
    pressed: bool = False
    consumed: bool = False
    seq: int = 0
    last_down_ts: float = 0.0
    last_up_ts: float = 0.0
    long_timer: Optional[threading.Timer] = None
    single_timer: Optional[threading.Timer] = None
    settle_timer: Optional[threading.Timer] = None
    pending_level: Optional[int] = None
    lock: threading.RLock = field(default_factory=threading.RLock)


def _cancel_timer(t: Optional[threading.Timer]) -> None:
    try:
        if t is not None:
            t.cancel()
    except Exception:
        pass


def _enqueue_gesture(button: int, gesture: str) -> None:
    try:
        _EVENT_Q.put_nowait({
            "type": "gesture",
            "button": int(button),
            "gesture": str(gesture),
            "ts": time.time(),
        })
    except Exception:
        logging.exception("BUTTON_ENQUEUE_FAIL button=%s gesture=%s", button, gesture)


def _button_still_pressed(button: int) -> bool:
    st = _STATES.get(int(button))
    if not st:
        return False
    try:
        with st.lock:
            return bool(st.pressed)
    except Exception:
        return False


def _sleep_until_repeat_or_release(button: int, interval_s: float) -> bool:
    deadline = _now() + float(interval_s)
    while _now() < deadline:
        if _STOP_EVENT.is_set():
            return False
        if not _button_still_pressed(button):
            return False
        time.sleep(0.025)
    return _button_still_pressed(button)


def _fire_long_if_still_pressed(button: int, seq: int) -> None:
    st = _STATES.get(button)
    if not st:
        return
    with st.lock:
        if st.seq != seq:
            return
        if not st.pressed:
            return
        if st.consumed:
            return
        st.consumed = True
        logging.info("BUTTON_GESTURE button=%s pin=%s gesture=long_press", st.button, st.pin)
        _enqueue_gesture(st.button, "long_press")


def _fire_single_if_not_consumed(button: int, seq: int) -> None:
    st = _STATES.get(button)
    if not st:
        return
    with st.lock:
        # This timer has now fired; clear the reference so the next physical
        # press is not mistaken for the second press of a stale double-press
        # window.
        st.single_timer = None

        if st.seq != seq:
            return
        if st.pressed:
            return
        if st.consumed:
            return
        st.consumed = True
        logging.info("BUTTON_GESTURE button=%s pin=%s gesture=press", st.button, st.pin)
        _enqueue_gesture(st.button, "press")


def _on_down(st: ButtonState) -> None:
    with st.lock:
        now = _now()
        if st.pressed:
            return

        if st.single_timer is not None:
            _cancel_timer(st.single_timer)
            st.single_timer = None

            st.seq += 1
            st.pressed = True
            st.consumed = True
            st.last_down_ts = now

            logging.info("BUTTON_DOWN button=%s pin=%s second=1", st.button, st.pin)
            logging.info("BUTTON_GESTURE button=%s pin=%s gesture=double_press", st.button, st.pin)
            _enqueue_gesture(st.button, "double_press")
            return

        st.seq += 1
        seq = st.seq
        st.pressed = True
        st.consumed = False
        st.last_down_ts = now

        logging.info("BUTTON_DOWN button=%s pin=%s", st.button, st.pin)

        long_s = _ms("PHYSICAL_BUTTON_LONG_PRESS_MS", 800)
        if long_s > 0:
            st.long_timer = threading.Timer(long_s, _fire_long_if_still_pressed, args=(st.button, seq))
            st.long_timer.daemon = True
            st.long_timer.start()


def _on_up(st: ButtonState) -> None:
    with st.lock:
        if not st.pressed:
            return

        st.pressed = False
        st.last_up_ts = _now()
        _cancel_timer(st.long_timer)
        st.long_timer = None

        logging.info("BUTTON_UP button=%s pin=%s consumed=%r", st.button, st.pin, st.consumed)

        if st.consumed:
            return

        seq = st.seq
        double_s = _ms("PHYSICAL_BUTTON_DOUBLE_PRESS_WINDOW_MS", 500)
        if double_s <= 0:
            st.consumed = True
            logging.info("BUTTON_GESTURE button=%s pin=%s gesture=press", st.button, st.pin)
            _enqueue_gesture(st.button, "press")
            return

        st.single_timer = threading.Timer(double_s, _fire_single_if_not_consumed, args=(st.button, seq))
        st.single_timer.daemon = True
        st.single_timer.start()



def _settle_ms() -> float:
    """
    Additional software settle window after a pigpio edge callback.

    This is separate from pigpio's glitch filter. It lets us confirm that the
    line stayed in the new state for a short interval before treating it as a
    real press/release transition.
    """
    try:
        return max(1.0, float(_prefs("PHYSICAL_BUTTON_SETTLE_MS", 25)))
    except Exception:
        return 25.0


def _active_level() -> int:
    return 0 if _active_low() else 1


def _confirm_stable_transition(button: int, expected_level: int) -> None:
    st = _STATES.get(button)
    if not st or _PI is None:
        return

    try:
        actual_level = int(_PI.read(int(st.pin)))
    except Exception:
        logging.exception("BUTTON_CONFIRM_READ_FAIL button=%s pin=%s", button, getattr(st, "pin", None))
        return

    with st.lock:
        # Ignore stale timers / superseded candidate levels.
        if st.pending_level is None:
            return
        if int(st.pending_level) != int(expected_level):
            return

        st.pending_level = None
        st.settle_timer = None

    if actual_level != int(expected_level):
        logging.info(
            "BUTTON_SETTLE_REJECT button=%s pin=%s expected=%s actual=%s",
            st.button, st.pin, expected_level, actual_level
        )
        return

    is_pressed = int(actual_level) == int(_active_level())
    logging.info(
        "BUTTON_SETTLE_ACCEPT button=%s pin=%s level=%s pressed=%r",
        st.button, st.pin, actual_level, is_pressed
    )

    if is_pressed:
        _on_down(st)
    else:
        _on_up(st)


def _pigpio_callback(pin: int, level: int, tick: int) -> None:
    button = _PIN_TO_BUTTON.get(int(pin))
    if button is None:
        return

    st = _STATES.get(button)
    if not st:
        return

    if level == pigpio.TIMEOUT:
        return

    try:
        level = int(level)

        with st.lock:
            st.pending_level = level
            _cancel_timer(st.settle_timer)

            settle_s = _settle_ms() / 1000.0
            st.settle_timer = threading.Timer(
                settle_s,
                _confirm_stable_transition,
                args=(st.button, level),
            )
            st.settle_timer.daemon = True
            st.settle_timer.start()

        logging.info(
            "BUTTON_EDGE button=%s pin=%s level=%s settle_ms=%s",
            st.button,
            st.pin,
            level,
            _settle_ms(),
        )
    except Exception:
        logging.exception("BUTTON_CALLBACK_FAIL button=%s pin=%s level=%r", button, pin, level)


def _execute_button_action(button: int, gesture: str) -> None:
    if _ignore_while_handset_up() and _handset_up():
        logging.info("BUTTON_IGNORED_HANDSET_UP button=%s gesture=%s", button, gesture)
        return

    action = _gesture_action(button, gesture)
    commands = _normalize_action_to_commands(action)

    if not commands:
        logging.info("BUTTON_NO_ACTION button=%s gesture=%s", button, gesture)
        return

    if not callable(_COMMAND_EXECUTOR):
        logging.error("BUTTON_NO_EXECUTOR button=%s gesture=%s commands=%r", button, gesture, commands)
        return

    repeat, repeat_interval_s, max_repeats = _repeat_config_for_action(action)
    if gesture != "long_press":
        repeat = False

    def _run_once(iteration: int) -> None:
        for cmd in commands:
            try:
                logging.info(
                    "BUTTON_EXEC_BEGIN button=%s gesture=%s repeat=%r iteration=%s command=%r",
                    button, gesture, repeat, iteration, cmd
                )
                result = _COMMAND_EXECUTOR(cmd)
                logging.info(
                    "BUTTON_EXEC_OK button=%s gesture=%s repeat=%r iteration=%s command=%r result=%r",
                    button, gesture, repeat, iteration, cmd, result
                )
            except Exception:
                logging.exception(
                    "BUTTON_EXEC_FAIL button=%s gesture=%s repeat=%r iteration=%s command=%r",
                    button, gesture, repeat, iteration, cmd
                )

    if not repeat:
        _run_once(1)
        return

    logging.info(
        "BUTTON_HOLD_REPEAT_START button=%s gesture=%s interval=%.3f max_repeats=%s commands=%r",
        button, gesture, repeat_interval_s, max_repeats, commands
    )

    iteration = 1
    while iteration <= max_repeats:
        if not _button_still_pressed(button):
            break

        _run_once(iteration)

        if iteration >= max_repeats:
            break

        if not _sleep_until_repeat_or_release(button, repeat_interval_s):
            break

        iteration += 1

    logging.info(
        "BUTTON_HOLD_REPEAT_END button=%s gesture=%s iterations=%s still_pressed=%r",
        button, gesture, iteration, _button_still_pressed(button)
    )


def _worker() -> None:
    logging.info("BUTTON_WORKER_STARTED")
    while not _STOP_EVENT.is_set():
        try:
            item = _EVENT_Q.get(timeout=0.25)
        except queue.Empty:
            continue

        try:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "gesture":
                continue

            logging.info(
                "BUTTON_EVENT button=%s gesture=%s ts=%s",
                item.get("button"),
                item.get("gesture"),
                item.get("ts"),
            )

            _execute_button_action(int(item["button"]), str(item["gesture"]))
        except Exception:
            logging.exception("BUTTON_WORKER_ITEM_FAIL item=%r", item)


def start_physical_buttons(
    *,
    command_executor,
    handset_is_up: Optional[Callable[[], bool]] = None,
) -> bool:
    global _STARTED, _WORKER_THREAD, _COMMAND_EXECUTOR, _HANDSET_IS_UP, _PI

    if _STARTED:
        logging.info("PHYSICAL_BUTTONS_ALREADY_STARTED")
        return True

    if not _enabled():
        logging.info("PHYSICAL_BUTTONS_DISABLED")
        return False

    if _backend() != "pigpio":
        logging.error("PHYSICAL_BUTTONS_UNSUPPORTED_BACKEND backend=%r", _backend())
        return False

    pins = _pins()
    if not pins:
        logging.warning("PHYSICAL_BUTTONS_NO_PINS")
        return False

    if pigpio is None:
        logging.error("PHYSICAL_BUTTONS_PIGPIO_IMPORT_FAIL")
        return False

    host = _host()
    port = _port()

    pi = pigpio.pi(host, port)
    if not pi.connected:
        logging.error("PHYSICAL_BUTTONS_PIGPIO_CONNECT_FAIL host=%s port=%s", host, port)
        return False

    _PI = pi
    _COMMAND_EXECUTOR = command_executor
    _HANDSET_IS_UP = handset_is_up
    _STOP_EVENT.clear()

    glitch_us = int(float(_prefs("PHYSICAL_BUTTON_DEBOUNCE_MS", 40)) * 1000.0)

    try:
        for button, pin in sorted(pins.items()):
            st = ButtonState(button=int(button), pin=int(pin))
            _STATES[int(button)] = st
            _PIN_TO_BUTTON[int(pin)] = int(button)

            pi.set_mode(int(pin), pigpio.INPUT)

            if _pull_up():
                pi.set_pull_up_down(int(pin), pigpio.PUD_UP)
            else:
                pi.set_pull_up_down(int(pin), pigpio.PUD_OFF)

            # Ignore very short glitches at the daemon level.
            try:
                pi.set_glitch_filter(int(pin), max(0, glitch_us))
            except Exception:
                logging.exception("PHYSICAL_BUTTON_GLITCH_FILTER_FAIL button=%s pin=%s", button, pin)

            cb = pi.callback(int(pin), pigpio.EITHER_EDGE, _pigpio_callback)
            _CALLBACKS.append(cb)

            logging.info("PHYSICAL_BUTTON_REGISTERED button=%s pin=%s", button, pin)

    except Exception:
        logging.exception("PHYSICAL_BUTTONS_SETUP_FAIL")
        try:
            pi.stop()
        except Exception:
            pass
        _PI = None
        return False

    _WORKER_THREAD = threading.Thread(target=_worker, daemon=True, name="physical_buttons")
    _WORKER_THREAD.start()

    _STARTED = True
    logging.info(
        "PHYSICAL_BUTTONS_STARTED backend=pigpio host=%s port=%s buttons=%s double_ms=%s long_ms=%s debounce_ms=%s active_low=%r pull_up=%r",
        host,
        port,
        sorted(pins.keys()),
        _prefs("PHYSICAL_BUTTON_DOUBLE_PRESS_WINDOW_MS", 500),
        _prefs("PHYSICAL_BUTTON_LONG_PRESS_MS", 800),
        _prefs("PHYSICAL_BUTTON_DEBOUNCE_MS", 40),
        _active_low(),
        _pull_up(),
    )
    return True


def stop_physical_buttons() -> None:
    global _STARTED, _PI

    _STOP_EVENT.set()

    for st in list(_STATES.values()):
        with st.lock:
            _cancel_timer(st.long_timer)
            _cancel_timer(st.single_timer)
            _cancel_timer(st.settle_timer)

    for cb in list(_CALLBACKS):
        try:
            cb.cancel()
        except Exception:
            pass
    _CALLBACKS.clear()

    if _PI is not None:
        try:
            _PI.stop()
        except Exception:
            pass
        _PI = None

    _STARTED = False
    logging.info("PHYSICAL_BUTTONS_STOPPED")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    def _print_executor(command: str):
        print(f"COMMAND: {command}")
        return {"printed": command}

    start_physical_buttons(command_executor=_print_executor)
    print("Physical button monitor running via pigpio. Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_physical_buttons()

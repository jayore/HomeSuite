"""Pure configuration rules for the maintained push-to-talk GPIO input."""

from __future__ import annotations


LISTEN_LEVELS = {"low": 0, "high": 1}
END_BEHAVIORS = {"cancel", "submit"}


def normalize_listen_level(value) -> str:
    level = str(value or "low").strip().lower()
    if level not in LISTEN_LEVELS:
        raise ValueError("PTT_LISTEN_LEVEL must be 'low' or 'high'")
    return level


def listen_level_value(value) -> int:
    return LISTEN_LEVELS[normalize_listen_level(value)]


def input_is_listening(raw_level, configured_level) -> bool:
    return int(raw_level) == listen_level_value(configured_level)


def normalize_end_behavior(value) -> str:
    behavior = str(value or "cancel").strip().lower()
    if behavior not in END_BEHAVIORS:
        raise ValueError("PTT_END_BEHAVIOR must be 'cancel' or 'submit'")
    return behavior


def exit_cancels_capture(value) -> bool:
    return normalize_end_behavior(value) == "cancel"

from __future__ import annotations

import logging
import re
from dataclasses import dataclass


@dataclass
class InteractionResult:
    handled: bool
    action_occurred: bool
    response_text: str
    source: str  # device_text | device_confirm | chatgpt | fallback


def _clean_text(text: str) -> str:
    return (text or "").strip()


def _looks_like_joke_request(gpio_ptt, text: str) -> bool:
    try:
        fn = getattr(gpio_ptt, "_looks_like_joke_request", None)
        if callable(fn):
            return bool(fn(text))
    except Exception:
        pass
    return False


def _looks_like_chatgpt_intent(gpio_ptt, text: str) -> bool:
    try:
        fn = getattr(gpio_ptt, "_looks_like_chatgpt_intent", None)
        if callable(fn):
            return bool(fn(text))
    except Exception:
        pass
    return False


def _is_user_facing_device_text(text: str) -> bool:
    t = _clean_text(text)
    if not t:
        return False

    tl = t.lower()

    # Internal trace/claim/debug-ish text should not surface in ppchat-style UX.
    if tl.startswith("claim:"):
        return False
    if " no match for " in tl:
        return False
    if tl.startswith("debug:"):
        return False
    if tl.startswith("ha_stub call:"):
        return False
    if tl.startswith("ha_blocked_write"):
        return False

    return True


def _make_context_aware_confirmation(gpio_ptt, text: str) -> str:
    try:
        ctx = getattr(gpio_ptt, "get_text_confirm_context", lambda: {})() or {}
    except Exception:
        ctx = {}

    kind = str(ctx.get("kind") or "").strip().lower()
    label = str(ctx.get("label") or "").strip()
    value = ctx.get("value")
    verb = str(ctx.get("verb") or "").strip().lower()

    if kind == "transport" and label and verb:
        if verb == "paused":
            return f"Paused the {label}."
        if verb == "resumed":
            return f"Resumed the {label}."
        if verb == "stopped":
            return f"Stopped the {label}."

    if kind == "volume" and label and value is not None:
        return f"Set {label} to {value}."

    if kind == "brightness" and label and value is not None:
        return f"{label.title()} set to {value}."

    return _make_text_confirmation(text)


def _make_text_confirmation(text: str) -> str:
    t = _clean_text(text)
    tl = t.lower()

    m = re.match(r"^turn on\s+(.+)$", tl)
    if m:
        return f"Turned on {m.group(1).strip()}."

    m = re.match(r"^turn off\s+(.+)$", tl)
    if m:
        return f"Turned off {m.group(1).strip()}."

    if re.search(
        r"\b(?:bright(?:er|en)|(?:more|get)\s+bright(?:er)?|make\s+(?:it\s+|the\s+\S+\s+)?bright(?:er)?|"
        r"(?:turn|crank)\s+(?:(?:it|the\s+\S+)\s+)?up\s+(?:the\s+)?bright(?:ness)?|"
        r"brightness\s+up|increase\s+(?:the\s+)?bright(?:ness)?|up\s+(?:the\s+)?bright(?:ness)?)\b",
        tl,
    ):
        return "Increased brightness."

    if re.search(
        r"\b(?:dim(?:mer)?|(?:less|more)\s+dim(?:mer)?|less\s+bright(?:er)?|(?:more|get)\s+dim(?:mer)?|"
        r"make\s+(?:it\s+|the\s+\S+\s+)?dim(?:mer)?|"
        r"(?:turn|crank)\s+(?:(?:it|the\s+\S+)\s+)?down\s+(?:the\s+)?bright(?:ness)?|"
        r"brightness\s+down|decrease\s+(?:the\s+)?bright(?:ness)?|"
        r"down\s+(?:the\s+)?bright(?:ness)?|lower\s+(?:the\s+)?bright(?:ness)?)\b",
        tl,
    ):
        return "Decreased brightness."

    m = re.match(r"^set\s+brightness\s+to\s+(.+)$", tl)
    if m:
        return f"Brightness set to {m.group(1).strip()}."

    m = re.match(r"^set\s+brightness\s+(.+)$", tl)
    if m:
        return f"Brightness set to {m.group(1).strip()}."

    m = re.match(r"^brightness\s+(.+)$", tl)
    if m:
        return f"Brightness set to {m.group(1).strip()}."

    m = re.match(r"^set\s+(.+?)\s+brightness\s+to\s+(.+)$", tl)
    if m:
        target = m.group(1).strip()
        value = m.group(2).strip()
        return f"{target.title()} brightness set to {value}."

    if tl == "volume up":
        return "Increased volume."

    if tl == "volume down":
        return "Decreased volume."

    if re.search(r"\b(?:toggle\s+mute|mute\s+toggle|mute\s+unmute|unmute\s+mute|toggle\s+muting)\b", tl):
        return "Toggled mute."

    if re.fullmatch(r"unmute(?:\s+.+)?", tl):
        return "Unmuted."

    if re.fullmatch(r"mute(?:\s+.+)?", tl):
        return "Muted."

    m = re.match(r"^set\s+volume\s+to\s+(.+)$", tl)
    if m:
        return f"Volume set to {m.group(1).strip()}."

    m = re.match(r"^volume\s+(.+)$", tl)
    if m:
        return f"Volume set to {m.group(1).strip()}."

    m = re.match(r"^set\s+(.+?)\s+to\s+(.+)$", tl)
    if m:
        target = m.group(1).strip()
        value = m.group(2).strip()
        return f"{target.title()} set to {value}."

    m = re.match(r"^announce\s+(.+)$", t, flags=re.IGNORECASE)
    if m:
        return f"Announced: {m.group(1).strip()}."

    m = re.match(r"^watch\s+(.+)$", tl)
    if m:
        return f"Playing {m.group(1).strip()}."

    m = re.match(r"^play\s+(.+)$", tl)
    if m:
        target = m.group(1).strip()
        return f"Playing {target}."

    if re.fullmatch(r"switch\s+to\s+tv\s+audio", tl):
        return "Switched to TV audio."

    m = re.match(r"^add\s+(.+)$", tl)
    if m:
        return f"Added {m.group(1).strip()}."

    m = re.match(r"^remove\s+(.+)$", tl)
    if m:
        return f"Removed {m.group(1).strip()}."

    m = re.match(r"^ungroup\s+(.+)$", tl)
    if m:
        return f"Ungrouped {m.group(1).strip()}."

    m = re.match(r"^group\s+(.+)$", tl)
    if m:
        return f"Grouped {m.group(1).strip()}."

    if tl == "pause":
        return "Paused."

    if tl in ("resume", "play"):
        return "Resumed playback."

    if tl == "stop":
        return "Stopped."

    if tl in ("toggle play pause",):
        return "Toggled playback."

    # Scene/script-like shortcut fallback
    if tl and not re.search(r"\b(what|why|how|when|where|who)\b", tl):
        return f"Set {t}."

    return "Okay."


def handle_text_interaction(gpio_ptt, text: str) -> InteractionResult:
    text = _clean_text(text)
    if not text:
        return InteractionResult(
            handled=False,
            action_occurred=False,
            response_text="Say something.",
            source="fallback",
        )

    try:
        gpio_ptt._ACTION_OCCURRED = False
    except Exception:
        pass
    try:
        import command_dispatch as _cd
        _cd._ACTION_OCCURRED = False
    except Exception:
        pass

    try:
        clear_fn = getattr(gpio_ptt, "clear_text_confirm_context", None)
        if callable(clear_fn):
            clear_fn()
    except Exception:
        pass

    device_response = None
    try:
        device_response = gpio_ptt.process_device_commands(text)
    except Exception as e:
        return InteractionResult(
            handled=True,
            action_occurred=False,
            response_text=f"Error while processing command: {e}",
            source="fallback",
        )

    action_occurred = bool(getattr(gpio_ptt, "_ACTION_OCCURRED", False))
    if not action_occurred:
        try:
            import command_dispatch as _cd
            action_occurred = bool(_cd._ACTION_OCCURRED)
        except Exception:
            pass
    response_text = _clean_text(device_response or "")

    # Informational / explicit text returned from device-command layer
    if response_text and _is_user_facing_device_text(response_text):
        inject_into_history(text, response_text)
        return InteractionResult(
            handled=True,
            action_occurred=action_occurred,
            response_text=response_text,
            source="device_text",
        )

    # Silent success (or suppressed dev-ish text) → generate readable text confirmation
    if action_occurred:
        return InteractionResult(
            handled=True,
            action_occurred=True,
            response_text=_make_context_aware_confirmation(gpio_ptt, text),
            source="device_confirm",
        )

    # ChatGPT fallback path
    if _looks_like_chatgpt_intent(gpio_ptt, text):
        try:
            if _looks_like_joke_request(gpio_ptt, text):
                reply = _clean_text(gpio_ptt.get_chatgpt_joke_response(text))
            else:
                reply = _clean_text(gpio_ptt.get_chatgpt_response(text))
        except Exception as e:
            return InteractionResult(
                handled=True,
                action_occurred=False,
                response_text=f"I couldn't get a response right now: {e}",
                source="fallback",
            )

        if reply:
            return InteractionResult(
                handled=True,
                action_occurred=False,
                response_text=reply,
                source="chatgpt",
            )

    return InteractionResult(
        handled=False,
        action_occurred=False,
        response_text="I didn't understand that.",
        source="fallback",
    )


# =========================
# CONVERSATION HISTORY
# =========================

MAX_HISTORY_MESSAGES = 20

_BASE_SYSTEM_MESSAGE = {
    "role": "system",
    "content": "You are a helpful assistant that can answer questions concisely and control smart home devices.",
}

conversation_history: list = [_BASE_SYSTEM_MESSAGE.copy()]


def trim_history() -> None:
    global conversation_history
    if len(conversation_history) <= 1 + MAX_HISTORY_MESSAGES:
        return
    system = conversation_history[0]
    tail = conversation_history[-MAX_HISTORY_MESSAGES:]
    conversation_history = [system] + tail


def reset_history() -> None:
    global conversation_history
    conversation_history = [_BASE_SYSTEM_MESSAGE.copy()]


def inject_into_history(
    user_text: str,
    assistant_text: str,
    *,
    force: bool = False,
    assistant_context_text: str | None = None,
) -> None:
    """
    Inject a deterministic spoken response into conversation_history so the AI
    can answer follow-up questions that reference it.

    Examples:
      "What's the weather in Tampa?" (deterministic) → "What time is it there?"
      "What time is it in Tokyo?" (deterministic)   → "And what's the weather?"

    Only injects responses with substantive content by default. Short action
    confirmations ("Okay.", "Playing X.") are skipped since device follow-ups
    are handled by the deterministic entity memory system, not AI history.

    Some deterministic answers are short but still important context, such as a
    now-playing title. Callers can force injection and optionally provide a
    clearer assistant_context_text for history without changing the spoken text.
    """
    if not user_text or not assistant_text:
        return
    history_assistant_text = assistant_context_text or assistant_text
    # Skip short confirmations — anything under ~6 words is probably "Okay." or
    # "Setting side lamp to blue." which doesn't need to be in AI context.
    if not force and len(history_assistant_text.split()) < 6:
        return
    conversation_history.append({"role": "user", "content": user_text})
    conversation_history.append({"role": "assistant", "content": history_assistant_text})
    trim_history()
    logging.info(
        "HISTORY_INJECT%s: user=%r assistant=%r",
        "_FORCED" if force else "",
        user_text[:60],
        history_assistant_text[:60],
    )

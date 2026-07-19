"""Resolve a transcript into device action, conversation, or fallback output.

This module is the policy layer after transcription. It gives deterministic
device handlers the first appropriate opportunity, delegates conversational
requests to ChatGPT, and returns a normalized ``InteractionResult`` describing
both user-facing text and whether a real action occurred. Trigger mechanics
such as PTT and wakeword capture deliberately stay outside this module.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass

from app_config import CHATGPT_CONTINUATION_WINDOW_SECONDS, INTERACTION_CANCEL_PHRASES
from dialogue_state import current_scope_id, forget_intent_frame, forget_referents
from request_context import get_current_request_context
from semantic_router import RouteOutcome, RouteResult, route_utterance


@dataclass
class InteractionResult:
    """Outcome consumed by the runtime's tone, speech, and logging decisions."""
    handled: bool
    action_occurred: bool
    response_text: str
    source: str  # device_text | device_confirm | chatgpt | fallback


def _clean_text(text: str) -> str:
    return (text or "").strip()


def _normalize_cancel_text(text: str) -> str:
    value = _clean_text(text).lower().replace("’", "'")
    value = re.sub(r"[^a-z0-9'\s]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"^please\s+", "", value).strip()
    value = re.sub(r"\s+please$", "", value).strip()
    return value


def is_interaction_cancel(text: str) -> bool:
    """Return True only for an exact, configured dismissal phrase."""
    normalized = _normalize_cancel_text(text)
    if not normalized:
        return False
    phrases = set()
    for phrase in (INTERACTION_CANCEL_PHRASES or ()):
        candidate = _normalize_cancel_text(phrase)
        if candidate:
            phrases.add(candidate)
    return normalized in phrases


_EXPLICIT_JOKE_RE = re.compile(r"\b(?:joke|something\s+funny|make\s+me\s+laugh)\b")
_JOKE_FOLLOWUP_RE = re.compile(
    r"(?:(?:tell|give)\s+me\s+)?"
    r"(?:another(?:\s+one)?|one\s+more|more|again|a\s+different\s+one)"
)


def _normalize_joke_text(text: str) -> str:
    value = _clean_text(text).lower().replace("’", "'")
    value = re.sub(r"[^a-z0-9'\s]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def looks_like_joke_request(text: str, *, now_ts: float | None = None) -> bool:
    """Recognize explicit jokes and source-scoped immediate follow-ups."""
    normalized = _normalize_joke_text(text)
    if not normalized:
        return False
    if _EXPLICIT_JOKE_RE.search(normalized):
        return True
    return bool(
        _JOKE_FOLLOWUP_RE.fullmatch(normalized)
        and is_recent_joke_turn(now_ts=now_ts)
    )


def _looks_like_joke_request(gpio_ptt, text: str) -> bool:
    if looks_like_joke_request(text):
        return True
    try:
        fn = getattr(gpio_ptt, "_looks_like_joke_request", None)
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


def _effective_confirmation_text(gpio_ptt, original_text: str) -> str:
    """Prefer the command that routing actually executed over its short follow-up."""
    try:
        resolver = getattr(gpio_ptt, "get_effective_command_text", None)
        if callable(resolver):
            effective = _clean_text(resolver(original_text))
            if effective:
                return effective
    except Exception:
        pass
    return _clean_text(original_text)


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

    if re.fullmatch(
        r"(?:volume\s+up|louder|make\s+.+?\s+louder|"
        r"increase\s+(?:.+?\s+)?volume(?:\s+by\s+\d{1,3})?)",
        tl,
    ):
        return "Increased volume."

    if re.fullmatch(
        r"(?:volume\s+down|quieter|make\s+.+?\s+quieter|"
        r"decrease\s+(?:.+?\s+)?volume(?:\s+by\s+\d{1,3})?)",
        tl,
    ):
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
    """Route one normalized transcript and report text plus action semantics."""
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

    if is_interaction_cancel(text):
        from clarification_controls import cancel_pending_clarification
        from confirmation_controls import cancel_pending_confirmation

        clear_joke_turn()
        cancel_pending_clarification()
        cancel_pending_confirmation()
        forget_referents(capability="pending_interaction")
        logging.info("INTERACTION_CANCEL source=text text=%r", text)
        return InteractionResult(
            handled=True,
            action_occurred=False,
            response_text="",
            source="cancelled",
        )

    device_response = None
    try:
        device_response = gpio_ptt.process_device_commands(text)
    except Exception as e:
        forget_intent_frame()
        clear_joke_turn()
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
        clear_joke_turn()
        inject_device_response_history(text, device_response)
        return InteractionResult(
            handled=True,
            action_occurred=action_occurred,
            response_text=response_text,
            source="device_text",
        )

    # Silent success (or suppressed dev-ish text) → generate readable text confirmation
    if action_occurred:
        clear_joke_turn()
        confirmation_text = _effective_confirmation_text(gpio_ptt, text)
        if confirmation_text != text:
            logging.info(
                "INTERACTION_CONFIRM_EFFECTIVE_TEXT input=%r effective=%r",
                text,
                confirmation_text,
            )
        return InteractionResult(
            handled=True,
            action_occurred=True,
            response_text=_make_context_aware_confirmation(
                gpio_ptt,
                confirmation_text,
            ),
            source="device_confirm",
        )

    # Deterministic handlers have declined the request. Apply the same shared,
    # source-scoped semantic policy used by local voice interactions.
    route_result = route_unhandled_utterance(text)
    if route_result.outcome == RouteOutcome.CHATGPT:
        joke_request = _looks_like_joke_request(gpio_ptt, text)
        try:
            if joke_request:
                reply = _clean_text(gpio_ptt.get_chatgpt_joke_response(text))
            else:
                clear_joke_turn()
                reply = _clean_text(gpio_ptt.get_chatgpt_response(text))
        except Exception as e:
            forget_intent_frame()
            clear_joke_turn()
            return InteractionResult(
                handled=True,
                action_occurred=False,
                response_text=f"I couldn't get a response right now: {e}",
                source="fallback",
            )

        if reply:
            if joke_request:
                record_joke_turn(text, reply)
            else:
                mark_chatgpt_turn()
            # Once a turn enters open-ended AI conversation, short phrases such
            # as "what about Thursday?" belong to that conversation rather than
            # an older deterministic intent frame. AI history remains intact.
            forget_intent_frame()
            return InteractionResult(
                handled=True,
                action_occurred=False,
                response_text=reply,
                source="chatgpt",
            )

    forget_intent_frame()
    clear_joke_turn()
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
    "content": (
        "You are a helpful voice assistant that can answer questions concisely "
        "after Home Suite's deterministic command handlers have declined a request. "
        "You cannot execute or confirm smart-home, media, scheduling, or service "
        "actions; never imply that an action occurred. Keep answers natural when spoken aloud. "
        "When web search is available, use it for current or time-sensitive facts. "
        "Do not read URLs aloud; name important publications briefly when useful."
    ),
}

conversation_history: list = [_BASE_SYSTEM_MESSAGE.copy()]
_HISTORY_LOCK = threading.RLock()
_HISTORIES_BY_SCOPE: dict[str, list] = {"process": conversation_history}
_LAST_CHATGPT_TS_BY_SCOPE: dict[str, float] = {}
_LAST_JOKE_TS_BY_SCOPE: dict[str, float] = {}


def _scope_key(scope_id: str | None = None) -> str:
    return str(scope_id or current_scope_id()).strip() or "process"


def get_last_chatgpt_ts(scope_id: str | None = None) -> float | None:
    """Return the most recent successful AI turn for one continuity scope."""
    with _HISTORY_LOCK:
        return _LAST_CHATGPT_TS_BY_SCOPE.get(_scope_key(scope_id))


def mark_chatgpt_turn(
    *,
    now_ts: float | None = None,
    scope_id: str | None = None,
) -> None:
    """Record an AI turn without leaking recency into another source."""
    timestamp = time.time() if now_ts is None else float(now_ts)
    with _HISTORY_LOCK:
        _LAST_CHATGPT_TS_BY_SCOPE[_scope_key(scope_id)] = timestamp


def get_last_joke_ts(scope_id: str | None = None) -> float | None:
    """Return the most recent dedicated joke turn for one continuity scope."""
    with _HISTORY_LOCK:
        return _LAST_JOKE_TS_BY_SCOPE.get(_scope_key(scope_id))


def mark_joke_turn(
    *,
    now_ts: float | None = None,
    scope_id: str | None = None,
) -> None:
    timestamp = time.time() if now_ts is None else float(now_ts)
    with _HISTORY_LOCK:
        _LAST_JOKE_TS_BY_SCOPE[_scope_key(scope_id)] = timestamp


def clear_joke_turn(scope_id: str | None = None) -> None:
    with _HISTORY_LOCK:
        _LAST_JOKE_TS_BY_SCOPE.pop(_scope_key(scope_id), None)


def is_recent_joke_turn(
    *,
    now_ts: float | None = None,
    scope_id: str | None = None,
) -> bool:
    now = time.time() if now_ts is None else float(now_ts)
    last = get_last_joke_ts(scope_id)
    if last is None:
        return False
    try:
        window = max(0.0, float(CHATGPT_CONTINUATION_WINDOW_SECONDS))
    except (TypeError, ValueError):
        window = 120.0
    return 0.0 <= now - last <= window


def _current_source_type() -> str:
    ctx = get_current_request_context()
    return str(
        getattr(ctx, "source_type", None)
        or getattr(ctx, "origin", None)
        or "text"
    ).strip().lower()


def route_unhandled_utterance(
    text: str,
    *,
    now_ts: float | None = None,
    source_type: str | None = None,
) -> RouteResult:
    """Apply shared fallback policy using this source's own AI recency."""
    timestamp = time.time() if now_ts is None else float(now_ts)
    return route_utterance(
        text=text,
        now_ts=timestamp,
        last_chatgpt_ts=get_last_chatgpt_ts(),
        source_type=source_type or _current_source_type(),
    )


def _history_for_scope(scope_id: str | None = None) -> list:
    scope = str(scope_id or current_scope_id()).strip() or "process"
    with _HISTORY_LOCK:
        history = _HISTORIES_BY_SCOPE.get(scope)
        if history is None:
            history = [_BASE_SYSTEM_MESSAGE.copy()]
            _HISTORIES_BY_SCOPE[scope] = history
        return history


def get_history_snapshot(scope_id: str | None = None) -> list:
    with _HISTORY_LOCK:
        return [dict(message) for message in _history_for_scope(scope_id)]


def append_history_message(role: str, content: str, scope_id: str | None = None) -> None:
    if not role or not content:
        return
    with _HISTORY_LOCK:
        _history_for_scope(scope_id).append({"role": role, "content": content})
        trim_history(scope_id)


def record_joke_turn(
    user_text: str,
    assistant_text: str,
    *,
    now_ts: float | None = None,
    scope_id: str | None = None,
) -> None:
    """Bridge a dedicated joke response into normal scoped conversation."""
    append_history_message("user", user_text, scope_id)
    append_history_message("assistant", assistant_text, scope_id)
    mark_joke_turn(now_ts=now_ts, scope_id=scope_id)
    mark_chatgpt_turn(now_ts=now_ts, scope_id=scope_id)
    logging.info("JOKE_TURN_RECORDED scope=%r", _scope_key(scope_id))


def trim_history(scope_id: str | None = None) -> None:
    with _HISTORY_LOCK:
        history = _history_for_scope(scope_id)
        if len(history) <= 1 + MAX_HISTORY_MESSAGES:
            return
        system = history[0]
        tail = history[-MAX_HISTORY_MESSAGES:]
        history[:] = [system] + tail


def reset_history(scope_id: str | None = None, *, all_scopes: bool = False) -> None:
    with _HISTORY_LOCK:
        if all_scopes:
            _HISTORIES_BY_SCOPE.clear()
            _LAST_CHATGPT_TS_BY_SCOPE.clear()
            _LAST_JOKE_TS_BY_SCOPE.clear()
            conversation_history[:] = [_BASE_SYSTEM_MESSAGE.copy()]
            _HISTORIES_BY_SCOPE["process"] = conversation_history
            return
        scope = _scope_key(scope_id)
        history = _history_for_scope(scope)
        history[:] = [_BASE_SYSTEM_MESSAGE.copy()]
        _LAST_CHATGPT_TS_BY_SCOPE.pop(scope, None)
        _LAST_JOKE_TS_BY_SCOPE.pop(scope, None)


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
    with _HISTORY_LOCK:
        history = _history_for_scope()
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": history_assistant_text})
        trim_history()
    logging.info(
        "HISTORY_INJECT%s: user=%r assistant=%r",
        "_FORCED" if force else "",
        user_text[:60],
        history_assistant_text[:60],
    )


def inject_device_response_history(user_text: str, assistant_text: str) -> None:
    """Bridge deterministic information into AI history with domain-aware context."""
    from response_context import consume_response_context

    response_context = consume_response_context() or {}
    context_kind = str(response_context.get("kind") or "").strip().lower()
    context_data = response_context.get("data") or {}

    try:
        from now_playing_controls import (
            format_now_playing_history_context,
            is_now_playing_query,
        )

        now_playing = context_kind == "now_playing" or bool(is_now_playing_query(user_text))
    except Exception:
        now_playing = context_kind == "now_playing"

    assistant_context_text = None
    if now_playing:
        try:
            assistant_context_text = format_now_playing_history_context(
                context_data,
                str(assistant_text or "").strip(),
            )
        except Exception:
            assistant_context_text = f"Currently playing: {assistant_text}"

    inject_into_history(
        user_text,
        assistant_text,
        force=now_playing,
        assistant_context_text=assistant_context_text,
    )

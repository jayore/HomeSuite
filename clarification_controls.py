"""Source-scoped deterministic clarification for ambiguous commands.

A clarification stores a small set of human labels and ordinary HomeSuite
commands. A short selection replays the chosen command through the regular
dispatcher, where live entity resolution, capability checks, and confirmation
policy run again. The clarification itself never authorizes a device write.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re
import uuid
from typing import Callable, Iterable, Optional

from dialogue_state import forget_referent, remember_referent, resolve_referent
from request_context import get_current_request_context


_CLARIFICATION_KIND = "command_clarification"
_CLARIFICATION_CAPABILITY = "clarify_action"
_PENDING_CAPABILITY = "pending_interaction"
_NEGATIVE = {"cancel", "cancel that", "never mind", "nevermind", "neither", "none"}
_NEW_TURN_PREFIX = re.compile(
    r"^(?:turn|set|switch|power|toggle|lock|unlock|open|close|play|pause|stop|"
    r"start|cancel|delete|what|when|where|why|how|who|is|are|do|can|could|would)\b"
)


@dataclass(frozen=True)
class ClarificationOption:
    label: str
    command: str
    aliases: tuple[str, ...] = ()

    def to_data(self) -> dict:
        normalized_aliases = {_norm(self.label)}
        normalized_aliases.update(_norm(alias) for alias in self.aliases)
        return {
            "label": str(self.label or "").strip(),
            "command": str(self.command or "").strip(),
            "aliases": sorted(alias for alias in normalized_aliases if alias),
        }


def _norm(value: str) -> str:
    text = str(value or "").strip().lower().replace("’", "'")
    text = re.sub(r"[^a-z0-9'\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _selection_norm(value: str) -> str:
    text = _norm(value)
    text = re.sub(r"^(?:no\s+|actually\s+)", "", text).strip()
    text = re.sub(r"^(?:i\s+mean|i\s+meant|choose|pick)\s+", "", text).strip()
    text = re.sub(r"^(?:the\s+)", "", text).strip()
    text = re.sub(r"\s+(?:one|please)$", "", text).strip()
    return text


def _clarification_scope_id() -> str:
    ctx = get_current_request_context()
    source_id = str(getattr(ctx, "source_id", None) or "").strip()
    if not source_id:
        return "clarification:process"
    return f"clarification:source:{_norm(source_id).replace(' ', '_')}"


def _ttl_seconds() -> float:
    try:
        import app_config

        return max(5.0, float(getattr(app_config, "COMMAND_CLARIFICATION_TTL_SECONDS", 45)))
    except Exception:
        return 45.0


def request_command_clarification(
    *,
    prompt: str,
    options: Iterable[ClarificationOption],
    original_command: str,
) -> Optional[str]:
    """Store a bounded set of replayable options and return the prompt."""
    rows = [option.to_data() for option in options]
    rows = [row for row in rows if row["label"] and row["command"] and row["aliases"]]
    if len(rows) < 2:
        return None

    clarification_id = str(uuid.uuid4())[:8]
    remember_referent(
        _CLARIFICATION_KIND,
        clarification_id,
        label=str(prompt or "").strip(),
        capabilities={_CLARIFICATION_CAPABILITY, _PENDING_CAPABILITY},
        data={
            "id": clarification_id,
            "prompt": str(prompt or "").strip(),
            "original_command": str(original_command or "").strip(),
            "options": rows,
        },
        ttl_seconds=_ttl_seconds(),
        scope_id=_clarification_scope_id(),
        source="clarification",
    )
    logging.info(
        "COMMAND_CLARIFICATION_REQUEST id=%s options=%s command=%r",
        clarification_id,
        [row["label"] for row in rows],
        original_command,
    )
    return str(prompt or "").strip()


def pending_clarification() -> Optional[dict]:
    return resolve_referent(
        kinds={_CLARIFICATION_KIND},
        capability=_CLARIFICATION_CAPABILITY,
        scope_id=_clarification_scope_id(),
    )


def cancel_pending_clarification() -> bool:
    entry = pending_clarification()
    if not entry:
        return False
    forget_referent(
        _CLARIFICATION_KIND,
        key=str(entry.get("key") or ""),
        scope_id=_clarification_scope_id(),
    )
    return True


def _retry_prompt(options: list[dict]) -> str:
    labels = [str(row.get("label") or "").strip() for row in options]
    labels = [label for label in labels if label]
    if len(labels) == 2:
        choices = f"{labels[0]} or {labels[1]}"
    else:
        choices = ", ".join(labels[:-1]) + f", or {labels[-1]}"
    return f"Which one: {choices}?"


def handle_clarification_controls(
    *,
    tl: str,
    execute_command: Callable[[str], Optional[str]],
) -> Optional[str]:
    """Resolve a short option reply before ordinary command routing."""
    entry = pending_clarification()
    if not entry:
        return None

    reply = _selection_norm(tl)
    entry_id = str(entry.get("key") or "")
    data = dict(entry.get("data") or {})
    options = [dict(row) for row in (data.get("options") or ()) if isinstance(row, dict)]

    if reply in _NEGATIVE:
        cancel_pending_clarification()
        logging.info("COMMAND_CLARIFICATION_CANCEL id=%s", entry_id)
        return ""

    matches = []
    for row in options:
        aliases = {_selection_norm(alias) for alias in (row.get("aliases") or ())}
        if reply and reply in aliases:
            matches.append(row)

    if len(matches) == 1:
        cancel_pending_clarification()
        command = str(matches[0].get("command") or "").strip()
        logging.info(
            "COMMAND_CLARIFICATION_RESOLVE id=%s label=%r command=%r",
            entry_id,
            matches[0].get("label"),
            command,
        )
        try:
            response = execute_command(command)
        except Exception:
            logging.exception("COMMAND_CLARIFICATION_REPLAY_FAIL id=%s", entry_id)
            return "I couldn't complete that action."
        return response if response is not None else "I couldn't find that device anymore."

    # A new complete command supersedes this pending question. Short noun-like
    # replies get one concise retry because they were probably a selection.
    if _NEW_TURN_PREFIX.match(reply) or len(reply.split()) > 5:
        cancel_pending_clarification()
        logging.info(
            "COMMAND_CLARIFICATION_SUPERSEDE id=%s replacement=%r",
            entry_id,
            tl,
        )
        return None

    logging.info("COMMAND_CLARIFICATION_RETRY id=%s reply=%r", entry_id, tl)
    return _retry_prompt(options)

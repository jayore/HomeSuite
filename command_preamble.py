"""
Command preamble: routing-repair + request-context resolution.

Extracted from gpio_ptt.process_device_commands so the function body reads
as a routing pipeline rather than starting with ~80 lines of text shaping
and context setup. Pure helpers — no gpio_ptt-runtime state, no closures.

The CommandRequestCtx dataclass is the shared shape that future extractions
(e.g. the transport disambiguation block) will also consume.

NOT moved here:
  * normalize call + globals()['_LAST_STT_NORM_OUT'] write — those must stay
    in main.py because globals() writes target the owning module.
  * apple-tv hard override block — stays inline in process_device_commands.
"""

from dataclasses import dataclass
from typing import Optional
import logging
import re

from phonetic_repairs import (
    should_apply_routing_repairs as _should_apply_routing_repairs,
    apply_phonetic_routing_repairs as _apply_phonetic_routing_repairs,
)
from app_config import SONOS_PLAYERS
from room_context import (
    _extract_explicit_room_id_from_text,
    _request_default_tv_context,
)


@dataclass
class CommandRequestCtx:
    explicit_room_id: Optional[str] = None
    request_tv_room_id: Optional[str] = None
    request_tv_entity: Optional[str] = None
    request_tv_remote: Optional[str] = None
    request_tv_on_scene: Optional[str] = None
    request_plex_client_name: Optional[str] = None
    request_plex_launch_script: Optional[str] = None


def apply_routing_repairs(text: str, repair_pass: int) -> str:
    """Phonetic pass 1: ANSI-arrow + safe routing/command repairs.

    Returns the (possibly repaired) text. Skips work when repair_pass != 1
    so caller-side recursion doesn't re-repair already-repaired text.
    """
    text = (text or "").strip()
    if repair_pass != 1:
        return text

    try:
        _tl0 = text.lower()
        # Match real ANSI arrow sequences: ESC [ a/b/c/d (lowercased earlier in pipeline)
        _m0 = re.fullmatch("\x1b\\[([abcd])", _tl0)
        if _m0:
            _mapped = {"a": "up", "b": "down", "c": "right", "d": "left"}[_m0.group(1)]
            logging.info("UTTERANCE_REPAIR_ROUTING: %r -> %r (ansi_arrow)", text, _mapped)
            text = _mapped
    except Exception:
        pass

    if _should_apply_routing_repairs(text, sonos_players=SONOS_PLAYERS):
        _r = _apply_phonetic_routing_repairs(text, sonos_players=SONOS_PLAYERS)
        if _r != text:
            logging.info("UTTERANCE_REPAIR_ROUTING: %r -> %r", text, _r)
        text = _r

    return text


def resolve_request_context(tl: str) -> CommandRequestCtx:
    """Resolve per-request context from the normalized lowercase utterance.

    * Extracts an explicit room mention (if any)
    * Drops the explicit-room override for move/swap/unswap intents, since
      rooms in those commands are operation targets, not request context.
      Without this, "here" inside handle_sonos_controls resolves to the
      destination and trips the source-vs-destination equality check.
    * Resolves request-aware TV context (entity, remote, on-scene, Plex)
      using the (possibly dropped) explicit room as the override.
    """
    try:
        explicit_room_id = _extract_explicit_room_id_from_text(tl)
    except Exception:
        explicit_room_id = None

    if explicit_room_id and re.match(r"^(move|swap|unswap)\b", tl):
        try:
            logging.info(
                "REQUEST_EXPLICIT_ROOM_OVERRIDE_SKIP_SWAP text=%r dropped_room_id=%r",
                tl,
                explicit_room_id,
            )
        except Exception:
            pass
        explicit_room_id = None

    try:
        if explicit_room_id:
            logging.info(
                "REQUEST_EXPLICIT_ROOM_OVERRIDE text=%r room_id=%r",
                tl,
                explicit_room_id,
            )
    except Exception:
        pass

    tv_ctx = _request_default_tv_context(room_override=explicit_room_id) or {}
    ctx = CommandRequestCtx(
        explicit_room_id=explicit_room_id,
        request_tv_room_id=tv_ctx.get("room_id"),
        request_tv_entity=tv_ctx.get("tv_entity"),
        request_tv_remote=tv_ctx.get("tv_remote"),
        request_tv_on_scene=tv_ctx.get("tv_on_scene"),
        request_plex_client_name=tv_ctx.get("plex_client_name"),
        request_plex_launch_script=tv_ctx.get("plex_launch_script"),
    )

    try:
        logging.info(
            "REQUEST_TV_CONTEXT_EARLY room_id=%r tv=%r remote=%r tv_on_scene=%r plex_client=%r plex_launch=%r",
            ctx.request_tv_room_id,
            ctx.request_tv_entity,
            ctx.request_tv_remote,
            ctx.request_tv_on_scene,
            ctx.request_plex_client_name,
            ctx.request_plex_launch_script,
        )
    except Exception:
        pass

    return ctx

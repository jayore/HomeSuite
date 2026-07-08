from sonos_utils import homesuite_media_url_for_path, sonos_play_media
import re
import logging
import os

try:
    from env_compat import install_homesuite_env_aliases
    install_homesuite_env_aliases()
except Exception:
    pass
import uuid
from runtime_mode import allow_real_effects
from typing import Optional, Dict

from request_context import get_active_room_for_request_defaults
from home_registry import get_room


# Announce prefs (safe fallback)
try:
    from app_config import ANNOUNCE_VOLUME_FLOOR
except Exception:
    ANNOUNCE_VOLUME_FLOOR = 15
def _find_room_in_text(tl: str, players_map: Dict[str, str]) -> Optional[str]:
    if not tl or not players_map:
        return None
    t = tl.lower()
    for room in sorted(players_map.keys(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(room)}\b", t):
            return room
    return None


def _resolve_request_room_to_players_key(request_room: Optional[str], players_map: Dict[str, str]) -> Optional[str]:
    """
    Convert a request-context room identifier into a players_map key.

    Handles cases like:
    * request room id: "living_room"
    * players_map key:  "living room"
    """
    if not request_room or not players_map:
        return None

    rr = str(request_room).strip()
    if not rr:
        return None

    # Direct hit first
    if rr in players_map:
        return rr

    rr_spoken = rr.replace("_", " ").strip().lower()
    if rr_spoken in players_map:
        return rr_spoken

    room_cfg = get_room(rr)
    if isinstance(room_cfg, dict):
        aliases = room_cfg.get("aliases") or []
        for alias in aliases:
            a = str(alias).strip().lower()
            if a in players_map:
                return a

    return None


def handle_announcement_controls(
    *,
    tl: str,
    maybe_say,
    players_map: Dict[str, str],
    default_sonos_room: Optional[str],
    tts_generate_audio,     # injected from gpio_ptt
    sonos_play_media,       # injected from gpio_ptt
    mark_action_occurred=None,
) -> Optional[str]:

    t = (tl or "").strip().lower()
    if not t.startswith("announce"):
        return None

    # Strip leading keyword
    msg = re.sub(r"^announce\b", "", tl, flags=re.IGNORECASE).strip()
    if not msg:
        return None

    # Detect room anywhere in sentence
    room = _find_room_in_text(msg, players_map)

    # Remove room phrase variants and clean up leftover prepositions
    if room:
        # Remove "to kitchen", "in kitchen", "on kitchen", "in the kitchen", etc.
        msg = re.sub(rf"\b(?:to|in|on)\s+(?:the\s+)?{re.escape(room)}\b", "", msg, flags=re.IGNORECASE).strip()
        # Also remove bare room token if it appears standalone
        msg = re.sub(rf"\b{re.escape(room)}\b", "", msg, flags=re.IGNORECASE).strip()

    # Normalize whitespace
    msg = re.sub(r"\s+", " ", msg).strip()

    # If message accidentally starts with leftover "in"/"on"/"to"/"the"
    msg = re.sub(r"^(?:to|in|on)\b\s*", "", msg, flags=re.IGNORECASE).strip()
    msg = re.sub(r"^(?:the)\b\s*", "", msg, flags=re.IGNORECASE).strip()
    msg = re.sub(r"\s+", " ", msg).strip()

    if not msg:
        return None
    
     # Resolve target room / entity with precedence:
    # 1) explicit room in utterance
    # 2) effective target room from request context
    # 3) room-local request room
    # 4) legacy default Sonos room fallback
    request_room = get_active_room_for_request_defaults()
    request_room_key = _resolve_request_room_to_players_key(request_room, players_map)

    resolved_room = None
    if room and room in players_map:
        resolved_room = room
    elif request_room_key and request_room_key in players_map:
        resolved_room = request_room_key
    elif default_sonos_room and default_sonos_room in players_map:
        resolved_room = default_sonos_room

    if not resolved_room:
        logging.error("Announcement: no valid target room")
        return None

    entity_id = players_map[resolved_room]

    logging.info(
        "CLAIM: announcement text=%r explicit_room=%r request_room=%r resolved_room=%r",
        msg,
        room,
        request_room,
        resolved_room,
    )


    # TEST MODE GUARD
    if not allow_real_effects():
        if mark_action_occurred:
            mark_action_occurred()
        return maybe_say(f"(test) Announcing: {msg}")

    # Optional announce volume (0-100). Leave unset to let Sonos pick.
    announce_volume = None
    try:
        if os.environ.get("PIPHONE_ANNOUNCE_VOLUME"):
            announce_volume = int(os.environ["PIPHONE_ANNOUNCE_VOLUME"])
    except Exception:
        announce_volume = None

    audio_path = f"/tmp/announce_{uuid.uuid4().hex}.mp3"
    ok = tts_generate_audio(msg, audio_path)
    if not ok:
        logging.error("Announcement: TTS generation failed")
        return None

    media_url = homesuite_media_url_for_path(audio_path)

    ok2 = sonos_play_media(
        entity_id=entity_id,
        media_url=media_url,
        media_type="music",
        announce=True,
        announce_volume=announce_volume,
        announce_volume_floor=ANNOUNCE_VOLUME_FLOOR,
    )

    if not ok2:
        logging.error("Announcement: sonos_play_media failed")
        return None

    if mark_action_occurred:
        mark_action_occurred()

    return maybe_say(f"Announced: {msg}")

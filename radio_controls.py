import re
from typing import Optional, Dict


def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9\s]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def handle_pinned_radio_controls(
    *,
    tl: str,
    sonos_tl: str,
    pinned_radio_stations: Dict[str, str],
    sonos_entity_id: str,
    call_ha_service,
    maybe_say,
) -> Optional[str]:
    """
    Plays pinned radio stations via Sonos.

    Inputs:
        tl: full utterance (lowercased, stripped punctuation)
        sonos_tl: utterance with trailing "in/on <room>" removed (same as used for Sonos play routing)
        pinned_radio_stations: mapping of normalized station name -> media_content_id URI
        sonos_entity_id: target media_player entity
    Returns:
        None: not a pinned radio request
        "" / str: handled
    """
    if not pinned_radio_stations:
        return None

    # Parse "play <name>" (strip optional trailing "in/on <room>" already done upstream as sonos_tl)
    m_radio = re.match(r"^(play|put on|listen to|start)\s+(.+)$", sonos_tl or "")
    if not m_radio:
        return None

    q_raw = (m_radio.group(2) or "").strip()
    q_norm = _norm(q_raw)
    if not q_norm:
        return None

    uri = (pinned_radio_stations or {}).get(q_norm)
    if not uri:
        return None

    ok = call_ha_service(
        "media_player/play_media",
        {
            "entity_id": sonos_entity_id,
            "media_content_type": "music",
            "media_content_id": uri,
        },
    )
    if not ok:
        return None

    return maybe_say("Playing.")

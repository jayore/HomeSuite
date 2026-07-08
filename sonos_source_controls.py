import re
import logging
from typing import Optional, Dict, Callable, Any

def _is_ok(x: Any) -> bool:
    # In pptest, call_ha_service often returns None (dry-run). Treat that as success.
    return True if x is None else bool(x)

def handle_sonos_source_controls(
    *,
    tl: str,
    states_snapshot=None,
    call_ha_service,
    maybe_say,
    players_map: Dict[str, str],
    default_room: str,
    apple_tv_entity: Optional[str] = None,
    set_transport_focus: Optional[Callable[..., Any]] = None,
) -> Optional[str]:
    """
    Sonos source switching.

    Examples:
      - "tv audio"
      - "switch to tv"
      - "switch to tv audio"
      - "tv audio in kitchen"
      - "switch to tv audio in the bedroom"

    Behavior:
      - Implicit room => default_room
      - Explicit room => that room (if present in players_map)
    """
    t = (tl or "").strip().lower()
    if not t:
        return None

    # Build a room matcher from SONOS_PLAYERS keys (scales automatically).
    room_keys = [k for k in (players_map or {}).keys() if isinstance(k, str) and k.strip()]
    room_keys_norm = sorted({rk.strip().lower() for rk in room_keys}, key=len, reverse=True)

    room_pat = None
    if room_keys_norm:
        room_pat = "(" + "|".join(re.escape(r) for r in room_keys_norm) + ")"

    # Accept only "TV audio"-style intents (avoid stealing unrelated commands).
    # We intentionally do NOT match bare "tv" to reduce collisions.
    m = None
    if room_pat:
        m = re.fullmatch(
            rf"(?:switch\s+to|switch|set\s+to|set|use|go\s+to)?\s*tv(?:\s+audio)?(?:\s+(?:in|on)\s+(?:the\s+)?(?P<room>{room_pat}))?$",
            t,
        )
    else:
        m = re.fullmatch(
            r"(?:switch\s+to|switch|set\s+to|set|use|go\s+to)?\s*tv(?:\s+audio)?$",
            t,
        )

    # Also allow exact "tv audio" (even if regex above is strict about prefix words)
    if not m and t != "tv audio":
        return None

    room = None
    if m and m.groupdict().get("room"):
        room = (m.group("room") or "").strip().lower()

    if not room:
        room = (default_room or "").strip().lower()

    target_eid = (players_map or {}).get(room) or (players_map or {}).get((default_room or "").strip().lower())
    if not target_eid:
        return None

    # --- Group-aware TV source switching ---
    # Observed Sonos/HA behavior (matches your snapshots):
    # - "TV" appears in source_list only when the soundbar room (e.g., living room) is the group coordinator.
    # - If another room is coordinator (e.g., kitchen), source_list often omits "TV" and select_source fails.
    #
    # Mitigation:
    # - If we're in a group and the current target doesn't expose TV, re-form the group with a TV-capable
    #   player as master (prefer default_room), then call select_source on that master.
    def _get_state_obj(eid: str):
        if not states_snapshot or not eid:
            return None
        for st in states_snapshot:
            if st.get("entity_id") == eid:
                return st
        return None

    def _get_attrs(eid: str) -> dict:
        st = _get_state_obj(eid) or {}
        return st.get("attributes") or {}

    def _has_tv(eid: str) -> bool:
        try:
            attrs = _get_attrs(eid)
            sl = attrs.get("source_list")
            src = attrs.get("source")
            if isinstance(sl, list):
                for x in sl:
                    if str(x).strip().lower() == "tv":
                        return True
            if str(src or "").strip().lower() == "tv":
                return True
        except Exception:
            pass
        return False

    try:
        attrs = _get_attrs(target_eid)
        gm = attrs.get("group_members")
        grouped = isinstance(gm, list) and len(gm) > 1
    except Exception:
        gm = None
        grouped = False

    if grouped and isinstance(gm, list):
        group_eids = [e for e in gm if isinstance(e, str) and e.strip()]

        # If current target already exposes TV, don't disturb the group.
        if not _has_tv(target_eid):
            default_key = (default_room or "").strip().lower()
            default_eid = (players_map or {}).get(default_key)

            master_eid = None

            # Prefer default room if it's part of the group.
            if default_eid and default_eid in group_eids:
                master_eid = default_eid

            # Else prefer any group member that exposes TV.
            if not master_eid:
                for ee in group_eids:
                    if _has_tv(ee):
                        master_eid = ee
                        break

            # Else fall back to first member.
            if not master_eid and group_eids:
                master_eid = group_eids[0]

            if master_eid and master_eid != target_eid:
                members = [ee for ee in group_eids if ee != master_eid]
                if members:
                    ok_join = _is_ok(
                        call_ha_service(
                            "media_player/join",
                            {"entity_id": master_eid, "group_members": members},
                        )
                    )
                    if ok_join:
                        logging.info(
                            f"CLAIM: sonos_tv_audio_regroup master={master_eid} members={members}"
                        )
                        target_eid = master_eid

    def _try_tv(eid: str) -> bool:
        return _is_ok(call_ha_service("media_player/select_source", {"entity_id": eid, "source": "TV"}))

    ok = _try_tv(target_eid)

    # If select_source failed, and we're grouped, force regroup under default_room and retry.
    # This makes the behavior robust even when source_list is missing on the current coordinator.
    if not ok and states_snapshot:
        try:
            st0 = None
            for st in states_snapshot:
                if st.get("entity_id") == target_eid:
                    st0 = st
                    break
            attrs0 = (st0.get("attributes") or {}) if st0 else {}
            gm0 = attrs0.get("group_members")
            grouped0 = isinstance(gm0, list) and len(gm0) > 1
        except Exception:
            gm0 = None
            grouped0 = False

        if grouped0 and isinstance(gm0, list):
            default_key = (default_room or "").strip().lower()
            default_eid = (players_map or {}).get(default_key)

            group_eids = [e for e in gm0 if isinstance(e, str) and e.strip()]
            logging.info(f"CLAIM: sonos_tv_audio_select_failed grouped=1 target={target_eid} default_eid={default_eid} group={group_eids}")

            if default_eid and default_eid in group_eids:
                members = [e for e in group_eids if e != default_eid]
                if members:
                    ok_join = _is_ok(call_ha_service("media_player/join", {"entity_id": default_eid, "group_members": members}))
                    logging.info(f"CLAIM: sonos_tv_audio_forced_regroup ok_join={ok_join} master={default_eid} members={members}")
                    if ok_join:
                        target_eid = default_eid
                        ok = _try_tv(target_eid)

    if not ok:
        logging.info(f"CLAIM: sonos_tv_audio_failed entity_id={target_eid}")
        return None

    # Best-effort: bump transport focus toward TV so bare pause/resume feels right.
    if set_transport_focus and apple_tv_entity:
        try:
            set_transport_focus("tv", apple_tv_entity)
        except TypeError:
            try:
                set_transport_focus(kind="tv", entity_id=apple_tv_entity)
            except Exception:
                pass
        except Exception:
            pass

    try:
        return maybe_say("TV audio.") if maybe_say else "TV audio."
    except Exception:
        return "TV audio."

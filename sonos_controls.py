"""Execute Sonos grouping and transport operations through Home Assistant.

The handler resolves spoken rooms against the configured player map, uses the
current Home Assistant group state to select coordinators, and supports group,
ungroup, transport, and now-playing operations. Short-lived local state exists
only to restore volume/mute settings during the TV-passthrough pseudo-swap.

Playback search, Spotify browsing, sources, favorites, and volume have separate
handlers. This module returns ``None`` for those intents so dispatch ordering
remains predictable.
"""

from __future__ import annotations

import logging
import os

try:
    from env_compat import install_homesuite_env_aliases
    install_homesuite_env_aliases()
except Exception:
    pass
import re
import time
from datetime import datetime
from typing import Dict, Optional



from multi_target_utils import split_targets

def _is_ok(x) -> bool:
    # HA service calls in this project often return None on success.
    # Treat None as success so we don't mis-detect success as failure.
    return True if x is None else bool(x)

def _norm_room(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s


def _find_room_in_text(tl: str, players_map: Dict[str, str]) -> Optional[str]:
    t = (tl or "").lower()
    # Prefer longest keys first (e.g., "living room" before "room")
    for room in sorted(players_map.keys(), key=len, reverse=True):
        if re.search(rf"\b{re.escape(room)}\b", t):
            return room
    return None


def _is_playing(entity_id: str, states_snapshot: Optional[list]) -> bool:
    if not states_snapshot:
        return False
    st = next((s for s in states_snapshot if s.get("entity_id") == entity_id), None)
    return bool(st and st.get("state") == "playing")


def _get_now_playing(entity_id: str, states_snapshot: Optional[list]) -> Optional[str]:
    if not states_snapshot:
        return None
    st = next((s for s in states_snapshot if s.get("entity_id") == entity_id), None)
    if not st:
        return None
    attrs = st.get("attributes") or {}
    title = (attrs.get("media_title") or "").strip()
    artist = (attrs.get("media_artist") or "").strip()
    if not title and not artist:
        return None
    if title and artist:
        return f"It's {title} by {artist}."
    if title:
        return f"It's {title}."
    return f"It's {artist}."



# --- TV passthrough detection (MUST match now_playing_controls.py heuristics) ---
def _is_tv_passthrough(entity_id: str, states_snapshot) -> bool:
    if not entity_id or not states_snapshot:
        return False
    st = next((x for x in states_snapshot if x.get("entity_id") == entity_id), None)
    if not st:
        return False
    attrs = st.get("attributes") or {}
    source = str(attrs.get("source") or "").strip().lower()
    title = str(attrs.get("media_title") or "").strip().lower()
    cid_l = str(attrs.get("media_content_id") or "").strip().lower()

    # Mirror now_playing_controls.py:
    # - Treat Sonos source=TV / htastream/spdif as TV passthrough.
    # - TV input commonly shows htastream/spdif and/or title 'TV'
    if "spdif" in cid_l and (source == "tv" or title == "tv"):
        return True
    if "htastream" in cid_l and (source == "tv" or title == "tv"):
        return True
    if source == "tv" and title == "tv":
        return True
    # Some configs show source="TV" and no title/artist
    if source == "tv":
        return True
    return False


# --- Pseudo-swap state (TV passthrough only) ---
_PSEUDO_TV_SWAP = {
    "active": False,
    "master_room": None,        # soundbar room (e.g., living room)
    "member_rooms": [],         # rooms joined into master
    "prev_muted": None,         # bool
    "prev_volume": None,        # float 0..1
}

def _snapshot_mute_and_volume(entity_id: str, states_snapshot):
    if not entity_id or not states_snapshot:
        return None, None
    st = next((x for x in states_snapshot if x.get("entity_id") == entity_id), None) or {}
    attrs = st.get("attributes") or {}
    prev_muted = attrs.get("is_volume_muted")
    prev_vol = attrs.get("volume_level")
    try:
        prev_vol = float(prev_vol) if prev_vol is not None else None
    except Exception:
        prev_vol = None
    return prev_muted, prev_vol

def _mute_or_quiet(entity_id: str, call_ha_service, *, prefer_mute=True) -> bool:
    ok_any = False
    if prefer_mute:
        try:
            ok_any = _is_ok(call_ha_service("media_player/volume_mute", {"entity_id": entity_id, "is_volume_muted": True}))
        except Exception:
            pass
        if ok_any:
            return True
    # Fallback: set very low volume (1%)
    try:
        ok_any = _is_ok(call_ha_service("media_player/volume_set", {"entity_id": entity_id, "volume_level": 0.01})) or ok_any
    except Exception:
        pass
    return bool(ok_any)

def _restore_mute_and_volume(entity_id: str, call_ha_service, prev_muted, prev_volume) -> bool:
    ok_any = False
    try:
        if prev_muted is not None:
            ok_any = _is_ok(call_ha_service("media_player/volume_mute", {"entity_id": entity_id, "is_volume_muted": bool(prev_muted)})) or ok_any
    except Exception:
        pass
    try:
        if prev_volume is not None:
            ok_any = _is_ok(call_ha_service("media_player/volume_set", {"entity_id": entity_id, "volume_level": float(prev_volume)})) or ok_any
    except Exception:
        pass
    return bool(ok_any)

def handle_sonos_controls(
    *,
    tl: str,
    states_snapshot: Optional[list],
    call_ha_service,
    maybe_say,
    players_map: Dict[str, str],
    default_room: str,
    get_last_master_room=None,
    set_last_master_room=None,
) -> Optional[str]:
    """
    Sonos controls via HA:
    - grouping: group A with B, add room, remove/ungroup room
    - transport: next/previous/pause/resume/stop
    - now playing: what's playing

    Returns:
    - None : not a Sonos command
    - ""/str: handled (silent or spoken via maybe_say)
    """
    t = (tl or "").strip().lower()
    default_room = _norm_room(default_room)

    # --- Grouping master selection (prefer HA state over PiPhone memory) ---
    # Recency mode:
    #   - interaction (default): pick the most recently *active/updated* playing room
    #   - started: pick the most recently *started* playing room (last_changed only)
    _RECENCY_MODE = (os.getenv("PIPHONE_SONOS_GROUP_RECENCY") or "interaction").strip().lower()

    # Reverse lookup for coordinator mapping
    _eid_to_room = {v: _norm_room(k) for k, v in (players_map or {}).items() if v}

    def _group_state_for_eid(eid: str) -> Optional[dict]:
        if not states_snapshot or not eid:
            return None
        return next((st for st in states_snapshot if st.get("entity_id") == eid), None)

    def _group_parse_dt(v: str):
        if not v or not isinstance(v, str):
            return None
        vv = v.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(vv)
        except Exception:
            return None

    def _group_ts_for_eid(eid: str):
        st = _group_state_for_eid(eid) or {}
        attrs = st.get("attributes") or {}

        if _RECENCY_MODE in ("started", "start", "play_started"):
            return _group_parse_dt(st.get("last_changed"))

        # interaction (default): prefer last_updated / position-updated, then last_changed
        for k in ("last_updated",):
            ts = _group_parse_dt(st.get(k))
            if ts:
                return ts
        ts = _group_parse_dt(attrs.get("media_position_updated_at"))
        if ts:
            return ts
        return _group_parse_dt(st.get("last_changed"))

    def _coordinator_room_for_room(room: str) -> str:
        rrn = _norm_room(room)
        eid = room_to_eid(rrn)
        st = _group_state_for_eid(eid) or {}
        attrs = st.get("attributes") or {}

        coord_eid = (
            attrs.get("coordinator")
            or attrs.get("group_coordinator")
            or attrs.get("group_coordinator_entity_id")
            or attrs.get("coordinator_entity_id")
        )
        if coord_eid and coord_eid in _eid_to_room:
            return _eid_to_room[coord_eid]
        return rrn

    def _pick_active_master_room(*, exclude_room: Optional[str] = None, allow_fallback_to_excluded: bool = False) -> Optional[str]:
        ex = _norm_room(exclude_room) if exclude_room else None
        best_room = None
        best_ts = None

        for rr, ee in (players_map or {}).items():
            if not ee:
                continue
            rrn = _norm_room(rr)
            if ex and rrn == ex and not allow_fallback_to_excluded:
                continue
            if not _is_playing(ee, states_snapshot):
                continue

            ts = _group_ts_for_eid(ee)
            if best_room is None:
                best_room, best_ts = rrn, ts
            else:
                if ts and (best_ts is None or ts > best_ts):
                    best_room, best_ts = rrn, ts

        if best_room is None and ex and allow_fallback_to_excluded:
            ee = room_to_eid(ex)
            if ee and _is_playing(ee, states_snapshot):
                best_room = ex

        if best_room:
            coord_room = _coordinator_room_for_room(best_room)
            if coord_room != best_room:
                logging.info("SONOS_GROUP_MASTER_COORD: %s -> %s", best_room, coord_room)
            return coord_room
        return None

    def _pick_master_for_grouping(*, exclude_room: Optional[str] = None, prefer_default_if_playing: bool = True) -> str:
        # 1) Prefer default room if it's playing (when allowed)
        if prefer_default_if_playing:
            dr_eid = room_to_eid(default_room)
            if dr_eid and _is_playing(dr_eid, states_snapshot):
                if not exclude_room or _norm_room(exclude_room) != _norm_room(default_room):
                    return _coordinator_room_for_room(default_room)

        # 2) Prefer most-recent playing room (optionally excluding a room)
        rr = _pick_active_master_room(exclude_room=exclude_room)
        if rr:
            logging.info("SONOS_GROUP_PICK_MASTER mode=%s picked=%s exclude=%r", _RECENCY_MODE, rr, exclude_room)
            return rr

        # 3) Fallback to PiPhone memory (optional, only when nothing is actively playing)
        try:
            lm = get_last_master_room() if get_last_master_room else None
            lm = _norm_room(lm) if lm else None
            if lm and lm in players_map:
                logging.info("SONOS_GROUP_PICK_MASTER fallback=last_master %s", lm)
                return _coordinator_room_for_room(lm)
        except Exception:
            pass

        # 4) Final fallback
        return default_room

    def room_to_eid(room: str) -> Optional[str]:
        return players_map.get(_norm_room(room))


    def _resolve_room_token(tok: str) -> Optional[str]:
        tok = (tok or "").strip()
        tok = re.sub(r"(?:'s|’s)$", "", tok).strip()
        tok = re.sub(r"^the\s+", "", tok).strip()
        tok_n = _norm_room(tok)
        if tok_n in ("here", "this room"):
            return default_room
        if tok_n in players_map:
            return tok_n
        return _find_room_in_text(tok_n, players_map)

    def _resolve_room_list_strict(text: str) -> Optional[list]:
        # Split via shared utility; be strict: if any token can't be resolved, return None.
        toks = split_targets((text or "").strip())
        if not toks:
            return None
        out = []
        seen = set()
        for tok in toks:
            rr = _resolve_room_token(tok)
            if not rr:
                return None
            rn = _norm_room(rr)
            if rn not in seen:
                out.append(rn)
                seen.add(rn)
        return out if out else None

    def _coordinator_eid(eid: str) -> str:
        """Return a stable coordinator entity_id for the group containing `eid`.

        HA's Sonos entity often does not expose a coordinator attribute. In that case, we
        use get_last_master_room() as a coordinator hint: if that room's entity_id is in
        the same group_members list as `eid`, we prefer it.
        """
        if not eid or not states_snapshot:
            return eid
        try:
            st = next((s for s in states_snapshot if s.get("entity_id") == eid), None) or {}
            attrs = st.get("attributes") or {}
            gm = attrs.get("group_members")
            if not isinstance(gm, list) or len(gm) <= 1:
                return eid

            # Prefer last-master if it is part of this group.
            try:
                lm = get_last_master_room() if get_last_master_room else None
                lm = _norm_room(lm) if lm else None
                if lm:
                    lm_eid = room_to_eid(lm)
                    if lm_eid and lm_eid in gm:
                        return lm_eid
            except Exception:
                pass

            return eid
        except Exception:
            return eid

    def _join_many(*, master_room: str, member_rooms: list) -> bool:
        master_room = _norm_room(master_room)
        members = [r for r in (member_rooms or []) if _norm_room(r) != master_room]
        if not members:
            return False
        master_eid = _coordinator_eid(room_to_eid(master_room))
        member_eids = [room_to_eid(r) for r in members]
        member_eids = [e for e in member_eids if e]
        if not master_eid or not member_eids:
            return False
        return _is_ok(call_ha_service("media_player/join", {"entity_id": master_eid, "group_members": member_eids}))

    # ----------------------------
    # "What's playing?"
    # ----------------------------
    if re.search(r"\b(what's playing|what is playing|what song is this|what track is this)\b", t):
        room = _find_room_in_text(t, players_map) or default_room
        eid = room_to_eid(room)
        if not eid:
            return None
        np = _get_now_playing(eid, states_snapshot)
        if np:
            return np
        return maybe_say("Nothing is playing.")  # or return None if you prefer error tone

    # ----------------------------
    # Grouping
    # ----------------------------

    # "group everywhere" / "group all" (+ optional "to <room>")
    # Examples:
    #   "group everywhere"
    #   "group everywhere to kitchen"
    #   "group all to here"
    # Behavior:
    #   - If destination is specified, that room is master.
    #   - Else: prefer default room if it's playing; otherwise pick the most-recently-active playing room.
    m_all = re.match(
        r"^group\s+(?:up\s+)?(?:everywhere|all(?:\s+(?:rooms|speakers))?)"
        r"(?:\s+(?:to|two|2|into|with)\s+(.+))?$",
        t,
    )
    if m_all:
        dest_tok = (m_all.group(1) or "").strip().lower()

        def _tok_to_room(tok: str) -> Optional[str]:
            tok = (tok or "").strip().lower()
            tok = re.sub(r"\s+", " ", tok).strip()
            tok = re.sub(r"^the\s+", "", tok).strip()
            if tok in ("here", "this room"):
                return default_room
            return _find_room_in_text(tok, players_map)

        def _state_for_eid(eid: str) -> Optional[dict]:
            if not states_snapshot or not eid:
                return None
            return next((st for st in states_snapshot if st.get("entity_id") == eid), None)

        def _parse_dt(v: str):
            if not v or not isinstance(v, str):
                return None
            vv = v.strip()
            # HA often uses ISO strings; sometimes with trailing Z
            vv = vv.replace("Z", "+00:00")
            try:
                return datetime.fromisoformat(vv)
            except Exception:
                return None

        def _most_recent_playing_room(*, exclude_room: Optional[str] = None) -> Optional[str]:
            ex = _norm_room(exclude_room) if exclude_room else None
            best_room = None
            best_ts = None

            for rr, ee in (players_map or {}).items():
                if not ee:
                    continue
                rrn = _norm_room(rr)
                if ex and rrn == ex:
                    continue
                if not _is_playing(ee, states_snapshot):
                    continue

                st = _state_for_eid(ee) or {}
                ts = None
                # Prefer state timestamps; they generally update when playback state changes
                for k in ("last_changed", "last_updated"):
                    ts = _parse_dt(st.get(k))
                    if ts:
                        break
                if not ts:
                    attrs = st.get("attributes") or {}
                    ts = _parse_dt(attrs.get("media_position_updated_at"))

                if best_room is None:
                    best_room, best_ts = rrn, ts
                else:
                    if ts and (best_ts is None or ts > best_ts):
                        best_room, best_ts = rrn, ts

            return best_room

        specified = _tok_to_room(dest_tok) if dest_tok else None

        # Pick master:
        if specified:
            master_room = specified
        else:
            # Prefer default if it's playing
            dr_eid = room_to_eid(default_room)
            if dr_eid and _is_playing(dr_eid, states_snapshot):
                master_room = default_room
            else:
                master_room = _most_recent_playing_room() or default_room

        master_eid = _coordinator_eid(room_to_eid(master_room))
        if not master_eid:
            return None

        # Members: all other rooms
        members = []
        for rr, ee in (players_map or {}).items():
            if not ee:
                continue
            if _norm_room(rr) == _norm_room(master_room):
                continue
            members.append(ee)

        if not members:
            return None

        ok = call_ha_service(
            "media_player/join",
            {"entity_id": master_eid, "group_members": members},
        )
        if ok and set_last_master_room:
            set_last_master_room(master_room)
        return maybe_say("Okay.") if ok else None

    # Bare "group": join default room into whatever is currently playing elsewhere
    if re.fullmatch(r"group( up)?", t):
        target_room = default_room
        target_eid = room_to_eid(target_room)
        if not target_eid:
            return None

        # Pick the most-recent playing room EXCLUDING the default room (so "group" in a playing default room
        # groups *into* the other active stream, not into itself).
        master_room = _pick_active_master_room(exclude_room=target_room)
        if not master_room:
            return None

        ok = call_ha_service(
            "media_player/join",
            {"entity_id": _coordinator_eid(room_to_eid(master_room)), "group_members": [target_eid]},
        )
        if ok and set_last_master_room:
            set_last_master_room(master_room)
        if ok:
            logging.info("CLAIM: sonos_group_bare master=%s member=%s", master_room, target_room)
        return maybe_say(f"Grouping {target_room} with {master_room}.") if ok else None

    # Bare "ungroup": unjoin default room
    if re.fullmatch(r"ungroup", t):
        target_room = default_room
        target_eid = room_to_eid(target_room)
        if not target_eid:
            return None
        ok = call_ha_service("media_player/unjoin", {"entity_id": target_eid})
        return maybe_say(f"Ungrouping {target_room}.") if ok else None

    # Explicit grouping:
    # - "group kitchen to living room"
    # - "group kitchen and bathroom to living room"
    # - "group kitchen with living room"
    # - "group kitchen and bathroom with living room"
    #
    # NOTE: we intentionally DO NOT treat the word "and" as the separator between LHS/RHS here,
    # because "and" is used inside lists. We only split sides using (to|into|with).
    m_grp = re.search(r"^group\s+(.+?)\s+(to|two|2|into|with)\s+(.+)$", t)
    if m_grp:
        left = (m_grp.group(1) or "").strip()
        mode = (m_grp.group(2) or "").strip()
        right = (m_grp.group(3) or "").strip()

        left_rooms = _resolve_room_list_strict(left)
        right_room = _resolve_room_token(right)

        if not left_rooms or not right_room:
            return None

        right_room = _norm_room(right_room)
        if right_room not in players_map:
            return None

        # Single-left "with" keeps legacy behavior: prefer whichever is playing (else default/order).
        if mode == "with" and len(left_rooms) == 1:
            a = left_rooms[0]
            b = right_room
            if a == b:
                return None

            a_eid = room_to_eid(a)
            b_eid = room_to_eid(b)
            if not a_eid or not b_eid:
                return None

            if _is_playing(a_eid, states_snapshot):
                master_room, member_rooms = a, [b]
            elif _is_playing(b_eid, states_snapshot):
                master_room, member_rooms = b, [a]
            else:
                if default_room in (a, b):
                    master_room = default_room
                    member_rooms = [b if master_room == a else a]
                else:
                    master_room, member_rooms = a, [b]

        else:
            # For "to/into" OR multi-left "with": treat RHS as master.
            master_room = right_room
            member_rooms = [r for r in left_rooms if _norm_room(r) != _norm_room(master_room)]

        ok = _join_many(master_room=master_room, member_rooms=member_rooms)
        if ok and set_last_master_room:
            try:
                set_last_master_room(master_room)
            except Exception:
                pass
        if ok:
            logging.info(f"CLAIM: sonos_group_multi mode={mode} members={member_rooms} master={master_room}")
            if len(member_rooms) == 1:
                return maybe_say(f"Grouping {member_rooms[0]} with {master_room}.")
            return maybe_say("Okay.")
        return None

    # Two-room shorthand:
    # "group kitchen and living room"  (no 'to/with/into')
    # "group to kitchen" / "group into kitchen" / "group with kitchen"
    # Treat as: group <here/default> to/into/with <room>
    m_to_only = re.match(r"^group\s+(to|two|2|into|with)\s+(.+)$", t)
    if m_to_only:
        sep = (m_to_only.group(1) or "").strip()
        right = (m_to_only.group(2) or "").strip()

        a = default_room  # implicit "here"
        b = _find_room_in_text(right, players_map)
        if not b or b == a:
            return None

        a_eid = room_to_eid(a)
        b_eid = room_to_eid(b)
        if not a_eid or not b_eid:
            return None

        # Decide master/member
        if sep in ("to", "two", "2", "into"):
            master_room, member_room = b, a
        else:
            # with: prefer whichever is currently playing
            if _is_playing(a_eid, states_snapshot):
                master_room, member_room = a, b
            elif _is_playing(b_eid, states_snapshot):
                master_room, member_room = b, a
            else:
                master_room, member_room = a, b

        ok = call_ha_service(
            "media_player/join",
            {"entity_id": _coordinator_eid(room_to_eid(master_room)), "group_members": [room_to_eid(member_room)]},
        )
        if ok and set_last_master_room:
            set_last_master_room(master_room)
        if ok:
            logging.info(f"CLAIM: sonos_group_to_only sep={sep} member={member_room} master={master_room}")
            return maybe_say(f"Grouping {member_room} with {master_room}.")
        return None

    m_pair = re.search(r"^group\s+(.+?)\s+and\s+(.+)$", t)
    if m_pair:
        left = (m_pair.group(1) or "").strip()
        right = (m_pair.group(2) or "").strip()

        # If RHS contains 'to/with/into', let the more specific handler above take it.
        if re.search(r"\b(to|two|2|into|with)\b", right):
            return None

        a = _resolve_room_token(left)
        b = _resolve_room_token(right)
        if not a or not b:
            return None
        a = _norm_room(a)
        b = _norm_room(b)
        if a == b or a not in players_map or b not in players_map:
            return None

        a_eid = room_to_eid(a)
        b_eid = room_to_eid(b)
        if not a_eid or not b_eid:
            return None

        # Prefer whichever is currently playing
        if _is_playing(a_eid, states_snapshot):
            master_room, member_rooms = a, [b]
        elif _is_playing(b_eid, states_snapshot):
            master_room, member_rooms = b, [a]
        else:
            if default_room in (a, b):
                master_room = default_room
                member_rooms = [b if master_room == a else a]
            else:
                master_room, member_rooms = a, [b]

        ok = _join_many(master_room=master_room, member_rooms=member_rooms)
        if ok and set_last_master_room:
            try:
                set_last_master_room(master_room)
            except Exception:
                pass
        if ok:
            logging.info(f"CLAIM: sonos_group_pair member={member_rooms[0]} master={master_room}")
            return maybe_say(f"Grouping {member_rooms[0]} with {master_room}.")
        return None

    # "group kitchen" (shorthand): join target room to last master (or default)
    # "group kitchen" (shorthand): join target(s) to last master (or default)
    m = re.fullmatch(r"group\s+(.+)", t)
    if m:
        rest = (m.group(1) or "").strip().lower()

        # If this looks like an explicit pairing ("group A to/into/with B"),
        # do NOT consume it here; let the more specific handler above handle it.
        if (
            " to " in f" {rest} "
            or " into " in f" {rest} "
            or " with " in f" {rest} "
        ):
            return None

        targets_list = _resolve_room_list_strict(m.group(1))
        if not targets_list:
            return None

        # If they said "group living room" and living room is default, treat like bare group
        if len(targets_list) == 1 and targets_list[0] == default_room:
            return None

        master = _pick_master_for_grouping(prefer_default_if_playing=True)

        # Remove master if included; joining master to itself is meaningless
        members = [r for r in targets_list if _norm_room(r) != _norm_room(master)]
        if not members:
            return None

        ok = _join_many(master_room=master, member_rooms=members)
        if ok and set_last_master_room:
            try:
                set_last_master_room(master)
            except Exception:
                pass

        if ok:
            if len(members) == 1:
                return maybe_say(f"Grouping {members[0]} with {master}.")
            return maybe_say("Okay.")
        return None

    # "add kitchen" / "join kitchen" (+ explicit destination: "add kitchen to living room")
    # Supports multi-target: "add kitchen and bathroom" / "add kitchen and bathroom to living room"
    m = re.match(r"^(add|join)\s+(.+?)\s+(?:to|two|2|into|with)\s+(.+)$", t)
    if m:
        targets_list = _resolve_room_list_strict(m.group(2))
        master_tok = (m.group(3) or "").strip()
        master = _resolve_room_token(master_tok)
        master = _norm_room(master) if master else None
        if not targets_list or not master or master not in players_map:
            return None

        members = [r for r in targets_list if _norm_room(r) != _norm_room(master)]
        if not members:
            return None

        ok = _join_many(master_room=master, member_rooms=members)
        if ok and set_last_master_room:
            try:
                set_last_master_room(master)
            except Exception:
                pass

        if ok:
            logging.info("CLAIM: sonos_add_to master=%s members=%s", master, ",".join(members))
            if len(members) == 1:
                return maybe_say(f"Adding {members[0]}.")
            return maybe_say("Okay.")
        return None

    m = re.search(r"\b(add|join)\s+(.+)\b", t)
    if m:
        targets_list = _resolve_room_list_strict(m.group(2))
        if not targets_list:
            return None

        master = _pick_master_for_grouping(prefer_default_if_playing=True)

        members = [r for r in targets_list if _norm_room(r) != _norm_room(master)]
        if not members:
            return None

        ok = _join_many(master_room=master, member_rooms=members)
        if ok and set_last_master_room:
            try:
                set_last_master_room(master)
            except Exception:
                pass

        if ok:
            logging.info("CLAIM: sonos_add master=%s members=%s", master, ",".join(members))
            if len(members) == 1:
                return maybe_say(f"Adding {members[0]}.")
            return maybe_say("Okay.")
        return None

        master = get_last_master_room() if get_last_master_room else None
        master = _norm_room(master) if master else default_room
        if master not in players_map:
            master = default_room

        members = [r for r in targets_list if _norm_room(r) != _norm_room(master)]
        if not members:
            return None

        ok = _join_many(master_room=master, member_rooms=members)
        if ok and set_last_master_room:
            try:
                set_last_master_room(master)
            except Exception:
                pass

        if ok:
            if len(members) == 1:
                return maybe_say(f"Adding {members[0]}.")
            return maybe_say("Okay.")
        return None

    # "remove kitchen" / "ungroup kitchen"  (supports multi-target: "remove kitchen and bathroom")
    m = re.search(r"\b(remove|ungroup|drop)\s+(.+)\b", t)
    if m:
        targets_list = _resolve_room_list_strict(m.group(2))
        if not targets_list:
            return None

        any_ok = False
        for rr in targets_list:
            ee = room_to_eid(rr)
            if not ee:
                return None  # strict: all must resolve
            try:
                ok = call_ha_service("media_player/unjoin", {"entity_id": ee})
                any_ok = any_ok or bool(ok)
            except Exception:
                pass

        if any_ok:
            if len(targets_list) == 1:
                return maybe_say(f"Removing {targets_list[0]}.")
            return maybe_say("Okay.")
        return None

    # ----------------------------
    # Move / swap playback between rooms
    # ----------------------------
    # Implemented as:
    #   join(to into from) -> brief delay -> unjoin(from)
    #
    # Supported phrases (examples):
    #   "move music to kitchen"
    #   "move music from kitchen to bookshelf"
    #   "move audio from bookshelf to kitchen"
    #   "move music here" / "move audio here" / "move here"
    #   "swap audio from kitchen"          (interpreted as move from kitchen -> here)
    #   "swap audio to kitchen"            (interpreted as move from here -> kitchen)
    #   "swap audio from kitchen to bedroom"
    #   "swap audio" / "swap music" / "swap here"  (implicit: grab what's playing -> here)
    #
    # Notes:
    # - "here" == default_room
    # - If "from here" is implied but default_room isn't playing, we try to pick ANY playing room as the source.
    # - If "from" is omitted and destination is "here", we pick ANY playing room (prefer not default).
    m_mv = re.match(r"^(move|swap|unswap)(?:\s+(?:(?:the\s+)?(?:music|audio)\b\s*)?(.*))?$", t)
    if m_mv:
        verb = (m_mv.group(1) or "").strip().lower()
        rest = (m_mv.group(2) or "").strip().lower()

        def _tok_to_room(tok: str) -> Optional[str]:
            tok = (tok or "").strip().lower()
            tok = re.sub(r"\s+", " ", tok).strip()
            if tok in ("here", "this room"):
                return default_room
            return _find_room_in_text(tok, players_map)

        def _tok_to_rooms(tok: str) -> Optional[list]:
            # Split destinations via shared splitter. Strict: all tokens must resolve.
            toks = split_targets((tok or "").strip())
            if not toks:
                return None
            out = []
            seen = set()
            for tt in toks:
                rr = _tok_to_room(tt)
                if not rr:
                    return None
                rn = _norm_room(rr)
                if rn not in seen:
                    out.append(rn)
                    seen.add(rn)
            return out if out else None


        def _pick_any_playing_room(*, exclude_room: Optional[str] = None) -> Optional[str]:
            ex = _norm_room(exclude_room) if exclude_room else None
            # Prefer any playing room that is NOT exclude_room
            try:
                for rr, ee in (players_map or {}).items():
                    if not ee:
                        continue
                    if ex and _norm_room(rr) == ex:
                        continue
                    if _is_playing(ee, states_snapshot):
                        return _norm_room(rr)
            except Exception:
                pass
            # Fall back: if exclude_room was set and nothing else is playing, allow exclude_room if playing
            if ex:
                try:
                    ee = room_to_eid(ex)
                    if ee and _is_playing(ee, states_snapshot):
                        return ex
                except Exception:
                    pass
            return None

        AUTO = "__AUTO__"
        from_tok = None
        to_tok = None

        # Normalize empty / "here" shorthand
        #   "swap" -> AUTO -> here
        #   "swap audio" -> AUTO -> here
        #   "swap back" / "unswap" -> AUTO -> here
        #   "swap back to kitchen" -> AUTO -> kitchen
        #   "move music here" -> AUTO -> here
        if verb in ("swap", "unswap") and rest.startswith("back"):
            # "swap back" (optionally: "back to <room>")
            r2 = rest[len("back"):].strip()
            if r2.startswith("to "):
                to_tok = r2[len("to "):].strip() or "here"
            else:
                to_tok = "here"
            from_tok = AUTO

        elif rest == "":
            if verb in ("swap", "unswap"):
                from_tok, to_tok = AUTO, "here"
            else:
                # "move" with nothing else is ambiguous; ignore
                from_tok, to_tok = None, None

        elif rest in ("here", "this room"):
            from_tok, to_tok = AUTO, "here"

        elif rest.startswith("from "):
            r2 = rest[len("from ") :].strip()
            if " to " in f" {r2} ":
                a, b = r2.split(" to ", 1)
                from_tok = a.strip()
                to_tok = b.strip()
            else:
                # "from X" implies "to here"
                from_tok = r2.strip()
                to_tok = "here"

        elif rest.startswith("to "):
            # "to X" implies "from here" (unless X is "here", then AUTO->here)
            to_tok = rest[len("to ") :].strip()
            if to_tok in ("here", "this room"):
                from_tok = AUTO
            else:
                from_tok = "here"

        elif " to " in f" {rest} ":
            # "X to Y" -> from X, to Y (implicit "from")
            a, b = rest.split(" to ", 1)
            from_tok = a.strip()
            to_tok = b.strip()

        else:
            # Not a move/swap phrase we recognize
            from_tok = None
            to_tok = None
        if from_tok and to_tok:
            to_rooms = _tok_to_rooms(to_tok)
            if not to_rooms:
                return None

            # Resolve "from"
            if from_tok == AUTO:
                from_room = _pick_any_playing_room(exclude_room=to_rooms[0])
            else:
                from_room = _tok_to_room(from_tok)

            if not from_room:
                # If nothing is playing anywhere, provide a clean response (don't error-tone)
                return maybe_say("Nothing is playing.")

            if _norm_room(from_room) in {_norm_room(r) for r in (to_rooms or [])}:
                return None

            from_eid = room_to_eid(from_room)
            to_eids = [room_to_eid(r) for r in (to_rooms or [])]
            to_eids = [e for e in to_eids if e]
            if not from_eid or not to_eids:
                return None

            # If "from here" is implied but default isn't playing, auto-pick ANY playing room as true source.
            actual_from_room = from_room
            try:
                if from_tok in ("here", "this room") and states_snapshot:
                    if not _is_playing(from_eid, states_snapshot):
                        picked = _pick_any_playing_room(exclude_room=to_rooms[0])
                        if picked:
                            actual_from_room = picked
                            from_eid = room_to_eid(actual_from_room)
            except Exception:
                pass

            # --- TV passthrough special-case (pseudo-swap) ---
            # Sonos TV audio (soundbar HDMI-ARC/eARC) generally only plays to groups that INCLUDE the soundbar room.
            # A true "swap" (join dest -> unjoin soundbar) makes TV audio go silent.
            #
            # So for TV passthrough we do:
            #   - Keep DEFAULT_SONOS_ROOM (soundbar) as coordinator
            #   - Join destination room(s) into DEFAULT
            #   - Mute (or 1% volume fallback) ONLY the soundbar
            #
            # IMPORTANT: TV passthrough metadata can appear on group members too.
            # Therefore we detect TV passthrough ONLY on the DEFAULT/soundbar entity.

            try:
                _default_room_n = _norm_room(default_room)
                _default_eid = room_to_eid(_default_room_n)

                # Destinations for this move/swap
                _to_rooms = list(to_rooms or [])
                _to_rooms_n = [_norm_room(r) for r in _to_rooms]
                _primary_to_room = _to_rooms[0] if _to_rooms else None
                _primary_to_room_n = _norm_room(_primary_to_room) if _primary_to_room else None

                # Only consider destinations that are NOT the default room
                _dest_rooms = [r for r in _to_rooms if _norm_room(r) != _default_room_n]
                _dest_eids = [room_to_eid(r) for r in _dest_rooms]
                _dest_eids = [e for e in _dest_eids if e]

                def _attrs(eid: str) -> dict:
                    if not eid or not states_snapshot:
                        return {}
                    stx = next((st for st in states_snapshot if st.get("entity_id") == eid), None)
                    return (stx.get("attributes") or {}) if stx else {}

                def _group_members(eid: str) -> list:
                    gm = _attrs(eid).get("group_members")
                    return list(gm) if isinstance(gm, list) else []

                # Detect TV passthrough ONLY on default/soundbar
                _tv = False
                try:
                    if _default_eid:
                        _tv = bool(_is_tv_passthrough(_default_eid, states_snapshot))
                except Exception:
                    _tv = False

                # ---- Return behavior: user is moving TO the default room while pseudo mode is active
                if _tv and _PSEUDO_TV_SWAP.get("active") and _default_eid and _primary_to_room_n == _default_room_n:
                    did_unjoin = False

                    # If they explicitly said "from <room>", unjoin that member (if possible).
                    try:
                        _from_room_n = _norm_room(actual_from_room or from_room or "")
                    except Exception:
                        _from_room_n = _norm_room(from_room or "")

                    if _from_room_n and _from_room_n != _default_room_n:
                        _from_eid = room_to_eid(_from_room_n)
                        if _from_eid:
                            try:
                                call_ha_service("media_player/unjoin", {"entity_id": _from_eid})
                                did_unjoin = True
                            except Exception:
                                pass
                    else:
                        # Otherwise unjoin all non-default members currently grouped with default
                        for me in _group_members(_default_eid):
                            if me and me != _default_eid:
                                try:
                                    call_ha_service("media_player/unjoin", {"entity_id": me})
                                    did_unjoin = True
                                except Exception:
                                    pass

                    # Restore default's previous mute/volume
                    try:
                        _restore_mute_and_volume(
                            _default_eid,
                            call_ha_service,
                            _PSEUDO_TV_SWAP.get("prev_muted"),
                            _PSEUDO_TV_SWAP.get("prev_volume"),
                        )
                    except Exception:
                        # best-effort fallback: unmute
                        try:
                            call_ha_service("media_player/volume_mute", {"entity_id": _default_eid, "is_volume_muted": False})
                        except Exception:
                            pass

                    _PSEUDO_TV_SWAP["active"] = False
                    _PSEUDO_TV_SWAP["master_room"] = None
                    _PSEUDO_TV_SWAP["member_rooms"] = []
                    _PSEUDO_TV_SWAP["prev_muted"] = None
                    _PSEUDO_TV_SWAP["prev_volume"] = None

                    logging.info(f"CLAIM: sonos_tv_pseudo_return to=default did_unjoin={did_unjoin}")
                    return maybe_say("Okay.")

                # ---- Forward behavior: moving TV audio away from default
                # Only do this when the *actual source room* is the default room (soundbar).
                if (
                    _tv
                    and _default_eid
                    and _dest_eids
                    and _norm_room(actual_from_room) == _default_room_n
                    and _primary_to_room_n != _default_room_n
                ):
                    ok_group = False
                    try:
                        ok_group = bool(call_ha_service(
                            "media_player/join",
                            {"entity_id": _default_eid, "group_members": _dest_eids},
                        ))
                    except Exception:
                        ok_group = False

                    if ok_group:
                        prev_muted, prev_vol = _snapshot_mute_and_volume(_default_eid, states_snapshot)
                        _PSEUDO_TV_SWAP["active"] = True
                        _PSEUDO_TV_SWAP["master_room"] = _default_room_n
                        _PSEUDO_TV_SWAP["member_rooms"] = list(_dest_rooms)
                        _PSEUDO_TV_SWAP["prev_muted"] = prev_muted
                        _PSEUDO_TV_SWAP["prev_volume"] = prev_vol

                        try:
                            _mute_or_quiet(_default_eid, call_ha_service, prefer_mute=True)
                        except Exception:
                            pass

                        try:
                            if set_last_master_room:
                                set_last_master_room(_default_room_n)
                        except Exception:
                            pass

                        logging.info(f"CLAIM: sonos_tv_pseudo_swap from=default to={_dest_rooms}")
                        if len(_dest_rooms) == 1:
                            return maybe_say(f"Moving TV audio to {_dest_rooms[0]}.")
                        return maybe_say("Moving TV audio.")
                # If not TV passthrough (or grouping failed), fall through to normal move/swap below.
            except Exception as e:
                # Do NOT silently swallow; this is critical routing logic.
                try:
                    logging.info(f"TV_PSEUDO_SWAP_ERROR: {e}")
                except Exception:
                    pass
# Execute: join target into source, then unjoin source.
            any_ok = False
            try:
                ok1 = call_ha_service(
                    "media_player/join",
                    {"entity_id": from_eid, "group_members": to_eids},
                )
                any_ok = any_ok or bool(ok1)
            except Exception:
                ok1 = False

            # brief settle time helps reduce flakiness
            try:
                time.sleep(0.35)
            except Exception:
                pass

            try:
                ok2 = call_ha_service("media_player/unjoin", {"entity_id": from_eid})
                any_ok = any_ok or bool(ok2)
            except Exception:
                ok2 = False

            if any_ok and set_last_master_room:
                try:
                    set_last_master_room(default_room if default_room in (to_rooms or []) else to_rooms[0])
                except Exception:
                    pass

            if any_ok:
                # If destination is only "here", keep it tight.
                if len(to_rooms) == 1 and _norm_room(to_rooms[0]) == _norm_room(default_room):
                    return maybe_say("Moving music here.")
                # Multi-destination (or non-here): keep confirmations minimal.
                if len(to_rooms) == 1:
                    return maybe_say(f"Moving music from {actual_from_room} to {to_rooms[0]}.")
                return maybe_say("Moving music.")
            return None

    # ----------------------------
    # Transport
    # ----------------------------
    # ----------------------------
    # Global transport: "pause everywhere"
    # ----------------------------
    # Examples:
    #   "pause everywhere"
    #   "pause all"
    #   "pause all music"
    #   "pause all sonos"
    #   "pause all speakers"
    if re.search(r"\bpause\s+(?:everywhere|all(?:\s+(?:music|sonos|speakers))?)\b", t):
        # Prefer pausing only actively playing rooms to reduce unnecessary HA calls.
        # If we don't have state, fall back to sending pause to all players.
        eids = [
            eid
            for eid in (players_map or {}).values()
            if isinstance(eid, str) and eid.strip()
        ]
        if not eids:
            return None

        targets = []
        if states_snapshot:
            for eid in eids:
                if _is_playing(eid, states_snapshot):
                    targets.append(eid)
        if not targets:
            targets = eids  # fallback

        any_ok = False
        for eid in targets:
            try:
                ok = call_ha_service("media_player/media_pause", {"entity_id": eid})
                any_ok = any_ok or bool(ok)
            except Exception:
                pass

        return maybe_say("Paused everywhere.") if any_ok else None

    # Avoid stealing volume phrases
    if "volume" in t:
        return None

    # Pick target room if mentioned, else default
    room = _find_room_in_text(t, players_map) or default_room
    eid = room_to_eid(room)
    if not eid:
        return None

    if re.search(r"\b(next|skip track|next track|next song)\b", t):
        ok = call_ha_service("media_player/media_next_track", {"entity_id": eid})
        return maybe_say("Skipping.") if ok else None

    if re.search(r"\b(previous|prev track|previous track|previous song)\b", t):
        ok = call_ha_service("media_player/media_previous_track", {"entity_id": eid})
        return maybe_say("Previous.") if ok else None

    if re.search(r"\bpause\b", t):
        ok = call_ha_service("media_player/media_pause", {"entity_id": eid})
        return maybe_say("Paused.") if ok else None

    if re.fullmatch(r"(resume|play)", t):
        ok = call_ha_service("media_player/media_play", {"entity_id": eid})
        return maybe_say("Playing.") if ok else None

    if re.search(r"\bstop\b", t):
        ok = call_ha_service("media_player/media_stop", {"entity_id": eid})
        return maybe_say("Stopped.") if ok else None

    return None

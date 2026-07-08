from typing import Optional

_transport_focus = {"kind": None, "entity_id": None, "ts": 0.0}
_transport_stopped_ts = {"tv": 0.0, "music": 0.0}


def get_transport_focus():
    """Return (kind, entity_id) for the current transport focus."""
    try:
        kind = (_transport_focus or {}).get("kind")
        eid = (_transport_focus or {}).get("entity_id")
    except Exception:
        return (None, None)

    try:
        if kind and not focus_is_valid(kind):
            return (None, None)
    except Exception:
        pass

    return (kind, eid)


def set_transport_focus(kind: str, entity_id: str, now_ts: float = None):
    """Set the transport focus (shared state used by now-playing + transport)."""
    try:
        if not kind or not entity_id:
            _transport_focus.update({"kind": None, "entity_id": None, "ts": 0.0})
            return

        if now_ts is None:
            import time as _time
            now_ts = float(_time.time())

        _transport_focus.update({"kind": kind, "entity_id": entity_id, "ts": float(now_ts)})
    except Exception:
        pass


def ha_ok(x) -> bool:
    # In dry-run/test paths, call_ha_service may return None; treat that as success.
    return True if x is None else bool(x)


def get_state(entity_id: str, states_snapshot) -> str:
    try:
        for s in (states_snapshot or []):
            if s.get("entity_id") == entity_id:
                return (s.get("state") or "").lower().strip()
    except Exception:
        pass
    return ""


def is_playing(entity_id: str, states_snapshot) -> bool:
    return get_state(entity_id, states_snapshot) == "playing"


def is_activeish(entity_id: str, states_snapshot) -> bool:
    st = get_state(entity_id, states_snapshot)
    return st in ("playing", "paused")


def pick_sonos_player(states_snapshot, sonos_players, default_room: str):
    try:
        default_eid = (sonos_players or {}).get(default_room)
        if default_eid and is_activeish(default_eid, states_snapshot):
            return default_eid
    except Exception:
        pass
    try:
        for _room, _eid in (sonos_players or {}).items():
            if _eid and is_activeish(_eid, states_snapshot):
                return _eid
    except Exception:
        pass
    return None


def mark_transport(kind: str, entity_id: str, verb: str):
    import time
    now = time.time()
    verb = (verb or "").lower().strip()

    key = "music" if kind == "sonos" else kind

    if verb == "stop":
        try:
            _transport_stopped_ts[key] = now
        except Exception:
            pass
        if _transport_focus.get("kind") == kind:
            _transport_focus.update({"kind": None, "entity_id": None, "ts": 0.0})
        return

    _transport_focus.update({"kind": kind, "entity_id": entity_id, "ts": now})


def focus_is_valid(kind: str) -> bool:
    try:
        fkind = _transport_focus.get("kind")
        fts = float(_transport_focus.get("ts") or 0.0)
        if fkind != kind or fts <= 0:
            return False
        key = "music" if kind == "sonos" else kind
        return float(_transport_stopped_ts.get(key) or 0.0) <= fts
    except Exception:
        return False


def call_media_transport(call_ha_service, entity_id: str, verb: str) -> bool:
    verb = (verb or "").lower().strip()
    if verb in ("play", "resume"):
        return ha_ok(call_ha_service("media_player/media_play", {"entity_id": entity_id}))
    if verb == "pause":
        return ha_ok(call_ha_service("media_player/media_pause", {"entity_id": entity_id}))
    if verb == "stop":
        ok = ha_ok(call_ha_service("media_player/media_stop", {"entity_id": entity_id}))
        if ok:
            return True
        return ha_ok(call_ha_service("media_player/media_pause", {"entity_id": entity_id}))
    return False


def get_state_obj(states_snapshot, entity_id: str):
    if not entity_id:
        return None
    for s in (states_snapshot or []):
        if s.get("entity_id") == entity_id:
            return s
    return None


def get_attr(states_snapshot, entity_id: str, key: str, default=None):
    st = get_state_obj(states_snapshot, entity_id) or {}
    attrs = st.get("attributes") or {}
    return attrs.get(key, default)


def get_state_str(states_snapshot, entity_id: str) -> str:
    st = get_state_obj(states_snapshot, entity_id) or {}
    return str(st.get("state") or "").strip().lower()


def ensure_apple_tv_awake(
    *,
    states_snapshot,
    call_ha_service,
    apple_tv_entity: str,
    tv_on_scene=None,
    force: bool = False,
    allow_play_fallback: bool = True,
) -> bool:
    """
    Wake Apple TV if needed.

    Why force exists:
      HA's Apple TV media_player state can be stale/misleading (often 'idle' or 'on')
      while the device is effectively asleep/not advertising apps like Plex. That breaks
      Plex /clients discovery. In watch-preflight we prefer to attempt a wake regardless.

    Returns True if we attempted a wake, else False.
    """
    st = get_state_str(states_snapshot, apple_tv_entity)

    if (not force) and st in ("playing", "paused"):
        return False

    did_attempt = False
    try:
        did_attempt = True
        call_ha_service("media_player/turn_on", {"entity_id": apple_tv_entity})
        return True
    except Exception:
        if not allow_play_fallback:
            return did_attempt
        try:
            did_attempt = True
            call_ha_service("media_player/media_play", {"entity_id": apple_tv_entity})
            return True
        except Exception:
            return did_attempt


def maybe_turn_on_tv_scene(*, call_ha_service, tv_on_scene: Optional[str], cooldown_s: int) -> bool:
    """
    Fire TV-on scene, rate-limited by cooldown.
    We cannot query physical TV power state; this prevents spamming the trigger while still
    allowing fast 'watch channel flipping' behavior.
    Returns True if we invoked scene/turn_on, else False.
    """
    if not tv_on_scene:
        return False
    try:
        cooldown_s = int(cooldown_s or 0)
    except Exception:
        cooldown_s = 0

    try:
        import time as _time
        now = float(_time.time())
    except Exception:
        now = 0.0

    key = "_piphone_last_tv_on_scene_ts"
    last = globals().get(key)
    if isinstance(last, (int, float)) and cooldown_s > 0:
        if (now - float(last)) < float(cooldown_s):
            return False

    try:
        call_ha_service("scene/turn_on", {"entity_id": tv_on_scene})
    except Exception:
        return False

    globals()[key] = now
    return True


def ensure_apple_tv_app(*, states_snapshot, call_ha_service, apple_tv_entity: str, desired_app: str, launch_script: str) -> bool:
    """Ensure a particular app is frontmost on ATV. Returns True if we invoked launch."""
    cur = str(get_attr(states_snapshot, apple_tv_entity, "app_name", "") or "").strip()
    if cur == desired_app:
        return False
    try:
        call_ha_service("script/turn_on", {"entity_id": launch_script})
        return True
    except Exception:
        return False


def get_local_transport_context(
    *,
    states_snapshot,
    apple_tv_entity: str,
    sonos_players,
    default_sonos_room: str,
    get_recent_transport_focus,
    get_last_paused_transport,
    is_sonos_tv_passthrough,
):
    """
    Build the local/default-room transport context used by bare transport and
    play-pause toggle logic.

    Notes:
    - "local" means Apple TV + default-room Sonos
    - passthrough detection remains caller-provided because Sonos TV/source
      semantics are currently shared across multiple subsystems but not yet
      centralized into one canonical helper module
    """
    tv_eid = apple_tv_entity
    sonos_eid = (sonos_players or {}).get(default_sonos_room)

    tv_state = get_state(tv_eid, states_snapshot)
    sonos_state = get_state(sonos_eid, states_snapshot) if sonos_eid else ""
    sonos_is_music = bool(sonos_eid) and (not is_sonos_tv_passthrough(sonos_eid))

    return {
        "tv_eid": tv_eid,
        "sonos_eid": sonos_eid,
        "tv_state": tv_state,
        "sonos_state": sonos_state,
        "sonos_is_music": sonos_is_music,
        "focus": get_recent_transport_focus(),
        "last_paused": get_last_paused_transport(),
    }


def decide_local_play_pause_toggle(ctx: dict):
    """
    Decide the target/action for the local-room play-pause toggle.

    Returns:
        ("pause"|"play", "tv"|"sonos", entity_id) or None
    """
    if not isinstance(ctx, dict):
        return None

    tv_eid = ctx.get("tv_eid")
    sonos_eid = ctx.get("sonos_eid")
    tv_state = ctx.get("tv_state")
    sonos_state = ctx.get("sonos_state")
    sonos_is_music = bool(ctx.get("sonos_is_music"))
    focus = ctx.get("focus")
    last = ctx.get("last_paused")

    # Pause-first: if something local is actively playing, toggle should pause it.
    if focus == "tv" and tv_eid and tv_state == "playing":
        return ("pause", "tv", tv_eid)

    if focus == "sonos" and sonos_eid and sonos_is_music and sonos_state == "playing":
        return ("pause", "sonos", sonos_eid)

    if sonos_eid and sonos_is_music and sonos_state == "playing":
        return ("pause", "sonos", sonos_eid)

    if tv_eid and tv_state == "playing":
        return ("pause", "tv", tv_eid)

    # Resume path: mirror current bare play/resume behavior as closely as possible.
    if last == "sonos" and sonos_eid and sonos_is_music and sonos_state in {"paused", "idle"}:
        return ("play", "sonos", sonos_eid)

    if last == "tv" and tv_eid and tv_state == "paused":
        return ("play", "tv", tv_eid)

    if sonos_eid and sonos_is_music and sonos_state == "paused":
        return ("play", "sonos", sonos_eid)

    if tv_eid and tv_state == "paused":
        return ("play", "tv", tv_eid)

    if last == "sonos" and sonos_eid and sonos_is_music and sonos_state == "idle":
        return ("play", "sonos", sonos_eid)

    return None

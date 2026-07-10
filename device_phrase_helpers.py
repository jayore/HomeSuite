from typing import Callable, Dict, Optional, Tuple
import re


def sanitize_device_phrase(device: str, *, logger=None) -> str:
    d = (device or "").strip().lower()

    # Remove filler words
    d = re.sub(r"\b(please|thanks|thank you)\b", "", d).strip()

    # Strip punctuation (keep alphanum + spaces)
    d = re.sub(r"[^\w\s]", "", d).strip()

    # Collapse whitespace
    d = re.sub(r"\s+", " ", d).strip()

    # -----------------------------
    # Phonetic token repairs (DEVICE/HA matching only)
    # -----------------------------
    # Deterministic rewrites for common STT mishearings.
    # Notes:
    #  - Applied only to device/scene phrase sanitation (not ChatGPT conversation).
    #  - Longest mishearing phrases win (multi-word before single-word).
    #  - Uses whole-token boundaries so we don't replace inside other words.
    try:
        cache = getattr(sanitize_device_phrase, "_phonetic_repairs_cache", None)
        if cache is None:
            try:
                from app_config import PHONETIC_DEVICE_REPAIRS as PHONETIC_TOKEN_REPAIRS
            except Exception:
                try:
                    from app_config import PHONETIC_TOKEN_REPAIRS
                except Exception:
                    PHONETIC_TOKEN_REPAIRS = {}

            def _norm_token(x: str) -> str:
                x = (x or "").strip().lower()
                x = re.sub(r"\b(please|thanks|thank you)\b", "", x).strip()
                x = re.sub(r"[^\w\s]", "", x).strip()
                x = re.sub(r"\s+", " ", x).strip()
                return x

            pairs = []
            if isinstance(PHONETIC_TOKEN_REPAIRS, dict):
                for intended, mis_list in (PHONETIC_TOKEN_REPAIRS or {}).items():
                    if not isinstance(intended, str):
                        continue
                    intended_n = _norm_token(intended)
                    if not intended_n:
                        continue
                    if not isinstance(mis_list, (list, tuple, set)):
                        continue
                    for mis in mis_list:
                        if not isinstance(mis, str):
                            continue
                        mis_n = _norm_token(mis)
                        if mis_n and mis_n != intended_n:
                            pairs.append((mis_n, intended_n))

            # Longest misheard phrases first (so "timing light" beats "timing")
            pairs.sort(key=lambda t: len(t[0]), reverse=True)

            compiled = []
            for mis_n, intended_n in pairs:
                pat = re.compile(r"(?<!\w)" + re.escape(mis_n) + r"(?!\w)")
                compiled.append((pat, intended_n, mis_n))

            cache = compiled
            setattr(sanitize_device_phrase, "_phonetic_repairs_cache", cache)

        changed = False
        before = d
        for pat, repl, mis_n in cache:
            if pat.search(d):
                d = pat.sub(repl, d)
                changed = True

        if changed:
            d = re.sub(r"\s+", " ", d).strip()
            try:
                if logger is not None:
                    logger.info("PHONETIC_REPAIR: %r -> %r", before, d)
            except Exception:
                pass
    except Exception:
        pass

    return d


def light_entity_id(device: str, *, logger=None) -> str:
    d = sanitize_device_phrase(device, logger=logger)
    return f"light.{d.replace(' ', '_')}"


def resolve_light_target(
    raw_target: str,
    *,
    light_phrase_overrides: Dict[str, str],
    get_recent_light: Callable[[], Optional[str]],
    entity_exists: Optional[Callable[[str], bool]] = None,
    allow_generated_light_ids: bool = False,
    logger=None,
) -> Tuple[Optional[str], bool]:
    pronouns = {"it", "that", "this", "them", "those", "these"}
    cleaned = sanitize_device_phrase(raw_target, logger=logger)

    # Hard overrides first (exact sanitized match)
    if cleaned in light_phrase_overrides:
        return light_phrase_overrides[cleaned], False

    if cleaned in pronouns or cleaned == "":
        recent = get_recent_light()
        if recent:
            return recent, True
        return None, True

    candidate = light_entity_id(raw_target, logger=logger)
    if allow_generated_light_ids:
        return candidate, False

    if callable(entity_exists):
        try:
            if entity_exists(candidate):
                return candidate, False
        except Exception:
            try:
                if logger is not None:
                    logger.exception("LIGHT_TARGET_VALIDATE_FAIL raw=%r candidate=%r", raw_target, candidate)
            except Exception:
                pass
            return None, False

        try:
            if logger is not None:
                logger.info(
                    "LIGHT_TARGET_REJECT_UNRESOLVED raw=%r cleaned=%r candidate=%r",
                    raw_target,
                    cleaned,
                    candidate,
                )
        except Exception:
            pass
        return None, False

    try:
        if logger is not None:
            logger.info(
                "LIGHT_TARGET_REJECT_UNVALIDATED raw=%r cleaned=%r candidate=%r",
                raw_target,
                cleaned,
                candidate,
            )
    except Exception:
        pass
    return None, False


def try_light_turn_on(entity_id: str, payloads: list, *, call_ha_service) -> bool:
    for data in payloads:
        d = {"entity_id": entity_id}
        d.update(data)
        if call_ha_service("light/turn_on", d):
            return True
    return False


def normalize_scene_phrase(s: str, *, logger=None) -> str:
    s = sanitize_device_phrase(s, logger=logger)
    s = re.sub(r"\b(scene|trigger|run|running|activate|activated)\b", "", s).strip()
    s = re.sub(r"\s+", " ", s).strip()
    return s

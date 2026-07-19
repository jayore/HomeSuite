"""Bounded conversational language contracts for deterministic commands.

This module makes ordinary phrasing easier to accept without making execution
fuzzy. It removes harmless request wrappers, canonicalizes a small set of safe
paraphrases, and resolves short follow-ups against a typed prior intent frame.
Every rewrite still re-enters HomeSuite's normal deterministic handlers, so
entity resolution, capability checks, confirmations, and live-state validation
remain authoritative.

The functions here are pure. Source scoping and expiry belong to
``dialogue_state``; command execution belongs to ``command_dispatch``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable, Mapping, Optional

from color_resolver import is_known_css_color


_LEADING_DISCOURSE = re.compile(
    r"^(?:ok(?:ay)?|alright|actually)\b[\s,;:.-]*",
    re.IGNORECASE,
)
_LEADING_REQUESTS = (
    re.compile(r"^would\s+you\s+mind(?:\s+please)?\s+", re.IGNORECASE),
    re.compile(r"^(?:could|would|can|will)\s+you(?:\s+please)?\s+", re.IGNORECASE),
    re.compile(r"^(?:do\s+me\s+a\s+favor\s+and|i\s+need\s+you\s+to|i(?:'d|\s+would)\s+like\s+you\s+to)\s+", re.IGNORECASE),
    re.compile(r"^go\s+ahead\s+and\s+", re.IGNORECASE),
    re.compile(r"^please\s+", re.IGNORECASE),
)
_TRAILING_COURTESY = re.compile(
    r"(?:\s*[,;:-]?\s*)(?:please|for\s+me|real\s+quick|thanks|thank\s+you)\s*[.?!]*$",
    re.IGNORECASE,
)
_CONTENT_BEARING_PREFIX = re.compile(
    r"^(?:say|announce|play|watch|listen|remind|add|create|schedule)\b",
    re.IGNORECASE,
)
_TRAILING_PUNCTUATION = re.compile(r"[\s.!,?]+$")

_DEGERUND = {
    "closing": "close",
    "dimming": "dim",
    "locking": "lock",
    "making": "make",
    "muting": "mute",
    "opening": "open",
    "pausing": "pause",
    "playing": "play",
    "setting": "set",
    "stopping": "stop",
    "switching": "switch",
    "turning": "turn",
    "unlocking": "unlock",
}

_WEEKDAYS = (
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
)
_PLANETS = {
    "mercury",
    "venus",
    "mars",
    "jupiter",
    "saturn",
    "uranus",
    "neptune",
}
_TEMPORAL_REFINEMENT = re.compile(
    r"^(?:today|tomorrow|tonight|later\s+today|"
    r"(?:this|next)\s+(?:week|weekend)|"
    r"(?:next\s+)?(?:" + "|".join(_WEEKDAYS) + r")|"
    r"(?:the\s+)?next\s+(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|twelve)\s+(?:hours?|days?))$"
)
_FULL_COMMAND_PREFIX = re.compile(
    r"^(?:turn|set|switch|power|shut|toggle|lock|unlock|open|close|play|pause|"
    r"resume|stop|start|run|announce|say|remind|cancel|delete|create)\b"
)
_DIRECTIONAL_CONTINUATION = re.compile(
    r"^(?:more|(?:just\s+)?(?:a\s+)?(?:little|bit)\s+more|some\s+more|"
    r"even\s+more|one\s+more|keep\s+going)$"
)


@dataclass(frozen=True)
class IntentFrame:
    """The semantic shape of one successfully claimed deterministic turn."""

    domain: str
    intent: str
    canonical_command: str
    slots: Mapping[str, Any] = field(default_factory=dict)
    target_keys: tuple[str, ...] = ()
    followups: frozenset[str] = frozenset()

    def to_data(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "intent": self.intent,
            "canonical_command": self.canonical_command,
            "slots": dict(self.slots),
            "target_keys": list(self.target_keys),
            "followups": sorted(self.followups),
        }

    @classmethod
    def from_data(cls, data: Mapping[str, Any]) -> Optional["IntentFrame"]:
        domain = str(data.get("domain") or "").strip().lower()
        intent = str(data.get("intent") or "").strip().lower()
        command = str(data.get("canonical_command") or "").strip()
        if not domain or not intent or not command:
            return None
        return cls(
            domain=domain,
            intent=intent,
            canonical_command=command,
            slots=dict(data.get("slots") or {}),
            target_keys=tuple(str(value) for value in (data.get("target_keys") or ()) if str(value)),
            followups=frozenset(
                str(value).strip().lower()
                for value in (data.get("followups") or ())
                if str(value).strip()
            ),
        )


@dataclass(frozen=True)
class FollowupResolution:
    rewritten_text: str
    kind: str


def _squash_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _norm(text: str) -> str:
    value = _squash_spaces(text).lower().replace("’", "'")
    return _TRAILING_PUNCTUATION.sub("", value).strip()


def _canonical_duration_unit(raw_unit: str, amount: str) -> str:
    unit = _norm(raw_unit)
    if unit.startswith("sec"):
        base = "second"
    elif unit.startswith("min"):
        base = "minute"
    else:
        base = "hour"
    return base if amount in {"1", "one"} else base + "s"


def normalize_conversational_shell(text: str) -> str:
    """Remove bounded politeness/discourse wrappers without touching payloads."""
    value = _squash_spaces(text)
    if not value:
        return value

    mind_prefix_removed = False
    for _ in range(5):
        previous = value
        value = _LEADING_DISCOURSE.sub("", value, count=1).strip()
        for pattern in _LEADING_REQUESTS:
            match = pattern.match(value)
            if not match:
                continue
            if pattern.pattern.startswith("^would\\s+you\\s+mind"):
                mind_prefix_removed = True
            value = value[match.end():].strip()
            break
        if value == previous:
            break

    if mind_prefix_removed:
        first, separator, remainder = value.partition(" ")
        replacement = _DEGERUND.get(first.lower())
        if replacement:
            value = replacement + (separator + remainder if separator else "")

    # Do not trim message content from "say ..." or "announce ..." commands.
    if not _CONTENT_BEARING_PREFIX.match(value):
        for _ in range(2):
            trimmed = _TRAILING_COURTESY.sub("", value).strip()
            if trimmed == value:
                break
            value = trimmed

    return _squash_spaces(value)


def _semantic_paraphrase(text: str) -> str:
    """Canonicalize only paraphrases whose execution meaning is unambiguous."""
    original = _squash_spaces(text)
    t = _norm(original)
    if not t:
        return original

    duration = (
        r"(?P<amount>\d+|a|an|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve|thirteen|fourteen|fifteen|twenty|thirty|forty|forty-five)\s+"
        r"(?P<unit>seconds?|secs?|minutes?|mins?|hours?|hrs?)"
    )
    match = re.fullmatch(rf"give\s+me\s+{duration}", t)
    if match:
        amount = "one" if match.group("amount") in {"a", "an"} else match.group("amount")
        unit = _canonical_duration_unit(match.group("unit"), amount)
        return f"set a timer for {amount} {unit}"

    match = re.fullmatch(rf"(?:set|start|create)\s+(?:a|an)?\s*{duration}\s+timer", t)
    if match:
        amount = "one" if match.group("amount") in {"a", "an"} else match.group("amount")
        unit = _canonical_duration_unit(match.group("unit"), amount)
        return f"set a timer for {amount} {unit}"

    match = re.fullmatch(r"(?:switch|power)\s+(on|off)\s+(?:the\s+)?(.+)", t)
    if match:
        return f"turn {match.group(2).strip()} {match.group(1)}"

    match = re.fullmatch(r"(?:switch|power)\s+(?:the\s+)?(.+?)\s+(on|off)", t)
    if match:
        return f"turn {match.group(1).strip()} {match.group(2)}"

    match = re.fullmatch(r"shut\s+off\s+(?:the\s+)?(.+)", t)
    if match:
        return f"turn {match.group(1).strip()} off"

    match = re.fullmatch(r"shut\s+(?:the\s+)?(.+?)\s+off", t)
    if match:
        return f"turn {match.group(1).strip()} off"

    level_words = {
        "half": 50,
        "half brightness": 50,
        "full": 100,
        "full brightness": 100,
        "maximum": 100,
        "max": 100,
        "all the way up": 100,
        "minimum": 0,
        "min": 0,
        "all the way down": 0,
    }

    volume_match = re.fullmatch(
        r"(?:set|make|turn)\s+(?:the\s+)?(.+?)\s+volumes?\s+(?:to\s+)?"
        r"(half|full|maximum|max|minimum|min|all\s+the\s+way\s+(?:up|down))",
        t,
    )
    if volume_match:
        value = level_words.get(volume_match.group(2))
        if value is not None:
            return f"set {volume_match.group(1).strip()} volume to {value}%"

    volume_match = re.fullmatch(
        r"(?:set|make)\s+(?:the\s+)?(.+?)\s+(?:to\s+)?(half|full)\s+volume",
        t,
    )
    if volume_match:
        value = level_words.get(volume_match.group(2))
        if value is not None:
            return f"set {volume_match.group(1).strip()} volume to {value}%"

    volume_match = re.fullmatch(
        r"(?:set\s+)?volumes?\s+(?:to\s+)?"
        r"(half|full|maximum|max|minimum|min|all\s+the\s+way\s+(?:up|down))",
        t,
    )
    if volume_match:
        value = level_words.get(volume_match.group(1))
        if value is not None:
            return f"set volume to {value}%"

    match = re.fullmatch(
        r"(?:set|make|turn)\s+(?:the\s+)?(.+?)\s+(?:to\s+)?"
        r"(half(?:\s+brightness)?|full(?:\s+brightness)?|maximum|max|minimum|min|all\s+the\s+way\s+(?:up|down))",
        t,
    )
    if match:
        value = level_words.get(match.group(2))
        if value is not None:
            return f"set {match.group(1).strip()} to {value}%"

    match = re.fullmatch(
        r"(?:set\s+)?brightness\s+(?:to\s+)?"
        r"(half|full|maximum|max|minimum|min|all\s+the\s+way\s+(?:up|down))",
        t,
    )
    if match:
        value = level_words.get(match.group(1))
        if value is not None:
            return f"set brightness to {value}%"

    return original


def normalize_conversational_command(text: str) -> str:
    """Apply the shared shell and safe semantic paraphrase passes once."""
    return _semantic_paraphrase(normalize_conversational_shell(text))


def _target(value: str) -> str:
    result = _norm(value)
    result = re.sub(r"^(?:the|my)\s+", "", result).strip()
    return result


def _frame(
    domain: str,
    intent: str,
    command: str,
    slots: Mapping[str, Any],
    followups: Iterable[str],
    target_keys: Iterable[str],
) -> IntentFrame:
    return IntentFrame(
        domain=domain,
        intent=intent,
        canonical_command=_norm(command),
        slots=dict(slots),
        target_keys=tuple(str(value) for value in target_keys if str(value)),
        followups=frozenset(followups),
    )


def build_intent_frame(
    claim: str,
    text: str,
    *,
    metadata: Optional[Mapping[str, Any]] = None,
    target_keys: Iterable[str] = (),
) -> Optional[IntentFrame]:
    """Build a typed frame only for an explicitly successful handler claim."""
    claim_n = _norm(claim).replace(" ", "_")
    t = _norm(text)
    meta = dict(metadata or {})
    if not claim_n or not t:
        return None

    if claim_n == "binary":
        match = re.fullmatch(r"turn\s+(on|off)\s+(?:the\s+)?(.+)", t)
        if match:
            state, raw_target = match.group(1), _target(match.group(2))
        else:
            match = re.fullmatch(r"turn\s+(?:the\s+)?(.+?)\s+(on|off)", t)
            if not match:
                return None
            raw_target, state = _target(match.group(1)), match.group(2)
        if not raw_target:
            return None
        return _frame(
            "device",
            f"turn_{state}",
            f"turn {raw_target} {state}",
            {"target": raw_target, "state": state},
            {"target_transfer", "repeat"},
            target_keys,
        )

    if claim_n == "color":
        patterns = (
            re.compile(r"(?:set|make|change|put|adjust|turn)\s+(?:the\s+)?(.+?)\s+colors?\s+to\s+(.+)$"),
            re.compile(r"(?:set|make|change|put|adjust|turn)\s+(?:the\s+)?(.+?)\s+to\s+(.+)$"),
            re.compile(r"(?:set|make|change|put|adjust|turn)\s+(?:the\s+)?(.+?)\s+([a-z]+)$"),
        )
        raw_target = ""
        value = ""
        global_match = re.fullmatch(r"(?:set\s+)?color\s+(?:to\s+)?(.+)", t)
        if global_match:
            raw_target, value = "it", _norm(global_match.group(1))
        else:
            for pattern in patterns:
                match = pattern.fullmatch(t)
                if match:
                    raw_target, value = _target(match.group(1)), _norm(match.group(2))
                    break
        if not raw_target or not value:
            return None
        return _frame(
            "light",
            "set_color",
            f"set {raw_target} to {value}",
            {"target": raw_target, "value": value},
            {"target_transfer", "value_correction", "repeat"},
            target_keys,
        )

    if claim_n == "brightness":
        patterns = (
            re.compile(r"set\s+(?:the\s+)?(.+?)\s+brightness(?:es)?\s+(?:to\s+)?(\d{1,3})\s*%?"),
            re.compile(r"set\s+(?:the\s+)?(.+?)\s+to\s+(\d{1,3})\s*%"),
        )
        raw_target = ""
        value: Optional[int] = None
        global_match = re.fullmatch(r"(?:set\s+)?brightness(?:es)?\s+(?:to\s+)?(\d{1,3})\s*%?", t)
        if global_match:
            value = int(global_match.group(1))
        else:
            for pattern in patterns:
                match = pattern.fullmatch(t)
                if match:
                    raw_target = _target(match.group(1))
                    value = int(match.group(2))
                    break
        if value is not None:
            value = max(0, min(100, value))
            command = (
                f"set {raw_target} brightness to {value}%"
                if raw_target
                else f"set brightness to {value}%"
            )
            return _frame(
                "light",
                "set_brightness",
                command,
                {"target": raw_target, "value": value},
                {"target_transfer", "value_correction", "repeat"},
                target_keys,
            )

        if re.search(
            r"\b(?:brighter|brighten(?:\s+up)?|brightness\s+up|"
            r"increase\b.*\bbrightness|more\s+bright(?:er)?|get\s+bright(?:er)?|"
            r"up\s+(?:the\s+)?brightness)\b",
            t,
        ):
            direction = "brighter"
        elif re.search(
            r"\b(?:dimmer|dim|darker|brightness\s+down|decrease\b.*\bbrightness|"
            r"lower\b.*\bbrightness|less\s+bright(?:er)?|not\s+so\s+bright|"
            r"more\s+dim(?:mer)?|get\s+dim(?:mer)?|down\s+(?:the\s+)?brightness)\b",
            t,
        ):
            direction = "dimmer"
        else:
            return None
        target_match = re.search(
            r"(?:make|turn)\s+(?:the\s+)?(.+?)\s+"
            r"(?:brighter|dimmer|darker|less\s+bright|not\s+so\s+bright)",
            t,
        )
        if not target_match:
            target_match = re.search(
                r"(?:increase|decrease|lower)\s+(?:the\s+)?(.+?)\s+brightness",
                t,
            )
        if not target_match:
            target_match = re.fullmatch(
                r"(?:turn\s+)?(?:the\s+)?(.+?)\s+brightness\s+(?:up|down)",
                t,
            )
        raw_target = _target(target_match.group(1)) if target_match else "it"
        amount_match = re.search(r"\bby\s+(\d{1,3})\b", t)
        amount = max(1, min(100, int(amount_match.group(1)))) if amount_match else None
        command = f"make {raw_target} {direction}"
        if amount is not None:
            command += f" by {amount}"
        return _frame(
            "light",
            "adjust_brightness",
            command,
            {"target": raw_target, "direction": direction, "amount": amount},
            {"target_transfer", "repeat", "continue_adjustment"},
            target_keys,
        )

    if claim_n == "volume":
        patterns = (
            re.compile(r"set\s+volumes?\s+(?:in|on)\s+(?:the\s+)?(.+?)\s+(?:to\s+)?(\d{1,3})\s*%?"),
            re.compile(r"set\s+(?:the\s+)?(.+?)\s+volumes?\s+(?:to\s+)?(\d{1,3})\s*%?"),
        )
        raw_target = ""
        value = None
        global_match = re.fullmatch(r"(?:set\s+)?volumes?\s+(?:to\s+)?(\d{1,3})\s*%?", t)
        if global_match:
            value = int(global_match.group(1))
        else:
            for pattern in patterns:
                match = pattern.fullmatch(t)
                if match:
                    raw_target = _target(match.group(1))
                    value = int(match.group(2))
                    break
        if value is not None:
            value = max(0, min(100, value))
            command = f"set {raw_target} volume to {value}%" if raw_target else f"set volume to {value}%"
            return _frame(
                "media",
                "set_volume",
                command,
                {"target": raw_target, "value": value},
                {"target_transfer", "value_correction", "repeat"},
                target_keys,
            )

        if re.search(r"\blouder\b|\bvolume\s+up\b|\bincrease\b.*\bvolume\b", t):
            direction = "louder"
        elif re.search(r"\bquieter\b|\bvolume\s+down\b|\bdecrease\b.*\bvolume\b|\bnot\s+so\s+loud\b", t):
            direction = "quieter"
        else:
            return None
        target_match = re.search(
            r"(?:make|turn)\s+(?:the\s+)?(.+?)\s+"
            r"(?:louder|quieter|not\s+so\s+loud|less\s+loud)",
            t,
        ) or re.search(r"(?:increase|decrease)\s+(?:the\s+)?(.+?)\s+volume", t)
        if not target_match:
            target_match = re.fullmatch(
                r"(?:turn\s+)?(?:the\s+)?(.+?)\s+volume\s+(?:up|down)",
                t,
            )
        if not target_match:
            target_match = re.fullmatch(
                r"(?:volume\s+(?:up|down)|louder|quieter)\s+"
                r"(?:in|on)\s+(?:the\s+)?(.+)",
                t,
            )
        raw_target = _target(target_match.group(1)) if target_match else ""
        amount_match = re.search(r"\bby\s+(\d{1,3})\b", t)
        amount = max(1, min(100, int(amount_match.group(1)))) if amount_match else None
        if amount is not None:
            verb = "increase" if direction == "louder" else "decrease"
            command = f"{verb} {raw_target + ' ' if raw_target else ''}volume by {amount}"
        else:
            command = f"make {raw_target} {direction}" if raw_target else direction
        return _frame(
            "media",
            "adjust_volume",
            command,
            {"target": raw_target, "direction": direction, "amount": amount},
            {"target_transfer", "repeat", "continue_adjustment"},
            target_keys,
        )

    if claim_n == "weather":
        return _frame(
            "weather",
            "weather_query",
            t,
            meta,
            {"query_refinement"},
            target_keys,
        )

    if claim_n == "astronomy":
        intent = _norm(str(meta.get("intent") or "astronomy_query"))
        return _frame(
            "astronomy",
            intent,
            t,
            meta,
            {"query_refinement"},
            target_keys,
        )

    if claim_n == "calendar":
        return _frame(
            "calendar",
            "calendar_query",
            t,
            meta,
            {"query_refinement"},
            target_keys,
        )

    if claim_n == "timer":
        match = re.fullmatch(
            r"(?:set\s+)?(?:(?:an|a)\s+)?(?:(?P<label>[a-z0-9][a-z0-9\s-]*?)\s+)?"
            r"timer\s+for\s+(?P<amount>\d+|[a-z]+(?:\s+[a-z]+)?)\s+"
            r"(?P<unit>seconds?|secs?|minutes?|mins?|hours?|hrs?)",
            t,
        )
        if not match:
            return None
        unit = match.group("unit")
        if unit.startswith("sec"):
            unit = "seconds"
        elif unit.startswith("min"):
            unit = "minutes"
        else:
            unit = "hours"
        return _frame(
            "timer",
            "create_timer",
            t,
            {
                "label": _norm(match.group("label") or ""),
                "amount": _norm(match.group("amount")),
                "unit": unit,
            },
            {"duration_adjustment", "value_correction"},
            target_keys,
        )

    return None


def _valid_transfer_target(value: str) -> bool:
    target = _target(value)
    if not target or target in {"it", "that", "this", "there", "here", "me"}:
        return False
    if target in {
        "that's all",
        "thats all",
        "that's it",
        "thats it",
        "nothing else",
        "never mind",
        "nevermind",
        "thank you",
        "thanks",
    }:
        return False
    tokens = target.split()
    if not 1 <= len(tokens) <= 7:
        return False
    blocked = {
        "add", "adjust", "announce", "brighten", "cancel", "change", "close",
        "create", "decrease", "delete", "dim", "do", "give", "increase",
        "lock", "lower", "make", "mute", "open", "pause", "play", "power",
        "raise", "remind", "resume", "run", "say", "set", "start", "stop",
        "switch", "turn", "unlock", "unmute",
        "today", "tomorrow", "tonight", "why", "what", "when", "where", "how",
    }
    return not any(token in blocked for token in tokens)


def _render_target_transfer(
    frame: IntentFrame,
    target: str,
    *,
    room_targets: frozenset[str] = frozenset(),
) -> Optional[str]:
    target = _target(target)
    slots = dict(frame.slots)
    if not _valid_transfer_target(target):
        return None
    is_room = target in room_targets
    if frame.intent in {"turn_on", "turn_off"}:
        prior_target = _target(str(slots.get("target") or ""))
        prior_was_light = bool(
            any(key.startswith("light.") for key in frame.target_keys)
            or re.search(r"\b(?:light|lights|lamp|lamps)\b", prior_target)
        )
        command_target = f"{target} lights" if is_room and prior_was_light else target
        return f"turn {command_target} {slots.get('state')}"
    if frame.intent == "set_color":
        command_target = f"{target} lights" if is_room else target
        return f"set {command_target} to {slots.get('value')}"
    if frame.intent == "set_brightness":
        if is_room:
            return f"set {target} lights to {int(slots.get('value'))}%"
        return f"set {target} brightness to {int(slots.get('value'))}%"
    if frame.intent == "adjust_brightness":
        command = f"make the {target} {slots.get('direction')}"
        if slots.get("amount") is not None:
            command += f" by {int(slots.get('amount'))}"
        return command
    if frame.intent == "set_volume":
        return f"set {target} volume to {int(slots.get('value'))}%"
    if frame.intent == "adjust_volume":
        if slots.get("amount") is not None:
            verb = "increase" if slots.get("direction") == "louder" else "decrease"
            return f"{verb} {target} volume by {int(slots.get('amount'))}"
        return f"make the {target} {slots.get('direction')}"
    return None


def _level_value(value: str) -> Optional[int]:
    cleaned = _norm(value)
    aliases = {
        "half": 50,
        "half brightness": 50,
        "half volume": 50,
        "full": 100,
        "full brightness": 100,
        "full volume": 100,
        "maximum": 100,
        "max": 100,
        "all the way up": 100,
        "minimum": 0,
        "min": 0,
        "all the way down": 0,
    }
    if cleaned in aliases:
        return aliases[cleaned]
    match = re.fullmatch(r"(\d{1,3})\s*(?:%|percent)?", cleaned)
    if match:
        return max(0, min(100, int(match.group(1))))
    return None


def _render_value_correction(frame: IntentFrame, value: str) -> Optional[str]:
    cleaned = _norm(value)
    cleaned = re.sub(r"\s+instead$", "", cleaned).strip()
    if not cleaned:
        return None
    slots = dict(frame.slots)
    target = _target(str(slots.get("target") or "it")) or "it"

    if frame.intent == "set_color":
        cleaned = re.sub(r"^(?:the\s+)?color\s+(?:to\s+|of\s+)?", "", cleaned).strip()
        if is_known_css_color(cleaned) or cleaned.startswith("the color of "):
            return f"set {target} to {cleaned}"
        return None

    if frame.intent in {"set_brightness", "set_volume"}:
        numeric = _level_value(cleaned)
        if numeric is None:
            return None
        noun = "brightness" if frame.intent == "set_brightness" else "volume"
        if target == "it" and not slots.get("target"):
            return f"set {noun} to {numeric}%"
        return f"set {target} {noun} to {numeric}%"

    if frame.intent == "create_timer":
        unit = str(slots.get("unit") or "minutes")
        if re.search(r"\b(?:seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", cleaned):
            duration = cleaned
        elif re.fullmatch(r"\d+|[a-z]+(?:\s+[a-z]+)?", cleaned):
            duration = f"{cleaned} {unit}"
        else:
            return None
        return f"set it to {duration}"

    return None


def _weather_refinement(frame: IntentFrame, subject: str) -> Optional[str]:
    subject = _norm(subject)
    if not subject:
        return None
    slots = dict(frame.slots)
    location = _norm(str(slots.get("location") or ""))

    if subject.startswith("in "):
        return f"what's the weather {subject}"

    if _TEMPORAL_REFINEMENT.fullmatch(subject):
        command = f"weather {subject}"
        if location:
            command += f" in {location}"
        return command

    precipitation = re.fullmatch(r"(?:rain|snow)(?:\s+(.+))?", subject)
    if precipitation:
        command = f"will it {precipitation.group(0)}"
        if location:
            command += f" in {location}"
        return command

    if re.fullmatch(r"[a-z0-9][a-z0-9\s.'-]{0,80}", subject):
        return f"what's the weather in {subject}"
    return None


def _astronomy_day_phrase(slots: Mapping[str, Any]) -> str:
    if bool(slots.get("night_window")):
        return "tonight"
    if slots.get("day_offset") == 1:
        return "tomorrow"
    if slots.get("day_offset") == 0 and bool(slots.get("explicit_day")):
        return "today"
    weekday = slots.get("weekday")
    if isinstance(weekday, int) and 0 <= weekday < len(_WEEKDAYS):
        prefix = "next " if bool(slots.get("next_weekday")) else ""
        return prefix + _WEEKDAYS[weekday]
    return ""


def _astronomy_refinement(frame: IntentFrame, subject: str) -> Optional[str]:
    subject = _norm(subject)
    if not subject:
        return None
    slots = dict(frame.slots)
    intent = frame.intent
    planet = _norm(str(slots.get("planet") or ""))
    event = _norm(str(slots.get("event") or ""))
    prior_day = _astronomy_day_phrase(slots)

    if _TEMPORAL_REFINEMENT.fullmatch(subject):
        if intent == "visible_planets":
            return f"what planets are visible {subject}"
        if intent == "planet_event" and planet and event:
            return f"when does {planet} {event} {subject}"
        if intent in {"planet_visible", "planet_up"} and planet:
            predicate = "visible" if intent == "planet_visible" else "up"
            return f"is {planet} {predicate} {subject}"
        if intent == "planet_best" and planet:
            return f"when is the best time to see {planet} {subject}"
        if intent == "planet_position" and planet:
            return f"where is {planet} {subject}"
        if event:
            return f"when is {event} {subject}"
        return None

    if subject in _PLANETS:
        suffix = f" {prior_day}" if prior_day else ""
        if intent == "planet_event" and event:
            return f"when does {subject} {event}{suffix}"
        if intent == "planet_up":
            return f"is {subject} up{suffix}"
        if intent == "planet_best":
            return f"when is the best time to see {subject}{suffix}"
        if intent == "planet_position":
            return f"where is {subject}{suffix}"
        return f"is {subject} visible{suffix}"
    return None


def _calendar_refinement(subject: str) -> Optional[str]:
    subject = _norm(subject)
    if _TEMPORAL_REFINEMENT.fullmatch(subject):
        return f"what's on my calendar {subject}"
    if subject in {"next", "the next one", "my next event", "my next appointment"}:
        return "what's my next event"
    return None


def resolve_intent_followup(
    text: str,
    frame: IntentFrame,
    *,
    room_targets: Iterable[str] = (),
) -> Optional[FollowupResolution]:
    """Rewrite a bounded short follow-up using one compatible typed frame."""
    t = _norm(normalize_conversational_shell(text))
    if not t or _FULL_COMMAND_PREFIX.match(t):
        return None
    rooms = frozenset(_target(room) for room in room_targets if _target(room))

    # "Now <value>" and "now <target>" share the same conversational shell.
    # Give a typed value correction first refusal; if the value is invalid for
    # the prior intent, target-transfer rules below can still claim the phrase.
    now_match = re.fullmatch(r"now\s+(?:the\s+)?(.+)", t)
    if now_match and "value_correction" in frame.followups:
        rewritten = _render_value_correction(frame, now_match.group(1))
        if rewritten:
            return FollowupResolution(rewritten, "value_correction")

    if "target_transfer" in frame.followups:
        # Natural handoff after a completed action: "now the side lamp". Keep
        # this narrower than a generic "now <word>" target rule so unrecognized
        # values do not become fabricated device names.
        if now_match:
            candidate = _target(now_match.group(1))
            if candidate in rooms or re.search(r"\b(?:light|lights|lamp|lamps)\b", candidate):
                rewritten = _render_target_transfer(
                    frame,
                    candidate,
                    room_targets=rooms,
                )
                if rewritten:
                    return FollowupResolution(rewritten, "target_transfer")

        patterns = (
            re.compile(r"^(?:and\s+)?(?:do\s+)?(?:the\s+)?same(?:\s+thing)?\s+(?:in|for|to|with|on)\s+(?:the\s+)?(.+)$"),
            re.compile(r"^(?:(?:and|also)\s+)?(?:do\s+)?(?:that|it)\s+(?:in|for|to|with|on)\s+(?:the\s+)?(.+?)(?:\s+too)?$"),
            re.compile(r"^(?:and|also)\s+(?:in|for|to|with|on)\s+(?:the\s+)?(.+?)(?:\s+(?:too|also|as\s+well))?$"),
            re.compile(r"^(?:in|for|to|with|on)\s+(?:the\s+)?(.+?)\s+(?:too|also|as\s+well)$"),
            re.compile(r"^(?:and|also)\s+(?:do\s+)?(?:the\s+)?(.+?)(?:\s+(?:too|also|as\s+well))?$"),
            re.compile(r"^(?:and\s+)?(?:the\s+)?(.+?)\s+(?:too|also|as\s+well)$"),
        )
        for pattern in patterns:
            match = pattern.fullmatch(t)
            if not match:
                continue
            rewritten = _render_target_transfer(
                frame,
                match.group(1),
                room_targets=rooms,
            )
            if rewritten:
                return FollowupResolution(rewritten, "target_transfer")

    if "value_correction" in frame.followups:
        correction_patterns = (
            re.compile(r"^(?:actually\s*,?\s*)?(?:make|set|change)\s+(?:it|that)(?:\s+one)?\s+(?:to\s+)?(.+)$"),
            re.compile(r"^actually\s*,?\s*make\s+that\s+(.+)$"),
            re.compile(r"^(?:i\s+mean|i\s+meant)\s+(.+)$"),
        )
        for pattern in correction_patterns:
            match = pattern.fullmatch(t)
            if not match:
                continue
            rewritten = _render_value_correction(frame, match.group(1))
            if rewritten:
                return FollowupResolution(rewritten, "value_correction")

        if frame.intent == "set_color" and is_known_css_color(t):
            rewritten = _render_value_correction(frame, t)
            if rewritten:
                return FollowupResolution(rewritten, "value_correction")

    if "duration_adjustment" in frame.followups and frame.intent == "create_timer":
        match = re.fullmatch(
            r"(?:give\s+me\s+)?(?:another|an\s+extra)\s+"
            r"(?:(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+)?"
            r"(seconds?|secs?|minutes?|mins?|hours?|hrs?)",
            t,
        )
        if match:
            amount = match.group(1) or "one"
            return FollowupResolution(
                f"add {amount} {match.group(2)} to it",
                "duration_adjustment",
            )

    if "query_refinement" in frame.followups and frame.domain == "weather":
        match = re.fullmatch(r"(?:and\s+)?(?:what|how)\s+about\s+(.+)", t)
        if not match:
            match = re.fullmatch(r"actually\s*,?\s*(.+)", t)
        if not match:
            match = re.fullmatch(r"(?:i\s+mean|i\s+meant)\s+(.+)", t)
        subject = match.group(1) if match else re.sub(r"^and\s+", "", t).strip()
        if match or _TEMPORAL_REFINEMENT.fullmatch(subject):
            rewritten = _weather_refinement(frame, subject)
            if rewritten:
                return FollowupResolution(rewritten, "query_refinement")

    if "query_refinement" in frame.followups and frame.domain == "astronomy":
        match = re.fullmatch(r"(?:and\s+)?(?:what|how)\s+about\s+(.+)", t)
        if not match:
            match = re.fullmatch(r"(?:i\s+mean|i\s+meant)\s+(.+)", t)
        subject = match.group(1) if match else re.sub(r"^and\s+", "", t).strip()
        if match or subject in _PLANETS or _TEMPORAL_REFINEMENT.fullmatch(subject):
            rewritten = _astronomy_refinement(frame, subject)
            if rewritten:
                return FollowupResolution(rewritten, "query_refinement")

    if "query_refinement" in frame.followups and frame.domain == "calendar":
        match = re.fullmatch(r"(?:and\s+)?(?:what|how)\s+about\s+(.+)", t)
        if not match:
            match = re.fullmatch(r"(?:i\s+mean|i\s+meant)\s+(.+)", t)
        subject = match.group(1) if match else re.sub(r"^and\s+", "", t).strip()
        if match or _TEMPORAL_REFINEMENT.fullmatch(subject) or subject == "next":
            rewritten = _calendar_refinement(subject)
            if rewritten:
                return FollowupResolution(rewritten, "query_refinement")

    if (
        "continue_adjustment" in frame.followups
        and frame.intent in {"adjust_brightness", "adjust_volume"}
        and _DIRECTIONAL_CONTINUATION.fullmatch(t)
    ):
        return FollowupResolution(frame.canonical_command, "continue_adjustment")

    if "repeat" in frame.followups and t in {
        "same",
        "same thing",
        "do that again",
        "do it again",
        "again",
    }:
        return FollowupResolution(frame.canonical_command, "repeat")

    return None

"""Detect narrowly ambiguous device names before executing them."""

from __future__ import annotations

from dataclasses import dataclass
import re
import string
from typing import Callable, Iterable, Mapping, Optional

from clarification_controls import ClarificationOption
from color_resolver import is_known_css_color
from on_off_controls import supports_binary_action


_NON_SPECIFIC_CONTEXT_TARGETS = {
    "all lights",
    "brightness",
    "color",
    "colour",
    "every light",
    "it",
    "light",
    "lights",
    "music",
    "speaker",
    "speakers",
    "that",
    "this",
    "tv",
    "television",
}
_BAD_SURFACE_TOKENS = {
    "effect",
    "flicker",
    "preset",
    "scene",
    "trigger",
    "underwater",
}
_SURFACE_PUNCTUATION = str.maketrans({character: " " for character in string.punctuation})


@dataclass(frozen=True)
class DeviceClarification:
    prompt: str
    options: tuple[ClarificationOption, ...] = ()


def _norm(value: str) -> str:
    text = str(value or "").strip().lower().replace("_", " ")
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _surface_norm(value: str) -> str:
    """Cheap normalization for already-canonical Home Assistant names."""
    text = str(value or "").strip().lower().replace("_", " ")
    return " ".join(text.translate(_SURFACE_PUNCTUATION).split())


def _binary_parts(text: str) -> Optional[tuple[str, str]]:
    t = _norm(text)
    match = re.fullmatch(r"turn\s+(on|off)\s+(?:the\s+)?(.+)", t)
    if match:
        return match.group(1), _norm(match.group(2))
    match = re.fullmatch(r"turn\s+(?:the\s+)?(.+?)\s+(on|off)", t)
    if match:
        return match.group(2), _norm(match.group(1))
    return None


def _has_configured_alias(target: str, aliases_by_entity: Mapping[str, Iterable[str]]) -> bool:
    for entity_id, aliases in (aliases_by_entity or {}).items():
        surfaces = list(aliases or ()) if not isinstance(aliases, str) else [aliases]
        if "." in str(entity_id):
            surfaces.append(str(entity_id).split(".", 1)[1])
        if any(_norm(surface) == target for surface in surfaces):
            return True
    return False


def _candidate_rows(
    target: str,
    states_snapshot,
    *,
    light_only: bool = False,
    max_partial_rows: Optional[int] = None,
) -> list[dict]:
    target_tokens = frozenset(target.split())
    exact_rows = []
    eligible = []
    seen = set()
    for state in states_snapshot or ():
        if not isinstance(state, dict):
            continue
        entity_id = str(state.get("entity_id") or "").strip()
        if "." not in entity_id or entity_id in seen:
            continue
        domain, object_id = entity_id.split(".", 1)
        if (light_only and domain != "light") or (not light_only and not supports_binary_action(domain)):
            continue
        if str(state.get("state") or "").strip().lower() in {"unavailable", "unknown"}:
            continue

        attrs = state.get("attributes") or {}
        friendly = str(attrs.get("friendly_name") or "").strip()
        label = friendly or object_id.replace("_", " ")
        seen.add(entity_id)
        friendly_fast = " ".join(friendly.lower().replace("_", " ").split())
        object_fast = " ".join(object_id.lower().replace("_", " ").split())
        eligible.append((entity_id, label, friendly_fast, object_fast))
        if target in {friendly_fast, object_fast}:
            exact_rows.append(
                {
                    "entity_id": entity_id,
                    "label": label,
                    "label_norm": _surface_norm(label),
                    "object_norm": _surface_norm(object_id),
                    "exact": True,
                }
            )

    # Exact full names remain authoritative even when many broader partial
    # matches occurred earlier in the snapshot.
    if exact_rows:
        return exact_rows

    partial_rows = []
    for entity_id, label, friendly_fast, object_fast in eligible:
        if target not in friendly_fast and target not in object_fast:
            continue
        friendly_norm = _surface_norm(friendly_fast)
        object_norm = _surface_norm(object_fast)
        surfaces = [surface for surface in (friendly_norm, object_norm) if surface]
        if any(token in _BAD_SURFACE_TOKENS for surface in surfaces for token in surface.split()):
            continue
        if not any(target_tokens.issubset(set(surface.split())) for surface in surfaces):
            continue
        partial_rows.append(
            {
                "entity_id": entity_id,
                "label": label,
                "label_norm": _surface_norm(label),
                "object_norm": object_norm,
                "exact": False,
            }
        )
        if max_partial_rows is not None and len(partial_rows) >= max_partial_rows:
            break
    return partial_rows


def _spoken_list(labels: list[str]) -> str:
    if len(labels) == 2:
        return f"{labels[0]} or {labels[1]}"
    return ", ".join(labels[:-1]) + f", or {labels[-1]}"


def _build_clarification(
    *,
    target: str,
    rows: list[dict],
    command_builder: Callable[[dict], str],
    max_options: int,
) -> Optional[DeviceClarification]:
    exact = [row for row in rows if row["exact"]]
    if len(exact) == 1:
        return None
    if exact:
        rows = exact
    if len(rows) < 2:
        return None
    if len(rows) > max_options:
        return DeviceClarification(
            prompt=f"I found several matches for {target}. Say a more specific device name."
        )

    alias_sets = []
    target_tokens = set(target.split())
    for row in rows:
        aliases = {row["label_norm"], row["object_norm"]}
        distinguishing = [
            token for token in row["label_norm"].split() if token not in target_tokens
        ]
        if distinguishing:
            aliases.add(" ".join(distinguishing))
        alias_sets.append({alias for alias in aliases if alias})

    alias_counts: dict[str, int] = {}
    for aliases in alias_sets:
        for alias in aliases:
            alias_counts[alias] = alias_counts.get(alias, 0) + 1

    options = []
    for row, aliases in zip(rows, alias_sets):
        unique_aliases = tuple(sorted(alias for alias in aliases if alias_counts[alias] == 1))
        if not unique_aliases:
            continue
        options.append(
            ClarificationOption(
                label=row["label"],
                command=command_builder(row),
                aliases=unique_aliases,
            )
        )

    labels = [str(row["label"]) for row in rows]
    if len(options) < 2:
        return DeviceClarification(
            prompt=f"I found more than one match for {target}. Say the full device name."
        )
    return DeviceClarification(
        prompt=f"Which {target}: {_spoken_list(labels)}?",
        options=tuple(options),
    )


def build_binary_device_clarification(
    text: str,
    *,
    states_snapshot,
    aliases_by_entity: Optional[Mapping[str, Iterable[str]]] = None,
    max_options: int = 4,
) -> Optional[DeviceClarification]:
    """Return a clarification only when a short target has multiple live matches."""
    parts = _binary_parts(text)
    if not parts:
        return None
    action, target = parts
    if (
        not target
        or target in _NON_SPECIFIC_CONTEXT_TARGETS
        or len(target.split()) > 4
        or _has_configured_alias(target, aliases_by_entity or {})
    ):
        return None

    return _build_clarification(
        target=target,
        rows=_candidate_rows(
            target,
            states_snapshot,
            max_partial_rows=max_options + 1,
        ),
        command_builder=lambda row: f"turn {row['label_norm']} {action}",
        max_options=max_options,
    )


def build_light_device_clarification(
    text: str,
    *,
    states_snapshot,
    aliases_by_entity: Optional[Mapping[str, Iterable[str]]] = None,
    authoritative_targets: Iterable[str] = (),
    max_options: int = 4,
) -> Optional[DeviceClarification]:
    """Clarify ambiguous named-light color and brightness commands."""
    t = _norm(text)
    target = ""
    value = ""
    mode = ""

    match = re.fullmatch(
        r"set\s+(?:the\s+)?(.+?)\s+brightness\s+(?:to\s+)?(\d{1,3})(?:\s+percent)?",
        t,
    )
    if match:
        target, value, mode = _norm(match.group(1)), match.group(2), "brightness"
    else:
        match = re.fullmatch(r"set\s+(?:the\s+)?(.+?)\s+to\s+(\d{1,3})(?:\s+percent)?", t)
        if match:
            target, value, mode = _norm(match.group(1)), match.group(2), "brightness"

    if not mode:
        match = re.fullmatch(
            r"(?:set|make|change)\s+(?:the\s+)?(.+?)\s+(?:to\s+)?([a-z]+)",
            t,
        )
        if match and is_known_css_color(match.group(2)):
            target, value, mode = _norm(match.group(1)), match.group(2), "color"

    reserved = {_norm(item) for item in authoritative_targets if _norm(item)}
    if (
        not mode
        or not target
        or target in _NON_SPECIFIC_CONTEXT_TARGETS
        or target in reserved
        or len(target.split()) > 4
        or _has_configured_alias(target, aliases_by_entity or {})
    ):
        return None

    if mode == "brightness":
        numeric = max(0, min(100, int(value)))
        builder = lambda row: f"set {row['label_norm']} brightness to {numeric}%"
    else:
        builder = lambda row: f"set {row['label_norm']} to {value}"

    return _build_clarification(
        target=target,
        rows=_candidate_rows(
            target,
            states_snapshot,
            light_only=True,
            max_partial_rows=max_options + 1,
        ),
        command_builder=builder,
        max_options=max_options,
    )

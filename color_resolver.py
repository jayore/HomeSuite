from __future__ import annotations

import logging
from typing import Optional

# ---------------------------------------------------------------------------
# Known CSS color names that HA's color_name field accepts directly.
# Colors in this set skip AI resolution entirely.
# Source: CSS Color Level 3 named colors (subset of the most useful ones).
# ---------------------------------------------------------------------------
_KNOWN_CSS_COLORS = {
    "aliceblue", "antiquewhite", "aqua", "aquamarine", "azure",
    "beige", "bisque", "black", "blanchedalmond", "blue",
    "blueviolet", "brown", "burlywood", "cadetblue", "chartreuse",
    "chocolate", "coral", "cornflowerblue", "cornsilk", "crimson",
    "cyan", "darkblue", "darkcyan", "darkgoldenrod", "darkgray",
    "darkgreen", "darkkhaki", "darkmagenta", "darkolivegreen",
    "darkorange", "darkorchid", "darkred", "darksalmon", "darkseagreen",
    "darkslateblue", "darkslategray", "darkturquoise", "darkviolet",
    "deeppink", "deepskyblue", "dimgray", "dodgerblue", "firebrick",
    "floralwhite", "forestgreen", "fuchsia", "gainsboro", "ghostwhite",
    "gold", "goldenrod", "gray", "green", "greenyellow", "honeydew",
    "hotpink", "indianred", "indigo", "ivory", "khaki", "lavender",
    "lavenderblush", "lawngreen", "lemonchiffon", "lightblue",
    "lightcoral", "lightcyan", "lightgoldenrodyellow", "lightgray",
    "lightgreen", "lightpink", "lightsalmon", "lightseagreen",
    "lightskyblue", "lightslategray", "lightsteelblue", "lightyellow",
    "lime", "limegreen", "linen", "magenta", "maroon", "mediumaquamarine",
    "mediumblue", "mediumorchid", "mediumpurple", "mediumseagreen",
    "mediumslateblue", "mediumspringgreen", "mediumturquoise",
    "mediumvioletred", "midnightblue", "mintcream", "mistyrose",
    "moccasin", "navajowhite", "navy", "oldlace", "olive", "olivedrab",
    "orange", "orangered", "orchid", "palegoldenrod", "palegreen",
    "paleturquoise", "palevioletred", "papayawhip", "peachpuff", "peru",
    "pink", "plum", "powderblue", "purple", "red", "rosybrown",
    "royalblue", "saddlebrown", "salmon", "sandybrown", "seagreen",
    "seashell", "sienna", "silver", "skyblue", "slateblue", "slategray",
    "snow", "springgreen", "steelblue", "tan", "teal", "thistle",
    "tomato", "turquoise", "violet", "wheat", "white", "whitesmoke",
    "yellow", "yellowgreen", "amber",
}

# Simple in-memory cache — evocative descriptions resolve to the same color
# every time, so there's no reason to re-call the API.
_cache: dict[str, str] = {}

_SYSTEM_PROMPT = (
    "You are a color resolver for a smart home lighting system. "
    "The user described a light color using everyday or evocative language. "
    "Your job: return ONLY a single valid CSS named color (e.g. steelblue, coral, forestgreen) "
    "that best matches the description. "
    "Use only standard CSS color names — no hex codes, no RGB values, no explanations. "
    "One word, lowercase, nothing else."
)


def is_known_css_color(color: str) -> bool:
    """Return True if color is already a valid CSS named color."""
    return (color or "").strip().lower() in _KNOWN_CSS_COLORS


def resolve_color_description(description: str, openai_client) -> Optional[str]:
    """
    Resolve an evocative color description to a CSS named color via AI.

    Returns a CSS color name string on success, or None if resolution fails.
    Results are cached in memory for the lifetime of the process.
    """
    if not description or not openai_client:
        return None

    key = description.strip().lower()
    if key in _cache:
        logging.info(f"[color_resolver] cache hit: {key!r} → {_cache[key]!r}")
        return _cache[key]

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f'Color description: "{key}"'},
            ],
            temperature=0.0,
            max_tokens=10,
        )
        raw = (response.choices[0].message.content or "").strip().lower()
        # Strip any accidental punctuation
        raw = raw.strip('.,;:!?"\'')

        if raw and raw in _KNOWN_CSS_COLORS:
            logging.info(f"[color_resolver] resolved: {key!r} → {raw!r}")
            _cache[key] = raw
            return raw

        # AI returned something not in our known set — log and reject rather
        # than pass an unvalidated string to HA.
        logging.warning(
            f"[color_resolver] AI returned unknown color {raw!r} for {key!r}; discarding"
        )
        return None

    except Exception as e:
        logging.error(f"[color_resolver] resolution failed for {key!r}: {e}")
        return None

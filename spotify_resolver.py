from __future__ import annotations

import json
import logging
import re
from typing import Optional
from media_intents import normalize_music_intent, public_music_result

# In-memory cache — same description always resolves the same way
_cache: dict[str, dict] = {}

_SYSTEM_PROMPT = (
    "You are a music identification assistant for a smart home system. "
    "The user is trying to play a specific song, album, or artist using a "
    "description, partial lyrics, or fuzzy reference. "
    "Identify the exact title and artist.\n\n"
    "Return ONLY a JSON object with these fields:\n"
    '  "title": exact song/album/artist name (string)\n'
    '  "artist": artist name (string, or null for pure artist queries)\n'
    '  "kind": one of "track", "album", "artist" (default "track")\n'
    '  "confident": true if you are certain of the match, false if you are guessing\n\n'
    "IMPORTANT: For lyrics-based queries, only return confident=true if you actually "
    "recognise those specific lyrics. Do NOT default to the artist's most famous song "
    "just because the artist was mentioned. If you are not sure which specific track "
    "the lyrics are from, return confident=false.\n\n"
    "Examples:\n"
    '  "the beatles song about a walrus" → '
    '{"title": "I Am the Walrus", "artist": "The Beatles", "kind": "track", "confident": true}\n'
    '  "song with the lyrics I\'ve got a hunger twisting my stomach into knots" → '
    '{"title": "Ho Hey", "artist": "The Lumineers", "kind": "track", "confident": true}\n'
    '  "that 90s grunge album with smells like teen spirit" → '
    '{"title": "Nevermind", "artist": "Nirvana", "kind": "album", "confident": true}\n'
    '  "the radiohead song with the weird beeping" → '
    '{"title": "Idioteque", "artist": "Radiohead", "kind": "track", "confident": false}\n\n'
    "Return only the JSON object, nothing else."
)


def _looks_fuzzy_music_query(query: str) -> bool:
    """
    Return True if this query looks like a description rather than an exact title.
    Conservative — only triggers on clear indicators so we don't mis-intercept
    normal play-by-name requests.
    """
    q = (query or "").lower()

    # Explicit description / lyrics markers
    if re.search(r"\bwith\s+the\s+lyrics\b", q):
        return True
    if re.search(r"\bthat\s+goes\b", q):
        return True
    if re.search(r"\bthe\s+one\s+(that|where|about|with)\b", q):
        return True
    if re.search(r"\bsounds?\s+like\b", q):
        return True
    if re.search(r"\b(song|track|album)\s+(about|where|that|with)\b", q):
        return True
    if re.search(r"\bsong\s+from\b", q):
        return True
    if re.search(r"\bcolor\s+of\b", q):  # "song the color of..." poetic descriptions
        return True

    return False


def resolve_spotify_description(description: str, openai_client) -> Optional[dict]:
    """
    Resolve a fuzzy music description to {"title", "artist", "kind"}.

    Returns a dict on success, None on failure.
    Results are cached in memory for the lifetime of the process.
    """
    if not description or not openai_client:
        return None

    key = description.strip().lower()
    if key in _cache:
        logging.info("[spotify_resolver] cache hit: %r → %r", key, _cache[key])
        return _cache[key]

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f'Music description: "{key}"'},
            ],
            temperature=0.0,
            max_tokens=100,
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        data = json.loads(raw)

        if not data.get("confident", True):
            logging.info("[spotify_resolver] low confidence for %r, skipping", key)
            return None

        intent = normalize_music_intent(data, source="spotify_resolver", include_type=False)
        if not intent:
            logging.warning("[spotify_resolver] AI returned no title for %r", key)
            return None

        result = public_music_result(intent)
        logging.info("[spotify_resolver] resolved: %r → %r", key, result)
        _cache[key] = result
        return result

    except Exception as e:
        logging.error("[spotify_resolver] failed for %r: %s", key, e)
        return None

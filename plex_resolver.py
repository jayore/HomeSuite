"""Resolve fuzzy movie/show descriptions before Plex library matching.

When Plex candidates are supplied, the model may select only from that library
result set and the caller validates the returned candidate. A pure-description
fallback can propose a title only when it reports high confidence; the Plex
control layer must still search the configured server before playback.

Positive and negative in-process caches avoid repeated model calls for the same
normalized description.
"""

from __future__ import annotations

import json
import logging
import re
from typing import List, Optional
from media_intents import normalize_video_intent, public_video_result

_cache: dict[str, dict] = {}
_negative_cache: set[str] = set()  # queries where AI returned low confidence — skip retries

_SYSTEM_PROMPT = (
    "You are a movie and TV show identification assistant for a smart home system. "
    "The user is trying to play something on Plex using a description, partial memory, "
    "or fuzzy reference. Identify the exact title.\n\n"
    "Return ONLY a JSON object with these fields:\n"
    '  "title": exact movie or show title (string)\n'
    '  "kind": one of "movie", "show" (default "movie")\n'
    '  "confident": true if you are certain of the match, false if you are guessing\n\n'
    "IMPORTANT: Only return confident=true if you are genuinely certain. "
    "If the description is vague or matches multiple titles, return confident=false. "
    "When a user says 'movie where ...', 'movie about ...', 'movie with ...', "
    "or similar, treat the words after that phrase as a plot/premise description, "
    "not as a literal title. If that premise uniquely identifies a well-known "
    "movie or show, return that title with confident=true.\n\n"
    "EXCEPTION: For 'latest', 'newest', or 'most recent' queries about a specific person or "
    "franchise (e.g. 'latest Ryan Gosling movie', 'newest Marvel film'), always return your "
    "best-known most recent title and set confident=true. You know filmographies well enough "
    "to give a useful answer even if your knowledge has a cutoff date.\n\n"
    "Examples:\n"
    '  "the mutant movie with the guy from star trek" → '
    '{"title": "X-Men", "kind": "movie", "confident": true}\n'
    '  "that 90s movie with the bus that cant slow down" → '
    '{"title": "Speed", "kind": "movie", "confident": true}\n'
    '  "the show about the meth teacher" → '
    '{"title": "Breaking Bad", "kind": "show", "confident": true}\n'
    '  "latest movie starring ryan gosling" → '
    '{"title": "The Fall Guy", "kind": "movie", "confident": true}\n'
    '  "movie with the alien named rocky" → '
    '{"title": "Project Hail Mary", "kind": "movie", "confident": true}\n'
    '  "that sci-fi movie with the dome" → '
    '{"title": "The Truman Show", "kind": "movie", "confident": false}\n\n'
    "Return only the JSON object, nothing else."
)

_LIBRARY_SYSTEM_PROMPT = (
    "You are a movie and TV show identification assistant for a smart home system. "
    "The user wants to watch something on their Plex server. "
    "You have been given a list of titles actually in their library with years.\n\n"
    "Return ONLY a JSON object with these fields:\n"
    '  "title": exact title from the library list below (string)\n'
    '  "kind": one of "movie", "show"\n'
    '  "confident": true if the library contains a clear match, false if you are guessing\n\n'
    "IMPORTANT: The library list was produced by searching the user's Plex library. "
    "Treat every title in the list as a confirmed match for the search — do NOT use your "
    "own knowledge to second-guess whether a specific actor/director is actually in a film. "
    "The library metadata is the ground truth.\n\n"
    "For 'latest/newest/most recent' queries, simply pick the title with the highest year "
    "from the list — that is definitively the most recent one they own. "
    "If nothing in the library matches the description, return confident=false and your "
    "best guess at what the title would be called (even if not in the list).\n\n"
    "Return only the JSON object, nothing else."
)


def _looks_fuzzy_plex_query(query: str) -> bool:
    """
    Return True if the query looks like a description rather than an exact title.
    Conservative — only fires on clear description-style indicators.
    """
    q = (query or "").lower()

    if re.search(r"\bwith\s+the\b", q):
        return True
    if re.search(r"\bwhere\s+the\b", q):
        return True
    if re.search(r"\bthat\s+goes\b", q):
        return True
    if re.search(r"\bthe\s+one\s+(that|where|about|with)\b", q):
        return True
    if re.search(r"\b(movie|film|show)\s+(about|where|when|that|with|which|who)\b", q):
        return True
    if re.search(r"\bsounds?\s+like\b", q):
        return True
    if re.search(r"\bthat\s+\w+\s+(movie|film|show)\b", q):
        return True
    if re.search(r"\b(movie|film|show)\s+from\b", q):
        return True
    if re.search(r"\b(latest|newest|most\s+recent)\b", q):
        return True
    if re.search(r"\bstarring\b", q):
        return True

    return False


def resolve_plex_description(
    description: str,
    openai_client,
    candidates: Optional[List[dict]] = None,
) -> Optional[dict]:
    """Resolve a fuzzy description, preferring grounded Plex candidates."""
    """
    Resolve a fuzzy movie/show description to {"title", "kind"}.

    If candidates (library titles from Plex hubs/search) are provided, the AI
    picks from the actual library contents. Otherwise falls back to pure AI.

    Library-context results are not cached (library can change).
    Pure-AI results are cached in memory for the lifetime of the process.
    """
    if not description or not openai_client:
        return None

    key = description.strip().lower()

    # Library-context path: always re-ask AI with actual Plex results
    if candidates:
        return _resolve_with_library(key, openai_client, candidates)

    # Pure-AI path: cacheable
    if key in _cache:
        logging.info("[plex_resolver] cache hit: %r → %r", key, _cache[key])
        return _cache[key]

    return _resolve_pure_ai(key, openai_client)


def _resolve_with_library(
    description: str,
    openai_client,
    candidates: List[dict],
) -> Optional[dict]:
    candidate_lines = "\n".join(
        f"- {c['title']} ({c.get('year') or '?'}) [{c.get('kind', 'movie')}]"
        for c in candidates
    )
    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": _LIBRARY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Library contents:\n{candidate_lines}\n\n"
                        f'User request: "{description}"'
                    ),
                },
            ],
            temperature=0.0,
            max_tokens=80,
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        data = json.loads(raw)

        if not data.get("confident", True):
            logging.info("[plex_resolver] library AI low confidence for %r, skipping", description)
            return None

        intent = normalize_video_intent(data, source="plex_resolver", include_type=False)
        if not intent:
            logging.warning("[plex_resolver] library AI returned no title for %r", description)
            return None
        result = public_video_result(intent)
        logging.info("[plex_resolver] library resolved: %r → %r", description, result)
        return result

    except Exception as e:
        logging.error("[plex_resolver] library resolve failed for %r: %s", description, e)
        return None


def _resolve_pure_ai(description: str, openai_client) -> Optional[dict]:
    if description in _negative_cache:
        return None  # don't re-call API during retry loops

    try:
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f'Media description: "{description}"'},
            ],
            temperature=0.0,
            max_tokens=80,
            response_format={"type": "json_object"},
        )
        raw = (response.choices[0].message.content or "").strip()
        data = json.loads(raw)

        if not data.get("confident", True):
            logging.info("[plex_resolver] low confidence for %r, skipping", description)
            _negative_cache.add(description)
            return None

        intent = normalize_video_intent(data, source="plex_resolver", include_type=False)
        if not intent:
            logging.warning("[plex_resolver] AI returned no title for %r", description)
            return None
        result = public_video_result(intent)
        logging.info("[plex_resolver] resolved: %r → %r", description, result)
        _cache[description] = result
        return result

    except Exception as e:
        logging.error("[plex_resolver] failed for %r: %s", description, e)
        return None

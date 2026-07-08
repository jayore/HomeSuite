"""
plex_match_utils.py

Standalone utilities for normalizing and scoring Plex search candidates.
This module is intentionally PURE:
- no Plex calls
- no Home Assistant calls
- no PiPhone globals

Goal:
Improve match quality for movie vs episode vs show selection by
normalizing titles and applying explainable scoring rules.

Initial focus:
- punctuation / colon / dash normalization
- number + roman numeral normalization
- basic fuzzy scoring
- strong penalties for wrong media types (e.g. episodes when user wants movie)

Safe to evolve independently before wiring into the live pipeline.
"""

import re
from typing import Dict, List, Tuple


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

_ROMAN_MAP = {
    "i": 1,
    "ii": 2,
    "iii": 3,
    "iv": 4,
    "v": 5,
    "vi": 6,
    "vii": 7,
    "viii": 8,
    "ix": 9,
    "x": 10,
}

_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}


def normalize_text(text: str) -> str:
    """
    Normalize a title or query string into a comparable form.

    Examples:
      "Wall-E"            -> "wall e"
      "Alien: Earth"      -> "alien earth"
      "Part II"           -> "part 2"
      "Episode Five"      -> "episode 5"
      "Allen v. Farrow"   -> "allen versus farrow"
    """

    t = text.lower()

    # --------------------------------------------------
    # Spoken-language normalization (before numerics)
    # --------------------------------------------------

    # Legal / documentary separators
    t = re.sub(r"\bvs?\.\b", "versus", t)
    t = re.sub(r"\bv\.\b", "versus", t)

    # Ampersand → and
    t = t.replace("&", " and ")

    # Casual conjunctions: " n " / " n' " → and
    t = re.sub(r"\b n'\b", " and", t)
    t = re.sub(r"\b n \b", " and ", t)

    # Replace punctuation with spaces
    t = re.sub(r"[:\-–—/.,()]", " ", t)

    # Normalize roman numerals (word boundaries only)
    for roman, num in _ROMAN_MAP.items():
        t = re.sub(rf"\b{roman}\b", str(num), t)

    # Normalize written numbers
    for word, num in _WORD_NUMBERS.items():
        t = re.sub(rf"\b{word}\b", str(num), t)

    # Collapse whitespace
    t = re.sub(r"\s+", " ", t).strip()

    return t


def tokenize(text: str) -> List[str]:
    """
    Tokenize normalized text into words.
    """
    return normalize_text(text).split()


# ---------------------------------------------------------------------------
# Query intent heuristics
# ---------------------------------------------------------------------------

def infer_query_intent(query: str) -> Dict[str, bool]:
    """
    Infer high-level intent signals from the raw query.
    This is deliberately heuristic, not NLP-heavy.
    """

    q = query.lower()

    return {
        "wants_episode": bool(re.search(r"\bepisode\b|\bseason\b", q)),
        "wants_movie": bool(re.search(r"\bmovie\b|\bfilm\b|\bwatch\b", q)),
        "explicit_episode": bool(re.search(r"\bepisode\s+\d+\b", q)),
    }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_candidate(
    query: str,
    candidate: Dict[str, str],
) -> Tuple[int, Dict[str, int]]:
    """
    Score a Plex candidate against the user's query.

    candidate should minimally include:
      {
        "title": str,
        "type": "movie" | "show" | "episode"
      }

    Returns:
      (score, breakdown_dict)

    Breakdown is intentionally returned so scoring decisions are inspectable.
    """

    breakdown: Dict[str, int] = {}
    score = 0

    q_norm = normalize_text(query)
    c_norm = normalize_text(candidate.get("title", ""))

    q_tokens = set(q_norm.split())
    c_tokens = set(c_norm.split())

    intent = infer_query_intent(query)
    c_type = candidate.get("type", "").lower()

    # ------------------------------------------------------------------
    # Token overlap
    # ------------------------------------------------------------------

    overlap = q_tokens & c_tokens
    overlap_score = len(overlap) * 100
    score += overlap_score
    breakdown["token_overlap"] = overlap_score

    # ------------------------------------------------------------------
    # Exact / prefix matches
    # ------------------------------------------------------------------

    if c_norm == q_norm:
        score += 400
        breakdown["exact_match"] = 400

    elif c_norm.startswith(q_norm) or q_norm.startswith(c_norm):
        score += 200
        breakdown["prefix_match"] = 200

    # ------------------------------------------------------------------
    # Length penalty (avoid matching long episode titles accidentally)
    # ------------------------------------------------------------------

    length_diff = abs(len(c_tokens) - len(q_tokens))
    length_penalty = min(length_diff * 15, 150)
    score -= length_penalty
    breakdown["length_penalty"] = -length_penalty

    # ------------------------------------------------------------------
    # Media type weighting (VERY important)
    # ------------------------------------------------------------------

    if c_type == "episode":
        if not intent["wants_episode"]:
            score -= 300
            breakdown["episode_penalty"] = -300
    elif c_type == "movie":
        if intent["wants_episode"]:
            score -= 150
            breakdown["movie_penalty"] = -150
        else:
            score += 50
            breakdown["movie_bonus"] = 50

    # ------------------------------------------------------------------
    # Short-query guardrail
    # Prevent "wally" -> obscure episode over popular movie
    # ------------------------------------------------------------------

    if len(q_tokens) <= 2 and c_type == "episode":
        score -= 200
        breakdown["short_query_episode_penalty"] = -200

    return score, breakdown


# ---------------------------------------------------------------------------
# Batch helper
# ---------------------------------------------------------------------------

def rank_candidates(
    query: str,
    candidates: List[Dict[str, str]],
) -> List[Dict]:
    """
    Rank a list of candidates by score.

    Returns:
      [
        {
          "candidate": {...},
          "score": int,
          "breakdown": {...}
        },
        ...
      ]
    """

    ranked = []
    for c in candidates:
        score, breakdown = score_candidate(query, c)
        ranked.append({
            "candidate": c,
            "score": score,
            "breakdown": breakdown,
        })

    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked

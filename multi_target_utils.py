import re
from typing import List


def split_targets(raw: str) -> List[str]:
    """
    Split a target phrase into multiple targets.
    Supports:
      - "a and b"
      - "a, b and c"
      - "a, b, c"
    Conservative rule:
      - If no comma and no standalone 'and' present, return [raw] unchanged.
    """
    s = (raw or "").strip()
    if not s:
        return []

    if ("," not in s) and (re.search(r"\band\b", s, flags=re.I) is None):
        return [re.sub(r"\s+", " ", s).strip()]

    # Normalize separators into commas
    s = re.sub(r"\s*,\s*", ",", s)
    s = re.sub(r"\s*,?\s*\band(?:\s+also)?\b\s+", ",", s, flags=re.I)

    parts = [p.strip() for p in s.split(",") if p.strip()]
    out: List[str] = []
    seen = set()

    for p in parts:
        p = re.sub(r"^(?:also\s+)?the\s+", "", p, flags=re.I)
        p = re.sub(r"^also\s+", "", p, flags=re.I)
        p = re.sub(r"\s+", " ", p).strip()
        key = p.lower()
        if key and key not in seen:
            out.append(p)
            seen.add(key)

    return out or [re.sub(r"\s+", " ", (raw or "").strip()).strip()]

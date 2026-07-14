"""Low-level text and spoken-number normalization for device commands.

These helpers perform deterministic, context-free cleanup before dispatch.
They deliberately avoid entity lookup, phonetic preference maps, and intent
execution so callers can reuse them without mutating conversational text.
"""

import re

_NUM_WORDS_0_19 = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
    "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}


def _looks_like_device_command(t: str) -> bool:
    """Return whether text has enough control language to justify normalization."""
    if not t:
        return False
    tl = t.strip().lower()

    for p in (
        "set ", "turn ", "switch ", "play ", "pause", "resume", "stop", "group", "ungroup",
        "add ", "remove ", "join ", "leave ", "lock ", "unlock ", "announce ",
        "what's playing", "whats playing",
    ):
        if tl.startswith(p):
            return True

    if any(k in tl for k in (" brightness", "volume", " kelvin", " rgb", " color", " percent", "%", "#")):
        return True

    if " to " in tl:
        return True

    return False


def _normalize_restorative_device_phrase(t: str) -> str:
    """Canonicalize natural "turn it back ..." state-setting language.

    "Back" expresses restoration here, but the existing deterministic handlers
    only need the target and desired state. Keep this as a narrow whole-command
    rewrite so entity names containing "back" are left untouched.
    """
    if not t:
        return t

    text = str(t).strip()
    binary = re.fullmatch(
        r"turn\s+(.+?)\s+back\s+(on|off)[\s.?!]*",
        text,
        flags=re.IGNORECASE,
    )
    if binary:
        return f"turn {binary.group(1).strip()} {binary.group(2).lower()}"

    state = re.fullmatch(
        r"turn\s+(.+?)\s+back\s+to\s+(.+?)[\s.?!]*",
        text,
        flags=re.IGNORECASE,
    )
    if state:
        return f"set {state.group(1).strip()} to {state.group(2).strip()}"

    return t


def _parse_number_words(phrase: str):
    """Parse small spoken numbers up to 100. Returns int or None."""
    if not phrase:
        return None
    p = phrase.strip().lower().replace("-", " ")
    toks = [t for t in p.split() if t]
    if not toks:
        return None

    if len(toks) == 1 and toks[0].isdigit():
        try:
            return int(toks[0])
        except Exception:
            return None

    if toks in (["one", "hundred"], ["a", "hundred"], ["hundred"]):
        return 100
    if "hundred" in toks and toks not in (["one", "hundred"], ["a", "hundred"], ["hundred"]):
        return None

    total = 0
    i = 0
    while i < len(toks):
        w = toks[i]
        if w in _NUM_WORDS_0_19:
            total += _NUM_WORDS_0_19[w]
            i += 1
            continue
        if w in _TENS:
            total += _TENS[w]
            i += 1
            if i < len(toks) and toks[i] in _NUM_WORDS_0_19 and _NUM_WORDS_0_19[toks[i]] < 10:
                total += _NUM_WORDS_0_19[toks[i]]
                i += 1
            continue
        return None

    if 0 <= total <= 100:
        return total
    return None


def _normalize_device_text(t: str) -> str:
    if not t:
        return t
    t = str(t)

    # 0) Digits + "percent" -> "<digits>%"
    try:
        t = re.sub(r"\b(\d{1,3})\s*percent\b", r"\1%", t, flags=re.IGNORECASE)
    except Exception:
        pass

    # 1) "to/at <number words> percent" OR "<number words> percent" -> "<digits>%"
    # Token-based to avoid greedy regex grabbing earlier words (e.g. "holiday to fifty percent").
    try:
        percent_re = re.compile(r"\bpercent\b", re.IGNORECASE)
        word_re = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)?")
        out = t
        offset = 0

        for pm in list(percent_re.finditer(t)):
            pstart, pend = pm.start(), pm.end()
            left = t[:pstart].rstrip()
            words = [(wm.group(0), wm.start(), wm.end()) for wm in word_re.finditer(left)]
            if not words:
                continue

            for k in (3, 2, 1):
                if len(words) < k:
                    continue
                cand = words[-k:]
                tokens = [w[0].lower() for w in cand]
                spans = [(w[1], w[2]) for w in cand]

                if tokens and tokens[0] in ("to", "at"):
                    tokens = tokens[1:]
                    spans = spans[1:]
                if not tokens:
                    continue

                phrase = " ".join(tokens)
                n = _parse_number_words(phrase)
                if n is None:
                    continue

                rep_start = spans[0][0]
                rep_end = pend
                out = out[:rep_start + offset] + f"{n}%" + out[rep_end + offset:]
                offset += len(f"{n}%") - (rep_end - rep_start)
                break
    except Exception:
        out = t

    t = out

    # 2) "to/at <number words>" -> "to/at <digits>"
    def _to_repl(m):
        prep = (m.group(1) or "").strip()
        raw = (m.group(2) or "").strip()
        n = _parse_number_words(raw)
        if n is None:
            return m.group(0)
        return f"{prep} {n}"

    t = re.sub(
        r"\b(to|at)\s+([a-zA-Z\-]+(?:\s+[a-zA-Z\-]+){0,2})\b",
        _to_repl,
        t,
        flags=re.IGNORECASE,
    )

    # 3) "brightness/volume <number words>" -> digits
    def _kw_num_repl(m):
        kw = (m.group(1) or "").strip()
        raw = (m.group(2) or "").strip()
        n = _parse_number_words(raw)
        if n is None:
            return m.group(0)
        return f"{kw} {n}"

    t = re.sub(
        r"\b(brightness|volume)\s+([a-zA-Z\-]+(?:\s+[a-zA-Z\-]+){0,2})\b",
        _kw_num_repl,
        t,
        flags=re.IGNORECASE,
    )

    # 4) Targeted singularization for known trouble phrases
    t = re.sub(r"\bside\s+lamps\b", "side lamp", t, flags=re.IGNORECASE)
    t = re.sub(r"\bside\s+lights\b", "side light", t, flags=re.IGNORECASE)

    # 5) Split glued suffixes: "sidelamp(s)" -> "side lamp", "sidelight(s)" -> "side light"
    def _split_glued(m):
        stem = m.group(1)
        suf = m.group(2).lower()
        if suf == "lamps":
            suf = "lamp"
        elif suf == "lights":
            suf = "light"
        return f"{stem} {suf}"

    t = re.sub(
        r"([a-zA-Z]{3,})(lamps|lamp|lights|light)\b",
        _split_glued,
        t,
        flags=re.IGNORECASE,
    )

    return t

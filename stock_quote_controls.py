"""Parse and answer read-only stock quote questions through Alpaca.

The integration intentionally exposes market data only. It requests snapshots
and the U.S. market clock, normalizes those responses into small dataclasses,
and never calls account, portfolio, or order endpoints. Short caches keep voice,
Telegram, and HTTP clients from multiplying identical provider requests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import math
import os
import re
import threading
import time
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

import requests

from app_config import (
    STOCK_MARKET_CLOCK_CACHE_SECONDS,
    STOCK_QUOTE_CACHE_SECONDS,
    STOCK_QUOTE_DATA_BASE_URL,
    STOCK_QUOTE_DATA_FEED,
    STOCK_QUOTE_MAX_SYMBOLS,
    STOCK_QUOTE_TIMEOUT_SECONDS,
    STOCK_QUOTE_TRADING_BASE_URL,
    STOCK_SYMBOL_ALIAS_OVERRIDES,
    STOCK_SYMBOL_ALIASES,
    STOCK_SYMBOL_LABEL_OVERRIDES,
    STOCK_SYMBOL_LABELS,
)


_SYMBOL_PATTERN = re.compile(r"^[a-z]{1,5}(?:\.[a-z]{1,2})?$")
_TARGET_PATTERNS = (
    re.compile(
        r"^(?:(?:give|get|show)\s+me\s+)?(?:a\s+|the\s+)?"
        r"(?:stock\s+)?quote(?:\s+(?:for|on|of))?\s+(?P<target>.+)$"
    ),
    re.compile(
        r"^(?:stock|share)\s+(?:price|quote)\s+(?:for|of|on)\s+"
        r"(?P<target>.+)$"
    ),
    re.compile(
        r"^(?:what(?:'s|\s+is)|tell\s+me)\s+(?:the\s+)?"
        r"(?:stock|share)\s+(?:price|quote)(?:\s+(?:for|of))?\s+"
        r"(?P<target>.+)$"
    ),
    re.compile(
        r"^(?:what(?:'s|\s+is)|how\s+much\s+is)\s+(?P<target>.+?)\s+"
        r"(?:stock|shares?)(?:\s+(?:price|quote))?"
        r"(?:\s+(?:today|now|right\s+now))?$"
    ),
    re.compile(
        r"^what(?:'s|\s+is)\s+(?P<target>.+?)\s+trading\s+at"
        r"(?:\s+(?:today|now|right\s+now))?$"
    ),
    re.compile(
        r"^how\s+(?:is|are)\s+(?P<target>.+?)\s+(?:stock|shares?)"
        r"(?:\s+(?:doing|performing|trading))?"
        r"(?:\s+(?:today|now|right\s+now))?$"
    ),
    re.compile(r"^(?P<target>.+?)\s+stock\s+quote$"),
)
_CLOSE_PATTERN = re.compile(
    r"^(?:how\s+did|what\s+did)\s+(?P<target>.+?)\s+close(?:\s+at)?$"
)
_TRAILING_TARGET_WORDS = re.compile(
    r"\s+(?:stock|shares?|price|quote|today|currently|now|right\s+now)$"
)
_CACHE_LOCK = threading.Lock()
_SNAPSHOT_CACHE: dict[tuple, tuple[float, tuple["StockQuote", ...]]] = {}
_CLOCK_CACHE: dict[tuple, tuple[float, "MarketClock"]] = {}


@dataclass(frozen=True)
class StockQuery:
    """A parsed stock quote or U.S. market-clock request."""

    intent: str
    symbols: tuple[str, ...] = ()
    too_many_symbols: bool = False


@dataclass(frozen=True)
class StockQuote:
    """A normalized latest trade and previous-session comparison."""

    symbol: str
    price: float
    session_close: Optional[float]
    previous_close: Optional[float]
    change: Optional[float]
    change_percent: Optional[float]
    as_of: Optional[datetime]


@dataclass(frozen=True)
class MarketClock:
    """Current U.S. regular-session state from Alpaca's clock endpoint."""

    is_open: bool
    timestamp: Optional[datetime]
    next_open: Optional[datetime]
    next_close: Optional[datetime]


class StockQuoteServiceError(RuntimeError):
    """Provider/configuration failure represented by a stable public code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _normalize(text: str) -> str:
    value = str(text or "").lower().replace("’", "'")
    value = re.sub(r"[^a-z0-9.'&,$\s-]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip(" .,!?")
    return value


def _normalized_aliases(aliases: Mapping[str, str]) -> dict[str, str]:
    normalized = {}
    for alias, symbol in (aliases or {}).items():
        key = _normalize(alias)
        ticker = str(symbol or "").strip().upper()
        if key and ticker:
            normalized[key] = ticker
    return normalized


def _clean_target_fragment(value: str) -> str:
    cleaned = _normalize(value).strip()
    cleaned = re.sub(r"^(?:the|a)\s+", "", cleaned)
    while True:
        updated = _TRAILING_TARGET_WORDS.sub("", cleaned).strip()
        if updated == cleaned:
            return re.sub(r"'s$", "", cleaned).strip()
        cleaned = updated


def _configured_aliases() -> dict[str, str]:
    return {
        **(STOCK_SYMBOL_ALIASES or {}),
        **(STOCK_SYMBOL_ALIAS_OVERRIDES or {}),
    }


def _configured_labels() -> dict[str, str]:
    return {
        **(STOCK_SYMBOL_LABELS or {}),
        **(STOCK_SYMBOL_LABEL_OVERRIDES or {}),
    }


def _resolve_target_symbols(
    target: str,
    *,
    aliases: Mapping[str, str],
) -> list[str]:
    normalized_aliases = _normalized_aliases(aliases)
    whole = _clean_target_fragment(target)
    if not whole:
        return []
    if whole in normalized_aliases:
        return [normalized_aliases[whole]]

    segments = [
        _clean_target_fragment(segment)
        for segment in re.split(r"\s*(?:,|&|\band\b)\s*", whole)
        if _clean_target_fragment(segment)
    ]
    symbols = []
    for segment in segments:
        symbol = normalized_aliases.get(segment)
        if symbol is None:
            compact_tokens = segment.replace("$", "").split()
            if compact_tokens and all(
                len(token) == 1 and token.isalpha() for token in compact_tokens
            ):
                candidate = "".join(compact_tokens)
            else:
                candidate = segment.replace("$", "")
            if _SYMBOL_PATTERN.fullmatch(candidate):
                symbol = candidate.upper()
        if not symbol:
            return []
        if symbol not in symbols:
            symbols.append(symbol)
    return symbols


def parse_stock_query(
    text: str,
    *,
    aliases: Optional[Mapping[str, str]] = None,
    max_symbols: Optional[int] = None,
) -> Optional[StockQuery]:
    """Parse conservative quote and stock-market-clock language."""
    normalized = _normalize(text)
    if not normalized:
        return None

    market_status = re.fullmatch(
        r"(?:is|are)\s+(?:the\s+)?(?:(?:u\.?\s*s\.?|american)\s+)?"
        r"(?:stock\s+)?market\s+(?:open|closed)"
        r"(?:\s+(?:today|now|right\s+now))?",
        normalized,
    )
    if market_status:
        return StockQuery(intent="market_status")

    market_event = re.fullmatch(
        r"(?:when|what\s+time)\s+(?:does|will)\s+(?:the\s+)?"
        r"(?:(?:u\.?\s*s\.?|american)\s+)?(?:stock\s+)?market\s+"
        r"(?P<event>open|close)(?:\s+(?:today|next))?",
        normalized,
    )
    if market_event:
        intent = "market_open" if market_event.group("event") == "open" else "market_close"
        return StockQuery(intent=intent)

    intent = "quote"
    close_match = _CLOSE_PATTERN.fullmatch(normalized)
    target = close_match.group("target") if close_match else None
    if close_match:
        intent = "close"
    else:
        for pattern in _TARGET_PATTERNS:
            match = pattern.fullmatch(normalized)
            if match:
                target = match.group("target")
                break
    if target is None:
        return None

    resolved = _resolve_target_symbols(
        target,
        aliases=aliases if aliases is not None else _configured_aliases(),
    )
    if not resolved:
        return None
    limit = max(1, int(max_symbols or STOCK_QUOTE_MAX_SYMBOLS))
    return StockQuery(
        intent=intent,
        symbols=tuple(resolved[:limit]),
        too_many_symbols=len(resolved) > limit,
    )


def looks_like_stock_query(text: str) -> bool:
    return parse_stock_query(text) is not None


def _private_value(*names: str) -> str:
    try:
        import private_config
    except Exception:
        private_config = None

    for name in names:
        value = getattr(private_config, name, "") if private_config is not None else ""
        value = str(value or os.getenv(name, "") or "").strip()
        if value:
            return value
    return ""


def _credentials() -> tuple[str, str]:
    return (
        _private_value("ALPACA_API_KEY_ID", "APCA_API_KEY_ID"),
        _private_value("ALPACA_API_SECRET_KEY", "APCA_API_SECRET_KEY"),
    )


def stock_quotes_configured() -> bool:
    key_id, secret_key = _credentials()
    return bool(key_id and secret_key)


def _headers() -> dict[str, str]:
    key_id, secret_key = _credentials()
    if not key_id or not secret_key:
        raise StockQuoteServiceError("not_configured")
    return {
        "APCA-API-KEY-ID": key_id,
        "APCA-API-SECRET-KEY": secret_key,
        "Accept": "application/json",
    }


def _request_json(url: str, *, params: Optional[dict] = None) -> dict:
    try:
        response = requests.get(
            url,
            headers=_headers(),
            params=params or {},
            timeout=float(STOCK_QUOTE_TIMEOUT_SECONDS),
        )
    except requests.Timeout as exc:
        raise StockQuoteServiceError("timeout") from exc
    except requests.RequestException as exc:
        raise StockQuoteServiceError("unavailable") from exc

    if response.status_code in {401, 403}:
        raise StockQuoteServiceError("authentication")
    if response.status_code == 404:
        raise StockQuoteServiceError("not_found")
    if response.status_code == 429:
        raise StockQuoteServiceError("rate_limited")
    if response.status_code >= 400:
        raise StockQuoteServiceError("unavailable")
    try:
        payload = response.json()
    except Exception as exc:
        raise StockQuoteServiceError("invalid_response") from exc
    if not isinstance(payload, dict):
        raise StockQuoteServiceError("invalid_response")
    return payload


def _number(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _parse_timestamp(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _object(snapshot: Mapping[str, Any], *names: str) -> Mapping[str, Any]:
    for name in names:
        value = snapshot.get(name)
        if isinstance(value, dict):
            return value
    return {}


def _snapshot_for_symbol(payload: Mapping[str, Any], symbol: str) -> Mapping[str, Any]:
    collections = [payload]
    nested = payload.get("snapshots")
    if isinstance(nested, dict):
        collections.append(nested)
    for collection in collections:
        value = collection.get(symbol) or collection.get(symbol.lower())
        if isinstance(value, dict):
            return value
    return {}


def _normalize_snapshot(symbol: str, snapshot: Mapping[str, Any]) -> Optional[StockQuote]:
    latest_trade = _object(snapshot, "latestTrade", "LatestTrade")
    minute_bar = _object(snapshot, "minuteBar", "MinuteBar")
    daily_bar = _object(snapshot, "dailyBar", "DailyBar")
    previous_bar = _object(snapshot, "prevDailyBar", "PrevDailyBar")

    price = _number(latest_trade.get("p", latest_trade.get("Price")))
    if price is None:
        price = _number(minute_bar.get("c", minute_bar.get("ClosePrice")))
    if price is None:
        price = _number(daily_bar.get("c", daily_bar.get("ClosePrice")))
    if price is None:
        return None

    previous_close = _number(previous_bar.get("c", previous_bar.get("ClosePrice")))
    session_close = _number(daily_bar.get("c", daily_bar.get("ClosePrice")))
    change = None
    change_percent = None
    if previous_close is not None:
        change = price - previous_close
        if previous_close != 0:
            change_percent = (change / previous_close) * 100.0

    timestamp = latest_trade.get("t", latest_trade.get("Timestamp"))
    if not timestamp:
        timestamp = minute_bar.get("t", minute_bar.get("Timestamp"))
    if not timestamp:
        timestamp = daily_bar.get("t", daily_bar.get("Timestamp"))
    return StockQuote(
        symbol=symbol,
        price=price,
        session_close=session_close,
        previous_close=previous_close,
        change=change,
        change_percent=change_percent,
        as_of=_parse_timestamp(timestamp),
    )


def fetch_stock_quotes(symbols: Sequence[str]) -> list[StockQuote]:
    """Fetch one multi-symbol snapshot request and normalize available quotes."""
    normalized = tuple(
        dict.fromkeys(str(symbol or "").strip().upper() for symbol in symbols if symbol)
    )
    if not normalized:
        return []
    if not stock_quotes_configured():
        raise StockQuoteServiceError("not_configured")

    cache_key = (
        STOCK_QUOTE_DATA_BASE_URL.rstrip("/"),
        str(STOCK_QUOTE_DATA_FEED).lower(),
        normalized,
    )
    now_monotonic = time.monotonic()
    with _CACHE_LOCK:
        cached = _SNAPSHOT_CACHE.get(cache_key)
    if cached and now_monotonic - cached[0] <= float(STOCK_QUOTE_CACHE_SECONDS):
        logging.info("STOCK_QUOTE_CACHE_HIT symbols=%s", ",".join(normalized))
        return list(cached[1])

    payload = _request_json(
        f"{STOCK_QUOTE_DATA_BASE_URL.rstrip('/')}/v2/stocks/snapshots",
        params={
            "symbols": ",".join(normalized),
            "feed": str(STOCK_QUOTE_DATA_FEED).lower(),
        },
    )
    quotes = []
    for symbol in normalized:
        quote = _normalize_snapshot(symbol, _snapshot_for_symbol(payload, symbol))
        if quote is not None:
            quotes.append(quote)
    with _CACHE_LOCK:
        _SNAPSHOT_CACHE[cache_key] = (time.monotonic(), tuple(quotes))
    return quotes


def fetch_market_clock() -> MarketClock:
    """Fetch Alpaca's U.S. market clock using the same read-only credentials."""
    if not stock_quotes_configured():
        raise StockQuoteServiceError("not_configured")
    cache_key = (STOCK_QUOTE_TRADING_BASE_URL.rstrip("/"),)
    now_monotonic = time.monotonic()
    with _CACHE_LOCK:
        cached = _CLOCK_CACHE.get(cache_key)
    if cached and now_monotonic - cached[0] <= float(STOCK_MARKET_CLOCK_CACHE_SECONDS):
        logging.info("STOCK_MARKET_CLOCK_CACHE_HIT")
        return cached[1]

    payload = _request_json(
        f"{STOCK_QUOTE_TRADING_BASE_URL.rstrip('/')}/v2/clock"
    )
    clock = MarketClock(
        is_open=bool(payload.get("is_open")),
        timestamp=_parse_timestamp(payload.get("timestamp")),
        next_open=_parse_timestamp(payload.get("next_open")),
        next_close=_parse_timestamp(payload.get("next_close")),
    )
    with _CACHE_LOCK:
        _CLOCK_CACHE[cache_key] = (time.monotonic(), clock)
    return clock


def _timezone(home_location: Optional[dict]):
    timezone_name = ""
    if isinstance(home_location, dict):
        timezone_name = str(home_location.get("timezone") or "").strip()
    if timezone_name:
        try:
            return ZoneInfo(timezone_name)
        except Exception:
            logging.warning("STOCK_TIMEZONE_INVALID timezone=%r", timezone_name)
    return datetime.now().astimezone().tzinfo


def _local_now(home_location: Optional[dict], now: Optional[datetime]) -> datetime:
    timezone_info = _timezone(home_location)
    current = now or datetime.now(timezone_info)
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone_info)
    return current.astimezone(timezone_info)


def _clock_text(value: datetime) -> str:
    if value.second + value.microsecond / 1_000_000 >= 30:
        value = value + timedelta(minutes=1)
    value = value.replace(second=0, microsecond=0)
    try:
        return value.strftime("%-I:%M %p")
    except Exception:
        return value.strftime("%I:%M %p").lstrip("0")


def _relative_time_text(
    value: Optional[datetime],
    *,
    home_location: Optional[dict],
    now: datetime,
) -> str:
    if value is None:
        return "at its next regular session"
    local_value = value.astimezone(_timezone(home_location))
    if local_value.date() == now.date():
        suffix = "today"
    elif local_value.date() == now.date() + timedelta(days=1):
        suffix = "tomorrow"
    else:
        suffix = f"on {local_value.strftime('%A, %B')} {local_value.day}"
    return f"at {_clock_text(local_value)} {suffix}"


def _format_price(value: float) -> str:
    if abs(value) < 1.0:
        return f"${value:,.4f}"
    return f"${value:,.2f}"


def _quote_subject(symbol: str) -> str:
    return str(_configured_labels().get(symbol, symbol) or symbol)


def _quote_sentence(quote: StockQuote) -> str:
    subject = _quote_subject(quote.symbol)
    lead = f"{subject} is at {_format_price(quote.price)}"

    if quote.change is None or quote.change_percent is None:
        return f"{lead}."
    if abs(quote.change) < 0.005 and abs(quote.change_percent) < 0.005:
        return f"{lead}, unchanged."
    direction = "up" if quote.change > 0 else "down"
    return (
        f"{lead}, {direction} {_format_price(abs(quote.change))}, or "
        f"{abs(quote.change_percent):.2f} percent."
    )


def _close_sentence(quote: StockQuote, *, market_open: Optional[bool]) -> str:
    subject = _quote_subject(quote.symbol)

    if market_open and quote.previous_close is not None:
        return f"{subject}'s previous close was {_format_price(quote.previous_close)}."
    if market_open is None and quote.previous_close is not None:
        return (
            f"{subject}'s latest confirmed close was "
            f"{_format_price(quote.previous_close)}."
        )

    close = quote.session_close
    if close is None:
        close = quote.previous_close if quote.previous_close is not None else quote.price
    lead = f"{subject} closed at {_format_price(close)}"
    if quote.session_close is None or quote.previous_close is None:
        return f"{lead}."

    change = quote.session_close - quote.previous_close
    if quote.previous_close == 0:
        return f"{lead}."
    change_percent = (change / quote.previous_close) * 100.0
    if abs(change) < 0.005 and abs(change_percent) < 0.005:
        return f"{lead}, unchanged."
    direction = "up" if change > 0 else "down"
    return (
        f"{lead}, {direction} {_format_price(abs(change))}, or "
        f"{abs(change_percent):.2f} percent."
    )


def _service_error_response(error: StockQuoteServiceError) -> str:
    if error.code == "not_configured":
        return (
            "Stock quotes aren't configured yet. Add an Alpaca API key ID "
            "and secret key to private configuration."
        )
    if error.code == "authentication":
        return "Alpaca rejected the configured stock quote credentials."
    if error.code == "rate_limited":
        return "The stock quote service is rate-limited right now."
    if error.code == "not_found":
        return "I couldn't find that stock quote."
    if error.code == "timeout":
        return "The stock quote service took too long to respond."
    return "I couldn't reach the stock quote service right now."


def _market_clock_response(
    intent: str,
    clock: MarketClock,
    *,
    home_location: Optional[dict],
    now: datetime,
) -> str:
    next_open = _relative_time_text(clock.next_open, home_location=home_location, now=now)
    next_close = _relative_time_text(clock.next_close, home_location=home_location, now=now)
    if intent == "market_status":
        if clock.is_open:
            return f"Yes. The U.S. stock market is open and closes {next_close}."
        return f"No. The U.S. stock market is closed and next opens {next_open}."
    if intent == "market_open":
        if clock.is_open:
            return f"The U.S. stock market is already open and closes {next_close}."
        return f"The U.S. stock market next opens {next_open}."
    if clock.is_open:
        return f"The U.S. stock market closes {next_close}."
    return f"The next regular U.S. stock market close is {next_close}."


def handle_stock_quote_query(
    text: str,
    *,
    home_location: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> Optional[str]:
    """Return a deterministic read-only stock response, or None if unrelated."""
    query = parse_stock_query(text)
    if query is None:
        return None
    current = _local_now(home_location, now)

    if query.intent not in {"quote", "close"}:
        try:
            clock = fetch_market_clock()
        except StockQuoteServiceError as exc:
            return _service_error_response(exc)
        return _market_clock_response(
            query.intent,
            clock,
            home_location=home_location,
            now=current,
        )

    if query.too_many_symbols:
        return f"I can quote up to {STOCK_QUOTE_MAX_SYMBOLS} stocks at once."
    try:
        quotes = fetch_stock_quotes(query.symbols)
    except StockQuoteServiceError as exc:
        return _service_error_response(exc)
    if not quotes:
        symbols = " and ".join(query.symbols)
        return f"I couldn't find a current quote for {symbols}."

    if query.intent == "close":
        clock = None
        try:
            clock = fetch_market_clock()
        except StockQuoteServiceError:
            logging.info("STOCK_MARKET_CLOCK_UNAVAILABLE_FOR_CLOSE")
        sentences = [
            _close_sentence(quote, market_open=clock.is_open if clock else None)
            for quote in quotes
        ]
    else:
        sentences = [_quote_sentence(quote) for quote in quotes]
    return " ".join(sentences)


def _clear_caches_for_tests() -> None:
    with _CACHE_LOCK:
        _SNAPSHOT_CACHE.clear()
        _CLOCK_CACHE.clear()

from __future__ import annotations

from datetime import datetime
import sys
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import stock_quote_controls
from semantic_router import RouteOutcome, route_utterance
from stock_quote_controls import MarketClock, StockQuote, StockQuoteServiceError


PACIFIC = ZoneInfo("America/Los_Angeles")
HOME_LOCATION = {"timezone": "America/Los_Angeles"}
NOW = datetime(2026, 7, 13, 3, 0, tzinfo=PACIFIC)


class StockQueryParserTests(unittest.TestCase):
    def test_company_ticker_and_multi_symbol_forms(self):
        apple = stock_quote_controls.parse_stock_query("What's Apple's stock price?")
        ticker = stock_quote_controls.parse_stock_query("What is MSFT trading at?")
        multiple = stock_quote_controls.parse_stock_query(
            "Stock quote for Apple and Microsoft"
        )

        self.assertEqual((apple.intent, apple.symbols), ("quote", ("AAPL",)))
        self.assertEqual(ticker.symbols, ("MSFT",))
        self.assertEqual(multiple.symbols, ("AAPL", "MSFT"))

    def test_deployment_aliases_extend_builtin_company_names(self):
        with mock.patch.object(
            stock_quote_controls,
            "STOCK_SYMBOL_ALIAS_OVERRIDES",
            {"my company": "ACME"},
        ):
            builtin = stock_quote_controls.parse_stock_query("quote Apple")
            custom = stock_quote_controls.parse_stock_query("quote my company")

        self.assertEqual(builtin.symbols, ("AAPL",))
        self.assertEqual(custom.symbols, ("ACME",))

    def test_daily_performance_and_close_forms_are_distinct(self):
        current = stock_quote_controls.parse_stock_query(
            "How is Nvidia stock doing today?"
        )
        close = stock_quote_controls.parse_stock_query("How did Apple close?")

        self.assertEqual((current.intent, current.symbols), ("quote", ("NVDA",)))
        self.assertEqual((close.intent, close.symbols), ("close", ("AAPL",)))

    def test_natural_stock_contractions_and_word_orders(self):
        cases = {
            "How’s Apple stock?": ("AAPL",),
            "What's Apple stock doing?": ("AAPL",),
            "What's the price of Microsoft stock?": ("MSFT",),
            "Check Nvidia stock": ("NVDA",),
            "Apple stock price": ("AAPL",),
            "How are Apple and Microsoft stocks doing?": ("AAPL", "MSFT"),
        }

        for phrase, symbols in cases.items():
            with self.subTest(phrase=phrase):
                query = stock_quote_controls.parse_stock_query(phrase)
                self.assertIsNotNone(query)
                self.assertEqual((query.intent, query.symbols), ("quote", symbols))

    def test_company_conversation_without_market_language_is_not_claimed(self):
        for phrase in (
            "How's Apple doing as a company?",
            "What's Apple known for?",
            "Tell me about Nvidia",
        ):
            with self.subTest(phrase=phrase):
                self.assertIsNone(stock_quote_controls.parse_stock_query(phrase))

    def test_market_clock_forms(self):
        status = stock_quote_controls.parse_stock_query("Is the stock market open?")
        opening = stock_quote_controls.parse_stock_query(
            "When does the U.S. stock market open?"
        )
        closing = stock_quote_controls.parse_stock_query(
            "What time will the market close?"
        )

        self.assertEqual(status.intent, "market_status")
        self.assertEqual(opening.intent, "market_open")
        self.assertEqual(closing.intent, "market_close")

    def test_unrelated_commands_are_not_claimed(self):
        for phrase in (
            "open the garage door",
            "what's the weather tomorrow",
            "play Market Street",
            "how is the lamp doing",
        ):
            with self.subTest(phrase=phrase):
                self.assertIsNone(stock_quote_controls.parse_stock_query(phrase))

    def test_symbol_limit_is_reported_without_dropping_the_intent(self):
        query = stock_quote_controls.parse_stock_query(
            "quote AAPL, MSFT, NVDA, TSLA, AMZN and META",
            max_symbols=5,
        )

        self.assertEqual(query.symbols, ("AAPL", "MSFT", "NVDA", "TSLA", "AMZN"))
        self.assertTrue(query.too_many_symbols)

    def test_stock_queries_route_as_deterministic_utilities(self):
        for phrase in (
            "quote MSFT",
            "is the stock market open",
            "How’s Apple stock?",
            "Apple stock price",
        ):
            with self.subTest(phrase=phrase):
                self.assertEqual(
                    route_utterance(text=phrase).outcome,
                    RouteOutcome.DEVICE,
                )


class StockProviderTests(unittest.TestCase):
    def setUp(self):
        stock_quote_controls._clear_caches_for_tests()

    def test_snapshot_normalization_retains_trade_and_daily_close(self):
        quote = stock_quote_controls._normalize_snapshot(
            "AAPL",
            {
                "latestTrade": {"p": 201.25, "t": "2026-07-13T18:00:00Z"},
                "dailyBar": {"c": 200.0},
                "prevDailyBar": {"c": 195.0},
            },
        )

        self.assertEqual(quote.price, 201.25)
        self.assertEqual(quote.session_close, 200.0)
        self.assertEqual(quote.previous_close, 195.0)
        self.assertAlmostEqual(quote.change, 6.25)
        self.assertAlmostEqual(quote.change_percent, 3.205128, places=5)
        self.assertEqual(quote.as_of.isoformat(), "2026-07-13T18:00:00+00:00")

    def test_multi_symbol_snapshot_is_fetched_once_and_cached(self):
        payload = {
            "AAPL": {"latestTrade": {"p": 200}, "prevDailyBar": {"c": 195}},
            "MSFT": {"latestTrade": {"p": 500}, "prevDailyBar": {"c": 490}},
        }
        with (
            mock.patch.object(
                stock_quote_controls, "stock_quotes_configured", return_value=True
            ),
            mock.patch.object(
                stock_quote_controls, "_request_json", return_value=payload
            ) as request_json,
        ):
            first = stock_quote_controls.fetch_stock_quotes(["AAPL", "MSFT"])
            second = stock_quote_controls.fetch_stock_quotes(["AAPL", "MSFT"])

        self.assertEqual([quote.symbol for quote in first], ["AAPL", "MSFT"])
        self.assertEqual(first, second)
        request_json.assert_called_once()
        self.assertEqual(
            request_json.call_args.kwargs["params"]["symbols"], "AAPL,MSFT"
        )
        self.assertEqual(request_json.call_args.kwargs["params"]["feed"], "iex")

    def test_missing_credentials_fail_before_any_request(self):
        with mock.patch.object(
            stock_quote_controls, "stock_quotes_configured", return_value=False
        ):
            with self.assertRaises(StockQuoteServiceError) as raised:
                stock_quote_controls.fetch_stock_quotes(["AAPL"])

        self.assertEqual(raised.exception.code, "not_configured")


class StockResponseTests(unittest.TestCase):
    def setUp(self):
        stock_quote_controls._clear_caches_for_tests()
        self.quote = StockQuote(
            symbol="AAPL",
            price=200.0,
            session_close=198.0,
            previous_close=195.0,
            change=5.0,
            change_percent=(5.0 / 195.0) * 100.0,
            as_of=datetime(2026, 7, 13, 18, 0, tzinfo=ZoneInfo("UTC")),
        )

    def test_live_quote_is_concise_and_skips_market_clock(self):
        with (
            mock.patch.object(
                stock_quote_controls, "fetch_stock_quotes", return_value=[self.quote]
            ),
            mock.patch.object(
                stock_quote_controls,
                "fetch_market_clock",
            ) as fetch_clock,
        ):
            response = stock_quote_controls.handle_stock_quote_query(
                "what's Apple stock price",
                home_location=HOME_LOCATION,
                now=NOW,
            )

        self.assertEqual(
            response,
            "Apple is at $200.00, up $5.00, or 2.56 percent.",
        )
        fetch_clock.assert_not_called()

    def test_close_during_market_hours_uses_previous_close(self):
        with (
            mock.patch.object(
                stock_quote_controls, "fetch_stock_quotes", return_value=[self.quote]
            ),
            mock.patch.object(
                stock_quote_controls,
                "fetch_market_clock",
                return_value=MarketClock(True, NOW, None, None),
            ),
        ):
            response = stock_quote_controls.handle_stock_quote_query(
                "how did Apple close",
                home_location=HOME_LOCATION,
                now=NOW,
            )

        self.assertEqual(
            response,
            "Apple's previous close was $195.00.",
        )

    def test_close_without_market_clock_uses_confirmed_previous_bar(self):
        with (
            mock.patch.object(
                stock_quote_controls, "fetch_stock_quotes", return_value=[self.quote]
            ),
            mock.patch.object(
                stock_quote_controls,
                "fetch_market_clock",
                side_effect=StockQuoteServiceError("timeout"),
            ),
        ):
            response = stock_quote_controls.handle_stock_quote_query(
                "how did Apple close",
                home_location=HOME_LOCATION,
                now=NOW,
            )

        self.assertEqual(response, "Apple's latest confirmed close was $195.00.")

    def test_closed_market_reports_next_open_in_home_timezone(self):
        next_open = datetime(2026, 7, 13, 13, 30, tzinfo=ZoneInfo("UTC"))
        clock = MarketClock(False, NOW, next_open, None)
        with mock.patch.object(
            stock_quote_controls, "fetch_market_clock", return_value=clock
        ):
            response = stock_quote_controls.handle_stock_quote_query(
                "is the stock market open",
                home_location=HOME_LOCATION,
                now=NOW,
            )

        self.assertEqual(
            response,
            "No. The U.S. stock market is closed and next opens at 6:30 AM today.",
        )

    def test_not_configured_response_is_explicit(self):
        with mock.patch.object(
            stock_quote_controls,
            "fetch_stock_quotes",
            side_effect=StockQuoteServiceError("not_configured"),
        ):
            response = stock_quote_controls.handle_stock_quote_query(
                "quote AAPL",
                now=NOW,
            )

        self.assertIn("aren't configured yet", response)
        self.assertIn("Alpaca API key ID", response)


if __name__ == "__main__":
    unittest.main()

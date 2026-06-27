"""Unit tests for `src/paper_trader.PaperTrader` with fully mocked Alpaca clients.

Critical regressions covered:
- F1.1: round() preserves fractional shares (no int() truncation drift).
- F1.3: `cancel_open_orders(symbol=t)` is scoped per ticker, not blanket.
- F1.4: SELL uses `min(held, trade_sell_qty)` partial-close logic.
- F1.5: position cap uses the real ask price (not a hardcoded $100).
"""

from unittest.mock import MagicMock

import pytest
from alpaca.trading.enums import OrderSide

from config import Config
from src.paper_trader import PaperTrader


@pytest.fixture
def cfg() -> Config:
    c = Config()
    c.alpaca_api_key = "pk_test"
    c.alpaca_secret_key = "test-secret-key"  # noqa: S105
    c.alpaca_paper = True
    c.trade_buy_qty = 10
    c.trade_sell_qty = 10
    c.trade_max_position_pct = 0.02
    return c


@pytest.fixture
def trader(cfg: Config) -> PaperTrader:
    t = PaperTrader(cfg)
    t.trade_client = MagicMock()
    t._stock_client = MagicMock()  # noqa: SLF001
    t._crypto_client = MagicMock()  # noqa: SLF001
    t.trade_client.get_clock.return_value.is_open = True
    t.trade_client.get_orders.return_value = []
    return t


def _position(
    symbol: str,
    qty: float,
    side: str = "long",
    current_price: float = 100.0,
) -> MagicMock:
    p = MagicMock()
    p.symbol = symbol
    p.qty = qty
    p.market_value = abs(qty) * current_price
    p.avg_entry_price = current_price
    p.current_price = current_price
    p.unrealized_pl = 0.0
    p.unrealized_plpc = 0.0
    p.side = side
    return p


def _quote(ask: float | None, bid: float | None = None):
    """Build a fake Alpaca Quote object with the given bid/ask prices."""
    q = MagicMock()
    q.ask_price = ask
    q.bid_price = bid if bid is not None else ask
    q.ask_size = 1000
    q.bid_size = 1000
    return q


def _set_quotes(trader: PaperTrader, prices: dict[str, float | None]) -> None:
    """Configure trader._stock_client.get_stock_latest_quote to return the prices map.

    `MagicMock(sym=...)` sets up attributes, NOT dict-style `.get('.sym')` lookups,
    so we override `.get` with `side_effect` to emulate the real Alpaca response
    object (a mapping of symbol → Quote).

    Pass `None` as a price to simulate Alpaca returning a quote with no ask
    (e.g. during an outage). `_quote` will then carry `ask_price=None`.
    """
    quotes = {sym: _quote(ask=price) for sym, price in prices.items()}
    response = MagicMock()
    response.get.side_effect = lambda sym, default=None: quotes.get(sym, default)
    trader._stock_client.get_stock_latest_quote.return_value = response  # noqa: SLF001


def _set_account(trader: PaperTrader, equity: float) -> None:
    trader.trade_client.get_account.return_value = MagicMock(
        equity=equity,
        cash=equity / 2,
        buying_power=equity,
        last_equity=max(0.0, equity - 250.0),
    )


# ───────────────────────── F1.1 round() fractional shares ─────────────────────────


def test_sell_round_trips_fractional_long_position(
    trader: PaperTrader, cfg: Config
) -> None:
    """Held 12.7 shares → sell 10 (bounded by trade_sell_qty), not 12 or 13."""
    trader.trade_client.get_all_positions.return_value = [_position("AAPL", 12.7)]
    _set_quotes(trader, {"AAPL": 100.0})
    _set_account(trader, equity=100_000.0)
    cfg.trade_sell_qty = 10
    trades = trader.reconcile({"AAPL": {"signal": "SELL", "score": -0.9}})
    assert trades == [("AAPL", 10, "SELL")]
    sent_order = trader.trade_client.submit_order.call_args.kwargs["order_data"]
    assert sent_order.qty == 10
    assert sent_order.side == OrderSide.SELL


def test_sell_round_trips_fractional_short_position(trader: PaperTrader) -> None:
    """Short position qty = -3.7 → BUY 3 shares (floor(abs), never sell more than held)."""
    trader.trade_client.get_all_positions.return_value = [
        _position("TSLA", -3.7, side="short")
    ]
    _set_quotes(trader, {"TSLA": 200.0})
    _set_account(trader, equity=100_000.0)
    trades = trader.reconcile({"TSLA": {"signal": "SELL", "score": -0.9}})
    assert trades == [("TSLA", 3, "SELL")]
    sent_order = trader.trade_client.submit_order.call_args.kwargs["order_data"]
    assert sent_order.qty == 3
    assert sent_order.side == OrderSide.BUY


# ───────────────────────── Bulk cancel (UX-N1 fix) ─────────────────────────


def test_cancel_called_once(trader: PaperTrader) -> None:
    """Bulk cancel_orders() is called once per reconcile, regardless of signals."""
    trader.trade_client.get_all_positions.return_value = []
    _set_quotes(trader, {"AAPL": 100.0})
    _set_account(trader, equity=100_000.0)
    trader.reconcile({"AAPL": {"signal": "BUY", "score": 0.9}})
    trader.trade_client.cancel_orders.assert_called_once()


def test_cancel_clears_all_open_orders(trader: PaperTrader) -> None:
    """cancel_orders() is called with no arguments = cancels everything."""
    trader.trade_client.get_all_positions.return_value = []
    _set_quotes(trader, {})
    _set_account(trader, equity=100_000.0)
    trader.reconcile({"AAPL": {"signal": "BUY", "score": 0.9}})
    trader.trade_client.cancel_orders.assert_called_once_with()


# ───────────────────────── F1.4 partial close bounded by trade_sell_qty ─────────────────────────


def test_sell_partial_close_when_position_is_large(
    trader: PaperTrader, cfg: Config
) -> None:
    """Held 1000 shares with trade_sell_qty=20 → close only 20, not 1000."""
    cfg.trade_sell_qty = 20
    trader.trade_client.get_all_positions.return_value = [_position("MSFT", 1000)]
    _set_quotes(trader, {"MSFT": 300.0})
    _set_account(trader, equity=100_000.0)
    trades = trader.reconcile({"MSFT": {"signal": "SELL", "score": -0.8}})
    sent_order = trader.trade_client.submit_order.call_args.kwargs["order_data"]
    assert sent_order.qty == 20
    assert trades == [("MSFT", 20, "SELL")]


def test_sell_full_close_when_position_is_smaller_than_qty(
    trader: PaperTrader, cfg: Config
) -> None:
    """Held 5 shares with trade_sell_qty=10 → close all 5 (not 10, can't sell more)."""
    cfg.trade_sell_qty = 10
    trader.trade_client.get_all_positions.return_value = [_position("MSFT", 5)]
    _set_quotes(trader, {"MSFT": 300.0})
    _set_account(trader, equity=100_000.0)
    trader.reconcile({"MSFT": {"signal": "SELL", "score": -0.8}})
    sent_order = trader.trade_client.submit_order.call_args.kwargs["order_data"]
    assert sent_order.qty == 5


# ───────────────────────── F1.5 position cap uses real ask ─────────────────────────


def test_position_cap_engages_on_expensive_stock(
    trader: PaperTrader, cfg: Config
) -> None:
    """Ask=$1000 x 10 shares = $10000 > 2% of $100k ($2000) -> MAX_POS_CAP."""
    cfg.trade_max_position_pct = 0.02
    _set_account(trader, equity=100_000.0)
    trader.trade_client.get_all_positions.return_value = []
    _set_quotes(trader, {"AAPL": 1000.0})
    trades = trader.reconcile({"AAPL": {"signal": "BUY", "score": 0.9}})
    assert trades == [("AAPL", 0, "MAX_POS_CAP")]
    trader.trade_client.submit_order.assert_not_called()


def test_position_cap_relaxes_on_cheap_stock(trader: PaperTrader, cfg: Config) -> None:
    """Ask=$5 x 10 shares = $50 < 2% of $100k ($2000) -> trade goes through."""
    cfg.trade_max_position_pct = 0.02
    _set_account(trader, equity=100_000.0)
    trader.trade_client.get_all_positions.return_value = []
    _set_quotes(trader, {"XYZ": 5.0})
    trades = trader.reconcile({"XYZ": {"signal": "BUY", "score": 0.9}})
    assert trades == [("XYZ", 10, "BUY")]
    trader.trade_client.submit_order.assert_called_once()


def test_position_cap_no_equity_blocks_trade(trader: PaperTrader) -> None:
    """Equity=$0 → NO_EQUITY guard blocks any BUY, regardless of ask."""
    _set_account(trader, equity=0.0)
    trader.trade_client.get_all_positions.return_value = []
    _set_quotes(trader, {"AAPL": 100.0})
    trades = trader.reconcile({"AAPL": {"signal": "BUY", "score": 0.9}})
    assert trades == [("AAPL", 0, "NO_EQUITY")]
    trader.trade_client.submit_order.assert_not_called()


def test_position_cap_misses_ask_logs_warning(
    trader: PaperTrader, caplog: pytest.LogCaptureFixture
) -> None:
    """If Alpaca returns None for ask, refuse to trade."""
    _set_account(trader, equity=100_000.0)
    trader.trade_client.get_all_positions.return_value = []
    _set_quotes(trader, {"AAPL": None})
    with caplog.at_level("WARNING"):
        trades = trader.reconcile({"AAPL": {"signal": "BUY", "score": 0.9}})
    assert trades == [("AAPL", 0, "NO_ASK")]
    assert any("No usable ask" in r.message for r in caplog.records)


# ───────────────────────── Order exception handling ─────────────────────────


def test_buy_failure_records_trade_entry(trader: PaperTrader) -> None:
    """submit_order raises → trade entry is "BUY_FAIL:<exc>", no qty placed."""
    _set_account(trader, equity=100_000.0)
    trader.trade_client.get_all_positions.return_value = []
    _set_quotes(trader, {"AAPL": 100.0})
    trader.trade_client.submit_order.side_effect = RuntimeError("insufficient balance")
    trades = trader.reconcile({"AAPL": {"signal": "BUY", "score": 0.9}})
    assert len(trades) == 1
    assert trades[0][0] == "AAPL" and trades[0][1] == 0
    assert trades[0][2].startswith("BUY_FAIL:")


def test_no_quotes_calls_when_no_new_buys(trader: PaperTrader) -> None:
    """If no tickers need a BUY, do not hit Alpaca for quotes (rate-limit hygiene).

    Cancel_orders is still called (bulk cancel — cheap single API call).
    """
    _set_account(trader, equity=100_000.0)
    trader.trade_client.get_all_positions.return_value = [_position("AAPL", 10)]
    trader.reconcile(
        {
            "AAPL": {"signal": "HOLD", "score": 0.0},
            "TSLA": {"signal": "HOLD", "score": 0.0},
        }
    )
    trader._stock_client.get_stock_latest_quote.assert_not_called()  # noqa: SLF001
    trader.trade_client.cancel_orders.assert_called_once()


def test_cancel_is_universal(trader: PaperTrader) -> None:
    """Bulk cancel_orders() runs for every reconcile, covering all tickers."""
    _set_account(trader, equity=100_000.0)
    trader.trade_client.get_all_positions.return_value = [_position("TSLA", 5)]
    _set_quotes(trader, {"AAPL": 100.0})
    trader.reconcile(
        {
            "AAPL": {"signal": "BUY", "score": 0.9},
            "TSLA": {"signal": "SELL", "score": -0.9},
            "NFLX": {"signal": "HOLD", "score": 0.0},
            "AMZN": {"signal": "HOLD", "score": 0.0},
        }
    )
    trader.trade_client.cancel_orders.assert_called_once()


# ───────────────────────── N-TEST-GAP-1: coverage gaps ─────────────────────────


def test_crypto_market_open(trader: PaperTrader, cfg: Config) -> None:
    """Crypto market_open() returns True without calling get_clock."""
    cfg.asset_class = "crypto"
    assert trader.market_open() is True
    trader.trade_client.get_clock.assert_not_called()


def test_buy_blocked_when_short_position_exists(trader: PaperTrader) -> None:
    """BUY signal on a ticker with a short position is skipped (no-pyramid)."""
    trader.trade_client.get_all_positions.return_value = [
        _position("TSLA", -10, side="short")
    ]
    _set_account(trader, equity=100_000.0)
    trades = trader.reconcile({"TSLA": {"signal": "BUY", "score": 0.9}})
    assert len(trades) == 0
    trader.trade_client.submit_order.assert_not_called()


def test_no_ask_zero_price(
    trader: PaperTrader, caplog: pytest.LogCaptureFixture
) -> None:
    """Ask price of 0 also triggers NO_ASK."""
    _set_account(trader, equity=100_000.0)
    trader.trade_client.get_all_positions.return_value = []
    _set_quotes(trader, {"AAPL": 0.0})
    with caplog.at_level("WARNING"):
        trades = trader.reconcile({"AAPL": {"signal": "BUY", "score": 0.9}})
    assert trades == [("AAPL", 0, "NO_ASK")]


def test_reconcile_floor_match_display(trader: PaperTrader) -> None:
    """Reconcile math.floor(abs(qty)) matches _update_table display logic."""
    import math

    trader.trade_client.get_all_positions.return_value = [_position("AAPL", 5.7)]
    _set_quotes(trader, {"AAPL": 200.0})
    _set_account(trader, equity=100_000.0)
    held = math.floor(abs(5.7))
    trader.reconcile({"AAPL": {"signal": "SELL", "score": -0.9}})
    sent_order = trader.trade_client.submit_order.call_args.kwargs["order_data"]
    assert sent_order.qty == held

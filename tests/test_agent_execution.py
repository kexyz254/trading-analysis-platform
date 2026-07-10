import pytest

from trading_assistant.agents.execution import ExecutionAgent
from trading_assistant.portfolio.manager import PortfolioManager
from trading_assistant.strategies.base import Signal


class DummyExchange:
    def __init__(self):
        self.buy_orders = []
        self.sell_orders = []
        self.stop_orders = []  # (symbol, type, side, amount, price, params)
        self.cancelled_orders = []
        self._next_stop_id = 1
        self._ticker = {"bid": 100.0, "ask": 100.1}  # tight spread by default

    def fetch_ticker(self, symbol):
        return dict(self._ticker)

    def create_market_buy_order(self, symbol, quantity):
        self.buy_orders.append((symbol, quantity))
        return {"symbol": symbol, "quantity": quantity, "side": "buy"}

    def create_market_sell_order(self, symbol, quantity):
        self.sell_orders.append((symbol, quantity))
        return {"symbol": symbol, "quantity": quantity, "side": "sell"}

    def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.stop_orders.append((symbol, type_, side, amount, price, params or {}))
        order_id = str(self._next_stop_id)
        self._next_stop_id += 1
        return {"id": order_id, "symbol": symbol, "type": type_}

    def cancel_order(self, order_id, symbol):
        self.cancelled_orders.append((order_id, symbol))


class FlakyExchange(DummyExchange):
    def __init__(self, fail_times):
        super().__init__()
        self.fail_times = fail_times
        self.attempts = 0

    def create_market_buy_order(self, symbol, quantity):
        self.attempts += 1
        if self.attempts <= self.fail_times:
            raise ConnectionError("simulated network blip")
        return super().create_market_buy_order(symbol, quantity)


def test_buy_opens_position_and_places_exchange_order():
    portfolio = PortfolioManager(initial_capital=10000.0)
    exchange = DummyExchange()
    agent = ExecutionAgent(portfolio=portfolio, exchange=exchange)

    result = agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0)

    assert result.action == "opened_position"
    assert "BTC/USDT" in portfolio.positions
    assert exchange.buy_orders == [("BTC/USDT", 1.0)]


def test_sell_closes_position_and_places_exchange_sell_order():
    """Regression test for the fixed bug: the old LiveTradingEngine sell path
    never called exchange.create_market_sell_order, leaving a real position open."""
    portfolio = PortfolioManager(initial_capital=10000.0)
    portfolio.open_position("BTC/USDT", price=100.0, quantity=1.0)
    exchange = DummyExchange()
    agent = ExecutionAgent(portfolio=portfolio, exchange=exchange)

    result = agent.execute("BTC/USDT", Signal.SELL, price=110.0, quantity=0.0)

    assert result.action == "closed_position"
    assert result.pnl == pytest.approx(10.0)
    assert "BTC/USDT" not in portfolio.positions
    assert exchange.sell_orders == [("BTC/USDT", 1.0)]


def test_buy_rejects_before_touching_exchange_when_capital_insufficient():
    """Regression test: the exchange order used to be placed BEFORE checking
    affordability, so a real order could fill while the portfolio silently
    dropped it as 'insufficient capital' with no record and no rollback."""
    portfolio = PortfolioManager(initial_capital=100.0)
    exchange = DummyExchange()
    agent = ExecutionAgent(portfolio=portfolio, exchange=exchange)

    result = agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=10.0)  # cost=1000 > capital=100

    assert result.action == "no_action"
    assert result.error == "insufficient capital"
    assert exchange.buy_orders == []  # the exchange must never have been called
    assert "BTC/USDT" not in portfolio.positions


def test_no_action_when_buy_signal_but_already_holding():
    portfolio = PortfolioManager(initial_capital=10000.0)
    portfolio.open_position("BTC/USDT", price=100.0, quantity=1.0)
    agent = ExecutionAgent(portfolio=portfolio, exchange=DummyExchange())

    result = agent.execute("BTC/USDT", Signal.BUY, price=105.0, quantity=1.0)

    assert result.action == "no_action"


def test_buy_retries_transient_exchange_errors_and_succeeds():
    portfolio = PortfolioManager(initial_capital=10000.0)
    exchange = FlakyExchange(fail_times=2)
    agent = ExecutionAgent(portfolio=portfolio, exchange=exchange, max_retries=3, backoff_seconds=0.0)

    result = agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0)

    assert result.action == "opened_position"
    assert exchange.attempts == 3


def test_buy_reports_error_after_exhausting_retries():
    portfolio = PortfolioManager(initial_capital=10000.0)
    exchange = FlakyExchange(fail_times=10)
    agent = ExecutionAgent(portfolio=portfolio, exchange=exchange, max_retries=2, backoff_seconds=0.0)

    result = agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0)

    assert result.action == "error"
    assert "BTC/USDT" not in portfolio.positions


def test_buy_places_a_real_protective_stop_when_stop_loss_pct_configured():
    """Regression test for the most critical gap found in the whole system:
    a real position previously had NO protective floor at all. A resting
    STOP_LOSS_LIMIT sell must be placed immediately after every real buy."""
    portfolio = PortfolioManager(initial_capital=10000.0)
    exchange = DummyExchange()
    agent = ExecutionAgent(portfolio=portfolio, exchange=exchange, stop_loss_pct=0.02)

    result = agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0)

    assert result.action == "opened_position"
    assert len(exchange.stop_orders) == 1
    symbol, type_, side, amount, price, params = exchange.stop_orders[0]
    assert symbol == "BTC/USDT"
    assert type_ == "STOP_LOSS_LIMIT"
    assert side == "sell"
    assert amount == 1.0
    assert params["stopPrice"] == pytest.approx(98.0)  # 100 * (1 - 0.02)
    assert portfolio.positions["BTC/USDT"]["stop_order_id"] == "1"


def test_buy_places_no_protective_stop_when_stop_loss_pct_not_configured():
    """Backward compatible: existing callers that never opt in keep the
    original (unprotected) behavior rather than erroring."""
    portfolio = PortfolioManager(initial_capital=10000.0)
    exchange = DummyExchange()
    agent = ExecutionAgent(portfolio=portfolio, exchange=exchange)  # stop_loss_pct defaults to None

    agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0)

    assert exchange.stop_orders == []
    assert "stop_order_id" not in portfolio.positions["BTC/USDT"]


def test_buy_uses_a_caller_supplied_stop_loss_pct_override():
    """RiskAgent's volatility-adjusted per-trade stop distance must win
    over ExecutionAgent's own static default - the real protective stop
    has to match whatever distance position sizing already assumed."""
    portfolio = PortfolioManager(initial_capital=10000.0)
    exchange = DummyExchange()
    agent = ExecutionAgent(portfolio=portfolio, exchange=exchange, stop_loss_pct=0.02)

    agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0, stop_loss_pct=0.05)

    _, _, _, _, _, params = exchange.stop_orders[0]
    assert params["stopPrice"] == pytest.approx(95.0)  # 100 * (1 - 0.05), not the static 0.02
    assert portfolio.positions["BTC/USDT"]["stop_price"] == pytest.approx(95.0)


def test_buy_falls_back_to_the_static_stop_loss_pct_without_an_override():
    portfolio = PortfolioManager(initial_capital=10000.0)
    exchange = DummyExchange()
    agent = ExecutionAgent(portfolio=portfolio, exchange=exchange, stop_loss_pct=0.02)

    agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0)  # no override

    _, _, _, _, _, params = exchange.stop_orders[0]
    assert params["stopPrice"] == pytest.approx(98.0)


def test_buy_tracks_stop_price_alongside_stop_order_id():
    """The trailing-stop updater needs a baseline stop_price to compare a
    candidate higher stop against - it must be recorded at buy time, not
    re-derived from entry_price later (which would break once trailing
    has already moved it)."""
    portfolio = PortfolioManager(initial_capital=10000.0)
    exchange = DummyExchange()
    agent = ExecutionAgent(portfolio=portfolio, exchange=exchange, stop_loss_pct=0.02)

    agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0)

    assert portfolio.positions["BTC/USDT"]["stop_price"] == pytest.approx(98.0)


class StopFailingExchange(DummyExchange):
    """Every regular order (buy/sell) succeeds normally, but placing a
    protective stop (create_order) always fails - simulates the real
    testnet failure mode this session found: a position opens/sells fine,
    but the STOP_LOSS_LIMIT leg specifically doesn't go through."""

    def create_order(self, symbol, type_, side, amount, price=None, params=None):
        raise ConnectionError("simulated stop placement failure")


def test_buy_result_flags_stop_order_placed_true_on_success():
    portfolio = PortfolioManager(initial_capital=10000.0)
    exchange = DummyExchange()
    agent = ExecutionAgent(portfolio=portfolio, exchange=exchange, stop_loss_pct=0.02)

    result = agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0)

    assert result.stop_order_placed is True


def test_buy_result_flags_stop_order_placed_false_when_placement_fails():
    # Regression test: a failed stop placement after a real buy fill used
    # to be a log line nobody sees in real time - the caller (Orchestrator)
    # needs this on the result to raise a loud alert instead.
    portfolio = PortfolioManager(initial_capital=10000.0)
    exchange = StopFailingExchange()
    agent = ExecutionAgent(portfolio=portfolio, exchange=exchange, stop_loss_pct=0.02)

    result = agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0)

    assert result.action == "opened_position"  # the buy itself still succeeded
    assert result.stop_order_placed is False
    assert portfolio.positions["BTC/USDT"]["stop_order_id"] is None


def test_buy_result_leaves_stop_order_placed_none_when_not_configured():
    portfolio = PortfolioManager(initial_capital=10000.0)
    exchange = DummyExchange()
    agent = ExecutionAgent(portfolio=portfolio, exchange=exchange)  # no stop_loss_pct

    result = agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0)

    assert result.stop_order_placed is None


def test_sell_cancels_the_resting_protective_stop_before_market_selling():
    """A resting stop holds quantity in reserve (not free balance) - it must
    be cancelled before a market sell for the same quantity fires, or the
    market sell would fail with insufficient free balance on a real exchange."""
    portfolio = PortfolioManager(initial_capital=10000.0)
    exchange = DummyExchange()
    agent = ExecutionAgent(portfolio=portfolio, exchange=exchange, stop_loss_pct=0.02)
    agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0)
    stop_order_id = portfolio.positions["BTC/USDT"]["stop_order_id"]

    result = agent.execute("BTC/USDT", Signal.SELL, price=110.0, quantity=0.0)

    assert result.action == "closed_position"
    assert exchange.cancelled_orders == [(stop_order_id, "BTC/USDT")]
    assert exchange.sell_orders == [("BTC/USDT", 1.0)]


def test_full_close_leaves_stop_order_placed_none_no_remainder_to_protect():
    # A full close has nothing left to re-protect - stop_order_placed must
    # stay None (not False), or the caller would raise a false "unprotected
    # position" alert for a position that no longer exists.
    portfolio = PortfolioManager(initial_capital=10000.0)
    exchange = DummyExchange()
    agent = ExecutionAgent(portfolio=portfolio, exchange=exchange, stop_loss_pct=0.02)
    agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0)

    result = agent.execute("BTC/USDT", Signal.SELL, price=110.0, quantity=0.0)

    assert result.stop_order_placed is None


def test_buy_rejects_when_spread_too_wide():
    portfolio = PortfolioManager(initial_capital=10000.0)
    exchange = DummyExchange()
    exchange._ticker = {"bid": 100.0, "ask": 102.0}  # ~2% spread
    agent = ExecutionAgent(portfolio=portfolio, exchange=exchange, max_spread_pct=0.005)  # 0.5% cap

    result = agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0)

    assert result.action == "no_action"
    assert result.error == "spread too wide"
    assert exchange.buy_orders == []  # the exchange must never have received the order
    assert "BTC/USDT" not in portfolio.positions


def test_buy_approves_when_spread_within_cap():
    portfolio = PortfolioManager(initial_capital=10000.0)
    exchange = DummyExchange()
    exchange._ticker = {"bid": 100.0, "ask": 100.1}  # tight spread
    agent = ExecutionAgent(portfolio=portfolio, exchange=exchange, max_spread_pct=0.01)

    result = agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0)

    assert result.action == "opened_position"
    assert exchange.buy_orders == [("BTC/USDT", 1.0)]


def test_buy_skips_spread_check_when_not_configured():
    """Backward compatible: existing callers that never opt in (no
    max_spread_pct) keep the original behavior, even against a wide spread."""
    portfolio = PortfolioManager(initial_capital=10000.0)
    exchange = DummyExchange()
    exchange._ticker = {"bid": 100.0, "ask": 150.0}  # wildly wide spread
    agent = ExecutionAgent(portfolio=portfolio, exchange=exchange)  # max_spread_pct defaults to None

    result = agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0)

    assert result.action == "opened_position"


class TestExecutePartialSell:
    def test_sells_only_the_requested_quantity_and_leaves_position_open(self):
        portfolio = PortfolioManager(initial_capital=10000.0)
        exchange = DummyExchange()
        agent = ExecutionAgent(portfolio=portfolio, exchange=exchange, stop_loss_pct=0.02)
        agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=10.0)

        result = agent.execute_partial_sell("BTC/USDT", sell_quantity=4.0, price=110.0)

        assert result.action == "partial_close"
        assert result.quantity == 4.0
        assert exchange.sell_orders == [("BTC/USDT", 4.0)]
        assert "BTC/USDT" in portfolio.positions
        assert portfolio.positions["BTC/USDT"]["quantity"] == 6.0

    def test_cancels_old_stop_and_places_a_new_one_for_the_remainder(self):
        portfolio = PortfolioManager(initial_capital=10000.0)
        exchange = DummyExchange()
        agent = ExecutionAgent(portfolio=portfolio, exchange=exchange, stop_loss_pct=0.02)
        agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=10.0)
        original_stop_id = portfolio.positions["BTC/USDT"]["stop_order_id"]

        agent.execute_partial_sell("BTC/USDT", sell_quantity=4.0, price=110.0)

        assert exchange.cancelled_orders == [(original_stop_id, "BTC/USDT")]
        # a fresh stop was placed for the remaining 6.0, at the SAME stop
        # price the position already had (not re-derived from a stale entry price)
        assert len(exchange.stop_orders) == 2
        _, _, _, amount, _, params = exchange.stop_orders[-1]
        assert amount == 6.0
        assert params["stopPrice"] == pytest.approx(98.0)
        assert portfolio.positions["BTC/USDT"]["stop_order_id"] != original_stop_id

    def test_result_flags_stop_order_placed_false_when_reprotection_fails(self):
        # Regression test for the second gap this session found: a partial
        # sell that leaves a remainder with no protective stop used to be
        # only a log line ("... UNPROTECTED") - now it must surface on the
        # result so the caller can alert a human.
        portfolio = PortfolioManager(initial_capital=10000.0)
        exchange = DummyExchange()
        agent = ExecutionAgent(portfolio=portfolio, exchange=exchange, stop_loss_pct=0.02)
        agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=10.0)  # initial stop placed fine
        agent.exchange = StopFailingExchange()  # re-protection after the partial sell now fails

        result = agent.execute_partial_sell("BTC/USDT", sell_quantity=4.0, price=110.0)

        assert result.action == "partial_close"  # the sell itself still succeeded
        assert result.stop_order_placed is False
        assert portfolio.positions["BTC/USDT"]["stop_order_id"] is None

    def test_quantity_larger_than_position_closes_it_entirely(self):
        portfolio = PortfolioManager(initial_capital=10000.0)
        exchange = DummyExchange()
        agent = ExecutionAgent(portfolio=portfolio, exchange=exchange, stop_loss_pct=0.02)
        agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=10.0)

        result = agent.execute_partial_sell("BTC/USDT", sell_quantity=999.0, price=110.0)

        assert result.quantity == 10.0
        assert "BTC/USDT" not in portfolio.positions
        # no remaining position to re-protect - no extra stop order placed
        assert len(exchange.stop_orders) == 1

    def test_no_position_is_a_noop(self):
        portfolio = PortfolioManager(initial_capital=10000.0)
        exchange = DummyExchange()
        agent = ExecutionAgent(portfolio=portfolio, exchange=exchange)

        result = agent.execute_partial_sell("BTC/USDT", sell_quantity=1.0, price=110.0)

        assert result.action == "no_action"

    def test_zero_or_negative_quantity_is_a_noop(self):
        portfolio = PortfolioManager(initial_capital=10000.0)
        exchange = DummyExchange()
        agent = ExecutionAgent(portfolio=portfolio, exchange=exchange, stop_loss_pct=0.02)
        agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=10.0)

        result = agent.execute_partial_sell("BTC/USDT", sell_quantity=0.0, price=110.0)

        assert result.action == "no_action"
        assert portfolio.positions["BTC/USDT"]["quantity"] == 10.0

    def test_reports_error_on_exchange_failure(self):
        class RaisingSellExchange(DummyExchange):
            def create_market_sell_order(self, symbol, quantity):
                raise ConnectionError("simulated outage")

        portfolio = PortfolioManager(initial_capital=10000.0)
        exchange = RaisingSellExchange()
        agent = ExecutionAgent(portfolio=portfolio, exchange=exchange, stop_loss_pct=0.02)
        agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=10.0)

        result = agent.execute_partial_sell("BTC/USDT", sell_quantity=4.0, price=110.0)

        assert result.action == "error"
        # position must be left exactly as it was before the failed attempt
        assert portfolio.positions["BTC/USDT"]["quantity"] == 10.0


def test_buy_does_not_block_when_fetch_ticker_fails():
    """A failed quote check is a data gap, not evidence the market is
    actually illiquid right now - it must not block the trade."""

    class BrokenTickerExchange(DummyExchange):
        def fetch_ticker(self, symbol):
            raise ConnectionError("simulated outage")

    portfolio = PortfolioManager(initial_capital=10000.0)
    exchange = BrokenTickerExchange()
    agent = ExecutionAgent(portfolio=portfolio, exchange=exchange, max_spread_pct=0.01)

    result = agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0)

    assert result.action == "opened_position"


class PartialFillExchange(DummyExchange):
    """Reports fills that differ from what was requested - a market order
    is not guaranteed to fill completely or at exactly the last-seen
    price, and this is the fake that exercises that gap."""

    def __init__(self, filled_quantity, average_price):
        super().__init__()
        self.filled_quantity = filled_quantity
        self.average_price = average_price

    def create_market_buy_order(self, symbol, quantity):
        self.buy_orders.append((symbol, quantity))
        return {"symbol": symbol, "quantity": quantity, "filled": self.filled_quantity, "average": self.average_price}

    def create_market_sell_order(self, symbol, quantity):
        self.sell_orders.append((symbol, quantity))
        return {"symbol": symbol, "quantity": quantity, "filled": self.filled_quantity, "average": self.average_price}


class TestPartialFillResolution:
    def test_buy_records_the_actual_filled_quantity_and_price_not_the_requested_ones(self):
        """Regression: the exchange order's return value was previously
        discarded entirely - a partial fill (or a fill at a different
        price than last-seen) would silently corrupt the portfolio's cost
        basis, since it always recorded the REQUESTED quantity/price
        regardless of what the exchange actually did."""
        portfolio = PortfolioManager(initial_capital=10000.0)
        exchange = PartialFillExchange(filled_quantity=6.0, average_price=101.5)
        agent = ExecutionAgent(portfolio=portfolio, exchange=exchange)

        result = agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=10.0)

        assert result.action == "opened_position"
        assert result.quantity == 6.0
        assert result.price == 101.5
        assert portfolio.positions["BTC/USDT"]["quantity"] == 6.0

    def test_buy_zero_fill_does_not_open_a_position(self):
        portfolio = PortfolioManager(initial_capital=10000.0)
        exchange = PartialFillExchange(filled_quantity=0.0, average_price=100.0)
        agent = ExecutionAgent(portfolio=portfolio, exchange=exchange)

        result = agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=10.0)

        assert result.action == "no_action"
        assert "BTC/USDT" not in portfolio.positions

    def test_buy_protective_stop_sized_off_the_actual_fill_not_the_request(self):
        portfolio = PortfolioManager(initial_capital=10000.0)
        exchange = PartialFillExchange(filled_quantity=6.0, average_price=101.5)
        agent = ExecutionAgent(portfolio=portfolio, exchange=exchange, stop_loss_pct=0.02)

        agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=10.0)

        _, _, _, amount, _, params = exchange.stop_orders[0]
        assert amount == 6.0
        assert params["stopPrice"] == pytest.approx(101.5 * 0.98)

    def test_sell_partial_fill_leaves_the_remainder_open_and_re_protected(self):
        """A SELL signal means "close the position", but if the exchange
        only partially fills the market sell, the portfolio must reflect
        what actually sold, not silently mark the position fully closed
        while the exchange still holds the rest."""
        portfolio = PortfolioManager(initial_capital=10000.0)
        exchange = DummyExchange()
        agent = ExecutionAgent(portfolio=portfolio, exchange=exchange, stop_loss_pct=0.02)
        agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=10.0)

        # Now the sell only partially fills.
        exchange.create_market_sell_order = lambda symbol, quantity: {
            "symbol": symbol, "quantity": quantity, "filled": 4.0, "average": 99.0,
        }

        result = agent.execute("BTC/USDT", Signal.SELL, price=100.0, quantity=0.0)

        assert result.action == "closed_position"
        assert result.quantity == 4.0
        assert "BTC/USDT" in portfolio.positions  # NOT fully closed
        assert portfolio.positions["BTC/USDT"]["quantity"] == 6.0
        # remainder re-protected with a fresh resting stop
        assert len(exchange.stop_orders) == 2

    def test_sell_full_fill_closes_the_position_as_before(self):
        """Backward compatible: a normal full-fill sell still fully closes
        the position, exactly as before fill resolution existed."""
        portfolio = PortfolioManager(initial_capital=10000.0)
        exchange = DummyExchange()
        agent = ExecutionAgent(portfolio=portfolio, exchange=exchange)
        agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=10.0)

        result = agent.execute("BTC/USDT", Signal.SELL, price=110.0, quantity=0.0)

        assert result.action == "closed_position"
        assert result.quantity == 10.0
        assert "BTC/USDT" not in portfolio.positions


class LimitCapableExchange(DummyExchange):
    def __init__(self, ticker=None):
        super().__init__()
        if ticker is not None:
            self._ticker = ticker
        self.limit_buy_orders = []

    def create_limit_buy_order(self, symbol, quantity, limit_price):
        self.limit_buy_orders.append((symbol, quantity, limit_price))
        return {"id": "limit-1", "symbol": symbol, "quantity": quantity, "filled": quantity, "average": limit_price}


class TestAdaptiveOrderPlacement:
    def test_disabled_by_default_always_uses_market_orders(self):
        portfolio = PortfolioManager(initial_capital=10000.0)
        exchange = LimitCapableExchange(ticker={"bid": 100.0, "ask": 100.05})  # very tight spread
        agent = ExecutionAgent(portfolio=portfolio, exchange=exchange, max_spread_pct=0.01)

        result = agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0)

        assert result.order_type == "market"
        assert exchange.buy_orders == [("BTC/USDT", 1.0)]
        assert exchange.limit_buy_orders == []

    def test_tight_spread_uses_a_marketable_limit_order_when_enabled(self):
        portfolio = PortfolioManager(initial_capital=10000.0)
        exchange = LimitCapableExchange(ticker={"bid": 100.0, "ask": 100.05})  # spread ~0.05% << max 1%
        agent = ExecutionAgent(
            portfolio=portfolio, exchange=exchange, max_spread_pct=0.01, enable_adaptive_order_type=True
        )

        result = agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0)

        assert result.order_type == "limit"
        assert len(exchange.limit_buy_orders) == 1
        assert exchange.buy_orders == []
        assert result.action == "opened_position"

    def test_wide_but_acceptable_spread_still_uses_market_when_enabled(self):
        portfolio = PortfolioManager(initial_capital=10000.0)
        exchange = LimitCapableExchange(ticker={"bid": 100.0, "ask": 100.8})  # spread 0.8%, close to the 1% cap
        agent = ExecutionAgent(
            portfolio=portfolio, exchange=exchange, max_spread_pct=0.01, enable_adaptive_order_type=True
        )

        result = agent.execute("BTC/USDT", Signal.BUY, price=100.4, quantity=1.0)

        assert result.order_type == "market"
        assert exchange.limit_buy_orders == []

    def test_unfilled_limit_order_is_cancelled_not_left_orphaned(self):
        class UnfilledLimitExchange(LimitCapableExchange):
            def create_limit_buy_order(self, symbol, quantity, limit_price):
                self.limit_buy_orders.append((symbol, quantity, limit_price))
                return {"id": "limit-99", "symbol": symbol, "filled": 0.0}

        portfolio = PortfolioManager(initial_capital=10000.0)
        exchange = UnfilledLimitExchange(ticker={"bid": 100.0, "ask": 100.05})
        agent = ExecutionAgent(
            portfolio=portfolio, exchange=exchange, max_spread_pct=0.01, enable_adaptive_order_type=True
        )

        result = agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0)

        assert result.action == "no_action"
        assert exchange.cancelled_orders == [("limit-99", "BTC/USDT")]
        assert "BTC/USDT" not in portfolio.positions


class TestExecutionQualityTelemetry:
    def test_buy_reports_slippage_and_order_type(self):
        portfolio = PortfolioManager(initial_capital=10000.0)
        exchange = PartialFillExchange(filled_quantity=1.0, average_price=101.0)
        agent = ExecutionAgent(portfolio=portfolio, exchange=exchange)

        result = agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0)

        assert result.slippage_pct == pytest.approx(0.01)  # paid 1% more than intended
        assert result.order_type == "market"
        assert result.latency_ms is not None and result.latency_ms >= 0

    def test_sell_reports_slippage(self):
        portfolio = PortfolioManager(initial_capital=10000.0)
        exchange = DummyExchange()
        agent = ExecutionAgent(portfolio=portfolio, exchange=exchange)
        agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=10.0)
        exchange.create_market_sell_order = lambda symbol, quantity: {
            "symbol": symbol, "quantity": quantity, "filled": quantity, "average": 99.0,
        }

        result = agent.execute("BTC/USDT", Signal.SELL, price=100.0, quantity=0.0)

        assert result.slippage_pct == pytest.approx(0.01)  # received 1% less than intended

    def test_no_exchange_reports_no_latency_but_still_reports_order_type(self):
        """No real exchange means nothing to time (latency_ms is None) and
        fill == intended exactly (slippage 0) - but the order TYPE is
        still meaningfully "market", conceptually, whether or not a real
        exchange is behind it."""
        portfolio = PortfolioManager(initial_capital=10000.0)
        agent = ExecutionAgent(portfolio=portfolio, exchange=None)

        result = agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0)

        assert result.latency_ms is None
        assert result.order_type == "market"
        assert result.slippage_pct == 0.0  # no exchange means fill == intended exactly


class MinNotionalExchange(DummyExchange):
    """Reports a real exchange minimum order value via market(), like a
    genuine ccxt exchange object would after load_markets()."""

    def __init__(self, min_notional):
        super().__init__()
        self._min_notional = min_notional

    def market(self, symbol):
        return {"limits": {"cost": {"min": self._min_notional}}}


class TestExchangeMinNotionalDefensiveCheck:
    """Regression coverage: RiskAgent.evaluate_buy already sizes against
    the real exchange minimum, but that lookup can fail or be stale
    between evaluation and execution - this is the final line of defense
    that must catch an under-sized order BEFORE it's ever sent to the real
    exchange, without counting as a genuine execution "error" (it never
    touched the exchange at all)."""

    def test_rejects_an_order_below_the_real_exchange_minimum(self):
        portfolio = PortfolioManager(initial_capital=10000.0)
        exchange = MinNotionalExchange(min_notional=5.0)
        agent = ExecutionAgent(portfolio=portfolio, exchange=exchange)

        # price * quantity = 0.074 * 1.0 = $0.074, well below the $5 minimum
        result = agent.execute("DOGE/USDT", Signal.BUY, price=0.074, quantity=1.0)

        assert result.action == "no_action"  # not "error" - must never touch circuit-breaker budget
        assert "below exchange minimum" in result.error
        assert exchange.buy_orders == []  # the real exchange was never actually called

    def test_allows_an_order_at_or_above_the_real_exchange_minimum(self):
        portfolio = PortfolioManager(initial_capital=10000.0)
        exchange = MinNotionalExchange(min_notional=5.0)
        agent = ExecutionAgent(portfolio=portfolio, exchange=exchange)

        # price * quantity = 0.074 * 100 = $7.40, clears the $5 minimum
        result = agent.execute("DOGE/USDT", Signal.BUY, price=0.074, quantity=100.0)

        assert result.action == "opened_position"
        assert exchange.buy_orders == [("DOGE/USDT", 100.0)]

    def test_missing_market_lookup_does_not_block_the_trade(self):
        # DummyExchange (the base class) has no market() method at all -
        # the lookup failure must be swallowed, not treated as a rejection.
        portfolio = PortfolioManager(initial_capital=10000.0)
        exchange = DummyExchange()
        agent = ExecutionAgent(portfolio=portfolio, exchange=exchange)

        result = agent.execute("BTC/USDT", Signal.BUY, price=100.0, quantity=1.0)

        assert result.action == "opened_position"


class LotSizeExchange(MinNotionalExchange):
    """A real exchange rounds the executed quantity DOWN to a lot-size step
    before filling - mimics that via floor-to-step amount_to_precision,
    like ccxt's Binance implementation does."""

    def __init__(self, min_notional, step):
        super().__init__(min_notional)
        self._step = step

    def amount_to_precision(self, symbol, amount):
        import math

        return math.floor(amount / self._step) * self._step


class TestPrecisionRoundingCanShaveBelowMinNotional:
    """Regression for a real incident: RiskAgent bumps an undersized order up
    to exactly the exchange's minimum notional, but the exchange then rounds
    the quantity DOWN to its lot-size step before filling - for BNB/USDT
    (0.001 lot step, ~$574/BNB) that shaved a $5.00-exact order down to
    $4.59 and Binance's real NOTIONAL filter rejected it, tripping the
    circuit breaker on what was actually a sizing mismatch, not a genuine
    execution failure."""

    def test_bumps_the_quantity_up_when_precision_rounding_would_fall_short(self):
        portfolio = PortfolioManager(initial_capital=10000.0)
        # price=574, min_notional=5.0, step=0.001 - same shape as the real
        # BNB/USDT incident: raw qty for $5.00 is 0.00871..., which floors
        # to 0.008 (=$4.59, short of the $5 minimum).
        exchange = LotSizeExchange(min_notional=5.0, step=0.001)
        agent = ExecutionAgent(portfolio=portfolio, exchange=exchange)

        result = agent.execute("BNB/USDT", Signal.BUY, price=574.0, quantity=5.0 / 574.0)

        assert result.action == "opened_position"
        filled_symbol, filled_quantity = exchange.buy_orders[0]
        assert filled_symbol == "BNB/USDT"
        assert filled_quantity * 574.0 >= 5.0  # the actually-executed notional clears the real minimum
        assert filled_quantity == pytest.approx(0.009)  # smallest step that clears $5 at this price

    def test_refuses_cleanly_when_no_bump_can_clear_the_minimum(self):
        portfolio = PortfolioManager(initial_capital=10000.0)
        # An enormous lot step relative to the minimum - no bump within the
        # tried range can land a step that clears it, so this must refuse
        # cleanly rather than send an order Binance's filter will reject.
        exchange = LotSizeExchange(min_notional=5.0, step=100.0)
        agent = ExecutionAgent(portfolio=portfolio, exchange=exchange)

        result = agent.execute("BNB/USDT", Signal.BUY, price=574.0, quantity=5.0 / 574.0)

        assert result.action == "no_action"
        assert exchange.buy_orders == []

    def test_no_bump_needed_when_precision_rounding_still_clears_the_minimum(self):
        portfolio = PortfolioManager(initial_capital=10000.0)
        exchange = LotSizeExchange(min_notional=5.0, step=0.001)
        agent = ExecutionAgent(portfolio=portfolio, exchange=exchange)

        # Comfortably above the minimum even after floor-rounding to 0.001.
        result = agent.execute("BNB/USDT", Signal.BUY, price=574.0, quantity=1.0)

        assert result.action == "opened_position"
        assert exchange.buy_orders == [("BNB/USDT", 1.0)]

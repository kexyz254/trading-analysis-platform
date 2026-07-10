from trading_assistant.agents.protective_stops import (
    STOP_LIMIT_BUFFER_PCT,
    cancel_protective_stop,
    check_triggered_stop,
    find_open_protective_stop,
    place_protective_stop,
    place_stop_at_price,
    replace_protective_stop,
)


class FakeExchange:
    def __init__(self):
        self.create_order_calls = []
        self.cancel_order_calls = []
        self.edit_order_calls = []
        self._open_orders = {}
        self._orders_by_id = {}
        self._next_id = 1

    def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.create_order_calls.append((symbol, type_, side, amount, price, params or {}))
        order_id = str(self._next_id)
        self._next_id += 1
        order = {"id": order_id, "symbol": symbol, "type": type_, "side": side, "amount": amount, "status": "open"}
        self._open_orders.setdefault(symbol, []).append(order)
        self._orders_by_id[order_id] = order
        return order

    def cancel_order(self, order_id, symbol):
        self.cancel_order_calls.append((order_id, symbol))
        self._open_orders[symbol] = [o for o in self._open_orders.get(symbol, []) if o["id"] != order_id]
        if order_id in self._orders_by_id:
            self._orders_by_id[order_id]["status"] = "canceled"

    def edit_order(self, id, symbol, type_, side, amount, price=None, params=None):
        # Mimics Binance's real atomic cancelReplace: cancel the old order
        # and place the new one as a single exchange-side operation, not
        # two separate client calls.
        self.edit_order_calls.append((id, symbol, type_, side, amount, price, params or {}))
        self.cancel_order(id, symbol)
        return self.create_order(symbol, type_, side, amount, price, params)

    def fetch_open_orders(self, symbol):
        return list(self._open_orders.get(symbol, []))

    def fetch_order(self, order_id, symbol):
        return self._orders_by_id[order_id]

    def _mark_triggered(self, order_id, fill_price):
        order = self._orders_by_id[order_id]
        order["status"] = "closed"
        order["average"] = fill_price
        symbol = order["symbol"]
        self._open_orders[symbol] = [o for o in self._open_orders.get(symbol, []) if o["id"] != order_id]


class RaisingExchange:
    def create_order(self, *args, **kwargs):
        raise ConnectionError("simulated exchange outage")

    def cancel_order(self, *args, **kwargs):
        raise ConnectionError("already gone")

    def edit_order(self, *args, **kwargs):
        raise ConnectionError("simulated exchange rejection")

    def fetch_open_orders(self, *args, **kwargs):
        raise ConnectionError("simulated exchange outage")

    def fetch_order(self, *args, **kwargs):
        raise ConnectionError("simulated exchange outage")


def test_place_protective_stop_uses_correct_order_type_and_buffered_limit_price():
    exchange = FakeExchange()

    order_id = place_protective_stop(exchange, "BTC/USDT", quantity=0.5, entry_price=60000.0, stop_loss_pct=0.02)

    assert order_id == "1"
    assert len(exchange.create_order_calls) == 1
    symbol, type_, side, amount, price, params = exchange.create_order_calls[0]
    assert symbol == "BTC/USDT"
    assert type_ == "STOP_LOSS_LIMIT"
    assert side == "sell"
    assert amount == 0.5
    stop_price = 60000.0 * (1 - 0.02)
    assert params["stopPrice"] == stop_price
    assert price == stop_price * (1 - STOP_LIMIT_BUFFER_PCT)
    assert price < stop_price  # limit leg must sit below the trigger to be marketable


def test_place_protective_stop_returns_none_and_logs_on_exchange_failure():
    result = place_protective_stop(RaisingExchange(), "BTC/USDT", quantity=1.0, entry_price=100.0, stop_loss_pct=0.01)
    assert result is None


def test_place_protective_stop_is_a_noop_with_invalid_inputs():
    exchange = FakeExchange()
    assert place_protective_stop(None, "BTC/USDT", 1.0, 100.0, 0.01) is None
    assert place_protective_stop(exchange, "BTC/USDT", 0.0, 100.0, 0.01) is None
    assert place_protective_stop(exchange, "BTC/USDT", 1.0, 0.0, 0.01) is None
    assert not exchange.create_order_calls


def test_cancel_protective_stop_calls_exchange_and_removes_from_open_orders():
    exchange = FakeExchange()
    order_id = place_protective_stop(exchange, "BTC/USDT", 1.0, 100.0, 0.01)

    cancel_protective_stop(exchange, "BTC/USDT", order_id)

    assert exchange.cancel_order_calls == [(order_id, "BTC/USDT")]
    assert find_open_protective_stop(exchange, "BTC/USDT") is None


def test_cancel_protective_stop_is_a_noop_with_no_order_id():
    exchange = FakeExchange()
    cancel_protective_stop(exchange, "BTC/USDT", None)
    assert not exchange.cancel_order_calls


def test_cancel_protective_stop_swallows_already_gone_errors():
    cancel_protective_stop(RaisingExchange(), "BTC/USDT", "123")  # must not raise


def test_find_open_protective_stop_detects_existing_stop_order():
    exchange = FakeExchange()
    order_id = place_protective_stop(exchange, "BTC/USDT", 1.0, 100.0, 0.01)

    assert find_open_protective_stop(exchange, "BTC/USDT") == order_id


def test_find_open_protective_stop_returns_none_when_no_stop_exists():
    exchange = FakeExchange()
    assert find_open_protective_stop(exchange, "BTC/USDT") is None
    assert find_open_protective_stop(RaisingExchange(), "BTC/USDT") is None
    assert find_open_protective_stop(None, "BTC/USDT") is None


def test_check_triggered_stop_returns_none_while_order_still_open():
    exchange = FakeExchange()
    order_id = place_protective_stop(exchange, "BTC/USDT", 1.0, 100.0, 0.01)

    assert check_triggered_stop(exchange, "BTC/USDT", order_id) is None


def test_check_triggered_stop_returns_fill_details_once_triggered():
    """The core correctness case this whole module exists for: once the
    exchange fills a resting stop, this process must be able to find out -
    otherwise its in-memory portfolio silently desyncs from reality."""
    exchange = FakeExchange()
    order_id = place_protective_stop(exchange, "BTC/USDT", 1.0, 100.0, 0.01)
    exchange._mark_triggered(order_id, fill_price=98.5)

    result = check_triggered_stop(exchange, "BTC/USDT", order_id)

    assert result is not None
    assert result["average"] == 98.5
    assert find_open_protective_stop(exchange, "BTC/USDT") is None  # no longer resting


def test_check_triggered_stop_is_a_noop_with_no_order_id():
    exchange = FakeExchange()
    assert check_triggered_stop(exchange, "BTC/USDT", None) is None
    assert check_triggered_stop(None, "BTC/USDT", "1") is None


def test_place_stop_at_price_uses_the_explicit_price_directly():
    exchange = FakeExchange()

    order_id = place_stop_at_price(exchange, "BTC/USDT", quantity=0.5, stop_price=58000.0)

    assert order_id == "1"
    symbol, type_, side, amount, price, params = exchange.create_order_calls[0]
    assert params["stopPrice"] == 58000.0
    assert price == 58000.0 * (1 - STOP_LIMIT_BUFFER_PCT)


def test_place_stop_at_price_is_a_noop_with_invalid_inputs():
    exchange = FakeExchange()
    assert place_stop_at_price(None, "BTC/USDT", 1.0, 100.0) is None
    assert place_stop_at_price(exchange, "BTC/USDT", 0.0, 100.0) is None
    assert place_stop_at_price(exchange, "BTC/USDT", 1.0, 0.0) is None
    assert not exchange.create_order_calls


def test_place_protective_stop_delegates_to_place_stop_at_price_with_computed_price():
    """Regression: place_protective_stop was refactored to build on
    place_stop_at_price so both share one order-placement code path -
    verify the computed stop price still matches the original formula."""
    exchange = FakeExchange()

    order_id = place_protective_stop(exchange, "BTC/USDT", quantity=1.0, entry_price=200.0, stop_loss_pct=0.1)

    assert order_id == "1"
    _, _, _, _, _, params = exchange.create_order_calls[0]
    assert params["stopPrice"] == 180.0


def test_replace_protective_stop_uses_the_exchanges_atomic_cancel_replace():
    exchange = FakeExchange()
    old_order_id = place_stop_at_price(exchange, "BTC/USDT", quantity=1.0, stop_price=95.0)

    new_order_id = replace_protective_stop(exchange, "BTC/USDT", old_order_id, quantity=1.0, new_stop_price=98.0)

    assert new_order_id is not None
    assert new_order_id != old_order_id
    assert len(exchange.edit_order_calls) == 1
    edited_id, symbol, type_, side, amount, price, params = exchange.edit_order_calls[0]
    assert edited_id == old_order_id
    assert symbol == "BTC/USDT"
    assert type_ == "STOP_LOSS_LIMIT"
    assert side == "sell"
    assert amount == 1.0
    assert params["stopPrice"] == 98.0
    assert params["cancelReplaceMode"] == "STOP_ON_FAILURE"
    assert price == 98.0 * (1 - STOP_LIMIT_BUFFER_PCT)
    # replace_protective_stop itself calls exchange.edit_order exactly once -
    # it never calls create_order/cancel_order directly (FakeExchange's own
    # edit_order internally simulates the exchange's atomic behavior, which
    # is a separate concern from what replace_protective_stop calls).
    assert find_open_protective_stop(exchange, "BTC/USDT") == new_order_id


def test_replace_protective_stop_falls_back_to_a_plain_placement_with_no_prior_order():
    exchange = FakeExchange()

    order_id = replace_protective_stop(exchange, "BTC/USDT", None, quantity=1.0, new_stop_price=95.0)

    assert order_id == "1"
    assert exchange.edit_order_calls == []  # went through place_stop_at_price instead
    assert len(exchange.create_order_calls) == 1


def test_replace_protective_stop_returns_none_and_does_not_raise_on_exchange_rejection():
    """The core safety property: a rejected replacement must not crash the
    caller, and (per STOP_ON_FAILURE) the exchange never touches the old
    order in this case - nothing here pretends to know the old order's
    fate beyond that contract, callers are responsible for treating it as
    still valid."""
    result = replace_protective_stop(RaisingExchange(), "BTC/USDT", "1", quantity=1.0, new_stop_price=95.0)

    assert result is None


def test_replace_protective_stop_is_a_noop_with_invalid_inputs():
    exchange = FakeExchange()
    assert replace_protective_stop(None, "BTC/USDT", "1", 1.0, 100.0) is None
    assert replace_protective_stop(exchange, "BTC/USDT", "1", 0.0, 100.0) is None
    assert replace_protective_stop(exchange, "BTC/USDT", "1", 1.0, 0.0) is None
    assert not exchange.edit_order_calls
    assert not exchange.create_order_calls

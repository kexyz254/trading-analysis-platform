import logging

import pytest

from trading_assistant.agents.portfolio import PortfolioAgent
from trading_assistant.persistence.journal import Journal
from trading_assistant.portfolio.manager import PortfolioManager


def test_mark_to_market_appends_unrealized_pnl_to_equity_curve():
    """Regression test: PortfolioManager.equity_curve previously only updated
    on close_position, so unrealized PnL on an open position never showed up."""
    portfolio = PortfolioManager(initial_capital=10000.0)
    portfolio.open_position("BTC/USDT", price=100.0, quantity=10.0)
    agent = PortfolioAgent(portfolio=portfolio)

    assert len(portfolio.equity_curve) == 1  # only the initial capital so far

    total_value = agent.mark_to_market({"BTC/USDT": 110.0})

    assert total_value == pytest.approx(10000.0 - 1000.0 + 1100.0)
    assert len(portfolio.equity_curve) == 2
    assert portfolio.equity_curve[-1] == pytest.approx(total_value)


def test_positions_frame_reflects_current_prices():
    portfolio = PortfolioManager(initial_capital=10000.0)
    portfolio.open_position("BTC/USDT", price=100.0, quantity=2.0)
    agent = PortfolioAgent(portfolio=portfolio)

    frame = agent.positions_frame({"BTC/USDT": 120.0})

    assert list(frame["symbol"]) == ["BTC/USDT"]
    assert frame.iloc[0]["price"] == 120.0
    assert frame.iloc[0]["quantity"] == 2.0


def test_exposure_summary_none_when_no_positions():
    portfolio = PortfolioManager(initial_capital=10000.0)
    agent = PortfolioAgent(portfolio=portfolio)

    assert agent.exposure_summary({}) is None


def test_record_trade_writes_to_journal(tmp_path):
    portfolio = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    agent = PortfolioAgent(portfolio=portfolio, journal=journal)

    agent.record_trade("BTC/USDT", "buy", price=100.0, quantity=1.0)

    trades = journal.recent_trades()
    assert len(trades) == 1
    assert trades[0]["symbol"] == "BTC/USDT"


def test_capital_property_reflects_underlying_portfolio():
    portfolio = PortfolioManager(initial_capital=5000.0)
    agent = PortfolioAgent(portfolio=portfolio)

    assert agent.capital == 5000.0


def test_mark_to_market_persists_equity_snapshot_to_journal(tmp_path):
    """Regression: equity_curve previously only lived in-memory, so a
    dashboard reading the journal after a process restart saw nothing real."""
    portfolio = PortfolioManager(initial_capital=10000.0)
    portfolio.open_position("BTC/USDT", price=100.0, quantity=10.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    agent = PortfolioAgent(portfolio=portfolio, journal=journal)

    total_value = agent.mark_to_market({"BTC/USDT": 110.0})

    snapshot = journal.latest_equity_snapshot()
    assert snapshot is not None
    assert snapshot["total_equity"] == pytest.approx(total_value)
    assert snapshot["capital"] == pytest.approx(portfolio.capital)
    assert snapshot["positions"]["BTC/USDT"]["quantity"] == 10.0
    assert snapshot["positions"]["BTC/USDT"]["price"] == 110.0


def test_mark_to_market_surfaces_unexplained_quantity_in_journaled_positions(tmp_path):
    """The dashboard needs this to flag balances the strategy never
    actually traded into (e.g. testnet faucet grants) instead of silently
    presenting them as strategy positions - see reconcile_with_exchange."""
    portfolio = PortfolioManager(initial_capital=10000.0)
    portfolio.positions["WBTC/USDT"] = {
        "price": 60000.0, "quantity": 1.0, "cost": 60000.0, "unexplained_quantity": 1.0,
        "stop_loss": None, "take_profit": None,
    }
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    agent = PortfolioAgent(portfolio=portfolio, journal=journal)

    agent.mark_to_market({"WBTC/USDT": 61000.0})

    snapshot = journal.latest_equity_snapshot()
    assert snapshot["positions"]["WBTC/USDT"]["unexplained_quantity"] == 1.0


def test_mark_to_market_without_journal_does_not_raise():
    portfolio = PortfolioManager(initial_capital=10000.0)
    agent = PortfolioAgent(portfolio=portfolio)

    agent.mark_to_market({})  # no journal configured - must be a no-op, not an error


class FakeReconcileExchange:
    def __init__(self, balances, tickers=None, existing_open_orders=None, balances_after_failure=None, fail_create_order=False):
        self._balances = balances
        self._tickers = tickers or {}
        self._open_orders = existing_open_orders or {}
        self.create_order_calls = []
        self._next_id = 100
        self._fail_create_order = fail_create_order
        # Simulates the exact live failure found: a testnet balance that
        # changes BETWEEN the initial reconciliation snapshot and a later
        # re-verification call within the same pass.
        self._balances_after_failure = balances_after_failure
        self._fetch_balance_calls = 0

    def fetch_balance(self):
        self._fetch_balance_calls += 1
        if self._fetch_balance_calls > 1 and self._balances_after_failure is not None:
            return self._balances_after_failure
        return self._balances

    def fetch_ticker(self, symbol):
        return {"last": self._tickers[symbol]}

    def fetch_open_orders(self, symbol):
        return list(self._open_orders.get(symbol, []))

    def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.create_order_calls.append((symbol, type_, side, amount, price, params or {}))
        if self._fail_create_order:
            raise Exception("simulated InsufficientFunds: balance vanished")
        order_id = str(self._next_id)
        self._next_id += 1
        return {"id": order_id, "symbol": symbol, "type": type_}


def test_find_untracked_assets_flags_balances_outside_the_tracked_universe():
    """Regression test for a real account audit: Binance testnet's faucet
    seeds a small amount of essentially every listed asset (400+ found),
    while the system's own discovered universe covers only ~20-30 - this
    must be detected, not silently invisible."""
    balance = {"total": {"BTC": 1.0, "XRP": 2267.0, "SHIB": 18446.0, "PEPE": 18446.0, "DUST": 0.0}}

    untracked = PortfolioAgent._find_untracked_assets(balance, tracked_symbols=["BTC/USDT", "XRP/USDT"])

    assert untracked == ["PEPE", "SHIB"]  # sorted; BTC/XRP are tracked, DUST is zero


def test_find_untracked_assets_empty_when_everything_is_tracked():
    balance = {"total": {"BTC": 1.0, "XRP": 2267.0}}

    untracked = PortfolioAgent._find_untracked_assets(balance, tracked_symbols=["BTC/USDT", "XRP/USDT"])

    assert untracked == []


def test_find_untracked_assets_handles_missing_total_key_gracefully():
    assert PortfolioAgent._find_untracked_assets({}, tracked_symbols=["BTC/USDT"]) == []


def test_reconcile_logs_a_warning_for_untracked_balances(tmp_path, caplog):
    portfolio = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    agent = PortfolioAgent(portfolio=portfolio, journal=journal)
    exchange = FakeReconcileExchange(
        balances={"total": {"XRP": 1000.0, "SHIB": 18446.0, "PEPE": 18446.0}}
    )

    with caplog.at_level(logging.WARNING):
        agent.reconcile_with_exchange(exchange, ["XRP/USDT"])

    warnings = [r for r in caplog.records if "OUTSIDE the tracked trading universe" in r.message]
    assert len(warnings) == 1
    assert warnings[0].context["untracked_asset_count"] == 2  # SHIB, PEPE


def test_reconcile_does_not_log_when_nothing_is_untracked(tmp_path, caplog):
    portfolio = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    agent = PortfolioAgent(portfolio=portfolio, journal=journal)
    exchange = FakeReconcileExchange(balances={"total": {"XRP": 1000.0}})

    with caplog.at_level(logging.WARNING):
        agent.reconcile_with_exchange(exchange, ["XRP/USDT"])

    warnings = [r for r in caplog.records if "OUTSIDE the tracked trading universe" in r.message]
    assert warnings == []


def test_reconcile_matches_real_balance_to_journal_trade_history(tmp_path):
    """Regression: PortfolioManager's positions live only in process memory
    with no persistence across restarts - a real testnet account was found
    holding 2267 XRP while the in-memory portfolio, freshly restarted, only
    tracked the most recent 933 (the earlier buy's position record was lost
    entirely on restart). Reconciliation must rebuild the position AND
    correctly deduct its cost from starting capital."""
    portfolio = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    journal.record_trade(symbol="XRP/USDT", side="buy", price=1.0, quantity=600.0)
    journal.record_trade(symbol="XRP/USDT", side="buy", price=2.0, quantity=400.0)
    # weighted avg cost = (600*1.0 + 400*2.0) / 1000 = 1.4, total cost = 1400

    agent = PortfolioAgent(portfolio=portfolio, journal=journal)
    exchange = FakeReconcileExchange(balances={"XRP": {"total": 1000.0}})

    agent.reconcile_with_exchange(exchange, ["XRP/USDT"])

    assert "XRP/USDT" in portfolio.positions
    assert portfolio.positions["XRP/USDT"]["quantity"] == 1000.0
    assert portfolio.positions["XRP/USDT"]["price"] == pytest.approx(1.4)
    assert portfolio.capital == pytest.approx(10000.0 - 1400.0)
    assert portfolio.positions["XRP/USDT"]["unexplained_quantity"] == 0.0


def test_reconcile_flags_and_prices_untracked_exchange_balance(tmp_path):
    """The journal only explains part of the real balance (e.g. a testnet
    faucet grant or a manual/out-of-band trade never journaled) - the
    unexplained portion must be priced at the current market rate for PnL
    purposes (not silently ignored or assumed free from a $0 basis), but
    since it was never actually paid for out of this system's capital,
    capital must only be reduced by the EXPLAINED portion - regression
    test for a real reconciliation that drove capital to -$90,253 by
    deducting the current market value of 19 unexplained/faucet balances."""
    portfolio = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    journal.record_trade(symbol="XRP/USDT", side="buy", price=1.0, quantity=600.0)
    # real balance is 1000, journal only explains 600 -> 400 unexplained

    agent = PortfolioAgent(portfolio=portfolio, journal=journal)
    exchange = FakeReconcileExchange(
        balances={"XRP": {"total": 1000.0}}, tickers={"XRP/USDT": 2.0}
    )

    agent.reconcile_with_exchange(exchange, ["XRP/USDT"])

    position = portfolio.positions["XRP/USDT"]
    assert position["quantity"] == 1000.0
    pnl_cost_basis = 600.0 * 1.0 + 400.0 * 2.0  # explained portion at journal price, rest at current market price
    assert position["price"] == pytest.approx(pnl_cost_basis / 1000.0)
    explained_cost_only = 600.0 * 1.0  # capital must NOT be charged for the unexplained 400
    assert portfolio.capital == pytest.approx(10000.0 - explained_cost_only)
    assert position["unexplained_quantity"] == pytest.approx(400.0)


def test_reconcile_unexplained_balance_is_excluded_from_live_equity(tmp_path):
    """The actual accounting bug this guards against: a fully- or
    partially-unexplained balance (e.g. a testnet faucet grant) must not
    inflate live 'equity' with its full current market value just because
    reconciliation tracks its quantity for exposure/double-buy-prevention
    purposes. Regression for a real account where a single unexplained
    WBTC balance (~$61k) alternated in and out of a ~$100k vs ~$37k
    reported equity swing."""
    portfolio = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    journal.record_trade(symbol="XRP/USDT", side="buy", price=1.0, quantity=600.0)
    agent = PortfolioAgent(portfolio=portfolio, journal=journal)
    exchange = FakeReconcileExchange(
        balances={"XRP": {"total": 1000.0}}, tickers={"XRP/USDT": 2.0}
    )

    agent.reconcile_with_exchange(exchange, ["XRP/USDT"])
    total_value = portfolio.get_total_value({"XRP/USDT": 2.0})

    # only the explained 600 XRP (bought at 1.0, now worth 2.0 each) counts
    # as tracked equity - the unexplained 400 XRP's $800 current value must
    # NOT appear here, even though it's real, held, exchange balance.
    explained_cost_only = 600.0 * 1.0
    expected_capital = 10000.0 - explained_cost_only
    expected_tracked_position_value = 600.0 * 2.0
    assert total_value == pytest.approx(expected_capital + expected_tracked_position_value)


def test_reconcile_skips_symbols_with_no_real_balance(tmp_path):
    portfolio = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    agent = PortfolioAgent(portfolio=portfolio, journal=journal)
    exchange = FakeReconcileExchange(balances={})

    agent.reconcile_with_exchange(exchange, ["BTC/USDT", "ETH/USDT"])

    assert portfolio.positions == {}
    assert portfolio.capital == 10000.0


def test_reconcile_backfills_a_missing_protective_stop(tmp_path):
    """A position surviving a restart with no resting stop order (e.g. one
    opened before this feature existed) would otherwise sit completely
    unprotected indefinitely - reconciliation must backfill one."""
    portfolio = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    journal.record_trade(symbol="XRP/USDT", side="buy", price=1.0, quantity=1000.0)
    agent = PortfolioAgent(portfolio=portfolio, journal=journal)
    exchange = FakeReconcileExchange(balances={"XRP": {"total": 1000.0}})

    agent.reconcile_with_exchange(exchange, ["XRP/USDT"], stop_loss_pct=0.02)

    assert len(exchange.create_order_calls) == 1
    symbol, type_, side, amount, price, params = exchange.create_order_calls[0]
    assert symbol == "XRP/USDT" and type_ == "STOP_LOSS_LIMIT" and side == "sell" and amount == 1000.0
    assert params["stopPrice"] == pytest.approx(0.98)  # 1.0 * (1 - 0.02)
    assert portfolio.positions["XRP/USDT"]["stop_order_id"] == "100"


def test_reconcile_returns_empty_list_when_backfill_succeeds(tmp_path):
    portfolio = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    journal.record_trade(symbol="XRP/USDT", side="buy", price=1.0, quantity=1000.0)
    agent = PortfolioAgent(portfolio=portfolio, journal=journal)
    exchange = FakeReconcileExchange(balances={"XRP": {"total": 1000.0}})

    unprotected = agent.reconcile_with_exchange(exchange, ["XRP/USDT"], stop_loss_pct=0.02)

    assert unprotected == []


def test_reconcile_reports_symbol_when_backfill_fails_and_position_survives(tmp_path):
    # Regression test: this is the signal the caller (Orchestrator) uses to
    # raise a real "unprotected position" alert - a bare log line used to
    # be the only trace of this real capital-at-risk event.
    portfolio = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    journal.record_trade(symbol="LINK/USDT", side="buy", price=7.5, quantity=58.0)
    agent = PortfolioAgent(portfolio=portfolio, journal=journal)
    exchange = FakeReconcileExchange(
        balances={"LINK": {"total": 58.0}},
        balances_after_failure={"LINK": {"total": 30.0}},  # still held, just reduced
        fail_create_order=True,
    )

    unprotected = agent.reconcile_with_exchange(exchange, ["LINK/USDT"], stop_loss_pct=0.02)

    assert unprotected == ["LINK/USDT"]


def test_reconcile_does_not_report_a_symbol_removed_after_balance_vanished(tmp_path):
    # If the position was removed entirely (balance genuinely gone), it's
    # not "unprotected" - it no longer exists, so it must not be reported.
    portfolio = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    journal.record_trade(symbol="LINK/USDT", side="buy", price=7.5, quantity=58.0)
    agent = PortfolioAgent(portfolio=portfolio, journal=journal)
    exchange = FakeReconcileExchange(
        balances={"LINK": {"total": 58.0}},
        balances_after_failure={"LINK": {"total": 0.0}},
        fail_create_order=True,
    )

    unprotected = agent.reconcile_with_exchange(exchange, ["LINK/USDT"], stop_loss_pct=0.02)

    assert unprotected == []
    assert "LINK/USDT" not in portfolio.positions


def test_reconcile_returns_empty_list_when_stop_loss_pct_not_configured(tmp_path):
    portfolio = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    journal.record_trade(symbol="XRP/USDT", side="buy", price=1.0, quantity=1000.0)
    agent = PortfolioAgent(portfolio=portfolio, journal=journal)
    exchange = FakeReconcileExchange(balances={"XRP": {"total": 1000.0}})

    unprotected = agent.reconcile_with_exchange(exchange, ["XRP/USDT"])  # no stop_loss_pct

    assert unprotected == []


def test_reconcile_does_not_duplicate_an_existing_protective_stop(tmp_path):
    portfolio = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    journal.record_trade(symbol="XRP/USDT", side="buy", price=1.0, quantity=1000.0)
    agent = PortfolioAgent(portfolio=portfolio, journal=journal)
    exchange = FakeReconcileExchange(
        balances={"XRP": {"total": 1000.0}},
        existing_open_orders={"XRP/USDT": [{"id": "already-there", "type": "STOP_LOSS_LIMIT"}]},
    )

    agent.reconcile_with_exchange(exchange, ["XRP/USDT"], stop_loss_pct=0.02)

    assert exchange.create_order_calls == []  # no duplicate stop placed
    assert portfolio.positions["XRP/USDT"]["stop_order_id"] == "already-there"


def test_reconcile_without_stop_loss_pct_does_not_touch_stop_orders(tmp_path):
    """Backward compatible: existing callers that never opt in keep the
    original behavior rather than unexpectedly placing real orders."""
    portfolio = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    journal.record_trade(symbol="XRP/USDT", side="buy", price=1.0, quantity=1000.0)
    agent = PortfolioAgent(portfolio=portfolio, journal=journal)
    exchange = FakeReconcileExchange(balances={"XRP": {"total": 1000.0}})

    agent.reconcile_with_exchange(exchange, ["XRP/USDT"])  # stop_loss_pct defaults to None

    assert exchange.create_order_calls == []
    assert "stop_order_id" not in portfolio.positions["XRP/USDT"]


def test_reconcile_removes_stale_position_when_balance_vanishes_before_stop_placement(tmp_path):
    """Regression test for a real live failure: a testnet balance can
    change BETWEEN the reconciliation snapshot (fetch_balance at the top of
    the pass) and the protective-stop placement moments later in the same
    pass - found via a real LINK/USDT reconciliation that recorded
    real_qty=58 right before the exchange reported the balance as fully
    gone, leaving a stale, wrong position record with no protection and no
    correction. A failed stop placement must trigger an immediate
    re-verification, not just a logged error."""
    portfolio = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    journal.record_trade(symbol="LINK/USDT", side="buy", price=7.5, quantity=58.0)
    agent = PortfolioAgent(portfolio=portfolio, journal=journal)
    exchange = FakeReconcileExchange(
        balances={"LINK": {"total": 58.0}},
        balances_after_failure={"LINK": {"total": 0.0}},  # gone by the time we re-check
        fail_create_order=True,
    )

    agent.reconcile_with_exchange(exchange, ["LINK/USDT"], stop_loss_pct=0.02)

    assert "LINK/USDT" not in portfolio.positions  # stale record removed, not left dangling


def test_reconcile_flags_but_keeps_position_when_balance_merely_changed(tmp_path):
    """If the re-verified balance is nonzero (just different from the
    snapshot) the position record is kept - flagged as possibly stale via
    the warning log, but a real, still-held position must not be silently
    dropped from tracking."""
    portfolio = PortfolioManager(initial_capital=10000.0)
    journal = Journal(db_path=str(tmp_path / "journal.db"))
    journal.record_trade(symbol="LINK/USDT", side="buy", price=7.5, quantity=58.0)
    agent = PortfolioAgent(portfolio=portfolio, journal=journal)
    exchange = FakeReconcileExchange(
        balances={"LINK": {"total": 58.0}},
        balances_after_failure={"LINK": {"total": 30.0}},  # reduced, not gone
        fail_create_order=True,
    )

    agent.reconcile_with_exchange(exchange, ["LINK/USDT"], stop_loss_pct=0.02)

    assert "LINK/USDT" in portfolio.positions  # kept, even though now known to be stale


def test_reconcile_handles_exchange_failure_gracefully(tmp_path):
    class BrokenExchange:
        def fetch_balance(self):
            raise ConnectionError("simulated outage")

    portfolio = PortfolioManager(initial_capital=10000.0)
    agent = PortfolioAgent(portfolio=portfolio, journal=Journal(db_path=str(tmp_path / "journal.db")))

    agent.reconcile_with_exchange(BrokenExchange(), ["BTC/USDT"])  # must not raise

    assert portfolio.positions == {}
    assert portfolio.capital == 10000.0


class FakeCapitalExchange:
    def __init__(self, balances):
        self._balances = balances

    def fetch_balance(self):
        return self._balances


class TestSyncCapitalWithExchange:
    """Regression coverage for a real gap found going live: capital was
    ALWAYS just config's initial_capital, with nothing ever syncing it to
    the real account's actual balance - a fresh live account with $0 would
    still report the full configured capital and size real orders against
    it, all doomed to be rejected for insufficient balance."""

    def test_sets_capital_to_the_real_free_quote_currency_balance(self, tmp_path):
        portfolio = PortfolioManager(initial_capital=1000.0)
        agent = PortfolioAgent(portfolio=portfolio, journal=Journal(db_path=str(tmp_path / "journal.db")))
        exchange = FakeCapitalExchange({"USDT": {"free": 42.5, "total": 50.0}})

        agent.sync_capital_with_exchange(exchange, "USDT")

        assert portfolio.capital == 42.5  # free, not total - total includes locked-in-open-orders funds

    def test_zero_real_balance_correctly_zeroes_out_fictional_capital(self, tmp_path):
        # The exact scenario that motivated this fix: a live account with
        # no funds yet must show $0 capital, not the configured constant.
        portfolio = PortfolioManager(initial_capital=1000.0)
        agent = PortfolioAgent(portfolio=portfolio, journal=Journal(db_path=str(tmp_path / "journal.db")))
        exchange = FakeCapitalExchange({"USDT": {"free": 0.0, "total": 0.0}})

        agent.sync_capital_with_exchange(exchange, "USDT")

        assert portfolio.capital == 0.0

    def test_missing_quote_currency_in_balance_treated_as_zero(self, tmp_path):
        portfolio = PortfolioManager(initial_capital=1000.0)
        agent = PortfolioAgent(portfolio=portfolio, journal=Journal(db_path=str(tmp_path / "journal.db")))
        exchange = FakeCapitalExchange({})  # no USDT key at all

        agent.sync_capital_with_exchange(exchange, "USDT")

        assert portfolio.capital == 0.0

    def test_exchange_failure_leaves_capital_unchanged(self, tmp_path):
        class BrokenExchange:
            def fetch_balance(self):
                raise ConnectionError("simulated outage")

        portfolio = PortfolioManager(initial_capital=1000.0)
        agent = PortfolioAgent(portfolio=portfolio, journal=Journal(db_path=str(tmp_path / "journal.db")))

        agent.sync_capital_with_exchange(BrokenExchange(), "USDT")  # must not raise

        assert portfolio.capital == 1000.0  # unchanged, not silently zeroed by a failed fetch

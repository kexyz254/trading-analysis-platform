import pandas as pd

from trading_assistant.agents.champion_challenger import ChampionChallengerAgent
from trading_assistant.agents.features import build_features
from trading_assistant.agents.strategy_manager import StrategyCandidate, StrategyManager
from trading_assistant.portfolio.manager import PortfolioManager
from trading_assistant.strategies.base import Signal


def _uptrend_ohlcv(n=300, start=100.0, step=0.5):
    closes = [start + i * step for i in range(n)]
    return pd.DataFrame(
        {
            "timestamp": pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC"),
            "open": closes,
            "high": [c + 1 for c in closes],
            "low": [c - 1 for c in closes],
            "close": closes,
            "volume": [100] * n,
        }
    )


class AlwaysBuyStrategy:
    def generate_signal(self, data):
        return Signal.BUY


class AlwaysSellStrategy:
    def generate_signal(self, data):
        return Signal.SELL


class AlwaysHoldStrategy:
    def generate_signal(self, data):
        return Signal.HOLD


def _manager_with_fake_candidates():
    manager = StrategyManager(
        candidates=[
            StrategyCandidate("buyer", "rule", AlwaysBuyStrategy),
            StrategyCandidate("seller", "rule", AlwaysSellStrategy),
            StrategyCandidate("holder", "rule", AlwaysHoldStrategy),
        ],
        train_window_bars=60,
        test_window_bars=20,
        min_folds_required=2,
    )
    return manager


def test_process_cycle_skips_the_champion_and_paper_trades_everyone_else():
    manager = _manager_with_fake_candidates()
    ohlcv = _uptrend_ohlcv(n=300)
    manager.run_tournament("BTC/USDT", ohlcv)  # populates live_instances

    agent = ChampionChallengerAgent(strategy_manager=manager)
    features = build_features(ohlcv)

    # force a champion designation directly (bypassing the promotion bar)
    # so the test isolates process_cycle's skip logic, not _select_winners
    from trading_assistant.agents.strategy_manager import RegimeSelection

    manager.last_tournament["BTC/USDT"].winners["trending_up"] = RegimeSelection(
        regime="trending_up", winner="buyer", reason="forced for test"
    )

    agent.process_cycle("BTC/USDT", "trending_up", features, current_price=200.0)

    portfolios = agent._portfolios["BTC/USDT"]
    assert "buyer" not in portfolios  # champion - never paper-traded
    assert "seller" in portfolios
    assert "holder" in portfolios


def test_exclude_champion_false_paper_trades_the_champion_too():
    """When the tournament is observation-only (drive_live_trading=False),
    nothing else trades the would-be champion for real, so exclude_champion=
    False must include it in paper trading too - otherwise its bankroll
    would be the one candidate missing from the leaderboard."""
    manager = _manager_with_fake_candidates()
    ohlcv = _uptrend_ohlcv(n=300)
    manager.run_tournament("BTC/USDT", ohlcv)

    from trading_assistant.agents.strategy_manager import RegimeSelection

    manager.last_tournament["BTC/USDT"].winners["trending_up"] = RegimeSelection(
        regime="trending_up", winner="buyer", reason="forced for test"
    )

    agent = ChampionChallengerAgent(strategy_manager=manager)
    features = build_features(ohlcv)

    agent.process_cycle("BTC/USDT", "trending_up", features, current_price=200.0, exclude_champion=False)

    portfolios = agent._portfolios["BTC/USDT"]
    assert "buyer" in portfolios  # champion included this time
    assert "seller" in portfolios
    assert "holder" in portfolios


def test_process_cycle_blocks_new_positions_while_sitting_out():
    """Regression: paper challengers used to keep trading through
    panic/illiquid bars the real champion sits out - accumulating P&L
    from exactly the risk the real system is designed to avoid, biasing
    the champion-vs-challenger comparison."""
    manager = _manager_with_fake_candidates()
    ohlcv = _uptrend_ohlcv(n=300)
    manager.run_tournament("BTC/USDT", ohlcv)
    agent = ChampionChallengerAgent(strategy_manager=manager)
    features = build_features(ohlcv)

    agent.process_cycle("BTC/USDT", "trending_up", features, current_price=200.0, sitting_out=True, exclude_champion=False)

    portfolios = agent._portfolios["BTC/USDT"]
    assert "BTC/USDT" not in portfolios["buyer"].positions  # BUY signal, but blocked by sitting_out


def test_process_cycle_still_allows_closing_an_existing_position_while_sitting_out():
    """A challenger cutting a losing position during a panic bar isn't the
    fairness problem - accumulating NEW risk during exactly the conditions
    the real system avoids is. Existing positions must still be closeable."""
    manager = _manager_with_fake_candidates()
    ohlcv = _uptrend_ohlcv(n=300)
    manager.run_tournament("BTC/USDT", ohlcv)
    agent = ChampionChallengerAgent(strategy_manager=manager)
    features = build_features(ohlcv)

    # open a position normally first (not sitting out)
    agent.process_cycle("BTC/USDT", "trending_up", features, current_price=200.0, exclude_champion=False)
    assert "BTC/USDT" in agent._portfolios["BTC/USDT"]["buyer"].positions

    # now force the "seller" candidate's portfolio into a held position, then
    # confirm a SELL signal still closes it even while sitting_out=True
    seller_portfolio = agent._get_or_create_portfolio("BTC/USDT", "seller")
    seller_portfolio.open_position("BTC/USDT", 200.0, 1.0)
    agent.process_cycle("BTC/USDT", "trending_up", features, current_price=210.0, sitting_out=True)

    assert "BTC/USDT" not in seller_portfolio.positions  # SELL signal still closed it


def test_process_cycle_journals_sit_out_action_for_a_blocked_buy(tmp_path):
    from trading_assistant.persistence.journal import Journal

    journal = Journal(db_path=str(tmp_path / "journal.db"))
    manager = _manager_with_fake_candidates()
    ohlcv = _uptrend_ohlcv(n=300)
    manager.run_tournament("BTC/USDT", ohlcv)
    agent = ChampionChallengerAgent(strategy_manager=manager, journal=journal)
    features = build_features(ohlcv)

    agent.process_cycle("BTC/USDT", "trending_up", features, current_price=200.0, sitting_out=True, exclude_champion=False)

    decisions = journal.recent_decisions(symbol="BTC/USDT")
    buyer_decisions = [d for d in decisions if d["details"].get("candidate") == "buyer"]
    assert buyer_decisions[-1]["action"] == "sit_out"


def test_process_cycle_default_sitting_out_false_is_backward_compatible():
    manager = _manager_with_fake_candidates()
    ohlcv = _uptrend_ohlcv(n=300)
    manager.run_tournament("BTC/USDT", ohlcv)
    agent = ChampionChallengerAgent(strategy_manager=manager)
    features = build_features(ohlcv)

    agent.process_cycle("BTC/USDT", "trending_up", features, current_price=200.0, exclude_champion=False)  # no sitting_out kwarg

    assert "BTC/USDT" in agent._portfolios["BTC/USDT"]["buyer"].positions


def test_process_cycle_persists_paper_equity_snapshot_to_journal(tmp_path):
    from trading_assistant.persistence.journal import Journal

    manager = _manager_with_fake_candidates()
    ohlcv = _uptrend_ohlcv(n=300)
    manager.run_tournament("BTC/USDT", ohlcv)

    from trading_assistant.agents.strategy_manager import RegimeSelection

    manager.last_tournament["BTC/USDT"].winners["trending_up"] = RegimeSelection(
        regime="trending_up", winner="holder", reason="forced for test"
    )

    journal = Journal(db_path=str(tmp_path / "journal.db"))
    agent = ChampionChallengerAgent(strategy_manager=manager, journal=journal)
    features = build_features(ohlcv)

    agent.process_cycle("BTC/USDT", "trending_up", features, current_price=200.0)

    snapshots = journal.recent_paper_equity_snapshots(symbol="BTC/USDT")
    assert {s["candidate"] for s in snapshots} == {"buyer", "seller"}  # holder is champion, excluded by default
    leaderboard = journal.latest_paper_equity_by_candidate("BTC/USDT")
    assert leaderboard["buyer"]["total_equity"] > 0


def test_buyer_challenger_opens_a_paper_position_seller_and_holder_do_not():
    manager = _manager_with_fake_candidates()
    ohlcv = _uptrend_ohlcv(n=300)
    manager.run_tournament("BTC/USDT", ohlcv)

    from trading_assistant.agents.strategy_manager import RegimeSelection

    # champion is "holder" (HOLD-only) so buyer/seller are challengers this cycle
    manager.last_tournament["BTC/USDT"].winners["trending_up"] = RegimeSelection(
        regime="trending_up", winner="holder", reason="forced for test"
    )

    agent = ChampionChallengerAgent(strategy_manager=manager)
    features = build_features(ohlcv)

    agent.process_cycle("BTC/USDT", "trending_up", features, current_price=200.0)

    buyer_portfolio = agent._portfolios["BTC/USDT"]["buyer"]
    seller_portfolio = agent._portfolios["BTC/USDT"]["seller"]

    assert "BTC/USDT" in buyer_portfolio.positions  # AlwaysBuyStrategy opened a paper long
    assert "BTC/USDT" not in seller_portfolio.positions  # nothing to sell - never held anything


def test_paper_trade_uses_risk_based_sizing_when_configured():
    """Parity fix: a challenger's paper track record must reflect the same
    risk_per_trade/stop-distance sizing live trading uses, not the old flat
    ~95%-of-capital assumption - otherwise "keeping pace with the champion"
    describes a risk level nothing here would ever really be traded at."""
    manager = _manager_with_fake_candidates()
    manager.run_tournament("BTC/USDT", _uptrend_ohlcv(n=300))

    agent = ChampionChallengerAgent(
        strategy_manager=manager,
        paper_capital=10000.0,
        risk_per_trade=0.02,
        position_stop_loss_pct=0.01,
        max_position_size=0.1,
    )
    features = build_features(_uptrend_ohlcv(n=300))

    agent._paper_trade("BTC/USDT", "buyer", features, current_price=100.0)

    position = agent._portfolios["BTC/USDT"]["buyer"].positions["BTC/USDT"]
    # risk_amount = 10000*0.02 = 200; notional = 200/0.01 = 20000, capped at
    # 10000*0.1 = 1000 -> quantity = 10.0, a small fraction of the old
    # (10000*0.95)/100 = 95.0 flat-sizing default would have opened.
    assert position["quantity"] == 10.0


def test_paper_trade_keeps_old_flat_sizing_by_default():
    """Backward compatible: risk_per_trade defaults to None."""
    manager = _manager_with_fake_candidates()
    manager.run_tournament("BTC/USDT", _uptrend_ohlcv(n=300))

    agent = ChampionChallengerAgent(strategy_manager=manager, paper_capital=10000.0)
    features = build_features(_uptrend_ohlcv(n=300))

    agent._paper_trade("BTC/USDT", "buyer", features, current_price=100.0)

    position = agent._portfolios["BTC/USDT"]["buyer"].positions["BTC/USDT"]
    assert position["quantity"] == 95.0


def test_paper_trading_never_touches_a_real_exchange():
    """PortfolioManager (what paper trades are recorded against) has no
    concept of a real exchange connection at all - this is a structural
    guarantee, not just a runtime check, but assert it stays that way."""
    manager = _manager_with_fake_candidates()
    manager.run_tournament("BTC/USDT", _uptrend_ohlcv(n=300))
    agent = ChampionChallengerAgent(strategy_manager=manager)

    portfolio = agent._get_or_create_portfolio("BTC/USDT", "buyer")

    assert not hasattr(portfolio, "exchange")
    assert isinstance(portfolio, PortfolioManager)


def test_paper_portfolios_are_constructed_with_configured_friction():
    """Champion-challenger WITH FRICTION: a challenger's paper P&L must not
    look better than the champion's real P&L purely because it skips the
    transaction costs the champion actually pays."""
    manager = _manager_with_fake_candidates()
    ohlcv = _uptrend_ohlcv(n=300)
    manager.run_tournament("BTC/USDT", ohlcv)

    agent = ChampionChallengerAgent(
        strategy_manager=manager, commission_rate=0.001, slippage_rate=0.0005
    )
    portfolio = agent._get_or_create_portfolio("BTC/USDT", "buyer")

    assert portfolio.commission_rate == 0.001
    assert portfolio.slippage_rate == 0.0005


def test_paper_trading_defaults_to_zero_friction_when_not_configured():
    """Backward compatible: existing callers that never pass commission/
    slippage rates keep the original zero-friction paper simulation."""
    manager = _manager_with_fake_candidates()
    manager.run_tournament("BTC/USDT", _uptrend_ohlcv(n=300))

    agent = ChampionChallengerAgent(strategy_manager=manager)
    portfolio = agent._get_or_create_portfolio("BTC/USDT", "buyer")

    assert portfolio.commission_rate == 0.0
    assert portfolio.slippage_rate == 0.0


def test_report_reflects_accumulated_paper_performance():
    manager = _manager_with_fake_candidates()
    ohlcv = _uptrend_ohlcv(n=300)
    manager.run_tournament("BTC/USDT", ohlcv)

    from trading_assistant.agents.strategy_manager import RegimeSelection

    manager.last_tournament["BTC/USDT"].winners["trending_up"] = RegimeSelection(
        regime="trending_up", winner="holder", reason="forced for test"
    )

    agent = ChampionChallengerAgent(strategy_manager=manager)
    features = build_features(ohlcv)

    # buyer opens a paper long at 200, price rises to 220 next cycle -> unrealized profit
    agent.process_cycle("BTC/USDT", "trending_up", features, current_price=200.0)
    agent.process_cycle("BTC/USDT", "trending_up", features, current_price=220.0)

    reports = {rep.candidate_name: rep for rep in agent.report("BTC/USDT")}
    assert reports["buyer"].total_value > agent.paper_capital  # unrealized gain reflected
    assert reports["seller"].total_value == agent.paper_capital  # never traded, unchanged


def test_compare_to_champion_flags_outperforming_challengers_only():
    manager = _manager_with_fake_candidates()
    ohlcv = _uptrend_ohlcv(n=300)
    manager.run_tournament("BTC/USDT", ohlcv)

    from trading_assistant.agents.strategy_manager import RegimeSelection

    manager.last_tournament["BTC/USDT"].winners["trending_up"] = RegimeSelection(
        regime="trending_up", winner="holder", reason="forced for test"
    )

    agent = ChampionChallengerAgent(strategy_manager=manager, min_trades_to_compare=1)
    features = build_features(ohlcv)

    for price in [200.0, 210.0, 220.0, 230.0, 240.0]:
        agent.process_cycle("BTC/USDT", "trending_up", features, current_price=price)
    agent._paper_trade("BTC/USDT", "buyer", features, current_price=240.0)  # ensure a closed round-trip exists

    champion_portfolio = PortfolioManager(initial_capital=10000.0)  # flat champion (sharpe 0) - easy to beat

    outperforming_names = {rep.candidate_name for rep in agent.compare_to_champion("BTC/USDT", champion_portfolio)}
    assert "seller" not in outperforming_names  # never opened a position in an uptrend, sharpe 0, doesn't beat 0


def test_underperforming_candidates_flags_underperforming_challengers_only():
    manager = _manager_with_fake_candidates()
    manager.run_tournament("BTC/USDT", _uptrend_ohlcv(n=300))

    agent = ChampionChallengerAgent(strategy_manager=manager, min_trades_to_compare=1)
    # a clearly WINNING candidate must not be flagged, only the clearly
    # LOSING one, against a middling champion in between the two.
    winning_portfolio = agent._get_or_create_portfolio("BTC/USDT", "buyer")
    winning_portfolio.equity_curve = [10000.0, 12000.0, 14000.0, 16000.0]
    winning_portfolio.trade_history = [{"pnl": 2000.0}] * 3

    losing_portfolio = agent._get_or_create_portfolio("BTC/USDT", "seller")
    losing_portfolio.equity_curve = [10000.0, 9000.0, 8000.0]
    losing_portfolio.trade_history = [{"pnl": -500.0}] * 2

    champion_portfolio = PortfolioManager(initial_capital=10000.0)
    champion_portfolio.equity_curve = [10000.0, 10200.0, 10100.0, 10300.0]

    underperforming_names = {
        rep.candidate_name for rep in agent.underperforming_candidates("BTC/USDT", champion_portfolio)
    }
    assert "seller" in underperforming_names
    assert "buyer" not in underperforming_names


def test_underperforming_candidates_raises_alert_when_configured(tmp_path):
    from trading_assistant.agents.alerting import AlertManager
    from trading_assistant.persistence.journal import Journal

    manager = _manager_with_fake_candidates()
    manager.run_tournament("BTC/USDT", _uptrend_ohlcv(n=300))

    journal = Journal(db_path=str(tmp_path / "journal.db"))
    alert_manager = AlertManager(journal=journal)
    agent = ChampionChallengerAgent(
        strategy_manager=manager, min_trades_to_compare=1, alert_manager=alert_manager
    )
    losing_portfolio = agent._get_or_create_portfolio("BTC/USDT", "seller")
    losing_portfolio.equity_curve = [10000.0, 9000.0, 8000.0]
    losing_portfolio.trade_history = [{"pnl": -500.0}]

    champion_portfolio = PortfolioManager(initial_capital=10000.0)
    champion_portfolio.equity_curve = [10000.0, 11000.0, 12000.0, 13000.0]

    agent.underperforming_candidates("BTC/USDT", champion_portfolio)

    alerts = journal.recent_alerts()
    assert len(alerts) == 1
    assert alerts[0]["category"] == "challenger_underperforming"
    assert "seller" in alerts[0]["message"]


def test_underperforming_candidates_silent_when_no_alert_manager_configured():
    """Backward compatible: alert_manager defaults to None."""
    manager = _manager_with_fake_candidates()
    manager.run_tournament("BTC/USDT", _uptrend_ohlcv(n=300))
    agent = ChampionChallengerAgent(strategy_manager=manager, min_trades_to_compare=1)
    losing_portfolio = agent._get_or_create_portfolio("BTC/USDT", "seller")
    losing_portfolio.equity_curve = [10000.0, 9000.0, 8000.0]
    losing_portfolio.trade_history = [{"pnl": -500.0}]
    champion_portfolio = PortfolioManager(initial_capital=10000.0)
    champion_portfolio.equity_curve = [10000.0, 11000.0, 12000.0]

    result = agent.underperforming_candidates("BTC/USDT", champion_portfolio)

    assert len(result) == 1  # still computed and returned - just not alerted


def test_restart_resumes_equity_curve_continuity_instead_of_resetting(tmp_path):
    """Regression test for the paper-trading analog of the real portfolio's
    restart-amnesia bug: without resuming from the journal, every restart
    would silently reset a candidate's paper equity back to a flat
    paper_capital start, corrupting any equity curve spanning the restart
    with a fake 'reset' discontinuity - invalidating any Sharpe/drawdown
    computed across the full history, which is the entire point of
    tracking paper performance as evidence of a real edge."""
    from trading_assistant.persistence.journal import Journal

    journal = Journal(db_path=str(tmp_path / "journal.db"))
    # Simulate persisted history from BEFORE a restart: candidate grew from
    # 10000 to 10500 over 3 cycles, with 2 trades recorded.
    for total_equity, capital, num_trades in [(10100.0, 9000.0, 1), (10300.0, 9000.0, 1), (10500.0, 500.0, 2)]:
        journal.record_paper_equity_snapshot(
            symbol="BTC/USDT", candidate="buyer", capital=capital, total_equity=total_equity, num_trades=num_trades
        )

    # A FRESH agent instance, as if the process had just restarted -
    # ChampionChallengerAgent itself carries no memory of the run before.
    manager = _manager_with_fake_candidates()
    manager.run_tournament("BTC/USDT", _uptrend_ohlcv(n=300))
    agent = ChampionChallengerAgent(strategy_manager=manager, paper_capital=10000.0, journal=journal)

    portfolio = agent._get_or_create_portfolio("BTC/USDT", "buyer")

    assert portfolio.capital == 500.0  # resumed from the LAST persisted snapshot, not reset to 10000
    assert portfolio.equity_curve[-1] == 10500.0
    assert portfolio.equity_curve[0] == 10000.0  # initial_capital preserved as the curve's true starting point
    assert len(portfolio.trade_history) == 2  # resumed trade count, not reset to 0


def test_fresh_candidate_with_no_journal_history_starts_normally(tmp_path):
    """First-run case: a candidate with no persisted history yet must start
    exactly as before this feature existed - flat at paper_capital."""
    from trading_assistant.persistence.journal import Journal

    journal = Journal(db_path=str(tmp_path / "journal.db"))
    manager = _manager_with_fake_candidates()
    manager.run_tournament("BTC/USDT", _uptrend_ohlcv(n=300))
    agent = ChampionChallengerAgent(strategy_manager=manager, paper_capital=10000.0, journal=journal)

    portfolio = agent._get_or_create_portfolio("BTC/USDT", "buyer")

    assert portfolio.capital == 10000.0
    assert portfolio.equity_curve == [10000.0]
    assert portfolio.trade_history == []


def test_resume_is_a_noop_without_a_journal():
    manager = _manager_with_fake_candidates()
    manager.run_tournament("BTC/USDT", _uptrend_ohlcv(n=300))
    agent = ChampionChallengerAgent(strategy_manager=manager, paper_capital=10000.0)  # no journal

    portfolio = agent._get_or_create_portfolio("BTC/USDT", "buyer")  # must not raise

    assert portfolio.capital == 10000.0

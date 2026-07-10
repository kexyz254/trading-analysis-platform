"""Entrypoint for running the autonomous trading system as a long-lived process.

Usage:
    python run_system.py                  # run forever against config.yaml (testnet by default)
    python run_system.py --cycles 5        # run 5 cycles then exit (smoke testing)
    python run_system.py --config my.yaml  # use an alternate config file
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Optional

import ccxt

from trading_assistant.agents.alerting import AlertManager
from trading_assistant.agents.champion_challenger import ChampionChallengerAgent
from trading_assistant.agents.execution import ExecutionAgent
from trading_assistant.agents.learning import LearningAgent
from trading_assistant.agents.market_monitor import MarketMonitorAgent
from trading_assistant.agents.orchestrator import Orchestrator
from trading_assistant.agents.portfolio import PortfolioAgent
from trading_assistant.agents.prediction import PredictionAgent
from trading_assistant.agents.risk import RiskAgent
from trading_assistant.agents.strategy_manager import StrategyManager
from trading_assistant.agents.symbol_discovery import SymbolDiscoveryAgent
from trading_assistant.annualization import bars_per_year, periods_per_year_from_seconds
from trading_assistant.config import Config, load_config
from trading_assistant.logging_setup import configure_logging
from trading_assistant.persistence.journal import Journal
from trading_assistant.portfolio.manager import PortfolioManager
from trading_assistant.process_lock import AlreadyRunningError, acquire_singleton_lock

# Repo root, not CWD - this script is always at the repo root, so the lock
# lives next to it regardless of where the process was launched from.
_LOCK_PATH = Path(__file__).resolve().parent / "run_system.lock"
from trading_assistant.risk.adaptive import DrawdownTier

logger = logging.getLogger(__name__)


def build_exchange(config: Config):
    exchange_class = getattr(ccxt, config.exchange.name)
    exchange = exchange_class({"enableRateLimit": True, "timeout": config.exchange.timeout_ms})

    api_key, api_secret = config.get_api_credentials()
    if config.is_live and not (api_key and api_secret):
        # Refuse to start rather than silently run "live" 24/7 while every
        # order fails forever with only a log line - previously there was
        # no check here at all, so a missing/misconfigured .env on a fresh
        # VPS deploy would look like a healthy running system that just
        # never actually trades, with nothing loud enough to notice.
        raise RuntimeError(
            f"mode: live requires {config.exchange.api_key_env}/{config.exchange.api_secret_env} "
            "to be set (real credentials, not testnet ones) - refusing to start."
        )
    if api_key and api_secret:
        exchange.apiKey = api_key
        exchange.secret = api_secret

    if not config.is_live:
        exchange.set_sandbox_mode(True)
        logger.info("exchange configured in TESTNET/sandbox mode")
    else:
        logger.warning("exchange configured in LIVE mode - real funds are at risk")

    return exchange


def build_alert_manager(journal: Journal) -> AlertManager:
    # Unset by default (no external calls unless an operator deliberately
    # opts in) - matches the same env-var-for-secrets pattern as the
    # exchange API key/secret, see Config.get_api_credentials.
    webhook_url = os.environ.get("ALERT_WEBHOOK_URL")
    if not webhook_url:
        logger.info("ALERT_WEBHOOK_URL not set - alerts will be journaled but not delivered externally")

    # Email via Gmail SMTP: SMTP_USERNAME is the full Gmail address,
    # SMTP_PASSWORD is a Gmail APP PASSWORD (myaccount.google.com/apppasswords),
    # never the real account password - Gmail rejects plain password SMTP
    # auth once 2FA is enabled, which it should be. ALERT_EMAIL_TO is
    # comma-separated for multiple recipients.
    smtp_username = os.environ.get("SMTP_USERNAME")
    smtp_password = os.environ.get("SMTP_PASSWORD")
    email_to = [addr.strip() for addr in os.environ.get("ALERT_EMAIL_TO", "").split(",") if addr.strip()]
    if not (smtp_username and smtp_password and email_to):
        logger.info("SMTP email alerting not fully configured - alerts will be journaled but not emailed")

    return AlertManager(
        journal=journal,
        webhook_url=webhook_url,
        smtp_host=os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        smtp_port=int(os.environ.get("SMTP_PORT", "587")),
        smtp_username=smtp_username,
        smtp_password=smtp_password,
        email_from=os.environ.get("ALERT_EMAIL_FROM") or smtp_username,
        email_to=email_to,
        email_min_severity=os.environ.get("ALERT_EMAIL_MIN_SEVERITY", "critical"),
    )


def build_orchestrator(config: Config) -> Orchestrator:
    exchange = build_exchange(config)
    journal = Journal(db_path="trading_system.db")
    alert_manager = build_alert_manager(journal)

    # See trading_assistant.annualization: a Sharpe ratio's annualization
    # factor must match how often the underlying equity curve actually gets
    # a new point, not a hardcoded 252 (daily trading year) - these two
    # differ because walk-forward validation samples once per OHLCV bar
    # (config.timeframe, e.g. hourly) while the live/paper portfolio's
    # equity curve is appended once per Orchestrator poll cycle
    # (config.poll_interval_seconds, e.g. every 5 minutes).
    backtest_periods_per_year = bars_per_year(config.timeframe)
    live_periods_per_year = periods_per_year_from_seconds(config.poll_interval_seconds)

    portfolio_manager = PortfolioManager(
        initial_capital=config.portfolio.initial_capital,
        commission_rate=config.portfolio.commission_rate,
        slippage_rate=config.portfolio.slippage_rate,
        periods_per_year=live_periods_per_year,
    )
    prediction_agent = PredictionAgent(
        buy_threshold=config.prediction.buy_threshold, sell_threshold=config.prediction.sell_threshold
    )

    learning_agent = LearningAgent(
        prediction_agent=prediction_agent,
        min_sharpe_to_promote=config.learning.min_sharpe_to_promote,
        max_drawdown_to_promote=config.learning.max_drawdown_to_promote,
        train_window_bars=config.learning.train_window_bars,
        test_window_bars=config.learning.test_window_bars,
        target_folds=config.learning.target_folds,
        min_folds_required=config.learning.min_folds_required,
        min_pct_folds_profitable=config.learning.min_pct_folds_profitable,
        journal=journal,
        exchange=exchange,
        alert_manager=alert_manager,
        min_live_trades_for_decay_check=config.learning.min_live_trades_for_decay_check,
        decay_win_rate_fraction=config.learning.decay_win_rate_fraction,
        decay_lookback_trades=config.learning.decay_lookback_trades,
        risk_per_trade=config.risk.risk_per_trade,
        position_stop_loss_pct=config.risk.stop_loss_pct,
        max_position_size=config.risk.max_position_size,
        commission_rate=config.portfolio.commission_rate,
        slippage_rate=config.portfolio.slippage_rate,
        periods_per_year=backtest_periods_per_year,
    )

    symbol_discovery_agent = None
    if config.symbol_discovery.enabled:
        symbol_discovery_agent = SymbolDiscoveryAgent(
            exchange=exchange,
            quote_currency=config.symbol_discovery.quote_currency,
            min_24h_volume_usd=config.symbol_discovery.min_24h_volume_usd,
            max_symbols=config.symbol_discovery.max_symbols,
        )

    strategy_manager = None
    champion_challenger_agent = None
    if config.tournament.enabled:
        strategy_manager = StrategyManager(
            train_window_bars=config.tournament.train_window_bars,
            test_window_bars=config.tournament.test_window_bars,
            target_folds=config.tournament.target_folds,
            min_folds_required=config.tournament.min_folds_required,
            min_sharpe_to_promote=config.tournament.min_sharpe_to_promote,
            min_pct_folds_profitable=config.tournament.min_pct_folds_profitable,
            allow_short=config.tournament.allow_short,
            journal=journal,
            exchange=exchange,
            champion_switch_min_runs=config.tournament.champion_switch_min_runs,
            champion_switch_margin=config.tournament.champion_switch_margin,
            risk_per_trade=config.risk.risk_per_trade,
            position_stop_loss_pct=config.risk.stop_loss_pct,
            max_position_size=config.risk.max_position_size,
            # Matches ChampionChallengerAgent's paper_commission_rate/
            # paper_slippage_rate below, not portfolio.commission_rate -
            # the tournament's OWN backtest validation and its paper-trading
            # comparison must share the same friction assumption, or a
            # candidate could look better in one than the other purely from
            # a cost-basis mismatch, not real performance.
            commission_rate=config.tournament.paper_commission_rate,
            slippage_rate=config.tournament.paper_slippage_rate,
            periods_per_year=backtest_periods_per_year,
        )
        # Paper-trading runs whenever the tournament is enabled, independent
        # of drive_live_trading - it never touches real (even testnet)
        # execution, so there's no reason to gate it behind the live-trading
        # escalation. See ChampionChallengerAgent.process_cycle's
        # exclude_champion parameter for how the two interact once
        # drive_live_trading is eventually turned on.
        champion_challenger_agent = ChampionChallengerAgent(
            strategy_manager=strategy_manager,
            paper_capital=config.tournament.paper_capital,
            min_trades_to_compare=config.tournament.min_trades_to_compare,
            journal=journal,
            commission_rate=config.tournament.paper_commission_rate,
            slippage_rate=config.tournament.paper_slippage_rate,
            risk_per_trade=config.risk.risk_per_trade,
            position_stop_loss_pct=config.risk.stop_loss_pct,
            max_position_size=config.risk.max_position_size,
            alert_manager=alert_manager,
            periods_per_year=live_periods_per_year,
        )

    return Orchestrator(
        market_monitor=MarketMonitorAgent(
            symbols=config.symbols,
            timeframe=config.timeframe,
            # Matches the tournament's test_window_bars so live regime
            # classification uses the same "windowed average" definition
            # the tournament validated a champion against - see
            # MarketMonitorAgent's regime_window_bars docstring.
            regime_window_bars=config.tournament.test_window_bars,
            exchange=exchange,
            enable_integrity_checks=config.market_data.enable_integrity_checks,
            max_single_bar_move_pct=config.market_data.max_single_bar_move_pct,
            max_staleness_multiple=config.market_data.max_staleness_multiple,
            alert_manager=alert_manager,
            panic_atr_threshold=config.market_data.panic_atr_threshold,
            illiquid_volume_threshold=config.market_data.illiquid_volume_threshold,
            regime_history_window=config.market_data.regime_history_window,
            regime_churn_alert_threshold=config.market_data.regime_churn_alert_threshold,
        ),
        prediction=prediction_agent,
        risk=RiskAgent(
            max_position_size=config.risk.max_position_size,
            max_daily_loss=config.risk.max_daily_loss,
            risk_per_trade=config.risk.risk_per_trade,
            stop_loss_pct=config.risk.stop_loss_pct,
            max_position_weight=config.risk.max_position_weight,
            max_gross_exposure=config.risk.max_gross_exposure,
            max_net_exposure=config.risk.max_net_exposure,
            max_consecutive_errors=config.risk.max_consecutive_errors,
            journal=journal,
            max_correlated_exposure_pct=config.risk.max_correlated_exposure_pct,
            correlation_threshold=config.risk.correlation_threshold,
            correlation_lookback_bars=config.risk.correlation_lookback_bars,
            drawdown_tiers=(
                [DrawdownTier(**tier) for tier in config.risk.drawdown_tiers]
                if config.risk.drawdown_tiers
                else None
            ),
            higher_timeframe_factor=config.risk.higher_timeframe_factor,
            trend_alignment_fast_period=config.risk.trend_alignment_fast_period,
            trend_alignment_slow_period=config.risk.trend_alignment_slow_period,
            alert_manager=alert_manager,
            max_drawdown_pct=config.risk.max_drawdown_pct,
            atr_multiplier=config.risk.atr_multiplier,
            min_atr_stop_pct=config.risk.min_atr_stop_pct,
            max_atr_stop_pct=config.risk.max_atr_stop_pct,
            kelly_min_trades=config.risk.kelly_min_trades,
            kelly_lookback_trades=config.risk.kelly_lookback_trades,
            crisis_drop_threshold=config.risk.crisis_drop_threshold,
            crisis_symbol=config.risk.crisis_symbol,
            crisis_window_hours=config.risk.crisis_window_hours,
            crisis_duration_hours=config.risk.crisis_duration_hours,
            min_stake_usd=config.risk.min_stake_usd,
        ),
        execution=ExecutionAgent(
            portfolio=portfolio_manager,
            exchange=exchange,
            stop_loss_pct=config.risk.stop_loss_pct,
            max_spread_pct=config.risk.max_spread_pct,
            enable_adaptive_order_type=config.risk.enable_adaptive_order_type,
        ),
        portfolio=PortfolioAgent(portfolio=portfolio_manager, journal=journal, is_live=config.is_live),
        journal=journal,
        poll_interval_seconds=config.poll_interval_seconds,
        learning=learning_agent,
        retrain_interval_seconds=config.learning.retrain_interval_hours * 3600,
        symbol_discovery=symbol_discovery_agent,
        symbol_discovery_interval_seconds=config.symbol_discovery.refresh_interval_hours * 3600,
        strategy_manager=strategy_manager,
        tournament_interval_seconds=config.tournament.refresh_interval_hours * 3600,
        champion_challenger=champion_challenger_agent,
        drive_live_trading=config.tournament.drive_live_trading,
        alert_manager=alert_manager,
        is_live=config.is_live,
        quote_currency=config.symbol_discovery.quote_currency,
        min_live_capital_usd=config.portfolio.min_live_capital_usd,
        trailing_stop_pct=config.risk.trailing_stop_pct,
        partial_profit_pct=config.risk.partial_profit_pct,
        partial_profit_sell_fraction=config.risk.partial_profit_sell_fraction,
        retention_days=config.retention.retention_days,
        retention_interval_seconds=(
            config.retention.run_interval_hours * 3600 if config.retention.enabled else None
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the autonomous trading system")
    parser.add_argument("--config", default=None, help="Path to config.yaml")
    parser.add_argument(
        "--cycles", type=int, default=None, help="Run a fixed number of cycles then exit (for smoke testing)"
    )
    args = parser.parse_args()

    # First, before anything else (including config/logging setup) - a
    # second instance racing this one against the same database/exchange
    # account is a correctness emergency, not something worth doing any
    # other work before checking. See process_lock's module docstring for
    # why this exists: it already happened, more than once, in this
    # project's own history.
    try:
        lock_file = acquire_singleton_lock(str(_LOCK_PATH))
    except AlreadyRunningError as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        config = load_config(args.config)
        configure_logging(level=config.logging.level, log_file=config.logging.file)
        logger.info(
            "starting trading system",
            extra={"context": {"mode": config.mode, "symbols": config.symbols, "cycles": args.cycles}},
        )

        orchestrator = build_orchestrator(config)
        asyncio.run(_run_with_graceful_shutdown(orchestrator, args.cycles))
    finally:
        lock_file.close()


async def _run_with_graceful_shutdown(orchestrator: Orchestrator, max_cycles: Optional[int]) -> None:
    # SIGTERM/SIGINT (what systemd/Docker send on stop/restart) must not
    # kill this process mid-cycle - loop.add_signal_handler registers on
    # THIS specific running event loop (must be called from inside a
    # running loop, hence this wrapper rather than registering before
    # asyncio.run), triggering Orchestrator.request_shutdown() so
    # run_forever exits cleanly after the current cycle instead of Python's
    # default SIGTERM disposition (immediate termination, skipping
    # run_forever's finally block that cancels background tasks cleanly).
    # add_signal_handler is POSIX-only - a no-op guard on Windows (this
    # session's dev machine), fully functional on the Linux VPS target.
    if sys.platform != "win32":
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, orchestrator.request_shutdown)
    await orchestrator.run_forever(max_cycles=max_cycles)


if __name__ == "__main__":
    main()

"""Configuration loading for the autonomous trading system.

Loads settings from a YAML file (default: ``config.yaml`` at the repo root)
and secrets from environment variables (populated via a ``.env`` file
through python-dotenv). The system defaults to Binance testnet/paper mode;
live trading requires an explicit ``mode: live`` in config plus real API
keys in the environment.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yaml
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


@dataclass
class ExchangeConfig:
    name: str = "binance"
    api_key_env: str = "BINANCE_API_KEY"
    api_secret_env: str = "BINANCE_API_SECRET"
    timeout_ms: int = 20000


@dataclass
class RiskConfig:
    max_position_size: float = 0.1
    max_daily_loss: float = 0.02
    risk_per_trade: float = 0.02
    stop_loss_pct: float = 0.01
    max_position_weight: float = 0.25
    min_stake_usd: float = 1.0
    max_gross_exposure_pct: Optional[float] = 0.8
    max_net_exposure_pct: Optional[float] = 0.8
    max_consecutive_errors: int = 3
    max_correlated_exposure_pct: Optional[float] = 0.4
    correlation_threshold: float = 0.6
    correlation_lookback_bars: int = 50
    drawdown_tiers: List[Dict[str, float]] = field(
        default_factory=lambda: [
            {"drawdown_pct": 0.05, "risk_scale": 0.75},
            {"drawdown_pct": 0.10, "risk_scale": 0.50},
            {"drawdown_pct": 0.15, "risk_scale": 0.25},
            {"drawdown_pct": 0.20, "risk_scale": 0.0},
        ]
    )
    higher_timeframe_factor: Optional[int] = 4
    trend_alignment_fast_period: Optional[int] = 20
    trend_alignment_slow_period: int = 50
    require_sma_slope_rising: bool = True
    adx_threshold: Optional[float] = 25.0
    volume_confirmation_threshold: Optional[float] = 1.0
    max_spread_pct: Optional[float] = 0.01
    enable_adaptive_order_type: bool = True
    trailing_stop_pct: Optional[float] = None
    trailing_stop_activation_pct: Optional[float] = None
    trend_exit_sma_period: Optional[int] = None
    partial_profit_pct: Optional[float] = None
    partial_profit_sell_fraction: float = 0.5
    max_drawdown_pct: Optional[float] = None
    atr_multiplier: Optional[float] = 2.0
    min_atr_stop_pct: float = 0.005
    max_atr_stop_pct: float = 0.05
    kelly_min_trades: Optional[int] = 15
    kelly_lookback_trades: int = 30
    crisis_drop_threshold: Optional[float] = None
    crisis_symbol: str = "BTC/USDT"
    crisis_window_hours: float = 1.0
    crisis_duration_hours: float = 24.0


@dataclass
class MarketDataConfig:
    enable_integrity_checks: bool = False
    max_single_bar_move_pct: Optional[float] = 0.5
    max_staleness_multiple: Optional[float] = 3.0
    panic_atr_threshold: float = 0.08
    illiquid_volume_threshold: float = 0.3
    regime_history_window: int = 20
    regime_churn_alert_threshold: Optional[float] = 0.5


@dataclass
class PortfolioConfig:
    initial_capital: float = 10000.0
    commission_rate: float = 0.001
    slippage_rate: float = 0.0005
    min_live_capital_usd: float = 5.0


@dataclass
class PredictionConfig:
    buy_threshold: float = 0.54
    sell_threshold: float = 0.46


@dataclass
class LearningConfig:
    retrain_interval_hours: float = 24.0
    min_sharpe_to_promote: float = 0.5
    max_drawdown_to_promote: float = 0.25
    train_window_bars: int = 500
    test_window_bars: int = 100
    target_folds: int = 8
    min_folds_required: int = 3
    min_pct_folds_profitable: float = 0.5
    min_trades_per_fold: int = 3
    min_live_trades_for_decay_check: Optional[int] = 15
    decay_win_rate_fraction: float = 0.5
    decay_lookback_trades: int = 30


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: str = "logs/trading_system.log"


@dataclass
class SymbolDiscoveryConfig:
    enabled: bool = False
    quote_currency: str = "USDT"
    min_24h_volume_usd: float = 5_000_000.0
    max_symbols: int = 20
    refresh_interval_hours: float = 24.0


@dataclass
class TournamentConfig:
    enabled: bool = True
    drive_live_trading: bool = False
    train_window_bars: int = 500
    test_window_bars: int = 100
    target_folds: int = 10
    min_folds_required: int = 3
    min_sharpe_to_promote: float = 0.5
    min_pct_folds_profitable: float = 0.5
    allow_short: bool = False
    refresh_interval_hours: float = 24.0
    paper_capital: float = 10000.0
    min_trades_to_compare: int = 5
    paper_commission_rate: float = 0.001
    paper_slippage_rate: float = 0.0005
    champion_switch_min_runs: int = 3
    champion_switch_margin: float = 0.1


@dataclass
class ReadinessConfig:
    min_tournament_runs: int = 20
    min_stability_score: float = 0.8
    min_real_trades: int = 10
    min_real_sharpe: float = 0.5
    min_elapsed_days: float = 7.0
    min_records_for_correlation: int = 5
    min_live_trades_for_correlation: int = 5


@dataclass
class RetentionConfig:
    enabled: bool = True
    retention_days: int = 180
    run_interval_hours: float = 24.0


# ---------------------------------------------------------------------------
# NEW: 15‑minute ML candidate configuration
# ---------------------------------------------------------------------------
@dataclass
class ML15mConfig:
    enabled: bool = False
    atr_multiplier: float = 0.5
    horizon_bars: int = 4
    min_confidence: float = 0.55
    min_rr_ratio: float = 2.0
    fee_rate: float = 0.0015
    stop_loss_atr_mult: float = 2.0
    take_profit_atr_mult: float = 3.0
# ---------------------------------------------------------------------------


@dataclass
class Config:
    mode: str = "testnet"
    execution_mode: str = "manual_assist"
    manual_live_execution_enabled: bool = False
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    symbols: List[str] = field(default_factory=lambda: ["BTC/USDT"])
    timeframe: str = "1h"
    poll_interval_seconds: float = 300.0
    position_monitor_interval_seconds: Optional[float] = 20.0
    risk: RiskConfig = field(default_factory=RiskConfig)
    market_data: MarketDataConfig = field(default_factory=MarketDataConfig)
    portfolio: PortfolioConfig = field(default_factory=PortfolioConfig)
    prediction: PredictionConfig = field(default_factory=PredictionConfig)
    learning: LearningConfig = field(default_factory=LearningConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    symbol_discovery: SymbolDiscoveryConfig = field(default_factory=SymbolDiscoveryConfig)
    tournament: TournamentConfig = field(default_factory=TournamentConfig)
    readiness: ReadinessConfig = field(default_factory=ReadinessConfig)
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    # NEW 15‑minute candidate config
    ml_15m: ML15mConfig = field(default_factory=ML15mConfig)

    @property
    def is_live(self) -> bool:
        return self.mode == "live"

    def get_api_credentials(self) -> Tuple[Optional[str], Optional[str]]:
        api_key = os.environ.get(self.exchange.api_key_env)
        api_secret = os.environ.get(self.exchange.api_secret_env)
        return api_key, api_secret


def load_config(path: Optional[os.PathLike] = None, *, env_file: Optional[os.PathLike] = None) -> Config:
    load_dotenv(dotenv_path=env_file)

    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    data = {}
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    elif path is not None:
        logger.warning(
            "config path %s does not exist - falling back to built-in defaults for everything it would "
            "have set (integrity checks, tournament, symbol discovery, trailing stop/partial-profit, and "
            "the hard drawdown circuit breaker are all OFF by default)",
            config_path,
        )

    mode = data.get("mode", "testnet")
    if mode not in ("testnet", "live"):
        raise ValueError(f"config 'mode' must be 'testnet' or 'live', got {mode!r}")

    execution_mode = data.get("execution_mode", "manual_assist")
    if execution_mode not in ("manual_assist", "autonomous"):
        raise ValueError(
            "config 'execution_mode' must be 'manual_assist' or "
            f"'autonomous', got {execution_mode!r}"
        )
    manual_live_execution_enabled = data.get(
        "manual_live_execution_enabled", False
    )
    if not isinstance(manual_live_execution_enabled, bool):
        raise ValueError(
            "config 'manual_live_execution_enabled' must be boolean"
        )


    defaults = Config()
    return Config(
        mode=mode,
        execution_mode=execution_mode,
        manual_live_execution_enabled=manual_live_execution_enabled,
        exchange=ExchangeConfig(**data.get("exchange", {})),
        symbols=data.get("symbols") or defaults.symbols,
        timeframe=data.get("timeframe", defaults.timeframe),
        poll_interval_seconds=data.get("poll_interval_seconds", defaults.poll_interval_seconds),
        position_monitor_interval_seconds=data.get(
            "position_monitor_interval_seconds", defaults.position_monitor_interval_seconds
        ),
        risk=RiskConfig(**data.get("risk", {})),
        market_data=MarketDataConfig(**data.get("market_data", {})),
        portfolio=PortfolioConfig(**data.get("portfolio", {})),
        prediction=PredictionConfig(**data.get("prediction", {})),
        learning=LearningConfig(**data.get("learning", {})),
        logging=LoggingConfig(**data.get("logging", {})),
        symbol_discovery=SymbolDiscoveryConfig(**data.get("symbol_discovery", {})),
        tournament=TournamentConfig(**data.get("tournament", {})),
        readiness=ReadinessConfig(**data.get("readiness", {})),
        retention=RetentionConfig(**data.get("retention", {})),
        # NEW: parse the ml_15m section from YAML
        ml_15m=ML15mConfig(**data.get("ml_15m", {})),
    )
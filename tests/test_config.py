import logging
import textwrap

import pytest

from trading_assistant.config import load_config


def test_load_config_warns_when_an_explicit_path_does_not_exist(tmp_path, caplog):
    """Regression: a caller-supplied --config path that doesn't exist (e.g.
    a typo) used to silently fall back to full defaults with zero
    indication anything went wrong - dropping most of the system's safety
    net (integrity checks, tournament, trailing stop, the hard drawdown
    circuit breaker) invisibly. Must warn loudly for this case."""
    with caplog.at_level(logging.WARNING):
        load_config(tmp_path / "does_not_exist.yaml", env_file=tmp_path / "does_not_exist.env")

    assert any("does not exist" in r.message for r in caplog.records)


def test_load_config_does_not_warn_when_no_path_is_given(tmp_path, monkeypatch, caplog):
    """The no-argument case (DEFAULT_CONFIG_PATH missing) stays silent by
    design - zero-setup testnet/paper mode is a deliberate, documented
    feature, not a typo to warn about."""
    monkeypatch.chdir(tmp_path)  # no config.yaml here
    monkeypatch.setattr("trading_assistant.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")

    with caplog.at_level(logging.WARNING):
        load_config(env_file=tmp_path / "does_not_exist.env")

    assert caplog.records == []


def test_load_config_defaults_when_file_missing(tmp_path):
    config = load_config(tmp_path / "does_not_exist.yaml", env_file=tmp_path / "does_not_exist.env")
    assert config.mode == "testnet"
    assert config.is_live is False
    assert config.symbols == ["BTC/USDT"]
    assert config.risk.max_daily_loss == 0.02
    assert config.risk.trailing_stop_pct is None
    assert config.risk.partial_profit_pct is None
    assert config.risk.max_drawdown_pct is None
    assert config.risk.atr_multiplier == 2.0
    assert config.exchange.timeout_ms == 20000
    assert config.market_data.enable_integrity_checks is False
    assert config.market_data.panic_atr_threshold == 0.08
    assert config.market_data.illiquid_volume_threshold == 0.3
    assert config.market_data.regime_churn_alert_threshold == 0.5
    assert config.tournament.champion_switch_min_runs == 3
    # Matches TournamentConfig's own docstring ("By default...runs
    # OBSERVATION-ONLY") - regression for a real docstring/default drift
    # that would have silently disabled the tournament pipeline on any
    # fallback-to-defaults path (e.g. a typo'd --config), contradicting
    # every comment claiming it was on by default.
    assert config.tournament.enabled is True
    assert config.tournament.drive_live_trading is False  # the live-trading escalation stays opt-in
    assert config.tournament.champion_switch_margin == 0.1
    assert config.prediction.buy_threshold == 0.54
    assert config.prediction.sell_threshold == 0.46
    assert config.portfolio.min_live_capital_usd == 5.0


def test_load_config_reads_yaml_overrides(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        textwrap.dedent(
            """
            mode: testnet
            symbols:
              - BTC/USDT
              - ETH/USDT
            risk:
              max_daily_loss: 0.05
              trailing_stop_pct: 0.03
              partial_profit_pct: 0.05
            market_data:
              enable_integrity_checks: true
              max_single_bar_move_pct: 0.3
              panic_atr_threshold: 0.12
              regime_churn_alert_threshold: 0.75
            tournament:
              champion_switch_min_runs: 5
              champion_switch_margin: 0.2
            portfolio:
              initial_capital: 500.0
            prediction:
              buy_threshold: 0.58
              sell_threshold: 0.42
            """
        )
    )
    config = load_config(config_file, env_file=tmp_path / "does_not_exist.env")
    assert config.symbols == ["BTC/USDT", "ETH/USDT"]
    assert config.risk.max_daily_loss == 0.05
    assert config.risk.trailing_stop_pct == 0.03
    assert config.risk.partial_profit_pct == 0.05
    assert config.market_data.enable_integrity_checks is True
    assert config.market_data.max_single_bar_move_pct == 0.3
    assert config.market_data.panic_atr_threshold == 0.12
    assert config.market_data.regime_churn_alert_threshold == 0.75
    assert config.tournament.champion_switch_min_runs == 5
    assert config.tournament.champion_switch_margin == 0.2
    assert config.portfolio.initial_capital == 500.0
    assert config.prediction.buy_threshold == 0.58
    assert config.prediction.sell_threshold == 0.42


def test_load_config_rejects_invalid_mode(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("mode: production\n")
    with pytest.raises(ValueError):
        load_config(config_file, env_file=tmp_path / "does_not_exist.env")


def test_get_api_credentials_reads_named_env_vars(tmp_path, monkeypatch):
    monkeypatch.setenv("BINANCE_API_KEY", "test-key")
    monkeypatch.setenv("BINANCE_API_SECRET", "test-secret")
    config = load_config(tmp_path / "does_not_exist.yaml", env_file=tmp_path / "does_not_exist.env")
    api_key, api_secret = config.get_api_credentials()
    assert api_key == "test-key"
    assert api_secret == "test-secret"


def test_is_live_reflects_mode(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("mode: live\n")
    config = load_config(config_file, env_file=tmp_path / "does_not_exist.env")
    assert config.is_live is True

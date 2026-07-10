import pytest

from run_system import build_exchange
from trading_assistant.config import Config, ExchangeConfig


def _config(mode="testnet", api_key_env="TEST_BINANCE_KEY", api_secret_env="TEST_BINANCE_SECRET"):
    return Config(mode=mode, exchange=ExchangeConfig(api_key_env=api_key_env, api_secret_env=api_secret_env))


def test_build_exchange_applies_configured_timeout(monkeypatch):
    monkeypatch.delenv("TEST_BINANCE_KEY", raising=False)
    monkeypatch.delenv("TEST_BINANCE_SECRET", raising=False)

    exchange = build_exchange(_config())

    assert exchange.timeout == 20000


def test_build_exchange_uses_the_configured_timeout_value(monkeypatch):
    monkeypatch.delenv("TEST_BINANCE_KEY", raising=False)
    monkeypatch.delenv("TEST_BINANCE_SECRET", raising=False)
    config = Config(
        mode="testnet",
        exchange=ExchangeConfig(
            api_key_env="TEST_BINANCE_KEY", api_secret_env="TEST_BINANCE_SECRET", timeout_ms=5000
        ),
    )

    exchange = build_exchange(config)

    assert exchange.timeout == 5000


def test_build_exchange_testnet_does_not_require_credentials(monkeypatch):
    monkeypatch.delenv("TEST_BINANCE_KEY", raising=False)
    monkeypatch.delenv("TEST_BINANCE_SECRET", raising=False)

    exchange = build_exchange(_config(mode="testnet"))  # must not raise

    assert exchange.apiKey is None or exchange.apiKey == ""


def test_build_exchange_live_mode_refuses_to_start_without_credentials(monkeypatch):
    monkeypatch.delenv("TEST_BINANCE_KEY", raising=False)
    monkeypatch.delenv("TEST_BINANCE_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="mode: live requires"):
        build_exchange(_config(mode="live"))


def test_build_exchange_live_mode_proceeds_with_credentials_set(monkeypatch):
    monkeypatch.setenv("TEST_BINANCE_KEY", "fake-key")
    monkeypatch.setenv("TEST_BINANCE_SECRET", "fake-secret")

    exchange = build_exchange(_config(mode="live"))

    assert exchange.apiKey == "fake-key"
    assert exchange.secret == "fake-secret"

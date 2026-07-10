from pytest import approx

from trading_assistant.config import Config
from webapp import data_access as da


class FakeExchange:
    def __init__(self, balances, tickers):
        self._balances = balances
        self._tickers = tickers

    def fetch_balance(self):
        return self._balances

    def fetch_tickers(self):
        return self._tickers


def _patch_exchange(monkeypatch, exchange):
    monkeypatch.setattr(da, "get_exchange", lambda config: exchange)
    da._cache._store.clear()  # avoid stale results from another test's cache entry


def test_real_wallet_balance_sums_priced_assets(monkeypatch):
    exchange = FakeExchange(
        balances={
            "USDT": {"total": 1.58},
            "BTC": {"total": 0.00008},
            "info": {}, "free": {}, "used": {}, "total": {},
        },
        tickers={"BTC/USDT": {"last": 64000.0}},
    )
    _patch_exchange(monkeypatch, exchange)

    result = da.real_wallet_balance(Config(), quote_currency="USDT")

    assert result["quote_currency"] == "USDT"
    assert result["has_unpriced_assets"] is False
    # 1.58 (USDT, priced at 1.0) + 0.00008 * 64000 (BTC)
    assert result["total_value"] == approx(1.58 + 0.00008 * 64000.0)
    assets_by_symbol = {a["asset"]: a for a in result["assets"]}
    assert assets_by_symbol["USDT"]["price"] == 1.0
    assert assets_by_symbol["BTC"]["price"] == 64000.0


def test_real_wallet_balance_flags_unpriced_assets_without_dropping_them(monkeypatch):
    exchange = FakeExchange(
        balances={
            "USDT": {"total": 10.0},
            "LDUSDT": {"total": 5.0},  # no direct LDUSDT/USDT ticker - e.g. an Earn product token
        },
        tickers={},
    )
    _patch_exchange(monkeypatch, exchange)

    result = da.real_wallet_balance(Config(), quote_currency="USDT")

    assert result["has_unpriced_assets"] is True
    assert result["total_value"] == 10.0  # only the priced USDT counted
    assets_by_symbol = {a["asset"]: a for a in result["assets"]}
    assert assets_by_symbol["LDUSDT"]["price"] is None
    assert assets_by_symbol["LDUSDT"]["value"] is None
    assert assets_by_symbol["LDUSDT"]["quantity"] == 5.0  # still shown, not dropped


def test_real_wallet_balance_excludes_zero_and_metadata_keys(monkeypatch):
    exchange = FakeExchange(
        balances={
            "USDT": {"total": 10.0},
            "ETH": {"total": 0.0},  # held nothing - must not appear
            "info": {"raw": "exchange-specific blob"},
        },
        tickers={},
    )
    _patch_exchange(monkeypatch, exchange)

    result = da.real_wallet_balance(Config(), quote_currency="USDT")

    symbols = {a["asset"] for a in result["assets"]}
    assert symbols == {"USDT"}

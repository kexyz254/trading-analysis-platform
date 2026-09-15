import pytest

from trading_assistant.market.universe import MarketUniverse


class Exchange:
    def load_markets(self):
        def market(symbol, base):
            return {
                "spot": True,
                "active": True,
                "base": base,
                "quote": "USDT",
                "limits": {
                    "amount": {"min": 0.001},
                    "cost": {"min": 5},
                },
                "precision": {"price": 2, "amount": 4},
                "info": {
                    "status": "TRADING",
                    "orderTypes": ["LIMIT", "MARKET"],
                },
            }

        return {
            "BTC/USDT": market("BTC/USDT", "BTC"),
            "ETH/USDT": market("ETH/USDT", "ETH"),
            "SOL/USDT": market("SOL/USDT", "SOL"),
        }


def test_universe_refresh_and_bootstrap():
    universe = MarketUniverse()

    assert universe.refresh(Exchange()) == 3

    universe.bootstrap(
        configured_symbols=["BTC/USDT"],
        watchlist_symbols=["ETH/USDT"],
    )

    assert universe.tracked.reasons("BTC/USDT") == {"ats_candidate"}
    assert universe.tracked.reasons("ETH/USDT") == {"watchlist"}


def test_select_promotes_supported_market():
    universe = MarketUniverse()
    universe.refresh(Exchange())

    universe.select("SOL/USDT")

    assert universe.tracked.reasons("SOL/USDT") == {"selected"}


def test_select_rejects_unknown_market():
    universe = MarketUniverse()
    universe.refresh(Exchange())

    with pytest.raises(ValueError):
        universe.select("FAKE/USDT")


def test_search_returns_serializable_market_records():
    universe = MarketUniverse()
    universe.refresh(Exchange())

    result = universe.search("btc")

    assert result[0]["symbol"] == "BTC/USDT"

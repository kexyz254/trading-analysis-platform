from trading_assistant.market.catalog import MarketCatalog


class Exchange:
    def load_markets(self):
        return {
            "BTC/USDT": {
                "spot": True,
                "active": True,
                "base": "BTC",
                "quote": "USDT",
                "precision": {"price": 2, "amount": 6},
                "limits": {"amount": {"min": 0.00001}, "cost": {"min": 5}},
                "info": {
                    "status": "TRADING",
                    "orderTypes": ["LIMIT", "MARKET"],
                    "filters": [
                        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
                        {"filterType": "LOT_SIZE", "stepSize": "0.00001"},
                    ],
                },
            },
            "OLD/USDT": {
                "spot": True,
                "active": False,
                "base": "OLD",
                "quote": "USDT",
                "info": {"status": "BREAK"},
            },
            "BTC/USDT:USDT": {
                "spot": False,
                "type": "swap",
                "active": True,
                "base": "BTC",
                "quote": "USDT",
                "info": {"status": "TRADING"},
            },
        }


def test_catalog_keeps_only_active_spot_markets():
    catalog = MarketCatalog()
    assert catalog.refresh(Exchange()) == 1

    market = catalog.get("BTC/USDT")
    assert market is not None
    assert market.tick_size == 0.01
    assert market.lot_step == 0.00001
    assert market.min_notional == 5.0
    assert "MARKET" in market.order_types


def test_catalog_searches_by_pair_components():
    catalog = MarketCatalog()
    catalog.refresh(Exchange())

    assert [m.symbol for m in catalog.search("btc")] == ["BTC/USDT"]
    assert [m.symbol for m in catalog.search(quote="USDT")] == ["BTC/USDT"]

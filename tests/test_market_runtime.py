from trading_assistant.market.runtime import bootstrap_market_universe


class Exchange:
    mutation_calls = 0

    def load_markets(self):
        def market(base):
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
            "BTC/USDT": market("BTC"),
            "ETH/USDT": market("ETH"),
            "SOL/USDT": market("SOL"),
        }

    def fetch_balance(self):
        return {
            "total": {
                "USDT": 100.0,
                "ETH": 0.5,
            }
        }

    def fetch_open_orders(self):
        return [{"symbol": "SOL/USDT"}]

    def create_order(self, *args, **kwargs):
        self.mutation_calls += 1
        raise AssertionError("market bootstrap attempted an exchange mutation")


def test_runtime_bootstraps_all_relevant_sources_without_mutation():
    exchange = Exchange()

    universe = bootstrap_market_universe(
        exchange,
        ["BTC/USDT"],
        quote_currency="USDT",
    )

    assert universe.tracked.reasons("BTC/USDT") == {"ats_candidate"}
    assert universe.tracked.reasons("ETH/USDT") == {"account_holding"}
    assert universe.tracked.reasons("SOL/USDT") == {"open_order"}

    assert exchange.mutation_calls == 0

from trading_assistant.execution.ownership import OwnershipService
from trading_assistant.execution.provenance import TradeProvenance
from trading_assistant.market.catalog import MarketCatalog
from trading_assistant.persistence.journal import Journal
from trading_assistant.portfolio.account_snapshot import AccountSnapshotService


class Broker:
    def __init__(self, total, free=None):
        self.total = total
        self.free = free or total

    def fetch_balance(self):
        return {
            "total": self.total,
            "free": self.free,
        }

    def fetch_ticker(self, symbol):
        prices = {
            "BTC/USDT": 110,
            "ETH/USDT": 90,
        }
        return {"last": prices[symbol]}


class Exchange:
    def load_markets(self):
        def market(base):
            return {
                "spot": True,
                "active": True,
                "base": base,
                "quote": "USDT",
                "limits": {},
                "precision": {},
                "info": {"status": "TRADING"},
            }

        return {
            "BTC/USDT": market("BTC"),
            "ETH/USDT": market("ETH"),
        }


def catalog():
    c = MarketCatalog()
    c.refresh(Exchange())
    return c


def test_live_equity_separates_manual_ats_and_residual(tmp_path):
    journal = Journal(str(tmp_path / "db.sqlite"))
    owned = OwnershipService(journal)

    owned.record_fill(
        account_id="primary",
        symbol="BTC/USDT",
        provenance=TradeProvenance.MANUAL,
        side="buy",
        quantity=.4,
        price=100,
    )

    owned.record_fill(
        account_id="primary",
        symbol="BTC/USDT",
        provenance=TradeProvenance.ATS_AUTONOMOUS,
        side="buy",
        quantity=.5,
        price=100,
    )

    broker = Broker(
        total={"USDT": 600, "BTC": 1},
        free={"USDT": 500, "BTC": 1},
    )

    result = AccountSnapshotService(
        broker=broker,
        catalog=catalog(),
        journal=journal,
    ).snapshot()

    assert result.total_account_equity == 710
    assert result.free_quote_cash == 500
    assert result.reserved_quote_cash == 100

    assert result.manual_position_equity == 44
    assert result.ats_position_equity == 55

    # 0.1 BTC exists at broker but has no ownership record.
    assert round(result.residual_unattributed_equity, 8) == 11


def test_unknown_asset_makes_total_equity_fail_closed(tmp_path):
    journal = Journal(str(tmp_path / "db.sqlite"))

    result = AccountSnapshotService(
        broker=Broker(
            total={"USDT": 100, "XYZ": 2},
        ),
        catalog=catalog(),
        journal=journal,
    ).snapshot()

    assert result.total_account_equity is None
    assert result.unpriced_assets == ("XYZ",)


def test_manual_activity_never_appears_in_ats_equity(tmp_path):
    journal = Journal(str(tmp_path / "db.sqlite"))
    owned = OwnershipService(journal)

    owned.record_fill(
        account_id="primary",
        symbol="BTC/USDT",
        provenance=TradeProvenance.MANUAL,
        side="buy",
        quantity=1,
        price=100,
    )

    result = AccountSnapshotService(
        broker=Broker(
            total={"USDT": 0, "BTC": 1},
        ),
        catalog=catalog(),
        journal=journal,
    ).snapshot()

    assert result.manual_position_equity == 110
    assert result.ats_position_equity == 0


def test_broker_ownership_quantity_mismatch_is_visible(tmp_path):
    journal = Journal(str(tmp_path / "db.sqlite"))
    owned = OwnershipService(journal)

    owned.record_fill(
        account_id="primary",
        symbol="BTC/USDT",
        provenance=TradeProvenance.MANUAL,
        side="buy",
        quantity=.5,
        price=100,
    )

    result = AccountSnapshotService(
        broker=Broker(
            total={"USDT": 0, "BTC": 1},
        ),
        catalog=catalog(),
        journal=journal,
    ).snapshot()

    assert any(
        "differs from broker quantity" in warning
        for warning in result.warnings
    )

from trading_assistant.portfolio.ownership_accounting import (
    build_ownership_accounting,
)


def event(
    *,
    provenance,
    event_type,
    quantity,
    price,
    symbol="BTC/USDT",
    realized_pnl=None,
):
    return {
        "symbol": symbol,
        "provenance": provenance,
        "event_type": event_type,
        "quantity": quantity,
        "price": price,
        "realized_pnl": realized_pnl,
        "details": "{}",
    }


def test_manual_and_ats_equity_are_separated():
    events = [
        event(
            provenance="MANUAL",
            event_type="POSITION_INCREASE",
            quantity=1,
            price=100,
        ),
        event(
            provenance="ATS_AUTONOMOUS",
            event_type="POSITION_INCREASE",
            quantity=2,
            price=100,
            symbol="ETH/USDT",
        ),
    ]

    result = build_ownership_accounting(
        events,
        {
            "BTC/USDT": 110,
            "ETH/USDT": 90,
        },
    )

    assert result.manual.market_value == 110
    assert result.manual.unrealized_pnl == 10

    assert result.ats.market_value == 180
    assert result.ats.unrealized_pnl == -20


def test_assisted_manual_belongs_to_manual_bucket():
    events = [
        event(
            provenance="ATS_ASSISTED_MANUAL",
            event_type="POSITION_INCREASE",
            quantity=1,
            price=100,
        )
    ]

    result = build_ownership_accounting(
        events,
        {"BTC/USDT": 120},
    )

    assert result.manual.market_value == 120
    assert result.ats.market_value == 0


def test_external_and_unknown_are_never_counted_as_ats():
    events = [
        event(
            provenance="EXTERNAL",
            event_type="POSITION_INCREASE",
            quantity=1,
            price=100,
        ),
        event(
            provenance="UNKNOWN",
            event_type="POSITION_INCREASE",
            quantity=1,
            price=100,
            symbol="ETH/USDT",
        ),
    ]

    result = build_ownership_accounting(
        events,
        {
            "BTC/USDT": 105,
            "ETH/USDT": 95,
        },
    )

    assert result.ats.market_value == 0
    assert result.unattributed.market_value == 200


def test_partial_manual_exit_tracks_realized_and_remaining_position():
    events = [
        event(
            provenance="MANUAL",
            event_type="POSITION_INCREASE",
            quantity=2,
            price=100,
        ),
        event(
            provenance="MANUAL",
            event_type="POSITION_DECREASE",
            quantity=1,
            price=120,
        ),
    ]

    result = build_ownership_accounting(
        events,
        {"BTC/USDT": 130},
    )

    assert result.manual.realized_pnl == 20
    assert result.manual.market_value == 130
    assert result.manual.unrealized_pnl == 30


def test_explicit_realized_pnl_is_authoritative():
    events = [
        event(
            provenance="ATS_AUTONOMOUS",
            event_type="POSITION_INCREASE",
            quantity=1,
            price=100,
        ),
        event(
            provenance="ATS_AUTONOMOUS",
            event_type="POSITION_DECREASE",
            quantity=1,
            price=120,
            realized_pnl=17.5,
        ),
    ]

    result = build_ownership_accounting(events, {})

    assert result.ats.realized_pnl == 17.5


def test_oversell_generates_accounting_warning():
    events = [
        event(
            provenance="MANUAL",
            event_type="POSITION_INCREASE",
            quantity=1,
            price=100,
        ),
        event(
            provenance="MANUAL",
            event_type="POSITION_DECREASE",
            quantity=2,
            price=110,
        ),
    ]

    result = build_ownership_accounting(events, {})

    assert result.warnings
    assert "exceeded owned quantity" in result.warnings[0]


def test_manual_loss_cannot_reduce_ats_performance():
    events = [
        event(
            provenance="MANUAL",
            event_type="POSITION_INCREASE",
            quantity=1,
            price=100,
        ),
        event(
            provenance="MANUAL",
            event_type="POSITION_DECREASE",
            quantity=1,
            price=50,
        ),
        event(
            provenance="ATS_AUTONOMOUS",
            event_type="POSITION_INCREASE",
            quantity=1,
            price=100,
            symbol="ETH/USDT",
        ),
    ]

    result = build_ownership_accounting(
        events,
        {"ETH/USDT": 110},
    )

    assert result.manual.realized_pnl == -50
    assert result.ats.realized_pnl == 0
    assert result.ats.unrealized_pnl == 10

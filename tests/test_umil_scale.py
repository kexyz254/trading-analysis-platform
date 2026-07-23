from datetime import datetime, timezone

from services.umil.app.scale import normalize_market_scale


def test_duplicate_provider_symbols_are_quarantined() -> None:
    observed_at = datetime(2026, 7, 23, tzinfo=timezone.utc)

    rows = [
        {
            "id": "bitcoin",
            "symbol": "btc",
            "market_cap": 1000,
            "market_cap_rank": 1,
        },
        {
            "id": "dai",
            "symbol": "dai",
            "market_cap": 500,
            "market_cap_rank": 20,
        },
        {
            "id": "another-dai",
            "symbol": "DAI",
            "market_cap": 10,
            "market_cap_rank": 900,
        },
    ]

    normalized, ambiguous = normalize_market_scale(
        rows,
        observed_at=observed_at,
    )

    assert normalized["BTC"].asset_id == "bitcoin"
    assert "DAI" not in normalized
    assert ambiguous == frozenset({"DAI"})

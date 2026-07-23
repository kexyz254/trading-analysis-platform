from __future__ import annotations

from datetime import datetime, timezone

from services.umil.app.contracts import Instrument, InstrumentKind, MarketTicker


NOW = datetime(2026, 7, 22, 16, 0, tzinfo=timezone.utc)


def instrument(symbol: str, base: str, quote: str = "USDT") -> Instrument:
    return Instrument(
        instrument_id=f"binance:spot:{symbol}",
        venue="binance",
        venue_symbol=symbol,
        base_asset=base,
        quote_asset=quote,
        kind=InstrumentKind.SPOT,
        status="TRADING",
        price_precision=8,
        quantity_precision=8,
    )


def ticker(
    symbol: str,
    last: float,
    open_: float,
    volume: float,
    *,
    event_time: datetime = NOW,
) -> MarketTicker:
    return MarketTicker(
        instrument_id=f"binance:spot:{symbol}",
        venue_symbol=symbol,
        last_price=last,
        open_price_24h=open_,
        high_price_24h=max(last, open_) * 1.01,
        low_price_24h=min(last, open_) * 0.99,
        base_volume_24h=volume / max(last, 1.0),
        quote_volume_24h=volume,
        event_time=event_time,
    )


EXCHANGE_INFO = {
    "symbols": [
        {
            "symbol": "BTCUSDT", "status": "TRADING", "baseAsset": "BTC",
            "quoteAsset": "USDT", "permissions": ["SPOT"],
            "quotePrecision": 8, "baseAssetPrecision": 8,
        },
        {
            "symbol": "ETHUSDT", "status": "TRADING", "baseAsset": "ETH",
            "quoteAsset": "USDT", "permissions": ["SPOT"],
            "quotePrecision": 8, "baseAssetPrecision": 8,
        },
        {
            "symbol": "OLDUSDT", "status": "BREAK", "baseAsset": "OLD",
            "quoteAsset": "USDT", "permissions": ["SPOT"],
        },
        {
            "symbol": "BTCFDUSD", "status": "TRADING", "baseAsset": "BTC",
            "quoteAsset": "FDUSD", "permissions": ["SPOT"],
        },
    ]
}


REST_TICKERS = [
    {
        "symbol": "BTCUSDT", "lastPrice": "102", "openPrice": "100",
        "highPrice": "103", "lowPrice": "98", "volume": "1000",
        "quoteVolume": "101000", "bidPrice": "101.9", "askPrice": "102.1",
        "count": 100, "closeTime": int(NOW.timestamp() * 1000),
    },
    {
        "symbol": "ETHUSDT", "lastPrice": "49", "openPrice": "50",
        "highPrice": "51", "lowPrice": "48", "volume": "2000",
        "quoteVolume": "99000", "bidPrice": "48.9", "askPrice": "49.1",
        "count": 200, "closeTime": int(NOW.timestamp() * 1000),
    },
]

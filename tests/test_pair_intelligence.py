from datetime import datetime, timezone

import pytest

from trading_assistant.market.intelligence import (
    PairIntelligence,
    TimeframeAnalysis,
)


def test_pair_intelligence_serializes_cleanly():
    tf = TimeframeAnalysis(
        timeframe="15m",
        bias="bullish",
        confidence=0.78,
        regime="trend",
        strategy="pullback",
    )

    intelligence = PairIntelligence(
        symbol="BTC/USDT",
        timestamp=datetime.now(timezone.utc),
        price=65000.0,
        spread_pct=0.0002,
        liquidity_score=0.95,
        data_quality=1.0,
        regime="trend",
        direction="bullish",
        confidence=0.78,
        preferred_timeframe="15m",
        preferred_strategy="pullback",
        timeframes=(tf,),
        expected_rr=2.6,
    )

    data = intelligence.to_dict()

    assert data["symbol"] == "BTC/USDT"
    assert data["timeframes"][0]["timeframe"] == "15m"
    assert data["expected_rr"] == 2.6


def test_invalid_confidence_is_rejected():
    with pytest.raises(ValueError):
        TimeframeAnalysis(
            timeframe="1h",
            bias="bullish",
            confidence=1.5,
            regime="trend",
        )

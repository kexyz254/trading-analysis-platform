from datetime import datetime, timedelta, timezone

import pytest

from trading_assistant.market.tracking import TrackedPairRegistry


def test_multiple_reasons_keep_pair_tracked():
    registry = TrackedPairRegistry()

    registry.track("BTC/USDT", "selected")
    registry.track("BTC/USDT", "manual_position")

    registry.untrack("BTC/USDT", "selected")

    assert registry.is_tracked("BTC/USDT")
    assert registry.reasons("BTC/USDT") == {"manual_position"}


def test_transient_tracking_expires_but_positions_survive():
    registry = TrackedPairRegistry()

    old = datetime.now(timezone.utc) - timedelta(hours=2)

    registry.track("ETH/USDT", "selected", now=old)
    registry.track("BTC/USDT", "manual_position", now=old)

    registry.expire_transient(3600)

    assert not registry.is_tracked("ETH/USDT")
    assert registry.is_tracked("BTC/USDT")


def test_unknown_tracking_reason_is_rejected():
    registry = TrackedPairRegistry()

    with pytest.raises(ValueError):
        registry.track("BTC/USDT", "mystery")

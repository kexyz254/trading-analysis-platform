"""Execution-authority boundary for exchange access."""

from __future__ import annotations

from typing import Any


class ExchangeMutationBlocked(RuntimeError):
    pass


class ReadOnlyExchangeProxy:
    """Expose exchange reads while refusing account/order mutations."""

    _BLOCKED_PREFIXES = (
        "create_",
        "cancel_",
        "edit_",
        "withdraw",
        "transfer",
        "borrow",
        "repay",
        "set_leverage",
        "set_margin",
        "set_position",
    )

    def __init__(self, exchange: Any):
        self._exchange = exchange

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._exchange, name)

        lowered = name.lower()
        blocked = (
            lowered.startswith(self._BLOCKED_PREFIXES)
            or lowered.startswith("private")
        )

        if blocked and callable(attr):
            def deny(*args, **kwargs):
                raise ExchangeMutationBlocked(
                    f"exchange mutation {name!r} blocked: "
                    "execution_mode=manual_assist"
                )
            return deny

        return attr

"""Short-lived, tamper-evident confirmation tokens for manual orders."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any


DEFAULT_TTL_SECONDS = 60


class ConfirmationError(ValueError):
    pass


@dataclass(frozen=True)
class ConfirmedOrder:
    operator_id: str
    request_id: str
    symbol: str
    side: str
    order_type: str
    quantity: float
    limit_price: float | None
    issued_at: int
    expires_at: int
    fingerprint: str


class OrderConfirmationService:
    def __init__(self, secret: str, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        if not secret:
            raise ValueError("confirmation secret required")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")

        self.secret = secret.encode("utf-8")
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def _canonical_order(
        *,
        operator_id: str,
        request_id: str,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        limit_price: float | None,
    ) -> dict[str, Any]:
        return {
            "operator_id": operator_id,
            "request_id": request_id,
            "symbol": symbol.upper(),
            "side": side.lower(),
            "order_type": order_type.lower(),
            "quantity": float(quantity),
            "limit_price": (
                float(limit_price) if limit_price is not None else None
            ),
        }

    @classmethod
    def fingerprint(cls, **kwargs) -> str:
        canonical = cls._canonical_order(**kwargs)

        encoded = json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")

        return hashlib.sha256(encoded).hexdigest()

    def issue(self, **kwargs) -> str:
        now = int(time.time())

        canonical = self._canonical_order(**kwargs)

        payload = {
            **canonical,
            "issued_at": now,
            "expires_at": now + self.ttl_seconds,
            "fingerprint": self.fingerprint(**canonical),
        }

        body = base64.urlsafe_b64encode(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).rstrip(b"=")

        signature = hmac.new(
            self.secret,
            body,
            hashlib.sha256,
        ).digest()

        sig = base64.urlsafe_b64encode(signature).rstrip(b"=")

        return body.decode() + "." + sig.decode()

    def verify(self, token: str) -> ConfirmedOrder:
        try:
            body_text, sig_text = token.split(".", 1)
        except ValueError as exc:
            raise ConfirmationError("malformed confirmation token") from exc

        body = body_text.encode("ascii")

        expected = hmac.new(
            self.secret,
            body,
            hashlib.sha256,
        ).digest()

        padding = "=" * (-len(sig_text) % 4)

        try:
            actual = base64.urlsafe_b64decode(sig_text + padding)
        except Exception as exc:
            raise ConfirmationError("invalid confirmation signature") from exc

        if not hmac.compare_digest(actual, expected):
            raise ConfirmationError("invalid confirmation signature")

        padding = "=" * (-len(body_text) % 4)

        try:
            payload = json.loads(
                base64.urlsafe_b64decode(body_text + padding)
            )
        except Exception as exc:
            raise ConfirmationError("invalid confirmation payload") from exc

        now = int(time.time())

        if int(payload["expires_at"]) < now:
            raise ConfirmationError("confirmation token expired")

        expected_fingerprint = self.fingerprint(
            operator_id=payload["operator_id"],
            request_id=payload["request_id"],
            symbol=payload["symbol"],
            side=payload["side"],
            order_type=payload["order_type"],
            quantity=payload["quantity"],
            limit_price=payload.get("limit_price"),
        )

        if not hmac.compare_digest(
            payload["fingerprint"],
            expected_fingerprint,
        ):
            raise ConfirmationError("confirmation fingerprint mismatch")

        return ConfirmedOrder(
            operator_id=payload["operator_id"],
            request_id=payload["request_id"],
            symbol=payload["symbol"],
            side=payload["side"],
            order_type=payload["order_type"],
            quantity=float(payload["quantity"]),
            limit_price=(
                float(payload["limit_price"])
                if payload.get("limit_price") is not None
                else None
            ),
            issued_at=int(payload["issued_at"]),
            expires_at=int(payload["expires_at"]),
            fingerprint=payload["fingerprint"],
        )

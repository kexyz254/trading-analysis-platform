"""Fail-closed authority declarations for UMIL.

This module contains data, not toggles.  Authority cannot be elevated through
configuration or an API request.  A future release that changes an authority
bit must be reviewed as a governance change, not deployed as routine config.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class AuthorityContract:
    read_only: bool = True
    can_trade: bool = False
    can_cancel_orders: bool = False
    can_access_trading_credentials: bool = False
    can_train: bool = False
    can_influence_ats: bool = False
    can_modify_risk: bool = False
    can_modify_tournament: bool = False
    can_write_ats_storage: bool = False

    def validate_fail_closed(self) -> None:
        values = asdict(self)
        if values.pop("read_only") is not True or any(values.values()):
            raise RuntimeError("UMIL authority contract is not fail-closed")

    def as_dict(self) -> dict[str, bool]:
        self.validate_fail_closed()
        return asdict(self)


AUTHORITY = AuthorityContract()
AUTHORITY.validate_fail_closed()
AUTHORITY_VIEW: Mapping[str, Any] = MappingProxyType(AUTHORITY.as_dict())

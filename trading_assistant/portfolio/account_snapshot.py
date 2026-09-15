"""Reconcile live broker equity with manual/ATS ownership."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

from trading_assistant.portfolio.ownership_accounting import (
    ATS_PROVENANCE,
    MANUAL_PROVENANCE,
    UNATTRIBUTED_PROVENANCE,
    replay_owned_positions,
)


@dataclass(frozen=True)
class AccountSnapshot:
    account_id: str
    quote_currency: str

    free_quote_cash: float
    reserved_quote_cash: float
    total_quote_balance: float

    total_account_equity: Optional[float]

    manual_position_equity: float
    ats_position_equity: float
    owned_unattributed_equity: float
    residual_unattributed_equity: Optional[float]

    manual_realized_pnl: float
    ats_realized_pnl: float

    ownership_valuation_complete: bool
    unpriced_assets: Tuple[str, ...]
    warnings: Tuple[str, ...]


class AccountSnapshotService:
    def __init__(
        self,
        *,
        broker,
        catalog,
        journal,
        account_id: str = "primary",
        quote_currency: str = "USDT",
    ):
        self.broker = broker
        self.catalog = catalog
        self.journal = journal
        self.account_id = account_id
        self.quote_currency = quote_currency.upper()

    def _price(self, symbol: str) -> Optional[float]:
        try:
            ticker = self.broker.fetch_ticker(symbol)
        except Exception:
            return None

        last = ticker.get("last")
        if last is not None:
            try:
                value = float(last)
                if value > 0:
                    return value
            except (TypeError, ValueError):
                pass

        try:
            bid = float(ticker.get("bid") or 0)
            ask = float(ticker.get("ask") or 0)
            if bid > 0 and ask > 0:
                return (bid + ask) / 2
        except (TypeError, ValueError):
            pass

        return None

    def snapshot(self) -> AccountSnapshot:
        balance = self.broker.fetch_balance()

        free = balance.get("free") or {}
        total = balance.get("total") or {}

        quote = self.quote_currency

        free_quote = float(free.get(quote) or 0)
        total_quote = float(total.get(quote) or 0)
        reserved_quote = max(0.0, total_quote - free_quote)

        mark_prices = {}
        asset_prices = {}
        unpriced_assets = []

        valued_equity = total_quote

        for asset, raw_quantity in total.items():
            asset = str(asset).upper()

            try:
                quantity = float(raw_quantity or 0)
            except (TypeError, ValueError):
                continue

            if quantity <= 0 or asset == quote:
                continue

            symbol = f"{asset}/{quote}"

            if self.catalog.get(symbol) is None:
                unpriced_assets.append(asset)
                continue

            price = self._price(symbol)

            if price is None:
                unpriced_assets.append(asset)
                continue

            asset_prices[asset] = price
            mark_prices[symbol] = price
            valued_equity += quantity * price

        events = self.journal.position_ownership_events(
            self.account_id
        )

        states, warnings = replay_owned_positions(events)

        manual_equity = 0.0
        ats_equity = 0.0
        unattributed_equity = 0.0

        manual_realized = 0.0
        ats_realized = 0.0

        owned_quantities = {}
        ownership_unpriced = False

        for state in states.values():
            if state.quantity <= 0:
                if state.provenance in MANUAL_PROVENANCE:
                    manual_realized += state.realized_pnl
                elif state.provenance in ATS_PROVENANCE:
                    ats_realized += state.realized_pnl
                continue

            price = mark_prices.get(state.symbol)

            if price is None:
                ownership_unpriced = True
                warnings.append(
                    f"{state.provenance}:{state.symbol}: "
                    "live mark unavailable"
                )
                value = 0.0
            else:
                value = state.quantity * price

            if state.provenance in MANUAL_PROVENANCE:
                manual_equity += value
                manual_realized += state.realized_pnl
            elif state.provenance in ATS_PROVENANCE:
                ats_equity += value
                ats_realized += state.realized_pnl
            elif state.provenance in UNATTRIBUTED_PROVENANCE:
                unattributed_equity += value

            owned_quantities[state.symbol] = (
                owned_quantities.get(state.symbol, 0.0)
                + state.quantity
            )

        # Verify owned quantities against what the broker really holds.
        for symbol, owned_quantity in owned_quantities.items():
            market = self.catalog.get(symbol)
            if market is None:
                continue

            broker_quantity = float(
                total.get(market.base) or 0
            )

            tolerance = max(
                1e-10,
                abs(broker_quantity) * 1e-6,
            )

            if abs(broker_quantity - owned_quantity) > tolerance:
                warnings.append(
                    f"{symbol}: ownership quantity "
                    f"{owned_quantity:.12g} differs from broker "
                    f"quantity {broker_quantity:.12g}"
                )

        total_equity = (
            None
            if unpriced_assets
            else valued_equity
        )

        residual = None

        if total_equity is not None:
            residual = (
                total_equity
                - total_quote
                - manual_equity
                - ats_equity
                - unattributed_equity
            )

            if abs(residual) < 1e-10:
                residual = 0.0

        return AccountSnapshot(
            account_id=self.account_id,
            quote_currency=quote,
            free_quote_cash=free_quote,
            reserved_quote_cash=reserved_quote,
            total_quote_balance=total_quote,
            total_account_equity=total_equity,
            manual_position_equity=manual_equity,
            ats_position_equity=ats_equity,
            owned_unattributed_equity=unattributed_equity,
            residual_unattributed_equity=residual,
            manual_realized_pnl=manual_realized,
            ats_realized_pnl=ats_realized,
            ownership_valuation_complete=not ownership_unpriced,
            unpriced_assets=tuple(sorted(set(unpriced_assets))),
            warnings=tuple(warnings),
        )

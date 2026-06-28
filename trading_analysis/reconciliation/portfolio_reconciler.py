"""Portfolio reconciliation primitives for trading-analysis-platform.

The module compares expected positions, broker positions, cash ledgers,
execution fills, and settlement records. It is intentionally independent of
external services so it can be used from batch jobs, local audits, and tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


class BreakSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class BreakType(str, Enum):
    MISSING_POSITION = "missing_position"
    EXTRA_POSITION = "extra_position"
    QUANTITY_MISMATCH = "quantity_mismatch"
    CASH_MISMATCH = "cash_mismatch"
    PRICE_MISMATCH = "price_mismatch"
    SETTLEMENT_MISMATCH = "settlement_mismatch"
    DUPLICATE_RECORD = "duplicate_record"
    INVALID_RECORD = "invalid_record"


@dataclass(frozen=True)
class PositionKey:
    account_id: str
    symbol: str
    venue: str = ""

    def normalized(self) -> "PositionKey":
        return PositionKey(
            account_id=self.account_id.strip().upper(),
            symbol=self.symbol.strip().upper(),
            venue=self.venue.strip().upper(),
        )


@dataclass(frozen=True)
class Position:
    account_id: str
    symbol: str
    quantity: Decimal
    market_price: Decimal = Decimal("0")
    venue: str = ""
    currency: str = "USD"

    @property
    def key(self) -> PositionKey:
        return PositionKey(self.account_id, self.symbol, self.venue).normalized()

    @property
    def market_value(self) -> Decimal:
        return self.quantity * self.market_price


@dataclass(frozen=True)
class CashBalance:
    account_id: str
    currency: str
    amount: Decimal

    @property
    def key(self) -> Tuple[str, str]:
        return (self.account_id.strip().upper(), self.currency.strip().upper())


@dataclass(frozen=True)
class ExecutionFill:
    execution_id: str
    account_id: str
    symbol: str
    side: str
    quantity: Decimal
    price: Decimal
    fees: Decimal = Decimal("0")
    currency: str = "USD"
    venue: str = ""

    @property
    def signed_quantity(self) -> Decimal:
        side = self.side.strip().upper()
        if side in {"BUY", "B", "LONG"}:
            return self.quantity
        if side in {"SELL", "S", "SHORT"}:
            return -self.quantity
        raise ValueError(f"unsupported execution side: {self.side!r}")

    @property
    def cash_effect(self) -> Decimal:
        notional = self.quantity * self.price
        side = self.side.strip().upper()
        if side in {"BUY", "B", "LONG"}:
            return -notional - self.fees
        if side in {"SELL", "S", "SHORT"}:
            return notional - self.fees
        raise ValueError(f"unsupported execution side: {self.side!r}")


@dataclass(frozen=True)
class SettlementRecord:
    settlement_id: str
    account_id: str
    currency: str
    expected_amount: Decimal
    broker_amount: Decimal
    settlement_date: str
    source: str = ""


@dataclass
class ReconciliationBreak:
    break_type: BreakType
    severity: BreakSeverity
    key: str
    expected: str
    actual: str
    difference: str
    message: str
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class ReconciliationSummary:
    breaks: List[ReconciliationBreak] = field(default_factory=list)
    checked_positions: int = 0
    checked_cash_balances: int = 0
    checked_settlements: int = 0
    duplicate_records: int = 0
    invalid_records: int = 0

    @property
    def ok(self) -> bool:
        return not any(
            item.severity in {BreakSeverity.ERROR, BreakSeverity.CRITICAL}
            for item in self.breaks
        )

    @property
    def error_count(self) -> int:
        return sum(
            1 for item in self.breaks
            if item.severity in {BreakSeverity.ERROR, BreakSeverity.CRITICAL}
        )

    @property
    def warning_count(self) -> int:
        return sum(1 for item in self.breaks if item.severity == BreakSeverity.WARNING)


def decimal_value(value: object, default: Decimal = Decimal("0")) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if value is None:
        return default
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return default


def quantize_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def quantize_quantity(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.000001"))


def normalize_symbol(symbol: str) -> str:
    return symbol.strip().upper().replace("/", "-")


def normalize_account(account_id: str) -> str:
    return account_id.strip().upper()


def normalize_currency(currency: str) -> str:
    return currency.strip().upper() or "USD"


def position_from_mapping(row: Mapping[str, object]) -> Position:
    return Position(
        account_id=str(row.get("account_id", "")),
        symbol=normalize_symbol(str(row.get("symbol", ""))),
        quantity=decimal_value(row.get("quantity")),
        market_price=decimal_value(row.get("market_price")),
        venue=str(row.get("venue", "")),
        currency=normalize_currency(str(row.get("currency", "USD"))),
    )


def cash_from_mapping(row: Mapping[str, object]) -> CashBalance:
    return CashBalance(
        account_id=normalize_account(str(row.get("account_id", ""))),
        currency=normalize_currency(str(row.get("currency", "USD"))),
        amount=decimal_value(row.get("amount")),
    )


def fill_from_mapping(row: Mapping[str, object]) -> ExecutionFill:
    return ExecutionFill(
        execution_id=str(row.get("execution_id", "")),
        account_id=normalize_account(str(row.get("account_id", ""))),
        symbol=normalize_symbol(str(row.get("symbol", ""))),
        side=str(row.get("side", "")),
        quantity=decimal_value(row.get("quantity")),
        price=decimal_value(row.get("price")),
        fees=decimal_value(row.get("fees")),
        currency=normalize_currency(str(row.get("currency", "USD"))),
        venue=str(row.get("venue", "")),
    )


def settlement_from_mapping(row: Mapping[str, object]) -> SettlementRecord:
    return SettlementRecord(
        settlement_id=str(row.get("settlement_id", "")),
        account_id=normalize_account(str(row.get("account_id", ""))),
        currency=normalize_currency(str(row.get("currency", "USD"))),
        expected_amount=decimal_value(row.get("expected_amount")),
        broker_amount=decimal_value(row.get("broker_amount")),
        settlement_date=str(row.get("settlement_date", "")),
        source=str(row.get("source", "")),
    )


class PortfolioReconciler:
    def __init__(
        self,
        quantity_tolerance: Decimal = Decimal("0.000001"),
        cash_tolerance: Decimal = Decimal("0.01"),
        price_tolerance: Decimal = Decimal("0.0001"),
    ) -> None:
        self.quantity_tolerance = quantity_tolerance
        self.cash_tolerance = cash_tolerance
        self.price_tolerance = price_tolerance

    def reconcile_positions(
        self,
        expected_positions: Sequence[Position],
        broker_positions: Sequence[Position],
    ) -> ReconciliationSummary:
        summary = ReconciliationSummary()
        expected_index = self._index_positions(expected_positions, summary, "expected")
        broker_index = self._index_positions(broker_positions, summary, "broker")

        all_keys = sorted(
            set(expected_index.keys()) | set(broker_index.keys()),
            key=lambda item: (item.account_id, item.symbol, item.venue),
        )

        for key in all_keys:
            expected = expected_index.get(key)
            broker = broker_index.get(key)
            summary.checked_positions += 1

            if expected is None and broker is not None:
                summary.breaks.append(
                    ReconciliationBreak(
                        BreakType.EXTRA_POSITION,
                        BreakSeverity.ERROR,
                        self._position_key_text(key),
                        "missing",
                        str(broker.quantity),
                        str(broker.quantity),
                        "broker has a position that is absent from expected books",
                    )
                )
                continue

            if broker is None and expected is not None:
                summary.breaks.append(
                    ReconciliationBreak(
                        BreakType.MISSING_POSITION,
                        BreakSeverity.ERROR,
                        self._position_key_text(key),
                        str(expected.quantity),
                        "missing",
                        str(expected.quantity),
                        "expected position is absent from broker books",
                    )
                )
                continue

            assert expected is not None
            assert broker is not None

            quantity_diff = broker.quantity - expected.quantity
            if abs(quantity_diff) > self.quantity_tolerance:
                summary.breaks.append(
                    ReconciliationBreak(
                        BreakType.QUANTITY_MISMATCH,
                        BreakSeverity.ERROR,
                        self._position_key_text(key),
                        str(expected.quantity),
                        str(broker.quantity),
                        str(quantity_diff),
                        "position quantity mismatch exceeds tolerance",
                    )
                )

            price_diff = broker.market_price - expected.market_price
            if expected.market_price and abs(price_diff) > self.price_tolerance:
                summary.breaks.append(
                    ReconciliationBreak(
                        BreakType.PRICE_MISMATCH,
                        BreakSeverity.WARNING,
                        self._position_key_text(key),
                        str(expected.market_price),
                        str(broker.market_price),
                        str(price_diff),
                        "market price mismatch exceeds tolerance",
                    )
                )

        return summary

    def reconcile_cash(
        self,
        expected_balances: Sequence[CashBalance],
        broker_balances: Sequence[CashBalance],
    ) -> ReconciliationSummary:
        summary = ReconciliationSummary()
        expected_index = self._index_cash(expected_balances, summary, "expected")
        broker_index = self._index_cash(broker_balances, summary, "broker")

        all_keys = sorted(set(expected_index.keys()) | set(broker_index.keys()))

        for key in all_keys:
            expected = expected_index.get(key)
            broker = broker_index.get(key)
            summary.checked_cash_balances += 1

            expected_amount = expected.amount if expected else Decimal("0")
            broker_amount = broker.amount if broker else Decimal("0")
            diff = broker_amount - expected_amount

            if abs(diff) > self.cash_tolerance:
                severity = BreakSeverity.CRITICAL if abs(diff) > Decimal("10000") else BreakSeverity.ERROR
                summary.breaks.append(
                    ReconciliationBreak(
                        BreakType.CASH_MISMATCH,
                        severity,
                        f"{key[0]}:{key[1]}",
                        str(expected_amount),
                        str(broker_amount),
                        str(diff),
                        "cash balance mismatch exceeds tolerance",
                    )
                )

        return summary

    def reconcile_settlements(
        self,
        settlements: Sequence[SettlementRecord],
    ) -> ReconciliationSummary:
        summary = ReconciliationSummary()
        seen: set[str] = set()

        for record in settlements:
            summary.checked_settlements += 1

            if not record.settlement_id:
                summary.invalid_records += 1
                summary.breaks.append(
                    ReconciliationBreak(
                        BreakType.INVALID_RECORD,
                        BreakSeverity.ERROR,
                        record.account_id,
                        "settlement_id",
                        "missing",
                        "",
                        "settlement record has no settlement id",
                    )
                )
                continue

            if record.settlement_id in seen:
                summary.duplicate_records += 1
                summary.breaks.append(
                    ReconciliationBreak(
                        BreakType.DUPLICATE_RECORD,
                        BreakSeverity.ERROR,
                        record.settlement_id,
                        "unique",
                        "duplicate",
                        "",
                        "duplicate settlement id",
                    )
                )
                continue

            seen.add(record.settlement_id)
            diff = record.broker_amount - record.expected_amount

            if abs(diff) > self.cash_tolerance:
                summary.breaks.append(
                    ReconciliationBreak(
                        BreakType.SETTLEMENT_MISMATCH,
                        BreakSeverity.ERROR,
                        record.settlement_id,
                        str(record.expected_amount),
                        str(record.broker_amount),
                        str(diff),
                        "settlement amount mismatch exceeds tolerance",
                        {
                            "account_id": record.account_id,
                            "currency": record.currency,
                            "settlement_date": record.settlement_date,
                            "source": record.source,
                        },
                    )
                )

        return summary

    def expected_from_fills(
        self,
        opening_positions: Sequence[Position],
        fills: Sequence[ExecutionFill],
    ) -> List[Position]:
        index: Dict[PositionKey, Position] = {
            position.key: position for position in opening_positions
        }

        for fill in fills:
            key = PositionKey(fill.account_id, fill.symbol, fill.venue).normalized()
            existing = index.get(
                key,
                Position(
                    account_id=fill.account_id,
                    symbol=fill.symbol,
                    quantity=Decimal("0"),
                    market_price=fill.price,
                    venue=fill.venue,
                    currency=fill.currency,
                ),
            )

            index[key] = Position(
                account_id=existing.account_id,
                symbol=existing.symbol,
                quantity=existing.quantity + fill.signed_quantity,
                market_price=fill.price,
                venue=existing.venue,
                currency=existing.currency,
            )

        return list(index.values())

    def cash_from_fills(
        self,
        opening_cash: Sequence[CashBalance],
        fills: Sequence[ExecutionFill],
    ) -> List[CashBalance]:
        balances: Dict[Tuple[str, str], Decimal] = {
            item.key: item.amount for item in opening_cash
        }

        for fill in fills:
            key = (normalize_account(fill.account_id), normalize_currency(fill.currency))
            balances[key] = balances.get(key, Decimal("0")) + fill.cash_effect

        return [
            CashBalance(account_id=account_id, currency=currency, amount=amount)
            for (account_id, currency), amount in balances.items()
        ]

    def merge_summaries(self, *summaries: ReconciliationSummary) -> ReconciliationSummary:
        merged = ReconciliationSummary()

        for summary in summaries:
            merged.breaks.extend(summary.breaks)
            merged.checked_positions += summary.checked_positions
            merged.checked_cash_balances += summary.checked_cash_balances
            merged.checked_settlements += summary.checked_settlements
            merged.duplicate_records += summary.duplicate_records
            merged.invalid_records += summary.invalid_records

        return merged

    def _index_positions(
        self,
        positions: Sequence[Position],
        summary: ReconciliationSummary,
        source: str,
    ) -> Dict[PositionKey, Position]:
        index: Dict[PositionKey, Position] = {}

        for position in positions:
            key = position.key

            if not key.account_id or not key.symbol:
                summary.invalid_records += 1
                summary.breaks.append(
                    ReconciliationBreak(
                        BreakType.INVALID_RECORD,
                        BreakSeverity.ERROR,
                        source,
                        "valid position key",
                        str(key),
                        "",
                        "position has missing account or symbol",
                    )
                )
                continue

            if key in index:
                summary.duplicate_records += 1
                summary.breaks.append(
                    ReconciliationBreak(
                        BreakType.DUPLICATE_RECORD,
                        BreakSeverity.ERROR,
                        self._position_key_text(key),
                        "unique position",
                        "duplicate position",
                        "",
                        "duplicate position record",
                        {"source": source},
                    )
                )
                continue

            index[key] = position

        return index

    def _index_cash(
        self,
        balances: Sequence[CashBalance],
        summary: ReconciliationSummary,
        source: str,
    ) -> Dict[Tuple[str, str], CashBalance]:
        index: Dict[Tuple[str, str], CashBalance] = {}

        for balance in balances:
            key = balance.key

            if not key[0] or not key[1]:
                summary.invalid_records += 1
                summary.breaks.append(
                    ReconciliationBreak(
                        BreakType.INVALID_RECORD,
                        BreakSeverity.ERROR,
                        source,
                        "valid cash key",
                        str(key),
                        "",
                        "cash balance has missing account or currency",
                    )
                )
                continue

            if key in index:
                summary.duplicate_records += 1
                summary.breaks.append(
                    ReconciliationBreak(
                        BreakType.DUPLICATE_RECORD,
                        BreakSeverity.ERROR,
                        f"{key[0]}:{key[1]}",
                        "unique cash balance",
                        "duplicate cash balance",
                        "",
                        "duplicate cash balance record",
                        {"source": source},
                    )
                )
                continue

            index[key] = balance

        return index

    @staticmethod
    def _position_key_text(key: PositionKey) -> str:
        if key.venue:
            return f"{key.account_id}:{key.symbol}:{key.venue}"
        return f"{key.account_id}:{key.symbol}"

"""Transactional, tamper-evident and append-only evidence persistence."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from .canonical import canonical_json, sha256_hex
from .contracts import Instrument
from .versions import VERSIONS


_GENESIS_HASH = "0" * 64


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("evidence timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    evidence_id: str
    sequence: int
    event_type: str
    instrument_id: str | None
    observed_at: str
    received_at: str
    calculated_at: str
    payload: dict[str, Any]
    payload_hash: str
    previous_chain_hash: str
    chain_hash: str
    supersedes_evidence_id: str | None
    correction_reason: str | None

    def as_dict(self, *, include_payload: bool = True) -> dict[str, Any]:
        row = {
            "evidence_id": self.evidence_id,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "instrument_id": self.instrument_id,
            "observed_at": self.observed_at,
            "received_at": self.received_at,
            "calculated_at": self.calculated_at,
            "payload_hash": self.payload_hash,
            "previous_chain_hash": self.previous_chain_hash,
            "chain_hash": self.chain_hash,
            "supersedes_evidence_id": self.supersedes_evidence_id,
            "correction_reason": self.correction_reason,
        }
        if include_payload:
            row["payload"] = self.payload
        return row


class EvidenceLedger:
    """Owns the UMIL database; never opens an ATS database or mutates evidence."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        if self.database_path.name.lower() in {"analytics.db", "trading_system.db"}:
            raise ValueError("refusing to open an ATS Core database")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()
        self._verification_run_lock = threading.Lock()
        self._verification_lock = threading.Lock()
        self._verified_sequence = 0
        self._verified_chain_hash = _GENESIS_HASH
        self._verification_state = "VALID"
        self._verification_error: str | None = None
        self._verification_checked_at: str | None = None
        self._initialize()
        with self._connection() as connection:
            metadata = connection.execute(
                "SELECT last_sequence FROM ledger_metadata WHERE singleton_id = 1"
            ).fetchone()
        if metadata is not None and int(metadata["last_sequence"]) > 0:
            self._verification_state = "UNVERIFIED"

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """Provide a transaction-scoped connection and always release its handle."""
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        migration = Path(__file__).resolve().parents[1] / "migrations" / "001_append_only_evidence.sql"
        with self._connection() as connection:
            connection.executescript(migration.read_text(encoding="utf-8"))

    def append(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
        observed_at: datetime,
        received_at: datetime | None = None,
        calculated_at: datetime | None = None,
        instrument_id: str | None = None,
        supersedes_evidence_id: str | None = None,
        correction_reason: str | None = None,
    ) -> EvidenceRecord:
        if not event_type or not event_type.replace("_", "").isalnum():
            raise ValueError("event_type must be a non-empty identifier")
        if (supersedes_evidence_id is None) != (correction_reason is None):
            raise ValueError("corrections require both predecessor and reason")
        received_at = received_at or datetime.now(timezone.utc)
        calculated_at = calculated_at or received_at
        observed_text = _timestamp(observed_at)
        received_text = _timestamp(received_at)
        calculated_text = _timestamp(calculated_at)
        created_text = _timestamp(datetime.now(timezone.utc))
        payload_json = canonical_json(payload)
        payload_hash = sha256_hex(payload_json)
        evidence_id = str(uuid.uuid4())

        with self._write_lock, self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            metadata = connection.execute(
                "SELECT last_sequence, last_chain_hash FROM ledger_metadata WHERE singleton_id = 1"
            ).fetchone()
            if metadata is None:
                raise RuntimeError("evidence ledger metadata is missing")
            sequence = int(metadata["last_sequence"]) + 1
            previous_hash = str(metadata["last_chain_hash"])
            chain_material = canonical_json({
                "sequence": sequence,
                "evidence_id": evidence_id,
                "event_type": event_type,
                "instrument_id": instrument_id,
                "observed_at": observed_text,
                "received_at": received_text,
                "calculated_at": calculated_text,
                "payload_hash": payload_hash,
                "previous_chain_hash": previous_hash,
                "supersedes_evidence_id": supersedes_evidence_id,
                "correction_reason": correction_reason,
                "evidence_schema_version": VERSIONS.evidence_schema_version,
                "calculation_version": VERSIONS.calculation_version,
            })
            chain_hash = sha256_hex(chain_material)
            connection.execute(
                """
                INSERT INTO evidence_records (
                    evidence_id, sequence, event_type, instrument_id,
                    observed_at, received_at, calculated_at,
                    evidence_schema_version, calculation_version,
                    payload_json, payload_hash, previous_chain_hash, chain_hash,
                    supersedes_evidence_id, correction_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id, sequence, event_type, instrument_id,
                    observed_text, received_text, calculated_text,
                    VERSIONS.evidence_schema_version, VERSIONS.calculation_version,
                    payload_json, payload_hash, previous_hash, chain_hash,
                    supersedes_evidence_id, correction_reason, created_text,
                ),
            )
            connection.execute(
                "UPDATE ledger_metadata SET last_sequence = ?, last_chain_hash = ? WHERE singleton_id = 1",
                (sequence, chain_hash),
            )
            connection.commit()
        with self._verification_lock:
            if (
                self._verification_state == "VALID"
                and self._verified_sequence == sequence - 1
                and self._verified_chain_hash == previous_hash
            ):
                self._verified_sequence = sequence
                self._verified_chain_hash = chain_hash
                self._verification_checked_at = created_text
        return EvidenceRecord(
            evidence_id=evidence_id,
            sequence=sequence,
            event_type=event_type,
            instrument_id=instrument_id,
            observed_at=observed_text,
            received_at=received_text,
            calculated_at=calculated_text,
            payload=json.loads(payload_json),
            payload_hash=payload_hash,
            previous_chain_hash=previous_hash,
            chain_hash=chain_hash,
            supersedes_evidence_id=supersedes_evidence_id,
            correction_reason=correction_reason,
        )

    def set_projection(self, projection_key: str, evidence_id: str) -> None:
        if not projection_key:
            raise ValueError("projection_key cannot be empty")
        now = _timestamp(datetime.now(timezone.utc))
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO latest_projections (projection_key, evidence_id, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(projection_key) DO UPDATE SET
                    evidence_id = excluded.evidence_id,
                    updated_at = excluded.updated_at
                """,
                (projection_key, evidence_id, now),
            )

    def get(self, evidence_id: str) -> EvidenceRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM evidence_records WHERE evidence_id = ?", (evidence_id,)
            ).fetchone()
        return self._from_row(row) if row else None

    def latest(self, projection_key: str) -> EvidenceRecord | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT e.* FROM latest_projections p
                JOIN evidence_records e ON e.evidence_id = p.evidence_id
                WHERE p.projection_key = ?
                """,
                (projection_key,),
            ).fetchone()
        return self._from_row(row) if row else None

    def latest_as_of(
        self,
        *,
        event_type: str,
        as_of: datetime,
        instrument_id: str | None = None,
    ) -> EvidenceRecord | None:
        """Return only evidence that had been received by the requested cutoff."""
        cutoff = _timestamp(as_of)
        query = """
            SELECT * FROM evidence_records
            WHERE event_type = ? AND received_at <= ?
        """
        params: list[Any] = [event_type, cutoff]
        if instrument_id is not None:
            query += " AND instrument_id = ?"
            params.append(instrument_id)
        query += " ORDER BY received_at DESC, sequence DESC LIMIT 1"
        with self._connection() as connection:
            row = connection.execute(query, params).fetchone()
        return self._from_row(row) if row else None

    def history(
        self,
        *,
        event_type: str,
        instrument_id: str | None = None,
        before: datetime | None = None,
        limit: int = 100,
    ) -> list[EvidenceRecord]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be in [1, 1000]")
        query = "SELECT * FROM evidence_records WHERE event_type = ?"
        params: list[Any] = [event_type]
        if instrument_id is not None:
            query += " AND instrument_id = ?"
            params.append(instrument_id)
        if before is not None:
            query += " AND received_at <= ?"
            params.append(_timestamp(before))
        query += " ORDER BY sequence DESC LIMIT ?"
        params.append(limit)
        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._from_row(row) for row in rows]

    def latest_per_instrument(
        self, *, event_type: str, limit: int = 500, as_of: datetime | None = None,
    ) -> list[EvidenceRecord]:
        if not 1 <= limit <= 5000:
            raise ValueError("limit must be in [1, 5000]")
        cutoff_clause = " AND received_at <= ?" if as_of is not None else ""
        params: list[Any] = [event_type]
        if as_of is not None:
            params.append(_timestamp(as_of))
        params.append(limit)
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT e.* FROM evidence_records e
                JOIN (
                    SELECT instrument_id, MAX(sequence) AS max_sequence
                    FROM evidence_records
                    WHERE event_type = ? AND instrument_id IS NOT NULL
                    %s
                    GROUP BY instrument_id
                ) latest ON latest.max_sequence = e.sequence
                ORDER BY e.sequence DESC
                LIMIT ?
                """ % cutoff_clause,
                params,
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def replace_universe_projection(
        self,
        instruments: Iterable[Instrument],
        source_evidence_id: str,
        observed_at: datetime,
    ) -> None:
        timestamp = _timestamp(observed_at)
        rows = tuple(instruments)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for instrument in rows:
                connection.execute(
                    """
                    INSERT INTO universe_instruments (
                        instrument_id, venue, venue_symbol, base_asset, quote_asset,
                        kind, status, price_precision, quantity_precision,
                        source_evidence_id, first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(instrument_id) DO UPDATE SET
                        status = excluded.status,
                        price_precision = excluded.price_precision,
                        quantity_precision = excluded.quantity_precision,
                        source_evidence_id = excluded.source_evidence_id,
                        last_seen_at = excluded.last_seen_at
                    """,
                    (
                        instrument.instrument_id, instrument.venue,
                        instrument.venue_symbol, instrument.base_asset,
                        instrument.quote_asset, instrument.kind.value,
                        instrument.status, instrument.price_precision,
                        instrument.quantity_precision, source_evidence_id,
                        timestamp, timestamp,
                    ),
                )
            connection.commit()

    def list_instruments(
        self,
        *,
        quote_asset: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be in [1, 500]")
        if offset < 0:
            raise ValueError("offset cannot be negative")
        query = "SELECT * FROM universe_instruments"
        params: list[Any] = []
        if quote_asset:
            query += " WHERE quote_asset = ?"
            params.append(quote_asset.upper())
        query += " ORDER BY quote_asset, venue_symbol LIMIT ? OFFSET ?"
        params.extend((limit, offset))
        with self._connection() as connection:
            rows = connection.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def verification_status(self) -> dict[str, Any]:
        """Return the last known verification state without touching SQLite."""
        with self._verification_lock:
            state = self._verification_state
            return {
                "state": state,
                "valid": True if state == "VALID" else False if state == "INVALID" else None,
                "error": self._verification_error,
                "verified_sequence": self._verified_sequence,
                "verified_chain_hash": self._verified_chain_hash,
                "checked_at": self._verification_checked_at,
            }

    def _set_verification_result(
        self,
        *,
        state: str,
        error: str | None,
        sequence: int,
        chain_hash: str,
    ) -> None:
        with self._verification_lock:
            self._verification_state = state
            self._verification_error = error
            self._verified_sequence = sequence
            self._verified_chain_hash = chain_hash
            self._verification_checked_at = _timestamp(datetime.now(timezone.utc))

    def verify_chain(
        self,
        *,
        full: bool = True,
        batch_size: int = 64,
    ) -> tuple[bool, str | None]:
        """Verify the append-only chain in bounded batches.

        Incremental checks resume from the last verified sequence.  A full check
        restarts at genesis and is intended for controlled background audits.
        No readiness request should call this method directly.
        """
        if not 16 <= batch_size <= 4096:
            raise ValueError("batch_size must be in [16, 4096]")

        with self._verification_run_lock:
            with self._verification_lock:
                cached_state = self._verification_state
                cached_sequence = self._verified_sequence
                cached_hash = self._verified_chain_hash
                self._verification_state = "VERIFYING"
                self._verification_error = None

            if full or cached_state not in {"VALID", "VERIFYING"}:
                expected_sequence = 1
                expected_previous = _GENESIS_HASH
            else:
                expected_sequence = cached_sequence + 1
                expected_previous = cached_hash

            try:
                with self._connection() as connection:
                    metadata = connection.execute(
                        "SELECT last_sequence, last_chain_hash "
                        "FROM ledger_metadata WHERE singleton_id = 1"
                    ).fetchone()
                if metadata is None:
                    error = "evidence ledger metadata is missing"
                    self._set_verification_result(
                        state="INVALID", error=error, sequence=0, chain_hash=_GENESIS_HASH
                    )
                    return False, error

                target_sequence = int(metadata["last_sequence"])
                target_hash = str(metadata["last_chain_hash"])

                while expected_sequence <= target_sequence:
                    with self._connection() as connection:
                        cursor = connection.execute(
                            "SELECT * FROM evidence_records "
                            "WHERE sequence >= ? AND sequence <= ? "
                            "ORDER BY sequence LIMIT ?",
                            (expected_sequence, target_sequence, batch_size),
                        )
                        rows = cursor.fetchmany(batch_size)
                    if not rows:
                        error = f"sequence gap at {expected_sequence}"
                        self._set_verification_result(
                            state="INVALID", error=error,
                            sequence=expected_sequence - 1, chain_hash=expected_previous,
                        )
                        return False, error

                    for row in rows:
                        if int(row["sequence"]) != expected_sequence:
                            error = f"sequence gap at {expected_sequence}"
                            self._set_verification_result(
                                state="INVALID", error=error,
                                sequence=expected_sequence - 1, chain_hash=expected_previous,
                            )
                            return False, error
                        if row["previous_chain_hash"] != expected_previous:
                            error = f"previous hash mismatch at {expected_sequence}"
                            self._set_verification_result(
                                state="INVALID", error=error,
                                sequence=expected_sequence - 1, chain_hash=expected_previous,
                            )
                            return False, error
                        if sha256_hex(row["payload_json"]) != row["payload_hash"]:
                            error = f"payload hash mismatch at {expected_sequence}"
                            self._set_verification_result(
                                state="INVALID", error=error,
                                sequence=expected_sequence - 1, chain_hash=expected_previous,
                            )
                            return False, error
                        material = canonical_json({
                            "sequence": int(row["sequence"]),
                            "evidence_id": row["evidence_id"],
                            "event_type": row["event_type"],
                            "instrument_id": row["instrument_id"],
                            "observed_at": row["observed_at"],
                            "received_at": row["received_at"],
                            "calculated_at": row["calculated_at"],
                            "payload_hash": row["payload_hash"],
                            "previous_chain_hash": row["previous_chain_hash"],
                            "supersedes_evidence_id": row["supersedes_evidence_id"],
                            "correction_reason": row["correction_reason"],
                            "evidence_schema_version": row["evidence_schema_version"],
                            "calculation_version": row["calculation_version"],
                        })
                        if sha256_hex(material) != row["chain_hash"]:
                            error = f"chain hash mismatch at {expected_sequence}"
                            self._set_verification_result(
                                state="INVALID", error=error,
                                sequence=expected_sequence - 1, chain_hash=expected_previous,
                            )
                            return False, error
                        expected_previous = str(row["chain_hash"])
                        expected_sequence += 1

                verified_sequence = expected_sequence - 1
                if verified_sequence != target_sequence:
                    error = f"sequence gap at {expected_sequence}"
                    self._set_verification_result(
                        state="INVALID", error=error, sequence=verified_sequence,
                        chain_hash=expected_previous,
                    )
                    return False, error
                if expected_previous != target_hash:
                    error = f"ledger metadata hash mismatch at {target_sequence}"
                    self._set_verification_result(
                        state="INVALID", error=error, sequence=verified_sequence,
                        chain_hash=expected_previous,
                    )
                    return False, error

                self._set_verification_result(
                    state="VALID", error=None, sequence=target_sequence, chain_hash=target_hash
                )
                return True, None
            except Exception as exc:  # operational failure is not proof of tampering
                error = f"verification failed: {type(exc).__name__}: {exc}"
                with self._verification_lock:
                    self._verification_state = "ERROR"
                    self._verification_error = error
                    self._verification_checked_at = _timestamp(datetime.now(timezone.utc))
                return False, error

    @staticmethod
    def _from_row(row: sqlite3.Row) -> EvidenceRecord:
        return EvidenceRecord(
            evidence_id=row["evidence_id"], sequence=int(row["sequence"]),
            event_type=row["event_type"], instrument_id=row["instrument_id"],
            observed_at=row["observed_at"], received_at=row["received_at"],
            calculated_at=row["calculated_at"], payload=json.loads(row["payload_json"]),
            payload_hash=row["payload_hash"], previous_chain_hash=row["previous_chain_hash"],
            chain_hash=row["chain_hash"], supersedes_evidence_id=row["supersedes_evidence_id"],
            correction_reason=row["correction_reason"],
        )

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA synchronous = FULL;

CREATE TABLE IF NOT EXISTS ledger_metadata (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    last_sequence INTEGER NOT NULL CHECK (last_sequence >= 0),
    last_chain_hash TEXT NOT NULL CHECK (length(last_chain_hash) = 64)
);

INSERT OR IGNORE INTO ledger_metadata
    (singleton_id, last_sequence, last_chain_hash)
VALUES
    (1, 0, '0000000000000000000000000000000000000000000000000000000000000000');

CREATE TABLE IF NOT EXISTS evidence_records (
    evidence_id TEXT PRIMARY KEY,
    sequence INTEGER NOT NULL UNIQUE CHECK (sequence > 0),
    event_type TEXT NOT NULL,
    instrument_id TEXT,
    observed_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    calculated_at TEXT NOT NULL,
    evidence_schema_version TEXT NOT NULL,
    calculation_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL CHECK (length(payload_hash) = 64),
    previous_chain_hash TEXT NOT NULL CHECK (length(previous_chain_hash) = 64),
    chain_hash TEXT NOT NULL UNIQUE CHECK (length(chain_hash) = 64),
    supersedes_evidence_id TEXT REFERENCES evidence_records(evidence_id),
    correction_reason TEXT,
    created_at TEXT NOT NULL,
    CHECK (
        (supersedes_evidence_id IS NULL AND correction_reason IS NULL)
        OR
        (supersedes_evidence_id IS NOT NULL AND length(correction_reason) > 0)
    )
);

CREATE INDEX IF NOT EXISTS evidence_event_sequence_idx
    ON evidence_records(event_type, sequence DESC);
CREATE INDEX IF NOT EXISTS evidence_instrument_sequence_idx
    ON evidence_records(instrument_id, sequence DESC);

CREATE TRIGGER IF NOT EXISTS evidence_records_no_update
BEFORE UPDATE ON evidence_records
BEGIN
    SELECT RAISE(ABORT, 'evidence records are append-only');
END;

CREATE TRIGGER IF NOT EXISTS evidence_records_no_delete
BEFORE DELETE ON evidence_records
BEGIN
    SELECT RAISE(ABORT, 'evidence records are append-only');
END;

CREATE TABLE IF NOT EXISTS latest_projections (
    projection_key TEXT PRIMARY KEY,
    evidence_id TEXT NOT NULL REFERENCES evidence_records(evidence_id),
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS universe_instruments (
    instrument_id TEXT PRIMARY KEY,
    venue TEXT NOT NULL,
    venue_symbol TEXT NOT NULL,
    base_asset TEXT NOT NULL,
    quote_asset TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    price_precision INTEGER,
    quantity_precision INTEGER,
    source_evidence_id TEXT NOT NULL REFERENCES evidence_records(evidence_id),
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE (venue, kind, venue_symbol)
);

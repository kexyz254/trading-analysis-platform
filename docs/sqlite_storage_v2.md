# SQLite storage v2

Production must mount the trading journal as a **directory**, never as an
individual SQLite database file.

SQLite WAL mode stores committed state across three neighbouring files:
`trading_system.db`, `trading_system.db-wal`, and `trading_system.db-shm`.
A single-file Docker bind mount shares only the main database inode; each
container can otherwise receive private sidecars in its overlay filesystem.
That layout is unsafe for an engine writer plus dashboard reader and makes
schema-changing deployments difficult to verify and roll back reliably.

The supported layout is:

- host: `./trading_data/`
- engine: `/app/trading_data` read/write
- dashboard: `/app/trading_data` read-only
- database: `/app/trading_data/trading_system.db`
- runtime path variable: `ATS_TRADING_DB_PATH`

The dashboard additionally opens the journal with SQLite `mode=ro` and
`PRAGMA query_only=ON`. Therefore read-only behaviour is enforced both by the
container mount and SQLite itself.

## Migration rule

A production migration must first verify that the circuit breaker is engaged,
there are no local positions, there are no exchange open orders or locked
balances, and a consistent backup has passed `PRAGMA quick_check`. The engine
and dashboard are then stopped, the stopped engine's main database and any WAL
sidecars are captured as one set, and SQLite's backup API produces the new
`trading_data/trading_system.db`. The old database is retained as recovery
evidence; it is never deleted as part of the migration.

## Deployment rule

Before any image build that may replace a mutable `latest` tag, the currently
running engine and dashboard image IDs must be assigned immutable rollback
tags. A deployment is successful only after the engine emits a new heartbeat,
the dashboard becomes healthy, both containers show the directory mount, and
the database passes a read-only quick check from the engine container.

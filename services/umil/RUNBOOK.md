# UMIL v1.5.5 local-first acceptance runbook

Do not merge or connect UMIL to ATS during this runbook. Local acceptance tests
the standalone product and its isolation boundary.

## Phase 1 — clean-room package verification

1. Extract the final archive into a new directory.
2. Verify its checksum manifest before installing dependencies.
3. Confirm it contains no `.env`, database, cache, trading credentials, ATS
   execution code, or generated evidence.
4. Compile all UMIL and consumer-validator modules.
5. Run the complete unit and architecture suite.

## Phase 2 — isolated startup

1. Keep ATS on port 8000 unchanged.
2. Create a dedicated UMIL virtual environment.
3. remove all trading secrets from that process environment.
4. Point `UMIL_DATABASE_PATH` at a new UMIL-only file.
5. Start UMIL in the foreground on `127.0.0.1:8100`.
6. Open `http://127.0.0.1:8100/dashboard/`.

## Phase 3 — live provider acceptance

Verify:

- liveness responds immediately;
- readiness progresses through honest warm-up states;
- Binance metadata and ticker bootstrap establish non-zero membership;
- WebSocket provider state reaches `LIVE`;
- advancers + decliners + unchanged equals observed instruments;
- stale observations disappear from breadth rather than becoming unchanged;
- the all-market table exposes a `universe_state` for every fresh instrument;
- focus symbols progressively receive Market DNA;
- market-cap evidence reports provider age and ambiguous identities;
- derivatives failures degrade only derivatives evidence;
- no output invents a directional probability.

## Phase 4 — full tier rotation and capacity

Observe at least one complete scheduler rotation. Record:

- eligible symbols and rich snapshots produced;
- provider calls and HTTP failures;
- p50/p95/p99 cycle latency;
- memory and CPU high-water marks;
- SQLite growth per hour;
- ticker freshness distribution;
- oldest rich-intelligence age per tier;
- focus, active, and universe completion rates.

The scheduler must not retry one failing symbol tightly or starve the long-tail
universe. Raise batch size only after provider-budget and latency evidence
supports it.

## Phase 5 — replay and lineage

For several assets:

1. Save the exact current `as_of`, snapshot ID, and evidence record ID.
2. Query the historical endpoint using a cutoff before the snapshot; it must not
   return that future knowledge.
3. Query at or after `received_at`; it must return the correct snapshot.
4. Trace its raw input, feature, microstructure, and watch evidence.
5. Run evidence-chain verification.
6. Attempt update and delete operations against a test database; both must fail.

## Phase 6 — chaos and recovery

Test controlled failures one at a time:

| Failure | Required result |
|---|---|
| Initial REST failure | `UNAVAILABLE`/`DEGRADED`, bounded retry, no ATS effect |
| Ticker WebSocket loss | recovery counted, stale state visible, reconnect with jitter |
| Out-of-order ticker | rejected and counted |
| Candle endpoint failure | affected instrument remains warming/degraded |
| Order-book failure | pressure/liquidity depth absent, never forward-filled |
| Futures failure | spot intelligence remains available |
| Scale-provider failure | cap/FDV stale or unavailable; exchange state remains live |
| Corrupted evidence row | health becomes `INVALID` |
| UMIL termination | ATS remains unchanged |

## Phase 7 — non-interference proof

Capture ATS values before, during, and after UMIL runtime:

- tournament cycle and decision counts;
- Price Call settlement counts;
- economic-policy state;
- risk state;
- exchange mode and execution lock;
- CPU, memory, and event-loop latency.

UMIL passes only if no ATS state, timing, storage, or authority changes.

## Integration gate

Only after all phases pass may a separate change add the consumer-owned
validator to an ATS research display. Initial consumption remains optional,
read-only, and fail-open for ATS operation: invalid UMIL data is quarantined,
and ATS continues normally.

Production hosting remains a later gate. It requires PostgreSQL/object-storage
migration, authentication, quotas, tenant isolation, telemetry, backups,
licensing review, and gateway policy. Local success does not authorize public
deployment or trading influence.

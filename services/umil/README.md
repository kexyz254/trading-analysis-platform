# Universe Market Intelligence Layer v1.5.5

UMIL is a standalone, public-data-only financial intelligence service. It
interprets an exchange universe in real time, preserves exactly what it knew at
each point in time, and exposes intelligence independently from ATS. It is not
a strategy, signal generator, execution component, or required ATS dependency.

## Product capabilities

- Live discovery and one-second lightweight interpretation for every eligible
  Binance Spot symbol in the configured quote universe.
- Quality-aware breadth, volume concentration, market posture, and cross-market
  anomaly discovery.
- Rate-limited `UNIVERSE`, `ACTIVE`, and `FOCUS` coverage tiers designed to
  remain credible at 1,000+ instruments.
- Closed-candle-only 5m, 15m, 1h, 4h, and 1d deterministic features.
- Eight-dimensional Market DNA: trend, momentum, pressure, structure,
  volatility, liquidity, activity, and risk.
- Point-in-time robust calibration against the asset's own prior observations
  and the cross-sectional peer universe, with explicit warm-up counts.
- Taker flow, CVD, depth imbalance, spread, slippage, and resilience for focus
  markets. Missing flow and book evidence is never forward-filled.
- Public perpetual context: funding, basis, open-interest change, and crowding.
- Supply-aware market capitalization, FDV, circulating supply, and rank with
  ambiguous symbol identities visibly quarantined.
- Dynamic range-derived watch conditions with creation, trigger, invalidation,
  expiry, and source-evidence lineage.
- Deterministic commentary whose claims reference approved inputs and evidence;
  profitability promises and unsupported claims fail validation.
- A hash-chained, database-enforced append-only Evidence Registry containing
  raw input batches, features, microstructure, watch transitions, and final
  intelligence snapshots.
- Historical queries based on `received_at`, answering what UMIL actually knew
  by a requested time without late-data leakage.
- A standalone read-only dashboard at `/dashboard/` and a consumer-owned trust
  validator that imports no UMIL server internals.

## Trust and authority

ATS must treat UMIL as an untrusted advisory service. UMIL may be unavailable,
stale, malformed, or incompatible. Every future ATS display response must pass
the independent validator in `services.umil_client`.

UMIL cannot:

- read trading credentials;
- submit, modify, or cancel orders;
- access ATS databases;
- change risk, economic, or tournament policy;
- train ATS models;
- influence ATS decisions; or
- write to ATS storage.

The process refuses to start when known exchange trading credentials are
present. Architecture tests reject ATS/execution imports and order methods.

## Two-speed universe model

“All markets” does not mean opening deep books and five candle histories for
1,000 symbols every minute. That would violate provider budgets and create an
unreliable system.

Every eligible symbol receives `universe_state` from the all-market stream:
24-hour trend, range position, volatility, relative activity, liquidity when
available, anomaly score, freshness, and missing-dimension declarations.

The tier scheduler progressively produces full Market DNA:

| Tier | Coverage | Evidence |
|---|---|---|
| `UNIVERSE` | Every symbol | ticker, return, range, volume percentile, freshness |
| `ACTIVE` | Liquid rotating set | multi-timeframe closed candles and full features |
| `FOCUS` | Top liquidity, viewed, or anomalous | trades, CVD, depth, slippage, derivatives |

A missing rich dimension is reported as `WARMING`; it is never converted to a
neutral market score.

## Local installation — only after package acceptance

UMIL must use a separate environment and database from ATS:

```powershell
py -3.11 -m venv .venv-umil
& .\.venv-umil\Scripts\python.exe -m pip install -r services\umil\requirements.txt

Remove-Item Env:BINANCE_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:BINANCE_API_SECRET -ErrorAction SilentlyContinue
Remove-Item Env:EXCHANGE_API_KEY -ErrorAction SilentlyContinue
Remove-Item Env:EXCHANGE_API_SECRET -ErrorAction SilentlyContinue

$env:UMIL_DATABASE_PATH = "C:\Users\jeuri\trading-analysis-platform\services\umil\var\umil.db"
$env:UMIL_BIND_HOST = "127.0.0.1"
$env:UMIL_BIND_PORT = "8100"
$env:UMIL_QUOTE_ASSETS = "USDT"
$env:UMIL_INTELLIGENCE_BATCH_SIZE = "2"
$env:UMIL_INTELLIGENCE_REFRESH_SECONDS = "60"
$env:UMIL_ENABLE_SCALE_PROVIDER = "true"

& .\.venv-umil\Scripts\python.exe -m uvicorn `
    services.umil.app.main:app `
    --host 127.0.0.1 `
    --port 8100 `
    --log-level info
```

ATS remains separate on port 8000. Do not add UMIL to `webapp.server`, its
lifespan, its environment, or its database.

## Product API

```text
GET /health/live
GET /health/ready
GET /api/v1/authority
GET /api/v1/market-universe
GET /api/v1/market-intelligence
GET /api/v1/market-intelligence/{symbol}
GET /api/v1/market-dna/{symbol}
GET /api/v1/market-insights/{symbol}
GET /api/v1/market-breadth
GET /api/v1/market-anomalies
GET /api/v1/market-scale
GET /api/v1/market-scale/{symbol}
GET /api/v1/historical/intelligence/{symbol}?as_of=...
GET /api/v1/evidence/{evidence_id}
GET /dashboard/
```

Non-read methods return HTTP 405. Responses disable browser caching and include
a fail-closed authority object. External commercialization should add gateway
authentication, quotas, tenant isolation, and contractual data entitlements;
those controls must remain outside ATS Core.

## Verification

```powershell
& .\.venv-umil\Scripts\python.exe -m compileall -q services\umil services\umil_client
& .\.venv-umil\Scripts\python.exe -m unittest discover -s services\umil\tests -t . -v
```

The service is not ready for ATS integration merely because unit tests pass.
Local acceptance must also verify live provider recovery, full-universe
freshness, a complete tier rotation, evidence-chain validity, resource use,
historical replay, dashboard behavior, and zero change to ATS cycles.

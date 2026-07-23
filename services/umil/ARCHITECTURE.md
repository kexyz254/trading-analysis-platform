# UMIL v1.5.5 architecture baseline

## Boundary

UMIL is a second process, environment, database, provider client, scheduler,
dashboard, and API. It has no imports from `trading_assistant` or `webapp` and
no reverse control path into ATS.

```text
public market providers
        |
        v
universe + tiered ingestion
        |
        v
validated point-in-time observations
        |
        +--> all-symbol lightweight states
        |
        +--> features --> robust normalization --> Market DNA
        |                         |
        +--> flow/book/derivatives+
                                  |
                    immutable Evidence Registry
                                  |
                watches + governed explanation
                                  |
                  read-only API and dashboard
                                  |
                    untrusted network boundary
                                  |
              consumer-owned validating adapter
                                  |
                  future research display only
```

## Proprietary intelligence layer

Market DNA is not a static indicator average. Each dimension stores its raw
score, time-series percentile, cross-sectional percentile, history count, peer
count, normalization state, confidence, input names, and evidence IDs.

Calibration uses only observations whose `received_at` is not later than the
snapshot cutoff. Winsorization and median-absolute-deviation scaling limit
outlier influence. When history or peer coverage is insufficient, the raw
deterministic score remains visible and normalization reports `WARMING`.

`RSK` is adverse-oriented: higher means riskier. Volatility measures intensity,
not opportunity. No DNA value is represented as expected return, probability,
or trading advice.

## Evidence and historical replay

The registry is append-only at the database level. Each record includes a
monotonic sequence, payload hash, prior chain hash, current chain hash,
observation time, receipt time, calculation time, versions, and correction
lineage. SQLite triggers reject update and delete operations.

An intelligence cycle appends:

1. `MARKET_INPUT_BATCH` with the exact candles, trades, and book used.
2. `FEATURE_OBSERVATION` referencing that input batch.
3. `MICROSTRUCTURE_OBSERVATION` when flow/book evidence exists.
4. Watch creation or transition evidence when state changes.
5. `MARKET_INTELLIGENCE_SNAPSHOT` containing DNA, quality, explanations, and
   references to the exact source records.

Historical lookup filters on `received_at <= requested_as_of`. Late-arriving
data cannot appear inside an earlier knowledge state. Corrections append new
records and never rewrite what a user previously saw.

## Scale model

The one-second all-market stream supplies every eligible symbol. Deep analysis
is allocated by a deterministic tier manager. Provider or symbol failure is
isolated and recorded per instrument. A failed focus refresh cannot kill the
universe feed or breadth calculation.

SQLite is for single-process localhost validation. Multi-instance production
requires PostgreSQL append-only policies, partitioned evidence tables, object
storage for large raw replay payloads, a bounded cache, and a gateway. The
semantic contracts and hashes must remain unchanged during that migration.

## Independent contracts

The following evolve independently: service, API, snapshot, feature, DNA,
commentary, calculation, normalization, quality, and evidence schemas. The
consumer maintains an explicit compatibility allowlist and never imports
server models.

## Fail-closed rules

- Stale observations are excluded, not made neutral.
- Missing values are not replaced by zero.
- Flow and books are never forward-filled.
- Open candles never enter deterministic features.
- Ambiguous market-cap symbol identity remains visibly ambiguous.
- Unsupported commentary claims are rejected.
- Directional probability remains `null` without a separately calibrated model.
- UMIL failure cannot affect ATS startup, risk, tournament, or execution.

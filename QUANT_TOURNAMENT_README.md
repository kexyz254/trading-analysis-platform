# ATS Learning Verification and Observability v1.3.1

## v1.3.1 closed-gap patch

This release is an additive observability and correctness overlay on the
verified v1.3.0 evidence policy. It does not change signal thresholds, cost
assumptions, bankroll rules, promotion gates, execution permissions, or any
historical row.

- The former generic green `Learning` light is replaced by independent
  `Legacy ML`, `Forecast ML`, and `Tournament ML` states backed by actual
  training, inference, feature, model-identity, and feedback evidence.
- Tournament ML now distinguishes `MODEL_WARMING` from
  `MODEL_READY_LOW_EDGE`; a neutral trained model is no longer reported as
  not ready.
- Every newly trained rolling tournament model records an opaque SHA-256
  fingerprint, training timestamp, training-window boundaries, sample count,
  and label balance. Raw weights and training rows remain server-side.
- Tournament reports include safe normalized feature importance, last
  processed cycle, last inference, and settled-feedback counts. Missing model
  identity for a ready model degrades integrity health.
- Primary forecast-lead statistics now use primary observations only. Late
  calls remain preserved and classified but cannot distort the primary lead
  median or percentiles.
- Next-3-Candles responses identify their training instance and explicitly
  declare `DASHBOARD_ANALYTICS_ONLY`, closed-candle-only operation, and zero
  execution or promotion authority.
- `/api/pro/learning-health/{symbol}` is read-only and never triggers training.
  It reports the three paths separately and reserves zero authority for the
  future chart interpreter.
- The legacy feature endpoint is explicitly labelled
  `LEGACY_CHAMPION_SHAP`; absence of legacy SHAP data can no longer be confused
  with absence of tournament ML features.

The authority boundary is:

```text
legacy LearningAgent -> existing validated execution path
forecast ML          -> dashboard analytics only
tournament ML        -> shadow tournament evidence and promotion gates only
future interpreter   -> explanation only; no execution or promotion authority
```

Deployment is an in-place file overlay followed by one service restart. Schema
changes are additive `ALTER TABLE ... ADD COLUMN` operations. Do not delete the
database, reset ledgers, replay old decisions, or clear model history.

## Preserved v1.3.0 economic evidence contract

## v1.3.0 primary economic evidence contract

This release adds a point-in-time eligibility boundary on top of the verified
v1.2.1 market-state and tournament architecture. It does not delete, reset,
backfill, or rewrite any existing database row.

- Every new call records its actual issuance delay, remaining forecast lead
  time, lead-time ratio, evidence-policy version, and timing class.
- A decision issued within 10 seconds of its exchange-cycle boundary is
  `BOUNDARY_ISSUED`. A later decision remains preserved but is classified
  `LATE_ISSUED` and cannot enter the primary benchmark.
- Invalid, expired, or misaligned timing fails closed.
- Existing v1.2.1 calls and candidate records remain byte-for-byte preserved
  as pre-policy evidence. They remain part of historical integrity totals but
  cannot be retroactively promoted into the v1.3 primary sample.
- Signal accuracy and simulated exposure are separate. A boundary-issued
  directional forecast remains measurable, while simulated exposure is opened
  only when a confidence-adjusted, point-in-time movement estimate clears
  modeled round-trip fees and slippage with a safety margin.
- The movement estimate consumes only ticks at or before the decision
  timestamp. A regression verifies that later ticks cannot change it.
- `NO_TRADE` is the required result when timing is ineligible or the economic
  cost hurdle is not cleared. No threshold is loosened to create activity.
- Candidate decisions inherit the same evidence-policy classification as their
  price-call cycle. A late or pre-policy cycle cannot count as an eligible
  tournament trade or advance promotion gates.
- Official close delivery is independent of forecast timing. Stream,
  REST-recovery, and restart-reconciliation evidence are reported separately;
  a REST-recovered official close remains valid when its original decision was
  boundary-issued.
- Primary probability quality is compared with both a 50/50 baseline and a
  Laplace-smoothed prior-drift baseline. The drift forecast uses only outcomes
  settled before the observation being scored.
- Aggregate primary gross return, modeled costs, net return, participation,
  abstention, lead time, and provenance are available in the Price Calls
  workspace. Per-decision movement estimates, cost hurdles, model versions,
  feature inputs, and tournament weights remain outside the browser contract.
- Tournament Health verifies that all thirteen decisions share their parent
  call's evidence version, timing class, and primary-eligibility flag. A broken
  or missing policy link changes health to `DEGRADED`.

The v1.3 evidence boundary is:

```text
all prospective cycles
  -> preserved integrity history

boundary-issued cycles only
  -> primary signal benchmark
  -> probability baselines
  -> tournament promotion sample

primary cycles that also clear modeled friction
  -> simulated economic exposure
```

Settlement recovery never repairs or upgrades forecast timing. It can recover
only the official close for a decision whose original timestamp remains
immutable.

## v1.2.1 Windows reliability correction

- SQLite connections are explicitly closed after every transaction and query.
- Temporary database tests use explicit close semantics before directory cleanup.
- Regression coverage verifies both write and read connections are unusable
  after their context exits.
- The market-state and tournament evidence versions remain v1.2.0 because this
  maintenance release does not change signals, classifications, or accounting.

## v1.2.0 point-in-time market intelligence

This release adds a versioned, append-only Market State layer to the verified
v1.1.1 price-call and strategy-tournament architecture.

- Binance REST warms 5m and 15m indicators from fully closed candles before
  the live stream starts.
- Warm-up candles never create candidate decisions, settlements, trades,
  equity, rankings, or historical tournament credit.
- Every new prospective decision set references one immutable market-state
  snapshot containing its state version, readiness, regime, confidence, and
  candle-integrity result.
- Point-in-time queries use only candles closed before the cycle and observed
  no later than the decision timestamp.
- Missing, stale, misaligned, invalid, or conflicting cycles fail closed.
- Stream reconnects repeat the idempotent REST warm-up so gaps are repaired
  before new decisions are made.
- An interrupted candidate settlement is completed after restart from the
  already-persisted official candle, never from a substituted price.
- Candidate decision inputs are first committed to a same-database
  transactional outbox. Interrupted decision batches retry idempotently, and
  live order-book/tick payloads are scrubbed immediately after all thirteen
  decision records are verified.
- Structured `NO_TRADE` categories are stored internally; only coarse
  aggregated categories reach the dashboard.
- Raw features, thresholds, model weights, decision reasons, and candle rows
  remain server-side.
- All professional API failures now return a generic external error while
  complete diagnostics remain in protected server logs.

The cross-engine contract for each new cycle is:

```text
one Binance cycle
  = one point-in-time market-state snapshot
  = thirteen immutable candidate decisions
  = thirteen final outcomes after the official close
```

Cycles created before v1.2.0 remain immutable and are labelled as preserved
pre-market-state evidence. They are not rewritten or falsely backfilled.

## v1.1.1 verified cycle-accounting refinement

Tournament Health now separates recorded, settled, finalized, and active
cycles. Decision-record totals are explicitly labelled as including active
cycles, and finalized cycles are reconciled against their expected outcome
records. This removes the apparent mismatch between completed cycles and the
larger decision total while preserving the same immutable evidence.

The health state becomes `DEGRADED` if any finalized cycle is missing one or
more candidate outcomes, in addition to the existing missing-decision and
duplicate-record checks.

The chart runtime is now pinned locally at Lightweight Charts 4.2.0 rather
than loaded from a public CDN. Its upstream license is shipped with the asset.
The research surface also applies a restrictive Content Security Policy plus
same-origin opener and resource policies, preventing unapproved third-party
script loading and passive cross-origin embedding.

## v1.1.0 professional research workspace

This release keeps the verified v1.0.1 tournament engine and adds a
professional information architecture around it:

- dedicated Overview, Price Calls, Tournament, Research, and Execution tabs;
- a live Tournament Health contract for expected, actual, missing, duplicate,
  settled, voided, and pending candidate records;
- prospective standings for every active candidate before the first scheduled
  tournament evaluation;
- deterministic `COLLECTING`, `ELIGIBLE`, `QUALIFIED`, and `CHAMPION`
  lifecycle display, with `RETIRED` reserved for a future explicit retirement
  record;
- clear separation of legacy strategy accounts from tournament competitors;
- aggregate-only tournament API output with raw feature snapshots and decision
  reasons retained server-side;
- no-store research responses, anti-embedding headers, and opt-in allowlisted
  CORS instead of wildcard credentialed access.

The integrity contract is:

```text
expected decision records = prospective cycles recorded × active candidates
```

Every active candidate must still create exactly one immutable record per
prospective cycle, including `NO_TRADE`.

## Preserved v1.0.1 cross-platform fix

Tournament database contexts now close SQLite handles explicitly after commit
or rollback. This prevents Windows from retaining a lock on temporary or
rotated database files. The change is additive and does not reset, rewrite, or
delete existing price-call or tournament evidence.

This overlay adds a cost-aware, multi-strategy shadow tournament to the
existing Binance-aligned 5m/15m price-call system. It is additive: it does not
reset, truncate, migrate away, or reinterpret any existing `price_calls`, paper
bankroll, strategy account, model, or journal record.

## Economic objective

Candidates may issue `UP`, `DOWN`, or `NO_TRADE`. A directional decision is
accepted into a candidate's shadow ledger only when its estimated move exceeds
the configured round-trip fee and slippage assumption with a safety margin.
Every accepted decision is settled against the same official Binance close as
the verified price call.

The tournament records, per candidate and cycle:

- immutable decision, model/candidate version, regime, confidence and reason;
- entry, official close, post-decision high/low, MFE and MAE;
- gross return, fees, slippage and net return;
- virtual equity, drawdown, expectancy, profit factor and win/loss profile;
- probability calibration and Brier score;
- participation rate, reliability and settlement source;
- walk-forward fold performance plus monthly and quarterly statistics;
- ML feature importance for the forward-only rolling ensemble.

Post-decision aggregate trades provide the preferred MFE/MAE path. After a
process restart or stream gap, the official full-cycle kline is retained as an
explicitly labeled fallback instead of presenting it as exact post-decision
path evidence.

## Candidate roster

- `cost_filtered_price_call` (baseline)
- `rsi_mean_reversion`
- `macd`
- `bollinger_bands`
- `time_series_momentum`
- `sma_crossover`
- `candlestick_patterns`
- `ml_ensemble` (regularized rolling logistic model plus trend component)
- `micro_momentum`
- `orderbook_imbalance`
- `micro_mean_reversion`
- `regime_trend_following`
- `volatility_breakout`

Every family has a real decision rule and a documented warm-up/no-trade path.
There is no always-win fixture, fabricated dashboard statistic, or forced
fallback champion in production code.

## Promotion policy

Hard gates are evaluated before composite ranking. A candidate must have:

- positive net expectancy after fees and slippage;
- a 95% lower expectancy confidence bound above zero;
- profit factor greater than 1;
- maximum drawdown within the configured limit;
- at least 300 eligible trades by default;
- at least three complete chronological 50-trade folds;
- at least 60% profitable walk-forward folds;
- at least 95% settlement reliability.

Only candidates that pass every gate are ranked:

- 40% post-cost expectancy
- 20% walk-forward stability
- 15% profit factor
- 10% drawdown control
- 10% calibration
- 5% reliability

Separate champions are maintained for every `(symbol, horizon, regime)`
contract. Regimes are `trending_up`, `trending_down`, `ranging`,
`breakout_up`, `breakout_down`, `high_volatility`, `low_volatility`, and
initial `unclassified` warm-up.

A qualifying challenger must beat a viable incumbent by five composite points
across three consecutive evaluations by default. If no candidate qualifies,
the correct output is `NONE PROMOTED`.

Champion confidence is derived from the expectancy confidence interval,
eligible sample size, walk-forward consistency, and settlement reliability. It
is not a decorative or manually entered percentage.

## Safe application

Stop Uvicorn, extract the overlay at the repository root, then run:

```powershell
python -m pytest -q tests/test_price_call.py tests/test_quant_tournament.py tests/test_market_state.py tests/test_economic_evidence.py
python -m py_compile webapp\economic_evidence.py webapp\market_state.py webapp\quant_tournament.py webapp\price_call.py webapp\server.py
node --check webapp\pro_static\app.js
node --check webapp\pro_static\price_call.js
```

The focused Python suite contains 41 tests: eleven price-call regressions,
eleven tournament/confidentiality tests, nine market-state/recovery tests, and
ten economic-eligibility tests covering timing boundaries, future-tick
exclusion, cost abstention, pre-policy preservation, REST provenance,
confidential API output, health-link integrity, and deterministic connection
cleanup. Then restart normally:

```powershell
$env:PRICE_CALL_SYMBOL = "BTC/USDT"
python -m uvicorn webapp.server:app --host 127.0.0.1 --port 8000
```

Use `Ctrl+F5` once after restart so the new dashboard assets replace the
browser cache.

## Confidential deployment boundary

The dashboard now defaults to same-origin browser access. If a separate
trusted frontend origin is required, configure an exact allowlist before
startup:

```powershell
$env:ATS_ALLOWED_ORIGINS = "https://research.example.com"
```

Do not place `/pro` or `/api/pro/*` directly on a public domain without
authentication at the reverse proxy or private-network layer. No-cache and
anti-embedding headers reduce passive leakage; they are not a substitute for
identity-based access control, TLS, firewall policy, or secret management.

## First-run expectations

- The existing price-call evidence and bankroll balances continue unchanged.
- Existing tournament decisions and outcomes continue unchanged.
- Existing rows appear as preserved pre-economic-policy evidence and are not
  rewritten into the primary v1.3 benchmark.
- Primary sample counts begin prospectively with v1.3 boundary-issued cycles.
- A recovered close is separated by provenance but is not automatically
  excluded when the original call was issued at the boundary.
- Closed-candle warm-up builds indicators only and creates no historical
  candidate evidence.
- Tournament evidence starts prospectively with the first cycle after this
  overlay is deployed. Historical candidates are not fabricated retroactively.
- The first quantitative ranking occurs after 25 new settled cycles by default.
- A champion cannot be promoted until all gates, including the eligible-trade
  minimum, pass. `NO_TRADE` cycles do not count toward that minimum.
- This remains real Binance market data with simulated execution; it does not
  place exchange orders or authorize live capital.

## Optional policy environment variables

Defaults are intentionally conservative. Changes apply only after a restart:

```text
QUANT_MIN_ELIGIBLE_TRADES=300
QUANT_MIN_WALK_FORWARD_FOLDS=3
QUANT_WALK_FORWARD_FOLD_SIZE=50
QUANT_MIN_PROFITABLE_FOLD_RATE=0.60
QUANT_MAX_DRAWDOWN_PCT=10.0
QUANT_MIN_RELIABILITY=0.95
QUANT_CHALLENGER_MARGIN_POINTS=5.0
QUANT_CHALLENGER_CONFIRMATIONS=3
QUANT_EVALUATE_EVERY_CYCLES=25
```

Reducing these values makes promotion less conservative. It does not improve
the evidence itself and should not be done merely to force a winner.

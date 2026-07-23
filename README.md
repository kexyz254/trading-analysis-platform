# ATS Overall Market Interpreter v1.4.0

This additive overlay includes the corrected v1.3.1-r2 learning-observability
release and adds a deterministic Overall Market Interpreter to the Research
workspace.  It changes no strategy threshold, tournament gate, bankroll rule,
execution permission, historical evidence row, or policy identity.

## What the panel means

The interpreter synthesizes separately labeled evidence from:

- point-in-time market regime for the selected 5m/15m horizon;
- validated Next 3 Candles forecast directions;
- right-edge chart-pattern geometry;
- the legacy strategy signal only when its engine timeframe matches;
- Tournament ML readiness without exposing private direction or weights; and
- the current price-call economic gate for matching 5m/15m cycles.

`alignment_score` measures agreement among usable sources.  It is explicitly
not a probability of a correct call, profit, or trade success.  Forecast
horizons that fail to beat their recent directional baseline cannot drive the
outlook.  Chart-pattern confidence remains geometry quality, not predictive
probability.

## Authority boundary

The interpreter is read-only.  It cannot train a model, create a strategy or
price-call decision, open exposure, alter risk, modify the bankroll, settle a
cycle, or promote a tournament candidate.  A source failure becomes a named
warning and never fabricates replacement evidence.

## Verification

```bash
python -m py_compile \
  webapp/economic_evidence.py webapp/forecast.py \
  webapp/learning_observability.py webapp/market_interpreter.py \
  webapp/market_state.py webapp/price_call.py \
  webapp/quant_tournament.py webapp/server.py

node --check webapp/pro_static/app.js
node --check webapp/pro_static/price_call.js

python -m pytest -q \
  tests/test_economic_evidence.py tests/test_market_state.py \
  tests/test_price_call.py tests/test_quant_tournament.py \
  tests/test_forecast_learning.py tests/test_learning_observability.py \
  tests/test_market_interpreter.py
```

Expected focused result: `55 passed`.

## Deployment

1. Stop the single ATS process and back up both the execution journal and
   `dashboard_data/analytics.db`.
2. Overlay this ZIP at the repository root.
3. Run the verification commands above.
4. Restart exactly one ATS process; do not reset any ledger.
5. Hard-refresh `/pro`, open Research, and select 5m then 15m.

## Live acceptance checks

- `/api/pro/market-interpreter/BTC/USDT?timeframe=15m` returns HTTP 200.
- `authority` is entirely fail-closed and `read_only` is true.
- `alignment_score_is_probability` is false.
- `economics.separate_from_directional_outlook` is true.
- A `NO_TRADE` economic action remains `ABSTAIN` even when sources align.
- Forecast ML with no holdout edge reports `READY_LOW_EDGE` and cannot drive
  the outlook.
- Legacy signals from another timeframe report `CONTEXT_ONLY`.
- Tournament ML direction remains `NOT_EXPOSED`.
- Missing/stale sources appear under Evidence gaps; they never disappear.
- All missing, duplicate, broken-link, and pending-recovery integrity metrics
  remain zero, with exactly 13 tournament decisions per cycle.
- Existing price-call, bankroll, tournament, and evidence counts do not reset.

The tournament policy remains `quant-tournament-v1.3.0`; v1.4.0 is a research
interpretation and observability release, not a strategy-policy change.

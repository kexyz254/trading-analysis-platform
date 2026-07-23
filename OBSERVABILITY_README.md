# ATS v1.3.1 deployment overlay

Apply this package over the same project root that received v1.3.0, then
restart the ATS service once.

## Files

- `webapp/economic_evidence.py` — primary-only lead-time aggregation.
- `webapp/quant_tournament.py` — explicit readiness states, model identity,
  training-window metadata, safe feature telemetry, and feedback health.
- `webapp/forecast.py` — forecast-model identity and closed-candle/isolation
  contract.
- `webapp/learning_observability.py` — read-only three-path health report.
- `webapp/server.py` — learning-health API, explicit legacy-SHAP source, and
  separate legacy-journal/tournament-analytics database routing.
- `webapp/pro_static/index.html` and `app.js` — separate learning states.
- `tests/` — focused v1.3.0 regressions plus v1.3.1 learning tests.

## Safety rules

1. Back up the database before deployment.
2. Do not remove or replace the existing database.
3. Do not run the price-call reset utility.
4. Do not modify economic thresholds or tournament gates.
5. Restart exactly one ATS process and verify there is no second writer.
6. Confirm the existing ledgers and pre-policy counts remain unchanged.

## Verification

```bash
python -m py_compile \
  webapp/economic_evidence.py webapp/forecast.py \
  webapp/learning_observability.py webapp/market_state.py \
  webapp/price_call.py webapp/quant_tournament.py webapp/server.py

node --check webapp/pro_static/app.js
node --check webapp/pro_static/price_call.js

python -m pytest -q \
  tests/test_economic_evidence.py tests/test_market_state.py \
  tests/test_price_call.py tests/test_quant_tournament.py \
  tests/test_forecast_learning.py tests/test_learning_observability.py
```

Expected focused result: `50 passed`.

## First live checks

- All three learning states are shown separately; a generic process error rate
  never controls them.
- Legacy Learning reads the execution journal while Tournament ML reads
  `dashboard_data/analytics.db`; evidence from either path must not be queried
  through the other path's database.
- A ready neutral tournament model reports `MODEL_READY_LOW_EDGE`.
- A warming model reports `MODEL_WARMING`.
- Primary lead time approaches the exchange horizon and excludes late calls.
- Forecast ML declares dashboard-only authority.
- Tournament decisions continue to total exactly 13 per cycle.
- Missing decisions, outcomes, state links, policy links, and ready-model
  telemetry links remain zero.

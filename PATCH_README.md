# Binance-aligned price-call patch

This overlay replaces relative polling timers with Binance Spot mainnet public
market evidence. It remains paper-only and never sends an exchange order.

## What changes

- Entry: Binance aggregate-trade price and exchange trade timestamp.
- Expiry: the next exact UTC 5m or 15m boundary.
- Settlement: official Binance closed-kline price (`x=true`).
- Persistence: idempotent SQLite calls, paper positions, accounts, and
  settlements linked by `price_call_id`.
- Frequency and stake: one configured price-call symbol, exactly one call per
  5m/15m Binance cycle, and a fixed `$1.00` simulated stake per horizon.
- Costs: deterministic 0.10% fee and 2 bps slippage per side, applied to
  simulated notional.
- Metrics: Wilson 95% interval, Brier score/skill, calibration error, coverage,
  settlement latency, paper return, expectancy, profit factor, and drawdown.
- Disclosure: real Binance prices, simulated execution, not live capital.
- Presentation: wider price-call and paper-performance cards with larger,
  higher-contrast text.
- Separation: strategy-model signals, positions, and trades are explicitly
  labeled as separate from the Binance price-call evidence.

Existing second-based rows are preserved as `LEGACY_RELATIVE_TIMER` and
excluded from all new evidence and paper-performance statistics. No database
or CSV file is included in this patch, so current data is not overwritten.

## Apply on Windows PowerShell

Stop the dashboard worker, place the ZIP in the repository root, and run:

```powershell
Set-Location C:\Users\jeuri\trading-analysis-platform
$ErrorActionPreference = "Stop"

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
Copy-Item dashboard_data\analytics.db `
    "dashboard_data\analytics-before-binance-cycles-$stamp.db"

Expand-Archive `
    -Path .\price-call-final-clean-reset-patch.zip `
    -DestinationPath . `
    -Force

python -m pip install -r requirements.txt
python -m pytest -q tests/test_price_call.py
python -m py_compile `
    webapp/price_call.py `
    webapp/server.py `
    webapp/bankroll_api.py `
    trading_assistant/analytics/price_call_bankroll.py
node --check webapp/pro_static/app.js
node --check webapp/pro_static/price_call.js
node --check webapp/static/ws.js
```

## Start a fresh evidence run

After the tests pass, keep the server stopped and run the included reset tool.
It first creates a timestamped SQLite backup, then clears only the price-call
and linked paper-ledger records. Other analytics tables remain untouched.

```powershell
python scripts/reset_price_call_track_record.py --confirm RESET
```

The reset output must show the backup path and zero remaining price calls,
positions, and settlements. Then restart Uvicorn and let the engine run. To
make BTC/USDT explicit for this evidence run, use:

```powershell
$env:PRICE_CALL_SYMBOL = "BTC/USDT"
python -m uvicorn webapp.server:app --host 127.0.0.1 --port 8000
```

Restart the existing Uvicorn process, hard-refresh `/pro` with `Ctrl+F5`, and
verify:

```powershell
Invoke-RestMethod `
    -Uri "http://127.0.0.1:8000/api/pro/price-calls/BTC/USDT?limit=20" `
    -TimeoutSec 20 | ConvertTo-Json -Depth 12

Invoke-RestMethod `
    -Uri "http://127.0.0.1:8000/api/pro/bankroll" `
    -TimeoutSec 20 | ConvertTo-Json -Depth 12
```

The first endpoint should show millisecond timestamps, `EXCHANGE_BOUNDARY_MS`,
`BINANCE_SPOT_MAINNET`, and 5m/15m expiries on exact clock boundaries. The
tracked symbol has one prediction and one `$1.00` linked paper position per
horizon/cycle. If the trading configuration contains several symbols, the
price-call engine uses the first one. To select another configured market, set
`PRICE_CALL_SYMBOL` before starting Uvicorn.
After an official close, it should show `settlement_method: STREAM_KLINE`, the
Binance close price, settlement latency, and exactly one linked settlement.

Relevant logs begin with:

```text
[ENGINE PRO CALL CAST]
[PRO CALL SETTLED]
Binance price-call stream connected
```

Do not describe paper-bankroll results as actual investment returns. A sample
below 100 directional settlements per horizon remains visibly marked
`INSUFFICIENT SAMPLE`.

# Final dashboard observability patch

This overlay finalizes the verified Binance price-call area without clearing or
rewriting its accumulated evidence. It does not contain a database, CSV,
credential, model, cache, Git metadata, or reset script.

## What it fixes

- `MARKET LIVE` now requires both a fresh backend Binance aggregate-trade
  message and a fresh browser Binance ticker message.
- The dashboard automatically changes to `MARKET STALE` or `MARKET OFFLINE`
  instead of painting Exchange green unconditionally.
- The footer shows the age of the latest backend Binance message and exposes
  the last stream error in its tooltip.
- Strategy confidence is labeled `signal score`, separately from directional
  probability.
- Strategy positions display their journal timestamp and are visibly marked
  `STALE MARK` after 30 minutes.
- Model information no longer remains on `Loading…`: optional old-schema data
  is degraded gracefully, and a real endpoint failure is shown visibly.
- DOWN price calls are explicitly described as simulated directional exposure,
  not Binance Spot short orders.
- Uptime is explicitly labeled `Engine uptime`.

The one-call-per-symbol/horizon/cycle rules, fixed $1 paper stake, official
closed-kline settlement, and existing paper ledger are unchanged.

## Apply on Windows PowerShell

Stop Uvicorn with `Ctrl+C`, then run:

```powershell
Set-Location C:\Users\jeuri\trading-analysis-platform
$ErrorActionPreference = "Stop"

$zip = "$HOME\Downloads\price-call-dashboard-final-observability-patch.zip"
if (-not (Test-Path $zip)) {
    throw "Final observability patch was not found in Downloads."
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
git diff | Set-Content "before-dashboard-observability-$stamp.diff"

Expand-Archive -Path $zip -DestinationPath . -Force

python -m pytest -q tests/test_price_call.py
if ($LASTEXITCODE -ne 0) {
    throw "Price-call regression tests failed. Do not restart yet."
}

python -m py_compile webapp\price_call.py webapp\server.py
node --check webapp\pro_static\app.js
node --check webapp\pro_static\price_call.js
```

The focused suite should report `10 passed`.

## Restart without resetting

Do **not** run `reset_price_call_track_record.py`. Restart the existing server:

```powershell
$env:PRICE_CALL_SYMBOL = "BTC/USDT"
python -m uvicorn webapp.server:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/pro` and press `Ctrl+F5`.

Expected behavior:

- `MARKET LIVE` and a green Binance footer only while both feeds are fresh.
- `MARKET STALE` within seconds after messages stop.
- `MARKET OFFLINE` when the backend stream reports a disconnect.
- Strategy position mark age shown below its price.
- Signal score clearly separated from Up/Down probabilities.
- Existing settled calls and balances remain present.

## Read-only verification

```powershell
$base = "http://127.0.0.1:8000"
$calls = Invoke-RestMethod "$base/api/pro/price-calls/BTC/USDT?limit=5"

$calls.market.stream | Format-List `
    status,connected,fresh,age_ms,last_message_at_ms,last_error,freshness_limit_ms

Invoke-RestMethod "$base/api/pro/model-info/BTC/USDT" |
    ConvertTo-Json -Depth 8
```

The market stream should normally show `status: LIVE`, `connected: True`,
`fresh: True`, and a small `age_ms`. This verifies data freshness; it does not
represent live-capital execution.

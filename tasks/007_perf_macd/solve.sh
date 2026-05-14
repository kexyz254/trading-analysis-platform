#!/bin/bash
set -e
grep -q "lru_cache" trading_assistant/core/indicators.py
python -m pytest tests/test_indicators.py -k "macd" -v
echo '{"status":"success"}' > /tmp/reward.txt
exit 0

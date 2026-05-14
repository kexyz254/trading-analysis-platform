#!/bin/bash
set -e
echo "Checking SMA cache..."
grep -q "lru_cache" trading_assistant/core/indicators.py
python -m pytest tests/test_indicators.py -k "sma" -v
echo '{"status":"success","task_id":"013"}' > /tmp/reward.txt
exit 0

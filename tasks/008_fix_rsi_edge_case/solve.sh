#!/bin/bash
set -e
echo "Checking RSI fix..."
python -m pytest tests/test_indicators.py -k "rsi" -v
echo '{"status":"success","task_id":"008"}' > /tmp/reward.txt
exit 0

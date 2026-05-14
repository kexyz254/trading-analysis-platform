#!/bin/bash
set -e

echo "checking sma function..."
if grep -q "def sma" trading_assistant/core/indicators.py; then
    echo "found sma"
else
    echo "sma missing"
    exit 1
fi

echo "running tests..."
python -m pytest tests/test_indicators.py -k "sma" -v

echo '{"status":"success","task_id":"006"}' > /tmp/reward.txt
echo "task 006 done"
exit 0

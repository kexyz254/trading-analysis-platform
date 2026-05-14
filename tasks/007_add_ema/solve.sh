#!/bin/bash
set -e

if grep -q "def ema" trading_assistant/core/indicators.py; then
    echo "✓ ema function found"
else
    echo "✗ ema missing"
    exit 1
fi

python -m pytest tests/test_indicators.py -v
echo '{"status":"success","task_id":"007"}' > /tmp/reward.txt
exit 0

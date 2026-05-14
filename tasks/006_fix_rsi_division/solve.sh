#!/bin/bash
set -e
grep -q "fillna(50)" trading_assistant/core/indicators.py
python -m pytest tests/test_indicators.py -k "rsi" -v
echo '{"status":"success"}' > /tmp/reward.txt
exit 0

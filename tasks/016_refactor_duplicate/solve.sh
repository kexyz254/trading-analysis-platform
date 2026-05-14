#!/bin/bash
set -e
grep -q "validate_ohlc" trading_assistant/core/validation.py
python -m pytest tests/test_ohlc.py -v
echo '{"status":"success"}' > /tmp/reward.txt
exit 0

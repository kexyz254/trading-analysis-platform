#!/bin/bash
set -e
grep -q "def williams_r" trading_assistant/core/indicators.py || exit 1
python -m pytest tests/test_indicators.py -k "williams" -v
echo '{"status":"success"}' > /tmp/reward.txt
exit 0

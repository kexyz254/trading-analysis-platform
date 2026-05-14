#!/bin/bash
set -e
grep -q "def stochastic_k" trading_assistant/core/indicators.py || exit 1
python -m pytest tests/test_indicators.py -k "stochastic" -v
echo '{"status":"success"}' > /tmp/reward.txt
exit 0

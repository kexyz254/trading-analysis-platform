#!/bin/bash
set -e
grep -q "def stochastic_k" trading_assistant/core/indicators.py
grep -q "def williams_r" trading_assistant/core/indicators.py
python -m pytest tests/ -v
echo '{"status":"success"}' > /tmp/reward.txt
exit 0

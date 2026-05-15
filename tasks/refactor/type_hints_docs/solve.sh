#!/bin/bash
set -e
grep -q "-> pd.Series" trading_assistant/core/indicators.py
grep -q "-> tuple" trading_assistant/core/indicators.py
echo '{"status":"success"}' > /tmp/reward.txt
exit 0

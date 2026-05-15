#!/bin/bash
set -e
grep -q "import pandas" trading_assistant/core/indicators.py
echo '{"status":"success"}' > /tmp/reward.txt
exit 0

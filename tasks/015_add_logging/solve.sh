#!/bin/bash
set -e
grep -q "import logging" trading_assistant/backtesting/engine.py
echo '{"status":"success"}' > /tmp/reward.txt
exit 0

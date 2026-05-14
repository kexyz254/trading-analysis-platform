#!/bin/bash
set -e
echo "Checking backtest fix..."
python -m pytest tests/test_backtest.py -v
echo '{"status":"success","task_id":"010"}' > /tmp/reward.txt
exit 0

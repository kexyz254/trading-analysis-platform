#!/bin/bash
set -e
python -m pytest tests/test_backtest.py -v
echo '{"status":"success"}' > /tmp/reward.txt
exit 0

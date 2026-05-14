#!/bin/bash
set -e
echo "Checking validation refactor..."
python -m pytest tests/test_ohlc.py -v
echo '{"status":"success","task_id":"012"}' > /tmp/reward.txt
exit 0

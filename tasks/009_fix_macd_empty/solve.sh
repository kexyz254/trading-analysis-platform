#!/bin/bash
set -e
echo "Checking MACD empty data fix..."
python -m pytest tests/test_indicators.py -k "macd" -v
echo '{"status":"success","task_id":"009"}' > /tmp/reward.txt
exit 0

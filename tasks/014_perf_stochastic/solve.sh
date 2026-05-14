#!/bin/bash
set -e
echo "Checking stochastic optimization..."
python -m pytest tests/test_indicators.py -k "stochastic" -v
echo '{"status":"success","task_id":"014"}' > /tmp/reward.txt
exit 0

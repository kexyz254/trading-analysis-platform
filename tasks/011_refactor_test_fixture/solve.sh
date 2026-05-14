#!/bin/bash
set -e
echo "Checking fixture..."
grep -q "def sample_prices" conftest.py
python -m pytest tests/ -v --tb=no | grep -q "passed"
echo '{"status":"success","task_id":"011"}' > /tmp/reward.txt
exit 0

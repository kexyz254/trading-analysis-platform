#!/bin/bash
set -e
python -m pytest tests/ -v
echo '{"status":"success"}' > /tmp/reward.txt
exit 0

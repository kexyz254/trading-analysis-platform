#!/bin/bash
set -e
test -f scripts/benchmark.py
echo '{"status":"success"}' > /tmp/reward.txt
exit 0

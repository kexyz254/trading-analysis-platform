#!/bin/bash
set -e
grep -q "f\"Missing columns:" trading_assistant/core/validation.py
echo '{"status":"success"}' > /tmp/reward.txt
exit 0

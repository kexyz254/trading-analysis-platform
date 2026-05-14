#!/bin/bash
set -e

echo "========================================="
echo "Task 004: ATR Indicator Verification"
echo "========================================="

if grep -q "def calculate_atr" trading_assistant/core/indicators.py; then
    echo "✓ ATR function found"
else
    echo "✗ ATR function missing"
    exit 1
fi

echo ""
echo "Running all tests..."
python -m pytest tests/test_indicators.py -v

cat > /tmp/reward.txt << 'REWARDEOF'
{"status":"success","task_id":"004","task_name":"ATR Indicator"}
REWARDEOF

echo ""
echo "========================================="
echo "Task 004 VERIFIED"
echo "========================================="
exit 0

#!/bin/bash
set -e

echo "========================================="
echo "Task 003: Bollinger Bands Verification"
echo "========================================="

if grep -q "def calculate_bollinger_bands" trading_assistant/core/indicators.py; then
    echo "✓ Bollinger Bands function found"
else
    echo "✗ Function missing"
    exit 1
fi

echo ""
echo "Running all tests..."
python -m pytest tests/test_indicators.py -v

cat > /tmp/reward.txt << 'REWARDEOF'
{"status":"success","task_id":"003","task_name":"Bollinger Bands"}
REWARDEOF

echo ""
echo "========================================="
echo "Task 003 VERIFIED"
echo "========================================="
exit 0

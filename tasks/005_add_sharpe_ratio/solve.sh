#!/bin/bash
set -e

echo "========================================="
echo "Task 005: Sharpe Ratio Verification"
echo "========================================="

if grep -q "def calculate_sharpe_ratio" trading_assistant/core/indicators.py; then
    echo "✓ Sharpe Ratio function found"
else
    echo "✗ Sharpe Ratio function missing"
    exit 1
fi

echo ""
echo "Running all tests..."
python -m pytest tests/test_indicators.py -v

cat > /tmp/reward.txt << 'REWARDEOF'
{"status":"success","task_id":"005","task_name":"Sharpe Ratio"}
REWARDEOF

echo ""
echo "========================================="
echo "Task 005 VERIFIED"
echo "========================================="
exit 0

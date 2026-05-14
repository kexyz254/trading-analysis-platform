#!/bin/bash
# Task 001 Solution: Add RSI Indicator
set -e

echo "========================================="
echo "Task 001: Verifying RSI Indicator"
echo "========================================="

if [ ! -f "trading_assistant/core/indicators.py" ]; then
    echo "ERROR: indicators.py not found"
    exit 1
fi
echo "indicators.py exists"

if ! grep -q "def calculate_rsi" trading_assistant/core/indicators.py; then
    echo "ERROR: calculate_rsi function not found"
    exit 1
fi
echo "calculate_rsi function found"

if [ ! -f "tests/test_indicators.py" ]; then
    echo "ERROR: test_indicators.py not found"
    exit 1
fi
echo "test file exists"

echo ""
echo "Running tests..."
python -m pytest tests/test_indicators.py -v

if [ $? -ne 0 ]; then
    echo ""
    echo "ERROR: Tests failed"
    exit 1
fi

echo ""
echo "Writing reward file..."
cat > /tmp/reward.txt << "REWARDEOF"
{
  "status": "success",
  "task_id": "001",
  "task_name": "Add RSI Indicator",
  "tests_passed": 4,
  "message": "RSI indicator successfully implemented"
}
REWARDEOF

echo ""
echo "========================================="
echo "TASK 001 VERIFIED SUCCESSFULLY!"
echo "========================================="

exit 0

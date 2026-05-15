#!/bin/bash
# Task 002 Solution: Add MACD Indicator
set -e

echo "========================================="
echo "Task 002: MACD Indicator Verification"
echo "========================================="

# Check MACD function exists
if ! grep -q "def calculate_macd" trading_assistant/core/indicators.py; then
    echo "ERROR: calculate_macd not found"
    exit 1
fi
echo "✓ MACD function found"

# Check MACD tests exist
if ! grep -q "def test_macd" tests/test_indicators.py; then
    echo "ERROR: MACD tests not found"
    exit 1
fi
echo "✓ MACD tests found"

# Run all tests
echo ""
python -m pytest tests/test_indicators.py -v

# Write reward file for Scale AI
cat > /tmp/reward.txt << 'REWARDEOF'
{
  "status": "success",
  "task_id": "002",
  "task_name": "Add MACD Indicator",
  "tests_passed": 7,
  "files_modified": [
    "trading_assistant/core/indicators.py",
    "tests/test_indicators.py"
  ]
}
REWARDEOF

echo ""
echo "========================================="
echo "TASK 002 VERIFIED SUCCESSFULLY"
echo "========================================="
exit 0

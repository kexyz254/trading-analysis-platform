import pandas as pd
import pytest

from trading_assistant.portfolio import analyze_allocation_drift
from trading_assistant.portfolio.rebalance import PortfolioRebalanceError


def sample_positions():
    return pd.DataFrame(
        {
            "symbol": ["AAPL", "MSFT", "TSLA"],
            "quantity": [10, 5, 2],
            "price": [200.0, 100.0, 250.0],
        }
    )


def drifts_by_symbol(report):
    return {drift.symbol: drift for drift in report.drifts}


def test_analyze_allocation_drift_reports_current_vs_target_weights():
    report = analyze_allocation_drift(
        sample_positions(),
        {"AAPL": 0.40, "MSFT": 0.40, "TSLA": 0.20},
        tolerance=0.01,
    )
    drifts = drifts_by_symbol(report)

    assert report.total_value == 3000.0
    assert round(drifts["AAPL"].current_weight, 4) == round(2000.0 / 3000.0, 4)
    assert round(drifts["AAPL"].target_weight, 4) == 0.40
    assert "AAPL" in report.out_of_tolerance_symbols


def test_allocation_drift_report_to_dict_is_json_friendly():
    report = analyze_allocation_drift(
        sample_positions(),
        {"AAPL": 0.40, "MSFT": 0.40, "TSLA": 0.20},
    )

    payload = report.to_dict()

    assert payload["total_value"] == 3000.0
    assert isinstance(payload["drifts"], list)
    assert isinstance(payload["out_of_tolerance_symbols"], list)
    assert payload["max_absolute_drift"] == report.max_absolute_drift


def test_analyze_allocation_drift_includes_symbols_missing_from_targets():
    report = analyze_allocation_drift(
        sample_positions(),
        {"AAPL": 1.0},
        tolerance=0.01,
    )
    drifts = drifts_by_symbol(report)

    assert drifts["MSFT"].target_weight == 0.0
    assert drifts["TSLA"].target_weight == 0.0
    assert "MSFT" in report.out_of_tolerance_symbols
    assert "TSLA" in report.out_of_tolerance_symbols


def test_analyze_allocation_drift_includes_targets_missing_from_positions():
    report = analyze_allocation_drift(
        sample_positions(),
        {"AAPL": 0.5, "MSFT": 0.25, "TSLA": 0.15, "NVDA": 0.10},
        tolerance=0.01,
    )
    drifts = drifts_by_symbol(report)

    assert drifts["NVDA"].current_value == 0.0
    assert drifts["NVDA"].target_weight == 0.10
    assert "NVDA" in report.out_of_tolerance_symbols


def test_analyze_allocation_drift_normalizes_percentage_targets():
    report = analyze_allocation_drift(
        sample_positions(),
        {"AAPL": 40, "MSFT": 40, "TSLA": 20},
    )
    drifts = drifts_by_symbol(report)

    assert round(drifts["MSFT"].target_weight, 2) == 0.40


def test_allocation_drift_supports_custom_columns():
    positions = pd.DataFrame(
        {
            "ticker": ["AAPL", "MSFT"],
            "shares": [10, 5],
            "mark": [100.0, 200.0],
        }
    )

    report = analyze_allocation_drift(
        positions,
        {"AAPL": 0.5, "MSFT": 0.5},
        symbol_column="ticker",
        quantity_column="shares",
        price_column="mark",
    )

    assert report.max_absolute_drift == 0.0


def test_analyze_allocation_drift_rejects_negative_tolerance():
    with pytest.raises(PortfolioRebalanceError, match="tolerance"):
        analyze_allocation_drift(sample_positions(), {"AAPL": 1.0}, tolerance=-0.1)


def test_analyze_allocation_drift_rejects_short_positions():
    positions = sample_positions()
    positions.loc[0, "quantity"] = -1

    with pytest.raises(PortfolioRebalanceError, match="long positions"):
        analyze_allocation_drift(positions, {"AAPL": 1.0})


def test_analyze_allocation_drift_rejects_bad_target_weights():
    with pytest.raises(PortfolioRebalanceError, match="target weights"):
        analyze_allocation_drift(sample_positions(), {"AAPL": -1.0})


def test_analyze_allocation_drift_rejects_zero_value_portfolio():
    positions = pd.DataFrame(
        {
            "symbol": ["AAPL"],
            "quantity": [0],
            "price": [100.0],
        }
    )

    with pytest.raises(PortfolioRebalanceError, match="positive"):
        analyze_allocation_drift(positions, {"AAPL": 1.0})

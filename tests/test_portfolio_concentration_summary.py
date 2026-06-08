import pandas as pd
import pytest

from trading_assistant.portfolio import summarize_position_concentration
from trading_assistant.portfolio.exposure import (
    PortfolioConcentrationSummary,
    PortfolioExposureError,
)


def sample_positions():
    return pd.DataFrame(
        {
            "symbol": ["AAPL", "MSFT", "TSLA", "NVDA"],
            "quantity": [10, 5, -3, -2],
            "price": [200.0, 100.0, 250.0, 300.0],
        }
    )


def test_summarize_position_concentration_reports_top_symbol_and_weight():
    summary = summarize_position_concentration(sample_positions(), max_weight=0.50)

    assert summary.total_market_value == 3850.0
    assert summary.top_symbol == "AAPL"
    assert round(summary.top_weight, 4) == round(2000.0 / 3850.0, 4)
    assert summary.over_limit_symbols == ("AAPL",)


def test_summarize_position_concentration_groups_duplicate_symbols():
    positions = pd.DataFrame(
        {
            "symbol": ["AAPL", "AAPL", "MSFT"],
            "quantity": [5, -2, 4],
            "price": [100.0, 100.0, 50.0],
        }
    )

    summary = summarize_position_concentration(positions, max_weight=0.70)

    assert summary.total_market_value == 900.0
    assert round(summary.position_weights["AAPL"], 4) == round(700.0 / 900.0, 4)
    assert summary.over_limit_symbols == ("AAPL",)


def test_summarize_position_concentration_to_dict_is_json_friendly():
    payload = summarize_position_concentration(sample_positions()).to_dict()

    assert payload["total_market_value"] == 3850.0
    assert payload["top_symbol"] == "AAPL"
    assert isinstance(payload["position_weights"], dict)
    assert isinstance(payload["over_limit_symbols"], list)


def test_summarize_position_concentration_handles_empty_positions():
    positions = pd.DataFrame(columns=["symbol", "quantity", "price"])

    summary = summarize_position_concentration(positions)

    assert summary == PortfolioConcentrationSummary(
        total_market_value=0.0,
        top_symbol=None,
        top_weight=0.0,
        position_weights={},
        over_limit_symbols=(),
    )


def test_summarize_position_concentration_handles_zero_value_positions():
    positions = pd.DataFrame(
        {
            "symbol": ["AAPL", "MSFT"],
            "quantity": [0, 0],
            "price": [100.0, 200.0],
        }
    )

    summary = summarize_position_concentration(positions)

    assert summary.total_market_value == 0.0
    assert summary.top_symbol is None
    assert summary.top_weight == 0.0
    assert summary.position_weights == {"AAPL": 0.0, "MSFT": 0.0}


def test_summarize_position_concentration_supports_custom_columns():
    positions = pd.DataFrame(
        {
            "ticker": ["AAPL", "TSLA"],
            "shares": [4, -2],
            "mark": [100.0, 150.0],
        }
    )

    summary = summarize_position_concentration(
        positions,
        symbol_column="ticker",
        quantity_column="shares",
        price_column="mark",
    )

    assert summary.total_market_value == 700.0
    assert summary.top_symbol == "AAPL"


def test_summarize_position_concentration_rejects_invalid_limit():
    with pytest.raises(PortfolioExposureError, match="max_weight"):
        summarize_position_concentration(sample_positions(), max_weight=1.5)


def test_summarize_position_concentration_rejects_missing_columns():
    positions = pd.DataFrame({"symbol": ["AAPL"], "quantity": [10]})

    with pytest.raises(PortfolioExposureError, match="missing required"):
        summarize_position_concentration(positions)


def test_summarize_position_concentration_rejects_non_numeric_values():
    positions = sample_positions().astype({"price": "object"})
    positions.loc[0, "price"] = "bad-price"

    with pytest.raises(PortfolioExposureError, match="numeric"):
        summarize_position_concentration(positions)


def test_summarize_position_concentration_rejects_negative_prices():
    positions = sample_positions()
    positions.loc[0, "price"] = -10

    with pytest.raises(PortfolioExposureError, match="negative"):
        summarize_position_concentration(positions)

import pandas as pd
import pytest

from trading_assistant.portfolio import summarize_portfolio_exposure
from trading_assistant.portfolio.exposure import (
    PortfolioExposureError,
    PortfolioExposureSummary,
)


def sample_positions():
    return pd.DataFrame(
        {
            "symbol": ["AAPL", "MSFT", "TSLA", "NVDA"],
            "quantity": [10, 5, -3, -2],
            "price": [200.0, 100.0, 250.0, 300.0],
        }
    )


def test_summarize_portfolio_exposure_calculates_long_short_gross_and_net():
    summary = summarize_portfolio_exposure(sample_positions())

    assert summary.position_count == 4
    assert summary.long_exposure == 2500.0
    assert summary.short_exposure == 1350.0
    assert summary.gross_exposure == 3850.0
    assert summary.net_exposure == 1150.0


def test_summarize_portfolio_exposure_reports_largest_position():
    summary = summarize_portfolio_exposure(sample_positions())

    assert summary.largest_symbol == "AAPL"
    assert summary.largest_position_value == 2000.0


def test_portfolio_exposure_ratios_are_based_on_gross_exposure():
    summary = summarize_portfolio_exposure(sample_positions())

    assert round(summary.long_ratio, 4) == round(2500.0 / 3850.0, 4)
    assert round(summary.short_ratio, 4) == round(1350.0 / 3850.0, 4)
    assert round(summary.net_ratio, 4) == round(1150.0 / 3850.0, 4)


def test_portfolio_exposure_summary_to_dict_is_json_friendly():
    payload = summarize_portfolio_exposure(sample_positions()).to_dict()

    assert payload["position_count"] == 4
    assert payload["long_exposure"] == 2500.0
    assert payload["short_exposure"] == 1350.0
    assert payload["largest_symbol"] == "AAPL"


def test_summarize_portfolio_exposure_handles_empty_positions():
    positions = pd.DataFrame(columns=["symbol", "quantity", "price"])

    summary = summarize_portfolio_exposure(positions)

    assert summary == PortfolioExposureSummary(
        position_count=0,
        long_exposure=0.0,
        short_exposure=0.0,
        gross_exposure=0.0,
        net_exposure=0.0,
        largest_symbol=None,
        largest_position_value=0.0,
    )
    assert summary.long_ratio == 0.0
    assert summary.short_ratio == 0.0
    assert summary.net_ratio == 0.0


def test_summarize_portfolio_exposure_supports_custom_columns():
    positions = pd.DataFrame(
        {
            "ticker": ["AAPL", "TSLA"],
            "shares": [4, -2],
            "mark": [100.0, 150.0],
        }
    )

    summary = summarize_portfolio_exposure(
        positions,
        symbol_column="ticker",
        quantity_column="shares",
        price_column="mark",
    )

    assert summary.long_exposure == 400.0
    assert summary.short_exposure == 300.0
    assert summary.net_exposure == 100.0


def test_summarize_portfolio_exposure_rejects_missing_columns():
    positions = pd.DataFrame({"symbol": ["AAPL"], "quantity": [10]})

    with pytest.raises(PortfolioExposureError, match="missing required"):
        summarize_portfolio_exposure(positions)


def test_summarize_portfolio_exposure_rejects_non_numeric_values():
    positions = sample_positions().astype({"quantity": "object"})
    positions.loc[0, "quantity"] = "ten"

    with pytest.raises(PortfolioExposureError, match="numeric"):
        summarize_portfolio_exposure(positions)


def test_summarize_portfolio_exposure_rejects_negative_prices():
    positions = sample_positions()
    positions.loc[0, "price"] = -1

    with pytest.raises(PortfolioExposureError, match="negative"):
        summarize_portfolio_exposure(positions)

import pandas as pd
import pytest

from trading_assistant.portfolio import check_portfolio_limits
from trading_assistant.portfolio.exposure import PortfolioExposureError
from trading_assistant.portfolio.limits import PortfolioLimitReport


def sample_positions():
    return pd.DataFrame(
        {
            "symbol": ["AAPL", "MSFT", "TSLA", "NVDA"],
            "quantity": [10, 5, -3, -2],
            "price": [200.0, 100.0, 250.0, 300.0],
        }
    )


def violations_by_name(report):
    return {violation.limit_name: violation for violation in report.violations}


def test_check_portfolio_limits_passes_when_all_limits_are_respected():
    report = check_portfolio_limits(
        sample_positions(),
        max_position_weight=0.60,
        max_gross_exposure=5000.0,
        max_net_exposure=1500.0,
        max_short_exposure=1500.0,
    )

    assert isinstance(report, PortfolioLimitReport)
    assert report.is_within_limits
    assert report.violation_count == 0


def test_check_portfolio_limits_detects_position_weight_breach():
    report = check_portfolio_limits(
        sample_positions(),
        max_position_weight=0.50,
    )

    assert not report.is_within_limits
    assert report.violations[0].limit_name == "max_position_weight"
    assert report.violations[0].symbol == "AAPL"


def test_check_portfolio_limits_detects_exposure_breaches():
    report = check_portfolio_limits(
        sample_positions(),
        max_gross_exposure=3000.0,
        max_net_exposure=1000.0,
        max_short_exposure=1000.0,
    )
    violations = violations_by_name(report)

    assert report.violation_count == 3
    assert violations["max_gross_exposure"].actual_value == 3850.0
    assert violations["max_net_exposure"].actual_value == 1150.0
    assert violations["max_short_exposure"].actual_value == 1350.0


def test_portfolio_limit_report_to_dict_is_json_friendly():
    report = check_portfolio_limits(sample_positions(), max_position_weight=0.50)

    payload = report.to_dict()

    assert payload["checked_limits"] == ["max_position_weight"]
    assert payload["is_within_limits"] is False
    assert payload["violation_count"] == 1
    assert payload["violations"][0]["symbol"] == "AAPL"


def test_check_portfolio_limits_supports_custom_columns():
    positions = pd.DataFrame(
        {
            "ticker": ["AAPL", "MSFT"],
            "shares": [4, -2],
            "mark": [100.0, 150.0],
        }
    )

    report = check_portfolio_limits(
        positions,
        symbol_column="ticker",
        quantity_column="shares",
        price_column="mark",
        max_gross_exposure=1000.0,
    )

    assert report.is_within_limits


def test_check_portfolio_limits_requires_at_least_one_limit():
    with pytest.raises(PortfolioExposureError, match="at least one"):
        check_portfolio_limits(sample_positions())


def test_check_portfolio_limits_rejects_invalid_weight_limit():
    with pytest.raises(PortfolioExposureError, match="between 0 and 1"):
        check_portfolio_limits(sample_positions(), max_position_weight=1.5)


def test_check_portfolio_limits_rejects_negative_absolute_limit():
    with pytest.raises(PortfolioExposureError, match="cannot be negative"):
        check_portfolio_limits(sample_positions(), max_gross_exposure=-1.0)


def test_check_portfolio_limits_reuses_position_validation_errors():
    positions = sample_positions()
    positions.loc[0, "price"] = -10

    with pytest.raises(PortfolioExposureError, match="negative"):
        check_portfolio_limits(positions, max_gross_exposure=1000.0)

import math

import numpy as np
import pandas as pd

from trading_assistant.risk.advanced_metrics import (
    AdvancedRiskMetrics,
    PortfolioRiskAggregator,
)


def sample_returns():
    return pd.Series([0.02, -0.01, 0.015, -0.03, 0.04, -0.02, 0.01, 0.005])


def test_advanced_risk_var_and_cvar_methods_return_finite_values():
    risk = AdvancedRiskMetrics(sample_returns())

    results = [
        risk.historical_var(confidence=0.95),
        risk.historical_cvar(confidence=0.95),
        risk.parametric_var(confidence=0.95),
        risk.parametric_cvar(confidence=0.95),
        risk.cornish_fisher_var(confidence=0.95),
        risk.expected_shortfall(confidence=0.95),
    ]

    assert all(math.isfinite(float(value)) for value in results)


def test_advanced_risk_drawdown_methods_return_valid_values():
    risk = AdvancedRiskMetrics(sample_returns())

    max_drawdown = risk.maximum_drawdown()
    average_drawdown = risk.average_drawdown()
    duration = risk.drawdown_duration()

    assert math.isfinite(float(max_drawdown))
    assert math.isfinite(float(average_drawdown))
    assert isinstance(duration, dict)
    assert all(value >= 0 for value in duration.values())


def test_advanced_risk_ratio_methods_are_numeric_or_infinite():
    risk = AdvancedRiskMetrics(sample_returns())

    results = [
        risk.calmar_ratio(periods_per_year=252),
        risk.sterling_ratio(periods_per_year=252),
        risk.burke_ratio(periods_per_year=252),
        risk.ulcer_index(),
        risk.pain_index(),
        risk.gain_to_pain_ratio(),
        risk.tail_ratio(),
        risk.omega_ratio(threshold=0.0),
    ]

    assert all(
        math.isfinite(float(value)) or math.isinf(float(value))
        for value in results
    )


def test_advanced_risk_handles_all_positive_returns_without_crashing():
    risk = AdvancedRiskMetrics(pd.Series([0.01, 0.02, 0.015, 0.03]))

    duration = risk.drawdown_duration()

    assert risk.maximum_drawdown() <= 0
    assert isinstance(duration, dict)
    assert all(value >= 0 for value in duration.values())
    assert math.isfinite(float(risk.ulcer_index()))
    pain_index = risk.pain_index()
    assert math.isnan(float(pain_index)) or math.isfinite(float(pain_index))


def test_portfolio_risk_aggregator_var_and_cvar_are_finite():
    returns_df = pd.DataFrame(
        {
            "asset_a": [0.01, -0.02, 0.03, 0.01, -0.01],
            "asset_b": [-0.01, 0.015, 0.02, -0.005, 0.01],
            "asset_c": [0.005, 0.01, -0.015, 0.02, -0.01],
        }
    )
    weights = np.array([0.5, 0.3, 0.2])

    aggregator = PortfolioRiskAggregator(returns_df)

    assert math.isfinite(float(aggregator.portfolio_var(weights, confidence=0.95)))
    assert math.isfinite(float(aggregator.portfolio_cvar(weights, confidence=0.95)))


def test_portfolio_risk_decomposition_contains_each_asset():
    returns_df = pd.DataFrame(
        {
            "asset_a": [0.01, -0.02, 0.03, 0.01, -0.01],
            "asset_b": [-0.01, 0.015, 0.02, -0.005, 0.01],
            "asset_c": [0.005, 0.01, -0.015, 0.02, -0.01],
        }
    )
    weights = np.array([0.5, 0.3, 0.2])

    aggregator = PortfolioRiskAggregator(returns_df)
    decomposition = aggregator.risk_decomposition(weights, confidence=0.95)

    raw_values = np.asarray(
        decomposition.values if hasattr(decomposition, "values") else decomposition,
        dtype=object,
    ).ravel()

    numeric_values = [
        float(value)
        for value in raw_values
        if isinstance(value, (int, float, np.integer, np.floating))
    ]

    assert len(numeric_values) >= 3
    assert np.all(np.isfinite(numeric_values))


def test_portfolio_marginal_component_and_incremental_var_are_finite():
    returns_df = pd.DataFrame(
        {
            "asset_a": [0.01, -0.02, 0.03, 0.01, -0.01],
            "asset_b": [-0.01, 0.015, 0.02, -0.005, 0.01],
        }
    )
    weights = np.array([0.6, 0.4])
    new_asset_returns = pd.Series([0.005, -0.01, 0.02, 0.015, -0.005])

    aggregator = PortfolioRiskAggregator(returns_df)

    assert math.isfinite(float(aggregator.marginal_var(weights, asset_index=0, confidence=0.95)))
    assert math.isfinite(float(aggregator.component_var(weights, asset_index=1, confidence=0.95)))
    assert math.isfinite(
        float(
            aggregator.incremental_var(
                weights,
                new_asset_returns,
                new_weight=0.2,
                confidence=0.95,
            )
        )
    )

# Trading Analysis Platform

A Python-based algorithmic trading research framework for testing technical indicators, portfolio simulations, risk metrics, and reproducible backtesting workflows.

## Overview

This project provides a modular research environment for exploring systematic trading strategies. It focuses on deterministic calculations, clean data validation, testable components, and reusable analytics for historical market data.

The project is intended for research and education only. It is not connected to any live broker and does not execute real trades.

## Core Areas

### Market Data

Utilities for validating OHLCV-style market data, preparing time series inputs, and supporting deterministic test fixtures.

### Technical Indicators

Indicator implementations used by research strategies, including moving averages, RSI, MACD, Bollinger Bands, ATR, stochastic oscillators, momentum indicators, and volatility bands.

### Strategy Research

Reusable strategy components for generating signals from indicator outputs and evaluating systematic trading ideas.

### Backtesting

A simplified backtesting layer for simulating strategy behavior over historical data, tracking positions, cash, fills, and portfolio value.

### Risk and Performance

Risk and analytics utilities for drawdown, returns, volatility, exposure, Sharpe-style calculations, and portfolio-level summaries.

## Design Goals

- Deterministic test results
- Modular Python package structure
- Clear separation between indicators, strategy logic, simulation, and risk analytics
- Reproducible local development
- No broker dependencies
- No live-trading functionality

## Running Locally

Install dependencies:

    pip install -r requirements.txt

Run the test suite:

    pytest tests

Run coverage:

    pytest --cov=trading_assistant --cov-report=term-missing

## Current Quality Baseline

- 146 pytest tests passing
- Coverage configured for active package modules
- Current coverage target: 80%+
- No external broker or live trading dependency

## Project Status

This is an active personal research project. The current focus is improving data validation, simulation correctness, risk analytics, and portfolio-level behavior.

## CSV Market Data Loading

The data layer includes a CSV loader for OHLCV research datasets. It reads a local CSV file, forwards optional `pandas.read_csv` settings, and validates the loaded data with the market-data validator.

Example usage:

    from trading_assistant.data import load_ohlcv_csv

    prices = load_ohlcv_csv("data/prices.csv")

The loader raises `FileNotFoundError` for missing files and `MarketDataValidationError` when the CSV contents violate the expected OHLCV schema.

## Timestamp Parsing Options

Market data validation supports explicit timestamp parsing formats. By default, ISO-style timestamps are parsed with a deterministic `ISO8601` format to avoid pandas inference warnings. Custom date formats can be supplied when validating DataFrames or loading CSV files.

Example usage:

    from trading_assistant.data import load_ohlcv_csv

    prices = load_ohlcv_csv("data/prices.csv", timestamp_format="%d/%m/%Y")

This is useful for regional datasets that use day-first dates or other non-ISO timestamp layouts.

## Market Data Quality Reports

The data package can also generate a compact quality report from validated OHLCV data. The report includes row count, start and end timestamps, low/high bounds, average close, and total volume.

Example usage:

    from trading_assistant.data import analyze_ohlcv_quality

    report = analyze_ohlcv_quality(prices)
    payload = report.to_dict()

This is useful when checking whether a research dataset has the expected coverage before running indicators, backtests, or portfolio analytics.

## Market Data Gap Detection

The data package can detect missing timestamps inside validated OHLCV time series. This helps identify incomplete research datasets before running indicators, simulations, or portfolio analysis.

Example usage:

    from trading_assistant.data import find_ohlcv_time_gaps

    gaps = find_ohlcv_time_gaps(prices, expected_frequency="D")

Each detected gap includes the expected timestamp plus the nearest previous and next observed timestamps. The gap detector reuses the OHLCV validator, so malformed prices, duplicate timestamps, and invalid rows are rejected before interval checks run.

## Market Data Gap Summaries

Gap detection also supports compact summaries for reporting workflows. A summary includes the number of missing timestamps, the first missing timestamp, the last missing timestamp, and the complete list of missing timestamps.

Example usage:

    from trading_assistant.data import summarize_ohlcv_time_gaps

    summary = summarize_ohlcv_time_gaps(prices, expected_frequency="D")
    payload = summary.to_dict()

This is useful for quickly deciding whether a dataset is complete enough for indicator research or backtesting.

## Portfolio Exposure Reporting

The portfolio package can summarize position exposure from quantity and mark price data. Positive quantities are treated as long positions, negative quantities are treated as short positions, and the summary reports long, short, gross, and net exposure.

Example usage:

    import pandas as pd
    from trading_assistant.portfolio import summarize_portfolio_exposure

    positions = pd.DataFrame(
        {
            "symbol": ["AAPL", "MSFT", "TSLA"],
            "quantity": [10, 5, -3],
            "price": [200.0, 100.0, 250.0],
        }
    )

    exposure = summarize_portfolio_exposure(positions)
    payload = exposure.to_dict()

This is useful for checking portfolio concentration, long/short balance, and gross exposure before running risk analytics.

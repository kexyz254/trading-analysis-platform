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

- 115 pytest tests passing
- Coverage configured for active package modules
- Current coverage target: 80%+
- No external broker or live trading dependency

## Project Status

This is an active personal research project. The current focus is improving data validation, simulation correctness, risk analytics, and portfolio-level behavior.

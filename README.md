Trading Assistant — Algorithmic Trading Research Framework
🧠 Project Overview

This is a modular algorithmic trading research framework designed for systematic exploration of trading strategies, technical indicators, and risk-aware portfolio simulation.

It is built for educational and research purposes, focusing on clean architecture, test-driven development, and reproducible backtesting workflows.

🏗 System Architecture

The system follows a layered design:

Market Data
    ↓
Technical Indicators
    ↓
Trading Strategies
    ↓
Backtesting Engine
    ↓
Portfolio Manager
    ↓
Risk & Performance Analytics

Each layer is independently testable and designed to support modular experimentation.

📦 Core Modules
📊 Indicators (trading_assistant/core/indicators.py)

Implements a suite of technical indicators including:

RSI (Relative Strength Index)
MACD
SMA / EMA
Bollinger Bands
ATR
Stochastic Oscillator
Williams %R
OBV (On-Balance Volume)
ROC (Rate of Change)
Keltner Channels
Donchian Channels
🧪 Backtesting Engine (backtesting/)

Provides a simplified event-driven simulation framework:

Trade execution simulation
Position tracking
PnL computation
Robust handling of edge cases (e.g., zero prices)
Performance metric integration
💼 Portfolio Management (portfolio/)

Handles portfolio-level tracking:

Position lifecycle management
Unrealized / realized PnL calculation
Total portfolio valuation
Performance summaries
Risk-aware aggregation of returns
⚠️ Risk Module (risk/)

Implements quantitative risk controls and metrics:

Position sizing (Fixed fraction, Kelly, volatility-based)
Value at Risk (VaR)
Sortino Ratio
Beta estimation
Risk exposure modeling
📉 Strategy Framework (strategies/)

Supports plug-and-play trading strategies:

SMA Crossover Strategy
RSI-based Strategy
MACD-based Strategy
Base strategy abstraction for extension
📡 Data Layer (data/)

Provides structured access to market data:

Synthetic OHLC generation
Data validation utilities
Price series preprocessing tools
🧰 Utilities (utils/)

Shared helper functions:

Returns calculation
Drawdown analysis
Statistical helpers
🧪 Testing

The project uses pytest with comprehensive coverage across all major components.

Run tests:
pytest tests/
Coverage includes:
Indicators correctness
Backtest engine behavior
Portfolio state transitions
Risk metric validation
Strategy signal generation

Total tests: 72 passing

🧭 Task System

Development is organized into structured task categories:

tasks/
├── indicators/
├── backtesting/
├── performance/
├── refactor/

Each task contains:

instruction.md → problem specification
solve.sh → reproducible solution workflow

This enables incremental, test-driven development across the system.

⚙️ Design Principles

This project follows these core engineering principles:

Modularity — each subsystem is independently testable
Reproducibility — deterministic test outcomes via pytest
Separation of concerns — clear boundaries between layers
Extensibility — new indicators/strategies can be added easily
Simplicity over complexity — focus on interpretable implementations
▶️ Running the Project
Install dependencies
pip install -r requirements.txt
Run tests
pytest tests/
📊 System Intent

This framework is intended for:

Algorithmic trading research
Strategy prototyping and validation
Risk modeling experiments
Educational exploration of quant systems

It is not intended for live trading execution.

🏁 Summary

This repository demonstrates a full pipeline for systematic trading research, from raw indicators through to portfolio-level evaluation, with strong emphasis on modular design and test-driven correctness.

🧩 Notes for Reviewers
All components are fully test-covered
No external broker dependencies
Deterministic backtesting environment
Designed for reproducibility and evaluation clarity
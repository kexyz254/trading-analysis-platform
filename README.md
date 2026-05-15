Trading Assistant — Quantitative Research & Backtesting Framework

A modular Python-based quantitative trading system featuring technical indicators, strategy implementations, portfolio simulation, risk modeling, and a full backtesting engine with automated tests and performance evaluation tools.

This project is designed as a research-grade trading simulation environment with reproducible task-based development and extensive test coverage.

 Key Features
📊 Technical Indicators

Implemented from scratch using NumPy/Pandas:

RSI (Relative Strength Index)
MACD (Moving Average Convergence Divergence)
Bollinger Bands
ATR (Average True Range)
SMA / EMA
Stochastic Oscillator
Williams %R
OBV, ROC, Keltner & Donchian Channels
🧠 Strategy Framework

Pluggable strategy system:

SMA Crossover Strategy
RSI-based strategy
MACD-based strategy
Extensible base strategy architecture
💼 Portfolio Management
Position tracking (open/close lifecycle)
Unrealized & realized PnL
Portfolio valuation
Performance summaries
Stop-loss handling
⚖️ Risk Management
Position sizing (Fixed Fraction, Percent Risk, Volatility-based)
Kelly Criterion implementation
Value at Risk (VaR)
Sortino Ratio
Beta calculations
Risk-adjusted allocation models
🔁 Backtesting Engine

Event-driven simulation engine supporting:

Trade execution simulation
Zero-price and edge-case safety handling
Performance metrics computation
Strategy evaluation over historical data
📈 Analytics & Metrics
Sharpe Ratio (annualized)
Win rate & expectancy
Profit factor
Maximum drawdown
Calmar ratio
Return distributions
⚡ Performance Optimizations
Cached SMA/MACD computations
Optimized stochastic calculations
Lazy imports for startup speed
Memory-efficient rolling calculations
🧪 Testing
72+ automated unit tests (pytest)
Coverage across all modules:
indicators
risk engine
portfolio system
backtesting engine
strategies
Edge case validation (zero division, empty inputs, flat markets)

Run tests:

pytest -q
🧱 Project Structure
trading-assistant/
│
├── trading_assistant/
│   ├── analysis/        # Performance analytics
│   ├── backtesting/     # Simulation engine
│   ├── core/            # Indicators & validation
│   ├── data/            # Data generation & fetching
│   ├── portfolio/       # Portfolio simulation
│   ├── risk/            # Risk models & sizing
│   ├── strategies/      # Trading strategies
│   └── utils/           # Helper utilities
│
├── tasks/               # Structured engineering tasks
│   ├── indicators/
│   ├── backtesting/
│   ├── performance/
│   └── refactor/
│
├── tests/               # Pytest suite (72 tests)
├── scripts/             # Benchmarking tools
├── Dockerfile
├── requirements.txt
└── README.md
  Task System (Benchmark Design)

This repository includes a structured task system used for iterative development and evaluation.

Each task contains:

instruction.md → problem specification
solve.sh → reproducible solution steps
Task Categories
Indicators (01) → implementation of technical indicators
Backtesting (02) → simulation & correctness fixes
Performance (03) → optimization & caching improvements
Refactor (04) → code quality, structure, and maintainability
    Design Philosophy

This project was built around:

modularity over monoliths
reproducible research workflows
test-driven financial modeling
incremental improvement via task-based development
real-world quant system architecture patterns
⚙️ Installation
pip install -r requirements.txt
pip install -e .
▶️ Example Usage
from trading_assistant.core.indicators import calculate_rsi
from trading_assistant.backtesting.engine import BacktestEngine
from trading_assistant.strategies.implementations import RSIStrategy
📦 Requirements
Python 3.11+
numpy
pandas
pytest
📊 Project Status
✔ 72 tests passing
✔ Fully modular architecture
✔ Performance optimized core indicators
✔ Complete backtesting pipeline
✔ Risk + portfolio system implemented
✔ Task-driven engineering structure
🧭 Notes for Reviewers

This repository is intentionally designed as:

a quantitative research simulation environment
a structured engineering benchmark system
a test-driven trading framework

It emphasizes:

correctness
modular design
reproducibility
incremental development history (Git commits)
📄 License

For evaluation and educational purposes.
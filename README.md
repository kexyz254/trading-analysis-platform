cd ~/trading-assistant

cat > README.md << 'EOF'
# trading-assistant

a python library for backtesting trading strategies

also contains coding tasks for ai agents to practice

## tasks

20 tasks total, covering different types:

**add (5)**
- 001: rsi indicator
- 002: macd indicator
- 003: bollinger bands
- 004: atr indicator
- 005: sharpe ratio

**bug fixes (6)**
- 006: rsi division by zero
- 008: rsi flat prices
- 009: macd empty data
- 010: backtest zero price
- 017: memory usage
- 020: error messages

**performance (4)**
- 007: macd caching
- 013: sma caching
- 014: stochastic optimization
- 019: lazy imports

**refactor (4)**
- 011: test fixtures
- 012: validation module
- 015: naming consistency
- 016: duplicate code

**docs (1)**
- 018: type hints

## what's in the code

**core indicators:**
- rsi, macd, bollinger bands, atr, sharpe ratio
- sma, ema, stochastic, williams %r
- obv, roc, keltner, donchian
- ichimoku, vortex, fibonacci

**backtesting:**
- engine with position tracking
- metrics (sharpe, sortino, calmar, drawdown)

**risk management:**
- position sizing (kelly, fixed fraction, volatility)
- var, cvar, beta

**portfolio:**
- position tracking
- pnl calculation
- performance metrics

**strategies:**
- sma crossover
- rsi mean reversion
- macd
- bollinger bands
- grid trading

**data:**
- synthetic price generator
- ohlc validation

## testing

all tests pass (72 total)

```bash
pytest tests/ -v
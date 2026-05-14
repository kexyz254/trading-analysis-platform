# trading-assistant

few tasks for ai agents to practice coding

## what's here

5 tasks right now:
- 001: add rsi indicator
- 002: add macd indicator  
- 003: add bollinger bands
- 004: add atr indicator
- 005: add sharpe ratio

each task has:
- instruction.md - what to build
- solve.sh - checks if it works

## running tests

pytest tests/

## install

pip install -e .

## docker

docker build -t trading-assistant .
docker run trading-assistant

## tests passing

16 tests, all pass

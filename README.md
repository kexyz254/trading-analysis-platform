# Trading Analysis Platform Native Core

Trading Analysis Platform Native Core is a C++ market-data and allocation-validation library.

It parses allocation plans, validates portfolio targets, evaluates reconciliation rules, checks risk and liquidity limits, and supports fuzz testing of structured allocation input.

The primary native component under fuzzing is the allocation-plan parser and validator. The allocation_plan_fuzzer.cc target feeds structured bytes into the C++ allocation parser and exercises the native market-data validation layer.

## Native C++ components

- Allocation plan parser
- Allocation diagnostics engine
- Portfolio reconciliation engine
- Trade lifecycle validator
- Margin risk engine
- Stress scenario engine
- Execution quality engine
- Position limit engine
- Settlement risk engine
- Liquidity risk engine
- Market data parser
- Binary feed decoder
- Replay event log parser
- FIX message parser
- Portfolio snapshot parser
- Risk limits parser
- Symbol registry
- Volatility surface parser

## Fuzz target

The main fuzz target is allocation_plan_fuzzer.cc.

It is connected to the native C++ allocation-plan parsing and validation code under native/marketdata.

## Build

ClusterFuzzLite builds the C++ harnesses from .clusterfuzzlite/build.sh.

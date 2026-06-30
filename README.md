# Trading Analysis Platform Native Core

Trading Analysis Platform Native Core is a C++ market-data and allocation-validation library.

It parses allocation plans, validates portfolio targets, plans execution orders, evaluates allocation risk budgets, simulates rebalance execution, solves allocation constraints, and builds allocation trace diagnostics.

The primary native component under fuzzing is the allocation-plan parser and validator. The allocation_plan_fuzzer.cc target feeds structured bytes into the C++ allocation parser and exercises native market-data validation code under native/marketdata.

## Native C++ components

- Allocation plan parser
- Allocation diagnostics engine
- Allocation execution planner
- Allocation risk budget evaluator
- Allocation rebalance simulator
- Allocation constraint solver
- Allocation trace builder
- Portfolio reconciliation engine
- Trade lifecycle validator
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

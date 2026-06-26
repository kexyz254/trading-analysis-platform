# Native Market Data Layer

This directory contains the C++ market-data parsing surface used by the fuzzing targets.

The native layer is intentionally dependency-free and deterministic. It models trading-platform input boundaries such as OHLCV CSV records, order messages, symbol validation, price validation, sequence ordering, and malformed market-data streams.

This gives the repository a supported native fuzzing surface while keeping the existing Python trading analysis package unchanged.

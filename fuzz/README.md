# Fuzzing Targets

This repository includes ClusterFuzzLite-compatible fuzz targets for the native market-data parsing layer.

## Targets

- ohlcv_csv_fuzzer: exercises OHLCV CSV parsing and validation.
- order_message_fuzzer: exercises pipe-delimited order message and order stream parsing.

## Seed Corpus

Per-target seeds are stored under:

- fuzz/corpus/ohlcv_csv_fuzzer/
- fuzz/corpus/order_message_fuzzer/

## Local Smoke Test

Run:

./tools/check_fuzz_build.sh

ClusterFuzzLite entry point:

.clusterfuzzlite/build.sh

The fuzz build does not require credentials, network services, local absolute paths, or interactive prompts.

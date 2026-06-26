# Fuzzing Targets

This repository includes ClusterFuzzLite-compatible fuzz targets for the native market-data parsing layer.

## Targets

- ohlcv_csv_fuzzer
- order_message_fuzzer
- binary_feed_fuzzer
- order_book_snapshot_fuzzer
- fix_message_fuzzer
- portfolio_snapshot_fuzzer
- replay_event_log_fuzzer
- risk_limits_fuzzer
- session_calendar_fuzzer
- symbol_registry_fuzzer
- corporate_actions_fuzzer
- feature_matrix_fuzzer
- market_event_frame_fuzzer
- allocation_plan_fuzzer
- volatility_surface_fuzzer
- market_data_router_fuzzer

## Seed Corpus

Per-target seeds are stored under fuzz/corpus/.

## Local Smoke Test

Run:

./tools/check_fuzz_build.sh

ClusterFuzzLite entry point:

.clusterfuzzlite/build.sh

The fuzz build does not require credentials, network services, local absolute paths, or interactive prompts.

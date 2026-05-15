#!/usr/bin/env bash

set -e

echo "Starting task restructure..."

mkdir -p tasks/01_indicators
mkdir -p tasks/02_backtesting
mkdir -p tasks/04_performance
mkdir -p tasks/05_refactor

# INDICATORS
git mv -f tasks/001_add_rsi_indicator tasks/01_indicators/rsi_implementation
git mv -f tasks/002_add_macd_indicator tasks/01_indicators/macd_implementation
git mv -f tasks/003_add_bollinger_bands tasks/01_indicators/bollinger_bands
git mv -f tasks/004_add_atr_indicator tasks/01_indicators/atr_indicator

# BACKTESTING
git mv -f tasks/005_add_sharpe_ratio tasks/02_backtesting/sharpe_ratio_metric
git mv -f tasks/008_fix_backtest_position tasks/02_backtesting/fix_position_handling
git mv -f tasks/010_fix_backtest_zero tasks/02_backtesting/fix_zero_division
git mv -f tasks/011_refactor_test_fixture tasks/02_backtesting/test_fixture_refactor

# PERFORMANCE
git mv -f tasks/007_perf_macd tasks/04_performance/macd_optimization
git mv -f tasks/013_perf_cache_sma tasks/04_performance/sma_cache
git mv -f tasks/014_perf_stochastic tasks/04_performance/stochastic_optimization
git mv -f tasks/019_perf_imports tasks/04_performance/import_cleanup

# REFACTOR
git mv -f tasks/008_fix_rsi_edge_case tasks/05_refactor/rsi_edge_case_fix
git mv -f tasks/009_fix_macd_empty tasks/05_refactor/macd_empty_fix
git mv -f tasks/012_refactor_validation tasks/05_refactor/validation_layer
git mv -f tasks/015_refactor_naming tasks/05_refactor/naming_conventions
git mv -f tasks/016_refactor_duplicate tasks/05_refactor/remove_duplicates
git mv -f tasks/017_fix_memory tasks/05_refactor/memory_fix
git mv -f tasks/018_docs_type_hints tasks/05_refactor/type_hints_docs
git mv -f tasks/020_fix_error_messages tasks/05_refactor/error_messages_cleanup

echo "Done!"

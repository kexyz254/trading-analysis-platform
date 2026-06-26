#!/bin/bash -eu

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

export SRC="${repo_root}"
export OUT="${repo_root}/out"

rm -rf "${OUT}"

"${repo_root}/.clusterfuzzlite/build.sh"

for seed in "${repo_root}"/fuzz/corpus/ohlcv_csv_fuzzer/*; do
  "${OUT}/ohlcv_csv_fuzzer" "${seed}" -runs=1 >/dev/null
done

for seed in "${repo_root}"/fuzz/corpus/order_message_fuzzer/*; do
  "${OUT}/order_message_fuzzer" "${seed}" -runs=1 >/dev/null
done

for seed in "${repo_root}"/fuzz/corpus/binary_feed_fuzzer/*; do
  "${OUT}/binary_feed_fuzzer" "${seed}" -runs=1 >/dev/null
done

for seed in "${repo_root}"/fuzz/corpus/order_book_snapshot_fuzzer/*; do
  "${OUT}/order_book_snapshot_fuzzer" "${seed}" -runs=1 >/dev/null
done


for seed in "${repo_root}"/fuzz/corpus/fix_message_fuzzer/*; do
  "${OUT}/fix_message_fuzzer" "${seed}" -runs=1 >/dev/null
done

for seed in "${repo_root}"/fuzz/corpus/portfolio_snapshot_fuzzer/*; do
  "${OUT}/portfolio_snapshot_fuzzer" "${seed}" -runs=1 >/dev/null
done

for seed in "${repo_root}"/fuzz/corpus/replay_event_log_fuzzer/*; do
  "${OUT}/replay_event_log_fuzzer" "${seed}" -runs=1 >/dev/null
done


for seed in "${repo_root}"/fuzz/corpus/risk_limits_fuzzer/*; do
  "${OUT}/risk_limits_fuzzer" "${seed}" -runs=1 >/dev/null
done


for seed in "${repo_root}"/fuzz/corpus/session_calendar_fuzzer/*; do
  "${OUT}/session_calendar_fuzzer" "${seed}" -runs=1 >/dev/null
done


for seed in "${repo_root}"/fuzz/corpus/symbol_registry_fuzzer/*; do
  "${OUT}/symbol_registry_fuzzer" "${seed}" -runs=1 >/dev/null
done


for seed in "${repo_root}"/fuzz/corpus/corporate_actions_fuzzer/*; do
  "${OUT}/corporate_actions_fuzzer" "${seed}" -runs=1 >/dev/null
done


for seed in "${repo_root}"/fuzz/corpus/feature_matrix_fuzzer/*; do
  "${OUT}/feature_matrix_fuzzer" "${seed}" -runs=1 >/dev/null
done


for seed in "${repo_root}"/fuzz/corpus/market_event_frame_fuzzer/*; do
  "${OUT}/market_event_frame_fuzzer" "${seed}" -runs=1 >/dev/null
done


for seed in "${repo_root}"/fuzz/corpus/allocation_plan_fuzzer/*; do
  "${OUT}/allocation_plan_fuzzer" "${seed}" -runs=1 >/dev/null
done


for seed in "${repo_root}"/fuzz/corpus/volatility_surface_fuzzer/*; do
  "${OUT}/volatility_surface_fuzzer" "${seed}" -runs=1 >/dev/null
done


for seed in "${repo_root}"/fuzz/corpus/market_data_router_fuzzer/*; do
  "${OUT}/market_data_router_fuzzer" "${seed}" -runs=1 >/dev/null
done

printf 'fuzz build and seed smoke test passed\n'

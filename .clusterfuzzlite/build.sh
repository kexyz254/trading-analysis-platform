#!/bin/bash -eu

: "${SRC:=$(pwd)}"
: "${OUT:=${SRC}/out}"
mkdir -p "${OUT}"

CXX_BIN="${CXX:-clang++}"

COMMON_FLAGS=(
  -std=c++17
  -O1
  -g
  -fno-omit-frame-pointer
  -I"${SRC}"
)

if [[ -n "${CXXFLAGS:-}" ]]; then
  EXTRA_CXXFLAGS=(${CXXFLAGS})
  COMMON_FLAGS+=("${EXTRA_CXXFLAGS[@]}")
fi

NATIVE_SOURCES=("${SRC}"/native/marketdata/*.cc)

build_target() {
  local target_name="$1"
  local harness_path="$2"

  if [[ -n "${LIB_FUZZING_ENGINE:-}" ]]; then
    "${CXX_BIN}" "${COMMON_FLAGS[@]}"       "${NATIVE_SOURCES[@]}"       "${SRC}/${harness_path}"       "${LIB_FUZZING_ENGINE}"       -o "${OUT}/${target_name}"
  else
    LOCAL_FUZZ_SANITIZERS="${LOCAL_FUZZ_SANITIZERS:-fuzzer,address,undefined}"
    "${CXX_BIN}" "${COMMON_FLAGS[@]}"       -fsanitize="${LOCAL_FUZZ_SANITIZERS}"       "${NATIVE_SOURCES[@]}"       "${SRC}/${harness_path}"       -o "${OUT}/${target_name}"
  fi
}

build_target ohlcv_csv_fuzzer fuzz/ohlcv_csv_fuzzer.cc
build_target order_message_fuzzer fuzz/order_message_fuzzer.cc
build_target binary_feed_fuzzer fuzz/binary_feed_fuzzer.cc
build_target order_book_snapshot_fuzzer fuzz/order_book_snapshot_fuzzer.cc
build_target fix_message_fuzzer fuzz/fix_message_fuzzer.cc
build_target portfolio_snapshot_fuzzer fuzz/portfolio_snapshot_fuzzer.cc
build_target replay_event_log_fuzzer fuzz/replay_event_log_fuzzer.cc
build_target risk_limits_fuzzer fuzz/risk_limits_fuzzer.cc
build_target session_calendar_fuzzer fuzz/session_calendar_fuzzer.cc
build_target symbol_registry_fuzzer fuzz/symbol_registry_fuzzer.cc
build_target corporate_actions_fuzzer fuzz/corporate_actions_fuzzer.cc
build_target feature_matrix_fuzzer fuzz/feature_matrix_fuzzer.cc
build_target market_event_frame_fuzzer fuzz/market_event_frame_fuzzer.cc
build_target allocation_plan_fuzzer fuzz/allocation_plan_fuzzer.cc
build_target volatility_surface_fuzzer fuzz/volatility_surface_fuzzer.cc
build_target market_data_router_fuzzer fuzz/market_data_router_fuzzer.cc

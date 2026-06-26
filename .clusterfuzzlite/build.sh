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

build_target() {
  local target_name="$1"
  local harness_path="$2"

  if [[ -n "${LIB_FUZZING_ENGINE:-}" ]]; then
    "${CXX_BIN}" "${COMMON_FLAGS[@]}" \
      "${SRC}/native/marketdata/market_data_parser.cc" \
      "${SRC}/${harness_path}" \
      "${LIB_FUZZING_ENGINE}" \
      -o "${OUT}/${target_name}"
  else
    LOCAL_FUZZ_SANITIZERS="${LOCAL_FUZZ_SANITIZERS:-fuzzer,address,undefined}"
    "${CXX_BIN}" "${COMMON_FLAGS[@]}" \
      -fsanitize="${LOCAL_FUZZ_SANITIZERS}" \
      "${SRC}/native/marketdata/market_data_parser.cc" \
      "${SRC}/${harness_path}" \
      -o "${OUT}/${target_name}"
  fi
}

build_target ohlcv_csv_fuzzer fuzz/ohlcv_csv_fuzzer.cc
build_target order_message_fuzzer fuzz/order_message_fuzzer.cc

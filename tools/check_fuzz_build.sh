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

printf 'fuzz build and seed smoke test passed\n'

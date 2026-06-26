#!/usr/bin/env bash
set -Eeuo pipefail

SECONDS_PER_TARGET="${1:-1800}"

cd "$(git rev-parse --show-toplevel)"

export SRC="$PWD"
export OUT="$PWD/out"
export ASAN_OPTIONS="detect_leaks=1:abort_on_error=1:symbolize=1"
export UBSAN_OPTIONS="print_stacktrace=1:halt_on_error=1"

TARGETS=(
  binary_feed_fuzzer
  market_event_frame_fuzzer
  fix_message_fuzzer
  order_book_snapshot_fuzzer
  market_data_router_fuzzer
)

rm -rf "$OUT"
mkdir -p findings

echo "[1/3] Build all fuzz targets"
.clusterfuzzlite/build.sh

echo "[2/3] Confirm targets"
for target in "${TARGETS[@]}"; do
  test -x "$OUT/$target"
  echo "OK: $target"
done

echo "[3/3] Fuzz high-risk targets for ${SECONDS_PER_TARGET}s each"
for target in "${TARGETS[@]}"; do
  corpus="findings/$target/corpus"
  artifacts="findings/$target/artifacts"
  seed="fuzz/corpus/$target"

  mkdir -p "$corpus" "$artifacts"

  if [ -d "$seed" ] && [ -z "$(find "$corpus" -type f 2>/dev/null | head -1)" ]; then
    cp -a "$seed"/. "$corpus"/
  fi

  echo
  echo "============================================================"
  echo "FUZZING: $target"
  echo "============================================================"

  set +e
  "$OUT/$target" "$corpus" \
    -max_total_time="$SECONDS_PER_TARGET" \
    -max_len=4096 \
    -artifact_prefix="$artifacts/" \
    -print_final_stats=1 \
    -use_value_profile=1 \
    -detect_leaks=1 \
    2>&1 | tee "findings/$target/run.log"
  rc="${PIPESTATUS[0]}"
  set -e

  echo "$rc" > "findings/$target/exit_code.txt"

  if find "$artifacts" -type f \( -name 'crash-*' -o -name 'leak-*' -o -name 'timeout-*' -o -name 'oom-*' \) | grep -q .; then
    echo "CRASH ARTIFACT FOUND FOR $target"
    find "$artifacts" -type f \( -name 'crash-*' -o -name 'leak-*' -o -name 'timeout-*' -o -name 'oom-*' \) -print
    exit 2
  fi
done

echo "No crash artifacts found."

#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(git rev-parse --show-toplevel)"

export SRC="$PWD"
export OUT="$PWD/out"
export ASAN_OPTIONS="detect_leaks=1:abort_on_error=1:symbolize=1"
export UBSAN_OPTIONS="print_stacktrace=1:halt_on_error=1"

SECONDS_PER_TARGET="${1:-300}"

TARGETS=(
  binary_feed_fuzzer
  market_event_frame_fuzzer
  fix_message_fuzzer
  order_book_snapshot_fuzzer
  market_data_router_fuzzer
  replay_event_log_fuzzer
  order_message_fuzzer
)

rm -rf "$OUT" findings/pre_submit
mkdir -p findings/pre_submit

chmod +x .clusterfuzzlite/build.sh

echo "[1/3] Building fuzz targets..."
.clusterfuzzlite/build.sh

echo "[2/3] Checking targets..."
for target in "${TARGETS[@]}"; do
  if [ ! -x "$OUT/$target" ]; then
    echo "Missing target: $target"
    exit 1
  fi
  echo "OK: $target"
done

echo "[3/3] Running pre-submit fuzz gate..."
for target in "${TARGETS[@]}"; do
  seed="fuzz/corpus/$target"
  work="findings/pre_submit/$target"
  corpus="$work/corpus"
  artifacts="$work/artifacts"

  mkdir -p "$corpus" "$artifacts"

  if [ -d "$seed" ]; then
    cp -a "$seed"/. "$corpus"/ 2>/dev/null || true
  fi

  echo
  echo "============================================================"
  echo "FUZZING: $target"
  echo "============================================================"

  set +e
  "$OUT/$target" "$corpus" \
    -max_total_time="$SECONDS_PER_TARGET" \
    -max_len=8192 \
    -artifact_prefix="$artifacts/" \
    -print_final_stats=1 \
    -use_value_profile=1 \
    -detect_leaks=1 \
    2>&1 | tee "$work/run.log"
  rc="${PIPESTATUS[0]}"
  set -e

  echo "$rc" > "$work/exit_code.txt"

  if find "$artifacts" -type f \( -name 'crash-*' -o -name 'leak-*' -o -name 'timeout-*' -o -name 'oom-*' \) | grep -q .; then
    echo
    echo "CRASH ARTIFACT FOUND:"
    find "$artifacts" -type f \( -name 'crash-*' -o -name 'leak-*' -o -name 'timeout-*' -o -name 'oom-*' \) -print
    exit 2
  fi
done

echo
echo "No crash artifacts found."
